"""City goal roster binding must agree with the native episode, not a witness guess."""

from copy import deepcopy

import pytest

from demo.benchmark.goal_city_native_binding import review_city_goal_native_binding
from demo.benchmark.goal_driven_contract import (
    validate_goal_bundle, validate_goal_participant_api, validate_goal_task_spec,
)
from demo.benchmark.goal_driven_tasks import (
    build_goal_task_package,
    build_goal_task_spec,
    review_goal_business_witness,
)
from demo.benchmark.provenance import canonical_sha256
from demo.benchmark.goal_session import GoalSession
from demo.benchmark.skill_ontology import public_skill_catalog
from demo.city_benchmark.contracts import build_episode_binding


def _semantic_map():
    centers = {f"assembly_candidate_{index:03d}": [float(index * 10), 6.0]
               for index in range(1, 5)}
    route_centers = (centers["assembly_candidate_003"], centers["assembly_candidate_001"],
                     centers["assembly_candidate_002"], centers["assembly_candidate_004"])
    return {
        "surface_heights_m": {"sidewalks": 0.3, "ground": 0.22},
        "pedestrian_routes": [
            {"id": f"p{index}", "points": [[center[0] - 2., center[1]],
                                            [center[0] - 1., center[1]]]}
            for index, center in enumerate(route_centers)
        ],
        "bindings": [{"id": identifier, "candidate_radius_m": .75,
                      "geometry": {"type": "Point", "coordinates": center}}
                     for identifier, center in centers.items()],
        "regions": [{"id": identifier, "kind": "assembly_candidate",
                     "public_description": "assembly", "access_modes": ["ground_navigation"],
                     "resource_capacity": 1} for identifier in centers],
    }


def _native_inputs():
    semantic_map = _semantic_map()
    binding = build_episode_binding(semantic_map, people_count=4, seed=7)
    person_ids = [row["person_id"] for row in binding["affected_person_roster"]]
    assembly_ids = [row["region_id"] for row in binding["assembly_capacity"]]
    vehicles = [f"following_vehicle_{index:02d}" for index in range(6)]
    report = {"schema": "eai.city.native_service_probe_v1", "status": "blocked",
              "geometry_scope": "author_frozen_episode_binding", "episode_id": "episode",
              "run_id": "episode", "trace": {
                  "geometry": {"roster_ids": person_ids, "assembly_regions": {
                      identifier: {} for identifier in assembly_ids}, "vehicle_ids": list(vehicles)},
                  "services": {"traffic_hold": {
                      "request": {"entity_ids": list(vehicles)},
                      "review": {"entity_ids": list(vehicles)}}}}}
    return {"episode_binding": binding}, semantic_map, report


