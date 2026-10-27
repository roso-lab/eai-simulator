from __future__ import annotations

import json
from copy import deepcopy
import sys
import threading
import time
import types
from types import SimpleNamespace

import pytest

from tools.topology_authoring.isaac_bridge import (
    IsaacAuthoringClient, IsaacBridgeError, IsaacEditDispatcher,
    IsaacTopologyController,
    IsaacTopologyWindow,
)
from tools.topology_authoring.viewport import (
    TopologyEditorViewport, _TopologyEditorGesture, _TopologyEditorViewportScene,
    nearest_projected_hit,
)
from tools.topology_authoring.web_overlay import build_unified_overlay_specs


def _context(*, active_layer="aerial", revision=4, generation=2):
    ground = {"revision": revision, "version": 2,
              "nodes": [{"id": "g1", "position_xyz": [0., 2., 0.],
                         "level_id": "ground"}], "edges": [], "semantics": {}}
    aerial = {"revision": revision, "version": 2, "nodes": [
        {"id": "n1", "position_xyz": [0., 0., 2.], "level_id": "ground",
         "edit_mode": "3d", "label": "A", "kind": "waypoint"},
        {"id": "n2", "position_xyz": [2., 0., 2.], "level_id": "ground",
         "edit_mode": "2.5d", "label": "B", "kind": "waypoint"}],
        "edges": [{"id": "e1", "from": "n1", "to": "n2",
                   "polyline_xyz": [[0., 0., 2.], [1., .5, 2.], [2., 0., 2.]],
                   "directed": False, "traversal_mode": "walk"}],
        "semantics": {"regions": [{"id": "legacy", "polygon_xyz":
                                      [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
                                      "height_m": 2}]}}
    semantic = [
        {"id": "s1", "position_xyz": [4., 1., 1.5], "edit_mode": "3d",
         "label": "Desk", "kind": "furniture", "description": "target",
         "aliases": ["table"], "properties": {"color": "oak"}, "navigation_bindings": []},
        {"id": "s2", "position_xyz": [6., 1., 2.5], "edit_mode": "2.5d",
         "label": "Lamp", "kind": "fixture", "description": "",
         "aliases": [], "properties": {}}]
    domain = active_layer if active_layer in {"ground", "aerial"} else "aerial"
    return {"revision": revision, "csrf_token": "token", "scene_key": "scene",
            "scene_generation": 1, "context_generation": generation,
            "active_domain": domain, "active_layer": active_layer,
            "last_navigation_layer": "aerial", "editor_tool":
                "add-semantic-point" if active_layer == "semantic_points" else "add-node",
            "active_level": "ground", "reference_level": "ground",
            "levels": [{"id": "ground", "label": "Ground", "support_z_m": 0.}],
            "visible_layers": {"ground": True, "aerial": True, "semantic_points": True},
            "visible_types": {"nodes": True, "edges": True, "semantic_points": True},
            "new_point_settings": {"aerial": {"z": 1.5, "edit_mode": "3d"},
                                   "semantic_points": {"z": 2.5, "edit_mode": "2.5d"}},
            "document_snapshots": {"ground": ground, "aerial": aerial},
            "document_snapshot": ground if domain == "ground" else aerial,
            "semantic_points": semantic, "web_selection": None}


class _Response:
    def __init__(self, value): self.value = value
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self): return json.dumps(self.value).encode()


class _Client:
    def __init__(self): self.context = None
    def update_context(self, context): self.context = context
    def identity(self):
        c = self.context
        return {"revision": c["revision"], "csrf_token": c["csrf_token"],
                "scene_key": c["scene_key"], "scene_generation": c["scene_generation"],
                "context_generation": c["context_generation"],
                "domain": c["active_domain"], "active_layer": c["active_layer"]}


class _Dispatcher:
    def __init__(self): self.client, self.calls, self.pending = _Client(), [], False
    def submit(self, *args, **kwargs): self.calls.append((args, kwargs)); return True


class _Viewport:
    world_to_ndc = staticmethod(lambda point: point)
    ndc_to_world = staticmethod(lambda point: point)
    @staticmethod
    def map_ndc_to_texture_pixel(point): return point, None


def test_authoring_identity_includes_active_layer_and_navigation_bindings():
    calls = []
    client = IsaacAuthoringClient(opener=lambda request, timeout:
                                  calls.append(json.loads(request.data)) or _Response({"ok": True}))
    client.update_context(_context(active_layer="semantic_points"))
    bindings = [{"domain": "ground", "node_id": "g1"}]
    client.edit("set_semantic_point_navigation_bindings", id="s1", bindings=bindings)
    assert calls[-1]["active_layer"] == "semantic_points"
    assert calls[-1]["bindings"] == bindings


def test_native_navigation_binding_without_isaac_selection():
    dispatcher = _Dispatcher(); controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context(active_layer="semantic_points"))
    controller.select({"kind": "semantic_point", "collection": None, "id": "s1"})
    assert controller.begin_navigation_binding()
    assert controller.toggle_navigation_binding("ground", "g1")
    assert controller.toggle_navigation_binding("aerial", "n2")
    assert not controller.toggle_navigation_binding("aerial", "missing")
    assert controller.commit_navigation_bindings()
    args, kwargs = dispatcher.calls[-1]
    assert args[2] == "set_semantic_point_navigation_bindings"
    assert kwargs["bindings"] == [
        {"domain": "aerial", "node_id": "n2"}, {"domain": "ground", "node_id": "g1"}]
    assert controller.toggle_navigation_binding("aerial", "n2")
    assert controller.toggle_navigation_binding("ground", "g1")
    assert controller.commit_navigation_bindings()
    assert dispatcher.calls[-1][1]["bindings"] == []


