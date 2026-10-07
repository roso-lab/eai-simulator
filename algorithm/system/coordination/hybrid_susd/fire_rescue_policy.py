"""Fire Rescue assignment and adapter-level reallocation for Hybrid SUSD."""

from __future__ import annotations

from typing import Iterable, Mapping

from algorithm.system.coordination.emos.policy import AssignmentRequest
from algorithm.heterocoord.fire_rescue_bridge import build_fire_rescue_records

from .adapter import HybridSUSDAllocator, HybridTask


def _tasks(request: AssignmentRequest, records) -> tuple[HybridTask, ...]:
    _, typed_tasks = records
    def xy(task_id: str) -> tuple[float, float]:
        value = request.subtask_positions.get(task_id)
        return (float(value[0]), float(value[1])) if value is not None else (0.0, 0.0)

    return tuple(
        HybridTask(
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
    return HybridSUSDAllocator.from_capabilities(robots, request.robot_positions), records


def assign(request: AssignmentRequest) -> Mapping[str, str]:
    if not isinstance(request, AssignmentRequest):
        raise TypeError("Hybrid SUSD Fire Rescue policy expects an AssignmentRequest")
    allocator, records = _allocator(request)
    plan = allocator.allocate(_tasks(request, records))
    if not plan.assignments:
        raise ValueError("Hybrid SUSD allocator found no feasible assignment")
    return plan.mapping


def repair(
    request: AssignmentRequest,
    previous: Mapping[str, str],
    *,
    failed_robot_ids: Iterable[str] = (),
    delayed_task_ids: Iterable[str] = (),
) -> Mapping[str, str]:
    if not isinstance(request, AssignmentRequest):
        raise TypeError("Hybrid SUSD Fire Rescue policy expects an AssignmentRequest")
    allocator, records = _allocator(request)
    plan = allocator.repair(
        _tasks(request, records),
        previous,
        failed_robot_ids=failed_robot_ids,
        delayed_task_ids=delayed_task_ids,
    )
    return plan.mapping


__all__ = ["assign", "repair"]
