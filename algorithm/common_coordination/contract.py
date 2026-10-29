"""Small, dependency-free records for auditable coordination conditions.

These records deliberately do not score episodes.  They make the boundary in
the HeteroCoord paper explicit: planning and assignment are method outputs;
execution feedback and physical milestones are evidence owned by EAI and the
evaluator.  A missing external record is therefore represented as ``None`` or
an explicit failure code, never as an inferred success.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
import math
from typing import Any, Iterable, Mapping, Sequence


class ContractError(ValueError):
    """Raised when an adapter emits an unsafe or malformed record."""


class FailureCode(str, Enum):
    NONE = "none"
    INVALID_PLAN = "invalid_plan"
    INFEASIBLE_TEAM = "infeasible_team"
    FEASIBILITY_UNVERIFIED = "feasibility_unverified"
    COMMUNICATION_DELAY = "communication_delay"
    COMMUNICATION_LOSS = "communication_loss"
    MEMBER_EXIT = "member_exit"
    CAPABILITY_LOSS = "capability_loss"
    EXECUTION_FAILURE = "execution_failure"
    SAFETY_VIOLATION = "safety_violation"
    SAFE_STOP = "safe_stop"
    TIMEOUT = "timeout"
    MILESTONE_UNVERIFIED = "milestone_unverified"


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise ContractError(f"{name} must be finite numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"{name} must be finite numeric") from exc
    if not math.isfinite(result):
        raise ContractError(f"{name} must be finite")
    return result


@dataclass(frozen=True)
class AlgorithmCondition:
    """Versioned identity of one evaluated algorithm condition."""

    algorithm_id: str
    source_reference: str
    implementation: str
    execution_scope: str
    planner: str
    model_or_api: str = "not_applicable"
    checkpoint: str = "not_applicable_no_learned_weights"
    prompt_revision: str = "not_applicable"
    supported_primitives: tuple[str, ...] = ()
    unsupported_dimensions: tuple[str, ...] = ()
    physical_executor_owner: str = "eai_runtime"
    serial_action_loop: bool = False
    notes: str = ""

    def __post_init__(self) -> None:
        for field_name in ("algorithm_id", "source_reference", "implementation", "execution_scope", "planner"):
            if not str(getattr(self, field_name)).strip():
                raise ContractError(f"{field_name} cannot be empty")
        object.__setattr__(self, "algorithm_id", str(self.algorithm_id).strip())
        object.__setattr__(self, "source_reference", str(self.source_reference).strip())
        object.__setattr__(self, "implementation", str(self.implementation).strip())
        object.__setattr__(self, "execution_scope", str(self.execution_scope).strip())
        object.__setattr__(self, "planner", str(self.planner).strip())
        object.__setattr__(self, "supported_primitives", tuple(str(item) for item in self.supported_primitives))
        object.__setattr__(self, "unsupported_dimensions", tuple(str(item) for item in self.unsupported_dimensions))

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RobotCapability:
    robot_id: str
    capabilities: frozenset[str]
    active: bool = True

    def __post_init__(self) -> None:
        if not str(self.robot_id).strip():
            raise ContractError("robot_id cannot be empty")
        if any(not isinstance(item, str) or not item.strip() for item in self.capabilities):
            raise ContractError("capabilities must be non-empty strings")
        object.__setattr__(self, "robot_id", str(self.robot_id).strip())
        object.__setattr__(self, "capabilities", frozenset(str(item).strip() for item in self.capabilities))


@dataclass(frozen=True)
class CoordinationTask:
    task_id: str
    description: str
    required_capabilities: frozenset[str] = frozenset()
    prerequisites: tuple[str, ...] = ()
    target_ref: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.task_id).strip() or not str(self.description).strip():
            raise ContractError("task_id and description are required")
        if self.task_id in self.prerequisites:
            raise ContractError(f"task cannot depend on itself: {self.task_id}")
        if len(set(self.prerequisites)) != len(self.prerequisites):
            raise ContractError(f"duplicate prerequisites for {self.task_id}")
        object.__setattr__(self, "task_id", str(self.task_id).strip())
        object.__setattr__(self, "description", str(self.description).strip())
        object.__setattr__(self, "required_capabilities", frozenset(str(item) for item in self.required_capabilities))
        object.__setattr__(self, "prerequisites", tuple(str(item) for item in self.prerequisites))
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class TaskGraph:
    tasks: tuple[CoordinationTask, ...]
    source: str
    valid: bool = True

    def __post_init__(self) -> None:
        if not self.tasks:
            raise ContractError("task graph cannot be empty")
        ids = {task.task_id for task in self.tasks}
        if len(ids) != len(self.tasks):
            raise ContractError("task graph task ids must be unique")
        unknown = {dep for task in self.tasks for dep in task.prerequisites if dep not in ids}
        if unknown:
            raise ContractError(f"task graph has unknown prerequisites: {sorted(unknown)}")
        self._check_acyclic()

    def _check_acyclic(self) -> None:
        by_id = {task.task_id: task for task in self.tasks}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise ContractError(f"cyclic task graph involving {task_id}")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in by_id[task_id].prerequisites:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in by_id:
            visit(task_id)


@dataclass(frozen=True)
class Assignment:
    task_id: str
    robot_id: str
    source: str
    plan_version: str = ""

    def __post_init__(self) -> None:
        if not str(self.task_id).strip() or not str(self.robot_id).strip():
            raise ContractError("assignment task_id and robot_id are required")


@dataclass(frozen=True)
class CoalitionAssignment:
    """A lossless native multi-robot assignment proposal.

    This record is intentionally independent from ``Assignment``. The current
    TaskRunner still consumes one owner per task; adding this type does not
    silently change that execution contract.
    """

    task_id: str
    robot_ids: tuple[str, ...]
    source: str
    plan_version: str = ""
    role_bindings: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.task_id).strip() or not str(self.source).strip():
            raise ContractError("coalition task_id and source are required")
        if isinstance(self.robot_ids, (str, bytes)):
            raise ContractError("coalition robot_ids must be a sequence")
        members = tuple(str(item).strip() for item in self.robot_ids)
        if len(members) < 2 or any(not item for item in members):
            raise ContractError("coalition robot_ids must contain at least two members")
        if len(set(members)) != len(members):
            raise ContractError("coalition robot_ids must be unique")
        bindings = dict(self.role_bindings)
        if any(not isinstance(key, str) or not key.strip() for key in bindings):
            raise ContractError("coalition role binding robot IDs must be non-empty strings")
        if any(not isinstance(value, str) or not value.strip() for value in bindings.values()):
            raise ContractError("coalition role bindings must be non-empty strings")
        if not set(bindings).issubset(members):
            raise ContractError("coalition role bindings contain an unknown robot")
        object.__setattr__(self, "task_id", str(self.task_id).strip())
        object.__setattr__(self, "robot_ids", tuple(sorted(members)))
        object.__setattr__(self, "source", str(self.source).strip())
        object.__setattr__(self, "plan_version", str(self.plan_version))
        object.__setattr__(self, "role_bindings", bindings)


@dataclass(frozen=True)
class CoalitionSkillRequest:
    """Execution request envelope; it does not claim execution or success."""

    assignment: CoalitionAssignment
    skill: str
    sync_window_ticks: tuple[int, int] | None = None
    minimum_holders: int | None = None

    def __post_init__(self) -> None:
        if not str(self.skill).strip():
            raise ContractError("coalition skill is required")
        object.__setattr__(self, "skill", str(self.skill).strip())
        if self.sync_window_ticks is not None:
            if len(self.sync_window_ticks) != 2 or any(
                isinstance(item, bool) or not isinstance(item, int) or item < 0
                for item in self.sync_window_ticks
            ) or self.sync_window_ticks[0] > self.sync_window_ticks[1]:
                raise ContractError("coalition sync window must be an ordered nonnegative tick pair")
            object.__setattr__(self, "sync_window_ticks", tuple(self.sync_window_ticks))
        if self.minimum_holders is not None:
            if isinstance(self.minimum_holders, bool) or not isinstance(self.minimum_holders, int):
                raise ContractError("coalition minimum_holders must be an integer")
            if not 2 <= self.minimum_holders <= len(self.assignment.robot_ids):
                raise ContractError("coalition minimum_holders must fit the native member set")

    @property
    def coalition_id(self) -> str:
        payload = json.dumps({"task_id": self.assignment.task_id,
                              "robot_ids": self.assignment.robot_ids,
                              "plan_version": self.assignment.plan_version},
                             separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict[str, object]:
        return {"schema": "heterocoord.coalition_skill_request.v1",
                "coalition_id": self.coalition_id,
                "task_id": self.assignment.task_id,
                "robot_ids": list(self.assignment.robot_ids),
                "source": self.assignment.source,
                "plan_version": self.assignment.plan_version,
                "role_bindings": dict(self.assignment.role_bindings),
                "skill": self.skill,
                "sync_window_ticks": list(self.sync_window_ticks) if self.sync_window_ticks else None,
                "minimum_holders": self.minimum_holders,
                "execution_status": "not_started"}


@dataclass(frozen=True)
class CoalitionExecutionReceipt:
    """Host session receipt; it contains evidence, never an evaluator verdict."""

    coalition_id: str
    task_id: str
    robot_ids: tuple[str, ...]
    status: str
    session_id: str
    evidence_digest: str
    evaluator_evidence_ref: str | None = None

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (self.coalition_id, self.task_id, self.status, self.session_id, self.evidence_digest)):
            raise ContractError("coalition receipt identity and evidence fields are required")
        members = tuple(str(item).strip() for item in self.robot_ids)
        if len(members) < 2 or len(set(members)) != len(members) or any(not item for item in members):
            raise ContractError("coalition receipt requires unique members")
        if len(self.evidence_digest) != 64 or any(item not in "0123456789abcdef" for item in self.evidence_digest):
            raise ContractError("coalition receipt evidence_digest must be SHA-256")
        object.__setattr__(self, "robot_ids", tuple(sorted(members)))
        if self.evaluator_evidence_ref is not None and not isinstance(self.evaluator_evidence_ref, str):
            raise ContractError("evaluator_evidence_ref must be text or None")

    def to_dict(self) -> dict[str, object]:
        return {"schema": "heterocoord.coalition_execution_receipt.v1", **self.__dict__,
                "robot_ids": list(self.robot_ids), "evaluator_verdict": "external_only"}


@dataclass(frozen=True)
class ExecutionFeedback:
    task_id: str
    robot_id: str
    outcome: str
    failure_code: FailureCode = FailureCode.NONE
    message: str = ""
    timestamp_s: float = 0.0
    world_changes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "failure_code", FailureCode(self.failure_code))
        object.__setattr__(self, "timestamp_s", _finite(self.timestamp_s, "timestamp_s"))
        if not str(self.task_id).strip() or not str(self.robot_id).strip():
            raise ContractError("feedback task_id and robot_id are required")
        if not str(self.outcome).strip():
            raise ContractError("feedback outcome is required")
        object.__setattr__(self, "world_changes", dict(self.world_changes))


@dataclass(frozen=True)
class MilestoneEvidence:
    milestone_id: str
    satisfied: bool | None
    source: str
    timestamp_s: float | None = None
    predicate_version: str = ""
    evidence_hash: str = ""

    def __post_init__(self) -> None:
        if not str(self.milestone_id).strip() or not str(self.source).strip():
            raise ContractError("milestone_id and source are required")
        if self.timestamp_s is not None:
            object.__setattr__(self, "timestamp_s", _finite(self.timestamp_s, "timestamp_s"))
        if self.satisfied is not None and not isinstance(self.satisfied, bool):
            raise ContractError("milestone satisfied must be bool or None")


class EventLedger:
    """Append-only JSON-safe event ledger for plan/execution provenance."""

    def __init__(self, *, schema_version: str = "coordination.v1") -> None:
        self.schema_version = str(schema_version)
        self._events: list[dict[str, object]] = []

    def append(self, kind: str, **payload: object) -> dict[str, object]:
        if not str(kind).strip():
            raise ContractError("ledger event kind cannot be empty")
        event = {
            "sequence": len(self._events),
            "kind": str(kind),
            "payload": dict(payload),
        }
        self._events.append(event)
        return dict(event)

    @property
    def events(self) -> tuple[dict[str, object], ...]:
        return tuple(dict(item) for item in self._events)

    def digest(self) -> str:
        payload = json.dumps(
            {"schema_version": self.schema_version, "events": self._events},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class RunManifest:
    condition: AlgorithmCondition
    task_id: str
    scene_id: str
    branch: str = "fact"
    repeat_id: str = ""
    checkpoint_digest: str = ""
    task_generator_hash: str = ""
    asset_hash: str = ""
    simulator_version: str = "Isaac Sim 5.1 / Isaac Lab 2.x"
    observation_schema: str = ""
    status: str = "pending"

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["condition"] = self.condition.to_dict()
        return data


def assignments_to_mapping(assignments: Iterable[Assignment]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in assignments:
        if item.robot_id in result:
            raise ContractError(f"robot assigned more than once: {item.robot_id}")
        result[item.robot_id] = item.task_id
    return result