def test_navigation_binding_can_finish_map_pick_and_apply_draft_later():
    dispatcher = _Dispatcher(); controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context(active_layer="semantic_points"))
    controller.select({"kind": "semantic_point", "collection": None, "id": "s1"})
    assert controller.begin_navigation_binding()
    assert controller.toggle_navigation_binding("ground", "g1")
    assert controller.toggle_navigation_binding("aerial", "n2")
    assert controller.finish_navigation_binding()
    assert not controller.navigation_binding_mode
    assert controller.navigation_binding_draft == [
        {"domain": "aerial", "node_id": "n2"},
        {"domain": "ground", "node_id": "g1"}]
    assert controller.commit_navigation_bindings()
    assert dispatcher.calls[-1][0][2] == "set_semantic_point_navigation_bindings"
    assert dispatcher.calls[-1][1]["bindings"] == controller.navigation_binding_draft


def test_changing_selection_discards_navigation_binding_draft():
    dispatcher = _Dispatcher(); controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context(active_layer="semantic_points"))
    controller.select({"kind": "semantic_point", "collection": None, "id": "s1"})
    assert controller.begin_navigation_binding()
    assert controller.toggle_navigation_binding("ground", "g1")
    controller.select({"kind": "semantic_point", "collection": None, "id": "s2"})
    assert not controller.navigation_binding_mode
    assert controller.navigation_binding_draft is None
    assert dispatcher.calls[-1][1]["binding_target_mode"] is None


def test_native_binding_pick_searches_ground_and_aerial_snapshots(monkeypatch):
    import tools.topology_authoring.viewport as viewport_module

    controller = SimpleNamespace(
        navigation_binding_mode=True,
        context={"visible_layers": {"ground": True, "aerial": True},
                 "active_level": "ground"},
        document_snapshots={"ground": {"domain": "ground"},
                            "aerial": {"domain": "aerial"}},
        meters_per_unit=1.0,
    )
    monkeypatch.setattr(viewport_module, "nearest_projected_hit",
        lambda snapshot, *_args, **_kwargs: {
            "kind": "node", "id": snapshot["domain"],
            "position_xyz": [1.0, 0.0, 0.0] if snapshot["domain"] == "ground" else [0.0, 0.0, 0.0]})
    monkeypatch.setattr(viewport_module, "world_to_viewport_pixel",
                        lambda _api, point, **_kwargs: [point[0], point[1]])
    gesture = object.__new__(_TopologyEditorGesture)
    gesture.controller = controller
    gesture.viewport_api = _Viewport()
    hit = gesture._pick((0.0, 0.0))
    assert hit["domain"] == "aerial"
    assert hit["id"] == "aerial"


def test_context_controls_send_contract_fields_and_independent_defaults():
    dispatcher = _Dispatcher(); controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context())
    controller.set_active_layer("semantic_points"); controller.set_tool("add-semantic-point")
    controller.set_reference_level(None); controller.set_layer_visible("ground", False)
    controller.set_type_visible("edges", False)
    controller.set_new_point_z("semantic_points", 4.25)
    controller.set_new_point_edit_mode("aerial", "2.5d")
    sent = [kwargs for _args, kwargs in dispatcher.calls]
    assert sent[0]["active_layer"] == "semantic_points"
    assert sent[1]["editor_tool"] == "add-semantic-point"
    assert "reference_level" in sent[2] and sent[2]["reference_level"] is None
    assert sent[3]["visible_layers"]["ground"] is False
    assert sent[4]["visible_types"]["edges"] is False
    assert sent[5]["new_point_settings"]["semantic_points"]["z"] == 4.25
    assert sent[6]["new_point_settings"]["aerial"]["edit_mode"] == "2.5d"


def test_aerial_road_tool_height_controls_and_commit():
    dispatcher = _Dispatcher(); controller = IsaacTopologyController(dispatcher)
    ground = _context(active_layer="ground")
    controller.update_context(ground)
    assert controller.set_tool("build-aerial-road")
    assert dispatcher.calls[-1][1]["editor_tool"] == "build-aerial-road"
    assert dispatcher.calls[-1][1]["active_layer"] == "aerial"

    aerial = _context(active_layer="aerial")
    aerial["editor_tool"] = "build-aerial-road"
    aerial_doc = {"domain": "aerial", "version": 2, "nodes": [], "edges": [], "semantics": {}}
    aerial["document_snapshots"]["aerial"] = aerial_doc
    aerial["document_snapshot"] = aerial_doc
    controller.update_context(aerial)
    assert controller.adjust_aerial_road_start_height(.1)
    assert controller.new_point_settings["aerial"]["z"] == 1.6
    assert dispatcher.calls[-1][1]["new_point_settings"]["aerial"]["z"] == 1.6
    assert controller.adjust_aerial_road_start_height(-.1)
    assert controller.new_point_settings["aerial"]["z"] == 1.5
    controller._road_end_z = 2.0
    controller.gesture = {"target": {"kind": "road"}, "original": [0., 0., 2.],
                          "identity": controller.dispatcher.client.identity()}
    assert not controller.adjust_aerial_road_start_height(.1)
    controller.update_road_drag([1., 1., 20.])
    assert controller.preview["position_xyz"] == [1., 1., 2.]
    assert controller.adjust_road_end_height(.1)
    assert controller.preview["position_xyz"] == [1., 1., 2.1]
    assert controller.new_point_settings["aerial"]["z"] == 1.5
    assert controller.end_drag()
    assert controller.road_draft["start_xyz"] == [0., 0., 2.]
    assert controller.road_draft["end_xyz"] == [1., 1., 2.1]
    assert controller.confirm_road(connect_intersections=False)
    assert dispatcher.calls[-1][0][2] == "build_aerial_road"
    assert dispatcher.calls[-1][1]["start_xyz"] == [0., 0., 2.]
    assert dispatcher.calls[-1][1]["end_xyz"] == [1., 1., 2.1]


