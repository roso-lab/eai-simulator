"""Capture coordinate-aligned Camera-Light material views for scene semantics.

All face filtering and lights live on the USD session layer.  Source files are
hashed before and after capture and are never saved.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DESCRIPTOR = ROOT / "tools/topology_authoring/maps/living_public_scenes.json"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _fan_triangles(indices, counts):
    """Return deterministic triangle indices for arbitrary polygon faces."""
    import numpy as np

    indices = np.asarray(indices, dtype=np.int64)
    counts = np.asarray(counts, dtype=np.int64)
    if np.any(counts < 3) or int(counts.sum()) != len(indices):
        raise ValueError("malformed polygon face counts/indices")
    triangles = []
    offset = 0
    for count in counts:
        face = indices[offset:offset + int(count)]
        offset += int(count)
        triangles.extend((int(face[0]), int(face[k]), int(face[k + 1]))
                         for k in range(1, int(count) - 1))
    return np.asarray(triangles, dtype=np.int64).reshape(-1, 3)


def _world_points_metres(points, local_to_world, meters_per_unit):
    """Transform row-vector USD points and return world coordinates in metres."""
    import numpy as np

    meters = float(meters_per_unit)
    if not math.isfinite(meters) or meters <= 0:
        raise ValueError(f"invalid metersPerUnit: {meters}")
    points = np.asarray(points, dtype=float)
    matrix = np.asarray(local_to_world, dtype=float)
    return (np.c_[points, np.ones(len(points))] @ matrix)[:, :3] * meters


def _metres_to_stage_units(values, meters_per_unit):
    """Convert metric coordinates to the authored stage coordinate scale."""
    meters = float(meters_per_unit)
    if not math.isfinite(meters) or meters <= 0:
        raise ValueError(f"invalid metersPerUnit: {meters}")
    return tuple(float(value) / meters for value in values)


def _copy_stage_metrics(source_stage, wrapper_stage, usd_geom=None):
    """Preserve source units/up-axis on a disposable root-layer wrapper."""
    if usd_geom is None:
        from pxr import UsdGeom as usd_geom

    meters = float(usd_geom.GetStageMetersPerUnit(source_stage))
    if not math.isfinite(meters) or meters <= 0:
        raise ValueError(f"invalid source metersPerUnit: {meters}")
    up_axis = usd_geom.GetStageUpAxis(source_stage)
    usd_geom.SetStageMetersPerUnit(wrapper_stage, meters)
    usd_geom.SetStageUpAxis(wrapper_stage, up_axis)
    return {"meters_per_unit": meters, "up_axis": str(up_axis)}


def _parse_capture_bounds(value, label):
    """Validate descriptor ``[xmin, ymin, xmax, ymax]`` bounds in metres."""
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError(f"{label} must be [xmin, ymin, xmax, ymax]")
    bounds = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in bounds):
        raise ValueError(f"{label} must contain finite values")
    xmin, ymin, xmax, ymax = bounds
    if not xmin < xmax or not ymin < ymax:
        raise ValueError(f"{label} must have positive width and height")
    return bounds


def _preflight_descriptor(descriptor, selected, hard_exit=False):
    """Validate selection and capture-only descriptor fields without Kit."""
    scenes = descriptor.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("descriptor.scenes must be a non-empty list")
    by_id = {}
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            raise ValueError(f"descriptor.scenes[{index}] must be an object")
        scene_id = scene.get("id")
        if not isinstance(scene_id, str) or not scene_id.strip():
            raise ValueError(f"descriptor.scenes[{index}].id must be non-empty")
        if scene_id in by_id:
            raise ValueError(f"duplicate scene id: {scene_id}")
        by_id[scene_id] = scene
        _parse_capture_bounds(scene.get("capture_bounds_xy"),
                              f"scene {scene_id} capture_bounds_xy")
        levels = scene.get("levels")
        if not isinstance(levels, list) or not levels:
            raise ValueError(f"scene {scene_id} must define at least one level")
        level_ids = set()
        previous_z = None
        for level in levels:
            level_id = level.get("id") if isinstance(level, dict) else None
            if not isinstance(level_id, str) or not level_id or level_id in level_ids:
                raise ValueError(f"scene {scene_id} has invalid or duplicate level id: {level_id}")
            level_ids.add(level_id)
            z = float(level.get("support_z_m"))
            if not math.isfinite(z):
                raise ValueError(f"scene {scene_id}/{level_id} support_z_m must be finite")
            if previous_z is not None and z <= previous_z:
                raise ValueError(f"scene {scene_id} levels must have strictly increasing support_z_m")
            previous_z = z
            _parse_capture_bounds(level.get("capture_bounds_xy"),
                                  f"scene {scene_id}/{level_id} capture_bounds_xy")
    selected = set(selected)
    missing = selected - set(by_id)
    if missing:
        raise ValueError("unknown scene IDs: " + ", ".join(sorted(missing)))
    if hard_exit and len(selected) != 1:
        raise ValueError("--hard-exit requires exactly one known --scene")
    return [scene for scene in scenes if not selected or scene["id"] in selected]


def _image_to_world(extent_xy):
    """Normalized top-image UV (right/down) to world XY metres."""
    xmin, xmax, ymin, ymax = map(float, extent_xy)
    return {
        "coordinates": "normalized image uv to USD world metres",
        "matrix_3x3": [[xmax - xmin, 0.0, xmin],
                       [0.0, -(ymax - ymin), ymax],
                       [0.0, 0.0, 1.0]],
        "image_right": "+X",
        "image_down": "-Y",
        "camera_forward": "-Z",
        "camera_up": "+Y",
    }


def _clipping_range_metres(eye, bounds_xy, z_bounds):
    """Conservative near/far range covering all eight ROI box corners."""
    import numpy as np

    xmin, ymin, xmax, ymax = bounds_xy
    zmin, zmax = z_bounds
    corners = np.asarray([[x, y, z] for x in (xmin, xmax)
                          for y in (ymin, ymax) for z in (zmin, zmax)], dtype=float)
    distances = np.linalg.norm(corners - np.asarray(eye, dtype=float), axis=1)
    positive = distances[distances > 1e-9]
    closest = float(positive.min()) if len(positive) else 1.0
    farthest = float(distances.max()) if len(distances) else 1.0
    near = max(0.001, closest * 1e-4)
    far = max(near * 10.0, farthest * 1.2 + 1.0)
    return near, far


def _validate_capture_set(expected_by_level):
    """Decode every expected image and return SHA-256 metadata."""
    from PIL import Image

    metadata = {}
    for level_id, expected in expected_by_level.items():
        if len(expected) != 5:
            raise ValueError(f"{level_id} must have exactly five capture images")
        paths = [Path(path) for path, _ in expected]
        if len(set(paths)) != 5:
            raise ValueError(f"{level_id} capture paths must be unique")
        rows = []
        for path, expected_size in expected:
            path = Path(path)
            if not path.is_file() or path.stat().st_size <= 0:
                raise RuntimeError(f"capture missing or empty: {path}")
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                image.load()
                size = tuple(int(value) for value in image.size)
            if size[0] <= 0 or size[1] <= 0 or size != tuple(expected_size):
                raise RuntimeError(
                    f"capture size mismatch for {path}: {size} != {tuple(expected_size)}")
            image_fd = os.open(path, os.O_RDONLY)
            try:
                os.fsync(image_fd)
            finally:
                os.close(image_fd)
            rows.append({"path": str(path), "sha256": digest(path),
                         "pixel_size": [size[0], size[1]],
                         "bytes": path.stat().st_size})
        metadata[level_id] = rows
    return metadata


def _fsync_directory(path):
    """Persist directory-entry changes before publishing completion."""
    directory_fd = os.open(Path(path), os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _write_json_atomic(path, payload):
    """Durably replace a JSON file so its presence is a reliable receipt."""
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    _fsync_directory(path.parent)


def _published_path(path, staging_directory, final_directory):
    """Translate one staging path to the location visible after directory rename."""
    relative = Path(path).relative_to(Path(staging_directory))
    return str(Path(final_directory) / relative)


def _publish_scene_directory(staging_directory, final_directory):
    """Atomically expose one fully verified scene directory exactly once."""
    staging_directory = Path(staging_directory)
    final_directory = Path(final_directory)
    if final_directory.exists():
        raise FileExistsError(f"capture output already exists: {final_directory}")
    for name in ("capture.json", "capture-complete.json"):
        if not (staging_directory / name).is_file():
            raise RuntimeError(f"cannot publish incomplete capture: missing {name}")
    receipt = json.loads((staging_directory / "capture-complete.json").read_text())
    if receipt.get("complete") is not True:
        raise RuntimeError("cannot publish capture without a complete receipt")
    os.rename(staging_directory, final_directory)
    _fsync_directory(final_directory.parent)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--descriptor",type=Path,default=DEFAULT_DESCRIPTOR)
    parser.add_argument("--output-root",type=Path,required=True)
    parser.add_argument("--scene",action="append")
    parser.add_argument("--hard-exit",action="store_true",
                        help="single-scene worker: exit after verified files to avoid a known Kit shutdown crash")
    args=parser.parse_args()
    descriptor=json.loads(args.descriptor.read_text())
    is_v2 = descriptor.get("schema") == "eai.semantic_map.scene_batch" and descriptor.get("version") == 2
    scene_roots = ({category: Path(root) for category, root in descriptor["scene_roots"].items()}
                   if is_v2 else {None: Path(descriptor["scene_root"])})
    selected=set(args.scene or [])
    try:
        scenes = _preflight_descriptor(descriptor, selected, args.hard_exit)
    except (TypeError, ValueError) as exc:
        parser.error(str(exc))
    existing = [args.output_root/scene["id"] for scene in scenes
                if (args.output_root/scene["id"]).exists()]
    if existing:
        parser.error("capture output already exists: " + ", ".join(map(str, existing)))
    args.output_root.mkdir(parents=True,exist_ok=True)

    from isaacsim import SimulationApp
    app=SimulationApp({"headless":False,"fast_shutdown":False,"width":1400,"height":1400})
    context=None
    try:
        import numpy as np
        import carb.settings
        import omni.usd
        from pxr import Usd,UsdGeom,UsdLux,Gf,Sdf
        from omni.kit.viewport.utility import get_active_viewport,capture_viewport_to_file
        context=omni.usd.get_context();viewport=get_active_viewport()
        carb.settings.get_settings().set_bool("/rtx/useViewLightingMode",True)
        for scene in scenes:
            scene_id=scene["id"];final_out=args.output_root/scene_id
            out=args.output_root/f".{scene_id}.capture-tmp-{os.getpid()}"
            out.mkdir(parents=False,exist_ok=False)
            relative=scene.get("visual_usd",scene["usd"])
            root = scene_roots.get(scene.get("category")) if is_v2 else scene_roots[None]
            usd_path=(root/relative).resolve()
            source_stage=Usd.Stage.Open(str(usd_path))
            source_before={str(Path(layer.realPath).resolve()):digest(layer.realPath)
                           for layer in source_stage.GetUsedLayers() if layer.realPath}
            # A disposable wrapper authors collisionEnabled=false before Kit/PhysX
            # sees the stage.  This avoids asynchronous convex decomposition for
            # a visual-only capture and never edits the source layers.
            wrapper=out/"capture-session-wrapper.usda"
            wrapper_layer=Sdf.Layer.CreateNew(str(wrapper));wrapper_layer.subLayerPaths=[str(usd_path)]
            wrapper_stage=Usd.Stage.Open(wrapper_layer);wrapper_stage.SetEditTarget(wrapper_layer)
            source_metrics=_copy_stage_metrics(source_stage,wrapper_stage,UsdGeom)
            disabled=0
            for prim in wrapper_stage.Traverse():
                applied=[name for name in prim.GetAppliedSchemas()
                         if any(token in name.lower() for token in ("collision","rigidbody","massapi","physx"))]
                if applied:
                    spec=Sdf.CreatePrimInLayer(wrapper_layer,prim.GetPath())
                    operation=Sdf.TokenListOp();operation.deletedItems=applied
                    spec.SetInfo("apiSchemas",operation);disabled+=1
            wrapper_stage.GetRootLayer().Save();wrapper_stage=None;source_stage=None
            if not context.open_stage(str(wrapper)):
                raise RuntimeError(f"cannot open capture wrapper for {usd_path}")
            for _ in range(100):app.update()
            stage=context.get_stage();stage.SetEditTarget(stage.GetSessionLayer())
            meters=float(UsdGeom.GetStageMetersPerUnit(stage))
            if not math.isfinite(meters) or meters <= 0:
                raise ValueError(f"invalid metersPerUnit for {scene_id}: {meters}")
            up_axis=str(UsdGeom.GetStageUpAxis(stage))
            if not math.isclose(meters,source_metrics["meters_per_unit"],rel_tol=0.0,abs_tol=1e-12):
                raise RuntimeError(f"wrapper metersPerUnit drift for {scene_id}: {meters}")
            if up_axis!=source_metrics["up_axis"]:
                raise RuntimeError(f"wrapper upAxis drift for {scene_id}: {up_axis}")
            light=UsdLux.DomeLight.Define(stage,"/World/SemanticObservationLight")
            light.CreateIntensityAttr(650);light.CreateColorAttr(Gf.Vec3f(1.0,.97,.92))
            key_light=UsdLux.DistantLight.Define(stage,"/World/SemanticObservationKeyLight")
            key_light.CreateIntensityAttr(2200);key_light.CreateAngleAttr(2.0)
            key_light.CreateColorAttr(Gf.Vec3f(1.0,.95,.88))
            cam=UsdGeom.Camera.Define(stage,"/World/SemanticTopCamera")
            camera_xform=cam.MakeMatrixXform()
            cache=UsdGeom.XformCache();originals=[];all_min=[];all_max=[];skipped=0
            polygon_meshes=0
            for prim in stage.Traverse():
                if not prim.IsA(UsdGeom.Mesh):continue
                mesh=UsdGeom.Mesh(prim);points=np.asarray(mesh.GetPointsAttr().Get(),dtype=float)
                counts=np.asarray(mesh.GetFaceVertexCountsAttr().Get(),dtype=int)
                indices=np.asarray(mesh.GetFaceVertexIndicesAttr().Get(),dtype=int)
                if not len(points) or not len(counts) or not len(indices):
                    skipped+=1;continue
                try:
                    triangle_indices = _fan_triangles(indices, counts)
                except (TypeError, ValueError):
                    skipped+=1;continue
                if not len(triangle_indices):
                    skipped+=1;continue
                polygon_meshes += int(not np.all(counts == 3))
                matrix=np.asarray(cache.GetLocalToWorldTransform(prim))
                world=_world_points_metres(points,matrix,meters)
                triangles=world[triangle_indices]
                all_min.append(world.min(0));all_max.append(world.max(0))
                originals.append((mesh,triangles))
            if not originals:raise RuntimeError(f"no renderable polygon meshes: {scene_id}")
            low=np.min(np.asarray(all_min),axis=0);high=np.max(np.asarray(all_max),axis=0)
            automatic_bounds=(float(low[0]),float(low[1]),float(high[0]),float(high[1]))
            viewport.camera_path=cam.GetPath()

            def set_look_at(eye,target,up,bounds_xy,z_bounds):
                matrix=Gf.Matrix4d(1.0)
                eye_stage=_metres_to_stage_units(eye,meters)
                target_stage=_metres_to_stage_units(target,meters)
                matrix.SetLookAt(Gf.Vec3d(*eye_stage),Gf.Vec3d(*target_stage),Gf.Vec3d(*up))
                camera_to_world=matrix.GetInverse()
                camera_xform.Set(camera_to_world)
                key_light.MakeMatrixXform().Set(camera_to_world)
                near_m,far_m=_clipping_range_metres(eye,bounds_xy,z_bounds)
                cam.CreateClippingRangeAttr(Gf.Vec2f(near_m/meters,far_m/meters)).Set(
                    Gf.Vec2f(near_m/meters,far_m/meters))
                return [float(near_m),float(far_m)]

            def capture_view(output):
                for _ in range(35):app.update()
                handle=capture_viewport_to_file(viewport,str(output))
                for _ in range(45):app.update()
                if not output.is_file():raise RuntimeError(f"capture did not write {output}")
                return handle
            stats=[];expected_by_level={}
            for level_index,level in enumerate(scene["levels"]):
                level_id=level["id"];z=float(level["support_z_m"])
                next_z=float(scene["levels"][level_index+1]["support_z_m"]) if level_index+1<len(scene["levels"]) else None
                band=float(scene.get("capture_band_height_m",descriptor["defaults"]["capture_band_height_m"]))
                maximum=min(z+band,next_z-.04) if next_z is not None else z+band
                configured_bounds=_parse_capture_bounds(
                    level.get("capture_bounds_xy",scene.get("capture_bounds_xy")),
                    f"scene {scene_id}/{level_id} capture_bounds_xy")
                if configured_bounds is None:
                    xmin,ymin,xmax,ymax=automatic_bounds
                    xpad=max((xmax-xmin)*.04,.01);ypad=max((ymax-ymin)*.04,.01)
                    xmin-=xpad;xmax+=xpad;ymin-=ypad;ymax+=ypad
                    bounds_source="automatic_mesh_bounds_plus_4pct"
                else:
                    xmin,ymin,xmax,ymax=configured_bounds
                    bounds_source="descriptor_capture_bounds_xy"
                width=float(xmax-xmin);height=float(ymax-ymin)
                center=np.asarray([(xmin+xmax)/2,(ymin+ymax)/2],dtype=float)
                aspect=width/height
                image_h=1400;image_w=int(np.clip(round(image_h*aspect),900,1800))
                if image_w in (900,1800):image_h=int(np.clip(round(image_w/aspect),900,1800))
                retained=0;visible_lows=[];visible_highs=[]
                for mesh,tri in originals:
                    # Visibility is edited at mesh granularity.  Reauthoring a
                    # triangle subset can invalidate face-varying material data
                    # in imported assets and has produced visually blank maps.
                    # Geometry maps still use exact triangle rasterization; this
                    # capture is evidence for Flash, so preserve intact meshes.
                    tri_min=tri.min(1);tri_max=tri.max(1)
                    keep_height=(tri_max[:,2]>=z-.04)&(tri_min[:,2]<=maximum)
                    keep_xy=((tri_max[:,0]>=xmin)&(tri_min[:,0]<=xmax)&
                             (tri_max[:,1]>=ymin)&(tri_min[:,1]<=ymax))
                    keep=keep_height&keep_xy
                    retained+=int(keep.sum())
                    mesh.GetVisibilityAttr().Set("inherited" if keep.any() else "invisible")
                    if keep.any():
                        # The intact mesh is rendered, so clipping must cover
                        # all of that mesh, not just the retained helper faces.
                        visible_lows.append(float(tri[:,:,2].min()))
                        visible_highs.append(float(tri[:,:,2].max()))
                if not retained:
                    raise RuntimeError(f"no faces in capture ROI/band: {scene_id}/{level_id}")
                visible_low=min(visible_lows);visible_high=max(visible_highs)
                visible_span=max(visible_high-visible_low,.1)
                target=(float(center[0]),float(center[1]),float(z+min(maximum-z,.9)*.45))
                cam.CreateProjectionAttr("orthographic").Set("orthographic")
                cam.CreateHorizontalApertureAttr(max(width/meters,1.0)*10).Set(max(width/meters,1.0)*10)
                cam.CreateVerticalApertureAttr(max(height/meters,1.0)*10).Set(max(height/meters,1.0)*10)
                viewport.set_texture_resolution((image_w,image_h))
                top_eye=(float(center[0]),float(center[1]),
                         float(visible_high+max(visible_span,5.0)))
                top_clipping=set_look_at(top_eye,target,(0.0,1.0,0.0),
                                         (xmin,ymin,xmax,ymax),(visible_low,visible_high))
                output=out/f"{level_id}-material-top.png";capture=capture_view(output)
                oblique=[];oblique_clipping={}
                cam.GetProjectionAttr().Set("perspective")
                cam.CreateFocalLengthAttr(22.0).Set(22.0)
                cam.GetHorizontalApertureAttr().Set(20.955)
                cam.GetVerticalApertureAttr().Set(20.955)
                viewport.set_texture_resolution((1400,1100))
                radius=max(width,height)*.72+max(float(maximum-z),2.0)*1.4
                eye_z=max(float(z+max(maximum-z,2.0)*1.6+max(width,height)*.35),
                          float(visible_high+max(1.0,visible_span*.05)))
                for suffix,dx,dy in (("ne",1,1),("nw",-1,1),("sw",-1,-1),("se",1,-1)):
                    eye=(float(center[0]+dx*radius),float(center[1]+dy*radius),eye_z)
                    oblique_clipping[suffix]=set_look_at(
                        eye,target,(0.0,0.0,1.0),(xmin,ymin,xmax,ymax),
                        (visible_low,visible_high))
                    oblique_path=out/f"{level_id}-material-{suffix}.png"
                    capture=capture_view(oblique_path);oblique.append(str(oblique_path))
                extent=[float(xmin),float(xmax),float(ymin),float(ymax)]
                expected_by_level[level_id]=[(output,(image_w,image_h))]+[
                    (Path(path),(1400,1100)) for path in oblique]
                stats.append({"level":level_id,"floor_z":z,"max_z":maximum,"faces":retained,
                    "image":str(output),"projection":"orthographic X-right Y-up",
                    "oblique_images":oblique,
                    "center_xy":[float(center[0]),float(center[1])],"width_m":width,"height_m":height,
                    "extent_xy":extent,"capture_bounds_xy":[float(xmin),float(ymin),float(xmax),float(ymax)],
                    "bounds_source":bounds_source,"image_to_world":_image_to_world(extent),
                    "top_view_direction":{"image_right":"+X","image_down":"-Y",
                                          "camera_forward":"-Z","camera_up":"+Y"},
                    "pixel_size":[image_w,image_h],"camera_light":True,
                    "top_clipping_range_m":top_clipping,
                    "oblique_clipping_range_m":oblique_clipping})
                print("CAPTURE",scene_id,level_id,output,*oblique,flush=True);capture=None
            image_metadata=_validate_capture_set(expected_by_level)
            for view in stats:
                rows=image_metadata[view["level"]]
                hashes={row["path"]:row["sha256"] for row in rows}
                view["image_sha256"]=hashes[view["image"]]
                view["oblique_image_sha256"]={_published_path(path,out,final_out):hashes[path]
                                                for path in view["oblique_images"]}
                view["image"]=_published_path(view["image"],out,final_out)
                view["oblique_images"]=[_published_path(path,out,final_out)
                                          for path in view["oblique_images"]]
            image_metadata={level_id:[{**row,"path":_published_path(row["path"],out,final_out)}
                                      for row in rows]
                            for level_id,rows in image_metadata.items()}
            source_after={path:digest(path) for path in source_before}
            report={"schema":"eai.semantic_capture","version":1,"scene_id":scene_id,
                "visual_usd":str(usd_path),"source_unchanged":source_after==source_before,
                "source_digests":source_before,"views":stats,"camera_light":True,
                "meters_per_unit":meters,"up_axis":up_axis,"capture_contract_version":2,
                "meshes":len(originals),"skipped_nontriangle_or_empty_meshes":skipped,
                "polygon_meshes_fan_triangulated_for_height_test":polygon_meshes,
                "collision_prims_disabled_before_kit_load":disabled,
                "note":"intact mesh/material rendering with fan-triangulated height tests and Camera Light; visual evidence, not occupancy"}
            if not report["source_unchanged"]:raise RuntimeError(f"source changed: {scene_id}")
            capture_report=out/"capture.json";_write_json_atomic(capture_report,report)
            wrapper.unlink(missing_ok=True)
            receipt={"schema":"eai.semantic_capture.complete","version":1,
                "complete":True,"scene_id":scene_id,
                "capture_report":str(final_out/"capture.json"),
                "capture_report_sha256":digest(capture_report),"source_digests":source_after,
                "images":image_metadata,"generator_sha256":digest(Path(__file__))}
            receipt_path=out/"capture-complete.json";_write_json_atomic(receipt_path,receipt)
            _publish_scene_directory(out,final_out)
            receipt_path=final_out/"capture-complete.json"
            if args.hard_exit:
                print("VERIFIED_HARD_EXIT",scene_id,receipt_path,flush=True)
                os._exit(0)
            context.close_stage();stage=None;originals.clear();gc.collect()
            for _ in range(20):app.update()
    finally:
        if context:
            try:context.close_stage()
            except Exception:pass
        gc.collect()
        from simulator import _prepare_gui_for_app_close
        _prepare_gui_for_app_close(app);app.close()


if __name__=="__main__":main()
