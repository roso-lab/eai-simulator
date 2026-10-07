"""Stateless Fire Rescue callback for the BT-capabilities condition.

The callback preserves the upstream capability/utility auction idea while
leaving physical execution to the EAI runner.  Replanning calls create a new
mission from the current AssignmentRequest; member failure and feedback are
handled by :class:`CapabilityMission` in the typed bridge and fixture tests.
"""

from __future__ import annotations

from typing import Mapping

from algorithm.system.coordination.emos.policy import AssignmentRequest, assignment_tasks
from algorithm.common_coordination import RobotCapability

from .adapter import BTCapability, CapabilityMission
from algorithm.heterocoord.fire_rescue_bridge import build_fire_rescue_records


def assign(request: AssignmentRequest) -> Mapping[str, str]:
    if not isinstance(request, AssignmentRequest):
        raise TypeError("BT capabilities Fire Rescue policy expects an AssignmentRequest")
    robots, tasks = build_fire_rescue_records(request)
    preferred = dict(request.scenario.preferred_fallback)
    utilities = {
        (robot.robot_id, task.task_id):
        (100.0 if preferred.get(robot.robot_id) == task.task_id else 0.0)
        for robot in robots
        for task in tasks
    }
    mission = CapabilityMission(
        tuple(
            BTCapability(
                task,
                {robot.robot_id: utilities[(robot.robot_id, task.task_id)] for robot in robots},
            )
            for task in tasks
        ),
        robots,
    )
    plan = mission.tick()
    if not plan.assignments:
        raise ValueError("BT capabilities auction found no capable Fire Rescue assignment")
    return {item.robot_id: item.task_id for item in plan.assignments}


__all__ = ["assign"]
