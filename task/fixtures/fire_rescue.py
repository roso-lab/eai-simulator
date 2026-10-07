"""Dependency-light Fire Rescue request fixture.

The fixture replaces the deleted demo scenario in adapter tests.  It is a
symbolic :class:`AssignmentRequest`, not an Isaac scene and not a source of
physical success evidence.  Runtime integrations should construct the same
request from the live EAI observation boundary.
"""

from __future__ import annotations

from algorithm.system.coordination.emos.policy import AssignmentRequest
from algorithm.system.coordination.emos.types import (
    EMOSScenarioConfig,
    PositionRule,
    RobotProfileSpec,
    SubtaskSpec,
)


def build_fire_rescue_assignment_request(
    *,
    excluded_subtask_ids: tuple[str, ...] = (),
    completed_extinguisher: tuple[str, ...] = (),
    completed_rescue: tuple[str, ...] = (),
) -> AssignmentRequest:
    """Return a deterministic four-robot symbolic Fire Rescue request."""

    robot_profiles = {
        "m20_1": RobotProfileSpec("M20", ["quadruped", "terrain", "extinguisher"]),
        "m20_2": RobotProfileSpec("M20", ["quadruped", "terrain", "extinguisher"]),
        "carter_1": RobotProfileSpec("Carter", ["sensor", "camera", "relay"]),
        "scout_1": RobotProfileSpec("Scout arm", ["arm", "button"]),
    }
    subtasks = {
        "red": SubtaskSpec("red", "hazard inspection", "inspect the hazard"),
        "green": SubtaskSpec("green", "rescue channel", "press the rescue channel button"),
        "blue": SubtaskSpec("blue", "sensor relay", "establish a sensor relay"),
        "yellow": SubtaskSpec("yellow", "extinguisher delivery", "carry and deliver the extinguisher"),
    }
    scenario = EMOSScenarioConfig(
        scenario_id="task-fixture-fire-rescue",
        scene_title="Fire Rescue symbolic task fixture",
        anchor_label="hazard",
        task_description="Inspect, relay, open the rescue channel and deliver the extinguisher.",
        subtasks=subtasks,
        position_rules={
            "red": PositionRule("fixed", (-4.5, -4.0)),
            "green": PositionRule("fixed", (10.5, 1.0)),
            "blue": PositionRule("fixed", (-7.5, -4.0)),
            "yellow": PositionRule("fixed", (1.8, -9.4)),
        },
        robot_profiles=robot_profiles,
        preferred_fallback={
            "m20_1": "red",
            "m20_2": "yellow",
            "carter_1": "blue",
            "scout_1": "green",
        },
        yellow_robot_prefix="m20",
        green_robot_name="scout_1",
    )
    return AssignmentRequest(
        task_description=scenario.task_description,
        scenario=scenario,
        hazard_id=1,
        hazard_pos=(-6.0, -4.0, 0.0),
        robot_positions={
            "m20_1": (-3.0, 5.0, 0.0),
            "m20_2": (3.0, 1.0, 0.0),
            "carter_1": (-7.6, -8.0, 0.0),
            "scout_1": (6.0, 5.5, 0.0),
        },
        subtask_positions={
            "red": (-4.5, -4.0),
            "green": (10.5, 1.0),
            "blue": (-7.5, -4.0),
            "yellow": (1.8, -9.4),
        },
        completed_extinguisher=completed_extinguisher,
        completed_rescue=completed_rescue,
        rescue_constraints=None,
        excluded_subtask_ids=excluded_subtask_ids,
        position_source="task.fixture.symbolic",
    )


__all__ = ["build_fire_rescue_assignment_request"]
