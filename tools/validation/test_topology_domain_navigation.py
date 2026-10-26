"""Current node_route API: aerial editor modes never erase altitude semantics."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from algorithm.skills.contracts import SkillContractError
from algorithm.skills.navigation import NavigationObservation, NavigationPose, NavigationProvider, NodeRouteTracker, aerial_navigation_profile, ground_navigation_profile, select_node_route
from algorithm.skills.navigation.topology import validate_topology
from task.executors.navigation import NavigationExecutor
from task.executors.navigation_backends import PlanarVelocityNavigationAdapter
from tools.topology_authoring.model import TopologyDocument


def aerial():
    doc = TopologyDocument("office", domain="aerial")
    doc.add_node([0, 0, 2], node_id="lower", edit_mode="2.5d")
    doc.add_node([0, 0, 4], node_id="upper", edit_mode="2.5d")
    doc.add_node([1, 0, 4], node_id="goal", edit_mode="3d")
    doc.add_edge("lower", "upper", [[0, 0, 2], [0, 0, 4]], directed=True)
    doc.add_edge("upper", "goal", [[0, 0, 4], [1, 0, 4]])
    return doc


def request():
    return {"request_id": "domain-check", "task_id": "domain-check", "skill_id": "eai.navigation.node_route",
            "skill_version": "simulator.skill.v1", "robot_id": "r1", "target_ref": "goal",
            "parameters": {"intent": "start", "world_state_seq": 1}}


def test_25d_nodes_match_height_and_vertical_edge_uses_xyz():
    doc, profile = aerial(), aerial_navigation_profile()
    route = select_node_route(doc, NavigationPose((0, 0, 4)), profile=profile, target_ref="goal")
    assert route["admitted"] and route["topology_node_ids"] == ["upper", "goal"]
    route = select_node_route(doc, NavigationPose((0, 0, 2)), profile=profile, target_ref="goal")
    assert route["topology_node_ids"] == ["lower", "upper", "goal"]
    assert route["route_length_m"] == pytest.approx(3)
    off_graph = select_node_route(doc, NavigationPose((0, 0, 3)), profile=profile, target_ref="goal")
    assert not off_graph["admitted"] and off_graph["reason"] == "origin_not_reached"
    legacy = deepcopy(doc.to_dict())
    for node in legacy["nodes"]:
        node.pop("edit_mode")
    assert not validate_topology(legacy, metric="xyz")
    tracker = NodeRouteTracker([[0, 0, 4]], profile, ["upper"])
    assert tracker.advance(NavigationPose((0, 0, 2))).node_id == "upper"
    assert not tracker.arrived
    assert tracker.advance(NavigationPose((0, 0, 4))) is None and tracker.arrived


def test_ground_cross_floor_route_keeps_xyz_with_xy_distance():
    doc = TopologyDocument("office")
    doc.set_levels([{"id": "lower", "label": "Lower", "support_z_m": 0},
                    {"id": "upper", "label": "Upper", "support_z_m": 3}])
    doc.add_node([0, 0, 0], node_id="start", level_id="lower")
    doc.add_node([1, 0, 1.5], node_id="stair", kind="transition")
    doc.add_node([2, 0, 3], node_id="goal", level_id="upper")
    for first, last in (("start", "stair"), ("stair", "goal")):
        doc.add_edge(first, last, [doc.node(first)["position_xyz"], doc.node(last)["position_xyz"]],
                     traversal_mode="stairs", required_controller_capabilities=["stair_capable"])
    selected = select_node_route(doc, NavigationPose((0, 0, 0)),
        profile=ground_navigation_profile(controller_capabilities=["stair_capable"]), target_ref="goal")
    assert selected["admitted"] and selected["topology_node_ids"] == ["start", "stair", "goal"]
    assert selected["route_points_m"] == [[0, 0, 0], [1, 0, 1.5], [2, 0, 3]]
    assert selected["route_length_m"] == pytest.approx(2)


@pytest.mark.parametrize("mode", ["walk", "ramp", "stairs"])
def test_ground_visual_z_does_not_change_route_matching_cost_or_arrival(mode):
    doc = TopologyDocument("office")
    doc.set_levels([{"id": "ground", "label": "Ground", "support_z_m": 0}])
    for node_id, point in (("a", [0, 0, 100]), ("z", [0, 0, -50]),
                           ("short", [1, 0, -100]), ("detour", [0, 2, 0]),
                           ("goal", [2, 0, 50])):
        doc.add_node(point, node_id=node_id, level_id="ground")
    for first, last in (("a", "short"), ("short", "goal"), ("a", "detour"), ("detour", "goal")):
        doc.add_edge(first, last, [doc.node(first)["position_xyz"], doc.node(last)["position_xyz"]],
            directed=True, traversal_mode=mode,
            required_controller_capabilities=["stair_capable"] if mode == "stairs" else [])
    profile = ground_navigation_profile(controller_capabilities=["stair_capable"])
    assert not validate_topology(doc, metric="xy")
    route = select_node_route(doc, NavigationPose((0, 0, -50)), profile=profile, target_ref="goal",
                             controller_capabilities=[mode, "stair_capable"])
    assert route["admitted"] and route["topology_node_ids"] == ["a", "short", "goal"]
    assert route["route_length_m"] == pytest.approx(2)
    assert route["route_points_m"] == [[0, 0, 100], [1, 0, -100], [2, 0, 50]]
    tracker = NodeRouteTracker(route["route_points_m"], profile, route["topology_node_ids"])
    assert tracker.advance(NavigationPose((0, 0, -50))).node_id == "short"
    assert tracker.advance(NavigationPose((1, 0, 77))).node_id == "goal"
    assert tracker.advance(NavigationPose((2, 0, -77))) is None and tracker.arrived
    flattened = deepcopy(doc.to_dict())
    for node in flattened["nodes"]:
        node["position_xyz"][2] = 0
    flat_route = select_node_route(flattened, NavigationPose((0, 0, 0)), profile=profile, target_ref="goal")
    assert flat_route["topology_node_ids"] == route["topology_node_ids"]
    assert flat_route["route_length_m"] == route["route_length_m"]
    reverse = select_node_route(doc, NavigationPose((2, 0, -500)), profile=profile, target_ref="a")
    assert not reverse["admitted"]
    if mode == "stairs":
        blocked = select_node_route(doc, NavigationPose((0, 0, 0)),
            profile=ground_navigation_profile(), target_ref="goal", controller_capabilities=["walk", "stairs"])
        assert not blocked["admitted"]


def test_ground_controller_commands_ignore_visual_target_and_robot_z():
    adapter = PlanarVelocityNavigationAdapter("ground", pose_reader=lambda: None)
    tracker = NodeRouteTracker([[2, 0, 99]], ground_navigation_profile(), ["goal"])
    target = tracker.advance(NavigationPose((0, 0, 0)))
    commands = []
    for target_z, pose_z in ((99, 0), (-99, 100)):
        observation = NavigationObservation(NavigationPose((0, 0, pose_z)), 1)
        adapter.apply_navigation_target(replace(target, position_m=(2, 0, target_z)), observation)
        commands.append(adapter.last_command)
    assert commands[0] == commands[1]


def test_replan_explicitly_rejects_wrong_domain():
    doc, profile = aerial(), aerial_navigation_profile()
    provider = NavigationProvider(doc, profile=profile)
    initial = request()
    provider.dispatch(initial)
    wrong = doc.to_dict()
    wrong["domain"] = "ground"
    update = deepcopy(initial)
    update["parameters"].update(intent="replan", world_state_seq=2)
    response = provider.control(update,
        observation=NavigationObservation(NavigationPose((0, 0, 4)), 2), topology=wrong)
    assert "domain" in response.private_evidence["failure"]["reason"]
    assert not response.plan.commands


@pytest.mark.parametrize("entry", ["direct", "provider", "executor"])
def test_ground_profile_rejects_aerial_before_any_motion(entry):
    doc, profile = aerial(), ground_navigation_profile()
    with pytest.raises(SkillContractError, match="domain"):
        if entry == "direct":
            select_node_route(doc, NavigationPose((0, 0, 2)), profile=profile, target_ref="goal")
        elif entry == "provider":
            NavigationProvider(doc, profile=profile).dispatch(request())
        else:
            executor = NavigationExecutor(topology=doc)
            adapter = SimpleNamespace(robot_id="r1", profile=profile, validate=lambda _: None,
                observe=lambda: pytest.fail("domain mismatch must precede observations"),
                apply_navigation_target=lambda _: pytest.fail("unexpected motion"), hold=lambda _: None,
                before_step=lambda: None, after_step=lambda: None, request_stop=lambda _: None)
            executor.register_adapter(adapter)
            executor.plan(request())


def test_aerial_profile_rejects_ground_and_invalid_mode():
    doc = TopologyDocument("office")
    doc.add_node([0, 0, 0], node_id="a")
    doc.add_node([1, 0, 0], node_id="goal")
    doc.add_edge("a", "goal", [[0, 0, 0], [1, 0, 0]])
    with pytest.raises(SkillContractError, match="domain"):
        NavigationProvider(doc, profile=aerial_navigation_profile()).dispatch(request())
    air = aerial().to_dict()
    air["nodes"][0]["edit_mode"] = "invalid"
    assert any("edit_mode" in error for error in validate_topology(air))
