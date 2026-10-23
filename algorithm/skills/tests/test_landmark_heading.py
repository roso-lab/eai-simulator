"""Authored landmark and final-heading semantics, without request NLP."""
import math

import pytest

from algorithm.skills.contracts import SkillContractError
from algorithm.skills.navigation import (
    NavigationPose,
    NavigationProvider,
    NodeRouteTracker,
    ground_navigation_profile,
)
from algorithm.skills.navigation.landmarks import landmark_inventory, resolve_landmark_route
from algorithm.skills.navigation.topology import route_graph_digest, validate_topology
from algorithm.skills.tests.test_ground_navigation import _observation, _request
from algorithm.skills.tests.test_multilevel_navigation import three_floors
from tools.topology_authoring.model import TopologyDocument
from tools.topology_authoring.product import load_product, save_draft


def named_map():
    graph = three_floors()
    for node in graph["nodes"]:
        node["target_refs"] = []
    graph["nodes"][0].update(label="pantry", aliases=["tea_room"], heading_rad=0.0)
    graph["nodes"][3].update(label="meeting_room", heading_rad=math.pi / 2)
    graph["nodes"][5].update(label="meeting_room", heading_rad=-math.pi / 2)
    return graph


def test_english_labels_resolve_floor_and_export_authored_heading():
    graph = named_map()
    route = resolve_landmark_route(
        graph, origin_ref="tea_room", destination_ref="meeting_room",
        destination_level_id="2F", controller_capabilities=("walk", "stairs"),
    )
    assert route["admitted"] and route["topology_node_ids"] == ["n0", "n1", "n2", "n3"]
    assert route["route_points_m"][-1] == [0.0, 0.0, 3.0]
    assert route["route_headings_rad"] == [0.0, None, None, math.pi / 2]
    assert route["target_ref"] == "meeting_room"
    assert route["skill_parameters"]["target_yaw"] == math.pi / 2
    with pytest.raises(SkillContractError, match="ambiguous"):
        resolve_landmark_route(graph, origin_ref="pantry", destination_ref="meeting_room")
    before = route_graph_digest(graph)
    graph["nodes"][3]["heading_rad"] = 0.0
    assert route_graph_digest(graph) != before


def test_model_heading_normalization_history_and_persistence(tmp_path):
    doc = TopologyDocument("pose_test")
    doc.set_levels([{"id": "1F", "label": "Floor 1", "support_z_m": 0.0}])
    doc.begin_node_preview((0, 0, 0), heading_rad=5 * math.pi / 2)
    node = doc.confirm_node_preview(level_id="1F", label="pantry_1f")
    assert doc.node(node)["heading_rad"] == pytest.approx(math.pi / 2)
    doc.set_node_heading(node, None)
    assert "heading_rad" not in doc.node(node)
    assert doc.undo() and doc.node(node)["heading_rad"] == pytest.approx(math.pi / 2)
    assert doc.redo() and "heading_rad" not in doc.node(node)
    doc.set_node_heading(node, -math.pi / 2)
    save_draft(tmp_path / "draft.json", doc)
    assert load_product(tmp_path / "draft.json").to_dict() == doc.to_dict()
    before = doc.to_dict()
    with pytest.raises(ValueError):
        doc.set_node_heading(node, math.inf)
    assert doc.to_dict() == before
    with pytest.raises(ValueError):
        TopologyDocument("v1").add_node((0, 0, 0), heading_rad=0.0)


@pytest.mark.parametrize("heading", [True, "90", math.inf, math.nan, 4.0])
def test_invalid_heading_is_rejected_before_route_selection(heading):
    graph = named_map()
    graph["nodes"][0]["heading_rad"] = heading
    assert validate_topology(graph)


