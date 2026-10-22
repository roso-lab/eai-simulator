"""Stacked-floor authored-route invariants; synthetic, not physics evidence."""
from copy import deepcopy

import pytest

from algorithm.skills.contracts import SkillContractError
from algorithm.skills.navigation import (
    NavigationPose,
    NavigationProvider,
    NodeRouteTracker,
    aerial_navigation_profile,
    ground_navigation_profile,
    select_node_route,
)
from algorithm.skills.navigation.landmarks import resolve_landmark, resolve_landmark_route
from algorithm.skills.navigation.topology import route_graph_digest, validate_topology
from algorithm.skills.tests.test_ground_navigation import _observation, _request


def three_floors():
    # Same XY locations recur on different levels; only authored edges connect them.
    points = [(0, 0, 0), (1, 0, 1.5), (2, 0, 3), (0, 0, 3), (2, 0, 6), (0, 0, 6)]
    floors = ["1F", None, "2F", "2F", "3F", "3F"]
    return {
        "version": 2,
        "levels": [
            {"id": f"{index + 1}F", "label": f"Floor {index + 1}", "support_z_m": index * 3}
            for index in range(3)
        ],
        "nodes": [
            {
                "id": f"n{index}", "position_xyz": list(point), "level_id": floors[index],
                "kind": "transition" if floors[index] is None else "custom",
                "target_refs": [f"place:{index}"],
                "label": "meeting" if index in (3, 5) else f"point{index}",
                "aliases": ["lobby"] if index == 0 else [],
            }
            for index, point in enumerate(points)
        ],
        "edges": [
            {
                "id": f"e{index}", "from": f"n{index}", "to": f"n{index + 1}",
                "directed": True, "traversal_mode": mode,
            }
            for index, mode in enumerate(("stairs", "stairs", "walk", "stairs", "walk"))
        ],
    }


def test_semantic_route_and_execution_router_share_xyz_graph_identity():
    graph = three_floors()
    route = resolve_landmark_route(
        graph, origin_ref="lobby", destination_ref="meeting", destination_level_id="3F",
        controller_capabilities=("walk", "stairs"),
    )
    assert route["admitted"] and route["topology_node_ids"] == [f"n{i}" for i in range(6)]
    assert route["route_points_m"][-1] == [0.0, 0.0, 6.0]
    assert route["route_level_ids"] == ["1F", None, "2F", "2F", "3F", "3F"]
    assert route["skill_parameters"]["route_graph_digest"] == route_graph_digest(graph)
    actual = select_node_route(
        graph, NavigationPose((0, 0, 0)), profile=ground_navigation_profile(),
        target_ref="n5", origin_ref="n0",
        controller_capabilities=("walk", "stairs"),
    )
    assert actual["route_points_m"] == route["route_points_m"]


@pytest.mark.parametrize("query,reason", [("meeting", "ambiguous"), ("conference123", "unknown")])
def test_unresolved_semantics_never_guess_a_floor(query, reason):
    with pytest.raises(SkillContractError, match=reason):
        resolve_landmark(three_floors(), query)


def test_exact_reference_survives_alias_collision_and_broken_node_identity_fails_validation():
    graph = three_floors()
    graph["nodes"][0]["aliases"] = ["place:5"]
    assert resolve_landmark(graph, "place:5")["id"] == "n5"
    graph["nodes"][0]["target_refs"].append("place:collision")
    graph["nodes"][5]["id"] = "place:collision"
    assert any("invalid edge endpoints" in error for error in validate_topology(graph))


def test_explicit_origin_is_required_instead_of_retired_body_height_inference():
    graph = three_floors()
    # XY ground navigation does not infer a floor from chassis/body height.
    route = select_node_route(
        graph, NavigationPose((0, 0, 3.65)), profile=ground_navigation_profile(),
        origin_ref="place:3", target_ref="place:5",
        controller_capabilities=("walk", "stairs"),
    )
    assert route["admitted"] and route["topology_node_ids"][0] == "n3"
    distant = select_node_route(
        graph, NavigationPose((20, 0, 3)), profile=ground_navigation_profile(),
        origin_ref="place:3", target_ref="place:5",
        controller_capabilities=("walk", "stairs"),
    )
    assert not distant["admitted"] and distant["reason"] == "origin_not_reached"


def test_xyz_profile_does_not_confuse_stacked_nodes_with_same_xy():
    graph = deepcopy(three_floors())
    graph["domain"] = "aerial"
    result = select_node_route(
        graph, NavigationPose((0, 0, 3)), profile=aerial_navigation_profile(),
        origin_ref="place:3", target_ref="place:5",
        controller_capabilities=("walk", "stairs"),
    )
    assert result["admitted"] and result["topology_node_ids"] == ["n3", "n4", "n5"]


