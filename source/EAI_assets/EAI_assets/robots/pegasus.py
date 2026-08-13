# Copyright (c) 2023, Marcelo Fialho Jacinto.
# Copyright (c) 2026, EAI Simulator contributors.
# SPDX-License-Identifier: BSD-3-Clause

"""Pegasus Simulator multirotor asset configurations for Isaac Lab."""

from __future__ import annotations

import isaaclab.sim as sim_utils
from EAI_assets.asset_resolver import asset_path
from isaaclab.actuators import ImplicitActuatorCfg
from isaaclab.assets import ArticulationCfg


def _multirotor_cfg(relative_path: str) -> ArticulationCfg:
    return ArticulationCfg(
        prim_path="{ENV_REGEX_NS}/Robot",
        spawn=sim_utils.UsdFileCfg(
            usd_path=asset_path(relative_path),
            rigid_props=sim_utils.RigidBodyPropertiesCfg(
                disable_gravity=False,
                max_depenetration_velocity=10.0,
                enable_gyroscopic_forces=True,
            ),
            articulation_props=sim_utils.ArticulationRootPropertiesCfg(
                enabled_self_collisions=False,
                solver_position_iteration_count=4,
                solver_velocity_iteration_count=1,
                sleep_threshold=0.005,
                stabilization_threshold=0.001,
            ),
            copy_from_source=False,
        ),
        init_state=ArticulationCfg.InitialStateCfg(
            pos=(0.0, 0.0, 1.0),
            joint_pos={".*": 0.0},
            joint_vel={".*": 0.0},
        ),
        actuators={
            "rotors": ImplicitActuatorCfg(
                joint_names_expr=["joint[0-3]"],
                stiffness=0.0,
                damping=0.0,
            )
        },
    )


PEGASUS_IRIS_CFG = _multirotor_cfg("robot/pegasus/iris/iris.usd")
"""3DR Iris asset shipped by Pegasus Simulator."""

PEGASUS_X4_CFG = _multirotor_cfg("robot/pegasus/pegasus/pegasus_optimized.usdc")
"""Optimized Pegasus research quadrotor asset."""
