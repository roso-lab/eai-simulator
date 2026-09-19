"""Synthetic evaluator fixtures; these do not certify an Isaac rollout."""

from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest

from demo.benchmark.domain_evaluator import (
    build_domain_contract, evaluate_domain_evidence, public_domain_requirements,
)
from demo.benchmark.provenance import canonical_sha256
from demo.benchmark.scene_challenge import generate_scene_scenario


def evidence_fixture(scene="warehouse_nvd"):
    scenario = generate_scene_scenario(scene_key=scene, task_name="evaluator_fixture", seed=1, origin=(0., 0.))
    aerial_id = next(robot["id"] for robot in scenario["robots"]
                     if robot["type"] in {"cf2x", "iris", "pegasus"})
    if scene == "modern_house_nvd":
        positions = {"furniture_parking_zone": [4., 4., 0.], "room_review_zone": [3., 0., 0.],
                     "keepsake_staging_zone": [4., -4., 0.]}
        for target in scenario["composition"]["targets"]:
            target["position"] = positions.get(target["id"], target["position"])
    contract = build_domain_contract(scenario)
    report = {"scheduling": {"states": {}, "time_s": 120.}, "events": [], "final_ownership": {},
              "pose_trajectory": {"samples": []},
              "observation_store": {"observations": [], "detections": [], "consumptions": []},
              "domain_evidence": {"contract_sha256": canonical_sha256(contract), "identity_checks": []}}
    base_positions = {e["id"]: e["spawn_pose"]["position"] for e in scenario["composition"]["entities"]}
    base_positions.update({
        robot["id"]: list(robot["spawn_pose"]["position"][:2])
        for robot in scenario["robots"]
    })
    for index, node in enumerate(scenario["phase_graph"]["nodes"]):
        report["scheduling"]["states"][node["id"]] = {
            "status": "succeeded", "started_at_s": index * 10., "finished_at_s": index * 10. + 4}

    def sample(time, updates=None):
        row = {"time_s": time, "positions": deepcopy(base_positions)}
        row["positions"].update(updates or {})
        report["pose_trajectory"]["samples"].append(row)

    def frame(identifier, time, target_ref=None):
        report["observation_store"]["observations"].append({
            "observation_id": identifier, "producer": aerial_id, "frame_id": identifier,
            "timestamp_s": time, "valid_until_s": time + 20., "target_ref": target_ref,
            "quality": {"frame_acquired": True}, "sensor_config": {"frame_sha256": "a" * 64}})

    for row in contract["deliveries"]:
        start = report["scheduling"]["states"][row["node_id"]]["started_at_s"]
        agents = ["m20_1", "carter_1"][:row["team_size"]]
        owner = agents if len(agents) > 1 else agents[0]
        report["events"].extend([
            {"event": "pickup", "node": row["node_id"], "object": row["object_id"],
             "time_s": start+1, "agents": agents, "owner_before": None, "owner_after": owner},
            {"event": "release", "node": row["node_id"], "object": row["object_id"],
             "time_s": start+2, "owner_before": owner, "owner_after": None,
             "target_ref": row["target_ref"], "object_position": row["position"],
             "confirmed_after_sim_step": True}])
        sample(start+2, {row["object_id"]: row["position"]})

    for row in contract["identity_requirements"]:
        start = report["scheduling"]["states"][row["consumer_node"]]["started_at_s"]
        obj = row["object_ids"][0]
        identifier = f"frame-{row['id']}"
        sample(start-1)
        frame(identifier, start-1)
        report["observation_store"]["detections"].append({
            "detection_id": row["id"], "target_id": obj, "source_observation_id": identifier,
            "detector_source": "synthetic_unit_fixture", "detector_version": "1",
            "estimated_position_m": base_positions[obj], "timestamp_s": start-.75})
        report["observation_store"]["consumptions"].append({
            "observation_id": identifier, "consumer": row["consumer_node"], "timestamp_s": start-.5})
        report["domain_evidence"]["identity_checks"].append({
            "requirement_id": row["id"], "detection_ids": [row["id"]]})

    final = {r["object_id"]: r["position"] for r in contract["deliveries"]}
    report["final_ownership"] = {obj: None for obj in final}
    sample(120., final)
    if scene == "warehouse_nvd":
        sample(83., final)
        frame("dock-frame", 82.5, "outbound_dock_zone")
        report["domain_service_events"] = [
            {"id": "dock-state", "kind": "dock_state", "source": "benchmark_dock_controller",
             "target_ref": "outbound_dock_zone", "state": "ready", "time_s": 82., "valid_until_s": 85.},
            {"id": "receipt", "kind": "dock_handoff", "source": "benchmark_dock_controller",
             "dock_state_id": "dock-state", "target_ref": "outbound_dock_zone", "time_s": 83.,
             "accepted_object_ids": ["priority_carton_proxy"], "observation_id": "dock-frame"}]
        report["domain_evidence"]["dock_receipt_id"] = "receipt"
    else:
        sample(29.)
        frame("before", 29., "entry_scan_zone")
        frame("after", 120., "room_review_zone")
        target_positions = {
            target["id"]: target["position"][:2]
            for target in scenario["composition"]["targets"]
        }
        report["domain_evidence"].update(access_map_sha256="test-map", access_paths={
            phase: {"time_s": time, "observation_id": phase,
                    "points": [target_positions["entry_scan_zone"],
                               target_positions["room_review_zone"]]}
            for phase, time in (("before", 29.), ("after", 120.))})
    report["pose_trajectory"]["samples"].sort(key=lambda row: row["time_s"])
    return scenario, contract, report