def test_directed_stairs_and_capabilities_are_admission_constraints():
    graph = three_floors()
    assert not resolve_landmark_route(
        graph, origin_ref="place:5", destination_ref="lobby",
        controller_capabilities=("walk", "stairs"),
    )["admitted"]
    graph["edges"][0]["required_controller_capabilities"] = ["narrow_stairs"]
    assert not resolve_landmark_route(
        graph, origin_ref="lobby", destination_ref="place:5",
        controller_capabilities=("walk", "stairs"),
    )["admitted"]


@pytest.mark.parametrize("change", ["floor", "mode", "capability", "alias"])
def test_route_identity_includes_multilevel_admission_and_semantics(change):
    graph = three_floors()
    before = route_graph_digest(graph)
    if change == "floor":
        graph["levels"][0]["support_z_m"] = 0.1
    elif change == "mode":
        graph["edges"][0]["traversal_mode"] = "ramp"
    elif change == "capability":
        graph["edges"][0]["required_controller_capabilities"] = ["narrow"]
    else:
        graph["nodes"][0]["aliases"].append("大厅")
    assert route_graph_digest(graph) != before
    reversed_graph = deepcopy(graph)
    for name in ("nodes", "edges", "levels"):
        reversed_graph[name].reverse()
    assert route_graph_digest(graph) == route_graph_digest(reversed_graph)


@pytest.mark.parametrize("change", ["bad_level", "walk_crossing", "vertical", "bad_version"])
def test_invalid_multilevel_topology_fails_validation(change):
    graph = three_floors()
    if change == "bad_level":
        graph["nodes"][0]["level_id"] = "4F"
    elif change == "walk_crossing":
        graph["edges"][0]["traversal_mode"] = "walk"
    elif change == "vertical":
        graph["nodes"][1]["position_xyz"][:2] = [0, 0]
    else:
        graph["version"] = True
    assert validate_topology(graph)


def test_tracker_keeps_authored_order_with_planar_ground_metric():
    tracker = NodeRouteTracker(
        [(0, 0, 0), (2, 0, 3), (0, 0, 6)], ground_navigation_profile(),
        ("n0", "n2", "n5"),
    )
    assert tracker.advance(NavigationPose((0, 0, 0))).node_id == "n2"
    assert tracker.advance(NavigationPose((0, 0, 6))).node_id == "n2"
    assert tracker.advance(NavigationPose((2, 0, 99))).node_id == "n5"
    assert tracker.advance(NavigationPose((0, 0, -9))) is None


def test_provider_binds_host_capabilities_and_frozen_graph_digest():
    graph = three_floors()
    request = _request(
        robot_id="go2_1", origin_ref="place:0", route_graph_digest=route_graph_digest(graph),
    )
    request["target_ref"] = "place:5"
    observation = _observation(position=(0, 0, 0))

    no_stairs = NavigationProvider(graph, profile=ground_navigation_profile(
        controller_capabilities=("walk",)
    ))
    no_stairs.dispatch(request, observation=observation)
    assert no_stairs.control(request, observation=observation).state["reason_code"] == "route_unreachable"

    provider = NavigationProvider(graph, profile=ground_navigation_profile(
        controller_capabilities=("walk", "stairs")
    ))
    provider.dispatch(request, observation=observation)
    graph["edges"][0]["directed"] = False
    response = provider.control(request, observation=observation, topology=graph)
    assert response.state["status"] == "failed"
    assert response.state["reason_code"] == "invalid_state"


@pytest.mark.parametrize("alias", [False, True])
def test_route_cli_never_overwrites_topology_input(tmp_path, alias):
    from tools.topology_authoring.model import TopologyDocument
    from tools.topology_authoring.product import save_draft
    from tools.topology_authoring.route import main

    doc = TopologyDocument("test")
    doc.add_node([0, 0, 0], node_id="a", target_refs=["place:a"])
    doc.add_node([1, 0, 0], node_id="b", target_refs=["place:b"])
    doc.add_edge("a", "b", [[0, 0, 0], [1, 0, 0]])
    path = tmp_path / "topology.json"
    save_draft(path, doc)
    before = path.read_bytes()
    output = tmp_path / "alias.json" if alias else path
    if alias:
        output.symlink_to(path)
    with pytest.raises(SystemExit):
        main(["--topology", str(path), "--origin", "place:a", "--destination", "place:b", "--output", str(output)])
    assert path.read_bytes() == before
