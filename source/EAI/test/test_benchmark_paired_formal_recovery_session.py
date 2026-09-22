from demo.benchmark.goal_driven_tasks import build_goal_task_package
from demo.warehouse_benchmark.paired_native_probe import (
    _admit_formal_pair_recovery,
    _complete_formal_pair_recovery,
    _formal_goal_recovery_session,
    _member_exit_lifecycle_record,
)


def test_member_exit_lifecycle_is_episode_bound_and_reports_reassignment_latency():
    before = {"object": {"position_m": [-10.6, -11.7, .3]}, "physics_step": 12}
    after = {"object": {"position_m": [-10.6, -11.7, .3]}, "physics_step": 14}
    receipt = _member_exit_lifecycle_record(
        episode_id="warehouse-formal-paired-recovery:member-exit",
        event_time_s=12.0, exited_member="warehouse_ground_3",
        remaining_member="warehouse_ground_1", pair_before=before,
        object_before=before["object"], single_failure={
            "status": "failed", "failure_reason": "member allocated load exceeds public payload",
            "episode_id": "warehouse-formal-paired-recovery:member-exit",
            "member_id": "warehouse_ground_1", "object_after": before["object"],
            "object_moved": False, "task_advanced": False},
        pair_after=after, rejoin_time_s=15.5)
    assert receipt["event"] == "member_exit"
    assert receipt["episode_id"].endswith("member-exit")
    assert receipt["reassignment"]["reassignment_latency_s"] == 3.5
    assert receipt["task_advanced"] is False
    assert receipt["execution_validated"] is False
    assert receipt["scoreable"] is False


def test_member_exit_rejects_object_motion_or_cross_episode_failure():
    base = dict(event_time_s=1.0, exited_member="a", remaining_member="b",
                pair_before={}, object_before={"p": 1}, pair_after={}, rejoin_time_s=2.0)
    failed = {"status": "failed", "failure_reason": "member allocated load exceeds public payload",
              "episode_id": "episode", "member_id": "b", "object_after": {"p": 2}}
    import pytest
    with pytest.raises(ValueError, match="moved"):
        _member_exit_lifecycle_record(episode_id="episode", single_failure=failed, **base)
    failed["object_after"] = {"p": 1}
    failed["episode_id"] = "other"
    with pytest.raises(ValueError, match="episode-bound"):
        _member_exit_lifecycle_record(episode_id="episode", single_failure=failed, **base)


def test_paired_formal_recovery_revises_after_failed_overload():
    public = build_goal_task_package("warehouse_nvd")["public_task"]
    formal = _formal_goal_recovery_session(
        public, run_id="paired-formal-test", deployment={"sha256": "deployment-test"},
        initial_time_s=16.0)
    session = formal["session"]

    failed = session.complete_skill(
        "formal_overload_crate", time_s=16.0, status="failed",
        receipt={
            "backend_id": "test-overload-backend",
            "receipt_ref": "sha256:test-overload",
            "task_id": "formal_overload_crate",
            "action_id": "formal_dispatch_overload",
            "status": "failed", "completed_at_s": 16.0,
            "episode_id": formal["episode_id"],
            "evidence_refs": ["sha256:test-overload-evidence"],
        })
    assert failed["accepted"] is True
    session.update_runtime_state(16.0, formal["regions"], formal["objects"])

    recovery = _admit_formal_pair_recovery(formal)
    assert all(row["accepted"] for row in recovery["accepted"])
    report = session.report()
    assert report["selected_team"] == ["warehouse_ground_1", "warehouse_ground_3"]
    assert report["tasks"]["formal_overload_crate"]["status"] == "failed"
    assert report["tasks"]["formal_paired_recovery"]["status"] == "pending"
    assert report["tasks"]["formal_paired_recovery"]["task"]["arguments"]["handling_mode_id"] == "paired_support"
    assert report["tasks"]["formal_paired_recovery"]["task"]["execution"]["start_s"] == 16.0

    terminal = _complete_formal_pair_recovery(
        formal, native_report={"object_displacement_m": 0.65}, time_s=30.0)
    assert terminal["completion"]["accepted"] is True
    assert terminal["evidence"]["object_moved"] is True
    assert terminal["evidence"]["task_advanced"] is False
    assert terminal["session_report"]["tasks"]["formal_paired_recovery"]["status"] == "failed"
    assert terminal["session_report"]["business_task_success"] is None
    assert terminal["session_report"]["scoreable"] is False
