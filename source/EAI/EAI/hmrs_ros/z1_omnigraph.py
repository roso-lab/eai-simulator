from __future__ import annotations

from typing import Any

from .manipulator_omnigraph import (
    ManipulatorActiveCommand,
    ManipulatorGripperCommand,
    ManipulatorModelSpec,
    ManipulatorOmniGraphManager,
    get_manipulator_graph_manager,
    manipulator_topic_names,
)


Z1_JOINT_NAMES = ("joint1", "joint2", "joint3", "joint4", "joint5", "joint6")
Z1_GRIPPER_JOINT_NAME = "jointGripper"
Z1_MODEL_SPEC = ManipulatorModelSpec(
    model="z1",
    joint_names=Z1_JOINT_NAMES,
    ee_body_names=("link06", "gripperStator"),
    gripper_joint_name=Z1_GRIPPER_JOINT_NAME,
)


def z1_topic_names(robot_name: str) -> dict[str, str]:
    return manipulator_topic_names(robot_name, Z1_MODEL_SPEC)


def get_z1_graph_manager(env: Any) -> ManipulatorOmniGraphManager | None:
    return get_manipulator_graph_manager(env)


Z1ActiveCommand = ManipulatorActiveCommand
Z1GripperCommand = ManipulatorGripperCommand
Z1OmniGraphManager = ManipulatorOmniGraphManager


__all__ = [
    "Z1ActiveCommand",
    "Z1GripperCommand",
    "Z1OmniGraphManager",
    "Z1_GRIPPER_JOINT_NAME",
    "Z1_JOINT_NAMES",
    "Z1_MODEL_SPEC",
    "get_z1_graph_manager",
    "z1_topic_names",
]
