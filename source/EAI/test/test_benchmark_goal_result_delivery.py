"""Delayed-result timing must not become a spurious execution failure."""
from copy import deepcopy

import pytest

from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.benchmark.goal_session_audit import AuditedGoalSession, replay_goal_session_audit
from source.EAI.test.test_benchmark_goal_session import (
    GROUND, _task, _plan, _submit, _snapshot, _dispatch,
)


def ready():
    bundle = build_goal_task_package("warehouse_nvd")
    session = AuditedGoalSession(bundle["public_task"], bundle["participant_api"],
                                 episode_id="dual-clock-test")
    _submit(session, "select_team", {"agent_ids": [GROUND]})
    _submit(session, "submit_plan", {"plan": _plan(_task())})
    _snapshot(session, bundle["public_task"], 0.)
    _dispatch(session, aid="dispatch-nav")
    return session, bundle


def receipt(*, physical=2., status="succeeded"):
    return {"backend_id": "synthetic-dual-clock-backend", "receipt_ref": "test:terminal",
            "task_id": "nav", "action_id": "dispatch-nav", "episode_id": "dual-clock-test",
            "status": status, "completed_at_s": physical, "evidence_refs": ["test:physical-state"]}


def replay(session, bundle):
    result = replay_goal_session_audit(session.audit_journal(), bundle["public_task"],
                                      bundle["participant_api"])
    assert result["validated"] is True


def test_on_time_execution_survives_late_uplink_without_rewinding_cloud_clock():
    session, bundle = ready()
    _snapshot(session, bundle["public_task"], 9.)
    assert session.report()["tasks"]["nav"]["status"] == "pending"
    result = session.complete_skill_delivery("nav", physical_completed_at_s=2.,
        delivered_at_s=10., status="succeeded", receipt=receipt())
    assert result["accepted"] is True
    report = session.report()
    row = report["tasks"]["nav"]
    assert row["completed_at_s"] == row["receipt"]["completed_at_s"] == 2.
    assert row["result_delivered_at_s"] == report["current_time_s"] == 10.
    assert report["events"][-1]["time_s"] == 10.
    assert report["events"][-1]["payload"]["status"] == "succeeded"
    assert all(report[k] is False for k in (
        "scoreable", "execution_validated", "business_outcomes_validated"))
    replay(session, bundle)


@pytest.mark.parametrize("physical,delivered,receipt_time", [
    (9., 10., 9.),  # Execution itself missed its eight-second window.
    (5., 4., 5.),   # A result cannot arrive before it physically exists.
    (-1., 10., -1.),
    (2., 10., 3.),  # The immutable receipt disagrees with supplied physical time.
    (float("nan"), 10., 2.),
])
def test_invalid_physical_or_delivery_evidence_preserves_pending_state(physical, delivered, receipt_time):
    session, bundle = ready()
    before = session.report()
    with pytest.raises(ValueError):
        session.complete_skill_delivery("nav", physical_completed_at_s=physical,
            delivered_at_s=delivered, status="succeeded", receipt=receipt(physical=receipt_time))
    assert session.report() == before
    # The audit encodes rejected nonfinite arguments separately for strict JSON.
    replay(session, bundle)


def test_cloud_delivery_time_cannot_regress_even_when_physical_result_is_old():
    session, bundle = ready()
    _snapshot(session, bundle["public_task"], 9.)
    before = session.report()
    with pytest.raises(ValueError):
        session.complete_skill_delivery("nav", physical_completed_at_s=2.,
            delivered_at_s=8., status="succeeded", receipt=receipt())
    assert session.report() == before
    replay(session, bundle)


@pytest.mark.parametrize("change", ["cancel", "member_exit"])
def test_late_success_does_not_resurrect_cancelled_or_invalidated_task(change):
    session, bundle = ready()
    session.emit("tick", 3., {"state_digest": "test:tick3"})
    if change == "cancel":
        _submit(session, "cancel_skill", {"task_id": "nav", "reason": "participant replan"})
    else:
        session.emit("member_exit", 3., {"agent_id": GROUND})
    before = session.report()
    with pytest.raises(ValueError, match="invalidated/cancel-requested"):
        session.complete_skill_delivery("nav", physical_completed_at_s=2.,
            delivered_at_s=10., status="succeeded", receipt=receipt())
    assert session.report() == before
    # An actual later cancellation acknowledgement has its own physical evidence.
    session.complete_skill_delivery("nav", physical_completed_at_s=4., delivered_at_s=11.,
        status="cancelled", receipt=receipt(physical=4., status="cancelled"))
    assert session.report()["tasks"]["nav"]["status"] == "cancelled"
    replay(session, bundle)


def test_legacy_same_time_completion_retains_old_report_shape():
    session, bundle = ready()
    session.complete_skill("nav", time_s=2., status="succeeded", receipt=receipt())
    row = session.report()["tasks"]["nav"]
    assert row["completed_at_s"] == 2.
    assert "result_delivered_at_s" not in row
    replay(session, bundle)


def test_episode_and_dispatch_identity_remain_bound_on_delivery():
    session, bundle = ready()
    before = session.report()
    for field in ("episode_id", "action_id", "task_id"):
        wrong = deepcopy(receipt())
        wrong[field] = "foreign"
        with pytest.raises(ValueError):
            session.complete_skill_delivery("nav", physical_completed_at_s=2.,
                delivered_at_s=10., status="succeeded", receipt=wrong)
        assert session.report() == before
    replay(session, bundle)
