from types import SimpleNamespace

import pytest

from algorithm.common_coordination.contract import RobotCapability
from benchmark.evidence import BranchLedger
from benchmark.fleet import (
    DeliveryStatus,
    FleetIdentity,
    FleetLifecycle,
    FleetMessage,
    FleetRuntime,
    LockRequest,
    LockStatus,
    SafeStopPhase,
)
from benchmark.protocol import BranchRole, RunKey


def _runtime(*, suffix="fact"):
    key = RunKey("base", "system", "condition", "repeat", "round", BranchRole(suffix))
    return FleetRuntime(key), key


def _binding():
    return SimpleNamespace(
        session_identity="session-1",
        scene_id="factory",
        robots=(
            SimpleNamespace(capability=RobotCapability("robot-a", frozenset({"navigate"})), metadata={}),
            SimpleNamespace(capability=RobotCapability("robot-b", frozenset({"handoff"})), metadata={}),
        ),
    )


def test_fleet_is_branch_scoped_and_audited():
    first, key = _runtime()
    first.start(SimpleNamespace(env_name="factory"), _binding())
    receipt = first.send(FleetMessage("m1", "robot-a", "robot-b", "handoff", 0, {"ref": "p2"}))
    assert receipt.status is DeliveryStatus.DELIVERED
    assert first.receive("robot-b")[0].payload == {"ref": "p2"}
    second, _ = _runtime(suffix="reference")
    second.start(SimpleNamespace(env_name="factory"), _binding())
    assert second.receive("robot-b") == ()
    assert first.branch_key != second.branch_key
    summary = first.close()
    assert summary.lifecycle is FleetLifecycle.SEALED
    assert any(event.kind == "fleet.runtime_sealed" for event in first.ledger.events)
    assert first.ledger.sealed


def test_typed_locks_reject_conflict_and_expire():
    runtime, _ = _runtime()
    runtime.start(SimpleNamespace(env_name="factory"), _binding())
    acquired = runtime.acquire(LockRequest("r1", "handoff-zone", "robot-a", "exclusive", "release", 0, lease_ticks=2))
    assert acquired.status is LockStatus.ACQUIRED
    conflict = runtime.acquire(LockRequest("r2", "handoff-zone", "robot-b", "exclusive", "accept", 0))
    assert conflict.status is LockStatus.REJECTED
    runtime.record_step(tick=2, sim_time_s=0.04, phase="execution")
    reacquired = runtime.acquire(LockRequest("r3", "handoff-zone", "robot-b", "exclusive", "accept", 2))
    assert reacquired.status is LockStatus.ACQUIRED


def test_p2_coordination_events_are_audited_in_release_handoff_accept_order():
    runtime, _ = _runtime()
    runtime.start(SimpleNamespace(env_name="factory"), _binding())
    lease = runtime.acquire(LockRequest("release-1", "parcel-1", "robot-a", "exclusive", "release", 0))
    runtime.release(lease.lease_id, owner_id="robot-a", tick=1)
    runtime.handoff_request(sender_id="robot-a", receiver_id="robot-b", handoff_ref="parcel-1", tick=2)
    runtime.handoff_accept(receiver_id="robot-b", sender_id="robot-a", handoff_ref="parcel-1", tick=3)
    kinds = [event.kind for event in runtime.ledger.events]
    assert kinds.index("fleet.resource_release") < kinds.index("fleet.handoff_request") < kinds.index("fleet.handoff_accept")
    runtime.close(tick=4)


def test_member_exit_requests_safe_stop_and_close_is_idempotent():
    runtime, _ = _runtime()
    runtime.start(SimpleNamespace(env_name="factory"), _binding())
    pending = runtime.member_exit("robot-a", tick=4)
    assert pending.phase is SafeStopPhase.REQUESTED
    # The exited member is no longer part of the active acknowledgement set.
    stopped = runtime.acknowledge_safe_stop("robot-b", tick=5)
    assert stopped.phase is SafeStopPhase.STOPPED
    summary = runtime.close(tick=5)
    assert summary is runtime.close(tick=99)
    with pytest.raises(RuntimeError):
        runtime.send(FleetMessage("m2", "robot-a", "robot-b", "stop", 6))


def test_scope_cannot_leak_across_branch_or_hidden_session_fields():
    runtime, _ = _runtime()
    binding = _binding()
    binding.hidden_evaluator_state = {"private": True}
    runtime.start(SimpleNamespace(env_name="factory", hidden_evaluator_state={"private": True}), binding)
    snapshot = runtime.snapshot()
    assert "hidden_evaluator_state" not in snapshot
    assert "private" not in str(snapshot)
    with pytest.raises(ValueError):
        runtime.send(FleetMessage("bad", "robot-a", "robot-b", "observation", 0, session_identity="other"))
