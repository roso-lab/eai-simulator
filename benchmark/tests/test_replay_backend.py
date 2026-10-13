from types import SimpleNamespace

from benchmark.protocol import BranchRole, CheckpointRef, RunKey
from benchmark.replay import PairStatus, PairedBranchCoordinator, PairedBranchPlan
from benchmark.replay_backend import PhysicsReplayBackend


def _plan():
    checkpoint = CheckpointRef("cp0", "episode_step", 4, "artifact://cp0", "digest0", "source-run")
    return PairedBranchPlan.create(
        base_key=RunKey("base", "system", "condition", "repeat", "round", BranchRole.FACT),
        checkpoint=checkpoint,
        intervention_id="handoff-v1",
    )


class Coordinator:
    def __init__(self):
        self.steps = []

    def restore(self, step):
        self.steps.append(step)
        return SimpleNamespace(
            step_index=step,
            diagnostics=(),
            physical=SimpleNamespace(
                status="restored",
                step_index=step,
                unrestored_channels=(),
                diagnostics=(),
            ),
        )


def test_physics_backend_restores_same_checkpoint_and_defaults_to_observable_state():
    plan = _plan()
    coordinator = Coordinator()
    treated = []
    backend = PhysicsReplayBackend(
        coordinator,
        checkpoint=plan.checkpoint,
        treatment_applier=lambda branch: treated.append(branch.run_key.branch_role),
    )
    paired = PairedBranchCoordinator(plan, backend)
    for branch in plan.branches:
        paired.restore_branch(branch)
    paired.calibrate_sham({}, {})
    assert coordinator.steps == [4, 4, 4, 4]
    assert treated == [item.run_key.branch_role for item in plan.branches]
    status, reason = paired.strict_paired_status()
    assert status is PairStatus.UNSUPPORTED
    assert "causal_replay" in reason


def test_physics_backend_requires_explicit_fidelity_provider_for_causal_pair():
    plan = _plan()
    backend = PhysicsReplayBackend(
        Coordinator(),
        checkpoint=plan.checkpoint,
        fidelity_provider=lambda _report, _branch: "causal_replay",
    )
    paired = PairedBranchCoordinator(plan, backend)
    for branch in plan.branches:
        paired.restore_branch(branch)
    paired.calibrate_sham({"pose": [0.0]}, {"pose": [0.0]})
    assert paired.strict_paired_status() == (PairStatus.VALID, "strict_paired_claim_admitted")


def test_physics_backend_accepts_serialized_coordinator_report():
    plan = _plan()

    class MappingCoordinator:
        def restore(self, step):
            return {
                "step_index": step,
                "physical": {
                    "status": "physical_state_only",
                    "step_index": step,
                    "unrestored_channels": [],
                    "diagnostics": [],
                },
                "diagnostics": [],
            }

    backend = PhysicsReplayBackend(MappingCoordinator(), checkpoint=plan.checkpoint)
    report = backend.fork(plan.checkpoint, plan.branches[0])
    assert report["status"] == "physical_state_only"
    assert report["step_index"] == plan.checkpoint.step


def test_physics_backend_adapts_live_checkpoint_readback_without_claiming_causal_replay():
    plan = _plan()

    class LiveStyleCoordinator:
        def restore(self, step):
            return {
                "checkpoint_id": 0,
                "step_index": step,
                "passed": True,
                "phase": "readback",
                "fidelity": "observable_state_checkpoint",
                "readback_errors": {},
            }

    backend = PhysicsReplayBackend(LiveStyleCoordinator(), checkpoint=plan.checkpoint)
    report = backend.fork(plan.checkpoint, plan.branches[0])
    assert report["status"] == "physical_state_only"
    assert report["replay_fidelity"] == "physical_state_only"
    assert report["step_index"] == plan.checkpoint.step
