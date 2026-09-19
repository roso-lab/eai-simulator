"""Native goal props: configuration, USD geometry, and separate PhysX readback.

No simulator is started here. Call configure_goal_objects before scene creation;
the attached spawner runs in the caller-owned Isaac session. USD schema values
are authored configuration, never substituted for live PhysX mass or pose.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import math
from pathlib import Path
import re


_NATIVE = {
    "order_crate_01": "wooden_crate_a", "order_carton_01": "cardbox_c",
    "living_armchair_01": "mh_armchair", "resident_bookstack_01": "mh_bookstack",
}


def _sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _confirm(files):
    for path, digest in files.items():
        if not Path(path).is_file() or _sha(path) != digest:
            raise ValueError(f"native object source missing or changed: {path}")


def _validate(deployment):
    from .goal_deployment import validate_goal_deployment
    validate_goal_deployment(deployment)


def _vector(value, count, label, *, positive=False):
    if (not isinstance(value, (tuple, list)) or len(value) != count
            or any(isinstance(x, bool) or not isinstance(x, (int, float))
                   or not math.isfinite(x) or (positive and x <= 0) for x in value)):
        raise ValueError(f"invalid {label}")
    return [float(x) for x in value]


def _bounds(stage, geometry_root, *, relative_to=None):
    """Bounds of composed mesh points, ignoring potentially stale extent metadata."""
    from pxr import Gf, Usd, UsdGeom
    cache = UsdGeom.XformCache(Usd.TimeCode.Default())
    inverse = (cache.GetLocalToWorldTransform(relative_to).GetInverse()
               if relative_to is not None else Gf.Matrix4d(1))
    low, high = [math.inf] * 3, [-math.inf] * 3
    meshes = []
    for prim in Usd.PrimRange(geometry_root):
        if not prim.IsA(UsdGeom.Mesh):
            continue
        mesh = UsdGeom.Mesh(prim)
        points = mesh.GetPointsAttr().Get()
        if not points:
            raise ValueError(f"mesh has no default geometry points: {prim.GetPath()}")
        if mesh.GetPointsAttr().ValueMightBeTimeVarying():
            raise ValueError("animated native mesh geometry is unsupported")
        transform = cache.GetLocalToWorldTransform(prim) * inverse
        for point in points:
            value = transform.Transform(Gf.Vec3d(*point))
            if not all(math.isfinite(x) for x in value):
                raise ValueError("nonfinite native geometry")
            low = [min(low[i], value[i]) for i in range(3)]
            high = [max(high[i], value[i]) for i in range(3)]
        meshes.append(str(prim.GetPath()))
    size = [high[i] - low[i] for i in range(3)]
    if not meshes or any(not math.isfinite(x) or x <= 0 for x in size):
        raise ValueError("native asset has no nondegenerate composed mesh volume")
    return {"min": low, "max": high, "size": size,
            "center": [(low[i] + high[i]) / 2 for i in range(3)], "meshes": meshes}


def _asset_path(row, deployment):
    directive = row["scene_binding"].get("deployment_directive", {})
    asset = directive.get("asset", {})
    if (row["public_id"] not in _NATIVE
            or asset.get("asset_key") != _NATIVE[row["public_id"]]
            or asset.get("kind") != "native_usd_asset_with_public_parameter_overrides"):
        raise ValueError("native_asset_binding_missing_no_proxy_substitution")
    relative = Path(asset["usd_rel"])
    if relative.is_absolute() or ".." in relative.parts or len(relative.parts) < 2:
        raise ValueError("invalid native source relative path")
    # The bound absolute file is explicit; package directory spelling is not
    # treated as a runtime registry alias. Ambiguous candidates fail closed.
    suffix = Path(*relative.parts[1:]).as_posix()
    candidates = [Path(path) for path in deployment["scene_overlay"]["source_files"]
                  if path.endswith("/" + suffix)]
    if len(candidates) != 1:
        raise ValueError("native source requires exactly one source-bound local USD")
    return candidates[0]


def _composition_errors(stage, root=None):
    """USD24 (Kit5.1) exposes composition errors through each Pcp prim index."""
    from pxr import Usd
    if root is None and hasattr(stage, "GetCompositionErrors"):
        return list(stage.GetCompositionErrors())
    prims = stage.TraverseAll() if root is None else Usd.PrimRange(root)
    return [error for prim in prims for error in prim.GetPrimIndex().localErrors]


def _reference(stage, path, source_path, source_root, source_up):
    from pxr import Gf, UsdGeom
    geometry = UsdGeom.Xform.Define(stage, path)
    axis = UsdGeom.Xform.Define(stage, path + "/Axis")
    if source_up == "Y":
        axis.AddRotateXOp().Set(90.0)
    elif source_up != "Z":
        raise ValueError(f"unsupported source up axis: {source_up}")
    native = stage.DefinePrim(path + "/Axis/Native")
    native.GetReferences().AddReference(str(source_path), source_root)
    if _composition_errors(stage, native):
        raise ValueError("unresolved native USD composition")
    return geometry


def _prepare(row, deployment):
    from pxr import Usd, UsdGeom, UsdPhysics
    path = _asset_path(row, deployment)
    physical = deepcopy(row["physical_properties"])
    size = _vector(physical["size_m"], 3, "public size", positive=True)
    mass = _vector([physical["mass_kg"]], 1, "public mass", positive=True)[0]
    directive = row["scene_binding"]["deployment_directive"]
    for key, value in physical.items():
        if directive.get(key) != value:
            raise ValueError(f"directive differs from frozen object parameters: {key}")
    if row["scene_binding"].get("status") == "blocked":
        raise ValueError("object deployment candidate is blocked")
    runtime_id = row["runtime_id"]
    if (not re.fullmatch(r"goal_object_[1-9][0-9]*", runtime_id)
            or row["prim_path"] != f"/World/envs/env_0/{runtime_id}"):
        raise ValueError("object runtime identity/path mismatch")
    source_digest = _sha(path)
    if source_digest != deployment["scene_overlay"]["source_files"][str(path)]:
        raise ValueError("native source differs from deployment binding")
    source = Usd.Stage.Open(str(path), load=Usd.Stage.LoadAll)
    if source is None or _composition_errors(source):
        raise ValueError("native USD composition unavailable")
    root = source.GetDefaultPrim()
    if not root:
        raise ValueError("native USD requires an explicit default prim")
    source_up = str(UsdGeom.GetStageUpAxis(source))
    source_units = float(UsdGeom.GetStageMetersPerUnit(source))
    if not math.isfinite(source_units) or source_units <= 0:
        raise ValueError("invalid native USD units")
    probe = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(probe, 1.0)
    UsdGeom.SetStageUpAxis(probe, "Z")
    geometry = _reference(probe, "/Geometry", path, str(root.GetPath()), source_up)
    for prim in Usd.PrimRange(geometry.GetPrim()):
        if (prim.HasAPI(UsdPhysics.RigidBodyAPI) or prim.HasAPI(UsdPhysics.ArticulationRootAPI)
                or prim.IsA(UsdPhysics.Joint)):
            raise ValueError("nested native rigid bodies or joints require separate binding")
        if UsdGeom.Xformable(prim) and UsdGeom.Xformable(prim).GetResetXformStack():
            raise ValueError("native resetXformStack bypasses normalization")
        if prim.IsInstance():
            raise ValueError("instanced native geometry requires explicit authoring support")
    files = {str(path.resolve()): source_digest}
    for layer in (*source.GetUsedLayers(), *probe.GetUsedLayers()):
        if not layer.anonymous:
            if not layer.realPath or not Path(layer.realPath).is_file():
                raise ValueError("native composition contains a nonlocal layer")
            files.setdefault(str(Path(layer.realPath).resolve()), _sha(layer.realPath))
    measured = _bounds(probe, geometry.GetPrim())
    _confirm(files)
    points = physical.get("grasp_points", [])
    ids = set()
    for point in points:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", point["id"]) or point["id"] in ids:
            raise ValueError("invalid or repeated fixed point id")
        ids.add(point["id"])
        _vector(point["position_m"], 3, "fixed point")
    rotation = _vector(directive["rotation_wxyz"], 4, "object rotation")
    if not math.isclose(sum(x*x for x in rotation), 1.0, abs_tol=1e-6):
        raise ValueError("object rotation must be a unit quaternion")
    return {
        "public_id": row["public_id"], "runtime_id": runtime_id, "prim_path": row["prim_path"],
        "source_path": str(path.resolve()), "source_root": str(root.GetPath()),
        "source_up_axis": source_up, "source_meters_per_unit": source_units,
        "source_files": files, "composed_axis_aligned_bounds_source_units": measured,
        "geometry_scale_xyz": [size[i] / measured["size"][i] for i in range(3)],
        "geometry_center_source_units": measured["center"], "size_m": size, "mass_kg": mass,
        "position_m": _vector(directive["position_m"], 3, "object position"),
        "rotation_wxyz": rotation,
        "grasp_points": deepcopy(points),
    }


def _author_object(stage, root_path, plan, *, translation=None, orientation=None):
    """Spawner core, also exercisable with standalone OpenUSD."""
    from pxr import Gf, Usd, UsdGeom, UsdPhysics
    _confirm(plan["source_files"])
    if str(UsdGeom.GetStageUpAxis(stage)) != "Z" or not math.isclose(
            UsdGeom.GetStageMetersPerUnit(stage), 1.0, abs_tol=1e-12):
        raise ValueError("goal object target stage requires meters and Z up")
    if stage.GetPrimAtPath(root_path):
        raise ValueError(f"refusing to replace existing goal object: {root_path}")
    position = plan["position_m"] if translation is None else translation
    rotation = plan["rotation_wxyz"] if orientation is None else orientation
    root = UsdGeom.Xform.Define(stage, root_path)
    root.AddTranslateOp().Set(Gf.Vec3d(*position))
    root.AddOrientOp().Set(Gf.Quatf(rotation[0], Gf.Vec3f(*rotation[1:])))
    geometry = _reference(stage, root_path + "/Geometry", plan["source_path"],
                          plan["source_root"], plan["source_up_axis"])
    scale = plan["geometry_scale_xyz"]
    geometry.AddTranslateOp().Set(Gf.Vec3d(*[-plan["geometry_center_source_units"][i] * scale[i]
                                          for i in range(3)]))
    geometry.AddScaleOp(precision=UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(*scale))
    rigid = UsdPhysics.RigidBodyAPI.Apply(root.GetPrim())
    rigid.CreateRigidBodyEnabledAttr().Set(True)
    rigid.CreateKinematicEnabledAttr().Set(False)
    UsdPhysics.MassAPI.Apply(root.GetPrim()).CreateMassAttr().Set(plan["mass_kg"])
    for prim in Usd.PrimRange(geometry.GetPrim()):
        if prim.IsA(UsdGeom.Mesh):
            UsdPhysics.CollisionAPI.Apply(prim).CreateCollisionEnabledAttr().Set(True)
            UsdPhysics.MeshCollisionAPI.Apply(prim).CreateApproximationAttr().Set("convexHull")
    for point in plan["grasp_points"]:
        grasp = UsdGeom.Xform.Define(stage, f"{root_path}/GraspPoints/{point['id']}")
        grasp.AddTranslateOp().Set(Gf.Vec3d(*point["position_m"]))
        # Interface metadata declares an intended connector, not a live joint.
        grasp.GetPrim().SetCustomDataByKey("goal_interface", point["interface"])
    _confirm(plan["source_files"])
    return root.GetPrim()


def _spawn_goal_object(prim_path, cfg, translation=None, orientation=None, **kwargs):
    import isaaclab.sim as sim_utils
    return _author_object(sim_utils.get_current_stage(), prim_path, cfg.goal_object_plan,
                          translation=translation, orientation=orientation)


def _make_cfg(plan):
    import isaaclab.sim as sim_utils
    from isaaclab.assets import RigidObjectCfg
    from isaaclab.sim.utils import clone
    from isaaclab.utils import configclass

    @configclass
    class GoalObjectUsdCfg(sim_utils.UsdFileCfg):
        goal_object_plan: object = None

    return RigidObjectCfg(
        prim_path=f"{{ENV_REGEX_NS}}/{plan['runtime_id']}",
        spawn=GoalObjectUsdCfg(usd_path=plan["source_path"], func=clone(_spawn_goal_object),
                               goal_object_plan=deepcopy(plan)),
        init_state=RigidObjectCfg.InitialStateCfg(pos=tuple(plan["position_m"]),
                                                  rot=tuple(plan["rotation_wxyz"])),
    )


def configure_goal_objects(env_cfg, deployment):
    """Attach native RigidObjectCfg entries; return application intent, not physics evidence.

    Scene keys are the compiled runtime IDs. Unknown/non-native objects get
    blocked rows and no cfg. An occupied scene cfg key is never overwritten.
    """
    _validate(deployment)
    bound_files = deployment["scene_overlay"]["source_files"]
    _confirm(bound_files)
    rows, staged = [], []
    sources = {**bound_files, str(Path(__file__).resolve()): _sha(__file__)}
    for row in deployment["object_instances"]:
        result = {"public_id": row["public_id"], "runtime_id": row["runtime_id"],
                  "prim_path": row["prim_path"], "physics_execution_validated": False}
        try:
            plan = _prepare(row, deployment)
        except (ValueError, OSError) as exc:
            rows.append({**result, "status": "blocked", "reason": str(exc)})
            continue
        if hasattr(env_cfg.scene, row["runtime_id"]):
            raise ValueError(f"scene cfg key already exists: {row['runtime_id']}")
        staged.append((row["runtime_id"], _make_cfg(plan)))
        sources.update(plan["source_files"])
        rows.append({**result, "status": "configured", "plan": plan,
                     "scope": "native_mesh_cfg_attached_not_spawned_or_physx_verified"})
    _confirm(sources)
    for key, cfg in staged:
        setattr(env_cfg.scene, key, cfg)
    return {"schema": "eai.goal_native_object_application", "version": 1,
            "deployment_sha256": deployment.get("sha256"), "objects": rows,
            "source_files": sources, "physics_execution_validated": False,
            "scoreable": False}


def _flat(value):
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, (tuple, list)):
        return [item for child in value for item in _flat(child)]
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite PhysX readback")
    return [number]


def _read_physx(scene, row):
    try:
        asset = scene[row["runtime_id"]]
        view = asset.root_physx_view
        paths = list(view.prim_paths)
        if paths.count(row["prim_path"]) != 1:
            return {"status": "mismatch", "reason": "PhysX body identity mismatch", "prim_paths": paths}
        index = paths.index(row["prim_path"])
        mass = _flat(view.get_masses()[index])
        transform = _flat(view.get_transforms()[index])
        if len(mass) != 1 or len(transform) != 7:
            raise ValueError("unexpected PhysX rigid-body tensor shape")
        return {"status": "measured", "source": "root_physx_view",
                "prim_path": paths[index], "mass_kg": mass[0],
                "position_m": transform[:3], "rotation_xyzw": transform[3:],
                "collision_cooking_validated": False}
    except (KeyError, AttributeError, TypeError, RuntimeError, ValueError, IndexError) as exc:
        return {"status": "unknown", "reason": str(exc), "source": "root_physx_view"}


def read_goal_objects(stage, scene, deployment):
    """Read actual mesh points, physics schemas, fixed frames, and live body tensors.

    Returns per-public-ID rows in ``objects``. USD-authored mass is a separate
    field from PhysX mass. Missing live handles remain unknown. Matching values
    do not prove collision cooking, grasp execution, or a business outcome.
    """
    from pxr import Gf, Usd, UsdGeom, UsdPhysics
    rows = []
    meters = float(UsdGeom.GetStageMetersPerUnit(stage))
    for row in deployment["object_instances"]:
        result = {"public_id": row["public_id"], "runtime_id": row["runtime_id"],
                  "prim_path": row["prim_path"], "scoreable": False}
        root = stage.GetPrimAtPath(row["prim_path"])
        if not root:
            rows.append({**result, "status": "blocked", "reason": "goal_object_prim_missing"})
            continue
        if row["public_id"] not in _NATIVE:
            rows.append({**result, "status": "blocked", "reason": "native_binding_missing"})
            continue
        geometry = stage.GetPrimAtPath(row["prim_path"] + "/Geometry")
        if not geometry:
            rows.append({**result, "status": "blocked", "reason": "native_geometry_prim_missing"})
            continue
        mismatches, unknowns = [], []
        native = stage.GetPrimAtPath(row["prim_path"] + "/Geometry/Axis/Native")
        try:
            source_path = _asset_path(row, deployment)
            expected_hash = deployment["scene_overlay"]["source_files"][str(source_path)]
            if _sha(source_path) != expected_hash:
                raise ValueError("source hash differs from deployment")
            if not native or not any(
                    spec.layer.realPath and Path(spec.layer.realPath).resolve() == source_path.resolve()
                    for spec in native.GetPrimStack()):
                raise ValueError("actual native reference is not the source-bound asset")
            native_source = {"path": str(source_path), "sha256": expected_hash,
                             "status": "composed_reference_verified"}
        except (OSError, ValueError) as exc:
            rows.append({**result, "status": "blocked", "reason": str(exc)})
            continue
        if str(UsdGeom.GetStageUpAxis(stage)) != "Z" or not math.isclose(meters, 1.0, abs_tol=1e-12):
            rows.append({**result, "status": "blocked", "reason": "unsupported_live_stage_units_or_axis"})
            continue
        try:
            local = _bounds(stage, geometry, relative_to=root)
            world = _bounds(stage, geometry)
        except ValueError as exc:
            rows.append({**result, "status": "blocked", "reason": str(exc)})
            continue
        expected = row["physical_properties"]
        if any(abs(local["size"][i] - expected["size_m"][i]) > 1e-4 for i in range(3)):
            mismatches.append("mesh_size_differs_from_frozen_size")
        if any(abs(x) > 1e-4 for x in local["center"]):
            mismatches.append("mesh_not_centered_on_rigid_root")
        rigid = UsdPhysics.RigidBodyAPI(root)
        mass = UsdPhysics.MassAPI(root)
        usd_mass = mass.GetMassAttr().Get() if mass else None
        rigid_enabled = rigid.GetRigidBodyEnabledAttr().Get() if rigid else None
        kinematic = rigid.GetKinematicEnabledAttr().Get() if rigid else None
        if rigid_enabled is not True or kinematic is not False:
            mismatches.append("dynamic_rigid_body_not_enabled")
        if usd_mass is None or not math.isclose(usd_mass, expected["mass_kg"], rel_tol=1e-5):
            mismatches.append("usd_mass_differs_from_frozen_mass")
        collisions = []
        for path in local["meshes"]:
            prim = stage.GetPrimAtPath(path)
            api = UsdPhysics.CollisionAPI(prim)
            enabled = api.GetCollisionEnabledAttr().Get() if api else False
            mesh_api = UsdPhysics.MeshCollisionAPI(prim)
            approximation = mesh_api.GetApproximationAttr().Get() if mesh_api else None
            collisions.append({"prim_path": path, "enabled": enabled, "approximation": approximation})
            if enabled is not True or approximation != "convexHull":
                mismatches.append("native_mesh_collision_schema_mismatch:" + path)
        cache = UsdGeom.XformCache(Usd.TimeCode.Default())
        root_world = cache.GetLocalToWorldTransform(root)
        basis = [root_world.TransformDir(Gf.Vec3d(*(1 if j == i else 0 for j in range(3))))
                 for i in range(3)]
        if any(abs(float(basis[i] * basis[j]) - (1 if i == j else 0)) > 1e-5
               for i in range(3) for j in range(3)):
            mismatches.append("rigid_root_world_scale_or_shear_is_not_identity")
        fixed_points = []
        for point in expected["grasp_points"]:
            path = f"{row['prim_path']}/GraspPoints/{point['id']}"
            prim = stage.GetPrimAtPath(path)
            if not prim:
                mismatches.append("fixed_point_missing:" + point["id"])
                fixed_points.append({"id": point["id"], "prim_path": path, "status": "missing"})
                continue
            transform = cache.GetLocalToWorldTransform(prim)
            local_position = list((transform * root_world.GetInverse()).Transform(Gf.Vec3d(0)))
            fixed_points.append({"id": point["id"], "prim_path": path, "status": "usd_measured",
                                 "position_local_m": local_position,
                                 "position_world_usd_m": list(transform.Transform(Gf.Vec3d(0))),
                                 "declared_interface": prim.GetCustomDataByKey("goal_interface"),
                                 "physical_connector_verified": False})
            if any(abs(local_position[i] - point["position_m"][i]) > 1e-5 for i in range(3)):
                mismatches.append("fixed_point_transform_mismatch:" + point["id"])
            if prim.GetCustomDataByKey("goal_interface") != point["interface"]:
                mismatches.append("fixed_point_declared_interface_mismatch:" + point["id"])
        physx = _read_physx(scene, row)
        if physx["status"] == "measured":
            if not math.isclose(physx["mass_kg"], expected["mass_kg"], rel_tol=1e-5):
                mismatches.append("physx_mass_differs_from_frozen_mass")
        elif physx["status"] == "mismatch":
            mismatches.append("physx_body_identity_mismatch")
        else:
            unknowns.append("live_physx_mass_and_pose_unavailable")
        rows.append({**result, "status": "mismatch" if mismatches else "unknown" if unknowns else "measured",
                     "usd": {"scope": "actual_composed_mesh_points_and_authored_physics_schemas",
                             "local_mesh_bounds_m": local, "world_mesh_bounds_m": world,
                             "root_position_m": list(root_world.ExtractTranslation()),
                             "authored_mass_kg": usd_mass, "rigid_body_enabled": rigid_enabled,
                             "kinematic_enabled": kinematic, "collisions": collisions},
                     "physx": physx, "fixed_points": fixed_points,
                     "native_source": native_source,
                     "mismatches": mismatches, "unknowns": unknowns,
                     "collision_cooking_validated": False, "grasp_execution_validated": False})
    return {"schema": "eai.goal_native_object_readback", "version": 1,
            "deployment_sha256": deployment.get("sha256"), "objects": rows,
            "stage_identifier": stage.GetRootLayer().identifier,
            "meters_per_unit": meters, "up_axis": str(UsdGeom.GetStageUpAxis(stage)),
            "scoreable": False}


__all__ = ["configure_goal_objects", "read_goal_objects"]
