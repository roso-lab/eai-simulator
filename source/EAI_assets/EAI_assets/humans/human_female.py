# Copyright (c) 2022-2026, The Isaac Lab Project Developers.
# SPDX-License-Identifier: BSD-3-Clause

"""HumanFemale local UsdSkel asset configuration."""

from __future__ import annotations

import isaaclab.sim as sim_utils
from isaaclab.assets import AssetBaseCfg
from isaaclab.sim.spawners.from_files import UsdFileCfg
from isaaclab.sim.utils import clone
from isaaclab.utils import configclass

from EAI_assets.asset_resolver import asset_path

HUMAN_FEMALE_IDLE_USD_PATH = asset_path("human/HumanFemale.usd")
HUMAN_FEMALE_WALK_USD_PATH = asset_path("human/HumanFemale.walk_in_place.usd")
HUMAN_FEMALE_USD_PATH = HUMAN_FEMALE_WALK_USD_PATH

HUMAN_FEMALE_SCALE = (0.01, 0.01, 0.01)
HUMAN_FEMALE_DEFAULT_Z = 0.135
HUMAN_FEMALE_COLLISION_PROXY_NAME = "Collision"
HUMAN_FEMALE_COLLISION_PROXY_RADIUS = 0.40
HUMAN_FEMALE_COLLISION_PROXY_HEIGHT = 0.55
HUMAN_FEMALE_COLLISION_PROXY_Z_OFFSET = 0.54
HUMAN_FEMALE_COLLISION_PROXY_CONTACT_OFFSET = 0.08
HUMAN_FEMALE_COLLISION_PROXY_REST_OFFSET = 0.0


@clone
def spawn_xform(
    prim_path: str,
    cfg: sim_utils.SpawnerCfg,
    translation: tuple[float, float, float] | None = None,
    orientation: tuple[float, float, float, float] | None = None,
    **kwargs,
):
    """Spawn a plain Xform that can parent a visual-only animated human USD."""
    import omni.usd
    from pxr import Gf, PhysxSchema, UsdGeom, UsdPhysics

    stage = omni.usd.get_context().get_stage()
    prim = stage.GetPrimAtPath(prim_path)
    if not prim.IsValid():
        prim = UsdGeom.Xform.Define(stage, prim_path).GetPrim()
    xform = UsdGeom.Xformable(prim)
    translate_op = None
    orient_op = None
    for op in xform.GetOrderedXformOps():
        if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
            translate_op = op
        elif op.GetOpType() == UsdGeom.XformOp.TypeOrient:
            orient_op = op
    if translation is not None:
        translate_op = translate_op or xform.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble)
        translate_op.Set(Gf.Vec3d(*translation))
    if orientation is not None:
        orient_op = orient_op or xform.AddOrientOp(UsdGeom.XformOp.PrecisionDouble)
        orient_op.Set(Gf.Quatd(orientation[0], Gf.Vec3d(*orientation[1:])))

    if not getattr(cfg, "enable_collision_proxy", False):
        return prim

    if not prim.HasAPI(UsdPhysics.RigidBodyAPI):
        UsdPhysics.RigidBodyAPI.Apply(prim)
    rigid_body_api = UsdPhysics.RigidBodyAPI(prim)
    rigid_body_api.CreateRigidBodyEnabledAttr(True).Set(True)
    rigid_body_api.CreateKinematicEnabledAttr(True).Set(True)
    if not prim.HasAPI(PhysxSchema.PhysxRigidBodyAPI):
        PhysxSchema.PhysxRigidBodyAPI.Apply(prim)
    if not prim.HasAPI(UsdPhysics.MassAPI):
        UsdPhysics.MassAPI.Apply(prim)
    UsdPhysics.MassAPI(prim).CreateMassAttr(float(cfg.collision_proxy_mass)).Set(float(cfg.collision_proxy_mass))

    proxy_path = f"{prim_path}/{cfg.collision_proxy_name}"
    proxy = stage.GetPrimAtPath(proxy_path)
    if not proxy.IsValid():
        capsule = UsdGeom.Capsule.Define(prim.GetStage(), proxy_path)
        proxy = capsule.GetPrim()
    else:
        capsule = UsdGeom.Capsule(proxy)

    capsule.CreateRadiusAttr(float(cfg.collision_proxy_radius)).Set(float(cfg.collision_proxy_radius))
    capsule.CreateHeightAttr(float(cfg.collision_proxy_height)).Set(float(cfg.collision_proxy_height))
    capsule.CreateAxisAttr("Z").Set("Z")

    xform = UsdGeom.Xformable(proxy)
    translate_op = None
    for op in xform.GetOrderedXformOps():
        if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
            translate_op = op
            break
    if translate_op is None:
        translate_op = xform.AddTranslateOp(UsdGeom.XformOp.PrecisionDouble)
    translate_op.Set((0.0, 0.0, float(cfg.collision_proxy_z_offset)))

    if not proxy.HasAPI(UsdPhysics.CollisionAPI):
        UsdPhysics.CollisionAPI.Apply(proxy)
    UsdPhysics.CollisionAPI(proxy).CreateCollisionEnabledAttr(True)
    if not proxy.HasAPI(PhysxSchema.PhysxCollisionAPI):
        PhysxSchema.PhysxCollisionAPI.Apply(proxy)
    physx_collision_api = PhysxSchema.PhysxCollisionAPI(proxy)
    physx_collision_api.CreateContactOffsetAttr(float(cfg.collision_proxy_contact_offset)).Set(
        float(cfg.collision_proxy_contact_offset)
    )
    physx_collision_api.CreateRestOffsetAttr(float(cfg.collision_proxy_rest_offset)).Set(
        float(cfg.collision_proxy_rest_offset)
    )
    if proxy.HasAPI(UsdPhysics.RigidBodyAPI):
        proxy.RemoveAPI(UsdPhysics.RigidBodyAPI)
    if proxy.HasAPI(PhysxSchema.PhysxRigidBodyAPI):
        proxy.RemoveAPI(PhysxSchema.PhysxRigidBodyAPI)
    if proxy.HasAPI(UsdPhysics.MassAPI):
        proxy.RemoveAPI(UsdPhysics.MassAPI)

    UsdGeom.Imageable(proxy).MakeInvisible()
    return prim


