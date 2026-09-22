"""Meaningful authoring boundaries; no Kit, GPU, ROS, or remote assets."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from demo.benchmark.goal_driven_contract import validate_goal_task_spec, validate_goal_participant_api
from demo.city_benchmark.config import CityPreparationConfig, integration_manifest
from demo.city_benchmark.contracts import (authoring_record, build_public_task,
                                            participant_service_coverage, review_readiness)
from demo.city_benchmark.maps import rasterize, semantic_map, write_map_products
from demo.city_benchmark.selection import build_selected_selection
from demo.city_benchmark.response import (CityResponseServices, PeopleResponseRuntime, HumanEvacuationBinding,
                                          build_response_people, spawn_response_people)
from demo.city_benchmark.traffic import TrafficControlRuntime, build_following_traffic
from demo.city_benchmark.incident import IncidentReportRuntime


def polygon(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


@pytest.fixture
def scene():
    layout = {"geometry": {"boundary": polygon(0, 0, 20, 20), "road_surface": polygon(0, 0, 20, 5),
                           "sidewalk_surface": polygon(0, 5, 20, 10)}, "surface_heights": {"ground": .22}}
    traffic = {"crosswalks": [], "vehicle_routes": [{"route_id": "v1", "points": [[1, 2], [19, 2]]}],
               "pedestrian_routes": [{"route_id": "p1", "points": [[1, 6], [19, 6]], "segment_crosswalk_ids": [None]}]}
    obstacles = [{"id": "building_001", "kind": "building", "bounds_m": [[14, 1, 0], [18, 4, 8]],
                  "runtime_prim_path": "/World/City/Building_A"}]
    grid, meta = rasterize(layout, obstacles, resolution_m=.25, inflation_m=.45)
    semantics = semantic_map(layout, traffic, obstacles, meta, grid)
    return layout, traffic, obstacles, grid, meta, semantics


def at(grid, meta, x, y):
    return grid[meta["height"] - 1 - int((y - meta["origin_xyz_m"][1]) / meta["resolution_m"]),
                int((x - meta["origin_xyz_m"][0]) / meta["resolution_m"])]


def test_map_does_not_invent_free_space_or_ignore_footprints(scene):
    _, _, _, grid, meta, _ = scene
    assert at(grid, meta, 3, 3) == 254
    assert at(grid, meta, 16, 2) == 0
    assert at(grid, meta, 13.8, 2) == 0  # occupied after footprint inflation
    assert at(grid, meta, 3, 5) == 0  # curb crossing is not assumed supported
    assert at(grid, meta, 3, 15) == 205  # unsurveyed lot stays unknown
    assert not meta["navigation_validated"]


def test_candidate_search_keeps_clearance_and_never_asserts_safety(scene):
    _, _, _, _, _, semantics = scene
    candidates = [r for r in semantics["bindings"] if r["id"].startswith("assembly_candidate")]
    assert candidates  # centerline hugs curb; bounded lateral search finds room
    assert all(not c["safety_validated"] and c["capacity_people"] is None for c in candidates)


def test_png_yaml_coordinate_orientation_and_unknown_threshold(scene, tmp_path):
    import yaml
    from PIL import Image
    _, _, _, grid, meta, semantics = scene
    write_map_products(tmp_path / "maps", grid, meta, semantics)
    config = yaml.safe_load((tmp_path / "maps/occupancy.yaml").read_text())
    image = Image.open(tmp_path / "maps" / config["image"])
    assert image.getpixel((12, 19)) == 205  # north / undeclared lot
    assert image.getpixel((12, 67)) == 254  # south / road
    unknown_probability = (255 - 205) / 255
    assert config["free_thresh"] < unknown_probability < config["occupied_thresh"]
    assert config["origin"] == [0., 0., 0.]
    with pytest.raises(FileExistsError):
        write_map_products(tmp_path / "maps", grid, meta, semantics)


def test_public_draft_uses_shared_goal_api_without_answers(scene):
    from demo.benchmark.goal_driven_contract import build_goal_participant_api
    public = build_public_task(scene[-1])
    assert validate_goal_task_spec(public)["validated"]
    api = build_goal_participant_api(public)
    assert validate_goal_participant_api(api, task_spec=public)["validated"]
    text = json.dumps(public) + json.dumps(api)
    for forbidden in ("eligible_agent_ids", "affected_node_ids", "preferred_agent", "impact_time", "reference_plan"):
        # Shared API describes fields it withholds in prose; check actual keys.
        assert f'"{forbidden}":' not in text
    assert '"prerequisites":' not in text
    assert '"assigned_agents":' not in text
    assert '"2.5"' not in text
    assert public["public_world"]["objects"] == []  # observed actor identities are not fabricated
    assert any("15 秒" in text and "送达" in text for text in public["business_constraints"])
    assert any("35 秒" in text and "派遣" in text for text in public["business_constraints"])
    assert any("5 秒" in text and "车辆轨迹" in text for text in public["business_constraints"])
    broken = deepcopy(public)
    broken["required_outcomes"][0]["skill"] = "observe"
    with pytest.raises(ValueError):
        validate_goal_task_spec(broken)


def test_city_public_capabilities_can_submit_observation_and_team_message(scene):
    from demo.benchmark.goal_driven_contract import validate_goal_plan
    public = build_public_task(scene[-1])
    plan = {
        "schema": "eai.benchmark.goal_plan", "version": 3, "plan_id": "city_probe",
        "assigned_team": ["iris_1"],
        "tasks": [
            {"task_id": "observe_building", "skill": "observe",
             "arguments": {"region_id": "building_001", "observation_mode": "incident"},
             "prerequisites": [], "required_capabilities": ["observe"],
             "assigned_agents": ["iris_1"], "evidence_refs": [],
             "execution": {"start_s": 0.0, "duration_s": 4.0}},
            {"task_id": "publish_guidance", "skill": "relay",
             "arguments": {"message_kind": "guide_people"},
             "prerequisites": ["observe_building"], "required_capabilities": ["relay"],
             "assigned_agents": ["iris_1"], "evidence_refs": [],
             "execution": {"start_s": 4.0, "duration_s": 1.0}},
        ],
    }
    assert validate_goal_plan(plan, task_spec=public)["validated"]


def test_missing_services_and_unique_observer_cannot_pass_admission(scene):
    public = build_public_task(scene[-1])
    review = review_readiness(public, scene[-1])
    assert review["public_contract_validated"]
    assert review["authoring_status"] == "blocked"
    assert review["valid_full_mission_plan_count"] == 0
    assert not review["scoreable"]
    assert review["private_capability_candidates"]["ground_navigation"] == ["go2_1", "carter_1"]
    assert review["private_capability_candidates"]["observe"] == ["iris_1"]
    hidden = authoring_record(public, {}, scene[2])
    assert hidden["full_mission_witnesses"] == []
    assert hidden["required_services"][0]["status"] == "public_skill_registered_runtime_pending"
    assert hidden["required_services"][1]["status"] == "public_skill_registered_runtime_pending"
    assert hidden["incident_template"]["timing"]["ignition_time"] == 2.5
    coverage = participant_service_coverage()
    assert coverage["all_services_publicly_invocable"]
    assert all(row["public_entry_point"] for row in coverage["services"])
    assert all(row["runtime_status"] == "pending" for row in coverage["services"])
    assert "public_domain_service_actions" not in review["blockers"]
    assert "city_service_runtime_witness" in review["blockers"]


@pytest.fixture
def city_acceptance_evidence():
    return {
        "incident_assessed": {"observation_ref": "incident_obs_1", "fresh_at_decision": True,
                              "source_visible_to_participant": True,
                              "observation_delivery_time_s": 10.0, "decision_time_s": 18.0},
        "responder_dispatch": {"selected_agent_id": "carter_1", "agent_active_at_dispatch": True,
                               "capability_admitted": True, "route_admitted": True,
                               "dispatch_time_s": 18.0, "arrival_time_s": 45.0,
                               "eta_was_minimum_among_admitted_options": True},
        "people": [{"person_id": "affected_person_00", "observation_ref": "people_obs_1",
                    "guidance_acknowledged": True, "route_admitted": True,
                    "entered_hazard": False, "arrived_at_admitted_assembly": True,
                    "guidance_request_time_s": 48.0,
                    "guidance_time_s": 50.0, "arrival_time_s": 100.0}],
        "traffic": [{"vehicle_id": "following_vehicle_00", "following_incident": True,
                     "control_acknowledged": True, "trajectory_sample_after_ack": True,
                     "entered_exclusion_boundary": False, "request_time_s": 20.0,
                     "ack_time_s": 25.0, "trajectory_check_time_s": 30.0}],
        "incident_report": {"observation_refs": ["incident_obs_1"], "receipt_received": True,
                            "submit_time_s": 105.0, "receipt_time_s": 110.0},
        "response_access": {"continuous_access_check": True, "route_admitted": True,
                            "hazard_free_throughout": True, "check_start_time_s": 108.0,
                            "check_end_time_s": 112.0},
    }


def _city_accept(evidence):
    from demo.city_benchmark.task_logic import default_deadline_policy, evaluate_city_acceptance

    return evaluate_city_acceptance(
        evidence, default_deadline_policy(),
        expected_affected_person_ids={"affected_person_00"},
        expected_following_vehicle_ids={"following_vehicle_00"})


def test_city_acceptance_requires_business_evidence_and_deadlines(city_acceptance_evidence):
    evidence = city_acceptance_evidence
    result = _city_accept(evidence)
    assert result["mission_success"]
    assert result["predicate_only"] and not result["scoreable"]
    assert not result["execution_validated"]
    evidence["traffic"][0]["trajectory_sample_after_ack"] = False
    failed = _city_accept(evidence)
    assert not failed["outcomes"]["secondary_traffic_protected"]["success"]
    assert "vehicle:following_vehicle_00:trajectory_response_missing" in \
        failed["outcomes"]["secondary_traffic_protected"]["failure_codes"]


@pytest.mark.parametrize("section", ["people", "traffic", "both"])
def test_city_acceptance_missing_roster_alone_blocks_success(city_acceptance_evidence, section):
    from demo.city_benchmark.task_logic import default_deadline_policy, evaluate_city_acceptance

    assert _city_accept(city_acceptance_evidence)["mission_success"]
    rosters = {
        "expected_affected_person_ids": {"affected_person_00"},
        "expected_following_vehicle_ids": {"following_vehicle_00"},
    }
    if section in {"people", "both"}:
        rosters.pop("expected_affected_person_ids")
    if section in {"traffic", "both"}:
        rosters.pop("expected_following_vehicle_ids")
    result = evaluate_city_acceptance(city_acceptance_evidence, default_deadline_policy(), **rosters)
    assert not result["mission_success"]
    if section in {"people", "both"}:
        assert "affected_person_roster_binding_missing" in \
            result["outcomes"]["exposed_people_accounted_for"]["failure_codes"]
    if section in {"traffic", "both"}:
        assert "following_traffic_roster_binding_missing" in \
            result["outcomes"]["secondary_traffic_protected"]["failure_codes"]


@pytest.mark.parametrize("section,outcome,code", [
    ("people", "exposed_people_accounted_for", "duplicate_affected_person_evidence"),
    ("traffic", "secondary_traffic_protected", "duplicate_following_vehicle_evidence"),
])
def test_city_acceptance_duplicate_identity_alone_blocks_success(
        city_acceptance_evidence, section, outcome, code):
    evidence = city_acceptance_evidence
    assert _city_accept(evidence)["mission_success"]
    evidence[section].append(deepcopy(evidence[section][0]))
    result = _city_accept(evidence)
    assert not result["mission_success"]
    assert code in result["outcomes"][outcome]["failure_codes"]


@pytest.mark.parametrize("kind", ["person", "vehicle"])
@pytest.mark.parametrize("roster", [None, set(), {None}, "p", ["p", "p"]])
def test_city_acceptance_rosters_cannot_be_unbound_or_coerced(city_acceptance_evidence, kind, roster):
    from demo.city_benchmark.task_logic import default_deadline_policy, evaluate_city_acceptance

    rosters = {"expected_affected_person_ids": {"affected_person_00"},
               "expected_following_vehicle_ids": {"following_vehicle_00"}}
    key = "expected_affected_person_ids" if kind == "person" else "expected_following_vehicle_ids"
    rosters[key] = roster
    if roster is None:
        assert not evaluate_city_acceptance(
            city_acceptance_evidence, default_deadline_policy(), **rosters)["mission_success"]
    else:
        with pytest.raises(ValueError, match="roster"):
            evaluate_city_acceptance(city_acceptance_evidence, default_deadline_policy(), **rosters)


def test_city_acceptance_missing_access_alone_blocks_success(city_acceptance_evidence):
    assert _city_accept(city_acceptance_evidence)["mission_success"]
    city_acceptance_evidence.pop("response_access")
    result = _city_accept(city_acceptance_evidence)
    assert not result["mission_success"]
    assert result["outcomes"]["response_access_preserved"]["failure_codes"] == [
        "continuous_access_evidence_missing"]


@pytest.mark.parametrize("section,field,value,code", [
    ("incident_assessed", "decision_time_s", 9.0, "incident_decision_before_observation_delivery"),
    ("incident_assessed", "decision_time_s", 26.0, "incident_confirmation_deadline_exceeded"),
    ("responder_dispatch", "arrival_time_s", 17.0, "responder_arrival_before_dispatch"),
    ("responder_dispatch", "arrival_time_s", 54.0, "first_responder_arrival_deadline_exceeded"),
    ("responder_dispatch", "dispatch_time_s", 9.0, "responder_dispatch_before_observation_delivery"),
    ("people", "arrival_time_s", 49.0, "person:affected_person_00:arrival_before_guidance"),
    ("people", "arrival_time_s", 171.0, "person:affected_person_00:assembly_deadline_exceeded"),
    ("people", "guidance_time_s", 47.0, "person:affected_person_00:guidance_acknowledgement_before_request"),
    ("people", "guidance_time_s", 69.0, "person:affected_person_00:guidance_acknowledgement_deadline_exceeded"),
    ("traffic", "ack_time_s", 19.0, "vehicle:following_vehicle_00:control_acknowledgement_before_request"),
    ("traffic", "ack_time_s", 31.0, "vehicle:following_vehicle_00:control_acknowledgement_deadline_exceeded"),
    ("traffic", "trajectory_check_time_s", 24.0, "vehicle:following_vehicle_00:trajectory_check_before_acknowledgement"),
    ("traffic", "trajectory_check_time_s", 29.0, "vehicle:following_vehicle_00:reaction_window_not_observed"),
    ("incident_report", "receipt_time_s", 104.0, "report_receipt_before_submission"),
    ("incident_report", "receipt_time_s", 121.0, "report_receipt_deadline_exceeded"),
    ("response_access", "check_start_time_s", 113.0, "continuous_access_end_before_start"),
    ("response_access", "check_end_time_s", 108.0, "continuous_access_interval_empty"),
    ("response_access", "check_end_time_s", 109.0, "continuous_access_does_not_cover_receipt"),
    ("response_access", "continuous_access_check", False, "continuous_access_check_failed"),
    ("response_access", "continuous_access_check", "false", "continuous_access_check_failed"),
    ("response_access", "route_admitted", False, "response_route_not_admitted"),
    ("response_access", "hazard_free_throughout", False, "response_route_hazard_clearance_missing"),
])
def test_city_acceptance_causal_and_access_counterexamples(
        city_acceptance_evidence, section, field, value, code):
    evidence = city_acceptance_evidence
    assert _city_accept(evidence)["mission_success"]
    row = evidence[section][0] if section in {"people", "traffic"} else evidence[section]
    row[field] = value
    result = _city_accept(evidence)
    assert not result["mission_success"]
    assert code in [failure for outcome in result["outcomes"].values()
                    for failure in outcome["failure_codes"]]


@pytest.mark.parametrize("minimum_eta", [False, None])
def test_city_acceptance_allows_legal_nonminimum_eta_dispatch(city_acceptance_evidence, minimum_eta):
    dispatch = city_acceptance_evidence["responder_dispatch"]
    if minimum_eta is None:
        dispatch.pop("eta_was_minimum_among_admitted_options")
    else:
        dispatch["eta_was_minimum_among_admitted_options"] = minimum_eta
    result = _city_accept(city_acceptance_evidence)
    assert result["mission_success"]
    assert result["outcomes"]["response_dispatch"]["failure_codes"] == []


def test_city_acceptance_relative_windows_cannot_extend_total_budget(city_acceptance_evidence):
    for value in city_acceptance_evidence.values():
        for row in value if isinstance(value, list) else [value]:
            for key in row:
                if key.endswith("_time_s"):
                    row[key] += 180.0
    result = _city_accept(city_acceptance_evidence)
    assert not result["mission_success"]
    assert result["failure_codes"] == ["response_budget_exceeded"]


@pytest.mark.parametrize("section,field", [
    ("people", "guidance_request_time_s"), ("traffic", "request_time_s"),
    ("response_access", "check_start_time_s"), ("response_access", "check_end_time_s"),
])
def test_city_acceptance_requires_public_timing_anchors(city_acceptance_evidence, section, field):
    row = city_acceptance_evidence[section]
    (row[0] if isinstance(row, list) else row).pop(field)
    assert not _city_accept(city_acceptance_evidence)["mission_success"]


@pytest.mark.parametrize("section,key", [("people", "person_id"), ("traffic", "vehicle_id")])
@pytest.mark.parametrize("identity", [None, [], "another_actor"])
def test_city_acceptance_evidence_must_match_bound_identities(
        city_acceptance_evidence, section, key, identity):
    city_acceptance_evidence[section][0][key] = identity
    assert not _city_accept(city_acceptance_evidence)["mission_success"]


def test_city_deadlines_are_public_with_same_private_policy(scene):
    from demo.city_benchmark.task_logic import public_deadline_constraints

    public = build_public_task(scene[-1], response_budget_s=240.0)
    private = authoring_record(public, {}, scene[2])
    policy = private["task_logic"]["deadline_policy"]
    assert policy["response_budget_s"] == public["budgets"]["simulation_s"] == 240.0
    assert all(rule in public["business_constraints"] for rule in public_deadline_constraints(policy))
    constraints = "\n".join(public["business_constraints"])
    assert "引导请求提交后 20 秒" in constraints
    assert "交通控制请求提交后 10 秒" in constraints
    assert "最小 ETA 仅为诊断" in constraints


def test_selected_teams_preserve_builder_identity_without_fixed_roles():
    a = build_selected_selection(["iris_1", "go2_1"], max_active_agents=2)
    b = build_selected_selection(["carter_1", "iris_1"], max_active_agents=2)
    assert [r["type"] for r in a["robots"]] == ["go2", "iris"]
    assert [r["type"] for r in b["robots"]] == ["carter", "iris"]
    assert a["scene_key"] == "modern_house_nvd"  # scaffold only
    assert a["task_name"] == "city_benchmark"
    for agents in (["go2_1", "go2_1"], ["reserve_hidden"], []):
        with pytest.raises(ValueError):
            build_selected_selection(agents)
    with pytest.raises(ValueError, match="budget"):
        build_selected_selection(["go2_1", "iris_1"], max_active_agents=1)
    adapted = build_selected_selection(["carter_1"], spawn_positions={"carter_1": [2., 3., .3]})
    assert adapted["robots"][0]["spawn_pose"]["position"] == [2., 3., .3]
    with pytest.raises(ValueError, match="exactly cover"):
        build_selected_selection(["carter_1"], spawn_positions={})
    with pytest.raises(ValueError, match="finite"):
        build_selected_selection(["carter_1"], spawn_positions={"carter_1": [1., 1., float('nan')]})


def test_navigation_handoff_never_admits_uncertified_starts(scene):
    from demo.city_benchmark.selection import navigation_handoff
    nav = navigation_handoff(scene[3], scene[4], scene[5])
    # Synthetic map is far from real city starts, so no fake proposal is made.
    assert all(r["candidate_spawn_xyz_m"] is None for r in nav["start_reviews"])
    assert all(not r["physical_spawn_validated"] for r in nav["start_reviews"])
    assert not nav["navigation_ready"]


def test_composed_geometry_finds_default_prim_buildings_without_source_edits(tmp_path):
    pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom
    from demo.city_benchmark.maps import inspect_geometry, file_hash
    path = tmp_path / "city_static.usd"
    stage = Usd.Stage.CreateNew(str(path))
    root = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(root.GetPrim())
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdGeom.Xform.Define(stage, "/World/City")
    UsdGeom.Xform.Define(stage, "/World/Building_A")
    mesh = UsdGeom.Mesh.Define(stage, "/World/Building_A/wall")
    mesh.CreatePointsAttr([(0., 0., 0.), (2., 0., 0.), (2., 2., 3.), (0., 2., 3.)])
    mesh.CreateFaceVertexCountsAttr([4])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
    stage.GetRootLayer().Save()
    digest = file_hash(path)
    rows, layers = inspect_geometry(tmp_path)
    assert rows[0]["runtime_prim_path"] == "/World/City/Building_A"
    assert rows[0]["bounds_m"][0][2] == pytest.approx(.22)
    assert file_hash(path) == digest
    assert layers[str(path)] == digest


@pytest.mark.parametrize("kwargs", [{"resolution_m": float('nan')}, {"inflation_m": -1},
                                  {"response_budget_s": 0}, {"max_active_agents": True}])
def test_invalid_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError):
        CityPreparationConfig(**kwargs)


def test_no_global_or_physical_activation_claim():
    manifest = integration_manifest()
    assert manifest["scene_key"] == "city"
    assert not manifest["global_registry_registered"]
    assert not manifest["execution_validated"]
    assert manifest["participant_execution_adapter"] is None


def test_prepare_refuses_existing_output_before_loading_assets(tmp_path):
    from demo.city_benchmark.prepare import prepare
    sentinel = tmp_path / "existing-user-file"
    sentinel.write_text("preserve")
    with pytest.raises(FileExistsError):
        prepare(tmp_path, config=CityPreparationConfig(asset_root=Path('/nonexistent')))
    assert sentinel.read_text() == "preserve"


def test_people_response_requires_guidance_and_accounts_for_jog_arrival(scene):
    response_map = deepcopy(scene[-1])
    response_map["pedestrian_routes"] = [
        {"id": "p1", "points": [[1, 6], [4, 6]]},
        {"id": "p2", "points": [[1, 7], [4, 7]]},
    ]
    response_map["bindings"] = [
        {"id": "assembly_candidate_001", "geometry": {"type": "Point", "coordinates": [5, 6]}},
        {"id": "assembly_candidate_002", "geometry": {"type": "Point", "coordinates": [5, 7]}},
    ]
    people = build_response_people(response_map, count=2, seed=7)
    runtime = PeopleResponseRuntime(people)
    assert runtime.accounting()["arrived_count"] == 0
    observation = runtime.observe()
    observation_ref = observation[0]["observation_ref"]
    with pytest.raises(ValueError):
        runtime.guide(["unobserved_person"], observation_refs=[observation_ref])
    events = runtime.guide([people[0].person_id], observation_refs=[observation_ref])
    assert events[0]["motion"] == "jog"
    assert runtime.observe()[0]["status"] in {"awaiting_guidance", "evacuating"}
    for _ in range(1000):
        runtime.step(.1)
        if runtime.accounting()["arrived_count"] == 1:
            break
    assert runtime.accounting()["arrived_count"] == 1
    assert runtime.accounting()["acknowledged_count"] == 1


def test_human_manifest_admits_jog_for_city_assets():
    from EAI_assets.humans.registry import HumanAssetRegistry
    registry = HumanAssetRegistry.load("usd/human/manifest.json", asset_root="usd/human", file_policy="metadata")
    for asset_id in ("rp-aaron-rigged-001", "rp-amit-rigged-008-yup-t", "synbody-0000001"):
        assert registry.require_motion(asset_id, "jog").id == "jog"


def test_human_evacuation_binding_requires_runtime_acknowledgement():
    class FakeRuntime:
        def __init__(self):
            self.calls = []
        def play_action(self, *args, **kwargs):
            self.calls.append(("play_action", args, kwargs))
        def set_path_speed(self, *args, **kwargs):
            self.calls.append(("set_path_speed", args, kwargs))
        def cancel_action(self, *args, **kwargs):
            self.calls.append(("cancel_action", args, kwargs))

    person = build_response_people({"pedestrian_routes": [{"id": "p", "points": [[0, 0], [1, 0]]}],
                                    "bindings": [{"id": "assembly_candidate_001", "geometry": {"type": "Point", "coordinates": [2, 0]}}]},
                                   count=1)[0]
    fake = FakeRuntime()
    binding = HumanEvacuationBinding(fake, {person.person_id: person})
    assert binding.start([person.person_id])[0]["motion"] == "jog"
    assert [call[0] for call in fake.calls] == ["play_action", "set_path_speed"]
    binding.finish([person.person_id])
    assert [call[0] for call in fake.calls[-2:]] == ["cancel_action", "set_path_speed"]


def test_following_traffic_requires_acknowledged_control_and_does_not_control_accident_vehicle(scene):
    vehicles = build_following_traffic(scene[-1], count=1)
    runtime = TrafficControlRuntime(vehicles)
    observation = runtime.observe()
    request = runtime.request([vehicles[0].vehicle_id], "hold",
                              observation_refs=[observation[0]["observation_ref"]])
    assert request["status"] == "pending_acknowledgement"
    runtime.step(1.0)
    acknowledged = runtime.acknowledge(request["request_id"])
    assert acknowledged["status"] == "acknowledged"
    runtime.step(1.0)
    evidence = runtime.evidence()
    assert evidence["all_selected_control_acknowledged"]
    assert evidence["protected_vehicle_ids"] == [vehicles[0].vehicle_id]


def test_incident_report_requires_receipt_before_delivery_evidence():
    runtime = IncidentReportRuntime()
    runtime.register_observation("obs_001", time_s=2.0, valid_until_s=10.0)
    runtime.register_observation("obs_002", time_s=2.5, valid_until_s=10.0)
    report = runtime.submit(["obs_001", "obs_002"], "city_dispatch", time_s=3.0)
    assert runtime.evidence(report.report_id)["status"] == "pending_receipt"
    received = runtime.acknowledge(report.report_id, time_s=3.5)
    assert received.status == "received"
    assert runtime.evidence(report.report_id)["receipt_time_s"] == 3.5


def test_city_response_services_connect_people_traffic_and_report_adapters():
    from demo.city_benchmark.traffic import TrafficControlRuntime, build_following_traffic
    people_map = {"surface_heights_m": {"sidewalks": .3},
                  "pedestrian_routes": [{"id": "p", "points": [[0, 0], [1, 0]]}],
                  "bindings": [{"id": "assembly_candidate_001", "geometry":
                                {"type": "Point", "coordinates": [2, 0]}}]}
    person = build_response_people(people_map, count=1)[0]
    people = PeopleResponseRuntime([person])
    traffic = TrafficControlRuntime([build_following_traffic({"vehicle_routes":
        [{"id": "v", "points": [[0, 0], [5, 0]]}], "crosswalks": []}, count=1)[0]])
    reports = IncidentReportRuntime()
    services = CityResponseServices(people, traffic, reports)
    people_observation = people.observe()
    person_ref = people_observation[0]["observation_ref"]
    assert services.guide_people(person_ids=[person.person_id], observation_refs=[person_ref],
                                  assembly_region_id=person.assembly_region_id)[0]["motion"] == "jog"
    traffic_observation = traffic.observe()
    traffic_ref = traffic_observation[0]["observation_ref"]
    assert services.control_following_traffic(["following_vehicle_00"], "hold",
                                              observation_refs=[traffic_ref])["status"] == "pending_acknowledgement"
    reports.register_observation("obs_1", time_s=.5, valid_until_s=3.0)
    assert services.report_incident(["obs_1"], "dispatch", time_s=1.0).status == "pending_receipt"


def test_spawned_human_path_speed_can_change_for_response_mode():
    from EAI_assets.humans.path_follower import PathFollower
    from EAI_assets.humans.spawner import SpawnedHuman
    actor = SpawnedHuman("person", "asset", movement_mode="path",
                         follower=PathFollower("person", [(0, 0, 0), (10, 0, 0)], speed=1.0))
    actor.reset_path()
    actor.set_path_speed(2.0)
    actor.advance_path(1.0)
    assert actor.pending_pose.position[0] == pytest.approx(2.0)


def test_real_usd_human_switches_walk_to_jog_and_back_without_duplicate_xform_ops():
    pytest.importorskip("pxr")
    from pxr import Usd
    person = build_response_people({
        "surface_heights_m": {"sidewalks": .3},
        "pedestrian_routes": [{"id": "p", "points": [[0, 0], [3, 0]]}],
        "bindings": [{"id": "assembly_candidate_001", "geometry":
                      {"type": "Point", "coordinates": [4, 0]}}],
    }, count=1)[0]
    runtime, binding, _ = spawn_response_people(Usd.Stage.CreateInMemory(), [person])
    try:
        binding.start([person.person_id])
        update = runtime.update(.5)
        assert any(event.motion_id == "jog" for event in update.events)
        assert runtime.position(person.person_id)[0] == pytest.approx(.9)
        binding.finish([person.person_id])
        runtime.update(.1)
    finally:
        runtime.close()
