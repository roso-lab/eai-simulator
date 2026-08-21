#!/usr/bin/env python3
"""Publish test commands to an EAI robot's ROS2 cmd_vel topic.

Run this external client with the selected system ROS Python, not the Isaac Lab
Conda interpreter. The simulator bridge has no stale-command watchdog, so a
process exit alone does not prove the robot stopped.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from collections.abc import Callable, Sequence
from typing import Any


STOP_MESSAGE_COUNT = 3


def normalize_robot_name(value: str) -> str:
    robot_name = str(value or "").strip().strip("/")
    if not robot_name:
        raise argparse.ArgumentTypeError("robot name must not be empty")
    if "/" in robot_name:
        raise argparse.ArgumentTypeError("robot name must not contain embedded slashes")
    return robot_name


def finite_float(value: str | float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("value must be a number") from exc
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError("value must be finite")
    return number


def non_negative_rate(value: str | float) -> float:
    rate = finite_float(value)
    if rate < 0.0:
        raise argparse.ArgumentTypeError("rate must be non-negative")
    return rate


def cmd_vel_topic(robot_name: str) -> str:
    return f"/{normalize_robot_name(robot_name)}/cmd_vel"


def build_twist(twist_type: type, linear_x: float, angular_z: float):
    message = twist_type()
    message.linear.x = float(linear_x)
    message.linear.y = 0.0
    message.linear.z = 0.0
    message.angular.x = 0.0
    message.angular.y = 0.0
    message.angular.z = float(angular_z)
    return message


def publish_zero_velocity(
    publisher: Any,
    twist_type: type,
    *,
    repeat_count: int = STOP_MESSAGE_COUNT,
    wait_for_delivery: Callable[[], None] = lambda: None,
) -> tuple[tuple[str, Exception], ...]:
    errors: list[tuple[str, Exception]] = []
    for attempt in range(1, max(0, int(repeat_count)) + 1):
        try:
            publisher.publish(build_twist(twist_type, 0.0, 0.0))
        except Exception as exc:
            errors.append((f"stop publish {attempt}", exc))
        try:
            wait_for_delivery()
        except Exception as exc:
            errors.append((f"stop delivery wait {attempt}", exc))
    return tuple(errors)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send cmd_vel commands to an EAI simulator robot.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "The simulator bridge has no stale-command watchdog. Process exit alone "
            "does not prove the robot stopped; verify delivery of zero velocity and "
            "observe the robot."
        ),
    )
    parser.add_argument(
        "--robot",
        type=normalize_robot_name,
        default="carter_1",
        help="Robot instance name (default: carter_1).",
    )
    parser.add_argument(
        "--linear",
        type=finite_float,
        default=0.0,
        help="linear.x velocity in m/s (default: 0.0).",
    )
    parser.add_argument(
        "--angular",
        type=finite_float,
        default=0.0,
        help="angular.z velocity in rad/s (default: 0.0).",
    )
    parser.add_argument(
        "--rate",
        type=non_negative_rate,
        default=0.0,
        help="Publication rate in Hz; 0 publishes once (default: 0).",
    )
    return parser


def _load_ros():
    try:
        import rclpy
        from geometry_msgs.msg import Twist
        from rclpy.node import Node
        from rclpy.signals import SignalHandlerOptions
    except Exception as exc:
        ros_distro = os.environ.get("ROS_DISTRO", "humble")
        raise RuntimeError(
            f"ROS2 Python modules are unavailable. Source /opt/ros/{ros_distro}/setup.bash "
            "and run this tool with the selected system ROS Python."
        ) from exc
    return rclpy, Node, Twist, SignalHandlerOptions


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        rclpy, Node, Twist, SignalHandlerOptions = _load_ros()
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 2

    initialized = False
    node = None
    publisher = None
    timer = None
    primary_error: Exception | None = None
    cleanup_errors: list[tuple[str, Exception]] = []
    teardown_errors: list[tuple[str, Exception]] = []
    try:
        rclpy.init(signal_handler_options=SignalHandlerOptions.NO)
        initialized = True
        node = Node("cmd_vel_test_publisher")
        topic = cmd_vel_topic(args.robot)
        publisher = node.create_publisher(Twist, topic, 10)

        def publish_command() -> None:
            publisher.publish(build_twist(Twist, args.linear, args.angular))

        if args.rate > 0.0:
            timer = node.create_timer(1.0 / args.rate, publish_command)
            node.get_logger().info(f"Publishing continuously to {topic} ({args.rate:g} Hz)")
            node.get_logger().info(f"linear.x={args.linear:.2f}, angular.z={args.angular:.2f}")
            node.get_logger().info("Press Ctrl+C to stop")
            try:
                rclpy.spin(node)
            except KeyboardInterrupt:
                pass
            except Exception as exc:
                primary_error = exc
        else:
            publish_command()
            node.get_logger().info(f"Published once to {topic}")
            node.get_logger().info(f"linear.x={args.linear:.2f}, angular.z={args.angular:.2f}")
            rclpy.spin_once(node, timeout_sec=0.5)
    except Exception as exc:
        primary_error = exc
    finally:
        if args.rate > 0.0 and publisher is not None:
            timer_stopped = timer is None
            if timer is not None:
                try:
                    timer.cancel()
                    timer_stopped = True
                except Exception as exc:
                    cleanup_errors.append(("timer cancellation", exc))
                    destroy_timer = getattr(node, "destroy_timer", None)
                    if destroy_timer is not None:
                        try:
                            destroy_timer(timer)
                            timer_stopped = True
                        except Exception as destroy_exc:
                            cleanup_errors.append(("timer destruction", destroy_exc))

            def wait_for_stop_delivery() -> None:
                if timer_stopped:
                    rclpy.spin_once(node, timeout_sec=0.1)

            cleanup_errors.extend(
                publish_zero_velocity(
                    publisher,
                    Twist,
                    wait_for_delivery=wait_for_stop_delivery,
                )
            )
        if node is not None:
            try:
                node.destroy_node()
            except Exception as exc:
                teardown_errors.append(("node destruction", exc))
        if initialized:
            try:
                rclpy.shutdown()
            except Exception as exc:
                teardown_errors.append(("ROS2 shutdown", exc))

    if primary_error is not None:
        print(f"Failed to publish cmd_vel: {primary_error}", file=sys.stderr)
    for phase, exc in cleanup_errors:
        print(f"cmd_vel cleanup failed during {phase}: {exc}", file=sys.stderr)
    for phase, exc in teardown_errors:
        print(f"cmd_vel teardown failed during {phase}: {exc}", file=sys.stderr)
    return 2 if primary_error is not None or cleanup_errors or teardown_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
