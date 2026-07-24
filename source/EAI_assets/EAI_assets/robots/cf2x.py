# Copyright (c) 2022-2025, The Isaac Lab Project Developers (https://github.com/isaac-sim/IsaacLab/blob/main/CONTRIBUTORS.md).
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause

import isaaclab.sim as sim_utils
from isaaclab.actuators import ImplicitActuatorCfg
from isaaclab.assets.articulation import ArticulationCfg

from EAI_assets.asset_resolver import asset_path

usd_path = asset_path("robot/cf2x/cf2x.usd")

CF2X_V1_CFG = ArticulationCfg(
    spawn=sim_utils.UsdFileCfg(
        usd_path=usd_path,
        activate_contact_sensors=True,
    ),
    soft_joint_pos_limit_factor=0.9,
    actuators={
        "all": ImplicitActuatorCfg(
            joint_names_expr=".*",
            stiffness=None,
            damping=None,
        ),
    },
)