def test_ground_height_context_creation_properties_and_planar_drag():
    dispatcher = _Dispatcher(); controller = IsaacTopologyController(dispatcher)
    context = _context(active_layer="ground")
    context["levels"][0]["support_z_m"] = 3
    context["document_snapshots"]["ground"]["nodes"][0]["position_xyz"][2] = 4.5
    controller.update_context(context)
    assert controller.active_z() == 3
    assert controller.set_new_point_z("ground", 4.5)
    assert dispatcher.calls[-1][1]["new_point_settings"] == {"ground": {"z": 4.5}}
    assert controller.active_z() == 4.5
    assert controller.new_point_settings["aerial"] == {"z": 1.5, "edit_mode": "3d"}
    assert controller.new_point_settings["semantic_points"] == {"z": 2.5, "edit_mode": "2.5d"}
    with pytest.raises(IsaacBridgeError):
        controller.set_new_point_edit_mode("ground", "3d")
    assert controller.add_node([1, 2, controller.active_z()])
    assert dispatcher.calls[-1][1]["position_xyz"] == [1, 2, 4.5]
    assert dispatcher.calls[-1][1]["level_id"] == "ground"
    assert "edit_mode" not in dispatcher.calls[-1][1]
    controller.select({"kind": "node", "id": "g1"})
    assert controller.set_selected_node_properties([0, 2, 6], level_id="ground")
    assert dispatcher.calls[-1][1]["position_xyz"] == [0, 2, 6]
    controller.begin_drag({"kind": "node", "id": "g1"}, [0, 2, 4.5])
    controller.update_drag([1, 3, 99])
    assert controller.preview["position_xyz"] == [1, 3, 4.5]
    assert controller.end_drag()
    assert dispatcher.calls[-1][0][2] == "move_node_xy"
    assert dispatcher.calls[-1][1]["position_xy"] == [1, 3]
    context = deepcopy(context)
    context["levels"].append({"id": "upper", "support_z_m": 8})
    context["active_level"] = "upper"
    controller.update_context(context)
    assert controller.active_z() == 8
    fresh = _context(active_layer="ground", generation=3)
    fresh["new_point_settings"]["ground"] = {"z": 2.25}
    controller.update_context(fresh)
    assert controller.active_z() == 2.25


def test_ground_viewport_creation_plane_uses_default_or_custom_height(monkeypatch):
    from tools.topology_authoring import viewport
    controller = IsaacTopologyController(_Dispatcher())
    context = _context(active_layer="ground")
    context["levels"][0]["support_z_m"] = 3
    controller.update_context(context)
    scene = SimpleNamespace(viewport_api=_Viewport(), controller=controller)
    monkeypatch.setattr(viewport, "viewport_ray", lambda *_args, **_kwargs: ((1, 2, 10), (0, 0, -1)))
    assert _TopologyEditorGesture._world_point(scene, (0, 0)) == pytest.approx((1, 2, 3))
    controller.set_new_point_z("ground", 4.5)
    assert _TopologyEditorGesture._world_point(scene, (0, 0)) == pytest.approx((1, 2, 4.5))
    # Existing nodes drag in their own plane, regardless of the new-node Z.
    assert _TopologyEditorGesture._world_point(
        scene, (0, 0), {"position_xyz": [1, 2, 6]}) == pytest.approx((1, 2, 6))


def test_ground_overlay_keeps_visual_z_and_filters_by_level():
    context = _context(active_layer="ground")
    snapshot = context["document_snapshots"]["ground"]
    snapshot["nodes"][0]["position_xyz"][2] = 1.5
    snapshot["nodes"].extend([
        {"id": "g2", "position_xyz": [1, 2, 3], "level_id": "ground"},
        {"id": "other", "position_xyz": [0, 0, 1.5], "level_id": "upper"}])
    snapshot["edges"] = [{"id": "stairs", "from": "g1", "to": "g2",
        "polyline_xyz": [[0, 2, 1.5], [1, 2, 3]], "directed": True}]
    specs = build_unified_overlay_specs(context["document_snapshots"], [], active_layer="ground",
        active_level="ground", visible_layers={"ground": True, "aerial": False, "semantic_points": False})
    nodes = {row["key"]: row["position"] for row in specs["prims"] if row.get("shape") == "sphere"}
    assert nodes == {"g1": [0, 2, 1.5], "g2": [1, 2, 3]}
    edge = next(row for row in specs["prims"] if row.get("shape") == "polyline")
    assert edge["points"] == [[0, 2, 1.5], [1, 2, 3]]


def test_result_state_with_web_document_keys_refreshes_native_data_immediately():
    dispatcher = _Dispatcher(); controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context(active_layer="aerial"))
    state = _context(active_layer="aerial", generation=3)
    state["documents"] = state.pop("document_snapshots")
    state["document"] = state.pop("document_snapshot")
    state["web_selection"] = {"kind": "node", "collection": None, "id": "n2"}
    state["documents"]["aerial"]["nodes"].append(
        {"id": "n3", "position_xyz": [9., 9., 3.], "edit_mode": "3d",
         "label": "new", "kind": "waypoint"})
    controller.handle_result(("edit", {"state": state}, None))
    assert controller.node("n3")["label"] == "new"
    assert controller.selection == state["web_selection"]
    assert controller.point_for_selection()["id"] == "n2"
    assert controller.set_selected_node_properties(
        [2., 3., 4.], label="immediate", kind="waypoint")

    semantic_state = _context(active_layer="semantic_points", generation=4)
    semantic_state["documents"] = semantic_state.pop("document_snapshots")
    semantic_state["document"] = semantic_state.pop("document_snapshot")
    semantic_state["web_selection"] = {
        "kind": "semantic_point", "collection": None, "id": "s1"}
    controller.handle_result(("edit", {"state": semantic_state}, None))
    assert controller.semantic_point("s1")["label"] == "Desk"
    assert controller.point_for_selection()["id"] == "s1"
    assert controller.set_selected_semantic_point_properties(
        [4., 5., 6.], label="immediate", kind="furniture", properties={})


