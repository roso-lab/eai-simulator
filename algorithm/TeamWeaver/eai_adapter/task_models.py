from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from types import MappingProxyType
from typing import Any, Mapping


class TaskType(str, Enum):
    NAVIGATE = "navigate"
    INSPECT = "inspect"
    ESTABLISH_RELAY = "establish_relay"
    PICK_EXTINGUISHER = "pick_extinguisher"
    DELIVER_EXTINGUISHER = "deliver_extinguisher"
    PRESS_RESCUE_BUTTON = "press_rescue_button"
    REMOVE_OBSTACLE = "remove_obstacle"
    WAIT = "wait"


class TaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    ASSIGNED = "assigned"
    NAVIGATING = "navigating"
    OPERATING = "operating"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class FeedbackOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"


class FailureKind(str, Enum):
    NONE = "none"
    EXECUTION_FAILURE = "execution_failure"
    PATH_FAILURE = "path_failure"
    TIMEOUT = "timeout"
    ROBOT_FALLEN = "robot_fallen"
    NAVIGATION_STALLED = "navigation_stalled"
    CONTROL_FAILURE = "control_failure"
    CONSTRAINT_VIOLATION = "constraint_violation"
    WORLD_STATE_CONFLICT = "world_state_conflict"
    RELAY_LOST = "relay_lost"


@dataclass(frozen=True)
class CapabilityRequirement:
    minimum: float
    weight: float
    hard: bool

    def to_payload(self) -> dict[str, object]:
        return {
            "minimum": float(self.minimum),
            "weight": float(self.weight),
            "hard": bool(self.hard),
        }


@dataclass(frozen=True)
class TargetSnapshot:
    ref: str
    position: tuple[float, float, float] | None
    available: bool
    kind: str
    compatible_task_types: tuple[TaskType, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "ref", str(self.ref))
        if self.position is not None:
            object.__setattr__(
                self,
                "position",
                tuple(float(value) for value in self.position),
            )
        object.__setattr__(
            self,
            "compatible_task_types",
            tuple(TaskType(item) for item in self.compatible_task_types),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "ref": self.ref,
            "position": None if self.position is None else list(self.position),
            "available": bool(self.available),
            "kind": self.kind,
            "compatible_task_types": [item.value for item in self.compatible_task_types],
        }


@dataclass(frozen=True)
class ObstacleSnapshot:
    obstacle_id: str
    position: tuple[float, float, float]
    dimensions: tuple[float, float, float]
    active: bool
    removed: bool
    blocking_task_id: str | None
    blocked_robot: str | None
    standoff_position: tuple[float, float, float]
    drag_target_position: tuple[float, float, float]
    removal_attempts: int = 0

    def __post_init__(self) -> None:
        obstacle_id = str(self.obstacle_id).strip()
        if not obstacle_id:
            raise ValueError("obstacle_id must be non-empty")
        object.__setattr__(self, "obstacle_id", obstacle_id)
        for name in (
            "position",
            "dimensions",
            "standoff_position",
            "drag_target_position",
        ):
            values = tuple(float(value) for value in getattr(self, name))
            if len(values) != 3 or not all(math.isfinite(value) for value in values):
                raise ValueError(f"{name} must contain three finite values")
            if name == "dimensions" and any(value <= 0.0 for value in values):
                raise ValueError("dimensions must be positive")
            object.__setattr__(self, name, values)
        for name in ("blocking_task_id", "blocked_robot"):
            value = getattr(self, name)
            normalized = None if value is None else str(value).strip()
            if value is not None and not normalized:
                raise ValueError(f"{name} must be non-empty when provided")
            object.__setattr__(self, name, normalized)
        if isinstance(self.removal_attempts, bool) or not isinstance(
            self.removal_attempts, int
        ):
            raise ValueError("removal_attempts must be an integer")
        if self.removal_attempts < 0:
            raise ValueError("removal_attempts must be non-negative")
        if self.active and self.removed:
            raise ValueError("an active obstacle cannot already be removed")

    def to_payload(self) -> dict[str, object]:
        return {
            "obstacle_id": self.obstacle_id,
            "position": list(self.position),
            "dimensions": list(self.dimensions),
            "active": bool(self.active),
            "removed": bool(self.removed),
            "blocking_task_id": self.blocking_task_id,
            "blocked_robot": self.blocked_robot,
            "standoff_position": list(self.standoff_position),
            "drag_target_position": list(self.drag_target_position),
            "removal_attempts": int(self.removal_attempts),
        }


