"""Candidate-only unscaled Kinova Gen3 mobile mount.

This module is intentionally not registered in the Env DIY catalog.  The
external Gen3 USD must be supplied and its Kortex URDF source must be retained
with every candidate report.
"""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from functools import partial
import os
from pathlib import Path

import isaaclab.sim as sim_utils
from isaaclab.actuators import ImplicitActuatorCfg
from isaaclab.assets import ArticulationCfg
from isaaclab.utils import configclass

from EAI_assets.asset_resolver import asset_path
from EAI_assets.robots.kinova_physics import model_massless_interface
from EAI_assets.robots.kinova_inertials import apply_source_inertias
from EAI_assets.robots.manipulator_mount import (
    create_excluded_fixed_joint, deactivate_root_joint, find_articulation_root,
    find_first_valid_child,
)


KINOVA_GEN3_USD_PATH = asset_path("payloads/manipulators/kinova_gen3/gen3n7_instanceable.usd")
KINOVA_GEN3_JOINT_NAMES = tuple(f"joint_{index}" for index in range(1, 8))


@dataclass(frozen=True)
class KinovaMountProfile:
    robot_type: str
    mount_body_path: str
    mount_position: tuple[float, float, float]
    mount_rotation: tuple[float, float, float, float]


KINOVA_MOUNT_PROFILES = {
    # Same screened Z1 flange on the Orsus chassis link (see ur5_mount carter note).
    "carter": KinovaMountProfile("carter", "Carter/Orsus_chassis_link", (0.0, 0.0, 0.414), (0.0, 0.0, 0.0, 1.0)),
    "scout": KinovaMountProfile("scout", "base_link", (0.0, 0.0, 0.1162), (0.0, 0.0, 0.0, 1.0)),
    "b2": KinovaMountProfile("b2", "base_link", (0.0, 0.0, 0.085), (0.0, 0.0, 0.0, 1.0)),
    "go2": KinovaMountProfile("go2", "base", (0.0, 0.0, 0.056), (0.0, 0.0, 0.0, 1.0)),
    "m20": KinovaMountProfile("m20", "base_link", (0.0, 0.0, 0.0852), (0.0, 0.0, 0.0, 1.0)),
    "lite3": KinovaMountProfile("lite3", "TORSO", (0.0, 0.0, 0.073), (0.0, 0.0, 0.0, 1.0)),
}


def default_kinova_gen3_usd() -> str:
    configured = os.environ.get("EAI_KINOVA_GEN3_USD")
    return str(Path(configured).expanduser().resolve()) if configured else KINOVA_GEN3_USD_PATH


def default_kinova_gen3_urdf() -> str:
    configured = os.environ.get("EAI_KINOVA_GEN3_URDF")
    if configured:
        return str(Path(configured).expanduser().resolve())
    return str(Path(KINOVA_GEN3_USD_PATH).with_name("gen3.urdf"))


def _spawn_candidate(stage, *, host_path: str, arm_path: str, cfg: sim_utils.UsdFileCfg,
                     profile: KinovaMountProfile, urdf_path: str) -> None:
    from pxr import Gf, PhysxSchema, UsdGeom, UsdPhysics

    host_body_path = f"{host_path}/{profile.mount_body_path}"
    host_body = stage.GetPrimAtPath(host_body_path)
    if not host_body.IsValid():
        raise RuntimeError(f"{host_path}: Kinova mount body does not exist: {host_body_path}")
    host_world = UsdGeom.Xformable(host_body).ComputeLocalToWorldTransform(0)
    arm_rotation = host_world.ExtractRotationQuat() * Gf.Quatd(
        profile.mount_rotation[0], Gf.Vec3d(*profile.mount_rotation[1:]))
    with (sim_utils.use_stage(stage) if callable(getattr(sim_utils, "use_stage", None)) else nullcontext()):
        sim_utils.spawn_from_usd(
            arm_path, cfg,
            translation=tuple(host_world.Transform(Gf.Vec3d(*profile.mount_position))),
            orientation=(arm_rotation.GetReal(), *arm_rotation.GetImaginary()),
        )
    base_path = find_first_valid_child(stage, arm_path, ("base_link", "gen3/base_link"))
    if base_path is None:
        raise RuntimeError(f"Kinova Gen3 base link does not exist below: {arm_path}")
    apply_source_inertias(stage, base_path.rsplit("/", 1)[0], urdf_path)
    model_massless_interface(stage, base_path.rsplit("/", 1)[0], urdf_path)
    deactivate_root_joint(stage, arm_path, joint_names=("root_joint", "joints/base_static_joint"))
    world = stage.GetPrimAtPath(f"{arm_path}/world")
    if world.IsValid():
        world.SetActive(False)
    root = find_articulation_root(stage, base_path)
    if root is None:
        raise RuntimeError(f"Kinova Gen3 articulation root does not exist: {base_path}")
    if root != base_path:
        old_root = stage.GetPrimAtPath(root)
        base = stage.GetPrimAtPath(base_path)
        for schema in (UsdPhysics.ArticulationRootAPI, PhysxSchema.PhysxArticulationAPI):
            schema.Apply(base)
            for name in schema.GetSchemaAttributeNames():
                attribute = old_root.GetAttribute(name)
                if attribute and attribute.HasAuthoredValue():
                    base.GetAttribute(name).Set(attribute.Get())
            old_root.RemoveAPI(schema)
    create_excluded_fixed_joint(stage, f"{base_path}/kinova_mount_joint",
                                host_body_path=host_body_path, arm_body_path=base_path,
                                mount_position=profile.mount_position,
                                mount_rotation=profile.mount_rotation)


