from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tools.topology_authoring.isaac_bridge import (
    IsaacAuthoringClient,
    IsaacBridgeError,
    IsaacTopologyController,
    IsaacTopologyWindow,
)
from tools.topology_authoring.viewport import (
    _TopologyEditorGesture,
    nearest_projected_hit,
    ray_axis_intersection,
    ray_plane_intersection,
    viewport_ray,
    world_to_viewport_pixel,
)
from tools.topology_authoring.web_overlay import (
    build_overlay_specs,
    compact_document_snapshot,
)


def _context(*, revision=4, context_generation=2, edit_mode="3d",
             active_domain="aerial"):
    return {
        "revision": revision,
        "csrf_token": "token",
        "scene_key": "scene",
        "scene_generation": 1,
        "context_generation": context_generation,
        "active_domain": active_domain,
        "active_level": "ground",
        "editor_tool": "add-node",
        "levels": [{"id": "ground", "support_z_m": 0.0}],
        "web_selection": None,
        "document_snapshot": {
            "revision": revision,
            "version": 2,
            "nodes": [
                {"id": "n1", "position_xyz": [0.0, 0.0, 2.0],
                 "level_id": "ground", "edit_mode": edit_mode},
                {"id": "n2", "position_xyz": [2.0, 0.0, 2.0],
                 "level_id": "ground", "edit_mode": "3d"},
            ],
            "edges": [
                {"id": "e1", "from": "n1", "to": "n2",
                 "polyline_xyz": [[0.0, 0.0, 2.0], [2.0, 0.0, 2.0]],
                 "directed": False}
            ],
            "semantics": {"regions": [
                {"id": "r1", "level_id": "ground", "height_m": 2.5,
                 "polygon_xyz": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0],
                                 [1.0, 1.0, 0.0]]}
            ]},
        },
    }


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


def test_isaac_authoring_client_sends_complete_identity_and_frozen_override():
    calls = []

    def opener(request, timeout):
        calls.append((request.full_url, json.loads(request.data), timeout))
        return _Response({"ok": True})

    client = IsaacAuthoringClient("http://127.0.0.1:9999", opener=opener)
    client.update_context(_context())
    frozen = client.identity()
    client.update_context(_context(revision=8))
    client.edit("move_node_xy", id="n1", position_xy=[3.0, 4.0],
                _identity=frozen)

    body = calls[-1][1]
    assert calls[-1][0].endswith("/api/edit")
    assert body == {
        "revision": 4,
        "csrf_token": "token",
        "scene_key": "scene",
        "scene_generation": 1,
        "context_generation": 2,
        "domain": "aerial",
        "operation": "move_node_xy",
        "id": "n1",
        "position_xy": [3.0, 4.0],
    }


def test_ray_plane_axis_and_viewport_ray_are_deterministic():
    assert ray_plane_intersection((0, 0, 5), (0, 0, -1), 2) == (0.0, 0.0, 2.0)
    assert ray_plane_intersection((0, 0, 5), (1, 0, 0), 2) is None
    point = ray_axis_intersection((0, 2, 1), (0, -1, 0), (1, 0, 0), "x")
    assert point == pytest.approx((0.0, 0.0, 0.0))
    viewport = SimpleNamespace(ndc_to_world=lambda point: point)
    origin, direction = viewport_ray(viewport, (0.25, -0.5))
    assert origin == pytest.approx((0.25, -0.5, -1.0))
    assert direction == pytest.approx((0.0, 0.0, 1.0))


def test_viewport_boundary_converts_between_metric_data_and_stage_units():
    viewport = SimpleNamespace(
        world_to_ndc=lambda point: point,
        ndc_to_world=lambda point: point,
        map_ndc_to_texture_pixel=lambda point: (point, None),
    )
    assert world_to_viewport_pixel(
        viewport, (1.0, 2.0, 3.0), meters_per_unit=.01) == (100.0, 200.0)
    origin, direction = viewport_ray(
        viewport, (25.0, -50.0), meters_per_unit=.01)
    assert origin == pytest.approx((.25, -.5, -.01))
    assert direction == pytest.approx((0.0, 0.0, 1.0))

    snapshot = _context()["document_snapshot"]
    hit = nearest_projected_hit(
        snapshot, viewport, (200.0, 0.0), active_level="ground",
        domain="aerial", tolerance_px=.1, meters_per_unit=.01)
    assert hit == {"kind": "node", "id": "n2", "position_xyz": [2.0, 0.0, 2.0]}


