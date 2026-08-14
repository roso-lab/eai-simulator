from __future__ import annotations

from dataclasses import replace

import pytest

from TeamWeaver.tests.eai_test_support import (
    make_factory_world,
    tasks_from_payloads,
    valid_button_payload,
    valid_inspect_payload,
    valid_relay_payload,
    valid_remove_obstacle_payload,
)
from TeamWeaver.eai_adapter.task_models import ObstacleSnapshot, RobotSnapshot


def _four_inspect_tasks(world):
    return tasks_from_payloads(
        world,
        valid_inspect_payload(task_id="p1", priority=1),
        valid_inspect_payload(task_id="p2", priority=2),
        valid_inspect_payload(task_id="p3", priority=3),
        valid_inspect_payload(task_id="p5", priority=5),
    )


def test_three_robots_defer_lowest_priority_of_four_tasks(factory_world):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator

    world = replace(factory_world, robots=factory_world.robots[:3])
    tasks = _four_inspect_tasks(world)
    result = DynamicMIQPAllocator(prefer_miqp=False).allocate(world, tasks, {})

    assert len(result.assignments) == 3
    assert result.deferred_task_ids == ("p5",)
    assert result.solver == "hungarian"
    assert set(result.assignment_by_task) == {"p1", "p2", "p3"}


def test_ur5_equipment_is_a_hard_assignment_constraint(factory_world):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator

    task = tasks_from_payloads(factory_world, valid_button_payload())[0]
    result = DynamicMIQPAllocator(prefer_miqp=False).allocate(
        factory_world, (task,), {}
    )
    assert result.assignments[0].robot_name != "carter_1"


@pytest.mark.parametrize("prefer_miqp", [False, True])
def test_delivery_never_reassigns_extinguisher_away_from_carrier(
    factory_world,
    prefer_miqp,
):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator

    robots = tuple(
        replace(
            robot,
            reliability={name: 0.8 for name in robot.reliability},
        )
        if robot.name == "m20_1"
        else robot
        for robot in factory_world.robots
    )
    world = replace(
        factory_world,
        robots=robots,
        extinguisher_available=False,
        extinguisher_carrier="m20_1",
    )
    delivery_payload = valid_button_payload(
        task_id="deliver_extinguisher",
        task_type="deliver_extinguisher",
        description="Deliver the carried extinguisher to the fire",
        target_ref="hazard_1_yellow_delivery",
    )
    task = tasks_from_payloads(world, delivery_payload)[0]

    result = DynamicMIQPAllocator(prefer_miqp=prefer_miqp).allocate(
        world,
        (task,),
        {},
    )

    assert result.assignments == ()
    assert result.deferred_task_ids == ("deliver_extinguisher",)
    assert result.hard_infeasible_task_ids == ("deliver_extinguisher",)


def test_pair_cost_uses_soft_gap_preference_and_live_distance(factory_world):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator

    task = tasks_from_payloads(
        factory_world,
        valid_inspect_payload(
            requirements={
                "navigation": {"minimum": 0.6, "weight": 1.0, "hard": True},
                "sensing": {"minimum": 1.0, "weight": 1.0, "hard": False},
            }
        ),
    )[0]
    allocator = DynamicMIQPAllocator(prefer_miqp=False)
    carter = factory_world.robot_by_name("carter_1")
    m20 = factory_world.robot_by_name("m20_1")

    assert allocator.soft_capability_gap(carter, task) == 0.0
    assert allocator.soft_capability_gap(m20, task) == pytest.approx(0.3)
    assert allocator.preferred_agent_penalty(carter, task) == 0.0
    preferred = replace(task, preferred_agent="carter_1")
    assert allocator.preferred_agent_penalty(m20, preferred) == 1.0
    assert allocator.distance(m20, task, factory_world) == pytest.approx(
        ((-3.0 + 4.5) ** 2 + (5.0 + 4.0) ** 2) ** 0.5
    )


