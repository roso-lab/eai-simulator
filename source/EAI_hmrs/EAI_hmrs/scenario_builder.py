"""Compile scenario entities into native USD-backed scene assets.

NVD props are authored in centimetre stages and commonly contain visual
meshes only.  Isaac Lab's regular ``UsdFileCfg`` does not convert stage units
and only modifies physics APIs that already exist below the referenced prim.
This module closes that gap without replacing native geometry with a box.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from EAI_assets.scenario_assets import ASSETS


@dataclass(frozen=True)
class NativeUsdGeometry:
    source_meters_per_unit: float
    source_up_axis: str
    bbox_min: tuple[float, float, float]
    bbox_max: tuple[float, float, float]
    bbox_center: tuple[float, float, float]
    bbox_size: tuple[float, float, float]


def inspect_native_usd(usd_path: str | Path, *, load: bool = True) -> NativeUsdGeometry:
    """Inspect a native USD asset without modifying it."""
    from pxr import Usd, UsdGeom

    path = Path(usd_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    stage = Usd.Stage.Open(str(path), load=Usd.Stage.LoadAll if load else Usd.Stage.LoadNone)
    if stage is None:
        raise ValueError(f"could not open native USD asset: {path}")
    root = stage.GetDefaultPrim()
    if not root or not root.IsValid():
        root = next((prim for prim in stage.Traverse() if prim.IsA(UsdGeom.Xformable)), None)
    if root is None or not root.IsValid():
        raise ValueError(f"native USD asset has no xformable default root: {path}")
    cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy, UsdGeom.Tokens.guide],
        useExtentsHint=False,
    )
    aligned = cache.ComputeWorldBound(root).ComputeAlignedRange()
    minimum = tuple(float(value) for value in aligned.GetMin())
    maximum = tuple(float(value) for value in aligned.GetMax())
    size = tuple(maximum[i] - minimum[i] for i in range(3))
    if any(not math.isfinite(value) or value <= 0.0 for value in size):
        raise ValueError(f"native USD asset has invalid geometry bounds: {path}: {size}")
    meters_per_unit = float(UsdGeom.GetStageMetersPerUnit(stage))
    if not math.isfinite(meters_per_unit) or meters_per_unit <= 0.0:
        raise ValueError(f"native USD asset has invalid metersPerUnit: {path}: {meters_per_unit}")
    return NativeUsdGeometry(
        source_meters_per_unit=meters_per_unit,
        source_up_axis=str(UsdGeom.GetStageUpAxis(stage)),
        bbox_min=minimum,
        bbox_max=maximum,
        bbox_center=tuple((minimum[i] + maximum[i]) / 2.0 for i in range(3)),
        bbox_size=size,
    )


def _quat_multiply(left: tuple[float, float, float, float], right: tuple[float, float, float, float]):
    lw, lx, ly, lz = left
    rw, rx, ry, rz = right
    return (lw * rw - lx * rx - ly * ry - lz * rz,
            lw * rx + lx * rw + ly * rz - lz * ry,
            lw * ry - lx * rz + ly * rw + lz * rx,
            lw * rz + lx * ry - ly * rx + lz * rw)


def _quat_rotate(quaternion: tuple[float, float, float, float], vector: tuple[float, float, float]):
    pure = (0.0, vector[0], vector[1], vector[2])
    inverse = (quaternion[0], -quaternion[1], -quaternion[2], -quaternion[3])
    return _quat_multiply(_quat_multiply(quaternion, pure), inverse)[1:]


def _axis_quaternion(source_up: str, target_up: str) -> tuple[float, float, float, float]:
    """Return a deterministic rotation mapping source up to target up."""
    axes = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}
    source, target = axes.get(str(source_up).upper()), axes.get(str(target_up).upper())
    if source is None or target is None:
        raise ValueError(f"unsupported USD up axis conversion: {source_up!r} -> {target_up!r}")
    dot = sum(source[i] * target[i] for i in range(3))
    cross = (source[1] * target[2] - source[2] * target[1],
             source[2] * target[0] - source[0] * target[2],
             source[0] * target[1] - source[1] * target[0])
    if dot > 1.0 - 1e-12:
        return (1.0, 0.0, 0.0, 0.0)
    if dot < -1.0 + 1e-12:
        orthogonal = (1.0, 0.0, 0.0) if abs(source[0]) < 0.5 else (0.0, 1.0, 0.0)
        axis = (source[1] * orthogonal[2] - source[2] * orthogonal[1],
                source[2] * orthogonal[0] - source[0] * orthogonal[2],
                source[0] * orthogonal[1] - source[1] * orthogonal[0])
        length = math.sqrt(sum(value * value for value in axis))
        return (0.0, axis[0] / length, axis[1] / length, axis[2] / length)
    scale = math.sqrt(2.0 * (1.0 + dot))
    quaternion = (scale / 2.0, cross[0] / scale, cross[1] / scale, cross[2] / scale)
    norm = math.sqrt(sum(value * value for value in quaternion))
    return tuple(value / norm for value in quaternion)


def native_spawn_transform(geometry: NativeUsdGeometry, *, target_meters_per_unit: float,
                           target_up_axis: str, desired_position: tuple[float, float, float],
                           desired_orientation: tuple[float, float, float, float]) -> dict[str, tuple[float, ...]]:
    """Compute unit-correct reference scale and a bbox-centred root pose."""
    if not math.isfinite(target_meters_per_unit) or target_meters_per_unit <= 0.0:
        raise ValueError("target_meters_per_unit must be positive and finite")
    scale = geometry.source_meters_per_unit / target_meters_per_unit
    axis_rotation = _axis_quaternion(geometry.source_up_axis, target_up_axis)
    orientation = _quat_multiply(tuple(float(v) for v in desired_orientation), axis_rotation)
    offset = _quat_rotate(orientation, tuple(scale * v for v in geometry.bbox_center))
    translation = tuple(float(desired_position[i]) - offset[i] for i in range(3))
    return {"translation": translation, "orientation": tuple(float(v) for v in orientation),
            "scale": (scale, scale, scale), "bbox_size_m": tuple(scale * value for value in geometry.bbox_size),
            "bbox_center_world": tuple(float(v) for v in desired_position)}


def author_native_physics(stage: Any, root_path: str, *, dynamic: bool, kinematic: bool,
                          mass_kg: float | None, static_friction: float, dynamic_friction: float,
                          restitution: float, collision_props: Any | None = None,
                          rigid_props: Any | None = None, mass_props: Any | None = None) -> dict[str, Any]:
    """Author rigid/mass/collision/material APIs while retaining native meshes."""
    from pxr import UsdGeom, UsdPhysics, UsdShade

    root = stage.GetPrimAtPath(root_path)
    if not root.IsValid():
        raise ValueError(f"native asset root does not exist: {root_path}")
    if dynamic:
        UsdPhysics.RigidBodyAPI.Apply(root)
        rigid = UsdPhysics.RigidBodyAPI(root)
        rigid.CreateRigidBodyEnabledAttr().Set(True)
        rigid.CreateKinematicEnabledAttr().Set(bool(kinematic))
        if mass_kg is not None:
            UsdPhysics.MassAPI.Apply(root)
            UsdPhysics.MassAPI(root).CreateMassAttr().Set(float(mass_kg))
    meshes = [prim for prim in stage.Traverse()
              if prim.IsA(UsdGeom.Mesh) and prim.GetPath().HasPrefix(root.GetPath())]
    if not meshes and dynamic:
        raise ValueError(f"native asset has no mesh descendants for collision: {root_path}")
    material = None
    if meshes and static_friction is not None:
        material = UsdShade.Material.Define(stage, f"{root_path}/__eai_physics_material")
        material_api = UsdPhysics.MaterialAPI.Apply(material.GetPrim())
        material_api.CreateStaticFrictionAttr().Set(float(static_friction))
        material_api.CreateDynamicFrictionAttr().Set(float(dynamic_friction))
        material_api.CreateRestitutionAttr().Set(float(restitution))
    try:
        from isaaclab.sim import schemas as schemas_module
    except ImportError:
        schemas_module = None
    for mesh in meshes:
        UsdPhysics.CollisionAPI.Apply(mesh)
        UsdPhysics.MeshCollisionAPI.Apply(mesh).CreateApproximationAttr().Set("convexDecomposition")
        if schemas_module is not None and collision_props is not None:
            schemas_module.modify_collision_properties(str(mesh.GetPath()), collision_props, stage=stage)
        if material is not None:
            UsdShade.MaterialBindingAPI.Apply(mesh).Bind(
                material, bindingStrength=UsdShade.Tokens.strongerThanDescendants, materialPurpose="physics")
    if schemas_module is not None:
        if dynamic and rigid_props is not None:
            schemas_module.modify_rigid_body_properties(root_path, rigid_props, stage=stage)
        if dynamic and mass_props is not None:
            schemas_module.modify_mass_properties(root_path, mass_props, stage=stage)
    return {"root": root_path, "mesh_count": len(meshes),
            "collision_meshes": [str(mesh.GetPath()) for mesh in meshes],
            "physics_material": str(material.GetPath()) if material is not None else None,
            "native_collision_approximation": "convexDecomposition"}


def _spawn_native_usd(prim_path: str, cfg: Any, translation=None, orientation=None, **kwargs):
    """Isaac Lab spawner callback for a measured native prop."""
    import isaaclab.sim as sim_utils
    from pxr import UsdGeom

    stage = sim_utils.get_current_stage()
    geometry = inspect_native_usd(cfg.usd_path)
    transform = native_spawn_transform(
        geometry, target_meters_per_unit=float(UsdGeom.GetStageMetersPerUnit(stage)),
        target_up_axis=str(UsdGeom.GetStageUpAxis(stage)),
        desired_position=tuple(translation or (0.0, 0.0, 0.0)),
        desired_orientation=tuple(orientation or (1.0, 0.0, 0.0, 0.0)))
    root = sim_utils.create_prim(
        prim_path, usd_path=cfg.usd_path, stage=stage,
        translation=transform["translation"], orientation=transform["orientation"],
        scale=transform["scale"],
    )
    if hasattr(cfg, "semantic_tags") and cfg.semantic_tags:
        from isaaclab.sim.utils import add_labels
        for semantic_type, semantic_value in cfg.semantic_tags:
            add_labels(root, labels=[str(semantic_value).replace(" ", "_")],
                       instance_name=str(semantic_type).replace(" ", "_"))
    if hasattr(cfg, "visible"):
        sim_utils.set_prim_visibility(root, bool(cfg.visible))
    if getattr(cfg, "native_physics", None) is not None:
        author_native_physics(stage, prim_path, **cfg.native_physics)
    return root


def add_scenario_entities(attrs, composition):
    import isaaclab.sim as sim_utils
    from isaaclab.assets import AssetBaseCfg, RigidObjectCfg
    from isaaclab.sim.utils import clone
    from isaaclab.utils import configclass
    from EAI_assets.scenario_assets import resolve_usd_path

    # InteractiveScene passes ``{ENV_REGEX_NS}`` as a regular-expression prim
    # path.  Match Isaac Lab's built-in file spawner semantics so the first
    # native prop is authored once and then cloned into every environment.
    spawn_native_usd = clone(_spawn_native_usd)

    @configclass
    class NativeUsdFileCfg(sim_utils.UsdFileCfg):
        native_physics: object | None = None

    for entity in composition["entities"]:
        asset_key = entity["asset_key"]
        if asset_key not in ASSETS:
            raise FileNotFoundError(
                f"scenario asset {asset_key!r} is registered but unavailable; "
                "prepare its native USD closure and set EAI_NVD_ASSET_ROOT to that shadow root"
            )
        profile = ASSETS[asset_key]
        physics = entity["physics"]
        mode = physics["mode"]
        dynamic = mode in {"dynamic", "kinematic"}
        usd_path = resolve_usd_path(asset_key)
        if usd_path is not None:
            mass = physics["mass_kg"] or profile.suggested_mass_kg or 1.0
            spawn = NativeUsdFileCfg(
                usd_path=str(usd_path), func=spawn_native_usd,
                rigid_props=(sim_utils.RigidBodyPropertiesCfg(
                    rigid_body_enabled=True, kinematic_enabled=mode == "kinematic", disable_gravity=False) if dynamic else None),
                mass_props=sim_utils.MassPropertiesCfg(mass=mass) if dynamic else None,
                collision_props=(sim_utils.CollisionPropertiesCfg(collision_enabled=True)
                                if mode != "visual" else None),
                native_physics=({"dynamic": dynamic, "kinematic": mode == "kinematic",
                                 "mass_kg": mass if dynamic else None,
                                 "static_friction": physics["static_friction"],
                                 "dynamic_friction": physics["dynamic_friction"],
                                 "restitution": physics["restitution"],
                                 "collision_props": sim_utils.CollisionPropertiesCfg(collision_enabled=True),
                                 "rigid_props": (sim_utils.RigidBodyPropertiesCfg(
                                     rigid_body_enabled=True, kinematic_enabled=mode == "kinematic", disable_gravity=False) if dynamic else None),
                                 "mass_props": sim_utils.MassPropertiesCfg(mass=mass) if dynamic else None}
                                if mode != "visual" else None))
        else:
            spawn = sim_utils.CuboidCfg(
                size=tuple(entity["size_m"]), visual_material=sim_utils.PreviewSurfaceCfg(diffuse_color=profile.color),
                rigid_props=(sim_utils.RigidBodyPropertiesCfg(
                    rigid_body_enabled=True, kinematic_enabled=mode == "kinematic", disable_gravity=False) if dynamic else None),
                mass_props=sim_utils.MassPropertiesCfg(mass=physics["mass_kg"]) if dynamic else None,
                collision_props=(sim_utils.CollisionPropertiesCfg(collision_enabled=True) if mode != "visual" else None),
                physics_material=(sim_utils.RigidBodyMaterialCfg(
                    static_friction=physics["static_friction"], dynamic_friction=physics["dynamic_friction"],
                    restitution=physics["restitution"], friction_combine_mode="average", restitution_combine_mode="average")
                    if mode != "visual" else None))
        cls = RigidObjectCfg if dynamic else AssetBaseCfg
        key = f"scenario_{entity['id']}"
        attrs["__annotations__"][key] = cls
        attrs[key] = cls(prim_path=f"{{ENV_REGEX_NS}}/{key}", spawn=spawn,
                         init_state=cls.InitialStateCfg(pos=tuple(entity["spawn_pose"]["position"]),
                                                        rot=tuple(entity["spawn_pose"]["rotation"])))


__all__ = ["NativeUsdGeometry", "inspect_native_usd", "native_spawn_transform",
           "author_native_physics", "add_scenario_entities"]