def test_dispatcher_keeps_completed_result_pending_until_drain():
    started = threading.Event(); release = threading.Event()

    class Client:
        def edit(self, operation, **_kwargs):
            started.set()
            release.wait(1.0)
            return {"operation": operation}

    dispatcher = IsaacEditDispatcher(Client())
    try:
        assert dispatcher.submit("first", "edit", "update_node")
        assert started.wait(1.0)
        assert dispatcher.pending
        release.set()
        deadline = time.time() + 1.0
        while not dispatcher._future.done() and time.time() < deadline:
            time.sleep(0.005)
        assert dispatcher._future.done()
        assert dispatcher.pending
        assert not dispatcher.submit("second", "edit", "update_node")
        assert dispatcher.drain()[0] == "first"
        assert not dispatcher.pending
        assert dispatcher.submit("second", "edit", "update_node")
    finally:
        dispatcher.close()


def test_drag_is_queued_while_selection_sync_is_busy_and_submitted_once():
    started = threading.Event(); release = threading.Event()

    class Client:
        def __init__(self):
            self.context = None
            self.edits = []

        def update_context(self, context):
            self.context = context

        def identity(self):
            context = self.context
            return {"revision": context["revision"], "csrf_token": context["csrf_token"],
                    "scene_key": context["scene_key"],
                    "scene_generation": context["scene_generation"],
                    "context_generation": context["context_generation"],
                    "domain": context["active_domain"],
                    "active_layer": context["active_layer"]}

        def select(self, selection, **_kwargs):
            started.set()
            release.wait(1.0)
            return {"selection": selection}

        def edit(self, operation, **kwargs):
            self.edits.append((operation, kwargs))
            return {"operation": operation}

    client = Client()
    dispatcher = IsaacEditDispatcher(client)
    controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context())
    try:
        controller.select({"kind": "node", "collection": None, "id": "n1"})
        assert controller.flush_selection()
        assert started.wait(1.0)

        controller.begin_drag({"kind": "node", "id": "n1"}, [0., 0., 2.])
        controller.update_drag([1., 2., 9.])
        assert controller.end_drag()
        assert controller.pending_edit is not None
        assert client.edits == []

        release.set()
        deadline = time.time() + 1.0
        while dispatcher._future is not None and not dispatcher._future.done() and time.time() < deadline:
            time.sleep(.005)
        result = dispatcher.drain()
        controller.handle_result(result)
        assert controller.flush_pending_edit()
        deadline = time.time() + 1.0
        while dispatcher._future is not None and not dispatcher._future.done() and time.time() < deadline:
            time.sleep(.005)
        controller.handle_result(dispatcher.drain())
        assert client.edits == [("move_node", {
            "id": "n1", "position_xyz": [1., 2., 9.],
            "_identity": client.identity()})]
        assert controller.preview is None
        assert controller.pending_edit is None
        assert controller.edit_status == "synced"
    finally:
        dispatcher.close()


def test_failed_drag_rolls_back_preview_and_preserves_409_retry_draft():
    dispatcher = _Dispatcher(); controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context())
    controller.begin_drag({"kind": "node", "id": "n1"}, [0., 0., 2.])
    controller.update_drag([1., 2., 9.])
    assert controller.end_drag()
    tag = dispatcher.calls[-1][0][0]
    error = IsaacBridgeError("revision conflict")
    error.status = 409
    controller.handle_result((tag, None, error))
    assert controller.preview is None and controller.gesture is None
    assert controller.retry_request is not None
    assert controller.edit_status == "retry"

    controller.retry_request = None
    controller.begin_drag({"kind": "node", "id": "n1"}, [0., 0., 2.])
    controller.update_drag([1., 2., 9.])
    assert controller.end_drag()
    tag = dispatcher.calls[-1][0][0]
    controller.handle_result((tag, None, IsaacBridgeError("offline")))
    assert controller.preview is None and controller.gesture is None
    assert controller.retry_request is None
    assert controller.edit_status == "failed"


def test_successful_move_refreshes_authoritative_snapshot_and_clears_preview():
    dispatcher = _Dispatcher(); controller = IsaacTopologyController(dispatcher)
    context = _context(); controller.update_context(context)
    controller.begin_drag({"kind": "node", "id": "n1"}, [0., 0., 2.])
    controller.update_drag([1., 2., 8.])
    assert controller.end_drag()
    tag = dispatcher.calls[-1][0][0]
    fresh = _context(revision=5, generation=3)
    fresh["document_snapshots"]["aerial"]["nodes"][0]["position_xyz"] = [1., 2., 8.]
    fresh["document_snapshot"] = fresh["document_snapshots"]["aerial"]
    fresh["web_selection"] = {"kind": "node", "collection": None, "id": "n1"}
    controller.handle_result((tag, {"state": fresh}, None))
    assert controller.node("n1")["position_xyz"] == [1., 2., 8.]
    assert controller.preview is None and controller.gesture is None
    assert controller.edit_status == "synced"


def test_drag_result_does_not_replace_dirty_native_selection_with_stale_web_selection():
    dispatcher = _Dispatcher(); controller = IsaacTopologyController(dispatcher)
    context = _context(active_layer="aerial")
    controller.update_context(context)
    controller.select({"kind": "node", "collection": None, "id": "n1"})
    controller.begin_drag({"kind": "node", "id": "n1"}, [0., 0., 2.])
    controller.update_drag([1., 2., 8.])
    assert controller.end_drag()
    tag = dispatcher.calls[-1][0][0]
    fresh = _context(active_layer="aerial", revision=5, generation=3)
    fresh["document_snapshots"]["aerial"]["nodes"][0]["position_xyz"] = [1., 2., 8.]
    fresh["document_snapshot"] = fresh["document_snapshots"]["aerial"]
    fresh["web_selection"] = None
    controller.handle_result((tag, {"state": fresh}, None))
    assert controller.selection == {"kind": "node", "collection": None, "id": "n1"}
    assert controller.selection_dirty


