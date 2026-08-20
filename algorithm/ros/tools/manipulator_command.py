#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import os
import time
from dataclasses import dataclass
from typing import Any, Sequence


MODEL_JOINTS = {
    "ur5": ("shoulder_pan_joint", "shoulder_lift_joint", "elbow_joint", "wrist_1_joint", "wrist_2_joint", "wrist_3_joint"),
    "z1": ("joint1", "joint2", "joint3", "joint4", "joint5", "joint6"),
}


def topic_names(robot: str, model: str) -> dict[str, str]:
    namespace = f"/{robot.strip().strip('/')}/{model}"
    return {
        "target_pose": f"{namespace}/target_pose",
        "joint_command": f"{namespace}/joint_command",
        "joint_states": f"{namespace}/joint_states",
        "ee_pose": f"{namespace}/ee_pose",
        "gripper_command": f"{namespace}/gripper_command",
        "gripper_state": f"{namespace}/gripper_state",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Send native EAI manipulator ROS2 commands.")
    parser.add_argument("--robot", required=True)
    parser.add_argument("--model", required=True, choices=sorted(MODEL_JOINTS))
    targets = parser.add_mutually_exclusive_group(required=True)
    targets.add_argument("--joint", nargs=6, type=float)
    targets.add_argument("--xyz", nargs=3, type=float)
    targets.add_argument("--gripper", type=float)
    parser.add_argument("--quat", nargs=4, type=float, default=(0.0, 0.0, 0.0, 0.0))
    parser.add_argument("--frame-id", choices=("world", "base_link"), default="world")
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--eps", type=float, default=0.07)
    parser.add_argument("--joint-eps", type=float, default=0.05)
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


def _wait_for_subscriber(rclpy, node, publisher, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while publisher.get_subscription_count() == 0 and time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.05)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.gripper is not None and args.model != "z1":
        raise SystemExit("--gripper is only supported by model z1")
    rclpy, Node, PoseStamped, JointState = _load_ros()
    topics = topic_names(args.robot, args.model)
    rclpy.init()
    node = Node(f"eai_{args.model}_command_{args.robot}")
    reached = False
    last_error: float | None = None
    subscription = None
    try:
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
                values = {str(name).rsplit("/", 1)[-1]: float(value) for name, value in zip(state.name, state.position)}
                if any(name not in values for name in names):
                    return
                last_error = math.sqrt(sum((values[name] - expected) ** 2 for name, expected in zip(names, target)))
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
                last_error = math.sqrt(sum((a - b) ** 2 for a, b in zip(actual, target)))
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
                print(f"[success] robot={args.robot} model={args.model} error={last_error:.5f}")
                return 0
            if time.monotonic() >= deadline:
                print(f"[timeout] robot={args.robot} model={args.model} error={last_error}")
                return 2
            time.sleep(0.4)
    finally:
        if subscription is not None:
            node.destroy_subscription(subscription)
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
