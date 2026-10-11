"""Authored-node routing, heading PID and pure-provider contracts."""
from __future__ import annotations

import ast
from copy import deepcopy
import json
import math
from pathlib import Path

import pytest

from algorithm.skills.contracts import SkillContractError, SkillKind, SkillRequest
from algorithm.skills.navigation import (
    GroundNavigationProvider, GroundPose, GroundTrackingConfig, NodeRouteTracker,
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
        "request_id": f"{task_id}/{intent}", "task_id": task_id,
        "skill_id": "eai.navigation.ground_navigation", "skill_version": "simulator.skill.v1",
        "robot_id": robot_id, "target_ref": "place:goal",
        "parameters": {"intent": intent, "world_state_seq": 1, **parameters},
    }


def _observation(position=(0.0, 0.0, 0.0), yaw=0.0, *, topology=None, **overrides):
    return {
        "world_state_seq": 1, "valid": True, "fresh": True,
        "pose": GroundPose(position, yaw),
        "topology": _topology() if topology is None else topology, **overrides,
    }


def _velocity(response):
    return response.plan.commands[0].payload["velocity"]


@pytest.mark.parametrize("yaw", [math.pi, -math.pi, math.pi / 2])
def test_reverse_or_large_heading_rotates_before_cruising(yaw):
    tracker = NodeRouteTracker([[2.0, 0.0, 0.0]])
    command = tracker.step(GroundPose((0.0, 0.0, 0.0), yaw))
    assert command[:2] == [0.0, 0.0]
    assert abs(command[2]) == 1.0
    assert tracker.step(GroundPose((0.0, 0.0, 0.0), 0.0))[0] == 0.5


def test_pid_wraps_heading_and_derivative_at_pi_boundary():
    tracker = NodeRouteTracker([[-2.0, 0.0, 0.0]],
                              GroundTrackingConfig(kp=0.0, kd=1.0, max_yaw_rate_rad_s=10.0))
    first = tracker.step(GroundPose((0.0, 0.0, 0.0), -0.01))
    second = tracker.step(GroundPose((0.0, 0.0, 0.0), 0.01))
    assert first == [0.0, 0.0, 0.0]  # No derivative on the first sample.
    assert second[2] == pytest.approx(-0.2)
    near_wrap = NodeRouteTracker([[-2.0, 0.0, 0.0]])
    command = near_wrap.step(GroundPose((0.0, 0.0, 0.0), -math.pi + 0.01))
    assert command == pytest.approx([0.5, 0.0, -0.02])


def test_integral_is_clamped_and_node_transition_resets_pid():
    tracker = NodeRouteTracker([[2.0, 0.0, 0.0], [2.0, 2.0, 0.0]],
                              GroundTrackingConfig(kp=0.0, ki=2.0, kd=0.0, max_yaw_rate_rad_s=3.0))
    assert tracker.step(GroundPose((0.0, 0.0, 0.0), -math.pi / 2), dt_s=2.0)[2] == 2.0
    assert tracker.integral == 1.0
    tracker.step(GroundPose((2.0, 0.0, 9.0), math.pi / 2))
    assert tracker.node_index == 1
    assert tracker.integral == 0.0


def test_nodes_advance_in_order_without_looking_ahead_and_ignore_z():
    tracker = NodeRouteTracker([[2.0, 0.0, 0.0], [2.0, 2.0, 0.0]])
    tracker.step(GroundPose((2.0, 2.0, 0.0), 0.0))
    assert tracker.node_index == 0
    tracker.step(GroundPose((2.0, 0.0, 8.0), 0.0))
    assert tracker.node_index == 1
    assert tracker.step(GroundPose((2.0, 1.71, 9.0), -math.pi)) == [0.0, 0.0, 0.0]
    assert tracker.arrived and tracker.previous_error is None


@pytest.mark.parametrize("dt", [0.0, -0.1, math.inf, math.nan, True])
def test_invalid_time_step_cannot_produce_velocity(dt):
    with pytest.raises(SkillContractError):
        NodeRouteTracker([[2.0, 0.0, 0.0]]).step(GroundPose((0.0, 0.0, 0.0), 0.0), dt_s=dt)