def test_native_roster_profile_has_distinct_legal_witnesses_and_keeps_default():
    default = build_goal_task_package("city")
    assert default == build_goal_task_package("city", city_roster_profile="abstract_v1")
    assert canonical_sha256(default["public_task"]) == (
        "51423656d67d350023c1d3e6fe2c7981383c806ca9fd8b3f37030be3757ec57f")
    assert default["public_task"]["skill_catalog_version"] == "eai.skills.v2"
    assert default["participant_api"]["skill_catalog"] == public_skill_catalog()
    native = build_goal_task_package("city", city_roster_profile="bound_native_v1")
    assert native["public_task"]["task_id"] == "CITY-GOAL-NATIVE-01"
    assert native["public_task"]["skill_catalog_version"] == "eai.skills.v3"
    assert native["participant_api"]["skill_catalog"] == public_skill_catalog(version=3)
    assert native["participant_api"]["skill_catalog"]["skills"]["guide_people"][
        "terminal_semantics"] == "all_targets_acknowledged_by_native_service_while_field_role_held"
    assert native["public_task"]["execution_contract"]["skill_durations_s"]["guide_people"] == 20.
    assert validate_goal_bundle(native)["validated"]
    assert build_goal_task_spec("city", city_roster_profile="bound_native_v1") == native["public_task"]
    for witness in native["hidden_evaluator"]["witness_plans"]:
        guided = {person: row["arguments"]["assembly_region_id"]
                  for row in witness["plan"]["tasks"] if row["skill"] == "guide_people"
                  for person in row["arguments"]["person_ids"]}
        assert guided == {
            "affected_person_00": "assembly_candidate_004",
            "affected_person_01": "assembly_candidate_001",
            "affected_person_02": "assembly_candidate_003",
            "affected_person_03": "assembly_candidate_002",
        }
        controls = [row for row in witness["plan"]["tasks"]
                    if row["skill"] == "control_following_traffic"]
        assert len(controls) == 1 and len(controls[0]["arguments"]["vehicle_ids"]) == 6
        assert [row["execution"]["duration_s"] for row in witness["plan"]["tasks"]
                if row["skill"] == "guide_people"] == [20.] * 4
        report = next(row for row in witness["plan"]["tasks"]
                      if row["skill"] == "report_incident")
        assert report["arguments"]["time_s"] == report["execution"]["start_s"]
        assert report["execution"]["start_s"] + report["execution"]["duration_s"] <= 180.
    with pytest.raises(ValueError, match="applies only"):
        build_goal_task_package("warehouse_nvd", city_roster_profile="bound_native_v1")
    with pytest.raises(ValueError, match="unsupported City roster profile"):
        build_goal_task_package("city", city_roster_profile="unregistered")


def test_native_catalog_versions_fail_closed_without_changing_default():
    bundle = build_goal_task_package("city", city_roster_profile="bound_native_v1")
    public, api = bundle["public_task"], bundle["participant_api"]
    for task_id, scene, catalog in (("CITY-GOAL-01", "city", "eai.skills.v3"),
                                    ("OTHER-01", "city", "eai.skills.v3"),
                                    ("CITY-GOAL-NATIVE-01", "factory", "eai.skills.v3"),
                                    ("CITY-GOAL-NATIVE-01", "city", "eai.skills.v2"),
                                    ("CITY-GOAL-NATIVE-01", "city", "eai.skills.v4")):
        changed = deepcopy(public)
        changed["task_id"] = task_id
        changed["mission"]["scene_key"] = scene
        changed["skill_catalog_version"] = catalog
        with pytest.raises(ValueError, match="unsupported skill catalog"):
            validate_goal_task_spec(changed)
    for field, value in (("skill_catalog_version", "eai.skills.v2"),
                         ("skill_catalog", public_skill_catalog())):
        changed = deepcopy(api)
        changed[field] = value
        with pytest.raises(ValueError, match="skill catalog"):
            validate_goal_participant_api(changed, task_spec=public)
    with pytest.raises(ValueError, match="unsupported skill catalog version"):
        public_skill_catalog(version=4)


