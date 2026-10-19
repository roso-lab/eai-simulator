"""Semantic and true-XYZ route drawing controls for the native editor."""
from __future__ import annotations

from copy import deepcopy
import json

from .model import TopologyDocument
from .semantics import COLLECTIONS, empty_semantics


class SemanticWindowMixin:
    def _reset_semantic_record_editor(self):
        self._selected_semantic_id = None
        self._semantic_collection = None
        if getattr(self, "_semantic_record_model", None) is not None:
            self._semantic_record_model.as_string = ""
        if getattr(self, "_levels_json_model", None) is not None:
            self._levels_json_model.as_string = json.dumps(self.document.levels, ensure_ascii=False, indent=2)

    def _build_semantic_controls(self):
        ui = self._ui
        with ui.CollapsableFrame("Rooms / Regions / 3D Routes", collapsed=False, height=0):
            with ui.VStack(height=0, spacing=4):
                ui.Label("Draw a room boundary on its floor, or an XYZ waypoint route between existing nodes.", word_wrap=True)
                for label, name, default in (("Stable ID", "_semantic_id_model", "region:room_1"),
                        ("Name", "_semantic_label_model", "Room"),
                        ("Kind", "_semantic_kind_model", "room"),
                        ("Height (m)", "_region_height_model", "2.5"),
                        ("Entrance node IDs (comma-separated)", "_region_entrances_model", "")):
                    if getattr(self, name, None) is None:
                        setattr(self, name, ui.SimpleStringModel(default))
                    with ui.HStack(height=28, spacing=4):
                        ui.Label(label, width=180)
                        ui.StringField(getattr(self, name))
                with ui.HStack(height=28, spacing=4):
                    ui.Button("Draw Region", width=0, clicked_fn=self.begin_region_drawing)
                    ui.Button("Confirm Region", width=0, clicked_fn=self.confirm_region_drawing)
                    ui.Button("Undo Vertex", width=0, clicked_fn=self.pop_drawing_point)
                ui.Label("Route endpoints are existing node IDs; intermediate clicks become real nodes. Select All Levels for stairs/ramp.", word_wrap=True)
                for label, name in (("Start node", "_draw_route_start_model"), ("End node", "_draw_route_end_model")):
                    if getattr(self, name, None) is None:
                        setattr(self, name, ui.SimpleStringModel(""))
                    with ui.HStack(height=28, spacing=4):
                        ui.Label(label, width=100)
                        ui.StringField(getattr(self, name))
                with ui.HStack(height=28, spacing=4):
                    for mode in ("walk", "stairs", "ramp"):
                        ui.Button("Draw " + mode, width=0, clicked_fn=lambda value=mode: self.begin_route_drawing(value))
                    ui.Button("Confirm Route", width=0, clicked_fn=self.confirm_route_drawing)
                if getattr(self, "_draw_route_directed_model", None) is None:
                    self._draw_route_directed_model = ui.SimpleBoolModel(False)
                    self._draw_route_capabilities_model = ui.SimpleStringModel("")
                with ui.HStack(height=28, spacing=4):
                    ui.CheckBox(self._draw_route_directed_model, width=20)
                    ui.Label("One way", width=80)
                    ui.Label("Extra capabilities", width=120)
                    ui.StringField(self._draw_route_capabilities_model)
                pending = getattr(self, "_semantic_drawing", None)
                ui.Label(f"Drawing: {pending['mode']} ({len(pending['points'])} points)" if pending else "No pending region or route")

    def _build_semantic_records(self):
        ui = self._ui
        with ui.CollapsableFrame("Editable Semantic Records", collapsed=False, height=0):
            with ui.VStack(height=0, spacing=4):
                ui.Label("Select any saved region, route, entity or interaction pose to edit its JSON. Add task poses later using the templates below.", word_wrap=True)
                for collection in COLLECTIONS:
                    for row in (self.document.semantics or empty_semantics())[collection]:
                        ui.Button(f"{collection}: {row.get('label') or row['id']}", height=28,
                            clicked_fn=lambda name=collection, record_id=row["id"]: self.select_semantic_record(name, record_id))
                with ui.HStack(height=28, spacing=4):
                    ui.Button("New Entity", width=0, clicked_fn=lambda: self.new_semantic_record("entities"))
                    ui.Button("New Interaction Pose", width=0, clicked_fn=lambda: self.new_semantic_record("interaction_poses"))
                    ui.Button("New Region JSON", width=0, clicked_fn=lambda: self.new_semantic_record("regions"))
                if getattr(self, "_semantic_record_model", None) is None:
                    self._semantic_record_model = ui.SimpleStringModel("")
                ui.Label("Record JSON (XYZ metres; heading_rad in radians; properties are extensible)", word_wrap=True)
                ui.StringField(self._semantic_record_model, multiline=True, height=170)
                with ui.HStack(height=28, spacing=4):
                    ui.Button("Apply Record", width=0, clicked_fn=self.apply_semantic_record)
                    ui.Button("Delete Record", width=0, clicked_fn=self.delete_semantic_record)

    def _build_level_editor(self):
        ui = self._ui
        with ui.CollapsableFrame("Add / Edit Floors", collapsed=True, height=0):
            with ui.VStack(height=0, spacing=4):
                if getattr(self, "_levels_json_model", None) is None:
                    self._levels_json_model = ui.SimpleStringModel(json.dumps(self.document.levels or [
                        {"id": "ground", "label": "Ground", "support_z_m": 0.0}], ensure_ascii=False))
                ui.Label("Declare floor IDs and support heights before drawing. Existing bindings must remain valid.", word_wrap=True)
                ui.StringField(self._levels_json_model, multiline=True, height=90)
                ui.Button("Apply Floors", height=28, clicked_fn=self.apply_floor_records)

    def apply_floor_records(self):
        if not self._require_stage_ready():
            return
        try:
            from .product import validate_document
            candidate = TopologyDocument.from_dict(self.document.to_dict())
            levels = json.loads(self._levels_json_model.as_string)
            # Validate an upgrade after all v2 graph fields have been migrated.
            semantics = candidate.semantics
            if self.document.version == 1:
                candidate.semantics = None
            candidate.set_levels(levels)
            # Upgrade legacy points to explicit ground levels only when unambiguous.
            if self.document.version == 1:
                for node in candidate.nodes:
                    matches = [row["id"] for row in levels if abs(node["position_xyz"][2]-row["support_z_m"]) <= .20]
                    if len(matches) != 1:
                        raise ValueError("legacy node needs one matching floor")
                    node.update(level_id=matches[0], label="", aliases=[])
                for edge in candidate.edges:
                    edge.update(traversal_mode="walk", required_controller_capabilities=[])
                candidate.semantics = semantics
            errors = validate_document(candidate)
            if errors:
                raise ValueError("; ".join(errors))
            self.document._record()
            self.document._restore_snapshot(candidate._snapshot())
            self._semantic_drawing = None
            self.document.cancel_preview()
            self.active_level_id = candidate.levels[0]["id"]
            self._apply_active_level_view()
            # The static Preview frame contains version-dependent node controls.
            self._frames = []
            self._build()
            self._set_status("Floors updated")
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            self._set_status(f"Floor update failed: {exc}")
        self._refresh()

    def begin_region_drawing(self):
        if not self._require_stage_ready():
            return
        if self.document.version != 2 or getattr(self, "active_level_id", None) is None:
            self._set_status("Declare/select a floor before drawing a room or region")
            return
        self.document.cancel_preview()
        self._heading_pick_active = False
        self._semantic_drawing = {"mode": "region", "level_id": self.active_level_id, "points": []}
        self._set_status("Click room boundary vertices; Confirm Region closes the polygon")
        self._refresh()

    def begin_route_drawing(self, traversal_mode):
        if not self._require_stage_ready():
            return
        try:
            if self.document.version != 2:
                raise ValueError("declare floors before drawing an XYZ route")
            first = self._draw_route_start_model.as_string.strip()
            last = self._draw_route_end_model.as_string.strip()
            start = self.document.node(first)
            end = self.document.node(last)
            if traversal_mode not in {"walk", "stairs", "ramp"} or first == last:
                raise ValueError("choose a traversal mode and two different endpoint nodes")
            if traversal_mode == "walk" and (start.get("level_id") is None or start.get("level_id") != end.get("level_id")):
                raise ValueError("walk route endpoints must share a declared floor")
            self.document.cancel_preview()
            self._heading_pick_active = False
            self._semantic_drawing = {"mode": "route", "traversal_mode": traversal_mode,
                "start_node_id": first, "end_node_id": last, "points": []}
            self.active_level_id = start.get("level_id") if traversal_mode == "walk" else None
            self._apply_active_level_view()
            self._set_status("Click intermediate waypoints on the real surface, including stair turns; Confirm Route joins both endpoints")
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            self._set_status(f"Route drawing failed: {exc}")
        self._refresh()

    def semantic_drawing_click(self, position):
        drawing = getattr(self, "_semantic_drawing", None)
        if drawing is None:
            return False
        try:
            point = list(self.adapter.project_to_support_surface(position, self.source))
            if drawing["mode"] == "region":
                height = next(row["support_z_m"] for row in self.document.levels if row["id"] == drawing["level_id"])
                if abs(point[2]-height) > .20:
                    raise ValueError("region vertex must be on the selected floor")
                point[2] = height
            # Reuse model's strict finite XYZ contract without adding history.
            from .model import _point
            drawing["points"].append(_point(point))
            self._set_status(f"{len(drawing['points'])} drawing points; confirm or Undo Vertex")
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            self._set_status(f"Drawing point failed: {exc}")
        self._refresh()
        return True

    def pop_drawing_point(self):
        drawing = getattr(self, "_semantic_drawing", None)
        if drawing and drawing["points"]:
            drawing["points"].pop()
        self._refresh()

    def confirm_region_drawing(self):
        if not self._require_stage_ready():
            return
        try:
            from .ui import _csv_strings
            drawing = getattr(self, "_semantic_drawing", None)
            if not drawing or drawing["mode"] != "region":
                raise ValueError("start a region drawing first")
            record = {"id": self._semantic_id_model.as_string.strip(),
                "label": self._semantic_label_model.as_string, "aliases": [],
                "kind": self._semantic_kind_model.as_string.strip(),
                "level_id": drawing["level_id"], "polygon_xyz": drawing["points"],
                "height_m": float(self._region_height_model.as_string),
                "entrance_node_ids": list(_csv_strings(self._region_entrances_model.as_string)),
                "properties": {}}
            if any(row["id"] == record["id"] for row in (self.document.semantics or empty_semantics())["regions"]):
                raise ValueError("region ID already exists; select its saved record to edit")
            identifier = self.document.upsert_semantic("regions", record)
            self._semantic_drawing = None
            self.select_semantic_record("regions", identifier)
            self._set_status(f"Region {identifier} saved")
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            self._set_status(f"Region failed: {exc}")
        self._refresh()

    def confirm_route_drawing(self):
        if not self._require_stage_ready():
            return
        try:
            from .ui import _csv_strings
            from .product import validate_document
            drawing = getattr(self, "_semantic_drawing", None)
            if not drawing or drawing["mode"] != "route":
                raise ValueError("start a route drawing first")
            candidate = TopologyDocument.from_dict(self.document.to_dict())
            identifier = candidate.add_waypoint_route(
                route_id=self._semantic_id_model.as_string.strip(), label=self._semantic_label_model.as_string,
                start_node_id=drawing["start_node_id"], end_node_id=drawing["end_node_id"],
                intermediate_xyz=drawing["points"], traversal_mode=drawing["traversal_mode"],
                directed=self._draw_route_directed_model.as_bool,
                required_controller_capabilities=_csv_strings(self._draw_route_capabilities_model.as_string))
            errors = validate_document(candidate, self.adapter, self.source)
            if errors:
                raise ValueError("; ".join(errors))
            self.document._record()
            self.document._restore_snapshot(candidate._snapshot())
            self.document.cancel_preview()
            self._semantic_drawing = None
            self.select_semantic_record("routes", identifier)
            self._set_status(f"Route {identifier} saved as real XYZ nodes and edges")
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            self._set_status(f"Route failed: {exc}")
        self._refresh()

    def select_semantic_record(self, collection, identifier):
        try:
            row = next(row for row in self.document.semantics[collection] if row["id"] == identifier)
            self._semantic_collection = collection
            self._selected_semantic_id = identifier
            self._semantic_record_model.as_string = json.dumps(row, ensure_ascii=False, indent=2)
            if row.get("level_id") is not None and getattr(self, "_semantic_drawing", None) is None:
                self.set_active_level(row["level_id"])
        except (AttributeError, KeyError, TypeError, StopIteration) as exc:
            self._set_status(f"Semantic selection failed: {exc}")

    def new_semantic_record(self, collection):
        level = getattr(self, "active_level_id", None)
        height = next((row["support_z_m"] for row in self.document.levels if row["id"] == level), 0.0)
        record = {"id": f"{collection}:new", "kind": "object", "label": "", "aliases": [], "properties": {}}
        if collection == "entities":
            record.update(region_id=None, level_id=level, position_xyz=None, source_ref=None)
        elif collection == "interaction_poses":
            record.update(kind="grasp", owner_id="REPLACE_WITH_ENTITY_OR_REGION_ID", level_id=level,
                position_xyz=[0.0, 0.0, height], heading_rad=None, node_id=None)
        elif collection == "regions":
            record.update(kind="room", level_id=level,
                polygon_xyz=[[0,0,height], [1,0,height], [1,1,height], [0,1,height]],
                height_m=2.5, entrance_node_ids=[])
        else:
            raise ValueError("unsupported record template")
        self._semantic_collection = collection
        self._selected_semantic_id = None
        self._semantic_record_model.as_string = json.dumps(record, ensure_ascii=False, indent=2)
        self._set_status("Fill stable ID, location and bindings, then Apply Record")

    def apply_semantic_record(self):
        if not self._require_stage_ready():
            return
        try:
            from .product import _unique_object, _reject_constant
            record = json.loads(self._semantic_record_model.as_string,
                object_pairs_hook=_unique_object, parse_constant=_reject_constant)
            selected = getattr(self, "_selected_semantic_id", None)
            if selected is not None and record.get("id") != selected:
                raise ValueError("existing stable ID cannot be renamed; create a new record and rebind references")
            identifier = self.document.upsert_semantic(self._semantic_collection, record)
            self._selected_semantic_id = identifier
            self._set_status(f"Semantic record {identifier} saved")
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            self._set_status(f"Semantic edit failed: {exc}")
        self._refresh()

    def delete_semantic_record(self):
        if not self._require_stage_ready():
            return
        try:
            self.document.remove_semantic(self._semantic_collection, self._selected_semantic_id)
            self._selected_semantic_id = None
            self._semantic_record_model.as_string = ""
            self._set_status("Record deleted; route nodes/edges remain editable")
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            self._set_status(f"Semantic deletion failed: {exc}")
        self._refresh()

    def semantic_preview(self):
        drawing = getattr(self, "_semantic_drawing", None)
        if not drawing:
            return ()
        points = deepcopy(drawing["points"])
        if drawing["mode"] == "region":
            return [*points, points[0]] if len(points) > 2 else points
        return [self.document.node(drawing["start_node_id"])["position_xyz"],
            *points, self.document.node(drawing["end_node_id"])["position_xyz"]]
