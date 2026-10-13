"""XYZ routing, measured holds and the standalone GUI import boundary."""
from copy import deepcopy
from dataclasses import replace
import json
import math
from pathlib import Path
import subprocess
import sys

import pytest

from algorithm.skills.contracts import SkillContractError, SkillRequest
from algorithm.skills.navigation import (
    AerialNavigationProvider, AerialPose, AerialNodeRouteTracker, select_aerial_node_route,
)
from algorithm.skills.simulator_schema import SimulatorSchemaError, validate_simulator_skill_request


def topology():
    return {"coordinate_frame": "scene_world", "nodes": [
        {"id": "a", "position_xyz": [5., 6., 1.], "target_refs": []},
        {"id": "b", "position_xyz": [5., 6., 3.], "target_refs": []},
        {"id": "c", "position_xyz": [7., 6., 3.], "target_refs": ["place:goal"]}],
        "edges": [{"id": "ab", "from": "a", "to": "b", "directed": True},
                  {"id": "bc", "from": "b", "to": "c", "directed": True}]}


def request(intent="start", **kwargs):
    return SkillRequest("req", "eai.navigation.aerial_navigation", "iris_1",
                        task_id="flight", target_ref="place:goal",
                        parameters={"intent": intent, "world_state_seq": 1}, **kwargs)


def observation(position=(5., 6., 1.), **kwargs):
    return {"valid": True, "fresh": True, "world_state_seq": 1,
            "pose": AerialPose(position), "topology": topology(), **kwargs}


def goal(response):
    assert response.plan.commands[0].command_type == "goal_position"
    return tuple(response.plan.commands[0].payload["target_pose"])


def test_nearest_xyz_directed_vertical_route_and_target_pose_matching():
    route = select_aerial_node_route(topology(), AerialPose((5, 6, 1)), target_refs=["place:goal"])
    assert route["topology_node_ids"] == ["a", "b", "c"]
    assert route["route_length_m"] == 4.
    upper = select_aerial_node_route(topology(), AerialPose((5, 6, 2.9)), target_pose=[7, 6, 3])
    assert upper["topology_node_ids"] == ["b", "c"]
    assert not select_aerial_node_route(topology(), AerialPose((7, 6, 3)), target_pose=[5, 6, 1])["admitted"]
    assert not select_aerial_node_route(topology(), AerialPose((5, 6, 1)), target_pose=[7, 6, 3.31])["admitted"]


def test_equal_distance_routes_and_parallel_edges_are_stable():
    graph = {"nodes": [{"id": node, "position_xyz": point,
                         "target_refs": ["place:goal"] if node == "g" else []}
                        for node, point in (("s", [0, 0, 1]), ("a", [1, 1, 1]),
                                            ("b", [1, -1, 1]), ("g", [2, 0, 1]))],
             "edges": [{"id": first + last, "from": first, "to": last, "directed": True}
                       for first, last in (("s", "b"), ("b", "g"), ("s", "a"), ("a", "g"))]}
    graph["edges"].append({"id": "zz", "from": "s", "to": "a", "directed": True})
    first = select_aerial_node_route(graph, AerialPose((0, 0, 1)), target_refs=["place:goal"])
    graph["nodes"].reverse()
    graph["edges"].reverse()
    assert first == select_aerial_node_route(graph, AerialPose((0, 0, 1)), target_refs=["place:goal"])
    assert first["route_length_m"] == pytest.approx(2 * math.sqrt(2))
    assert first["topology_node_ids"] == ["s", "a", "g"]
    assert first["topology_edge_ids"] == ["sa", "ag"]


def test_polyline_decoration_cannot_change_route_or_executable_digest():
    graph = topology()
    before = select_aerial_node_route(graph, AerialPose((5, 6, 1)), target_refs=["place:goal"])
    graph["edges"][0]["polyline_xyz"] = [[5, 6, 1], [99, 99, 99], [5, 6, 3]]
    assert before == select_aerial_node_route(graph, AerialPose((5, 6, 1)), target_refs=["place:goal"])


@pytest.mark.parametrize("mutation", [
    lambda graph: graph["nodes"][0].update(position_xyz=[0, 0, float("inf")]),
    lambda graph: graph["nodes"][0].update(position_xyz=[True, 0, 1]),
    lambda graph: graph["nodes"].append(deepcopy(graph["nodes"][0])),
    lambda graph: graph["edges"][0].update(to=[]),
    lambda graph: graph["edges"][0].update(to="missing"),
    lambda graph: graph["edges"][0].update(directed="yes"),
    lambda graph: graph.update(coordinate_frame="wrong"),
    lambda graph: graph.update(meters_per_unit=True),
])
def test_invalid_graph_rejected(mutation):
    graph = topology()
    mutation(graph)
    with pytest.raises(SkillContractError):
        select_aerial_node_route(graph, AerialPose((5, 6, 1)), target_refs=["place:goal"])


