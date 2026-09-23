"""Offline House formal-episode driver: v2 deployment, witness plan, GoalSession."""

from pathlib import Path

from demo.benchmark.goal_constraints import validate_plan_constraints
from demo.benchmark.goal_deployment import compile_goal_deployment, validate_goal_deployment
from demo.benchmark.goal_driven_tasks import build_goal_task_package, build_goal_task_spec
from demo.benchmark.goal_house_episode import (
    HOUSE_EPISODE_PLAN_ID,
    HouseNativeGoalBackend,
    _PENDING_REASON,
    build_house_deployment,
    build_house_episode_plan,
    house_runtime_state,
    house_selection,
    occupancy_waypoints,
    run_house_episode_offline,
)
from demo.benchmark.goal_indoor_deployment import build_indoor_deployment_overlay


def test_legacy_house_overlay_stays_frozen_and_v2_roster_compiles():
    legacy = build_goal_task_spec("modern_house_nvd")
    legacy_overlay = build_indoor_deployment_overlay(legacy, verify_sources=False)
    assert "allow_extended_roster" not in legacy_overlay
    assert len(compile_goal_deployment(legacy, legacy_overlay)["robot_instances"]) == 4

    package = build_goal_task_package("modern_house_nvd", indoor_roster_profile="bound_native_v2")
    overlay = build_indoor_deployment_overlay(
        package["public_task"], verify_sources=False, allow_extended_roster=True)
    deployment = compile_goal_deployment(package["public_task"], overlay)
    assert validate_goal_deployment(deployment)["validated"] is True
    ids = [row["public_id"] for row in deployment["robot_instances"]]
    assert ids == [
        "house_aerial_1", "house_ground_1", "house_ground_2", "house_ground_3",
        "house_iris_2", "house_go2_1", "house_lite3_1",
    ]
    assert [row["runtime_id"] for row in deployment["robot_instances"]] == [
        "iris_1", "m20_1", "carter_1", "m20_2", "iris_2", "go2_1", "lite3_1",
    ]
    built, deployment = build_house_deployment("bound_native_v2")
    del built
    selection = house_selection(deployment)
    assert [robot["type"] for robot in selection["robots"]] == [
        "iris", "m20", "carter", "m20", "iris", "go2", "lite3",
    ]


def test_factory_and_warehouse_v2_have_the_same_episode_entry():
    from demo.benchmark.goal_house_episode import build_house_episode_plan
    for scene, task_id, skill in (
            ("factory", "FACTORY-GOAL-NATIVE-02", "stair_traversal"),
            ("warehouse_nvd", "WAREHOUSE-GOAL-NATIVE-02", "cooperative_carry")):
        package, deployment = build_house_deployment("bound_native_v2", scene_key=scene)
        assert package["public_task"]["task_id"] == task_id
        assert validate_goal_deployment(deployment)["validated"] is True
        assert len(deployment["robot_instances"]) == 7
        plan = build_house_episode_plan(package)
        assert skill in {task["skill"] for task in plan["tasks"]}
        state = house_runtime_state(package["public_task"], plan)
        admitted = validate_plan_constraints(
            plan, task_spec=package["public_task"],
            context={"robot_regions": state["robot_regions"],
                     "object_states": state["object_states"]})
        assert admitted["task_count"] == len(plan["tasks"])
    factory, _deployment = build_house_deployment("bound_native_v2", scene_key="factory")
    factory_plan = build_house_episode_plan(factory)
    factory_state = house_runtime_state(factory["public_task"], factory_plan)
    assert factory_state["robot_regions"]["factory_ground_1"] == "response_staging"
    assert factory_state["robot_regions"]["factory_aerial_1"] == "factory_overview"
    warehouse, _deployment = build_house_deployment("bound_native_v2", scene_key="warehouse_nvd")
    warehouse_plan = build_house_episode_plan(warehouse)
    warehouse_state = house_runtime_state(warehouse["public_task"], warehouse_plan)
    assert warehouse_state["robot_regions"]["warehouse_aerial_1"] == "rack_aisle_a"


