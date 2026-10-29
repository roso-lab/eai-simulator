"""Fail-closed EAI adapter for the room_06/liv-t-04 probe hook.

The probe loads :func:`collect_room06_runtime_hook` by a content-addressed
file reference.  This module does not create a task runner, translate commands
into milestones, or infer terminal success.  A production session must attach
an observer-owned ``room06_runtime_hook_source`` to ``session.base_env``.  That
source drives the real task and reports completed skill-session observations
through the collector passed to ``collect_room06``.

The collector reads benchmark-owned logical object state after real controller
arrival and completed host skill sessions. USD task-object identity is never
required and tested-system messages cannot set the ledger.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import json
from typing import Any


HOOK_CAPTURE_SCHEMA = "heterocoord.room06-runtime-hook-capture.v1"
HOOK_SOURCE_SCHEMA = "heterocoord.eai-room06-runtime-hook-source.v1"
OBSERVED_SKILL_EVENT_SCHEMA = "heterocoord.observed-skill-event.v1"
HORIZON_TICKS = 15_000
_HEX = frozenset("0123456789abcdef")
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
_ASSIGNMENT_FIELDS = {"tick", "assigned_robot_ids", "decision_digest"}
_TERMINAL_INPUT_FIELDS = {
    "episode",
    "d1",
    "d4_first_event_observation",
    "d6_measurement",
}
_SOURCE_RESULT_FIELDS = {"assignment", "terminal_inputs"}


class Room06RuntimeHookError(RuntimeError):
    """Raised when the live observer/readback chain is incomplete."""


class CallbackRoom06RuntimeHookSource:
    """Explicit registration wrapper for a host-owned room06 observer.

    The wrapper contains no task semantics and never fabricates observations.
    Hosts provide the logical object definition reader and the skill-session collector
    as separate callbacks; :func:`collect_room06_runtime_hook` continues to
    enforce all identity, hold, event, and terminal-input invariants.
    """

    schema = HOOK_SOURCE_SCHEMA

    def __init__(self, *, object_bindings: Any, collect_room06: Any) -> None:
        if not callable(object_bindings):
            raise TypeError("object_bindings callback must be callable")
        if not callable(collect_room06):
            raise TypeError("collect_room06 callback must be callable")
        self._object_bindings = object_bindings
        self._collect_room06 = collect_room06

    def object_bindings(self, **kwargs: Any) -> Any:
        return self._object_bindings(**kwargs)

    def collect_room06(self, **kwargs: Any) -> Any:
        return self._collect_room06(**kwargs)


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Room06RuntimeHookError(f"{name} must be non-empty text")
    return value


def _digest(value: Any, name: str) -> str:
    value = _text(value, name)
    if len(value) != 64 or any(character not in _HEX for character in value):
        raise Room06RuntimeHookError(f"{name} must be a lowercase SHA-256")
    return value


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise Room06RuntimeHookError(f"{name} must be an object")
    return value


def _exact(value: Any, fields: set[str], name: str) -> Mapping[str, Any]:
    result = _mapping(value, name)
    if set(result) != fields:
        raise Room06RuntimeHookError(f"{name} schema mismatch")
    return result


def _json_copy(value: Any, name: str) -> Any:
    try:
        return json.loads(
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        )
    except (TypeError, ValueError) as exc:
        raise Room06RuntimeHookError(f"{name} must be finite canonical JSON") from exc


def _selected_robot_ids(package: Mapping[str, Any]) -> tuple[str, ...]:
    plan = _mapping(package.get("plan"), "package.plan")
    value = plan.get("selected_robot_ids")
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise Room06RuntimeHookError(
            "package.plan.selected_robot_ids must be a sequence"
        )
    result = tuple(_text(item, "selected_robot_id") for item in value)
    if not result or len(set(result)) != len(result):
        raise Room06RuntimeHookError(
            "package.plan.selected_robot_ids must be unique and non-empty"
        )
    return result


def _task_object_ids(package: Mapping[str, Any]) -> tuple[str, ...]:
    from benchmark.room06_task_instance_v2 import logical_task_objects

    setup = package.get("task_setup")
    if isinstance(setup, Mapping):
        try:
            rows = logical_task_objects(setup, require_individual_books=False)
        except ValueError as exc:
            raise Room06RuntimeHookError(str(exc)) from exc
        return tuple(row["entity_id"] for row in rows)
    bindings = _mapping(package.get("task_object_bindings"), "task_object_bindings")
    rows = bindings.get("objects")
    if not isinstance(rows, list) or not rows:
        raise Room06RuntimeHookError("task_object_bindings.objects must be non-empty")
    result: list[str] = []
    for index, row in enumerate(rows):
        item = _mapping(row, f"task_object_bindings.objects[{index}]")
        result.append(_text(item.get("entity_id"), f"task object {index}.entity_id"))
    if len(set(result)) != len(result):
        raise Room06RuntimeHookError("task object entity IDs must be unique")
    return tuple(result)


def _runtime_agent_map(
    value: Mapping[str, Any], selected_robot_ids: Sequence[str]
) -> dict[str, str]:
    mapping = _mapping(value, "instance_agent_map")
    if set(mapping) != set(selected_robot_ids):
        raise Room06RuntimeHookError(
            "instance_agent_map must exactly cover the frozen runtime roster"
        )
    result = {
        robot_id: _text(mapping[robot_id], f"instance_agent_map[{robot_id!r}]")
        for robot_id in selected_robot_ids
    }
    if len(set(result.values())) != len(result):
        raise Room06RuntimeHookError("runtime agent names must be unique")
    return result


def _object_bindings(value: Any, object_ids: Sequence[str]) -> dict[str, Any]:
    if not isinstance(value, list) or any(not isinstance(row, Mapping) for row in value):
        raise Room06RuntimeHookError("logical object bindings must be definition rows")
    rows = {row.get("entity_id"): dict(row) for row in value}
    if len(rows) != len(value) or set(rows) != set(object_ids):
        raise Room06RuntimeHookError("logical object bindings must exactly cover task objects")
    return rows

def _physics_tick(base_env: Any) -> int:
    value = getattr(base_env, "common_step_counter", None)
    if value is None:
        value = getattr(getattr(base_env, "sim", None), "current_time_step_index", None)
    if type(value) is not int or not 0 <= value <= HORIZON_TICKS:
        raise Room06RuntimeHookError(
            "environment exposes no valid integer room06 physics tick"
        )
    return value


def _normalize_event(value: Any) -> dict[str, Any]:
    event = _exact(value, _EVENT_FIELDS, "completed skill-session observation")
    if event.get("schema") != OBSERVED_SKILL_EVENT_SCHEMA:
        raise Room06RuntimeHookError("unsupported observed skill event schema")
    if event.get("status") != "completed":
        raise Room06RuntimeHookError(
            "only completed skill-session observations may be collected"
        )
    event_type = _text(event.get("event_type"), "event_type")
    if event_type not in _EVENT_TYPES:
        raise Room06RuntimeHookError(
            f"unsupported observed skill event type: {event_type}"
        )
    tick = event.get("tick")
    if type(tick) is not int or not 0 <= tick <= HORIZON_TICKS:
        raise Room06RuntimeHookError("skill-session tick is invalid")
    return {
        "schema": OBSERVED_SKILL_EVENT_SCHEMA,
        "session_id": _text(event.get("session_id"), "session_id"),
        "event_type": event_type,
        "status": "completed",
        "tick": tick,
        "robot_id": _text(event.get("robot_id"), "robot_id"),
        "object_id": _text(event.get("object_id"), "object_id"),
        "target_ref": _text(event.get("target_ref"), "target_ref"),
        "evidence_digest": _digest(event.get("evidence_digest"), "evidence_digest"),
    }


class Room06RuntimeHookCollector:
    """Observer sink whose hold rows come only from benchmark-owned logical skill state."""

    def __init__(
        self,
        *,
        base_env: Any,
        selected_robot_ids: Sequence[str],
        instance_agent_map: Mapping[str, str],
        object_bindings: Mapping[str, Any],
    ) -> None:
        self._base_env = base_env
        self._selected_robot_ids = tuple(selected_robot_ids)
        self._instance_agent_map = dict(instance_agent_map)
        self._object_definitions = dict(object_bindings)
        self._observations: list[dict[str, Any]] = []
        self._session_ids: set[tuple[str, str]] = set()
        self._event_robot_ids: set[str] = set()
        self._first_tick: int | None = None
        self._last_tick = -1
        self._event_count = 0
        self._hold_count = 0
        self._horizon_count = 0
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def _require_open(self) -> None:
        if self._closed:
            raise Room06RuntimeHookError("room06 runtime hook collector is closed")

    def _advance(self, tick: int) -> None:
        if tick < self._last_tick:
            raise Room06RuntimeHookError(
                f"observer tick {tick} precedes prior tick {self._last_tick}"
            )
        if self._first_tick is None:
            self._first_tick = tick
        self._last_tick = tick

    def _logical_runtime(self):
        from task.runtime.logical_objects import LogicalObjectRuntime
        runtime = getattr(self._base_env, "logical_object_runtime", None)
        if not isinstance(runtime, LogicalObjectRuntime):
            raise Room06RuntimeHookError("benchmark logical object ledger is unavailable")
        if runtime.definitions != self._object_definitions or set(runtime.robot_ids) != set(self._selected_robot_ids):
            raise Room06RuntimeHookError("logical object ledger identity differs from the frozen case")
        return runtime

    def observe_completed_skill_session(self, value: Mapping[str, Any]) -> None:
        """Retain one observer-owned completion without deriving its semantics."""

        self._require_open()
        event = _normalize_event(value)
        runtime = self._logical_runtime()
        try:
            record = runtime.session_record(event["session_id"])
        except KeyError as exc:
            raise Room06RuntimeHookError("event has no benchmark-owned successful skill session") from exc
        if (record["status"] != "completed" or event["robot_id"] not in record["robot_ids"]
                or record["tick"] != event["tick"] or record["evidence_digest"] != event["evidence_digest"]):
            raise Room06RuntimeHookError("event differs from benchmark-owned skill-session evidence")
        operations = {
            "relay_pickup_completed": {"pick", "load"},
            "topology_handoff_completed": {"receive"},
            "relay_delivery_completed": {"place", "deliver", "store"},
            "heavy_carry_completed": {"place", "deliver", "store"},
        }
        if event["event_type"] == "task_accepted":
            if any(runtime.object_state(entity)["state"] != "delivered" for entity in runtime.definitions):
                raise Room06RuntimeHookError("task acceptance requires all logical objects delivered")
        elif (record["operation"] not in operations[event["event_type"]]
              or record["object_id"] != event["object_id"]
              or record["semantic_point"] != event["target_ref"]):
            raise Room06RuntimeHookError("event semantics differ from the completed skill session")
        if event["robot_id"] not in self._selected_robot_ids:
            raise Room06RuntimeHookError(
                "completed skill-session robot is outside the frozen roster"
            )
        session_event = (event["session_id"], event["event_type"])
        if session_event in self._session_ids:
            raise Room06RuntimeHookError("completed skill-session ID was duplicated")
        self._advance(event["tick"])
        self._session_ids.add(session_event)
        self._event_robot_ids.add(event["robot_id"])
        self._observations.append({"kind": "skill_event", "event": event})
        self._event_count += 1

    def sample_holds(self) -> None:
        """Read one exact-roster logical-hold snapshot at the live env tick."""

        self._require_open()
        tick = _physics_tick(self._base_env)
        self._advance(tick)
        runtime = self._logical_runtime()
        try:
            held_objects = runtime.sample_holds()
        except (ValueError, KeyError) as exc:
            raise Room06RuntimeHookError(f"logical holding observation failed: {exc}") from exc
        if set(held_objects) != set(self._selected_robot_ids):
            raise Room06RuntimeHookError("logical holding snapshot must cover the deployed roster")
        if any(value is not None and value not in self._object_definitions for value in held_objects.values()):
            raise Room06RuntimeHookError("unknown task object in logical holding snapshot")
        self._observations.append(
            {"kind": "held_object_snapshot", "tick": tick, "held_objects": held_objects}
        )
        self._hold_count += 1

    def observe_horizon(self) -> None:
        """Retain an explicit observer-owned horizon only at live tick 15000."""

        self._require_open()
        tick = _physics_tick(self._base_env)
        if tick != HORIZON_TICKS:
            raise Room06RuntimeHookError(
                "room06 horizon observation requires live tick 15000"
            )
        if self._horizon_count:
            raise Room06RuntimeHookError("room06 horizon was already observed")
        self._advance(tick)
        self._observations.append(
            {"kind": "horizon_reached", "tick": HORIZON_TICKS, "source": "observer"}
        )
        self._horizon_count = 1

    def close(self) -> None:
        """Close the source-owned collection once all live callbacks have run."""

        if self._closed:
            raise Room06RuntimeHookError("room06 runtime hook collector closed twice")
        self._closed = True

    def capture(
        self,
        *,
        assignment: Mapping[str, Any],
        terminal_inputs: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not self._closed:
            raise Room06RuntimeHookError(
                "room06 runtime hook source did not close its collector"
            )
        if self._event_count == 0 and self._horizon_count == 0:
            raise Room06RuntimeHookError(
                "room06 runtime hook source emitted no completed skill-session observations or live horizon"
            )
        if self._hold_count == 0:
            raise Room06RuntimeHookError(
                "room06 runtime hook source emitted no held_object snapshots"
            )
        observed_assignment = _exact(assignment, _ASSIGNMENT_FIELDS, "assignment")
        tick = observed_assignment.get("tick")
        if (
            type(tick) is not int
            or tick < 0
            or self._first_tick is None
            or tick > self._first_tick
        ):
            raise Room06RuntimeHookError("assignment tick is invalid or follows observations")
        assigned = observed_assignment.get("assigned_robot_ids")
        if isinstance(assigned, (str, bytes)) or not isinstance(assigned, Sequence):
            raise Room06RuntimeHookError("assignment.assigned_robot_ids must be a sequence")
        assigned_ids = tuple(_text(item, "assigned_robot_id") for item in assigned)
        if (
            len(set(assigned_ids)) != len(assigned_ids)
            or not set(assigned_ids).issubset(self._selected_robot_ids)
        ):
            raise Room06RuntimeHookError(
                "assignment robot IDs must be a unique frozen-roster subset"
            )
        if not self._event_robot_ids.issubset(assigned_ids):
            raise Room06RuntimeHookError(
                "completed skill-session robot is outside the observed assignment"
            )
        decision_digest = _digest(
            observed_assignment.get("decision_digest"), "assignment.decision_digest"
        )
        terminal = _exact(
            terminal_inputs, _TERMINAL_INPUT_FIELDS, "terminal_inputs"
        )
        result = {
            "schema": HOOK_CAPTURE_SCHEMA,
            "assignment": {
                "tick": tick,
                "assigned_robot_ids": list(assigned_ids),
                "decision_digest": decision_digest,
            },
            "observations": deepcopy(self._observations),
            "terminal_inputs": _json_copy(dict(terminal), "terminal_inputs"),
            "collector_closed": True,
        }
        return _json_copy(result, "runtime hook capture")


def collect_room06_runtime_hook(
    *,
    session: Any,
    package: Mapping[str, Any],
    instance_agent_map: Mapping[str, Any],
) -> dict[str, Any]:
    """Collect real source callbacks into the probe's frozen capture schema.

    Required session capability (currently not registered by the production
    room_06 runner): ``session.base_env.room06_runtime_hook_source`` with
    ``schema == HOOK_SOURCE_SCHEMA`` and two callables:

    - ``object_bindings(package=..., instance_agent_map=...)`` returns an
      independent logical object definitions matching the case ledger.
    - ``collect_room06(package=..., instance_agent_map=..., collector=...)``
      drives/observes the real task, calls the collector methods, explicitly
      closes it, and returns exactly ``assignment`` plus ``terminal_inputs``.
    """

    package = _mapping(package, "package")
    base_env = getattr(session, "base_env", None)
    if base_env is None:
        raise Room06RuntimeHookError("simulator session exposes no base_env")
    num_envs = getattr(base_env, "num_envs", None)
    if type(num_envs) is not int or num_envs != 1:
        raise Room06RuntimeHookError("room06 runtime hook requires exactly one environment")
    selected = _selected_robot_ids(package)
    agents = _runtime_agent_map(instance_agent_map, selected)
    object_ids = _task_object_ids(package)

    source = getattr(base_env, "room06_runtime_hook_source", None)
    if source is None or getattr(source, "schema", None) != HOOK_SOURCE_SCHEMA:
        raise Room06RuntimeHookError(
            "production room06 skill-session observer source is unavailable"
        )
    binding_reader = getattr(source, "object_bindings", None)
    collect = getattr(source, "collect_room06", None)
    if not callable(binding_reader) or not callable(collect):
        raise Room06RuntimeHookError("room06 runtime hook source interface is incomplete")
    try:
        raw_bindings = binding_reader(
            package=_json_copy(package, "package"),
            instance_agent_map=dict(agents),
        )
    except Exception as exc:
        raise Room06RuntimeHookError(
            f"room06 logical object binding failed: {exc}"
        ) from exc
    bindings = _object_bindings(raw_bindings, object_ids)
    collector = Room06RuntimeHookCollector(
        base_env=base_env,
        selected_robot_ids=selected,
        instance_agent_map=agents,
        object_bindings=bindings,
    )
    try:
        source_result = collect(
            package=_json_copy(package, "package"),
            instance_agent_map=dict(agents),
            collector=collector,
        )
    except Room06RuntimeHookError:
        raise
    except Exception as exc:
        raise Room06RuntimeHookError(
            f"production room06 observer collection failed: {exc}"
        ) from exc
    result = _exact(source_result, _SOURCE_RESULT_FIELDS, "room06 source result")
    return collector.capture(
        assignment=result["assignment"],
        terminal_inputs=result["terminal_inputs"],
    )


__all__ = [
    "HOOK_CAPTURE_SCHEMA",
    "HOOK_SOURCE_SCHEMA",
    "CallbackRoom06RuntimeHookSource",
    "OBSERVED_SKILL_EVENT_SCHEMA",
    "Room06RuntimeHookCollector",
    "Room06RuntimeHookError",
    "collect_room06_runtime_hook",
]
