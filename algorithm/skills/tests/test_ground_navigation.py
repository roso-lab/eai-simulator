"""Canonical authored-node routing contracts; no controller implementation."""
from __future__ import annotations

import ast
from copy import deepcopy
import json
import math
from pathlib import Path

import pytest

from algorithm.skills.contracts import SkillContractError, SkillKind, SkillRequest
from algorithm.skills.navigation import (
    NavigationPose,
    NavigationProvider,
    NodeRouteTracker,
    ground_navigation_profile,
    select_node_route,
)


def _topology():
    return {
        "nodes": [
            {"id": "a", "position_xyz": [0.0, 0.0, 0.0], "target_refs": []},
            {"id": "b", "position_xyz": [2.0, 0.0, 0.0], "target_refs": []},
            {"id": "c", "position_xyz": [2.0, 2.0, 0.0], "target_refs": ["place:goal"]},
        ],
        "edges": [
            {"id": "ab", "from": "a", "to": "b", "directed": True},
            {"id": "bc", "from": "b", "to": "c", "directed": True},
        ],
    }


def _request(intent="start", *, task_id="navigate", robot_id="carter_1", **parameters):
    return {
        "request_id": f"{task_id}/{intent}",
        "task_id": task_id,
        "skill_id": "eai.navigation.node_route",
        "skill_version": "simulator.skill.v1",
        "robot_id": robot_id,
        "target_ref": "place:goal",
        "parameters": {"intent": intent, "world_state_seq": 1, **parameters},
    }


def _observation(position=(0.0, 0.0, 0.0), yaw=0.0, **overrides):
    values = {
        "world_state_seq": 1,
        "simulation_time_s": 0.0,
        "valid": True,
        "fresh": True,
        "pose": NavigationPose(position, yaw),
    }
    values.update(overrides)
    return values


def _target(response):
    return response.plan.commands[0].payload


def test_tracker_advances_authored_nodes_in_order_and_uses_xy_metric():
    tracker = NodeRouteTracker(
        [(0, 0, 0), (2, 0, 0), (2, 2, 0)],
        ground_navigation_profile(),
        ("a", "b", "c"),
    )
    assert tracker.advance(NavigationPose((2, 2, 9), 0)).node_id == "a"
    assert tracker.advance(NavigationPose((0, 0, 9), 0)).node_id == "b"
    assert tracker.advance(NavigationPose((2, 0, -8), 0)).node_id == "c"
    assert tracker.advance(NavigationPose((2, 1.71, 4), 0)) is None
    assert tracker.arrived


def test_final_heading_is_measured_and_wraps_across_pi():
    tracker = NodeRouteTracker(
        [(0, 0, 0)], ground_navigation_profile(), ("a",), -math.pi + 0.01,
    )
    target = tracker.advance(NavigationPose((0, 0, 0), math.pi - 0.20))
    assert target is not None and target.heading_rad == pytest.approx(-math.pi + 0.01)
    assert tracker.advance(NavigationPose((0, 0, 0), math.pi - 0.01)) is None
    assert tracker.arrived


def test_provider_dispatch_continue_hold_replan_stop_and_identity():
    provider = NavigationProvider(_topology(), profile=ground_navigation_profile())
    observation = _observation()
    dispatched = provider.dispatch(_request(), observation=observation)
    assert dispatched.plan.commands == ()
    assert "route_selection" not in dispatched.private_evidence
    waiting = provider.control(_request("continue"), observation=observation)
    assert waiting.state["reason_code"] == "awaiting_start"

    moving = provider.control(_request(), observation=observation)
    assert _target(moving)["node_id"] == "b"
    assert moving.state["reason_code"] == "tracking_node"
    tracker = provider._tasks["navigate"].tracker

    held = provider.control(_request("hold"), observation=observation)
    assert held.plan.commands == () and tracker.node_index == 1
    continued = provider.control(_request("continue"), observation=observation)
    # The task keeps the immutable dispatch identity; the intent is lifecycle state.
    assert continued.plan.request_id == "navigate/start"

    provider.control(_request("replan"), observation=_observation((1.8, 0.0, 0.0)))
    assert provider._tasks["navigate"].tracker is not tracker
    stopped = provider.control(_request("stop"), observation=observation)
    assert stopped.state["status"] == "cancelled" and stopped.plan.commands == ()
    assert provider.active_task_ids == ()
    with pytest.raises(SkillContractError, match="not active"):
        provider.control(_request("continue"), observation=observation)