def test_sub_meter_distance_uses_actual_max_pair_distance(factory_world):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator

    carter = replace(
        factory_world.robot_by_name("carter_1"),
        position=(-4.0, -4.0),
    )
    world = replace(factory_world, robots=(carter,))
    task = tasks_from_payloads(world, valid_inspect_payload())[0]

    result = DynamicMIQPAllocator(prefer_miqp=False).allocate(world, (task,), {})

    assert result.assignments[0].pair_cost == pytest.approx(1.0)


def test_previous_assignment_prevents_unnecessary_miqp_transition(factory_world):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator

    task = tasks_from_payloads(factory_world, valid_inspect_payload())[0]
    result = DynamicMIQPAllocator(prefer_miqp=True).allocate(
        factory_world,
        (task,),
        {task.task_id: "m20_1"},
    )

    assert result.solver == "miqp"
    assert result.assignments[0].robot_name == "m20_1"
    assert result.objective.transition == 0.0
    assert result.changed_task_ids == ()


def test_miqp_objective_matches_breakdown_when_previous_robot_is_busy(
    factory_world, monkeypatch
):
    import gurobipy as gp

    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator

    captured_models = []
    original_model = gp.Model

    def tracking_model(*args, **kwargs):
        model = original_model(*args, **kwargs)
        captured_models.append(model)
        return model

    monkeypatch.setattr(gp, "Model", tracking_model)
    robots = tuple(
        replace(robot, busy=True) if robot.name == "carter_1" else robot
        for robot in factory_world.robots
    )
    world = replace(factory_world, robots=robots)
    task = tasks_from_payloads(world, valid_inspect_payload())[0]

    result = DynamicMIQPAllocator(prefer_miqp=True).allocate(
        world,
        (task,),
        {task.task_id: "carter_1"},
    )

    assert result.solver == "miqp"
    assert result.objective.transition == 4.0
    assert captured_models[0].ObjVal == pytest.approx(result.total_cost)


def test_gurobi_failure_uses_hungarian_without_creating_tasks(factory_world):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator

    tasks = _four_inspect_tasks(factory_world)
    allocator = DynamicMIQPAllocator(
        prefer_miqp=True,
        miqp_solver=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("license")
        ),
    )
    result = allocator.allocate(factory_world, tasks, {})

    assert result.solver == "hungarian"
    assert result.fallback_reason == "license"
    known = {task.task_id for task in tasks}
    assert set(result.assignment_by_task).issubset(known)
    assert set(result.deferred_task_ids).issubset(known)


def test_all_hard_infeasible_tasks_are_deferred(factory_world):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator

    relay = tasks_from_payloads(factory_world, valid_relay_payload())[0]
    no_carter = replace(
        factory_world,
        robots=tuple(robot for robot in factory_world.robots if robot.name != "carter_1"),
    )
    result = DynamicMIQPAllocator(prefer_miqp=False).allocate(no_carter, (relay,), {})

    assert result.assignments == ()
    assert result.deferred_task_ids == ("establish_relay",)
    assert result.hard_infeasible_task_ids == ("establish_relay",)


def test_busy_and_unsafe_robots_are_never_selected(factory_world):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator

    robots = tuple(
        replace(robot, busy=True) if robot.name == "carter_1" else robot
        for robot in factory_world.robots
    )
    robots = tuple(
        replace(robot, safe=False) if robot.name == "m20_1" else robot
        for robot in robots
    )
    world = replace(factory_world, robots=robots)
    task = tasks_from_payloads(world, valid_inspect_payload())[0]
    result = DynamicMIQPAllocator(prefer_miqp=False).allocate(world, (task,), {})

    assert result.assignments[0].robot_name not in {"carter_1", "m20_1"}


def test_system_continuation_required_agent_is_a_hard_constraint(factory_world):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator

    task = replace(
        tasks_from_payloads(factory_world, valid_inspect_payload())[0],
        continuation_of="prior_remote_task",
        required_agent="m20_1",
    )
    result = DynamicMIQPAllocator(prefer_miqp=False).allocate(
        factory_world,
        (task,),
        {},
    )

    assert result.assignments[0].robot_name == "m20_1"


