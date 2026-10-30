from pathlib import Path

import isaaclab.sim as sim_utils
from isaaclab.actuators import DCMotorCfg, ImplicitActuatorCfg
from isaaclab.assets.articulation import ArticulationCfg
from isaaclab.utils.assets import ISAAC_NUCLEUS_DIR, ISAACLAB_NUCLEUS_DIR

from EAI_assets.asset_resolver import asset_path

_SOURCE_USD_PATH = asset_path("robot/b2/b2.usd")
_CANONICAL_USD_PATH = asset_path("robot/b2/b2_canonical.usdc")
usd_path = _CANONICAL_USD_PATH if Path(_CANONICAL_USD_PATH).is_file() else _SOURCE_USD_PATH

B2_CFG = ArticulationCfg(
    spawn=sim_utils.UsdFileCfg(
        usd_path=usd_path,
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
            enabled_self_collisions=False, solver_position_iteration_count=4, solver_velocity_iteration_count=0
        ),
    ),
    
    init_state=ArticulationCfg.InitialStateCfg(
        pos=(0, 0.0, 0.58),  # B2 initial height 0.58m (matching training config)
        joint_pos={
            ".*L_hip_joint": 0.0,
            ".*R_hip_joint": -0.0,
            "F.*_thigh_joint": 0.8,
            "R.*_thigh_joint": 0.8,
            ".*_calf_joint": -1.5,
        },
        joint_vel={".*": 0.0},
    ),
    soft_joint_pos_limit_factor=0.9,
    actuators={
        "hip": DCMotorCfg(  # Matches robot_lab actuator names and type
            joint_names_expr=[".*_hip_joint"],
            effort_limit=200.0,
            saturation_effort=200.0,
            velocity_limit=23.0,
            stiffness=160.0,  # 160.0
            damping=5.0,  # 5.0
            friction=0.0,  # Matches robot_lab
        ),
        "thigh": DCMotorCfg(  # Matches robot_lab actuator names and type
            joint_names_expr=[".*_thigh_joint"],
            effort_limit=200.0,
            saturation_effort=200.0,
            velocity_limit=23.0,
            stiffness=160.0,
            damping=5.0,
            friction=0.0,  # Matches robot_lab
        ),
        "calf": DCMotorCfg(  # Matches robot_lab actuator names and type
            joint_names_expr=[".*_calf_joint"],
            effort_limit=320.0,
            saturation_effort=320.0,
            velocity_limit=14.0,
            stiffness=160.0,
            damping=5.0,
            friction=0.0,  # Matches robot_lab
        ),
    },
)
