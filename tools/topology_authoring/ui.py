"""Interactive, scene-independent topology authoring window."""
from __future__ import annotations

import json
import math
from pathlib import Path

from .model import PROFILES


class TopologyAuthoringWindow:
    """Viewport-first editor with explicit selection and property panels."""

    def __init__(self, document, adapter, source, output_root: Path):
        import omni.ui as ui

        self.document = document
        self.adapter = adapter
        self.source = source
        self.output_root = Path(output_root)
        self._ui = ui
        self.status = None
        self.object_summary = None
        self._frames = []
        self._overlay = None
        self.window = ui.Window("Topology Authoring", width=520, height=760)
        self._build()
        from .viewport import ViewportOverlay

        self._overlay = ViewportOverlay(document, self.on_viewport_click, self.cancel_preview)
        self._refresh()

    def _build(self):
        ui = self._ui
        with self.window.frame:
            with ui.VStack(spacing=7, style={"margin": 10}):
                ui.Label(f"Topology Authoring  |  {self.source.scene_key}", height=30, style={"font_size": 19})
                self.status = ui.Label(
                    "Click empty ground to preview a node. Confirm it, then click a node to start an edge.",
                    height=38, word_wrap=True,
                )
                ui.Separator()
                with ui.CollapsableFrame("File", collapsed=False):
                    with ui.HStack(spacing=5):
                        ui.Button("Load Source", clicked_fn=self.load_source)
                        ui.Button("Load Draft", clicked_fn=self.load_draft)
                        ui.Button("Save Draft", clicked_fn=self.save_draft)
                    with ui.HStack(spacing=5):
                        ui.Button("Validate", clicked_fn=self.validate)
                        ui.Button("Generate", clicked_fn=self.generate)
                with ui.CollapsableFrame("History", collapsed=False):
                    with ui.HStack(spacing=5):
                        ui.Button("Undo", clicked_fn=self.undo)
                        ui.Button("Redo", clicked_fn=self.redo)
                        ui.Button("Cancel Preview", clicked_fn=self.cancel_preview)
                with ui.CollapsableFrame("Preview", collapsed=False):
                    with ui.VStack(spacing=4):
                        ui.Label(
                            "Empty click = red node preview. Confirm Node commits it. "
                            "Click a green node, then click a destination node.", word_wrap=True,
                        )
                        with ui.HStack(spacing=5):
                            ui.Button("Confirm Node", clicked_fn=self.confirm_node)
                            ui.Button("Confirm Edge", clicked_fn=self.confirm_edge)
                            ui.Button("Clear Selection", clicked_fn=self.clear_selection)
                        with ui.HStack(spacing=5):
                            ui.Button("One-way", clicked_fn=lambda: self.set_pending_direction(True))
                            ui.Button("Two-way", clicked_fn=lambda: self.set_pending_direction(False))
                for builder in (self._build_selection, self._build_node_properties,
                                self._build_edge_properties, self._build_target_properties):
                    frame = ui.Frame()
                    frame.set_build_fn(builder)
                    self._frames.append(frame)
                with ui.CollapsableFrame("Status", collapsed=False):
                    self.object_summary = ui.Label("", word_wrap=True)

    @staticmethod
    def _format_point(point):
        return "(" + ", ".join(f"{float(value):.2f}" for value in point) + ")"

    def _build_selection(self):
        ui = self._ui
        with ui.CollapsableFrame("Selection", collapsed=False):
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
        with ui.CollapsableFrame("Node Properties", collapsed=False):
            if self.document.selected_node_id is None:
                ui.Label("Select a node in the viewport to edit it.")
                return
            node = self.document.node(self.document.selected_node_id)
            if node.get("locked"):
                ui.Label("This node is locked.")
                return
            models = [ui.SimpleFloatModel(float(value)) for value in node["position_xyz"]]
            with ui.HStack(spacing=4):
                for label, model in zip(("X", "Y", "Z"), models):
                    ui.Label(label, width=18)
                    ui.FloatField(model, width=85)
            with ui.HStack(spacing=5):
                ui.Button("Apply Move", clicked_fn=lambda: self._apply_node_move(models))
                ui.Button("Delete Node", clicked_fn=self.delete_selected_node)
            refs = node.get("target_refs", ())
            ui.Label("Bound targets: " + (", ".join(refs) if refs else "none"), word_wrap=True)

    def _apply_node_move(self, models):
        try:
            position = [float(model.as_float) for model in models]
            self.document.move_node(self.document.selected_node_id, position)
            self._set_status("Node position updated")
        except (AttributeError, TypeError, ValueError, KeyError) as exc:
            self._set_status(f"Move failed: {exc}")
        self._refresh()

    def _build_edge_properties(self):
        ui = self._ui
        with ui.CollapsableFrame("Edge Properties", collapsed=False):
            if self.document.selected_edge_id is None:
                ui.Label("Select an edge in the viewport to edit it.")
                return
            edge = self.document.edge(self.document.selected_edge_id)
            locked = bool(edge.get("locked_geometry"))
            ui.Label(f"From: {edge['from']}    To: {edge['to']}")
            ui.Label(f"Geometry locked: {locked}")
            with ui.HStack(spacing=5):
                ui.Button("Set One-way", clicked_fn=lambda: self.set_selected_direction(True))
                ui.Button("Set Two-way", clicked_fn=lambda: self.set_selected_direction(False))
                ui.Button("Delete Edge", clicked_fn=self.delete_selected_edge)
            for profile in PROFILES:
                current = edge.get("profiles", {}).get(profile, {}).get("clearance_m", PROFILES[profile])
                model = ui.SimpleFloatModel(float(current))
                with ui.HStack(spacing=4):
                    ui.Label(f"{profile} clearance", width=145)
                    ui.FloatField(model, width=100)
                    ui.Button("Apply", width=60, clicked_fn=lambda p=profile, m=model: self._apply_profile(p, m))
            if not locked:
                polyline_model = ui.SimpleStringModel(json.dumps(edge["polyline_xyz"], separators=(",", ":")))
                ui.Label("Polyline XYZ JSON", word_wrap=True)
                with ui.HStack(spacing=4):
                    ui.StringField(polyline_model)
                    ui.Button("Apply", width=60, clicked_fn=lambda m=polyline_model: self._apply_polyline(m))

    def _apply_profile(self, profile, model):
        try:
            value = float(model.as_float)
            self.document.set_edge_profile(self.document.selected_edge_id, profile, value)
            self._set_status(f"{profile} clearance updated")
        except (AttributeError, TypeError, ValueError, KeyError) as exc:
            self._set_status(f"Profile update failed: {exc}")
        self._refresh()

    def _apply_polyline(self, model):
        try:
            points = json.loads(model.as_string)
            self.document.set_edge_polyline(self.document.selected_edge_id, points)
            self._set_status("Edge polyline updated")
        except (AttributeError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            self._set_status(f"Polyline update failed: {exc}")
        self._refresh()

    def _build_target_properties(self):
        ui = self._ui
        with ui.CollapsableFrame("Semantic Target Bindings", collapsed=True):
            if self.document.selected_node_id is None:
                ui.Label("Select a node to bind one or more semantic target entrances.")
                return
            for target in self.adapter.semantic_targets(self.source):
                ui.Button(f"Bind {target.ref}", clicked_fn=lambda ref=target.ref: self.bind_current(ref))

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
        try:
            node_id = self.document.confirm_node_preview()
            self._set_status(f"Node {node_id} committed")
        except ValueError as exc:
            self._set_status(str(exc))
        self._refresh()

    def confirm_edge(self):
        try:
            edge_id = self.document.confirm_edge_preview()
            self._set_status(f"Edge {edge_id} committed")
        except ValueError as exc:
            self._set_status(str(exc))
        self._refresh()

    def set_pending_direction(self, directed):
        if self.document.pending_edge:
            self.document.pending_edge.directed = bool(directed)
            self._set_status("Preview direction updated")
        else:
            self._set_status("Start an edge preview first")
        self._refresh()

    def set_selected_direction(self, directed):
        try:
            self.document.set_edge_direction(self.document.selected_edge_id, directed)
            self._set_status("Edge direction updated")
        except (KeyError, ValueError) as exc:
            self._set_status(str(exc))
        self._refresh()

    def bind_current(self, ref):
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
        try:
            node_id = self.document.selected_node_id
            self.document.remove_node(node_id)
            self.document.cancel_preview()
            self._set_status(f"Deleted node {node_id}")
        except (KeyError, ValueError) as exc:
            self._set_status(str(exc))
        self._refresh()

    def delete_selected_edge(self):
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
        self.document.undo()
        self._set_status("Undo applied")
        self._refresh()

    def redo(self):
        self.document.redo()
        self._set_status("Redo applied")
        self._refresh()

    def load_source(self):
        try:
            self.source = self.adapter.load_source(self.source.asset_root)
            self.document.nodes = []
            self.document.edges = []
            self.document.source = dict(self.adapter.source_digests(self.source))
            self.document.cancel_preview()
            self.adapter.load_stage(self.source)
            self._set_status("Source reloaded; topology reset to empty")
            self._refresh()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self._set_status(f"Source reload failed: {exc}")

    def validate(self):
        from .product import validate_document

        errors = validate_document(self.document, self.adapter, self.source)
        self._set_status("Validation passed" if not errors else "Validation errors: " + "; ".join(errors[:5]))
        return errors

    def save_draft(self):
        from .product import save_draft

        save_draft(self.output_root / "authoring/navigation/ground/topology.draft.json", self.document)
        self._set_status("Draft saved")

    def load_draft(self):
        from .product import load_product

        try:
            loaded = load_product(
                self.output_root / "authoring/navigation/ground/topology.draft.json",
                self.adapter,
                self.source,
            )
            if loaded.scene_key != self.document.scene_key:
                raise ValueError("draft scene differs from source")
            self.document.nodes, self.document.edges = loaded.nodes, loaded.edges
            self.document.source = loaded.source
            self.document.cancel_preview()
            self._set_status("Draft loaded")
            self._refresh()
        except (OSError, ValueError, KeyError) as exc:
            self._set_status(f"Load failed: {exc}")

    def generate(self):
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
        if self._overlay:
            self._overlay.destroy()
            self._overlay = None
        self.window.destroy()