@pytest.mark.parametrize("prefer_miqp", [False, True])
def test_ready_continuation_reserves_its_required_robot(factory_world, prefer_miqp):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator

    world = replace(
        factory_world,
        robots=(factory_world.robot_by_name("m20_1"),),
    )
    normal, continuation = tasks_from_payloads(
        world,
        valid_inspect_payload(task_id="normal", priority=1),
        valid_inspect_payload(
            task_id="return_to_fire",
            task_type="navigate",
            description="Return the completed task owner to the fire",
            requirements={
                "navigation": {"minimum": 0.6, "weight": 1.0, "hard": True}
            },
            priority=5,
        ),
    )
    continuation = replace(
        continuation,
        continuation_of="completed_remote_task",
        required_agent="m20_1",
    )

    result = DynamicMIQPAllocator(prefer_miqp=prefer_miqp).allocate(
        world,
        (normal, continuation),
        {},
    )

    assert tuple(result.assignment_by_task) == ("return_to_fire",)
    assert result.deferred_task_ids == ("normal",)


def test_soft_gap_reports_relaxation_and_objective_sum(factory_world):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator

    m20_only = replace(
        factory_world,
        robots=(factory_world.robot_by_name("m20_1"),),
    )
    task = tasks_from_payloads(
        m20_only,
        valid_inspect_payload(
            requirements={
                "navigation": {"minimum": 0.6, "weight": 1.0, "hard": True},
                "sensing": {"minimum": 1.0, "weight": 1.0, "hard": False},
            }
        ),
    )[0]
    result = DynamicMIQPAllocator(prefer_miqp=False).allocate(m20_only, (task,), {})

    assert result.relaxed_task_ids == ("inspect_fire",)
    assert result.assignments[0].relaxed is True
    assert result.total_cost == pytest.approx(result.objective.total)


def test_reliability_changes_winner_for_otherwise_equal_robots(factory_world):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator

    first = RobotSnapshot(
        name="first",
        position=(-4.5, -4.0),
        base_capabilities={"navigation": 1.0, "sensing": 1.0},
        reliability={"navigation": 1.0, "sensing": 0.5},
        equipment=frozenset(),
        busy=False,
        current_task=None,
        current_load=0,
        safe=True,
    )
    second = replace(
        first,
        name="second",
        reliability={"navigation": 1.0, "sensing": 1.0},
    )
    world = replace(factory_world, robots=(first, second))
    task = tasks_from_payloads(
        world,
        valid_inspect_payload(
            requirements={
                "navigation": {"minimum": 0.6, "weight": 1.0, "hard": True},
                "sensing": {"minimum": 1.0, "weight": 1.0, "hard": False},
            }
        ),
    )[0]

    result = DynamicMIQPAllocator(prefer_miqp=False).allocate(world, (task,), {})
    assert result.assignments[0].robot_name == "second"


def test_empty_frontier_and_virtual_wait_are_supported(factory_world):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator

    allocator = DynamicMIQPAllocator(prefer_miqp=False)
    empty = allocator.allocate(factory_world, (), {})
    assert empty.assignments == ()
    assert empty.solver == "none"

    wait_payload = {
        "task_id": "pause",
        "task_type": "wait",
        "description": "Hold safely",
        "target_ref": "hold_current_position",
        "priority": 3,
        "requirements": {},
        "prerequisites": [],
        "can_parallel": True,
        "estimated_duration_s": 5.0,
        "preferred_agent": None,
    }
    wait_task = tasks_from_payloads(factory_world, wait_payload)[0]
    assert allocator.distance(
        factory_world.robot_by_name("m20_1"), wait_task, factory_world
    ) == 0.0


def _preemption_world():
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
    world = make_factory_world(obstacles=(obstacle,))
    robots = tuple(
        replace(
            robot,
            position=(-5.0, -1.0),
            busy=True,
            current_task="inspect_fire",
            current_stage="navigating",
            preemptible=True,
            current_load=1,
        )
        if robot.name == "m20_1"
        else replace(robot, position=(20.0, 20.0), current_load=20)
        if robot.name == "m20_2"
        else replace(robot, safe=False)
        if robot.name == "scout_1"
        else robot
        for robot in world.robots
    )
    return replace(world, robots=robots)


