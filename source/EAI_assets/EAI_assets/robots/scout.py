# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause
"""Scout mobile base and Scout+UR5 articulation configs (local USD)."""

import isaaclab.sim as sim_utils
from isaaclab.actuators import ImplicitActuatorCfg
from isaaclab.assets.articulation import ArticulationCfg

from EAI_assets.asset_resolver import asset_path
from EAI_assets.robots.ur5_mount import (
    UR5_DEFAULT_JOINT_POS,
    UR5_JOINT_NAMES,
    UR5_MOUNT_PROFILES,
    build_ur5_host_cfg,
)


usd_path = asset_path("robot/scout/scout_v2.usd")

SCOUT_CFG = ArticulationCfg(
    spawn=sim_utils.UsdFileCfg(
        usd_path=usd_path,
        activate_contact_sensors=True,
        rigid_props=sim_utils.RigidBodyPropertiesCfg(
            disable_gravity=False,
            max_depenetration_velocity=100.0,
            enable_gyroscopic_forces=True,
        ),
        articulation_props=sim_utils.ArticulationRootPropertiesCfg(
            enabled_self_collisions=True,
            solver_position_iteration_count=4,
            solver_velocity_iteration_count=0,
            sleep_threshold=0.005,
            stabilization_threshold=0.001,
        ),
    ),
    init_state=ArticulationCfg.InitialStateCfg(pos=(0.0, 0.0, 0.2)),
    actuators={
        "base_wheels": ImplicitActuatorCfg(
            joint_names_expr=[".*_wheel"],
            stiffness=0.0,
            damping=10000.0,
            effort_limit=None,
            velocity_limit=None,
        ),
    },
)

SCOUT_UR5_JOINT_NAMES = list(UR5_JOINT_NAMES)
SCOUT_UR5_DEFAULT_JOINT_POS = dict(UR5_DEFAULT_JOINT_POS)
SCOUT_UR5_MOUNT_PROFILE = UR5_MOUNT_PROFILES["scout"]
SCOUT_UR5_CFG = build_ur5_host_cfg(SCOUT_CFG, SCOUT_UR5_MOUNT_PROFILE)