@dataclass(frozen=True)
class RobotSnapshot:
    name: str
    position: tuple[float, float]
    base_capabilities: Mapping[str, float]
    reliability: Mapping[str, float]
    equipment: frozenset[str]
    busy: bool
    current_task: str | None
    current_load: int
    safe: bool
    current_stage: str | None = None
    preemptible: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", str(self.name))
        object.__setattr__(self, "position", tuple(float(value) for value in self.position))
        object.__setattr__(
            self,
            "base_capabilities",
            MappingProxyType(
                {str(key): float(value) for key, value in self.base_capabilities.items()}
            ),
        )
        object.__setattr__(
            self,
            "reliability",
            MappingProxyType(
                {str(key): float(value) for key, value in self.reliability.items()}
            ),
        )
        object.__setattr__(self, "equipment", frozenset(str(item) for item in self.equipment))
        current_stage = (
            None if self.current_stage is None else str(self.current_stage).strip()
        )
        if self.current_stage is not None and not current_stage:
            raise ValueError("current_stage must be non-empty when provided")
        object.__setattr__(self, "current_stage", current_stage)

    @property
    def effective_capabilities(self) -> dict[str, float]:
        return {
            name: float(value) * float(self.reliability.get(name, 1.0))
            for name, value in self.base_capabilities.items()
        }

    def to_payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "position": list(self.position),
            "base_capabilities": _sorted_float_mapping(self.base_capabilities),
            "reliability": _sorted_float_mapping(self.reliability),
            "effective_capabilities": _sorted_float_mapping(
                self.effective_capabilities
            ),
            "equipment": sorted(self.equipment),
            "busy": bool(self.busy),
            "current_task": self.current_task,
            "current_load": int(self.current_load),
            "safe": bool(self.safe),
            "current_stage": self.current_stage,
            "preemptible": bool(self.preemptible),
        }


