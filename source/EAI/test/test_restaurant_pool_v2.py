"""Deployment-realism v2 checks for the Restaurant authoring pool."""

from math import dist

from demo.benchmark.goal_driven_tasks import build_goal_task_spec


def test_restaurant_pool_exposes_capability_overlap_and_a_decoy():
    public = build_goal_task_spec("restaurant")
    rows = {row["id"]: row for row in public["candidate_robot_pool"]}

    assert {rows["restaurant_carter_1"]["type"], rows["restaurant_carter_2"]["type"]} == {"carter"}
    assert {rows["restaurant_m20_1"]["type"], rows["restaurant_m20_2"]["type"]} == {"m20"}
    assert "relay" in rows["restaurant_carter_1"]["public_capabilities"]
    assert "relay" not in rows["restaurant_lite3_1"]["public_capabilities"]
    assert "ground_navigation" in rows["restaurant_lite3_1"]["public_capabilities"]
    assert any(row["mobility"] == "aerial" for row in rows.values())


def test_restaurant_spawn_anchors_are_distributed():
    public = build_goal_task_spec("restaurant")
    positions = public["public_world"]["robot_spawn_positions"]
    points = [tuple(position[:2]) for position in positions.values()]

    assert len(points) == 8
    assert min(dist(left, right) for index, left in enumerate(points)
               for right in points[index + 1:]) >= 3.0
