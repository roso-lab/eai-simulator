from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from task.resources import (
    InvalidResourceLeaseError,
    ResourceBusyError,
    ResourceLease,
    ResourceManager,
    ResourceQuarantinedError,
)


def test_acquire_is_atomic_on_contention_and_rejects_same_owner_overlap():
    manager = ResourceManager()
    first = manager.acquire("worker-a", ["robot:r1"])

    with pytest.raises(ResourceBusyError, match="already leased"):
        manager.acquire("worker-b", ["robot:r2", "robot:r1"])

    # The failed multi-resource request must not retain its conflict-free item.
    second = manager.acquire("worker-c", ["robot:r2"])
    with pytest.raises(ResourceBusyError, match="already has"):
        manager.acquire("worker-a", ["robot:r3"])

    manager.release(first)
    manager.release(second)


@pytest.mark.parametrize(
    ("owner", "resources"),
    [
        ("", ["robot:r1"]),
        ("   ", ["robot:r1"]),
        ("worker", []),
        ("worker", [""]),
        ("worker", ["   "]),
        ("worker", ["robot:r1", "robot:r1"]),
        ("worker", "robot:r1"),
    ],
)
def test_acquire_rejects_invalid_or_duplicate_identifiers(owner, resources):
    with pytest.raises(ValueError):
        ResourceManager().acquire(owner, resources)


def test_thread_race_grants_exactly_one_lease():
    manager = ResourceManager()
    barrier = Barrier(2)

    def contend(owner):
        barrier.wait()
        try:
            return manager.acquire(owner, ["shared-arm"])
        except ResourceBusyError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(contend, ("worker-a", "worker-b")))

    winners = [lease for lease in results if lease is not None]
    assert len(winners) == 1
    assert manager.snapshot()["resources"] == {
        "shared-arm": winners[0].lease_id,
    }


def test_forged_unknown_and_stale_tokens_cannot_release_another_lease():
    manager = ResourceManager()
    old = manager.acquire("worker-a", ["robot:r1"])
    forged = ResourceLease(old.lease_id, old.owner, old.resources)
    unknown = ResourceLease("unknown", "worker-a", old.resources)

    with pytest.raises(InvalidResourceLeaseError):
        manager.release(forged)
    with pytest.raises(InvalidResourceLeaseError):
        manager.release(unknown)

    manager.release(old)
    current = manager.acquire("worker-b", ["robot:r1"])
    manager.release(old)  # authentic, already-released tokens are idempotent

    with pytest.raises(ResourceBusyError):
        manager.acquire("worker-c", ["robot:r1"])
    assert manager.snapshot()["resources"]["robot:r1"] == current.lease_id


def test_quarantine_retains_occupancy_until_recovery_then_explicit_release():
    manager = ResourceManager()
    lease = manager.acquire("worker-a", ["robot:r1", "arm:r1"])
    manager.quarantine(lease, "executor stop is not confirmed")

    with pytest.raises(ResourceQuarantinedError):
        manager.release(lease)
    with pytest.raises(ResourceBusyError):
        manager.acquire("worker-b", ["arm:r1"])
    with pytest.raises(ValueError, match="exactly True"):
        manager.recover(lease, quiescent=1)

    snapshot = manager.snapshot()
    assert snapshot["leases"][0]["state"] == "quarantined"
    assert snapshot["leases"][0]["quarantine_reason"] == (
        "executor stop is not confirmed"
    )

    manager.recover(lease, quiescent=True)
    assert manager.snapshot()["leases"][0]["state"] == "active"
    with pytest.raises(ResourceBusyError):
        manager.acquire("worker-b", ["robot:r1"])

    manager.release(lease)
    replacement = manager.acquire("worker-b", ["robot:r1"])
    assert replacement.owner == "worker-b"


def test_release_is_idempotent_only_for_authentic_released_lease():
    manager = ResourceManager()
    lease = manager.acquire("worker-a", ["robot:r1"])

    manager.release(lease)
    manager.release(lease)

    assert manager.snapshot() == {"leases": [], "resources": {}}
    with pytest.raises(InvalidResourceLeaseError):
        manager.release(ResourceLease(lease.lease_id, lease.owner, lease.resources))


def test_validate_lease_requires_exact_active_object_and_resource_coverage():
    manager = ResourceManager()
    lease = manager.acquire("worker-a", ["robot:r1", "object:box"])

    assert manager.validate_lease(
        lease,
        required_resources=("robot:r1", "object:box"),
    ) is lease
    with pytest.raises(InvalidResourceLeaseError, match="does not cover"):
        manager.validate_lease(lease, required_resources=("robot:r2",))
    with pytest.raises(InvalidResourceLeaseError, match="not issued"):
        manager.validate_lease(
            ResourceLease(lease.lease_id, lease.owner, lease.resources),
        )

    manager.quarantine(lease, "runtime stop is unresolved")
    with pytest.raises(ResourceQuarantinedError, match="quarantined"):
        manager.validate_lease(lease)
    manager.recover(lease, quiescent=True)
    manager.release(lease)
    with pytest.raises(InvalidResourceLeaseError, match="not active"):
        manager.validate_lease(lease)


def test_snapshot_is_detached_and_json_safe():
    manager = ResourceManager()
    lease = manager.acquire("worker-a", ["arm:r1", "robot:r1"])
    snapshot = manager.snapshot()

    json.dumps(snapshot)
    snapshot["leases"][0]["owner"] = "tampered"
    snapshot["leases"][0]["resources"].clear()
    snapshot["resources"].clear()

    fresh = manager.snapshot()
    assert fresh["leases"][0]["owner"] == "worker-a"
    assert fresh["leases"][0]["resources"] == ["arm:r1", "robot:r1"]
    assert fresh["resources"] == {
        "arm:r1": lease.lease_id,
        "robot:r1": lease.lease_id,
    }
