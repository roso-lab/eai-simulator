from __future__ import annotations

import argparse
import os
import sys
import termios
import time
import tty
from dataclasses import dataclass


@dataclass(frozen=True)
class VelocityCommand:
    linear_x: float = 0.0
    linear_y: float = 0.0
    linear_z: float = 0.0
    angular_z: float = 0.0


def topic_for_robot(robot_name: str) -> str:
    clean = str(robot_name).strip().strip("/")
    if not clean:
        raise ValueError("robot name cannot be empty")
    return f"/{clean}/cmd_vel"


def parse_args(args=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish keyboard velocity commands to an EAI simulator cmd_vel topic.")
    parser.add_argument("--robot", type=str, default=None, help="Single robot name used to build /<robot>/cmd_vel.")
    parser.add_argument("--robots", type=str, default="", help="Comma-separated robot names for Q switching.")
    parser.add_argument("--topic", type=str, default=None, help="Explicit single cmd_vel topic override.")
    parser.add_argument(
        "--linear-speed",
        type=float,
        default=0.5,
        help="Forward/backward speed for W/S (A/D lateral input is only used by holonomic bases).",
    )
    parser.add_argument(
        "--vertical-speed",
        type=float,
        default=0.5,
        help="Vertical speed for aerial robots using R/F.",
    )
    parser.add_argument("--angular-speed", type=float, default=0.8, help="Angular speed for C/V keys.")
    parser.add_argument("--rate", type=float, default=20.0, help="Publish rate in Hz.")
    parser.add_argument("--discover-timeout", type=float, default=5.0, help="Seconds to wait for /<robot>/cmd_vel topics.")
    parsed = parser.parse_args(args)
    parsed.robots = parse_robot_names(parsed.robots)
    return parsed


def parse_robot_names(raw: str) -> tuple[str, ...]:
    return tuple(name.strip().strip("/") for name in str(raw or "").split(",") if name.strip().strip("/"))


def initial_topics_from_args(parsed: argparse.Namespace) -> tuple[str, ...]:
    if parsed.topic:
        return (str(parsed.topic),)
    if parsed.robots:
        return tuple(topic_for_robot(robot) for robot in parsed.robots)
    if parsed.robot:
        return (topic_for_robot(parsed.robot),)
    return ()


def discover_cmd_vel_topics(topic_names_and_types) -> tuple[str, ...]:
    topics: list[str] = []
    for name, types in topic_names_and_types:
        if not str(name).endswith("/cmd_vel"):
            continue
        if "geometry_msgs/msg/Twist" not in set(types):
            continue
        topics.append(str(name))
    return tuple(sorted(set(topics)))


def next_topic_index(current_index: int, topics: tuple[str, ...]) -> int:
    if not topics:
        return 0
    return (current_index + 1) % len(topics)


def command_for_key(
    key: str,
    *,
    linear_speed: float,
    vertical_speed: float = 0.5,
    angular_speed: float,
) -> VelocityCommand:
    return {
        "w": VelocityCommand(linear_x=linear_speed),
        "s": VelocityCommand(linear_x=-linear_speed),
        "a": VelocityCommand(linear_y=linear_speed),
        "d": VelocityCommand(linear_y=-linear_speed),
        "r": VelocityCommand(linear_z=vertical_speed),
        "f": VelocityCommand(linear_z=-vertical_speed),
        "c": VelocityCommand(angular_z=angular_speed),
        "v": VelocityCommand(angular_z=-angular_speed),
        "k": VelocityCommand(),
        " ": VelocityCommand(),
    }.get(key.lower(), VelocityCommand())


def _read_key() -> str:
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def _import_ros_modules():
    try:
        import rclpy
        from geometry_msgs.msg import Twist
    except ModuleNotFoundError as exc:
        _raise_ros_import_error(exc)
    return rclpy, Twist


def _raise_ros_import_error(exc: ModuleNotFoundError) -> None:
    message = str(exc)
    if "_rclpy_pybind11" in message or sys.prefix != sys.base_prefix:
        ros_distro = os.environ.get("ROS_DISTRO", "humble")
        python3 = "/usr/bin/python3"
        hint_command = f"source /opt/ros/{ros_distro}/setup.bash && {python3} algorithm/keyboard/keyboard.py"
        raise SystemExit(
            f"[EAI Keyboard] ROS2 {ros_distro} rclpy is unavailable in the current Python. "
            f"Current Python is {sys.version.split()[0]} ({sys.executable}).\n"
            f"Run with ROS Python instead:\n  {hint_command}"
        ) from exc
    raise exc


def _wait_for_discovered_topics(node, rclpy, timeout_sec: float) -> tuple[str, ...]:
    deadline = time.monotonic() + max(0.0, timeout_sec)
    topics: tuple[str, ...] = ()
    while rclpy.ok():
        topics = discover_cmd_vel_topics(node.get_topic_names_and_types())
        if topics or time.monotonic() >= deadline:
            return topics
        rclpy.spin_once(node, timeout_sec=0.1)
    return topics


def _publish_command(Twist, publisher, command: VelocityCommand) -> None:
    msg = Twist()
    msg.linear.x = command.linear_x
    msg.linear.y = command.linear_y
    msg.linear.z = command.linear_z
    msg.angular.z = command.angular_z
    publisher.publish(msg)


def main(args=None) -> None:
    parsed = parse_args(args)
    rclpy, Twist = _import_ros_modules()

    rclpy.init()
    node = rclpy.create_node("eai_keyboard_cmd_vel")
    topics = initial_topics_from_args(parsed)
    if not topics:
        print("[EAI Keyboard] Discovering /<robot>/cmd_vel topics...")
        topics = _wait_for_discovered_topics(node, rclpy, parsed.discover_timeout)
    if not topics:
        node.destroy_node()
        rclpy.shutdown()
        raise SystemExit(
            "[EAI Keyboard] No /<robot>/cmd_vel topics found. "
            "Start simulator.py with keyboard/ros tool first, or pass --robots go2_1,lite3_1."
        )

    publishers = {topic: node.create_publisher(Twist, topic, 10) for topic in topics}
    current_index = 0
    print("[EAI Keyboard] Topics:")
    for index, topic in enumerate(topics, start=1):
        marker = " <- current" if index == 1 else ""
        print(f"  [{index}] {topic}{marker}")
    print(
        "[EAI Keyboard] W/S forward/back, A/D lateral, R/F ascend/descend, "
        "C/V yaw, K or Space stop, Q switch robot, Esc/Ctrl-C quit."
    )
    try:
        while rclpy.ok():
            key = _read_key()
            if key in {"\x03", "\x1b"}:
                break
            if key.lower() == "q":
                _publish_command(Twist, publishers[topics[current_index]], VelocityCommand())
                current_index = next_topic_index(current_index, topics)
                print(f"\n[EAI Keyboard] Switched to {topics[current_index]}")
                continue
            command = command_for_key(
                key,
                linear_speed=parsed.linear_speed,
                vertical_speed=parsed.vertical_speed,
                angular_speed=parsed.angular_speed,
            )
            _publish_command(Twist, publishers[topics[current_index]], command)
            rclpy.spin_once(node, timeout_sec=0)
    finally:
        stop = VelocityCommand()
        for publisher in publishers.values():
            _publish_command(Twist, publisher, stop)
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
