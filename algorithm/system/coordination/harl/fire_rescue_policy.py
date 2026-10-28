"""Checkpoint-gated HAPPO input boundary for Fire Rescue."""

from __future__ import annotations

from typing import Any, Mapping

from algorithm.system.coordination.emos.policy import AssignmentRequest
from algorithm.heterocoord.fire_rescue_bridge import build_fire_rescue_records


class HAPPOCheckpointUnavailable(RuntimeError):
    """The source policy cannot be evaluated without a registered checkpoint."""


def build_observation(request: AssignmentRequest) -> dict[str, Any]:
    """Build a credential-free symbolic observation for a future HAPPO policy.

    The record is useful for registering the input contract, but it is not a
    policy result and must not be interpreted as a successful inference.
    """

    if not isinstance(request, AssignmentRequest):
        raise TypeError("HARL/HAPPO Fire Rescue policy expects an AssignmentRequest")
    robots, tasks = build_fire_rescue_records(request)
    return {
        "robots": [
            {
                "robot_id": robot.robot_id,
                "capabilities": sorted(robot.capabilities),
                "active": robot.active,
                "position_xy": list(request.robot_positions[robot.robot_id][:2]),
            }
            for robot in robots
        ],
        "tasks": [
            {
                "task_id": task.task_id,
                "required_capabilities": sorted(task.required_capabilities),
                "target_xy": list(request.subtask_positions.get(task.task_id, request.hazard_pos[:2])),
            }
            for task in tasks
        ],
        "observation_schema": "heterocoord.fire_rescue.happo.v1",
    }


def assign(request: AssignmentRequest) -> Mapping[str, str]:
    build_observation(request)
    raise HAPPOCheckpointUnavailable(
        "HARL/HAPPO source adapter is ready for inference, but no verifiable "
        "HAPPO checkpoint and matching Fire Rescue environment contract are registered"
    )


__all__ = ["HAPPOCheckpointUnavailable", "assign", "build_observation"]