def test_native_goal_session_ack_terminal_does_not_validate_arrival():
    bundle = build_goal_task_package("city", city_roster_profile="bound_native_v1")
    session = GoalSession(bundle["public_task"], bundle["participant_api"], episode_id="city-test")
    session.update_runtime_state(0., {"go2_1": "road_surface"}, {})
    session.emit("observation", 0., {
        "message_id": "obs-person", "producer_id": "iris_1", "kind": "people_survey",
        "data": {"person_id": "affected_person_00"}, "valid_until_s": 25.,
    })
    task = {"task_id": "guide-one", "skill": "guide_people",
            "arguments": {"person_ids": ["affected_person_00"],
                          "observation_refs": ["obs-person"],
                          "assembly_region_id": "assembly_candidate_004"},
            "required_capabilities": ["ground_navigation", "relay"],
            "assigned_agents": ["go2_1"], "prerequisites": [], "evidence_refs": ["obs-person"],
            "execution": {"start_s": 0., "duration_s": 20.}}
    plan = {"schema": "eai.benchmark.goal_plan", "version": 3,
            "plan_id": "city-contact", "assigned_team": ["go2_1"], "tasks": [task]}

    def action(kind, payload):
        return session.submit_action({
            "schema": "eai.benchmark.goal_action", "version": 1,
            "action_id": f"{kind}-{len(session.report()['actions'])}",
            "submitted_at_s": session.report()["current_time_s"],
            "type": kind, "payload": payload,
        })

    assert action("select_team", {"agent_ids": ["go2_1"]})["accepted"]
    assert action("consume_message", {"message_id": "obs-person"})["accepted"]
    assert action("submit_plan", {"plan": plan})["accepted"]
    assert action("dispatch_skill", {"task_id": "guide-one",
                                     "evidence_refs": ["obs-person"]})["accepted"]
    dispatch_id = session.report()["tasks"]["guide-one"]["dispatch_action_id"]

    def receipt(at):
        return {"backend_id": "unit-test-native-ack", "receipt_ref": "ack:person-00",
                "episode_id": "city-test", "task_id": "guide-one", "action_id": dispatch_id,
                "status": "succeeded", "completed_at_s": at,
                "evidence_refs": ["ack:person-00"]}

    before = session.report()
    with pytest.raises(ValueError, match="execution time window"):
        session.complete_skill("guide-one", time_s=21., status="succeeded", receipt=receipt(21.))
    assert session.report() == before
    assert session.complete_skill("guide-one", time_s=15., status="succeeded",
                                  receipt=receipt(15.))["accepted"]
    report = session.report()
    assert report["tasks"]["guide-one"]["status"] == "succeeded"
    assert report["business_outcomes_validated"] is False
    assert report["execution_validated"] is False and report["scoreable"] is False


def test_native_binding_requires_full_author_and_runtime_identity():
    bundle = build_goal_task_package("city", city_roster_profile="bound_native_v1")
    private, semantic_map, report = _native_inputs()
    result = review_city_goal_native_binding(bundle, private, semantic_map, report)
    assert result["identity_binding_validated"]
    assert (result["person_count"], result["assembly_count"],
            result["following_vehicle_count"]) == (4, 4, 6)
    assert result["execution_validated"] is False and result["scoreable"] is False

    with pytest.raises(ValueError, match="opt-in"):
        review_city_goal_native_binding(build_goal_task_package("city"),
                                        private, semantic_map, report)
    missing_vehicle = deepcopy(report)
    missing_vehicle["trace"]["geometry"]["vehicle_ids"].pop()
    with pytest.raises(ValueError, match="traffic request/readback"):
        review_city_goal_native_binding(bundle, private, semantic_map, missing_vehicle)
    missing_person = deepcopy(report)
    missing_person["trace"]["geometry"]["roster_ids"].pop()
    with pytest.raises(ValueError, match="native person roster"):
        review_city_goal_native_binding(bundle, private, semantic_map, missing_person)
    duplicate_person = deepcopy(report)
    duplicate_person["trace"]["geometry"]["roster_ids"].append(
        duplicate_person["trace"]["geometry"]["roster_ids"][0])
    with pytest.raises(ValueError, match="native person roster"):
        review_city_goal_native_binding(bundle, private, semantic_map, duplicate_person)
    foreign_assembly = deepcopy(private)
    foreign_assembly["episode_binding"]["affected_person_roster"][0][
        "assembly_region_id"] = "assembly_candidate_002"
    with pytest.raises(ValueError, match="episode binding rejected|witness differs"):
        review_city_goal_native_binding(bundle, foreign_assembly, semantic_map, report)


def test_native_witness_business_review_rejects_roster_and_vehicle_tampering():
    bundle = build_goal_task_package("city", city_roster_profile="bound_native_v1")
    public = bundle["public_task"]
    witness = deepcopy(bundle["hidden_evaluator"]["witness_plans"][0])
    guide = next(row for row in witness["plan"]["tasks"] if row["skill"] == "guide_people")
    guide["arguments"]["assembly_region_id"] = "assembly_candidate_002"
    with pytest.raises(ValueError, match="person_bound_assembly_respected"):
        review_goal_business_witness(public, witness)
    witness = deepcopy(bundle["hidden_evaluator"]["witness_plans"][0])
    control = next(row for row in witness["plan"]["tasks"]
                   if row["skill"] == "control_following_traffic")
    control["arguments"]["vehicle_ids"].pop()
    with pytest.raises(ValueError, match="control_covers_frozen_following_traffic"):
        review_goal_business_witness(public, witness)


