"""Web-editor selection overlay for Isaac Sim, planned as pure data.

The planner helpers (compact_document_snapshot, build_overlay_specs,
normalize_web_selection) have no Isaac/Kit import at module scope and run in
normal Python for unit tests, mirroring isaac_bridge.py.  WebOverlayRenderer
imports pxr lazily inside its methods so importing this module never requires
Isaac (same convention as viewport.py's drawing helpers).  All coordinates are
scene_world metres, Z-up; no conversion happens here.
"""
from __future__ import annotations

import json
import math

from .semantics import COLLECTIONS
from .viewport import _orient_z, _set_color

# Deliberately distinct from viewport.OVERLAY_ROOT so this overlay never
# collides with the native 3D authoring editor's own prims.
WEB_OVERLAY_ROOT = "/SemanticMapOverlay"

# Selection accent shared with viewport.py's node/edge highlight.
HIGHLIGHT_COLOR = (0.95, 0.06, 0.05)
NODE_COLOR = (0.08, 0.85, 0.18)
REGION_COLOR = (0.65, 0.25, 0.95)
ENTITY_COLOR = (0.95, 0.80, 0.15)
POSE_COLOR = (0.95, 0.2, 0.65)
GROUND_COLOR = (0.08, 0.85, 0.18)
AERIAL_COLOR = (0.05, 0.75, 0.95)
SEMANTIC_POINT_COLOR = (0.95, 0.75, 0.08)
REFERENCE_FACTOR = .45

WEB_SELECTION_KINDS = ("node", "edge", "semantic")
MAX_SNAPSHOT_BYTES = 1_048_576
MAX_NODE_ROWS = 5000
MAX_EDGE_ROWS = 5000
MAX_SEMANTIC_ROWS = 2000

_COLLECTION_CATEGORY = {"regions": "region", "entities": "entity",
                        "interaction_poses": "pose", "routes": "routes"}


def _copy_point(value):
    """Structural copy of an XYZ row; None when it is not a coordinate."""
    if not isinstance(value, (list, tuple)):
        return None
    try:
        return [float(coordinate) for coordinate in value]
    except (TypeError, ValueError):
        return None


