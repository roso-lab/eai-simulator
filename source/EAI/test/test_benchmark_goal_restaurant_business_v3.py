"""Focused archive facts for the full8 Restaurant business-v3 verifier."""

from copy import deepcopy
import json
import math
from pathlib import Path

import pytest

from demo.benchmark.communication import validate_communication_report
from demo.benchmark.goal_algorithm_adapter import GoalAlgorithmHost
from demo.benchmark.goal_restaurant_autonomous_v2 import RestaurantAutonomousContext
from demo.benchmark.goal_restaurant_business_v3 import (
    INSPECTION_AVAILABILITY,
    RECEIPT_AVAILABILITY,
    _member_exit_between,
    verify_restaurant_business_v3,
)
from demo.benchmark.goal_restaurant_deployment_v2 import build_restaurant_deployment_v2
from demo.benchmark.goal_restaurant_execution_v2 import RestaurantExecutionPump
from demo.benchmark.goal_restaurant_fleet import FLEET_CONTROLLER_REGISTRY
from demo.benchmark.goal_restaurant_native_observation import (
    RestaurantNativeObservationProvider,
)
from demo.benchmark.goal_session_audit import AuditedGoalSession
from demo.benchmark.provenance import canonical_sha256
from source.EAI.test.test_benchmark_goal_restaurant_business_v2 import _scene
from source.EAI.test.test_benchmark_goal_restaurant_execution_v2 import (
    FakeFleet,
    Participant,
    _parallel_plan,
    _route,
    _runtime,
    _task,
)


EPISODE = "business-v3-fixture"


class BusinessFleet(FakeFleet):
    """Focused full8 driver that retains the production terminal fact fields."""

    def _snapshot(self):
        snapshot = super()._snapshot()
        for operation in snapshot["operations"].values():
            operation["goal_binding"]["source"] = "trusted_public_region_geometry"
            operation["maximum_tilt_rad"] = 0.0
            if operation["status"] == "arrived":
                position = snapshot["agents"][operation["public_agent_id"]]["position_m"]
                operation["distance_to_goal_m"] = math.dist(
                    position[:2], operation["goal_xyz_m"][:2])
        return snapshot


class BusinessObservationProvider:
    def __init__(self, fleet):
        self.fleet = fleet

    def ground_readiness(self, producer_id, region_id):
        sample = self.fleet.snapshot()["agents"][producer_id]
        step = sample["physics_step"]
        scene = _scene(
            producer_id, region_id, step,
            time_by_step={step: sample["time_s"]})
        scene["stamp"]["root_state_w"] = deepcopy(sample["root_state_w"])
        scene["observed_robot_xy_m"] = deepcopy(sample["root_state_w"][:2])
        scene["region_error_m"] = math.dist(
            scene["observed_robot_xy_m"], scene["region_center_xy_m"])
        return scene

    @staticmethod
    def bind_transport_stamp(scene):
        return RestaurantNativeObservationProvider.bind_transport_stamp(scene)


def _closed():
    return {
        "execution_validated": False,
        "business_outcomes_validated": False,
        "feasibility_oracle_verified": False,
        "release_ready": False,
        "scoreable": False,
    }


def _non_carter_plan(contract):
    plan = _parallel_plan(contract)
    replacements = {
        "restaurant_carter_1": "restaurant_m20_1",
        "restaurant_carter_2": "restaurant_go2_1",
    }
    public = contract["public_task"]
    deployment = contract["deployment"]
    for task in plan["tasks"]:
        old = task["assigned_agents"][0]
        agent = replacements[old]
        task["assigned_agents"] = [agent]
        if task["skill"] == "ground_navigation":
            region = task["arguments"]["region_id"]
            spawn = deployment["spawn_regions"][agent]
            task["arguments"]["route_ids"] = [_route(public, spawn, region)]
    plan["assigned_team"] = list(replacements.values())
    return plan


