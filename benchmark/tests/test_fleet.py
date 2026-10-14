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
    assert receipt.status is DeliveryStatus.QUEUED
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


def test_member_exit_is_local_survivor_continues_and_close_never_auto_acks():
    runtime, _ = _runtime()
    binding = _binding()
    binding.robots += (SimpleNamespace(capability=RobotCapability("reserve", frozenset({"navigate"}), False)),)
    runtime.start(SimpleNamespace(env_name="factory"), binding)
    assert runtime.member_exit("robot-a", tick=4).phase is SafeStopPhase.REQUESTED
    assert runtime.lifecycle is FleetLifecycle.RUNNING
    receipt = runtime.send(FleetMessage("survive", "robot-b", "robot-b", "local", 5))
    assert receipt.status is DeliveryStatus.QUEUED
    runtime.member_join(FleetIdentity("reserve", runtime.session_identity, runtime.branch_key, frozenset({"navigate"})), tick=5)
    with pytest.raises(KeyError):
        runtime.member_join(FleetIdentity("new-body", runtime.session_identity, runtime.branch_key), tick=5)
    summary = runtime.close(tick=5)
    assert set(summary.safe_stop_status["pending_robot_ids"]) == {"robot-a", "robot-b", "reserve"}
    assert summary is runtime.close(tick=99)
    with pytest.raises(RuntimeError):
        runtime.send(FleetMessage("m2", "robot-a", "robot-b", "stop", 6))


def test_global_stop_requires_all_physical_members_and_measured_evidence():
    runtime, _ = _runtime()
    runtime.start(SimpleNamespace(), _binding())
    runtime.member_exit("robot-a", tick=1)
    runtime.request_safe_stop(tick=2)
    with pytest.raises(ValueError):
        runtime.acknowledge_safe_stop("robot-b", tick=3, evidence_hash="")
    runtime.acknowledge_safe_stop("robot-b", tick=3, evidence_hash="measured-b")
    assert runtime.lifecycle is FleetLifecycle.STOP_REQUESTED
    runtime.acknowledge_safe_stop("robot-a", tick=3, evidence_hash="measured-a")
    assert runtime.lifecycle is FleetLifecycle.STOPPED


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


def test_transport_delay_ttl_reconnection_and_logical_bit_accounting():
    from benchmark.fleet import MessageRouter
    router = MessageRouter(session_identity="s", branch_key="b", delay_ticks=2, ttl_ticks=5)
    router.send(FleetMessage("m", "a", "b", "notice", 1, {}, session_identity="s", branch_key="b"))
    assert router.receive("b", tick=2) == ()
    router.set_link_available("a", "b", False)
    assert router.receive("b", tick=3) == ()
    router.set_link_available("a", "b", True)
    assert router.receive("b", tick=6) == ()  # restore does not renew TTL
    assert router.offered_bits == router.transmitted_bits > 0
    with pytest.raises(ValueError, match="duplicate"):
        router.send(FleetMessage("m", "a", "b", "notice", 7, {}, session_identity="s", branch_key="b"))


def test_transport_bandwidth_loss_and_selective_consumption():
    from benchmark.fleet import MessageRouter
    router = MessageRouter(session_identity="s", branch_key="b", bits_per_tick=8)
    for mid in ("notice", "assignment"):
        router.send(FleetMessage(mid, "a", "b", mid, 0, {}, session_identity="s", branch_key="b"))
    assert router.receive("b", tick=1) == ()
    assert router.receive("b", tick=1000, message_id="assignment")[0].message_id == "assignment"
    assert router.receive("b", tick=1000)[0].message_id == "notice"
    lost = MessageRouter(session_identity="s", branch_key="b", loss_probability=1)
    assert lost.send(FleetMessage("loss", "a", "b", "x", 0, {}, session_identity="s", branch_key="b")).status is DeliveryStatus.DROPPED
    assert lost.receive("b") == ()
