"""Experimental unscaled UR5/2F-85 assembly for contact validation.

Assets remain external inputs. The gripper's missing inertial data is estimated:
two outer knuckles receive 9 g each and the base receives the remainder of the
925 g manufacturer assembly mass. PhysX computes missing inertias from shapes.
This assembly is the default UR5 tool, not a hardware-calibrated model.
"""
from __future__ import annotations

from pathlib import Path
import os


GRIPPER_MASS_KG = 0.925
GRIPPER_MASS_SOURCE = "https://blog.robotiq.com/hubfs/support-files/2F-85_2F-140_UR_PDF_2026-03-17.pdf"
GRIPPER_ASSET_RELATIVE_PATH = "payloads/grippers/robotiq_2f85/Robotiq_2F_85_edit.usd"


def default_robotiq_85_usd() -> str:
    configured = os.environ.get("EAI_ROBOTIQ_2F85_USD")
    if configured:
        return str(Path(configured).expanduser().resolve())
    from EAI_assets.asset_resolver import asset_path
    return asset_path(GRIPPER_ASSET_RELATIVE_PATH)


def attach_robotiq_85(stage, *, arm_path: str, gripper_usd: str):
    """Attach a physical gripper to the wrist; never attach the grasped object."""
    import isaaclab.sim as sim_utils
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics

    if not Path(gripper_usd).is_file():
        raise FileNotFoundError(gripper_usd)
    wrist_path = f"{arm_path}/wrist_3_link"
    wrist = stage.GetPrimAtPath(wrist_path)
    if not wrist.IsValid():
        raise ValueError(f"Missing UR5 wrist: {wrist_path}")
    mount_path = f"{arm_path}/robotiq"
    if stage.GetPrimAtPath(mount_path).IsValid():
        raise ValueError(f"Gripper already exists at {mount_path}")
    wrist_matrix = UsdGeom.Xformable(wrist).ComputeLocalToWorldTransform(0)
    rotation = Gf.Quatd(1)
    world_rotation = wrist_matrix.ExtractRotationQuat() * rotation
    world_position = wrist_matrix.Transform(Gf.Vec3d(0, 0, 0))
    parent_matrix = UsdGeom.Xformable(stage.GetPrimAtPath(arm_path)).ComputeLocalToWorldTransform(0)
    local_position = parent_matrix.GetInverse().Transform(world_position)
    local_rotation = parent_matrix.ExtractRotationQuat().GetInverse() * world_rotation
    cfg = sim_utils.UsdFileCfg(usd_path=str(Path(gripper_usd).resolve()))
    cfg.func(mount_path, cfg, translation=tuple(local_position),
             orientation=(local_rotation.GetReal(), *local_rotation.GetImaginary()))
    inner_path = f"{mount_path}/Robotiq_2F_85"
    root = stage.GetPrimAtPath(inner_path)
    root.RemoveAPI(UsdPhysics.ArticulationRootAPI)
    for prim in Usd.PrimRange(stage.GetPrimAtPath(mount_path)):
        if prim.HasAPI(UsdPhysics.ArticulationRootAPI):
            prim.RemoveAPI(UsdPhysics.ArticulationRootAPI)
    joint = UsdPhysics.FixedJoint.Define(stage, f"{arm_path}/joints/gripper_mount")
    joint.CreateBody0Rel().SetTargets([Sdf.Path(wrist_path)])
    joint.CreateBody1Rel().SetTargets([Sdf.Path(f"{inner_path}/base_link")])
    joint.CreateLocalPos0Attr().Set(Gf.Vec3f(0))
    joint.CreateLocalRot0Attr().Set(Gf.Quatf(rotation))
    joint.CreateLocalPos1Attr().Set(Gf.Vec3f(0))
    joint.CreateLocalRot1Attr().Set(Gf.Quatf(1))
    joint.CreateExcludeFromArticulationAttr().Set(False)
    other_mass = 0.0
    for prim in Usd.PrimRange(root):
        if not prim.HasAPI(UsdPhysics.RigidBodyAPI) or prim.GetName() == "base_link":
            continue
        mass_api = UsdPhysics.MassAPI.Apply(prim)
        mass = mass_api.GetMassAttr().Get()
        if not mass:
            if prim.GetName() not in {"left_outer_knuckle", "right_outer_knuckle"}:
                raise ValueError(f"Unexpected gripper body without mass: {prim.GetPath()}")
            mass = 0.009
            mass_api.CreateMassAttr().Set(mass)
        other_mass += float(mass)
    base_mass = GRIPPER_MASS_KG - other_mass
    if base_mass <= 0:
        raise ValueError("Gripper link masses exceed assembly mass")
    UsdPhysics.MassAPI.Apply(stage.GetPrimAtPath(f"{inner_path}/base_link")).CreateMassAttr().Set(base_mass)
    bodies = [prim for prim in Usd.PrimRange(root) if prim.HasAPI(UsdPhysics.RigidBodyAPI)]
    for index, body in enumerate(bodies):
        filtered = UsdPhysics.FilteredPairsAPI.Apply(body).CreateFilteredPairsRel()
        for other in bodies[index + 1:]:
            if {body.GetName(), other.GetName()} != {"left_inner_finger", "right_inner_finger"}:
                filtered.AddTarget(other.GetPath())
    sim_utils.activate_contact_sensors(arm_path)
    return robotiq_85_mount_metadata(stage, arm_path=arm_path)


def robotiq_85_mount_metadata(stage, *, arm_path: str):
    from pxr import UsdPhysics

    inner_path = f"{arm_path}/robotiq/Robotiq_2F_85"
    base = stage.GetPrimAtPath(f"{inner_path}/base_link")
    joint = UsdPhysics.FixedJoint(stage.GetPrimAtPath(f"{arm_path}/joints/gripper_mount"))
    if not base.IsValid() or not joint:
        raise ValueError(f"Missing mounted Robotiq at {arm_path}")
    rotation = joint.GetLocalRot0Attr().Get()
    base_mass = UsdPhysics.MassAPI(base).GetMassAttr().Get()
    return {"root_path": inner_path, "left_contact_path": f"{inner_path}/left_inner_finger",
            "right_contact_path": f"{inner_path}/right_inner_finger", "mass_kg": GRIPPER_MASS_KG,
            "mass_source": GRIPPER_MASS_SOURCE, "base_mass_estimate_kg": base_mass,
            "provenance": {"assembly_mass": "manufacturer_declared",
                           "missing_knuckle_mass": "estimated_0.009_kg_each",
                           "base_mass": "residual_from_assembly_mass",
                           "missing_inertia": "shape_inferred_by_PhysX",
                           "hardware_calibration": "unknown"},
            "internal_linkage_collision_filter": "link pairs except opposing fingertips",
            "inertia_status": "source_or_shape_estimate",
            "mount_rotation_wxyz": (rotation.GetReal(), *rotation.GetImaginary())}