def test_provider_dispatch_continue_hold_replan_stop_and_identity():
    provider = GroundNavigationProvider()
    observation = _observation()
    dispatched = provider.dispatch(_request(), observation=observation)
    assert _velocity(dispatched) == [0.0, 0.0, 0.0]
    assert "route_selection" not in dispatched.private_evidence
    assert provider.control(_request("continue"), observation=observation).state["reason_code"] == "awaiting_start"
    moving = provider.control(_request(), observation=observation)
    assert _velocity(moving) == [0.5, 0.0, 0.0]
    assert moving.state["reason_code"] == "pid_follow_nodes"
    tracker = provider._tasks["navigate"].tracker
    assert tracker.node_index == 1
    hold = provider.control(_request("hold"), observation=observation)
    assert _velocity(hold) == [0.0, 0.0, 0.0]
    assert tracker.node_index == 1 and tracker.previous_error is None
    continued = provider.control(_request("continue"), observation=observation)
    assert continued.plan.request_id == "navigate/continue"
    provider.control(_request("replan"), observation=_observation((1.8, 0.0, 0.0)))
    assert provider._tasks["navigate"].tracker is not tracker
    stopped = provider.control(_request("stop"), observation=observation)
    assert stopped.state["status"] == "cancelled"
    assert _velocity(stopped) == [0.0, 0.0, 0.0]
    assert provider.active_task_ids == ()
    with pytest.raises(SkillContractError):
        provider.control(_request("continue"), observation=observation)


def test_provider_final_distance_is_the_only_arrival_condition_and_routes_stay_private():
    provider = GroundNavigationProvider()
    request = _request(target_yaw=math.pi)
    provider.dispatch(request, observation=_observation())
    provider.control(request, observation=_observation())
    provider.control(_request("continue"), observation=_observation((2.0, 0.0, 0.0)))
    request = _request("continue", target_yaw=0.0)
    response = provider.control(request, observation=_observation((2.0, 1.71, 8.0), -math.pi / 2))
    assert response.state["status"] == "succeeded"
    assert _velocity(response) == [0.0, 0.0, 0.0]
    public = json.dumps(response.to_mapping())
    assert all(key not in public for key in ("route_points_m", "topology_node_ids", "topology_edge_ids", "node_index"))
    assert response.plan.status == "planned"
    assert response.plan.metadata["physical_outcome"] == "external_evaluator_required"
    response.private_evidence["route_selection"]["route_points_m"].clear()
    assert provider.response("navigate").private_evidence["route_selection"]["route_points_m"]


def test_provider_tasks_have_independent_pid_and_hold_state():
    provider = GroundNavigationProvider()
    for task_id in ("one", "two"):
        provider.dispatch(_request(task_id=task_id), observation=_observation())
        provider.control(_request(task_id=task_id), observation=_observation(yaw=0.2))
    one, two = (provider._tasks[name].tracker for name in ("one", "two"))
    provider.control(_request("hold", task_id="one"), observation=_observation())
    assert one is not two
    assert one.previous_error is None and two.previous_error is not None


@pytest.mark.parametrize("overrides", [{"valid": False}, {"fresh": False}, {"world_state_seq": 0}, {"pose": None}])
def test_invalid_motion_observation_compiles_terminal_zero(overrides):
    provider = GroundNavigationProvider()
    provider.dispatch(_request(), observation=_observation())
    response = provider.control(_request(), observation=_observation(**overrides))
    assert response.state["status"] == "failed"
    assert _velocity(response) == [0.0, 0.0, 0.0]


def test_plan_implements_existing_skill_provider_api_without_side_effects():
    provider = GroundNavigationProvider()
    request = SkillRequest.from_mapping(_request())
    plan = provider.plan(request, observation=_observation())
    assert provider.kind is SkillKind.NAVIGATION
    assert plan.commands[0].payload["velocity"] == [0.5, 0.0, 0.0]
    assert plan.to_json() == provider.response("navigate").plan.to_json()
    invalid = GroundNavigationProvider().plan(request)
    assert invalid.status == "invalid_request" and invalid.commands == ()


