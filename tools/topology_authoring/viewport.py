"""Isaac Sim viewport picking and topology overlay."""
from __future__ import annotations
import math
from typing import Any, Callable

OVERLAY_ROOT = "/World/TopologyAuthoringOverlay"


def visible_node_ids(document, active_level_id=None) -> set[str]:
    """Return nodes shown/pickable for one authoring level.

    Version 1 and all-level mode show the complete graph.  A level view also
    exposes unassigned transition nodes directly connected to that level so a
    stair/ramp hand-off can be edited from either side.
    """
    nodes = list(document.nodes)
    if getattr(document, "version", 1) != 2 or active_level_id is None:
        return {node["id"] for node in nodes}
    visible = {
        node["id"] for node in nodes if node.get("level_id") == active_level_id
    }
    adjacent = set(visible)
    for edge in document.edges:
        if edge.get("from") in visible:
            adjacent.add(edge.get("to"))
        if edge.get("to") in visible:
            adjacent.add(edge.get("from"))
    visible.update(
        node["id"] for node in nodes
        if node["id"] in adjacent and node.get("kind") == "transition"
        and node.get("level_id") is None
    )
    return visible


def visible_edges(document, active_level_id=None):
    node_ids = visible_node_ids(document, active_level_id)
    if getattr(document, "version", 1) != 2 or active_level_id is None:
        return list(document.edges)
    # Cross-level edges remain visible from either endpoint level.  This makes
    # a stair/ramp connection inspectable without exposing unrelated floors.
    return [edge for edge in document.edges
            if edge.get("from") in node_ids or edge.get("to") in node_ids]

def project_click(viewport_api, pixel, callback):
    if viewport_api is None: raise RuntimeError("viewport API is unavailable")
    viewport_api.request_query(pixel, callback, query_name="eai.topology_authoring.pick")

def visible_semantics(document, active_level_id=None):
    """Filter annotations by explicit or owner/region-inherited floor."""
    from copy import deepcopy
    from .semantics import empty_semantics
    data = document.semantics or empty_semantics()
    owners = {row["id"]: row for name in ("regions", "entities") for row in data[name]}
    def level(row, seen=()):
        if row.get("level_id") is not None:
            return row["level_id"]
        ref = row.get("owner_id") or row.get("region_id")
        if ref in owners and ref not in seen:
            return level(owners[ref], (*seen, ref))
        return None
    return {name: [deepcopy(row) for row in data[name]
                   if active_level_id is None or level(row) in (None, active_level_id)]
            for name in ("regions", "entities", "interaction_poses")}

def draw_overlay(stage, document, *, active_level_id=None, route_points_m=(),
                 route_node_ids=(), route_headings_rad=(), semantic_preview=()):
    del stage
    node_ids = visible_node_ids(document, active_level_id)
    return {"nodes": [dict(id=n["id"], position=n["position_xyz"], locked=n.get("locked", False),
                           level_id=n.get("level_id"), label=n.get("label", ""),
                           aliases=list(n.get("aliases", ())))
                      for n in document.nodes if n["id"] in node_ids],
            "edges": [dict(id=e["id"], points=e["polyline_xyz"], directed=e.get("directed", False),
                           locked=e.get("locked_geometry", False),
                           kind=e.get("kind", "custom_connection"))
                      for e in visible_edges(document, active_level_id)],
            "pending_node": getattr(document.pending_node, "position_xyz", None),
            "node_headings_rad": {n["id"]: n.get("heading_rad") for n in document.nodes
                                  if n.get("heading_rad") is not None and n["id"] in node_ids},
            "pending_heading_rad": getattr(document.pending_node, "heading_rad", None),
            "pending_edge": document.pending_edge.preview_polyline_xyz if getattr(document, "pending_edge", None) else None,
            "selected_node": getattr(document, "selected_node_id", None),
            "selected_edge": getattr(document, "selected_edge_id", None),
            "active_level_id": active_level_id,
            "route_points_m": [list(point) for point in route_points_m],
            "route_node_ids": list(route_node_ids),
            "route_headings_rad": list(route_headings_rad),
            "semantics": visible_semantics(document, active_level_id),
            "semantic_preview": [list(point) for point in semantic_preview]}

def _set_color(prim, value):
    from pxr import Gf, UsdGeom
    UsdGeom.Gprim(prim).CreateDisplayColorAttr().Set([Gf.Vec3f(*value)])

def _orient_z(prim, direction):
    from pxr import Gf, UsdGeom
    d=Gf.Vec3d(*direction)
    if d.GetLength() <= 1e-9: return
    d.Normalize()
    quatd = Gf.Rotation(Gf.Vec3d(0,0,1), d).GetQuat()
    # Cone orientation is a quaternion transform.  SetRotate expects Euler
    # angles, so passing quaternion components there produces incorrect
    # arrowheads in current OpenUSD builds.
    # Cone orient ops are authored as GfQuatf in this USD build.  Rotation
    # from Vec3d returns GfQuatd, so convert explicitly before setting the
    # attribute instead of relying on an implicit USD type conversion.
    imaginary = quatd.GetImaginary()
    quatf = Gf.Quatf(
        float(quatd.GetReal()),
        Gf.Vec3f(
            float(imaginary[0]), float(imaginary[1]), float(imaginary[2])
        ),
    )
    UsdGeom.Xformable(prim).AddOrientOp().Set(quatf)

def _current_stage():
    try:
        import omni.usd
    except ImportError:
        return None
    return omni.usd.get_context().get_stage()