def _finite_point(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return None
    try:
        point = [float(coordinate) for coordinate in value]
    except (TypeError, ValueError):
        return None
    return point if all(math.isfinite(coordinate) for coordinate in point) else None


def _finite_scalar(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def normalize_web_selection(document, selection):
    """Validate a browser selection payload against the live document.

    Returns a normalized {"kind", "collection", "id"} row, or None for a clear.
    Raises ValueError (English, matching map_server's error style) otherwise.
    """
    if selection is None:
        return None
    if not isinstance(selection, dict):
        raise ValueError("selection must be an object or null")
    kind = selection.get("kind")
    if kind not in WEB_SELECTION_KINDS:
        raise ValueError("selection kind must be node, edge or semantic")
    collection = selection.get("collection")
    if kind == "semantic":
        if collection not in COLLECTIONS:
            raise ValueError("selection collection must be regions, entities, "
                             "interaction_poses or routes")
    elif collection is not None:
        raise ValueError("selection collection must be null for node and edge selections")
    identifier = selection.get("id")
    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError("selection id must be a nonempty string")
    if kind == "node":
        rows = document.nodes
    elif kind == "edge":
        rows = document.edges
    else:
        # TopologyDocument.semantics may legitimately be None.
        rows = (document.semantics or {}).get(collection) or []
    if not any(row.get("id") == identifier for row in rows):
        raise ValueError(f"unknown web selection id: {identifier}")
    return {"kind": kind, "collection": collection, "id": identifier}


def _drop_stale_web_selection(document, web_selection):
    """Clear a stored web selection whose target id no longer exists."""
    selection = web_selection.get("selection")
    if selection is None:
        return False
    try:
        normalize_web_selection(document, selection)
    except ValueError:
        web_selection["selection"] = None
        return True
    return False


def _encoded_size(value):
    try:
        return len(json.dumps(value, ensure_ascii=False, allow_nan=False,
                              separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError):
        return MAX_SNAPSHOT_BYTES + 1


def compact_document_snapshot(document, revision):
    """Project a TopologyDocument onto the geometry fields the overlay draws.

    Strips profiles/properties/aliases and similar heavy fields; only drawing
    data, editable summary fields, and stable ids survive.  Pathological
    documents are capped (semantics first, then edges; nodes always stay) and
    flagged truncated.
    """
    nodes = []
    for row in document.nodes:
        node = {"id": row.get("id"), "position_xyz": _copy_point(row.get("position_xyz")),
                "level_id": row.get("level_id"), "label": row.get("label", ""),
                "kind": row.get("kind", "custom")}
        if row.get("edit_mode") is not None:
            node["edit_mode"] = row["edit_mode"]
        heading = row.get("heading_rad")
        if heading is not None:
            node["heading_rad"] = heading
        nodes.append(node)
    edges = [{"id": row.get("id"), "from": row.get("from"), "to": row.get("to"),
              "polyline_xyz": [_copy_point(point) for point in row.get("polyline_xyz") or []],
              "directed": bool(row.get("directed", False)),
              "traversal_mode": row.get("traversal_mode", "walk")}
             for row in document.edges]
    source = document.semantics or {}
    regions = [{"id": row.get("id"), "label": row.get("label", ""),
                "kind": row.get("kind", "room"),
                "polygon_xyz": [_copy_point(point) for point in row.get("polygon_xyz") or []],
                "height_m": row.get("height_m"), "level_id": row.get("level_id")}
               for row in source.get("regions") or []]
    entities = [{"id": row.get("id"), "position_xyz": _copy_point(row.get("position_xyz")),
                 "region_id": row.get("region_id"), "level_id": row.get("level_id")}
                for row in source.get("entities") or []]
    poses = []
    for row in source.get("interaction_poses") or []:
        pose = {"id": row.get("id"), "position_xyz": _copy_point(row.get("position_xyz")),
                "owner_id": row.get("owner_id"), "level_id": row.get("level_id")}
        heading = row.get("heading_rad")
        if heading is not None:
            pose["heading_rad"] = heading
        poses.append(pose)
    semantics = {"regions": regions, "entities": entities,
                 "interaction_poses": poses,
                 "routes": [{"id": row.get("id"), "node_ids": list(row.get("node_ids") or []),
                             "edge_ids": list(row.get("edge_ids") or [])}
                            for row in source.get("routes") or []]}
    truncated = False
    if len(nodes) > MAX_NODE_ROWS:
        nodes, truncated = nodes[:MAX_NODE_ROWS], True
    if len(edges) > MAX_EDGE_ROWS:
        edges, truncated = edges[:MAX_EDGE_ROWS], True
    for name, rows in semantics.items():
        if len(rows) > MAX_SEMANTIC_ROWS:
            semantics[name], truncated = rows[:MAX_SEMANTIC_ROWS], True
    snapshot = {"revision": revision, "version": getattr(document, "version", 1),
                "truncated": truncated, "nodes": nodes, "edges": edges,
                "semantics": semantics}
    if _encoded_size(snapshot) > MAX_SNAPSHOT_BYTES:
        snapshot["semantics"] = {name: [] for name in semantics}
        snapshot["truncated"] = True
        if _encoded_size(snapshot) > MAX_SNAPSHOT_BYTES:
            snapshot["edges"] = []
    return snapshot


def _arrow_spec(path, start, end, scale, color, category, key):
    """Cone spec mirroring viewport._draw_arrow's tip placement and sizing."""
    delta = [end[index] - start[index] for index in range(3)]
    length = math.sqrt(sum(value * value for value in delta))
    if length <= 1e-8:
        return None
    direction = [value / length for value in delta]
    tip = [end[index] - direction[index] * min(.35 * scale, length * .25)
           for index in range(3)]
    return {"path": path, "category": category, "key": key, "shape": "cone",
            "position": tip, "direction": direction,
            "length": .7 * scale, "radius": .32 * scale, "color": list(color)}


def _heading_arrow_spec(path, origin, heading, length, scale, color, category, key):
    end = [origin[0] + length * math.cos(heading),
           origin[1] + length * math.sin(heading), origin[2]]
    return _arrow_spec(path, origin, end, scale, color, category, key)


def build_overlay_specs(snapshot, *, active_level=None, layer="ground",
                        color=None, visible_types=None, reference=False):
    """Plan overlay prims as plain data (floats/lists/strs only, no Kit).

    Deterministic for a given snapshot: paths follow viewport.py's naming
    (Nodes/N%05d, Edges/E%05d, Regions/R%05d/Floor|Top|Side%d, entities/P%05d,
    interaction_poses/P%05d, Headings/H%05d, SemanticHeadings/...).  Sizes
    scale with the snapshot version exactly like viewport.py; semantic spheres
    stay .07 in both versions.  Routes produce no prims, only route_members.
    Invalid rows (non-finite/short coordinates) are skipped, never raised.
    """
    version = snapshot.get("version", 1)
    compact = version == 2
    node_radius = .06 if compact else .42
    line_width = .025 if compact else .16
    arrow_scale = .18 if compact else 1.0
    heading_length = .18 if compact else .5
    prims = []
    route_members = {}
    enabled = {"nodes": True, "edges": True, **(visible_types or {})}
    base_color = tuple(color or NODE_COLOR)
    layer_root = WEB_OVERLAY_ROOT
    visible_node_ids = {
        row.get("id") for row in snapshot.get("nodes") or []
        if active_level is None or row.get("level_id") in (None, active_level)
    }

    for index, row in enumerate(snapshot.get("nodes") or [] if enabled["nodes"] else []):
        if row.get("id") not in visible_node_ids:
            continue
        position = _finite_point(row.get("position_xyz"))
        if position is None:
            continue
        identifier = row.get("id")
        prims.append({"path": f"{layer_root}/Nodes/N{index:05d}",
                      "category": "node", "key": identifier, "shape": "sphere",
                      "position": position, "radius": node_radius,
                      "color": list(base_color)})
        heading = _finite_scalar(row.get("heading_rad"))
        if heading is not None:
            prims.append(_heading_arrow_spec(
                f"{layer_root}/Headings/H{index:05d}", position, heading,
                heading_length, arrow_scale, base_color, "node", identifier))

    for index, row in enumerate(snapshot.get("edges") or [] if enabled["edges"] else []):
        if (active_level is not None
                and (row.get("from") not in visible_node_ids
                     and row.get("to") not in visible_node_ids)):
            continue
        points = [_finite_point(point) for point in row.get("polyline_xyz") or []]
        if len(points) < 2 or any(point is None for point in points):
            continue
        identifier = row.get("id")
        path = f"{layer_root}/Edges/E{index:05d}"
        prims.append({"path": path, "category": "edge", "key": identifier,
                      "shape": "polyline", "points": points, "width": line_width,
                      "color": list(base_color)})
        segments = ([(path + "/Arrow", points[-2], points[-1])] if row.get("directed")
                   else [(path + "/ArrowA", points[-2], points[-1]),
                         (path + "/ArrowB", points[1], points[0])])
        for arrow_path, start, end in segments:
            spec = _arrow_spec(arrow_path, start, end, arrow_scale,
                               base_color, "edge", identifier)
            if spec is not None:
                prims.append(spec)

    semantics = snapshot.get("semantics") or {}
    for index, row in enumerate(semantics.get("regions") or []):
        if active_level is not None and row.get("level_id") not in (None, active_level):
            continue
        polygon = [_finite_point(point) for point in row.get("polygon_xyz") or []]
        height = _finite_scalar(row.get("height_m"))
        if height is None or height <= 0 or len(polygon) < 3 or any(
                point is None for point in polygon):
            continue
        identifier = row.get("id")
        path = f"{WEB_OVERLAY_ROOT}/Regions/R{index:05d}"
        bottom = [[point[0], point[1], point[2] + .035] for point in polygon]
        top = [[point[0], point[1], point[2] + height] for point in polygon]
        loops = [("Floor", [*bottom, bottom[0]]), ("Top", [*top, top[0]])]
        loops.extend((f"Side{vertex}", [bottom[vertex], top[vertex]])
                     for vertex in range(len(polygon)))
        for name, points in loops:
            prims.append({"path": f"{path}/{name}", "category": "region",
                          "key": identifier, "shape": "polyline", "points": points,
                          "width": line_width, "color": list(REGION_COLOR)})
    for collection, category, semantic_color in (
            ("entities", "entity", ENTITY_COLOR),
            ("interaction_poses", "pose", POSE_COLOR)):
        for index, row in enumerate(semantics.get(collection) or []):
            if active_level is not None and row.get("level_id") not in (None, active_level):
                continue
            position = _finite_point(row.get("position_xyz"))
            if position is None:
                continue
            identifier = row.get("id")
            prims.append({"path": f"{WEB_OVERLAY_ROOT}/{collection}/P{index:05d}",
                          "category": category, "key": identifier, "shape": "sphere",
                          "position": position, "radius": .07,
                          "color": list(semantic_color)})
            heading = _finite_scalar(row.get("heading_rad"))
            if heading is not None:
                prims.append(_heading_arrow_spec(
                    f"{WEB_OVERLAY_ROOT}/SemanticHeadings/{collection}{index:05d}",
                    position, heading, heading_length, arrow_scale,
                    semantic_color, category, identifier))
    for row in semantics.get("routes") or []:
        route_members[row.get("id")] = (
            [{"category": "node", "id": value} for value in row.get("node_ids") or []]
            + [{"category": "edge", "id": value} for value in row.get("edge_ids") or []])
    return {"root": WEB_OVERLAY_ROOT, "prims": prims, "route_members": route_members}


def build_unified_overlay_specs(document_snapshots, semantic_points, *,
                                active_layer="ground", active_level=None,
                                reference_level=None, visible_layers=None,
                                visible_types=None, binding_target_id=None,
                                binding_target_draft=None, binding_mode=False):
    """Plan all visible independent layers; reference layers are never hit targets."""
    visible_layers = {"ground": True, "aerial": True, "semantic_points": True,
                      **(visible_layers or {})}
    visible_types = {"nodes": True, "edges": True, "semantic_points": True,
                     **(visible_types or {})}
    if binding_mode:
        # Binding is a deliberate cross-layer inspection mode. Keep the
        # semantic layer active but expose both navigation graphs for the
        # highlighted/dimmed node overlay and native viewport picking.
        visible_layers["ground"] = True
        visible_layers["aerial"] = True
    prims = []
    for layer in ("ground", "aerial"):
        if not visible_layers[layer]:
            continue
        snapshot = (document_snapshots or {}).get(layer) or {}
        level = (active_level if active_layer == "ground" and layer == "ground"
                 else reference_level if layer == "ground" else None)
        planned = build_overlay_specs(
            snapshot, active_level=level, layer=layer,
            visible_types=visible_types)
        base = GROUND_COLOR if layer == "ground" else AERIAL_COLOR
        if layer != active_layer:
            base = tuple(value * REFERENCE_FACTOR for value in base)
        prefix = f"{WEB_OVERLAY_ROOT}/Layers/{'Ground' if layer == 'ground' else 'Aerial'}"
        for spec in planned["prims"]:
            # Unified native overlay contains graph primitives only. Legacy
            # semantic specs remain available to compatibility callers above.
            if spec.get("category") not in {"node", "edge"}:
                continue
            prims.append({**spec,
                          "path": spec["path"].replace(WEB_OVERLAY_ROOT, prefix, 1),
                          "category": f"{layer}:{spec['category']}",
                          "color": list(base)})
    if binding_mode and binding_target_id:
        bound = {(row.get("domain"), row.get("node_id"))
                 for row in binding_target_draft or []
                 if row.get("domain") in {"ground", "aerial"}
                 and isinstance(row.get("node_id"), str)}
        for spec in prims:
            if spec.get("category") not in {"ground:node", "aerial:node"}:
                continue
            layer, _category = spec["category"].split(":", 1)
            spec["color"] = ([1.0, .76, .25] if (layer, spec.get("key")) in bound
                             else [.025, .035, .045])
    if visible_layers["semantic_points"] and visible_types["semantic_points"]:
        color = SEMANTIC_POINT_COLOR
        if active_layer != "semantic_points":
            color = tuple(value * REFERENCE_FACTOR for value in color)
        for index, row in enumerate(semantic_points or []):
            position = _finite_point(row.get("position_xyz"))
            if position is None:
                continue
            prims.append({
                "path": f"{WEB_OVERLAY_ROOT}/Layers/SemanticPoints/P{index:05d}",
                "category": "semantic_point", "key": row.get("id"),
                "shape": "sphere", "position": position, "radius": .075,
                "color": list(color),
            })
    return {"root": WEB_OVERLAY_ROOT, "prims": prims, "route_members": {}}


class WebOverlayRenderer:
    """Draw and highlight the overlay in the stage's session layer only.

    Every mutation (rebuild, highlight, destroy) happens inside
    Usd.EditContext(stage, session_layer) so the author layers never get
    dirty.  Highlight repaints displayColor only; geometry and Kit's own
    selection state are untouched.  Main-thread use only, like the bridge.
    """

    def __init__(self, stage):
        self.stage = stage
        self._by_key = {}
        self._route_members = {}
        self._highlighted = frozenset()
        self._editor_state_token = None
        self._preview_hidden = set()

    def rebuild(self, specs):
        from pxr import Gf, Usd, UsdGeom
        stage = self.stage
        meters_per_unit = float(UsdGeom.GetStageMetersPerUnit(stage))
        if not math.isfinite(meters_per_unit) or meters_per_unit <= 0:
            raise ValueError("USD metersPerUnit must be positive and finite")
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            root = stage.GetPrimAtPath(WEB_OVERLAY_ROOT)
            if root.IsValid():
                stage.RemovePrim(WEB_OVERLAY_ROOT)
            root_xform = UsdGeom.Xform.Define(stage, WEB_OVERLAY_ROOT)
            if meters_per_unit != 1.0:
                factor = 1.0 / meters_per_unit
                root_xform.AddScaleOp().Set(Gf.Vec3d(factor, factor, factor))
            self._by_key = {}
            for spec in specs.get("prims", ()):
                self._define(spec)
        self._route_members = specs.get("route_members") or {}
        # Fresh prims carry base colors only; the caller replays highlight.
        self._highlighted = frozenset()
        self._editor_state_token = None
        self._preview_hidden = set()

    def _define(self, spec):
        from pxr import Gf, UsdGeom
        path = spec["path"]
        if spec["shape"] == "sphere":
            schema = UsdGeom.Sphere.Define(self.stage, path)
            schema.CreateRadiusAttr().Set(float(spec["radius"]))
            schema.AddTranslateOp().Set(Gf.Vec3d(*spec["position"]))
        elif spec["shape"] == "polyline":
            schema = UsdGeom.BasisCurves.Define(self.stage, path)
            schema.CreateTypeAttr().Set(UsdGeom.Tokens.linear)
            schema.CreateCurveVertexCountsAttr().Set([len(spec["points"])])
            schema.CreatePointsAttr().Set([Gf.Vec3f(*point) for point in spec["points"]])
            schema.CreateWidthsAttr().Set([float(spec["width"])])
            set_interpolation = getattr(schema, "SetWidthsInterpolation", None)
            if callable(set_interpolation):
                set_interpolation(getattr(UsdGeom.Tokens, "constant", "constant"))
        elif spec["shape"] == "cone":
            schema = UsdGeom.Cone.Define(self.stage, path)
            schema.CreateRadiusAttr().Set(float(spec["radius"]))
            schema.CreateHeightAttr().Set(float(spec["length"]))
            schema.AddTranslateOp().Set(Gf.Vec3d(*spec["position"]))
            _orient_z(schema.GetPrim(), spec["direction"])
        else:
            return
        prim = schema.GetPrim()
        prim.SetCustomDataByKey("semantic_id", spec.get("key"))
        _set_color(prim, spec["color"])
        category, key = spec.get("category"), spec.get("key")
        if category and key:
            self._by_key.setdefault((category, key), []).append(
                (path, tuple(spec["color"])))

    def highlight(self, web_selection, active_layer=None):
        """Repaint only displayColor for the selection's prims (None clears)."""
        from pxr import Usd
        target = self._selection_keys(web_selection, active_layer=active_layer)
        if target and not any(key in self._by_key for key in target):
            # Unknown ids (truncated or deleted rows): a safe no-op that keeps
            # the current paint instead of silently dropping it.
            return
        restore, apply = self._highlighted - target, target - self._highlighted
        if not restore and not apply:
            return
        stage = self.stage
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            for key in restore:
                for path, base_color in self._by_key.get(key, ()):
                    self._paint(path, base_color)
            for key in apply:
                for path, _base_color in self._by_key.get(key, ()):
                    self._paint(path, HIGHLIGHT_COLOR)
        self._highlighted = target

    def _paint(self, path, color):
        prim = self.stage.GetPrimAtPath(path)
        if prim is None or not prim.IsValid():
            return
        _set_color(prim, color)

    def _selection_keys(self, web_selection, active_layer=None):
        if not web_selection:
            return frozenset()
        kind = web_selection.get("kind")
        identifier = web_selection.get("id")
        if kind == "node":
            category = f"{active_layer}:node" if active_layer else "node"
            return frozenset({(category, identifier)})
        if kind == "edge":
            category = f"{active_layer}:edge" if active_layer else "edge"
            return frozenset({(category, identifier)})
        if kind == "semantic_point":
            return frozenset({("semantic_point", identifier)})
        if kind == "semantic":
            collection = web_selection.get("collection")
            if collection == "routes":
                members = self._route_members.get(identifier) or []
                return frozenset({(member.get("category"), member.get("id"))
                                  for member in members})
            category = _COLLECTION_CATEGORY.get(collection)
            return frozenset({(category, identifier)}) if category else frozenset()
        return frozenset()

    def editor_state(self, *, snapshot, domain, selection=None, preview=None,
                     semantic_points=(), active_layer=None, selection_items=()):
        """Draw transient active-layer point/edge previews in the session layer."""
        if self.stage is None:
            return
        layer = active_layer or domain
        token = json.dumps(
            {"snapshot_revision": snapshot.get("revision"), "domain": domain,
             "selection": selection, "selection_items": selection_items, "preview": preview,
             "semantic_points": semantic_points, "active_layer": layer},
            sort_keys=True, separators=(",", ":"), allow_nan=False)
        if token == getattr(self, "_editor_state_token", None):
            return
        self._editor_state_token = token
        from pxr import Usd, UsdGeom
        root_path = f"{WEB_OVERLAY_ROOT}/Editor"
        stage = self.stage
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            root = stage.GetPrimAtPath(root_path)
            if root.IsValid():
                stage.RemovePrim(root_path)
            UsdGeom.Xform.Define(stage, root_path)
            nodes = {row.get("id"): row for row in snapshot.get("nodes") or []}
            selected_items = list(selection_items or [])
            if not selected_items and selection:
                selected_items = [selection]
            semantic_by_id = {row.get("id"): row for row in semantic_points or []}
            preview_geometry = {"version": snapshot.get("version", 2),
                                "nodes": [], "edges": []}
            hidden_keys = set()
            if preview and preview.get("kind") in {"node", "semantic-point"}:
                identifier = preview.get("id")
                if preview["kind"] == "node":
                    hidden_keys.add((f"{layer}:node", identifier))
                    for edge in snapshot.get("edges") or []:
                        if identifier not in (edge.get("from"), edge.get("to")):
                            continue
                        points = [list(point) for point in edge.get("polyline_xyz") or []]
                        if len(points) < 2:
                            continue
                        if edge.get("from") == identifier:
                            points[0] = list(preview["position_xyz"])
                        if edge.get("to") == identifier:
                            points[-1] = list(preview["position_xyz"])
                        preview_geometry["edges"].append({**edge, "polyline_xyz": points})
                        hidden_keys.add((f"{layer}:edge", edge.get("id")))
                else:
                    hidden_keys.add(("semantic_point", identifier))
            elif preview and preview.get("kind") == "edge":
                moved_nodes = {node.get("id"): list(node.get("position_xyz") or [])
                               for node in preview.get("associated_nodes") or []}
                for node in preview.get("associated_nodes") or []:
                    hidden_keys.add((f"{layer}:node", node.get("id")))
                    preview_geometry["nodes"].append({
                        "id": node.get("id"), "position_xyz": node.get("position_xyz"),
                        "level_id": None,
                    })
                for edge in snapshot.get("edges") or []:
                    points = [list(point) for point in edge.get("polyline_xyz") or []]
                    if edge.get("id") == preview.get("id"):
                        points = [list(point) for point in preview.get("points") or []]
                    else:
                        touched = False
                        if points and edge.get("from") in moved_nodes:
                            points[0] = moved_nodes[edge.get("from")]
                            touched = True
                        if points and edge.get("to") in moved_nodes:
                            points[-1] = moved_nodes[edge.get("to")]
                            touched = True
                        if not touched:
                            continue
                    hidden_keys.add((f"{layer}:edge", edge.get("id")))
                    preview_geometry["edges"].append({**edge, "polyline_xyz": points})
            self._preview_visibility(hidden_keys)
            for spec in build_overlay_specs(preview_geometry, layer=layer)["prims"]:
                self._define({**spec,
                              "path": spec["path"].replace(
                                  WEB_OVERLAY_ROOT, f"{root_path}/Geometry", 1),
                              "color": list(HIGHLIGHT_COLOR),
                              "category": None, "key": None})
            if preview and preview.get("kind") == "edge-draft":
                points = [_finite_point(point) for point in preview.get("points") or []]
                if len(points) >= 2 and all(point is not None for point in points):
                    self._define({
                        "path": f"{root_path}/EdgeDraft", "shape": "polyline",
                        "points": points, "width": .03,
                        "color": list(HIGHLIGHT_COLOR), "category": None, "key": None,
                    })
                    if preview.get("directed"):
                        arrow = _arrow_spec(
                            f"{root_path}/EdgeDraftArrow", points[-2], points[-1],
                            .16, HIGHLIGHT_COLOR, None, None)
                        if arrow is not None:
                            self._define(arrow)
            if selection and selection.get("kind") in {"node", "semantic_point"}:
                kind = selection.get("kind")
                record = (nodes.get(selection.get("id")) if kind == "node"
                          else semantic_by_id.get(selection.get("id")))
                if record is not None:
                    position = list(record["position_xyz"])
                    preview_kind = "node" if kind == "node" else "semantic-point"
                    if preview and preview.get("kind") == preview_kind and preview.get("id") == record.get("id"):
                        position = list(preview["position_xyz"])
                    self._define({
                        "path": f"{root_path}/SelectedPoint", "shape": "sphere",
                        "position": position, "radius": .085,
                        "color": list(HIGHLIGHT_COLOR), "category": None, "key": None,
                    })
                    if record.get("edit_mode", "3d") == "3d":
                        for axis, delta, color in (
                            ("X", (.45, 0, 0), (1.0, .15, .12)),
                            ("Y", (0, .45, 0), (.18, 1.0, .25)),
                            ("Z", (0, 0, .45), (.2, .5, 1.0)),
                        ):
                            endpoint = [position[index] + delta[index] for index in range(3)]
                            self._define({
                                "path": f"{root_path}/Gizmo/{axis}", "shape": "polyline",
                                "points": [position, endpoint], "width": .018,
                                "color": list(color), "category": None, "key": None,
                            })
                            cone = _arrow_spec(
                                f"{root_path}/Gizmo/{axis}Tip", position, endpoint,
                                .12, color, None, None)
                            if cone is not None:
                                self._define(cone)
            for item in selected_items:
                kind, identifier = item.get("kind"), item.get("id")
                record = nodes.get(identifier) if kind == "node" else semantic_by_id.get(identifier)
                if kind == "edge":
                    edge = next((row for row in snapshot.get("edges") or [] if row.get("id") == identifier), None)
                    if edge:
                        points = [_finite_point(point) for point in edge.get("polyline_xyz") or []]
                        if len(points) >= 2 and all(point is not None for point in points):
                            self._define({"path": f"{root_path}/SelectedEdge/{identifier.replace(':', '_')}", "shape": "polyline", "points": points, "width": .045, "color": list(HIGHLIGHT_COLOR), "category": None, "key": None})
                elif record is not None:
                    position = _finite_point(record.get("position_xyz"))
                    if position is not None:
                        self._define({"path": f"{root_path}/SelectedItems/{identifier.replace(':', '_')}", "shape": "sphere", "position": position, "radius": .095, "color": list(HIGHLIGHT_COLOR), "category": None, "key": None})

    def _preview_visibility(self, hidden_keys):
        """Replace only the previewed overlay geometry and restore it on cancel."""
        from pxr import UsdGeom
        for key in self._preview_hidden | hidden_keys:
            visibility = (UsdGeom.Tokens.invisible if key in hidden_keys
                          else UsdGeom.Tokens.inherited)
            entries = self._by_key.get(key)
            if entries is None and isinstance(key[0], str) and ":" in key[0]:
                entries = self._by_key.get((key[0].split(":", 1)[1], key[1]))
            for path, _color in entries or ():
                prim = self.stage.GetPrimAtPath(path)
                if prim.IsValid():
                    UsdGeom.Imageable(prim).CreateVisibilityAttr().Set(visibility)
        self._preview_hidden = hidden_keys

    def destroy(self):
        if self.stage is None:
            return
        from pxr import Usd
        stage = self.stage
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            root = stage.GetPrimAtPath(WEB_OVERLAY_ROOT)
            if root.IsValid():
                stage.RemovePrim(WEB_OVERLAY_ROOT)
        self._by_key = {}
        self._route_members = {}
        self._highlighted = frozenset()
        self._editor_state_token = None
        self._preview_hidden = set()


__all__ = ["WEB_OVERLAY_ROOT", "HIGHLIGHT_COLOR", "WebOverlayRenderer",
           "build_overlay_specs", "build_unified_overlay_specs",
           "compact_document_snapshot",
           "normalize_web_selection"]
