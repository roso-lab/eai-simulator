from __future__ import annotations

import math
from typing import Any

from .manipulator_omnigraph import (
    ManipulatorActiveCommand,
    ManipulatorGripperCommand,
    ManipulatorModelSpec,
    ManipulatorOmniGraphManager,
    get_manipulator_graph_manager,
    manipulator_topic_names,
)


D1_JOINT_NAMES = ("Joint1", "Joint2", "Joint3", "Joint4", "Joint5", "Joint6")
D1_GRIPPER_JOINT_NAMES = ("Joint7_1", "Joint7_2")
D1_GRIPPER_STROKE_M = 0.03
D1_MODEL_SPEC = ManipulatorModelSpec(
    model="d1",
    joint_names=D1_JOINT_NAMES,
    ee_body_names=("Link6",),
    gripper_joint_names=D1_GRIPPER_JOINT_NAMES,
)


def d1_gripper_targets(closure01: float) -> tuple[float, float]:
    """Map a gripper closure coefficient to per-finger meter targets.

    D1 carries a mirrored prismatic finger pair: Joint7_1 opens toward +0.03 m
    and Joint7_2 toward -0.03 m, and both are fully closed at 0.0 m.  A single
    radian-style command cannot express both strokes, so callers drive the
    fingers with this closure mapping instead: ``closure01=0.0`` is open and
    ``closure01=1.0`` is closed.  The single-joint ROS gripper paths keep
    ignoring D1 until they are extended to consume this mapping.
    """
    closure = float(closure01)
    if not math.isfinite(closure) or closure < 0.0 or closure > 1.0:
        raise ValueError(f"d1 gripper closure must be within [0, 1]: {closure01}")
    stroke = D1_GRIPPER_STROKE_M * (1.0 - closure)
    return (stroke, -stroke)


def d1_topic_names(robot_name: str) -> dict[str, str]:
    return manipulator_topic_names(robot_name, D1_MODEL_SPEC)


def get_d1_graph_manager(env: Any) -> ManipulatorOmniGraphManager | None:
    return get_manipulator_graph_manager(env)


D1ActiveCommand = ManipulatorActiveCommand
D1GripperCommand = ManipulatorGripperCommand
D1OmniGraphManager = ManipulatorOmniGraphManager


__all__ = [
    "D1ActiveCommand",
    "D1GripperCommand",
    "D1OmniGraphManager",
    "D1_GRIPPER_JOINT_NAMES",
    "D1_GRIPPER_STROKE_M",
    "D1_JOINT_NAMES",
    "D1_MODEL_SPEC",
    "d1_gripper_targets",
    "d1_topic_names",
    "get_d1_graph_manager",
]
