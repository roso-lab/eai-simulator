"""GUI camera and viewport evidence for the city demo (Kit imports stay lazy)."""
from __future__ import annotations

import asyncio
import math
from pathlib import Path


def camera_pose(plan):
    """Frame the approach and measured wall from the road side."""
    points = plan.route.points
    lo = [min(p[i] for p in points) for i in range(2)]
    hi = [max(p[i] for p in points) for i in range(2)]
    target = ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, 2.0)
    _, yaw = plan.route.sample(plan.route.length)
    outward = (-math.cos(yaw), -math.sin(yaw))
    side = (-outward[1], outward[0])
    span = max(hi[0] - lo[0], hi[1] - lo[1], 24.0)
    eye = (target[0] + outward[0] * span * .83 - side[0] * span,
           target[1] + outward[1] * span * .83 - side[1] * span,
           span * 2.3)
    target = (target[0] - side[0] * span * .2, target[1] - side[1] * span * .2, 3.0)
    return eye, target


def configure_view(stage, plan):
    from pxr import Gf, Usd, UsdGeom
    from omni.kit.viewport.utility import get_active_viewport

    eye, target = camera_pose(plan)
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        camera = UsdGeom.Camera.Define(stage, "/World/CityDemoCamera")
        camera.GetFocalLengthAttr().Set(24.0)
        camera.GetClippingRangeAttr().Set(Gf.Vec2f(.1, 2000))
        xform = UsdGeom.Xformable(camera)
        xform.ClearXformOpOrder()
        xform.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(
            Gf.Vec3d(*eye), Gf.Vec3d(*target), Gf.Vec3d(0, 0, 1)).GetInverse())
    viewport = get_active_viewport()
    if viewport is None:
        raise RuntimeError("City GUI requires an active viewport")
    viewport.set_active_camera(str(camera.GetPath()))
    return viewport


def viewport_witness(stage, viewport):
    from pxr import Usd, UsdGeom
    import carb.settings
    import omni.kit.app
    import omni.timeline
    import omni.graph.core as og
    graph_path = "/World/CityFlowDiagnostics"
    if not stage.GetPrimAtPath(graph_path):
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            og.Controller.edit({"graph_path": graph_path, "evaluator_name": "push"}, {
                og.Controller.Keys.CREATE_NODES: [("Blocks", "omni.flowusd.FlowBlocks")],
            })
    og.Controller.node(graph_path + "/Blocks").request_compute()
    og.Controller.evaluate_sync(graph_path)
    camera = stage.GetPrimAtPath(str(viewport.camera_path))
    manager = omni.kit.app.get_app().get_extension_manager()
    return {
        "timeline_time": omni.timeline.get_timeline_interface().get_current_time(),
        "flow_active_blocks": og.Controller.get(og.Controller.attribute(graph_path + "/Blocks.outputs:activeBlockCount")),
        "flow_max_blocks": og.Controller.get(og.Controller.attribute(graph_path + "/Blocks.outputs:maxBlockCount")),
        "camera": str(viewport.camera_path),
        "camera_world_matrix": [list(row) for row in UsdGeom.Xformable(camera).ComputeLocalToWorldTransform(Usd.TimeCode.Default())],
        "flow_extensions": {name: manager.is_extension_enabled(name) for name in ("omni.flowusd", "omni.usd.schema.flow")},
        "flow_renderer_enabled": carb.settings.get_settings().get("/rtx/flow/enabled"),
        "rtx_composite_features": {
            feature: carb.settings.get_settings().get(f"/rtx/{feature}/enabled")
            for feature in ("translucency", "reflections", "indirectDiffuse")
        },
        "flow_prims": [{"path": str(p.GetPath()), "type": p.GetTypeName()} for p in stage.Traverse() if p.GetTypeName().startswith("Flow")],
    }