def test_arrival_requires_position_and_authored_heading_and_keeps_route_private():
    graph = _topology()
    graph["nodes"][-1]["heading_rad"] = math.pi
    graph.update(version=2, levels=[{"id": "1F", "label": "Ground", "support_z_m": 0.0}])
    for row in graph["nodes"]:
        row.update(label="", aliases=[], level_id="1F")
    provider = NavigationProvider(graph, profile=ground_navigation_profile())
    request = _request()
    provider.dispatch(request, observation=_observation())
    provider.control(request, observation=_observation())
    provider.control(_request("continue"), observation=_observation((2.0, 0.0, 7.0)))
    aligning = provider.control(
        _request("continue"), observation=_observation((2.0, 2.0, -5.0), 0.0)
    )
    assert aligning.state["status"] == "pending"
    assert _target(aligning)["target_yaw"] == pytest.approx(math.pi)
    arrived = provider.control(
        _request("continue"), observation=_observation((2.0, 2.0, 8.0), -math.pi)
    )
    assert arrived.state["status"] == "completed"
    public = json.dumps(arrived.to_mapping())
    assert all(key not in public for key in ("route_points_m", "topology_node_ids", "topology_edge_ids"))
    arrived.private_evidence["route_selection"]["route_points_m"].clear()
    assert provider.response("navigate").private_evidence["route_selection"]["route_points_m"]


def test_provider_tasks_have_independent_route_progress():
    provider = NavigationProvider(_topology(), profile=ground_navigation_profile())
    for task_id in ("one", "two"):
        provider.dispatch(_request(task_id=task_id), observation=_observation())
        provider.control(_request(task_id=task_id), observation=_observation())
    one, two = (provider._tasks[name].tracker for name in ("one", "two"))
    provider.control(_request("hold", task_id="one"), observation=_observation())
    assert one is not two and one.node_index == two.node_index == 1


@pytest.mark.parametrize(
    "overrides",
    [{"valid": False}, {"fresh": False}, {"world_state_seq": 0}, {"pose": None}],
)
def test_invalid_motion_observation_is_terminal(overrides):
    provider = NavigationProvider(_topology(), profile=ground_navigation_profile())
    provider.dispatch(_request(), observation=_observation())
    response = provider.control(_request(), observation=_observation(**overrides))
    assert response.state["status"] == "failed"
    assert response.state["safe_action"] == "stop"
    assert response.plan.commands == ()


def test_plan_implements_skill_provider_api_without_controller_side_effects():
    provider = NavigationProvider(_topology(), profile=ground_navigation_profile())
    request = SkillRequest.from_mapping(_request())
    plan = provider.plan(request, observation=_observation())
    assert provider.kind is SkillKind.NAVIGATION
    assert plan.commands[0].command_type == "navigation_target"
    assert plan.commands[0].payload["node_id"] == "b"
    assert plan.to_json() == provider.response("navigate").plan.to_json()


def test_provider_rejects_identity_changes_and_retired_direct_coordinate_contract():
    provider = NavigationProvider(_topology(), profile=ground_navigation_profile())
    provider.dispatch(_request(), observation=_observation())
    changed = _request()
    changed["target_ref"] = "place:other"
    response = provider.control(changed, observation=_observation())
    assert response.state["status"] == "failed"

    legacy = _request(task_id="legacy")
    legacy["target_ref"] = None
    legacy["target_pose"] = [2.0, 2.0, 0.0]
    with pytest.raises(SkillContractError, match="target_ref only"):
        NavigationProvider(_topology(), profile=ground_navigation_profile()).dispatch(
            legacy, observation=_observation()
        )


def test_node_dijkstra_ignores_edge_curves_and_respects_direction():
    graph = _topology()
    graph["edges"][0]["polyline_xyz"] = [[0, 0, 0], [90, 90, 0], [2, 0, 0]]
    profile = ground_navigation_profile()
    forward = select_node_route(
        graph, NavigationPose((0, 0, 0), 0), profile=profile, target_ref="place:goal"
    )
    assert forward["route_points_m"] == [[0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [2.0, 2.0, 0.0]]
    assert forward["route_length_m"] == 4.0
    graph["nodes"][0]["target_refs"] = ["place:return"]
    reverse = select_node_route(
        graph, NavigationPose((2, 2, 0), 0), profile=profile, target_ref="place:return"
    )
    assert reverse["admitted"] is False and reverse["reason"] == "route_unreachable"


def test_nearest_node_and_equal_cost_targets_are_deterministic():
    graph = _topology()
    graph["nodes"].append({"id": "aa", "position_xyz": [0, 0, 0], "target_refs": []})
    graph["nodes"][1]["target_refs"] = ["place:near"]
    graph["nodes"].append({"id": "bb", "position_xyz": [0, 2, 0], "target_refs": ["place:near"]})
    graph["edges"].append({"id": "abb", "from": "a", "to": "bb"})
    result = select_node_route(
        graph, NavigationPose((0, 0, 0), 0), profile=ground_navigation_profile(),
        target_ref="place:near", target_is_semantic=True,
    )
    assert result["topology_node_ids"] == ["a", "b"]
    shuffled = deepcopy(graph)
    shuffled["nodes"].reverse()
    shuffled["edges"].reverse()
    assert select_node_route(
        shuffled, NavigationPose((0, 0, 0), 0), profile=ground_navigation_profile(),
        target_ref="place:near", target_is_semantic=True,
    ) == result


def test_provider_imports_no_simulator_or_task_runtime_dependencies():
    path = Path(__file__).parents[1] / "navigation" / "node_route.py"
    tree = ast.parse(path.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported.isdisjoint({"torch", "isaaclab", "omni", "rclpy", "rospy", "simulator", "demo", "task", "tools"})
