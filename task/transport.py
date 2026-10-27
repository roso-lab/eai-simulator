"""Internal semantic transport contract and runtime load admission.

The tested system emits :class:`task.contracts.AlgorithmDecision`.  This
module defines the next, benchmark-owned boundary: an assignment is compiled
to a semantic transport command, then the transport skill checks the declared
object mass against the selected robot's payload before it creates any
attachment or calls a low-level controller.

Business fields such as order numbers, product names, and quantities do not
belong to this contract.  Locations are semantic-map references.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from algorithm.common_coordination.contract import ExecutionFeedback, FailureCode


TRANSPORT_COMMAND_SCHEMA = "heterocoord.transport_command.v1"


def _required_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True)
class TransportCommand:
    """Benchmark-owned semantic command produced after assignment."""

    request_id: str
    task_id: str
    robot_id: str
    object_ref: str
    source_ref: str
    target_ref: str
    operation: str = "pick"
    phase: str = "source_to_target"
    handoff_ref: str | None = None
    semantic_map_ref: str | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    schema: str = TRANSPORT_COMMAND_SCHEMA

    def __post_init__(self) -> None:
        for name in ("request_id", "task_id", "robot_id", "object_ref", "source_ref", "target_ref"):
            object.__setattr__(self, name, _required_string(getattr(self, name), name))
        if self.schema != TRANSPORT_COMMAND_SCHEMA:
            raise ValueError(f"unsupported transport schema: {self.schema!r}")
        if self.operation not in {"pick", "handoff", "place"}:
            raise ValueError("operation must be pick, handoff, or place")
        if self.phase not in {"source_to_target", "source_to_handoff", "handoff_to_target"}:
            raise ValueError("unsupported transport phase")
        if self.handoff_ref is not None:
            object.__setattr__(self, "handoff_ref", _required_string(self.handoff_ref, "handoff_ref"))
        if self.semantic_map_ref is not None:
            object.__setattr__(self, "semantic_map_ref", _required_string(self.semantic_map_ref, "semantic_map_ref"))
        object.__setattr__(self, "parameters", dict(self.parameters))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TransportCommand":
        if not isinstance(value, Mapping):
            raise ValueError("transport command must be an object")
        allowed = {
            "schema", "request_id", "task_id", "robot_id", "object_ref",
            "source_ref", "handoff_ref", "target_ref", "operation", "phase",
            "semantic_map_ref", "parameters",
        }
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise ValueError(f"transport command has unknown fields: {unknown}")
        required = {"request_id", "task_id", "robot_id", "object_ref", "source_ref", "target_ref"}
        missing = sorted(required - set(value))
        if missing:
            raise ValueError(f"transport command is missing fields: {missing}")
        return cls(**dict(value))

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "request_id": self.request_id,
            "task_id": self.task_id,
            "robot_id": self.robot_id,
            "object_ref": self.object_ref,
            "source_ref": self.source_ref,
            "handoff_ref": self.handoff_ref,
            "target_ref": self.target_ref,
            "operation": self.operation,
            "phase": self.phase,
            "semantic_map_ref": self.semantic_map_ref,
            "parameters": dict(self.parameters),
        }


def compile_transport_command(assignment: Any, task: Any, *, request_id: str) -> TransportCommand:
    """Compile one ``Assignment`` and task metadata into the internal command.

    The task metadata is the authored semantic description.  The assignment
    contributes only the selected robot; no business fields are inferred.
    """
    metadata = getattr(task, "metadata", {})
    transport = metadata.get("transport", {}) if isinstance(metadata, Mapping) else {}
    if not isinstance(transport, Mapping):
        raise ValueError("task.metadata.transport must be an object")
    task_id = _required_string(getattr(assignment, "task_id", getattr(task, "task_id", "")), "task_id")
    robot_id = _required_string(getattr(assignment, "robot_id", ""), "robot_id")
    object_ref = transport.get("object_ref")
    source_ref = transport.get("source_ref")
    target_ref = transport.get("target_ref", getattr(task, "target_ref", None))
    missing = [name for name, value in (("object_ref", object_ref), ("source_ref", source_ref), ("target_ref", target_ref))
               if not isinstance(value, str) or not value.strip()]
    if missing:
        raise ValueError(f"transport metadata is missing fields: {missing}")
    return TransportCommand(
        request_id=request_id,
        task_id=task_id,
        robot_id=robot_id,
        object_ref=object_ref,
        source_ref=source_ref,
        handoff_ref=transport.get("handoff_ref"),
        target_ref=target_ref,
        operation=transport.get("operation", "pick"),
        phase=transport.get("phase", "source_to_target"),
        semantic_map_ref=transport.get("semantic_map_ref"),
        parameters=transport.get("parameters", {}),
    )


@dataclass(frozen=True)
class TransportAdmissionResult:
    """Result of the mandatory runtime mass/payload comparison."""

    admitted: bool
    object_mass_kg: float | None
    robot_payload_kg: float | None
    reason: str

    @property
    def result_code(self) -> int:
        """The compact skill result required by the controller boundary."""
        return 1 if self.admitted else 0

    def to_mapping(self) -> dict[str, Any]:
        return {
            "admitted": self.admitted,
            "result": self.result_code,
            "object_mass_kg": self.object_mass_kg,
            "robot_payload_kg": self.robot_payload_kg,
            "reason": self.reason,
        }

    def to_execution_feedback(self, *, task_id: str, robot_id: str,
                              timestamp_s: float = 0.0) -> ExecutionFeedback:
        """Convert the gate result to the scheduler's standard feedback."""
        return ExecutionFeedback(
            task_id=task_id,
            robot_id=robot_id,
            outcome="completed" if self.admitted else "failed",
            failure_code=FailureCode.NONE if self.admitted else FailureCode.INFEASIBLE_TEAM,
            message=self.reason,
            timestamp_s=timestamp_s,
            world_changes={"transport_admission": self.to_mapping()},
        )


