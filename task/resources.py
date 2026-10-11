"""Session-local resource leases for task execution.

The manager deliberately owns no simulator lifecycle and performs no timed or
distributed recovery.  A caller must establish quiescence before recovering a
quarantined lease and must then release that lease explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any, Iterable
from uuid import uuid4


class ResourceError(RuntimeError):
    """Base error for resource lease operations."""


class ResourceBusyError(ResourceError):
    """Raised when an owner or requested resource is already leased."""


class InvalidResourceLeaseError(ResourceError, ValueError):
    """Raised for a lease object that was not issued by this manager."""


class ResourceQuarantinedError(ResourceBusyError):
    """Raised when release is attempted before explicit safe recovery."""


@dataclass(frozen=True, slots=True)
class ResourceLease:
    """Opaque manager-issued lease identity plus its public binding."""

    lease_id: str
    owner: str
    resources: tuple[str, ...]


@dataclass(slots=True)
class _LeaseRecord:
    lease: ResourceLease
    state: str = "active"
    quarantine_reason: str | None = None


class ResourceManager:
    """Thread-safe, in-process exclusive resource bookkeeping."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._records: dict[str, _LeaseRecord] = {}
        self._resource_owners: dict[str, str] = {}
        self._owner_leases: dict[str, str] = {}

    @staticmethod
    def _validate_identifier(value: object, *, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be a nonempty string")
        return value

    def acquire(self, owner: str, resources: Iterable[str]) -> ResourceLease:
        """Atomically acquire every requested resource for one owner."""

        owner_id = self._validate_identifier(owner, label="owner")
        if isinstance(resources, (str, bytes)):
            raise ValueError("resources must be an iterable of resource identifiers")
        try:
            requested = tuple(resources)
        except TypeError as exc:
            raise ValueError(
                "resources must be an iterable of resource identifiers"
            ) from exc
        if not requested:
            raise ValueError("resources must contain at least one identifier")
        resource_ids = tuple(
            self._validate_identifier(item, label="resource") for item in requested
        )
        if len(set(resource_ids)) != len(resource_ids):
            raise ValueError("resources must not contain duplicates")

        with self._lock:
            if owner_id in self._owner_leases:
                raise ResourceBusyError(
                    f"owner already has an active lease: {owner_id}"
                )
            conflicts = tuple(
                resource
                for resource in resource_ids
                if resource in self._resource_owners
            )
            if conflicts:
                raise ResourceBusyError(
                    f"resources are already leased: {list(conflicts)}"
                )

            lease = ResourceLease(
                lease_id=uuid4().hex,
                owner=owner_id,
                resources=resource_ids,
            )
            self._records[lease.lease_id] = _LeaseRecord(lease=lease)
            self._owner_leases[owner_id] = lease.lease_id
            for resource in resource_ids:
                self._resource_owners[resource] = lease.lease_id
            return lease

    def release(self, lease: ResourceLease) -> None:
        """Release an authentic active lease.

        Releasing the exact object again is an idempotent no-op.  A quarantined
        lease remains occupied until the caller first confirms quiescence with
        :meth:`recover`.
        """

        with self._lock:
            record = self._authentic_record(lease)
            if record.state == "released":
                return
            if record.state == "quarantined":
                raise ResourceQuarantinedError(
                    "quarantined lease must be recovered before release"
                )
            self._release_record(record)

    def quarantine(self, lease: ResourceLease, reason: str) -> None:
        """Retain an authentic lease while its resource state is uncertain."""

        quarantine_reason = self._validate_identifier(
            reason,
            label="quarantine reason",
        )
        with self._lock:
            record = self._authentic_record(lease)
            if record.state == "released":
                raise InvalidResourceLeaseError("cannot quarantine a released lease")
            if record.state == "quarantined":
                raise ResourceQuarantinedError("lease is already quarantined")
            record.state = "quarantined"
            record.quarantine_reason = quarantine_reason

    def recover(self, lease: ResourceLease, *, quiescent: bool) -> None:
        """Clear quarantine after an explicit exact-``True`` safety signal."""

        if quiescent is not True:
            raise ValueError("quiescent must be exactly True")
        with self._lock:
            record = self._authentic_record(lease)
            if record.state == "released":
                raise InvalidResourceLeaseError("cannot recover a released lease")
            if record.state != "quarantined":
                raise InvalidResourceLeaseError("lease is not quarantined")
            record.state = "active"
            record.quarantine_reason = None

    def snapshot(self) -> dict[str, Any]:
        """Return a detached, JSON-safe view of current occupancy."""

        with self._lock:
            leases = []
            for lease_id in sorted(self._owner_leases.values()):
                record = self._records[lease_id]
                leases.append(
                    {
                        "lease_id": record.lease.lease_id,
                        "owner": record.lease.owner,
                        "resources": list(record.lease.resources),
                        "state": record.state,
                        "quarantine_reason": record.quarantine_reason,
                    }
                )
            return {
                "leases": leases,
                "resources": dict(sorted(self._resource_owners.items())),
            }

    def quarantine_active(self, reason: str) -> tuple[ResourceLease, ...]:
        """Fail closed after a partial runtime restore; never release occupancy."""
        reason = self._validate_identifier(reason, label="quarantine reason")
        with self._lock:
            leases = []
            for lease_id in self._owner_leases.values():
                record = self._records[lease_id]
                if record.state == "active":
                    record.state = "quarantined"
                    record.quarantine_reason = reason
                leases.append(record.lease)
            return tuple(leases)

    def _authentic_record(self, lease: ResourceLease) -> _LeaseRecord:
        if not isinstance(lease, ResourceLease):
            raise InvalidResourceLeaseError("lease was not issued by this manager")
        record = self._records.get(lease.lease_id)
        if record is None or record.lease is not lease:
            raise InvalidResourceLeaseError("lease was not issued by this manager")
        return record

    def _release_record(self, record: _LeaseRecord) -> None:
        lease = record.lease
        for resource in lease.resources:
            if self._resource_owners.get(resource) != lease.lease_id:
                raise RuntimeError("resource lease bookkeeping is inconsistent")
        if self._owner_leases.get(lease.owner) != lease.lease_id:
            raise RuntimeError("owner lease bookkeeping is inconsistent")
        for resource in lease.resources:
            del self._resource_owners[resource]
        del self._owner_leases[lease.owner]
        record.state = "released"
        record.quarantine_reason = None
