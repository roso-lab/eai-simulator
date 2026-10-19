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
