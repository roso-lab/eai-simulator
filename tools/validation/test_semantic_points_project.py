"""Independent semantic point transactions, binding identities and retired data."""
from copy import deepcopy
import json
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
import pytest

from algorithm.skills.navigation.topology import route_graph_digest
from tools.topology_authoring.cleanup import clean_project
from tools.topology_authoring.map_project import MapProject, read_json, sha256
from tools.topology_authoring.map_server import make_server


@pytest.fixture
def project(tmp_path):
    p = MapProject.create_blank(tmp_path / "map", "scene", [-2, -2, 2, 2], .5,
        levels=[{"id": "1F", "label": "Lower", "support_z_m": 0},
                {"id": "2F", "label": "Upper", "support_z_m": 3}])
    p.manifest["provenance"]["source_usd"] = "scene/Test/scene/scene.usda"
    return p


def add_point(p, **kwargs):
    p.edit("add_semantic_point", id="s1", position_xyz=[0, 0, 1.5], kind="seat", **kwargs)


def test_points_preserve_modes_coordinates_navigation_bindings_and_graph_identity(project):
    project.edit("add_node", position_xyz=[0, 0, 0], level_id="1F")
    before = {d: route_graph_digest(doc) for d, doc in project.documents.items()}
    digest = project.product_digest
    add_point(project, label="Chair", description="Sitting place", aliases=["seat"], properties={"action": "sit"})
    assert project.product_digest != digest
    project.edit("update_semantic_point", id="s1", edit_mode="2.5d")
    project.edit("move_semantic_point_xy", id="s1", position_xy=[1, 2])
    assert project.semantic_points[0]["position_xyz"] == [1, 2, 1.5]
    project.edit("update_semantic_point", id="s1", position_xyz=[1, 2, 6])
    saved = project._snapshot()
    project.edit("set_semantic_point_navigation_bindings", id="s1",
                 bindings=[{"domain": "ground", "node_id": "node:0001"}])
    bound = deepcopy(project.semantic_points[0])
    project.undo()
    assert project._snapshot() == saved
    project.redo()
    project.edit("update_semantic_point", id="s1", edit_mode="3d")
    assert project.semantic_points[0]["position_xyz"] == bound["position_xyz"]
    assert project.semantic_points[0]["navigation_bindings"] == bound["navigation_bindings"]
    project.save()
    reopened = MapProject(project.root)
    assert reopened._snapshot() == project._snapshot()
    assert before == {d: route_graph_digest(doc) for d, doc in reopened.documents.items()}
    revision = read_json(reopened.root / reopened.saved_revision / "manifest.json")
    assert revision["semantic_point_product"] == "semantic-points.json"
    project.edit("remove_semantic_point", id="s1")
    assert not project.semantic_points
    project.undo()
    assert project.semantic_points[0]["navigation_bindings"] == bound["navigation_bindings"]


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("binding", [None, {
    "source_usd": "old/missing.usda", "prim_path": "/World/RemovedChair"}])
