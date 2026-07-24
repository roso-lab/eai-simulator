import isaaclab.sim as sim_utils
from isaaclab.actuators import ImplicitActuatorCfg
from isaaclab.assets.articulation import ArticulationCfg

from EAI_assets.asset_resolver import asset_path


usd_path = asset_path("payloads/manipulators/z1/z1_description.usda")
Z1_JOINT_NAMES = ("joint1", "joint2", "joint3", "joint4", "joint5", "joint6")
Z1_GRIPPER_JOINT_NAME = "jointGripper"
Z1_DEFAULT_JOINT_POS = {
    "joint1": 0.0,
    "joint2": 0.8,
    "joint3": -1.2,
    "joint4": 0.0,
    "joint5": 0.0,
    "joint6": 0.0,
    "jointGripper": -0.35,
}
Z1_ARM_ACTUATOR = {
    "effort_limit_sim": 60.0,
    "velocity_limit_sim": 3.1415,
    "stiffness": 300.0,
    "damping": 20.0,
}
Z1_GRIPPER_ACTUATOR = {
    "effort_limit_sim": 30.0,
    "velocity_limit_sim": 3.1415,
    "stiffness": 200.0,
    "damping": 10.0,
}


def build_z1_actuators() -> dict[str, ImplicitActuatorCfg]:
    return {
        "arm": ImplicitActuatorCfg(
            joint_names_expr=list(Z1_JOINT_NAMES),
            **Z1_ARM_ACTUATOR,
        ),
        "gripper": ImplicitActuatorCfg(
            joint_names_expr=[Z1_GRIPPER_JOINT_NAME],
            **Z1_GRIPPER_ACTUATOR,
        ),
    }

Z1_CFG = ArticulationCfg(
    spawn=sim_utils.UsdFileCfg(
        usd_path=usd_path,
        rigid_props=sim_utils.RigidBodyPropertiesCfg(
            disable_gravity=False,
            retain_accelerations=False,
            linear_damping=0.0,
            angular_damping=0.0,
            max_linear_velocity=1000.0,
            max_angular_velocity=1000.0,
            max_depenetration_velocity=5.0,
        ),
        articulation_props=sim_utils.ArticulationRootPropertiesCfg(
            enabled_self_collisions=False,
            solver_position_iteration_count=16,
            solver_velocity_iteration_count=1,
            fix_root_link=True,
        ),
        activate_contact_sensors=False,
    ),
    init_state=ArticulationCfg.InitialStateCfg(
        pos=(0.0, 0.0, 0.0),
        joint_pos=dict(Z1_DEFAULT_JOINT_POS),
        joint_vel={".*": 0.0},
    ),
    actuators=build_z1_actuators(),
)
