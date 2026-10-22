from __future__ import annotations

import math
from collections.abc import Sequence
from contextlib import nullcontext
from typing import Any


def find_first_valid_child(stage: Any, parent_path: str, child_names: Sequence[str]) -> str | None:
    parent_path = parent_path.rstrip("/")
    for child_name in child_names:
        candidate = f"{parent_path}/{child_name}"
        if stage.GetPrimAtPath(candidate).IsValid():
            return candidate
    return None


def find_articulation_root(stage: Any, body_path: str) -> str | None:
    from pxr import UsdPhysics

    prim = stage.GetPrimAtPath(body_path)
    while prim.IsValid() and not prim.IsPseudoRoot():
        if prim.HasAPI(UsdPhysics.ArticulationRootAPI):
            return str(prim.GetPath())
        prim = prim.GetParent()
    return None


def deactivate_root_joint(
    stage: Any,
    articulation_path: str,
    joint_names: Sequence[str] = ("root_joint",),
) -> None:
    root_path = articulation_path.rstrip("/")
    for joint_name in joint_names:
        joint = stage.GetPrimAtPath(f"{root_path}/{joint_name}")
        if joint.IsValid():
            joint.SetActive(False)


def scale_articulation_mass(
    stage: Any,
    articulation_path: str,
    mass_scale: float,
    minimum_mass: float = 1.0e-4,
) -> float:
    from pxr import Gf, Usd, UsdPhysics

    root = stage.GetPrimAtPath(articulation_path)
    if not root.IsValid():
        raise RuntimeError(f"Manipulator root prim does not exist: {articulation_path}")
    if not math.isfinite(mass_scale) or mass_scale <= 0.0:
        raise ValueError(f"Manipulator mass scale must be positive and finite: {mass_scale}")
    if not math.isfinite(minimum_mass) or minimum_mass <= 0.0:
        raise ValueError(f"Manipulator minimum mass must be positive and finite: {minimum_mass}")

    total_mass = 0.0
    for prim in Usd.PrimRange(root):
        if not prim.HasAPI(UsdPhysics.RigidBodyAPI):
            continue
        mass_api = UsdPhysics.MassAPI(prim)
        mass_attr = mass_api.GetMassAttr()
        mass = mass_attr.Get()
        if mass is not None and math.isfinite(float(mass)):
            scaled_mass = float(mass) * mass_scale if float(mass) > 0.0 else minimum_mass
            mass_attr.Set(scaled_mass)
            total_mass += scaled_mass

        inertia_attr = mass_api.GetDiagonalInertiaAttr()
        inertia = inertia_attr.Get()
        if inertia is not None and all(math.isfinite(float(value)) for value in inertia):
            inertia_attr.Set(Gf.Vec3f(*(float(value) * mass_scale for value in inertia)))
    return total_mass


def create_excluded_fixed_joint(
    stage: Any,
    joint_path: str,
    *,
    host_body_path: str,
    arm_body_path: str,
    mount_position: tuple[float, float, float],
    mount_rotation: tuple[float, float, float, float],
) -> Any:
    from pxr import Gf, Sdf, UsdPhysics

    joint = UsdPhysics.FixedJoint.Define(stage, joint_path)
    joint.CreateBody0Rel().SetTargets([Sdf.Path(host_body_path)])
    joint.CreateBody1Rel().SetTargets([Sdf.Path(arm_body_path)])
    joint.CreateLocalPos0Attr().Set(Gf.Vec3f(*mount_position))
    joint.CreateLocalRot0Attr().Set(Gf.Quatf(mount_rotation[0], Gf.Vec3f(*mount_rotation[1:])))
    joint.CreateLocalPos1Attr().Set(Gf.Vec3f(0.0, 0.0, 0.0))
    joint.CreateLocalRot1Attr().Set(Gf.Quatf(1.0, Gf.Vec3f(0.0, 0.0, 0.0)))
    joint.CreateExcludeFromArticulationAttr().Set(True)
    return joint


