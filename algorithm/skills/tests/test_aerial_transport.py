"""Pure compilation tests. These observations are fixtures, not physics proof."""
from dataclasses import replace
import json
import math
import subprocess
import sys

import pytest

from algorithm.skills import SkillContractError, SkillRequest
from algorithm.skills.aerial_transport import AerialPickPlaceProvider, RigidPose, VerticalProfile
from algorithm.skills.navigation.aerial_navigation import (AerialPose, AerialNavigationProvider,
    select_aerial_node_route)


def request(intent="start", key="carry", **parameters):
    return SkillRequest(f"{key}/{intent}", "eai.transport.aerial_pick_place", "iris_1", task_id=key,
        parameters={"object_ref": "box", "source_ref": "source", "destination_ref": "destination",
                    "robot_ids": ["iris_1"], "carry_mode": "fixed_joint", "world_state_seq": 1,
                    "pick_anchor_world_m": [0., 0., .2], "place_anchor_world_m": [1., 0., .3],
                    "intent": intent, **parameters})


def topology():
    return {"nodes": [{"id": "a", "position_xyz": [0., 0., 1.], "target_refs": ["source"]},
                      {"id": "b", "position_xyz": [1., 0., 1.], "target_refs": ["destination"]}],
            "edges": [{"id": "ab", "from": "a", "to": "b"}]}


def observation(t=0., root=(0., 0., 1.), obj=(0., 0., .2), drone=None, **extra):
    return {"valid": True, "fresh": True, "world_state_seq": 1, "topology": topology(),
            "drone_pose": RigidPose(root), "drone_anchor_world_m": drone or root,
            "object_anchor_world_m": obj, "simulation_time_s": t, **extra}


def operation(response):
    return next((c.payload for c in response.plan.commands if c.command_type == "transport_mutation"), None)


def ack(response):
    return {**operation(response), "applied": True}


def to_descent(provider):
    r = request()
    provider.dispatch(r, observation=observation())
    support = provider.control(r, observation=observation(.1))
    disabled = provider.control(request("continue"), observation=observation(.2, confirmation=ack(support)))
    descent = provider.control(request("continue"), observation=observation(.3, confirmation=ack(disabled)))
    return descent


@pytest.mark.parametrize("change", [
    {"unexpected": 1}, {"world_state_seq": True}, {"intent": None}, {"frame": "map"},
    {"robot_ids": ["other"]}, {"carry_mode": "teleport"}, {"handoff_ref": "handoff"},
    {"vertical_speed_m_s": 0}, {"vertical_acceleration_m_s2": True}, {"phase_timeout_s": float("inf")},
    {"pick_anchor_world_m": [0, 0]}, {"place_anchor_world_m": ["1", 0, 0]},
])
def test_invalid_fields_fail_closed(change):
    plan = AerialPickPlaceProvider().plan(request(**change), observation=observation())
    assert plan.status == "invalid_request" and not plan.commands


@pytest.mark.parametrize("name", ["object_ref", "source_ref", "destination_ref", "robot_ids", "carry_mode", "world_state_seq", "pick_anchor_world_m", "place_anchor_world_m"])
def test_every_required_field_is_required(name):
    r = request()
    parameters = dict(r.parameters)
    parameters.pop(name)
    assert AerialPickPlaceProvider().plan(replace(r, parameters=parameters), observation=observation()).status == "invalid_request"


@pytest.mark.parametrize("context", [{"timing": {"duration_s": -1}}, {"budget": {"duration_s": True}}, {"budget": {"ticks": 4}}])
def test_invalid_budgets(context):
    assert AerialPickPlaceProvider().plan(replace(request(), **context), observation=observation()).status == "invalid_request"


def test_pure_imports_and_stable_provenance():
    code = "from algorithm.skills.aerial_transport import AerialPickPlaceProvider; import sys; assert not any(x.split('.')[0] in {'torch','pxr','isaaclab','omni','rclpy'} for x in sys.modules)"
    subprocess.run([sys.executable, "-c", code], check=True)
    r = replace(request(), branch_context={"run_key": "run-1"})
    first = AerialPickPlaceProvider().plan(r, observation=observation())
    second = AerialPickPlaceProvider().plan(r, observation=observation())
    assert first.to_json() == second.to_json()
    assert first.metadata["physical_outcome"] == "unknown"
    assert first.metadata["branch_context"] == {"run_key": "run-1"}
    assert "joint_path" not in first.to_json()
    assert json.loads(first.to_json())["commands"][-1]["payload"]["operation"] == "create_support"


