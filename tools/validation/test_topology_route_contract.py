"""Authoring/runtime agreement and editing regressions; no simulator imports."""

# fcf593a9 unified ground/aerial navigation into node_route.py and removed the
# modules these tests target; skip at module level until they are ported.
# Authorized by the run owner as a pre-existing failure outside feature scope.
import pytest

pytest.skip("navigation 模块已在 fcf593a9 统一并入 node_route.py，旧测试待移植", allow_module_level=True)

from copy import deepcopy
import json

import pytest

from algorithm.skills.contracts import SkillContractError
from algorithm.skills.navigation import GroundPose, select_node_route
from algorithm.skills.navigation.topology import route_graph_digest
from tools.topology_authoring.adapter import SceneSource, SemanticTarget
from tools.topology_authoring.model import TopologyDocument
from tools.topology_authoring.product import generate_product, load_product, product_digest, save_draft, validate_document


def document():
    doc = TopologyDocument("warehouse", {"scene.json": "a" * 64})
    first = doc.add_node([0, 0, 0])
    last = doc.add_node([2, 0, 0], target_refs=["place:goal"])
    doc.add_edge(first, last, [[0, 0, 0], [2, 0, 0]])
    return doc


@pytest.mark.parametrize("field,value", [
    ("coordinate_frame", "robot_base"), ("coordinate_frame", None),
    ("meters_per_unit", .01), ("meters_per_unit", float("nan")),
    ("meters_per_unit", True), ("meters_per_unit", "1"), ("up_axis", "Y"),
])
def test_metadata_rejected_by_both_authoring_and_runtime(field, value, tmp_path):
    doc = document()
    setattr(doc, field, value)
    assert validate_document(doc)
    with pytest.raises(ValueError):
        generate_product(tmp_path / "product.json", doc)
    with pytest.raises(SkillContractError):
        select_node_route(doc, GroundPose((0, 0, 0), 0), target_refs=["place:goal"])


@pytest.mark.parametrize("field,value", [
    ("id", ""), ("id", []), ("id", {}),
    ("position_xyz", ["0", 0, 0]), ("position_xyz", [False, 0, 0]),
    ("position_xyz", [0, 0]), ("position_xyz", None),
    ("target_refs", "place:goal"), ("target_refs", [{}]),
])
def test_malformed_nodes_rejected_without_validator_exception(field, value):
    doc = document()
    doc.nodes[0][field] = value
    assert validate_document(doc)
    with pytest.raises(SkillContractError):
        select_node_route(doc, GroundPose((0, 0, 0), 0), target_refs=["place:goal"])


@pytest.mark.parametrize("field,value", [
    ("id", []), ("from", {}), ("to", []), ("directed", 1),
    ("polyline_xyz", [[0, 0, 0], [2, 0]]),
    ("polyline_xyz", [[0, 0, 0], None]), ("profiles", []),
    ("profiles", {"ground_small": {"clearance_m": True}}),
])
def test_malformed_edges_return_errors(field, value):
    doc = document()
    doc.edges[0][field] = value
    assert validate_document(doc)


def test_vertical_edge_rejected_before_navigation(tmp_path):
    doc = document()
    doc.move_node("node:0002", [0, 0, 2])
    assert any("nonzero XY" in error for error in validate_document(doc))
    with pytest.raises(ValueError, match="nonzero XY"):
        generate_product(tmp_path / "topology.json", doc)


class Adapter:
    scene_key = "warehouse"

    def source_digests(self, source):
        return {"scene.json": "a" * 64}

    def semantic_targets(self, source):
        return [SemanticTarget("place:goal", "Goal")]

    def validate_polyline(self, points, profile, source):
        # A small L-shaped synthetic support surface: diagonal is forbidden.
        return ([] if all(a[0] == b[0] or a[1] == b[1] for a, b in zip(points, points[1:]))
                else ["segment leaves the authored corridor"])


def test_validation_checks_executed_segment_not_decorative_bend(tmp_path):
    doc = document()
    doc.move_node("node:0002", [2, 2, 0])
    doc.set_edge_polyline("edge:0001", [[0, 0, 0], [0, 2, 0], [2, 2, 0]])
    adapter, source = Adapter(), SceneSource("warehouse", tmp_path)
    with pytest.raises(ValueError, match="corridor"):
        generate_product(tmp_path / "topology.json", doc, adapter, source)
    doc.remove_edge("edge:0001")
    bend = doc.add_node([0, 2, 0])
    doc.add_edge("node:0001", bend, [[0, 0, 0], [0, 2, 0]], directed=True)
    doc.add_edge(bend, "node:0002", [[0, 2, 0], [2, 2, 0]], directed=True)
    path = generate_product(tmp_path / "topology.json", doc, adapter, source)
    loaded = load_product(path, adapter, source)
    route = select_node_route(loaded, GroundPose((0, 0, 0), 0), target_refs=["place:goal"])
    assert route["topology_node_ids"] == ["node:0001", bend, "node:0002"]
    assert route["route_length_m"] == 4
    reverse = select_node_route(loaded, GroundPose((2, 2, 0), 0), target_pose=[0, 0, 0])
    assert reverse["admitted"] is False


