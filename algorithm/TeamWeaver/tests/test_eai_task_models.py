from __future__ import annotations

from dataclasses import FrozenInstanceError
import json

import pytest

from TeamWeaver.eai_adapter.task_models import (
    CapabilityRequirement,
    ExecutionFeedback,
    FailureKind,
    FeedbackOutcome,
    ObstacleSnapshot,
    RobotSnapshot,
    SemanticTask,
    SymbolicWorldState,
    TargetSnapshot,
    TaskStatus,
    TaskType,
)


def test_planning_contracts_are_immutable_and_json_ready():
    robot = RobotSnapshot(
        name="m20_1",
        position=(1.0, 2.0),
        base_capabilities={"navigation": 1.0, "manipulation": 0.8},
        reliability={"navigation": 0.9, "manipulation": 1.0},
        equipment=frozenset({"ur5"}),
        busy=False,
        current_task=None,
        current_load=0,
        safe=True,
    )
    task = SemanticTask(
        task_id="inspect_fire",
        task_type=TaskType.INSPECT,
        description="Inspect the active fire",
        target_ref="hazard_1_red_scout",
        priority=1,
        requirements={"sensing": CapabilityRequirement(0.6, 1.0, False)},
        prerequisites=(),
        can_parallel=True,
        estimated_duration_s=20.0,
        preferred_agent=None,
    )
    target = TargetSnapshot(
        "hazard_1_red_scout",
        (-4.5, -4.0, 0.0),
        True,
        "physical",
        (TaskType.NAVIGATE, TaskType.INSPECT),
    )
    world = SymbolicWorldState(
        hazard_id=1,
        hazard_position=(-6.0, -4.0, 0.0),
        hazard_active=True,
        targets=(target,),
        robots=(robot,),
        extinguisher_available=True,
        extinguisher_carrier=None,
        extinguisher_delivered=False,
        rescue_channel_open=False,
        completed_task_ids=(),
        failed_task_ids=(),
        recent_feedback=(),
        observations={},
    )

    assert robot.effective_capabilities["navigation"] == pytest.approx(0.9)
    assert world.to_payload()["robots"][0]["equipment"] == ["ur5"]
    assert world.to_payload()["targets"][0]["compatible_task_types"] == [
        "navigate",
        "inspect",
    ]
    assert task.to_payload()["task_type"] == "inspect"
    json.dumps(world.to_payload())
    with pytest.raises(FrozenInstanceError):
        robot.busy = True
    with pytest.raises(TypeError):
        robot.base_capabilities["navigation"] = 0.0


def test_feedback_contract_preserves_failure_kind_capabilities_and_time():
    feedback = ExecutionFeedback(
        task_id="collect_extinguisher",
        robot_name="m20_1",
        outcome=FeedbackOutcome.FAILED,
        reason="global planner returned no path",
        failure_kind=FailureKind.PATH_FAILURE,
        relevant_capabilities=("navigation",),
        world_changes={},
        timestamp_s=12.5,
    )

    assert feedback.to_payload() == {
        "task_id": "collect_extinguisher",
        "robot_name": "m20_1",
        "outcome": "failed",
        "reason": "global planner returned no path",
        "failure_kind": "path_failure",
        "relevant_capabilities": ["navigation"],
        "world_changes": {},
        "timestamp_s": 12.5,
    }
    assert TaskStatus.NAVIGATING.value == "navigating"
    assert TaskStatus.TIMED_OUT.value == "timed_out"


def test_world_lookup_rejects_unknown_names(factory_world):
    assert factory_world.robot_by_name("carter_1").name == "carter_1"
    assert factory_world.target_by_ref("hold_current_position").kind == "virtual"
    with pytest.raises(KeyError):
        factory_world.robot_by_name("missing")
    with pytest.raises(KeyError):
        factory_world.target_by_ref("missing")


def test_obstacle_and_preemption_contracts_are_immutable_and_json_ready(
    factory_world,
):
    obstacle = ObstacleSnapshot(
        obstacle_id="runtime_obstacle_1",
        position=(-5.0, -2.0, 0.25),
        dimensions=(2.3, 0.3, 0.5),
        active=True,
        removed=False,
        blocking_task_id="inspect_fire",
        blocked_robot="carter_1",
        standoff_position=(-5.0, -0.8, 0.0),
        drag_target_position=(-5.0, 1.7, 0.25),
        removal_attempts=0,
    )
    robot = RobotSnapshot(
        name="m20_1",
        position=(1.0, 2.0),
        base_capabilities={"navigation": 1.0},
        reliability={"navigation": 1.0},
        equipment=frozenset({"manipulator", "gripper"}),
        busy=True,
        current_task="inspect_fire",
        current_load=1,
        safe=True,
        current_stage="navigating",
        preemptible=True,
    )
    world = SymbolicWorldState(
        hazard_id=factory_world.hazard_id,
        hazard_position=factory_world.hazard_position,
        hazard_active=True,
        targets=factory_world.targets,
        robots=(robot,),
        extinguisher_available=True,
        extinguisher_carrier=None,
        extinguisher_delivered=False,
        rescue_channel_open=False,
        completed_task_ids=(),
        failed_task_ids=(),
        recent_feedback=(),
        observations={},
        obstacles=(obstacle,),
        extinguisher_delivered_by=None,
    )

    assert world.active_obstacle() is obstacle
    assert world.to_payload()["obstacles"][0]["blocking_task_id"] == "inspect_fire"
    assert world.to_payload()["robots"][0]["preemptible"] is True
    assert TaskType.REMOVE_OBSTACLE.value == "remove_obstacle"
    json.dumps(world.to_payload())


@pytest.mark.parametrize(
    "overrides",
    [
        {"obstacle_id": "  "},
        {"dimensions": (2.3, 0.0, 0.5)},
        {"position": (1.0, 2.0)},
        {"standoff_position": (1.0, 2.0, float("inf"))},
        {"removal_attempts": True},
        {"removal_attempts": -1},
        {"active": True, "removed": True},
    ],
)
def test_obstacle_contract_rejects_invalid_state(overrides):
    values = {
        "obstacle_id": "runtime_obstacle_1",
        "position": (-5.0, -2.0, 0.25),
        "dimensions": (2.3, 0.3, 0.5),
        "active": True,
        "removed": False,
        "blocking_task_id": "inspect_fire",
        "blocked_robot": "carter_1",
        "standoff_position": (-5.0, -0.8, 0.0),
        "drag_target_position": (-5.0, 1.7, 0.25),
        "removal_attempts": 0,
    }
    values.update(overrides)

    with pytest.raises(ValueError):
        ObstacleSnapshot(**values)


def test_world_rejects_multiple_active_obstacles(factory_world):
    obstacle = ObstacleSnapshot(
        "runtime_obstacle_1",
        (-5.0, -2.0, 0.25),
        (2.3, 0.3, 0.5),
        True,
        False,
        "inspect_fire",
        "carter_1",
        (-5.0, -0.8, 0.0),
        (-5.0, 1.7, 0.25),
    )
    world = SymbolicWorldState(
        **{
            **factory_world.__dict__,
            "obstacles": (obstacle, obstacle),
        }
    )

    with pytest.raises(ValueError, match="multiple active obstacles"):
        world.active_obstacle()