def build_restaurant_business_v3_fixture(
    *, non_carter=False, include_relay=True,
    drop_observation_results=False, drop_relay_result=False,
    different_inspection_producer=False, multi_leg_navigation=False,
    explicit_relay_refs=False, observation_before_arrival=False,
):
    """Build one small, real pump/audit/communication chain for other tests."""
    if drop_observation_results and drop_relay_result:
        raise ValueError("select at most one focused result-loss phase")
    contract = build_restaurant_deployment_v2()
    plan = _non_carter_plan(contract) if non_carter else _parallel_plan(contract)
    if different_inspection_producer:
        observation = next(task for task in plan["tasks"] if task["task_id"] == "obs-k")
        public = contract["public_task"]
        spawn = contract["deployment"]["spawn_regions"]["restaurant_m20_1"]
        plan["tasks"].append(_task(
            "nav-k-observer", "ground_navigation", "restaurant_m20_1", {
                "region_id": "kitchen_service",
                "route_ids": [_route(public, spawn, "kitchen_service")],
            }, start=0.0, duration=8.0))
        observation["assigned_agents"] = ["restaurant_m20_1"]
        observation["prerequisites"] = ["nav-k-observer"]
        relay = next(task for task in plan["tasks"] if task["task_id"] == "relay")
        relay["prerequisites"].append("nav-k")
        if "restaurant_m20_1" not in plan["assigned_team"]:
            plan["assigned_team"].append("restaurant_m20_1")
    if multi_leg_navigation:
        navigation = next(task for task in plan["tasks"] if task["task_id"] == "nav-d")
        public = contract["public_task"]
        spawn = contract["deployment"]["spawn_regions"]["restaurant_carter_2"]
        navigation["arguments"]["route_ids"] = [
            _route(public, spawn, "kitchen_service"),
            _route(public, "kitchen_service", "dining_service"),
        ]
    if observation_before_arrival:
        public = contract["public_task"]
        contract["deployment"]["spawn_regions"]["restaurant_carter_1"] = (
            "kitchen_service")
        navigation = next(task for task in plan["tasks"] if task["task_id"] == "nav-k")
        navigation["arguments"]["route_ids"] = [
            _route(public, "kitchen_service", "dining_service"),
            _route(public, "dining_service", "kitchen_service"),
        ]
        navigation["execution"]["start_s"] = 8.0
        navigation["prerequisites"] = ["obs-k"]
        observation = next(task for task in plan["tasks"] if task["task_id"] == "obs-k")
        observation["prerequisites"] = []
        observation["execution"]["start_s"] = 0.0
        relay = next(task for task in plan["tasks"] if task["task_id"] == "relay")
        relay["execution"]["start_s"] = 16.0
        relay["prerequisites"].append("nav-k")
    if not include_relay or drop_observation_results:
        plan["tasks"] = [task for task in plan["tasks"] if task["skill"] != "relay"]
    participant = Participant(plan, explicit_relay_refs=explicit_relay_refs)
    fleet = BusinessFleet(contract, arrival_ticks=1)
    session = AuditedGoalSession(
        contract["public_task"], contract["participant_api"], episode_id=EPISODE)
    regions = contract["deployment"]["spawn_regions"]
    session.initialize_deployment_runtime_state(regions, {})
    gate = session.report()["deployment_assessment"]
    session.record_deployment_assessment_evaluation({
        "assessment_id": gate["assessment_id"],
        "assessment_epoch": gate["assessment_epoch"],
        "feasibility_label": None,
        "oracle_status": "failed",
        "platform_valid": False,
        "evidence_refs": [],
        "evaluator_ref": "business-v3-focused-no-oracle",
    })
    host = GoalAlgorithmHost(
        contract["public_task"], contract["participant_api"], participant,
        session=session)
    binding = {
        "schema": "eai.restaurant.deployment_ack",
        "version": 2,
        "profile": contract["deployment"]["profile"],
        "deployment_sha256": canonical_sha256(contract["deployment"]),
        "public_task_sha256": canonical_sha256(contract["public_task"]),
        "episode_id": EPISODE,
        "native_initial_state_sha256": "1" * 64,
        "native_epoch_archive": {"path": "fixture", "sha256": "2" * 64},
    }
    context = RestaurantAutonomousContext(
        host, session, fleet, contract, {"robot_regions": deepcopy(regions)}, binding,
        _execution_initializer=participant.initialize_execution)
    runtime = _runtime(downlink_delay=0.02, uplink_delay=0.02)
    pump = RestaurantExecutionPump(
        context, runtime=runtime,
        observation_provider=BusinessObservationProvider(fleet))
    drop_after_s = (7.9 if drop_observation_results
                    else 11.9 if drop_relay_result else None)
    if drop_after_s is None:
        pump_report = pump.run(max_ticks=150)
    else:
        drop_enabled = False
        for _ in range(150):
            if (drop_after_s is not None and not drop_enabled
                    and fleet.time_s >= drop_after_s):
                runtime.set_conditions(
                    {"drop": True}, channel="goal_execution_result",
                    time_s=fleet.time_s)
                drop_enabled = True
            pump.step_once()
        pump_report = pump.report()
    context_report = pump_report["context"]
    journal = context_report["session_audit"]
    session_report = journal["final_report"]
    communication = pump_report["communication"]
    source_manifest = {
        "demo/benchmark/goal_restaurant_execution_v2.py": "3" * 64,
        "demo/benchmark/goal_session.py": "4" * 64,
    }
    metadata = {
        "schema": "eai.restaurant.native_episode_epoch",
        "version": 1,
        "episode_id": EPISODE,
        "task_id": contract["public_task"]["task_id"],
        "public_task_sha256": canonical_sha256(contract["public_task"]),
        "deployment_sha256": canonical_sha256(contract["deployment"]),
        "origin_physics_step": 100,
        "origin_time_s": 0.0,
        **_closed(),
    }
    return {
        "public_task": deepcopy(contract["public_task"]),
        "deployment_companion": deepcopy(contract["deployment"]),
        "episode_metadata": metadata,
        "context_report": context_report,
        "session_report": session_report,
        "audit_journal": journal,
        "audit_replay": pump_report["session_audit_replay"],
        "pump_report": pump_report,
        "communication_report": communication,
        "communication_replay": validate_communication_report(communication),
        "archived_source_sha256": deepcopy(source_manifest),
        "expected_source_sha256": deepcopy(source_manifest),
    }