class TopologyOverlay:
    def __init__(self, document):
        self.document=document; self.stage=_current_stage()
        self.active_level_id = None
        self.route_points_m = ()
        self.route_node_ids = ()
        self.route_headings_rad = ()
        self.semantic_points = ()
    def set_semantic_preview(self, points):
        self.semantic_points = tuple(tuple(point) for point in points)
    def set_active_level(self, level_id):
        self.active_level_id = level_id
    def set_route_preview(self, points, node_ids=(), headings=()):
        self.route_points_m = tuple(tuple(float(value) for value in point) for point in points)
        self.route_node_ids = tuple(node_ids)
        self.route_headings_rad = tuple(headings)
    def _remove_from_stage(self):
        if self.stage is None:
            return
        prim=self.stage.GetPrimAtPath(OVERLAY_ROOT)
        if prim.IsValid(): self.stage.RemovePrim(OVERLAY_ROOT)
    def rebind_stage(self):
        stage = _current_stage()
        if stage is self.stage:
            return
        self._remove_from_stage()
        self.stage = stage
    def rebuild(self):
        if self.stage is None: return
        from pxr import Gf, UsdGeom
        root=self.stage.GetPrimAtPath(OVERLAY_ROOT)
        if root.IsValid(): self.stage.RemovePrim(OVERLAY_ROOT)
        UsdGeom.Xform.Define(self.stage, OVERLAY_ROOT)
        annotations = visible_semantics(self.document, self.active_level_id)
        for index, region in enumerate(annotations["regions"]):
            path = f"{OVERLAY_ROOT}/Regions/R{index:05d}"
            points = region["polygon_xyz"]
            bottom = [[p[0], p[1], p[2]+.035] for p in points]
            top = [[p[0], p[1], p[2]+region["height_m"]] for p in points]
            self._draw_line(path+"/Floor", [*bottom, bottom[0]], (.65, .25, .95))
            self._draw_line(path+"/Top", [*top, top[0]], (.65, .25, .95))
            for vertex, (a, b) in enumerate(zip(bottom, top)):
                self._draw_line(path+f"/Side{vertex}", [a, b], (.65, .25, .95))
        for collection, color in (("entities", (.95, .80, .15)), ("interaction_poses", (.95, .2, .65))):
            for index, row in enumerate(annotations[collection]):
                point = row.get("position_xyz")
                if point is None:
                    continue
                path = f"{OVERLAY_ROOT}/{collection}/P{index:05d}"
                sphere = UsdGeom.Sphere.Define(self.stage, path)
                sphere.CreateRadiusAttr().Set(.07)
                sphere.AddTranslateOp().Set(Gf.Vec3d(*point))
                sphere.GetPrim().SetCustomDataByKey("semantic_id", row["id"])
                _set_color(sphere.GetPrim(), color)
                if row.get("heading_rad") is not None:
                    self._draw_heading_arrow(f"{OVERLAY_ROOT}/SemanticHeadings/{collection}{index:05d}",
                                             point, row["heading_rad"], color)
        if len(getattr(self, "semantic_points", ())) > 1:
            self._draw_line(f"{OVERLAY_ROOT}/Preview/Semantics", self.semantic_points, (.95, .1, .1))
        visible_ids = visible_node_ids(self.document, self.active_level_id)
        for i,node in enumerate(self.document.nodes):
            if node["id"] not in visible_ids:
                continue
            sphere=UsdGeom.Sphere.Define(self.stage, f"{OVERLAY_ROOT}/Nodes/N{i:05d}")
            sphere.CreateRadiusAttr().Set(.06 if self.document.version == 2 else .42); sphere.AddTranslateOp().Set(Gf.Vec3d(*node["position_xyz"]))
            color = (.95, .06, .05) if node["id"] == self.document.selected_node_id else (.08, .85, .18)
            _set_color(sphere.GetPrim(), color)
            if node.get("heading_rad") is not None:
                self._draw_heading_arrow(
                    f"{OVERLAY_ROOT}/Headings/H{i:05d}",
                    node["position_xyz"], node["heading_rad"], color,
                )
        for i,edge in enumerate(visible_edges(self.document, self.active_level_id)):
            color = (.95, .06, .05) if edge["id"] == self.document.selected_edge_id else (.08, .85, .18)
            self._draw_edge(f"{OVERLAY_ROOT}/Edges/E{i:05d}",edge["polyline_xyz"],edge.get("directed",False),color)
        if self.document.pending_node:
            sphere=UsdGeom.Sphere.Define(self.stage,f"{OVERLAY_ROOT}/Preview/Node"); sphere.CreateRadiusAttr().Set(.075 if self.document.version == 2 else .5); sphere.AddTranslateOp().Set(Gf.Vec3d(*self.document.pending_node.position_xyz)); _set_color(sphere.GetPrim(),(.95,.06,.05))
            if getattr(self.document.pending_node, "heading_rad", None) is not None:
                self._draw_heading_arrow(f"{OVERLAY_ROOT}/Preview/Heading", self.document.pending_node.position_xyz, self.document.pending_node.heading_rad, (.95,.06,.05))
        if self.document.pending_edge and self.document.pending_edge.preview_polyline_xyz:
            self._draw_edge(f"{OVERLAY_ROOT}/Preview/Edge",self.document.pending_edge.preview_polyline_xyz,self.document.pending_edge.directed,(.95,.06,.05))
        if len(self.route_points_m) >= 2:
            if len(self.route_node_ids) == len(self.route_points_m):
                for index, (start, end) in enumerate(zip(self.route_points_m, self.route_points_m[1:])):
                    if (self.active_level_id is None or self.route_node_ids[index] in visible_ids
                            or self.route_node_ids[index + 1] in visible_ids):
                        self._draw_edge(f"{OVERLAY_ROOT}/Route/S{index:05d}", [start, end], True,
                                        (.05, .85, .95))
            elif self.active_level_id is None:
                self._draw_edge(f"{OVERLAY_ROOT}/Route/Path", self.route_points_m, True,
                                (.05, .85, .95))
        if self.route_headings_rad and len(self.route_headings_rad) == len(self.route_points_m):
            terminal = self.route_headings_rad[-1]
            target_visible = (self.active_level_id is None or
                              (len(self.route_node_ids) == len(self.route_points_m) and
                               self.route_node_ids[-1] in visible_ids))
            if terminal is not None and target_visible:
                self._draw_heading_arrow(f"{OVERLAY_ROOT}/Route/TargetHeading", self.route_points_m[-1], terminal, (.05, .85, .95))
    def _draw_line(self,path,points,color):
        from pxr import Gf, UsdGeom
        curve=UsdGeom.BasisCurves.Define(self.stage,path); curve.CreateTypeAttr().Set(UsdGeom.Tokens.linear); curve.CreateCurveVertexCountsAttr().Set([len(points)]); curve.CreatePointsAttr().Set([Gf.Vec3f(*p) for p in points]); curve.CreateWidthsAttr().Set([.025 if self.document.version == 2 else .16]); _set_color(curve.GetPrim(),color)
    def _draw_edge(self,path,points,directed,color):
        self._draw_line(path,points,color)
        if len(points)>=2:
            if directed: self._draw_arrow(path+"/Arrow",points[-2],points[-1],color)
            else: self._draw_arrow(path+"/ArrowA",points[-2],points[-1],color); self._draw_arrow(path+"/ArrowB",points[1],points[0],color)
    def _draw_arrow(self,path,start,end,color):
        from pxr import Gf, UsdGeom
        a,b=Gf.Vec3d(*start),Gf.Vec3d(*end); d=b-a; length=d.GetLength()
        if length<=1e-8:return
        scale = .18 if self.document.version == 2 else 1.0
        d.Normalize(); tip=b-d*min(.35*scale,length*.25); cone=UsdGeom.Cone.Define(self.stage,path); cone.CreateRadiusAttr().Set(.32*scale); cone.CreateHeightAttr().Set(.7*scale); cone.AddTranslateOp().Set(tip); _orient_z(cone.GetPrim(),d); _set_color(cone.GetPrim(),color)
    def _draw_heading_arrow(self, path, origin, heading, color):
        length = .18 if self.document.version == 2 else .5
        end = [origin[0] + length * math.cos(heading), origin[1] + length * math.sin(heading), origin[2]]
        self._draw_arrow(path, origin, end, color)
    def destroy(self):
        self._remove_from_stage()
        self.stage = None