def test_factory_ground_path_detours_instead_of_crossing_the_hall():
    path = occupancy_waypoints("factory", (-6.95, -3.85), (5.55, 3.4))
    assert path is not None and len(path) >= 4
    # The free corridor first runs north. A straight shot east hits the hall wall.
    assert any(point[1] > -1.0 and point[0] < -4.0 for point in path)
    assert abs(path[-1][0] - 5.55) < 0.2 and abs(path[-1][1] - 3.4) < 0.2


def test_house_v2_witness_plan_runs_offline_without_claiming_success(tmp_path: Path):
    package = build_goal_task_package("modern_house_nvd", indoor_roster_profile="bound_native_v2")
    plan = build_house_episode_plan(package)
    assert plan["plan_id"] == HOUSE_EPISODE_PLAN_ID
    assert plan["assigned_team"] == ["house_aerial_1", "house_ground_1", "house_ground_3"]
    skills = [task["skill"] for task in plan["tasks"]]
    assert "cooperative_carry" in skills and "pick" in skills and "observe" in skills

    summary = run_house_episode_offline(tmp_path, roster_profile="bound_native_v2")
    assert summary["task_id"] == "MODERN_HOUSE-GOAL-NATIVE-02"
    assert summary["scoreable"] is False
    assert summary["skill_receipt_count"] >= 1
    assert "ground_navigation" in summary["pending_skills"]
    assert "prerequisite" in (summary["error"] or "")
    assert _PENDING_REASON


def test_object_approach_records_the_specific_carrier_and_keeps_reverse_disabled(monkeypatch):
    """The aggregate receipt must identify the carrier that missed the gate."""
    public = {
        "candidate_robot_pool": [
            {"id": "carrier_a", "mobility": "ground"},
            {"id": "carrier_b", "mobility": "ground"},
        ],
        "public_world": {
            "regions": [
                {"id": "air", "access_modes": ["aerial_navigation"]},
                {"id": "ground", "access_modes": ["ground_navigation"]},
            ],
            "objects": [{"id": "crate", "physical_properties": {"initial_region_id": "ground"}}],
        },
    }
    backend = HouseNativeGoalBackend(public)
    backend._runtime_id = lambda agent_id: {"carrier_a": "m20_1", "carrier_b": "m20_2"}[agent_id]
    states = {
        "m20_1": [0.4, 0.0, 0.5, 1.0, 0.0, 0.0, 0.0],
        "m20_2": [3.0, 0.0, 0.5, 1.0, 0.0, 0.0, 0.0],
    }
    monkeypatch.setattr(
        "demo.benchmark.goal_house_episode._root_state",
        lambda _session, runtime_id: list(states[runtime_id]),
    )
    backend.session = object()

    class Bodies:
        bindings = {"objects": {"crate": {"body_path": "/World/crate"}}}

        def __call__(self, _path):
            return {"position_m": [0.0, 0.0, 0.5]}

    seen = {}

    def fake_follow(runtime_ids, goals, *, arrived, label, allow_reverse):
        del arrived, label
        seen["allow_reverse"] = allow_reverse
        backend._last_navigation_diagnostics = {
            "agents": {
                "m20_1": {"final_root_pose_m": [0.4, 0.0, 0.5], "reverse_applied": False},
                "m20_2": {"final_root_pose_m": [3.0, 0.0, 0.5], "reverse_applied": False},
            },
            "status": "failed",
        }
        return "failed", "navigation_target_not_reached:crate:2.25m"

    monkeypatch.setattr(backend, "_follow_ground", fake_follow)
    status, reason = backend._approach_object("crate", ["carrier_a", "carrier_b"], Bodies())

    assert seen["allow_reverse"] is False
    assert status == "failed"
    assert reason == "approach_object_not_reached:crate:2.25m"
    diagnostics = backend._last_execution_diagnostics
    assert diagnostics["failure_stage"] == "approach_object"
    assert diagnostics["reach_check_entered"] is False
    assert diagnostics["failure_agent_ids"] == ["carrier_b"]
    rows = {row["agent_id"]: row for row in diagnostics["carriers"]}
    assert rows["carrier_a"]["within_approach_gate"] is True
    assert rows["carrier_b"]["within_approach_gate"] is False
    assert all(row["reverse_applied"] is False for row in rows.values())