def open_map():
    return SimpleNamespace(sha256="test-map", resolution=.1, width=200, height=200,
                           free=np.ones((200, 200), dtype=bool), local=lambda x, y: (x+10., y+10.))


@pytest.mark.parametrize("scene", ["warehouse_nvd", "modern_house_nvd"])
def test_complete_synthetic_evidence_passes_without_physics_claim(scene):
    scenario, contract, report = evidence_fixture(scene)
    original = deepcopy(report)
    result = evaluate_domain_evidence(report, scenario=scenario, contract=contract, occupancy=open_map())
    assert result["validated"], result
    assert result["business_success"] and not result["execution_validated"]
    assert report == original


def test_scheduler_success_and_participant_booleans_cannot_certify_a_domain_task():
    scenario, _, report = evidence_fixture()
    report.pop("domain_evidence")
    report.update(success=True, safe=True, ready=True)
    result = evaluate_domain_evidence(report, scenario=scenario)
    assert not result["validated"] and result["predicate_fraction"] == 0


@pytest.mark.parametrize("corruption", ["wrong_identity", "missing_frame", "future_detection", "stale_frame", "missing_consumption", "quantity", "localization"])
def test_identity_requires_quantity_truth_frame_localization_and_causal_consumption(corruption):
    scenario, _, report = evidence_fixture()
    store = report["observation_store"]
    if corruption == "wrong_identity":
        store["detections"][0]["target_id"] = "priority_carton_proxy"
    elif corruption == "missing_frame":
        store["observations"][0]["quality"]["frame_acquired"] = False
    elif corruption == "future_detection":
        store["detections"][0]["timestamp_s"] = 1000.
    elif corruption == "stale_frame":
        store["observations"][0]["valid_until_s"] = 29.2
    elif corruption == "missing_consumption":
        store["consumptions"].clear()
    elif corruption == "quantity":
        report["domain_evidence"]["identity_checks"][0]["detection_ids"] *= 2
    else:
        store["detections"][0]["estimated_position_m"] = [100, 100, 0]
    assert not evaluate_domain_evidence(report, scenario=scenario)["validated"]