class _TopologyClickGesture:
    """Ray-cast a viewport click and forward its world position."""

    def __init__(self, viewport_api: Any, callback: Callable, mouse_button: int):
        from omni.ui import scene as sc

        class _Gesture(sc.ClickGesture):
            def __init__(self, owner):
                super().__init__(mouse_button=mouse_button)
                self.owner = owner

            def on_ended(self, *_args):
                if self.state == sc.GestureState.CANCELED:
                    return
                if mouse_button == 1:
                    self.owner.callback(None, None)
                    return
                if self.owner.viewport_api is None:
                    return
                mouse_ndc = self.sender.gesture_payload.mouse
                mouse_pixel, query_viewport = self.owner.viewport_api.map_ndc_to_texture_pixel(
                    mouse_ndc
                )
                if mouse_pixel is None or query_viewport is None:
                    return
                from omni.kit.viewport.window.raycast import perform_raycast_query

                perform_raycast_query(
                    viewport_api=query_viewport,
                    mouse_ndc=mouse_ndc,
                    mouse_pixel=mouse_pixel,
                    on_complete_fn=self.owner.callback,
                    query_name="EAI topology authoring click",
                )

        self.viewport_api = viewport_api
        self.callback = callback
        self.gesture = _Gesture(self)

    def destroy(self):
        gesture = self.gesture
        self.gesture = None
        if gesture is not None:
            gesture.owner = None
        self.viewport_api = None
        self.callback = None


class _TopologyManipulator:
    def __init__(self, viewport_api: Any, on_left: Callable, on_right: Callable):
        from omni.ui import scene as sc

        class _Manipulator(sc.Manipulator):
            def __init__(self, owner):
                super().__init__()
                self.owner = owner
                self.root = None

            def on_build(self):
                self.root = sc.Transform()
                self.owner._destroy_gestures()
                self.owner._left_gesture = _TopologyClickGesture(
                    self.owner.viewport_api, self.owner.on_left, 0
                )
                self.owner._right_gesture = _TopologyClickGesture(
                    self.owner.viewport_api, self.owner.on_right, 1
                ) if self.owner.on_right is not None else None
                with self.root:
                    self.left_screen = sc.Screen(
                        gesture=self.owner._left_gesture.gesture
                    )
                    self.right_screen = sc.Screen(
                        gesture=self.owner._right_gesture.gesture
                    ) if self.owner._right_gesture is not None else None

            def destroy(self):
                self.clear()
                self.left_screen = None
                self.right_screen = None
                self.root = None

        self.viewport_api = viewport_api
        self.on_left = on_left
        self.on_right = on_right
        self._left_gesture = None
        self._right_gesture = None
        self._manipulator = _Manipulator(self)

    def _destroy_gestures(self):
        for name in ("_left_gesture", "_right_gesture"):
            gesture = getattr(self, name, None)
            setattr(self, name, None)
            if gesture is not None:
                gesture.destroy()

    @property
    def visible(self):
        return bool(self._manipulator.visible)

    @visible.setter
    def visible(self, value):
        self._manipulator.visible = bool(value)

    def refresh(self):
        self._manipulator.invalidate()

    def destroy(self):
        if self._manipulator is not None:
            self._manipulator.destroy()
            self._manipulator.owner = None
            self._manipulator = None
        self._destroy_gestures()
        self.viewport_api = None
        self.on_left = None
        self.on_right = None


class _TopologyViewportScene:
    def __init__(self, viewport_desc: Any, on_left: Callable, on_right: Callable):
        viewport_api = (
            viewport_desc.get("viewport_api")
            if hasattr(viewport_desc, "get")
            else getattr(viewport_desc, "viewport_api", None)
        )
        self._owner = _TopologyManipulator(viewport_api, on_left, on_right)
        self._manipulator = self._owner._manipulator

    @property
    def visible(self):
        return bool(self._manipulator and self._manipulator.visible)

    @visible.setter
    def visible(self, value):
        if self._manipulator is not None:
            self._manipulator.visible = bool(value)

    def destroy(self):
        if self._manipulator is not None:
            self._owner.destroy()
            self._manipulator = None
        self._owner = None