def spawn_mounted_arm_instance(
    stage: Any,
    *,
    host_path: str,
    arm_path: str,
    cfg: Any,
    mount_body_path: str,
    mount_position: tuple[float, float, float],
    mount_rotation: tuple[float, float, float, float],
    mass_scale: float,
    enable_self_collisions: bool,
    root_joint_names: Sequence[str],
    base_link_names: Sequence[str],
    set_force_drives: Any,
    label: str,
) -> str:
    """Spawn one arm USD anchored to a host body and return its base link path.

    Shared mechanics of the ``{name}`` host + ``{name}_arm`` articulation
    pattern: spawn the arm at the host mount anchor, neutralize the arm's own
    fixed root joints, move its ArticulationRootAPI onto the first base link,
    and weld that link to the host articulation root with an excluded fixed
    joint.  Asset-specific knowledge stays with the caller through
    ``root_joint_names``, ``base_link_names`` and the ``set_force_drives``
    callback.  ``isaaclab`` is imported lazily so this module stays loadable
    without an Isaac runtime.
    """
    import isaaclab.sim as sim_utils
    from pxr import Gf, UsdGeom, UsdPhysics

    base_cfg = sim_utils.UsdFileCfg(
        usd_path=cfg.usd_path,
        articulation_props=sim_utils.ArticulationRootPropertiesCfg(
            enabled_self_collisions=enable_self_collisions,
            fix_root_link=False,
            solver_position_iteration_count=64,
            solver_velocity_iteration_count=4,
        ),
        activate_contact_sensors=False,
    )
    mount_rotation_quat = Gf.Quatd(mount_rotation[0], Gf.Vec3d(*mount_rotation[1:]))
    host_body_path = f"{host_path}/{mount_body_path}"
    host_body = stage.GetPrimAtPath(host_body_path)
    if not host_body.IsValid():
        raise RuntimeError(f"{host_path}: mount body does not exist: {host_body_path}")
    joint_path = None
    try:
        host_world = UsdGeom.Xformable(host_body).ComputeLocalToWorldTransform(0)
        anchor_world = host_world.Transform(Gf.Vec3d(*mount_position))
        arm_rotation = host_world.ExtractRotationQuat() * mount_rotation_quat
        stage_scope = (
            sim_utils.use_stage(stage)
            if callable(getattr(sim_utils, "use_stage", None))
            else nullcontext()
        )
        with stage_scope:
            sim_utils.spawn_from_usd(
                arm_path,
                base_cfg,
                translation=tuple(anchor_world),
                orientation=(arm_rotation.GetReal(), *arm_rotation.GetImaginary()),
            )
        deactivate_root_joint(stage, arm_path, joint_names=tuple(root_joint_names))
        world_prim = stage.GetPrimAtPath(f"{arm_path}/world")
        if world_prim.IsValid():
            stage.GetPrimAtPath(f"{arm_path}/world").SetActive(False)
        set_force_drives(stage, arm_path)
        scale_articulation_mass(stage, arm_path, mass_scale)
        arm_base_path = find_first_valid_child(stage, arm_path, tuple(base_link_names))
        if arm_base_path is None:
            raise RuntimeError(f"{host_path}: {label} base link does not exist under {arm_path}")
        UsdPhysics.ArticulationRootAPI.Apply(stage.GetPrimAtPath(arm_base_path))
        sim_utils.modify_articulation_root_properties(
            arm_base_path, base_cfg.articulation_props, stage=stage
        )
        host_root = find_articulation_root(stage, host_body_path)
        if host_root is None:
            raise RuntimeError(f"{host_path}: host articulation root not found")
        joint_path = f"{host_root}/arm_fixed_joint_{host_path.rsplit('/', 1)[-1]}"
        create_excluded_fixed_joint(
            stage,
            joint_path,
            host_body_path=host_body_path,
            arm_body_path=arm_base_path,
            mount_position=mount_position,
            mount_rotation=mount_rotation,
        )
        return arm_base_path
    except Exception:
        if joint_path and stage.GetPrimAtPath(joint_path).IsValid():
            stage.RemovePrim(joint_path)
        if stage.GetPrimAtPath(arm_path).IsValid():
            stage.RemovePrim(arm_path)
        raise