def test_legacy_isaac_binding_is_dropped_on_load_and_save(project, version, binding):
    project.edit("add_node", position_xyz=[0, 0, 0], level_id="1F")
    project.edit("add_node", domain="aerial", position_xyz=[0, 0, 2])
    add_point(project, label="Chair", aliases=["seat"], properties={"color": "oak"})
    if version == 2:
        project.edit("set_semantic_point_navigation_bindings", id="s1", bindings=[
            {"domain": domain, "node_id": "node:0001"} for domain in ("ground", "aerial")])
    project.save()
    expected = project._snapshot()
    digest, product_digest = project.semantic_binding_digest, project.product_digest
    graphs = {d: route_graph_digest(doc) for d, doc in project.documents.items()}
    revision = project.root / project.saved_revision
    payload = read_json(revision / "semantic-points.json")
    payload["version"] = version
    payload["points"][0]["isaac_binding"] = binding
    if version == 1:
        payload["points"][0].pop("navigation_bindings")
    (revision / "semantic-points.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = read_json(revision / "manifest.json")
    manifest["files"]["semantic-points.json"] = sha256(revision / "semantic-points.json")
    (revision / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    reopened = MapProject(project.root)
    assert "isaac_binding" not in reopened.semantic_points[0]
    assert reopened._snapshot() == expected
    assert reopened.semantic_binding_digest == digest
    assert reopened.product_digest == product_digest
    assert {d: route_graph_digest(doc) for d, doc in reopened.documents.items()} == graphs
    assert read_json(revision / "semantic-points.json")["points"][0]["isaac_binding"] == binding
    reopened.save()
    payload = read_json(reopened.root / reopened.saved_revision / "semantic-points.json")
    assert "isaac_binding" not in payload["points"][0]
    assert MapProject(project.root)._snapshot() == expected


@pytest.mark.parametrize("operation, args", [
    ("bind_semantic_point", {"binding": {"prim_path": "/World/Chair"}}),
    ("unbind_semantic_point", {}),
    ("update_semantic_point", {"isaac_binding": None}),
    ("add_semantic_point", {"isaac_binding": None, "position_xyz": [0, 0, 1]}),
])
def test_retired_isaac_edits_do_not_mutate_project(project, operation, args):
    add_point(project)
    before, revision, history = project._snapshot(), project.revision, len(project._undo)
    with pytest.raises(ValueError):
        project.edit(operation, id="s1", **args)
    assert project._snapshot() == before
    assert project.revision == revision and len(project._undo) == history


@pytest.mark.parametrize("patch", [
    {"position_xyz": [1, 2, float("nan")]}, {"edit_mode": "2d"},
    {"aliases": ["same", "same"]}, {"properties": {"bad": float("inf")}},
])
def test_invalid_points_leave_complete_project_and_history_untouched(project, patch):
    add_point(project)
    before, revision, history = project._snapshot(), project.revision, len(project._undo)
    with pytest.raises(ValueError):
        project.edit("update_semantic_point", id="s1", **patch)
    assert project._snapshot() == before and project.revision == revision and len(project._undo) == history


def test_failed_save_keeps_all_three_products_old_and_pending_data_live(project, monkeypatch):
    project.save()
    pointer = read_json(project.root / "project.json")
    add_point(project)
    project.edit("add_node", domain="aerial", position_xyz=[1, 1, 9])
    pending = project._snapshot()
    export = project._export
    def fail(*args):
        export(*args)
        raise OSError("test disk failure")
    monkeypatch.setattr(project, "_export", fail)
    with pytest.raises(OSError):
        project.save()
    assert read_json(project.root / "project.json") == pointer
    assert project._snapshot() == pending
    reopened = MapProject(project.root)
    assert not reopened.semantic_points and not reopened.document_for("aerial").nodes


def test_edge_translation_moves_interior_and_shared_endpoints_once_with_undo(project):
    for pos in ([0, 0, 1], [2, 0, 4], [3, 2, 4]):
        project.edit("add_node", domain="aerial", position_xyz=pos, edit_mode="2.5d")
    project.edit("add_edge", domain="aerial", from_id="node:0001", to_id="node:0002")
    project.edit("add_edge", domain="aerial", from_id="node:0002", to_id="node:0003")
    doc = project.document_for("aerial")
    doc.edges[0]["polyline_xyz"].insert(1, [1, 0, 2])
    before, revision = project._snapshot(), project.revision
    project.edit("translate_edge_xy", domain="aerial", id=doc.edges[0]["id"], delta_xy=[1, 1])
    doc = project.document_for("aerial")
    assert doc.nodes[0]["position_xyz"] == [1, 1, 1]
    assert doc.nodes[1]["position_xyz"] == [3, 1, 4]
    assert doc.edges[0]["polyline_xyz"] == [[1, 1, 1], [2, 1, 2], [3, 1, 4]]
    assert doc.edges[1]["polyline_xyz"] == [[3, 1, 4], [3, 2, 4]]
    assert project.revision == revision + 1
    project.undo()
    assert project._snapshot() == before
    project.redo()
    doc = project.document_for("aerial")
    doc.edges[1]["locked_geometry"] = True
    before = project._snapshot()
    with pytest.raises(ValueError, match="locked"):
        project.edit("translate_edge_xy", domain="aerial", id=doc.edges[0]["id"], delta_xy=[2, 2])
    assert project._snapshot() == before


def test_cleanup_preserves_graph_points_base_and_overwrites_current_idempotently(project):
    region = {"id": "r1", "kind": "room", "label": "Room", "aliases": [], "properties": {},
              "level_id": "1F", "polygon_xyz": [[-1,-1,0],[1,-1,0],[1,1,0]],
              "height_m": 2, "entrance_node_ids": []}
    project.edit("upsert_semantic", collection="regions", record=region)
    add_point(project)
    project.edit("add_node", position_xyz=[0,0,0], level_id="1F")
    project.save()
    old = project.saved_revision
    graphs = {k: (deepcopy(d.nodes), deepcopy(d.edges)) for k,d in project.documents.items()}
    bases = {x["file"]: sha256(project.root/x["file"]) for x in project.manifest["base_grids"]}
    report = clean_project(project.root, apply=True)
    assert report["applied"] and report["old_revision"] == old and report["new_revision"] == "current"
    assert report["old_save_id"] != report["new_save_id"]
    assert not (project.root / "revisions").exists()
    reopened = MapProject(project.root)
    assert (project.root/old/"topology.json").exists()
    assert reopened.semantic_points == project.semantic_points
    for key, doc in reopened.documents.items():
        assert (doc.nodes,doc.edges) == graphs[key]
        assert all(not doc.semantics[k] for k in ("regions","entities","interaction_poses","routes"))
    assert bases == {x:sha256(project.root/x) for x in bases}
    for grid in (project.root/reopened.saved_revision).glob("*-semantic-grid.npz"):
        with np.load(grid) as data:
            assert not data["region_id"].any() and not (data["instance_id"] > 0).any()
    assert not clean_project(project.root, apply=True)["changed"]


@pytest.fixture
def api(project):
    server = make_server(project, 0, isaac_linked=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    def request(path, body=None):
        req = Request(base+path, data=None if body is None else json.dumps(body).encode(),
                      headers={"Content-Type":"application/json"})
        try:
            with urlopen(req) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)
    yield request, server
    server.shutdown(); server.server_close(); thread.join(2)


def identity(state):
    return {key:state[key] for key in ("csrf_token","revision","scene_key","scene_generation","context_generation")} | {
        "domain":state["active_domain"], "active_layer":state["active_layer"]}


def switch(request, state, **changes):
    code, fresh = request("/api/editor/context", {**identity(state), **changes})
    assert code == 200, fresh
    return fresh


def test_http_layers_type_isolation_modes_context_and_region_rejection(api):
    request, server = api
    _, state = request("/api/state")
    state = switch(request,state,active_layer="aerial",tool="add-node")
    stale = identity(state)
    state = switch(request,state,reference_level="2F",new_point_settings={"aerial":{"z":7,"edit_mode":"2.5d"}})
    assert state["active_layer"] == "aerial" and state["new_point_settings"]["aerial"]["z"] == 7
    code,_ = request("/api/edit", {**stale,"operation":"add_node","position_xyz":[0,0,7]})
    assert code == 409
    state = switch(request,state,tool="add-semantic-point")
    code,state = request("/api/edit",{**identity(state),"operation":"add_semantic_point","position_xyz":[0,0,7]})
    assert code == 200 and state["active_layer"] == "semantic_points"
    code,_ = request("/api/edit",{**identity(state),"operation":"add_node","position_xyz":[0,0,7]})
    assert code == 400
    state = switch(request,state,tool="add-edge")
    assert state["active_layer"] == "aerial" and state["active_domain"] == "aerial"
    code,_ = request("/api/edit",{**identity(state),"operation":"update_region","id":"r1","height_m":4})
    assert code == 400
    assert server.isaac_context()["semantic_points"] == state["semantic_points"]
    assert set(server.isaac_context()["document_snapshots"]) == {"ground","aerial"}


def test_http_tools_cannot_modify_reference_types_or_edge_endpoints(api):
    request, server = api
    _, state = request("/api/state")
    for point in ([0, 0, 0], [1, 0, 0]):
        code, state = request("/api/edit", {
            **identity(state), "operation": "add_node", "position_xyz": point, "level_id": "1F"})
        assert code == 200
    edge = {"operation": "add_edge", "from_id": "node:0001", "to_id": "node:0002"}
    assert request("/api/edit", {**identity(state), **edge})[0] == 400
    state = switch(request, state, tool="add-edge")
    code, state = request("/api/edit", {**identity(state), **edge})
    assert code == 200
    before = deepcopy(server.isaac_context())
    for operation in ("remove_node", "update_node", "move_node", "move_node_xy"):
        code, _ = request("/api/edit", {
            **identity(state), "operation": operation, "id": "node:0001",
            "position_xyz": [3, 4, 5], "position_xy": [3, 4]})
        assert code == 400
    assert server.isaac_context()["document_snapshots"] == before["document_snapshots"]
    state = switch(request, state, tool="add-node")
    for operation in ("remove_edge", "update_edge", "translate_edge_xy"):
        code, _ = request("/api/edit", {
            **identity(state), "operation": operation, "id": "edge:0001", "delta_xy": [1, 1],
            "directed": True, "traversal_mode": "walk"})
        assert code == 400
    assert request("/api/state")[1]["revision"] == state["revision"]


def test_http_rejects_isaac_binding_and_accepts_navigation_binding(api):
    request, server = api
    _,state = request("/api/state")
    state = switch(request, state, active_layer="ground", tool="add-node")
    _,state = request("/api/edit", {**identity(state), "operation":"add_node",
                                     "position_xyz":[0,0,0], "level_id":"1F"})
    state = switch(request,state,tool="add-semantic-point")
    _,state = request("/api/edit",{**identity(state),"operation":"add_semantic_point","id":"s1","position_xyz":[0,0,1]})
    bind = {**identity(state),"operation":"bind_semantic_point","id":"s1",
            "binding":{"source_usd":"scene/Test/scene/scene.usda","prim_path":"/World/Chair"}}
    assert request("/api/edit",bind)[0] == 400
    assert request("/api/edit", {**identity(state), "operation":"unbind_semantic_point", "id":"s1"})[0] == 400
    assert request("/api/edit", {**identity(state), "operation":"update_semantic_point",
                                 "id":"s1", "isaac_binding":None})[0] == 400
    assert request("/api/isaac/bindings", {**identity(state), "statuses":{}})[0] == 404
    code,state = request("/api/edit", {**identity(state),
        "operation":"set_semantic_point_navigation_bindings", "id":"s1",
        "bindings":[{"domain":"ground","node_id":"node:0001"}]})
    assert code == 200
    assert state["semantic_points"][0]["navigation_bindings"] == [
        {"domain":"ground", "node_id":"node:0001"}]
    assert "isaac_selection" not in state and "binding_status" not in state
    assert "isaac_selection" not in server.isaac_context()
    assert "binding_status" not in server.isaac_context()