class ViewportOverlay:
    def __init__(self, document, on_left: Callable, on_right: Callable):
        self.document = document
        self.overlay = TopologyOverlay(document)
        self._on_left_callback = on_left
        self._on_right_callback = on_right
        self._registration = None
        try:
            from omni.kit.viewport.registry import RegisterScene

            self._registration = RegisterScene(
                lambda desc: _TopologyViewportScene(
                    desc, self._on_left, self._on_right
                ),
                "eai.topology_authoring.interaction",
            )
        except (ImportError, AttributeError, RuntimeError, TypeError):
            # The model and product generator remain usable without Isaac.
            self._registration = None
        self.refresh()

    def _on_left(self, prim_path, world_position, *_args):
        if not prim_path or world_position is None:
            return
        try:
            point = tuple(float(world_position[index]) for index in range(3))
        except (IndexError, TypeError, ValueError):
            return
        if not all(math.isfinite(value) for value in point):
            return
        self._on_left_callback(point, prim_path)

    def _on_right(self, *_args):
        self._on_right_callback()

    def refresh(self):
        self.overlay.rebuild()

    def set_active_level(self, level_id):
        self.overlay.set_active_level(level_id)
        self.refresh()

    def set_route_preview(self, points, node_ids=(), headings=()):
        self.overlay.set_route_preview(points, node_ids, headings)
        self.refresh()

    def set_semantic_preview(self, points):
        self.overlay.set_semantic_preview(points)

    def rebind_stage(self):
        self.overlay.rebind_stage()

    def destroy(self):
        # Releasing the registration removes scene instances from viewports;
        # the USD overlay is explicitly removed from the authoring stage.
        registration = self._registration
        self._registration = None
        overlay = self.overlay
        self.overlay = None
        try:
            if registration is not None:
                registration.destroy()
        finally:
            try:
                if overlay is not None:
                    overlay.destroy()
            finally:
                self._on_left_callback = None
                self._on_right_callback = None
                self.document = None


def ray_plane_intersection(origin, direction, plane_z, *, epsilon=1e-9):
    """Intersect a world ray with a horizontal editing plane."""
    origin = tuple(float(value) for value in origin)
    direction = tuple(float(value) for value in direction)
    if len(origin) != 3 or len(direction) != 3 or not all(
            math.isfinite(value) for value in (*origin, *direction, float(plane_z))):
        return None
    if abs(direction[2]) <= epsilon:
        return None
    distance = (float(plane_z) - origin[2]) / direction[2]
    if distance < 0:
        return None
    return tuple(origin[index] + distance * direction[index] for index in range(3))


def ray_axis_intersection(origin, direction, axis_origin, axis):
    """Return the closest point on an XYZ gizmo axis to a world ray."""
    origin = tuple(float(value) for value in origin)
    direction = tuple(float(value) for value in direction)
    axis_origin = tuple(float(value) for value in axis_origin)
    axis_vector = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0),
                   "z": (0.0, 0.0, 1.0)}.get(axis)
    if axis_vector is None:
        return None
    dot = sum(direction[i] * axis_vector[i] for i in range(3))
    ray_norm = sum(value * value for value in direction)
    denominator = ray_norm - dot * dot
    if abs(denominator) <= 1e-10:
        return None
    delta = tuple(axis_origin[i] - origin[i] for i in range(3))
    ray_parameter = (sum(delta[i] * direction[i] for i in range(3))
                     - sum(delta[i] * axis_vector[i] for i in range(3)) * dot) / denominator
    axis_parameter = sum((origin[i] + ray_parameter * direction[i] - axis_origin[i])
                         * axis_vector[i] for i in range(3))
    return tuple(axis_origin[i] + axis_parameter * axis_vector[i] for i in range(3))


def _matrix_transform(transform, point):
    if callable(transform):
        return transform(point)
    method = getattr(transform, "Transform", None)
    if callable(method):
        try:
            from pxr import Gf
            return method(Gf.Vec3d(*point))
        except ImportError:
            return method(point)
    raise TypeError("viewport transform is not callable")


def viewport_ray(viewport_api, mouse_ndc, *, meters_per_unit=1.0):
    """Build a metric world ray from a scene-gesture NDC coordinate."""
    ndc_to_world = getattr(viewport_api, "ndc_to_world", None)
    if ndc_to_world is None:
        world_to_ndc = getattr(viewport_api, "world_to_ndc", None)
        inverse = getattr(world_to_ndc, "GetInverse", None)
        if callable(inverse):
            ndc_to_world = inverse()
    if ndc_to_world is None:
        return None
    mouse = (float(mouse_ndc[0]), float(mouse_ndc[1]))
    near = _matrix_transform(ndc_to_world, (mouse[0], mouse[1], -1.0))
    far = _matrix_transform(ndc_to_world, (mouse[0], mouse[1], 1.0))
    scale = float(meters_per_unit)
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("meters_per_unit must be a positive finite number")
    origin = tuple(float(near[index]) * scale for index in range(3))
    far = tuple(float(far[index]) * scale for index in range(3))
    delta = tuple(far[index] - origin[index] for index in range(3))
    length = math.sqrt(sum(value * value for value in delta))
    if length <= 1e-12:
        return None
    return origin, tuple(value / length for value in delta)


def world_to_viewport_pixel(viewport_api, point, *, meters_per_unit=1.0):
    """Project a metric world point into the viewport texture in pixels."""
    transform = getattr(viewport_api, "world_to_ndc", None)
    if transform is None:
        return None
    scale = float(meters_per_unit)
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("meters_per_unit must be a positive finite number")
    # Topology payloads and HTTP edits are always metres.  USD camera
    # transforms consume stage units, so convert only at this viewport edge.
    stage_point = tuple(float(value) / scale for value in point)
    ndc = _matrix_transform(transform, stage_point)
    pixel, _query_viewport = viewport_api.map_ndc_to_texture_pixel(
        (float(ndc[0]), float(ndc[1])))
    if pixel is None:
        return None
    return float(pixel[0]), float(pixel[1])