@pytest.mark.parametrize("field,value", [
    ("navigation_mode", "legacy"), ("target_level", "upper"), ("vertical_link_ref", "link:x"),
    ("horizon_ticks", 1.), ("target_yaw", float("nan")),
])
def test_schema_rejects_old_and_malformed_fields(field, value):
    with pytest.raises((SkillContractError, SimulatorSchemaError)):
        validate_simulator_skill_request(replace(request(), parameters={"world_state_seq": 1, field: value}))


def test_schema_targets_are_exclusive_and_horizon_propagates():
    for invalid in (replace(request(), target_ref=None), replace(request(), target_pose=(7, 6, 3))):
        with pytest.raises(SimulatorSchemaError, match="exactly one"):
            validate_simulator_skill_request(invalid)
    validate_simulator_skill_request(replace(request(), target_ref=None, target_pose=(7, 6, 3)))
    r = replace(request(), parameters={"world_state_seq": 1, "horizon_ticks": 2, "target_yaw": 1.})
    response = AerialNavigationProvider().dispatch(r, observation=observation())
    assert response.plan.commands[0].payload["horizon_ticks"] == 2


def test_dispatch_and_unstarted_continue_hold_without_consulting_topology():
    provider = AerialNavigationProvider()
    obs = observation(topology=None)
    response = provider.dispatch(request(), observation=obs)
    assert goal(response) == (5, 6, 1)
    assert response.private_evidence == {"node_index": 0}
    response = provider.control(request("continue"), observation=obs)
    assert response.state["reason_code"] == "awaiting_start"
    assert goal(response) == (5, 6, 1)


def test_start_hold_continue_replan_arrival_and_private_evidence():
    provider = AerialNavigationProvider()
    provider.dispatch(request(), observation=observation())
    response = provider.control(request(), observation=observation())
    assert goal(response) == (5, 6, 3)
    held = provider.control(request("hold"), observation=observation((5, 6, 2), fresh=False))
    assert goal(held) == (5, 6, 2)
    assert held.private_evidence["node_index"] == 1
    resumed = provider.control(request("continue"), observation=observation((5, 6, 2), topology=None))
    assert goal(resumed) == (5, 6, 3)
    replanned = provider.control(request("replan"), observation=observation((5, 6, 3)))
    assert replanned.private_evidence["route_selection"]["topology_node_ids"] == ["b", "c"]
    arrived = provider.control(request("continue"), observation=observation((6.8, 6, 3)))
    assert arrived.state["execution_state"] == "arrived"
    assert arrived.state["status"] == "succeeded"
    assert goal(arrived) == (6.8, 6, 3)
    assert not provider.active_task_ids
    assert "private_evidence" not in arrived.to_mapping()
    assert json.loads(arrived.plan.to_json())["commands"][0]["payload"]["target_pose"] == [6.8, 6., 3.]


def test_repeated_start_reselects_from_live_pose():
    provider = AerialNavigationProvider()
    provider.dispatch(request(), observation=observation())
    provider.control(request(), observation=observation())
    response = provider.control(request(), observation=observation((5, 6, 3)))
    assert response.private_evidence["route_selection"]["topology_node_ids"] == ["b", "c"]


@pytest.mark.parametrize("intent", ["hold", "stop"])
def test_hold_stop_remain_available_after_deadline(intent):
    provider = AerialNavigationProvider()
    r = request(intent, timing={"deadline_s": 2.})
    provider.dispatch(r, observation=observation(simulation_time_s=1.))
    response = provider.control(r, observation=observation((5, 6, 2), simulation_time_s=3., fresh=False))
    assert goal(response) == (5, 6, 2)
    assert response.state["status"] == ("cancelled" if intent == "stop" else "pending")


@pytest.mark.parametrize("limit", [{"timing": {"duration_s": 1.}}, {"timing": {"deadline_s": 11.}},
                                   {"budget": {"duration_s": 1.}}])
def test_timeout_uses_measured_current_hold_and_simulation_clock(limit):
    provider = AerialNavigationProvider()
    r = request(**limit)
    provider.dispatch(r, observation=observation(simulation_time_s=10.))
    result = provider.control(r, observation=observation((5, 6, 2), simulation_time_s=11.))
    assert result.state["reason_code"] == "navigation_timeout"
    assert goal(result) == (5, 6, 2)


def test_duration_without_clock_uses_dt_and_hold_retains_progress():
    provider = AerialNavigationProvider()
    r = request(timing={"duration_s": .3})
    provider.dispatch(r, observation=observation())
    assert provider.control(r, observation=observation(), dt_s=.1).state["status"] == "pending"
    result = provider.control(replace(r, parameters={"world_state_seq": 1, "intent": "continue"}),
                              observation=observation(), dt_s=.2)
    assert result.state["reason_code"] == "navigation_timeout"


