"""Offline House formal-episode driver: v2 deployment, witness plan, GoalSession."""

from pathlib import Path

from demo.benchmark.goal_deployment import compile_goal_deployment, validate_goal_deployment
from demo.benchmark.goal_driven_tasks import build_goal_task_package, build_goal_task_spec
from demo.benchmark.goal_house_episode import (
    HOUSE_EPISODE_PLAN_ID,
    _PENDING_REASON,
    build_house_deployment,
    build_house_episode_plan,
    house_selection,
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
