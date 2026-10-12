"""Stacked-floor routing/progress invariants; synthetic data, not physics evidence."""
from copy import deepcopy
import math

import pytest

from algorithm.skills.contracts import SkillContractError
from algorithm.skills.navigation.ground_navigation import (
    GroundPose, GroundNavigationProvider, NodeRouteTracker, select_node_route,
)
from algorithm.skills.navigation.landmarks import resolve_landmark_route, resolve_landmark
from algorithm.skills.navigation.topology import validate_ground_topology, route_graph_digest
from algorithm.skills.tests.test_ground_navigation import _request, _observation


def three_floors():
    # Same XY on all floors. Stairs have real XY progression, with an
    # intermediate support point between the first two landings.
    points = [(0, 0, 0), (1, 0, 1.5), (2, 0, 3), (0, 0, 3), (2, 0, 6), (0, 0, 6)]
    floors = ["1F", None, "2F", "2F", "3F", "3F"]
    return {"version": 2,
        "levels": [{"id": f"{i+1}F", "label": f"Floor {i+1}", "support_z_m": i*3} for i in range(3)],
        "nodes": [{"id": f"n{i}", "position_xyz": list(p), "level_id": floors[i],
                   "kind": "transition" if floors[i] is None else "custom",
                   "target_refs": [f"place:{i}"], "label": "meeting" if i in (3,5) else f"point{i}",
                   "aliases": ["lobby"] if i == 0 else []} for i,p in enumerate(points)],
        "edges": [{"id": f"e{i}", "from": f"n{i}", "to": f"n{i+1}", "directed": True,
                   "traversal_mode": mode} for i,mode in enumerate(("stairs", "stairs", "walk", "stairs", "walk"))]}


def test_semantic_route_outputs_xyz_and_same_graph_identity_as_execution():
    graph = three_floors()
    route = resolve_landmark_route(graph, origin_ref="lobby", destination_ref="meeting",
                                   destination_level_id="3F", controller_capabilities=("walk", "stairs"))
    assert route["admitted"] and route["topology_node_ids"] == [f"n{i}" for i in range(6)]
    assert route["route_points_m"][-1] == [0,0,6]
    assert route["route_level_ids"] == ["1F",None,"2F","2F","3F","3F"]
    assert route["route_length_m"] > 10
    assert route["skill_parameters"]["route_graph_digest"] == route_graph_digest(graph)
    actual = select_node_route(graph, GroundPose((0,0,.42),0,support_z_m=0),
                               target_refs=(route["target_ref"],), origin_ref=route["origin_ref"],
                               controller_capabilities=("walk","stairs"))
    assert actual["route_points_m"] == route["route_points_m"]


@pytest.mark.parametrize("query,reason", [("meeting","ambiguous"),("conference123","unknown")])
def test_unresolved_semantics_never_guess_a_floor(query, reason):
    with pytest.raises(SkillContractError, match=reason):
        resolve_landmark(three_floors(), query)


def test_exact_reference_survives_an_alias_collision():
    graph = three_floors()
    graph["nodes"][0]["aliases"] = ["place:5"]
    assert resolve_landmark(graph, "place:5")["id"] == "n5"


def test_explicit_reference_identity_is_not_replaced_by_another_binding():
    graph = three_floors()
    graph["nodes"][5]["target_refs"].insert(0, "place:aaa")
    route = resolve_landmark_route(graph, origin_ref="n0", destination_ref="place:5",
                                    controller_capabilities=("walk", "stairs"))
    assert route["origin_ref"] == "n0"
    assert route["target_ref"] == "place:5"
    graph["nodes"][0]["target_refs"].append("place:collision")
    graph["nodes"][5]["id"] = "place:collision"
    assert any("conflicts with another node id" in e for e in validate_ground_topology(graph))


def test_start_selection_uses_actual_support_floor_not_body_height_or_xy():
    graph = three_floors()
    route = select_node_route(graph, GroundPose((0,0,3.65),0,support_z_m=3),
                              target_refs=("place:5",),controller_capabilities=("walk","stairs"))
    assert route["topology_node_ids"][0] == "n3"
    assert not route["already_at_target"]
    wrong = select_node_route(graph, GroundPose((0,0,.5),0,support_z_m=0),
                              origin_ref="place:3",target_refs=("place:5",),controller_capabilities=("walk","stairs"))
    assert not wrong["admitted"]


def test_v2_refuses_missing_support_or_xy_only_goal_and_distant_start():
    graph = three_floors()
    with pytest.raises(SkillContractError, match="support"):
        select_node_route(graph,GroundPose((0,0,0),0),target_refs=("place:5",))
    with pytest.raises(SkillContractError, match="XYZ"):
        select_node_route(graph,GroundPose((0,0,.5),0,0),target_pose=(0,0))
    assert not select_node_route(graph,GroundPose((20,0,.5),0,0),target_refs=("place:5",))["admitted"]


