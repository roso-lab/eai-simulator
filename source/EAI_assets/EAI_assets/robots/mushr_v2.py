# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause
"""MuSHR Nano v2 robot configurations for Isaac Sim 5.1."""

from __future__ import annotations

import isaaclab.sim as sim_utils
from isaaclab.actuators import DCMotorCfg, ImplicitActuatorCfg
from isaaclab.assets import ArticulationCfg

from EAI_assets.asset_resolver import asset_path


MUSHR_V2_USD_PATH = asset_path("robot/mushr_v2/mushr_nano_v2.usd")

_MUSHR_V2_JOINT_POS = {
    "front_left_wheel_steer": 0.0,
    "front_right_wheel_steer": 0.0,
    "back_left_wheel_throttle": 0.0,
    "back_right_wheel_throttle": 0.0,
    "front_left_wheel_throttle": 0.0,
    "front_right_wheel_throttle": 0.0,
    "front_left_wheel_suspension": 0.0,
    "front_right_wheel_suspension": 0.0,
    "back_left_wheel_suspension": 0.0,
    "back_right_wheel_suspension": 0.0,
}


def _spawn_cfg() -> sim_utils.UsdFileCfg:
    return sim_utils.UsdFileCfg(
        usd_path=MUSHR_V2_USD_PATH,
        activate_contact_sensors=True,
        rigid_props=sim_utils.RigidBodyPropertiesCfg(
            disable_gravity=False,
            max_linear_velocity=1000.0,
            max_angular_velocity=100000.0,
            max_depenetration_velocity=100.0,
            enable_gyroscopic_forces=True,
        ),
        articulation_props=sim_utils.ArticulationRootPropertiesCfg(
            enabled_self_collisions=False,
            solver_position_iteration_count=4,
            solver_velocity_iteration_count=0,
            sleep_threshold=0.005,
            stabilization_threshold=0.001,
        ),
    )


_STEERING_ACTUATOR = ImplicitActuatorCfg(
    joint_names_expr=["front_left_wheel_steer", "front_right_wheel_steer"],
    velocity_limit_sim=10.0,
    effort_limit_sim=3.2,
    stiffness=100.0,
    damping=10.0,
    friction=0.0,
)

_FOUR_WHEEL_THROTTLE_ACTUATOR = DCMotorCfg(
    joint_names_expr=[".*_wheel_throttle"],
    saturation_effort=1.05,
    effort_limit=0.25,
    velocity_limit=450.0,
    effort_limit_sim=0.25,
    velocity_limit_sim=450.0,
    stiffness=0.0,
    damping=1000.0,
    friction=0.0,
)

_REAR_THROTTLE_ACTUATOR = DCMotorCfg(
    joint_names_expr=["back_.*wheel_throttle"],
    saturation_effort=1.05,
    effort_limit=0.5,
    velocity_limit=450.0,
    effort_limit_sim=0.5,
    velocity_limit_sim=450.0,
    stiffness=0.0,
    damping=1000.0,
    friction=0.0,
)

_PASSIVE_FRONT_THROTTLE_ACTUATOR = ImplicitActuatorCfg(
    joint_names_expr=["front_.*wheel_throttle"],
    effort_limit=None,
    velocity_limit=None,
    stiffness=0.0,
    damping=0.0,
    friction=0.0,
)

_SUSPENSION_ACTUATOR = ImplicitActuatorCfg(
    joint_names_expr=[".*_wheel_suspension"],
    effort_limit=None,
    velocity_limit=None,
    # Isaac Sim 5.1 ignores drive gains on articulation prismatic joints.
    # Leave these unset so the USD joint limits/native drive settings remain authoritative.
    stiffness=None,
    damping=None,
    friction=0.5,
)


MUSHR_V2_CFG = ArticulationCfg(
    spawn=_spawn_cfg(),
    init_state=ArticulationCfg.InitialStateCfg(
        pos=(0.0, 0.0, 0.0),
        joint_pos=_MUSHR_V2_JOINT_POS,
    ),
    actuators={
        "steering": _STEERING_ACTUATOR,
        "throttle": _FOUR_WHEEL_THROTTLE_ACTUATOR,
        "suspension": _SUSPENSION_ACTUATOR,
    },
)


MUSHR_V2_RWD_CFG = ArticulationCfg(
    spawn=_spawn_cfg(),
    init_state=ArticulationCfg.InitialStateCfg(
        pos=(0.0, 0.0, 0.0),
        joint_pos=_MUSHR_V2_JOINT_POS,
    ),
    actuators={
        "steering": _STEERING_ACTUATOR,
        "throttle": _REAR_THROTTLE_ACTUATOR,
        "passive_front_throttle": _PASSIVE_FRONT_THROTTLE_ACTUATOR,
        "suspension": _SUSPENSION_ACTUATOR,
    },
)


__all__ = ["MUSHR_V2_CFG", "MUSHR_V2_RWD_CFG", "MUSHR_V2_USD_PATH"]