class _Viewport:
    world_to_ndc = staticmethod(lambda point: point)
    ndc_to_world = staticmethod(lambda point: point)

    @staticmethod
    def map_ndc_to_texture_pixel(point):
        return (point[0], point[1]), None


def test_projected_hit_uses_stable_ids_and_includes_closing_region_edge():
    context = _context()
    viewport = _Viewport()
    node = nearest_projected_hit(
        context["document_snapshot"], viewport, (2.0, 0.0),
        active_level="ground", domain="aerial", tolerance_px=.1)
    assert node["kind"] == "node" and node["id"] == "n2"

    # This lies on the implicit closing edge r1[2] -> r1[0].
    boundary = nearest_projected_hit(
        context["document_snapshot"], viewport, (.5, .5),
        selection={"kind": "semantic", "collection": "regions", "id": "r1"},
        active_level="ground", domain="aerial", tolerance_px=.05)
    assert boundary["kind"] == "region-boundary"
    assert boundary["id"] == "r1" and boundary["index"] == 2

    gizmo = nearest_projected_hit(
        context["document_snapshot"], viewport, (.3, 0.0),
        selection={"kind": "node", "collection": None, "id": "n1"},
        active_level="ground", domain="aerial", tolerance_px=.05)
    assert gizmo["kind"] == "gizmo" and gizmo["axis"] == "x"


def test_region_boundary_candidate_uses_ray_plane_hit_not_screen_interpolation():
    class PerspectiveViewport:
        world_to_ndc = staticmethod(
            lambda point: (point[0] / 3.0, point[1] / 3.0, point[2]))
        ndc_to_world = staticmethod(
            lambda point: (point[0] * (point[2] + 4.0),
                           point[1] * (point[2] + 4.0), point[2]))

        @staticmethod
        def map_ndc_to_texture_pixel(point):
            return (point[0], point[1]), None

    snapshot = _context()["document_snapshot"]
    snapshot["semantics"]["regions"][0]["polygon_xyz"] = [
        [0.0, 0.0, 0.0], [3.0, 0.0, 0.0], [3.0, 3.0, 0.0]]
    hit = nearest_projected_hit(
        snapshot, PerspectiveViewport(), (.25, .25),
        selection={"kind": "semantic", "collection": "regions", "id": "r1"},
        active_level="ground", domain="aerial", tolerance_px=.01)
    assert hit["kind"] == "region-boundary" and hit["index"] == 2
    # Screen interpolation would yield [.75, .75, 0]; the click ray reaches
    # [1, 1, 0], which the server then projects onto the authoritative edge.
    assert hit["position_xyz"] == pytest.approx([1.0, 1.0, 0.0])


def test_projected_hit_excludes_edges_whose_endpoints_are_on_another_level():
    snapshot = _context()["document_snapshot"]
    snapshot["nodes"].extend([
        {"id": "u1", "position_xyz": [0.0, 5.0, 2.0], "level_id": "upstairs"},
        {"id": "u2", "position_xyz": [2.0, 5.0, 2.0], "level_id": "upstairs"},
    ])
    snapshot["edges"].append({
        "id": "hidden", "from": "u1", "to": "u2",
        "polyline_xyz": [[0.0, 5.0, 2.0], [2.0, 5.0, 2.0]],
        "directed": False,
    })
    hit = nearest_projected_hit(
        snapshot, _Viewport(), (1.0, 5.0), active_level="ground",
        domain="aerial", tolerance_px=.1)
    assert hit is None