def _verify(bundle):
    return verify_restaurant_business_v3(**bundle)


def _reseal(row):
    row["evidence_ref"] = "sha256:" + canonical_sha256({
        key: value for key, value in row.items() if key != "evidence_ref"})


def test_full8_business_facts_keep_physical_and_available_times_distinct():
    review = _verify(build_restaurant_business_v3_fixture())
    assert review["status"] == "complete"
    assert review["business_predicates_complete"] is True
    assert review["evidence_gaps"] == []
    for outcome in ("kitchen_service_inspected", "dining_service_inspected"):
        row = review["milestones"][outcome]
        assert row["status"] == "verified"
        assert row["availability_status"] == "delivered"
        assert row["physical_time_s"] < row["available_at_s"]
        assert row["arrival_physical_time_s"] < row["physical_time_s"]
        assert row["availability_event_kind"] == INSPECTION_AVAILABILITY
        assert len(row["task_ids"]) == 2
        assert len(row["message_ids"]) == 4
    receipt = review["milestones"]["readiness_receipt_committed"]
    assert receipt["physical_time_s"] == receipt["service_committed_at_s"]
    assert receipt["availability_status"] == "delivered"
    assert receipt["physical_time_s"] < receipt["available_at_s"]
    assert receipt["availability_event_kind"] == RECEIPT_AVAILABILITY
    assert receipt["service_transport_delivered_at_s"] <= receipt["physical_time_s"]
    assert receipt["result_transport_delivered_at_s"] <= receipt["available_at_s"]
    assert review["source_identity"]["native_truth_authenticated"] is False
    assert review["integrity_scope"].endswith(
        "not_independent_native_host_authentication")
    assert all(review[flag] is False for flag in (
        "execution_validated", "business_outcomes_validated",
        "feasibility_oracle_verified", "release_ready", "scoreable"))


def test_compatible_non_carter_members_can_supply_business_facts():
    review = _verify(build_restaurant_business_v3_fixture(non_carter=True))
    producers = {
        review["milestones"][name]["producer_id"]
        for name in ("kitchen_service_inspected", "dining_service_inspected")
    }
    assert producers == {"restaurant_m20_1", "restaurant_go2_1"}