@configclass
class HumanFemaleRigCfg(sim_utils.SpawnerCfg):
    """Movable Xform rig plus an invisible kinematic collision proxy."""

    func = spawn_xform
    enable_collision_proxy: bool = True
    collision_proxy_name: str = HUMAN_FEMALE_COLLISION_PROXY_NAME
    collision_proxy_radius: float = HUMAN_FEMALE_COLLISION_PROXY_RADIUS
    collision_proxy_height: float = HUMAN_FEMALE_COLLISION_PROXY_HEIGHT
    collision_proxy_z_offset: float = HUMAN_FEMALE_COLLISION_PROXY_Z_OFFSET
    collision_proxy_contact_offset: float = HUMAN_FEMALE_COLLISION_PROXY_CONTACT_OFFSET
    collision_proxy_rest_offset: float = HUMAN_FEMALE_COLLISION_PROXY_REST_OFFSET
    collision_proxy_mass: float = 60.0


HUMAN_FEMALE_RIG_CFG = AssetBaseCfg(
    spawn=HumanFemaleRigCfg(),
    init_state=AssetBaseCfg.InitialStateCfg(pos=(0.0, 0.0, HUMAN_FEMALE_DEFAULT_Z)),
)


@configclass
class HumanFemaleCfg(UsdFileCfg):
    """Local HumanFemale animated UsdSkel asset."""

    usd_path: str = HUMAN_FEMALE_WALK_USD_PATH
    scale: tuple[float, float, float] = HUMAN_FEMALE_SCALE


class HumanFemaleIdleCfg(HumanFemaleCfg):
    """Local HumanFemale static idle UsdSkel asset."""

    usd_path: str = HUMAN_FEMALE_IDLE_USD_PATH


HUMAN_FEMALE_WALK_CFG = AssetBaseCfg(
    spawn=HumanFemaleCfg(visible=False),
    init_state=AssetBaseCfg.InitialStateCfg(pos=(0.0, 0.0, 0.0)),
)

HUMAN_FEMALE_IDLE_CFG = AssetBaseCfg(
    spawn=HumanFemaleIdleCfg(),
    init_state=AssetBaseCfg.InitialStateCfg(pos=(0.0, 0.0, 0.0)),
)

HUMAN_FEMALE_CFG = HUMAN_FEMALE_WALK_CFG