def _mass_from_record(record: Mapping[str, Any]) -> float | None:
    value = record.get("mass_kg")
    physics = record.get("physics")
    if value is None and isinstance(physics, Mapping):
        value = physics.get("mass_kg")
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _payload_from_record(record: Mapping[str, Any]) -> float | None:
    manipulation = record.get("manipulation")
    value = manipulation.get("payload_kg") if isinstance(manipulation, Mapping) else record.get("payload_kg")
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


class TransportAdmissionResolver:
    """Resolve authoritative JSON records and perform runtime admission.

    ``objects`` and ``robots`` are the scene/robot JSON records already loaded
    by the host.  The resolver deliberately does not accept mass or payload
    values from the tested system's command.
    """

    def __init__(self, objects: Mapping[str, Mapping[str, Any]], robots: Mapping[str, Mapping[str, Any]]):
        self.objects = objects
        self.robots = robots
        self.records: list[dict[str, Any]] = []

    @classmethod
    def from_json_documents(cls, scene_document: Mapping[str, Any],
                            robot_document: Mapping[str, Any]) -> "TransportAdmissionResolver":
        """Build indexes from the authored scene and robot-resume JSON shapes."""
        def index(document: Mapping[str, Any], collections: tuple[str, ...], keys: tuple[str, ...]):
            rows: list[Mapping[str, Any]] = []
            if isinstance(document, Mapping):
                for name in collections:
                    value = document.get(name)
                    if isinstance(value, list):
                        rows.extend(item for item in value if isinstance(item, Mapping))
                    elif isinstance(value, Mapping):
                        nested = value.get("entities") or value.get("objects")
                        if isinstance(nested, list):
                            rows.extend(item for item in nested if isinstance(item, Mapping))
                if not rows and all(key in document for key in keys):
                    rows.append(document)
            result: dict[str, Mapping[str, Any]] = {}
            for row in rows:
                for key in keys:
                    value = row.get(key)
                    if isinstance(value, str) and value.strip():
                        result[value.strip()] = row
                identity = row.get("identity")
                if isinstance(identity, Mapping):
                    value = identity.get("robot_id")
                    if isinstance(value, str) and value.strip():
                        result[value.strip()] = row
            return result

        return cls(
            index(scene_document, ("entities", "objects", "composition"),
                  ("id", "ref", "object_ref")),
            index(robot_document, ("resumes", "configuration_profiles", "robots"),
                  ("robot_id", "resume_id", "profile_id", "id")),
        )

    @classmethod
    def from_runtime_roster_documents(
        cls,
        scene_document: Mapping[str, Any],
        runtime_roster_document: Mapping[str, Any],
        resume_document: Mapping[str, Any],
    ) -> "TransportAdmissionResolver":
        """Bind promoted instance IDs to authoritative resume capabilities.

        The promoted roster intentionally stores identity/fingerprint fields,
        while payload limits remain in the source resume.  This method makes
        that provenance explicit and rejects missing or ambiguous source
        records instead of accepting a caller-supplied payload.
        """
        if not isinstance(runtime_roster_document, Mapping) or not isinstance(resume_document, Mapping):
            raise ValueError("runtime roster and resume documents must be objects")
        roster_rows = runtime_roster_document.get("robots")
        resume_rows = resume_document.get("resumes")
        if not isinstance(roster_rows, list) or not roster_rows:
            raise ValueError("runtime roster robots must be a non-empty list")
        if not isinstance(resume_rows, list) or not resume_rows:
            raise ValueError("resume document resumes must be a non-empty list")
        by_resume: dict[str, Mapping[str, Any]] = {}
        for row in resume_rows:
            if not isinstance(row, Mapping):
                raise ValueError("resume rows must be objects")
            resume_id = row.get("resume_id")
            if not isinstance(resume_id, str) or not resume_id.strip():
                raise ValueError("resume rows require resume_id")
            if resume_id in by_resume:
                raise ValueError(f"duplicate source resume_id: {resume_id}")
            by_resume[resume_id] = row

        bound_rows: list[dict[str, Any]] = []
        seen_instances: set[str] = set()
        for index, roster_row in enumerate(roster_rows):
            if not isinstance(roster_row, Mapping):
                raise ValueError(f"runtime roster row {index} must be an object")
            instance_id = roster_row.get("instance_id", roster_row.get("robot_id"))
            source_resume_id = roster_row.get("source_resume_id")
            if not isinstance(instance_id, str) or not instance_id.strip():
                raise ValueError(f"runtime roster row {index} requires instance_id")
            if instance_id in seen_instances:
                raise ValueError(f"duplicate runtime instance_id: {instance_id}")
            seen_instances.add(instance_id)
            if not isinstance(source_resume_id, str) or not source_resume_id.strip():
                raise ValueError(f"runtime roster row {index} requires source_resume_id")
            source = by_resume.get(source_resume_id)
            if source is None:
                raise ValueError(f"source resume is unavailable: {source_resume_id}")
            capability_binding = roster_row.get("capability_binding")
            if isinstance(capability_binding, Mapping):
                source_input = capability_binding.get("source_input")
                if isinstance(source_input, Mapping):
                    declared_source = source_input.get("source_resume_id")
                    if declared_source != source_resume_id:
                        raise ValueError(
                            f"runtime roster source_resume_id disagrees with capability binding: {instance_id}"
                        )
            manipulation = source.get("manipulation")
            if not isinstance(manipulation, Mapping):
                raise ValueError(f"source resume lacks manipulation record: {source_resume_id}")
            bound_rows.append({
                "robot_id": instance_id,
                "instance_id": instance_id,
                "source_resume_id": source_resume_id,
                "manipulation": dict(manipulation),
                "capability_binding": capability_binding,
                "asset_binding": roster_row.get("asset_binding"),
                "initial_state_binding": roster_row.get("initial_state_binding"),
            })
        return cls.from_json_documents(scene_document, {"robots": bound_rows})

    def check(self, command: TransportCommand) -> TransportAdmissionResult:
        object_record = self.objects.get(command.object_ref)
        robot_record = self.robots.get(command.robot_id)
        mass = _mass_from_record(object_record) if isinstance(object_record, Mapping) else None
        payload = _payload_from_record(robot_record) if isinstance(robot_record, Mapping) else None
        if mass is None:
            result = TransportAdmissionResult(False, None, payload, "object mass_kg is unavailable")
        elif payload is None:
            result = TransportAdmissionResult(False, mass, None, "robot payload_kg is unavailable")
        elif mass > payload:
            result = TransportAdmissionResult(False, mass, payload, "object mass exceeds robot payload")
        else:
            result = TransportAdmissionResult(True, mass, payload, "admitted")
        self.records.append({"request_id": command.request_id,
                            "task_id": getattr(command, "task_id", command.request_id),
                            "robot_id": command.robot_id, "object_ref": command.object_ref,
                            **result.to_mapping()})
        return result


def execute_transport(command: TransportCommand, *, admission: TransportAdmissionResult,
                      action: Callable[[], Any]) -> bool:
    """Run the idealized action only after admission; return ``1``/``0`` as bool."""
    if not admission.admitted:
        return False
    action()
    return True


__all__ = ["TRANSPORT_COMMAND_SCHEMA", "TransportCommand", "compile_transport_command",
           "TransportAdmissionResult",
           "TransportAdmissionResolver",
           "execute_transport"]