def test_arrival_and_observation_may_have_different_valid_producers():
    review = _verify(build_restaurant_business_v3_fixture(
        different_inspection_producer=True))
    kitchen = review["milestones"]["kitchen_service_inspected"]
    assert kitchen["arrival_producer_id"] == "restaurant_carter_1"
    assert kitchen["producer_id"] == "restaurant_m20_1"


def test_multi_leg_navigation_and_both_relay_ref_styles_are_accepted():
    assert _verify(build_restaurant_business_v3_fixture(
        multi_leg_navigation=True))["status"] == "complete"
    assert _verify(build_restaurant_business_v3_fixture(
        explicit_relay_refs=True))["status"] == "complete"


def test_observation_before_later_arrival_does_not_form_inspection_chain():
    bundle = build_restaurant_business_v3_fixture(observation_before_arrival=True)
    physical = {row["task_id"]: row for row in bundle["pump_report"]["physical_records"]}
    assert physical["obs-k"]["physical_terminal_at_s"] < physical["nav-k"][
        "physical_terminal_at_s"]
    review = _verify(bundle)
    assert review["milestones"]["kitchen_service_inspected"]["status"] == (
        "missing_evidence")
    assert any(row["milestone_id"] == "kitchen_service_inspected"
               and row["code"] == "inspection_chain_missing"
               for row in review["evidence_gaps"])


def test_member_exit_interval_helper_filters_selected_not_unrelated_or_start_boundary():
    bundle = build_restaurant_business_v3_fixture()
    review = _verify(bundle)
    kitchen = review["milestones"]["kitchen_service_inspected"]
    start = kitchen["arrival_physical_time_s"]
    end = kitchen["physical_time_s"]
    journal = deepcopy(bundle["audit_journal"])
    journal["calls"].append({
        "method": "emit",
        "outcome": "returned",
        "arguments": {
            "type": "member_exit",
            "time_s": (start + end) / 2,
            "payload": {"agent_id": kitchen["arrival_producer_id"]},
        },
    })
    arrival_exited = _member_exit_between(
        journal, kitchen["arrival_producer_id"], start, end)
    observation_exited = _member_exit_between(
        journal, kitchen["producer_id"], start, end)
    assert arrival_exited is True
    assert observation_exited is True
    assert _member_exit_between(journal, "restaurant_iris_1", start, end) is False
    boundary = {"calls": [{
        "method": "emit",
        "outcome": "returned",
        "arguments": {
            "type": "member_exit", "time_s": start,
            "payload": {"agent_id": kitchen["arrival_producer_id"]},
        },
    }]}
    assert _member_exit_between(
        boundary, kitchen["arrival_producer_id"], start, end) is False


def test_external_source_manifest_mismatch_is_rejected():
    bundle = build_restaurant_business_v3_fixture()
    path = next(iter(bundle["expected_source_sha256"]))
    bundle["expected_source_sha256"][path] = "f" * 64
    with pytest.raises(ValueError, match="expected source"):
        _verify(bundle)


def test_independent_communication_replay_rejects_forged_result_wire():
    bundle = build_restaurant_business_v3_fixture()
    message = next(
        row for row in bundle["communication_report"]["messages"].values()
        if row["channel"] == "goal_execution_result")
    message["payload"]["estimate"]["physical_completed_at_s"] += 1.0
    with pytest.raises(ValueError, match="communication report|replay"):
        _verify(bundle)


def test_independent_audit_replay_rejects_receipt_substitution():
    bundle = build_restaurant_business_v3_fixture()
    bundle["session_report"]["tasks"]["nav-k"]["result_delivered_at_s"] += 1.0
    with pytest.raises(ValueError, match="audit final report|session and audit"):
        _verify(bundle)


def test_foreign_physical_record_is_invalid_even_when_resealed():
    bundle = build_restaurant_business_v3_fixture()
    row = bundle["pump_report"]["physical_records"][0]
    row["episode_id"] = "foreign"
    _reseal(row)
    with pytest.raises(ValueError, match="foreign episode"):
        _verify(bundle)


