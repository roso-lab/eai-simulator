from __future__ import annotations

from dataclasses import replace
import math

import pytest

from TeamWeaver.tests.eai_test_support import (
    make_factory_world,
    valid_button_payload,
    valid_inspect_payload,
    valid_remove_obstacle_payload,
)
from TeamWeaver.eai_adapter.capability_ontology import (
    PlanValidationError,
    TaskValidationError,
    hard_feasible,
    required_equipment,
    validate_plan_payload,
    validate_task_payload,
)
from TeamWeaver.eai_adapter.task_models import ObstacleSnapshot, TargetSnapshot, TaskType


def _delivery_payload(duration_s: float = 120.0):
    return valid_inspect_payload(
        task_id="deliver_extinguisher",
        task_type="deliver_extinguisher",
        description="Deliver the extinguisher to the fire response point",
        target_ref="hazard_1_yellow_delivery",
        requirements={},
        prerequisites=[],
        can_parallel=False,
        estimated_duration_s=duration_s,
    )


def test_validation_merges_hardware_invariants_without_weakening_them(factory_world):
    raw = {
        "task_id": "collect_extinguisher",
        "task_type": "pick_extinguisher",
        "description": "Collect the extinguisher",
        "target_ref": "fire_extinguisher_pickup",
        "priority": 2,
        "requirements": {
            "manipulation": {"minimum": 0.1, "weight": 0.2, "hard": False}
        },
        "prerequisites": [],
        "can_parallel": False,
        "estimated_duration_s": 45,
        "preferred_agent": None,
    }

    task = validate_task_payload(raw, factory_world)

    assert task.requirements["manipulation"].minimum == 0.7
    assert task.requirements["manipulation"].hard is True
    assert task.requirements["payload"].minimum == 0.7
    assert task.requirements["extinguisher_handling"].hard is True
    assert required_equipment(task.task_type) == frozenset(
        {"manipulator", "gripper"}
    )


def test_delivery_duration_has_physical_floor_from_pickup_without_carrier(
    factory_world,
):
    task = validate_task_payload(_delivery_payload(), factory_world)
    pickup = factory_world.target_by_ref("fire_extinguisher_pickup").position
    delivery = factory_world.target_by_ref("hazard_1_yellow_delivery").position
    expected_floor = 30.0 + 1.25 * math.dist(pickup[:2], delivery[:2]) / 0.05

    assert task.estimated_duration_s == pytest.approx(expected_floor)


def test_delivery_duration_uses_carrier_live_position(factory_world):
    world = replace(factory_world, extinguisher_carrier="m20_1")
    task = validate_task_payload(_delivery_payload(), world)
    carrier = world.robot_by_name("m20_1")
    delivery = world.target_by_ref("hazard_1_yellow_delivery").position
    expected_floor = 30.0 + 1.25 * math.dist(carrier.position, delivery[:2]) / 0.05

    assert task.estimated_duration_s == pytest.approx(expected_floor)


def test_delivery_duration_preserves_longer_llm_estimate(factory_world):
    task = validate_task_payload(_delivery_payload(duration_s=500.0), factory_world)

    assert task.estimated_duration_s == 500.0


def test_delivery_duration_physical_floor_respects_schema_limit(factory_world):
    targets = tuple(
        replace(target, position=(100.0, 100.0, 0.0))
        if target.ref == "hazard_1_yellow_delivery"
        else target
        for target in factory_world.targets
    )
    world = replace(factory_world, targets=targets)

    task = validate_task_payload(_delivery_payload(), world)

    assert task.estimated_duration_s == 600.0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("priority", 0),
        ("priority", 6),
        ("estimated_duration_s", 0),
        ("estimated_duration_s", 601),
        ("estimated_duration_s", math.inf),
        ("priority", True),
    ],
)
def test_validation_rejects_out_of_range_or_boolean_numbers(
    factory_world, field, value
):
    payload = valid_inspect_payload()
    payload[field] = value
    with pytest.raises(TaskValidationError):
        validate_task_payload(payload, factory_world)


def test_plan_validation_aggregates_unknown_target_and_cycle(factory_world):
    first = valid_inspect_payload(task_id="a", prerequisites=["b"])
    second = valid_inspect_payload(task_id="b", prerequisites=["a"])
    second["target_ref"] = "missing_target"

    with pytest.raises(PlanValidationError) as error:
        validate_plan_payload({"tasks": [first, second]}, factory_world)

    assert any("unknown target" in item for item in error.value.errors)
    assert any("cycle" in item for item in error.value.errors)


def test_target_kind_and_task_type_must_be_compatible(factory_world):
    payload = valid_button_payload(target_ref="hold_current_position")
    with pytest.raises(TaskValidationError, match="not compatible"):
        validate_task_payload(payload, factory_world)


def test_unknown_capability_and_preferred_agent_are_rejected(factory_world):
    payload = valid_inspect_payload(preferred_agent="robot_99")
    payload["requirements"]["vision_magic"] = {
        "minimum": 0.5,
        "weight": 1.0,
        "hard": False,
    }
    with pytest.raises(TaskValidationError) as error:
        validate_task_payload(payload, factory_world)
    assert "unknown capability" in str(error.value)
    assert "unknown preferred_agent" in str(error.value)