def test_selection_failure_does_not_drop_drag_waiting_behind_single_flight_request():
    class BusyDispatcher(_Dispatcher):
        def submit(self, *args, **kwargs):
            if self.pending:
                return False
            return super().submit(*args, **kwargs)

    dispatcher = BusyDispatcher(); controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context())
    dispatcher.pending = True
    controller.begin_drag({"kind": "node", "id": "n1"}, [0., 0., 2.])
    controller.update_drag([1., 2., 9.])
    assert controller.end_drag()
    assert controller.pending_edit is not None
    controller.handle_result(("selection", None, IsaacBridgeError("selection unavailable")))
    assert controller.pending_edit is not None
    assert controller.preview is not None
    dispatcher.pending = False
    assert controller.flush_pending_edit()
    assert dispatcher.calls[-1][0][2] == "move_node"


def test_native_status_bar_exposes_preview_submit_sync_failure_and_retry_states():
    dispatcher = _Dispatcher(); controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context())
    window = IsaacTopologyWindow(controller)
    window._status = SimpleNamespace(text="")

    controller.begin_drag({"kind": "node", "id": "n1"}, [0., 0., 2.])
    controller.update_drag([1., 0., 2.])
    window.refresh()
    assert "预览中" in window._status.text
    controller.end_drag(); window.refresh()
    assert "提交中" in window._status.text
    controller.handle_result((dispatcher.calls[-1][0][0], {}, None)); window.refresh()
    assert "已同步到网页" in window._status.text

    controller.retry_request = {"tag": ("move-node", "n1"), "operation": "move_node",
                                "kwargs": {"id": "n1", "position_xyz": [1., 0., 2.]}}
    controller.edit_status = "retry"; window.refresh()
    assert "待重试草稿" in window._status.text


def test_aerial_node_edits_omit_legacy_level_metadata():
    dispatcher = _Dispatcher(); controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context(active_layer="aerial"))
    assert controller.add_node([8., 9., 10.])
    args, kwargs = dispatcher.calls[-1]
    assert args[2] == "add_node"
    assert "level_id" not in kwargs
    controller.select({"kind": "node", "collection": None, "id": "n1"})
    assert controller.set_selected_node_properties(
        [1., 2., 3.], label="updated", kind="waypoint", level_id=None)
    args, kwargs = dispatcher.calls[-1]
    assert args[2] == "update_node"
    assert "level_id" not in kwargs


def test_ground_mode_and_edge_endpoint_mutations_are_rejected():
    dispatcher = _Dispatcher(); controller = IsaacTopologyController(dispatcher)
    ground = _context(active_layer="ground")
    controller.update_context(ground)
    controller.select({"kind": "node", "collection": None, "id": "g1"})
    assert not controller.set_selected_edit_mode("3d")
    assert controller.set_level("ground")

    aerial = _context(active_layer="aerial")
    aerial["editor_tool"] = "add-edge"
    controller.update_context(aerial)
    controller.select({"kind": "node", "collection": None, "id": "n1"})
    call_count = len(dispatcher.calls)
    assert not controller.set_selected_edit_mode("2.5d")
    assert not controller.set_selected_z(4.)
    assert not controller.set_selected_node_properties(
        [1., 2., 3.], label="blocked", kind="waypoint")
    assert not controller.delete_selected()
    assert len(dispatcher.calls) == call_count

    assert not controller.set_level("ground")
    assert len(dispatcher.calls) == call_count


def test_semantic_point_add_properties_modes_and_delete_use_frozen_identity():
    dispatcher = _Dispatcher(); controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context(active_layer="semantic_points"))
    assert controller.add_semantic_point([3, 4, 2.5])
    assert dispatcher.calls[-1][0][2] == "add_semantic_point"
    assert dispatcher.calls[-1][1]["edit_mode"] == "2.5d"
    controller.select({"kind": "semantic_point", "collection": None, "id": "s1"})
    assert controller.set_selected_semantic_point_properties(
        [5, 6, 7], label=" Desk ", kind="furniture", description="work",
        aliases=[" table ", ""], properties={"a": 1})
    args, kwargs = dispatcher.calls[-1]
    assert args[2] == "update_semantic_point"
    assert kwargs["aliases"] == ["table"] and kwargs["properties"] == {"a": 1}
    assert controller.set_selected_edit_mode("2.5d")
    assert controller.delete_selected()
    assert dispatcher.calls[-1][0][2] == "remove_semantic_point"


def test_semantic_point_25d_and_3d_axis_drag_freeze_grab_delta():
    dispatcher = _Dispatcher(); controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context(active_layer="semantic_points"))
    controller.begin_drag({"kind": "semantic-point", "id": "s2"}, [6.2, 1.1, 2.5])
    controller.update_drag([7.2, 3.1, 9.])
    assert controller.preview["position_xyz"] == [7., 3., 2.5]
    assert controller.end_drag()
    assert dispatcher.calls[-1][0][2] == "move_semantic_point_xy"
    dispatcher = _Dispatcher(); controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context(active_layer="semantic_points"))
    controller.begin_drag({"kind": "semantic-point", "id": "s1"}, [4., 1., 1.8], axis="z")
    controller.update_drag([4., 1., 2.1])
    assert controller.preview["position_xyz"] == pytest.approx([4., 1., 1.8])
    assert controller.end_drag()
    assert dispatcher.calls[-1][0][2] == "move_semantic_point"


