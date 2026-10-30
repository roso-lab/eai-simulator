import pytest


def _robot(name, x, y, **capabilities):
    from TeamWeaver.eai_adapter.dynamic_allocator import RobotState

    return RobotState(
        name=name,
        position=(x, y),
        capabilities={"navigation": 1.0, **capabilities},
    )


def test_allocator_assigns_each_robot_and_task_once():
    from TeamWeaver.eai_adapter.dynamic_allocator import DynamicFactoryAllocator
    from TeamWeaver.eai_adapter.factory_tasks import build_factory_task_specs

    robots = [
        _robot("carter_1", -7.6, -8.0, sensing=1.0, relay=1.0),
        _robot("m20_1", -3.0, 5.0, payload=1.0, manipulation=0.8),
        _robot("m20_2", 3.0, 1.0, payload=1.0, manipulation=0.8),
        _robot("scout_1", 6.0, 5.5, agility=1.0, manipulation=1.0),
    ]

    result = DynamicFactoryAllocator(prefer_miqp=False).allocate(
        robots,
        build_factory_task_specs(1),
    )

    assert result.solver == "hungarian"
    assert len(result.assignments) == 4
    assert len({item.robot.name for item in result.assignments}) == 4
    assert len({item.task.task_id for item in result.assignments}) == 4


def test_allocator_changes_assignment_when_positions_change():
    from TeamWeaver.eai_adapter.dynamic_allocator import DynamicFactoryAllocator
    from TeamWeaver.eai_adapter.factory_tasks import build_factory_task_specs

    allocator = DynamicFactoryAllocator(prefer_miqp=False, distance_weight=10.0)
    tasks = build_factory_task_specs(1)
    near_red = [
        _robot("a", -4.5, -4.0),
        _robot("b", 10.58, 1.0),
        _robot("c", 1.77, -9.38),
        _robot("d", -7.5, -4.0),
    ]
    swapped = [
        _robot("a", 10.58, 1.0),
        _robot("b", -4.5, -4.0),
        _robot("c", 1.77, -9.38),
        _robot("d", -7.5, -4.0),
    ]

    first = allocator.allocate(near_red, tasks).by_robot()
    second = allocator.allocate(swapped, tasks).by_robot()

    assert first["a"].task_id == "red"
    assert second["b"].task_id == "red"


def test_allocator_tie_breaker_prefers_matching_input_order():
    from TeamWeaver.eai_adapter.dynamic_allocator import DynamicFactoryAllocator
    from TeamWeaver.eai_adapter.factory_tasks import build_factory_task_specs

    allocator = DynamicFactoryAllocator(
        prefer_miqp=False,
        distance_weight=0.0,
        capability_weight=0.0,
        load_weight=0.0,
    )
    robots = [_robot(str(index), 0.0, 0.0) for index in range(4)]

    cost_matrix, _ = allocator._build_cost_matrix(
        robots,
        build_factory_task_specs(1),
    )

    diagonal_cost = sum(cost_matrix[index, index] for index in range(4))
    reverse_cost = sum(cost_matrix[index, 3 - index] for index in range(4))
    assert diagonal_cost < reverse_cost


def test_allocator_rejects_too_few_robots():
    from TeamWeaver.eai_adapter.dynamic_allocator import AllocationError, DynamicFactoryAllocator
    from TeamWeaver.eai_adapter.factory_tasks import build_factory_task_specs

    with pytest.raises(AllocationError, match="requires 4 available robots"):
        DynamicFactoryAllocator(prefer_miqp=False).allocate(
            [_robot("only", 0.0, 0.0)],
            build_factory_task_specs(1),
        )


def test_allocator_uses_injected_miqp_and_falls_back_on_error():
    from TeamWeaver.eai_adapter.dynamic_allocator import DynamicFactoryAllocator
    from TeamWeaver.eai_adapter.factory_tasks import build_factory_task_specs

    robots = [_robot(str(index), float(index), 0.0) for index in range(4)]
    tasks = build_factory_task_specs(1)
    used = DynamicFactoryAllocator(
        miqp_solver=lambda _cost, _feasible, _loads: [
            (0, 0),
            (1, 1),
            (2, 2),
            (3, 3),
        ]
    ).allocate(robots, tasks)
    fallback = DynamicFactoryAllocator(
        miqp_solver=lambda *_args: (_ for _ in ()).throw(RuntimeError("no license"))
    ).allocate(robots, tasks)

    assert used.solver == "miqp"
    assert fallback.solver == "hungarian"
    assert fallback.fallback_reason == "no license"
    assert len(fallback.assignments) == 4
