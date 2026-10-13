# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause

"""
Controllers Package for DirectMARL Environments

This package provides controller configurations and utilities for multi-robot DirectMARL environments.
No longer supports ManagerBased environments.
"""

from .base import ControllerCfg, load_all_controllers, normalize_controller_entry
from .differential_drive_controller import DifferentialDriveControllerCfg
from .planar_velocity_controller import PlanarVelocityControllerCfg
from .ackermann_controller import AckermannControllerCfg
from .ik_controller import (
    ArmPostApplyPipeline,
    apply_end_effector_pose,
    apply_manipulator_pose_command,
    apply_planar_end_effector_pose,
    hold_gripper_position,
    noop_post_apply,
    resolve_end_effector_body_id,
)
from .idealized_manipulation import (
    CylindricalPose,
    DEFAULT_ATTACHMENT_MODE,
    DEFAULT_ATTACHMENT_SETTLE_DURATION_S,
    DEFAULT_GRIPPER_POSITION_TOLERANCE_RAD,
    DEFAULT_PHASE_TIMEOUT_STEPS,
    DEFAULT_POSITION_TOLERANCE_M,
    DEFAULT_RADIAL_SPEED_M_S,
    DEFAULT_VERTICAL_SPEED_M_S,
    DEFAULT_YAW_SPEED_RAD_S,
    DEFAULT_YAW_TOLERANCE_RAD,
    DEFAULT_Z1_GRIPPER_CLOSED_POSITION_RAD,
    DEFAULT_Z1_GRIPPER_COLLISION_BODY_NAMES,
    DEFAULT_Z1_GRIPPER_OPEN_POSITION_RAD,
    HeldObject,
    IdealizedManipulationRuntime,
    ManipulationPhase,
    RobotManipulationState,
    PlanarIkSpec,
    cylindrical_to_local_position,
    cylindrical_to_world_position,
    local_position_to_cylindrical,
    planar_ik_spec_for_model,
    feasible_yaw_joint_target,
    normalize_cylindrical_pose,
    parse_cylindrical_pose,
    wrap_angle_rad,
)
from .rsl_controller import RSLControllerCfg
from .skrl_controller import SKRLControllerCfg

__all__ = [
    "ArmPostApplyPipeline",
    "ControllerCfg",
    "SKRLControllerCfg",
    "RSLControllerCfg",
    "DifferentialDriveControllerCfg",
    "PlanarVelocityControllerCfg",
    "AckermannControllerCfg",
    "load_all_controllers",
    "normalize_controller_entry",
    "noop_post_apply",
    "apply_end_effector_pose",
    "apply_manipulator_pose_command",
    "apply_planar_end_effector_pose",
    "hold_gripper_position",
    "resolve_end_effector_body_id",
    "CylindricalPose",
    "DEFAULT_ATTACHMENT_MODE",
    "DEFAULT_ATTACHMENT_SETTLE_DURATION_S",
    "DEFAULT_GRIPPER_POSITION_TOLERANCE_RAD",
    "DEFAULT_PHASE_TIMEOUT_STEPS",
    "DEFAULT_POSITION_TOLERANCE_M",
    "DEFAULT_RADIAL_SPEED_M_S",
    "DEFAULT_VERTICAL_SPEED_M_S",
    "DEFAULT_YAW_SPEED_RAD_S",
    "DEFAULT_YAW_TOLERANCE_RAD",
    "DEFAULT_Z1_GRIPPER_CLOSED_POSITION_RAD",
    "DEFAULT_Z1_GRIPPER_COLLISION_BODY_NAMES",
    "DEFAULT_Z1_GRIPPER_OPEN_POSITION_RAD",
    "HeldObject",
    "IdealizedManipulationRuntime",
    "ManipulationPhase",
    "RobotManipulationState",
    "PlanarIkSpec",
    "cylindrical_to_local_position",
    "cylindrical_to_world_position",
    "local_position_to_cylindrical",
    "planar_ik_spec_for_model",
    "feasible_yaw_joint_target",
    "normalize_cylindrical_pose",
    "parse_cylindrical_pose",
    "wrap_angle_rad",
]
