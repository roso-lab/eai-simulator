"""Public history must drive actual dispatch and checkpoint admissibility."""
import pytest
import json
import os

from tools.validation.project_local_task_reconstruction import PublicCoordinator, assert_active_boundary
from benchmark.fleet import LockRequest, TypedLockManager


def test_dispatch_waits_for_delivery_and_checkpoint_has_inflight_history():
    coordinator = PublicCoordinator()
    for tick in range(120):
        dispatch = coordinator.advance(tick)
        assert dispatch is (tick == 42)
        if dispatch:
            coordinator.dispatched()
    state = coordinator.snapshot()
    assert state["started"] and state["locks"]["counter"] == 1
    assert state["transport"]["in_flight"][0]["message_id"] == "public/119"
    assert state["transport"]["counters"]["transmitted_bits"] > 0
    assert {row["event"] for row in state["events"]} == {
        "submitted", "delivered", "consumed", "navigation_dispatched"}
    state["events"].clear()
    assert coordinator.snapshot()["events"]
    with pytest.raises(ValueError, match="exactly once"):
        coordinator.advance(119)


def test_lock_measurement_preserves_future_allocation_state_after_release():
    locks = TypedLockManager(session_identity="s", branch_key="b")
    before = locks.measurement_snapshot()
    lease = locks.acquire(LockRequest("req", "route", "robot", "exclusive", "test", 0))
    locks.release(lease.lease_id, owner_id="robot", tick=1)
    after = locks.measurement_snapshot()
    assert before["locks"] == after["locks"] == []
    assert before["counter"] == 0 and after["counter"] == 1


@pytest.mark.parametrize("status,prepared,error", [
    ("completed", False, "active task"), ("pending", True, "prepared physical step"),
])
def test_invalid_active_boundary_is_rejected(status, prepared, error):
    class Executor:
        active_task_ids = ("t",)
        def response(self, task_id):
            class Response:
                state = {"status": status, "quarantined": False}
            return Response()
        def needs_host_tick(self, task_id):
            return prepared
    with pytest.raises(RuntimeError, match=error):
        assert_active_boundary(Executor(), "t", PublicCoordinator())


def test_live_release_requires_bound_independent_full_sham(tmp_path):
    from tools.validation.project_local_task_reconstruction import validate_sham, digest
    original = tmp_path / "journal"
    original.mkdir()
    (original / "reconstruction.json").write_text(json.dumps({"pid": -1}))
    path = tmp_path / "sham.json"
    payload = {"passed": True, "status": "verified", "pid": -2,
        "inputs_unchanged": True, "fingerprint": {"source": "v1"},
        "reference_manifest_sha256": digest(original / "reconstruction.json"),
        "verification": {"prefix_verified": True, "original_continuation_verified": True,
            "released_for_live_continuation": False, "compared_steps": 160,
            "target_steps": 120, "first_divergence": None}}
    path.write_text(json.dumps(payload))
    assert validate_sham(path, {"source": "v1"}, original) == digest(path)
    for field, value in (("passed", False), ("inputs_unchanged", False),
                         ("pid", -1), ("pid", os.getpid()),
                         ("reference_manifest_sha256", "other")):
        changed = {**payload, field: value}
        path.write_text(json.dumps(changed))
        with pytest.raises(ValueError, match="independent sham"):
            validate_sham(path, {"source": "v1"}, original)
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="independent sham"):
        validate_sham(path, {"source": "v2"}, original)
    payload["verification"]["original_continuation_verified"] = False
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="independent sham"):
        validate_sham(path, {"source": "v1"}, original)
