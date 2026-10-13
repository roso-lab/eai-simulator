import threading
import time

import pytest

from benchmark.branch_execution import BranchExecutionScheduler
from benchmark.protocol import BranchRole, CheckpointRef, RunKey
from benchmark.replay import PairedBranchCoordinator, PairedBranchPlan


def _plan():
    checkpoint = CheckpointRef("cp0", "episode_step", 4, "artifact://cp0", "digest0", "source-run")
    return PairedBranchPlan.create(
        base_key=RunKey("base", "system", "condition", "repeat", "round", BranchRole.FACT),
        checkpoint=checkpoint,
        intervention_id="handoff-v1",
    )


class _Backend:
    def __init__(self, calls):
        self.calls = calls

    def fork(self, checkpoint, branch):
        self.calls.append((threading.get_ident(), branch.run_key.branch_role))
        return {
            "status": "physical_state_only",
            "replay_fidelity": "physical_state_only",
            "step_index": checkpoint.step,
        }


def test_sequential_scheduler_runs_all_branches_after_restore():
    plan = _plan()
    calls = []
    coordinator = PairedBranchCoordinator(plan, _Backend(calls))
    seen = []
    results = BranchExecutionScheduler().run(
        plan,
        coordinator=coordinator,
        worker=lambda branch, status: seen.append((branch.run_key.branch_role, status.restore.status)) or branch.run_key.branch_role.value,
    )
    assert [item.branch.run_key.branch_role for item in results] == [
        BranchRole.FACT, BranchRole.REFERENCE, BranchRole.SHAM, BranchRole.INTERVENTION
    ]
    assert seen == [(role, "physical_state_only") for role in (
        BranchRole.FACT, BranchRole.REFERENCE, BranchRole.SHAM, BranchRole.INTERVENTION
    )]
    assert all(item.completed for item in results)
    assert len(calls) == 4


def test_parallel_scheduler_requires_isolated_coordinator_factory_and_preserves_order():
    plan = _plan()
    created = []

    def factory(branch):
        calls = []
        created.append((branch.run_key.branch_role, calls))
        return PairedBranchCoordinator(plan, _Backend(calls))

    def worker(branch, _status):
        time.sleep(0.005)
        return branch.run_key.branch_role.value

    results = BranchExecutionScheduler(max_workers=4).run(
        plan, coordinator_factory=factory, worker=worker, parallel=True
    )
    assert [item.value for item in results] == [
        "fact", "reference", "sham", "intervention"
    ]
    assert len(created) == 4
    assert all(item.completed for item in results)

    with pytest.raises(ValueError, match="cannot share"):
        BranchExecutionScheduler(max_workers=4).run(
            plan,
            coordinator=PairedBranchCoordinator(plan, _Backend([])),
            coordinator_factory=factory,
            worker=worker,
            parallel=True,
        )


def test_tool_owned_resource_gate_wraps_restore_and_worker_and_releases_on_error():
    plan = _plan()

    class Gate:
        def __init__(self):
            self.events = []

        def acquire(self, branch):
            lease = f"lease:{branch.run_key.branch_role.value}"
            self.events.append(("acquire", branch.run_key.branch_role.value, lease))
            return lease

        def release(self, branch, lease, *, status):
            self.events.append(("release", branch.run_key.branch_role.value, lease, status))

    gate = Gate()
    coordinator = PairedBranchCoordinator(plan, _Backend([]))

    def worker(branch, _status):
        if branch.run_key.branch_role is BranchRole.SHAM:
            raise RuntimeError("worker failed")
        return branch.run_key.branch_role.value

    results = BranchExecutionScheduler().run(plan, coordinator=coordinator, worker=worker, resource_gate=gate)
    assert len(gate.events) == 8
    assert gate.events[0] == ("acquire", "fact", "lease:fact")
    assert gate.events[1] == ("release", "fact", "lease:fact", "completed")
    assert gate.events[4] == ("acquire", "sham", "lease:sham")
    assert gate.events[5] == ("release", "sham", "lease:sham", "worker_error")
    assert results[2].error == "worker_exception:worker failed"


def test_resource_admission_failure_does_not_restore_or_call_worker():
    plan = _plan()
    calls = []

    class Gate:
        def acquire(self, branch):
            raise RuntimeError("gpu capacity")

        def release(self, branch, lease, *, status):
            calls.append((branch, lease, status))

    class Backend(_Backend):
        def fork(self, checkpoint, branch):
            calls.append("restore")
            return super().fork(checkpoint, branch)

    results = BranchExecutionScheduler().run(
        plan,
        coordinator=PairedBranchCoordinator(plan, Backend([])),
        worker=lambda *_: calls.append("worker"),
        resource_gate=Gate(),
    )
    assert all(item.error.startswith("resource_admission_failed:") for item in results)
    assert "restore" not in calls and "worker" not in calls