def test_drag_ending_without_motion_clears_gesture_without_editing_or_deselecting():
    dispatcher = _Dispatcher(); controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context())
    selection = {"kind": "node", "collection": None, "id": "n1"}
    controller.select(selection)
    controller.begin_drag({"kind": "node", "id": "n1"}, [0., 0., 2.])
    call_count = len(dispatcher.calls)
    assert controller.gesture is not None and controller.preview is None
    assert not controller.end_drag()
    assert controller.gesture is None and controller.preview is None
    assert controller.selection == selection
    assert len(dispatcher.calls) == call_count


def test_whole_edge_xy_drag_previews_polyline_and_associated_endpoints():
    dispatcher = _Dispatcher(); controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context())
    controller.begin_drag({"kind": "edge", "id": "e1"}, [.5, .25, 2.])
    controller.update_drag([1.5, 2.25, 9.])
    assert controller.preview["delta_xy"] == [1., 2.]
    assert controller.preview["points"] == [[1., 2., 2.], [2., 2.5, 2.], [3., 2., 2.]]
    assert controller.preview["associated_nodes"] == [
        {"id": "n1", "position_xyz": [1., 2., 2.]},
        {"id": "n2", "position_xyz": [3., 2., 2.]}]
    assert controller.end_drag()
    args, kwargs = dispatcher.calls[-1]
    assert args[2] == "translate_edge_xy" and kwargs["delta_xy"] == [1., 2.]


def test_hit_isolation_uses_active_layer_and_visible_type():
    context, viewport = _context(active_layer="semantic_points"), _Viewport()
    snapshot = context["document_snapshots"]["aerial"]
    assert nearest_projected_hit(snapshot, viewport, (0., 0.),
        active_layer="semantic_points", semantic_points=context["semantic_points"],
        tolerance_px=.1) is None
    hit = nearest_projected_hit(snapshot, viewport, (4., 1.),
        active_layer="semantic_points", semantic_points=context["semantic_points"],
        tolerance_px=.1)
    assert hit["kind"] == "semantic-point" and hit["id"] == "s1"
    assert nearest_projected_hit(snapshot, viewport, (4., 1.),
        active_layer="semantic_points", semantic_points=context["semantic_points"],
        visible_types={"semantic_points": False}, tolerance_px=.1) is None
    node = nearest_projected_hit(snapshot, viewport, (0., 0.), active_layer="aerial",
                                 semantic_points=context["semantic_points"], tolerance_px=.1)
    assert node["kind"] == "node" and node["id"] == "n1"


def test_hidden_active_layer_has_no_native_hit_candidates():
    context, viewport = _context(), _Viewport()
    snapshot = context["document_snapshots"]["aerial"]
    hidden_aerial = {"ground": True, "aerial": False, "semantic_points": True}
    assert nearest_projected_hit(
        snapshot, viewport, (0., 0.), active_layer="aerial",
        selection={"kind": "node", "collection": None, "id": "n1"},
        visible_layers=hidden_aerial, editor_tool="add-node", tolerance_px=.1) is None
    assert nearest_projected_hit(
        snapshot, viewport, (1., .5), active_layer="aerial",
        visible_layers=hidden_aerial, editor_tool="add-edge", tolerance_px=.1) is None
    hidden_semantic = {"ground": True, "aerial": True, "semantic_points": False}
    assert nearest_projected_hit(
        snapshot, viewport, (4., 1.), active_layer="semantic_points",
        semantic_points=context["semantic_points"], visible_layers=hidden_semantic,
        editor_tool="add-semantic-point", tolerance_px=.1) is None


def test_hit_candidates_are_isolated_by_editor_tool():
    context, viewport = _context(), _Viewport()
    snapshot = context["document_snapshots"]["aerial"]
    assert nearest_projected_hit(
        snapshot, viewport, (1., .5), active_layer="aerial",
        editor_tool="add-node", tolerance_px=.1) is None
    edge = nearest_projected_hit(
        snapshot, viewport, (1., .5), active_layer="aerial",
        editor_tool="add-edge", tolerance_px=.1)
    assert edge["kind"] == "edge" and edge["id"] == "e1"
    node = nearest_projected_hit(
        snapshot, viewport, (0., 0.), active_layer="aerial",
        editor_tool="add-edge", tolerance_px=.1)
    assert node["kind"] == "node" and node["id"] == "n1"
    semantic = nearest_projected_hit(
        snapshot, viewport, (4., 1.), active_layer="semantic_points",
        semantic_points=context["semantic_points"],
        editor_tool="add-semantic-point", tolerance_px=.1)
    assert semantic["kind"] == "semantic-point" and semantic["id"] == "s1"
    assert nearest_projected_hit(
        snapshot, viewport, (0., 0.), active_layer="semantic_points",
        semantic_points=context["semantic_points"],
        editor_tool="add-semantic-point", tolerance_px=.1) is None


def test_gesture_routes_semantic_click_without_region_paths(monkeypatch):
    dispatcher = _Dispatcher(); controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context(active_layer="semantic_points"))
    picker_kwargs = []
    def pick(*_args, **kwargs):
        picker_kwargs.append(kwargs)
        return {"kind": "semantic-point", "id": "s1",
                "position_xyz": [4., 1., 1.5]}
    monkeypatch.setattr("tools.topology_authoring.viewport.nearest_projected_hit",
                        pick)
    gesture = object.__new__(_TopologyEditorGesture)
    gesture.controller, gesture.viewport_api, gesture._drag_target = controller, object(), None
    gesture._world_point = lambda *_args, **_kwargs: [4., 1., 1.5]
    gesture.handle("began", [0., 0.])
    assert controller.selection == {"kind": "semantic_point", "collection": None, "id": "s1"}
    assert controller.gesture is None
    gesture.handle("click", [0., 0.])
    assert controller.gesture is None
    assert not dispatcher.calls
    assert picker_kwargs[0]["visible_layers"] == controller.context["visible_layers"]
    assert not hasattr(controller, "region_draft")


