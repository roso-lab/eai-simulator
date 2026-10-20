"""Main-thread stage switching for the integrated map editor (Kit imports lazy)."""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
import time

from .isaac_bridge import (
    IsaacAuthoringClient,
    IsaacEditDispatcher,
    IsaacSelectionBridge,
    IsaacSelectionWindow,
    IsaacTopologyController,
    IsaacTopologyWindow,
    _stage_axis_and_scale,
    assert_source_matches,
)
from .viewport import TopologyEditorViewport
from .web_overlay import WebOverlayRenderer, build_overlay_specs


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
    """Serialize stage opens and bind selections only to the latest map request."""

    def __init__(self, context, server_url, read_context, report_status, *,
                 bridge_factory=IsaacSelectionBridge, window_factory=IsaacSelectionWindow,
                 authoring_client_factory=IsaacAuthoringClient,
                 editor_window_factory=IsaacTopologyWindow,
                 editor_view_factory=TopologyEditorViewport, on_open=None):
        self.context = context
        self.server_url = server_url
        self.read_context = read_context
        self.report_status = report_status
        self.bridge_factory = bridge_factory
        self.window_factory = window_factory
        self.on_open = on_open
        self.authoring_client_factory = authoring_client_factory
        self.editor_window_factory = editor_window_factory
        self.editor_view_factory = editor_view_factory
        self.bridge = self.window = self.task = None
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
        if self.window:
            self.window.destroy()
            self.window = None
        elif self.bridge:
            self.bridge.stop()
        self.bridge = None
        self._bridge_error_active = False

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
            status = self.bridge.status()
            if status["state"] == "error":
                if not self._bridge_error_active and self.editor_controller is not None:
                    self.editor_controller.cancel_gesture(clear_region=True)
                self._bridge_error_active = True
                self.report_status(generation, "error", status.get("last_error"))
            elif status["state"] == "connected":
                if self._bridge_error_active and self.editor_controller is not None:
                    # Input started during an outage also belongs to the old
                    # connection and cannot be committed after reconnecting.
                    self.editor_controller.cancel_gesture(clear_region=True)
                self._bridge_error_active = False
                self.report_status(generation, "ready")
            if self.editor_controller is not None:
                self.editor_controller.update_context(request)
                self.editor_controller.handle_result(self.editor_dispatcher.drain())
                self.editor_controller.flush_selection()
            self._update_overlay(request)

    def _update_overlay(self, request):
        """Rebuild on revision, graph or floor changes; replay selections."""
        # A failed renderer creation leaves overlay None; never let that break
        # the 0.25 s heartbeat shared with the selection bridge.
        if self.overlay is None:
            return
        snapshot = request.get("document_snapshot")
        revision = snapshot.get("revision") if isinstance(snapshot, dict) else None
        overlay_key = (revision, request.get("active_domain"), request.get("active_level"))
        if overlay_key != self.overlay_context_key:
            try:
                self.overlay.rebuild(build_overlay_specs(
                    snapshot, active_level=request.get("active_level")))
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
                region_draft=self.editor_controller.region_draft,
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
            # Establish the expected identity before bind's first snapshot.
            self.bridge.source_usd = request["source_usd"]
            self.bridge.start()
            self.bridge.bind_selection(context=self.context)
            # Topology manipulation uses a full-screen scene DragGesture and
            # stable projected IDs. The legacy physics raycast picker remains
            # available on IsaacSelectionBridge but is not part of this editor.
            self.editor_client = self.authoring_client_factory(self.server_url)
            self.editor_dispatcher = IsaacEditDispatcher(self.editor_client)
            self.editor_controller = IsaacTopologyController(self.editor_dispatcher)
            self.editor_controller.meters_per_unit = meters_per_unit
            self.editor_controller.update_context(request)
            self.editor_view = self.editor_view_factory(self.editor_controller)
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
