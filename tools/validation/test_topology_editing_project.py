"""Transactions and HTTP identities for the shared, two-domain editor."""
from copy import deepcopy
import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from algorithm.skills.navigation.topology import route_graph_digest
from tools.topology_authoring.map_project import MapProject, read_json, sha256, write_json
from tools.topology_authoring.map_server import make_server
from tools.topology_authoring.product import product_digest
from tools.topology_authoring.road_builder import plan_ground_road, plan_navigation_road


@pytest.fixture
def project(tmp_path):
    return MapProject.create_blank(tmp_path / "map", "office", [-2, -2, 2, 2], .5)


def region():
    return {"id": "r1", "kind": "room", "label": "Room", "aliases": [], "properties": {"custom": 1},
            "level_id": "ground", "polygon_xyz": [[-1, -1, 0], [1, -1, 0], [1, 1, 0], [-1, 1, 0]],
            "height_m": 2.5, "entrance_node_ids": []}


def test_graph_isolation_modes_roundtrip_and_history(project):
    project.edit("add_node", position_xyz=[0, 0, 0], level_id="ground")
    project.edit("add_node", domain="aerial", position_xyz=[0, 0, 2], level_id="ground", edit_mode="2.5d")
    project.edit("add_node", domain="aerial", position_xyz=[0, 0, 4], edit_mode="3d")
    project.edit("add_edge", domain="aerial", from_id="node:0001", to_id="node:0002")
    before = deepcopy(project.document_for("aerial").to_dict())
    graph_digest = route_graph_digest(before)
    product_before = product_digest(project.document_for("aerial"))
    project.edit("update_node", domain="aerial", id="node:0001", edit_mode="3d")
    assert route_graph_digest(project.document_for("aerial")) == graph_digest
    assert product_digest(project.document_for("aerial")) != product_before
    project.undo()
    assert project.document_for("aerial").to_dict() == before
    project.redo()
    project.edit("move_node_xy", domain="aerial", id="node:0001", position_xy=[.5, .25])
    assert project.document_for("aerial").node("node:0001")["position_xyz"] == [.5, .25, 2]
    assert project.document_for("aerial").edges[0]["polyline_xyz"][0] == [.5, .25, 2]
    assert project.document.node("node:0001")["position_xyz"] == [0, 0, 0]
    assert len(project.document.nodes) == 1 and not project.document.edges
    project.edit("move_node", domain="aerial", id="node:0001", position_xyz=[.5, .25, 3])
    project.save()
    restored = MapProject(project.root)
    assert restored._snapshot() == project._snapshot() and not restored.dirty
    assert restored.manifest["version"] == 2
    exported = read_json(restored.root / restored.saved_revision / "manifest.json")
    assert exported["navigation_products"] == {"ground": "topology.json", "aerial": "topology-aerial.json"}


def test_planar_drag_and_explicit_properties_of_25d_node(project):
    project.edit("add_node", domain="aerial", position_xyz=[0, 0, 2], edit_mode="2.5d")
    project.edit("move_node_xy", domain="aerial", id="node:0001", position_xy=[1, 1])
    assert project.document_for("aerial").node("node:0001")["position_xyz"] == [1, 1, 2]
    before = project._snapshot()
    project.edit("update_node", domain="aerial", id="node:0001", position_xyz=[1, 1, 4],
                 label="High waypoint", edit_mode="3d")
    node = project.document_for("aerial").node("node:0001")
    assert (node["position_xyz"], node["label"], node["edit_mode"]) == ([1, 1, 4], "High waypoint", "3d")
    project.undo()
    assert project._snapshot() == before


def test_ground_floor_and_height_properties_commit_together(tmp_path):
    project = MapProject.create_blank(tmp_path / "floors", "office", [-1, -1, 1, 1], .5,
        levels=[{"id": "lower", "label": "Lower", "support_z_m": 0},
                {"id": "upper", "label": "Upper", "support_z_m": 3}])
    project.edit("add_node", position_xyz=[0, 0, 0], level_id="lower")
    before = project._snapshot()
    project.edit("update_node", id="node:0001", position_xyz=[0, 0, 3], level_id="upper")
    assert project.document.node("node:0001")["level_id"] == "upper"
    assert project.document.node("node:0001")["position_xyz"] == [0, 0, 3]
    project.undo()
    assert project._snapshot() == before


