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


def test_points_preserve_modes_coordinates_semantics_binding_and_graph_identity(project):
    before = {d: route_graph_digest(doc) for d, doc in project.documents.items()}
    digest = project.product_digest
    add_point(project, label="Chair", description="Sitting place", aliases=["seat"], properties={"action": "sit"})
    assert project.product_digest != digest
    project.edit("update_semantic_point", id="s1", edit_mode="2.5d")
    project.edit("move_semantic_point_xy", id="s1", position_xy=[1, 2])
    assert project.semantic_points[0]["position_xyz"] == [1, 2, 1.5]
    project.edit("update_semantic_point", id="s1", position_xyz=[1, 2, 6])
    saved = project._snapshot()
    project.edit("bind_semantic_point", id="s1", binding={
        "source_usd": "scene/Test/scene/scene.usda", "prim_path": "/World/Chair"})
    bound = deepcopy(project.semantic_points[0])
    project.undo()
    assert project._snapshot() == saved
    project.redo()
    project.edit("update_semantic_point", id="s1", edit_mode="3d")
    assert project.semantic_points[0]["position_xyz"] == bound["position_xyz"]
    project.save()
    reopened = MapProject(project.root)
    assert reopened._snapshot() == project._snapshot()
    assert before == {d: route_graph_digest(doc) for d, doc in reopened.documents.items()}
    revision = read_json(reopened.root / reopened.saved_revision / "manifest.json")
    assert revision["semantic_point_product"] == "semantic-points.json"
    project.edit("remove_semantic_point", id="s1")
    assert not project.semantic_points
    project.undo()
    assert project.semantic_points[0]["isaac_binding"]["prim_path"] == "/World/Chair"


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


def test_http_binding_authority_rejects_stale_cross_scene_multiselect_and_overlay(api):
    request, server = api
    _,state = request("/api/state")
    state = switch(request,state,tool="add-semantic-point")
    _,state = request("/api/edit",{**identity(state),"operation":"add_semantic_point","id":"s1","position_xyz":[0,0,1]})
    server.update_isaac_runtime(state["scene_generation"], "ready")
    def selection(seq=1,paths=None,source=None):
        return {**identity(state),"selection_kind":"prim","session_id":"kit","sequence":seq,
                "timestamp":__import__('time').time(),"prim_paths":paths or ["/World/Chair"],
                "position_xyz":[1,2,3],"bbox_min_xyz":[0,1,2],"bbox_max_xyz":[2,3,4],
                "source_usd":source or "scene/Test/scene/scene.usda"}
    assert request("/api/isaac/selection",selection(source="wrong.usda"))[0] == 400
    assert request("/api/isaac/selection",selection())[0] == 200
    bind = {**identity(state),"operation":"bind_semantic_point","id":"s1","selection_session_id":"kit","selection_sequence":0}
    assert request("/api/edit",bind)[0] == 409
    bind["selection_sequence"] = 1
    code,state = request("/api/edit",bind)
    assert code == 200 and state["semantic_points"][0]["isaac_binding"]["prim_path"] == "/World/Chair"
    assert request("/api/isaac/selection",selection())[0] == 409
    assert request("/api/isaac/selection",selection(2,["/World/A","/World/B"]))[0] == 200
    bind.update(identity(state),selection_sequence=2)
    assert request("/api/edit",bind)[0] == 409
    assert request("/api/isaac/selection",selection(3,["/SemanticMapOverlay/Fake"]))[0] == 200
    bind["selection_sequence"] = 3
    assert request("/api/edit",bind)[0] == 409
    server.update_isaac_runtime(state["scene_generation"],"ready")
    report = {**identity(state),"statuses":{"s1":{"prim_path":"/World/Chair","status":"invalid"}}}
    assert request("/api/isaac/bindings",report)[0] == 200
    assert request("/api/state")[1]["binding_status"]["s1"] == "invalid"
    server.update_isaac_runtime(state["scene_generation"],"error","disconnected")
    assert request("/api/state")[1]["binding_status"]["s1"] == "unverified"