def spawn_mounted_kinova(prim_path: str, cfg: sim_utils.UsdFileCfg, translation=None,
                         orientation=None, *, profile: KinovaMountProfile, urdf_path: str) -> None:
    """Formal builder callback; expands the cloner env regex like the Z1/UR5 mounts."""
    from isaaclab.sim import utils as prim_utils

    stage = sim_utils.get_current_stage()
    target_name = prim_path.rsplit("/", 1)[-1]
    parent_regex = prim_path.rpartition("/")[0]
    matched_parents = prim_utils.find_matching_prim_paths(parent_regex)
    arm_paths = [f"{parent}/{target_name}" for parent in matched_parents] if matched_parents else [prim_path]
    host_paths = [path[: -len("_arm")] if path.endswith("_arm") else path for path in arm_paths]
    if len(arm_paths) != len(host_paths):
        raise RuntimeError(f"Kinova mount count mismatch: arms={len(arm_paths)} hosts={len(host_paths)}")
    errors: list[str] = []
    mounted = 0
    for arm_path, host_path in zip(arm_paths, host_paths):
        try:
            _spawn_candidate(stage, host_path=host_path, arm_path=arm_path, cfg=cfg,
                             profile=profile, urdf_path=urdf_path)
            mounted += 1
        except Exception as exc:
            errors.append(f"{host_path}: {exc}")
    if errors:
        raise RuntimeError("Kinova mount failed: " + "; ".join(errors))
    if mounted == 0:
        raise RuntimeError(f"Kinova mount matched no {profile.robot_type} instances")
    print(f"[KinovaMount] Mounted Gen3 on {mounted} {profile.robot_type} robot(s)", flush=True)


@configclass
class CandidateKinovaGen3Cfg(ArticulationCfg):
    asset_dependencies = (KINOVA_GEN3_USD_PATH,)


def build_candidate_kinova_gen3_cfg(prim_path: str, profile: KinovaMountProfile) -> CandidateKinovaGen3Cfg:
    urdf_path = default_kinova_gen3_urdf()
    if not Path(urdf_path).is_file():
        raise ValueError("Kinova candidate requires its source URDF "
                         "(set EAI_KINOVA_GEN3_URDF or place gen3.urdf beside the USD)")
    usd_path = default_kinova_gen3_usd()
    return CandidateKinovaGen3Cfg(
        prim_path=prim_path,
        asset_dependencies=(usd_path,),
        articulation_root_prim_path="/base_link",
        spawn=sim_utils.UsdFileCfg(
            usd_path=usd_path,
            func=partial(spawn_mounted_kinova, profile=profile, urdf_path=urdf_path),
            articulation_props=sim_utils.ArticulationRootPropertiesCfg(
                fix_root_link=False, enabled_self_collisions=True,
                solver_position_iteration_count=64, solver_velocity_iteration_count=4,
            ),
        ),
        init_state=ArticulationCfg.InitialStateCfg(joint_vel={".*": 0.0}),
        actuators={"gen3": ImplicitActuatorCfg(
            joint_names_expr=list(KINOVA_GEN3_JOINT_NAMES),
            effort_limit_sim={"joint_[1-4]": 39.0, "joint_[5-7]": 9.0},
            stiffness={"joint_[1-4]": 40.0, "joint_[5-7]": 15.0},
            damping={"joint_[1-4]": 1.0, "joint_[5-7]": 0.5},
        )},
    )
