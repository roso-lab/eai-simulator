"""Editable scene facts and task additions over the real XYZ navigation graph."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from algorithm.skills.navigation.landmarks import resolve_landmark_route
from algorithm.skills.navigation.topology import route_graph_digest
from tools.topology_authoring import author
from tools.topology_authoring.adapters.empty import EmptyTopologyAdapter
from tools.topology_authoring.adapters.usd_scene import UsdSceneAdapter
from tools.topology_authoring.model import TopologyDocument
from tools.topology_authoring.product import generate_product, load_product, product_digest, save_draft, validate_document
from tools.topology_authoring.semantics import empty_semantics, point_in_region, resolve_semantic, validate_semantics
from tools.topology_authoring.ui import TopologyAuthoringWindow
from tools.topology_authoring.viewport import draw_overlay, visible_semantics


def document():
    doc = TopologyDocument("house")
    doc.set_levels([{"id":"ground", "label":"Ground", "support_z_m":0.0},
                    {"id":"upper", "label":"Upper", "support_z_m":2.0}])
    doc.add_node([0,0,0], node_id="a", level_id="ground", label="Lobby")
    doc.add_node([3,1,2], node_id="b", level_id="upper", label="Bedroom")
    doc.upsert_semantic("regions", region())
    return doc


def region():
    return {"id":"room:lobby", "label":"Lobby", "aliases":["entrance"], "kind":"room",
            "level_id":"ground", "polygon_xyz":[[-1,-1,0],[1,-1,0],[1,1,0],[-1,1,0]],
            "height_m":2.5, "entrance_node_ids":["a"], "properties":{}}


def stairs(doc, directed=False):
    doc.add_waypoint_route(route_id="stairs:main", label="Main stairs", start_node_id="a",
        end_node_id="b", intermediate_xyz=[[1,0,.8],[2,1,1.5]], traversal_mode="stairs",
        directed=directed, required_controller_capabilities=("payload_stable",))


def entity():
    return {"id":"table:1", "label":"Table", "kind":"table", "region_id":"room:lobby",
            "position_xyz":[0,.5,.75], "properties":{"material":"wood"}}


def pose():
    return {"id":"pose:grasp", "kind":"grasp", "owner_id":"table:1", "label":"Grasp",
            "level_id":"ground", "position_xyz":[0,.5,.85], "heading_rad":1.2, "node_id":"a",
            "properties":{"task":"serve", "tool":"hand"}}


def test_later_task_additions_preserve_navigation_and_round_trip(tmp_path):
    doc = document()
    stairs(doc)
    before_graph = route_graph_digest(doc)
    before_product = product_digest(doc)
    frozen = deepcopy((doc.nodes, doc.edges, doc.levels))
    doc.upsert_semantic("entities", entity())
    doc.upsert_semantic("interaction_poses", pose())
    assert (doc.nodes, doc.edges, doc.levels) == frozen
    assert route_graph_digest(doc) == before_graph
    assert product_digest(doc) != before_product
    path = tmp_path/"topology.json"
    generate_product(path, doc)
    restored = load_product(path)
    assert restored.to_dict() == doc.to_dict()
    modified = pose()
    modified["position_xyz"][2] = 1.1
    restored.upsert_semantic("interaction_poses", modified)
    assert route_graph_digest(restored) == before_graph
    assert restored.undo()
    assert restored.to_dict() == doc.to_dict()
    assert restored.redo()
    assert restored.semantics["interaction_poses"][0]["position_xyz"][2] == 1.1


def test_stair_turns_are_real_ordered_nodes_with_capability_and_direction():
    doc = document()
    before = doc.to_dict()
    stairs(doc, directed=True)
    row = doc.semantics["routes"][0]
    assert len(row["node_ids"]) == 4 and len(row["edge_ids"]) == 3
    assert [doc.node(n)["position_xyz"] for n in row["node_ids"]] == [[0,0,0],[1,0,.8],[2,1,1.5],[3,1,2]]
    rejected = resolve_landmark_route(doc, origin_ref="a", destination_ref="b")
    assert rejected["admitted"] is False
    admitted = resolve_landmark_route(doc, origin_ref="a", destination_ref="b",
        controller_capabilities=("walk", "stairs", "payload_stable"))
    assert admitted["admitted"] and admitted["topology_node_ids"] == row["node_ids"]
    assert not resolve_landmark_route(doc, origin_ref="b", destination_ref="a",
        controller_capabilities=("walk", "stairs", "payload_stable"))["admitted"]
    assert doc.undo() and doc.to_dict() == before
    assert doc.redo() and len(doc.edges) == 3


@pytest.mark.parametrize("change", [
    {"polygon_xyz":[[0,0,0],[1,1,0],[0,1,0],[1,0,0]]},
    {"polygon_xyz":[[0,0,0],[1,0,0],[2,0,0]]},
    {"polygon_xyz":[[0,0,0],[1,0,0],[0,1,1]]},
    {"polygon_xyz":[[0,0,0],[1,0,0],[0,0,0]]},
    {"height_m":float("nan")}, {"height_m":True}, {"height_m":0},
    {"entrance_node_ids":["missing"]}, {"entrance_node_ids":["b"]},
    {"level_id":[]}, {"properties":{"bad":float("inf")}}, {"aliases":[{}]},
    {"kind":[]}, {"extra":{"bad":float("nan")}}
])
def test_invalid_region_is_atomic_preserving_redo(change):
    doc = document()
    doc.upsert_semantic("entities", entity())
    doc.undo()
    before = doc.to_dict()
    redo = deepcopy(doc._redo)
    row = region()
    row.update(change)
    with pytest.raises(ValueError):
        doc.upsert_semantic("regions", row)
    assert doc.to_dict() == before and doc._redo == redo


@pytest.mark.parametrize("change", [{"owner_id":[]}, {"owner_id":"missing"},
    {"level_id":[]}, {"node_id":{}}, {"node_id":"b"}, {"heading_rad":4}, {"position_xyz":[0,0,True]}])
def test_invalid_interactions_report_errors_without_exceptions(change):
    doc = document()
    doc.upsert_semantic("entities", entity())
    row = pose()
    row.update(change)
    with pytest.raises(ValueError):
        doc.upsert_semantic("interaction_poses", row)


def test_semantic_reference_deletions_rejected_and_explicit_unbinding_allowed():
    doc = document()
    stairs(doc)
    doc.upsert_semantic("entities", entity())
    doc.upsert_semantic("interaction_poses", pose())
    before = doc.to_dict()
    for action in (lambda:doc.remove_node("a"), lambda:doc.remove_edge(doc.edges[0]["id"]),
                   lambda:doc.remove_semantic("regions","room:lobby"),
                   lambda:doc.remove_semantic("entities","table:1")):
        with pytest.raises(ValueError):
            action()
        assert doc.to_dict() == before
    doc.remove_semantic("interaction_poses","pose:grasp")
    doc.remove_semantic("entities","table:1")
    doc.remove_semantic("regions","room:lobby")
    doc.remove_semantic("routes","stairs:main")
    doc.remove_node("a")
    assert validate_document(doc) == []


def test_named_route_checks_connectivity_direction_mode_and_schema():
    doc = document()
    stairs(doc, directed=True)
    baseline = doc.to_dict()
    doc.semantics["routes"][0]["node_ids"].reverse()
    assert any("direction/connectivity" in e for e in validate_document(doc))
    doc = TopologyDocument.from_dict(baseline)
    doc.semantics["routes"][0]["kind"] = "walk"
    assert any("traversal" in e for e in validate_document(doc))
    doc.semantics["version"] = 2
    assert validate_semantics(doc) == ["unsupported scene semantics"]
    with pytest.raises(ValueError):
        doc.replace_semantics(None)


def test_empty_annotations_legacy_and_replace_history_boundary():
    legacy = TopologyDocument("house")
    payload = legacy.to_dict()
    assert "semantics" not in payload
    assert TopologyDocument.from_dict(payload).to_dict() == payload
    doc = document()
    legacy.replace_content(doc)
    assert legacy.to_dict() == doc.to_dict() and not legacy.undo()
    legacy.replace_semantics(empty_semantics())
    assert legacy.undo() and legacy.semantics == doc.semantics


def test_inventory_and_volume_floor_filters():
    doc = document()
    upper = region()
    upper.update(id="room:upper", label="Lobby", aliases=[], level_id="upper", entrance_node_ids=["b"],
        polygon_xyz=[[2,0,2],[4,0,2],[4,2,2],[2,2,2]])
    doc.upsert_semantic("regions", upper)
    doc.upsert_semantic("entities", entity())
    doc.upsert_semantic("interaction_poses", pose())
    assert resolve_semantic(doc,"entrance")["id"] == "room:lobby"
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_semantic(doc,"Lobby")
    assert resolve_semantic(doc,"Lobby", level_id="upper")["id"] == "room:upper"
    assert resolve_semantic(doc,"table:1", level_id="ground")["id"] == "table:1"
    assert point_in_region(region(), [0,0,1])
    assert point_in_region(region(), [1,1,2.5])
    assert not point_in_region(region(), [0,0,3])
    assert visible_semantics(doc,"upper")["entities"] == []
    assert visible_semantics(doc,"upper")["interaction_poses"] == []
    metadata = draw_overlay(None,doc,active_level_id="ground",semantic_preview=[[0,0,0],[1,0,0]])
    assert len(metadata["semantics"]["regions"]) == 1 and len(metadata["semantic_preview"]) == 2


@pytest.mark.parametrize("kind", [[], {}])
def test_malformed_route_kind_is_a_validation_error(kind):
    doc = document()
    stairs(doc)
    doc.semantics["routes"][0]["kind"] = kind
    assert any("kind/label" in error for error in validate_semantics(doc))


@pytest.mark.parametrize("action", [
    lambda doc: doc.move_node("a", [0,0,float("nan")]),
    lambda doc: doc.set_node_metadata("a", level_id="upper", label="A", aliases=[]),
    lambda doc: doc.set_levels([{"id":"ground", "label":"Ground", "support_z_m":1}, {"id":"upper", "label":"Upper", "support_z_m":2}]),
    lambda doc: doc.set_edge_polyline(doc.edges[0]["id"], [[9,9,9],[10,10,10]]),
    lambda doc: doc.set_edge_traversal(doc.edges[0]["id"], "walk"),
    lambda doc: doc.set_edge_direction(doc.edges[-1]["id"], True),
])
def test_graph_edits_cannot_break_reloadable_semantic_map(action):
    doc = document()
    stairs(doc)
    route = deepcopy(doc.semantics["routes"][0])
    route["id"] = "stairs:reverse"
    route["node_ids"].reverse()
    route["edge_ids"].reverse()
    doc.upsert_semantic("routes", route)
    doc.upsert_semantic("entities", entity())
    doc.undo()
    before, redo = doc.to_dict(), deepcopy(doc._redo)
    with pytest.raises(ValueError):
        action(doc)
    assert doc.to_dict() == before and doc._redo == redo


def test_ground_visual_height_edit_keeps_semantic_annotations_reloadable(tmp_path):
    doc = document()
    stairs(doc)
    doc.upsert_semantic("entities", entity())
    doc.upsert_semantic("interaction_poses", pose())
    annotations = deepcopy(doc.semantics)
    before = doc.to_dict()
    doc.move_node("a", [0, 0, 1])
    assert not validate_document(doc)
    assert doc.node("a")["level_id"] == "ground"
    assert doc.semantics == annotations
    path = generate_product(tmp_path / "topology.json", doc)
    assert load_product(path).to_dict() == doc.to_dict()
    assert doc.undo()
    assert doc.to_dict() == before


def test_owner_floor_consistency_includes_inherited_approach_floor():
    doc = document()
    wrong_entity = dict(entity(), level_id="upper")
    with pytest.raises(ValueError, match="floor differs"):
        doc.upsert_semantic("entities", wrong_entity)
    doc.upsert_semantic("entities", entity())
    with pytest.raises(ValueError, match="floor differs"):
        doc.upsert_semantic("interaction_poses", dict(pose(), level_id="upper", node_id="b"))
    with pytest.raises(ValueError, match="another level"):
        doc.upsert_semantic("interaction_poses", dict(pose(), level_id=None, node_id="b"))


def test_history_changes_clear_semantic_json_to_prevent_stale_reapply():
    obj = window(document())
    obj.select_semantic_record("regions", "room:lobby")
    row = json.loads(obj._semantic_record_model.as_string)
    row["label"] = "Changed"
    obj._semantic_record_model.as_string = json.dumps(row)
    obj.apply_semantic_record()
    obj.undo()
    assert obj.document.semantics["regions"][0]["label"] == "Lobby"
    assert obj._semantic_record_model.as_string == "" and obj._selected_semantic_id is None
    obj.apply_semantic_record()
    assert obj.document.semantics["regions"][0]["label"] == "Lobby"
    obj.redo()
    assert obj.document.semantics["regions"][0]["label"] == "Changed"
    assert obj._semantic_record_model.as_string == ""


def test_annotated_legacy_map_can_upgrade_floors_atomically():
    doc = TopologyDocument("house")
    doc.add_node([0,0,0], node_id="a")
    doc.add_node([1,0,0], node_id="b")
    doc.add_edge("a", "b", [[0,0,0],[1,0,0]])
    doc.upsert_semantic("regions", dict(region(), level_id=None, entrance_node_ids=[]))
    before = doc.to_dict()
    obj = window(doc)
    obj._levels_json_model = SimpleNamespace(as_string=json.dumps([
        {"id":"ground", "label":"Ground", "support_z_m":0.0},
        {"id":"upper", "label":"Upper", "support_z_m":2.0}]))
    obj._build = lambda: None
    obj.apply_floor_records()
    assert doc.version == 2 and validate_document(doc) == []
    assert doc.semantics == before["semantics"]
    assert doc.undo() and doc.to_dict() == before


def window(doc):
    obj = TopologyAuthoringWindow.__new__(TopologyAuthoringWindow)
    obj.document = doc
    obj.adapter = EmptyTopologyAdapter("house")
    obj.source = obj.adapter.load_source(".")
    obj.active_level_id = "ground"
    obj._stage_ready = True
    obj._overlay = None
    obj._refresh = lambda:None
    obj.messages = []
    obj._set_status = obj.messages.append
    for name, value in (("_semantic_id_model","room:new"), ("_semantic_label_model","New room"),
        ("_semantic_kind_model","room"), ("_region_height_model","2.5"),
        ("_region_entrances_model","a"), ("_semantic_record_model",""),
        ("_draw_route_start_model","a"), ("_draw_route_end_model","b"),
        ("_draw_route_capabilities_model","")):
        setattr(obj,name,SimpleNamespace(as_string=value))
    obj._draw_route_directed_model = SimpleNamespace(as_bool=False)
    return obj


def test_region_and_stair_drawing_controls_reedit_and_atomic_failure():
    obj = window(document())
    obj.begin_region_drawing()
    for point in ([0,0,0],[1,0,0],[1,1,0],[0,1,0]):
        assert obj.semantic_drawing_click(point)
    obj.confirm_region_drawing()
    assert len(obj.document.semantics["regions"]) == 2 and obj._semantic_drawing is None
    row = json.loads(obj._semantic_record_model.as_string)
    row["label"] = "Renamed room"
    obj._semantic_record_model.as_string = json.dumps(row)
    obj.apply_semantic_record()
    assert resolve_semantic(obj.document,"Renamed room")["id"] == "room:new"
    obj._semantic_id_model.as_string = "route:stairs"
    obj.begin_route_drawing("stairs")
    assert obj.active_level_id is None
    obj.semantic_drawing_click([1,0,1])
    obj.semantic_drawing_click([2,1,1.5])
    obj.pop_drawing_point()
    obj.semantic_drawing_click([2,1,1.5])
    obj.confirm_route_drawing()
    assert obj.document.semantics["routes"][0]["node_ids"][1:3] == ["route:stairs:point:0001","route:stairs:point:0002"]
    before = obj.document.to_dict()
    obj.begin_route_drawing("walk")
    assert obj.document.to_dict() == before
    assert "share a declared floor" in obj.messages[-1]


def test_draft_reopens_headless_and_gui(monkeypatch,tmp_path,capsys):
    doc = document()
    stairs(doc)
    path = tmp_path/"draft.json"
    save_draft(path,doc)
    adapter = EmptyTopologyAdapter("house")
    monkeypatch.setattr(author,"_load",lambda *_:adapter)
    args = SimpleNamespace(adapter="unused",scene_key="house",asset_root=tmp_path,
        output_root=tmp_path,semantic_map=None,draft=path,generate=tmp_path/"published.json")
    assert author._headless(args) == 0
    assert json.loads(capsys.readouterr().out)["semantic_counts"]["regions"] == 1
    assert load_product(args.generate).to_dict() == doc.to_dict()
    from tools.validation.test_topology_authoring_ui import _install_fake_simulation_app
    _install_fake_simulation_app(monkeypatch)
    loaded = []
    import tools.topology_authoring.ui as ui
    monkeypatch.setattr(ui,"TopologyAuthoringWindow", lambda document,*_,**__:SimpleNamespace(
        destroy=lambda:None) if not loaded.append(document.to_dict()) else None)
    assert author._gui(args) == 0
    assert loaded == [doc.to_dict()]


def test_editor_added_floor_support_projection_does_not_mutate_source():
    adapter = UsdSceneAdapter("house")
    from tools.topology_authoring.adapter import SceneSource
    source = SceneSource("house",None,{"levels":[{"id":"ground","support_z_m":0.0}]})
    adapter.set_authoring_levels([{"id":"new","label":"New","support_z_m":2.0}])
    adapter.set_active_level("new",source)
    adapter._geometry = lambda _:SimpleNamespace(nearest=lambda point:tuple(point))
    assert adapter.project_to_support_surface([1,2,9],source) == (1,2,2)
    assert source.payload["levels"] == [{"id":"ground","support_z_m":0.0}]
