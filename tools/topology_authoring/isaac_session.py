"""Main-thread stage switching for the integrated map editor (Kit imports lazy)."""
from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
import time

from .isaac_bridge import (
    IsaacAuthoringClient,
    IsaacEditDispatcher,
    IsaacSelectionBridge,
    IsaacTopologyController,
    IsaacTopologyWindow,
    _stage_axis_and_scale,
    assert_source_matches,
)
from .viewport import TopologyEditorViewport
from .web_overlay import WebOverlayRenderer, build_unified_overlay_specs


def _disk_version(layer):
    try:
        stat = Path(layer.realPath).stat()
        return stat.st_mtime_ns, stat.st_size
    except (OSError, AttributeError, ValueError):
        return None


def _web_selection_token(selection):
    """Comparable cursor identity for a web selection payload."""
    if not selection:
        return None
    return (selection.get("kind"), selection.get("collection"), selection.get("id"))


class IsaacMapSession:
    """Serialize stage opens and topology edits to the latest map request."""

    def __init__(self, context, server_url, read_context, report_status, *,
                 bridge_factory=IsaacSelectionBridge,
                 window_factory=None,
                 authoring_client_factory=IsaacAuthoringClient,
                 editor_window_factory=IsaacTopologyWindow,
                 editor_view_factory=TopologyEditorViewport, on_open=None):
        self.context = context
        self.server_url = server_url
        self.read_context = read_context
        self.report_status = report_status
        self.bridge_factory = bridge_factory
        self.on_open = on_open
        self.authoring_client_factory = authoring_client_factory
        self.editor_window_factory = editor_window_factory
        self.editor_view_factory = editor_view_factory
        self.bridge = self.task = None
        self.editor_client = self.editor_dispatcher = self.editor_controller = None
        self.editor_window = self.editor_view = None
        self.overlay = None
        self.overlay_revision = None
        self.overlay_context_key = None
        self.web_selection_key = None
        self.generation = None
        self.blocked_generation = None
        self._bridge_error_active = False
        self.next_poll = 0
        self.loaded_layer_text = {}

    def _stop_bridge(self):
        if self.editor_controller is not None:
            self.editor_controller.cancel_gesture(clear_region=True)
        if self.editor_view is not None:
            self.editor_view.destroy()
            self.editor_view = None
        if self.editor_window is not None:
            self.editor_window.destroy()
            self.editor_window = None
        if self.editor_dispatcher is not None:
            self.editor_dispatcher.close()
            self.editor_dispatcher = None
        self.editor_controller = None
        self.editor_client = None
        if self.bridge:
            self.bridge.stop()
        self.bridge = None
        self._bridge_error_active = False

    def _clear_native_selection(self) -> None:
        """Clear Kit's USD selection when topology editing owns the mouse."""
        try:
            selection = self.context.get_selection()
            clear = getattr(selection, "clear_selected_prim_paths", None)
            if callable(clear):
                clear()
                return
            set_paths = getattr(selection, "set_selected_prim_paths", None)
            if callable(set_paths):
                set_paths([], False)
        except (AttributeError, RuntimeError, TypeError):
            # Some headless/test contexts do not expose a selection object.
            pass

    def _dirty_stage(self):
        stage = self.context.get_stage()
        if stage is None:
            return False
        # SimulationApp creates its own dirty anonymous startup stage. It has
        # no user-authored file and is replaceable on this process's first open.
        if self.generation is None and stage.GetRootLayer().anonymous:
            return False
        # References and payloads can also be edit targets. Session-layer
        # camera/light edits are ephemeral, not authored USD.
        session = stage.GetSessionLayer()
        transient = set()
        def exclude(layer):
            if layer.identifier in transient:
                return
            transient.add(layer.identifier)
            if layer.subLayerPaths:
                from pxr import Sdf
                for path in layer.subLayerPaths:
                    child = Sdf.Layer.FindRelativeToLayer(layer, path)
                    if child:
                        exclude(child)
        exclude(session)
        dirty = [layer.identifier for layer in stage.GetUsedLayers()
                 if layer.dirty and layer.identifier not in transient
                 and (layer.identifier not in self.loaded_layer_text
                      or (layer.ExportToString(), _disk_version(layer)) != self.loaded_layer_text[layer.identifier])]
        if dirty and self.blocked_generation is None:
            print(f"Semantic map: unsaved USD layers: {dirty}", flush=True)
        return bool(dirty)

    def tick(self):
        now = time.monotonic()
        if now < self.next_poll:
            self._update_editor_overlay()
            return
        self.next_poll = now + .25
        request = self.read_context()
        generation = request["scene_generation"]
        if generation != self.generation:
            self._stop_bridge()
            # The overlay lives in the old stage's session layer; drop it
            # eagerly (guarding the failed-_open residue) and zero the
            # cursors so the new scene's first tick must rebuild.
            if self.overlay is not None:
                self.overlay.destroy()
            self.overlay = None
            self.overlay_revision = None
            self.overlay_context_key = None
            self.web_selection_key = None
            # Let the existing USD open finish before starting the next one.
            # Cancellation can leave Kit's native stage loader in flight.
            if self.task is not None and not self.task.done():
                return
            if self._dirty_stage():
                if self.blocked_generation != generation:
                    self.report_status(generation, "error",
                                       "Isaac USD has unsaved edits. Save or discard them in Isaac before switching.")
                    self.blocked_generation = generation
                return
            self.blocked_generation = None
            self.generation = generation
            self.report_status(generation, "loading")
            self.task = asyncio.ensure_future(self._open(request))
        elif self.bridge:
            update_bridge_context = getattr(self.bridge, "update_context", None)
            if callable(update_bridge_context):
                update_bridge_context(request)
            status = self.bridge.status()
            if status["state"] == "error":
                if not self._bridge_error_active and self.editor_controller is not None:
                    self.editor_controller.cancel_gesture(clear_region=True,
                                                           preserve_retry=True)
                self._bridge_error_active = True
                self.report_status(generation, "error", status.get("last_error"))
            elif status["state"] == "connected":
                if self._bridge_error_active and self.editor_controller is not None:
                    # Input started during an outage also belongs to the old
                    # connection and cannot be committed after reconnecting.
                    self.editor_controller.cancel_gesture(clear_region=True,
                                                           preserve_retry=True)
                self._bridge_error_active = False
                self.report_status(generation, "ready")
            if self.editor_controller is not None:
                self.editor_controller.update_context(request)
                result = self.editor_dispatcher.drain()
                self.editor_controller.handle_result(result)
                # A drag that ended while selection/context synchronization was
                # in flight is submitted immediately after that request drains.
                self.editor_controller.flush_pending_context()
                self.editor_controller.flush_pending_edit()
                self.editor_controller.flush_selection()
                # The request sampled at the start of this tick may predate the
                # edit response. Refresh once so the session overlay and web
                # inspector both consume the authoritative coordinates.
                if result is not None and result[0] and result[0][0] in {
                        "move-node", "move-semantic-point", "translate-edge"}:
                    request = self.read_context()
                    self.editor_controller.update_context(request)
            self._update_overlay(request)

    def _update_overlay(self, request):
        """Rebuild on revision, graph or floor changes; replay selections."""
        # A failed renderer creation leaves overlay None; never let that break
        # the 0.25 s heartbeat shared with the selection bridge.
        if self.overlay is None:
            return
        snapshots = request.get("document_snapshots") or {
            request.get("active_domain", "ground"): request.get("document_snapshot") or {}}
        revisions = tuple((name, (value or {}).get("revision"))
                          for name, value in sorted(snapshots.items()))
        revision = request.get("revision")
        if revision is None:
            revision = (request.get("document_snapshot") or {}).get("revision")
        overlay_key = (
            revisions, request.get("active_layer"), request.get("active_level"),
            request.get("reference_level"),
            json.dumps(request.get("visible_layers"), sort_keys=True),
            json.dumps(request.get("visible_types"), sort_keys=True),
            json.dumps(request.get("semantic_points"), sort_keys=True),
            request.get("binding_target_id"),
            request.get("binding_target_mode"),
            json.dumps(request.get("binding_target_draft"), sort_keys=True),
        )
        if overlay_key != self.overlay_context_key:
            try:
                self.overlay.rebuild(build_unified_overlay_specs(
                    snapshots, request.get("semantic_points") or [],
                    active_layer=request.get("active_layer", request.get("active_domain", "ground")),
                    active_level=request.get("active_level"),
                    reference_level=request.get("reference_level"),
                    visible_layers=request.get("visible_layers"),
                    visible_types=request.get("visible_types"),
                    binding_target_id=request.get("binding_target_id"),
                    binding_target_draft=request.get("binding_target_draft"),
                    binding_mode=request.get("binding_target_mode") == "semantic_navigation"))
            except Exception as exc:
                # Abandon this revision instead of retrying every 0.25 s; the
                # next committed edit (fresh revision) may recover.
                print(f"Semantic map overlay rebuild failed: {exc}", flush=True)
                self.overlay_revision = revision
                self.overlay_context_key = overlay_key
                return
            self.overlay_revision = revision
            self.overlay_context_key = overlay_key
            # Fresh prims carry base colors only; force highlight replay.
            self.web_selection_key = None
        token = _web_selection_token(request.get("web_selection"))
        if token != self.web_selection_key:
            try:
                self.overlay.highlight(
                    request.get("web_selection"),
                    active_layer=request.get("active_layer", request.get("active_domain")))
            except TypeError:
                # Compatibility for injected renderers implementing the old
                # one-argument protocol.
                self.overlay.highlight(request.get("web_selection"))
            self.web_selection_key = token
        self._update_editor_overlay()

    def _update_editor_overlay(self):
        # Local drag samples run at viewport frame rate. They must not wait for
        # the authority/selection heartbeat; unchanged samples are cached by
        # the renderer and perform no USD writes.
        if (self.overlay is not None and self.editor_controller is not None
                and hasattr(self.overlay, "editor_state")):
            self.overlay.editor_state(
                snapshot=self.editor_controller.snapshot,
                domain=(self.editor_controller.context or {}).get("active_domain", "ground"),
                selection=self.editor_controller.selection,
                preview=self.editor_controller.preview,
                semantic_points=getattr(self.editor_controller, "semantic_points", ()),
                selection_items=getattr(self.editor_controller, "selection_items", ()),
                box_preview=getattr(self.editor_controller, "box_preview", None),
                active_layer=getattr(
                    self.editor_controller, "active_layer",
                    (self.editor_controller.context or {}).get("active_domain", "ground")),
            )

    async def _open(self, request):
        generation = request["scene_generation"]
        try:
            result = await self.context.open_stage_async(request["source_usd"])
            success = result[0] if isinstance(result, tuple) else result
            if success is False:
                raise RuntimeError(f"Could not open root USD: {request['source_path']}")
            stage = self.context.get_stage()
            if stage is None:
                raise RuntimeError("Isaac returned no stage after opening the root USD")
            assert_source_matches(stage, request["source_usd"])
            _axis, meters_per_unit = _stage_axis_and_scale(stage)
            self.loaded_layer_text = {layer.identifier: (layer.ExportToString(), _disk_version(layer))
                                      for layer in stage.GetUsedLayers() if layer.dirty}
            if self.read_context()["scene_generation"] != generation:
                return
            if self.on_open:
                result = self.on_open(stage)
                if inspect.isawaitable(result):
                    await result
            # Kit creates render/physics settings during open. Capture that
            # owned initialization before exposing the ready bridge. Keep the
            # original edit target so actual user asset edits remain protected.
            self.loaded_layer_text = {layer.identifier: (layer.ExportToString(), _disk_version(layer))
                                      for layer in stage.GetUsedLayers() if layer.dirty}
            if self.read_context()["scene_generation"] != generation:
                return
            self.bridge = self.bridge_factory(self.server_url, scene_key=request["scene_key"])
            # Establish the expected identity before the first topology snapshot.
            self.bridge.source_usd = request["source_usd"]
            self.bridge.start()
            # Topology editing always owns pointer input; do not pick assets.
            set_asset_selection_enabled = getattr(
                self.bridge, "set_asset_selection_enabled", None)
            if callable(set_asset_selection_enabled):
                set_asset_selection_enabled(False)
            self._clear_native_selection()
            # Topology manipulation uses a full-screen scene DragGesture and
            # stable projected IDs. The legacy physics raycast picker remains
            # available on IsaacSelectionBridge but is not part of this editor.
            self.editor_client = self.authoring_client_factory(self.server_url)
            self.editor_dispatcher = IsaacEditDispatcher(self.editor_client)
            self.editor_controller = IsaacTopologyController(self.editor_dispatcher)
            self.editor_controller.meters_per_unit = meters_per_unit
            self.editor_controller.update_context(request)
            self.editor_view = self.editor_view_factory(self.editor_controller)
            # Navigation binding picks use the same topology input handlers.
            self.editor_window = self.editor_window_factory(self.editor_controller)
            self.editor_window.show()
            # First rebuild happens on the next tick (<=0.25 s); keep _open minimal.
            self.overlay = WebOverlayRenderer(stage)
            self.report_status(generation, "ready")
        except Exception as exc:
            self._stop_bridge()
            self.report_status(generation, "error", str(exc))

    def close(self):
        self._stop_bridge()
        if self.overlay is not None:
            self.overlay.destroy()
            self.overlay = None
        if self.task is not None and not self.task.done():
            self.task.cancel()
