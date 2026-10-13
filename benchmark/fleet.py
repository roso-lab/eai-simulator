"""Branch-scoped Fleet Service Runtime.

The fleet service is deliberately dependency-light.  It owns the public
coordination substrate around an episode (identity, messages, leases and
safe-stop) but never plans a task, drives Isaac, or decides a physical
milestone.  Every mutation is mirrored into the branch-local
``BranchLedger`` so the same trace can be audited with the episode artifact.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from threading import RLock
from typing import Any, Callable, Mapping, Sequence

from algorithm.common_coordination.contract import RobotCapability

from .evidence import BranchLedger
from .protocol import BranchRole, BranchSpec, RunKey


class FleetLifecycle(str, Enum):
    CREATED = "created"
    BOUND = "bound"
    RUNNING = "running"
    STOP_REQUESTED = "stop_requested"
    STOPPED = "stopped"
    SEALED = "sealed"


class DeliveryStatus(str, Enum):
    DELIVERED = "delivered"
    DELAYED = "delayed"
    DROPPED = "dropped"
    REJECTED = "rejected"


class LockStatus(str, Enum):
    ACQUIRED = "acquired"
    REJECTED = "rejected"
    RELEASED = "released"
    EXPIRED = "expired"


class SafeStopPhase(str, Enum):
    IDLE = "idle"
    REQUESTED = "requested"
    STOPPED = "stopped"


def _required(value: Any, name: str) -> str:
    result = str(value).strip()
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FleetIdentity:
    robot_id: str
    session_identity: str
    branch_key: str
    capabilities: frozenset[str] = frozenset()
    active: bool = True
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "robot_id", _required(self.robot_id, "robot_id"))
        object.__setattr__(self, "session_identity", _required(self.session_identity, "session_identity"))
        object.__setattr__(self, "branch_key", _required(self.branch_key, "branch_key"))
        object.__setattr__(self, "capabilities", frozenset(str(item).strip() for item in self.capabilities if str(item).strip()))
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class FleetMessage:
    message_id: str
    sender_id: str
    receiver_id: str
    kind: str
    tick: int
    payload: Mapping[str, Any] = field(default_factory=dict)
    causal_parent: str | None = None
    session_identity: str = ""
    branch_key: str = ""

    def __post_init__(self) -> None:
        for name in ("message_id", "sender_id", "receiver_id", "kind"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        if self.tick < 0:
            raise ValueError("message tick cannot be negative")
        object.__setattr__(self, "payload", dict(self.payload))
        object.__setattr__(self, "causal_parent", None if self.causal_parent is None else str(self.causal_parent))


@dataclass(frozen=True)
class DeliveryReceipt:
    message_id: str
    receiver_id: str
    status: DeliveryStatus
    tick: int
    queue_depth: int = 0
    reason: str = ""
    session_identity: str = ""
    branch_key: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", DeliveryStatus(self.status))


@dataclass(frozen=True)
class LockRequest:
    request_id: str
    resource_id: str
    owner_id: str
    lock_type: str
    purpose: str
    tick: int
    lease_ticks: int | None = None
    session_identity: str = ""
    branch_key: str = ""

    def __post_init__(self) -> None:
        for name in ("request_id", "resource_id", "owner_id", "lock_type", "purpose"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        if self.tick < 0:
            raise ValueError("lock tick cannot be negative")
        if self.lease_ticks is not None and int(self.lease_ticks) <= 0:
            raise ValueError("lease_ticks must be positive")
        object.__setattr__(self, "lease_ticks", None if self.lease_ticks is None else int(self.lease_ticks))


@dataclass(frozen=True)
class LockState:
    lease_id: str
    resource_id: str
    owner_id: str
    lock_type: str
    purpose: str
    acquired_tick: int
    expires_tick: int | None = None
    session_identity: str = ""
    branch_key: str = ""


@dataclass(frozen=True)
class LockReceipt:
    lease_id: str
    resource_id: str
    owner_id: str
    status: LockStatus
    tick: int
    expires_tick: int | None = None
    reason: str = ""
    session_identity: str = ""
    branch_key: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", LockStatus(self.status))


@dataclass(frozen=True)
class SafeStopRequest:
    request_id: str
    source_id: str
    tick: int
    reason: str
    session_identity: str = ""
    branch_key: str = ""

    def __post_init__(self) -> None:
        for name in ("request_id", "source_id", "reason"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        if self.tick < 0:
            raise ValueError("safe-stop tick cannot be negative")


@dataclass(frozen=True)
class SafeStopReceipt:
    request_id: str
    phase: SafeStopPhase
    tick: int
    reason: str
    pending_robot_ids: tuple[str, ...] = ()
    session_identity: str = ""
    branch_key: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "phase", SafeStopPhase(self.phase))
        object.__setattr__(self, "pending_robot_ids", tuple(str(item) for item in self.pending_robot_ids))


@dataclass(frozen=True)
class SafeStopStatus:
    phase: SafeStopPhase
    request_id: str | None
    requested_tick: int | None
    acknowledged_robot_ids: tuple[str, ...]
    pending_robot_ids: tuple[str, ...]
    reason: str = ""


@dataclass(frozen=True)
class FleetAuditEvent:
    sequence: int
    kind: str
    tick: int
    source: str
    payload: Mapping[str, Any]
    event_hash: str


@dataclass(frozen=True)
class FleetAuditSummary:
    lifecycle: FleetLifecycle
    session_identity: str
    branch_key: str
    event_count: int
    ledger_digest: str
    communication_count: int
    lock_count: int
    safe_stop_request_ids: tuple[str, ...]
    active_members: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "lifecycle": self.lifecycle.value,
            "session_identity": self.session_identity,
            "branch_key": self.branch_key,
            "event_count": self.event_count,
            "ledger_digest": self.ledger_digest,
            "communication_count": self.communication_count,
            "lock_count": self.lock_count,
            "safe_stop_request_ids": list(self.safe_stop_request_ids),
            "active_members": list(self.active_members),
        }


class FleetAuditSink:
    """Append fleet events to one branch-local ledger."""

    def __init__(self, ledger: BranchLedger, *, session_identity: str, branch_key: str):
        self.ledger = ledger
        self.session_identity = _required(session_identity, "session_identity")
        self.branch_key = _required(branch_key, "branch_key")
        self._events: list[FleetAuditEvent] = []

    @property
    def events(self) -> tuple[FleetAuditEvent, ...]:
        return tuple(self._events)

    def append(self, kind: str, *, tick: int, source: str, payload: Mapping[str, Any] | None = None) -> FleetAuditEvent:
        if self.ledger.sealed:
            raise RuntimeError("cannot append fleet audit after branch ledger is sealed")
        event_payload = {
            "session_identity": self.session_identity,
            "branch_key": self.branch_key,
            **dict(payload or {}),
        }
        event = self.ledger.append(f"fleet.{_required(kind, 'kind')}", tick=tick, source=source, payload=event_payload)
        audit = FleetAuditEvent(
            sequence=event.sequence,
            kind=kind,
            tick=tick,
            source=source,
            payload=event_payload,
            event_hash=event.event_hash,
        )
        self._events.append(audit)
        return audit


class FleetIdentityRegistry:
    """Registry of the public robot identities in one branch scope."""

    def __init__(self, *, session_identity: str, branch_key: str, audit: FleetAuditSink | None = None):
        self.session_identity = _required(session_identity, "session_identity")
        self.branch_key = _required(branch_key, "branch_key")
        self.audit = audit
        self._items: dict[str, FleetIdentity] = {}

    def register(self, identity: FleetIdentity, *, tick: int = 0) -> None:
        self._check_scope(identity.session_identity, identity.branch_key)
        if identity.robot_id in self._items:
            raise ValueError(f"robot identity already registered: {identity.robot_id}")
        self._items[identity.robot_id] = identity
        self._record("member_join", tick, identity.robot_id, {"active": identity.active, "capabilities": sorted(identity.capabilities)})

    def join(self, identity: FleetIdentity, *, tick: int = 0) -> None:
        if identity.robot_id in self._items:
            existing = self._items[identity.robot_id]
            if existing.active:
                raise ValueError(f"active robot identity already registered: {identity.robot_id}")
            self._items[identity.robot_id] = identity
            self._record("member_join", tick, identity.robot_id, {"reactivated": True})
            return
        self.register(identity, tick=tick)

    def mark_active(self, robot_id: str, *, tick: int, reason: str = "") -> FleetIdentity:
        identity = self._get(robot_id)
        updated = FleetIdentity(identity.robot_id, identity.session_identity, identity.branch_key, identity.capabilities, True, identity.metadata)
        self._items[identity.robot_id] = updated
        self._record("member_active", tick, identity.robot_id, {"reason": reason})
        return updated

    def mark_inactive(self, robot_id: str, *, tick: int, reason: str = "") -> FleetIdentity:
        identity = self._get(robot_id)
        updated = FleetIdentity(identity.robot_id, identity.session_identity, identity.branch_key, identity.capabilities, False, identity.metadata)
        self._items[identity.robot_id] = updated
        self._record("member_exit", tick, identity.robot_id, {"reason": reason})
        return updated

    def get(self, robot_id: str) -> FleetIdentity:
        return self._get(robot_id)

    def snapshot(self) -> tuple[FleetIdentity, ...]:
        return tuple(self._items[key] for key in sorted(self._items))

    def active_ids(self) -> tuple[str, ...]:
        return tuple(item.robot_id for item in self.snapshot() if item.active)

    def _get(self, robot_id: str) -> FleetIdentity:
        key = _required(robot_id, "robot_id")
        try:
            return self._items[key]
        except KeyError as exc:
            raise KeyError(f"unknown fleet robot: {key}") from exc

    def _check_scope(self, session_identity: str, branch_key: str) -> None:
        if session_identity != self.session_identity or branch_key != self.branch_key:
            raise ValueError("fleet identity scope mismatch")

    def _record(self, kind: str, tick: int, robot_id: str, payload: Mapping[str, Any]) -> None:
        if self.audit is not None:
            self.audit.append(kind, tick=tick, source="fleet.identity", payload={"robot_id": robot_id, **dict(payload)})


class MessageRouter:
    """FIFO branch-local message router with typed delivery receipts."""

    def __init__(self, *, session_identity: str, branch_key: str, audit: FleetAuditSink | None = None, max_queue: int = 256):
        self.session_identity = _required(session_identity, "session_identity")
        self.branch_key = _required(branch_key, "branch_key")
        if max_queue <= 0:
            raise ValueError("max_queue must be positive")
        self.max_queue = int(max_queue)
        self.audit = audit
        self._queues: dict[str, deque[FleetMessage]] = {}
        self._closed = False

    def send(self, message: FleetMessage, *, active_receivers: Sequence[str] | None = None) -> DeliveryReceipt:
        if self._closed:
            return self._receipt(message, DeliveryStatus.REJECTED, "router_sealed")
        self._check_scope(message.session_identity, message.branch_key)
        if active_receivers is not None and message.receiver_id not in set(active_receivers):
            return self._receipt(message, DeliveryStatus.REJECTED, "receiver_inactive")
        queue = self._queues.setdefault(message.receiver_id, deque())
        if len(queue) >= self.max_queue:
            receipt = self._receipt(message, DeliveryStatus.DROPPED, "receiver_queue_full")
            self._record("message_dropped", message.tick, message, receipt.status.value)
            return receipt
        queue.append(message)
        receipt = self._receipt(message, DeliveryStatus.DELIVERED, "queued")
        self._record("message_sent", message.tick, message, receipt.status.value)
        return receipt

    def receive(self, receiver_id: str, *, max_items: int = 1, tick: int = 0) -> tuple[FleetMessage, ...]:
        if self._closed:
            raise RuntimeError("message router is sealed")
        if max_items <= 0:
            raise ValueError("max_items must be positive")
        queue = self._queues.setdefault(_required(receiver_id, "receiver_id"), deque())
        result = tuple(queue.popleft() for _ in range(min(max_items, len(queue))))
        for message in result:
            self._record("message_received", tick, message, "received")
        return result

    def close(self) -> None:
        self._closed = True

    def pending(self, receiver_id: str | None = None) -> int:
        if receiver_id is not None:
            return len(self._queues.get(str(receiver_id), ()))
        return sum(len(queue) for queue in self._queues.values())

    def _check_scope(self, session_identity: str, branch_key: str) -> None:
        # Empty scope values are accepted at the leaf boundary; FleetRuntime
        # injects the authoritative scope before calling this method.
        if session_identity and session_identity != self.session_identity:
            raise ValueError("message session scope mismatch")
        if branch_key and branch_key != self.branch_key:
            raise ValueError("message branch scope mismatch")

    def _receipt(self, message: FleetMessage, status: DeliveryStatus, reason: str) -> DeliveryReceipt:
        return DeliveryReceipt(message.message_id, message.receiver_id, status, message.tick, self.pending(message.receiver_id), reason, self.session_identity, self.branch_key)

    def _record(self, kind: str, tick: int, message: FleetMessage, status: str) -> None:
        if self.audit is not None:
            self.audit.append(kind, tick=tick, source="fleet.router", payload={"message_id": message.message_id, "sender_id": message.sender_id, "receiver_id": message.receiver_id, "message_kind": message.kind, "status": status, "causal_parent": message.causal_parent})


class TypedLockManager:
    """Typed exclusive/shared/handoff leases with deterministic expiry."""

    def __init__(self, *, session_identity: str, branch_key: str, audit: FleetAuditSink | None = None):
        self.session_identity = _required(session_identity, "session_identity")
        self.branch_key = _required(branch_key, "branch_key")
        self.audit = audit
        self._locks: dict[str, LockState] = {}
        self._counter = 0

    def acquire(self, request: LockRequest, *, active_owners: Sequence[str] | None = None) -> LockReceipt:
        self._check_scope(request.session_identity, request.branch_key)
        self.expire(request.tick)
        if active_owners is not None and request.owner_id not in set(active_owners):
            return self._reject(request, "owner_inactive")
        existing = [item for item in self._locks.values() if item.resource_id == request.resource_id]
        if any(self._conflicts(request.lock_type, item.lock_type, request.owner_id == item.owner_id) for item in existing):
            return self._reject(request, "resource_conflict")
        self._counter += 1
        lease_id = f"lease-{self._counter:06d}-{_digest((request.request_id, request.resource_id, request.owner_id))[:10]}"
        expires = request.tick + request.lease_ticks if request.lease_ticks is not None else None
        state = LockState(lease_id, request.resource_id, request.owner_id, request.lock_type, request.purpose, request.tick, expires, self.session_identity, self.branch_key)
        self._locks[lease_id] = state
        self._record("lock_acquired", request.tick, {"lease_id": lease_id, "resource_id": request.resource_id, "owner_id": request.owner_id, "lock_type": request.lock_type, "purpose": request.purpose, "expires_tick": expires})
        return LockReceipt(lease_id, request.resource_id, request.owner_id, LockStatus.ACQUIRED, request.tick, expires, "acquired", self.session_identity, self.branch_key)

    def release(self, lease_id: str, *, owner_id: str, tick: int, force: bool = False) -> LockReceipt:
        state = self._locks.get(_required(lease_id, "lease_id"))
        if state is None:
            return LockReceipt(lease_id, "", owner_id, LockStatus.REJECTED, tick, reason="unknown_lease", session_identity=self.session_identity, branch_key=self.branch_key)
        if state.owner_id != owner_id and not force:
            return LockReceipt(state.lease_id, state.resource_id, state.owner_id, LockStatus.REJECTED, tick, state.expires_tick, "owner_mismatch", self.session_identity, self.branch_key)
        del self._locks[state.lease_id]
        self._record("lock_released", tick, {"lease_id": state.lease_id, "resource_id": state.resource_id, "owner_id": state.owner_id, "forced": force})
        return LockReceipt(state.lease_id, state.resource_id, state.owner_id, LockStatus.RELEASED, tick, state.expires_tick, "released", self.session_identity, self.branch_key)

    def expire(self, tick: int) -> tuple[LockReceipt, ...]:
        expired: list[LockReceipt] = []
        for lease_id, state in tuple(self._locks.items()):
            if state.expires_tick is not None and tick >= state.expires_tick:
                del self._locks[lease_id]
                self._record("lock_expired", tick, {"lease_id": lease_id, "resource_id": state.resource_id, "owner_id": state.owner_id})
                expired.append(LockReceipt(state.lease_id, state.resource_id, state.owner_id, LockStatus.EXPIRED, tick, state.expires_tick, "lease_expired", self.session_identity, self.branch_key))
        return tuple(expired)

    def snapshot(self) -> tuple[LockState, ...]:
        return tuple(self._locks[key] for key in sorted(self._locks))

    @staticmethod
    def _conflicts(requested: str, existing: str, same_owner: bool) -> bool:
        requested = requested.lower()
        existing = existing.lower()
        if same_owner and requested == existing == "shared":
            return False
        return not (requested == "shared" and existing == "shared")

    def _reject(self, request: LockRequest, reason: str) -> LockReceipt:
        self._record("lock_rejected", request.tick, {"request_id": request.request_id, "resource_id": request.resource_id, "owner_id": request.owner_id, "lock_type": request.lock_type, "reason": reason})
        return LockReceipt("", request.resource_id, request.owner_id, LockStatus.REJECTED, request.tick, reason=reason, session_identity=self.session_identity, branch_key=self.branch_key)

    def _check_scope(self, session_identity: str, branch_key: str) -> None:
        if session_identity and session_identity != self.session_identity:
            raise ValueError("lock session scope mismatch")
        if branch_key and branch_key != self.branch_key:
            raise ValueError("lock branch scope mismatch")

    def _record(self, kind: str, tick: int, payload: Mapping[str, Any]) -> None:
        if self.audit is not None:
            self.audit.append(kind, tick=tick, source="fleet.lock", payload=payload)


class SafeStopCoordinator:
    """Collect stop requests and acknowledgements without judging outcomes."""

    def __init__(self, *, session_identity: str, branch_key: str, members: FleetIdentityRegistry, audit: FleetAuditSink | None = None):
        self.session_identity = _required(session_identity, "session_identity")
        self.branch_key = _required(branch_key, "branch_key")
        self.members = members
        self.audit = audit
        self._phase = SafeStopPhase.IDLE
        self._request_id: str | None = None
        self._requested_tick: int | None = None
        self._reason = ""
        self._acknowledged: set[str] = set()

    def request(self, request: SafeStopRequest) -> SafeStopReceipt:
        if request.session_identity and request.session_identity != self.session_identity:
            raise ValueError("safe-stop session scope mismatch")
        if request.branch_key and request.branch_key != self.branch_key:
            raise ValueError("safe-stop branch scope mismatch")
        if self._phase is SafeStopPhase.STOPPED:
            return self._receipt(request.tick)
        if self._phase is SafeStopPhase.IDLE:
            self._request_id = request.request_id
            self._requested_tick = request.tick
            self._reason = request.reason
            self._phase = SafeStopPhase.REQUESTED
            self._record("safe_stop_requested", request.tick, {"request_id": request.request_id, "source_id": request.source_id, "reason": request.reason})
        return self._receipt(request.tick)

    def acknowledge(self, robot_id: str, *, tick: int) -> SafeStopReceipt:
        if self._phase is SafeStopPhase.IDLE:
            raise RuntimeError("safe-stop has not been requested")
        identity = self.members.get(robot_id)
        self._acknowledged.add(identity.robot_id)
        self._record("safe_stop_acknowledged", tick, {"request_id": self._request_id, "robot_id": identity.robot_id})
        if set(self.members.active_ids()).issubset(self._acknowledged):
            self._phase = SafeStopPhase.STOPPED
            self._record("safe_stop_stopped", tick, {"request_id": self._request_id, "acknowledged_robot_ids": sorted(self._acknowledged)})
        return self._receipt(tick)

    def status(self) -> SafeStopStatus:
        pending = tuple(item for item in self.members.active_ids() if item not in self._acknowledged)
        return SafeStopStatus(self._phase, self._request_id, self._requested_tick, tuple(sorted(self._acknowledged)), pending, self._reason)

    def _receipt(self, tick: int) -> SafeStopReceipt:
        status = self.status()
        return SafeStopReceipt(status.request_id or "", status.phase, tick, status.reason, status.pending_robot_ids, self.session_identity, self.branch_key)

    def _record(self, kind: str, tick: int, payload: Mapping[str, Any]) -> None:
        if self.audit is not None:
            self.audit.append(kind, tick=tick, source="fleet.safe_stop", payload=payload)


@dataclass(frozen=True)
class FleetSession:
    session_identity: str
    branch_key: str
    scene_id: str
    branch_role: str


class FleetRuntime:
    """One mutable fleet service instance per ``RunKey``/branch."""

    def __init__(self, run_key: RunKey, *, ledger: BranchLedger | None = None, max_message_queue: int = 256):
        self.run_key = run_key
        self.branch_key = run_key.as_string()
        self.ledger = ledger or BranchLedger(run_key)
        self.lifecycle = FleetLifecycle.CREATED
        self.session_identity = ""
        self.scene_id = ""
        self._lock = RLock()
        self._summary: FleetAuditSummary | None = None
        self._safe_stop_request_ids: list[str] = []
        self._communication_count = 0
        self._lock_count = 0
        self.audit: FleetAuditSink | None = None
        self.identities: FleetIdentityRegistry | None = None
        self.router: MessageRouter | None = None
        self.locks: TypedLockManager | None = None
        self.safe_stop: SafeStopCoordinator | None = None
        self.max_message_queue = max_message_queue

    def start(self, session: Any, binding: Any, branch: BranchSpec | None = None) -> FleetSession:
        with self._lock:
            if branch is not None and branch.run_key != self.run_key:
                raise ValueError("fleet branch run_key does not match runtime")
            if self.lifecycle is FleetLifecycle.SEALED:
                raise RuntimeError("fleet runtime is sealed")
            if self.lifecycle is FleetLifecycle.RUNNING:
                return FleetSession(self.session_identity, self.branch_key, self.scene_id, self.run_key.branch_role.value)
            if self.lifecycle is not FleetLifecycle.CREATED:
                raise RuntimeError(f"fleet runtime cannot start from {self.lifecycle.value}")
            binding_identity = _required(getattr(binding, "session_identity", ""), "binding.session_identity")
            self.session_identity = binding_identity
            self.scene_id = str(getattr(binding, "scene_id", getattr(session, "env_name", "")) or "")
            self.audit = FleetAuditSink(self.ledger, session_identity=self.session_identity, branch_key=self.branch_key)
            self.identities = FleetIdentityRegistry(session_identity=self.session_identity, branch_key=self.branch_key, audit=self.audit)
            self.router = MessageRouter(session_identity=self.session_identity, branch_key=self.branch_key, audit=self.audit, max_queue=self.max_message_queue)
            self.locks = TypedLockManager(session_identity=self.session_identity, branch_key=self.branch_key, audit=self.audit)
            self.safe_stop = SafeStopCoordinator(session_identity=self.session_identity, branch_key=self.branch_key, members=self.identities, audit=self.audit)
            for robot in tuple(getattr(binding, "robots", ())):
                capability = getattr(robot, "capability", None)
                if capability is None:
                    continue
                self.identities.register(FleetIdentity(capability.robot_id, self.session_identity, self.branch_key, capability.capabilities, capability.active, getattr(robot, "metadata", {})))
            self.lifecycle = FleetLifecycle.BOUND
            self._audit("runtime_bound", tick=0, source="fleet.runtime", payload={"scene_id": self.scene_id, "robot_ids": list(self.identities.active_ids())})
            self.lifecycle = FleetLifecycle.RUNNING
            self._audit("runtime_started", tick=0, source="fleet.runtime", payload={"branch_role": self.run_key.branch_role.value})
            return FleetSession(self.session_identity, self.branch_key, self.scene_id, self.run_key.branch_role.value)

    def send(self, message: FleetMessage) -> DeliveryReceipt:
        with self._lock:
            self._require_running()
            assert self.router is not None and self.identities is not None
            if message.session_identity and message.session_identity != self.session_identity:
                raise ValueError("message session scope mismatch")
            if message.branch_key and message.branch_key != self.branch_key:
                raise ValueError("message branch scope mismatch")
            scoped = FleetMessage(message.message_id, message.sender_id, message.receiver_id, message.kind, message.tick, message.payload, message.causal_parent, self.session_identity, self.branch_key)
            receipt = self.router.send(scoped, active_receivers=self.identities.active_ids())
            if receipt.status in {DeliveryStatus.DELIVERED, DeliveryStatus.DELAYED}:
                self._communication_count += 1
            return receipt

    def record_step(self, *, tick: int, sim_time_s: float, phase: str) -> None:
        """Record the shared tick boundary without changing task semantics."""

        with self._lock:
            if self.lifecycle is FleetLifecycle.RUNNING:
                self._audit(
                    "tick_boundary",
                    tick=tick,
                    source="fleet.runtime",
                    payload={"sim_time_s": float(sim_time_s), "phase": str(phase)},
                )
                if self.locks is not None:
                    self.locks.expire(tick)

    def receive(self, receiver_id: str, *, max_items: int = 1, tick: int = 0) -> tuple[FleetMessage, ...]:
        with self._lock:
            self._require_running()
            assert self.router is not None
            return self.router.receive(receiver_id, max_items=max_items, tick=tick)

    def handoff_request(
        self,
        *,
        sender_id: str,
        receiver_id: str,
        handoff_ref: str,
        tick: int,
        resource_id: str | None = None,
        causal_parent: str | None = None,
    ) -> DeliveryReceipt:
        """Publish the P2 handoff request after precedence/release logic.

        The runtime records the public coordination intent only.  The
        evaluator still needs an external receipt and physical poses before
        declaring release, handoff or accept satisfied.
        """

        receipt = self.send(
            FleetMessage(
                message_id=f"handoff-request-{self.run_key.as_string()}-{tick}-{handoff_ref}",
                sender_id=sender_id,
                receiver_id=receiver_id,
                kind="handoff_request",
                tick=tick,
                payload={"handoff_ref": _required(handoff_ref, "handoff_ref"), "resource_id": resource_id or ""},
                causal_parent=causal_parent,
            )
        )
        self._audit("handoff_request", tick=tick, source="fleet.handoff", payload={"handoff_ref": handoff_ref, "sender_id": sender_id, "receiver_id": receiver_id, "resource_id": resource_id or "", "delivery_status": receipt.status.value})
        return receipt

    def handoff_accept(
        self,
        *,
        receiver_id: str,
        sender_id: str,
        handoff_ref: str,
        tick: int,
        causal_parent: str | None = None,
    ) -> DeliveryReceipt:
        receipt = self.send(
            FleetMessage(
                message_id=f"handoff-accept-{self.run_key.as_string()}-{tick}-{handoff_ref}",
                sender_id=receiver_id,
                receiver_id=sender_id,
                kind="handoff_accept",
                tick=tick,
                payload={"handoff_ref": _required(handoff_ref, "handoff_ref")},
                causal_parent=causal_parent,
            )
        )
        self._audit("handoff_accept", tick=tick, source="fleet.handoff", payload={"handoff_ref": handoff_ref, "receiver_id": receiver_id, "sender_id": sender_id, "delivery_status": receipt.status.value})
        return receipt

    def acquire(self, request: LockRequest) -> LockReceipt:
        with self._lock:
            self._require_running()
            assert self.locks is not None and self.identities is not None
            scoped = LockRequest(request.request_id, request.resource_id, request.owner_id, request.lock_type, request.purpose, request.tick, request.lease_ticks, self.session_identity, self.branch_key)
            receipt = self.locks.acquire(scoped, active_owners=self.identities.active_ids())
            if receipt.status is LockStatus.ACQUIRED:
                self._lock_count += 1
            return receipt

    def release(self, lease_id: str, *, owner_id: str, tick: int, force: bool = False) -> LockReceipt:
        with self._lock:
            if self.lifecycle not in {FleetLifecycle.RUNNING, FleetLifecycle.STOP_REQUESTED, FleetLifecycle.STOPPED}:
                raise RuntimeError("fleet runtime does not accept lock release")
            assert self.locks is not None
            receipt = self.locks.release(lease_id, owner_id=owner_id, tick=tick, force=force)
            if receipt.status is LockStatus.RELEASED:
                self._audit("resource_release", tick=tick, source="fleet.resource", payload={"lease_id": lease_id, "resource_id": receipt.resource_id, "owner_id": owner_id, "forced": force})
            return receipt

    def member_join(self, identity: FleetIdentity, *, tick: int = 0) -> None:
        with self._lock:
            self._require_running()
            assert self.identities is not None
            scoped = FleetIdentity(identity.robot_id, self.session_identity, self.branch_key, identity.capabilities, identity.active, identity.metadata)
            self.identities.join(scoped, tick=tick)

    def member_exit(self, robot_id: str, *, tick: int, reason: str = "member_exit") -> SafeStopReceipt:
        with self._lock:
            self._require_running()
            assert self.identities is not None and self.safe_stop is not None
            self.identities.mark_inactive(robot_id, tick=tick, reason=reason)
            request_id = f"safe-stop-{self.run_key.as_string()}-{tick}-{len(self._safe_stop_request_ids)}"
            receipt = self.safe_stop.request(SafeStopRequest(request_id, "fleet.runtime", tick, reason, self.session_identity, self.branch_key))
            if request_id not in self._safe_stop_request_ids:
                self._safe_stop_request_ids.append(request_id)
            self.lifecycle = FleetLifecycle.STOP_REQUESTED
            return receipt

    def request_safe_stop(self, *, source_id: str = "fleet.runtime", tick: int = 0, reason: str = "requested") -> SafeStopReceipt:
        with self._lock:
            if self.lifecycle not in {FleetLifecycle.RUNNING, FleetLifecycle.STOP_REQUESTED}:
                raise RuntimeError("fleet runtime is not running")
            assert self.safe_stop is not None
            request_id = f"safe-stop-{self.run_key.as_string()}-{tick}-{len(self._safe_stop_request_ids)}"
            receipt = self.safe_stop.request(SafeStopRequest(request_id, source_id, tick, reason, self.session_identity, self.branch_key))
            if request_id not in self._safe_stop_request_ids:
                self._safe_stop_request_ids.append(request_id)
            self.lifecycle = FleetLifecycle.STOP_REQUESTED
            return receipt

    def acknowledge_safe_stop(self, robot_id: str, *, tick: int) -> SafeStopReceipt:
        with self._lock:
            if self.lifecycle not in {FleetLifecycle.STOP_REQUESTED, FleetLifecycle.STOPPED}:
                raise RuntimeError("safe-stop is not requested")
            assert self.safe_stop is not None
            receipt = self.safe_stop.acknowledge(robot_id, tick=tick)
            if receipt.phase is SafeStopPhase.STOPPED:
                self.lifecycle = FleetLifecycle.STOPPED
            return receipt

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._require_bound()
            assert self.identities is not None and self.locks is not None and self.safe_stop is not None
            return {
                "lifecycle": self.lifecycle.value,
                "session_identity": self.session_identity,
                "branch_key": self.branch_key,
                "scene_id": self.scene_id,
                "members": [
                    {"robot_id": item.robot_id, "capabilities": sorted(item.capabilities), "active": item.active, "metadata": dict(item.metadata)}
                    for item in self.identities.snapshot()
                ],
                "locks": [state.__dict__.copy() for state in self.locks.snapshot()],
                "safe_stop": self.safe_stop.status().__dict__.copy(),
                "pending_messages": self.router.pending() if self.router is not None else 0,
            }

    def close(self, *, reason: str = "episode_complete", tick: int = 0) -> FleetAuditSummary:
        with self._lock:
            if self._summary is not None:
                return self._summary
            if self.lifecycle is FleetLifecycle.CREATED:
                self.lifecycle = FleetLifecycle.BOUND
                self.session_identity = self.session_identity or f"unbound:{self.branch_key}"
                self.scene_id = self.scene_id or "unknown"
                self.audit = self.audit or FleetAuditSink(self.ledger, session_identity=self.session_identity, branch_key=self.branch_key)
                self.identities = self.identities or FleetIdentityRegistry(session_identity=self.session_identity, branch_key=self.branch_key, audit=self.audit)
                self.router = self.router or MessageRouter(session_identity=self.session_identity, branch_key=self.branch_key, audit=self.audit)
                self.locks = self.locks or TypedLockManager(session_identity=self.session_identity, branch_key=self.branch_key, audit=self.audit)
                self.safe_stop = self.safe_stop or SafeStopCoordinator(session_identity=self.session_identity, branch_key=self.branch_key, members=self.identities, audit=self.audit)
            if self.lifecycle in {FleetLifecycle.BOUND, FleetLifecycle.RUNNING}:
                assert self.safe_stop is not None
                request_id = f"safe-stop-{self.run_key.as_string()}-{tick}-{len(self._safe_stop_request_ids)}"
                receipt = self.safe_stop.request(SafeStopRequest(request_id, "fleet.runtime", tick, reason, self.session_identity, self.branch_key))
                if request_id not in self._safe_stop_request_ids:
                    self._safe_stop_request_ids.append(request_id)
                self.lifecycle = FleetLifecycle.STOP_REQUESTED
            if self.lifecycle is FleetLifecycle.STOP_REQUESTED:
                assert self.safe_stop is not None and self.identities is not None
                # Closing is the controller-facing stop boundary.  Any
                # remaining active member is acknowledged here so the ledger
                # distinguishes a requested stop from a fully stopped scope.
                for robot_id in self.identities.active_ids():
                    if robot_id not in self.safe_stop.status().acknowledged_robot_ids:
                        self.safe_stop.acknowledge(robot_id, tick=tick)
                self.lifecycle = FleetLifecycle.STOPPED
            if self.router is not None:
                self.router.close()
            self._audit("runtime_sealed", tick=tick, source="fleet.runtime", payload={"reason": reason})
            self.lifecycle = FleetLifecycle.SEALED
            assert self.identities is not None and self.audit is not None
            active_members = self.identities.active_ids()
            self.ledger.seal()
            self._summary = FleetAuditSummary(self.lifecycle, self.session_identity, self.branch_key, len(self.audit.events), self.ledger.digest(), self._communication_count, self._lock_count, tuple(self._safe_stop_request_ids), active_members)
            return self._summary

    def _audit(self, kind: str, *, tick: int, source: str, payload: Mapping[str, Any]) -> None:
        if self.audit is None:
            return
        self.audit.append(kind, tick=tick, source=source, payload=payload)

    def _require_bound(self) -> None:
        if self.lifecycle is FleetLifecycle.CREATED:
            raise RuntimeError("fleet runtime is not bound")

    def _require_running(self) -> None:
        if self.lifecycle is not FleetLifecycle.RUNNING:
            raise RuntimeError(f"fleet runtime is not running: {self.lifecycle.value}")


def identity_from_robot(robot: Any, *, session_identity: str, branch_key: str) -> FleetIdentity:
    """Convert a public ``RobotState``-like object without importing task code."""

    capability: RobotCapability = getattr(robot, "capability", robot)
    return FleetIdentity(capability.robot_id, session_identity, branch_key, capability.capabilities, capability.active, getattr(robot, "metadata", {}))


__all__ = [
    "DeliveryReceipt",
    "DeliveryStatus",
    "FleetAuditEvent",
    "FleetAuditSink",
    "FleetAuditSummary",
    "FleetIdentity",
    "FleetIdentityRegistry",
    "FleetLifecycle",
    "FleetMessage",
    "FleetRuntime",
    "FleetSession",
    "LockReceipt",
    "LockRequest",
    "LockState",
    "LockStatus",
    "MessageRouter",
    "SafeStopCoordinator",
    "SafeStopPhase",
    "SafeStopReceipt",
    "SafeStopRequest",
    "SafeStopStatus",
    "TypedLockManager",
    "identity_from_robot",
]