def _screen_segment_distance(point, first, last):
    dx, dy = last[0] - first[0], last[1] - first[1]
    denominator = dx * dx + dy * dy
    ratio = (max(0.0, min(1.0, ((point[0] - first[0]) * dx
                                + (point[1] - first[1]) * dy) / denominator))
             if denominator else 0.0)
    projected = (first[0] + ratio * dx, first[1] + ratio * dy)
    return math.hypot(point[0] - projected[0], point[1] - projected[1]), ratio


def _screen_polygon_contains(point, polygon):
    inside = False
    for index, current in enumerate(polygon):
        previous = polygon[index - 1]
        if ((current[1] > point[1]) != (previous[1] > point[1])
                and point[0] < ((previous[0] - current[0])
                                * (point[1] - current[1])
                                / ((previous[1] - current[1]) or 1e-12)
                                + current[0])):
            inside = not inside
    return inside


def nearest_projected_hit(snapshot, viewport_api, mouse_ndc, *,
                          selection=None, active_level=None, domain="ground",
                          active_layer=None, semantic_points=(), visible_layers=None,
                          visible_types=None, editor_tool=None,
                          tolerance_px=14.0, meters_per_unit=1.0):
    """Pick only editable objects in the active layer by stable projected IDs."""
    pixel, _query = viewport_api.map_ndc_to_texture_pixel(mouse_ndc)
    if pixel is None:
        return None
    cursor = (float(pixel[0]), float(pixel[1]))
    candidates = []
    road_tools = {"build-ground-road", "delete-ground-edge", "build-aerial-road", "delete-aerial-edge"}
    pick_tolerance = 10.0 if editor_tool in road_tools else tolerance_px
    layer = active_layer or domain
    if not bool((visible_layers or {}).get(layer, True)):
        return None
    enabled = {"nodes": True, "edges": True, "semantic_points": True,
               **(visible_types or {})}
    if editor_tool == "add-node":
        enabled["edges"] = False
    elif editor_tool in road_tools | {"add-edge"}:
        enabled["semantic_points"] = False
    elif editor_tool == "add-semantic-point":
        enabled["nodes"] = enabled["edges"] = False
    nav_active = layer in {"ground", "aerial"}
    # Floors constrain the ground navigation document. Aerial navigation is
    # an independent 3D document and must remain pickable even when the last
    # ground floor carries an old level id.
    effective_level = active_level if layer == "ground" else None
    visible_node_ids = {
        row.get("id") for row in snapshot.get("nodes") or []
        if effective_level is None or row.get("level_id") in (None, effective_level)
    } if nav_active else set()

    selected_point = None
    selected_kind = None
    if selection and selection.get("kind") == "node" and nav_active:
        selected_point = next((row for row in snapshot.get("nodes") or []
                               if row.get("id") == selection.get("id")), None)
        selected_kind = "node"
    elif selection and selection.get("kind") == "semantic_point" and layer == "semantic_points":
        selected_point = next((row for row in semantic_points
                               if row.get("id") == selection.get("id")), None)
        selected_kind = "semantic-point"
    selected_visible = (selected_kind == "node" and enabled["nodes"]) or (
        selected_kind == "semantic-point" and enabled["semantic_points"])
    if (editor_tool != "add-edge" and selected_point and selected_visible
            and selected_point.get("edit_mode", "3d") == "3d"):
        origin = selected_point.get("position_xyz")
        origin_pixel = world_to_viewport_pixel(
            viewport_api, origin, meters_per_unit=meters_per_unit)
        if origin_pixel is not None:
            for axis, delta in (("x", (.45, 0, 0)), ("y", (0, .45, 0)),
                                ("z", (0, 0, .45))):
                endpoint = [origin[index] + delta[index] for index in range(3)]
                endpoint_pixel = world_to_viewport_pixel(
                    viewport_api, endpoint, meters_per_unit=meters_per_unit)
                if endpoint_pixel is not None:
                    distance, _ratio = _screen_segment_distance(
                        cursor, origin_pixel, endpoint_pixel)
                    if (distance <= tolerance_px and math.hypot(
                            cursor[0]-origin_pixel[0], cursor[1]-origin_pixel[1]) > 8.0):
                        candidates.append((distance - 3.0, {
                            "kind": "gizmo", "axis": axis,
                            "target_kind": selected_kind,
                            "id": selected_point["id"], "position_xyz": list(origin),
                        }))

    for node in (snapshot.get("nodes") or []) if nav_active and enabled["nodes"] else ():
        if effective_level is not None and node.get("level_id") not in (None, effective_level):
            continue
        point = world_to_viewport_pixel(
            viewport_api, node.get("position_xyz"),
            meters_per_unit=meters_per_unit)
        if point is None:
            continue
        distance = math.hypot(cursor[0] - point[0], cursor[1] - point[1])
        if distance <= pick_tolerance:
            candidates.append((distance - pick_tolerance - 4.0, {
                "kind": "node", "id": node["id"],
                "position_xyz": list(node["position_xyz"]),
            }))

    for edge in (snapshot.get("edges") or []) if nav_active and enabled["edges"] else ():
        if (effective_level is not None
                and (edge.get("from") not in visible_node_ids
                     and edge.get("to") not in visible_node_ids)):
            continue
        points = edge.get("polyline_xyz") or []
        projected = [world_to_viewport_pixel(
            viewport_api, point, meters_per_unit=meters_per_unit) for point in points]
        if len(projected) < 2 or any(point is None for point in projected):
            continue
        nearest = min((_screen_segment_distance(cursor, projected[index],
                                                 projected[index + 1])[0], index,
                       _screen_segment_distance(cursor, projected[index],
                                                projected[index + 1])[1])
                      for index in range(len(projected) - 1))
        distance, segment_index, ratio = nearest
        if distance <= pick_tolerance:
            first, last = points[segment_index], points[segment_index + 1]
            projected_position = [first[index] + ratio * (last[index] - first[index])
                                  for index in range(3)]
            candidates.append((distance + 1.0, {"kind": "edge", "id": edge["id"],
                                               "position_xyz": projected_position}))
    if layer == "semantic_points" and enabled["semantic_points"]:
        for point_row in semantic_points:
            point = world_to_viewport_pixel(
                viewport_api, point_row.get("position_xyz"),
                meters_per_unit=meters_per_unit)
            if point is None:
                continue
            distance = math.hypot(cursor[0] - point[0], cursor[1] - point[1])
            if distance <= tolerance_px:
                candidates.append((distance - tolerance_px - 4.0, {
                    "kind": "semantic-point", "id": point_row["id"],
                    "position_xyz": list(point_row["position_xyz"]),
                }))
    return min(candidates, key=lambda row: row[0])[1] if candidates else None


