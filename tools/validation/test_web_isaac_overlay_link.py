"""End-to-end checks for the web editor ↔ Isaac overlay link.

Drives a throwaway MapProject through make_server on a random port and
verifies the HTTP contract of the overlay plan: the extended
GET /api/isaac/context (document snapshot + web_selection), the
POST /api/web/selection validation rules, the /api/open reset, and the
pure planner's prim specs for nodes/edges/regions/entities/poses.  All
coordinates are scene_world metres, Z-up and pass through verbatim.
"""
from __future__ import annotations

import json
import math
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tools.topology_authoring.map_project import MapProject
from tools.topology_authoring.map_server import make_server
from tools.topology_authoring.model import TopologyDocument
from tools.topology_authoring.web_overlay import (
    WEB_OVERLAY_ROOT,
    build_overlay_specs,
    compact_document_snapshot,
)

GROUND = {"id": "ground", "label": "Ground", "support_z_m": 0.0}


def _request(url, payload=None, headers=None):
    """GET (payload None) or JSON-POST; always returns (status, body)."""
    request = Request(url, None if payload is None else json.dumps(payload).encode(),
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


def _populate(url, token):
    """Author navigation geometry and one independent semantic point."""
    # A freshly created project already saved once, so never assume revision 0.
    revision = json.load(urlopen(url + "/api/state"))["revision"]
    edits = [
        {"operation": "add_node", "position_xyz": [0.0, 0.0, 0.0],
         "level_id": "ground", "label": "Start", "heading_rad": math.pi / 4},
        {"operation": "add_node", "position_xyz": [1.0, 0.0, 0.0], "level_id": "ground"},
        {"operation": "add_node", "position_xyz": [1.0, 1.0, 0.0], "level_id": "ground"},
        {"operation": "add_edge", "from_id": "node:0001", "to_id": "node:0002",
         "directed": False},
        {"operation": "add_edge", "from_id": "node:0002", "to_id": "node:0003",
         "directed": True},
    ]
    for edit in edits:
        if edit["operation"] == "add_edge":
            _switch(url, "ground", "add-edge")
        code, body = _request(url + "/api/edit", {"csrf_token": token,
                                                  "revision": revision, **edit})
        assert code == 200, body
        revision = body["revision"]
    context = json.load(urlopen(url + "/api/isaac/context"))
    code, body = _request(url + "/api/editor/context", {
        **{key: context[key] for key in ("csrf_token", "revision", "scene_key",
                                         "scene_generation", "context_generation")},
        "active_domain": "ground", "active_layer": "semantic_points",
        "editor_tool": "add-semantic-point", "active_level": "ground"})
    assert code == 200, body
    context = body
    code, body = _request(url + "/api/edit", {
        "csrf_token": context["csrf_token"], "revision": context["revision"],
        "scene_key": context["scene_key"], "scene_generation": context["scene_generation"],
        "context_generation": context["context_generation"], "domain": "ground",
        "active_layer": "semantic_points", "operation": "add_semantic_point",
        "id": "semantic:0001",
        "label": "Desk", "kind": "furniture"})
    assert code == 200, body
    revision = body["revision"]
    return revision


def _switch(url, layer, tool):
    context = json.load(urlopen(url + "/api/isaac/context"))
    payload = {key: context[key] for key in (
        "csrf_token", "revision", "scene_key", "scene_generation", "context_generation")}
    payload.update(active_domain="ground", active_layer=layer,
                   editor_tool=tool, active_level="ground")
    code, body = _request(url + "/api/editor/context", payload)
    assert code == 200, body
    return body


# -------------------------------------------------------------------- context


def test_context_serves_document_snapshot_and_web_selection(tmp_path):
    project = MapProject.create_blank(tmp_path / "office", "office", [-1, -1, 1, 1], .5)
    server, thread, url = _serve(project)
    try:
        context = json.load(urlopen(url + "/api/isaac/context"))
        token = context["csrf_token"]
        revision = _populate(url, token)

        context = json.load(urlopen(url + "/api/isaac/context"))
        # Legacy fields survive the extension; the browser bridge only reads these.
        for field in ("scene_key", "source_usd", "source_path", "scene_generation",
                      "runtime", "levels", "csrf_token"):
            assert field in context, field
        assert context["scene_key"] == "office"
        assert context["web_selection"] is None

        snapshot = context["document_snapshot"]
        state = json.load(urlopen(url + "/api/state"))
        assert snapshot["revision"] == state["revision"] == revision
        assert snapshot["version"] == 2
        assert snapshot["truncated"] is False

        assert [row["id"] for row in snapshot["nodes"]] == [
            "node:0001", "node:0002", "node:0003"]
        # Geometry rows carry exactly the drawing fields; no heavy payload leaks.
        assert set(snapshot["nodes"][0]) == {
            "id", "position_xyz", "level_id", "heading_rad", "label", "kind"}
        assert set(snapshot["nodes"][1]) == {"id", "position_xyz", "level_id", "label", "kind"}
        assert snapshot["nodes"][0]["position_xyz"] == [0.0, 0.0, 0.0]
        assert snapshot["nodes"][0]["heading_rad"] == pytest.approx(math.pi / 4)
        assert snapshot["nodes"][0]["label"] == "Start"
        assert [row["id"] for row in snapshot["edges"]] == ["edge:0001", "edge:0002"]
        assert set(snapshot["edges"][0]) == {
            "id", "from", "to", "polyline_xyz", "directed", "traversal_mode"}
        assert (snapshot["edges"][0]["from"], snapshot["edges"][0]["to"]) == (
            "node:0001", "node:0002")
        assert snapshot["edges"][0]["polyline_xyz"] == [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]
        assert snapshot["edges"][0]["directed"] is False
        assert snapshot["edges"][1]["directed"] is True

        assert snapshot["semantics"] == {
            "regions": [], "entities": [], "interaction_poses": [], "routes": []}
        assert context["semantic_points"] == [{
            "id": "semantic:0001",
            "label": "Desk", "kind": "furniture", "description": "",
            "aliases": [], "properties": {}}]

        dumped = json.dumps(snapshot)
        for heavy in ("profiles", "properties", "aliases", "entrance_node_ids", "source_ref"):
            assert heavy not in dumped

        # The served snapshot must be planner-ready: the overlay link consumes
        # exactly this payload without any further server-side shaping.
        specs = build_overlay_specs(snapshot)
        keys = {prim.get("key") for prim in specs["prims"]}
        assert {"node:0001", "node:0002", "node:0003", "edge:0001", "edge:0002"} <= keys
        assert specs["route_members"] == {}
    finally:
        server.shutdown(); server.server_close(); thread.join()


# -------------------------------------------------------------- web selection


def test_web_selection_csrf_unknown_id_and_null_clear(tmp_path):
    project = MapProject.create_blank(tmp_path / "office", "office", [-1, -1, 1, 1], .5)
    server, thread, url = _serve(project)
    try:
        token = json.load(urlopen(url + "/api/isaac/context"))["csrf_token"]
        revision = _populate(url, token)
        _switch(url, "ground", "add-node")
        select_url = url + "/api/web/selection"

        # CSRF gate comes first: missing and wrong tokens are both rejected.
        code, body = _request(select_url, {
            "selection": {"kind": "node", "collection": None, "id": "node:0001"}})
        assert code == 403 and body["error"] == "editor token required"
        code, body = _request(select_url, {
            "csrf_token": "forged", "selection": None})
        assert code == 403 and body["error"] == "editor token required"
        code, body = _request(select_url, {
            "csrf_token": token, "selection": None}, headers={"Origin": "http://evil.example"})
        assert code == 403 and body["error"] == "local same-origin access required"

        # Unknown ids and malformed selections are 400s with English errors.
        for selection in ({"kind": "node", "collection": None, "id": "node:9999"},
                          {"kind": "semantic", "collection": "entities", "id": "missing"},
                          {"kind": "semantic", "collection": "routes", "id": "nope"},
                          {"kind": "edge", "collection": None, "id": "edge:9999"},
                          {"kind": "triangle", "collection": None, "id": "node:0001"},
                          {"kind": "semantic", "collection": "bogus", "id": "e1"},
                          {"kind": "node", "collection": "entities", "id": "node:0001"},
                          {"kind": "node", "collection": None, "id": ""},
                          "node:0001"):
            code, body = _request(select_url, {"csrf_token": token, "selection": selection})
            assert code == 400, selection
            assert body["error"]
        code, body = _request(select_url, {"csrf_token": token,
                                           "selection": {"kind": "node", "collection": None,
                                                         "id": "node:9999"}})
        assert body["error"] == "unknown web selection id: node:9999"

        # Rejections never touch the stored selection.
        assert json.load(urlopen(url + "/api/isaac/context"))["web_selection"] is None

        # Each unified kind round-trips only while its layer/tool is active.
        selections = (
            ("ground", "add-node", {"kind": "node", "collection": None,
                                      "id": "node:0001"}),
            ("ground", "add-edge", {"kind": "edge", "collection": None,
                                      "id": "edge:0001"}),
            ("semantic_points", "add-semantic-point",
             {"kind": "semantic_point", "collection": None, "id": "semantic:0001"}),
        )
        for layer, tool, selection in selections:
            _switch(url, layer, tool)
            code, body = _request(select_url, {"csrf_token": token, "selection": selection})
            assert code == 200, selection
            expected = dict(selection, collection=selection.get("collection"))
            assert body == {"selection": expected}
            stored = json.load(urlopen(url + "/api/isaac/context"))["web_selection"]
            assert stored == expected

        code, body = _request(select_url, {"csrf_token": token, "selection": {
            "kind": "semantic", "collection": "entities", "id": "e1"}})
        assert code == 400 and body["error"]

        # A null selection clears the highlight state without touching the
        # document revision: selections are ephemeral, edits are not.
        code, body = _request(select_url, {"csrf_token": token, "selection": None})
        assert code == 200 and body == {"selection": None}
        assert json.load(urlopen(url + "/api/isaac/context"))["web_selection"] is None
        assert json.load(urlopen(url + "/api/state"))["revision"] == revision
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_web_selection_dropped_when_edit_removes_its_target(tmp_path):
    project = MapProject.create_blank(tmp_path / "office", "office", [-1, -1, 1, 1], .5)
    server, thread, url = _serve(project)
    try:
        token = json.load(urlopen(url + "/api/isaac/context"))["csrf_token"]
        revision = _populate(url, token)
        _switch(url, "ground", "add-node")
        code, body = _request(url + "/api/web/selection", {
            "csrf_token": token,
            "selection": {"kind": "node", "collection": None, "id": "node:0001"}})
        assert code == 200
        assert json.load(urlopen(url + "/api/isaac/context"))["web_selection"]["id"] == "node:0001"

        # Deleting the selected node must clear the stored selection.
        code, body = _request(url + "/api/edit", {
            "csrf_token": token, "revision": revision,
            "operation": "remove_node", "id": "node:0001"})
        assert code == 200, body
        assert json.load(urlopen(url + "/api/isaac/context"))["web_selection"] is None
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_open_resets_selection_rotates_token_and_switches_snapshot(tmp_path):
    project = MapProject.create_blank(tmp_path / "office", "office", [-1, -1, 1, 1], .5)
    MapProject.create_blank(tmp_path / "other", "warehouse", [-1, -1, 1, 1], .5)
    server, thread, url = _serve(project, tmp_path)
    try:
        token = json.load(urlopen(url + "/api/isaac/context"))["csrf_token"]
        revision = _populate(url, token)
        generation = json.load(urlopen(url + "/api/isaac/context"))["scene_generation"]
        code, body = _request(url + "/api/web/selection", {
            "csrf_token": token,
            "selection": {"kind": "semantic_point", "collection": None,
                          "id": "semantic:0001"}})
        assert code == 200

        code, body = _request(url + "/api/open", {"csrf_token": token, "revision": revision,
                                                  "id": "other", "discard_unsaved": True})
        assert code == 200, body

        context = json.load(urlopen(url + "/api/isaac/context"))
        assert context["web_selection"] is None
        assert context["scene_key"] == "warehouse"
        assert context["scene_generation"] == generation + 1
        assert context["document_snapshot"]["nodes"] == []
        assert context["document_snapshot"]["semantics"]["routes"] == []
        assert context["csrf_token"] != token

        # The rotated token invalidates the previous browser page entirely.
        code, body = _request(url + "/api/web/selection", {
            "csrf_token": token,
            "selection": {"kind": "semantic_point", "collection": None,
                          "id": "semantic:0001"}})
        assert code == 403 and body["error"] == "editor token required"
    finally:
        server.shutdown(); server.server_close(); thread.join()


# -------------------------------------------------------------------- planner


def _planner_document():
    document = TopologyDocument("office")
    document.set_levels([GROUND])
    document.add_node([0.0, 0.0, 0.0], level_id="ground", label="Start",
                      heading_rad=math.pi / 4)
    document.add_node([1.0, 0.0, 0.0], level_id="ground")
    document.add_edge("node:0001", "node:0002", [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
                      directed=False)
    document.upsert_semantic("regions", {
        "id": "r1", "kind": "room", "label": "Bay", "aliases": [],
        "properties": {}, "polygon_xyz": [[0.0, 0.0, 0.0], [0.5, 0.0, 0.0], [0.5, 0.5, 0.0]],
        "height_m": 2.5, "level_id": "ground", "entrance_node_ids": []})
    document.upsert_semantic("entities", {
        "id": "e1", "kind": "furniture", "label": "Desk", "aliases": [],
        "properties": {}, "position_xyz": [0.25, 0.25, 0.5],
        "region_id": "r1", "source_ref": None})
    document.upsert_semantic("interaction_poses", {
        "id": "p1", "kind": "approach", "label": "Front", "aliases": [],
        "properties": {}, "position_xyz": [0.25, 0.25, 0.6],
        "heading_rad": math.pi / 2, "owner_id": "e1"})
    document.upsert_semantic("routes", {
        "id": "rt1", "kind": "walk", "label": "Loop", "aliases": [],
        "properties": {}, "node_ids": ["node:0001", "node:0002"],
        "edge_ids": ["edge:0001"]})
    return document


def test_planner_specs_nodes_edges_regions_entities_poses():
    specs = build_overlay_specs(compact_document_snapshot(_planner_document(), 4))
    assert specs["root"] == WEB_OVERLAY_ROOT == "/SemanticMapOverlay"
    prims = {prim["path"]: prim for prim in specs["prims"]}

    # Nodes: green spheres at the authored scene_world position, plus an
    # optional heading cone pointing along (cos h, sin h, 0).
    node = prims[f"{WEB_OVERLAY_ROOT}/Nodes/N00000"]
    assert node == {"path": f"{WEB_OVERLAY_ROOT}/Nodes/N00000", "category": "node",
                    "key": "node:0001", "shape": "sphere", "position": [0.0, 0.0, 0.0],
                    "radius": pytest.approx(.06), "color": pytest.approx([.08, .85, .18])}
    heading = prims[f"{WEB_OVERLAY_ROOT}/Headings/H00000"]
    assert heading["shape"] == "cone"
    assert heading["direction"] == pytest.approx(
        [math.cos(math.pi / 4), math.sin(math.pi / 4), 0.0])
    assert heading["length"] == pytest.approx(.7 * .18)
    assert heading["radius"] == pytest.approx(.32 * .18)

    # Undirected edge: one polyline plus two opposing arrow cones.
    edge_path = f"{WEB_OVERLAY_ROOT}/Edges/E00000"
    edge = prims[edge_path]
    assert edge["shape"] == "polyline" and edge["category"] == "edge"
    assert edge["points"] == [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]
    assert edge["width"] == pytest.approx(.025)
    assert edge["color"] == pytest.approx([.08, .85, .18])
    arrow_a, arrow_b = prims[edge_path + "/ArrowA"], prims[edge_path + "/ArrowB"]
    assert arrow_a["direction"] == pytest.approx([1.0, 0.0, 0.0])
    # Tip pulled back from the endpoint exactly like viewport._draw_arrow.
    assert arrow_a["position"] == pytest.approx([1.0 - min(.35 * .18, 1.0 * .25), 0.0, 0.0])
    assert arrow_b["direction"] == pytest.approx([-1.0, 0.0, 0.0])
    assert arrow_b["position"] == pytest.approx([min(.35 * .18, 1.0 * .25), 0.0, 0.0])
    assert arrow_a["length"] == pytest.approx(.7 * .18)
    assert arrow_a["radius"] == pytest.approx(.32 * .18)

    # Region: purple wireframe with a lifted floor, a top at height_m and one
    # side per vertex, all closed loops where the viewport draws them.
    region = f"{WEB_OVERLAY_ROOT}/Regions/R00000"
    floor = prims[region + "/Floor"]
    assert floor["color"] == pytest.approx([.65, .25, .95])
    assert floor["width"] == pytest.approx(.025)
    assert floor["points"] == [[0.0, 0.0, .035], [0.5, 0.0, .035], [0.5, 0.5, .035],
                               [0.0, 0.0, .035]]
    assert prims[region + "/Top"]["points"][0] == [0.0, 0.0, pytest.approx(2.5)]
    assert prims[region + "/Top"]["points"][-1] == prims[region + "/Top"]["points"][0]
    for vertex in range(3):
        side = prims[region + f"/Side{vertex}"]
        assert len(side["points"]) == 2
        assert side["points"][0][2] == pytest.approx(.035)
        assert side["points"][1][2] == pytest.approx(2.5)

    # Entities and poses: .07 m spheres in both versions, distinct accents.
    entity = prims[f"{WEB_OVERLAY_ROOT}/entities/P00000"]
    assert entity["category"] == "entity" and entity["key"] == "e1"
    assert entity["radius"] == pytest.approx(.07)
    assert entity["position"] == [0.25, 0.25, 0.5]
    assert entity["color"] == pytest.approx([.95, .80, .15])
    pose = prims[f"{WEB_OVERLAY_ROOT}/interaction_poses/P00000"]
    assert pose["category"] == "pose" and pose["key"] == "p1"
    assert pose["radius"] == pytest.approx(.07)
    assert pose["color"] == pytest.approx([.95, .2, .65])
    pose_heading = prims[f"{WEB_OVERLAY_ROOT}/SemanticHeadings/interaction_poses00000"]
    assert pose_heading["direction"] == pytest.approx([0.0, 1.0, 0.0])

    # Routes never draw their own prims; they only expand to members.
    assert not any("/Routes" in path for path in prims)
    assert specs["route_members"] == {"rt1": [
        {"category": "node", "id": "node:0001"},
        {"category": "node", "id": "node:0002"},
        {"category": "edge", "id": "edge:0001"}]}

    # Planning is deterministic for a given snapshot.
    assert build_overlay_specs(compact_document_snapshot(_planner_document(), 4)) == specs


def test_planner_specs_scale_for_version_one_documents():
    document = TopologyDocument("warehouse")
    document.add_node([0.0, 0.0, 0.0])
    document.add_node([1.0, 0.0, 0.0])
    document.add_edge("node:0001", "node:0002", [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    snapshot = compact_document_snapshot(document, 0)
    assert snapshot["version"] == 1

    specs = build_overlay_specs(snapshot)
    prims = {prim["path"]: prim for prim in specs["prims"]}
    assert prims[f"{WEB_OVERLAY_ROOT}/Nodes/N00000"]["radius"] == pytest.approx(.42)
    edge = prims[f"{WEB_OVERLAY_ROOT}/Edges/E00000"]
    assert edge["width"] == pytest.approx(.16)
    arrow = prims[f"{WEB_OVERLAY_ROOT}/Edges/E00000/ArrowA"]
    assert arrow["radius"] == pytest.approx(.32)
    assert arrow["length"] == pytest.approx(.7)