def test_ground_visual_height_survives_edits_history_and_export(project):
    aerial_before = project.document_for("aerial").to_dict()
    points_before = deepcopy(project.semantic_points)
    for point in ([0, 0, 1.5], [1, 0, 3], [1, 1, -2]):
        project.edit("add_node", position_xyz=point, level_id="ground")
    for first, last in (("node:0001", "node:0002"), ("node:0002", "node:0003")):
        project.edit("add_edge", from_id=first, to_id=last)
    project.edit("update_node", id="node:0001", position_xyz=[0, 0, 2.5])
    assert project.document.edges[0]["polyline_xyz"][0] == [0, 0, 2.5]
    project.edit("move_node_xy", id="node:0001", position_xy=[-.5, .5])
    assert project.document.nodes[0]["position_xyz"] == [-.5, .5, 2.5]
    project.document.edges[0]["polyline_xyz"].insert(1, [0, .25, 4])
    before = project._snapshot()
    project.edit("translate_edge_xy", id="edge:0001", delta_xy=[.25, -.25])
    doc = project.document
    assert doc.nodes[0]["position_xyz"] == [-.25, .25, 2.5]
    assert doc.nodes[1]["position_xyz"] == [1.25, -.25, 3]
    assert doc.nodes[2]["position_xyz"] == [1, 1, -2]
    assert doc.edges[0]["polyline_xyz"] == [[-.25, .25, 2.5], [.25, 0, 4], [1.25, -.25, 3]]
    assert doc.edges[1]["polyline_xyz"][0] == [1.25, -.25, 3]
    after = project._snapshot()
    project.undo()
    assert project._snapshot() == before
    project.redo()
    assert project._snapshot() == after
    project.save()
    restored = MapProject(project.root)
    assert restored._snapshot() == after
    assert restored.document_for("aerial").to_dict() == aerial_before
    assert restored.semantic_points == points_before
    geojson = read_json(project.root / "current" / "semantic-footprints.geojson")
    edges = [row for row in geojson["features"] if row["properties"]["feature_type"] == "topology_edge"]
    assert edges[0]["geometry"]["coordinates"] == [[-.25, .25, 2.5], [.25, 0, 4], [1.25, -.25, 3]]


@pytest.mark.parametrize("patch", [
    {"position_xyz": [0, 0, float("nan")]},
    {"position_xyz": [0, 0, float("inf")]},
    {"position_xyz": [0, 0, True]},
    {"level_id": "missing"},
])
def test_invalid_ground_height_or_floor_is_atomic(project, patch):
    project.edit("add_node", position_xyz=[0, 0, 2], level_id="ground")
    before, revision, history = project._snapshot(), project.revision, len(project._undo)
    with pytest.raises(ValueError):
        project.edit("update_node", id="node:0001", **patch)
    assert project._snapshot() == before
    assert project.revision == revision and len(project._undo) == history


def test_regions_shared_insert_closing_edge_move_and_atomic_undo(project):
    project.edit("upsert_semantic", collection="regions", record=region())
    project.edit("add_node", position_xyz=[0, 0, 0], level_id="ground")
    # Legacy cross-domain region fixture: construct the historical aerial
    # node directly on its document so the test does not exercise the unified
    # editor's aerial level binding rules.
    project.document_for("aerial").add_node([0, 0, 0], node_id="node:0001", level_id="ground")
    aerial = deepcopy(project.document_for("aerial").semantics["regions"][0])
    aerial["entrance_node_ids"] = ["node:0001"]
    project.edit("upsert_semantic", domain="aerial", collection="regions", record=aerial)
    project.edit("insert_region_vertex", domain="ground", id="r1", index=3, position_xyz=[-1.3, 0, 99])
    for domain in ("ground", "aerial"):
        polygon = project.document_for(domain).semantics["regions"][0]["polygon_xyz"]
        assert polygon[4] == [-1, 0, 0]
    assert project.document.semantics["regions"][0]["entrance_node_ids"] == []
    assert project.document_for("aerial").semantics["regions"][0]["entrance_node_ids"] == ["node:0001"]
    old = project._snapshot()
    revision = project.revision
    project.edit("move_region_vertex", domain="aerial", id="r1", index=4, position_xy=[-1.5, 0])
    assert project.revision == revision + 1
    assert project.document.semantics["regions"][0]["polygon_xyz"][4] == [-1.5, 0, 0]
    project.undo()
    assert project._snapshot() == old
    project.redo()
    project.save()
    assert MapProject(project.root)._snapshot() == project._snapshot()