class _TopologyEditorGesture:
    """Coordinate native clicks and drags using one frozen pointer press."""

    DRAG_THRESHOLD_PX = 5.0

    def __init__(self, viewport_api, controller):
        from omni.ui import scene as sc

        class _TopologyGestureManager(sc.GestureManager):
            def can_be_prevented(self, gesture):
                # Kit's SelectionDrag must not cancel the editor's input.
                # Only unmodified left input is registered below.
                return False

        class _Drag(sc.DragGesture):
            def __init__(self, owner):
                super().__init__(mouse_button=0, modifiers=0,
                                 name="eai.topology_authoring.drag",
                                 manager=_TopologyGestureManager())
                self.owner = owner

            def _send(self, phase):
                if self.owner is None:
                    return
                mouse = getattr(getattr(self.sender, "gesture_payload", None), "mouse", None)
                if mouse is not None:
                    self.owner.handle(phase, mouse)

            def on_began(self):
                self._send("began")

            def on_changed(self):
                self._send("changed")

            def on_ended(self):
                if self.owner is None:
                    return
                if self.state == sc.GestureState.CANCELED:
                    self.owner.cancel_from_scene()
                else:
                    self._send("ended")

        class _Click(sc.ClickGesture):
            def __init__(self, owner):
                super().__init__(mouse_button=0, modifiers=0,
                                 name="eai.topology_authoring.click",
                                 manager=_TopologyGestureManager())
                self.owner = owner

            def on_began(self):
                if self.owner is not None:
                    self.owner.handle("began", self.sender.gesture_payload.mouse)

            def on_ended(self):
                if self.owner is not None and self.state != sc.GestureState.CANCELED:
                    self.owner.handle("click", self.sender.gesture_payload.mouse)

        self.viewport_api = viewport_api
        self.controller = controller
        self.gesture = _Drag(self)
        self.click_gesture = _Click(self)
        self._press = None
        self._drag_target = None
        self._moved = False
        self._last_mouse = None

    def cancel_from_scene(self):
        # A click-only DragGesture is canceled by Kit; the ClickGesture still
        # owns the press. Cancel only actual movement, never the pending click.
        if self._moved:
            # Some Kit viewport versions report the editor DragGesture as
            # canceled when the native selection gesture releases the same
            # mouse button. Once the drag threshold was crossed this is still
            # a completed topology gesture: use the last sampled pointer and
            # submit exactly one edit. Explicit Esc, tool changes, disconnects
            # and scene switches call controller.cancel_gesture() first, so
            # their gesture is already gone and remains a true cancellation.
            if self.controller.gesture is not None:
                point = (self._world_point(self._last_mouse, self._drag_target)
                         if self._last_mouse is not None else None)
                if point is not None:
                    self.controller.update_drag(point)
                    self.controller.end_drag()
                else:
                    self.controller.cancel_gesture()
            self._press = None
            self._drag_target = None
            self._last_mouse = None
        elif self._press is not None:
            self._press["released"] = True

    def _world_point(self, mouse_ndc, target=None):
        ray = viewport_ray(
            self.viewport_api, mouse_ndc,
            meters_per_unit=self.controller.meters_per_unit)
        if ray is None:
            return None
        if target and target.get("kind") == "gizmo":
            return ray_axis_intersection(ray[0], ray[1], target["position_xyz"],
                                         target["axis"])
        z = (target or {}).get("position_xyz", [0, 0, self.controller.active_z()])[2]
        return ray_plane_intersection(ray[0], ray[1], z)

    def _context_key(self):
        context = self.controller.context or {}
        return (getattr(self.controller, "interaction_generation", 0), *(
            context.get(key) for key in ("scene_key", "scene_generation",
                "context_generation", "csrf_token", "active_layer", "active_domain", "editor_tool")))

    def _pick(self, mouse_ndc):
        context = self.controller.context or {}
        if getattr(self.controller, "navigation_binding_mode", False):
            pixel, _ = self.viewport_api.map_ndc_to_texture_pixel(mouse_ndc)
            hits = []
            for domain in ("ground", "aerial"):
                hit = nearest_projected_hit(
                    self.controller.document_snapshots.get(domain, {}), self.viewport_api,
                    mouse_ndc, selection=None,
                    active_level=context.get("active_level"), domain=domain,
                    active_layer=domain, semantic_points=(),
                    visible_layers=context.get("visible_layers"),
                    visible_types={"nodes": (context.get("visible_types") or {}).get("nodes", True),
                                   "edges": False, "semantic_points": False},
                    editor_tool="add-node", meters_per_unit=self.controller.meters_per_unit)
                if hit is not None and pixel is not None:
                    projected = world_to_viewport_pixel(self.viewport_api, hit.get("position_xyz"),
                                                        meters_per_unit=self.controller.meters_per_unit)
                    if projected is not None:
                        hits.append((math.hypot(float(pixel[0])-projected[0], float(pixel[1])-projected[1]), domain, hit))
            if hits:
                _, domain, hit = min(hits, key=lambda row: row[0])
                hit["domain"] = domain
                return hit
            return None
        return nearest_projected_hit(
            self.controller.snapshot, self.viewport_api, mouse_ndc,
            selection=self.controller.selection,
            active_level=context.get("active_level"),
            domain=context.get("active_domain", "ground"),
            active_layer=self.controller.active_layer,
            semantic_points=self.controller.semantic_points,
            visible_layers=context.get("visible_layers"),
            visible_types=context.get("visible_types"),
            editor_tool=context.get("editor_tool", "add-node"),
            meters_per_unit=self.controller.meters_per_unit)

    @staticmethod
    def _selection(hit):
        kind = hit.get("target_kind", "node") if hit["kind"] == "gizmo" else hit["kind"]
        return {"kind": "semantic_point" if kind == "semantic-point" else kind,
                "collection": None, "id": hit["id"]}

    def handle(self, phase, mouse_ndc):
        if self.controller is None:
            return
        context = self.controller.context or {}
        tool = context.get("editor_tool", "add-node")
        if phase == "began":
            self._last_mouse = tuple(mouse_ndc)
            if getattr(self, "_press", None) is None or self._press.get("released"):
                is_road_tool = tool in {"build-ground-road", "build-aerial-road"}
                hit = (None if is_road_tool and self.controller.road_free_snap
                       else self._pick(mouse_ndc))
                self._drag_target = hit
                self._press = {"ndc": tuple(mouse_ndc), "hit": hit,
                               "context": self._context_key()}
                self._moved = False
                # Select on press so Kit's competing selection gesture cannot
                # swallow a direct node click. Authoring still waits for the
                # click release or drag threshold.
                if hit:
                    selection = self._selection(hit)
                    if getattr(self.controller, "navigation_binding_mode", False) and selection["kind"] == "node":
                        self.controller.toggle_navigation_binding(hit.get("domain", self.controller.active_domain), hit["id"])
                        self._press = self._drag_target = None
                        self._last_mouse = None
                        return
                    self.controller.select(selection)
            return
        press = getattr(self, "_press", None)
        if press is None or press["context"] != self._context_key():
            self._press = self._drag_target = None
            return
        hit = press["hit"]
        if phase == "click":
            if not self._moved:
                if tool in {"delete-ground-edge", "delete-aerial-edge"} and hit and hit.get("kind") == "edge":
                    self.controller._submit_frozen_edit(("delete", "edge", hit["id"]), "remove_edge", id=hit["id"])
                    self.controller.select(None)
                    self._press = self._drag_target = None
                    self._last_mouse = None
                    return
                if hit and not (getattr(self.controller, "navigation_binding_mode", False)
                                and hit.get("kind") == "node"):
                    selection = self._selection(hit)
                    self.controller.select(selection)
                    if tool == "add-edge" and selection["kind"] == "node":
                        self.controller.activate_node_for_edge(hit["id"])
                elif tool not in {"build-ground-road", "delete-ground-edge", "build-aerial-road", "delete-aerial-edge"}:
                    point = self._world_point(mouse_ndc)
                    visible = context.get("visible_layers") or {}
                    types = context.get("visible_types") or {}
                    if point is not None and visible.get(self.controller.active_layer, True):
                        if tool == "add-node" and types.get("nodes", True):
                            self.controller.add_node(point)
                        elif tool == "add-semantic-point" and types.get("semantic_points", True):
                            self.controller.add_semantic_point(point)
            if not self._moved and self.controller.gesture is not None:
                # A press/release is a selection, not a move transaction.
                self.controller.end_drag()
            self._press = self._drag_target = None
            self._last_mouse = None
            return
        if phase == "changed":
            self._last_mouse = tuple(mouse_ndc)
            if not self._moved:
                first, _ = self.viewport_api.map_ndc_to_texture_pixel(press["ndc"])
                current, _ = self.viewport_api.map_ndc_to_texture_pixel(mouse_ndc)
                if first is None or current is None or math.hypot(
                        current[0]-first[0], current[1]-first[1]) < self.DRAG_THRESHOLD_PX:
                    return
                self._moved = True
                if tool in {"build-ground-road", "build-aerial-road"} and self.controller.active_layer in {"ground", "aerial"}:
                    hit_is_start_snap = bool(hit and hit.get("kind") in {"node", "edge"})
                    if (hit_is_start_snap and self.controller.active_layer == "aerial"
                            and abs(hit["position_xyz"][2] - self.controller.active_z()) > .10):
                        hit_is_start_snap = False
                    start = (list(hit.get("position_xyz")) if hit_is_start_snap
                             else self._world_point(press["ndc"]))
                    if start is not None:
                        if not hit_is_start_snap:
                            start[2] = self.controller.active_z()
                        self.controller._road_end_z = start[2]
                        self.controller._road_last_point = list(start)
                        self.controller.gesture = {"target": {"kind": "road"}, "identity": self.controller.dispatcher.client.identity(), "original": list(start)}
                    return
                if hit is None:
                    return  # Moving over empty space never authors a point.
                selection = self._selection(hit)
                if tool == "add-edge" and selection["kind"] == "node":
                    return  # Endpoint selection is click-only.
                start = self._world_point(press["ndc"], hit)
                if start is None:
                    return
                self.controller.select(selection)
                self.controller.begin_drag(
                    {"kind": "semantic-point" if selection["kind"] == "semantic_point"
                     else selection["kind"], "id": hit["id"]}, start,
                    axis=hit.get("axis"))
            if self.controller.gesture is not None:
                is_road_tool = tool in {"build-ground-road", "build-aerial-road"}
                endpoint_hit = (None if is_road_tool and self.controller.road_free_snap
                                else self._pick(mouse_ndc))
                point = (list(endpoint_hit["position_xyz"]) if is_road_tool
                         and endpoint_hit and endpoint_hit.get("kind") in {"node", "edge"}
                         else self._world_point(mouse_ndc, hit))
                if is_road_tool and point is not None and self.controller.active_layer == "aerial":
                    if endpoint_hit and abs(endpoint_hit["position_xyz"][2] - self.controller._road_end_z) > .10:
                        point = self._world_point(mouse_ndc, hit)
                    point[2] = self.controller._road_end_z
                if point is not None:
                    if is_road_tool:
                        self.controller.update_road_drag(point)
                    else:
                        self.controller.update_drag(point)
        elif phase == "ended" and self._moved:
            self._last_mouse = tuple(mouse_ndc)
            if self.controller.gesture is not None:
                is_road_tool = tool in {"build-ground-road", "build-aerial-road"}
                endpoint_hit = (None if is_road_tool and self.controller.road_free_snap
                                else self._pick(mouse_ndc))
                point = (list(endpoint_hit["position_xyz"]) if is_road_tool
                         and endpoint_hit and endpoint_hit.get("kind") in {"node", "edge"}
                         else self._world_point(mouse_ndc, hit))
                if is_road_tool and point is not None and self.controller.active_layer == "aerial":
                    if endpoint_hit and abs(endpoint_hit["position_xyz"][2] - self.controller._road_end_z) > .10:
                        point = self._world_point(mouse_ndc, hit)
                    point[2] = self.controller._road_end_z
                if point is not None:
                    if is_road_tool:
                        self.controller.update_road_drag(point)
                    else:
                        self.controller.update_drag(point)
                self.controller.end_drag()
            # Leave the moved flag set until the next press so a late click
            # callback from this same release cannot author a second command.
            self._press = self._drag_target = None
            self._last_mouse = None

    def destroy(self):
        for gesture in (self.gesture, self.click_gesture):
            if gesture is not None:
                gesture.owner = None
        self.gesture = self.click_gesture = None
        self._press = self._drag_target = None
        self._last_mouse = None
        self.viewport_api = self.controller = None