def test_dense_native_guidance_trace_separates_ack_from_later_arrival():
    bundle = build_goal_task_package("city", city_roster_profile="bound_native_v1")
    private, semantic_map, report = _native_inputs()
    for person in private["episode_binding"]["affected_person_roster"]:
        region_id, person_id = person["assembly_region_id"], person["person_id"]
        center = next(row["center_xy_m"] for row in private["episode_binding"]["assembly_capacity"]
                      if row["region_id"] == region_id)
        samples = [{"time_s": 5. + index * .02,
                    "position_xyz": [center[0] + (0. if index >= 1100 else 5.), center[1], .3]}
                   for index in range(1501)]
        report["trace"]["services"][f"guide_people:{region_id}"] = {
            "request": {"request_id": f"request:{person_id}", "service": "guide_people",
                        "entity_ids": [person_id], "agent_id": "go2_1",
                        "target_region_id": region_id, "submitted_time_s": 4.},
            "contact": {"request_id": f"request:{person_id}", "agent_id": "go2_1",
                        "status": "contact_confirmed", "ack_time_s": 5.,
                        "evidence_refs": [f"ack:{person_id}"],
                        "actor_acks": [{"id": person_id, "ack_time_s": 5.,
                                        "ack_evidence_ref": f"ack:{person_id}"}],
                        "business_outcome_validated": False, "scoreable": False},
            "review": {"status": "evidence_complete", "evidence": {"actors": [
                {"id": person_id, "ack_time_s": 5.,
                 "ack_evidence_ref": f"ack:{person_id}"}]}},
            "readback": {person_id: {"native_readback": {"trajectory_samples": samples}}},
        }
    review = review_city_goal_native_binding(bundle, private, semantic_map, report)
    assert review["temporal_witness_review"]["status"] == "arrival_after_contact_window"
    assert len(review["temporal_witness_review"]["arrival_later_person_ids"]) == 4
    assert review["ack_window_review"]["status"] == "not_disproved_by_native_ack"
    assert review["execution_validated"] is False

    pending_arrival = deepcopy(report)
    for row in pending_arrival["trace"]["services"].values():
        if row.get("contact"):
            row["review"] = {"status": "pending"}
    assert review_city_goal_native_binding(bundle, private, semantic_map, pending_arrival)[
        "ack_window_review"]["status"] == "not_disproved_by_native_ack"

    late_ack = deepcopy(report)
    region_id = private["episode_binding"]["affected_person_roster"][0]["assembly_region_id"]
    late_ack["trace"]["services"][f"guide_people:{region_id}"]["contact"]["ack_time_s"] = 25.
    assert review_city_goal_native_binding(bundle, private, semantic_map, late_ack)[
        "ack_window_review"]["status"] == "unassessed"
    late_ack["trace"]["services"][f"guide_people:{region_id}"]["contact"][
        "actor_acks"][0]["ack_time_s"] = 25.
    assert review_city_goal_native_binding(bundle, private, semantic_map, late_ack)[
        "ack_window_review"]["status"] == "blocked_by_native_ack_window"

    forged_ref = deepcopy(report)
    forged_ref["trace"]["services"][f"guide_people:{region_id}"]["contact"][
        "evidence_refs"] = ["other-ack"]
    assert review_city_goal_native_binding(bundle, private, semantic_map, forged_ref)[
        "ack_window_review"]["status"] == "unassessed"

    gapped = deepcopy(report)
    person_id = private["episode_binding"]["affected_person_roster"][0]["person_id"]
    gapped["trace"]["services"][f"guide_people:{region_id}"]["readback"][person_id][
        "native_readback"]["trajectory_samples"][100]["time_s"] += 1.
    assert review_city_goal_native_binding(bundle, private, semantic_map, gapped)[
        "temporal_witness_review"]["status"] == "unassessed"