def test_destination_orientation_requires_position_and_yaw_together():
    tracker = NodeRouteTracker(
        [(0, 0, 0), (1, 0, 3)], ground_navigation_profile(),
        ("n0", "n1"), math.pi / 2,
    )
    assert tracker.advance(NavigationPose((0, 0, 9), 0)).node_id == "n1"
    target = tracker.advance(NavigationPose((1, 0, -7), 0))
    assert target is not None and target.heading_rad == pytest.approx(math.pi / 2)
    # Drifting outside the final radius resumes positional targeting.
    assert tracker.advance(NavigationPose((0.4, 0, 3), math.pi / 2)).node_id == "n1"
    assert tracker.advance(NavigationPose((1, 0, 3), math.pi / 2)) is None
    assert tracker.arrived


def test_final_heading_uses_short_angle_across_pi():
    tracker = NodeRouteTracker(
        [(0, 0, 0)], ground_navigation_profile(), ("n0",), -math.pi + 0.01,
    )
    assert tracker.advance(NavigationPose((0, 0, 0), math.pi - 0.01)) is None
    assert tracker.arrived


def test_provider_aligns_authored_heading_and_rejects_target_yaw_mutation():
    graph = named_map()
    provider = NavigationProvider(
        graph, profile=ground_navigation_profile(controller_capabilities=("walk", "stairs")),
    )
    request = _request(robot_id="go2_1", origin_ref="n3")
    request["target_ref"] = "n3"
    observation = _observation((0, 0, 3), 0)
    provider.dispatch(request, observation=observation)
    response = provider.control(request, observation=observation)
    assert response.state["status"] == "pending"
    assert response.plan.commands[0].payload["target_yaw"] == pytest.approx(math.pi / 2)
    request["parameters"]["target_yaw"] = 0.0
    response = provider.control(request, observation=observation)
    assert response.state["status"] == "failed" and response.plan.commands == ()


def test_provider_uses_authored_heading_without_redundant_parameter():
    graph = named_map()
    provider = NavigationProvider(
        graph, profile=ground_navigation_profile(controller_capabilities=("walk", "stairs")),
    )
    request = _request(robot_id="go2_1", origin_ref="n3")
    request["target_ref"] = "n3"
    provider.dispatch(request, observation=_observation((0, 0, 3), 0))
    assert provider.control(request, observation=_observation((0, 0, 3), 0)).state["status"] == "pending"
    response = provider.control(request, observation=_observation((0, 0, 3), math.pi / 2))
    assert response.state["status"] == "completed"


def test_hold_cannot_rebase_immutable_dispatch_heading():
    graph = named_map()
    provider = NavigationProvider(
        graph, profile=ground_navigation_profile(controller_capabilities=("walk", "stairs")),
    )
    request = _request(robot_id="go2_1", origin_ref="n3", target_yaw=math.pi / 2)
    request["target_ref"] = "n3"
    provider.dispatch(request, observation=_observation((0, 0, 3), 0))
    request["parameters"].update(intent="hold", target_yaw=0.0)
    response = provider.control(request, observation=_observation((0, 0, 3), 0))
    assert response.state["status"] == "failed" and response.plan.commands == ()


@pytest.mark.parametrize("heading", [0.0, math.pi / 2])
def test_semantic_same_node_route_does_not_certify_unmeasured_heading(heading):
    graph = named_map()
    graph["nodes"][0]["heading_rad"] = heading
    route = resolve_landmark_route(graph, origin_ref="pantry", destination_ref="pantry")
    assert route["admitted"] and not route["already_at_target"]
    assert route["skill_parameters"]["target_yaw"] == heading


def test_v1_landmark_inventory_retains_exact_fields():
    doc = TopologyDocument("legacy")
    doc.add_node((0, 0, 0), node_id="n0", target_refs=["place:origin"])
    assert landmark_inventory(doc) == [{
        "node_id": "n0", "refs": ["place:origin"], "label": "", "aliases": [],
        "level_id": None, "position_xyz": [0.0, 0.0, 0.0],
    }]