def test_cross_level_edge_is_visible_and_pickable_from_either_endpoint_floor():
    snapshot = _context()["document_snapshot"]
    snapshot["nodes"] = [
        {"id": "ground", "position_xyz": [0.0, 4.0, 0.0],
         "level_id": "ground"},
        {"id": "upper", "position_xyz": [2.0, 4.0, 2.0],
         "level_id": "upper"},
        {"id": "other-a", "position_xyz": [0.0, 8.0, 2.0],
         "level_id": "other"},
        {"id": "other-b", "position_xyz": [2.0, 8.0, 2.0],
         "level_id": "other"},
    ]
    snapshot["edges"] = [
        {"id": "stairs", "from": "ground", "to": "upper",
         "polyline_xyz": [[0.0, 4.0, 0.0], [2.0, 4.0, 2.0]],
         "directed": False},
        {"id": "unrelated", "from": "other-a", "to": "other-b",
         "polyline_xyz": [[0.0, 8.0, 2.0], [2.0, 8.0, 2.0]],
         "directed": False},
    ]
    snapshot["semantics"] = {}

    for level in ("ground", "upper"):
        hit = nearest_projected_hit(
            snapshot, _Viewport(), (1.0, 4.0), active_level=level,
            domain="aerial", tolerance_px=.1)
        assert hit == {"kind": "edge", "id": "stairs"}
        specs = build_overlay_specs(snapshot, active_level=level)
        edge_keys = {row["key"] for row in specs["prims"]
                     if row["category"] == "edge"}
        assert edge_keys == {"stairs"}


def test_compact_snapshot_keeps_native_property_summary_fields_only():
    document = SimpleNamespace(
        version=2,
        nodes=[{"id": "n1", "position_xyz": [1, 2, 3],
                "level_id": "ground", "label": "Waypoint", "kind": "charger",
                "aliases": ["heavy"], "properties": {"payload": "omitted"}}],
        edges=[],
        semantics={"regions": [{
            "id": "r1", "label": "Lobby", "kind": "room",
            "polygon_xyz": [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
            "height_m": 3.0, "level_id": "ground",
            "aliases": ["heavy"], "properties": {"payload": "omitted"},
        }]},
    )
    snapshot = compact_document_snapshot(document, 7)
    assert snapshot["nodes"] == [{
        "id": "n1", "position_xyz": [1.0, 2.0, 3.0],
        "level_id": "ground", "label": "Waypoint", "kind": "charger",
    }]
    assert snapshot["semantics"]["regions"] == [{
        "id": "r1", "label": "Lobby", "kind": "room",
        "polygon_xyz": [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        "height_m": 3.0, "level_id": "ground",
    }]


class _Client:
    def __init__(self):
        self.context = None

    def update_context(self, context):
        self.context = context

    def identity(self):
        value = self.context
        return {
            **{key: value[key] for key in (
                "revision", "csrf_token", "scene_key", "scene_generation",
                "context_generation")},
            "domain": value["active_domain"],
        }


class _Dispatcher:
    def __init__(self):
        self.client = _Client()
        self.calls = []
        self.pending = False

    def submit(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return True


def test_controller_drag_modes_freeze_identity_and_commit_once():
    dispatcher = _Dispatcher()
    controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context(edit_mode="2.5d"))
    controller.begin_drag({"kind": "node", "id": "n1"}, [0, 0, 2])
    controller.update_drag([3, 4, 9])
    controller.context["revision"] = 99
    dispatcher.client.context["revision"] = 99
    assert controller.end_drag()
    args, kwargs = dispatcher.calls[-1]
    assert args[1:3] == ("edit", "move_node_xy")
    assert kwargs["position_xy"] == [3.0, 4.0]
    assert kwargs["_identity"]["revision"] == 4

    dispatcher = _Dispatcher()
    controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context(edit_mode="3d"))
    controller.begin_drag({"kind": "node", "id": "n1"}, [0, 0, 2], axis="z")
    controller.update_drag([0, 0, 7])
    assert controller.end_drag()
    args, kwargs = dispatcher.calls[-1]
    assert args[1:3] == ("edit", "move_node")
    assert kwargs["position_xyz"] == [0.0, 0.0, 7.0]
    assert len(dispatcher.calls) == 1


def test_controller_drag_uses_grab_delta_without_axis_or_vertex_jump():
    dispatcher = _Dispatcher()
    controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context(edit_mode="3d"))
    controller.begin_drag({"kind": "node", "id": "n1"}, [0, 0, 2.3], axis="z")
    controller.update_drag([0, 0, 2.4])
    assert controller.preview == {
        "kind": "node", "id": "n1", "position_xyz": [0.0, 0.0, 2.1]}

    controller.begin_drag(
        {"kind": "region-vertex", "id": "r1", "index": 1},
        [1.2, 0.3, 0.0],
    )
    controller.update_drag([1.4, 0.5, 8.0])
    assert controller.preview == {
        "kind": "region-vertex", "id": "r1", "index": 1,
        "position_xyz": [1.2, 0.2, 0.0],
    }