@pytest.mark.parametrize("corruption", ["one_carrier", "bad_owner", "release_before_pickup", "wrong_pose", "wrong_target", "final_drift"])
def test_delivery_uses_ownership_and_measured_positions(corruption):
    scenario, _, report = evidence_fixture()
    if corruption == "one_carrier":
        report["events"][0]["agents"] = ["m20_1"]
    elif corruption == "bad_owner":
        report["events"][1]["owner_before"] = "carter_1"
    elif corruption == "release_before_pickup":
        report["events"][1]["time_s"] = 30.
    elif corruption == "wrong_pose":
        report["events"][1]["object_position"] = [100., 100., 0.]
    elif corruption == "wrong_target":
        report["events"][1]["target_ref"] = "outbound_dock_zone"
    else:
        report["pose_trajectory"]["samples"][-1]["positions"]["priority_carton_proxy"] = [0., 0.]
    assert not evaluate_domain_evidence(report, scenario=scenario)["validated"]


def test_delivery_requires_a_pose_sample_at_the_release_state():
    scenario, contract, report = evidence_fixture()
    release = next(event for event in report["events"]
                   if event["event"] == "release"
                   and event["node"] == "oversized_crate_transfer")
    release_time = release["time_s"]
    report["pose_trajectory"]["samples"] = [
        sample for sample in report["pose_trajectory"]["samples"]
        if sample["time_s"] != release_time
    ]
    report["pose_trajectory"]["samples"].append({
        "time_s": release_time - 0.1,
        "positions": {
            **report["pose_trajectory"]["samples"][0]["positions"],
            "oversized_crate_proxy": [100.0, 100.0, 0.0],
        },
    })
    report["pose_trajectory"]["samples"].sort(key=lambda sample: sample["time_s"])

    result = evaluate_domain_evidence(report, scenario=scenario, contract=contract)
    predicate = next(row for row in result["predicates"]
                     if row["id"] == "delivery:oversized_crate_transfer")
    assert predicate["validated"] is False
    assert predicate["errors"] == ["object is not measured at its delivery target"]


@pytest.mark.parametrize("corruption", ["self_report", "wrong_order", "receipt_before_delivery", "closed_dock", "expired", "superseded"])
def test_dock_acknowledgement_requires_evaluator_service_and_causal_delivery(corruption):
    scenario, _, report = evidence_fixture()
    state, receipt = report["domain_service_events"]
    if corruption == "self_report":
        receipt["source"] = "participant"
    elif corruption == "wrong_order":
        receipt["accepted_object_ids"] = ["oversized_crate_proxy"]
    elif corruption == "receipt_before_delivery":
        receipt["time_s"] = 1.
    elif corruption == "closed_dock":
        state["state"] = "closed"
    elif corruption == "expired":
        state["valid_until_s"] = 82.1
    else:
        report["domain_service_events"].append({**state, "id": "new-state", "time_s": 82.8, "state": "closed"})
    assert not evaluate_domain_evidence(report, scenario=scenario)["validated"]


@pytest.mark.parametrize("corruption", ["furniture", "robot", "wall", "endpoint", "stale", "hash", "nan"])
def test_home_access_recomputes_geometry_instead_of_trusting_safe_flag(corruption):
    scenario, _, report = evidence_fixture("modern_house_nvd")
    grid = open_map()
    after = next(s for s in report["pose_trajectory"]["samples"] if s["time_s"] == 120.)
    record = report["domain_evidence"]["access_paths"]["after"]
    record["safe"] = True
    if corruption == "furniture":
        after["positions"]["armchair_proxy"] = [1.5, 0.]
    elif corruption == "robot":
        after["positions"]["carter_1"] = [1.5, 0.]
    elif corruption == "wall":
        grid.free[:, 115] = False
    elif corruption == "endpoint":
        record["points"] = [[1., 0.], [3., 0.]]
    elif corruption == "stale":
        record["time_s"] = 85.
    elif corruption == "hash":
        report["domain_evidence"]["access_map_sha256"] = "different"
    else:
        record["points"][1][0] = float("nan")
    assert not evaluate_domain_evidence(report, scenario=scenario, occupancy=grid)["validated"]


