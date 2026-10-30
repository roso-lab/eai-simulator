from __future__ import annotations

from copy import deepcopy
from typing import Any


def make_factory_world(*, obstacles=()):
    from TeamWeaver.eai_adapter.task_models import (
        RobotSnapshot,
        SymbolicWorldState,
        TargetSnapshot,
        TaskType,
    )

    capabilities = (
        "navigation",
        "sensing",
        "relay",
        "payload",
        "agility",
        "manipulation",
        "button_press",
        "extinguisher_handling",
        "obstacle_handling",
    )
    profiles = {
        "carter_1": {
            "navigation": 1.0,
            "sensing": 1.0,
            "relay": 1.0,
            "payload": 0.2,
            "agility": 0.4,
            "manipulation": 0.0,
            "button_press": 0.0,
            "extinguisher_handling": 0.0,
            "obstacle_handling": 0.0,
        },
        "m20_1": {
            "navigation": 1.0,
            "sensing": 0.7,
            "relay": 0.3,
            "payload": 1.0,
            "agility": 0.9,
            "manipulation": 0.8,
            "button_press": 0.8,
            "extinguisher_handling": 0.9,
            "obstacle_handling": 0.9,
        },
        "m20_2": {
            "navigation": 1.0,
            "sensing": 0.7,
            "relay": 0.3,
            "payload": 1.0,
            "agility": 0.9,
            "manipulation": 0.8,
            "button_press": 0.8,
            "extinguisher_handling": 0.9,
            "obstacle_handling": 0.9,
        },
        "scout_1": {
            "navigation": 1.0,
            "sensing": 0.6,
            "relay": 0.4,
            "payload": 0.7,
            "agility": 0.6,
            "manipulation": 1.0,
            "button_press": 1.0,
            "extinguisher_handling": 0.8,
            "obstacle_handling": 0.8,
        },
    }
    positions = {
        "carter_1": (-7.6, -8.0),
        "m20_1": (-3.0, 5.0),
        "m20_2": (3.0, 1.0),
        "scout_1": (6.0, 5.5),
    }
    robots = tuple(
        RobotSnapshot(
            name=name,
            position=positions[name],
            base_capabilities=profile,
            reliability={capability: 1.0 for capability in capabilities},
            equipment=(
                frozenset({"gshub"})
                if name == "carter_1"
                else frozenset(
                    {
                        "z1" if name == "m20_1" else "ur5",
                        "manipulator",
                        "gripper",
                    }
                )
            ),
            busy=False,
            current_task=None,
            current_load=0,
            safe=True,
        )
        for name, profile in profiles.items()
    )
    targets = (
        TargetSnapshot(
            "hazard_1_red_scout",
            (-4.5, -4.0, 0.0),
            True,
            "physical",
            (TaskType.NAVIGATE, TaskType.INSPECT),
        ),
        TargetSnapshot(
            "hazard_1_blue_relay",
            (-7.5, -4.0, 0.0),
            True,
            "physical",
            (TaskType.NAVIGATE, TaskType.ESTABLISH_RELAY),
        ),
        TargetSnapshot(
            "fire_extinguisher_pickup",
            (1.77, -9.38, 0.0),
            True,
            "physical",
            (TaskType.NAVIGATE, TaskType.PICK_EXTINGUISHER),
        ),
        TargetSnapshot(
            "hazard_1_yellow_delivery",
            (-7.2, -3.5, 0.0),
            True,
            "physical",
            (TaskType.NAVIGATE, TaskType.DELIVER_EXTINGUISHER),
        ),
        TargetSnapshot(
            "rescue_channel_button",
            (10.58, 1.0, 0.0),
            True,
            "physical",
            (TaskType.NAVIGATE, TaskType.PRESS_RESCUE_BUTTON),
        ),
        TargetSnapshot(
            "hold_current_position",
            (0.0, 0.0, 0.0),
            True,
            "virtual",
            (TaskType.WAIT,),
        ),
    )
    obstacle_targets = tuple(
        TargetSnapshot(
            obstacle.obstacle_id,
            obstacle.standoff_position,
            obstacle.active and not obstacle.removed,
            "obstacle",
            (TaskType.REMOVE_OBSTACLE,),
        )
        for obstacle in obstacles
    )
    return SymbolicWorldState(
        hazard_id=1,
        hazard_position=(-6.0, -4.0, 0.0),
        hazard_active=True,
        targets=targets + obstacle_targets,
        robots=robots,
        extinguisher_available=True,
        extinguisher_carrier=None,
        extinguisher_delivered=False,
        rescue_channel_open=False,
        completed_task_ids=(),
        failed_task_ids=(),
        recent_feedback=(),
        observations={},
        obstacles=tuple(obstacles),
    )