def test_clocked_hold_time_is_charged_on_resume():
    provider = AerialNavigationProvider()
    r = request(timing={"duration_s": 2.})
    provider.dispatch(r, observation=observation(simulation_time_s=0.))
    provider.control(r, observation=observation(simulation_time_s=.1))
    held = replace(r, parameters={"intent": "hold", "world_state_seq": 1})
    provider.control(held, observation=observation((5, 6, 2), simulation_time_s=1.))
    resumed = replace(r, parameters={"intent": "continue", "world_state_seq": 1})
    result = provider.control(resumed, observation=observation((5, 6, 2), simulation_time_s=3.))
    assert result.state["reason_code"] == "navigation_timeout"


def test_unreachable_and_host_failure_never_hold_at_origin():
    provider = AerialNavigationProvider()
    provider.dispatch(request(), observation=observation())
    graph = topology()
    graph["edges"] = []
    result = provider.control(request(), observation=observation(topology=graph))
    assert result.state["reason_code"] == "route_unreachable"
    assert goal(result) == (5, 6, 1)
    provider = AerialNavigationProvider()
    provider.dispatch(request(), observation=observation())
    result = provider.fail("flight", RuntimeError("controller unavailable"), hover_position=(5, 6, 2))
    assert goal(result) == (5, 6, 2)
    assert result.state["status"] == "failed"


def test_invalid_observation_fails_to_last_valid_pose_and_identity_is_immutable():
    provider = AerialNavigationProvider()
    provider.dispatch(request(), observation=observation())
    with pytest.raises(SkillContractError, match="immutable"):
        provider.control(replace(request(), robot_id="other"), observation=observation())
    assert provider.active_task_ids == ("flight",)
    result = provider.control(request(), observation=observation(valid=False, pose=None))
    assert goal(result) == (5, 6, 1)
    assert result.state["status"] == "failed"


def test_plan_interface_and_transport_route_composition_remain_compatible():
    provider = AerialNavigationProvider()
    plan = provider.plan(request(), observation=observation())
    assert plan.status == "planned"
    assert tuple(plan.commands[0].payload["target_pose"]) == (5, 6, 3)
    from algorithm.skills.aerial_transport import AerialPickPlaceProvider, RigidPose
    params = {"intent": "start", "world_state_seq": 1, "object_ref": "object:box",
              "source_ref": "place:goal", "destination_ref": "place:goal", "carry_mode": "fixed_joint",
              "robot_ids": ["iris_1"], "pick_anchor_world_m": [7, 6, 0], "place_anchor_world_m": [8, 6, 0]}
    r = SkillRequest("transport", "eai.transport.aerial_pick_place", "iris_1", task_id="carry", parameters=params)
    obs = {"valid": True, "fresh": True, "world_state_seq": 1, "simulation_time_s": 0.,
           "drone_pose": RigidPose((5, 6, 1)), "drone_anchor_world_m": [5, 6, 1],
           "object_anchor_world_m": [7, 6, 0], "topology": topology()}
    transport = AerialPickPlaceProvider()
    transport.dispatch(r, observation=obs)
    response = transport.control(r, observation={**obs, "simulation_time_s": .1})
    assert response.state["phase"] == "navigating_to_pick"
    assert tuple(response.plan.commands[0].payload["target_pose"]) == (5, 6, 3)
    assert AerialNodeRouteTracker([[1, 2, 3]], .3).target(AerialPose((1, 2, 3))) is None


def test_gui_import_and_help_do_not_load_native_modules_or_write_output(tmp_path):
    root = Path(__file__).resolve().parents[3]
    script = root / "algorithm/skills/navigation/examples/aerial_topology_gui_demo.py"
    code = """
import sys
from pathlib import Path
from algorithm.skills.navigation.examples import aerial_topology_gui_demo as demo
assert not {'isaacsim', 'isaaclab', 'torch', 'omni.ui', 'simulator'} & set(sys.modules)
assert demo.build_parser().parse_args([]).frames == 0
assert len(demo.build_topology()['nodes']) == 6
assert not list(Path.cwd().iterdir())
"""
    import os
    env = {**os.environ, "PYTHONPATH": str(root), "PYTHONDONTWRITEBYTECODE": "1"}
    subprocess.run([sys.executable, "-B", "-c", code], cwd=tmp_path, env=env, check=True)
    result = subprocess.run([sys.executable, "-B", str(script), "--help"], cwd=tmp_path, env=env,
                            check=True, capture_output=True, text=True)
    assert "--frames" in result.stdout
    assert not list(tmp_path.iterdir())
