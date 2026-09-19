# Copyright (c) 2026, EAI Simulator contributors.
# SPDX-License-Identifier: BSD-3-Clause
"""Gazebo-parity SimpleCar planar mobile robot configuration."""

import isaaclab.sim as sim_utils
from isaaclab.actuators import ImplicitActuatorCfg
from isaaclab.assets import ArticulationCfg
from isaaclab.sim.spawners.from_files import UsdFileCfg
from isaaclab.sim.spawners.from_files import from_files as _from_files
from isaaclab.utils import configclass

from EAI_assets.asset_resolver import asset_path

SIMPLE_CAR_USD_PATH = asset_path("robot/simple_car/simple_car.usda")
SIMPLE_CAR_LIDAR_PROFILE_PATH = asset_path(
    "payloads/sensors/octagon_aio_c01/OCTAGON_AIO_C01.json"
)


_SUPPORT_LINK_NAMES = (
    "front_left_wheel_link",
    "front_right_wheel_link",
    "rear_left_wheel_link",
    "rear_right_wheel_link",
)
_SUPPORT_JOINT_NAMES = tuple(
    name.removesuffix("_link") + "_joint" for name in _SUPPORT_LINK_NAMES
)
_SUPPORT_RADIUS_M = 0.06
_LEGACY_SUPPORT_MASS_KG = 0.001
_LEGACY_SUPPORT_INERTIA_KG_M2 = (
    0.4 * _LEGACY_SUPPORT_MASS_KG * _SUPPORT_RADIUS_M**2
)


def _spawn_simple_car_with_frictionless_supports_impl(
    prim_path, cfg, translation=None, orientation=None, **kwargs
):
    prim = _from_files.spawn_from_usd(
        prim_path, cfg, translation, orientation, **kwargs
    )

    import omni.usd
    from pxr import Gf, Usd, UsdGeom, UsdPhysics, UsdShade
    from isaaclab.sim.utils import find_matching_prim_paths

    stage = omni.usd.get_context().get_stage()
    robot_paths = find_matching_prim_paths(prim_path)
    if not robot_paths and prim and prim.IsValid():
        robot_paths = [str(prim.GetPath())]
    if not robot_paths:
        raise RuntimeError(f"SimpleCar spawn produced no robot roots for {prim_path}")

    for robot_path in robot_paths:
        sim_utils.make_uninstanceable(robot_path, stage=stage)
        robot_prim = stage.GetPrimAtPath(robot_path)
        support_links = {
            child.GetName(): child
            for child in Usd.PrimRange(robot_prim)
            if child.GetName() in _SUPPORT_LINK_NAMES
            and child.HasAPI(UsdPhysics.RigidBodyAPI)
        }
        if set(support_links) != set(_SUPPORT_LINK_NAMES):
            raise RuntimeError(
                f"SimpleCar support links under {robot_path} are "
                f"{sorted(support_links)}, expected {list(_SUPPORT_LINK_NAMES)}"
            )

        support_joints = {
            child.GetName(): child
            for child in Usd.PrimRange(robot_prim)
            if child.GetName() in _SUPPORT_JOINT_NAMES
        }
        if set(support_joints) != set(_SUPPORT_JOINT_NAMES):
            raise RuntimeError(
                f"SimpleCar support joints under {robot_path} are "
                f"{sorted(support_joints)}, expected {list(_SUPPORT_JOINT_NAMES)}"
            )
        legacy_revolute_count = 0
        for joint_name in _SUPPORT_JOINT_NAMES:
            joint_prim = support_joints[joint_name]
            if joint_prim.IsA(UsdPhysics.FixedJoint):
                continue
            if not joint_prim.IsA(UsdPhysics.RevoluteJoint):
                raise RuntimeError(
                    f"SimpleCar support {joint_name} under {robot_path} is neither "
                    "a fixed joint nor a legacy revolute joint"
                )
            legacy_revolute_count += 1
        if legacy_revolute_count not in (0, len(_SUPPORT_JOINT_NAMES)):
            raise RuntimeError(
                f"SimpleCar support topology under {robot_path} mixes fixed and legacy "
                "revolute joints"
            )
        if legacy_revolute_count:
            diagonal_inertia = Gf.Vec3f(
                *([_LEGACY_SUPPORT_INERTIA_KG_M2] * 3)
            )
            for link_prim in support_links.values():
                mass = UsdPhysics.MassAPI(link_prim)
                mass.CreateMassAttr(_LEGACY_SUPPORT_MASS_KG)
                mass.CreateDiagonalInertiaAttr(diagonal_inertia)
                if abs(
                    float(mass.GetMassAttr().Get()) - _LEGACY_SUPPORT_MASS_KG
                ) > 1.0e-9:
                    raise RuntimeError(
                        f"Failed to reduce legacy SimpleCar support mass at {link_prim.GetPath()}"
                    )

        material_path = f"{robot_path}/PhysicsMaterials/planar_support_zero_friction"
        material_cfg = sim_utils.RigidBodyMaterialCfg(
            static_friction=0.0,
            dynamic_friction=0.0,
            restitution=0.0,
            friction_combine_mode="min",
        )
        material_cfg.func(material_path, material_cfg)

        support_colliders = []
        created_count = 0
        for link_name in _SUPPORT_LINK_NAMES:
            link_prim = support_links[link_name]
            link_colliders = [
                child
                for child in Usd.PrimRange(link_prim)
                if child.HasAPI(UsdPhysics.CollisionAPI)
            ]
            if not link_colliders:
                collider_path = (
                    f"{robot_path}/{link_name}/collisions/planar_support_sphere"
                )
                sphere = UsdGeom.Sphere.Define(stage, collider_path)
                sphere.CreateRadiusAttr(_SUPPORT_RADIUS_M)
                sphere.CreateVisibilityAttr(UsdGeom.Tokens.invisible)
                collider = UsdPhysics.CollisionAPI.Apply(sphere.GetPrim())
                collider.CreateCollisionEnabledAttr(True)
                link_colliders = [sphere.GetPrim()]
                created_count += 1
            if len(link_colliders) != 1:
                raise RuntimeError(
                    f"SimpleCar support {link_name} under {robot_path} has "
                    f"{len(link_colliders)} colliders; expected one"
                )
            support_colliders.extend(link_colliders)

        for collider in support_colliders:
            sim_utils.bind_physics_material(
                collider.GetPath(), material_path, stage=stage
            )
            binding = UsdShade.MaterialBindingAPI(collider).GetDirectBinding(
                "physics"
            )
            if str(binding.GetMaterialPath()) != material_path:
                raise RuntimeError(
                    f"Failed to bind frictionless support material to {collider.GetPath()}"
                )
            collision = UsdPhysics.CollisionAPI(collider)
            collision.CreateCollisionEnabledAttr(False)
            if collision.GetCollisionEnabledAttr().Get() is not False:
                raise RuntimeError(
                    f"Failed to disable PhysX contact on SimpleCar support {collider.GetPath()}"
                )
        print(
            f"[SimpleCar] Prepared {len(support_colliders)} planar support shapes "
            f"under {robot_path} (PhysX contact disabled; restored {created_count}; "
            f"legacy revolute joints {legacy_revolute_count})"
        )
    return prim