def valid_inspect_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "task_id": "inspect_fire",
        "task_type": "inspect",
        "description": "Inspect the active fire from the safe scout point",
        "target_ref": "hazard_1_red_scout",
        "priority": 1,
        "requirements": {
            "navigation": {"minimum": 0.6, "weight": 1.0, "hard": True},
            "sensing": {"minimum": 0.7, "weight": 0.9, "hard": False},
        },
        "prerequisites": [],
        "can_parallel": True,
        "estimated_duration_s": 30.0,
        "preferred_agent": None,
    }
    payload.update(overrides)
    return deepcopy(payload)


def valid_button_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "task_id": "open_rescue_channel",
        "task_type": "press_rescue_button",
        "description": "Press the rescue-channel button",
        "target_ref": "rescue_channel_button",
        "priority": 2,
        "requirements": {
            "navigation": {"minimum": 0.6, "weight": 1.0, "hard": True},
            "manipulation": {"minimum": 0.7, "weight": 1.0, "hard": True},
        },
        "prerequisites": [],
        "can_parallel": True,
        "estimated_duration_s": 45.0,
        "preferred_agent": None,
    }
    payload.update(overrides)
    return deepcopy(payload)


def valid_relay_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "task_id": "establish_relay",
        "task_type": "establish_relay",
        "description": "Establish a communication relay near the fire",
        "target_ref": "hazard_1_blue_relay",
        "priority": 1,
        "requirements": {
            "navigation": {"minimum": 0.6, "weight": 1.0, "hard": True},
            "relay": {"minimum": 0.8, "weight": 1.0, "hard": True},
        },
        "prerequisites": [],
        "can_parallel": True,
        "estimated_duration_s": 30.0,
        "preferred_agent": None,
    }
    payload.update(overrides)
    return deepcopy(payload)


def valid_remove_obstacle_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "task_id": "clear_runtime_obstacle",
        "task_type": "remove_obstacle",
        "description": "Move the runtime obstacle out of the blocked corridor",
        "target_ref": "runtime_obstacle_1",
        "priority": 1,
        "requirements": {},
        "prerequisites": [],
        "can_parallel": True,
        "estimated_duration_s": 90.0,
        "preferred_agent": None,
    }
    payload.update(overrides)
    return deepcopy(payload)


def tasks_from_payloads(world, *payloads):
    from TeamWeaver.eai_adapter.capability_ontology import validate_plan_payload

    return validate_plan_payload({"tasks": list(payloads)}, world)


def success(task_id: str, robot_name: str, capabilities=("navigation",)):
    from TeamWeaver.eai_adapter.task_models import (
        ExecutionFeedback,
        FailureKind,
        FeedbackOutcome,
    )

    return ExecutionFeedback(
        task_id=task_id,
        robot_name=robot_name,
        outcome=FeedbackOutcome.SUCCEEDED,
        reason="physical completion fact observed",
        failure_kind=FailureKind.NONE,
        relevant_capabilities=tuple(capabilities),
        world_changes={},
        timestamp_s=1.0,
    )


def failure(
    task_id: str,
    robot_name: str,
    failure_kind,
    capabilities=("navigation",),
    *,
    reason: str = "execution failed",
):
    from TeamWeaver.eai_adapter.task_models import ExecutionFeedback, FeedbackOutcome

    return ExecutionFeedback(
        task_id=task_id,
        robot_name=robot_name,
        outcome=FeedbackOutcome.FAILED,
        reason=reason,
        failure_kind=failure_kind,
        relevant_capabilities=tuple(capabilities),
        world_changes={},
        timestamp_s=2.0,
    )


def path_failure(task_id: str, robot_name: str):
    from TeamWeaver.eai_adapter.task_models import FailureKind

    return failure(
        task_id,
        robot_name,
        FailureKind.PATH_FAILURE,
        reason="global planner returned no path",
    )