def test_directed_stairs_and_controller_capabilities_are_real_admission_constraints():
    graph = three_floors()
    assert not resolve_landmark_route(graph,origin_ref="lobby",destination_ref="place:5")["admitted"]
    assert not resolve_landmark_route(graph,origin_ref="place:5",destination_ref="lobby",
                                      controller_capabilities=("walk","stairs"))["admitted"]
    graph["edges"][0]["required_controller_capabilities"] = ["narrow_stairs"]
    assert not resolve_landmark_route(graph,origin_ref="lobby",destination_ref="place:5",
                                      controller_capabilities=("walk","stairs"))["admitted"]


@pytest.mark.parametrize("change", ["floor","mode","capability","alias"])
def test_route_identity_includes_multilevel_admission_and_semantics(change):
    graph = three_floors()
    before = route_graph_digest(graph)
    if change == "floor": graph["levels"][0]["support_z_m"] = .1
    if change == "mode": graph["edges"][0]["traversal_mode"] = "ramp"
    if change == "capability": graph["edges"][0]["required_controller_capabilities"] = ["narrow"]
    if change == "alias": graph["nodes"][0]["aliases"].append("大厅")
    assert route_graph_digest(graph) != before
    reversed_graph = deepcopy(graph)
    for name in ("nodes","edges","levels"): reversed_graph[name].reverse()
    assert route_graph_digest(graph) == route_graph_digest(reversed_graph)


@pytest.mark.parametrize("change", ["duplicate_ref","bad_level","walk_crossing","vertical","bad_version","missing_mode"])
def test_invalid_multilevel_topology_fails_validation(change):
    graph = three_floors()
    if change == "duplicate_ref": graph["nodes"][1]["target_refs"] = ["place:0"]
    if change == "bad_level": graph["nodes"][0]["level_id"] = "4F"
    if change == "walk_crossing": graph["edges"][0]["traversal_mode"] = "walk"
    if change == "vertical": graph["nodes"][1]["position_xyz"][:2] = [0,0]
    if change == "bad_version": graph["version"] = True
    if change == "missing_mode": del graph["edges"][0]["traversal_mode"]
    assert validate_ground_topology(graph)


def test_declared_floor_bands_cannot_overlap_arrival_height_tolerance():
    graph=three_floors()
    graph["levels"][1]["support_z_m"]=.1
    for node in graph["nodes"]:
        if node["level_id"]=="2F":
            node["position_xyz"][2]=.1
    assert any("floor height bands overlap" in e for e in validate_ground_topology(graph))
    with pytest.raises(SkillContractError,match="floor height bands overlap"):
        select_node_route(graph,GroundPose((0,0,.4),0,0),target_refs=("place:3",))


def test_tracker_never_advances_from_wrong_floor_and_only_commands_planar_motion():
    tracker = NodeRouteTracker([(0,0,0),(2,0,3),(0,0,6)],height_aware=True)
    assert tracker.step(GroundPose((0,0,.5),0,0)) == [.5,0,0]
    assert tracker.node_index == 1
    with pytest.raises(SkillContractError,match="wrong support height"):
        tracker.step(GroundPose((2,0,.5),0,0))
    assert tracker.node_index == 1 and not tracker.arrived
    tracker.step(GroundPose((2,0,3.8),math.pi,3))
    assert tracker.node_index == 2
    assert tracker.step(GroundPose((0,0,6.4),0,6)) == [0,0,0]
    assert tracker.arrived


def test_provider_requires_host_capabilities_and_freezes_resolved_identity():
    graph = three_floors()
    request = _request(robot_id="go2_1",origin_ref="place:0",route_graph_digest=route_graph_digest(graph))
    request["target_ref"] = "place:5"
    observation = _observation(topology=graph,pose=GroundPose((0,0,.4),0,0))
    provider = GroundNavigationProvider()
    provider.dispatch(request,observation=observation)
    response=provider.control(request,observation=observation)
    assert response.state["status"] == "failed" and response.plan.commands[0].payload["velocity"] == [0,0,0]
    provider=GroundNavigationProvider(controller_capabilities={"go2_1":("walk","stairs")})
    provider.dispatch(request,observation=observation)
    graph["edges"][0]["directed"] = False
    response=provider.control(request,observation=observation)
    assert response.state["status"] == "failed" and response.plan.commands[0].payload["velocity"] == [0,0,0]


@pytest.mark.parametrize("alias",[False,True])
def test_route_cli_never_overwrites_topology_input(tmp_path,alias):
    from tools.topology_authoring.model import TopologyDocument
    from tools.topology_authoring.product import save_draft
    from tools.topology_authoring.route import main
    doc=TopologyDocument("test")
    doc.add_node([0,0,0],node_id="a",target_refs=["place:a"])
    doc.add_node([1,0,0],node_id="b",target_refs=["place:b"])
    doc.add_edge("a","b",[[0,0,0],[1,0,0]])
    path=tmp_path/"topology.json";save_draft(path,doc)
    before=path.read_bytes()
    output=tmp_path/"alias.json" if alias else path
    if alias: output.symlink_to(path)
    with pytest.raises(SystemExit):
        main(["--topology",str(path),"--origin","place:a","--destination","place:b","--output",str(output)])
    assert path.read_bytes()==before
