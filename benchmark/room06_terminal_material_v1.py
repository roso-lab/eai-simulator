"""Strict terminal-material assembler for ``room_06 × liv-t-04``.

The task runtime owns the terminal event/hold ledger.  D1, the registered D4
first-event observation and D6 are separate measured inputs.  This module
joins those inputs at one narrow JSON boundary and returns a payload that can
be passed directly to :class:`Room06LivT04Evaluator`.

It intentionally does not run the task oracle.  In particular, the assembler
never derives obligations or final success and never accepts those fields from
its caller.  It derives only ``base_task_success`` from a fully checked runtime
terminal state; the evaluator/oracle remains the sole owner of the final
``success`` and ``obligations`` facts.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from .d4_first_event_v1 import (
    D4FirstEventError,
    FACT_KEY as D4_FIRST_EVENT_FACT_KEY,
    validate_observation,
)
from .evaluator.room06_livt04 import EVALUATOR_VERSION
from .room06_runtime_preflight_v1 import FORMAL_GATES, TASK_SETUP_SCHEMA
from .room06_task_runtime_v1 import (
    BOOK_BATCH_ID,
    HEAVY_OBJECT_ID,
    HORIZON_TICKS,
    MINIMUM_HOLDERS,
    RUNTIME_BINDING_SCHEMA,
    RUNTIME_EVIDENCE_SCHEMA,
    TASK_ID,
    normalize_task_definition,
)


ASSEMBLER_SCHEMA = "heterocoord.room06-terminal-material.v1"
D6_BITS_UNIT = "serialized_bit"
D6_TIME_UNIT = "simulated_physical_second"
_HEX = frozenset("0123456789abcdef")
_EVIDENCE_KEYS = {
    "schema",
    "task_id",
    "runtime_binding_digest",
    "transition_handoff_node_id",
    "runtime_binding",
    "task_setup",
    "streams",
    "safety_failures",
    "runtime_state",
    "room_specific_feasibility",
    "formal_gates",
}
_STREAM_KEYS = {
    "zone_intervals",
    "hold_intervals",
    "milestones",
    "task_events",
    "identity_records",
    "integrity_confirmed",
}
_EPISODE_REQUIRED = {
    "status",
    "condition_id",
    "branch",
    "repeat_id",
    "checkpoint_id",
    "evaluator_version",
}
_EPISODE_OPTIONAL = {"system_id", "base_instance_id"}
_MILESTONE_LABELS = {
    "assigned",
    "relay_started",
    "relay_complete",
    "heavy_carry_complete",
    "accepted",
}
_TASK_EVENT_NAMES = {
    "transition_handoff_observed",
    "bilateral_support_established",
    "joint_holder_added",
    "joint_holder_released",
    "joint_hold_safety_failure",
    "task_horizon_reached",
}
_SAFETY_CODES = {
    "joint_hold_duplicate_or_invalid_holder",
    "joint_hold_below_minimum_at_lift",
    "joint_hold_below_minimum_during_carry",
    "joint_hold_below_minimum_at_completion",
}


class Room06TerminalMaterialError(ValueError):
    """Raised when terminal inputs cannot support evaluator material."""


def _canonical_bytes(value: Any, name: str) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise Room06TerminalMaterialError(
            f"{name} must be finite canonical JSON"
        ) from exc


def _canonical_digest(value: Any, name: str) -> str:
    return hashlib.sha256(_canonical_bytes(value, name)).hexdigest()


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise Room06TerminalMaterialError(f"{name} must be an object")
    return value


def _exact(value: Mapping[str, Any], keys: set[str], name: str) -> None:
    if set(value) != keys:
        raise Room06TerminalMaterialError(f"{name} schema mismatch")


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Room06TerminalMaterialError(f"{name} must be non-empty text")
    return value


def _digest(value: Any, name: str) -> str:
    value = _text(value, name)
    if len(value) != 64 or any(character not in _HEX for character in value):
        raise Room06TerminalMaterialError(f"{name} must be a lowercase SHA-256")
    return value


def _tick(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise Room06TerminalMaterialError(
            f"{name} must be a non-negative integer tick"
        )
    return value


def _finite(
    value: Any,
    name: str,
    *,
    minimum: float = 0.0,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Room06TerminalMaterialError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise Room06TerminalMaterialError(f"{name} is outside its valid range")
    if maximum is not None and result > maximum:
        raise Room06TerminalMaterialError(f"{name} is outside its valid range")
    return result


def _normalize_episode(value: Any) -> dict[str, Any]:
    episode = _mapping(value, "episode")
    allowed = _EPISODE_REQUIRED | _EPISODE_OPTIONAL
    if not _EPISODE_REQUIRED.issubset(episode) or set(episode) - allowed:
        raise Room06TerminalMaterialError("episode schema mismatch")
    if episode.get("status") != "valid_terminal":
        raise Room06TerminalMaterialError(
            "room_06 task terminal evidence requires status=valid_terminal"
        )
    if episode.get("evaluator_version") != EVALUATOR_VERSION:
        raise Room06TerminalMaterialError("episode evaluator_version mismatch")
    normalized = {
        "status": "valid_terminal",
        "condition_id": _text(episode.get("condition_id"), "episode.condition_id"),
        "branch": _text(episode.get("branch"), "episode.branch"),
        "repeat_id": _text(episode.get("repeat_id"), "episode.repeat_id"),
        "checkpoint_id": _text(
            episode.get("checkpoint_id"), "episode.checkpoint_id"
        ),
        "evaluator_version": EVALUATOR_VERSION,
    }
    for key in sorted(_EPISODE_OPTIONAL):
        if key in episode:
            normalized[key] = _text(episode[key], f"episode.{key}")
    return normalized


def _normalize_d1(value: Any) -> dict[str, Any]:
    d1 = _mapping(value, "d1")
    _exact(d1, {"validity", "organization", "infeasible", "complete_plan"}, "d1")
    if type(d1.get("complete_plan")) is not bool:
        raise Room06TerminalMaterialError("d1.complete_plan must be boolean")
    return {
        "validity": _finite(d1.get("validity"), "d1.validity", maximum=1.0),
        "organization": _finite(
            d1.get("organization"), "d1.organization", maximum=1.0
        ),
        "infeasible": _finite(
            d1.get("infeasible"), "d1.infeasible", maximum=1.0
        ),
        "complete_plan": d1["complete_plan"],
    }


def _normalize_d6(value: Any) -> dict[str, Any]:
    d6 = _mapping(value, "d6_measurement")
    _exact(
        d6,
        {"transmitted_bits", "sim_time_s", "bits_unit", "time_unit"},
        "d6_measurement",
    )
    bits = d6.get("transmitted_bits")
    if type(bits) is not int or bits < 0:
        raise Room06TerminalMaterialError(
            "d6_measurement.transmitted_bits must be a non-negative integer"
        )
    if d6.get("bits_unit") != D6_BITS_UNIT:
        raise Room06TerminalMaterialError(
            f"d6_measurement.bits_unit must be {D6_BITS_UNIT}"
        )
    if d6.get("time_unit") != D6_TIME_UNIT:
        raise Room06TerminalMaterialError(
            f"d6_measurement.time_unit must be {D6_TIME_UNIT}"
        )
    return {
        "bits": bits,
        "time": _finite(d6.get("sim_time_s"), "d6_measurement.sim_time_s"),
    }


def _normalize_obligation_binding(value: Any) -> dict[str, Any]:
    binding = _mapping(value, "obligation_binding")
    _exact(binding, {"schema", "anchor_id", "obligations"}, "obligation_binding")
    if binding.get("schema") != "heterocoord.oracle-obligation-binding.v1":
        raise Room06TerminalMaterialError("unsupported obligation_binding schema")
    if binding.get("anchor_id") != TASK_ID:
        raise Room06TerminalMaterialError("obligation_binding must target liv-t-04")
    rows = binding.get("obligations")
    if not isinstance(rows, list) or len(rows) != 1:
        raise Room06TerminalMaterialError(
            "liv-t-04 obligation_binding must contain exactly one P2 obligation"
        )
    row = _mapping(rows[0], "obligation_binding.obligations[0]")
    _exact(
        row,
        {"obligation_id", "relation", "predicate_ids"},
        "obligation_binding.obligations[0]",
    )
    if row.get("relation") != "P2" or row.get("predicate_ids") != [
        "upper_shelf_delivery_after_transition_handoff"
    ]:
        raise Room06TerminalMaterialError(
            "liv-t-04 frozen P2 predicate binding mismatch"
        )
    return {
        "schema": "heterocoord.oracle-obligation-binding.v1",
        "anchor_id": TASK_ID,
        "obligations": [
            {
                "obligation_id": _text(
                    row.get("obligation_id"),
                    "obligation_binding.obligations[0].obligation_id",
                ),
                "relation": "P2",
                "predicate_ids": [
                    "upper_shelf_delivery_after_transition_handoff"
                ],
            }
        ],
    }


def _normalize_runtime_binding(
    value: Any, *, expected_digest: str, expected_node: str
) -> dict[str, str]:
    binding = _mapping(value, "runtime_evidence.runtime_binding")
    _exact(
        binding,
        {
            "schema",
            "anchor_id",
            "scene_id",
            "transition_handoff_node_id",
            "runtime_binding_digest",
        },
        "runtime_evidence.runtime_binding",
    )
    if (
        binding.get("schema") != RUNTIME_BINDING_SCHEMA
        or binding.get("anchor_id") != TASK_ID
        or binding.get("scene_id") != "room_06"
    ):
        raise Room06TerminalMaterialError("runtime_binding identity mismatch")
    node = _text(
        binding.get("transition_handoff_node_id"),
        "runtime_binding.transition_handoff_node_id",
    )
    if node == "topology_transition_node" or node != expected_node:
        raise Room06TerminalMaterialError("runtime_binding transition node mismatch")
    digest = _digest(
        binding.get("runtime_binding_digest"),
        "runtime_binding.runtime_binding_digest",
    )
    if digest != expected_digest:
        raise Room06TerminalMaterialError("runtime_binding digest mismatch")
    return {
        "schema": RUNTIME_BINDING_SCHEMA,
        "anchor_id": TASK_ID,
        "scene_id": "room_06",
        "transition_handoff_node_id": node,
        "runtime_binding_digest": digest,
    }


def _normalize_task_setup(value: Any) -> dict[str, Any]:
    setup = _mapping(value, "runtime_evidence.task_setup")
    _exact(
        setup,
        {
            "schema",
            "task_id",
            "scene_digest",
            "objects",
            "task_setup_digest",
            "source_task_setup_digest",
            "definition_digests",
        },
        "runtime_evidence.task_setup",
    )
    if setup.get("schema") != TASK_SETUP_SCHEMA or setup.get("task_id") != TASK_ID:
        raise Room06TerminalMaterialError("task_setup identity mismatch")
    scene_digest = _digest(setup.get("scene_digest"), "task_setup.scene_digest")
    rows = setup.get("objects")
    if not isinstance(rows, list) or len(rows) != 2:
        raise Room06TerminalMaterialError("task_setup must contain exactly two objects")
    normalized_rows: list[dict[str, Any]] = []
    found: set[str] = set()
    expected_digests: dict[str, str] = {}
    for index, raw_row in enumerate(rows):
        row = _mapping(raw_row, f"task_setup.objects[{index}]")
        _exact(
            row,
            {"entity_id", "definition", "definition_digest"},
            f"task_setup.objects[{index}]",
        )
        entity_id = _text(row.get("entity_id"), f"task_setup.objects[{index}].entity_id")
        if entity_id in found:
            raise Room06TerminalMaterialError("task_setup contains duplicate entity IDs")
        found.add(entity_id)
        try:
            definition = normalize_task_definition(
                entity_id,
                _mapping(row.get("definition"), f"task_setup.objects[{index}].definition"),
            )
        except ValueError as exc:
            raise Room06TerminalMaterialError(str(exc)) from exc
        definition_digest = _canonical_digest(
            definition, f"task_setup.objects[{index}].definition"
        )
        if _digest(
            row.get("definition_digest"),
            f"task_setup.objects[{index}].definition_digest",
        ) != definition_digest:
            raise Room06TerminalMaterialError("task object definition digest mismatch")
        expected_digests[entity_id] = definition_digest
        normalized_rows.append(
            {
                "entity_id": entity_id,
                "definition": definition,
                "definition_digest": definition_digest,
            }
        )
    if found != {BOOK_BATCH_ID, HEAVY_OBJECT_ID}:
        raise Room06TerminalMaterialError("task_setup entity roster mismatch")
    expected_order = [BOOK_BATCH_ID, HEAVY_OBJECT_ID]
    if [row["entity_id"] for row in normalized_rows] != expected_order:
        raise Room06TerminalMaterialError("task_setup object order is not canonical")
    declared_digests = _mapping(
        setup.get("definition_digests"), "task_setup.definition_digests"
    )
    if dict(declared_digests) != expected_digests:
        raise Room06TerminalMaterialError("task_setup definition_digests mismatch")
    digest_preimage = {
        "schema": TASK_SETUP_SCHEMA,
        "task_id": TASK_ID,
        "scene_digest": scene_digest,
        "objects": normalized_rows,
    }
    expected_setup_digest = _canonical_digest(digest_preimage, "task_setup")
    if _digest(setup.get("task_setup_digest"), "task_setup.task_setup_digest") != expected_setup_digest:
        raise Room06TerminalMaterialError("task_setup digest mismatch")
    source_digest = _digest(
        setup.get("source_task_setup_digest"), "task_setup.source_task_setup_digest"
    )
    return {
        **digest_preimage,
        "task_setup_digest": expected_setup_digest,
        "source_task_setup_digest": source_digest,
        "definition_digests": dict(expected_digests),
    }


def _normalize_streams(
    value: Any,
    *,
    binding_digest: str,
    transition_node: str,
    task_setup: Mapping[str, Any],
) -> dict[str, Any]:
    streams = _mapping(value, "runtime_evidence.streams")
    _exact(streams, _STREAM_KEYS, "runtime_evidence.streams")
    if streams.get("zone_intervals") != []:
        raise Room06TerminalMaterialError("liv-t-04 zone_intervals must be empty")
    if streams.get("integrity_confirmed") is not True:
        raise Room06TerminalMaterialError("runtime streams integrity is not confirmed")

    holds = streams.get("hold_intervals")
    if not isinstance(holds, list):
        raise Room06TerminalMaterialError("streams.hold_intervals must be a list")
    normalized_holds: list[dict[str, Any]] = []
    for index, raw_row in enumerate(holds):
        row = _mapping(raw_row, f"streams.hold_intervals[{index}]")
        _exact(
            row,
            {"object_id", "robot_id", "start_tick", "end_tick"},
            f"streams.hold_intervals[{index}]",
        )
        if row.get("object_id") != HEAVY_OBJECT_ID:
            raise Room06TerminalMaterialError("hold interval object identity mismatch")
        start = _tick(row.get("start_tick"), f"hold_intervals[{index}].start_tick")
        end = _tick(row.get("end_tick"), f"hold_intervals[{index}].end_tick")
        if end <= start:
            raise Room06TerminalMaterialError("hold interval must have positive duration")
        normalized_holds.append(
            {
                "object_id": HEAVY_OBJECT_ID,
                "robot_id": _text(row.get("robot_id"), f"hold_intervals[{index}].robot_id"),
                "start_tick": start,
                "end_tick": end,
            }
        )
    if normalized_holds != sorted(
        normalized_holds,
        key=lambda row: (row["start_tick"], row["end_tick"], row["robot_id"]),
    ):
        raise Room06TerminalMaterialError("hold_intervals are not canonically ordered")

    milestones = streams.get("milestones")
    if not isinstance(milestones, list):
        raise Room06TerminalMaterialError("streams.milestones must be a list")
    normalized_milestones: list[dict[str, Any]] = []
    labels: set[str] = set()
    for index, raw_row in enumerate(milestones):
        row = _mapping(raw_row, f"streams.milestones[{index}]")
        _exact(row, {"label", "tick"}, f"streams.milestones[{index}]")
        label = _text(row.get("label"), f"milestones[{index}].label")
        if label not in _MILESTONE_LABELS or label in labels:
            raise Room06TerminalMaterialError("unsupported or duplicate milestone")
        labels.add(label)
        normalized_milestones.append(
            {"label": label, "tick": _tick(row.get("tick"), f"milestones[{index}].tick")}
        )
    if normalized_milestones != sorted(
        normalized_milestones, key=lambda row: (row["tick"], row["label"])
    ):
        raise Room06TerminalMaterialError("milestones are not canonically ordered")

    events = streams.get("task_events")
    if not isinstance(events, list):
        raise Room06TerminalMaterialError("streams.task_events must be a list")
    normalized_events: list[dict[str, Any]] = []
    for index, raw_row in enumerate(events):
        row = _mapping(raw_row, f"streams.task_events[{index}]")
        _exact(row, {"name", "subject", "tick"}, f"streams.task_events[{index}]")
        name = _text(row.get("name"), f"task_events[{index}].name")
        subject = _text(row.get("subject"), f"task_events[{index}].subject")
        if name not in _TASK_EVENT_NAMES:
            raise Room06TerminalMaterialError("unsupported task event")
        if name == "transition_handoff_observed" and subject != transition_node:
            raise Room06TerminalMaterialError("transition event node mismatch")
        if name in {"bilateral_support_established", "joint_hold_safety_failure"} and subject != HEAVY_OBJECT_ID:
            raise Room06TerminalMaterialError("joint hold event object mismatch")
        if name == "task_horizon_reached" and subject != TASK_ID:
            raise Room06TerminalMaterialError("horizon event task identity mismatch")
        normalized_events.append(
            {"name": name, "subject": subject, "tick": _tick(row.get("tick"), f"task_events[{index}].tick")}
        )
    if normalized_events != sorted(
        normalized_events,
        key=lambda row: (row["tick"], row["name"], row["subject"]),
    ):
        raise Room06TerminalMaterialError("task_events are not canonically ordered")

    identities = _mapping(streams.get("identity_records"), "streams.identity_records")
    _exact(
        identities,
        {
            "task_setup_digest",
            "definition_digests",
            "transition_handoff_node_id",
            "runtime_binding_digest",
        },
        "streams.identity_records",
    )
    if identities.get("task_setup_digest") != task_setup["task_setup_digest"]:
        raise Room06TerminalMaterialError("identity task_setup_digest mismatch")
    if identities.get("definition_digests") != task_setup["definition_digests"]:
        raise Room06TerminalMaterialError("identity definition_digests mismatch")
    if identities.get("transition_handoff_node_id") != transition_node:
        raise Room06TerminalMaterialError("identity transition node mismatch")
    if identities.get("runtime_binding_digest") != binding_digest:
        raise Room06TerminalMaterialError("identity runtime binding digest mismatch")

    return {
        "zone_intervals": [],
        "hold_intervals": normalized_holds,
        "milestones": normalized_milestones,
        "task_events": normalized_events,
        "identity_records": {
            "task_setup_digest": task_setup["task_setup_digest"],
            "definition_digests": deepcopy(task_setup["definition_digests"]),
            "transition_handoff_node_id": transition_node,
            "runtime_binding_digest": binding_digest,
        },
        "integrity_confirmed": True,
    }


def _normalize_safety_failures(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise Room06TerminalMaterialError("runtime_evidence.safety_failures must be a list")
    normalized: list[dict[str, Any]] = []
    for index, raw_row in enumerate(value):
        row = _mapping(raw_row, f"safety_failures[{index}]")
        _exact(
            row,
            {
                "code",
                "tick",
                "object_id",
                "required_holders",
                "observed_holders",
                "holder_ids",
            },
            f"safety_failures[{index}]",
        )
        code = _text(row.get("code"), f"safety_failures[{index}].code")
        if code not in _SAFETY_CODES:
            raise Room06TerminalMaterialError("unsupported joint-hold safety code")
        if row.get("object_id") != HEAVY_OBJECT_ID:
            raise Room06TerminalMaterialError("safety failure object mismatch")
        if row.get("required_holders") != MINIMUM_HOLDERS:
            raise Room06TerminalMaterialError("safety failure holder threshold mismatch")
        holder_ids = row.get("holder_ids")
        if (
            not isinstance(holder_ids, list)
            or any(not isinstance(item, str) or not item.strip() for item in holder_ids)
            or len(set(holder_ids)) != len(holder_ids)
            or holder_ids != sorted(holder_ids)
        ):
            raise Room06TerminalMaterialError("safety failure holder_ids are invalid")
        observed = row.get("observed_holders")
        if type(observed) is not int or observed != len(holder_ids):
            raise Room06TerminalMaterialError("safety failure observed_holders mismatch")
        normalized.append(
            {
                "code": code,
                "tick": _tick(row.get("tick"), f"safety_failures[{index}].tick"),
                "object_id": HEAVY_OBJECT_ID,
                "required_holders": MINIMUM_HOLDERS,
                "observed_holders": observed,
                "holder_ids": list(holder_ids),
            }
        )
    return normalized


def _single_tick(rows: list[dict[str, Any]], field: str, value: str, name: str) -> int:
    ticks = [row["tick"] for row in rows if row[field] == value]
    if len(ticks) != 1:
        raise Room06TerminalMaterialError(f"terminal evidence requires exactly one {name}")
    return ticks[0]


def _derive_base_task_success(
    state: Any,
    streams: Mapping[str, Any],
    failures: list[dict[str, Any]],
) -> bool:
    if state not in {"complete", "failed", "incomplete"}:
        raise Room06TerminalMaterialError("runtime_state is not terminal")
    milestones = streams["milestones"]
    events = streams["task_events"]
    labels = {row["label"] for row in milestones}
    event_names = [row["name"] for row in events]
    accepted_ticks = [row["tick"] for row in milestones if row["label"] == "accepted"]

    if state == "complete":
        if failures:
            raise Room06TerminalMaterialError("complete runtime cannot contain safety failures")
        required = {
            "assigned",
            "relay_started",
            "relay_complete",
            "heavy_carry_complete",
            "accepted",
        }
        if labels != required:
            raise Room06TerminalMaterialError("complete runtime milestone chain mismatch")
        assigned = _single_tick(milestones, "label", "assigned", "assigned milestone")
        relay_start = _single_tick(milestones, "label", "relay_started", "relay_started milestone")
        transition = _single_tick(events, "name", "transition_handoff_observed", "transition event")
        relay_complete = _single_tick(milestones, "label", "relay_complete", "relay_complete milestone")
        support = _single_tick(events, "name", "bilateral_support_established", "bilateral support event")
        carry_complete = _single_tick(milestones, "label", "heavy_carry_complete", "heavy_carry_complete milestone")
        accepted = _single_tick(milestones, "label", "accepted", "accepted milestone")
        if not (
            assigned <= relay_start < transition < relay_complete < support
            and support <= carry_complete <= accepted <= HORIZON_TICKS
        ):
            raise Room06TerminalMaterialError("complete runtime terminal order mismatch")
        forbidden = {"joint_hold_safety_failure", "task_horizon_reached"}
        if forbidden.intersection(event_names):
            raise Room06TerminalMaterialError("complete runtime contains a failure terminal event")
        return True

    if accepted_ticks:
        raise Room06TerminalMaterialError(f"{state} runtime cannot contain accepted")
    if state == "failed":
        if len(failures) != 1:
            raise Room06TerminalMaterialError("failed runtime requires exactly one safety failure")
        failure_events = [row for row in events if row["name"] == "joint_hold_safety_failure"]
        if len(failure_events) != 1:
            raise Room06TerminalMaterialError("failed runtime requires its safety task event")
        if (
            failure_events[0]["tick"] != failures[0]["tick"]
            or failure_events[0]["subject"] != failures[0]["object_id"]
        ):
            raise Room06TerminalMaterialError("safety failure and task event disagree")
        if "task_horizon_reached" in event_names:
            raise Room06TerminalMaterialError("failed runtime cannot also be horizon-incomplete")
        return False

    if failures or "joint_hold_safety_failure" in event_names:
        raise Room06TerminalMaterialError("incomplete runtime cannot contain safety failure")
    horizon = _single_tick(events, "name", "task_horizon_reached", "horizon event")
    if horizon != HORIZON_TICKS:
        raise Room06TerminalMaterialError("incomplete runtime horizon event must be at tick 15000")
    return False


def _normalize_runtime_evidence(value: Any) -> tuple[dict[str, Any], bool]:
    evidence = _mapping(value, "runtime_evidence")
    _exact(evidence, _EVIDENCE_KEYS, "runtime_evidence")
    _canonical_bytes(evidence, "runtime_evidence")
    if evidence.get("schema") != RUNTIME_EVIDENCE_SCHEMA:
        raise Room06TerminalMaterialError("unsupported runtime evidence schema")
    if evidence.get("task_id") != TASK_ID:
        raise Room06TerminalMaterialError("runtime evidence must target liv-t-04")
    if evidence.get("room_specific_feasibility") is not False:
        raise Room06TerminalMaterialError("runtime evidence cannot claim room feasibility")
    if evidence.get("formal_gates") != FORMAL_GATES:
        raise Room06TerminalMaterialError("runtime evidence formal gates must remain false")
    binding_digest = _digest(
        evidence.get("runtime_binding_digest"), "runtime_evidence.runtime_binding_digest"
    )
    transition_node = _text(
        evidence.get("transition_handoff_node_id"),
        "runtime_evidence.transition_handoff_node_id",
    )
    if transition_node == "topology_transition_node":
        raise Room06TerminalMaterialError("runtime evidence uses a transition placeholder")
    binding = _normalize_runtime_binding(
        evidence.get("runtime_binding"),
        expected_digest=binding_digest,
        expected_node=transition_node,
    )
    task_setup = _normalize_task_setup(evidence.get("task_setup"))
    streams = _normalize_streams(
        evidence.get("streams"),
        binding_digest=binding_digest,
        transition_node=transition_node,
        task_setup=task_setup,
    )
    failures = _normalize_safety_failures(evidence.get("safety_failures"))
    state = evidence.get("runtime_state")
    base_task_success = _derive_base_task_success(state, streams, failures)
    normalized = {
        "schema": RUNTIME_EVIDENCE_SCHEMA,
        "task_id": TASK_ID,
        "runtime_binding_digest": binding_digest,
        "transition_handoff_node_id": transition_node,
        "runtime_binding": binding,
        "task_setup": task_setup,
        "streams": streams,
        "safety_failures": failures,
        "runtime_state": state,
        "room_specific_feasibility": False,
        "formal_gates": dict(FORMAL_GATES),
    }
    return normalized, base_task_success


def _normalize_d4_observation(value: Any) -> dict[str, Any]:
    try:
        return validate_observation(
            _mapping(value, "d4_first_event_observation")
        )
    except (D4FirstEventError, Room06TerminalMaterialError) as exc:
        raise Room06TerminalMaterialError(str(exc)) from exc


def assemble_room06_terminal_payload(
    *,
    runtime_evidence: Mapping[str, Any],
    episode: Mapping[str, Any],
    d1: Mapping[str, Any],
    d4_first_event_observation: Mapping[str, Any],
    d6_measurement: Mapping[str, Any],
    obligation_binding: Mapping[str, Any],
) -> dict[str, Any]:
    """Return evaluator-v2 payload from explicit, non-overridable inputs.

    The returned mapping itself can be passed to
    ``Room06LivT04Evaluator.collect_receipt_material``.  The registered D4
    first-event observation is retained as an evaluator-owned base fact; it is
    distinct from the D4 primary-score ``d4_items`` list and is therefore
    never copied into that list.
    """

    normalized_evidence, base_task_success = _normalize_runtime_evidence(
        runtime_evidence
    )
    normalized_episode = _normalize_episode(episode)
    normalized_d1 = _normalize_d1(d1)
    normalized_d4 = _normalize_d4_observation(d4_first_event_observation)
    normalized_d6 = _normalize_d6(d6_measurement)
    normalized_binding = _normalize_obligation_binding(obligation_binding)
    evidence_digest = _canonical_digest(normalized_evidence, "runtime_evidence")
    return {
        "episode": normalized_episode,
        "streams": deepcopy(normalized_evidence["streams"]),
        "base_facts": {
            "d1": normalized_d1,
            "base_task_success": base_task_success,
            "d4_items": [],
            "d6": normalized_d6,
            D4_FIRST_EVENT_FACT_KEY: normalized_d4,
        },
        "obligation_binding": normalized_binding,
        "runtime_binding": deepcopy(normalized_evidence["runtime_binding"]),
        "runtime_evidence_digest": evidence_digest,
        "runtime_state": normalized_evidence["runtime_state"],
        "assembler_schema": ASSEMBLER_SCHEMA,
        "formal_gates": dict(FORMAL_GATES),
    }


__all__ = [
    "ASSEMBLER_SCHEMA",
    "D6_BITS_UNIT",
    "D6_TIME_UNIT",
    "Room06TerminalMaterialError",
    "assemble_room06_terminal_payload",
]