def test_edge_tool_treats_selected_node_gizmo_as_endpoint(monkeypatch):
    dispatcher = _Dispatcher()
    controller = IsaacTopologyController(dispatcher)
    context = _context(edit_mode="3d")
    context["editor_tool"] = "add-edge"
    controller.update_context(context)
    controller.select({"kind": "node", "collection": None, "id": "n1"})

    monkeypatch.setattr(
        "tools.topology_authoring.viewport.nearest_projected_hit",
        lambda *_args, **_kwargs: {
            "kind": "gizmo", "axis": "x", "id": "n1",
            "position_xyz": [0.0, 0.0, 2.0],
        },
    )
    gesture = object.__new__(_TopologyEditorGesture)
    gesture.controller = controller
    gesture.viewport_api = object()
    gesture._drag_target = None
    gesture._world_point = lambda *_args, **_kwargs: [0.0, 0.0, 2.0]

    gesture.handle("began", [0.0, 0.0])

    assert controller.edge_start == "n1"
    assert controller.gesture is None
    assert gesture._drag_target is None


def test_click_only_scene_cancel_preserves_click_state_but_cancels_drag():
    dispatcher = _Dispatcher()
    controller = IsaacTopologyController(dispatcher)
    context = _context()
    context["editor_tool"] = "add-edge"
    controller.update_context(context)
    controller.activate_node_for_edge("n1")
    gesture = object.__new__(_TopologyEditorGesture)
    gesture.controller = controller
    gesture._drag_target = {"kind": "node", "id": "n1"}

    gesture.cancel_from_scene()
    assert controller.edge_start == "n1"
    assert gesture._drag_target is None

    menu_events = []
    controller.on_boundary_menu = menu_events.append
    candidate = {
        "kind": "region-boundary", "id": "r1", "index": 2,
        "position_xyz": [.5, .5, 0.0],
        "identity": dispatcher.client.identity(),
    }
    controller.set_boundary_candidate(candidate)
    gesture.cancel_from_scene()
    assert controller.boundary_candidate == candidate
    assert menu_events == [candidate]

    controller.add_region_point([0.0, 0.0, 0.0])
    controller.add_region_point([1.0, 0.0, 0.0])
    controller.add_region_point([1.0, 1.0, 0.0])
    assert controller.finish_region()
    active_edit = controller._active_edit
    gesture.cancel_from_scene()
    assert controller._active_edit == active_edit

    controller.begin_drag({"kind": "node", "id": "n1"}, [0.0, 0.0, 2.0])
    gesture.cancel_from_scene()
    assert controller.gesture is None
    assert controller.edge_start is None
    assert controller.boundary_candidate is None and menu_events[-1] is None
    assert controller._active_edit is None


def test_controller_preserves_region_and_edge_drafts_after_conflict():
    dispatcher = _Dispatcher()
    controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context())
    controller.add_region_point([0, 0, 0])
    controller.add_region_point([1, 0, 0])
    controller.add_region_point([1, 1, 0])
    controller.activate_node_for_edge("n1")
    region_identity = dict(controller.region_identity)
    edge_identity = dict(controller.edge_identity)

    conflict = IsaacBridgeError("stale editor revision")
    conflict.status = 409
    controller.handle_result((("add-region", "r2"), None, conflict))
    assert len(controller.region_draft) == 3
    assert controller.region_identity == region_identity
    assert controller.edge_start == "n1"
    assert controller.edge_identity == edge_identity
    assert controller.gesture is None and controller.preview is None