def test_hardware_feasibility_accepts_any_manipulator_model(factory_world):
    task = validate_task_payload(valid_button_payload(), factory_world)
    carter = factory_world.robot_by_name("carter_1")
    scout = factory_world.robot_by_name("scout_1")
    z1 = factory_world.robot_by_name("m20_1")

    assert hard_feasible(carter, task) is False
    assert hard_feasible(scout, task) is True
    assert hard_feasible(z1, task) is True


def test_plan_accepts_prerequisite_already_completed_in_world(factory_world):
    from dataclasses import replace

    completed_world = replace(factory_world, completed_task_ids=("inspect_fire",))
    task = valid_button_payload(prerequisites=["inspect_fire"])
    result = validate_plan_payload({"tasks": [task]}, completed_world)
    assert result[0].prerequisites == ("inspect_fire",)


def test_plan_rejects_returned_task_id_already_completed_in_world(factory_world):
    completed_world = replace(
        factory_world,
        completed_task_ids=("open_rescue_channel",),
    )

    with pytest.raises(PlanValidationError, match="already completed"):
        validate_plan_payload(
            {"tasks": [valid_button_payload()]},
            completed_world,
        )


def test_plan_requires_exact_top_level_tasks_key(factory_world):
    with pytest.raises(PlanValidationError, match="exactly one top-level"):
        validate_plan_payload(
            {"tasks": [valid_inspect_payload()], "explanation": "extra"},
            factory_world,
        )


def test_plan_rejects_task_ids_that_duplicate_after_normalization(factory_world):
    first = valid_inspect_payload(task_id="duplicate")
    second = valid_inspect_payload(task_id=" duplicate ")
    with pytest.raises(PlanValidationError, match="duplicate task ids"):
        validate_plan_payload({"tasks": [first, second]}, factory_world)


def test_remove_obstacle_merges_hard_requirements_and_equipment(factory_world):
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
    obstacle_target = TargetSnapshot(
        obstacle.obstacle_id,
        obstacle.standoff_position,
        True,
        "obstacle",
        (TaskType.REMOVE_OBSTACLE,),
    )
    world = replace(
        factory_world,
        targets=factory_world.targets + (obstacle_target,),
        obstacles=(obstacle,),
    )

    task = validate_task_payload(valid_remove_obstacle_payload(), world)

    assert task.requirements["obstacle_handling"].hard is True
    assert required_equipment(task.task_type) == frozenset(
        {"manipulator", "gripper"}
    )


def _world_with_blocking_obstacle():
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
    return make_factory_world(obstacles=(obstacle,))


def _valid_emergency_plan():
    return {
        "tasks": [
            valid_remove_obstacle_payload(),
            valid_inspect_payload(prerequisites=["clear_runtime_obstacle"]),
        ]
    }


def test_active_obstacle_plan_requires_exact_removal_dependency():
    tasks = validate_plan_payload(
        _valid_emergency_plan(),
        _world_with_blocking_obstacle(),
    )

    assert [task.task_id for task in tasks] == [
        "clear_runtime_obstacle",
        "inspect_fire",
    ]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda plan: plan["tasks"].pop(0),
            "exactly one obstacle removal task",
        ),
        (
            lambda plan: plan["tasks"].insert(
                1,
                valid_remove_obstacle_payload(task_id="clear_runtime_obstacle_2"),
            ),
            "exactly one obstacle removal task",
        ),
        (
            lambda plan: plan["tasks"][0].update(target_ref="other_obstacle"),
            "must target active obstacle",
        ),
        (
            lambda plan: plan["tasks"][0].update(priority=2),
            "must have priority 1",
        ),
        (
            lambda plan: plan["tasks"][0].update(can_parallel=False),
            "must allow independent parallel work",
        ),
        (
            lambda plan: plan["tasks"][0].update(preferred_agent="m20_1"),
            "cannot prefer a robot",
        ),
        (
            lambda plan: plan["tasks"][1].update(prerequisites=[]),
            "blocked task must depend on obstacle removal",
        ),
    ],
)
def test_active_obstacle_plan_rejects_invalid_emergency_contract(mutate, message):
    world = _world_with_blocking_obstacle()
    plan = _valid_emergency_plan()
    if message == "must target active obstacle":
        plan_world_target = TargetSnapshot(
            "other_obstacle",
            (-3.0, -2.0, 0.0),
            True,
            "obstacle",
            (TaskType.REMOVE_OBSTACLE,),
        )
        world = replace(world, targets=world.targets + (plan_world_target,))
    mutate(plan)

    with pytest.raises(PlanValidationError, match=message):
        validate_plan_payload(plan, world)


def test_remove_task_is_rejected_without_an_active_obstacle(factory_world):
    obstacle_target = TargetSnapshot(
        "runtime_obstacle_1",
        (-5.0, -0.8, 0.0),
        True,
        "obstacle",
        (TaskType.REMOVE_OBSTACLE,),
    )
    world = replace(factory_world, targets=factory_world.targets + (obstacle_target,))
    with pytest.raises(PlanValidationError, match="requires an active blocking obstacle"):
        validate_plan_payload(
            {"tasks": [valid_remove_obstacle_payload()]},
            world,
        )