def test_public_contract_exposes_requested_items_but_no_private_instance_association():
    scenario, contract, _ = evidence_fixture()
    public = public_domain_requirements(contract)
    assert public["items"][0]["item_code"] == "W-OVERSIZE-01"
    assert "object_ids" not in str(public) and "oversized_crate_proxy" not in str(public)
    changed = deepcopy(contract)
    changed["localization_tolerance_m"] = 999
    with pytest.raises(ValueError, match="binding"):
        evaluate_domain_evidence({}, scenario=scenario, contract=changed)


def test_duplicate_source_ids_fail_closed():
    scenario, _, report = evidence_fixture()
    report["observation_store"]["observations"].append(deepcopy(report["observation_store"]["observations"][0]))
    result = evaluate_domain_evidence(report, scenario=scenario)
    assert not result["validated"] and "duplicate" in result["errors"][0]


def test_hidden_dispatcher_executes_domain_predicates_and_keeps_partial_credit():
    from demo.benchmark.scene_challenge import _hidden_evaluator, build_scene_runtime_contract, evaluate_challenge_report
    scenario, _, report = evidence_fixture()
    public, _ = build_scene_runtime_contract(scenario)
    bundle = {"public_task": public, "hidden_evaluator": _hidden_evaluator(public, scenario, qa={})}
    assert evaluate_challenge_report(bundle, report)["task_success"]
    report["domain_service_events"][0]["state"] = "closed"
    result = evaluate_challenge_report(bundle, report)
    assert result["scheduler_task_success"] and result["partial_completion"] == 1.
    assert not result["task_success"] and not result["business_task_success"]
    assert 0 < result["domain_predicate_fraction"] < 1


def test_factory_generic_entrypoint_cannot_bypass_dedicated_dispatch_evidence():
    from demo.benchmark.scene_challenge import evaluate_challenge_report
    from demo.factory_benchmark.challenge import compile_challenge
    bundle = compile_challenge()
    report = {"scheduling": {"states": {row["id"]: {"status": "succeeded"}
                                        for row in bundle["public_task"]["objectives"]}}}
    result = evaluate_challenge_report(bundle, report)
    assert result["scheduler_task_success"] and not result["task_success"]
    assert result["dispatch_evaluation"]["dispatch_success"] is False


def assert_predicate_rejected(report, scenario, name):
    # Self-reported outcomes never repair missing or contradictory records.
    report.update(success=True, safe=True, ready=True, business_success=True)
    result = evaluate_domain_evidence(report, scenario=scenario, occupancy=open_map())
    assert not result["business_success"], result
    assert not result["execution_validated"]
    if result["status"] == "evidence_evaluated":
        predicate = next(row for row in result["predicates"] if row["id"] == name)
        assert not predicate["validated"], predicate
        assert predicate["errors"]
    else:
        assert result["status"] == "missing_or_invalid_evidence" and result["errors"]


@pytest.mark.parametrize("scene", ["warehouse_nvd", "modern_house_nvd"])
@pytest.mark.parametrize("snapshot", ["absent", "empty", "missing_key", "carried", "false", "empty_team"])
def test_final_ownership_requires_explicit_unowned_entry_for_every_delivery(scene, snapshot):
    scenario, contract, report = evidence_fixture(scene)
    obj = contract["deliveries"][0]["object_id"]
    if snapshot == "absent":
        report.pop("final_ownership")
    elif snapshot == "empty":
        report["final_ownership"] = {}
    elif snapshot == "missing_key":
        report["final_ownership"].pop(obj)
    else:
        report["final_ownership"][obj] = {"carried": "m20_1", "false": False, "empty_team": []}[snapshot]
    assert_predicate_rejected(report, scenario, "final_object_placement")


@pytest.mark.parametrize("field", ["owner_before", "owner_after"])
@pytest.mark.parametrize("event_index", [0, 1])
def test_ownership_ledger_requires_explicit_transition_endpoints(field, event_index):
    scenario, _, report = evidence_fixture()
    report["events"][event_index].pop(field)
    assert_predicate_rejected(report, scenario, "delivery:oversized_crate_transfer")