def test_controller_confirms_edge_preview_and_clears_drafts_only_after_success():
    dispatcher = _Dispatcher()
    controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context())

    controller.activate_node_for_edge("n1")
    controller.activate_node_for_edge("n2")
    assert dispatcher.calls == []
    assert controller.edge_start == "n1" and controller.edge_end == "n2"
    assert controller.preview == {
        "kind": "edge", "from": "n1", "to": "n2", "directed": False,
        "traversal_mode": "walk",
        "points": [[0.0, 0.0, 2.0], [2.0, 0.0, 2.0]],
    }
    assert controller.finish_edge()
    assert controller.edge_start == "n1" and controller.edge_end == "n2"
    args, kwargs = dispatcher.calls[-1]
    assert args[1:3] == ("edit", "add_edge")
    assert kwargs["_identity"]["revision"] == 4
    controller.handle_result((("add-edge", "n1", "n2"), {"ok": True}, None))
    assert controller.edge_start is None and controller.edge_end is None
    assert controller.preview is None

    controller.add_region_point([0, 0, 0])
    controller.add_region_point([1, 0, 0])
    controller.add_region_point([1, 1, 0])
    assert controller.finish_region()
    assert len(controller.region_draft) == 3
    tag = dispatcher.calls[-1][0][0]
    controller.handle_result((tag, {"ok": True}, None))
    assert controller.region_draft == [] and controller.region_identity is None


def test_new_edge_traversal_mode_is_previewed_and_submitted():
    dispatcher = _Dispatcher()
    controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context(active_domain="ground"))
    assert controller.set_new_edge_traversal_mode("stairs")
    controller.activate_node_for_edge("n1")
    controller.activate_node_for_edge("n2")
    assert controller.preview["traversal_mode"] == "stairs"
    assert controller.finish_edge()
    assert dispatcher.calls[-1][1]["traversal_mode"] == "stairs"

    assert controller.set_new_edge_traversal_mode("ramp")
    assert controller.preview["traversal_mode"] == "ramp"
    with pytest.raises(IsaacBridgeError, match="traversal_mode"):
        controller.set_new_edge_traversal_mode("teleport")

    aerial_dispatcher = _Dispatcher()
    aerial = IsaacTopologyController(aerial_dispatcher)
    aerial.update_context(_context(active_domain="aerial"))
    assert not aerial.set_new_edge_traversal_mode("stairs")
    # Even stale external assignment cannot leak a ground-only mode into an
    # aerial edge request.
    aerial.new_edge_traversal_mode = "ramp"
    aerial.activate_node_for_edge("n1")
    aerial.activate_node_for_edge("n2")
    assert aerial.preview["traversal_mode"] == "walk"
    assert aerial.finish_edge()
    assert aerial_dispatcher.calls[-1][1]["traversal_mode"] == "walk"


def test_controller_native_property_edits_and_new_node_default_mode():
    dispatcher = _Dispatcher()
    controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context())

    controller.set_new_node_edit_mode("2.5d")
    assert controller.add_node([3, 4, 5])
    assert dispatcher.calls[-1][1]["edit_mode"] == "2.5d"

    controller.select({"kind": "node", "collection": None, "id": "n1"})
    assert controller.set_selected_node_properties(
        [6, 7, 8], label="  Camera waypoint  ", kind="")
    args, kwargs = dispatcher.calls[-1]
    assert args[1:3] == ("edit", "update_node")
    identity = kwargs.pop("_identity")
    assert identity["revision"] == 4
    assert kwargs == {
        "id": "n1", "position_xyz": [6.0, 7.0, 8.0],
        "label": "Camera waypoint", "kind": "custom", "level_id": "ground",
        "edit_mode": "3d",
    }

    assert controller.set_selected_node_properties(
        [6, 7, 1.5], label="Landing", kind="transition", level_id=None)
    assert dispatcher.calls[-1][1]["level_id"] is None
    assert dispatcher.calls[-1][1]["position_xyz"] == [6.0, 7.0, 1.5]
    assert not controller.set_selected_node_properties(
        [6, 7, 1.5], label="Landing", kind="transition", level_id="missing")

    controller.select({"kind": "semantic", "collection": "regions", "id": "r1"})
    assert controller.set_selected_region_properties(
        label="  Lobby ", kind="", height_m=3.2)
    args, kwargs = dispatcher.calls[-1]
    assert args[1:3] == ("edit", "update_region")
    assert kwargs.pop("_identity")["revision"] == 4
    assert kwargs == {
        "id": "r1", "label": "Lobby", "kind": "room", "height_m": 3.2,
    }


