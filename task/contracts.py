"""Dependency-light contracts between algorithms, task runner, and EAI.

No class in this module imports Isaac Sim, Isaac Lab, Torch, ROS, or Gym.  A
task package describes work and robot capabilities; physical execution and
success evidence are supplied by injected EAI adapters.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from algorithm.common_coordination.contract import (
    AlgorithmCondition,
    Assignment,
    CoordinationTask,
    EventLedger,
    ExecutionFeedback,
    RobotCapability,
    TaskGraph,
)


@dataclass(frozen=True)
class RobotState:
    """Public robot state visible to an algorithm decision callback."""

    capability: RobotCapability
    position: tuple[float, float, float] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.position is not None and len(self.position) != 3:
            raise ValueError("robot position must contain exactly three coordinates")
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class EaiTaskPackage:
    """A scene-independent task graph and robot roster."""

    task_id: str
    scene_id: str
    graph: TaskGraph
    robots: tuple[RobotState, ...]
    observation_schema: str = "task.observation.v1"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.task_id).strip() or not str(self.scene_id).strip():
            raise ValueError("task_id and scene_id are required")
        robot_ids = [item.capability.robot_id for item in self.robots]
        if len(set(robot_ids)) != len(robot_ids):
            raise ValueError("robot ids must be unique")
        object.__setattr__(self, "metadata", dict(self.metadata))

    @classmethod
    def from_json(cls, path: str | Path) -> "EaiTaskPackage":
        import json

        source = Path(path)
        data = json.loads(source.read_text(encoding="utf-8"))
        tasks = tuple(
            CoordinationTask(
                task_id=item["task_id"],
                description=item["description"],
                required_capabilities=frozenset(item.get("required_capabilities", ())),
                prerequisites=tuple(item.get("prerequisites", ())),
                target_ref=item.get("target_ref"),
                metadata=item.get("metadata", {}),
            )
            for item in data["tasks"]
        )
        graph = TaskGraph(tasks=tasks, source=str(source), valid=True)
        robots = tuple(
            RobotState(
                capability=RobotCapability(
                    robot_id=item["robot_id"],
                    capabilities=frozenset(item.get("capabilities", ())),
                    active=bool(item.get("active", True)),
                ),
                position=tuple(item["position"]) if item.get("position") is not None else None,
                metadata=item.get("metadata", {}),
            )
            for item in data.get("robots", ())
        )
        return cls(
            task_id=str(data.get("task_id", source.stem)),
            scene_id=str(data["scene_id"]),
            graph=graph,
            robots=robots,
            observation_schema=str(data.get("observation_schema", "task.observation.v1")),
            metadata=data.get("metadata", {}),
        )

    def bind_runtime(
        self,
        *,
        robots: Sequence[RobotState],
        scene_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "EaiTaskPackage":
        """Bind a live EAI robot roster to a static task contract.

        Task JSON stays scene-independent.  The simulator/session adapter calls
        this method after it has created the selected EAI environment and
        sampled the active robot capabilities.  It is intentionally a pure
        operation so the same task definition can be used by fact and replay
        branches without sharing mutable simulator state.
        """

        merged_metadata = dict(self.metadata)
        if metadata:
            merged_metadata.update(metadata)
        return replace(
            self,
            scene_id=str(scene_id or self.scene_id),
            robots=tuple(robots),
            metadata=merged_metadata,
        )


@dataclass(frozen=True)
class AlgorithmDecision:
    """Algorithm output; it contains no physical success claim."""

    condition: AlgorithmCondition
    assignments: tuple[Assignment, ...] = ()
    unassigned_tasks: tuple[str, ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "assignments", tuple(self.assignments))
        object.__setattr__(self, "unassigned_tasks", tuple(str(item) for item in self.unassigned_tasks))
        object.__setattr__(self, "provenance", dict(self.provenance))


@dataclass(frozen=True)
class TaskRunRequest:
    task: EaiTaskPackage
    branch_id: str = "fact"
    checkpoint: str | None = None
    repeat_id: str = ""


@dataclass(frozen=True)
class TaskRunResult:
    request: TaskRunRequest
    decision: AlgorithmDecision
    feedback: tuple[ExecutionFeedback, ...]
    verdict: Mapping[str, Any]
    ledger: EventLedger

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.request.task.task_id,
            "scene_id": self.request.task.scene_id,
            "branch_id": self.request.branch_id,
            "checkpoint": self.request.checkpoint,
            "repeat_id": self.request.repeat_id,
            "condition": self.decision.condition.to_dict(),
            "assignments": [item.__dict__ for item in self.decision.assignments],
            "unassigned_tasks": list(self.decision.unassigned_tasks),
            "feedback": [
                {
                    **item.__dict__,
                    "failure_code": item.failure_code.value,
                }
                for item in self.feedback
            ],
            "verdict": dict(self.verdict),
            "ledger": {
                "schema_version": self.ledger.schema_version,
                "events": list(self.ledger.events),
                "digest": self.ledger.digest(),
            },
        }


__all__ = [
    "AlgorithmDecision",
    "Assignment",
    "EaiTaskPackage",
    "ExecutionFeedback",
    "RobotState",
    "TaskRunRequest",
    "TaskRunResult",
]