@dataclass(frozen=True)
class SemanticTask:
    task_id: str
    task_type: TaskType
    description: str
    target_ref: str
    priority: int
    requirements: Mapping[str, CapabilityRequirement]
    prerequisites: tuple[str, ...]
    can_parallel: bool
    estimated_duration_s: float
    preferred_agent: str | None
    continuation_of: str | None = None
    required_agent: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", str(self.task_id))
        object.__setattr__(self, "task_type", TaskType(self.task_type))
        object.__setattr__(self, "description", str(self.description))
        object.__setattr__(self, "target_ref", str(self.target_ref))
        object.__setattr__(
            self,
            "requirements",
            MappingProxyType(
                {
                    str(name): requirement
                    if isinstance(requirement, CapabilityRequirement)
                    else CapabilityRequirement(**requirement)
                    for name, requirement in self.requirements.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "prerequisites",
            tuple(str(item) for item in self.prerequisites),
        )
        continuation_of = (
            None
            if self.continuation_of is None
            else str(self.continuation_of).strip()
        )
        required_agent = (
            None if self.required_agent is None else str(self.required_agent).strip()
        )
        if self.continuation_of is not None and not continuation_of:
            raise ValueError("continuation_of must be non-empty when provided")
        if self.required_agent is not None and not required_agent:
            raise ValueError("required_agent must be non-empty when provided")
        if required_agent is not None and continuation_of is None:
            raise ValueError("required_agent is only valid for a continuation task")
        object.__setattr__(self, "continuation_of", continuation_of)
        object.__setattr__(self, "required_agent", required_agent)

    def to_payload(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type.value,
            "description": self.description,
            "target_ref": self.target_ref,
            "priority": int(self.priority),
            "requirements": {
                name: self.requirements[name].to_payload()
                for name in sorted(self.requirements)
            },
            "prerequisites": list(self.prerequisites),
            "can_parallel": bool(self.can_parallel),
            "estimated_duration_s": float(self.estimated_duration_s),
            "preferred_agent": self.preferred_agent,
            "system_generated": self.continuation_of is not None,
            "continuation_of": self.continuation_of,
            "required_agent": self.required_agent,
        }


@dataclass(frozen=True)
class SymbolicWorldState:
    hazard_id: int
    hazard_position: tuple[float, float, float]
    hazard_active: bool
    targets: tuple[TargetSnapshot, ...]
    robots: tuple[RobotSnapshot, ...]
    extinguisher_available: bool
    extinguisher_carrier: str | None
    extinguisher_delivered: bool
    rescue_channel_open: bool
    completed_task_ids: tuple[str, ...]
    failed_task_ids: tuple[str, ...]
    recent_feedback: tuple[Mapping[str, object], ...]
    observations: Mapping[str, object]
    obstacles: tuple[ObstacleSnapshot, ...] = ()
    extinguisher_delivered_by: str | None = None
    facts: Mapping[str, object] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "hazard_position",
            tuple(float(value) for value in self.hazard_position),
        )
        object.__setattr__(self, "targets", tuple(self.targets))
        object.__setattr__(self, "robots", tuple(self.robots))
        object.__setattr__(self, "obstacles", tuple(self.obstacles))
        delivered_by = (
            None
            if self.extinguisher_delivered_by is None
            else str(self.extinguisher_delivered_by).strip()
        )
        if self.extinguisher_delivered_by is not None and not delivered_by:
            raise ValueError("extinguisher_delivered_by must be non-empty when provided")
        object.__setattr__(self, "extinguisher_delivered_by", delivered_by)
        object.__setattr__(
            self,
            "completed_task_ids",
            tuple(str(item) for item in self.completed_task_ids),
        )
        object.__setattr__(
            self,
            "failed_task_ids",
            tuple(str(item) for item in self.failed_task_ids),
        )
        object.__setattr__(
            self,
            "recent_feedback",
            tuple(_freeze_json(item) for item in self.recent_feedback),
        )
        object.__setattr__(self, "observations", _freeze_json(self.observations))
        object.__setattr__(self, "facts", _freeze_json(self.facts))

    def robot_by_name(self, name: str) -> RobotSnapshot:
        for robot in self.robots:
            if robot.name == name:
                return robot
        raise KeyError(name)

    def target_by_ref(self, ref: str) -> TargetSnapshot:
        for target in self.targets:
            if target.ref == ref:
                return target
        raise KeyError(ref)

    def active_obstacle(self) -> ObstacleSnapshot | None:
        active = tuple(
            obstacle
            for obstacle in self.obstacles
            if obstacle.active and not obstacle.removed
        )
        if len(active) > 1:
            raise ValueError("world contains multiple active obstacles")
        return active[0] if active else None

    def fact(self, key: str) -> object:
        """Return a generic world fact by key, or None."""
        return self.facts.get(key)

    def to_payload(self) -> dict[str, object]:
        entities: dict[str, object] = {
            "fire_extinguisher": {
                "available": bool(self.extinguisher_available),
                "carried_by": self.extinguisher_carrier,
                "delivered": bool(self.extinguisher_delivered),
                "delivered_by": self.extinguisher_delivered_by,
            },
            "rescue_channel": {"open": bool(self.rescue_channel_open)},
        }
        if self.facts:
            entities["facts"] = _thaw_json(self.facts)
        return {
            "hazard": {
                "id": int(self.hazard_id),
                "position": list(self.hazard_position),
                "active": bool(self.hazard_active),
            },
            "targets": [target.to_payload() for target in self.targets],
            "robots": [robot.to_payload() for robot in self.robots],
            "entities": entities,
            "completed_tasks": list(self.completed_task_ids),
            "failed_tasks": list(self.failed_task_ids),
            "recent_feedback": [_thaw_json(item) for item in self.recent_feedback],
            "observations": _thaw_json(self.observations),
            "obstacles": [obstacle.to_payload() for obstacle in self.obstacles],
        }


@dataclass(frozen=True)
class ExecutionFeedback:
    task_id: str
    robot_name: str
    outcome: FeedbackOutcome
    reason: str
    failure_kind: FailureKind
    relevant_capabilities: tuple[str, ...]
    world_changes: Mapping[str, object]
    timestamp_s: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome", FeedbackOutcome(self.outcome))
        object.__setattr__(self, "failure_kind", FailureKind(self.failure_kind))
        object.__setattr__(
            self,
            "relevant_capabilities",
            tuple(str(item) for item in self.relevant_capabilities),
        )
        object.__setattr__(self, "world_changes", _freeze_json(self.world_changes))
        if not math.isfinite(float(self.timestamp_s)):
            raise ValueError("timestamp_s must be finite")

    def to_payload(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "robot_name": self.robot_name,
            "outcome": self.outcome.value,
            "reason": self.reason,
            "failure_kind": self.failure_kind.value,
            "relevant_capabilities": list(self.relevant_capabilities),
            "world_changes": _thaw_json(self.world_changes),
            "timestamp_s": float(self.timestamp_s),
        }


def _sorted_float_mapping(values: Mapping[str, float]) -> dict[str, float]:
    return {name: float(values[name]) for name in sorted(values)}


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    if isinstance(value, Enum):
        return value.value
    return value