def test_topology_viewport_enablement_updates_retained_scenes():
    class Scene:
        def __init__(self): self.visible, self.destroyed = True, False
        def destroy(self): self.destroyed = True

    first, second = Scene(), Scene()
    viewport = object.__new__(TopologyEditorViewport)
    viewport.controller = object()
    viewport._registration = None
    viewport._scenes = [first, second]
    viewport.enabled = True
    viewport.set_enabled(False)
    assert not first.visible and not second.visible and not viewport.enabled
    viewport.destroy()
    assert first.destroyed and second.destroyed and viewport.controller is None


def test_topology_viewport_input_toggle_owns_native_selection_lifetime(monkeypatch):
    utility = types.ModuleType("omni.kit.viewport.utility")
    handles = []
    utility.disable_selection = lambda viewport, disable_click=True: (
        handles.append((viewport, disable_click)) or object())
    monkeypatch.setitem(sys.modules, "omni.kit.viewport.utility", utility)
    scene = object.__new__(_TopologyEditorViewportScene)
    scene.viewport_api = "viewport-1"
    scene._selection_disable_handle = None
    scene.set_input_enabled(True)
    assert handles == [("viewport-1", True)]
    handle = scene._selection_disable_handle
    scene.set_input_enabled(False)
    assert scene._selection_disable_handle is None
    scene.set_input_enabled(True)
    assert len(handles) == 2 and scene._selection_disable_handle is not handle


def test_topology_drag_uses_non_preemptible_gesture_manager(monkeypatch):
    scene = types.ModuleType("omni.ui.scene")

    class GestureManager:
        def can_be_prevented(self, _gesture):
            return True

    class DragGesture:
        def __init__(self, **kwargs):
            self.manager = kwargs.get("manager")
            self.mouse_button = kwargs.get("mouse_button")
            self.modifiers = kwargs.get("modifiers")
            self.name = kwargs.get("name")
            self.state = None

    scene.GestureManager = GestureManager
    scene.DragGesture = DragGesture
    scene.ClickGesture = DragGesture
    scene.GestureState = SimpleNamespace(CANCELED="canceled")
    ui = types.ModuleType("omni.ui")
    ui.scene = scene
    omni = types.ModuleType("omni")
    omni.__path__ = []
    omni.ui = ui
    monkeypatch.setitem(sys.modules, "omni", omni)
    monkeypatch.setitem(sys.modules, "omni.ui", ui)
    monkeypatch.setitem(sys.modules, "omni.ui.scene", scene)

    gesture = _TopologyEditorGesture(object(), SimpleNamespace())
    assert gesture.gesture.manager.can_be_prevented(None) is False
    assert gesture.gesture.mouse_button == 0
    assert gesture.gesture.modifiers == 0
    assert gesture.gesture.name == "eai.topology_authoring.drag"


def test_unified_overlay_has_three_layers_and_no_region_specs():
    context = _context()
    specs = build_unified_overlay_specs(context["document_snapshots"],
        context["semantic_points"], active_layer="aerial", active_level="ground",
        reference_level="ground", visible_layers=context["visible_layers"],
        visible_types=context["visible_types"])
    paths = [row["path"] for row in specs["prims"]]
    assert any("/Layers/Ground/Nodes/" in path for path in paths)
    assert any("/Layers/Aerial/Edges/" in path for path in paths)
    assert any("/Layers/SemanticPoints/" in path for path in paths)
    assert not any("Region" in path for path in paths)
    ground = next(row for row in specs["prims"] if "/Layers/Ground/Nodes/" in row["path"])
    aerial = next(row for row in specs["prims"] if "/Layers/Aerial/Nodes/" in row["path"])
    assert sum(ground["color"]) < sum(aerial["color"])


def test_unified_overlay_highlights_only_current_navigation_binding_draft():
    context = _context()
    specs = build_unified_overlay_specs(
        context["document_snapshots"], context["semantic_points"],
        active_layer="semantic_points", binding_mode=True,
        binding_target_id="s1",
        binding_target_draft=[{"domain": "ground", "node_id": "g1"},
                              {"domain": "aerial", "node_id": "n2"}])
    ground = next(row for row in specs["prims"]
                  if "/Layers/Ground/Nodes/" in row["path"])
    aerial_nodes = [row for row in specs["prims"]
                    if "/Layers/Aerial/Nodes/" in row["path"]]
    assert ground["color"] == pytest.approx([1.0, .76, .25])
    assert next(row for row in aerial_nodes if row["key"] == "n2")["color"] == pytest.approx([1.0, .76, .25])
    assert next(row for row in aerial_nodes if row["key"] == "n1")["color"] == pytest.approx([.025, .035, .045])


class _ValueModel:
    def __init__(self, value=""): self.value = value
    def set_value(self, value): self.value = value
    def get_value_as_float(self): return float(self.value)
    def get_value_as_string(self): return str(self.value)


def test_semantic_property_form_roundtrip_and_invalid_json():
    dispatcher = _Dispatcher(); controller = IsaacTopologyController(dispatcher)
    context = _context(active_layer="semantic_points")
    context["web_selection"] = {"kind": "semantic_point", "collection": None, "id": "s1"}
    controller.update_context(context)
    window = IsaacTopologyWindow(controller); window._status = SimpleNamespace(text="")
    window._semantic_label_model = _ValueModel(); window._semantic_kind_model = _ValueModel()
    window._semantic_description_model = _ValueModel(); window._semantic_aliases_model = _ValueModel()
    window._semantic_properties_model = _ValueModel(); window._semantic_xyz_models = [
        _ValueModel(), _ValueModel(), _ValueModel()]
    window.refresh()
    assert window._semantic_label_model.value == "Desk"
    assert window._semantic_aliases_model.value == "table"
    assert json.loads(window._semantic_properties_model.value) == {"color": "oak"}
    window._semantic_properties_model.value = "[]"; window._apply_semantic_properties()
    assert "Invalid properties JSON" in controller.last_error


