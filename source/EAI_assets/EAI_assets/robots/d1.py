"""Unitree D1 six-DOF manipulator configuration.

The checked-in D1 USD keeps its authored URDF inertias: the full chain totals
about 1 kg while the physical D1 is specified at 2.37 kg.  The lighter masses
are retained deliberately so simulation matches the upstream URDF source; keep
the discrepancy in mind when tuning effort limits or payload screens.
"""

import isaaclab.sim as sim_utils
from isaaclab.actuators import ImplicitActuatorCfg
from isaaclab.assets.articulation import ArticulationCfg

from EAI_assets.asset_resolver import asset_path


usd_path = asset_path("payloads/manipulators/d1/d1.usd")
D1_JOINT_NAMES = ("Joint1", "Joint2", "Joint3", "Joint4", "Joint5", "Joint6")
D1_GRIPPER_JOINT_NAMES = ("Joint7_1", "Joint7_2")
D1_JOINT_LIMITS = {
    "Joint1": (-2.35, 2.35),
    "Joint2": (-1.57, 1.57),
    "Joint3": (-1.57, 1.57),
    "Joint4": (-2.35, 2.35),
    "Joint5": (-1.57, 1.57),
    "Joint6": (-2.35, 2.35),
}
D1_DEFAULT_JOINT_POS = {
    "Joint1": 0.0,
    "Joint2": -0.4,
    "Joint3": 0.0,
    "Joint4": 0.6,
    "Joint5": 0.0,
    "Joint6": 0.0,
    "Joint7_1": 0.03,
    "Joint7_2": -0.03,
}
D1_ARM_ACTUATOR = {
    "effort_limit_sim": 30.0,
    "velocity_limit_sim": 3.1415,
    "stiffness": 80.0,
    "damping": 8.0,
}
# Joint7_1/Joint7_2 are prismatic fingers, so their sim limits are meters and
# meters per second rather than radians.
D1_GRIPPER_ACTUATOR = {
    "effort_limit_sim": 10.0,
    "velocity_limit_sim": 0.2,
    "stiffness": 40.0,
    "damping": 4.0,
}


def build_d1_actuators() -> dict[str, ImplicitActuatorCfg]:
    return {
        "arm": ImplicitActuatorCfg(
            joint_names_expr=list(D1_JOINT_NAMES),
            **D1_ARM_ACTUATOR,
        ),
        "gripper": ImplicitActuatorCfg(
            joint_names_expr=list(D1_GRIPPER_JOINT_NAMES),
            **D1_GRIPPER_ACTUATOR,
        ),
    }


_D1_JOINT_SCOPES = ("joints", "Joints", "")


def find_d1_joint_prim(stage, prim_path: str, joint_name: str):
    """Return the first existing USD joint prim for a D1 joint name.

    D1 exports have been seen with joints under either a ``joints`` or a
    ``Joints`` scope (or directly under the articulation root); resolve all
    three layouts so spawn helpers do not depend on one exporter convention.
    """
    for scope in _D1_JOINT_SCOPES:
        candidate = f"{prim_path}/{joint_name}" if not scope else f"{prim_path}/{scope}/{joint_name}"
        joint = stage.GetPrimAtPath(candidate)
        if joint.IsValid():
            return joint
    return None


def _set_d1_force_drives(stage, prim_path: str) -> None:
    """Use force position drives for a standalone D1 instance.

    The arm joints (Joint1..Joint6) are revolute and the fingers
    (Joint7_1/Joint7_2) are prismatic, so accept either drive schema.
    """
    for joint_name in (*D1_JOINT_NAMES, *D1_GRIPPER_JOINT_NAMES):
        joint = find_d1_joint_prim(stage, prim_path, joint_name)
        if joint is None:
            raise RuntimeError(f"D1 drive joint does not exist: {prim_path}/{joint_name}")
        drive_type = None
        for attribute_name in ("drive:angular:physics:type", "drive:linear:physics:type"):
            attribute = joint.GetAttribute(attribute_name)
            if attribute.IsValid():
                drive_type = attribute
                break
        if drive_type is None:
            raise RuntimeError(f"D1 drive type attribute does not exist: {joint.GetPath()}")
        drive_type.Set("force")


def spawn_standalone_d1(
    prim_path: str,
    cfg: sim_utils.UsdFileCfg,
    translation=None,
    orientation=None,
) -> None:
    """Spawn D1 and override only its arm and finger drives to force mode."""
    stage = sim_utils.get_current_stage()
    base_cfg = sim_utils.UsdFileCfg(
        usd_path=cfg.usd_path,
        rigid_props=cfg.rigid_props,
        articulation_props=cfg.articulation_props,
        activate_contact_sensors=cfg.activate_contact_sensors,
    )
    sim_utils.spawn_from_usd(
        prim_path,
        base_cfg,
        translation=translation,
        orientation=orientation,
    )
    _set_d1_force_drives(stage, prim_path)

D1_CFG = ArticulationCfg(
    spawn=sim_utils.UsdFileCfg(
        usd_path=usd_path,
        func=spawn_standalone_d1,
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
        joint_pos=dict(D1_DEFAULT_JOINT_POS),
        joint_vel={".*": 0.0},
    ),
    actuators=build_d1_actuators(),
)
