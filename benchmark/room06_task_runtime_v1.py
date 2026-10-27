"""Deterministic CPU runtime core for ``room_06 × liv-t-04``.

The module owns task-generated object definitions, the evaluator-private
logical hold ledger and task-event ordering.  It deliberately has no Isaac,
USD-prim, physical-contact, lock-response, score or qualification dependency.
The real producer can drive this state machine from measured skill-session
transitions and pass its JSON streams to the existing receipt/oracle seam.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from .room06_runtime_preflight_v1 import (
    FORMAL_GATES,
    PREFLIGHT_SCHEMA,
    TASK_SETUP_SCHEMA,
)


RUNTIME_EVIDENCE_SCHEMA = "heterocoord.room06-task-runtime-evidence.v1"
RUNTIME_BINDING_SCHEMA = "heterocoord.room06-runtime-binding.v1"
TASK_ID = "liv-t-04"
HORIZON_TICKS = 15_000
BOOK_BATCH_ID = "book_batch_01"
HEAVY_OBJECT_ID = "heavy_object"
MINIMUM_HOLDERS = 2
TRANSITION_PLACEHOLDER = "topology_transition_node"
BOOK_DESTINATIONS = (
    "floor2-bookshelf-1",
    "floor2-bookshelf-2",
    "floor2-bookshelf-3",
)
_HEX = frozenset("0123456789abcdef")
_BOOK_FIELDS = {
    "kind",
    "count",
    "book_ids",
    "source_semantic_point",
    "destination_semantic_points",
}
_HEAVY_FIELDS = {
    "kind",
    "size_xyz_m",
    "mass_kg",
    "source_semantic_point",
    "destination_semantic_point",
    "minimum_holders",
}
_MILESTONE_LABELS = frozenset(
    {"relay_started", "relay_complete", "accepted"}
)
_SINGLETON_TASK_EVENTS = frozenset(
    {
        "transition_handoff_observed",
        "bilateral_support_established",
        "joint_hold_safety_failure",
        "task_horizon_reached",
    }
)


class Room06TaskRuntimeError(ValueError):
    """Raised when task evidence would become ambiguous or unsafe."""


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise Room06TaskRuntimeError("value is not finite canonical JSON") from exc


def _canonical_digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise Room06TaskRuntimeError(f"{name} must be an object")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Room06TaskRuntimeError(f"{name} must be non-empty text")
    return value


def _digest(value: Any, name: str) -> str:
    value = _text(value, name)
    if len(value) != 64 or any(character not in _HEX for character in value):
        raise Room06TaskRuntimeError(f"{name} must be a lowercase SHA-256")
    return value


def _tick(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise Room06TaskRuntimeError(
            f"{name} must be a non-negative integer tick"
        )
    return value


def _finite_positive(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Room06TaskRuntimeError(f"{name} must be a finite positive number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise Room06TaskRuntimeError(f"{name} must be a finite positive number")
    return result


def _unique_texts(
    value: Any, name: str, *, exact_count: int | None = None
) -> list[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item.strip() for item in value)
        or len(set(value)) != len(value)
        or (exact_count is not None and len(value) != exact_count)
    ):
        qualifier = f"exactly {exact_count}" if exact_count is not None else "unique"
        raise Room06TaskRuntimeError(
            f"{name} must contain {qualifier} non-empty IDs"
        )
    return list(value)


def normalize_task_definition(entity_id: str, value: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one task-generated definition without inventing a USD prim."""

    entity_id = _text(entity_id, "entity_id")
    definition = _mapping(value, f"{entity_id}.definition")
    _canonical_bytes(definition)
    if entity_id == BOOK_BATCH_ID:
        if set(definition) != _BOOK_FIELDS:
            raise Room06TaskRuntimeError("book_batch_01 definition schema mismatch")
        if definition.get("kind") != "book_batch":
            raise Room06TaskRuntimeError("book_batch_01.kind must be book_batch")
        count = definition.get("count")
        if type(count) is not int or count <= 0:
            raise Room06TaskRuntimeError("book_batch_01.count must be positive integer")
        book_ids = _unique_texts(
            definition.get("book_ids"), "book_batch_01.book_ids", exact_count=count
        )
        if definition.get("source_semantic_point") != "floor1-bookshelf":
            raise Room06TaskRuntimeError(
                "book_batch_01 source must be floor1-bookshelf"
            )
        destinations = _unique_texts(
            definition.get("destination_semantic_points"),
            "book_batch_01.destination_semantic_points",
            exact_count=len(BOOK_DESTINATIONS),
        )
        if set(destinations) != set(BOOK_DESTINATIONS):
            raise Room06TaskRuntimeError(
                "book_batch_01 destinations must be the three floor2 bookshelves"
            )
        return {
            "kind": "book_batch",
            "count": count,
            "book_ids": sorted(book_ids),
            "source_semantic_point": "floor1-bookshelf",
            "destination_semantic_points": list(BOOK_DESTINATIONS),
        }
    if entity_id == HEAVY_OBJECT_ID:
        if set(definition) != _HEAVY_FIELDS:
            raise Room06TaskRuntimeError("heavy_object definition schema mismatch")
        if definition.get("kind") != "logical_load":
            raise Room06TaskRuntimeError("heavy_object.kind must be logical_load")
        size = definition.get("size_xyz_m")
        if not isinstance(size, list) or len(size) != 3:
            raise Room06TaskRuntimeError("heavy_object.size_xyz_m must have 3 values")
        normalized_size = [
            _finite_positive(item, f"heavy_object.size_xyz_m[{index}]")
            for index, item in enumerate(size)
        ]
        mass = _finite_positive(definition.get("mass_kg"), "heavy_object.mass_kg")
        source = _text(
            definition.get("source_semantic_point"),
            "heavy_object.source_semantic_point",
        )
        destination = _text(
            definition.get("destination_semantic_point"),
            "heavy_object.destination_semantic_point",
        )
        if source == destination:
            raise Room06TaskRuntimeError(
                "heavy_object source and destination must differ"
            )
        if definition.get("minimum_holders") != MINIMUM_HOLDERS:
            raise Room06TaskRuntimeError(
                "heavy_object.minimum_holders must be exactly 2"
            )
        return {
            "kind": "logical_load",
            "size_xyz_m": normalized_size,
            "mass_kg": mass,
            "source_semantic_point": source,
            "destination_semantic_point": destination,
            "minimum_holders": MINIMUM_HOLDERS,
        }
    raise Room06TaskRuntimeError(f"unsupported task-generated entity: {entity_id}")


