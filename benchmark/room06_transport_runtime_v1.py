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
from copy import deepcopy
from typing import Any

from task.executors.transport_runtime import TransportOrchestrationExecutor
from .room06_task_instance_v2 import logical_task_objects


ROOM06_TRANSPORT_BINDING_SCHEMA = "heterocoord.room06-transport-runtime-binding.v1"
FIXED_PACKAGE_SCHEMA = "heterocoord.room06-fixed-package.v1"
TASK_SETUP_SCHEMA = "heterocoord.room06-task-setup.v1"
TASK_ID = "liv-t-04"
ORCHESTRATION_LEDGER_REF_SCHEMA = "heterocoord.orchestration-ledger-ref.v1"


class Room06TransportRuntimeError(ValueError):
    """Raised when a room-06 package cannot form an authoritative binding."""


def _ledger_ref(value: Any) -> dict[str, Any]:
    """Validate an explicit orchestration ledger and return its provenance ref.

    The ledger is runtime provenance only.  Its events are never interpreted as
    evaluator facts; the digest merely lets a receipt identify the scheduler
    record that accompanied the observed episode.
    """

    candidate = value
    if isinstance(candidate, Mapping) and "ledger" in candidate:
        candidate = candidate["ledger"]
    elif not isinstance(candidate, Mapping) and hasattr(candidate, "ledger"):
        candidate = getattr(candidate, "ledger")
    if isinstance(candidate, Mapping):
        schema_version = candidate.get("schema_version")
        events = candidate.get("events")
        declared_digest = candidate.get("digest")
    else:
        schema_version = getattr(candidate, "schema_version", None)
        events = getattr(candidate, "events", None)
        digest_method = getattr(candidate, "digest", None)
        declared_digest = digest_method() if callable(digest_method) else None
    if not isinstance(schema_version, str) or not schema_version.strip():
        raise Room06TransportRuntimeError(
            "orchestration ledger schema_version must be non-empty text"
        )
    if isinstance(events, tuple):
        events = list(events)
    if not isinstance(events, list) or any(not isinstance(item, Mapping) for item in events):
        raise Room06TransportRuntimeError(
            "orchestration ledger events must be a list of objects"
        )
    if not isinstance(declared_digest, str) or len(declared_digest) != 64 or any(
        char not in "0123456789abcdef" for char in declared_digest
    ):
        raise Room06TransportRuntimeError(
            "orchestration ledger digest must be a lowercase SHA-256"
        )
    detached_events = [dict(item) for item in events]
    try:
        preimage = json.dumps(
            {"schema_version": schema_version, "events": detached_events},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise Room06TransportRuntimeError(
            "orchestration ledger is not finite canonical JSON"
        ) from exc
    actual_digest = hashlib.sha256(preimage).hexdigest()
    if actual_digest != declared_digest:
        raise Room06TransportRuntimeError(
            "orchestration ledger digest does not bind its events"
        )
    return {
        "schema": ORCHESTRATION_LEDGER_REF_SCHEMA,
        "schema_version": schema_version,
        "digest": declared_digest,
        "event_count": len(detached_events),
    }


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
    if any(isinstance(row, Mapping) and isinstance(row.get("definition"), Mapping) and "books" in row["definition"] for row in rows):
        try:
            rows = logical_task_objects(setup)
        except ValueError as exc:
            raise Room06TransportRuntimeError(str(exc)) from exc
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


def bind_orchestration_ledger_provenance(
    runtime_payload: Mapping[str, Any],
    orchestration_result: Any,
) -> dict[str, Any]:
    """Attach a digest-only scheduler reference to room06 receipt streams.

    ``runtime_payload`` is the evaluator-facing payload produced by
    ``assemble_room06_terminal_payload`` (or the equivalent nested
    ``room06_livt04`` object).  Only ``streams.identity_records`` is extended;
    milestones, holds, success inputs and formal gates are untouched.  A
    mismatched existing reference or a tampered ledger fails closed.
    """

    if not isinstance(runtime_payload, Mapping):
        raise Room06TransportRuntimeError("runtime_payload must be an object")
    try:
        detached = json.loads(
            json.dumps(
                runtime_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
        )
    except (TypeError, ValueError) as exc:
        raise Room06TransportRuntimeError(
            "runtime_payload must be finite canonical JSON"
        ) from exc
    candidate = detached.get("room06_livt04", detached)
    if not isinstance(candidate, dict):
        raise Room06TransportRuntimeError("runtime_payload.room06_livt04 must be an object")
    streams = candidate.get("streams")
    if not isinstance(streams, dict):
        raise Room06TransportRuntimeError("runtime_payload streams must be an object")
    identity_records = streams.get("identity_records")
    if not isinstance(identity_records, dict):
        raise Room06TransportRuntimeError(
            "runtime_payload streams.identity_records must be an object"
        )
    reference = _ledger_ref(orchestration_result)
    existing = identity_records.get("orchestration_ledger")
    if existing is not None and existing != reference:
        raise Room06TransportRuntimeError(
            "runtime_payload orchestration ledger reference disagrees with result"
        )
    identity_records["orchestration_ledger"] = reference
    return detached


__all__ = [
    "ORCHESTRATION_LEDGER_REF_SCHEMA",
    "ROOM06_TRANSPORT_BINDING_SCHEMA",
    "Room06TransportBinding",
    "Room06TransportRuntimeError",
    "bind_orchestration_ledger_provenance",
    "bind_room06_transport_runtime",
]
