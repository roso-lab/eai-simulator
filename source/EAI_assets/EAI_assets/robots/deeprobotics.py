from pathlib import Path

# Copyright (c) 2025 Deep Robotics
# SPDX-License-Identifier: BSD 3-Clause

# Copyright (c) 2024-2025 Ziqi Fan
# SPDX-License-Identifier: Apache-2.0

import isaaclab.sim as sim_utils
from isaaclab.actuators import DelayedPDActuatorCfg
from isaaclab.assets import ArticulationCfg

from EAI_assets.asset_resolver import asset_path
from EAI_assets.robots.ur5_mount import (
    UR5_DEFAULT_JOINT_POS,
    UR5_JOINT_NAMES,
    UR5_MOUNT_PROFILES,
    build_ur5_host_cfg,
)


_LITE3_SOURCE_USD_PATH = asset_path("robot/lite3/Lite3.usd")
_LITE3_CANONICAL_USD_PATH = asset_path("robot/lite3/Lite3_canonical.usdc")
lite3_usd_path = (
    _LITE3_CANONICAL_USD_PATH
    if Path(_LITE3_CANONICAL_USD_PATH).is_file()
    else _LITE3_SOURCE_USD_PATH
)
m20_usd_path = asset_path("robot/m20/M20.usd")

DEEPROBOTICS_LITE3_CFG = ArticulationCfg(
    spawn=sim_utils.UsdFileCfg(
        usd_path=lite3_usd_path,
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
            solver_velocity_iteration_count=1,
        ),
    ),
    init_state=ArticulationCfg.InitialStateCfg(
        pos=(0.0, 0.0, 0.35),
        joint_pos={
            ".*HipX_joint": 0.0,
            ".*HipY_joint": -0.65,
            ".*Knee_joint": 1.3,
        },
        joint_vel={".*": 0.0},
    ),
    soft_joint_pos_limit_factor=0.99,
    actuators={
        "Hip": DelayedPDActuatorCfg(
            joint_names_expr=[".*_Hip[X,Y]_joint"],
            effort_limit=24.0,
            velocity_limit=26.2,
            stiffness=30.0,
            damping=1.0,
            friction=0.0,
            armature=0.0,
            min_delay=0,
            max_delay=5,
        ),
        "Knee": DelayedPDActuatorCfg(
            joint_names_expr=[".*_Knee_joint"],
            effort_limit=36.0,
            velocity_limit=17.3,
            stiffness=30.0,
            damping=1.0,
            friction=0.0,
            armature=0.0,
            min_delay=0,
            max_delay=5,
        ),
    },
)

DEEPROBOTICS_M20_CFG = ArticulationCfg(
    spawn=sim_utils.UsdFileCfg(
        usd_path=m20_usd_path,
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
            enabled_self_collisions=True,
            solver_position_iteration_count=4,
            solver_velocity_iteration_count=1,
        ),
    ),
    init_state=ArticulationCfg.InitialStateCfg(
        pos=(0.0, 0.0, 0.52),
        joint_pos={
            ".*hipx_joint": 0.0,
            "f[l,r]_hipy_joint": -0.6,
            "h[l,r]_hipy_joint": 0.6,
            "f[l,r]_knee_joint": 1.0,
            "h[l,r]_knee_joint": -1.0,
            ".*wheel_joint": 0.0,
        },
        joint_vel={".*": 0.0},
    ),
    soft_joint_pos_limit_factor=0.9,
    actuators={
        "joint": DelayedPDActuatorCfg(
            joint_names_expr=[".*hipx_joint", ".*hipy_joint", ".*knee_joint"],
            effort_limit=76.4,
            velocity_limit=22.4,
            stiffness=80.0,
            damping=2.0,
            friction=0.0,
            armature=0.0,
            min_delay=0,
            max_delay=5,
        ),
        "wheel": DelayedPDActuatorCfg(
            joint_names_expr=[".*_wheel_joint"],
            effort_limit=21.6,
            velocity_limit=79.3,
            stiffness=0.0,
            damping=0.6,
            friction=0.0,
            armature=0.00243216,
            min_delay=0,
            max_delay=5,
        ),
    },
)

M20_UR5_JOINT_NAMES = list(UR5_JOINT_NAMES)
M20_UR5_DEFAULT_JOINT_POS = dict(UR5_DEFAULT_JOINT_POS)
M20_UR5_MOUNT_PROFILE = UR5_MOUNT_PROFILES["m20"]
LITE3_UR5_MOUNT_PROFILE = UR5_MOUNT_PROFILES["lite3"]

DEEPROBOTICS_M20_UR5_CFG = build_ur5_host_cfg(DEEPROBOTICS_M20_CFG, M20_UR5_MOUNT_PROFILE)
DEEPROBOTICS_LITE3_UR5_CFG = build_ur5_host_cfg(DEEPROBOTICS_LITE3_CFG, LITE3_UR5_MOUNT_PROFILE)