def test_edge_direction_preserves_existing_traversal_mode():
    dispatcher = _Dispatcher()
    controller = IsaacTopologyController(dispatcher)
    context = _context()
    context["document_snapshot"]["edges"][0]["traversal_mode"] = "stairs"
    controller.update_context(context)
    controller.select({"kind": "edge", "collection": None, "id": "e1"})
    controller.set_directed(True)
    args, kwargs = dispatcher.calls[-1]
    assert args[1:3] == ("edit", "update_edge")
    assert kwargs["directed"] is True and kwargs["traversal_mode"] == "stairs"


class _ValueModel:
    def __init__(self, value=""):
        self.value = value

    def set_value(self, value):
        self.value = value

    def get_value_as_float(self):
        return float(self.value)

    def get_value_as_string(self):
        return str(self.value)


def test_native_property_form_refreshes_clean_revision_and_preserves_dirty_revision():
    dispatcher = _Dispatcher()
    controller = IsaacTopologyController(dispatcher)
    selection = {"kind": "node", "collection": None, "id": "n1"}
    context = _context()
    context["web_selection"] = selection
    controller.update_context(context)
    window = IsaacTopologyWindow(controller)
    window._status = SimpleNamespace(text="")
    window._node_label_model = _ValueModel()
    window._node_kind_model = _ValueModel()
    window._node_level_model = _ValueModel()
    window._node_xyz_models = [_ValueModel(), _ValueModel(), _ValueModel()]

    window.refresh()
    assert [model.value for model in window._node_xyz_models] == [0.0, 0.0, 2.0]
    assert window._node_level_model.value == "ground"
    clean = _context(revision=5)
    clean["web_selection"] = selection
    clean["document_snapshot"]["nodes"][0]["position_xyz"] = [3.0, 0.0, 2.0]
    controller.update_context(clean)
    window.refresh()
    assert window._node_xyz_models[0].value == 3.0
    assert window._property_identity["revision"] == 5

    window._node_xyz_models[0].value = 9.0
    window._mark_property_dirty()
    dirty_identity = dict(window._property_identity)
    remote = _context(revision=6)
    remote["web_selection"] = selection
    remote["document_snapshot"]["nodes"][0]["position_xyz"] = [4.0, 0.0, 2.0]
    controller.update_context(remote)
    window.refresh()
    assert window._node_xyz_models[0].value == 9.0
    assert window._property_identity == dirty_identity
    window._apply_node_properties()
    assert dispatcher.calls[-1][1]["_identity"]["revision"] == 5

    committed = _context(revision=7)
    committed["web_selection"] = selection
    committed["document_snapshot"]["nodes"][0]["position_xyz"] = [9.0, 0.0, 2.0]
    controller.update_context(committed)
    controller.handle_result((("node-properties", "n1"), {"ok": True}, None))
    window.refresh()
    assert not window._property_dirty
    assert window._property_identity["revision"] == 7
    window._node_xyz_models[0].value = 10.0
    window._mark_property_dirty()
    window._apply_node_properties()
    assert dispatcher.calls[-1][1]["_identity"]["revision"] == 7


def test_controller_transport_failure_cancels_frozen_drafts():
    dispatcher = _Dispatcher()
    controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context())
    controller.add_region_point([0, 0, 0])
    controller.activate_node_for_edge("n1")
    controller.activate_node_for_edge("n2")

    controller.handle_result((("add-edge", "n1", "n2"), None,
                              IsaacBridgeError("connection refused")))
    assert controller.region_draft == [] and controller.region_identity is None
    assert controller.edge_start is None and controller.edge_end is None
    assert controller.edge_identity is None and controller.preview is None


def test_controller_conflict_keeps_drag_preview_and_explicit_retry_refreshes_identity():
    dispatcher = _Dispatcher()
    controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context())
    controller.begin_drag({"kind": "node", "id": "n1"}, [0, 0, 2], axis="z")
    controller.update_drag([0, 0, 6])
    assert controller.end_drag()

    conflict = IsaacBridgeError("stale editor revision")
    conflict.status = 409
    controller.handle_result((("move-node", "n1"), None, conflict))
    assert controller.preview == {
        "kind": "node", "id": "n1", "position_xyz": [0.0, 0.0, 6.0]}
    assert controller.retry_request is not None

    controller.update_context(_context(revision=9))
    assert controller.retry_conflict()
    args, kwargs = dispatcher.calls[-1]
    assert args[1:3] == ("edit", "move_node")
    assert kwargs["_identity"]["revision"] == 9
    controller.handle_result((("move-node", "n1"), {"ok": True}, None))
    assert controller.preview is None and controller.retry_request is None


