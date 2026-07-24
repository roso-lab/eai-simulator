from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from functools import partial

import isaaclab.sim as sim_utils
from isaaclab.assets import ArticulationCfg
from isaaclab.utils import configclass

from EAI_assets.asset_resolver import asset_path
from EAI_assets.robots.manipulator_mount import (
    create_excluded_fixed_joint,
    deactivate_root_joint,
    find_articulation_root,
    find_first_valid_child,
    scale_articulation_mass,
)
from EAI_assets.robots.z1 import (
    Z1_ARM_ACTUATOR,
    Z1_DEFAULT_JOINT_POS,
    Z1_GRIPPER_ACTUATOR,
    Z1_GRIPPER_JOINT_NAME,
    Z1_JOINT_NAMES,
    build_z1_actuators,
)


Z1_STANDALONE_USD_PATH = asset_path("payloads/manipulators/z1/z1_description.usda")


@dataclass(frozen=True)
class Z1MountProfile:
    robot_type: str
    mount_body_path: str
    mount_position: tuple[float, float, float]
    mount_rotation: tuple[float, float, float, float]
    mass_scale: float
    enable_self_collisions: bool = False


_YAW_PI = (0.0, 0.0, 0.0, 1.0)
Z1_MOUNT_PROFILES = {
    "carter": Z1MountProfile("carter", "Carter/GS_Hub_chassis_link", (0.0, 0.0, 0.480), _YAW_PI, 0.20),
    "go2": Z1MountProfile("go2", "base", (0.0, 0.0, 0.089), _YAW_PI, 0.03),
    "b2": Z1MountProfile("b2", "base_link", (0.0, 0.0, 0.243), _YAW_PI, 0.20),
    "m20": Z1MountProfile("m20", "base_link", (0.0, 0.0, 0.0852), _YAW_PI, 0.10),
    "scout": Z1MountProfile("scout", "base_link", (0.0, 0.0, 0.1162), _YAW_PI, 0.15),
    "lite3": Z1MountProfile("lite3", "TORSO", (0.0, 0.0, 0.073), _YAW_PI, 0.03),
}


def _set_z1_force_drives(stage, arm_path: str) -> None:
    for joint_name in (*Z1_JOINT_NAMES, Z1_GRIPPER_JOINT_NAME):
        joint = stage.GetPrimAtPath(f"{arm_path}/joints/{joint_name}")
        if not joint.IsValid():
            continue
        drive_type = joint.GetAttribute("drive:angular:physics:type")
        if drive_type.IsValid():
            drive_type.Set("force")


def _spawn_mounted_z1_instance(stage, *, host_path: str, arm_path: str, cfg: sim_utils.UsdFileCfg, profile: Z1MountProfile) -> str:
    from pxr import Gf, UsdGeom, UsdPhysics
    base_cfg = sim_utils.UsdFileCfg(
        usd_path=cfg.usd_path,
        articulation_props=sim_utils.ArticulationRootPropertiesCfg(
            enabled_self_collisions=profile.enable_self_collisions,
            fix_root_link=False,
        ),
        activate_contact_sensors=False,
    )
    mount_rotation = Gf.Quatd(profile.mount_rotation[0], Gf.Vec3d(*profile.mount_rotation[1:]))
    host_body_path = f"{host_path}/{profile.mount_body_path}"
    host_body = stage.GetPrimAtPath(host_body_path)
    if not host_body.IsValid():
        raise RuntimeError(f"{host_path}: mount body does not exist: {host_body_path}")
    joint_path = None
    try:
        host_world = UsdGeom.Xformable(host_body).ComputeLocalToWorldTransform(0)
        anchor_world = host_world.Transform(Gf.Vec3d(*profile.mount_position))
        arm_rotation = host_world.ExtractRotationQuat() * mount_rotation
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
        deactivate_root_joint(
            stage,
            arm_path,
            joint_names=("root_joint", "joints/base_static_joint"),
        )
        world_prim = stage.GetPrimAtPath(f"{arm_path}/world")
        if world_prim.IsValid():
            stage.GetPrimAtPath(f"{arm_path}/world").SetActive(False)
        _set_z1_force_drives(stage, arm_path)
        scale_articulation_mass(stage, arm_path, profile.mass_scale)
        z1_base_path = find_first_valid_child(stage, arm_path, ("link00", "base_link", "base"))
        if z1_base_path is None:
            raise RuntimeError(f"{host_path}: Z1 base link does not exist under {arm_path}")
        UsdPhysics.ArticulationRootAPI.Apply(stage.GetPrimAtPath(z1_base_path))
        host_root = find_articulation_root(stage, host_body_path)
        if host_root is None:
            raise RuntimeError(f"{host_path}: host articulation root not found")
        joint_path = f"{host_root}/arm_fixed_joint_{host_path.rsplit('/', 1)[-1]}"
        create_excluded_fixed_joint(
            stage,
            joint_path,
            host_body_path=host_body_path,
            arm_body_path=z1_base_path,
            mount_position=profile.mount_position,
            mount_rotation=profile.mount_rotation,
        )
        return z1_base_path
    except Exception:
        if joint_path and stage.GetPrimAtPath(joint_path).IsValid():
            stage.RemovePrim(joint_path)
        if stage.GetPrimAtPath(arm_path).IsValid():
            stage.RemovePrim(arm_path)
        raise