def test_invalid_region_or_mode_leaves_both_graphs_and_history_untouched(project):
    project.edit("upsert_semantic", collection="regions", record=region())
    before, revision = project._snapshot(), project.revision
    history = len(project._undo)
    for operation, args in [
        ("move_region_vertex", {"id": "r1", "index": 0, "position_xy": [1, 1]}),
        ("insert_region_vertex", {"id": "r1", "index": 0, "position_xyz": [-1, -1, 0]}),
        ("insert_region_vertex", {"id": "r1", "index": True, "position_xyz": [0, -1, 0]}),
        ("add_node", {"position_xyz": [0, 0, 0], "edit_mode": "2.5d"}),
    ]:
        with pytest.raises((ValueError, TypeError)):
            project.edit(operation, **args)
        assert project._snapshot() == before and project.revision == revision
        assert len(project._undo) == history


def test_region_property_patch_preserves_metadata_and_domain_references(project):
    record = region()
    record["aliases"] = ["Work area"]
    project.edit("upsert_semantic", collection="regions", record=record)
    project.document_for("aerial").add_node([0, 0, 2], node_id="node:0001", level_id="ground")
    aerial = deepcopy(project.document_for("aerial").semantics["regions"][0])
    aerial["entrance_node_ids"] = ["node:0001"]
    project.edit("upsert_semantic", domain="aerial", collection="regions", record=aerial)
    before = project._snapshot()

    project.edit("update_region", domain="aerial", id="r1", label="Tall room", height_m=4)
    for domain in ("ground", "aerial"):
        updated = project.document_for(domain).semantics["regions"][0]
        assert updated["label"] == "Tall room" and updated["height_m"] == 4
        assert updated["properties"] == record["properties"]
        assert updated["aliases"] == record["aliases"]
        assert updated["polygon_xyz"] == record["polygon_xyz"]
        assert updated["entrance_node_ids"] == ([] if domain == "ground" else ["node:0001"])
    project.undo()
    assert project._snapshot() == before
    project.redo()
    valid = project._snapshot()
    with pytest.raises(ValueError):
        project.edit("update_region", id="r1", height_m=0)
    assert project._snapshot() == valid
    project.save()
    assert MapProject(project.root)._snapshot() == project._snapshot()


def test_failed_export_keeps_saved_pointer_and_live_documents(project, monkeypatch):
    project.edit("add_node", domain="aerial", position_xyz=[0, 0, 2])
    before, pointer = project._snapshot(), read_json(project.root / "project.json")
    real_export = project._export

    def fail_after_export(*args):
        real_export(*args)
        raise OSError("disk failure before publishing")

    monkeypatch.setattr(project, "_export", fail_after_export)
    with pytest.raises(OSError, match="disk failure"):
        project.save()
    assert read_json(project.root / "project.json") == pointer
    assert project._snapshot() == before and project.dirty
    assert not MapProject(project.root).document_for("aerial").nodes