def test_context_generation_change_invalidates_native_gesture_and_drafts():
    dispatcher = _Dispatcher()
    controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context())
    controller.add_region_point([0, 0, 0])
    controller.activate_node_for_edge("n1")
    controller.begin_drag({"kind": "node", "id": "n1"}, [0, 0, 2])
    controller.update_context(_context(context_generation=3))
    assert controller.region_draft == []
    assert controller.region_identity is None
    assert controller.edge_start is None and controller.edge_identity is None
    assert controller.gesture is None and controller.preview is None


def test_csrf_rotation_invalidates_native_gesture_and_drafts():
    dispatcher = _Dispatcher()
    controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context())
    controller.add_region_point([0, 0, 0])
    controller.activate_node_for_edge("n1")
    controller.begin_drag({"kind": "node", "id": "n1"}, [0, 0, 2])
    rotated = _context()
    rotated["csrf_token"] = "rotated-token"
    controller.update_context(rotated)
    assert controller.region_draft == [] and controller.region_identity is None
    assert controller.edge_start is None and controller.edge_identity is None
    assert controller.gesture is None and controller.preview is None


def test_boundary_candidate_menu_closes_on_escape_and_context_change():
    dispatcher = _Dispatcher()
    controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context())
    events = []
    controller.on_boundary_menu = events.append
    candidate = {
        "kind": "region-boundary", "id": "r1", "index": 2,
        "position_xyz": [.5, .5, 0.0],
        "identity": dispatcher.client.identity(),
    }

    controller.set_boundary_candidate(candidate)
    assert controller.boundary_candidate == candidate
    assert events[-1] == candidate
    controller.cancel_gesture(clear_region=True)
    assert controller.boundary_candidate is None and events[-1] is None

    controller.set_boundary_candidate(candidate)
    controller.update_context(_context(context_generation=3))
    assert controller.boundary_candidate is None and events[-1] is None


def test_boundary_menu_action_freezes_candidate_and_closes_after_submit():
    dispatcher = _Dispatcher()
    controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context())
    events = []
    controller.on_boundary_menu = events.append
    controller.set_boundary_candidate({
        "kind": "region-boundary", "id": "r1", "index": 2,
        "position_xyz": [.5, .5, 0.0],
        "identity": dispatcher.client.identity(),
    })

    assert controller.add_boundary_vertex()
    args, kwargs = dispatcher.calls[-1]
    assert args[1:3] == ("edit", "insert_region_vertex")
    assert kwargs["id"] == "r1" and kwargs["index"] == 2
    assert kwargs["position_xyz"] == [.5, .5, 0.0]
    assert kwargs["_identity"]["revision"] == 4
    assert controller.boundary_candidate is None and events[-1] is None


def test_local_selection_survives_stale_poll_until_it_is_flushed():
    dispatcher = _Dispatcher()
    controller = IsaacTopologyController(dispatcher)
    context = _context()
    controller.update_context(context)
    selection = {"kind": "node", "collection": None, "id": "n1"}
    controller.select(selection)

    controller.update_context(_context())
    assert controller.selection == selection
    assert controller.selection_dirty
    assert controller.flush_selection()
    args, kwargs = dispatcher.calls[-1]
    assert args == (("selection", selection), "select", selection)
    assert kwargs["_identity"]["revision"] == 4


def test_non_edit_actions_freeze_identity_before_dispatch():
    dispatcher = _Dispatcher()
    controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context())

    controller.set_tool("add-edge")
    controller.set_domain("ground")
    controller.set_level("ground")
    controller.undo()
    controller.redo()
    controller.save()
    assert len(dispatcher.calls) == 6
    for _args, kwargs in dispatcher.calls:
        assert kwargs["_identity"]["revision"] == 4

    # Later polling cannot mutate the already captured identity queued for a
    # worker thread.
    controller.update_context(_context(revision=9))
    assert all(kwargs["_identity"]["revision"] == 4
               for _args, kwargs in dispatcher.calls)