def _spawn_simple_car_with_frictionless_supports(
    prim_path, cfg, translation=None, orientation=None, **kwargs
):
    try:
        return _spawn_simple_car_with_frictionless_supports_impl(
            prim_path, cfg, translation, orientation, **kwargs
        )
    except Exception:
        import traceback

        traceback.print_exc()
        raise


@configclass
class SimpleCarUsdFileCfg(UsdFileCfg):
    func = _spawn_simple_car_with_frictionless_supports


SIMPLE_CAR_CFG = ArticulationCfg(
    spawn=SimpleCarUsdFileCfg(
        usd_path=SIMPLE_CAR_USD_PATH,
        activate_contact_sensors=True,
        rigid_props=sim_utils.RigidBodyPropertiesCfg(
            # Planar motion holds height without sphere-floor contacts. PhysX
            # otherwise locks yaw even with a zero-friction material.
            disable_gravity=True,
            max_depenetration_velocity=0.2,
            enable_gyroscopic_forces=True,
        ),
        articulation_props=sim_utils.ArticulationRootPropertiesCfg(
            enabled_self_collisions=False,
            solver_position_iteration_count=8,
            solver_velocity_iteration_count=2,
            sleep_threshold=0.0,
            stabilization_threshold=0.001,
        ),
    ),
    init_state=ArticulationCfg.InitialStateCfg(pos=(0.0, 0.0, 0.34)),
    actuators={
        # The current provider USD imported Gazebo's fixed support spheres as
        # revolute joints. They remain passive until the provider is regenerated.
        "passive_support_spheres": ImplicitActuatorCfg(
            joint_names_expr=[".*_wheel_joint"],
            stiffness=0.0,
            damping=0.0,
            effort_limit_sim=0.0,
        ),
    },
)

__all__ = ["SIMPLE_CAR_CFG", "SIMPLE_CAR_LIDAR_PROFILE_PATH", "SIMPLE_CAR_USD_PATH"]