class _TopologyEditorViewportScene:
    def __init__(self, viewport_desc, controller):
        from omni.ui import scene as sc
        viewport_api = (viewport_desc.get("viewport_api")
                        if hasattr(viewport_desc, "get")
                        else getattr(viewport_desc, "viewport_api", None))

        class _Manipulator(sc.Manipulator):
            def __init__(self, owner):
                super().__init__()
                self.owner = owner
                self.transform = None
                self.screen = None

            def on_build(self):
                if self.owner._gesture is not None:
                    self.owner._gesture.destroy()
                self.owner._gesture = _TopologyEditorGesture(
                    self.owner.viewport_api, self.owner.controller)
                # Scene objects are reference owned.  Retain the parent
                # transform as well as the Screen or Kit collects the entire
                # gesture surface immediately after on_build returns.
                self.transform = sc.Transform()
                with self.transform:
                    self.screen = sc.Screen(gesture=[g for g in
                                                     (self.owner._gesture.click_gesture,
                                                      self.owner._gesture.gesture) if g is not None])

        self.viewport_api = viewport_api
        self.controller = controller
        self._gesture = None
        # Kit's ObjectClick and Selection manipulators run independently of
        # the scene gesture surface.  Keep their disable handle alive while
        # this editor is active so a topology click cannot also select a USD
        # prim. The handle is deliberately scoped per viewport and released
        # when the topology editor is disabled.
        self._selection_disable_handle = None
        self._manipulator = _Manipulator(self)

    @property
    def visible(self):
        return bool(self._manipulator and self._manipulator.visible)

    @visible.setter
    def visible(self, value):
        if self._manipulator is not None:
            self._manipulator.visible = bool(value)

    def set_input_enabled(self, enabled: bool) -> None:
        """Toggle native USD picking together with the topology surface."""
        enabled = bool(enabled)
        if enabled and self._selection_disable_handle is None:
            try:
                from omni.kit.viewport.utility import disable_selection
                self._selection_disable_handle = disable_selection(
                    self.viewport_api, disable_click=True)
            except (ImportError, AttributeError, RuntimeError, TypeError):
                # Older Kit builds may not ship the utility extension. The
                # custom gesture still works; retaining this fallback keeps
                # startup compatible while newer builds get full isolation.
                self._selection_disable_handle = False
        elif not enabled and self._selection_disable_handle is not None:
            self._selection_disable_handle = None

    def destroy(self):
        if self._gesture is not None:
            self._gesture.destroy()
            self._gesture = None
        if self._manipulator is not None:
            self._manipulator.owner = None
            self._manipulator.screen = None
            self._manipulator.transform = None
            self._manipulator = None
        self._selection_disable_handle = None
        self.viewport_api = None
        self.controller = None


class TopologyEditorViewport:
    """Register coordinated topology click/drag input in every viewport."""

    def __init__(self, controller):
        self.controller = controller
        self._registration = None
        self._scenes = []
        self.enabled = True
        try:
            from omni.kit.viewport.registry import RegisterScene
            self._registration = RegisterScene(
                self._make_scene,
                "eai.topology_authoring.editor",
            )
        except (ImportError, AttributeError, RuntimeError, TypeError) as exc:
            raise RuntimeError(f"Isaac topology editor viewport is unavailable: {exc}") from exc

    def _make_scene(self, viewport_desc):
        scene = _TopologyEditorViewportScene(viewport_desc, self.controller)
        scene.visible = self.enabled
        scene.set_input_enabled(self.enabled)
        self._scenes.append(scene)
        return scene

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        for scene in tuple(self._scenes):
            scene.visible = self.enabled
            set_input_enabled = getattr(scene, "set_input_enabled", None)
            if callable(set_input_enabled):
                set_input_enabled(self.enabled)

    def destroy(self):
        for scene in self._scenes:
            scene.destroy()
        self._scenes = []
        registration, self._registration = self._registration, None
        if registration is not None:
            registration.destroy()
        self.controller = None
