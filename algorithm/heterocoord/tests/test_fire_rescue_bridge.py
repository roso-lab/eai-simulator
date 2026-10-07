from algorithm.common_coordination import ExecutionFeedback, MilestoneEvidence
from task.fixtures.fire_rescue import build_fire_rescue_assignment_request

from algorithm.heterocoord import FireRescueCoordinationBridge, PlanStatus


def _request():
    return build_fire_rescue_assignment_request()


def test_coherent_bridge_records_plan_and_keeps_physical_success_separate():
    bridge = FireRescueCoordinationBridge(
        "algorithm.system.coordination.coherent_eai.fire_rescue_policy:assign", task_id="trial-1"
    )
    plan = bridge.plan(_request())
    assert plan.status is PlanStatus.PLANNED
    assert plan.mapping == {
        "m20_1": "red",
        "scout_1": "green",
        "carter_1": "blue",
        "m20_2": "yellow",
    }
    bridge.observe_assignment(plan.mapping or {}, request=_request())
    bridge.observe_signals({"assignment_applied": True}, timestamp_s=1.0)
    report = bridge.finalize("success", timestamp_s=2.0)
    assert report["manifest"]["status"] == "finalized"
    assert report["ledger"]["events"][-1]["payload"]["physical_success_verified"] is False


def test_bt_bridge_matches_capability_auction_and_records_evidence_feedback_once():
    bridge = FireRescueCoordinationBridge(
        "algorithm.system.coordination.bt_capabilities_eai.fire_rescue_policy:assign", task_id="trial-2"
    )
    plan = bridge.plan(_request())
    assert plan.status is PlanStatus.PLANNED
    assert set(plan.mapping.values()) == {"red", "green", "blue", "yellow"}
    bridge.observe_signals({"rescue_channel_open": True}, timestamp_s=3.0)
    bridge.observe_signals({"rescue_channel_open": True}, timestamp_s=4.0)
    assert len(bridge.feedback_records) == 1
    assert bridge.feedback_records[0].task_id == "green"
    assert bridge.feedback_records[0].outcome == "success"


def test_fmc_ata_uses_default_assignment_for_full_execution_without_decomposition():
    bridge = FireRescueCoordinationBridge(
        "algorithm.system.coordination.fmc_ata_eai.fire_rescue_policy:assign", task_id="trial-3"
    )
    plan = bridge.plan(_request())
    assert plan.status is PlanStatus.PLANNED
    assert "indivisible" in plan.reason
    assert plan.allocation_provider == "fixed_default_assignment"
    assert plan.mapping == {
        "m20_1": "red",
        "scout_1": "green",
        "carter_1": "blue",
        "m20_2": "yellow",
    }


def test_bridge_serializes_feedback_and_milestone_records():
    bridge = FireRescueCoordinationBridge("emos", task_id="trial-4")
    bridge.record_feedback(
        ExecutionFeedback("yellow", "m20_1", "failed", failure_code="execution_failure")
    )
    bridge.record_milestone(
        MilestoneEvidence("mission_success", True, "eai.fire_rescue.evaluator", timestamp_s=5.0)
    )
    report = bridge.finalize("success")
    assert report["feedback"][0]["failure_code"] == "execution_failure"
    assert report["milestones"][0]["satisfied"] is True
    assert report["ledger"]["digest"]


def test_replay_branch_does_not_reuse_fact_success_evidence():
    bridge = FireRescueCoordinationBridge("emos", task_id="trial-5")
    bridge.record_milestone(MilestoneEvidence("mission_success", True, "evaluator"))
    bridge.record_branch_assignment(
        {"m20_1": {"colour": "red", "target_xy": [1.0, 2.0]}},
        checkpoint_id="assignment-0000",
    )
    report = bridge.finalize("fail")
    assert report["manifest"]["branch"] == "branch:assignment-0000"
    assert report["ledger"]["events"][-2]["kind"] == "terminal"
    assert report["ledger"]["events"][-2]["payload"]["physical_success_verified"] is False


def test_terminal_failure_emits_executor_feedback_and_explicit_replan_limit():
    bridge = FireRescueCoordinationBridge(
        "algorithm.system.coordination.bt_capabilities_eai.fire_rescue_policy:assign", task_id="trial-timeout"
    )
    plan = bridge.plan(_request())
    bridge.finalize("fail", timestamp_s=2.5)
    assert len(bridge.feedback_records) == len(plan.assignments)
    assert all(item.failure_code.value == "timeout" for item in bridge.feedback_records)
    kinds = [event["kind"] for event in bridge.ledger.events]
    assert "replan_requested" in kinds
    assert "replan_status" in kinds


def test_mrta_bridge_records_repair_plan_after_timeout():
    bridge = FireRescueCoordinationBridge(
        "algorithm.system.coordination.mrta_heuristic_eai.fire_rescue_policy:assign", task_id="trial-mrta"
    )
    plan = bridge.plan(_request())
    assert plan.status is PlanStatus.PLANNED
    bridge.finalize("fail", timestamp_s=1.0)
    events = bridge.ledger.events
    assert any(event["kind"] == "replan" for event in events)
    assert any(
        event["kind"] == "replan_status"
        and event["payload"]["status"] == "planned_not_dispatched"
        for event in events
    )


def test_hybrid_susd_bridge_records_adapter_reallocation_after_timeout():
    bridge = FireRescueCoordinationBridge(
        "algorithm.system.coordination.hybrid_susd_eai.fire_rescue_policy:assign", task_id="trial-hybrid-susd"
    )
    plan = bridge.plan(_request())
    assert plan.status is PlanStatus.PLANNED
    assert set(plan.mapping.values()) == {"red", "green", "blue", "yellow"}
    bridge.finalize("fail", timestamp_s=1.0)
    events = bridge.ledger.events
    replan = [event for event in events if event["kind"] == "replan"]
    assert replan
    assert replan[-1]["payload"]["allocation_provider"] == "Hybrid-SUSD-adapter-reallocation"
    assert any(
        event["kind"] == "replan_status"
        and event["payload"]["status"] == "planned_not_dispatched"
        for event in events
    )


def test_dcmrta_bridge_keeps_native_checkpoint_decision_separate():
    bridge = FireRescueCoordinationBridge(
        "algorithm.system.coordination.dcmrta_eai.fire_rescue_policy:assign", task_id="trial-dcmrta"
    )
    plan = bridge.plan(_request())
    assert plan.status is PlanStatus.PLANNED
    assert plan.allocation_provider == "fixed_default_assignment;dcmrta_native_decision_N/A"
    assert plan.mapping == {
        "m20_1": "red",
        "scout_1": "green",
        "carter_1": "blue",
        "m20_2": "yellow",
    }


def test_heteromrta_bridge_keeps_native_checkpoint_decision_separate():
    bridge = FireRescueCoordinationBridge(
        "algorithm.system.coordination.heteromrta_eai.fire_rescue_policy:assign", task_id="trial-heteromrta"
    )
    plan = bridge.plan(_request())
    assert plan.status is PlanStatus.PLANNED
    assert plan.allocation_provider == "fixed_default_assignment;heteromrta_native_decision_N/A"
    assert set(plan.mapping.values()) == {"red", "green", "blue", "yellow"}
