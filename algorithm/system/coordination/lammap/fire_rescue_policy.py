"""LaMMA-P-compatible dependency and skill assignment for Fire Rescue."""

from __future__ import annotations

import math
from typing import Iterable, Mapping

from algorithm.common_coordination import ContractError
from algorithm.system.coordination.emos.policy import AssignmentRequest, assignment_tasks
from algorithm.heterocoord.fire_rescue_bridge import build_fire_rescue_records


ALGORITHM_ID = "LaMMA-P-adapted-to-EAI"


def _distance(request: AssignmentRequest, robot_id: str, task_id: str) -> float:
    robot = request.robot_positions.get(robot_id)
    target = request.subtask_positions.get(task_id)
    if robot is None or target is None:
        return math.inf
    value = math.hypot(float(robot[0]) - float(target[0]), float(robot[1]) - float(target[1]))
    return value if math.isfinite(value) else math.inf


def _allocate(
    request: AssignmentRequest,
    *,
    excluded_robots: Iterable[str] = (),
    delayed_tasks: Iterable[str] = (),
    previous: Mapping[str, str] | None = None,
) -> dict[str, str]:
    robots, tasks = build_fire_rescue_records(request)
    unavailable = set(str(item) for item in excluded_robots)
    delayed = set(str(item) for item in delayed_tasks)
    previous = dict(previous or {})
    preferred = dict(getattr(request.scenario, "preferred_fallback", {}) or {})
    open_robots = [robot for robot in robots if robot.active and robot.robot_id not in unavailable]
    used: set[str] = set()
    result: dict[str, str] = {}
    # LaMMA-P's source contract separates dependency ordering from robot
    # allocation.  AssignmentRequest has already frozen the open task set; the
    # stable order below is the PDDL-compatible order for this adapter seam.
    ordered = sorted(tasks, key=lambda task: (task.task_id not in delayed, task.task_id))
    for task in ordered:
        candidates = [
            robot
            for robot in open_robots
            if robot.robot_id not in used and task.required_capabilities <= robot.capabilities
        ]
        if task.task_id == request.scenario.green_subtask_id and request.scenario.green_robot_name:
            candidates = [robot for robot in candidates if robot.robot_id == request.scenario.green_robot_name]
        if task.task_id == request.scenario.yellow_subtask_id and request.scenario.yellow_robot_prefix:
            candidates = [robot for robot in candidates if robot.robot_id.startswith(request.scenario.yellow_robot_prefix)]
        if not candidates:
            continue

        def score(robot) -> tuple[float, float, int, str]:
            priority = 0.0
            if preferred.get(robot.robot_id) == task.task_id:
                priority -= 100.0
            if previous.get(robot.robot_id) == task.task_id and task.task_id not in delayed:
                priority -= 20.0
            return priority, _distance(request, robot.robot_id, task.task_id), len(robot.capabilities), robot.robot_id

        selected = min(candidates, key=score)
        used.add(selected.robot_id)
        result[selected.robot_id] = task.task_id
    if not result:
        raise ContractError("LaMMA-P adapter found no feasible Fire Rescue assignment")
    assignment_tasks(request, result)
    return result


def assign(request: AssignmentRequest) -> Mapping[str, str]:
    if not isinstance(request, AssignmentRequest):
        raise TypeError("LaMMA-P Fire Rescue policy expects an AssignmentRequest")
    return _allocate(request)


def repair(
    request: AssignmentRequest,
    previous: Mapping[str, str],
    *,
    failed_robot_ids: Iterable[str] = (),
    delayed_task_ids: Iterable[str] = (),
) -> Mapping[str, str]:
    if not isinstance(request, AssignmentRequest):
        raise TypeError("LaMMA-P Fire Rescue policy expects an AssignmentRequest")
    return _allocate(
        request,
        excluded_robots=failed_robot_ids,
        delayed_tasks=delayed_task_ids,
        previous=previous,
    )


__all__ = ["assign", "repair"]
