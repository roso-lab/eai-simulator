from __future__ import annotations

import copy
from contextlib import nullcontext
import os
from dataclasses import dataclass
from functools import partial

import isaaclab.sim as sim_utils
from isaaclab.actuators import ImplicitActuatorCfg
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


UR5_STANDALONE_USD_PATH = asset_path("payloads/manipulators/ur5/ur5-noroot.usd")

UR5_JOINT_NAMES = (
    "shoulder_pan_joint",
    "shoulder_lift_joint",
    "elbow_joint",
    "wrist_1_joint",
    "wrist_2_joint",
    "wrist_3_joint",
)

UR5_DEFAULT_JOINT_POS = {
    "shoulder_pan_joint": 0.0,
    "shoulder_lift_joint": -1.57,
    "elbow_joint": 1.57,
    "wrist_1_joint": -1.57,
    "wrist_2_joint": -1.57,
    "wrist_3_joint": 0.0,
}


@dataclass(frozen=True)
class Ur5MountProfile:
    robot_type: str
    mount_body_path: str
    mount_position: tuple[float, float, float]
    mount_rotation: tuple[float, float, float, float]
    visual_scale: float
    mass_scale: float
    enable_self_collisions: bool


_YAW_PI = (0.0, 0.0, 0.0, 1.0)

UR5_MOUNT_PROFILES = {
    "carter": Ur5MountProfile(
        robot_type="carter",
        mount_body_path="Carter/GS_Hub_chassis_link",
        mount_position=(0.0, 0.0, 0.414),
        mount_rotation=_YAW_PI,
        visual_scale=1.0,
        mass_scale=0.05,
        enable_self_collisions=False,
    ),
    "go2": Ur5MountProfile(
        robot_type="go2",
        mount_body_path="base",
        mount_position=(0.0, 0.0, 0.056),
        mount_rotation=_YAW_PI,
        visual_scale=1.0,
        mass_scale=0.01,
        enable_self_collisions=False,
    ),
    "b2": Ur5MountProfile(
        robot_type="b2",
        mount_body_path="base_link",
        mount_position=(0.0, 0.0, 0.085),
        mount_rotation=_YAW_PI,
        visual_scale=1.0,
        mass_scale=0.07,
        enable_self_collisions=False,
    ),
    "m20": Ur5MountProfile(
        robot_type="m20",
        mount_body_path="base_link",
        mount_position=(0.0, 0.0, 0.0852),
        mount_rotation=_YAW_PI,
        visual_scale=1.0,
        mass_scale=0.03,
        enable_self_collisions=True,
    ),
    "scout": Ur5MountProfile(
        robot_type="scout",
        mount_body_path="base_link",
        mount_position=(0.0, 0.0, 0.1162),
        mount_rotation=_YAW_PI,
        visual_scale=1.0,
        mass_scale=0.05,
        enable_self_collisions=True,
    ),
    "lite3": Ur5MountProfile(
        robot_type="lite3",
        mount_body_path="TORSO",
        mount_position=(0.0, 0.0, 0.073),
        mount_rotation=_YAW_PI,
        visual_scale=1.0,
        mass_scale=0.01,
        enable_self_collisions=False,
    ),
}


def build_ur5_host_cfg(base_cfg: ArticulationCfg, profile: Ur5MountProfile) -> ArticulationCfg:
    return copy.deepcopy(base_cfg)


