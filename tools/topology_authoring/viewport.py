"""Isaac Sim viewport picking and topology overlay."""
from __future__ import annotations
from typing import Any, Callable

OVERLAY_ROOT = "/World/TopologyAuthoringOverlay"

def project_click(viewport_api, pixel, callback):
    if viewport_api is None: raise RuntimeError("viewport API is unavailable")
    viewport_api.request_query(pixel, callback, query_name="eai.topology_authoring.pick")

def draw_overlay(stage, document):
    return {"nodes": [dict(id=n["id"], position=n["position_xyz"], locked=n.get("locked", False)) for n in document.nodes],
            "edges": [dict(id=e["id"], points=e["polyline_xyz"], directed=e.get("directed", False), locked=e.get("locked_geometry", False)) for e in document.edges],
            "pending_node": getattr(document.pending_node, "position_xyz", None),
            "pending_edge": document.pending_edge.preview_polyline_xyz if document.pending_edge else None,
            "selected_node": document.selected_node_id,
            "selected_edge": document.selected_edge_id}

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

class TopologyOverlay:
    def __init__(self, document):
        self.document=document; self.stage=None
        try:
            import omni.usd; self.stage=omni.usd.get_context().get_stage()
        except ImportError: pass
    def rebuild(self):
        if self.stage is None: return
        from pxr import Gf, UsdGeom
        root=self.stage.GetPrimAtPath(OVERLAY_ROOT)
        if root.IsValid(): self.stage.RemovePrim(OVERLAY_ROOT)
        UsdGeom.Xform.Define(self.stage, OVERLAY_ROOT)
        for i,node in enumerate(self.document.nodes):
            sphere=UsdGeom.Sphere.Define(self.stage, f"{OVERLAY_ROOT}/Nodes/N{i:05d}")
            sphere.CreateRadiusAttr().Set(.42); sphere.AddTranslateOp().Set(Gf.Vec3d(*node["position_xyz"]))
            color = (.95, .06, .05) if node["id"] == self.document.selected_node_id else (.08, .85, .18)
            _set_color(sphere.GetPrim(), color)
        for i,edge in enumerate(self.document.edges):
            color = (.95, .06, .05) if edge["id"] == self.document.selected_edge_id else (.08, .85, .18)
            self._draw_edge(f"{OVERLAY_ROOT}/Edges/E{i:05d}",edge["polyline_xyz"],edge.get("directed",False),color)
        if self.document.pending_node:
            sphere=UsdGeom.Sphere.Define(self.stage,f"{OVERLAY_ROOT}/Preview/Node"); sphere.CreateRadiusAttr().Set(.5); sphere.AddTranslateOp().Set(Gf.Vec3d(*self.document.pending_node.position_xyz)); _set_color(sphere.GetPrim(),(.95,.06,.05))
        if self.document.pending_edge and self.document.pending_edge.preview_polyline_xyz:
            self._draw_edge(f"{OVERLAY_ROOT}/Preview/Edge",self.document.pending_edge.preview_polyline_xyz,self.document.pending_edge.directed,(.95,.06,.05))
    def _draw_edge(self,path,points,directed,color):
        from pxr import Gf, UsdGeom
        curve=UsdGeom.BasisCurves.Define(self.stage,path); curve.CreateTypeAttr().Set(UsdGeom.Tokens.linear); curve.CreateCurveVertexCountsAttr().Set([len(points)]); curve.CreatePointsAttr().Set([Gf.Vec3f(*p) for p in points]); curve.CreateWidthsAttr().Set([.16]); _set_color(curve.GetPrim(),color)
        if len(points)>=2:
            if directed: self._draw_arrow(path+"/Arrow",points[-2],points[-1],color)
            else: self._draw_arrow(path+"/ArrowA",points[-2],points[-1],color); self._draw_arrow(path+"/ArrowB",points[1],points[0],color)
    def _draw_arrow(self,path,start,end,color):
        from pxr import Gf, UsdGeom
        a,b=Gf.Vec3d(*start),Gf.Vec3d(*end); d=b-a; length=d.GetLength()
        if length<=1e-8:return
        d.Normalize(); tip=b-d*min(.35,length*.25); cone=UsdGeom.Cone.Define(self.stage,path); cone.CreateRadiusAttr().Set(.32); cone.CreateHeightAttr().Set(.7); cone.AddTranslateOp().Set(tip); _orient_z(cone.GetPrim(),d); _set_color(cone.GetPrim(),color)
    def destroy(self):
        if self.stage is not None:
            prim=self.stage.GetPrimAtPath(OVERLAY_ROOT)
            if prim.IsValid(): self.stage.RemovePrim(OVERLAY_ROOT)

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
                left = _TopologyClickGesture(self.owner.viewport_api, self.owner.on_left, 0).gesture
                right = _TopologyClickGesture(self.owner.viewport_api, self.owner.on_right, 1).gesture
                with self.root:
                    self.left_screen = sc.Screen(gesture=left)
                    self.right_screen = sc.Screen(gesture=right)

            def destroy(self):
                self.clear()
                self.root = None

        self.viewport_api = viewport_api
        self.on_left = on_left
        self.on_right = on_right
        self._manipulator = _Manipulator(self)

    @property
    def visible(self):
        return bool(self._manipulator.visible)

    @visible.setter
    def visible(self, value):
        self._manipulator.visible = bool(value)

    def refresh(self):
        self._manipulator.invalidate()

    def destroy(self):
        self._manipulator.destroy()
        self._manipulator = None


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
        if world_position is None:
            return
        try:
            point = tuple(float(world_position[index]) for index in range(3))
        except (IndexError, TypeError, ValueError):
            return
        self._on_left_callback(point, prim_path)

    def _on_right(self, *_args):
        self._on_right_callback()

    def refresh(self):
        self.overlay.rebuild()

    def destroy(self):
        # Releasing the registration removes scene instances from viewports;
        # the USD overlay is explicitly removed from the authoring stage.
        self._registration = None
        self.overlay.destroy()
