#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import sys
import time
from types import SimpleNamespace
from typing import Any, Sequence


UR5_JOINT_NAMES = (
    "shoulder_pan_joint",
    "shoulder_lift_joint",
    "elbow_joint",
    "wrist_1_joint",
    "wrist_2_joint",
    "wrist_3_joint",
)


def topic_names(robot: str) -> dict[str, str]:
    namespace = f"/{str(robot).strip().strip('/')}/ur5"
    return {
        "target_pose": f"{namespace}/target_pose",
        "joint_command": f"{namespace}/joint_command",
        "joint_states": f"{namespace}/joint_states",
        "ee_pose": f"{namespace}/ee_pose",
    }


def finite_values(values: Sequence[Any], count: int) -> list[float] | None:
    if len(values) < count:
        return None
    result: list[float] = []
    try:
        for value in values[:count]:
            number = float(value)
            if not math.isfinite(number):
                return None
            result.append(number)
    except (TypeError, ValueError):
        return None
    return result


def reorder_joint_positions(names: Sequence[Any], positions: Sequence[Any]) -> list[float] | None:
    parsed = finite_values(positions, 6)
    if parsed is None:
        return None
    if not names:
        return parsed
    if len(names) != len(positions):
        return None
    by_name: dict[str, float] = {}
    for name, position in zip(names, positions):
        key = str(name).strip().replace("\\", "/").rsplit("/", 1)[-1]
        try:
            value = float(position)
        except (TypeError, ValueError):
            return None
        if not key or key in by_name or not math.isfinite(value):
            return None
        by_name[key] = value
    if any(name not in by_name for name in UR5_JOINT_NAMES):
        return None
    return [by_name[name] for name in UR5_JOINT_NAMES]


def position_distance(actual: Sequence[Any], target: Sequence[Any]) -> float | None:
    actual_values = finite_values(actual, 3)
    target_values = finite_values(target, 3)
    if actual_values is None or target_values is None:
        return None
    return math.sqrt(sum((actual_values[index] - target_values[index]) ** 2 for index in range(3)))


def joint_error_norm(actual: Sequence[Any], target: Sequence[Any]) -> float | None:
    actual_values = finite_values(actual, 6)
    target_values = finite_values(target, 6)
    if actual_values is None or target_values is None:
        return None
    return math.sqrt(sum((actual_values[index] - target_values[index]) ** 2 for index in range(6)))


def load_ros2_modules() -> SimpleNamespace:
    try:
        import rclpy
        from geometry_msgs.msg import PoseStamped
        from rclpy.node import Node
        from sensor_msgs.msg import JointState
    except ImportError as exc:
        print(f"[error] ROS2 Python modules are unavailable: {exc}", file=sys.stderr)
        print("Run: source /opt/ros/humble/setup.bash", file=sys.stderr)
        raise SystemExit(1) from exc
    return SimpleNamespace(rclpy=rclpy, Node=Node, PoseStamped=PoseStamped, JointState=JointState)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send a native ROS2 command to an EAI UR5 attachment.")
    parser.add_argument("--robot", required=True, help="DIY robot instance name, for example m20_3")
    target_group = parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument("--xyz", nargs=3, type=float, metavar=("X", "Y", "Z"))
    target_group.add_argument(
        "--joint",
        nargs=6,
        type=float,
        metavar=("Q0", "Q1", "Q2", "Q3", "Q4", "Q5"),
    )
    parser.add_argument("--frame-id", default="world", choices=("world", "base_link"))
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--eps", type=float, default=0.07)
    parser.add_argument("--joint-eps", type=float, default=0.05)
    return parser


def _wait_for_discovery(ros: SimpleNamespace, node: Any, publisher: Any, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while publisher.get_subscription_count() == 0 and time.monotonic() < deadline:
        ros.rclpy.spin_once(node, timeout_sec=0.05)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    topics = topic_names(args.robot)
    ros = load_ros2_modules()
    ros.rclpy.init()
    node = ros.Node(f"eai_ur5_command_{args.robot}")
    reached = False
    last_error: float | None = None

    try:
        if args.xyz is not None:
            target = [float(value) for value in args.xyz]
            publisher = node.create_publisher(ros.PoseStamped, topics["target_pose"], 10)
            message = ros.PoseStamped()
            message.header.frame_id = args.frame_id
            message.pose.position.x, message.pose.position.y, message.pose.position.z = target
            message.pose.orientation.w = 1.0

            def on_pose(state: Any) -> None:
                nonlocal reached, last_error
                actual = [state.pose.position.x, state.pose.position.y, state.pose.position.z]
                last_error = position_distance(actual, target)
                reached = last_error is not None and last_error <= float(args.eps)

            if args.wait:
                node.create_subscription(ros.PoseStamped, topics["ee_pose"], on_pose, 10)
        else:
            target = [float(value) for value in args.joint]
            publisher = node.create_publisher(ros.JointState, topics["joint_command"], 10)
            message = ros.JointState()
            message.name = list(UR5_JOINT_NAMES)
            message.position = target

            def on_joint(state: Any) -> None:
                nonlocal reached, last_error
                actual = reorder_joint_positions(state.name, state.position)
                last_error = None if actual is None else joint_error_norm(actual, target)
                reached = last_error is not None and last_error <= float(args.joint_eps)

            if args.wait:
                node.create_subscription(ros.JointState, topics["joint_states"], on_joint, 10)

        _wait_for_discovery(ros, node, publisher)
        deadline = time.monotonic() + max(0.0, float(args.timeout))
        next_publish = 0.0
        while True:
            now = time.monotonic()
            if now >= next_publish:
                message.header.stamp = node.get_clock().now().to_msg()
                publisher.publish(message)
                next_publish = now + 0.5
            if not args.wait:
                print(f"[sent] robot={args.robot} topic={publisher.topic_name}")
                return 0
            ros.rclpy.spin_once(node, timeout_sec=0.05)
            if reached:
                print(f"[success] robot={args.robot} error={last_error:.5f}")
                return 0
            if now >= deadline:
                detail = "no state received" if last_error is None else f"last_error={last_error:.5f}"
                print(f"[timeout] robot={args.robot} {detail}", file=sys.stderr)
                return 2
    finally:
        node.destroy_node()
        ros.rclpy.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
