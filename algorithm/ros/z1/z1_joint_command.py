#!/usr/bin/env python3
"""Publish simple JointState commands for the Unitree Z1 Isaac Sim bridge."""

from __future__ import annotations

import argparse
import math
from typing import Iterable


JOINT_NAMES = ["joint1", "joint2", "joint3", "joint4", "joint5", "joint6", "jointGripper"]

PRESETS = {
    "home": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    "ready": [0.0, 0.8, -1.2, 0.0, 0.0, 0.0, -0.35],
}


def parse_position_list(raw_positions: str) -> list[float]:
    values = [part for part in raw_positions.replace(",", " ").split() if part]
    positions = [float(value) for value in values]
    if len(positions) != len(JOINT_NAMES):
        raise ValueError(f"Expected {len(JOINT_NAMES)} positions, got {len(positions)}")
    if not all(math.isfinite(position) for position in positions):
        raise ValueError("All joint positions must be finite numbers")
    return positions


def resolve_positions(preset: str, raw_positions: str | None) -> list[float]:
    if raw_positions:
        return parse_position_list(raw_positions)
    if preset not in PRESETS:
        choices = ", ".join(sorted(PRESETS))
        raise ValueError(f"Unknown preset {preset!r}. Available presets: {choices}")
    return list(PRESETS[preset])


def build_joint_state_msg(positions: Iterable[float]):
    from sensor_msgs.msg import JointState

    msg = JointState()
    msg.name = list(JOINT_NAMES)
    msg.position = [float(value) for value in positions]
    msg.velocity = []
    msg.effort = []
    return msg


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish Unitree Z1 JointState position commands.")
    parser.add_argument("--topic", default="/z1/joint_commands", help="Command topic consumed by Isaac Sim.")
    parser.add_argument("--preset", default="home", choices=sorted(PRESETS), help="Named pose preset.")
    parser.add_argument(
        "--positions",
        default=None,
        help="Seven joint positions in radians, separated by commas or spaces. Overrides --preset.",
    )
    parser.add_argument("--rate", type=float, default=10.0, help="Publish rate in Hz.")
    parser.add_argument("--once", action="store_true", help="Publish one message and exit.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    positions = resolve_positions(args.preset, args.positions)
    if args.rate <= 0.0:
        raise ValueError("--rate must be positive")

    import rclpy

    rclpy.init()
    node = rclpy.create_node("z1_joint_command")
    publisher = node.create_publisher(build_joint_state_msg(positions).__class__, args.topic, 10)
    period = 1.0 / args.rate

    def publish() -> None:
        msg = build_joint_state_msg(positions)
        msg.header.stamp = node.get_clock().now().to_msg()
        publisher.publish(msg)

    if args.once:
        publish()
        rclpy.spin_once(node, timeout_sec=0.1)
    else:
        timer = node.create_timer(period, publish)
        try:
            rclpy.spin(node)
        except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
            pass
        finally:
            node.destroy_timer(timer)

    node.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
