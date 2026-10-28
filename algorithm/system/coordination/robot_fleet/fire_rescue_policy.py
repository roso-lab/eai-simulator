"""RobotFleet-style DAG allocation for the typed Fire Rescue request."""

from __future__ import annotations

import math
from typing import Iterable, Mapping

from algorithm.common_coordination import ContractError
from algorithm.system.coordination.emos.policy import AssignmentRequest, assignment_tasks
from algorithm.heterocoord.fire_rescue_bridge import build_fire_rescue_records


ALGORITHM_ID = "RobotFleet-adapted-to-EAI"


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
    excluded = set(str(item) for item in excluded_robots)
    delayed = set(str(item) for item in delayed_tasks)
    previous = dict(previous or {})
    preferred = dict(getattr(request.scenario, "preferred_fallback", {}) or {})
    available = [robot for robot in robots if robot.active and robot.robot_id not in excluded]
    used: set[str] = set()
    result: dict[str, str] = {}
    ordered_tasks = sorted(tasks, key=lambda task: (task.task_id not in delayed, task.task_id))
    for task in ordered_tasks:
        task_id = task.task_id
        green = request.scenario.green_subtask_id
        yellow = request.scenario.yellow_subtask_id
        green_robot = str(request.scenario.green_robot_name or "")
        yellow_prefix = str(request.scenario.yellow_robot_prefix or "")
        candidates = [
            robot
            for robot in available
            if robot.robot_id not in used
            and task.required_capabilities <= robot.capabilities
            and (task_id != green or not green_robot or robot.robot_id == green_robot)
            and (task_id != yellow or not yellow_prefix or robot.robot_id.startswith(yellow_prefix))
        ]
        if not candidates:
            continue

        def score(robot) -> tuple[float, float, int, str]:
            affinity = 0.0
            if preferred.get(robot.robot_id) == task_id:
                affinity -= 100.0
            if previous.get(robot.robot_id) == task_id and task_id not in delayed:
                affinity -= 20.0
            return affinity, _distance(request, robot.robot_id, task_id), len(robot.capabilities), robot.robot_id

        selected = min(candidates, key=score)
        used.add(selected.robot_id)
        result[selected.robot_id] = task_id
    if not result:
        raise ContractError("RobotFleet allocator found no feasible Fire Rescue assignment")
    assignment_tasks(request, result)
    return result


def assign(request: AssignmentRequest) -> Mapping[str, str]:
    if not isinstance(request, AssignmentRequest):
        raise TypeError("RobotFleet Fire Rescue policy expects an AssignmentRequest")
    return _allocate(request)


def repair(
    request: AssignmentRequest,
    previous: Mapping[str, str],
    *,
    failed_robot_ids: Iterable[str] = (),
    delayed_task_ids: Iterable[str] = (),
) -> Mapping[str, str]:
    if not isinstance(request, AssignmentRequest):
        raise TypeError("RobotFleet Fire Rescue policy expects an AssignmentRequest")
    return _allocate(
        request,
        excluded_robots=failed_robot_ids,
        delayed_tasks=delayed_task_ids,
        previous=previous,
    )


__all__ = ["assign", "repair"]