def test_mutation_requires_matching_explicit_confirmation():
    provider = AerialPickPlaceProvider()
    provider.dispatch(request(), observation=observation())
    response = provider.control(request(), observation=observation(.1))
    assert operation(response)["operation"] == "create_support"
    again = provider.control(request("continue"), observation=observation(.2))
    assert operation(again) == operation(response)
    with pytest.raises(SkillContractError, match="confirmation"):
        provider.control(request("continue"), observation=observation(.3, confirmation={**ack(response), "confirmation_id": "foreign"}))
    disabled = provider.control(request("continue"), observation=observation(.3, confirmation=ack(response)))
    assert operation(disabled)["operation"] == "disable_collision"


def test_pick_requires_measured_xy_and_one_centimetre_z():
    provider = AerialPickPlaceProvider()
    to_descent(provider)
    response = provider.control(request("continue"), observation=observation(10., root=(.011, 0, .2)))
    assert operation(response) is None
    response = provider.control(request("continue"), observation=observation(10.1, root=(0, 0, .211)))
    assert operation(response) is None
    response = provider.control(request("continue"), observation=observation(10.2, root=(.005, 0, .205)))
    assert operation(response)["operation"] == "attach"
    assert response.state["attached"] is False
    response = provider.control(request("hold"), observation=observation(10.3, root=(.005, 0, .205), confirmation=ack(response)))
    assert response.state["attached"] is True
    assert response.state["phase"] == "removing_support" and operation(response) is None
    stopped = provider.control(request("stop"), observation=observation(10.4, root=(.005, 0, .205)))
    assert stopped.state["safe_action"] == "hover_retain_attachment"
    assert operation(stopped) is None
    with pytest.raises(SkillContractError, match="not active"):
        provider.control(request("continue"), observation=observation(10.5))


def test_hold_retains_stage_and_restarts_profile_without_jump():
    provider = AerialPickPlaceProvider()
    to_descent(provider)
    moving = provider.control(request("continue"), observation=observation(.8))
    z = moving.plan.commands[0].payload["target_pose"][2]
    held = provider.control(request("hold"), observation=observation(.9, root=(0, 0, z)))
    assert held.state["phase"] == "descending_to_pick"
    resumed = provider.control(request("continue"), observation=observation(10., root=(0, 0, z)))
    assert resumed.plan.commands[0].payload["target_pose"][2] == z
    assert resumed.state["status"] == "pending"


def test_isolation_immutable_targets_invalid_observations_and_replan():
    provider = AerialPickPlaceProvider()
    provider.dispatch(request(), observation=observation())
    provider.dispatch(request(key="other"), observation=observation())
    with pytest.raises(SkillContractError, match="immutable"):
        provider.control(request("continue", object_ref="different"), observation=observation(.1))
    with pytest.raises(SkillContractError, match="world state"):
        provider.control(request(), observation=observation(.1, fresh=False))
    assert provider.active_task_ids == ("carry", "other")
    route_obs = observation(.1, root=(-1, 0, 1))
    started = provider.control(request(), observation=route_obs)
    replanned = provider.control(request("replan"), observation=observation(.2, root=(-.5, 0, 1)))
    assert started.private_evidence["route_selection"] == replanned.private_evidence["route_selection"]
    assert provider.response("other").state["phase"] == "pending"
    provider.control(request("continue"), observation=observation(.3))
    with pytest.raises(SkillContractError, match="replan"):
        provider.control(request("replan"), observation=observation(.4))


def test_timeout_and_pregrab_stop_compile_rollback():
    provider = AerialPickPlaceProvider()
    provider.dispatch(request(), observation=observation())
    timed = provider.control(request(), observation=observation(61.))
    assert timed.state["reason_code"] == "timeout"
    assert operation(timed)["operation"] == "rollback"
    provider = AerialPickPlaceProvider()
    to_descent(provider)
    stopped = provider.control(request("stop"), observation=observation(.5))
    assert stopped.state["status"] == "cancelled" and operation(stopped)["operation"] == "rollback"


def test_alignment_does_not_cut_short_the_rest_to_rest_profile():
    provider = AerialPickPlaceProvider()
    to_descent(provider)
    near = provider.control(request("continue"), observation=observation(.4, root=(0, 0, .2)))
    assert near.state["phase"] == "descending_to_pick" and operation(near) is None
    ready = provider.control(request("continue"), observation=observation(10., root=(0, 0, .2)))
    assert operation(ready)["operation"] == "attach"