def test_out_of_region_terminal_root_is_rejected_before_delivery_state_can_replace_it():
    bundle = build_restaurant_business_v3_fixture()
    row = next(row for row in bundle["pump_report"]["physical_records"]
               if row["task_id"] == "nav-k")
    producer = row["task"]["assigned_agents"][0]
    row["region_record"]["agents"][producer]["root_state_w"][0] += 1.0
    _reseal(row["region_record"])
    row["region_record_sha256"] = canonical_sha256(row["region_record"])
    _reseal(row)
    with pytest.raises(ValueError, match="substituted|region"):
        _verify(bundle)


def test_service_geometry_does_not_accept_navigation_half_meter_tolerance():
    bundle = build_restaurant_business_v3_fixture()
    row = next(row for row in bundle["pump_report"]["physical_records"]
               if row["task_id"] == "nav-k")
    producer = row["task"]["assigned_agents"][0]
    center = bundle["deployment_companion"]["region_bindings"][
        "kitchen_service"]["center_xy_m"]
    root = deepcopy(row["fleet_snapshot"]["agents"][producer]["root_state_w"])
    root[0], root[1] = center[0] + 0.30, center[1]
    row["fleet_snapshot"]["agents"][producer]["root_state_w"] = deepcopy(root)
    row["fleet_snapshot"]["agents"][producer]["position_m"] = deepcopy(root[:3])
    operation = next(iter(row["fleet_snapshot"]["operations"].values()))
    operation["last_root_state_w"] = deepcopy(root)
    operation["distance_to_goal_m"] = 0.30
    row["native_evidence"] = [deepcopy(operation)]
    member = row["region_record"]["agents"][producer]
    member["root_state_w"] = deepcopy(root)
    match = next(item for item in member["matches"]
                 if item["region_id"] == "kitchen_service")
    match["distance_m"] = 0.30
    _reseal(row["region_record"])
    row["region_record_sha256"] = canonical_sha256(row["region_record"])
    row["fleet_snapshot_sha256"] = canonical_sha256(row["fleet_snapshot"])
    _reseal(row)
    with pytest.raises(ValueError, match="out-of-region"):
        _verify(bundle)


def test_tipped_and_stale_native_terminals_are_rejected():
    tipped = build_restaurant_business_v3_fixture()
    row = next(row for row in tipped["pump_report"]["physical_records"]
               if row["task_id"] == "nav-k")
    operation = next(iter(row["fleet_snapshot"]["operations"].values()))
    operation["maximum_tilt_rad"] = 0.20
    row["native_evidence"] = [deepcopy(operation)]
    row["fleet_snapshot_sha256"] = canonical_sha256(row["fleet_snapshot"])
    _reseal(row)
    with pytest.raises(ValueError, match="tipped"):
        _verify(tipped)

    stale = build_restaurant_business_v3_fixture()
    row = next(row for row in stale["pump_report"]["physical_records"]
               if row["task_id"] == "obs-k")
    row["region_record"]["time_s"] += 0.01
    _reseal(row["region_record"])
    row["region_record_sha256"] = canonical_sha256(row["region_record"])
    _reseal(row)
    with pytest.raises(ValueError, match="stamps differ"):
        _verify(stale)


def test_missing_relay_keeps_independent_inspections_and_reports_partial_gap():
    bundle = build_restaurant_business_v3_fixture(include_relay=False)
    review = _verify(bundle)
    assert review["status"] == "partial"
    assert review["business_predicates_complete"] is False
    assert all(review["milestones"][outcome]["status"] == "verified"
               for outcome in ("kitchen_service_inspected", "dining_service_inspected"))
    assert review["milestones"]["readiness_receipt_committed"]["status"] == (
        "missing_evidence")
    assert all(row["classification"] == "observed_incomplete_business_chain"
               for row in review["evidence_gaps"])