def test_provider_rejects_identity_changes_and_unknown_parameters():
    provider = GroundNavigationProvider()
    provider.dispatch(_request(), observation=_observation())
    for request in (_request(robot_id="go2_1"), _request(private_route=["a", "b"])):
        with pytest.raises(SkillContractError):
            provider.control(request, observation=_observation())
    changed = _request()
    changed["target_ref"] = "place:other"
    with pytest.raises(SkillContractError):
        provider.control(changed, observation=_observation())


def test_node_dijkstra_ignores_curves_and_clearance_and_respects_direction():
    graph = _topology()
    graph["edges"][0].update({"polyline_xyz": [[0, 0, 0], [90, 90, 0], [2, 0, 0]],
                              "profiles": {"ground_small": {"clearance_m": 0.001}}})
    forward = select_node_route(graph, GroundPose((0.0, 0.0, 0.0), 0.0), target_refs=["place:goal"])
    assert forward["route_points_m"] == [[0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [2.0, 2.0, 0.0]]
    assert forward["route_length_m"] == 4.0
    graph["nodes"][0]["target_refs"] = ["place:return"]
    reverse = select_node_route(graph, GroundPose((2.0, 2.0, 0.0), 0.0), target_refs=["place:return"])
    assert reverse["admitted"] is False


def test_nearest_node_and_equal_cost_targets_are_deterministic():
    graph = _topology()
    graph["nodes"].append({"id": "aa", "position_xyz": [0.0, 0.0, 0.0], "target_refs": []})
    graph["nodes"][1]["target_refs"] = ["place:goal"]
    graph["nodes"].append({"id": "bb", "position_xyz": [0.0, 2.0, 0.0], "target_refs": ["place:goal"]})
    graph["edges"].append({"id": "abb", "from": "a", "to": "bb"})
    result = select_node_route(graph, GroundPose((0.0, 0.0, 0.0), 0.0), target_refs=["place:goal"])
    assert result["topology_node_ids"] == ["a", "b"]
    shuffled = deepcopy(graph)
    shuffled["nodes"].reverse()
    shuffled["edges"].reverse()
    assert select_node_route(shuffled, GroundPose((0.0, 0.0, 0.0), 0.0), target_refs=["place:goal"]) == result


def test_multiple_bound_targets_choose_shortest_reachable_route():
    graph = _topology()
    graph["nodes"].append({"id": "isolated", "position_xyz": [0.1, 0.0, 0.0], "target_refs": ["place:goal"]})
    graph["nodes"][1]["target_refs"] = ["place:goal"]
    result = select_node_route(graph, GroundPose((0.0, 0.0, 0.0), 0.0), target_refs=["place:goal"])
    assert result["topology_node_ids"] == ["a", "b"]


def test_target_pose_must_match_an_existing_node_and_unreachable_is_zero():
    graph = _topology()
    pose = GroundPose((0.0, 0.0, 0.0), 0.0)
    assert select_node_route(graph, pose, target_pose=[2.0, 1.71])["admitted"]
    assert not select_node_route(graph, pose, target_pose=[2.0, 1.69])["admitted"]
    graph["edges"] = []
    provider = GroundNavigationProvider()
    provider.dispatch(_request(), observation=_observation())
    response = provider.control(_request(), observation=_observation(topology=graph))
    assert response.state["reason_code"] == "route_unreachable"
    assert response.state["status"] == "failed"
    assert _velocity(response) == [0.0, 0.0, 0.0]


def test_provider_imports_no_simulator_or_runtime_dependencies():
    path = Path(__file__).parents[1] / "navigation" / "ground_navigation.py"
    tree = ast.parse(path.read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported.isdisjoint({"torch", "isaaclab", "omni", "rclpy", "rospy", "simulator", "demo", "task", "tools"})
