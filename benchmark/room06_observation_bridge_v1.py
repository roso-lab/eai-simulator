"""Fail-closed observer bridge for ``room_06 × liv-t-04``.

The bridge accepts normalized observations owned by the benchmark runtime.  It
does not infer success from commands or lock grants: task milestones come from
completed skill-session observations, while the heavy-object safety predicate
comes from the measured logical-hold snapshot on every sampled tick.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from .room06_task_runtime_v1 import (
    BOOK_BATCH_ID,
    BOOK_DESTINATIONS,
    HEAVY_OBJECT_ID,
    HORIZON_TICKS,
    Room06TaskRuntime,
    Room06TaskRuntimeError,
)


OBSERVED_SKILL_EVENT_SCHEMA = "heterocoord.observed-skill-event.v1"
OBSERVATION_BRIDGE_SCHEMA = "heterocoord.room06-observation-bridge.v1"
_EVENT_FIELDS = {
    "schema",
    "session_id",
    "event_type",
    "status",
    "tick",
    "robot_id",
    "object_id",
    "target_ref",
    "evidence_digest",
}
_EVENT_TYPES = {
    "relay_pickup_completed",
    "topology_handoff_completed",
    "relay_delivery_completed",
    "heavy_carry_completed",
    "task_accepted",
}
_HEX = frozenset("0123456789abcdef")


class Room06ObservationBridgeError(ValueError):
    """Raised when observer input is incomplete, stale or contradictory."""


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Room06ObservationBridgeError(f"{name} must be non-empty text")
    return value


def _digest(value: Any, name: str) -> str:
    value = _text(value, name)
    if len(value) != 64 or any(character not in _HEX for character in value):
        raise Room06ObservationBridgeError(f"{name} must be a lowercase SHA-256")
    return value


def _tick(value: Any, name: str = "tick") -> int:
    if type(value) is not int or value < 0:
        raise Room06ObservationBridgeError(
            f"{name} must be a non-negative integer tick"
        )
    return value


def _canonical_digest(value: Any) -> str:
    try:
        payload = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise Room06ObservationBridgeError(
            "observation is not finite canonical JSON"
        ) from exc
    return hashlib.sha256(payload).hexdigest()


def normalize_skill_event(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _EVENT_FIELDS:
        raise Room06ObservationBridgeError("observed skill event schema mismatch")
    if value.get("schema") != OBSERVED_SKILL_EVENT_SCHEMA:
        raise Room06ObservationBridgeError("unsupported observed skill event schema")
    event_type = _text(value.get("event_type"), "event_type")
    if event_type not in _EVENT_TYPES:
        raise Room06ObservationBridgeError(
            f"unsupported observed skill event type: {event_type}"
        )
    if value.get("status") != "completed":
        raise Room06ObservationBridgeError(
            "only completed skill-session events can advance task evidence"
        )
    return {
        "schema": OBSERVED_SKILL_EVENT_SCHEMA,
        "session_id": _text(value.get("session_id"), "session_id"),
        "event_type": event_type,
        "status": "completed",
        "tick": _tick(value.get("tick"), "event.tick"),
        "robot_id": _text(value.get("robot_id"), "robot_id"),
        "object_id": _text(value.get("object_id"), "object_id"),
        "target_ref": _text(value.get("target_ref"), "target_ref"),
        "evidence_digest": _digest(value.get("evidence_digest"), "evidence_digest"),
    }


class Room06ObservationBridge:
    """Translate observer-owned facts into one evaluator-private runtime ledger."""

    def __init__(
        self,
        runtime: Room06TaskRuntime,
        *,
        robot_ids: Sequence[str],
        runtime_binding_digest: str,
    ) -> None:
        if not isinstance(runtime, Room06TaskRuntime):
            raise TypeError("runtime must be Room06TaskRuntime")
        if isinstance(robot_ids, (str, bytes)) or not isinstance(robot_ids, Sequence):
            raise Room06ObservationBridgeError("robot_ids must be a sequence")
        normalized = tuple(sorted(_text(item, "robot_id") for item in robot_ids))
        if not normalized or len(set(normalized)) != len(normalized):
            raise Room06ObservationBridgeError(
                "robot_ids must contain unique non-empty IDs"
            )
        self._runtime = runtime
        self._robot_ids = normalized
        self._runtime_binding_digest = _digest(
            runtime_binding_digest, "runtime_binding_digest"
        )
        self._assignment_digest: str | None = None
        self._assignment_robot_ids: tuple[str, ...] = ()
        self._session_digests: dict[str, str] = {}
        self._delivery_targets: set[str] = set()
        self._holders: set[str] = set()
        self._last_tick = -1
        self._observed_events: list[dict[str, Any]] = []

    @property
    def runtime(self) -> Room06TaskRuntime:
        return self._runtime

    def _advance(self, tick: int) -> int:
        tick = _tick(tick)
        if tick < self._last_tick:
            raise Room06ObservationBridgeError(
                f"observation tick {tick} precedes bridge tick {self._last_tick}"
            )
        self._last_tick = tick
        return tick

    def observe_assignment(
        self,
        *,
        tick: int,
        assigned_robot_ids: Sequence[str],
        decision_digest: str,
    ) -> None:
        if self._assignment_digest is not None:
            raise Room06ObservationBridgeError("assignment was already observed")
        if isinstance(assigned_robot_ids, (str, bytes)) or not isinstance(
            assigned_robot_ids, Sequence
        ):
            raise Room06ObservationBridgeError(
                "assigned_robot_ids must be a sequence"
            )
        assigned = tuple(
            sorted(_text(item, "assigned_robot_id") for item in assigned_robot_ids)
        )
        if not assigned or len(set(assigned)) != len(assigned):
            raise Room06ObservationBridgeError(
                "assigned_robot_ids must be unique and non-empty"
            )
        if not set(assigned).issubset(self._robot_ids):
            raise Room06ObservationBridgeError(
                "assignment contains a robot outside the frozen runtime roster"
            )
        tick = self._advance(tick)
        self._assignment_digest = _digest(decision_digest, "decision_digest")
        self._assignment_robot_ids = assigned
        self._runtime.record_assignment(tick)

    def observe_skill_event(self, value: Mapping[str, Any]) -> None:
        event = normalize_skill_event(value)
        if self._assignment_digest is None:
            raise Room06ObservationBridgeError(
                "skill event cannot precede an observed assignment"
            )
        if event["robot_id"] not in self._assignment_robot_ids:
            raise Room06ObservationBridgeError(
                "skill event robot is outside the observed assignment"
            )
        session_digest = _canonical_digest(event)
        previous = self._session_digests.get(event["session_id"])
        if previous is not None:
            if previous != session_digest:
                raise Room06ObservationBridgeError(
                    "skill session was replayed with different evidence"
                )
            return
        tick = self._advance(event["tick"])
        self._session_digests[event["session_id"]] = session_digest

        event_type = event["event_type"]
        object_id = event["object_id"]
        target_ref = event["target_ref"]
        try:
            if event_type == "relay_pickup_completed":
                if object_id != BOOK_BATCH_ID or target_ref != "floor1-bookshelf":
                    raise Room06ObservationBridgeError(
                        "relay pickup must bind book_batch_01 at floor1-bookshelf"
                    )
                self._runtime.record_milestone("relay_started", tick)
            elif event_type == "topology_handoff_completed":
                if object_id != BOOK_BATCH_ID:
                    raise Room06ObservationBridgeError(
                        "topology handoff must bind book_batch_01"
                    )
                self._runtime.record_transition_handoff(
                    tick=tick,
                    observed_node_id=target_ref,
                    runtime_binding_digest=self._runtime_binding_digest,
                )
            elif event_type == "relay_delivery_completed":
                if object_id != BOOK_BATCH_ID or target_ref not in BOOK_DESTINATIONS:
                    raise Room06ObservationBridgeError(
                        "relay delivery target is not a registered floor2 bookshelf"
                    )
                if target_ref in self._delivery_targets:
                    raise Room06ObservationBridgeError(
                        f"relay delivery target was duplicated: {target_ref}"
                    )
                self._delivery_targets.add(target_ref)
                if self._delivery_targets == set(BOOK_DESTINATIONS):
                    self._runtime.record_milestone("relay_complete", tick)
            elif event_type == "heavy_carry_completed":
                if object_id != HEAVY_OBJECT_ID:
                    raise Room06ObservationBridgeError(
                        "heavy carry completion must bind heavy_object"
                    )
                self._runtime.complete_joint_carry(tick=tick)
                self._holders.clear()
            else:
                if object_id != "liv-t-04" or target_ref != "accepted":
                    raise Room06ObservationBridgeError(
                        "task acceptance event must bind liv-t-04/accepted"
                    )
                self._runtime.record_milestone("accepted", tick)
        except Room06TaskRuntimeError as exc:
            raise Room06ObservationBridgeError(str(exc)) from exc
        self._observed_events.append(deepcopy(event))

    def sample_holds(self, *, tick: int, held_objects: Mapping[str, str | None]) -> None:
        """Consume a complete roster snapshot from held_object/skill-session state."""

        if not isinstance(held_objects, Mapping) or set(held_objects) != set(
            self._robot_ids
        ):
            raise Room06ObservationBridgeError(
                "held_objects must exactly cover the frozen runtime roster"
            )
        tick = self._advance(tick)
        observed: set[str] = set()
        for robot_id in self._robot_ids:
            object_id = held_objects[robot_id]
            if object_id is not None and (
                not isinstance(object_id, str) or not object_id.strip()
            ):
                raise Room06ObservationBridgeError(
                    f"held_objects[{robot_id!r}] must be an object ID or null"
                )
            if object_id == HEAVY_OBJECT_ID:
                observed.add(robot_id)
        if not observed.issubset(self._assignment_robot_ids):
            raise Room06ObservationBridgeError(
                "heavy-object holder is outside the observed assignment"
            )
        try:
            if not self._holders and observed:
                self._runtime.begin_joint_carry(
                    tick=tick, robot_ids=tuple(sorted(observed))
                )
                self._holders = set(observed)
                return
            if not self._holders:
                return
            for robot_id in sorted(observed - self._holders):
                self._runtime.add_holder(tick=tick, robot_id=robot_id)
                self._holders.add(robot_id)
            for robot_id in sorted(self._holders - observed):
                self._runtime.release_holder(tick=tick, robot_id=robot_id)
                self._holders.discard(robot_id)
                if self._runtime.state == "failed":
                    self._holders.clear()
                    return
        except Room06TaskRuntimeError as exc:
            raise Room06ObservationBridgeError(str(exc)) from exc

    def finalize_at_horizon(self) -> None:
        tick = self._advance(HORIZON_TICKS)
        try:
            self._runtime.finalize_at_horizon(tick=tick)
        except Room06TaskRuntimeError as exc:
            raise Room06ObservationBridgeError(str(exc)) from exc

    def evidence_material(self) -> dict[str, Any]:
        try:
            runtime = self._runtime.evidence_material()
        except Room06TaskRuntimeError as exc:
            raise Room06ObservationBridgeError(str(exc)) from exc
        return {
            "schema": OBSERVATION_BRIDGE_SCHEMA,
            "runtime_binding_digest": self._runtime_binding_digest,
            "assignment": {
                "decision_digest": self._assignment_digest,
                "robot_ids": list(self._assignment_robot_ids),
            },
            "observed_skill_events": deepcopy(self._observed_events),
            "runtime_evidence": runtime,
        }


__all__ = [
    "OBSERVATION_BRIDGE_SCHEMA",
    "OBSERVED_SKILL_EVENT_SCHEMA",
    "Room06ObservationBridge",
    "Room06ObservationBridgeError",
    "normalize_skill_event",
]
