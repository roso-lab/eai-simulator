import pytest

from benchmark.protocol import BranchRole, BranchSpec, CheckpointRef, RunKey
from benchmark.replay import PairStatus, PairedBranchCoordinator, PairedBranchPlan, ShamCalibrationReport


def _checkpoint():
    return CheckpointRef("cp0", "episode_step", 4, "artifact://cp0", "digest0", "source-run")


def _plan():
    return PairedBranchPlan.create(
        base_key=RunKey("base", "system", "condition", "r0", "round0", BranchRole.FACT),
        checkpoint=_checkpoint(),
        intervention_id="handoff_policy_v1",
    )


class Backend:
    def __init__(self):
        self.calls = []

    def fork(self, checkpoint, branch):
        self.calls.append((checkpoint.digest, branch.run_key.branch_role))
        return {
            "status": "restored",
            "replay_fidelity": "causal_replay",
            "step_index": checkpoint.step,
            "unrestored_channels": [],
        }


def test_paired_plan_uses_one_checkpoint_and_never_derives_treated_branch():
    plan = _plan()
    backend = Backend()
    coordinator = PairedBranchCoordinator(plan, backend)
    for branch in plan.branches:
        status = coordinator.restore_branch(branch)
        assert status.artifact_retained
    report = coordinator.calibrate_sham({"robot_a": [0.0, 1.0]}, {"robot_a": [0.0, 1.0]})
    assert report.matched
    assert coordinator.strict_paired_status() == (PairStatus.VALID, "strict_paired_claim_admitted")
    assert [role for _, role in backend.calls] == [
        BranchRole.FACT,
        BranchRole.REFERENCE,
        BranchRole.SHAM,
        BranchRole.INTERVENTION,
    ]
    assert all(digest == "digest0" for digest, _ in backend.calls)


def test_sham_mismatch_preserves_artifacts_but_invalidates_pair():
    plan = _plan()
    coordinator = PairedBranchCoordinator(plan, Backend())
    for branch in plan.branches:
        coordinator.restore_branch(branch)
    report = coordinator.calibrate_sham({"robot_a": [0.0]}, {"robot_a": [0.2]}, tolerance=0.01)
    assert report.status is PairStatus.INVALID
    assert coordinator.strict_paired_status()[0] is PairStatus.INVALID
    assert all(status.artifact_retained for status in coordinator.statuses)


def test_physical_state_restore_is_not_strict_pair_support():
    class LimitedBackend:
        def fork(self, checkpoint, branch):
            return {"status": "physical_state_only", "replay_fidelity": "physical_state_only", "step_index": checkpoint.step}

    coordinator = PairedBranchCoordinator(_plan(), LimitedBackend())
    for branch in coordinator.plan.branches:
        coordinator.restore_branch(branch)
    coordinator.calibrate_sham({}, {})
    status, reason = coordinator.strict_paired_status()
    assert status is PairStatus.UNSUPPORTED
    assert "causal_replay" in reason


def test_missing_sham_calibration_is_unsupported():
    report = ShamCalibrationReport.compare(None, {})
    assert report.status is PairStatus.UNSUPPORTED


def test_restore_step_mismatch_cannot_admit_strict_pair():
    class WrongStepBackend:
        def fork(self, checkpoint, branch):
            return {
                "status": "restored",
                "replay_fidelity": "causal_replay",
                "step_index": checkpoint.step + 1,
            }

    coordinator = PairedBranchCoordinator(_plan(), WrongStepBackend())
    for branch in coordinator.plan.branches:
        status = coordinator.restore_branch(branch)
        assert not status.restore.strict_paired_eligible
        assert any("restore_step_mismatch" in item for item in status.restore.diagnostics)


def test_paired_plan_rejects_duplicate_roles_and_forged_branch():
    plan = _plan()
    with pytest.raises(ValueError, match="exactly one"):
        PairedBranchPlan(plan.checkpoint, plan.branches[:-1] + (plan.branches[0],))
    coordinator = PairedBranchCoordinator(plan, Backend())
    forged_key = RunKey("base", "system", "condition", "r0", "round0", BranchRole.FACT)
    forged = BranchSpec(forged_key, _checkpoint(), treatment_id="malicious")
    with pytest.raises(ValueError, match="member"):
        coordinator.restore_branch(forged)
