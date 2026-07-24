# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause
"""Carter differential drive robot configuration."""

import isaaclab.sim as sim_utils
from isaaclab.actuators import ImplicitActuatorCfg
from isaaclab.assets.articulation import ArticulationCfg

from EAI_assets.asset_resolver import asset_path

usd_path = asset_path("robot/carter/carter.usd")

CARTER_CFG = ArticulationCfg(
    spawn=sim_utils.UsdFileCfg(
        usd_path=usd_path,
        activate_contact_sensors=True,

        rigid_props=sim_utils.RigidBodyPropertiesCfg(
            disable_gravity=False,
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
    ),

    init_state=ArticulationCfg.InitialStateCfg(
        pos=(0.0, 0.0, 0.2),
    ),

    
    actuators={
        "base_wheels": ImplicitActuatorCfg(
            # 控制两个主动轮：joint_wheel_left 和 joint_wheel_right
            joint_names_expr=["joint_wheel_left", "joint_wheel_right"],
            
            # 刚度 (Stiffness / P-gain): 必须设为 0.0 以实现速度控制
            stiffness=0.0,
            
            # 阻尼 (Damping / D-gain): 必须设得很大
            # 在刚度为 0 时，阻尼充当了"速度控制增益"的角色。
            # 物理公式：Force = Damping * (目标速度 - 当前速度)
            # Carter小车需要足够大的阻尼值才能推得动且刹得住。
            damping=10000.0, 
            
            effort_limit=None,   # 力矩上限 (设为None表示无限力)
            velocity_limit=None, # 速度上限 (设为None表示无限快)
        ),
        # 万向轮 / 摆臂等被动关节：与 Isaac Lab 关节数对齐，避免 2 != 7 的 actuator 警告
        "passive_casters": ImplicitActuatorCfg(
            joint_names_expr=[
                "joint_caster_base",
                "joint_swing_left",
                "joint_swing_right",
                "joint_caster_left",
                "joint_caster_right",
            ],
            stiffness=0.0,
            damping=0.0,
            effort_limit_sim=0.0,
        ),
    },
)







