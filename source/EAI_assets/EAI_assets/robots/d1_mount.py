from __future__ import annotations

from dataclasses import dataclass
from functools import partial

import isaaclab.sim as sim_utils
from isaaclab.assets import ArticulationCfg
from isaaclab.utils import configclass

from EAI_assets.asset_resolver import asset_path
from EAI_assets.robots.d1 import (
    D1_ARM_ACTUATOR,
    D1_DEFAULT_JOINT_POS,
    D1_GRIPPER_ACTUATOR,
    D1_GRIPPER_JOINT_NAMES,
    D1_JOINT_NAMES,
    build_d1_actuators,
    find_d1_joint_prim,
)
from EAI_assets.robots.manipulator_mount import spawn_mounted_arm_instance


D1_STANDALONE_USD_PATH = asset_path("payloads/manipulators/d1/d1.usd")


@dataclass(frozen=True)
class D1MountProfile:
    robot_type: str
    mount_body_path: str
    mount_position: tuple[float, float, float]
    mount_rotation: tuple[float, float, float, float]
    mass_scale: float
    enable_self_collisions: bool = False


_IDENTITY_QUAT = (1.0, 0.0, 0.0, 0.0)
_YAW_PI = (0.0, 0.0, 0.0, 1.0)
# 2026-10-22 装配政策放开的新宿主：carter/b2/m20/scout 复用各自 Z1 挂点
# （同一背板位置装 D1），lite3 按 TORSO 背部高度独立取值；除 go2 外均未
# 做静止摆臂等物理筛选。
D1_MOUNT_PROFILES = {
    "go2": D1MountProfile("go2", "base", (0.0, 0.0, 0.15), _IDENTITY_QUAT, 1.0),
    "carter": D1MountProfile("carter", "Carter/Orsus_chassis_link", (0.0, 0.0, 0.414), _YAW_PI, 1.0),
    "b2": D1MountProfile("b2", "base_link", (0.0, 0.0, 0.085), _YAW_PI, 1.0),
    "m20": D1MountProfile("m20", "base_link", (0.0, 0.0, 0.0852), _YAW_PI, 1.0),
    "scout": D1MountProfile("scout", "base_link", (0.0, 0.0, 0.1162), _YAW_PI, 1.0),
    "lite3": D1MountProfile("lite3", "TORSO", (0.0, 0.0, 0.12), _IDENTITY_QUAT, 1.0),
}

_D1_ROOT_JOINT_NAMES = ("root_joint", "joints/root_joint", "Joints/root_joint")


def _set_d1_force_drives(stage, arm_path: str) -> None:
    for joint_name in (*D1_JOINT_NAMES, *D1_GRIPPER_JOINT_NAMES):
        joint = find_d1_joint_prim(stage, arm_path, joint_name)
        if joint is None:
            continue
        for attribute_name in ("drive:angular:physics:type", "drive:linear:physics:type"):
            drive_type = joint.GetAttribute(attribute_name)
            if drive_type.IsValid():
                drive_type.Set("force")
                break


def _spawn_mounted_d1_instance(stage, *, host_path: str, arm_path: str, cfg: sim_utils.UsdFileCfg, profile: D1MountProfile) -> str:
    return spawn_mounted_arm_instance(
        stage,
        host_path=host_path,
        arm_path=arm_path,
        cfg=cfg,
        mount_body_path=profile.mount_body_path,
        mount_position=profile.mount_position,
        mount_rotation=profile.mount_rotation,
        mass_scale=profile.mass_scale,
        enable_self_collisions=profile.enable_self_collisions,
        root_joint_names=_D1_ROOT_JOINT_NAMES,
        base_link_names=("base_link",),
        set_force_drives=_set_d1_force_drives,
        label="D1",
    )


def spawn_mounted_d1_single_host(
    host_path: str,
    arm_path: str,
    cfg: sim_utils.UsdFileCfg,
    translation=None,
    orientation=None,
    *,
    profile: D1MountProfile,
) -> None:
    """Mount one D1 on one explicit host assembly for Env DIY preview."""
    import omni.usd

    stage = omni.usd.get_context().get_stage()
    _spawn_mounted_d1_instance(stage, host_path=host_path, arm_path=arm_path, cfg=cfg, profile=profile)


def spawn_mounted_d1(
    prim_path: str,
    cfg: sim_utils.UsdFileCfg,
    translation=None,
    orientation=None,
    *,
    profile: D1MountProfile,
) -> None:
    """Formal Builder callback retaining the multi-instance mount behavior."""
    from isaaclab.sim import utils as prim_utils

    stage = sim_utils.get_current_stage()
    target_name = prim_path.rsplit("/", 1)[-1]
    parent_regex = prim_path.rpartition("/")[0]
    matched_parents = prim_utils.find_matching_prim_paths(parent_regex)
    arm_paths = [f"{parent}/{target_name}" for parent in matched_parents] if matched_parents else [prim_path]
    host_paths = [path[: -len("_arm")] if path.endswith("_arm") else path for path in arm_paths]
    if len(arm_paths) != len(host_paths):
        raise RuntimeError(f"D1 mount count mismatch: arms={len(arm_paths)} hosts={len(host_paths)}")
    errors: list[str] = []
    mounted = 0
    for arm_path, host_path in zip(arm_paths, host_paths):
        try:
            _spawn_mounted_d1_instance(stage, host_path=host_path, arm_path=arm_path, cfg=cfg, profile=profile)
            mounted += 1
        except Exception as exc:
            errors.append(str(exc))
    if errors:
        raise RuntimeError("D1 mount failed: " + "; ".join(errors))
    if mounted == 0:
        raise RuntimeError(f"D1 mount matched no {profile.robot_type} instances")
    print(f"[D1Mount] Mounted D1 on {mounted} {profile.robot_type} robot(s)")


@configclass
class MountedD1ArmCfg(ArticulationCfg):
    asset_dependencies = (D1_STANDALONE_USD_PATH,)


def build_mounted_d1_asset_cfg(prim_path: str, profile: D1MountProfile) -> MountedD1ArmCfg:
    return MountedD1ArmCfg(
        prim_path=prim_path,
        articulation_root_prim_path="/base_link",
        spawn=sim_utils.UsdFileCfg(usd_path=D1_STANDALONE_USD_PATH, func=partial(spawn_mounted_d1, profile=profile)),
        init_state=ArticulationCfg.InitialStateCfg(
            joint_pos=dict(D1_DEFAULT_JOINT_POS),
            joint_vel={".*": 0.0},
        ),
        actuators=build_d1_actuators(),
    )
