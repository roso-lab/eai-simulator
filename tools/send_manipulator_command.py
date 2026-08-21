#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import os
import time
from typing import Any, Sequence


MODEL_JOINTS = {
    "ur5": ("shoulder_pan_joint", "shoulder_lift_joint", "elbow_joint", "wrist_1_joint", "wrist_2_joint", "wrist_3_joint"),
    "z1": ("joint1", "joint2", "joint3", "joint4", "joint5", "joint6"),
}
SUBSCRIBER_DISCOVERY_TIMEOUT = 3.0


def finite_float(value: str | float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("value must be a number") from exc
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError("value must be finite")
    return number


def non_negative_finite_float(value: str | float) -> float:
    number = finite_float(value)
    if number < 0.0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return number


def _normalize_topic_segment(value: str, label: str) -> str:
    normalized = str(value or "").strip().strip("/")
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    if "/" in normalized:
        raise ValueError(f"{label} must not contain embedded slashes")
    return normalized


def topic_names(robot: str, model: str) -> dict[str, str]:
    robot_name = _normalize_topic_segment(robot, "robot")
    model_name = _normalize_topic_segment(model, "model")
    if model_name not in MODEL_JOINTS:
        raise ValueError(f"Unsupported model: {model_name}")
    namespace = f"/{robot_name}/{model_name}"
    return {
        "target_pose": f"{namespace}/target_pose",
        "joint_command": f"{namespace}/joint_command",
        "joint_states": f"{namespace}/joint_states",
        "ee_pose": f"{namespace}/ee_pose",
        "gripper_command": f"{namespace}/gripper_command",
        "gripper_state": f"{namespace}/gripper_state",
    }


def validate_target(
    model: str,
    joint: Sequence[float] | None = None,
    xyz: Sequence[float] | None = None,
    gripper: float | None = None,
) -> None:
    model_name = _normalize_topic_segment(model, "model")
    if model_name not in MODEL_JOINTS:
        raise ValueError(f"Unsupported model: {model_name}")
    if sum(target is not None for target in (joint, xyz, gripper)) != 1:
        raise ValueError("Specify exactly one of joint, xyz, or gripper")
    if joint is not None and len(joint) != len(MODEL_JOINTS[model_name]):
        raise ValueError(f"Model {model_name} requires {len(MODEL_JOINTS[model_name])} joint values")
    if xyz is not None and len(xyz) != 3:
        raise ValueError("Cartesian targets require exactly three xyz values")
    if gripper is not None and model_name != "z1":
        raise ValueError("--gripper is only supported by model z1")


def joint_error(
    state_names: Sequence[Any],
    state_positions: Sequence[Any],
    target_names: Sequence[str],
    target_positions: Sequence[float],
) -> float | None:
    if len(target_names) != len(target_positions):
        raise ValueError("Target joint names and positions must have the same length")
    values = {
        str(name).rsplit("/", 1)[-1]: float(value)
        for name, value in zip(state_names, state_positions)
    }
    normalized_targets = [str(name).rsplit("/", 1)[-1] for name in target_names]
    if any(name not in values for name in normalized_targets):
        return None
    return math.sqrt(
        sum(
            (values[name] - float(expected)) ** 2
            for name, expected in zip(normalized_targets, target_positions)
        )
    )


def position_error(actual: Sequence[Any], target: Sequence[Any]) -> float:
    if len(actual) != 3 or len(target) != 3:
        raise ValueError("Cartesian positions require exactly three values")
    return math.sqrt(sum((float(a) - float(b)) ** 2 for a, b in zip(actual, target)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send native EAI manipulator ROS2 commands.")
    parser.add_argument("--robot", required=True)
    parser.add_argument("--model", required=True, choices=sorted(MODEL_JOINTS))
    targets = parser.add_mutually_exclusive_group(required=True)
    targets.add_argument("--joint", nargs=6, type=finite_float)
    targets.add_argument("--xyz", nargs=3, type=finite_float)
    targets.add_argument("--gripper", type=finite_float)
    parser.add_argument("--quat", nargs=4, type=finite_float, default=(0.0, 0.0, 0.0, 0.0))
    parser.add_argument("--frame-id", choices=("world", "base_link"), default="world")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument(
        "--timeout",
        type=non_negative_finite_float,
        default=20.0,
        help=(
            "Feedback wait after publication; subscriber discovery can take up to "
            f"{SUBSCRIBER_DISCOVERY_TIMEOUT:g} additional seconds (default: 20)."
        ),
    )
    parser.add_argument("--eps", type=non_negative_finite_float, default=0.07)
    parser.add_argument("--joint-eps", type=non_negative_finite_float, default=0.05)
    return parser


def _load_ros():
    try:
        import rclpy
        from geometry_msgs.msg import PoseStamped
        from rclpy.node import Node
        from sensor_msgs.msg import JointState
    except (ImportError, ModuleNotFoundError) as exc:
        ros_distro = os.environ.get("ROS_DISTRO", "humble")
        raise SystemExit(f"Run with ROS2 {ros_distro} Python after sourcing /opt/ros/{ros_distro}/setup.bash") from exc
    return rclpy, Node, PoseStamped, JointState


def _wait_for_subscriber(
    rclpy,
    node,
    publisher,
    timeout: float = SUBSCRIBER_DISCOVERY_TIMEOUT,
) -> None:
    deadline = time.monotonic() + timeout
    while publisher.get_subscription_count() == 0 and time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.05)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        validate_target(args.model, args.joint, args.xyz, args.gripper)
        if args.xyz is not None and args.wait and args.frame_id != "world":
            raise ValueError(
                "--wait for pose targets requires --frame-id world because ee_pose uses world coordinates"
            )
        robot_name = _normalize_topic_segment(args.robot, "robot")
        topics = topic_names(robot_name, args.model)
    except ValueError as exc:
        parser.error(str(exc))
    rclpy, Node, PoseStamped, JointState = _load_ros()
    initialized = False
    node = None
    reached = False
    last_error: float | None = None
    subscription = None
    try:
        rclpy.init()
        initialized = True
        node = Node(f"eai_{args.model}_command_{robot_name}")
        if args.joint is not None or args.gripper is not None:
            target = list(args.joint) if args.joint is not None else [float(args.gripper)]
            command_topic = topics["joint_command"] if args.joint is not None else topics["gripper_command"]
            state_topic = topics["joint_states"] if args.joint is not None else topics["gripper_state"]
            names = list(MODEL_JOINTS[args.model]) if args.joint is not None else ["jointGripper"]
            publisher = node.create_publisher(JointState, command_topic, 10)
            message = JointState()
            message.name = names
            message.position = target

            def on_joint(state: Any) -> None:
                nonlocal reached, last_error
                error = joint_error(state.name, state.position, names, target)
                if error is None:
                    return
                last_error = error
                reached = last_error <= float(args.joint_eps)

            if args.wait:
                subscription = node.create_subscription(JointState, state_topic, on_joint, 10)
        else:
            target = [float(value) for value in args.xyz]
            publisher = node.create_publisher(PoseStamped, topics["target_pose"], 10)
            message = PoseStamped()
            message.header.frame_id = args.frame_id
            message.pose.position.x, message.pose.position.y, message.pose.position.z = target
            message.pose.orientation.x, message.pose.orientation.y, message.pose.orientation.z, message.pose.orientation.w = args.quat

            def on_pose(state: Any) -> None:
                nonlocal reached, last_error
                actual = [state.pose.position.x, state.pose.position.y, state.pose.position.z]
                last_error = position_error(actual, target)
                reached = last_error <= float(args.eps)

            if args.wait:
                subscription = node.create_subscription(PoseStamped, topics["ee_pose"], on_pose, 10)
        _wait_for_subscriber(rclpy, node, publisher)
        deadline = time.monotonic() + max(0.0, float(args.timeout))
        while True:
            message.header.stamp = node.get_clock().now().to_msg()
            publisher.publish(message)
            if not args.wait:
                print(f"[sent] {publisher.topic_name}")
                return 0
            rclpy.spin_once(node, timeout_sec=0.1)
            if reached:
                print(f"[success] robot={robot_name} model={args.model} error={last_error:.5f}")
                return 0
            if time.monotonic() >= deadline:
                print(f"[timeout] robot={robot_name} model={args.model} error={last_error}")
                return 2
            time.sleep(0.4)
    except KeyboardInterrupt:
        return 130
    finally:
        try:
            if subscription is not None and node is not None:
                node.destroy_subscription(subscription)
            if node is not None:
                node.destroy_node()
        finally:
            if initialized:
                rclpy.try_shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
