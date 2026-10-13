"""EMOS-to-benchmark decision adapter.

This module adapts the existing EMOS assignment boundary into the generic
benchmark runtime.  It deliberately stops at ``AlgorithmDecision``:
controller calls, skill execution, physical milestones and terminal outcomes
remain owned by benchmark/EAI adapters.

The default callback is EMOS's deterministic fallback assignment so
the contract can be tested without an LLM, credentials, Isaac Sim or ROS.
Callers may inject the native EMOS assignment callback when those services are
available and should record that provenance in the resulting decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from algorithm.common_coordination.contract import AlgorithmCondition, Assignment, TaskGraph
from algorithm.heterocoord.fire_rescue_bridge import build_fire_rescue_records
from algorithm.system.coordination.emos.assignment import fallback_assignment
from algorithm.system.coordination.emos.benchmark_condition import condition_record
from algorithm.system.coordination.emos.policy import AssignmentRequest, assignment_tasks, validate_assignment_policy
from task.contracts import AlgorithmDecision, EaiTaskPackage, RobotState


AssignmentPolicy = Callable[[AssignmentRequest], Mapping[str, str]]


def emos_fallback_assign(request: AssignmentRequest) -> dict[str, str]:
    """Run EMOS's deterministic fallback without touching a live runtime."""

    assignments = fallback_assignment(
        request.scenario,
        dict(request.subtask_positions),
        sorted(request.robot_positions),
        robots_completed_extinguisher=set(request.completed_extinguisher),
        robots_completed_rescue=set(request.completed_rescue),
        sys_log=lambda _message: None,
        push_chat=lambda *_args: None,
        generate_report=lambda _assignments: None,
        excluded_subtask_ids=set(request.excluded_subtask_ids),
    )
    return {robot_id: robot_task.subtask_colour for robot_id, robot_task in assignments.items()}


def package_from_fire_rescue_request(
    request: AssignmentRequest,
    *,
    task_id: str | None = None,
    scene_id: str = "fire_rescue",
) -> EaiTaskPackage:
    """Translate an EMOS symbolic request to the generic task package.

    The returned package contains only task/robot contracts and public spawn
    observations.  It is not a physical scene and must not be treated as
    evaluator evidence.
    """

    capabilities, tasks = build_fire_rescue_records(request)
    capability_by_id = {item.robot_id: item for item in capabilities}
    robots = tuple(
        RobotState(
            capability=capability_by_id[robot_id],
            position=tuple(request.robot_positions[robot_id]),
            metadata={"position_source": request.position_source or "unknown"},
        )
        for robot_id in sorted(capability_by_id)
    )
    graph = TaskGraph(tasks=tasks, source=f"emos:{request.scenario.scenario_id}")
    return EaiTaskPackage(
        task_id=task_id or request.scenario.scenario_id,
        scene_id=scene_id,
        graph=graph,
        robots=robots,
        metadata={
            "algorithm_input": "emos.AssignmentRequest",
            "scenario_id": request.scenario.scenario_id,
            "position_source": request.position_source or "unknown",
            "subtask_positions": dict(request.subtask_positions),
            "completed_extinguisher": list(request.completed_extinguisher),
            "completed_rescue": list(request.completed_rescue),
        },
    )


@dataclass(frozen=True)
class EmosDecisionAdapter:
    """Expose an EMOS assignment policy as a benchmark DecisionProvider."""

    request: AssignmentRequest
    policy: AssignmentPolicy = emos_fallback_assign
    condition: AlgorithmCondition | None = None
    plan_version: str = "emos-assignment-v1"
    provenance: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy", validate_assignment_policy(self.policy))
        object.__setattr__(self, "condition", self.condition or condition_record(model_or_api="deterministic-emos-fallback"))
        object.__setattr__(self, "provenance", dict(self.provenance or {}))

    def __call__(self, task: EaiTaskPackage, robots: tuple[RobotState, ...]) -> AlgorithmDecision:
        """Run assignment only and convert the mapping to typed records."""

        runtime_robot_ids = {item.capability.robot_id for item in robots}
        request_robot_ids = set(self.request.robot_positions)
        if runtime_robot_ids != request_robot_ids:
            raise ValueError(
                "EMOS request/runtime roster mismatch: "
                f"request={sorted(request_robot_ids)}, runtime={sorted(runtime_robot_ids)}"
            )
        mapping = self.policy(self.request)
        if not isinstance(mapping, Mapping):
            raise TypeError("EMOS assignment policy must return a robot-to-subtask mapping")
        assignment_tasks(self.request, mapping)  # preserve EMOS-specific validation

        task_ids = {item.task_id for item in task.graph.tasks}
        robot_ids = {item.capability.robot_id for item in task.robots if item.capability.active}
        unknown_tasks = set(mapping.values()) - task_ids
        unknown_robots = set(mapping) - robot_ids
        if unknown_tasks or unknown_robots:
            raise ValueError(
                f"EMOS mapping does not match task package: unknown_tasks={sorted(unknown_tasks)}, "
                f"unknown_robots={sorted(unknown_robots)}"
            )

        assignments = tuple(
            Assignment(
                task_id=str(task_id),
                robot_id=str(robot_id),
                source=self.condition.algorithm_id,
                plan_version=self.plan_version,
            )
            for robot_id, task_id in sorted(mapping.items())
        )
        assigned = {item.task_id for item in assignments}
        provenance = {
            "adapter": "benchmark.emos.EmosDecisionAdapter",
            "policy": f"{self.policy.__module__}:{self.policy.__name__}",
            "scenario_id": self.request.scenario.scenario_id,
            "position_source": self.request.position_source or "unknown",
            **dict(self.provenance or {}),
        }
        return AlgorithmDecision(
            condition=self.condition,
            assignments=assignments,
            unassigned_tasks=tuple(sorted(task_ids - assigned)),
            provenance=provenance,
        )


__all__ = [
    "AssignmentPolicy",
    "EmosDecisionAdapter",
    "emos_fallback_assign",
    "package_from_fire_rescue_request",
]
