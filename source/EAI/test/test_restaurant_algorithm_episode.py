import pytest


def test_algorithm_pool_declares_heterogeneous_executable_members():
    from demo.benchmark import goal_restaurant_algorithm_episode as m

    assert m.ALGORITHM_POOL == (
        "restaurant_carter_1", "restaurant_carter_2", "restaurant_iris_1",
    )
    assert m.ALGORITHMS == ("teamweaver",)
    mobilities = {m.ROBOT_BINDINGS[public]["mobility"] for public in m.ALGORITHM_POOL}
    assert mobilities == {"ground", "aerial"}
    # Only the ground anchors have dispatchable drivers in the v1 executor.
    assert {
        definition["agent"] for definition in m.AGENT_PHYSICAL_LEGS.values()
    } == {"carter_1", "carter_2"}


def test_algorithm_selection_spawns_pool_members_at_public_anchors():
    from demo.benchmark import goal_restaurant_algorithm_episode as m
    from demo.restaurant_benchmark import config

    selection = m._algorithm_selection()
    assert selection["scene_key"] == config.BENCHMARK_SCENE_KEY
    by_type = {}
    for robot in selection["robots"]:
        by_type.setdefault(robot["type"], []).append(robot)
    assert len(by_type["carter"]) == 2
    assert len(by_type["iris"]) == 1
    assert by_type["iris"][0]["controller"]["cfg"] == "PEGASUS_IRIS_POSITION_CFG"
    spawns = [tuple(robot["spawn_pose"]["position"]) for robot in selection["robots"]]
    assert (8.325, 7.675, 3.0) in spawns


def test_derived_public_task_filters_pool_and_spawns_together():
    from demo.benchmark import goal_restaurant_algorithm_episode as m
    from demo.benchmark.goal_driven_contract import validate_goal_task_spec
    from demo.benchmark.goal_driven_tasks import build_goal_task_package

    task = m._algorithm_public_task(build_goal_task_package("restaurant"))
    report = validate_goal_task_spec(task)
    assert report["validated"] is True
    assert [row["id"] for row in task["candidate_robot_pool"]] == [
        "restaurant_carter_1", "restaurant_carter_2", "restaurant_iris_1",
    ]
    assert task["budgets"]["max_active_agents"] == 3
    assert set(task["public_world"]["robot_spawn_positions"]) == set(m.ALGORITHM_POOL)


def test_leg_matrix_resolves_unique_region_transitions_per_agent():
    from demo.benchmark import goal_restaurant_algorithm_episode as m
    from pathlib import Path

    routes = m._leg_routes(Path("tmp/restaurant_benchmark/products-candidate-v7"))
    assert set(routes) == set(m.AGENT_PHYSICAL_LEGS)
    for native, expected_transitions in (
        ("carter_1", {("restaurant_staging", "kitchen_service"),
                      ("kitchen_service", "restaurant_staging"),
                      ("restaurant_staging", "dining_service"),
                      ("dining_service", "restaurant_staging")}),
        ("carter_2", {("restaurant_staging", "kitchen_service"),
                      ("kitchen_service", "restaurant_staging"),
                      ("restaurant_staging", "dining_service"),
                      ("dining_service", "restaurant_staging")}),
    ):
        transitions = {(route["from_region_id"], route["to_region_id"])
                       for route in routes.values() if route["agent"] == native}
        assert transitions == expected_transitions
        for transition in expected_transitions:
            leg_id, route = m._leg_for(native, *transition, routes)
            assert leg_id and route["navigation_window_s"] >= 45.0


def test_mission_expansion_accepts_ground_assignments_within_budget():
    from demo.benchmark import goal_restaurant_algorithm_episode as m
    from demo.benchmark.goal_driven_tasks import build_goal_task_package
    from pathlib import Path

    task = m._algorithm_public_task(build_goal_task_package("restaurant"))
    routes = m._leg_routes(Path("tmp/restaurant_benchmark/products-candidate-v7"))
    cases = (
        {"kitchen_inspection": "restaurant_carter_1",
         "dining_inspection": "restaurant_carter_2",
         "readiness_relay": "restaurant_carter_1"},
        {"kitchen_inspection": "restaurant_carter_1",
         "dining_inspection": "restaurant_carter_1",
         "readiness_relay": "restaurant_carter_2"},
    )
    for assignment in cases:
        plan = m._expand_mission_plan(
            task, assignment, relay_agent=assignment["readiness_relay"],
            routes=routes, plan_id="test")
        last = plan["tasks"][-1]["execution"]
        assert last["start_s"] + last["duration_s"] <= task["budgets"]["simulation_s"]
        # The plan's route ids live on the task's public route graph while
        # the backend resolves agent-specific physical legs.
        for row in plan["tasks"]:
            for route_id in row["arguments"].get("route_ids", []):
                assert any(entry["id"] == route_id
                           for entry in task["public_world"]["routes"])


def test_mission_expansion_rejects_infeasible_aerial_dispatch():
    from demo.benchmark import goal_restaurant_algorithm_episode as m
    from demo.benchmark.goal_driven_tasks import build_goal_task_package
    from pathlib import Path

    task = m._algorithm_public_task(build_goal_task_package("restaurant"))
    routes = m._leg_routes(Path("tmp/restaurant_benchmark/products-candidate-v7"))
    for assignment in (
        {"kitchen_inspection": "restaurant_iris_1",
         "dining_inspection": "restaurant_carter_2",
         "readiness_relay": "restaurant_carter_1"},
        {"kitchen_inspection": "restaurant_carter_1",
         "dining_inspection": "restaurant_carter_2",
         "readiness_relay": "restaurant_iris_1"},
    ):
        with pytest.raises(ValueError):
            m._expand_mission_plan(
                task, assignment, relay_agent=assignment["readiness_relay"],
                routes=routes, plan_id="reject")


def test_request_contract_fails_closed_on_incomplete_requests():
    from demo.benchmark import goal_restaurant_algorithm_episode as m

    request = {
        "schema": "eai.restaurant.formal_goal_algorithm_episode_request",
        "version": 1, "run_id": "0" * 32, "episode_id": "restaurant-algo:" + "0" * 32,
        "algorithm": "teamweaver",
    }
    with pytest.raises(ValueError):
        m._validate_request(request)