def test_ground_road_connects_same_plane_crossing_atomically_and_undoes(project):
    project.edit("add_node", position_xyz=[-2, 0, 0], level_id="ground")
    project.edit("add_node", position_xyz=[2, 0, 0], level_id="ground")
    project.edit("add_edge", from_id="node:0001", to_id="node:0002")
    before_revision = project.revision
    project.edit("build_ground_road", start_xyz=[0, -2, 0], end_xyz=[0, 2, 0],
                 level_id="ground", snap_radius_m=0, connect_intersections=True)
    assert project.revision == before_revision + 1
    assert len(project.document.nodes) == 5
    crossing = next(node for node in project.document.nodes if node["position_xyz"] == [0, 0, 0])
    assert sum(crossing["id"] in (edge["from"], edge["to"]) for edge in project.document.edges) == 4
    assert project.document.edges[0]["id"] == "edge:0001"
    committed = project._snapshot()
    project.undo()
    assert len(project.document.edges) == 1
    project.redo()
    assert project._snapshot() == committed


def test_ground_road_splits_multiple_existing_edges_without_reusing_ids(project):
    for y in (-1, 1):
        project.edit("add_node", position_xyz=[-2, y, 0], level_id="ground")
        project.edit("add_node", position_xyz=[2, y, 0], level_id="ground")
        first = f"node:{len(project.document.nodes)-1:04d}"
        last = f"node:{len(project.document.nodes):04d}"
        project.edit("add_edge", from_id=first, to_id=last)
    project.edit("build_ground_road", start_xyz=[0, -2, 0], end_xyz=[0, 2, 0],
                 level_id="ground", snap_radius_m=0, connect_intersections=True)
    edge_ids = [edge["id"] for edge in project.document.edges]
    assert len(edge_ids) == len(set(edge_ids))
    assert len(edge_ids) == 7
    assert len([node for node in project.document.nodes
                if node["position_xyz"][0] == 0 and node["position_xyz"][1] in (-1, 1)]) == 2


def test_ground_road_no_connect_and_cross_plane_isolation(project):
    project.edit("add_node", position_xyz=[-2, 0, 1], level_id="ground")
    project.edit("add_node", position_xyz=[2, 0, 1], level_id="ground")
    project.edit("add_edge", from_id="node:0001", to_id="node:0002")
    no_connect = plan_ground_road(project.document, [0, -2, 0], [0, 2, 0],
                                  level_id="ground", connect_intersections=False)
    assert len(no_connect["crossings"]) == 0
    assert len(no_connect["document"]["edges"]) == 2
    isolated = plan_ground_road(project.document, [0, -2, 0], [0, 2, 0],
                                level_id="ground", connect_intersections=True)
    assert len(isolated["crossings"]) == 0
    assert len(isolated["document"]["edges"]) == 2


def test_ground_road_rejects_overlap_and_locked_intersection_without_mutation(project):
    project.edit("add_node", position_xyz=[-2, 0, 0], level_id="ground")
    project.edit("add_node", position_xyz=[2, 0, 0], level_id="ground")
    project.edit("add_edge", from_id="node:0001", to_id="node:0002")
    project.document.edges[0]["locked_geometry"] = True
    before, revision, history = project._snapshot(), project.revision, len(project._undo)
    with pytest.raises(ValueError, match="锁定边"):
        project.edit("build_ground_road", start_xyz=[0, -2, 0], end_xyz=[0, 2, 0],
                     level_id="ground", snap_radius_m=0)
    assert project._snapshot() == before and project.revision == revision
    assert len(project._undo) == history
    with pytest.raises(ValueError, match="共线重叠"):
        project.edit("build_ground_road", start_xyz=[-1, 0, 0], end_xyz=[1, 0, 0],
                     level_id="ground", snap_radius_m=0)


def test_aerial_road_connects_interpolated_height_crossings_and_roundtrips(project):
    project.edit("add_node", domain="aerial", position_xyz=[-2, 0, 1.0])
    project.edit("add_node", domain="aerial", position_xyz=[2, 0, 1.0])
    project.edit("add_edge", domain="aerial", from_id="node:0001", to_id="node:0002")
    before = project.revision
    plan = plan_navigation_road(project.document_for("aerial"), [0, -2, 1.0], [0, 2, 1.0])
    assert plan["crossings"] == [[0.0, 0.0, 1.0]]
    project.edit("build_aerial_road", domain="aerial", start_xyz=[0, -2, 1.0],
                 end_xyz=[0, 2, 1.0], connect_intersections=True)
    assert project.revision == before + 1
    assert len(project.document_for("aerial").nodes) == 5
    crossing = next(node for node in project.document_for("aerial").nodes
                    if node["position_xyz"] == [0.0, 0.0, 1.0])
    assert sum(crossing["id"] in (edge["from"], edge["to"])
               for edge in project.document_for("aerial").edges) == 4
    committed = project._snapshot()
    project.undo()
    assert len(project.document_for("aerial").edges) == 1
    project.redo()
    assert project._snapshot() == committed
    project.save()
    assert MapProject(project.root)._snapshot() == committed


