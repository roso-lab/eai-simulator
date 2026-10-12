"""Interactive, scene-independent topology authoring window."""
from __future__ import annotations

import json
import math
from pathlib import Path

from .adapter import SceneSource
from .model import PROFILES, TopologyDocument


def _with_semantic_map(source, semantic_map):
    if semantic_map is None:
        return source
    payload = dict(source.payload)
    payload["semantic"] = semantic_map
    return SceneSource(source.scene_key, Path(source.asset_root), payload)


def _stages_match(first, second):
    if first is second:
        return True
    if first is None or second is None:
        return False
    try:
        return bool(first == second)
    except (RuntimeError, TypeError, ValueError):
        return False


def _stage_ids_match(first, second):
    try:
        return first.ToLongInt() == second.ToLongInt()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        return False


class _UsdStageRestorePoint:
    """Keep the current USD stage alive across a possibly destructive reload."""

    def __init__(self, context, global_cache, global_stage_id,
                 owns_global_entry, private_cache, private_stage_id):
        self.context = context
        self.global_cache = global_cache
        self.global_stage_id = global_stage_id
        self.owns_global_entry = bool(owns_global_entry)
        self.private_cache = private_cache
        self.private_stage_id = private_stage_id
        self._retained_global_stage = None
        self._released = False

    def _held_stage(self):
        return self.private_cache.Find(self.private_stage_id)

    def current_stage_matches(self):
        return _stages_match(self.context.get_stage(), self._held_stage())

    def _ensure_global_entry(self):
        held_stage = self._held_stage()
        stage_id = self.global_cache.GetId(held_stage)
        if not stage_id.IsValid():
            stage_id = self.global_cache.Insert(held_stage)
            self.owns_global_entry = True
        elif not _stage_ids_match(stage_id, self.global_stage_id):
            # Another owner repopulated the global cache. Use its entry without
            # later erasing it.
            self.owns_global_entry = False
        self.global_stage_id = stage_id
        return stage_id

    def release(self):
        if self._released:
            return
        held_stage = self._held_stage()
        try:
            # UsdContext resolves an externally attached stage through the
            # global cache even after its completion callback.  Once this
            # stage is current, leave that cache entry in place for the
            # context to own until close_stage()/new_stage() replaces it.
            try:
                attached = _stages_match(self.context.get_stage(), held_stage)
            except (AttributeError, RuntimeError, TypeError, ValueError):
                attached = None
            current_id = self.global_cache.GetId(held_stage)
            if (self.owns_global_entry and current_id.IsValid()
                    and _stage_ids_match(current_id, self.global_stage_id)):
                if attached is False:
                    self.global_cache.Erase(held_stage)
                else:
                    # Keep cleanup ownership, but defer it until a later stage
                    # replacement proves this entry is no longer attached.
                    self._retained_global_stage = held_stage
        finally:
            self.private_cache.Erase(held_stage)
            self._released = True

    @property
    def retains_global_entry(self):
        return self._retained_global_stage is not None

    def release_inactive_global_entry(self):
        """Erase a retained entry only after it is no longer context-current."""
        stage = self._retained_global_stage
        if stage is None:
            return True
        try:
            if _stages_match(self.context.get_stage(), stage):
                return False
            current_id = self.global_cache.GetId(stage)
            if (current_id.IsValid()
                    and _stage_ids_match(current_id, self.global_stage_id)):
                self.global_cache.Erase(stage)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            # Preserve an entry whose attachment state cannot be proved.
            return False
        self._retained_global_stage = None
        self.owns_global_entry = False
        return True

    def restore(self, on_complete=None):
        """Attach the cached stage and release only after Kit's callback."""
        if self.current_stage_matches():
            self.release()
            return "unchanged"

        callback_result = {"succeeded": None}
        notified = False
        stage_id = self._ensure_global_entry()

        def completed(succeeded=True, *_args):
            nonlocal notified
            if notified:
                return
            restored = bool(succeeded) and self.current_stage_matches()
            callback_result["succeeded"] = restored
            self.release()
            notified = True
            if on_complete is not None:
                on_complete(restored)

        try:
            accepted = self.context.attach_stage_with_callback(
                stage_id.ToLongInt(), completed
            )
        except (AttributeError, RuntimeError, TypeError, ValueError):
            completed(False)
            return "failed"
        if callback_result["succeeded"] is not None:
            return "restored" if callback_result["succeeded"] else "failed"
        if accepted is False:
            completed(False)
            return "failed"
        return "scheduled"