@pytest.mark.parametrize("corruption", [
    "swapped_detection_ids", "colocated_wrong_object", "contradictory_item_code",
    "boolean_source", "boolean_version", "wrong_consumer", "consume_before_detection",
    "consume_after_action", "frame_after_detection", "frame_expired_at_action",
    "missing_measured_pose", "future_measured_pose", "stale_measured_pose",
])
def test_identity_is_bound_to_private_order_and_causal_frame_chain(corruption):
    scenario, contract, report = evidence_fixture()
    first, second = contract["identity_requirements"]
    checks = report["domain_evidence"]["identity_checks"]
    store = report["observation_store"]
    det, obs, consumed = store["detections"][0], store["observations"][0], store["consumptions"][0]
    sample = next(s for s in report["pose_trajectory"]["samples"] if s["time_s"] == obs["timestamp_s"])
    if corruption == "swapped_detection_ids":
        checks[0]["detection_ids"], checks[1]["detection_ids"] = checks[1]["detection_ids"], checks[0]["detection_ids"]
    elif corruption == "colocated_wrong_object":
        det["target_id"] = second["object_ids"][0]
        det["estimated_position_m"] = sample["positions"][first["object_ids"][0]]
        sample["positions"][det["target_id"]] = det["estimated_position_m"]
    elif corruption == "contradictory_item_code":
        det["item_code"] = second["item_code"]
    elif corruption == "boolean_source":
        det["detector_source"] = True
    elif corruption == "boolean_version":
        det["detector_version"] = True
    elif corruption == "wrong_consumer":
        consumed["consumer"] = second["consumer_node"]
    elif corruption == "consume_before_detection":
        consumed["timestamp_s"] = det["timestamp_s"] - .1
    elif corruption == "consume_after_action":
        consumed["timestamp_s"] = report["scheduling"]["states"][first["consumer_node"]]["started_at_s"] + .1
    elif corruption == "frame_after_detection":
        obs["timestamp_s"] = det["timestamp_s"] + .1
    elif corruption == "frame_expired_at_action":
        obs["valid_until_s"] = consumed["timestamp_s"]
    elif corruption == "missing_measured_pose":
        sample["positions"].pop(first["object_ids"][0])
    elif corruption == "future_measured_pose":
        sample["time_s"] += .01
    else:
        sample["time_s"] -= .26
    assert_predicate_rejected(report, scenario, "identity:oversized")


@pytest.mark.parametrize("corruption", [
    "same_time_conflict", "same_time_duplicate_ready", "missing_new_state_target",
    "missing_new_state_kind", "missing_new_state_source", "missing_new_state_expiry",
    "invalid_prior_state_expiry", "frame_before_current_state", "frame_future",
    "frame_expired", "object_drift_at_receipt", "missing_receipt_pose",
    "review_after_episode_end", "future_state_in_observed_log", "carried_at_receipt",
])
def test_dock_uses_unambiguous_complete_current_state_and_current_object_pose(corruption):
    scenario, _, report = evidence_fixture()
    state, receipt = report["domain_service_events"]
    frame = next(o for o in report["observation_store"]["observations"] if o["observation_id"] == "dock-frame")
    pose = next(s for s in report["pose_trajectory"]["samples"] if s["time_s"] == receipt["time_s"])
    if corruption.startswith("same_time"):
        report["domain_service_events"].append({**state, "id": "ambiguous-state",
                                              "state": "closed" if corruption == "same_time_conflict" else "ready"})
    elif corruption.startswith("missing_new_state"):
        field = corruption.removeprefix("missing_new_state_")
        field = {"target": "target_ref", "expiry": "valid_until_s"}.get(field, field)
        new = {**state, "id": "incomplete-state", "time_s": 82.8, "state": "closed"}
        new.pop(field)
        report["domain_service_events"].append(new)
    elif corruption == "invalid_prior_state_expiry":
        report["domain_service_events"].append({**state, "id": "invalid-prior-state", "time_s": 81., "valid_until_s": 80.})
    elif corruption == "frame_before_current_state":
        frame["timestamp_s"] = state["time_s"] - .1
    elif corruption == "frame_future":
        frame["timestamp_s"] = receipt["time_s"] + .1
    elif corruption == "frame_expired":
        frame["valid_until_s"] = receipt["time_s"] - .1
    elif corruption == "object_drift_at_receipt":
        pose["positions"]["priority_carton_proxy"] = [100., 100.]
    elif corruption == "missing_receipt_pose":
        report["pose_trajectory"]["samples"].remove(pose)
    elif corruption == "review_after_episode_end":
        report["scheduling"]["states"]["dock_confirmation_hold"]["finished_at_s"] = 121.
    elif corruption == "carried_at_receipt":
        report["events"].extend([
            {"event": "pickup", "object": "priority_carton_proxy", "node": "later_move",
             "time_s": 82.9, "owner_before": None, "owner_after": "m20_1"},
            {"event": "release", "object": "priority_carton_proxy", "node": "later_move",
             "time_s": 84., "owner_before": "m20_1", "owner_after": None}])
    else:
        report["domain_service_events"].append({**state, "id": "future-state", "time_s": 121., "valid_until_s": 125.})
    assert_predicate_rejected(report, scenario, "dock_handoff")