@pytest.mark.parametrize(("road_z", "should_connect"), [(1.1, True), (1.101, False)])
def test_aerial_road_uses_point_one_meter_height_tolerance(project, road_z, should_connect):
    project.edit("add_node", domain="aerial", position_xyz=[-2, 0, 1.0])
    project.edit("add_node", domain="aerial", position_xyz=[2, 0, 1.0])
    project.edit("add_edge", domain="aerial", from_id="node:0001", to_id="node:0002")
    plan = plan_navigation_road(project.document_for("aerial"), [0, -2, road_z],
                               [0, 2, road_z], snap_radius_m=.5)
    assert bool(plan["crossings"]) is should_connect
    snap_plan = plan_navigation_road(project.document_for("aerial"), [.01, .02, road_z],
                                    [1, 1, road_z], snap_radius_m=.05)
    snap = snap_plan["snap"]["start"]
    assert (snap["edge_id"] is not None) is should_connect


def test_aerial_road_http_preview_commit_and_delete_tool_are_domain_scoped(project):
    project.edit("add_node", domain="aerial", position_xyz=[-2, 0, 1.0])
    project.edit("add_node", domain="aerial", position_xyz=[2, 0, 1.0])
    project.edit("add_edge", domain="aerial", from_id="node:0001", to_id="node:0002")
    server = make_server(project, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        _, state = request(url + "/api/state")
        ident = identity(state)
        bad_tool = {**ident, "active_layer": "ground", "active_domain": "ground",
                    "tool": "build-aerial-road"}
        code, _ = request(url + "/api/editor/context", bad_tool)
        assert code == 400
        context = {**ident, "active_layer": "aerial", "active_domain": "aerial",
                   "tool": "build-aerial-road"}
        code, state = request(url + "/api/editor/context", context)
        assert code == 200 and state["editor_tool"] == "build-aerial-road"
        ident = identity(state)
        code, preview = request(url + "/api/road/preview", {**ident,
            "start_xyz": [0, -2, 1], "end_xyz": [0, 2, 1], "snap_radius_m": 0})
        assert code == 200 and preview["crossings"] == [[0.0, 0.0, 1.0]]
        code, committed = request(url + "/api/edit", {**ident, "operation": "build_aerial_road",
            "start_xyz": [0, -2, 1], "end_xyz": [0, 2, 1], "connect_intersections": True})
        assert code == 200 and committed["revision"] == state["revision"] + 1
        ident = identity(committed)
        context = {**ident, "active_layer": "aerial", "active_domain": "aerial",
                   "tool": "delete-aerial-edge"}
        code, state = request(url + "/api/editor/context", context)
        assert code == 200 and state["editor_tool"] == "delete-aerial-edge"
        ident = identity(state)
        edge_id = state["document"]["edges"][-1]["id"]
        code, deleted = request(url + "/api/edit", {**ident, "operation": "remove_edge", "id": edge_id})
        assert code == 200
        assert len(deleted["document"]["nodes"]) == 5
        assert all(row["id"] != edge_id for row in deleted["document"]["edges"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("domain", ["ground", "aerial"])
def test_legacy_single_graph_upgrade_preserves_original_domain(project, domain):
    current = project.root / project.saved_revision
    doc = project.document_for(domain)
    doc.add_node([0, 0, 0 if domain == "ground" else 2], node_id="legacy")
    write_json(current / "topology.json", doc.to_dict())
    manifest = read_json(current / "manifest.json")
    manifest.pop("navigation_products")
    manifest["version"] = 1
    manifest["files"]["topology.json"] = sha256(current / "topology.json")
    write_json(current / "manifest.json", manifest)
    root_manifest = read_json(project.root / "project.json")
    root_manifest["version"] = 1
    root_manifest.pop("navigation_products")
    write_json(project.root / "project.json", root_manifest)
    upgraded = MapProject(project.root)
    assert upgraded.document_for(domain).nodes[0]["id"] == "legacy"
    assert not upgraded.document_for("aerial" if domain == "ground" else "ground").nodes
    upgraded.save()
    assert MapProject(project.root).document_for(domain).nodes[0]["id"] == "legacy"


def request(url, body=None):
    req = Request(url, None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urlopen(req) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


def identity(state):
    return {key: state[key] for key in ("csrf_token", "revision", "scene_key", "scene_generation", "context_generation")} | {"domain": state["active_domain"]}


def test_http_domain_switch_invalidates_gesture_and_revision_conflicts(project):
    server = make_server(project, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        _, state = request(url + "/api/state")
        old_identity = identity(state)
        for field in ("revision", "scene_key", "scene_generation", "context_generation"):
            incomplete = {**old_identity, "domain": "aerial"}
            del incomplete[field]
            code, _ = request(url + "/api/editor/context", incomplete)
            assert code == 400
        _, unchanged = request(url + "/api/state")
        assert identity(unchanged) == old_identity
        code, state = request(url + "/api/editor/context", {**old_identity, "domain": "aerial"})
        assert code == 200 and state["active_domain"] == "aerial"
        code, _ = request(url + "/api/edit", {**old_identity, "operation": "add_node", "position_xyz": [0, 0, 0]})
        assert code == 409
        operation = {**identity(state), "operation": "add_node", "position_xyz": [0, 0, 2], "edit_mode": "2.5d"}
        code, state = request(url + "/api/edit", operation)
        assert code == 200 and state["document"]["nodes"][0]["edit_mode"] == "2.5d"
        code, _ = request(url + "/api/edit", operation)
        assert code == 409
        assert not project.document.nodes and len(project.document_for("aerial").nodes) == 1
        code, _ = request(url + "/api/web/selection", {**identity(state), "selection": {"kind": "node", "id": "node:0001"}})
        assert code == 200
        _, context = request(url + "/api/isaac/context")
        assert context["active_domain"] == "aerial" and context["web_selection"]["id"] == "node:0001"
        assert context["document_snapshot"]["nodes"][0]["edit_mode"] == "2.5d"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_http_ground_road_preview_is_read_only_and_commit_requires_road_tool(project):
    project.edit("add_node", position_xyz=[-2, 0, 0], level_id="ground")
    project.edit("add_node", position_xyz=[2, 0, 0], level_id="ground")
    project.edit("add_edge", from_id="node:0001", to_id="node:0002")
    revision, snapshot = project.revision, project._snapshot()
    server = make_server(project, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        _, state = request(url + "/api/state")
        ident = identity(state)
        code, _ = request(url + "/api/edit", {**ident, "operation": "build_ground_road",
            "start_xyz": [0, -2, 0], "end_xyz": [0, 2, 0], "level_id": "ground"})
        assert code == 400
        context = {**ident, "active_layer": "ground", "active_domain": "ground",
                   "tool": "build-ground-road", "level_id": "ground"}
        code, state = request(url + "/api/editor/context", context)
        assert code == 200
        ident = identity(state)
        code, preview = request(url + "/api/road/preview", {**ident,
            "start_xyz": [0, -2, 0], "end_xyz": [0, 2, 0], "snap_radius_m": 0})
        assert code == 200 and len(preview["crossings"]) == 1
        assert project.revision == revision and project._snapshot() == snapshot
        code, committed = request(url + "/api/edit", {**ident, "operation": "build_ground_road",
            "start_xyz": [0, -2, 0], "end_xyz": [0, 2, 0], "level_id": "ground",
            "snap_radius_m": 0, "connect_intersections": True})
        assert code == 200 and committed["revision"] == revision + 1
        assert len(project.document.nodes) == 5 and len(project.document.edges) == 4
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