def test_context_change_and_escape_cancel_only_unified_interaction_state():
    dispatcher = _Dispatcher(); controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context()); controller.activate_node_for_edge("n1")
    controller.begin_drag({"kind": "node", "id": "n1"}, [0., 0., 2.])
    controller.update_context(_context(generation=3))
    assert controller.edge_start is None and controller.gesture is None
    assert controller.preview is None


def test_validation_rejects_invalid_modes_layers_and_tools():
    controller = IsaacTopologyController(_Dispatcher()); controller.update_context(_context())
    with pytest.raises(IsaacBridgeError): controller.set_active_layer("regions")
    with pytest.raises(IsaacBridgeError): controller.set_tool("add-region")
    with pytest.raises(IsaacBridgeError): controller.set_new_point_edit_mode("aerial", "free")


def _pointer(controller, hits):
    gesture = object.__new__(_TopologyEditorGesture)
    gesture.controller, gesture.viewport_api = controller, _Viewport()
    gesture._press = gesture._drag_target = None
    gesture._moved = False
    gesture._pick = lambda point: hits.get(tuple(point))
    gesture._world_point = lambda point, target=None: [*point, 2.]
    return gesture


def test_direct_click_switches_unselected_nodes_without_creating_or_moving():
    dispatcher = _Dispatcher(); controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context())
    hits = {(0., 0.): {"kind": "node", "id": "n1", "position_xyz": [0, 0, 2]},
            (20., 0.): {"kind": "node", "id": "n2", "position_xyz": [2, 0, 2]}}
    pointer = _pointer(controller, hits)
    for xy, expected in [((0., 0.), "n1"), ((20., 0.), "n2")]:
        pointer.handle("began", xy)
        pointer.handle("changed", (xy[0]+1, xy[1]))
        pointer.cancel_from_scene()  # Kit cancels its drag on a click release.
        pointer.handle("click", xy)
        assert controller.selection["id"] == expected
        assert controller.gesture is None and controller.preview is None
    assert not dispatcher.calls


def test_direct_drag_keeps_grab_offset_and_commits_once_on_release():
    dispatcher = _Dispatcher(); controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context())
    pointer = _pointer(controller, {(20., 0.): {
        "kind": "node", "id": "n2", "position_xyz": [2., 0., 2.]}})
    pointer.handle("began", (20., 0.))
    pointer.handle("changed", (23., 0.))
    assert controller.gesture is None and not dispatcher.calls
    pointer.handle("changed", (30., 10.))
    assert controller.preview["position_xyz"] == [12., 10., 2.]
    assert not dispatcher.calls
    pointer.handle("ended", (30., 10.))
    pointer.handle("click", (30., 10.))
    assert len(dispatcher.calls) == 1
    args, kwargs = dispatcher.calls[0]
    assert args[2] == "move_node_xy" and kwargs["position_xy"] == [12., 10.]


def test_canceled_kit_drag_commits_last_sampled_position_once():
    dispatcher = _Dispatcher(); controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context())
    pointer = _pointer(controller, {(20., 0.): {
        "kind": "node", "id": "n2", "position_xyz": [2., 0., 2.]}})
    pointer.handle("began", (20., 0.))
    pointer.handle("changed", (30., 10.))
    assert controller.preview is not None
    pointer.cancel_from_scene()
    assert len(dispatcher.calls) == 1
    assert dispatcher.calls[0][0][2] == "move_node_xy"
    assert dispatcher.calls[0][1]["position_xy"] == [12., 10.]


def test_empty_drag_never_creates_and_endpoint_clicks_wait_for_confirmation():
    dispatcher = _Dispatcher(); controller = IsaacTopologyController(dispatcher)
    context = _context(); controller.update_context(context)
    pointer = _pointer(controller, {})
    pointer.handle("began", (0., 0.))
    pointer.handle("changed", (30., 0.))
    pointer.handle("ended", (30., 0.))
    pointer.handle("click", (30., 0.))
    assert not dispatcher.calls
    context["editor_tool"] = "add-edge"
    controller.update_context(context)
    pointer = _pointer(controller, {
        (0., 0.): {"kind": "node", "id": "n1", "position_xyz": [0, 0, 2]},
        (20., 0.): {"kind": "node", "id": "n2", "position_xyz": [2, 0, 2]}})
    for point in ((0., 0.), (20., 0.)):
        pointer.handle("began", point)
        pointer.handle("click", point)
    assert controller.preview["kind"] == "edge-draft"
    assert (controller.edge_start, controller.edge_end) == ("n1", "n2")
    assert not dispatcher.calls
    assert controller.finish_edge()
    assert len(dispatcher.calls) == 1 and dispatcher.calls[0][0][2] == "add_edge"


def test_escape_and_tool_switch_invalidate_held_pointer():
    dispatcher = _Dispatcher(); controller = IsaacTopologyController(dispatcher)
    controller.update_context(_context())
    pointer = _pointer(controller, {(0., 0.): {
        "kind": "node", "id": "n1", "position_xyz": [0., 0., 2.]}})
    pointer.handle("began", (0., 0.))
    controller.cancel_gesture()
    pointer.handle("changed", (30., 0.))
    pointer.handle("ended", (30., 0.))
    assert not dispatcher.calls and controller.gesture is None
    pointer.handle("began", (0., 0.))
    controller.set_tool("add-node")
    count = len(dispatcher.calls)
    pointer.handle("changed", (30., 0.))
    pointer.handle("ended", (30., 0.))
    assert len(dispatcher.calls) == count and controller.gesture is None
