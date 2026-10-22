"""D4 dynamic-loss contracts for the revised benchmark protocol.

This module is intentionally independent from :mod:`dynamic_events`.  The
legacy scheduler is responsible for advancing a registered event.  This file
freezes the causal affected-work set and turns adapter receipts/progress facts
into typed D4 records; it never invents a physical outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
import json
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Sequence

from .records_v3 import (
    DynamicItemOutcomeV3,
    IdentityV3,
    RecordStatus,
    canonical_digest,
)
from .metrics_v3 import ScoreResultV3, d4_score


def _text(value: Any, name: str) -> str:
    if value is None:
        raise ValueError(f"{name} is required")
    result = str(value).strip()
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _tuple_values(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (_text(value, "edge"),)
    return tuple(sorted({_text(item, "edge") for item in value}))


def _merge_values(*values: Any) -> tuple[str, ...]:
    merged: set[str] = set()
    for value in values:
        merged.update(_tuple_values(value))
    return tuple(sorted(merged))


def _shares_resource_family(left: WorkItemV3, right: WorkItemV3) -> bool:
    return any(set(values) & set(right.resource_families[family]) for family, values in left.resource_families.items())


def _freeze_nested(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_nested(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_nested(item) for item in value)
    return value


def _thaw_nested(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_nested(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_nested(item) for item in value]
    return value


def _work_value(work: Any, name: str, default: Any = None) -> Any:
    if isinstance(work, Mapping):
        return work.get(name, default)
    return getattr(work, name, default)


@dataclass(frozen=True)
class WorkItemV3:
    """Normalized work item used solely for closure construction."""

    item_id: str
    kind: str = "deadline"
    work_weight: float = 1.0
    precedence: tuple[str, ...] = ()
    service_dependencies: tuple[str, ...] = ()
    shared_resources: tuple[str, ...] = ()
    deadline_tick: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    shared_robot_ids: tuple[str, ...] = ()
    shared_tools: tuple[str, ...] = ()
    shared_spaces: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "item_id", _text(self.item_id, "item_id"))
        if self.kind not in {"deadline", "time_valued", "continuous_supply"}:
            raise ValueError(f"unknown dynamic item kind: {self.kind}")
        if not math.isfinite(float(self.work_weight)) or float(self.work_weight) <= 0:
            raise ValueError("work_weight must be positive")
        if self.deadline_tick is not None and (type(self.deadline_tick) is not int or self.deadline_tick < 0):
            raise ValueError("deadline_tick must be a nonnegative integer")
        object.__setattr__(self, "precedence", _tuple_values(self.precedence))
        object.__setattr__(self, "service_dependencies", _tuple_values(self.service_dependencies))
        object.__setattr__(self, "shared_resources", _tuple_values(self.shared_resources))
        object.__setattr__(self, "shared_robot_ids", _tuple_values(self.shared_robot_ids))
        object.__setattr__(self, "shared_tools", _tuple_values(self.shared_tools))
        object.__setattr__(self, "shared_spaces", _tuple_values(self.shared_spaces))
        # JSON round-trip detaches nested caller-owned values before freezing.
        snapshot = json.loads(json.dumps(dict(self.metadata), sort_keys=True, allow_nan=False))
        object.__setattr__(self, "metadata", _freeze_nested(snapshot))

    @classmethod
    def from_value(cls, value: Any) -> "WorkItemV3":
        if isinstance(value, cls):
            return value
        return cls(
            item_id=_work_value(value, "item_id", _work_value(value, "work_id")),
            kind=_work_value(value, "kind", "deadline"),
            work_weight=_work_value(value, "work_weight", 1.0),
            precedence=_work_value(value, "precedence", _work_value(value, "predecessors", ())),
            service_dependencies=_work_value(value, "service_dependencies", _work_value(value, "dependencies", ())),
            shared_resources=_work_value(value, "shared_resources", _work_value(value, "resources", ())),
            deadline_tick=_work_value(value, "deadline_tick"),
            metadata=_work_value(value, "metadata", {}),
            shared_robot_ids=_work_value(value, "shared_robot_ids", ()),
            shared_tools=_work_value(value, "shared_tools", ()),
            shared_spaces=_work_value(value, "shared_spaces", ()),
        )

    @property
    def resource_families(self) -> Mapping[str, tuple[str, ...]]:
        return {
            "shared_resource": self.shared_resources,
            "shared_robot": self.shared_robot_ids,
            "shared_tool": self.shared_tools,
            "shared_space": self.shared_spaces,
        }

    @property
    def all_shared_resources(self) -> tuple[str, ...]:
        return _merge_values(self.shared_resources, self.shared_robot_ids, self.shared_tools, self.shared_spaces)

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "kind": self.kind,
            "work_weight": float(self.work_weight),
            "precedence": list(self.precedence),
            "service_dependencies": list(self.service_dependencies),
            "shared_resources": list(self.shared_resources),
            "shared_robot_ids": list(self.shared_robot_ids),
            "shared_tools": list(self.shared_tools),
            "shared_spaces": list(self.shared_spaces),
            "deadline_tick": self.deadline_tick,
            "metadata": _thaw_nested(self.metadata),
        }

    def to_legacy_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "kind": self.kind,
            "work_weight": float(self.work_weight),
            "precedence": list(self.precedence),
            "service_dependencies": list(self.service_dependencies),
            "shared_resources": list(self.shared_resources),
            "deadline_tick": self.deadline_tick,
            "metadata": _thaw_nested(self.metadata),
        }


@dataclass(frozen=True)
class AffectedWorkClosureV3:
    """Frozen causal closure ``J`` for one function-loss event."""

    checkpoint_tick: int
    root_ids: tuple[str, ...]
    items: tuple[WorkItemV3, ...]
    closure_digest: str
    edge_families: Mapping[str, tuple[tuple[str, str], ...]] = field(default_factory=dict)
    canonical_version: str = "v2"

    def __post_init__(self) -> None:
        if type(self.checkpoint_tick) is not int or self.checkpoint_tick < 0:
            raise ValueError("checkpoint_tick must be a nonnegative integer")
        object.__setattr__(self, "root_ids", tuple(sorted({_text(item, "root_id") for item in self.root_ids})))
        object.__setattr__(self, "items", tuple(sorted(self.items, key=lambda item: item.item_id)))
        canonical_edges: dict[str, tuple[tuple[str, str], ...]] = {}
        for family, edges in dict(self.edge_families).items():
            family_name = _text(family, "edge family")
            normalized: set[tuple[str, str]] = set()
            for edge in edges:
                if isinstance(edge, str) or len(edge) != 2:
                    raise ValueError(f"{family_name} closure edge must contain two item IDs")
                normalized.add((_text(edge[0], "edge source"), _text(edge[1], "edge target")))
            canonical_edges[family_name] = tuple(sorted(normalized))
        object.__setattr__(self, "edge_families", MappingProxyType(canonical_edges))
        if not self.items:
            raise ValueError("affected-work closure cannot be empty")
        if len({item.item_id for item in self.items}) != len(self.items):
            raise ValueError("affected-work closure contains duplicate item IDs")
        item_ids = {item.item_id for item in self.items}
        if not set(self.root_ids) <= item_ids:
            raise ValueError("closure roots must be present in items")
        for family, edges in self.edge_families.items():
            if any(len(edge) != 2 or edge[0] not in item_ids or edge[1] not in item_ids for edge in edges):
                raise ValueError(f"{family} closure edge references an unknown item")
        expected = canonical_digest(self.to_canonical_dict(include_digest=False))
        if self.closure_digest != expected:
            legacy_payload = {
                "checkpoint_tick": self.checkpoint_tick,
                "root_ids": list(self.root_ids),
                "items": [item.to_legacy_dict() for item in self.items],
                "edge_families": {key: [list(edge) for edge in value] for key, value in sorted(self.edge_families.items()) if key in {"precedence", "service", "shared_resource"}},
            }
            if self.closure_digest != canonical_digest(legacy_payload):
                raise ValueError("closure_digest does not match frozen contents")
            object.__setattr__(self, "canonical_version", "v1_legacy")
        elif self.canonical_version not in {"v1_legacy", "v2"}:
            raise ValueError("unsupported closure canonical version")

    @property
    def item_ids(self) -> tuple[str, ...]:
        return tuple(item.item_id for item in self.items)

    def to_canonical_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        if self.canonical_version == "v1_legacy":
            payload = {
                "checkpoint_tick": self.checkpoint_tick,
                "root_ids": list(self.root_ids),
                "items": [item.to_legacy_dict() for item in self.items],
                "edge_families": {key: [list(edge) for edge in value] for key, value in sorted(self.edge_families.items()) if key in {"precedence", "service", "shared_resource"}},
            }
            if include_digest:
                payload["closure_digest"] = self.closure_digest
            return payload
        payload = {
            "checkpoint_tick": self.checkpoint_tick,
            "root_ids": list(self.root_ids),
            "items": [item.to_dict() for item in self.items],
            "edge_families": {key: [list(edge) for edge in value] for key, value in sorted(self.edge_families.items())},
        }
        if include_digest:
            payload["closure_digest"] = self.closure_digest
        return payload


def freeze_affected_work_closure(
    works: Iterable[Any],
    affected_work_ids: Iterable[str],
    *,
    checkpoint_tick: int,
) -> AffectedWorkClosureV3:
    """Expand and freeze ``J`` along precedence, service and shared resources.

    Edges are traversed in both directions because a loss can affect a
    predecessor that feeds a work item as well as a successor that consumes
    its output.  Shared resource membership is similarly bidirectional.
    """

    normalized: dict[str, WorkItemV3] = {}
    for value in works:
        item = WorkItemV3.from_value(value)
        if item.item_id in normalized:
            raise ValueError(f"duplicate work item ID: {item.item_id}")
        normalized[item.item_id] = item
    roots = {_text(item, "affected_work_id") for item in affected_work_ids}
    if not roots:
        raise ValueError("at least one affected work item is required")
    missing = roots - normalized.keys()
    if missing:
        raise ValueError(f"affected work item is not in directory: {sorted(missing)}")
    all_ids = set(normalized)
    for item in normalized.values():
        unknown = (set(item.precedence) | set(item.service_dependencies)) - all_ids
        if unknown:
            raise ValueError(f"work item {item.item_id} references unknown work: {sorted(unknown)}")
    closure = set(roots)
    changed = True
    while changed:
        changed = False
        for item in normalized.values():
            related = set(item.precedence) | set(item.service_dependencies)
            if item.item_id in closure or related & closure:
                candidate = {item.item_id} | related
                for other in normalized.values():
                    if set(other.precedence) & candidate or set(other.service_dependencies) & candidate:
                        candidate.add(other.item_id)
                    if _shares_resource_family(item, other):
                        candidate.add(other.item_id)
                candidate &= normalized.keys()
                if not candidate <= closure:
                    closure.update(candidate)
                    changed = True
        for left in normalized.values():
            if left.item_id not in closure:
                continue
            for right in normalized.values():
                if _shares_resource_family(left, right) and right.item_id not in closure:
                    closure.add(right.item_id)
                    changed = True
    selected = tuple(normalized[item_id] for item_id in sorted(closure))
    selected_ids = set(closure)
    # Build explicit edge lists from the frozen item directory.
    edge_families = {
        "precedence": tuple(sorted((source, item.item_id) for item in selected for source in item.precedence if source in selected_ids)),
        "service": tuple(sorted((source, item.item_id) for item in selected for source in item.service_dependencies if source in selected_ids)),
        "shared_resource": tuple(sorted((left.item_id, right.item_id) for index, left in enumerate(selected) for right in selected[index + 1:] if set(left.shared_resources) & set(right.shared_resources))),
        "shared_robot": tuple(sorted((left.item_id, right.item_id) for index, left in enumerate(selected) for right in selected[index + 1:] if set(left.shared_robot_ids) & set(right.shared_robot_ids))),
        "shared_tool": tuple(sorted((left.item_id, right.item_id) for index, left in enumerate(selected) for right in selected[index + 1:] if set(left.shared_tools) & set(right.shared_tools))),
        "shared_space": tuple(sorted((left.item_id, right.item_id) for index, left in enumerate(selected) for right in selected[index + 1:] if set(left.shared_spaces) & set(right.shared_spaces))),
    }
    canonical = {
        "checkpoint_tick": checkpoint_tick,
        "root_ids": sorted(roots),
        "items": [item.to_dict() for item in selected],
        "edge_families": {key: [list(edge) for edge in value] for key, value in sorted(edge_families.items())},
    }
    return AffectedWorkClosureV3(checkpoint_tick, tuple(sorted(roots)), selected, canonical_digest(canonical), edge_families)


@dataclass(frozen=True)
class PhysicalFunctionLossResultV3:
    status: RecordStatus
    event_id: str
    robot_id: str
    function: str
    available: bool
    tick: int
    evidence_hash: str | None = None
    reason: str | None = None
    safe_stop_requested: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", RecordStatus(self.status))
        if not all(isinstance(value, str) and value.strip() for value in (self.event_id, self.robot_id, self.function)):
            raise ValueError("physical result identity is required")
        if type(self.available) is not bool or type(self.tick) is not int or self.tick < 0:
            raise ValueError("physical result availability/tick is invalid")
        if type(self.safe_stop_requested) is not bool:
            raise ValueError("safe_stop_requested must be bool")
        if self.status is RecordStatus.VALID and not self.evidence_hash:
            raise ValueError("valid physical result requires evidence_hash")
        if self.status in {RecordStatus.UNKNOWN, RecordStatus.INCOMPLETE, RecordStatus.INVALID} and not self.reason:
            raise ValueError("non-valid physical result requires reason")


class DynamicDispositionV3(str, Enum):
    """Event-level D4 disposition, intentionally separate from item status."""

    RECOVERED = "recovered"
    SAFE_STOP = "certified_safe_stop"
    TERMINAL_FAILURE = "terminal_failure"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DynamicEventDispositionV3:
    event_id: str
    disposition: DynamicDispositionV3
    tick: int
    closure_digest: str
    evidence_digests: tuple[str, ...] = ()
    reason: str = ""
    physical_status: RecordStatus = RecordStatus.UNKNOWN
    safe_stop_certificate_digest: str | None = None

    def __post_init__(self) -> None:
        for name in ("event_id", "closure_digest"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} is required")
        if type(self.tick) is not int or self.tick < 0:
            raise ValueError("event disposition tick must be a nonnegative integer")
        object.__setattr__(self, "disposition", DynamicDispositionV3(self.disposition))
        object.__setattr__(self, "physical_status", RecordStatus(self.physical_status))
        if isinstance(self.evidence_digests, str) or any(not isinstance(item, str) or not item.strip() for item in self.evidence_digests):
            raise ValueError("evidence digests must be nonempty strings")
        object.__setattr__(self, "evidence_digests", tuple(self.evidence_digests))
        if self.safe_stop_certificate_digest is not None and (not isinstance(self.safe_stop_certificate_digest, str) or not self.safe_stop_certificate_digest.strip()):
            raise ValueError("safe-stop certificate digest must be a nonempty string")
        if self.disposition in {DynamicDispositionV3.SAFE_STOP, DynamicDispositionV3.RECOVERED, DynamicDispositionV3.TERMINAL_FAILURE} and not self.evidence_digests:
            raise ValueError("event disposition requires evidence digests")
        if self.disposition is DynamicDispositionV3.SAFE_STOP and not self.safe_stop_certificate_digest:
            raise ValueError("certified safe-stop requires a stop certificate digest")
        if self.disposition is DynamicDispositionV3.SAFE_STOP and self.physical_status is RecordStatus.VALID:
            raise ValueError("safe-stop disposition cannot carry valid physical status")
        if self.disposition is DynamicDispositionV3.RECOVERED and self.physical_status is not RecordStatus.VALID:
            raise ValueError("recovered disposition requires valid physical status")
        if self.disposition is DynamicDispositionV3.UNKNOWN and not self.reason.strip():
            raise ValueError("unknown event disposition requires reason")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "disposition": self.disposition.value,
            "tick": self.tick,
            "closure_digest": self.closure_digest,
            "evidence_digests": list(self.evidence_digests),
            "reason": self.reason,
            "physical_status": self.physical_status.value,
            "safe_stop_certificate_digest": self.safe_stop_certificate_digest,
        }


def make_dynamic_event_disposition_v3(
    physical_result: PhysicalFunctionLossResultV3,
    *,
    disposition: DynamicDispositionV3,
    closure_digest: str,
    evidence_digests: Sequence[str] = (),
    reason: str = "",
    safe_stop_certificate_digest: str | None = None,
) -> DynamicEventDispositionV3:
    """Create an event-level disposition without changing D4 item scores."""

    kind = DynamicDispositionV3(disposition)
    if kind is DynamicDispositionV3.SAFE_STOP and not physical_result.safe_stop_requested:
        raise ValueError("safe-stop disposition requires a safe-stop request")
    if kind is DynamicDispositionV3.RECOVERED and physical_result.status is not RecordStatus.VALID:
        raise ValueError("recovered disposition requires a valid physical result")
    return DynamicEventDispositionV3(
        event_id=physical_result.event_id,
        disposition=kind,
        tick=physical_result.tick,
        closure_digest=closure_digest,
        evidence_digests=tuple(evidence_digests) + tuple(x for x in (physical_result.evidence_hash,) if x),
        reason=reason,
        physical_status=physical_result.status,
        safe_stop_certificate_digest=safe_stop_certificate_digest,
    )


class PhysicalFunctionLossAdapterV3:
    """Verify a physical operation receipt and preserve safe-stop semantics."""

    def __init__(self, *, fleet: Any | None = None):
        self.fleet = fleet
        self.last_result: PhysicalFunctionLossResultV3 | None = None
        self._stop_keys: set[tuple[str, int]] = set()

    def verify(self, event: Any, *, available: bool, tick: int, receipt: Mapping[str, Any], request_safe_stop: bool = True) -> PhysicalFunctionLossResultV3:
        if type(request_safe_stop) is not bool:
            raise TypeError("request_safe_stop must be bool")
        fields = {
            "event_id": _work_value(event, "event_id"),
            "robot_id": _work_value(event, "robot_id"),
            "function": _work_value(event, "function"),
            "available": available,
            "tick": tick,
            "dependency_version": _work_value(event, "dependency_version"),
            "aftermath_contract": _work_value(event, "aftermath_contract"),
        }
        event_identity_ok = all(isinstance(fields[key], str) and bool(fields[key].strip()) for key in ("event_id", "robot_id", "function", "dependency_version", "aftermath_contract"))
        event_clock_ok = type(available) is bool and type(tick) is int and tick >= 0
        receipt_mapping_ok = isinstance(receipt, Mapping)
        receipt = receipt if receipt_mapping_ok else {}
        identity_ok = event_identity_ok and event_clock_ok and all(key in receipt and receipt[key] == value and type(receipt[key]) is type(value) for key, value in fields.items())
        evidence_ok = isinstance(receipt.get("evidence_hash"), str) and bool(receipt.get("evidence_hash"))
        guard_ok = receipt.get("guard_verified") is True
        world_ok = receipt.get("world_preserved") is True
        if identity_ok and evidence_ok and guard_ok and world_ok:
            self.last_result = PhysicalFunctionLossResultV3(RecordStatus.VALID, fields["event_id"], fields["robot_id"], fields["function"], available, tick, receipt["evidence_hash"])
            return self.last_result
        reason = "receipt_identity_incomplete" if not identity_ok or not evidence_ok else "guard_or_world_preservation_unverified"
        stop = False
        stop_key = (str(fields["event_id"]), int(tick) if type(tick) is int else -1)
        if request_safe_stop and stop_key not in self._stop_keys and self.fleet is not None and hasattr(self.fleet, "request_safe_stop"):
            try:
                self.fleet.request_safe_stop(tick=tick, reason="event_operation_unverified")
                stop = True
                self._stop_keys.add(stop_key)
            except Exception:
                reason = f"{reason};safe_stop_request_failed"
        status = RecordStatus.INCOMPLETE if not identity_ok or not evidence_ok else RecordStatus.UNKNOWN
        safe_id = lambda value, fallback: value.strip() if isinstance(value, str) and value.strip() else fallback
        safe_available = available if type(available) is bool else False
        safe_tick = tick if type(tick) is int and tick >= 0 else 0
        self.last_result = PhysicalFunctionLossResultV3(status, safe_id(fields["event_id"], "unknown-event"), safe_id(fields["robot_id"], "unknown-robot"), safe_id(fields["function"], "unknown-function"), safe_available, safe_tick, reason=reason, safe_stop_requested=stop)
        return self.last_result

    def operation(self, event: Any, *, underlying: Callable[[Any, bool, int], Mapping[str, Any]]) -> Callable[[Any, bool, int], Mapping[str, Any]]:
        """Wrap an existing physical adapter for ``AvailabilityScheduler``.

        The scheduler still raises on an unverified receipt; this wrapper also
        records the typed result for consumers that need an explicit status.
        """
        def wrapped(current_event: Any, available: bool, tick: int) -> Mapping[str, Any]:
            receipt = underlying(current_event, available, tick)
            result = self.verify(current_event, available=available, tick=tick, receipt=receipt, request_safe_stop=False)
            if result.status is not RecordStatus.VALID:
                raise ValueError(result.reason or "physical operation unverified")
            return receipt
        return wrapped


def dynamic_item_outcomes_v3(
    identity: IdentityV3,
    closure: AffectedWorkClosureV3,
    observations: Mapping[str, Mapping[str, Any]],
) -> tuple[DynamicItemOutcomeV3, ...]:
    """Normalize per-item progress facts into typed D4 records.

    Missing facts remain ``incomplete``; they are never silently interpreted as
    zero performance.  A physical outcome is valid only when the observation
    explicitly binds the operation evidence and remaining-feasibility proof.
    """

    rows: list[DynamicItemOutcomeV3] = []
    for item in closure.items:
        row = dict(observations.get(item.item_id, {}))
        status = RecordStatus(row.get("status", "valid" if row else "incomplete"))
        reason = row.get("invalid_reason")
        if not row:
            reason = "missing_dynamic_item_observation"
        if status is RecordStatus.UNKNOWN and not reason:
            reason = "dynamic_item_observation_unknown"
        required = ("operation_evidence_hash", "remaining_feasibility_hash")
        if status is RecordStatus.VALID and any(not row.get(key) for key in required):
            status = RecordStatus.INCOMPLETE
            reason = "physical_outcome_or_feasibility_evidence_missing"
        kwargs: dict[str, Any] = {
            "identity": identity,
            "status": status,
            "evidence_digests": tuple(x for x in (row.get("operation_evidence_hash"), row.get("remaining_feasibility_hash"), row.get("progress_evidence_hash")) if x),
            "invalid_reason": reason,
            "item_id": item.item_id,
            "item_type": item.kind,
            "work_weight": float(item.work_weight),
            "legal_completion_fraction": row.get("legal_completion_fraction"),
            "service_fraction": row.get("service_fraction"),
            "deadline_tick": item.deadline_tick,
            "extra_metadata": {
                "closure_digest": closure.closure_digest,
                "checkpoint_tick": closure.checkpoint_tick,
                **dict(row.get("extra_metadata", {})),
            },
        }
        rows.append(DynamicItemOutcomeV3(**kwargs))
    return tuple(rows)


def evaluate_dynamic_value_outcomes_v3(
    identity: IdentityV3,
    closure: AffectedWorkClosureV3,
    registrations: Mapping[str, Any],
    timelines: Mapping[str, Any],
):
    """Evaluate registered external timelines, then use the existing D4 bridge.

    Registrations must cover the frozen closure exactly.  A missing timeline is
    retained as an incomplete item observation; caller-supplied timelines never
    grant physical or formal qualification.
    """

    from .dynamic_value_v3 import (
        DynamicValueRegistrationV3,
        DynamicValueTimelineV3,
        evaluate_dynamic_value_v3,
    )

    item_by_id = {item.item_id: item for item in closure.items}
    if set(registrations) != set(item_by_id):
        raise ValueError("D4 value registrations must exactly cover the frozen closure")
    if not set(timelines) <= set(item_by_id):
        raise ValueError("D4 timeline contains an item outside the frozen closure")
    observations: dict[str, Mapping[str, Any]] = {}
    evaluations: dict[str, Any] = {}
    common_window = None
    for item_id, item in item_by_id.items():
        registration = registrations[item_id]
        if not isinstance(registration, DynamicValueRegistrationV3):
            raise TypeError("D4 value registration has the wrong type")
        if registration.item_id != item_id or registration.item_type != item.kind:
            raise ValueError("D4 value registration differs from frozen work identity/type")
        window = (registration.event_tick, registration.horizon_tick)
        if registration.event_tick < closure.checkpoint_tick or (common_window is not None and window != common_window):
            raise ValueError("D4 items require one common onset/window after the untreated checkpoint")
        common_window = window
        if item.deadline_tick is not None and registration.deadline_tick != item.deadline_tick:
            raise ValueError("D4 absolute deadline differs from the frozen work item")
        if item_id not in timelines:
            continue
        timeline = timelines[item_id]
        if not isinstance(timeline, DynamicValueTimelineV3):
            raise TypeError("D4 value timeline has the wrong type")
        evaluation = evaluate_dynamic_value_v3(registration, timeline)
        evaluations[item_id] = evaluation
        observations[item_id] = evaluation.to_dynamic_observation()
    return dynamic_item_outcomes_v3(identity, closure, observations), evaluations


def score_d4_outcomes_v3(
    outcomes: Sequence[DynamicItemOutcomeV3],
    *,
    expected_count: int | None = None,
) -> ScoreResultV3:
    """Bridge typed D4 outcomes to the pure CPU D4 estimator.

    Deadline items use legal completion at the registered deadline; finishing
    earlier does not increase their value.  Time-valued and continuous-supply
    items require both legal completion and service fractions and are scored by
    the estimator's ``(e + phi) / 2`` rule.  Any non-valid typed record keeps
    D4 incomplete rather than becoming an implicit zero.
    """

    if expected_count is not None and len(outcomes) != expected_count:
        return d4_score((), expected_count=expected_count)
    # Current-paper D1--D4 scoring requires three decidable repeats after the
    # registered rerun rule. Residual infrastructure unknowns retain their
    # scheduled support and measured evidence, but do not define a score range.
    if any(o.status in {RecordStatus.UNKNOWN, RecordStatus.INCOMPLETE} or o.legal_completion_fraction is None or (o.item_type in {"time_valued", "continuous_supply"} and o.service_fraction is None) for o in outcomes):
        if any(o.status not in {RecordStatus.VALID, RecordStatus.UNKNOWN, RecordStatus.INCOMPLETE} for o in outcomes):
            return ScoreResultV3("D4", "invalid", None, reason="item_requires_coverage_adjudication")
        known_weight = sum(1 for o in outcomes if o.status is RecordStatus.VALID
                           and o.legal_completion_fraction is not None
                           and (o.item_type not in {"time_valued", "continuous_supply"}
                                or o.service_fraction is not None))
        return ScoreResultV3(
            "D4", "incomplete", None,
            support_weight=(known_weight / len(outcomes) if outcomes else 0.),
            reason="residual_infrastructure_unknown_requires_three_decidable_repeats",
        )
    rows: list[dict[str, Any]] = []
    for outcome in outcomes:
        if outcome.status is not RecordStatus.VALID:
            return ScoreResultV3("D4", "incomplete", None, reason=f"item_status:{outcome.item_id}:{outcome.status.value}")
        if outcome.legal_completion_fraction is None:
            return ScoreResultV3("D4", "incomplete", None, reason=f"missing_legal_completion:{outcome.item_id}")
        row: dict[str, Any] = {"kind": outcome.item_type or "deadline", "e": outcome.legal_completion_fraction}
        if row["kind"] in {"time_valued", "continuous_supply"}:
            if outcome.service_fraction is None:
                return ScoreResultV3("D4", "incomplete", None, reason=f"missing_service_fraction:{outcome.item_id}")
            row["phi"] = outcome.service_fraction
        row["weight"] = float(outcome.work_weight)
        rows.append(row)
    # Paper eq. supp-d4-score gives every item equal weight. Outcome weights
    # inside achievement e_j must not become weights between work items.
    values: list[tuple[float, float]] = []
    for row in rows:
        e = float(row["e"])
        if not math.isfinite(e) or not 0 <= e <= 1:
            return ScoreResultV3("D4", "invalid", None, reason="completion_fraction_out_of_range")
        if row["kind"] == "deadline":
            q = e
        else:
            phi = float(row["phi"])
            if not math.isfinite(phi) or not 0 <= phi <= 1:
                return ScoreResultV3("D4", "invalid", None, reason="service_fraction_out_of_range")
            q = (e + phi) / 2
        values.append((1.0, q))
    total_weight = sum(weight for weight, _ in values)
    if total_weight <= 0:
        return ScoreResultV3("D4", "invalid", None, reason="nonpositive_work_weight")
    return ScoreResultV3("D4", "valid", 100 * sum(weight * value for weight, value in values) / total_weight, components={"item_count": float(len(values)), "total_work_weight": total_weight})


__all__ = [
    "WorkItemV3",
    "AffectedWorkClosureV3",
    "freeze_affected_work_closure",
    "PhysicalFunctionLossResultV3",
    "PhysicalFunctionLossAdapterV3",
    "DynamicDispositionV3",
    "DynamicEventDispositionV3",
    "make_dynamic_event_disposition_v3",
    "dynamic_item_outcomes_v3",
    "evaluate_dynamic_value_outcomes_v3",
    "score_d4_outcomes_v3",
]
