import pytest

from demo.benchmark.goal_checkpoint_branch import (
    branch_rng_digest,
    build_branch_plan,
    build_branch_replay_report,
    merge_sham_into_comparison,
    validate_branch_plan,
)


def _plan(branch_id, seed=7, *, stream_key="change-1", change_time=10.0,
          change_sequence=50, checkpoint_time=9.0, checkpoint_sequence=40):
    return build_branch_plan(
        branch_id, seed=seed, rng_stream_key=stream_key,
        changes=[{"change_id": "replan-1", "time_s": change_time,
                  "sequence": change_sequence,
                  "pre_change_checkpoint_id": "cp-1"}],
        checkpoints=[{"checkpoint_id": "cp-1", "time_s": checkpoint_time,
                      "sequence": checkpoint_sequence}])


def test_valid_plan_freezes_before_change_discipline():
    plan = _plan("treatment-a")
    assert plan["before_change_snapshot_discipline"] == "verified_at_build_time"
    assert plan["native_restore_verified"] is False
    assert validate_branch_plan(plan)["branch_id"] == "treatment-a"


def test_checkpoint_at_or_after_change_is_rejected():
    with pytest.raises(ValueError, match="not before its change"):
        _plan("late-checkpoint", change_time=9.0, checkpoint_time=9.0)
    with pytest.raises(ValueError, match="not before its change"):
        _plan("late-sequence", change_sequence=40, checkpoint_sequence=40)


def test_missing_checkpoint_reference_is_rejected():
    with pytest.raises(ValueError, match="unknown checkpoint"):
        build_branch_plan(
            "dangling", seed=1,
            changes=[{"change_id": "c", "time_s": 10.0, "sequence": 5,
                      "pre_change_checkpoint_id": "ghost"}],
            checkpoints=[{"checkpoint_id": "cp-1", "time_s": 1.0, "sequence": 1}])


def test_tampered_plan_digest_is_rejected():
    plan = _plan("treatment-a")
    plan["seed"] = 99
    with pytest.raises(ValueError):
        validate_branch_plan(plan)


def test_same_seed_same_stream_different_stream_key_diverges():
    left = branch_rng_digest(7, "change-1")
    right = branch_rng_digest(7, "change-1")
    other = branch_rng_digest(7, "change-2")
    assert left == right
    assert left != other


def test_strict_comparison_requires_identical_rng_replay():
    # Treatment and control of the same compared change share the stream key.
    treatment = _plan("treatment-a", seed=7, stream_key="change-1")
    control_same = _plan("control-b", seed=7, stream_key="change-1")
    control_other = _plan("control-c", seed=8, stream_key="change-1")
    control_other_key = _plan("control-d", seed=7, stream_key="change-2")
    same = build_branch_replay_report(treatment=treatment, control=control_same)
    assert same["rng_replay_identical"] is True
    assert same["strict_branch_comparison_eligible"] is True
    different = build_branch_replay_report(treatment=treatment, control=control_other)
    assert different["rng_replay_identical"] is False
    assert different["strict_branch_comparison_eligible"] is False
    mismatched_key = build_branch_replay_report(treatment=treatment,
                                                control=control_other_key)
    assert mismatched_key["rng_replay_identical"] is False
    assert mismatched_key["strict_branch_comparison_eligible"] is False


def test_sham_control_is_isolated_as_diagnostic():
    treatment = _plan("treatment-a", seed=7)
    control = _plan("control-b", seed=7)
    sham = _plan("sham-c", seed=7)
    report = build_branch_replay_report(treatment=treatment, control=control,
                                        sham_control=sham)
    assert report["sham_isolation"]["present"] is True
    assert report["sham_isolation"]["evidence_class"] == "diagnostic"
    assert report["sham_isolation"]["merged_into_strict_comparison"] is False
    assert report["scoreable"] is False
    with pytest.raises(ValueError, match="diagnostic-only"):
        merge_sham_into_comparison(report)


def test_report_stays_on_development_track():
    report = build_branch_replay_report(treatment=_plan("t", 3),
                                        control=_plan("c", 3))
    assert report["development_track"]["evidence_class"] == "contract"
    assert report["native_restore_verified"] is False
    assert report["infrastructure_blockers"] == [
        "native_restore_verification_pending"]
    assert report["formal_statistics"] is False
