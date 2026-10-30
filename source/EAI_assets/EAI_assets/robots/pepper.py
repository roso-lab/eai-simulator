# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause
"""Pepper mobile manipulator configuration for Isaac Lab."""

import isaaclab.sim as sim_utils
from isaaclab.actuators import ImplicitActuatorCfg
from isaaclab.assets.articulation import ArticulationCfg

from EAI_assets.asset_resolver import asset_path

usd_path = asset_path("robot/pepper/pepper.usd")

PEPPER_CFG = ArticulationCfg(
    spawn=sim_utils.UsdFileCfg(
        usd_path=usd_path,
        activate_contact_sensors=False,
        rigid_props=sim_utils.RigidBodyPropertiesCfg(
            disable_gravity=False,
            max_depenetration_velocity=5.0,
        ),
        articulation_props=sim_utils.ArticulationRootPropertiesCfg(
            enabled_self_collisions=False,
            solver_position_iteration_count=8,
            solver_velocity_iteration_count=0,
            sleep_threshold=0.005,
            stabilization_threshold=0.001,
        ),
    ),
    init_state=ArticulationCfg.InitialStateCfg(
        pos=(0.0, 0.0, 0.0),
        joint_pos={
            # Mobile base — virtual holonomic drive joints
            "XDisp":  0.0,
            "YDisp":  0.0,
            "ZRot":   0.0,
            # Head
            "HeadYaw":   0.0,
            "HeadPitch": 0.0,
            # Body (balance)
            "HipRoll":   0.0,
            "HipPitch":  0.0,
            "KneePitch": 0.0,
            # Left arm — arms-down resting pose
            "LShoulderPitch":  1.5,
            "LShoulderRoll":   0.1,
            "LElbowYaw":       0.0,
            "LElbowRoll":     -0.5,
            "LWristYaw":       0.0,
            "LHand":           0.0,
            # Right arm — arms-down resting pose
            "RShoulderPitch":  1.5,
            "RShoulderRoll":  -0.1,
            "RElbowYaw":       0.0,
            "RElbowRoll":      0.5,
            "RWristYaw":       0.0,
            "RHand":           0.0,
        },
    ),
    actuators={
        # Virtual holonomic drive base — position control
        "base": ImplicitActuatorCfg(
            joint_names_expr=["XDisp", "YDisp", "ZRot"],
            stiffness=800.0,
            damping=400.0,
        ),
        # Head joints
        "head": ImplicitActuatorCfg(
            joint_names_expr=["HeadYaw", "HeadPitch"],
            stiffness=200.0,
            damping=20.0,
        ),
        # Hip / knee (body balance)
        "body": ImplicitActuatorCfg(
            joint_names_expr=["HipRoll", "HipPitch", "KneePitch"],
            stiffness=200.0,
            damping=20.0,
        ),
        # Left arm
        "left_arm": ImplicitActuatorCfg(
            joint_names_expr=["LShoulderPitch", "LShoulderRoll",
                              "LElbowYaw", "LElbowRoll",
                              "LWristYaw", "LHand"],
            stiffness=200.0,
            damping=20.0,
        ),
        # Right arm
        "right_arm": ImplicitActuatorCfg(
            joint_names_expr=["RShoulderPitch", "RShoulderRoll",
                              "RElbowYaw", "RElbowRoll",
                              "RWristYaw", "RHand"],
            stiffness=200.0,
            damping=20.0,
        ),
        # Finger mimic joints — passive but damped so they don't free-swing
        # under gravity / contact perturbations.  Stiffness stays 0 (no
        # position target), damping > 0 dissipates motion.
        "fingers": ImplicitActuatorCfg(
            joint_names_expr=[".*Finger.*", ".*Thumb.*"],
            stiffness=0.0,
            damping=2.0,
        ),
    },
)