def _capture_usd_stage():
    try:
        import omni.usd
        from pxr import Usd, UsdUtils
    except ImportError:
        return None
    context = omni.usd.get_context()
    stage = context.get_stage()
    if stage is None:
        return None
    private_cache = Usd.StageCache()
    private_stage_id = private_cache.Insert(stage)
    global_cache = UsdUtils.StageCache.Get()
    global_stage_id = global_cache.GetId(stage)
    owns_global_entry = not global_stage_id.IsValid()
    if owns_global_entry:
        # This must happen before adapter.load_stage(): a context stage can be a
        # weak USD handle that expires immediately after context.new_stage().
        global_stage_id = global_cache.Insert(stage)
    return _UsdStageRestorePoint(
        context,
        global_cache,
        global_stage_id,
        owns_global_entry,
        private_cache,
        private_stage_id,
    )


class TopologyAuthoringWindow:
    """Viewport-first editor with explicit selection and property panels."""

    def __init__(self, document, adapter, source, output_root: Path, *, semantic_map=None):
        import omni.ui as ui

        self.document = document
        self.adapter = adapter
        self.source = source
        self.semantic_map = semantic_map
        self._stage_ready = True
        self.output_root = Path(output_root)
        self._ui = ui
        self.status = None
        self.object_summary = None
        self._frames = []
        self._overlay = None
        self._retained_stage_restore_points = []
        self.window = ui.Window("Topology Authoring", width=520, height=760)
        try:
            self._build()
            from .viewport import ViewportOverlay

            self._overlay = ViewportOverlay(document, self.on_viewport_click, self.cancel_preview)
            self._refresh()
        except Exception:
            try:
                self.destroy()
            except Exception:
                pass
            raise

    def _build(self):
        ui = self._ui
        with self.window.frame:
            with ui.ScrollingFrame(
                horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
                vertical_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_ON
            ):
                with ui.VStack(height=0, width=ui.Fraction(1), spacing=7, style={"margin": 10}):
                    ui.Label(f"Topology Authoring  |  {self.source.scene_key}", height=30, style={"font_size": 19})
                    self.status = ui.Label(
                        "Click empty ground to preview a node. Confirm it, then click a node to start an edge.",
                        height=38, word_wrap=True,
                    )
                    ui.Separator(height=2)
                    with ui.CollapsableFrame("File", collapsed=False, height=0):
                        with ui.VStack(height=61, spacing=5):
                            with ui.HStack(height=28, spacing=5):
                                ui.Button("Load Source", width=0, clicked_fn=self.load_source)
                                ui.Button("Load Draft", width=0, clicked_fn=self.load_draft)
                                ui.Button("Save Draft", width=0, clicked_fn=self.save_draft)
                            with ui.HStack(height=28, spacing=5):
                                ui.Button("Validate", width=0, clicked_fn=self.validate)
                                ui.Button("Generate", width=0, clicked_fn=self.generate)
                    with ui.CollapsableFrame("History", collapsed=False, height=0):
                        with ui.HStack(height=28, spacing=5):
                            ui.Button("Undo", width=0, clicked_fn=self.undo)
                            ui.Button("Redo", width=0, clicked_fn=self.redo)
                            ui.Button("Cancel Preview", width=0, clicked_fn=self.cancel_preview)
                    with ui.CollapsableFrame("Preview", collapsed=False, height=0):
                        with ui.VStack(height=100, spacing=4):
                            ui.Label(
                                "Empty click = red node preview. Confirm Node commits it. "
                                "Click a green node, then click a destination node.", word_wrap=True,
                            )
                            with ui.HStack(height=28, spacing=5):
                                ui.Button("Confirm Node", width=0, clicked_fn=self.confirm_node)
                                ui.Button("Confirm Edge", width=0, clicked_fn=self.confirm_edge)
                                ui.Button("Clear Selection", width=0, clicked_fn=self.clear_selection)
                            with ui.HStack(height=28, spacing=5):
                                ui.Button("One-way", width=0, clicked_fn=lambda: self.set_pending_direction(True))
                                ui.Button("Two-way", width=0, clicked_fn=lambda: self.set_pending_direction(False))
                    for builder in (self._build_selection, self._build_node_properties,
                                    self._build_edge_properties, self._build_target_properties):
                        frame = ui.Frame(height=0)
                        frame.set_build_fn(builder)
                        self._frames.append(frame)
                    with ui.CollapsableFrame("Status", collapsed=False, height=0):
                        self.object_summary = ui.Label("", word_wrap=True)

    @staticmethod
    def _format_point(point):
        return "(" + ", ".join(f"{float(value):.2f}" for value in point) + ")"

    def _build_selection(self):
        ui = self._ui
        with ui.CollapsableFrame("Selection", collapsed=False, height=0):
            with ui.VStack(height=0, spacing=4):
                kind, row = self.document.selected()
                if row is None:
                    ui.Label("Nothing selected. Green objects are committed; red objects are previews or selection.")
                    return
                ui.Label(f"Type: {kind}")
                ui.Label(f"ID: {row['id']}")
                ui.Label(f"Locked: {bool(row.get('locked') or row.get('locked_geometry'))}")
                if kind == "node":
                    ui.Label(f"Position: {self._format_point(row['position_xyz'])}")
                else:
                    ui.Label(f"From: {row['from']}    To: {row['to']}")
                    ui.Label(f"Direction: {'one-way' if row.get('directed') else 'two-way'}")

    def _build_node_properties(self):
        ui = self._ui
        with ui.CollapsableFrame("Node Properties", collapsed=False, height=0):
            with ui.VStack(height=0, spacing=4):
                if self.document.selected_node_id is None:
                    ui.Label("Select a node in the viewport to edit it.")
                    return
                node = self.document.node(self.document.selected_node_id)
                if node.get("locked"):
                    ui.Label("This node is locked.")
                    return
                models = [ui.SimpleFloatModel(float(value)) for value in node["position_xyz"]]
                with ui.HStack(height=28, spacing=4):
                    for label, model in zip(("X", "Y", "Z"), models):
                        ui.Label(label, width=18)
                        ui.FloatField(model, width=85)
                with ui.HStack(height=28, spacing=5):
                    ui.Button("Apply Move", clicked_fn=lambda: self._apply_node_move(models))
                    ui.Button("Delete Node", clicked_fn=self.delete_selected_node)
                refs = node.get("target_refs", ())
                ui.Label("Bound targets: " + (", ".join(refs) if refs else "none"), word_wrap=True)

    def _apply_node_move(self, models):
        if not self._require_stage_ready():
            return
        try:
            position = [float(model.as_float) for model in models]
            self.document.move_node(self.document.selected_node_id, position)
            self._set_status("Node position updated")
        except (AttributeError, TypeError, ValueError, KeyError) as exc:
            self._set_status(f"Move failed: {exc}")
        self._refresh()

    def _build_edge_properties(self):
        ui = self._ui
        with ui.CollapsableFrame("Edge Properties", collapsed=False, height=0):
            with ui.VStack(height=0, spacing=4):
                if self.document.selected_edge_id is None:
                    ui.Label("Select an edge in the viewport to edit it.")
                    return
                edge = self.document.edge(self.document.selected_edge_id)
                locked = bool(edge.get("locked_geometry"))
                ui.Label(f"From: {edge['from']}    To: {edge['to']}")
                ui.Label(f"Geometry locked: {locked}")
                with ui.HStack(height=28, spacing=5):
                    ui.Button("Set One-way", clicked_fn=lambda: self.set_selected_direction(True))
                    ui.Button("Set Two-way", clicked_fn=lambda: self.set_selected_direction(False))
                    ui.Button("Delete Edge", clicked_fn=self.delete_selected_edge)
                for profile in PROFILES:
                    current = edge.get("profiles", {}).get(profile, {}).get("clearance_m", PROFILES[profile])
                    model = ui.SimpleFloatModel(float(current))
                    with ui.HStack(height=28, spacing=4):
                        ui.Label(f"{profile} clearance", width=145)
                        ui.FloatField(model, width=100)
                        ui.Button("Apply", width=60, clicked_fn=lambda p=profile, m=model: self._apply_profile(p, m))
                if not locked:
                    polyline_model = ui.SimpleStringModel(json.dumps(edge["polyline_xyz"], separators=(",", ":")))
                    ui.Label("Polyline XYZ JSON", word_wrap=True)
                    with ui.HStack(height=28, spacing=4):
                        ui.StringField(polyline_model)
                        ui.Button("Apply", width=60, clicked_fn=lambda m=polyline_model: self._apply_polyline(m))

    def _apply_profile(self, profile, model):
        if not self._require_stage_ready():
            return
        try:
            value = float(model.as_float)
            self.document.set_edge_profile(self.document.selected_edge_id, profile, value)
            self._set_status(f"{profile} clearance updated")
        except (AttributeError, TypeError, ValueError, KeyError) as exc:
            self._set_status(f"Profile update failed: {exc}")
        self._refresh()

    def _apply_polyline(self, model):
        if not self._require_stage_ready():
            return
        try:
            points = json.loads(model.as_string)
            self.document.set_edge_polyline(self.document.selected_edge_id, points)
            self._set_status("Edge polyline updated")
        except (AttributeError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            self._set_status(f"Polyline update failed: {exc}")
        self._refresh()

    def _build_target_properties(self):
        ui = self._ui
        with ui.CollapsableFrame("Semantic Target Bindings", collapsed=True, height=0):
            with ui.VStack(height=0, spacing=4):
                if self.document.selected_node_id is None:
                    ui.Label("Select a node to bind one or more semantic target entrances.")
                    return
                for target in self.adapter.semantic_targets(self.source):
                    ui.Button(
                        f"Bind {target.ref}",
                        height=28,
                        clicked_fn=lambda ref=target.ref: self.bind_current(ref),
                    )

    def _refresh(self):
        if self._overlay:
            self._overlay.refresh()
        for frame in self._frames:
            frame.rebuild()
        if self.object_summary is not None:
            selected = self.document.selected_node_id or self.document.selected_edge_id or "none"
            states = ["red node preview"] if self.document.pending_node else []
            if self.document.pending_edge:
                states.append("red edge preview")
            suffix = " | " + ", ".join(states) if states else ""
            self.object_summary.text = (
                f"Green nodes: {len(self.document.nodes)} | green edges: {len(self.document.edges)} "
                f"| selected: {selected}{suffix}"
            )

    def _set_status(self, text):
        if self.status is not None:
            self.status.text = str(text)

    def _require_stage_ready(self):
        if self._stage_ready:
            return True
        self._set_status(
            "Authoring is disabled because the scene stage is not reliable; "
            "use Load Source to recover"
        )
        return False

    def _nearest_node(self, position):
        best = None
        for node in self.document.nodes:
            distance = math.dist(position[:2], node["position_xyz"][:2])
            if best is None or distance < best[0]:
                best = (distance, node)
        return best if best and best[0] <= 1.5 else None

    @staticmethod
    def _point_segment_distance(point, start, end):
        px, py = point[:2]
        ax, ay = start[:2]
        bx, by = end[:2]
        dx, dy = bx - ax, by - ay
        denom = dx * dx + dy * dy
        if denom <= 1e-12:
            return math.dist((px, py), (ax, ay))
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
        return math.dist((px, py), (ax + t * dx, ay + t * dy))

    def _nearest_edge(self, position):
        best = None
        for edge in self.document.edges:
            points = edge.get("polyline_xyz", ())
            distance = min(
                (self._point_segment_distance(position, a, b) for a, b in zip(points, points[1:])),
                default=float("inf"),
            )
            if best is None or distance < best[0]:
                best = (distance, edge)
        return best if best and best[0] <= 1.5 else None

    def on_viewport_click(self, position, _prim_path=None):
        if not self._require_stage_ready():
            return
        position = self.adapter.project_to_support_surface(position, self.source)
        if self.document.pending_node is not None:
            self.document.begin_node_preview(position)
            self._set_status("Red node preview moved. Confirm Node or right-click to cancel.")
        elif self.document.pending_edge is not None:
            hit = self._nearest_node(position)
            if hit and hit[1]["id"] != self.document.pending_edge.from_node_id:
                self.document.update_edge_preview(position, snapped_node_id=hit[1]["id"])
                self._set_status("Red edge preview snapped to destination. Confirm Edge.")
            else:
                self.document.update_edge_preview(position)
                self._set_status("Red edge preview; click a destination node.")
        else:
            node_hit = self._nearest_node(position)
            edge_hit = self._nearest_edge(position)
            if node_hit and (edge_hit is None or node_hit[0] <= edge_hit[0]):
                self.document.begin_edge_preview(node_hit[1]["id"])
                self.document.update_edge_preview(node_hit[1]["position_xyz"])
                self._set_status(f"Selected {node_hit[1]['id']} as edge source; click a destination node.")
            elif edge_hit:
                self.document.select_edge(edge_hit[1]["id"])
                self._set_status(f"Selected {edge_hit[1]['id']}")
            else:
                self.document.begin_node_preview(position)
                self._set_status("Red node preview. Confirm Node or right-click to cancel.")
        self._refresh()

    def confirm_node(self):
        if not self._require_stage_ready():
            return
        try:
            node_id = self.document.confirm_node_preview()
            self._set_status(f"Node {node_id} committed")
        except ValueError as exc:
            self._set_status(str(exc))
        self._refresh()

    def confirm_edge(self):
        if not self._require_stage_ready():
            return
        try:
            edge_id = self.document.confirm_edge_preview()
            self._set_status(f"Edge {edge_id} committed")
        except ValueError as exc:
            self._set_status(str(exc))
        self._refresh()

    def set_pending_direction(self, directed):
        if not self._require_stage_ready():
            return
        if self.document.pending_edge:
            self.document.pending_edge.directed = bool(directed)
            self._set_status("Preview direction updated")
        else:
            self._set_status("Start an edge preview first")
        self._refresh()

    def set_selected_direction(self, directed):
        if not self._require_stage_ready():
            return
        try:
            self.document.set_edge_direction(self.document.selected_edge_id, directed)
            self._set_status("Edge direction updated")
        except (KeyError, ValueError) as exc:
            self._set_status(str(exc))
        self._refresh()

    def bind_current(self, ref):
        if not self._require_stage_ready():
            return
        if self.document.selected_node_id is None:
            self._set_status("Select a node first")
            return
        try:
            self.document.bind_target(self.document.selected_node_id, ref)
            self._set_status(f"Bound {ref} to {self.document.selected_node_id}")
        except (KeyError, ValueError) as exc:
            self._set_status(str(exc))
        self._refresh()

    def delete_selected_node(self):
        if not self._require_stage_ready():
            return
        try:
            node_id = self.document.selected_node_id
            self.document.remove_node(node_id)
            self.document.cancel_preview()
            self._set_status(f"Deleted node {node_id}")
        except (KeyError, ValueError) as exc:
            self._set_status(str(exc))
        self._refresh()

    def delete_selected_edge(self):
        if not self._require_stage_ready():
            return
        try:
            edge_id = self.document.selected_edge_id
            self.document.remove_edge(edge_id)
            self.document.cancel_preview()
            self._set_status(f"Deleted edge {edge_id}")
        except (KeyError, ValueError) as exc:
            self._set_status(str(exc))
        self._refresh()

    def clear_selection(self):
        self.document.cancel_preview()
        self._set_status("Selection cleared")
        self._refresh()

    def cancel_preview(self):
        self.document.cancel_preview()
        self._set_status("Preview cancelled")
        self._refresh()

    def undo(self):
        if not self._require_stage_ready():
            return
        self.document.undo()
        self._set_status("Undo applied")
        self._refresh()

    def redo(self):
        if not self._require_stage_ready():
            return
        self.document.redo()
        self._set_status("Redo applied")
        self._refresh()

    def load_source(self):
        restore_point = _capture_usd_stage()
        stage_load_started = False
        try:
            source = _with_semantic_map(
                self.adapter.load_source(self.source.asset_root),
                self.semantic_map,
            )
            if source.scene_key != self.document.scene_key:
                raise ValueError(
                    f"reloaded source scene {source.scene_key!r} differs from "
                    f"current scene {self.document.scene_key!r}"
                )
            document = TopologyDocument(
                source.scene_key,
                self.adapter.source_digests(source),
            )
            from .product import validate_document

            errors = validate_document(document, self.adapter, source)
            if errors:
                raise ValueError(
                    "source validation failed: " + "; ".join(errors)
                )
            stage_load_started = True
            self.adapter.load_stage(source)
            self._release_inactive_restored_stages()
            if self._overlay:
                self._overlay.rebind_stage()
            self.document.replace_content(document)
            self.source = source
            self._stage_ready = True
        except Exception as exc:
            if not stage_load_started:
                restoration = "not_needed"
                if restore_point is not None:
                    restore_point.release()
            elif restore_point is not None and restore_point.current_stage_matches():
                # An adapter that edits the current stage before raising cannot
                # be rolled back generically.  Keep the draft, but do not allow
                # it to be used against a possibly changed scene.
                restoration = "unchanged"
                self._stage_ready = False
                restore_point.release()
            elif restore_point is None:
                restoration = "unavailable"
                self._stage_ready = False
            else:
                self._stage_ready = False
                restoration = restore_point.restore(
                    lambda restored: self._on_stage_restore_complete(
                        restored, restore_point
                    ),
                )
            suffix = {
                "not_needed": "",
                "restored": "; previous stage restored",
                "scheduled": "; previous stage restoration scheduled",
                "unchanged": (
                    "; previous stage remains attached; adapter in-place stage edits, "
                    "if any, cannot be rolled back"
                ),
                "unavailable": "; previous stage restoration is unavailable",
                "failed": "; previous stage could not be restored",
            }[restoration]
            self._set_status(f"Source reload failed: {exc}{suffix}")
            return
        if restore_point is not None:
            restore_point.release()
        self._set_status("Source reloaded; topology reset to empty")
        self._refresh()

    def _release_inactive_restored_stages(self):
        retained = []
        for restore_point in getattr(
            self, "_retained_stage_restore_points", ()
        ):
            if not restore_point.release_inactive_global_entry():
                retained.append(restore_point)
        self._retained_stage_restore_points = retained

    def _on_stage_restore_complete(self, restored, restore_point=None):
        self._stage_ready = bool(restored)
        if not restored:
            self._set_status(
                "Previous stage restoration failed; authoring remains disabled"
            )
            return
        if (restore_point is not None
                and getattr(restore_point, "retains_global_entry", False)):
            self._retained_stage_restore_points = list(getattr(
                self, "_retained_stage_restore_points", ()
            ))
            self._retained_stage_restore_points.append(restore_point)
        try:
            if self._overlay:
                self._overlay.rebind_stage()
            self._refresh()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            self._stage_ready = False
            self._set_status(
                "Previous stage was restored, but the overlay could not be rebound; "
                "authoring remains disabled"
            )
            return
        self._set_status("Previous stage restored; authoring re-enabled")

    def validate(self):
        if not self._require_stage_ready():
            return ["scene stage is not reliable"]
        from .product import validate_document

        errors = validate_document(self.document, self.adapter, self.source)
        self._set_status("Validation passed" if not errors else "Validation errors: " + "; ".join(errors[:5]))
        return errors

    def save_draft(self):
        from .product import save_draft

        save_draft(self.output_root / "authoring/navigation/ground/topology.draft.json", self.document)
        self._set_status("Draft saved")

    def load_draft(self):
        if not self._require_stage_ready():
            return
        from .product import load_product

        try:
            loaded = load_product(
                self.output_root / "authoring/navigation/ground/topology.draft.json",
                self.adapter,
                self.source,
            )
            if loaded.scene_key != self.document.scene_key:
                raise ValueError("draft scene differs from source")
            self.document.replace_content(loaded)
            self._set_status("Draft loaded")
            self._refresh()
        except (OSError, ValueError, KeyError) as exc:
            self._set_status(f"Load failed: {exc}")

    def generate(self):
        if not self._require_stage_ready():
            return
        from .product import generate_product

        if self.document.pending_node or self.document.pending_edge:
            self._set_status("Confirm or cancel the red preview before generating")
            return
        try:
            generate_product(
                self.output_root / "authoring/navigation/ground/topology.json",
                self.document,
                self.adapter,
                self.source,
            )
            self._set_status("Topology product generated")
        except ValueError as exc:
            self._set_status(str(exc))

    def destroy(self):
        overlay = self._overlay
        self._overlay = None
        window = self.window
        self.window = None
        try:
            if overlay is not None:
                overlay.destroy()
        finally:
            for frame in self._frames:
                try:
                    frame.set_build_fn(None)
                except (AttributeError, RuntimeError, TypeError):
                    pass
            self._frames.clear()
            self.status = None
            self.object_summary = None
            self._ui = None
            if window is not None:
                frame = getattr(window, "frame", None)
                if frame is not None:
                    try:
                        frame.set_build_fn(None)
                    except (AttributeError, RuntimeError, TypeError):
                        pass
                window.destroy()