@pytest.mark.parametrize("prefer_miqp", [False, True])
def test_emergency_allocator_can_preempt_navigation_with_explicit_cost(prefer_miqp):
    from TeamWeaver.eai_adapter.capability_ontology import validate_task_payload
    from TeamWeaver.eai_adapter.dynamic_miqp import (
        PREEMPTION_COST,
        DynamicMIQPAllocator,
    )

    world = _preemption_world()
    task = validate_task_payload(valid_remove_obstacle_payload(), world)

    result = DynamicMIQPAllocator(prefer_miqp=prefer_miqp).allocate(
        world,
        (task,),
        {},
    )

    assignment = result.assignment_by_task["clear_runtime_obstacle"]
    assert assignment.robot_name == "m20_1"
    assert assignment.preempted_task_id == "inspect_fire"
    assert result.objective.preemption == pytest.approx(PREEMPTION_COST)
    assert result.total_cost == pytest.approx(result.objective.total)


def test_emergency_allocator_rejects_blocked_nonpreemptible_and_unequipped_robots():
    from TeamWeaver.eai_adapter.capability_ontology import validate_task_payload
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator

    world = _preemption_world()
    obstacle = replace(world.active_obstacle(), blocked_robot="m20_1")
    robots = (
        replace(
            world.robot_by_name("m20_1"),
            busy=False,
            current_task=None,
            current_stage=None,
            preemptible=False,
        ),
        replace(
            world.robot_by_name("m20_2"),
            busy=True,
            current_task="inspect_other",
            current_stage="navigating",
            preemptible=False,
        ),
        replace(world.robot_by_name("carter_1"), safe=True),
    )
    world = replace(world, robots=robots, obstacles=(obstacle,))
    task = validate_task_payload(valid_remove_obstacle_payload(), world)

    result = DynamicMIQPAllocator(prefer_miqp=False).allocate(world, (task,), {})

    assert result.assignments == ()
    assert result.hard_infeasible_task_ids == ("clear_runtime_obstacle",)


def test_blocked_robot_cannot_receive_any_task_while_obstacle_active():
    """blocked_robot must not be assigned ANY task until the obstacle is cleared."""
    from TeamWeaver.eai_adapter.capability_ontology import validate_task_payload
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator

    # World with an active obstacle blocking m20_1.
    obstacle = ObstacleSnapshot(
        "runtime_obstacle_1",
        (-5.0, -2.0, 0.25),
        (2.3, 0.3, 0.5),
        True,
        False,
        "inspect_fire",
        "m20_1",
        (-5.0, -0.8, 0.0),
        (-5.0, 1.7, 0.25),
    )
    world = make_factory_world(obstacles=(obstacle,))
    # m20_1 is idle (not busy) — its task was suspended by the obstacle.
    robots = tuple(
        replace(robot, busy=False, current_task=None, current_stage=None)
        if robot.name == "m20_1"
        else robot
        for robot in world.robots
    )
    world = replace(world, robots=robots)

    inspect_task = validate_task_payload(valid_inspect_payload(), world)

    # With active obstacle, m20_1 (blocked_robot) must NOT be assigned.
    result = DynamicMIQPAllocator(prefer_miqp=False).allocate(
        world, (inspect_task,), {}
    )
    assignment = result.assignment_by_task.get("inspect_fire")
    assert assignment is None or assignment.robot_name != "m20_1", (
        f"blocked_robot m20_1 should not receive inspect_fire, got {assignment}"
    )

    # Without the obstacle, m20_1 SHOULD be eligible (task no longer hard-infeasible).
    world_no_obstacle = replace(world, obstacles=())
    result_no_obstacle = DynamicMIQPAllocator(prefer_miqp=False).allocate(
        world_no_obstacle, (inspect_task,), {}
    )
    assert "inspect_fire" not in result_no_obstacle.hard_infeasible_task_ids, (
        "inspect_fire should be feasible for at least one robot when no obstacle is active"
    )