def test_invalid_mapping_retains_error_provenance_and_response_does_not_mutate_task():
    r = replace(request(), branch_context={"run_key": "run-1"})
    mapping = r.to_mapping()
    mapping["parameters"]["unexpected"] = 1
    plan = AerialPickPlaceProvider().plan(mapping, observation=observation())
    assert plan.request_id == r.request_id and plan.robot_id == r.robot_id
    assert plan.metadata["task_id"] == r.task_id and plan.metadata["branch_context"] == r.branch_context
    provider = AerialPickPlaceProvider()
    result = provider.dispatch(r, observation=observation())
    result.plan.metadata["branch_context"]["run_key"] = "tampered"
    assert provider.response("carry").plan.metadata["branch_context"] == {"run_key": "run-1"}


@pytest.mark.parametrize("distance", [0., .003, .5, -1.])
def test_continuous_profile_has_monotonic_bounded_velocity_and_acceleration(distance):
    p = VerticalProfile(1., 1.+distance)
    dt = .001
    samples = [p.sample(i*dt) for i in range(math.ceil(p.duration_s/dt)+2)]
    assert samples[0] == (1., 0.) and samples[-1] == (1.+distance, 0.)
    direction = 1 if distance >= 0 else -1
    assert all(direction*(b[0]-a[0]) >= -1e-12 for a, b in zip(samples, samples[1:]))
    assert max(abs(v) for _, v in samples) <= .1+1e-12
    assert max(abs(b[1]-a[1])/dt for a, b in zip(samples, samples[1:])) <= .5+1e-9


def test_xyz_routing_accepts_vertical_edges_and_digest_is_order_independent():
    topo = {"nodes": [{"id": "a", "position_xyz": [0, 0, 0]},
                      {"id": "b", "position_xyz": [0, 0, 2], "target_refs": ["up"]}],
            "edges": [{"id": "vertical", "from": "a", "to": "b", "directed": True}]}
    route = select_aerial_node_route(topo, AerialPose((0, 0, 0)), target_refs=["up"])
    assert route["route_length_m"] == 2 and route["topology_node_ids"] == ["a", "b"]
    topo["nodes"].reverse()
    assert select_aerial_node_route(topo, AerialPose((0, 0, 0)), target_refs=["up"])["route_graph_digest"] == route["route_graph_digest"]
    assert not select_aerial_node_route(topo, AerialPose((0, 0, 2)), target_pose=(0, 0, 0))["admitted"]
    nav = AerialNavigationProvider()
    r = SkillRequest("nav", nav.skill_id, "iris_1", task_id="nav", target_ref="up", parameters={"world_state_seq": 1})
    plan = nav.plan(r, observation={"valid": True, "fresh": True, "world_state_seq": 1, "pose": AerialPose((0, 0, 0)), "topology": topo})
    assert plan.commands[0].payload["target_pose"] == (0., 0., 2.)


def test_full_cycle_preserves_carry_offset_and_both_hover_heights():
    provider = AerialPickPlaceProvider()
    to_descent(provider)
    attached = provider.control(request("continue"), observation=observation(10., root=(0, 0, .205)))
    remove = provider.control(request("continue"), observation=observation(10.1, root=(0, 0, .205), confirmation=ack(attached)))
    ascend = provider.control(request("continue"), observation=observation(10.2, root=(0, 0, .205), confirmation=ack(remove)))
    assert ascend.state["phase"] == "ascending_from_pick"
    provider.control(request("continue"), observation=observation(20., root=(0, 0, 1.), obj=(0, 0, .995)))
    provider.control(request("continue"), observation=observation(20.1, root=(0, 0, 1.), obj=(0, 0, .995)))
    provider.control(request("continue"), observation=observation(20.2, root=(1, 0, 1.), obj=(1, 0, .995)))
    descend = provider.control(request("continue"), observation=observation(20.3, root=(1, 0, 1.), obj=(1, 0, .995)))
    assert descend.state["phase"] == "descending_to_place"
    assert provider._tasks["carry"].vertical_target == pytest.approx(.305)
    release = provider.control(request("continue"), observation=observation(28., root=(1, 0, .305), obj=(1, 0, .3)))
    assert operation(release)["operation"] == "release"
    up = provider.control(request("continue"), observation=observation(28.1, root=(1, 0, .305), obj=(1, 0, .3), confirmation=ack(release)))
    assert up.state["attached"] is False
    complete = provider.control(request("continue"), observation=observation(36., root=(1, 0, 1.), obj=(1, 0, .3)))
    assert complete.state["status"] == "succeeded"
    assert complete.private_evidence["pick_hover_z"] == complete.private_evidence["place_hover_z"] == 1.
