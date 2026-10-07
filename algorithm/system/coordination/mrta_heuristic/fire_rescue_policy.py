"""Fire Rescue callback for the official MRTA heuristic adapter."""

from __future__ import annotations

from typing import Iterable, Mapping

from algorithm.system.coordination.emos.policy import AssignmentRequest
from algorithm.heterocoord.fire_rescue_bridge import build_fire_rescue_records

from .adapter import HeuristicTask, HeuristicTaskAllocator


def _tasks(request: AssignmentRequest, records):
    _, typed_tasks = records
    def xy(task_id: str) -> tuple[float, float]:
        value = request.subtask_positions.get(task_id)
        if value is None:
            return (0.0, 0.0)
        return (float(value[0]), float(value[1]))

    return tuple(
        HeuristicTask(
            task.task_id,
            task.required_capabilities,
            xy(task.task_id),
            priority=1.0 if task.task_id == "yellow" else 0.0,
        )
        for task in typed_tasks
    )


def _allocator(request: AssignmentRequest):
    records = build_fire_rescue_records(request)
    robots, _ = records
    allocator = HeuristicTaskAllocator.from_capabilities(robots, request.robot_positions)
    return allocator, records


def assign(request: AssignmentRequest) -> Mapping[str, str]:
    if not isinstance(request, AssignmentRequest):
        raise TypeError("MRTA heuristic Fire Rescue policy expects an AssignmentRequest")
    allocator, records = _allocator(request)
    plan = allocator.allocate(_tasks(request, records))
    if not plan.assignments:
        raise ValueError("MRTA heuristic allocator found no feasible assignment")
    return plan.mapping


def repair(
    request: AssignmentRequest,
    previous: Mapping[str, str],
    *,
    failed_robot_ids: Iterable[str] = (),
    delayed_task_ids: Iterable[str] = (),
) -> Mapping[str, str]:
    if not isinstance(request, AssignmentRequest):
        raise TypeError("MRTA heuristic Fire Rescue policy expects an AssignmentRequest")
    allocator, records = _allocator(request)
    tasks = _tasks(request, records)
    prior = allocator.allocate(tasks)
    # Preserve the externally supplied assignment as the authoritative prior
    # plan, then run the version-13 repair over affected queues.
    from algorithm.common_coordination import Assignment

    previous_plan = type(prior)(
        tuple(Assignment(task_id, robot_id, "MRTA-heuristic-adapted-to-EAI", "external-prior") for robot_id, task_id in sorted(previous.items())),
        prior.objective,
        "external-prior",
        prior.unassigned_task_ids,
    )
    return allocator.repair(
        tasks,
        previous_plan,
        failed_robot_ids=failed_robot_ids,
        delayed_task_ids=delayed_task_ids,
    ).mapping


__all__ = ["assign", "repair"]