def spawn_mounted_z1_single_host(
    host_path: str,
    arm_path: str,
    cfg: sim_utils.UsdFileCfg,
    translation=None,
    orientation=None,
    *,
    profile: Z1MountProfile,
) -> None:
    """Mount one Z1 on one explicit host assembly for Env DIY preview."""
    import omni.usd

    stage = omni.usd.get_context().get_stage()
    _spawn_mounted_z1_instance(stage, host_path=host_path, arm_path=arm_path, cfg=cfg, profile=profile)


def spawn_mounted_z1(
    prim_path: str,
    cfg: sim_utils.UsdFileCfg,
    translation=None,
    orientation=None,
    *,
    profile: Z1MountProfile,
) -> None:
    """Formal Builder callback retaining its historical multi-instance behavior."""
    from isaaclab.sim import utils as prim_utils

    stage = sim_utils.get_current_stage()
    target_name = prim_path.rsplit("/", 1)[-1]
    parent_regex = prim_path.rpartition("/")[0]
    matched_parents = prim_utils.find_matching_prim_paths(parent_regex)
    arm_paths = [f"{parent}/{target_name}" for parent in matched_parents] if matched_parents else [prim_path]
    host_paths = [path[: -len("_arm")] if path.endswith("_arm") else path for path in arm_paths]
    if len(arm_paths) != len(host_paths):
        raise RuntimeError(f"Z1 mount count mismatch: arms={len(arm_paths)} hosts={len(host_paths)}")
    errors: list[str] = []
    mounted = 0
    for arm_path, host_path in zip(arm_paths, host_paths):
        try:
            _spawn_mounted_z1_instance(stage, host_path=host_path, arm_path=arm_path, cfg=cfg, profile=profile)
            mounted += 1
        except Exception as exc:
            errors.append(str(exc))
    if errors:
        raise RuntimeError("Z1 mount failed: " + "; ".join(errors))
    if mounted == 0:
        raise RuntimeError(f"Z1 mount matched no {profile.robot_type} instances")
    print(f"[Z1Mount] Mounted Z1 on {mounted} {profile.robot_type} robot(s)")


@configclass
class MountedZ1ArmCfg(ArticulationCfg):
    asset_dependencies = (Z1_STANDALONE_USD_PATH,)


def build_mounted_z1_asset_cfg(prim_path: str, profile: Z1MountProfile) -> MountedZ1ArmCfg:
    return MountedZ1ArmCfg(
        prim_path=prim_path,
        articulation_root_prim_path="/link00",
        spawn=sim_utils.UsdFileCfg(usd_path=Z1_STANDALONE_USD_PATH, func=partial(spawn_mounted_z1, profile=profile)),
        init_state=ArticulationCfg.InitialStateCfg(
            joint_pos=dict(Z1_DEFAULT_JOINT_POS),
            joint_vel={".*": 0.0},
        ),
        actuators=build_z1_actuators(),
    )