def capture_vehicle_views(stage, viewport, app, render, annotator, directory):
    from pathlib import Path
    from pxr import Gf, Usd, UsdGeom
    results = []
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    camera = UsdGeom.Camera(stage.GetPrimAtPath(str(viewport.camera_path)))
    for vehicle in stage.GetPrimAtPath("/World/CityTraffic").GetChildren():
        center = cache.ComputeWorldBound(vehicle).ComputeAlignedRange().GetMidpoint()
        eye = center + Gf.Vec3d(7, -8, 7)
        with Usd.EditContext(stage, stage.GetSessionLayer()):
            camera.ClearXformOpOrder()
            camera.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(eye, center, Gf.Vec3d(0, 0, 1)).GetInverse())
        for _ in range(12):
            render()
        evidence = visible_vehicle_pixels(annotator)
        if evidence["vehicles"].get(vehicle.GetName(), 0) < 1000:
            with Usd.EditContext(stage, stage.GetSessionLayer()):
                camera.ClearXformOpOrder()
                camera.AddTransformOp().Set(Gf.Matrix4d().SetLookAt(center + Gf.Vec3d(0, 0, 15), center, Gf.Vec3d(0, 1, 0)).GetInverse())
            for _ in range(12):
                render()
            evidence = visible_vehicle_pixels(annotator)
        evidence["vehicle"] = str(vehicle.GetPath())
        evidence["capture"] = capture_viewport(app, viewport, Path(directory) / ("vehicle_" + vehicle.GetName() + ".png"), render=render)
        results.append(evidence)
    return results


def attach_instance_ids(viewport):
    import omni.replicator.core as rep
    annotator = rep.AnnotatorRegistry.get_annotator("instance_id_segmentation", init_params={"colorize": False})
    annotator.attach([viewport.render_product_path])
    return annotator


def visible_vehicle_pixels(annotator):
    import numpy as np
    result = annotator.get_data()
    ids, counts = np.unique(result["data"], return_counts=True)
    labels = result.get("info", {}).get("idToLabels", {})
    totals = {}
    for identifier, count in zip(ids, counts):
        path = str(labels.get(str(int(identifier)), ""))
        if "/World/CityTraffic/" in path:
            vehicle = path.split("/World/CityTraffic/", 1)[1].split("/", 1)[0]
            totals[vehicle] = totals.get(vehicle, 0) + int(count)
    return {"vehicles": totals, "data_shape": list(result["data"].shape),
            "unique_ids": len(ids), "labels_sample": dict(list(labels.items())[:8])}


class CityDemoControls:
    def __init__(self, city):
        import omni.ui as ui
        self.paused = False
        self.replay_requested = False
        self.city = city
        self.window = ui.Window("City accident demo", width=360, height=175)
        with self.window.frame:
            with ui.VStack(spacing=6):
                self.label = ui.Label("Preparing accident...")
                ui.Label("Vehicle fire -> wall impact -> building fire")
                with ui.HStack():
                    ui.Button("Pause / Resume", clicked_fn=self.toggle_pause)
                    ui.Button("Replay", clicked_fn=self.replay)
                    ui.Button("Focus accident", clicked_fn=self.focus)
                ui.Label("Stage: /World/CityFlow/Fire and DenseSmoke", word_wrap=True)

    def toggle_pause(self):
        self.paused = not self.paused

    def replay(self):
        self.replay_requested = True
        self.paused = False

    def focus(self):
        configure_view(self.city.stage, self.city.scene.plan)

    def update(self):
        self.label.text = f"{self.city.elapsed:5.1f} s  |  {self.city.phase}  |  {self.city.last_speed_mps:4.1f} m/s"

    def close(self):
        self.window.destroy()


def capture_viewport(app, viewport, path, *, render):
    """Save the GUI's own render product, including Flow volume rendering."""
    from omni.kit.viewport.utility import capture_viewport_to_file
    from omni.kit.renderer_capture import acquire_renderer_capture_interface

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    capture = capture_viewport_to_file(viewport, file_path=str(path))
    task = asyncio.ensure_future(capture.wait_for_result(completion_frames=1))
    try:
        for _ in range(180):
            if task.done() or not app.is_running():
                break
            render()
        if not task.done() or not task.result():
            raise RuntimeError(f"Viewport capture failed: {path}")
        acquire_renderer_capture_interface().wait_async_capture()
        if not path.is_file() or not path.stat().st_size:
            raise RuntimeError(f"Viewport capture is empty: {path}")
    finally:
        if not task.done():
            task.cancel()
    import numpy as np
    return {"path": str(path), "rendered_view": np.asarray(capture.view).reshape(4, 4).tolist(),
            "rendered_projection": np.asarray(capture.projection).reshape(4, 4).tolist(),
            "frame_number": capture.frame_number}
