"""Bind a verified room-06 fixed package to the runtime transport resolver.

The room-06 package already freezes task-generated object definitions and the
selected promoted roster instances.  This adapter turns those bytes into the
authoritative documents consumed by :class:`TransportAdmissionResolver`:
object mass comes only from ``task_setup`` and robot payload comes only from
the selected roster rows' source resumes.  It does not read mass/payload from
an algorithm command, USD metadata, lock responses or process state.

The adapter is CPU-only.  A host can construct it before Isaac starts and then
pass ``binding.executor`` to the task orchestration seam.  Its executor ledger
remains runtime provenance; evaluator-owned terminal receipt material is still
assembled by the room-06 evaluator.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any

from task.executors.transport_runtime import TransportOrchestrationExecutor


ROOM06_TRANSPORT_BINDING_SCHEMA = "heterocoord.room06-transport-runtime-binding.v1"
FIXED_PACKAGE_SCHEMA = "heterocoord.room06-fixed-package.v1"
TASK_SETUP_SCHEMA = "heterocoord.room06-task-setup.v1"
TASK_ID = "liv-t-04"


class Room06TransportRuntimeError(ValueError):
    """Raised when a room-06 package cannot form an authoritative binding."""


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise Room06TransportRuntimeError(f"{name} must be an object")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise Room06TransportRuntimeError(f"{name} must be non-empty text")
    return value.strip()


def _digest(value: Any, name: str) -> str:
    value = _text(value, name)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise Room06TransportRuntimeError(f"{name} must be a lowercase SHA-256")
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
        raise Room06TransportRuntimeError("package is not finite canonical JSON") from exc
    return hashlib.sha256(payload).hexdigest()


def _selected_ids(package: Mapping[str, Any]) -> tuple[str, ...]:
    plan = _mapping(package.get("plan"), "package.plan")
    value = plan.get("selected_robot_ids")
    if (
        isinstance(value, (str, bytes))
        or not isinstance(value, Sequence)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise Room06TransportRuntimeError(
            "package.plan.selected_robot_ids must be a non-empty sequence"
        )
    result = tuple(item.strip() for item in value)
    if len(set(result)) != len(result):
        raise Room06TransportRuntimeError(
            "package.plan.selected_robot_ids must be unique"
        )
    return result


def _task_objects(package: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    setup = _mapping(package.get("task_setup"), "package.task_setup")
    if setup.get("schema") != TASK_SETUP_SCHEMA or setup.get("task_id") != TASK_ID:
        raise Room06TransportRuntimeError("package.task_setup must identify liv-t-04")
    rows = setup.get("objects")
    if not isinstance(rows, list) or not rows:
        raise Room06TransportRuntimeError("package.task_setup.objects must be non-empty")
    objects: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        item = _mapping(row, f"package.task_setup.objects[{index}]")
        entity_id = _text(item.get("entity_id"), f"task object {index}.entity_id")
        if entity_id in seen:
            raise Room06TransportRuntimeError(
                f"package.task_setup contains duplicate object: {entity_id}"
            )
        seen.add(entity_id)
        definition = _mapping(item.get("definition"), f"{entity_id}.definition")
        # Preserve the complete task definition for provenance, but expose a
        # mass field only when the task explicitly authored one.  In
        # particular, no book-batch mass is guessed from count or size.
        record: dict[str, Any] = {"id": entity_id, "definition": dict(definition)}
        if "mass_kg" in definition:
            mass = definition["mass_kg"]
            if isinstance(mass, bool) or not isinstance(mass, (int, float)):
                raise Room06TransportRuntimeError(
                    f"{entity_id}.definition.mass_kg must be numeric"
                )
            mass = float(mass)
            if not math.isfinite(mass) or mass <= 0:
                raise Room06TransportRuntimeError(
                    f"{entity_id}.definition.mass_kg must be positive and finite"
                )
            record["mass_kg"] = mass
        objects.append(record)
    return tuple(objects)


def _selected_roster_document(
    roster_document: Mapping[str, Any], selected: Sequence[str]
) -> dict[str, Any]:
    roster = _mapping(roster_document, "runtime_roster_document")
    rows = roster.get("robots")
    if not isinstance(rows, list) or not rows:
        raise Room06TransportRuntimeError(
            "runtime_roster_document.robots must be a non-empty list"
        )
    by_id: dict[str, Mapping[str, Any]] = {}
    for index, row in enumerate(rows):
        item = _mapping(row, f"runtime roster row {index}")
        instance_id = _text(item.get("instance_id"), f"runtime roster row {index}.instance_id")
        if instance_id in by_id:
            raise Room06TransportRuntimeError(
                f"runtime roster contains duplicate instance_id: {instance_id}"
            )
        by_id[instance_id] = item
    missing = sorted(set(selected) - set(by_id))
    if missing:
        raise Room06TransportRuntimeError(
            f"selected runtime instances are missing from roster: {missing}"
        )
    return {"robots": [dict(by_id[item]) for item in selected]}


@dataclass(frozen=True)
class Room06TransportBinding:
    """Runtime transport executor plus immutable case binding metadata."""

    executor: TransportOrchestrationExecutor
    package_digest: str
    selected_robot_ids: tuple[str, ...]
    object_ids: tuple[str, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": ROOM06_TRANSPORT_BINDING_SCHEMA,
            "package_digest": self.package_digest,
            "selected_robot_ids": list(self.selected_robot_ids),
            "object_ids": list(self.object_ids),
            "executor_ledger_schema": self.executor.ledger.schema_version,
            "formal_gates": {
                "physical_qualified": False,
                "rankable": False,
                "paper_score_eligible": False,
            },
        }


def bind_room06_transport_runtime(
    package: Mapping[str, Any],
    *,
    runtime_roster_document: Mapping[str, Any],
    resume_document: Mapping[str, Any],
    execute_command: Any | None = None,
    navigate: Any | None = None,
    ledger: Any | None = None,
    clock: Any | None = None,
) -> Room06TransportBinding:
    """Build the case-scoped authoritative transport resolver.

    ``package`` must already have passed ``verify_room06_fixed_package``.  Its
    digest is checked again here so a caller cannot swap task definitions after
    package verification.  The full promoted roster may be supplied, but only
    the package-selected instance IDs are admitted into this binding.
    """

    package = _mapping(package, "package")
    if package.get("schema") != FIXED_PACKAGE_SCHEMA:
        raise Room06TransportRuntimeError("unsupported room06 fixed package schema")
    package_digest = _digest(package.get("package_digest"), "package.package_digest")
    body = dict(package)
    body.pop("package_digest", None)
    if _canonical_digest(body) != package_digest:
        raise Room06TransportRuntimeError("package.package_digest does not bind package bytes")

    selected = _selected_ids(package)
    objects = _task_objects(package)
    selected_roster = _selected_roster_document(runtime_roster_document, selected)
    executor = TransportOrchestrationExecutor.from_runtime_roster_documents(
        {"objects": list(objects)},
        selected_roster,
        resume_document,
        allowed_robot_ids=selected,
        execute_command=execute_command,
        navigate=navigate,
        ledger=ledger,
        clock=clock,
    )
    return Room06TransportBinding(
        executor=executor,
        package_digest=package_digest,
        selected_robot_ids=selected,
        object_ids=tuple(item["id"] for item in objects),
    )


__all__ = [
    "ROOM06_TRANSPORT_BINDING_SCHEMA",
    "Room06TransportBinding",
    "Room06TransportRuntimeError",
    "bind_room06_transport_runtime",
]
