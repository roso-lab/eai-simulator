"""Coco delivery robot configuration using the local AIRS-branded USD."""

from __future__ import annotations

import isaaclab.sim as sim_utils
from isaaclab.actuators import DCMotorCfg, ImplicitActuatorCfg
from isaaclab.assets import ArticulationCfg

from EAI_assets.asset_resolver import asset_path


COCO_USD_PATH = asset_path("robot/coco_one/coco_airs.usda")

_ZERO_JOINT_STATE = {
    ".*wheel_joint": 0.0,
    "base_to_front_axle_joint": 0.0,
    ".*shock_joint": 0.0,
}


COCO_CFG = ArticulationCfg(
    spawn=sim_utils.UsdFileCfg(
        usd_path=COCO_USD_PATH,
        activate_contact_sensors=True,
        rigid_props=sim_utils.RigidBodyPropertiesCfg(
            disable_gravity=False,
            retain_accelerations=False,
            linear_damping=0.0,
            angular_damping=0.0,
            max_linear_velocity=1000.0,
            max_angular_velocity=1000.0,
            max_depenetration_velocity=1.0,
        ),
        articulation_props=sim_utils.ArticulationRootPropertiesCfg(
            enabled_self_collisions=False,
            solver_position_iteration_count=4,
            solver_velocity_iteration_count=0,
            sleep_threshold=0.005,
        ),
    ),
    init_state=ArticulationCfg.InitialStateCfg(
        pos=(0.0, 0.0, 0.3),
        joint_pos=dict(_ZERO_JOINT_STATE),
        joint_vel=dict(_ZERO_JOINT_STATE),
    ),
    actuators={
        "wheels": DCMotorCfg(
            joint_names_expr=[".*wheel_joint"],
            saturation_effort=100.0,
            effort_limit=100.0,
            velocity_limit=100.0,
            stiffness={".*_wheel_joint": 0.0},
            damping={".*_wheel_joint": 50.0},
            friction={".*_wheel_joint": 0.0},
        ),
        "axle": DCMotorCfg(
            joint_names_expr=["base_to_front_axle_joint"],
            saturation_effort=64.0,
            effort_limit=64.0,
            velocity_limit=20.0,
            stiffness=25.0,
            damping=0.5,
            friction=0.0,
        ),
        "shock": ImplicitActuatorCfg(
            joint_names_expr=[".*shock_joint"],
            stiffness=0.0,
            damping=0.0,
        ),
    },
    soft_joint_pos_limit_factor=1.0,
)


__all__ = ["COCO_CFG", "COCO_USD_PATH"]
