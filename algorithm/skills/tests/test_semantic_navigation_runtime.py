"""Runtime behavior at the semantic-map/navigation boundary."""
import pytest

from algorithm.skills.contracts import SkillContractError
from algorithm.skills.navigation.landmarks import (
    landmark_inventory, resolve_landmark_binding, resolve_landmark_route,
)
from algorithm.skills.navigation.topology import route_graph_digest, validate_topology


def semantic_topology():
    return {
        "schema": "eai.navigation.topology",
        "version": 2,
        "coordinate_frame": "scene_world",
        "meters_per_unit": 1.0,
        "up_axis": "Z",
        "levels": [{"id": "1F", "label": "Ground", "support_z_m": 0.0}],
        "nodes": [
            {"id": "a", "position_xyz": [0.0, 0.0, 0.0], "target_refs": [],
             "level_id": "1F", "label": "A", "aliases": []},
            {"id": "b", "position_xyz": [1.0, 0.0, 0.0], "target_refs": [],
             "level_id": "1F", "label": "B", "aliases": []},
            {"id": "c", "position_xyz": [2.0, 0.0, 0.0], "target_refs": [],
             "level_id": "1F", "label": "C", "aliases": []},
        ],
        "edges": [
            {"id": "edge:ab", "from": "a", "to": "b", "directed": False,
             "traversal_mode": "walk", "required_controller_capabilities": []},
            {"id": "edge:bc", "from": "b", "to": "c", "directed": False,
             "traversal_mode": "walk", "required_controller_capabilities": []},
        ],
        "semantics": {
            "schema": "eai.scene.semantics",
            "version": 1,
            "regions": [
                {"id": "region:bound", "kind": "room", "label": "Lobby",
                 "aliases": ["entry"], "level_id": "1F", "entrance_node_ids": ["a"],
                 "polygon_xyz": [[0, 0, 0], [1, 0, 0], [1, 1, 0]], "height_m": 2.0},
                {"id": "region:unbound", "kind": "room", "label": "Unmapped",
                 "aliases": [], "level_id": "1F", "entrance_node_ids": [],
                 "polygon_xyz": [[3, 0, 0], [4, 0, 0], [4, 1, 0]], "height_m": 2.0},
            ],
            "entities": [
                {"id": "entity:reachable", "kind": "table", "label": "Table",
                 "aliases": ["desk"], "level_id": "1F", "region_id": "region:bound",
                 "position_xyz": [0.5, 0.5, 0.7]},
                {"id": "entity:unreachable", "kind": "chair", "label": "Chair",
                 "aliases": [], "level_id": "1F", "region_id": None,
                 "position_xyz": [3.5, 0.5, 0.4]},
            ],
            "interaction_poses": [
                {"id": "pose:approach", "kind": "inspect", "label": "Approach table",
                 "aliases": [], "owner_id": "entity:reachable", "level_id": "1F",
                 "position_xyz": [0.5, 0.5, 0.8], "heading_rad": 0.5, "node_id": "b"},
            ],
            "routes": [
                {"id": "route:lobby-to-table", "kind": "walk", "label": "Lobby to table",
                 "aliases": [], "node_ids": ["a", "b"], "edge_ids": ["edge:ab"]},
            ],
            "extensions": {},
        },
    }


def test_inventory_reports_unbound_semantics_but_route_resolution_fails_closed():
    inventory = landmark_inventory(semantic_topology())
    semantic_ids = {item["semantic_id"] for item in inventory if "semantic_id" in item}

    assert "region:unbound" in semantic_ids
    assert next(item for item in inventory if item.get("semantic_id") == "region:unbound")["node_ids"] == []
    with pytest.raises(SkillContractError, match="no navigation entrance"):
        resolve_landmark_route(
            semantic_topology(), origin_ref="region:bound", destination_ref="region:unbound",
        )


@pytest.mark.parametrize("change", ["region_entrance", "route_binding", "route_kind"])
def test_route_digest_tracks_semantic_bindings_that_change_the_executable_graph(change):
    graph = semantic_topology()
    before = route_graph_digest(graph)
    semantics = graph["semantics"]
    if change == "region_entrance":
        semantics["regions"][0]["entrance_node_ids"] = ["b"]
    elif change == "route_binding":
        semantics["routes"][0].update(node_ids=["b", "c"], edge_ids=["edge:bc"])
    elif change == "route_kind":
        graph["edges"][0]["traversal_mode"] = "stairs"
        semantics["routes"][0]["kind"] = "stairs"

    assert route_graph_digest(graph) != before