def test_real_observation_result_loss_preserves_physical_inspection_facts():
    review = _verify(build_restaurant_business_v3_fixture(
        include_relay=False, drop_observation_results=True))
    for outcome in ("kitchen_service_inspected", "dining_service_inspected"):
        row = review["milestones"][outcome]
        assert row["status"] == "verified"
        assert row["physical_time_s"] is not None
        assert row["availability_status"] == "not_delivered"
        assert row["available_at_s"] is None
        assert row["transport_outcome"] == "dropped"
        assert row["transport_reason"] == "active"
    assert review["status"] == "partial"
    assert review["public_availability_complete"] is False
    assert review["evidence_gaps"][0]["classification"] == (
        "observed_incomplete_business_chain")


def test_real_relay_result_loss_preserves_committed_business_receipt():
    review = _verify(build_restaurant_business_v3_fixture(drop_relay_result=True))
    receipt = review["milestones"]["readiness_receipt_committed"]
    assert receipt["status"] == "verified"
    assert receipt["physical_time_s"] == receipt["service_committed_at_s"]
    assert receipt["availability_status"] == "not_delivered"
    assert receipt["available_at_s"] is None
    assert receipt["transport_outcome"] == "dropped"
    assert review["business_predicates_complete"] is True
    assert review["public_availability_complete"] is False
    assert review["status"] == "partial"


def test_observation_root_must_match_the_full_fleet_snapshot():
    bundle = build_restaurant_business_v3_fixture()
    row = next(row for row in bundle["pump_report"]["physical_records"]
               if row["task_id"] == "obs-k")
    producer = row["task"]["assigned_agents"][0]
    row["fleet_snapshot"]["agents"][producer]["root_state_w"][0] += 0.01
    row["fleet_snapshot_sha256"] = canonical_sha256(row["fleet_snapshot"])
    _reseal(row)
    with pytest.raises(ValueError, match="snapshot root|out-of-region body"):
        _verify(bundle)


def test_foreign_episode_metadata_schema_is_rejected():
    bundle = build_restaurant_business_v3_fixture()
    bundle["episode_metadata"]["schema"] = "foreign.episode"
    with pytest.raises(ValueError, match="episode metadata identity"):
        _verify(bundle)


def test_extra_public_business_outcome_is_rejected():
    bundle = build_restaurant_business_v3_fixture()
    bundle["public_task"]["required_outcomes"].append({
        "id": "unverified_extra_outcome", "description": "must not be ignored"})
    with pytest.raises(ValueError, match="exactly the Restaurant business outcomes"):
        _verify(bundle)


def test_unrelated_failed_attempt_does_not_poison_legal_succeeded_chain():
    bundle = build_restaurant_business_v3_fixture()
    failed = deepcopy(bundle["pump_report"]["physical_records"][0])
    failed["task_id"] = "unrelated-failed-attempt"
    failed["task"]["task_id"] = "unrelated-failed-attempt"
    failed["status"] = "failed"
    failed["disposition"] = "platform_gap"
    _reseal(failed)
    bundle["pump_report"]["physical_records"].insert(0, failed)
    assert _verify(bundle)["status"] == "complete"


def test_stage53_native_archive_is_interpreted_offline_when_present():
    root = Path("tmp/stage53-validation/native-pump-1")
    if not (root / "pump.json").is_file():
        pytest.skip("Stage53 local native archive is not present")
    load = lambda name: json.loads((root / name).read_text())
    context = load("context.json")
    request = load("request.json")
    review = verify_restaurant_business_v3(
        public_task=context["public_contract"]["public_task"],
        deployment_companion=request["deployment"],
        episode_metadata=load("episode-metadata.json"),
        context_report=context,
        session_report=load("session.json"),
        audit_journal=load("audit.json"),
        audit_replay=load("audit-review.json"),
        pump_report=load("pump.json"),
        communication_report=load("communication.json"),
        communication_replay=load("communication-review.json"),
        archived_source_sha256=request["source_sha256"],
        expected_source_sha256=request["source_sha256"],
    )
    assert review["status"] == "complete"
    assert review["milestones"]["kitchen_service_inspected"][
        "physical_time_s"] == pytest.approx(61.06)
    assert review["milestones"]["readiness_receipt_committed"][
        "available_at_s"] == pytest.approx(72.18)


__all__ = ["build_restaurant_business_v3_fixture"]