@pytest.mark.parametrize("field", ["quality", "sensor_config"])
def test_malformed_frame_provenance_fails_closed_without_crashing(field):
    scenario, _, report = evidence_fixture()
    report["observation_store"]["observations"][0][field] = ["self-reported-proof"]
    assert_predicate_rejected(report, scenario, "identity:oversized")


@pytest.mark.parametrize("section,predicate", [
    ("detections", "identity:oversized"), ("consumptions", "identity:oversized"),
    ("events", "delivery:oversized_crate_transfer"), ("domain_service_events", "dock_handoff"),
])
def test_success_flags_and_claimed_order_cannot_replace_underlying_evidence(section, predicate):
    scenario, contract, report = evidence_fixture()
    container = report["observation_store"] if section in {"detections", "consumptions"} else report
    container[section] = []
    report["domain_evidence"].update(
        success=True, ready=True, validated=True,
        accepted_object_ids=contract["identity_requirements"][0]["object_ids"])
    assert_predicate_rejected(report, scenario, predicate)


def test_valid_receipt_uses_latest_state_at_handoff_not_list_order_or_later_state():
    scenario, _, report = evidence_fixture()
    state, receipt = report["domain_service_events"]
    report["domain_service_events"] = [
        {**state, "id": "later-closure", "time_s": 84., "state": "closed"},
        receipt, state,
        {**state, "id": "prior-closure", "time_s": 81., "valid_until_s": 81.5, "state": "closed"},
    ]
    state["valid_until_s"] = receipt["time_s"]  # Expiry is inclusive, as in ObservationStore.
    for observation in report["observation_store"]["observations"]:
        if observation["observation_id"] == "dock-frame":
            observation["timestamp_s"] = state["time_s"]
            observation["valid_until_s"] = receipt["time_s"]
    result = evaluate_domain_evidence(report, scenario=scenario)
    assert result["business_success"], result
    assert not result["execution_validated"]


def test_matching_optional_item_code_preserves_existing_target_binding_protocol():
    scenario, contract, report = evidence_fixture()
    for requirement in contract["identity_requirements"]:
        detection = next(d for d in report["observation_store"]["detections"] if d["detection_id"] == requirement["id"])
        detection["item_code"] = requirement["item_code"]
    result = evaluate_domain_evidence(report, scenario=scenario)
    assert result["business_success"], result


def test_explicit_empty_contract_cannot_silently_fall_back_to_default():
    scenario, _, report = evidence_fixture()
    with pytest.raises(ValueError, match="binding"):
        evaluate_domain_evidence(report, scenario=scenario, contract={})