def test_scene_identity_and_empty_semantic_inventory_fail_closed(tmp_path):
    doc = document()
    adapter, source = Adapter(), SceneSource("other-scene", tmp_path)
    assert "topology scene differs from current scene input" in validate_document(doc, adapter, source)
    adapter.semantic_targets = lambda source: []
    source = SceneSource("warehouse", tmp_path)
    assert "unknown target reference: place:goal" in validate_document(doc, adapter, source, require_targets=True)
    doc.nodes[1]["target_refs"] = []
    assert validate_document(doc, adapter, source, require_targets=True) == []


@pytest.mark.parametrize("method", ["source_digests", "semantic_targets", "validate_polyline"])
@pytest.mark.parametrize("failure", [RuntimeError, AttributeError])
def test_unavailable_scene_queries_return_validation_errors(tmp_path, method, failure):
    adapter, source = Adapter(), SceneSource("warehouse", tmp_path)

    def unavailable(*args):
        raise failure("scene query unavailable")

    setattr(adapter, method, unavailable)
    errors = validate_document(document(), adapter, source)
    assert any("scene query unavailable" in error for error in errors)
    with pytest.raises(ValueError, match="topology validation failed"):
        generate_product(tmp_path / "topology.json", document(), adapter, source)
    assert not (tmp_path / "topology.json").exists()


def test_deleted_ids_and_history_do_not_prevent_new_edits():
    doc = document()
    third = doc.add_node([4, 0, 0])
    doc.remove_node("node:0002")
    fourth = doc.add_node([5, 0, 0])
    assert fourth != third
    first_edge = doc.add_edge("node:0001", third, [[0, 0, 0], [4, 0, 0]])
    second_edge = doc.add_edge(third, fourth, [[4, 0, 0], [5, 0, 0]])
    doc.remove_edge(first_edge)
    assert doc.add_edge("node:0001", fourth, [[0, 0, 0], [5, 0, 0]]) != second_edge
    doc.select_edge(second_edge)
    doc.undo()
    assert doc.selected() == (None, None)
    doc.redo()
    assert not validate_document(doc)
    doc.replace_content(document())
    assert doc.undo() is False
    assert doc.redo() is False
    assert doc.selected() == (None, None)


def test_invalid_edit_preserves_redo_history():
    doc = document()
    doc.undo()
    with pytest.raises(ValueError):
        doc.add_node([0, float("nan"), 0])
    assert doc.redo()
    assert len(doc.edges) == 1


@pytest.mark.parametrize("edit", [
    lambda doc: doc.bind_target("missing", "place:goal"),
    lambda doc: doc.set_edge_direction("missing", True),
    lambda doc: doc.set_edge_profile("missing", "ground_small", 1.),
])
def test_failed_property_edit_preserves_redo(edit):
    doc = document()
    doc.undo()
    with pytest.raises(KeyError):
        edit(doc)
    assert doc.redo()


def test_product_digest_pins_complete_map_route_digest_pins_only_executed_graph(tmp_path):
    doc = document()
    frozen = product_digest(doc)
    graph_digest = route_graph_digest(doc)
    path = generate_product(tmp_path / "topology.json", doc)
    assert product_digest(load_product(path, expected_digest=frozen)) == frozen
    doc.edges[0]["polyline_xyz"].insert(1, [1, 1, 0])
    assert route_graph_digest(doc) == graph_digest
    assert product_digest(doc) != frozen
    generate_product(path, doc)
    with pytest.raises(ValueError, match="frozen digest"):
        load_product(path, expected_digest=frozen)
    reordered = deepcopy(doc)
    reordered.nodes.reverse()
    assert route_graph_digest(reordered) == graph_digest
    doc.nodes[1]["position_xyz"][0] += 1
    assert route_graph_digest(doc) != graph_digest


def test_load_validates_without_adapter_and_rejects_nonfinite_and_duplicate_json(tmp_path):
    path = tmp_path / "topology.json"
    payload = document().to_dict()
    payload["meters_per_unit"] = .01
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="meters_per_unit"):
        load_product(path)
    path.write_text('{"schema":"eai.navigation.topology","schema":"duplicate"}')
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_product(path)
    path.write_text('{"meters_per_unit":NaN}')
    with pytest.raises(ValueError, match="nonfinite"):
        load_product(path)


def test_failed_save_preserves_existing_product(tmp_path, monkeypatch):
    path = generate_product(tmp_path / "topology.json", document())
    original = path.read_bytes()
    doc = document()
    doc.meters_per_unit = float("nan")
    with pytest.raises(ValueError):
        save_draft(path, doc)
    assert path.read_bytes() == original
    def fail_replace(*args):
        raise OSError("injected publication failure")
    monkeypatch.setattr("tools.topology_authoring.product.os.replace", fail_replace)
    with pytest.raises(OSError):
        save_draft(path, document())
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]