def task_definition_digest(entity_id: str, value: Mapping[str, Any]) -> str:
    """Return the canonical digest of a normalized task definition."""

    return _canonical_digest(normalize_task_definition(entity_id, value))


def normalize_room06_task_setup(
    task_setup: Mapping[str, Any],
    task_object_bindings: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the two generated objects and bind caller-reported digests."""

    setup = _mapping(task_setup, "task_setup")
    source_task_setup_digest = _canonical_digest(setup)
    if set(setup) != {"schema", "task_id", "objects"}:
        raise Room06TaskRuntimeError("task_setup schema mismatch")
    if setup.get("schema") != TASK_SETUP_SCHEMA or setup.get("task_id") != TASK_ID:
        raise Room06TaskRuntimeError("task_setup must identify liv-t-04 v1")
    rows = setup.get("objects")
    if not isinstance(rows, list) or len(rows) != 2:
        raise Room06TaskRuntimeError(
            "task_setup must contain exactly book_batch_01 and heavy_object"
        )
    normalized: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        row = _mapping(row, f"task_setup.objects[{index}]")
        if set(row) != {"entity_id", "definition"}:
            raise Room06TaskRuntimeError(
                f"task_setup.objects[{index}] schema mismatch"
            )
        entity_id = _text(row.get("entity_id"), f"task_setup.objects[{index}].entity_id")
        if entity_id in normalized:
            raise Room06TaskRuntimeError(
                f"task_setup entity is duplicated: {entity_id}"
            )
        normalized[entity_id] = normalize_task_definition(
            entity_id, _mapping(row.get("definition"), f"{entity_id}.definition")
        )
    if set(normalized) != {BOOK_BATCH_ID, HEAVY_OBJECT_ID}:
        raise Room06TaskRuntimeError(
            "task_setup must exactly cover book_batch_01 and heavy_object"
        )

    bindings = _mapping(task_object_bindings, "task_object_bindings")
    if set(bindings) != {"schema", "scene_digest", "objects"}:
        raise Room06TaskRuntimeError("task_object_bindings schema mismatch")
    if bindings.get("schema") != "heterocoord.task-object-bindings.v1":
        raise Room06TaskRuntimeError("unsupported task_object_bindings schema")
    scene_digest = _digest(bindings.get("scene_digest"), "task_object_bindings.scene_digest")
    binding_rows = bindings.get("objects")
    if not isinstance(binding_rows, list) or len(binding_rows) != 2:
        raise Room06TaskRuntimeError(
            "task_object_bindings must contain exactly two objects"
        )
    by_id: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(binding_rows):
        row = _mapping(row, f"task_object_bindings.objects[{index}]")
        if set(row) != {
            "entity_id",
            "origin",
            "definition_ref",
            "definition_digest",
        }:
            raise Room06TaskRuntimeError(
                f"task_object_bindings.objects[{index}] schema mismatch"
            )
        entity_id = _text(
            row.get("entity_id"), f"task_object_bindings.objects[{index}].entity_id"
        )
        if entity_id in by_id:
            raise Room06TaskRuntimeError(
                f"task_object binding is duplicated: {entity_id}"
            )
        by_id[entity_id] = row
    if set(by_id) != set(normalized):
        raise Room06TaskRuntimeError(
            "task_object_bindings must exactly cover generated task entities"
        )
    objects: list[dict[str, Any]] = []
    definition_digests: dict[str, str] = {}
    for entity_id in (BOOK_BATCH_ID, HEAVY_OBJECT_ID):
        row = by_id[entity_id]
        if row.get("origin") != "task_generated":
            raise Room06TaskRuntimeError(
                f"task object origin must be task_generated: {entity_id}"
            )
        expected_ref = f"task-setup:{TASK_ID}/{entity_id}"
        if row.get("definition_ref") != expected_ref:
            raise Room06TaskRuntimeError(
                f"task object definition_ref mismatch: {entity_id}"
            )
        digest = _canonical_digest(normalized[entity_id])
        declared = _digest(
            row.get("definition_digest"), f"{entity_id}.definition_digest"
        )
        if declared != digest:
            raise Room06TaskRuntimeError(
                f"task object definition digest mismatch: {entity_id}"
            )
        definition_digests[entity_id] = digest
        objects.append(
            {
                "entity_id": entity_id,
                "definition": normalized[entity_id],
                "definition_digest": digest,
            }
        )
    normalized_setup = {
        "schema": TASK_SETUP_SCHEMA,
        "task_id": TASK_ID,
        "scene_digest": scene_digest,
        "objects": objects,
    }
    normalized_setup["task_setup_digest"] = _canonical_digest(normalized_setup)
    normalized_setup["source_task_setup_digest"] = source_task_setup_digest
    normalized_setup["definition_digests"] = definition_digests
    return normalized_setup


def _validate_preflight(
    report: Mapping[str, Any],
    expected_runtime_binding_digest: str,
    task_setup: Mapping[str, Any],
) -> str:
    report = _mapping(report, "preflight_report")
    expected = _digest(
        expected_runtime_binding_digest, "expected_runtime_binding_digest"
    )
    if report.get("schema") != PREFLIGHT_SCHEMA:
        raise Room06TaskRuntimeError("preflight report schema mismatch")
    if report.get("formal_gates") != FORMAL_GATES:
        raise Room06TaskRuntimeError("preflight formal gates must remain closed")
    if report.get("preflight_passed") is not True:
        raise Room06TaskRuntimeError("preflight report is not passing")
    if report.get("scene_smoke_may_start") is not True:
        raise Room06TaskRuntimeError("preflight did not authorize a scene smoke")
    if report.get("room_specific_feasibility") is not False:
        raise Room06TaskRuntimeError(
            "preflight cannot claim room-specific feasibility"
        )
    if report.get("runtime_binding_digest") != expected:
        raise Room06TaskRuntimeError("runtime_binding_digest mismatch")
    scene = _mapping(report.get("scene"), "preflight.scene")
    if scene.get("scene_asset_digest") != task_setup["scene_digest"]:
        raise Room06TaskRuntimeError(
            "preflight scene digest disagrees with task object bindings"
        )
    preflight_setup = _mapping(
        report.get("task_setup"), "preflight.task_setup"
    )
    if (
        preflight_setup.get("task_setup_digest")
        != task_setup["source_task_setup_digest"]
    ):
        raise Room06TaskRuntimeError("preflight task_setup_digest mismatch")
    if (
        preflight_setup.get("definition_digests")
        != task_setup["definition_digests"]
    ):
        raise Room06TaskRuntimeError(
            "preflight task definition digests mismatch"
        )
    node_id = _text(
        report.get("transition_handoff_node_id"),
        "preflight.transition_handoff_node_id",
    )
    if node_id == TRANSITION_PLACEHOLDER:
        raise Room06TaskRuntimeError(
            "transition_handoff_node_id cannot use the literal placeholder"
        )
    semantic = _mapping(report.get("semantic_map"), "preflight.semantic_map")
    if semantic.get("transition_handoff_node_id") != node_id:
        raise Room06TaskRuntimeError(
            "preflight transition_handoff_node_id is internally inconsistent"
        )
    if semantic.get("route_verified") is not True:
        raise Room06TaskRuntimeError("preflight transition route is not verified")
    cross_edges = semantic.get("cross_floor_edges")
    if not isinstance(cross_edges, list) or not cross_edges:
        raise Room06TaskRuntimeError("preflight has no cross-floor route evidence")
    if not any(
        isinstance(edge, Mapping)
        and node_id in {edge.get("source"), edge.get("target")}
        and edge.get("source_level") != edge.get("target_level")
        for edge in cross_edges
    ):
        raise Room06TaskRuntimeError(
            "preflight transition node is not bound to a cross-floor edge"
        )
    return node_id


class Room06TaskRuntime:
    """One monotonic evaluator-owned liv-t-04 logical evidence ledger."""

    def __init__(
        self,
        *,
        task_setup: Mapping[str, Any],
        task_object_bindings: Mapping[str, Any],
        preflight_report: Mapping[str, Any],
        runtime_binding_digest: str,
    ) -> None:
        self._task_setup = normalize_room06_task_setup(
            task_setup, task_object_bindings
        )
        self._runtime_binding_digest = _digest(
            runtime_binding_digest, "runtime_binding_digest"
        )
        self._transition_node_id = _validate_preflight(
            preflight_report,
            self._runtime_binding_digest,
            self._task_setup,
        )
        self._last_tick = -1
        self._state = "pending"
        self._open_holds: dict[str, int] = {}
        self._hold_intervals: list[dict[str, Any]] = []
        self._milestones: list[dict[str, Any]] = []
        self._task_events: list[dict[str, Any]] = []
        self._safety_failures: list[dict[str, Any]] = []
        self._singleton_event_keys: set[tuple[str, str]] = set()
        self._milestone_keys: set[str] = set()

    @property
    def state(self) -> str:
        return self._state

    def _advance(self, tick: int) -> int:
        tick = _tick(tick, "tick")
        if tick < self._last_tick:
            raise Room06TaskRuntimeError(
                f"tick {tick} precedes ledger tick {self._last_tick}"
            )
        self._last_tick = tick
        return tick

    def _require_state(self, *allowed: str) -> None:
        if self._state not in allowed:
            raise Room06TaskRuntimeError(
                f"operation is invalid while runtime state is {self._state}"
            )

    def _append_event(self, name: str, subject: str, tick: int) -> None:
        key = (_text(name, "task_event.name"), _text(subject, "task_event.subject"))
        if name in _SINGLETON_TASK_EVENTS and key in self._singleton_event_keys:
            raise Room06TaskRuntimeError(
                f"duplicate task event: {name}/{subject}"
            )
        if name in _SINGLETON_TASK_EVENTS:
            self._singleton_event_keys.add(key)
        self._task_events.append({"name": name, "subject": subject, "tick": tick})

    def _append_milestone(self, label: str, tick: int) -> None:
        key = _text(label, "milestone.label")
        if key in self._milestone_keys:
            raise Room06TaskRuntimeError(f"duplicate milestone: {label}")
        self._milestone_keys.add(key)
        self._milestones.append({"label": label, "tick": tick})

    def record_assignment(self, tick: int) -> None:
        self._require_state("pending")
        if "assigned" in self._milestone_keys:
            raise Room06TaskRuntimeError("duplicate milestone: assigned")
        tick = self._advance(tick)
        self._append_milestone("assigned", tick)

    def _acceptance_ticks(self, accepted_tick: int) -> tuple[int, ...]:
        milestone_ticks = {
            row["label"]: row["tick"] for row in self._milestones
        }
        transition_ticks = [
            row["tick"]
            for row in self._task_events
            if row["name"] == "transition_handoff_observed"
        ]
        required = {
            "assigned",
            "relay_started",
            "relay_complete",
            "heavy_carry_complete",
        }
        if not required.issubset(milestone_ticks) or len(transition_ticks) != 1:
            raise Room06TaskRuntimeError(
                "accepted requires the complete relay and carry evidence chain"
            )
        return (
            milestone_ticks["assigned"],
            milestone_ticks["relay_started"],
            transition_ticks[0],
            milestone_ticks["relay_complete"],
            milestone_ticks["heavy_carry_complete"],
            accepted_tick,
        )

    def _require_completed_relay_before_carry(self, carry_tick: int) -> None:
        milestone_ticks = {
            row["label"]: row["tick"] for row in self._milestones
        }
        transition_ticks = [
            row["tick"]
            for row in self._task_events
            if row["name"] == "transition_handoff_observed"
        ]
        required = {"assigned", "relay_started", "relay_complete"}
        if not required.issubset(milestone_ticks) or len(transition_ticks) != 1:
            raise Room06TaskRuntimeError(
                "joint carry requires the complete relay evidence chain"
            )
        chain = (
            milestone_ticks["assigned"],
            milestone_ticks["relay_started"],
            transition_ticks[0],
            milestone_ticks["relay_complete"],
            carry_tick,
        )
        if not (
            chain[0] <= chain[1]
            and chain[1] < chain[2] < chain[3] < chain[4]
        ):
            raise Room06TaskRuntimeError(
                "joint carry must start strictly after the ordered relay chain"
            )

    def record_milestone(self, label: str, tick: int) -> None:
        self._require_state("pending", "holding", "carried")
        if label not in _MILESTONE_LABELS:
            raise Room06TaskRuntimeError(f"unsupported caller milestone: {label}")
        if label == "accepted" and self._state != "carried":
            raise Room06TaskRuntimeError(
                "accepted cannot be recorded before joint carry completion"
            )
        if label == "accepted":
            tick = _tick(tick, "tick")
            if tick > HORIZON_TICKS:
                raise Room06TaskRuntimeError(
                    "accepted cannot be recorded after the liv-t-04 horizon"
                )
            chain = self._acceptance_ticks(tick)
            if not (
                chain[0] <= chain[1]
                and chain[1] < chain[2] < chain[3] < chain[4]
                and chain[4] <= chain[5]
            ):
                raise Room06TaskRuntimeError(
                    "accepted requires the authoritative relay/carry ordering"
                )
        tick = self._advance(tick)
        self._append_milestone(label, tick)
        if label == "accepted":
            self._state = "complete"

    def record_transition_handoff(
        self,
        *,
        tick: int,
        observed_node_id: str,
        runtime_binding_digest: str,
    ) -> None:
        self._require_state("pending")
        if _digest(runtime_binding_digest, "runtime_binding_digest") != self._runtime_binding_digest:
            raise Room06TaskRuntimeError("runtime_binding_digest mismatch")
        node_id = _text(observed_node_id, "observed_node_id")
        if node_id == TRANSITION_PLACEHOLDER:
            raise Room06TaskRuntimeError(
                "transition observation cannot use the literal placeholder"
            )
        if node_id != self._transition_node_id:
            raise Room06TaskRuntimeError(
                "transition observation does not match the frozen topology node"
            )
        tick = self._advance(tick)
        self._append_event("transition_handoff_observed", node_id, tick)

    def _normalize_holders(self, robot_ids: Sequence[str]) -> list[str]:
        if isinstance(robot_ids, (str, bytes)) or not isinstance(robot_ids, Sequence):
            raise Room06TaskRuntimeError("robot_ids must be a sequence")
        holders = [_text(value, "robot_id") for value in robot_ids]
        if len(set(holders)) != len(holders):
            raise Room06TaskRuntimeError("joint holders must be distinct robot IDs")
        return sorted(holders)

    def _close_holder(self, robot_id: str, tick: int) -> None:
        start = self._open_holds[robot_id]
        if tick < start:
            raise Room06TaskRuntimeError(
                f"hold interval for {robot_id} ends before it starts"
            )
        del self._open_holds[robot_id]
        if tick == start:
            return
        self._hold_intervals.append(
            {
                "object_id": HEAVY_OBJECT_ID,
                "robot_id": robot_id,
                "start_tick": start,
                "end_tick": tick,
            }
        )

    @staticmethod
    def _holder_evidence(attempted: Any) -> list[str]:
        if isinstance(attempted, (str, bytes)) or not isinstance(
            attempted, Sequence
        ):
            return []
        return sorted(
            {
                value
                for value in attempted
                if isinstance(value, str) and value.strip()
            }
        )

    def _safety_failure(self, *, tick: int, code: str, attempted: Any) -> None:
        for robot_id in sorted(tuple(self._open_holds)):
            self._close_holder(robot_id, tick)
        holder_ids = self._holder_evidence(attempted)
        failure = {
            "code": code,
            "tick": tick,
            "object_id": HEAVY_OBJECT_ID,
            "required_holders": MINIMUM_HOLDERS,
            "observed_holders": len(holder_ids),
            "holder_ids": holder_ids,
        }
        self._safety_failures.append(failure)
        self._append_event("joint_hold_safety_failure", HEAVY_OBJECT_ID, tick)
        self._state = "failed"

    def begin_joint_carry(self, *, tick: int, robot_ids: Sequence[str]) -> None:
        self._require_state("pending")
        tick = _tick(tick, "tick")
        self._require_completed_relay_before_carry(tick)
        tick = self._advance(tick)
        try:
            holders = self._normalize_holders(robot_ids)
        except Room06TaskRuntimeError:
            self._safety_failure(
                tick=tick,
                code="joint_hold_duplicate_or_invalid_holder",
                attempted=robot_ids,
            )
            raise
        if len(holders) < MINIMUM_HOLDERS:
            self._safety_failure(
                tick=tick,
                code="joint_hold_below_minimum_at_lift",
                attempted=holders,
            )
            raise Room06TaskRuntimeError(
                "heavy_object lift requires at least 2 distinct holders"
            )
        self._open_holds = {robot_id: tick for robot_id in holders}
        self._append_event("bilateral_support_established", HEAVY_OBJECT_ID, tick)
        self._state = "holding"

    def add_holder(self, *, tick: int, robot_id: str) -> None:
        self._require_state("holding")
        robot_id = _text(robot_id, "robot_id")
        if robot_id in self._open_holds:
            raise Room06TaskRuntimeError(f"holder is already active: {robot_id}")
        tick = self._advance(tick)
        self._open_holds[robot_id] = tick
        self._append_event("joint_holder_added", robot_id, tick)

    def release_holder(self, *, tick: int, robot_id: str) -> None:
        self._require_state("holding")
        robot_id = _text(robot_id, "robot_id")
        if robot_id not in self._open_holds:
            raise Room06TaskRuntimeError(f"holder is not active: {robot_id}")
        tick = self._advance(tick)
        remaining = sorted(set(self._open_holds) - {robot_id})
        if len(remaining) < MINIMUM_HOLDERS:
            self._safety_failure(
                tick=tick,
                code="joint_hold_below_minimum_during_carry",
                attempted=remaining,
            )
            return
        self._close_holder(robot_id, tick)
        self._append_event("joint_holder_released", robot_id, tick)

    def substitute_holder(
        self, *, tick: int, outgoing_robot_id: str, incoming_robot_id: str
    ) -> None:
        self._require_state("holding")
        outgoing = _text(outgoing_robot_id, "outgoing_robot_id")
        incoming = _text(incoming_robot_id, "incoming_robot_id")
        if outgoing == incoming:
            raise Room06TaskRuntimeError("substitute holder IDs must differ")
        if outgoing not in self._open_holds:
            raise Room06TaskRuntimeError(f"holder is not active: {outgoing}")
        if incoming in self._open_holds:
            raise Room06TaskRuntimeError(f"holder is already active: {incoming}")
        tick = self._advance(tick)
        # Add first and release second at the same tick.  Half-open intervals
        # make the substitution continuous: outgoing ends where incoming starts.
        self._open_holds[incoming] = tick
        self._append_event("joint_holder_added", incoming, tick)
        self._close_holder(outgoing, tick)
        self._append_event("joint_holder_released", outgoing, tick)

    def complete_joint_carry(self, *, tick: int) -> None:
        self._require_state("holding")
        tick = self._advance(tick)
        if len(self._open_holds) < MINIMUM_HOLDERS:
            self._safety_failure(
                tick=tick,
                code="joint_hold_below_minimum_at_completion",
                attempted=tuple(self._open_holds),
            )
            return
        for robot_id in sorted(tuple(self._open_holds)):
            self._close_holder(robot_id, tick)
        self._append_milestone("heavy_carry_complete", tick)
        self._state = "carried"

    def finalize_at_horizon(self, *, tick: int) -> None:
        """Seal complete partial streams when the task misses its horizon."""

        self._require_state("pending", "holding", "carried")
        tick = _tick(tick, "tick")
        if tick != HORIZON_TICKS:
            raise Room06TaskRuntimeError(
                f"horizon finalization tick must equal {HORIZON_TICKS}"
            )
        tick = self._advance(tick)
        for robot_id in sorted(tuple(self._open_holds)):
            self._close_holder(robot_id, tick)
        self._append_event("task_horizon_reached", TASK_ID, tick)
        self._state = "incomplete"

    def evidence_material(self) -> dict[str, Any]:
        """Return JSON evidence after success, safety failure or horizon miss."""

        self._require_state("complete", "failed", "incomplete")
        if self._open_holds:
            raise Room06TaskRuntimeError("cannot export evidence with open hold sessions")
        streams = {
            "zone_intervals": [],
            "hold_intervals": sorted(
                deepcopy(self._hold_intervals),
                key=lambda row: (
                    row["start_tick"],
                    row["end_tick"],
                    row["robot_id"],
                ),
            ),
            "milestones": sorted(
                deepcopy(self._milestones), key=lambda row: (row["tick"], row["label"])
            ),
            "task_events": sorted(
                deepcopy(self._task_events),
                key=lambda row: (row["tick"], row["name"], row["subject"]),
            ),
            "identity_records": {
                "task_setup_digest": self._task_setup["task_setup_digest"],
                "definition_digests": deepcopy(
                    self._task_setup["definition_digests"]
                ),
                "transition_handoff_node_id": self._transition_node_id,
                "runtime_binding_digest": self._runtime_binding_digest,
            },
            "integrity_confirmed": True,
        }
        return {
            "schema": RUNTIME_EVIDENCE_SCHEMA,
            "task_id": TASK_ID,
            "runtime_binding_digest": self._runtime_binding_digest,
            "transition_handoff_node_id": self._transition_node_id,
            "runtime_binding": {
                "schema": RUNTIME_BINDING_SCHEMA,
                "anchor_id": TASK_ID,
                "scene_id": "room_06",
                "transition_handoff_node_id": self._transition_node_id,
                "runtime_binding_digest": self._runtime_binding_digest,
            },
            "task_setup": deepcopy(self._task_setup),
            "streams": streams,
            "safety_failures": deepcopy(self._safety_failures),
            "runtime_state": self._state,
            "room_specific_feasibility": False,
            "formal_gates": dict(FORMAL_GATES),
        }


__all__ = [
    "BOOK_BATCH_ID",
    "BOOK_DESTINATIONS",
    "HEAVY_OBJECT_ID",
    "HORIZON_TICKS",
    "MINIMUM_HOLDERS",
    "RUNTIME_BINDING_SCHEMA",
    "RUNTIME_EVIDENCE_SCHEMA",
    "Room06TaskRuntime",
    "Room06TaskRuntimeError",
    "normalize_room06_task_setup",
    "normalize_task_definition",
    "task_definition_digest",
]
