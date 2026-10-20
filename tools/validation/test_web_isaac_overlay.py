"""Offline checks for the web-selection Isaac overlay (server, planner, executor)."""
from __future__ import annotations

import json
import math
import sys
import threading
import types
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tools.topology_authoring import isaac_bridge, web_overlay
from tools.topology_authoring.isaac_session import IsaacMapSession
from tools.topology_authoring.map_project import MapProject
from tools.topology_authoring.map_server import make_server
from tools.topology_authoring.model import TopologyDocument
from tools.topology_authoring.web_overlay import (
    HIGHLIGHT_COLOR,
    WEB_OVERLAY_ROOT,
    WebOverlayRenderer,
    build_overlay_specs,
    compact_document_snapshot,
    normalize_web_selection,
)


LEVELS = [{"id": "1f", "label": "First floor", "support_z_m": 0.0}]


def _document():
    document = TopologyDocument("office")
    document.set_levels(LEVELS)
    document._undo.clear()
    document._redo.clear()
    return document


def _rich_document():
    document = _document()
    document.add_node([0.0, 0.0, 0.0], level_id="1f", label="Start",
                      heading_rad=math.pi / 4)
    document.add_node([2.0, 0.0, 0.0], level_id="1f")
    document.add_node([2.0, 2.0, 0.0], level_id="1f")
    document.add_edge("node:0001", "node:0002", [[0.0, 0.0, 0.0], [2.0, 0.0, 0.0]],
                      directed=False)
    document.add_edge("node:0002", "node:0003", [[2.0, 0.0, 0.0], [2.0, 2.0, 0.0]],
                      directed=True)
    document.upsert_semantic("regions", {
        "id": "r1", "kind": "room", "label": "Bay", "aliases": [],
        "properties": {"confidence": "manual_unverified"},
        "polygon_xyz": [[0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [2.0, 2.0, 0.0]],
        "height_m": 2.5, "level_id": "1f", "entrance_node_ids": []})
    document.upsert_semantic("entities", {
        "id": "e1", "kind": "furniture", "label": "Desk", "aliases": [],
        "properties": {"weight": 12.5}, "position_xyz": [1.0, 1.0, 0.0],
        "region_id": "r1", "source_ref": None})
    document.upsert_semantic("interaction_poses", {
        "id": "p1", "kind": "approach", "label": "Front", "aliases": [],
        "properties": {}, "position_xyz": [1.0, 1.0, 0.9],
        "heading_rad": math.pi / 2, "owner_id": "e1"})
    document.upsert_semantic("routes", {
        "id": "rt1", "kind": "walk", "label": "Loop", "aliases": [],
        "properties": {}, "node_ids": ["node:0001", "node:0002"],
        "edge_ids": ["edge:0001"]})
    return document


# ---------------------------------------------------------------- pure planner


def test_compact_document_snapshot_strips_to_geometry_and_carries_revision():
    document = _rich_document()
    snapshot = compact_document_snapshot(document, 12)

    assert snapshot["revision"] == 12
    assert snapshot["version"] == 2
    assert snapshot["truncated"] is False
    assert [row["id"] for row in snapshot["nodes"]] == ["node:0001", "node:0002", "node:0003"]
    assert set(snapshot["nodes"][0]) == {"id", "position_xyz", "level_id", "heading_rad", "label", "kind"}
    assert "heading_rad" not in snapshot["nodes"][1]
    assert snapshot["nodes"][0]["heading_rad"] == pytest.approx(math.pi / 4)
    assert snapshot["nodes"][0]["label"] == "Start"
    assert set(snapshot["edges"][0]) == {
        "id", "from", "to", "polyline_xyz", "directed", "traversal_mode"}
    assert (snapshot["edges"][0]["from"], snapshot["edges"][0]["to"]) == (
        "node:0001", "node:0002")
    assert snapshot["edges"][1]["directed"] is True

    semantics = snapshot["semantics"]
    assert set(semantics) == {"regions", "entities", "interaction_poses", "routes"}
    assert set(semantics["regions"][0]) == {"id", "polygon_xyz", "height_m", "level_id", "label", "kind"}
    assert set(semantics["entities"][0]) == {"id", "position_xyz", "region_id", "level_id"}
    assert set(semantics["interaction_poses"][0]) == {
        "id", "position_xyz", "heading_rad", "owner_id", "level_id"}
    assert set(semantics["routes"][0]) == {"id", "node_ids", "edge_ids"}

    dumped = json.dumps(snapshot)
    for heavy in ("profiles", "properties", "aliases", "entrance_node_ids", "source_ref"):
        assert heavy not in dumped

    # The snapshot owns its rows: mutating it never leaks into the document.
    snapshot["nodes"][0]["position_xyz"][0] = 99.0
    snapshot["semantics"]["routes"][0]["node_ids"].append("node:0003")
    assert document.nodes[0]["position_xyz"][0] == 0.0
    assert document.semantics["routes"][0]["node_ids"] == ["node:0001", "node:0002"]


def test_build_overlay_specs_matches_viewport_visual_rules():
    snapshot = compact_document_snapshot(_rich_document(), 1)
    specs = build_overlay_specs(snapshot)

    assert specs["root"] == WEB_OVERLAY_ROOT == "/SemanticMapOverlay"
    prims = {prim["path"]: prim for prim in specs["prims"]}

    node = prims[f"{WEB_OVERLAY_ROOT}/Nodes/N00000"]
    assert node["shape"] == "sphere" and node["category"] == "node"
    assert node["key"] == "node:0001" and node["position"] == [0.0, 0.0, 0.0]
    assert node["radius"] == pytest.approx(.06)
    assert node["color"] == pytest.approx([0.08, 0.85, 0.18])

    heading = prims[f"{WEB_OVERLAY_ROOT}/Headings/H00000"]
    assert heading["shape"] == "cone"
    assert heading["direction"] == pytest.approx(
        [math.cos(math.pi / 4), math.sin(math.pi / 4), 0.0])
    assert heading["length"] == pytest.approx(.126)
    assert heading["radius"] == pytest.approx(.0576)

    # Undirected edge: two opposing arrows; directed edge: exactly one.
    assert f"{WEB_OVERLAY_ROOT}/Edges/E00000/ArrowA" in prims
    assert f"{WEB_OVERLAY_ROOT}/Edges/E00000/ArrowB" in prims
    assert f"{WEB_OVERLAY_ROOT}/Edges/E00000/Arrow" not in prims
    assert f"{WEB_OVERLAY_ROOT}/Edges/E00001/Arrow" in prims
    assert f"{WEB_OVERLAY_ROOT}/Edges/E00001/ArrowA" not in prims
    arrow = prims[f"{WEB_OVERLAY_ROOT}/Edges/E00000/ArrowA"]
    assert arrow["radius"] == pytest.approx(.32 * .18)
    assert arrow["length"] == pytest.approx(.7 * .18)

    # Region wireframe: one Floor, one Top, one Side per vertex; floor lifted.
    region = f"{WEB_OVERLAY_ROOT}/Regions/R00000"
    assert prims[region + "/Floor"]["points"][0][2] == pytest.approx(.035)
    assert prims[region + "/Floor"]["points"][-1] == prims[region + "/Floor"]["points"][0]
    assert prims[region + "/Top"]["points"][0][2] == pytest.approx(2.5)
    assert sorted(name for name in prims if name.startswith(region + "/Side")) == [
        region + "/Side0", region + "/Side1", region + "/Side2"]

    assert prims[f"{WEB_OVERLAY_ROOT}/entities/P00000"]["radius"] == pytest.approx(.07)
    assert prims[f"{WEB_OVERLAY_ROOT}/entities/P00000"]["color"] == pytest.approx([.95, .80, .15])
    assert prims[f"{WEB_OVERLAY_ROOT}/interaction_poses/P00000"]["color"] == pytest.approx([.95, .2, .65])

    # Version 1 documents scale exactly like viewport.py's legacy sizes.
    v1 = TopologyDocument("warehouse")
    v1.add_node([0.0, 0.0, 0.0])
    v1.add_node([1.0, 0.0, 0.0])
    v1.add_edge("node:0001", "node:0002", [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    v1_prims = {prim["path"]: prim for prim in
                build_overlay_specs(compact_document_snapshot(v1, 0))["prims"]}
    assert v1_prims[f"{WEB_OVERLAY_ROOT}/Nodes/N00000"]["radius"] == pytest.approx(.42)
    assert v1_prims[f"{WEB_OVERLAY_ROOT}/Edges/E00000"]["width"] == pytest.approx(.16)
    v1_arrow = v1_prims[f"{WEB_OVERLAY_ROOT}/Edges/E00000/ArrowA"]
    assert v1_arrow["radius"] == pytest.approx(.32)
    assert v1_arrow["length"] == pytest.approx(.7)
    # v1 heading arrows are .5 m long (tip pulled back by min(.35, .125)).
    v1_heading = build_overlay_specs(
        {"version": 1, "nodes": [{"id": "n", "position_xyz": [0.0, 0.0, 0.0],
                                  "heading_rad": 0.0}], "edges": [], "semantics": {}})
    heading_path = f"{WEB_OVERLAY_ROOT}/Headings/H00000"
    v1_heading_prim = {prim["path"]: prim for prim in v1_heading["prims"]}[heading_path]
    assert v1_heading_prim["position"] == pytest.approx([.5 - .125, 0.0, 0.0])

    # Planning is deterministic.
    assert build_overlay_specs(snapshot) == specs


def test_build_overlay_specs_skips_invalid_rows_and_routes_have_no_prims():
    snapshot = {
        "revision": 3, "version": 2, "truncated": False,
        "nodes": [
            {"id": "good", "position_xyz": [0.0, 0.0, 0.0]},
            {"id": "short", "position_xyz": [1.0, 2.0]},
            {"id": "nan", "position_xyz": [float("nan"), 0.0, 0.0]},
        ],
        "edges": [
            {"id": "one-point", "polyline_xyz": [[0.0, 0.0, 0.0]], "directed": False},
            {"id": "nan-edge", "polyline_xyz": [[0.0, 0.0, 0.0], [float("nan"), 0.0, 0.0]],
             "directed": True},
        ],
        "semantics": {
            "regions": [{"id": "r-bad", "polygon_xyz": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
                         "height_m": 2.0}],
            "entities": [{"id": "e-nopos", "position_xyz": None}],
            "interaction_poses": [{"id": "p-inf", "position_xyz": [float("inf"), 0.0, 0.0]}],
            "routes": [{"id": "rt", "node_ids": ["good"], "edge_ids": []}],
        },
    }
    specs = build_overlay_specs(snapshot)

    assert [prim["path"] for prim in specs["prims"]] == [f"{WEB_OVERLAY_ROOT}/Nodes/N00000"]
    assert specs["route_members"] == {"rt": [{"category": "node", "id": "good"}]}


def test_snapshot_truncation_flags_and_caps():
    document = _document()
    document.nodes = [{"id": f"node:{index:04d}", "position_xyz": [0.0, 0.0, 0.0]}
                      for index in range(web_overlay.MAX_NODE_ROWS + 1)]
    snapshot = compact_document_snapshot(document, 7)
    assert snapshot["truncated"] is True
    assert len(snapshot["nodes"]) == web_overlay.MAX_NODE_ROWS

    capped = _document()
    capped.semantics = {"entities": [
        {"id": f"e{index}", "position_xyz": [0.0, 0.0, 0.0], "region_id": None,
         "level_id": None} for index in range(web_overlay.MAX_SEMANTIC_ROWS + 1)]}
    snapshot = compact_document_snapshot(capped, 0)
    assert snapshot["truncated"] is True
    assert len(snapshot["semantics"]["entities"]) == web_overlay.MAX_SEMANTIC_ROWS

    # Serialization over 1 MiB drops semantics first, then edges; nodes stay.
    bulky = _document()
    bulky.nodes = [{"id": "node:0001", "position_xyz": [0.0, 0.0, 0.0]}]
    polyline = [[float(value), 0.0, 0.0] for value in range(1500)]
    bulky.edges = [{"id": f"edge:{index:04d}", "polyline_xyz": [list(point) for point in polyline],
                    "directed": False} for index in range(60)]
    bulky.semantics = {"entities": [{"id": "e1", "position_xyz": [0.0, 0.0, 0.0],
                                     "region_id": None, "level_id": None}]}
    snapshot = compact_document_snapshot(bulky, 0)
    assert snapshot["truncated"] is True
    assert snapshot["semantics"]["entities"] == []
    assert snapshot["edges"] == []
    assert [row["id"] for row in snapshot["nodes"]] == ["node:0001"]


# ------------------------------------------------------------------ executor


class _FakeStage:
    def __init__(self):
        self.session_layer = object()
        self.removed = []

    def GetSessionLayer(self):
        return self.session_layer

    def GetPrimAtPath(self, path):
        return SimpleNamespace(path=path, IsValid=lambda: True)

    def RemovePrim(self, path):
        self.removed.append(path)


def _install_renderer_fakes(monkeypatch):
    state = {"depth": 0, "contexts": [], "defines": [], "attrs": [], "custom": [],
             "colors": [], "orients": []}

    class _EditContext:
        def __init__(self, stage, layer):
            state["contexts"].append((stage, layer))

        def __enter__(self):
            state["depth"] += 1

        def __exit__(self, *_args):
            state["depth"] -= 1

    class _Attr:
        def __init__(self, path, name):
            self.path, self.name = path, name

        def Set(self, value):
            state["attrs"].append((self.path, self.name, value, state["depth"]))
            return self

    class _Prim:
        def __init__(self, path):
            self.path = path

        def IsValid(self):
            return True

        def SetCustomDataByKey(self, key, value):
            state["custom"].append((self.path, key, value))

    class _Schema:
        def __init__(self, path):
            self.path = path

        def CreateRadiusAttr(self):
            return _Attr(self.path, "radius")

        def CreateHeightAttr(self):
            return _Attr(self.path, "height")

        def CreateTypeAttr(self):
            return _Attr(self.path, "type")

        def CreateCurveVertexCountsAttr(self):
            return _Attr(self.path, "counts")

        def CreatePointsAttr(self):
            return _Attr(self.path, "points")

        def CreateWidthsAttr(self):
            return _Attr(self.path, "widths")

        def AddTranslateOp(self):
            return _Attr(self.path, "translate")

        def GetPrim(self):
            return _Prim(self.path)

    def _define(_stage, path):
        state["defines"].append((path, state["depth"]))
        return _Schema(path)

    usd = types.ModuleType("pxr.Usd")
    usd.EditContext = _EditContext
    usdgeom = types.ModuleType("pxr.UsdGeom")
    usdgeom.Tokens = types.SimpleNamespace(linear="linear")
    usdgeom.GetStageMetersPerUnit = lambda _stage: 1.0
    usdgeom.Xform = types.SimpleNamespace(Define=_define)
    usdgeom.Sphere = types.SimpleNamespace(Define=_define)
    usdgeom.BasisCurves = types.SimpleNamespace(Define=_define)
    usdgeom.Cone = types.SimpleNamespace(Define=_define)
    pxr = types.ModuleType("pxr")
    pxr.Usd = usd
    pxr.UsdGeom = usdgeom
    pxr.Gf = types.SimpleNamespace(Vec3d=lambda *value: list(value),
                                    Vec3f=lambda *value: list(value))
    monkeypatch.setitem(sys.modules, "pxr", pxr)
    monkeypatch.setitem(sys.modules, "pxr.Usd", usd)
    monkeypatch.setitem(sys.modules, "pxr.UsdGeom", usdgeom)
    monkeypatch.setattr(web_overlay, "_set_color", lambda prim, value: state["colors"].append(
        (prim.path, tuple(value), state["depth"])))
    monkeypatch.setattr(web_overlay, "_orient_z", lambda prim, direction: state["orients"].append(
        (prim.path, tuple(direction), state["depth"])))
    return state


def _last_colors(state):
    result = {}
    for path, color, _depth in state["colors"]:
        result[path] = color
    return result


def test_rebuild_writes_only_inside_session_layer_edit_context(monkeypatch):
    state = _install_renderer_fakes(monkeypatch)
    stage = _FakeStage()
    renderer = WebOverlayRenderer(stage)
    renderer.rebuild(build_overlay_specs(compact_document_snapshot(_rich_document(), 1)))

    assert stage.removed == [WEB_OVERLAY_ROOT]
    assert state["contexts"] == [(stage, stage.session_layer)]
    assert state["defines"] and all(depth == 1 for _path, depth in state["defines"])
    assert any(path == WEB_OVERLAY_ROOT for path, _depth in state["defines"])
    assert state["attrs"]  # geometry was actually authored
    assert all(depth == 1 for _path, _name, _value, depth in state["attrs"])
    assert (f"{WEB_OVERLAY_ROOT}/Nodes/N00000", "semantic_id", "node:0001") in state["custom"]

    renderer.rebuild(build_overlay_specs(compact_document_snapshot(_rich_document(), 2)))
    assert stage.removed == [WEB_OVERLAY_ROOT, WEB_OVERLAY_ROOT]
    assert len(state["contexts"]) == 2


def test_highlight_touches_display_color_only_and_restores_previous(monkeypatch):
    state = _install_renderer_fakes(monkeypatch)
    stage = _FakeStage()
    renderer = WebOverlayRenderer(stage)
    specs = build_overlay_specs(compact_document_snapshot(_rich_document(), 1))
    renderer.rebuild(specs)
    geometry_after_rebuild = list(state["attrs"])
    color_count_after_rebuild = len(state["colors"])
    node_paths = sorted(prim["path"] for prim in specs["prims"]
                        if prim.get("key") == "node:0001")

    renderer.highlight({"kind": "node", "collection": None, "id": "node:0001"})
    assert state["attrs"] == geometry_after_rebuild
    painted = state["colors"][color_count_after_rebuild:]
    assert sorted((path, color) for path, color, _depth in painted) == [
        (path, HIGHLIGHT_COLOR) for path in node_paths]
    assert all(depth == 1 for _path, _color, depth in painted)
    assert state["contexts"][-1] == (stage, stage.session_layer)

    # Switching selection restores the old key and paints the new one.
    entity_paths = sorted(prim["path"] for prim in specs["prims"] if prim.get("key") == "e1")
    renderer.highlight({"kind": "semantic", "collection": "entities", "id": "e1"})
    current = _last_colors(state)
    assert all(current[path] == tuple(web_overlay.NODE_COLOR) for path in node_paths)
    assert all(current[path] == tuple(HIGHLIGHT_COLOR) for path in entity_paths)

    # null clears every highlight back to base colors.
    renderer.highlight(None)
    current = _last_colors(state)
    assert set(current) == {prim["path"] for prim in specs["prims"]}
    assert set(current.values()) <= {tuple(prim["color"]) for prim in specs["prims"]}

    # Route selections expand to their member node and edge prims.
    renderer.highlight({"kind": "semantic", "collection": "routes", "id": "rt1"})
    member_paths = {prim["path"] for prim in specs["prims"]
                    if prim.get("key") in {"node:0001", "node:0002", "edge:0001"}}
    current = _last_colors(state)
    assert {path for path, color in current.items()
            if color == tuple(HIGHLIGHT_COLOR)} == member_paths

    # Unknown or truncated ids are a safe no-op, as is a repeated selection.
    before = list(state["colors"])
    renderer.highlight({"kind": "node", "collection": None, "id": "node:9999"})
    renderer.highlight({"kind": "semantic", "collection": "routes", "id": "rt1"})
    assert state["colors"] == before
    assert state["attrs"] == geometry_after_rebuild


def test_destroy_removes_root_and_clears_maps(monkeypatch):
    state = _install_renderer_fakes(monkeypatch)
    stage = _FakeStage()
    renderer = WebOverlayRenderer(stage)
    renderer.rebuild(build_overlay_specs(compact_document_snapshot(_rich_document(), 1)))
    assert renderer._by_key and renderer._route_members

    renderer.destroy()

    assert stage.removed[-1] == WEB_OVERLAY_ROOT
    assert state["contexts"][-1] == (stage, stage.session_layer)
    assert renderer._by_key == {} and renderer._route_members == {}
    colors = len(state["colors"])
    renderer.highlight({"kind": "node", "collection": None, "id": "node:0001"})
    assert len(state["colors"]) == colors


# -------------------------------------------------------------------- server


def _post(url, payload, headers=None):
    request = Request(url, json.dumps(payload).encode(),
                      headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urlopen(request) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.loads(error.read())


def _serve(project, catalog=None):
    server = make_server(project, 0, catalog)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_port}"


def test_web_selection_roundtrip_and_validation(tmp_path):
    project = MapProject.create_blank(tmp_path / "map", "office", [-1, -1, 1, 1], .5)
    project.edit("add_node", revision=project.revision, position_xyz=[0.0, 0.0, 0.0],
                 level_id="ground", label="Desk node")
    project.edit("upsert_semantic", revision=project.revision, collection="entities", record={
        "id": "e1", "kind": "furniture", "label": "Desk", "aliases": [],
        "properties": {}, "position_xyz": [.5, .5, 0.0], "region_id": None,
        "source_ref": None})
    server, thread, url = _serve(project)
    try:
        context = json.load(urlopen(url + "/api/isaac/context"))
        token = context["csrf_token"]
        state = json.load(urlopen(url + "/api/state"))
        assert context["document_snapshot"]["revision"] == state["revision"] == project.revision
        assert len(context["document_snapshot"]["nodes"]) == 1
        assert context["web_selection"] is None

        code, body = _post(url + "/api/web/selection", {
            "csrf_token": token,
            "selection": {"kind": "node", "collection": None, "id": "node:0001"}})
        assert code == 200
        assert body == {"selection": {"kind": "node", "collection": None, "id": "node:0001"}}
        context = json.load(urlopen(url + "/api/isaac/context"))
        assert context["web_selection"]["id"] == "node:0001"
        assert context["document_snapshot"]["revision"] == project.revision

        code, body = _post(url + "/api/web/selection", {
            "csrf_token": token,
            "selection": {"kind": "semantic", "collection": "entities", "id": "e1"}})
        assert code == 200 and body["selection"]["collection"] == "entities"
        assert json.load(urlopen(url + "/api/isaac/context"))["web_selection"]["id"] == "e1"

        for selection in ({"kind": "semantic", "collection": "entities", "id": "missing"},
                          {"kind": "node", "collection": None, "id": "node:9999"},
                          {"kind": "semantic", "collection": "bogus", "id": "e1"},
                          {"kind": "triangle", "collection": None, "id": "e1"},
                          {"kind": "node", "collection": "entities", "id": "node:0001"},
                          {"kind": "node", "collection": None, "id": ""},
                          "node:0001"):
            code, body = _post(url + "/api/web/selection", {"csrf_token": token,
                                                            "selection": selection})
            assert code == 400, selection
            assert body["error"]

        code, body = _post(url + "/api/web/selection", {"selection": None})
        assert code == 403 and body["error"] == "editor token required"
        code, body = _post(url + "/api/web/selection", {
            "csrf_token": token,
            "selection": {"kind": "node", "collection": None, "id": "node:0001"}},
            headers={"Origin": "http://evil.example"})
        assert code == 403 and body["error"] == "local same-origin access required"
        # Rejected payloads never touch the stored selection.
        assert json.load(urlopen(url + "/api/isaac/context"))["web_selection"]["id"] == "e1"

        code, body = _post(url + "/api/web/selection", {"csrf_token": token, "selection": None})
        assert code == 200 and body == {"selection": None}
        assert json.load(urlopen(url + "/api/isaac/context"))["web_selection"] is None
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_web_selection_cleared_when_edit_removes_target_and_on_scene_open(tmp_path):
    project = MapProject.create_blank(tmp_path / "map", "office", [-1, -1, 1, 1], .5)
    MapProject.create_blank(tmp_path / "other", "warehouse", [-1, -1, 1, 1], .5)
    project.edit("add_node", revision=project.revision, position_xyz=[0.0, 0.0, 0.0],
                 level_id="ground")
    server, thread, url = _serve(project, tmp_path)
    try:
        token = json.load(urlopen(url + "/api/isaac/context"))["csrf_token"]

        def selection_now():
            return json.load(urlopen(url + "/api/isaac/context"))["web_selection"]

        def revision_now():
            return json.load(urlopen(url + "/api/state"))["revision"]

        assert _post(url + "/api/web/selection", {
            "csrf_token": token,
            "selection": {"kind": "node", "collection": None, "id": "node:0001"}})[0] == 200

        # Undo falls back below the node's revision: selection must clear.
        code, _body = _post(url + "/api/undo", {"csrf_token": token,
                                                "revision": revision_now()})
        assert code == 200 and selection_now() is None
        # Redo restores the node but does not resurrect the selection.
        code, _body = _post(url + "/api/redo", {"csrf_token": token,
                                                "revision": revision_now()})
        assert code == 200 and selection_now() is None

        assert _post(url + "/api/web/selection", {
            "csrf_token": token,
            "selection": {"kind": "node", "collection": None, "id": "node:0001"}})[0] == 200
        # Deleting the selected node clears the selection again.
        code, _body = _post(url + "/api/edit", {"csrf_token": token,
                                                "revision": revision_now(),
                                                "operation": "remove_node", "id": "node:0001"})
        assert code == 200 and selection_now() is None

        code, _body = _post(url + "/api/edit", {"csrf_token": token,
                                                "revision": revision_now(),
                                                "operation": "add_node",
                                                "position_xyz": [0.0, 0.0, 0.0],
                                                "level_id": "ground"})
        assert code == 200
        assert _post(url + "/api/web/selection", {
            "csrf_token": token,
            "selection": {"kind": "node", "collection": None, "id": "node:0001"}})[0] == 200

        # Opening another project rotates the token and resets the selection.
        code, _body = _post(url + "/api/open", {"csrf_token": token,
                                                "revision": revision_now(),
                                                "id": "other", "discard_unsaved": True})
        assert code == 200
        context = json.load(urlopen(url + "/api/isaac/context"))
        assert context["web_selection"] is None
        assert context["scene_key"] == "warehouse"
        code, body = _post(url + "/api/web/selection", {
            "csrf_token": token,
            "selection": {"kind": "node", "collection": None, "id": "node:0001"}})
        assert code == 403 and body["error"] == "editor token required"
    finally:
        server.shutdown(); server.server_close(); thread.join()


# --------------------------------------------------------------------- bridge


class _BridgeStage:
    def __init__(self, path):
        self._layer = SimpleNamespace(realPath=str(path), identifier=str(path))

    def GetRootLayer(self):
        return self._layer


def test_bridge_never_publishes_overlay_prims(tmp_path, monkeypatch):
    bridge = isaac_bridge.IsaacSelectionBridge()
    sent = []
    monkeypatch.setattr(bridge, "_enqueue", lambda payload: sent.append(payload) or payload)
    monkeypatch.setattr(isaac_bridge, "prim_selection_payload",
                        lambda _stage, paths, **_kwargs: {"selection_kind": "prim",
                                                          "prim_paths": list(paths)})

    # (a) A point hit on an overlay prim is treated as a miss, not an error.
    bridge._point_enabled = True
    bridge._on_point_hit(f"{WEB_OVERLAY_ROOT}/Nodes/N00003", (1.0, 2.0, 3.0))
    assert sent == []
    assert bridge.status()["last_error"] is None

    stage = _BridgeStage(tmp_path / "scene.usd")
    bridge.source_usd = str(tmp_path / "scene.usd")

    def _bind(paths):
        bridge._selection = SimpleNamespace(get_selected_prim_paths=lambda: list(paths))
        bridge._selection_context = SimpleNamespace(get_selection=lambda: bridge._selection,
                                                    get_stage=lambda: stage)

    # (b) Asset mode with only overlay prims selected degrades to a clear.
    _bind([f"{WEB_OVERLAY_ROOT}/Nodes/N00003"])
    assert bridge.send_current_selection()["selection_kind"] == "clear"
    assert sent[-1]["selection_kind"] == "clear"

    # (c) A mixed selection publishes only the real scene asset.
    _bind([f"{WEB_OVERLAY_ROOT}/Edges/E00001", "/World/Desk"])
    payload = bridge.send_current_selection()
    assert payload["selection_kind"] == "prim"
    assert payload["prim_paths"] == ["/World/Desk"]

    for published in sent:
        assert all(not str(path).startswith(WEB_OVERLAY_ROOT)
                   for path in published.get("prim_paths", []))


# -------------------------------------------------------------- isaac_session


class _FakeRenderer:
    def __init__(self, rebuild_error=None):
        self.rebuild_error = rebuild_error
        self.rebuild_attempts = 0
        self.rebuilds = []
        self.highlights = []
        self.destroys = 0

    def rebuild(self, specs):
        self.rebuild_attempts += 1
        if self.rebuild_error is not None:
            raise self.rebuild_error
        self.rebuilds.append(specs)

    def highlight(self, selection):
        self.highlights.append(selection)

    def destroy(self):
        self.destroys += 1


_BASE_SNAPSHOT = compact_document_snapshot(_rich_document(), 5)


def _make_session(payload, renderer=None):
    holder = {"payload": payload}
    reports = []
    session = IsaacMapSession(
        SimpleNamespace(get_stage=lambda: None),
        "http://127.0.0.1:1",
        lambda: dict(holder["payload"]),
        lambda generation, state, error=None: reports.append((generation, state, error)),
        bridge_factory=lambda *_args, **_kwargs: pytest.fail("bridge_factory is offline"),
        window_factory=lambda *_args, **_kwargs: pytest.fail("window_factory is offline"))
    session.generation = payload["scene_generation"]
    session.bridge = SimpleNamespace(status=lambda: {"state": "connected"},
                                     stop=lambda: None)
    if renderer is not None:
        session.overlay = renderer
    return session, holder, reports


def _context(web_selection, revision=5, generation=7):
    return {"scene_generation": generation,
            "document_snapshot": {**_BASE_SNAPSHOT, "revision": revision},
            "web_selection": web_selection}


def _tick(session):
    session.next_poll = 0
    session.tick()


NODE_SELECTION = {"kind": "node", "collection": None, "id": "node:0001"}


def test_tick_rebuilds_on_revision_change_and_replays_highlight():
    renderer = _FakeRenderer()
    session, holder, _reports = _make_session(_context(NODE_SELECTION), renderer)

    _tick(session)
    assert len(renderer.rebuilds) == 1
    assert renderer.rebuilds[0]["root"] == WEB_OVERLAY_ROOT
    assert renderer.highlights == [NODE_SELECTION]
    assert session.overlay_revision == 5
    assert session.web_selection_key == ("node", None, "node:0001")

    _tick(session)
    assert len(renderer.rebuilds) == 1 and len(renderer.highlights) == 1

    holder["payload"]["document_snapshot"] = {**_BASE_SNAPSHOT, "revision": 6}
    _tick(session)
    assert len(renderer.rebuilds) == 2
    assert renderer.highlights == [NODE_SELECTION, NODE_SELECTION]
    assert session.overlay_revision == 6


def test_tick_rebuilds_when_domain_or_level_changes_without_revision_change():
    renderer = _FakeRenderer()
    payload = {**_context(NODE_SELECTION),
               "active_domain": "ground", "active_level": "L1"}
    session, holder, _reports = _make_session(payload, renderer)

    _tick(session)
    assert len(renderer.rebuilds) == 1
    holder["payload"]["active_domain"] = "aerial"
    _tick(session)
    assert len(renderer.rebuilds) == 2
    holder["payload"]["active_level"] = "L2"
    _tick(session)
    assert len(renderer.rebuilds) == 3
    assert session.overlay_context_key == (5, "aerial", "L2")


def test_tick_selection_only_change_calls_highlight_without_rebuild():
    renderer = _FakeRenderer()
    entity = {"kind": "semantic", "collection": "entities", "id": "e1"}
    session, holder, _reports = _make_session(_context(NODE_SELECTION), renderer)

    _tick(session)
    assert len(renderer.rebuilds) == 1

    holder["payload"]["web_selection"] = entity
    _tick(session)
    assert len(renderer.rebuilds) == 1
    assert renderer.highlights == [NODE_SELECTION, entity]

    holder["payload"]["web_selection"] = None
    _tick(session)
    assert len(renderer.rebuilds) == 1
    assert renderer.highlights == [NODE_SELECTION, entity, None]


def test_generation_switch_destroys_overlay_and_resets_cursors():
    renderer = _FakeRenderer()
    session, holder, _reports = _make_session(_context(None), renderer)
    session.task = SimpleNamespace(done=lambda: False, cancel=lambda: None)

    holder["payload"]["scene_generation"] = 8
    _tick(session)
    assert renderer.destroys == 1
    assert session.overlay is None
    assert session.overlay_revision is None
    assert session.web_selection_key is None

    # A residue overlay=None (failed _open) must survive another switch.
    session.bridge = SimpleNamespace(status=lambda: {"state": "connected"},
                                     stop=lambda: None)
    holder["payload"]["scene_generation"] = 9
    _tick(session)
    assert renderer.destroys == 1

    session.overlay = _FakeRenderer()
    session.close()
    assert session.overlay is None
    session.close()  # overlay None: close stays a no-op, never raises


def test_rebuild_failure_does_not_break_tick_or_retry_storm():
    renderer = _FakeRenderer(rebuild_error=RuntimeError("boom"))
    session, holder, reports = _make_session(_context(NODE_SELECTION), renderer)

    _tick(session)
    assert (7, "ready", None) in reports
    assert not any(state == "error" for _generation, state, _error in reports)
    assert renderer.rebuild_attempts == 1
    assert renderer.highlights == []
    assert session.overlay_revision == 5

    _tick(session)
    assert renderer.rebuild_attempts == 1  # same revision: no retry storm
    # The failed rebuild never advanced the selection cursor, so the unchanged
    # selection is still applied to whatever prims exist; that is harmless.

    entity = {"kind": "semantic", "collection": "entities", "id": "e1"}
    holder["payload"]["web_selection"] = entity
    _tick(session)
    assert renderer.rebuild_attempts == 1
    assert renderer.highlights == [NODE_SELECTION, entity]

    holder["payload"]["document_snapshot"] = {**_BASE_SNAPSHOT, "revision": 6}
    _tick(session)
    assert renderer.rebuild_attempts == 2  # a fresh revision may retry


def test_tick_with_overlay_none_is_silent_noop():
    session, _holder, reports = _make_session(_context(NODE_SELECTION), renderer=None)

    _tick(session)

    assert session.overlay is None
    assert reports == [(7, "ready", None)]


def test_normalize_web_selection_accepts_route_kinds():
    document = _rich_document()
    assert normalize_web_selection(document, None) is None
    assert normalize_web_selection(document, {"kind": "semantic", "collection": "routes",
                                              "id": "rt1"}) == {
        "kind": "semantic", "collection": "routes", "id": "rt1"}
    assert normalize_web_selection(document, {"kind": "edge", "id": "edge:0002"}) == {
        "kind": "edge", "collection": None, "id": "edge:0002"}
    with pytest.raises(ValueError, match="unknown web selection id"):
        normalize_web_selection(document, {"kind": "semantic", "collection": "routes",
                                            "id": "missing"})


@pytest.mark.parametrize("draft_kind", ["node", "edge", "region"])
def test_disconnect_and_reconnect_invalidate_native_input(draft_kind, monkeypatch):
    payload = {**_context(None), "revision": 5, "csrf_token": "token", "scene_key": "office",
               "context_generation": 0, "active_domain": "ground", "active_level": "ground",
               "editor_tool": "add-node",
               "levels": [{"id": "ground", "label": "Ground", "support_z_m": 0.0}]}
    session, _holder, reports = _make_session(payload)
    client = isaac_bridge.IsaacAuthoringClient()
    dispatcher = SimpleNamespace(client=client, pending=False, drain=lambda: None,
                                 submit=lambda *_args, **_kwargs: False)
    controller = isaac_bridge.IsaacTopologyController(dispatcher)
    controller.update_context(payload)
    session.editor_controller, session.editor_dispatcher = controller, dispatcher
    connection = {"state": "connected"}
    session.bridge = SimpleNamespace(status=lambda: dict(connection), stop=lambda: None)
    cancellations = []
    original_cancel = controller.cancel_gesture

    def cancel(**kwargs):
        cancellations.append(kwargs)
        original_cancel(**kwargs)

    monkeypatch.setattr(controller, "cancel_gesture", cancel)

    def begin_input():
        if draft_kind == "node":
            controller.begin_drag({"kind": "node", "id": "node:0001"}, [0, 0, 0])
            controller.update_drag([.1, .2, 0])
        elif draft_kind == "edge":
            controller.activate_node_for_edge("node:0001")
        else:
            controller.add_region_point([.1, .2, 0])

    def assert_cleared():
        assert controller.gesture is None and controller.preview is None
        assert controller.edge_start is None and controller.region_draft == []

    begin_input()
    connection.update(state="error", last_error="disconnected")
    _tick(session)
    assert_cleared()
    assert len(cancellations) == 1
    _tick(session)
    assert len(cancellations) == 1

    begin_input()  # Input begun offline is also stale when the link recovers.
    connection["state"] = "connected"
    _tick(session)
    assert_cleared()
    assert len(cancellations) == 2
    assert reports[-1] == (7, "ready", None)


def test_local_drag_preview_renders_between_authority_polls():
    frames = []
    renderer = _FakeRenderer()
    renderer.editor_state = lambda **state: frames.append(state)
    session, _holder, _reports = _make_session(_context(None), renderer)
    session.editor_controller = SimpleNamespace(
        snapshot=_BASE_SNAPSHOT, context={"active_domain": "aerial"},
        selection={"kind": "node", "id": "node:0001"},
        preview={"kind": "node", "id": "node:0001", "position_xyz": [1, 2, 3]},
        region_draft=[])
    session.next_poll = float("inf")

    def unexpected_poll():
        pytest.fail("local preview must not poll the authoritative context")

    session.read_context = unexpected_poll
    session.tick()
    assert len(frames) == 1
    assert frames[0]["preview"]["position_xyz"] == [1, 2, 3]
    session.editor_controller.preview = {
        "kind": "node", "id": "node:0001", "position_xyz": [2, 3, 4]}
    session.tick()
    assert len(frames) == 2
    assert frames[1]["preview"]["position_xyz"] == [2, 3, 4]
    assert renderer.rebuild_attempts == 0


@pytest.mark.parametrize("meters_per_unit", [1.0, .01])
def test_real_usd_overlay_uses_scene_units_and_preserves_author_layer(meters_per_unit):
    Usd = pytest.importorskip("pxr.Usd")
    from pxr import UsdGeom

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, meters_per_unit)
    before = stage.GetRootLayer().ExportToString()
    document = TopologyDocument("units", domain="aerial")
    document.add_node([1, 2, 3])
    snapshot = compact_document_snapshot(document, 0)
    renderer = WebOverlayRenderer(stage)

    for _ in range(2):
        renderer.rebuild(build_overlay_specs(snapshot))
        renderer.editor_state(
            snapshot=snapshot, domain="aerial",
            selection={"kind": "node", "id": "node:0001"}, preview=None, region_draft=[])
        node = stage.GetPrimAtPath(WEB_OVERLAY_ROOT + "/Nodes/N00000")
        position = UsdGeom.XformCache().GetLocalToWorldTransform(node).ExtractTranslation()
        assert list(position) == pytest.approx([value / meters_per_unit for value in (1, 2, 3)])
        assert all(spec.layer == stage.GetSessionLayer() for spec in node.GetPrimStack())
        assert stage.GetRootLayer().ExportToString() == before
    renderer.destroy()
    assert stage.GetRootLayer().ExportToString() == before
    assert not stage.GetPrimAtPath(WEB_OVERLAY_ROOT).IsValid()


def test_real_usd_drag_preview_updates_complete_geometry_and_cancels_cleanly():
    Usd = pytest.importorskip("pxr.Usd")
    from copy import deepcopy
    from pxr import UsdGeom

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    before = stage.GetRootLayer().ExportToString()
    snapshot = compact_document_snapshot(_rich_document(), 1)
    original = deepcopy(snapshot)
    renderer = WebOverlayRenderer(stage)
    renderer.rebuild(build_overlay_specs(snapshot))
    node_selection = {"kind": "node", "id": "node:0002"}
    node_preview = {"kind": "node", "id": "node:0002", "position_xyz": [3, 1, 0]}
    renderer.editor_state(snapshot=snapshot, domain="ground", selection=node_selection,
                          preview=node_preview)
    editor = WEB_OVERLAY_ROOT + "/Editor"
    for index, endpoint in ((0, -1), (1, 0)):
        edge_path = f"/Edges/E{index:05d}"
        edge = UsdGeom.BasisCurves(stage.GetPrimAtPath(editor + "/Geometry" + edge_path))
        assert list(edge.GetPointsAttr().Get()[endpoint]) == pytest.approx([3, 1, 0])
        old_edge = UsdGeom.Imageable(stage.GetPrimAtPath(WEB_OVERLAY_ROOT + edge_path))
        assert old_edge.ComputeVisibility() == UsdGeom.Tokens.invisible
    assert UsdGeom.Imageable(stage.GetPrimAtPath(
        WEB_OVERLAY_ROOT + "/Nodes/N00001")).ComputeVisibility() == UsdGeom.Tokens.invisible

    selection = {"kind": "semantic", "collection": "regions", "id": "r1"}
    renderer.editor_state(snapshot=snapshot, domain="ground", selection=selection,
        preview={"kind": "region-vertex", "id": "r1", "index": 1,
                 "position_xyz": [3, 0, 0]})
    assert UsdGeom.Imageable(stage.GetPrimAtPath(
        WEB_OVERLAY_ROOT + "/Edges/E00000")).ComputeVisibility() == UsdGeom.Tokens.inherited
    assert not stage.GetPrimAtPath(editor + "/Geometry/Edges/E00000").IsValid()
    floor = UsdGeom.BasisCurves(stage.GetPrimAtPath(
        editor + "/Geometry/Regions/R00000/Floor"))
    top = UsdGeom.BasisCurves(stage.GetPrimAtPath(
        editor + "/Geometry/Regions/R00000/Top"))
    assert list(floor.GetPointsAttr().Get()[1]) == pytest.approx([3, 0, .035])
    assert list(top.GetPointsAttr().Get()[1]) == pytest.approx([3, 0, 2.5])
    assert UsdGeom.Imageable(stage.GetPrimAtPath(WEB_OVERLAY_ROOT +
        "/Regions/R00000/Floor")).ComputeVisibility() == UsdGeom.Tokens.invisible

    renderer.editor_state(snapshot=snapshot, domain="ground", selection=selection, preview=None)
    assert UsdGeom.Imageable(stage.GetPrimAtPath(WEB_OVERLAY_ROOT +
        "/Regions/R00000/Floor")).ComputeVisibility() == UsdGeom.Tokens.inherited
    assert not stage.GetPrimAtPath(editor + "/Geometry").IsValid()
    assert snapshot == original
    assert stage.GetRootLayer().ExportToString() == before
