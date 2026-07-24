from __future__ import annotations

import math
from collections.abc import Sequence
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