def test_task_semantic_changes_do_not_change_executable_graph_digest():
    graph = semantic_topology()
    before = route_graph_digest(graph)
    semantics = graph["semantics"]
    semantics["entities"][0]["region_id"] = "region:unbound"
    semantics["interaction_poses"][0].update(node_id="c", heading_rad=1.0)
    semantics["routes"][0]["label"] = "Alternate route name"

    assert route_graph_digest(graph) == before


def test_route_digest_ignores_semantic_geometry_that_does_not_change_navigation():
    graph = semantic_topology()
    before = route_graph_digest(graph)
    semantics = graph["semantics"]
    semantics["regions"][0]["polygon_xyz"][0][0] = -0.25
    semantics["entities"][0]["position_xyz"] = [0.8, 0.6, 0.7]
    semantics["interaction_poses"][0]["position_xyz"] = [0.8, 0.6, 0.8]

    assert route_graph_digest(graph) == before


def test_named_route_executes_its_declared_node_and_edge_order():
    graph = semantic_topology()
    route = graph["semantics"]["routes"][0]
    route["node_ids"] = ["a", "b", "c"]
    route["edge_ids"] = ["edge:ab", "edge:bc"]

    result = resolve_landmark_route(
        graph, origin_ref="route:lobby-to-table", destination_ref="route:lobby-to-table",
    )

    assert result["admitted"]
    assert result["topology_node_ids"] == ["a", "b", "c"]
    assert result["topology_edge_ids"] == ["edge:ab", "edge:bc"]


@pytest.mark.parametrize(
    "collection,field,value",
    [
        ("entities", "region_id", "region:missing"),
        ("interaction_poses", "owner_id", "entity:missing"),
        ("interaction_poses", "position_xyz", [0.0, 0.0, True]),
        ("interaction_poses", "heading_rad", float("inf")),
    ],
)
def test_runtime_rejects_broken_semantic_references(collection, field, value):
    graph = semantic_topology()
    graph["semantics"][collection][0][field] = value

    assert validate_topology(graph)


def test_runtime_rejects_named_route_whose_kind_disagrees_with_edge_traversal():
    graph = semantic_topology()
    graph["edges"][0]["traversal_mode"] = "stairs"

    assert any("traversal" in error for error in validate_topology(graph))


def test_runtime_rejects_duplicate_nodes_in_named_route():
    graph = semantic_topology()
    graph["semantics"]["routes"][0]["node_ids"] = ["a", "b", "a"]
    graph["semantics"]["routes"][0]["edge_ids"] = ["edge:ab", "edge:ab"]

    assert any("semantic route" in error for error in validate_topology(graph))


def test_semantic_binding_still_resolves_approach_node_and_heading():
    binding = resolve_landmark_binding(semantic_topology(), "desk")

    assert binding["node_ids"] == ("b",)
    assert binding["heading_rad"] == pytest.approx(0.5)


def test_entity_route_uses_heading_attached_to_the_selected_interaction_node():
    graph = semantic_topology()
    graph["semantics"]["interaction_poses"].append(
        {"id": "pose:z-alternate", "kind": "inspect", "label": "Inspect table",
         "aliases": [], "owner_id": "entity:reachable", "level_id": "1F",
         "position_xyz": [1.5, 0.5, 0.8], "heading_rad": 1.2, "node_id": "c"}
    )

    result = resolve_landmark_route(
        graph, origin_ref="c", destination_ref="desk",
    )

    assert result["admitted"]
    assert result["topology_node_ids"] == ["c"]
    assert result["target_heading_rad"] == pytest.approx(1.2)
    assert result["skill_parameters"]["target_yaw"] == pytest.approx(1.2)


def test_entity_route_does_not_guess_between_conflicting_headings_at_one_node():
    graph = semantic_topology()
    graph["semantics"]["interaction_poses"].append(
        {"id": "pose:z-conflicting", "kind": "inspect", "label": "Inspect table",
         "aliases": [], "owner_id": "entity:reachable", "level_id": "1F",
         "position_xyz": [0.5, 0.5, 0.8], "heading_rad": 1.2, "node_id": "b"}
    )

    assert resolve_landmark_binding(graph, "desk").get("heading_rad") is None
    result = resolve_landmark_route(graph, origin_ref="b", destination_ref="desk")

    assert result["admitted"]
    assert result["target_heading_rad"] is None
    assert "target_yaw" not in result["skill_parameters"]
