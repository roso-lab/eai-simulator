"""Formal episode binding: author-frozen parameters, witnesses and admission.

Pure authoring checks only: no Isaac, GPU, ROS, network or provider assets.
They cover the three bindings that used to be runtime picks in the service
probe (roster/assembly/hazard/ingress/window/endpoint), the declaration-level
city goal bundle with its two witnesses, and the suite/freeze admission path.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
import zlib

import pytest

from demo.benchmark.goal_city_intake import load_city_goal_candidate
from demo.benchmark.goal_driven_contract import (
    build_goal_heterogeneity_certificate,
    semantic_goal_plan_signature,
    validate_goal_bundle,
)
from demo.benchmark.goal_driven_tasks import (
    _CITY_AFFECTED_PERSON_ASSEMBLY,
    _CITY_FOLLOWING_VEHICLE_IDS,
    build_goal_task_package,
    review_goal_business_witness,
    review_goal_task_design,
    validate_goal_factor_pair,
)
from demo.benchmark.goal_freeze import build_goal_freeze_manifest, validate_goal_freeze_manifest
from demo.benchmark.goal_suite import verify_goal_suite, write_goal_suite
from demo.benchmark.provenance import canonical_sha256
from demo.city_benchmark.contracts import (
    build_episode_binding,
    episode_parameter_names,
    review_episode_binding,
    build_public_task,
    authoring_record,
)


def _binding_map():
    return {
        "surface_heights_m": {"sidewalks": 0.3, "ground": 0.22},
        "pedestrian_routes": [
            {"id": f"p{index}", "points": [[float(index), 6.0], [float(index) + 3.0, 6.0]]}
            for index in range(4)
        ],
        "bindings": [
            {"id": "assembly_candidate_001", "candidate_radius_m": 0.75,
             "geometry": {"type": "Point", "coordinates": [10.0, 6.0]}},
            {"id": "assembly_candidate_002", "candidate_radius_m": 0.75,
             "geometry": {"type": "Point", "coordinates": [10.0, 7.0]}},
        ],
        "regions": [
            {"id": "road_surface", "kind": "road_network", "public_description": "城市道路网络",
             "access_modes": ["ground_navigation", "aerial_navigation"], "resource_capacity": 2},
            {"id": "assembly_candidate_001", "kind": "assembly_candidate",
             "public_description": "候选集合位置", "access_modes": ["ground_navigation"],
             "resource_capacity": 1},
            {"id": "assembly_candidate_002", "kind": "assembly_candidate",
             "public_description": "候选集合位置", "access_modes": ["ground_navigation"],
             "resource_capacity": 1},
        ],
    }


def test_episode_binding_freezes_the_formerly_runtime_picked_parameters():
    binding = build_episode_binding(_binding_map(), people_count=4, seed=7)
    report = review_episode_binding(binding, _binding_map())
    assert report["validated"] and report["runtime_admission_pending"] is True
    assert report["execution_validated"] is False
    roster = binding["affected_person_roster"]
    assert [row["person_id"] for row in roster] == [f"affected_person_{index:02d}" for index in range(4)]
    # Per-person assembly binding replaces "pick waypoints[1] at run time".
    # 阶段19 起为就近最优匹配：合成图 4 人只有 2 个区，最近者按距离分配
    # （前 min(N,人数) 个最近者各得其一，其余复用最近）。
    assemblies = {row["assembly_region_id"] for row in roster}
    assert assemblies <= {"assembly_candidate_001", "assembly_candidate_002"} and len(assemblies) >= 1
    for row in roster:
        route = row["evacuation_route"]
        assert route[0] == row["start_xyz"]
        assert len(route[-1]) == 3
    capacity = {row["region_id"]: row for row in binding["assembly_capacity"]}
    assert set(capacity) == assemblies
    for region_id, row in capacity.items():
        assert row["capacity_people"] == len(row["bound_person_ids"])
        assert sorted(row["bound_person_ids"]) == sorted(
            person["person_id"] for person in roster if person["assembly_region_id"] == region_id)
        assert row["radius_m"] == 0.75
    hazard = binding["dynamic_hazard_boundaries"]
    assert hazard["exclusion_half_extents_m"] == [4.0, 4.0]
    assert hazard["z_window_above_road_m"] == [0.05, 3.0]
    assert hazard["incident_seed"] == "freeze_at_episode_instantiation"
    ingress = binding["safe_ingress_control_points"]
    assert [tuple(offset) for offset in ingress["candidate_offsets_m"]] == [
        (4.0, 0.0), (-4.0, 0.0), (0.0, 4.0), (0.0, -4.0), (6.0, 6.0), (-6.0, -6.0)]
    assert ingress["probe_half_extents_xy_m"] == [1.0, 1.0]
    window = binding["following_traffic_response_window"]
    assert window["control_ack_deadline_s"] == 10.0
    assert window["trajectory_reaction_window_s"] == 5.0
    assert window["control_modes"] == ["hold", "divert", "release"]
    endpoint = binding["response_service_endpoint"]
    assert endpoint["service_id"] == "city_dispatch"
    assert endpoint["receipt_deadline_s"] == 15.0
    assert endpoint["continuous_access_min_span_s"] == 0.5


def test_episode_binding_is_deterministic_for_the_frozen_seed():
    assert build_episode_binding(_binding_map()) == build_episode_binding(_binding_map(), people_count=4, seed=7)
    different = build_episode_binding(_binding_map(), seed=8)
    assert different["seed"] == 8
    assert different["affected_person_roster"][0]["start_xyz"] != \
        build_episode_binding(_binding_map())["affected_person_roster"][0]["start_xyz"]


def test_authoring_record_carries_the_binding_and_bound_parameter_names():
    semantic_map = _binding_map()
    public = build_public_task(semantic_map)
    record = authoring_record(public, {}, [], semantic_map)
    assert record["required_episode_parameters"] == episode_parameter_names()
    assert set(record["required_episode_parameters"]) == {
        "affected_person_roster", "dynamic_hazard_boundaries", "assembly_capacity",
        "safe_ingress_control_points", "following_traffic_response_window", "response_service_endpoint"}
    assert review_episode_binding(record["episode_binding"], semantic_map)["validated"]
    assert record["full_mission_witnesses"] == []
    assert "declaration_level_witnesses" in record["witness_status"]
    # Without an authored map there is nothing to bind; the names stay explicit.
    bare = authoring_record(public, {}, [])
    assert bare["episode_binding"] is None
    assert bare["required_episode_parameters"] == episode_parameter_names()


@pytest.mark.parametrize("mutation", [
    lambda b: b["affected_person_roster"].pop(),
    lambda b: b["affected_person_roster"][1].update(person_id=b["affected_person_roster"][0]["person_id"]),
    lambda b: b["affected_person_roster"][0].update(
        assembly_region_id="assembly_candidate_001"
        if b["affected_person_roster"][0]["assembly_region_id"] != "assembly_candidate_001"
        else "assembly_candidate_002"),
    lambda b: b["affected_person_roster"][0]["evacuation_route"].reverse(),
    lambda b: b["affected_person_roster"][0].update(walk_speed_mps=3.0),
    lambda b: b["assembly_capacity"][0].update(capacity_people=99),
    lambda b: b["assembly_capacity"].pop(),
    lambda b: b["assembly_capacity"][0].update(center_xy_m=[13.0, 6.0]),
    lambda b: b["dynamic_hazard_boundaries"].update(exclusion_half_extents_m=[0.0, 4.0]),
    lambda b: b["safe_ingress_control_points"].update(candidate_offsets_m=[]),
    lambda b: b["following_traffic_response_window"].update(control_modes=["hold"]),
    lambda b: b["response_service_endpoint"].update(service_id=""),
    lambda b: b.update(status="runtime_selected_candidate_diagnostic"),
    lambda b: b.update(schema="eai.city.episode_binding", version=2),
])
def test_tampered_or_runtime_selected_bindings_are_rejected(mutation):
    binding = build_episode_binding(_binding_map())
    mutation(binding)
    with pytest.raises(ValueError, match="episode binding rejected"):
        review_episode_binding(binding, _binding_map())


def test_binding_must_match_the_authored_map_it_claims():
    binding = build_episode_binding(_binding_map())
    changed_map = _binding_map()
    changed_map["bindings"][0]["geometry"]["coordinates"] = [42.0, 6.0]
    with pytest.raises(ValueError, match="episode binding rejected"):
        review_episode_binding(binding, changed_map)


# ---------------------------------------------------------------------------
# Full city goal bundle: declaration-level witnesses and business review.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def city_bundle():
    return build_goal_task_package("city")


def _witness(city_bundle, index):
    return deepcopy(city_bundle["hidden_evaluator"]["witness_plans"][index])


def _remove_task(witness, task_id):
    plan = witness["plan"]
    removed = next(row for row in plan["tasks"] if row["task_id"] == task_id)
    plan["tasks"] = [row for row in plan["tasks"] if row["task_id"] != task_id]
    for row in plan["tasks"]:
        if task_id in row["prerequisites"]:
            row["prerequisites"] = sorted(
                (set(row["prerequisites"]) - {task_id}) | set(removed["prerequisites"]))
    for outcome, references in witness["outcome_support"].items():
        if task_id in references:
            witness["outcome_support"][outcome] = [
                ref for ref in references if ref != task_id] or list(removed["prerequisites"])


def test_city_bundle_has_two_semantically_distinct_legal_witnesses(city_bundle):
    public = city_bundle["public_task"]
    assert public["mission"]["scene_key"] == "city"
    assert {row["id"] for row in public["required_outcomes"]} == {
        "incident_assessed", "exposed_people_accounted_for",
        "secondary_traffic_protected", "response_access_preserved"}
    witnesses = city_bundle["hidden_evaluator"]["witness_plans"]
    assert len(witnesses) == 2
    assert len({semantic_goal_plan_signature(witness["plan"]) for witness in witnesses}) == 2
    assert len({tuple(sorted(w["plan"]["assigned_team"])) for w in witnesses}) == 2
    assert validate_goal_bundle(city_bundle)["validated"]
    gate = city_bundle["hidden_evaluator"]["release_gate"]
    assert gate["authoring_status"] == "passed"
    assert gate["execution_status"] == "external_pending"
    assert gate["benchmark_ready"] is False and gate["scoreable"] is False
    heterogeneity = build_goal_heterogeneity_certificate(public, witnesses)
    for row in heterogeneity["witnesses"]:
        assert row["cross_mobility_edges"]
        assert {counter["removed_mobility"] for counter in row["counterfactuals"]} == {"aerial", "ground"}
        assert all(counter["invalidated_outcome_ids"] for counter in row["counterfactuals"])


def test_city_witnesses_bind_the_full_frozen_roster_to_bound_assemblies(city_bundle):
    for witness in city_bundle["hidden_evaluator"]["witness_plans"]:
        guided = {}
        for task in witness["plan"]["tasks"]:
            if task["skill"] != "guide_people":
                continue
            for person_id in task["arguments"]["person_ids"]:
                assert person_id not in guided
                guided[person_id] = task["arguments"]["assembly_region_id"]
        assert guided == _CITY_AFFECTED_PERSON_ASSEMBLY
        controlled = {vehicle for task in witness["plan"]["tasks"]
                      if task["skill"] == "control_following_traffic"
                      for vehicle in task["arguments"]["vehicle_ids"]}
        assert controlled == set(_CITY_FOLLOWING_VEHICLE_IDS)
        reports = [task for task in witness["plan"]["tasks"] if task["skill"] == "report_incident"]
        assert len(reports) == 1
        assert reports[0]["arguments"]["recipient_service_id"] == "city_dispatch"
        # The declared submission time must agree with the serial execution window.
        report = reports[0]
        assert report["arguments"]["time_s"] == report["execution"]["start_s"]


@pytest.mark.parametrize("witness_index", [0, 1])
@pytest.mark.parametrize("mutation,code", [
    (lambda w: _remove_task(w, next(t["task_id"] for t in w["plan"]["tasks"]
                                    if t["skill"] == "guide_people" and
                                    t["arguments"]["person_ids"][0].endswith("02"))),
     "city:full_roster_guided"),
    (lambda w: _remove_task(w, next(t["task_id"] for t in w["plan"]["tasks"]
                                    if t["skill"] == "control_following_traffic")),
     "city:traffic_control_present"),
    (lambda w: _remove_task(w, next(t["task_id"] for t in w["plan"]["tasks"]
                                    if t.get("arguments", {}).get("observation_mode") == "controlled_traffic_readback")),
     "city:measured_traffic_readback_present"),
    (lambda w: _remove_task(w, next(t["task_id"] for t in w["plan"]["tasks"]
                                    if t["skill"] == "report_incident")),
     "city:continuous_access_and_report_present"),
    (lambda w: _remove_task(w, next(t["task_id"] for t in w["plan"]["tasks"]
                                    if t.get("arguments", {}).get("observation_mode") == "response_access_clearance")),
     "city:continuous_access_and_report_present"),
    (lambda w: next(t for t in w["plan"]["tasks"] if t["skill"] == "control_following_traffic")
        ["arguments"].update(vehicle_ids=["random_vehicle_99"]),
     "city:control_covers_frozen_following_traffic"),
    (lambda w: next(t for t in w["plan"]["tasks"] if t["skill"] == "guide_people")
        ["arguments"].update(assembly_region_id="traffic_control_point"),
     "city:guidance_targets_public_assembly_candidate|city:person_bound_assembly_respected"),
    (lambda w: next(t for t in w["plan"]["tasks"] if t["skill"] == "guide_people")
        ["arguments"].update(person_ids=["intruder_person"]),
     "city:guidance_references_frozen_roster"),
    (lambda w: next(t for t in w["plan"]["tasks"] if t["skill"] == "report_incident")
        ["arguments"].update(observation_refs=["observe_incident_unbound"]),
     "city:report_cites_continuous_access_check"),
    (lambda w: next(t for t in w["plan"]["tasks"] if t["skill"] == "guide_people")
        ["arguments"].update(observation_refs=["observe_incident_unbound"]),
     "city:guidance_cites_people_observation"),
])
def test_city_business_review_rejects_witness_shortcuts(city_bundle, witness_index, mutation, code):
    public = city_bundle["public_task"]
    witness = _witness(city_bundle, witness_index)
    mutation(witness)
    with pytest.raises(ValueError, match=code):
        review_goal_business_witness(public, witness)


def test_city_business_review_accepts_id_renamed_witness_plans(city_bundle):
    public = city_bundle["public_task"]
    witness = _witness(city_bundle, 1)
    mapping = {row["task_id"]: f"participant_step_{index}"
               for index, row in enumerate(witness["plan"]["tasks"])}
    for row in witness["plan"]["tasks"]:
        row["task_id"] = mapping[row["task_id"]]
        row["prerequisites"] = [mapping[ref] for ref in row["prerequisites"]]
        if "observation_refs" in row["arguments"]:
            row["arguments"]["observation_refs"] = [
                mapping.get(ref, ref) for ref in row["arguments"]["observation_refs"]]
    for outcome, refs in witness["outcome_support"].items():
        witness["outcome_support"][outcome] = [mapping[ref] for ref in refs]
    report = review_goal_business_witness(public, witness)
    assert report["validated"]
    assert report["runtime_outcomes_validated"] is False


def test_city_factor_pair_isolates_the_communication_treatment():
    control = build_goal_task_package("city")
    variant = build_goal_task_package("city", factor_level="delayed")
    pair = validate_goal_factor_pair(control, variant, factor_id="communication_quality")
    assert pair["factor_effect_validated"] is False and pair["execution_validated"] is False
    drifted = build_goal_task_package("city", factor_level="delayed")
    drifted["public_task"]["budgets"]["message_bytes"] += 1
    for name in ("participant_api", "hidden_evaluator", "baseline_manifest"):
        drifted[name]["public_task_sha256"] = canonical_sha256(drifted["public_task"])
    with pytest.raises(ValueError, match="frozen"):
        validate_goal_factor_pair(control, drifted, factor_id="communication_quality")


def test_city_full_bundle_can_be_frozen_and_revalidated(city_bundle):
    packages = {"city/immediate": city_bundle}
    sources = {"city": {"revision": "local-authoring-city-v1",
                        "files": {"tmp/declared-city-freeze.usd": "a" * 64}}}
    manifest = build_goal_freeze_manifest(
        packages, split_assignments={key: "reserved" for key in packages}, source_bindings=sources)
    assert validate_goal_freeze_manifest(manifest, packages)["validated"]
    assert manifest["per_scene"]["city"]["holdout_status"] == "reserved_candidate"
    assert manifest["held_out_validated"] is False
    tampered = deepcopy(packages)
    tampered["city/immediate"]["hidden_evaluator"]["witness_plans"][0]["plan"]["tasks"][0][
        "arguments"]["route_ids"] = []
    with pytest.raises(ValueError):
        build_goal_freeze_manifest(
            tampered, split_assignments={key: "reserved" for key in tampered}, source_bindings=sources)


# ---------------------------------------------------------------------------
# Suite admission: the draft stays a draft while the full bundle is audited.
# ---------------------------------------------------------------------------

def _png():
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xfe")) + chunk(b"IEND", b""))


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")


def _public():
    outcomes = ["incident_assessed", "exposed_people_accounted_for",
                "secondary_traffic_protected", "response_access_preserved"]
    return {
        "schema": "eai.benchmark.goal_task_spec", "version": 2,
        "task_id": "CITY-TEST-01", "evaluation_track": "goal_driven_team_planning_v1",
        "mission": {"scene_key": "city", "goal": "Protect people and following traffic",
                    "context": "Urban incident response"},
        "required_outcomes": [{"id": name, "description": name, "acceptance": "Independent current evidence",
                               "evidence_expectations": ["measured_state"]} for name in outcomes],
        "candidate_robot_pool": [
            {"id": "go2_1", "type": "go2", "mobility": "ground",
             "public_capabilities": ["ground_navigation", "relay"], "interfaces": ["velocity_command", "event_bus"],
             "physical_profile": {"configuration_id": "city_go2_navigation_v1", "body_size_m": [0.8, 0.5, 0.8],
                                  "effective_payload_kg": 0.0, "reach_m": 0.0, "grasp_interfaces": [],
                                  "max_step_height_m": 0.22}},
            {"id": "iris_1", "type": "iris", "mobility": "aerial",
             "public_capabilities": ["aerial_navigation", "observe", "relay"], "interfaces": ["camera", "event_bus"],
             "physical_profile": {"configuration_id": "city_iris_camera_v1", "body_size_m": [0.6, 0.6, 0.3],
                                  "effective_payload_kg": 0.0, "reach_m": 0.0, "grasp_interfaces": [],
                                  "max_step_height_m": 0.0}},
        ],
        "skill_catalog_version": "eai.skills.v2",
        "execution_contract": {
            "schema": "eai.benchmark.reliable_skills", "version": 1, "parameter_basis": "benchmark_defined",
            "scene_binding_status": "pending", "guarantee": "conditional_deterministic",
            "collision_policy": "hold_and_report", "observation_policy": "exact_within_declared_visibility",
            "clearance_margin_m": 0.05,
            "skill_durations_s": {"ground_navigation": 8.0, "aerial_navigation": 8.0, "stair_traversal": 12.0,
                                  "observe": 4.0, "pick": 4.0, "place": 8.0, "handoff": 4.0,
                                  "cooperative_carry": 16.0, "relay": 1.0, "guide_people": 10.0,
                                  "control_following_traffic": 6.0, "report_incident": 2.0, "synchronize": 2.0}},
        "public_world": {
            "regions": _binding_map()["regions"],
            "objects": [], "vertical_links": [],
            "public_notes": "Static prior; current actors require observations",
            "routes": [{"id": "road_link", "from_region_id": "road_surface", "to_region_id": "road_surface",
                        "access_modes": ["ground_navigation"], "length_m": 1.0, "clearance_m": [4.0, 4.0],
                        "max_step_height_m": 0.0, "resource_id": "road", "capacity": 1}],
        },
        "business_constraints": ["Requests do not establish successful outcomes"],
        "budgets": {"simulation_s": 180.0, "planning_s": 30.0, "message_bytes": 8192,
                    "model_tokens": 12000, "max_active_agents": 2},
        "experiment_factor": {"id": "communication_quality", "level": "immediate",
                              "public_rule": "Only delivered evidence",
                              "frozen_inputs": ["scene_layout", "incident_instance", "actor_initial_states",
                                                "candidate_robot_pool", "budgets", "model_budget"]},
    }


def _city_package(tmp_path):
    from demo.benchmark.goal_driven_contract import build_goal_participant_api
    from demo.benchmark.goal_driven_fixtures import execution_contract

    root = tmp_path / "prepared"
    sources = tmp_path / "city_assets"
    sources.mkdir()
    for name in ("city_layout.json", "city_scenario.json", "city_static.usd", "building.usd"):
        (sources / name).write_text("local test source: " + name, encoding="utf-8")
    public = _public()
    public["execution_contract"] = execution_contract()
    semantics = {
        "schema": "eai.city.semantic_map", "version": 1, "coordinate_frame": "city_world",
        "meters_per_unit": 1.0, "up_axis": "Z", "regions": deepcopy(public["public_world"]["regions"]),
        "bindings": _binding_map()["bindings"],
        "static_obstacles": [],
        "vehicle_routes": [], "pedestrian_routes": _binding_map()["pedestrian_routes"],
        "surface_heights_m": _binding_map()["surface_heights_m"], "scope": "static_prior_only",
    }
    metadata = {
        "schema": "eai.city.static_map", "version": 1, "coordinate_frame": "city_world",
        "meters_per_unit": 1.0, "up_axis": "Z", "resolution_m": 0.25,
        "origin_xyz_m": [0.0, 0.0, 0.0], "width": 1, "height": 1,
        "status": "static_geometry_candidate", "navigation_validated": False,
    }
    required = []
    for name, public_skill, outcome in (("guide_people", "guide_people", "exposed_people_accounted_for"),
                                        ("control_traffic", "control_following_traffic", "secondary_traffic_protected"),
                                        ("report_incident", "report_incident", "response_access_preserved")):
        required.append({"id": name, "status": "public_skill_registered_runtime_pending",
                         "participant_operation": name if name != "control_traffic" else "control_following_traffic",
                         "required_public_skill": public_skill, "arguments": ["current_evidence"],
                         "preconditions": ["fresh_evidence"], "effects": ["service_request"],
                         "failure_codes": ["unavailable"], "evidence_types": ["independent_receipt"],
                         "cancel_semantics": "safe_hold", "timeout_semantics": "bounded", "owner": "local_adapter",
                         "outcome_ids": [outcome]})
    private = {
        "schema": "eai.city.authoring_private", "version": 1, "task_id": public["task_id"],
        "public_task_sha256": canonical_sha256(public),
        "source_provenance": {
            "source_sha256": {name: hashlib.sha256((sources / name).read_bytes()).hexdigest()
                              for name in ("city_layout.json", "city_scenario.json", "city_static.usd")},
            "composed_usd_layers_sha256": {str(sources / name): hashlib.sha256((sources / name).read_bytes()).hexdigest()
                                           for name in ("city_static.usd", "building.usd")},
        },
        "static_prim_bindings": [],
        "incident_template": {"seed": 7, "selected_target": None, "visibility": "author_only"},
        "response_template": {"people": [{"person_id": "secret_affected_person_1", "start_xyz": [1.0, 2.0, 0.0]}]},
        "outcome_evidence_requirements": deepcopy(public["required_outcomes"]),
        "full_mission_witnesses": [], "required_services": required,
        "episode_binding": build_episode_binding(semantics),
        "private_factor_design": {"communication": "freeze content, vary delivery", "frozen": ["map", "actor_states"]},
        "required_episode_parameters": episode_parameter_names(),
    }
    review = {"schema": "eai.city.authoring_review", "version": 1, "public_contract_validated": True,
              "authoring_status": "blocked", "execution_validated": False, "scoreable": False,
              "valid_full_mission_plan_count": 0,
              "blockers": ["two_causally_complete_feasible_plans"]}
    navigation = {"schema": "eai.city.navigation_handoff", "version": 1,
                  "ground_map": "release/maps/occupancy.yaml", "coordinate_frame": "city_world",
                  "aerial_static_bounds": "release/maps/semantic_map.json:static_obstacles",
                  "start_reviews": [{"agent_id": row["id"], "physical_spawn_validated": False}
                                    for row in public["candidate_robot_pool"]], "navigation_ready": False}
    manifest = {
        "schema": "eai.city.integration_manifest", "version": 1, "scene_key": "city",
        "task_family": "urban_incident_response", "selection_scaffold_scene_key": "modern_house_nvd",
        "package_kind": "preparation_draft_not_goal_bundle", "release_status": "authoring_draft",
        "execution_validated": False, "scoreable": False,
        "public_task_sha256": canonical_sha256(public),
        "public_map_semantics_sha256": canonical_sha256(semantics),
        "public_inputs": ["release/public_task.json", "release/participant_api.json", "release/maps/"],
        "private_inputs": ["authoring/private.json", "authoring/review.json"],
    }
    for name, value in {"release/public_task.json": public,
                        "release/participant_api.json": build_goal_participant_api(public),
                        "release/maps/semantic_map.json": semantics, "release/maps/map_metadata.json": metadata,
                        "authoring/private.json": private, "authoring/review.json": review,
                        "authoring/navigation_config.json": navigation, "integration_manifest.json": manifest}.items():
        _write_json(root / name, value)
    for name in ("occupancy.png", "map_preview.png"):
        (root / "release/maps" / name).write_bytes(_png())
    (root / "release/maps/occupancy.yaml").write_text(
        "image: occupancy.png\nresolution: 0.25\norigin: [0.0, 0.0, 0.0]\nnegate: 0\n"
        "occupied_thresh: 0.65\nfree_thresh: 0.196\n", encoding="utf-8")
    manifest["artifact_sha256"] = {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file() and path.name != "integration_manifest.json"}
    _write_json(root / "integration_manifest.json", manifest)
    return root


@pytest.fixture(scope="module")
def city_suite(tmp_path_factory):
    base = tmp_path_factory.mktemp("city-formal-binding")
    package = _city_package(base)
    root = base / "suite"
    summary = write_goal_suite(root, scene_keys=["city"], city_package_dir=package)
    assert summary["package_count"] == 0 and summary["candidate_count"] == 1
    return root, package


def test_city_suite_audits_the_full_bundle_and_keeps_the_draft_blocked(city_suite):
    root, package = city_suite
    assert verify_goal_suite(root) == {
        "validated": True, "package_count": 0, "candidate_count": 1,
        "factor_pair_count": 0, "scoreable": False, "held_out_validated": False}
    report = json.loads((root / "audit_report.json").read_text(encoding="utf-8"))
    candidate = report["candidates"][0]
    assert candidate["package_kind"] == "preparation_draft_not_goal_bundle"
    assert candidate["authoring_status"] == "blocked"
    assert candidate["full_bundle_authoring_status"] == "passed"
    assert candidate["factor_pair_status"] == "concrete_treatments_and_witnesses_available_execution_pending"
    assert candidate["scoreable"] is False
    bundle_review = json.loads((root / "authoring/city_goal_bundle_review.json").read_text(encoding="utf-8"))
    assert bundle_review["authoring_status"] == "passed"
    assert bundle_review["execution_status"] == "external_pending"
    factor_pair = json.loads((root / "authoring/city_factor_pair.json").read_text(encoding="utf-8"))
    assert factor_pair["control_level"] == "immediate" and factor_pair["variant_level"] == "delayed"
    # The audited bundle is author material: no witness escapes into the draft.
    draft = json.loads((root / "candidates/city/authoring/private.json").read_text(encoding="utf-8"))
    assert draft["full_mission_witnesses"] == []
    intake = load_city_goal_candidate(package)
    assert intake["intake_review"]["episode_parameters_bound"] is True
    assert intake["episode_binding_review"]["validated"] is True


def test_city_suite_rejects_a_stale_full_bundle_audit(city_suite):
    root, _ = city_suite
    path = root / "authoring/city_goal_bundle_review.json"
    review = json.loads(path.read_text(encoding="utf-8"))
    review["distinct_plan_count"] = 1
    _write_json(path, review)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    report_path = root / "audit_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["artifact_sha256"][path.relative_to(root).as_posix()] = digest
    _write_json(report_path, report)
    with pytest.raises(ValueError, match="goal bundle review is stale"):
        verify_goal_suite(root)


def test_city_suite_rejects_a_promoted_draft_record(city_suite):
    root, _ = city_suite
    report_path = root / "audit_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["candidates"][0]["package_kind"] = "goal_bundle"
    _write_json(report_path, report)
    with pytest.raises(ValueError, match="cannot be promoted"):
        verify_goal_suite(root)


def test_city_registry_still_compiles_only_the_draft_candidate(city_suite):
    from demo.benchmark.goal_scene_registry import compile_registered_goal_scene, goal_scene_registration
    _, package = city_suite
    assert goal_scene_registration("city")["package_kind"] == "preparation_draft_not_goal_bundle"
    compiled = compile_registered_goal_scene("city", city_package_dir=package)
    assert "candidate" in compiled and "bundle" not in compiled
    assert compiled["candidate"]["intake_review"]["episode_parameters_bound"] is True
    with pytest.raises(ValueError, match="prepared package"):
        compile_registered_goal_scene("city")
    with pytest.raises(ValueError, match="hidden treatments"):
        compile_registered_goal_scene("city", city_package_dir=package, factor_level="delayed")