def _mount_debug_enabled() -> bool:
    return os.environ.get("EAI_UR5_MOUNT_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}


def _spawn_mounted_ur5_instance(stage, *, host_path: str, arm_path: str, profile: Ur5MountProfile) -> str:
    from pxr import Gf, UsdGeom, UsdPhysics

    ur5_cfg = sim_utils.UsdFileCfg(
        usd_path=UR5_STANDALONE_USD_PATH,
        articulation_props=sim_utils.ArticulationRootPropertiesCfg(
            enabled_self_collisions=profile.enable_self_collisions,
            fix_root_link=False,
        ),
        activate_contact_sensors=False,
    )

    mount_rotation = Gf.Quatd(
        profile.mount_rotation[0],
        Gf.Vec3d(*profile.mount_rotation[1:]),
    )

    host_body_path = f"{host_path}/{profile.mount_body_path}"
    host_body = stage.GetPrimAtPath(host_body_path)
    if not host_body.IsValid():
        raise RuntimeError(f"{host_path}: mount body does not exist: {host_body_path}")

    joint_path = None
    try:
        host_world = UsdGeom.Xformable(host_body).ComputeLocalToWorldTransform(0)
        host_rotation = host_world.ExtractRotationQuat()
        anchor_world = host_world.Transform(Gf.Vec3d(*profile.mount_position))
        arm_rotation = host_rotation * mount_rotation
        arm_orientation = (
            arm_rotation.GetReal(),
            arm_rotation.GetImaginary()[0],
            arm_rotation.GetImaginary()[1],
            arm_rotation.GetImaginary()[2],
        )

        stage_scope = (
            sim_utils.use_stage(stage)
            if callable(getattr(sim_utils, "use_stage", None))
            else nullcontext()
        )
        with stage_scope:
            sim_utils.spawn_from_usd(
                arm_path,
                ur5_cfg,
                translation=tuple(anchor_world),
                orientation=arm_orientation,
            )
        deactivate_root_joint(stage, arm_path)
        total_mass = scale_articulation_mass(stage, arm_path, profile.mass_scale)

        ur5_base_path = find_first_valid_child(stage, arm_path, ("base_link", "base", "world", "root_link"))
        if ur5_base_path is None:
            raise RuntimeError(f"{host_path}: UR5 base_link does not exist under {arm_path}")
        ur5_base = stage.GetPrimAtPath(ur5_base_path)
        UsdPhysics.ArticulationRootAPI.Apply(ur5_base)

        articulation_root_path = find_articulation_root(stage, host_body_path)
        if articulation_root_path is None:
            raise RuntimeError(f"{host_path}: host articulation root does not exist above {host_body_path}")
        joint_path = f"{articulation_root_path}/arm_fixed_joint_{host_path.rsplit('/', 1)[-1]}"
        create_excluded_fixed_joint(
            stage,
            joint_path,
            host_body_path=host_body_path,
            arm_body_path=ur5_base_path,
            mount_position=profile.mount_position,
            mount_rotation=profile.mount_rotation,
        )
        if _mount_debug_enabled():
            print(
                f"[UR5MountDebug] robot={host_path.rsplit('/', 1)[-1]} type={profile.robot_type} "
                f"body0={host_body_path} body1={ur5_base_path} "
                f"mount={profile.mount_position} scale={profile.visual_scale:.3f} "
                f"mass_scale={profile.mass_scale:.3f} arm_mass={total_mass:.3f}"
            )

        return ur5_base_path
    except Exception:
        if joint_path and stage.GetPrimAtPath(joint_path).IsValid():
            stage.RemovePrim(joint_path)
        if stage.GetPrimAtPath(arm_path).IsValid():
            stage.RemovePrim(arm_path)
        raise


def spawn_mounted_ur5_single_host(
    host_path: str,
    arm_path: str,
    cfg: sim_utils.UsdFileCfg | None = None,
    translation: tuple[float, float, float] | None = None,
    orientation: tuple[float, float, float, float] | None = None,
    *,
    profile: Ur5MountProfile,
) -> None:
    """Mount one UR5 on one explicit host assembly.

    The preview editor uses this entrypoint so a robot ID can never affect a
    sibling assembly through a parent-path regular expression.
    """
    import omni.usd

    stage = omni.usd.get_context().get_stage()
    _spawn_mounted_ur5_instance(stage, host_path=host_path, arm_path=arm_path, profile=profile)


def spawn_mounted_ur5(
    prim_path: str,
    cfg: sim_utils.UsdFileCfg,
    translation: tuple[float, float, float] | None,
    orientation: tuple[float, float, float, float] | None,
    *,
    profile: Ur5MountProfile,
) -> None:
    """Formal Builder callback retaining its historical multi-instance behavior."""
    import omni.usd
    from isaaclab.sim import utils as prim_utils

    stage = omni.usd.get_context().get_stage()
    target_name = prim_path.rsplit("/", 1)[-1]
    parent_regex = prim_path.rpartition("/")[0]
    matched_parents = prim_utils.find_matching_prim_paths(parent_regex)
    arm_paths = [f"{parent}/{target_name}" for parent in matched_parents] if matched_parents else [prim_path]
    host_paths = [path[: -len("_arm")] if path.endswith("_arm") else path for path in arm_paths]
    errors: list[str] = []
    mounted_count = 0
    for arm_path, host_path in zip(arm_paths, host_paths):
        try:
            _spawn_mounted_ur5_instance(stage, host_path=host_path, arm_path=arm_path, profile=profile)
            mounted_count += 1
        except Exception as exc:
            errors.append(str(exc))
    if errors:
        raise RuntimeError("UR5 mount failed: " + "; ".join(errors))
    if mounted_count == 0:
        raise RuntimeError(f"UR5 mount matched no {profile.robot_type} robot instances for {prim_path}")
    print(f"[UR5Mount] Mounted UR5 on {mounted_count} {profile.robot_type} robot(s)")


@configclass
class MountedUr5ArmCfg(ArticulationCfg):
    asset_dependencies = (UR5_STANDALONE_USD_PATH,)


def build_mounted_ur5_asset_cfg(prim_path: str, profile: Ur5MountProfile) -> MountedUr5ArmCfg:
    return MountedUr5ArmCfg(
        prim_path=prim_path,
        articulation_root_prim_path="/base_link",
        spawn=sim_utils.UsdFileCfg(
            usd_path=UR5_STANDALONE_USD_PATH,
            func=partial(spawn_mounted_ur5, profile=profile),
        ),
        init_state=ArticulationCfg.InitialStateCfg(
            joint_pos=dict(UR5_DEFAULT_JOINT_POS),
            joint_vel={".*": 0.0},
        ),
        actuators={
            "ur5_arm": ImplicitActuatorCfg(
                joint_names_expr=list(UR5_JOINT_NAMES),
                effort_limit_sim=87.0 * profile.mass_scale,
                stiffness=800.0 * profile.mass_scale,
                damping=40.0 * profile.mass_scale,
            ),
        },
    )
