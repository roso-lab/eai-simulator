#!/usr/bin/env python3
"""Launch the independent system-ROS EAI-to-product navigation adapter."""

from __future__ import annotations

import os
from pathlib import Path
import sys

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from profile_config import load_profile  # noqa: E402


DEFAULT_PROFILES = THIS_DIR / "config" / "profiles.yaml"
ADAPTER_NODE = THIS_DIR / "adapter_node.py"
SYSTEM_ROS_PYTHON = "/usr/bin/python3"


def _vector_override(value: str, field: str) -> list[float] | None:
    value = value.strip().strip("[]")
    if not value:
        return None
    try:
        result = [float(item.strip()) for item in value.split(",")]
    except ValueError as exc:
        raise ValueError(f"{field} must contain comma-separated numeric values") from exc
    if len(result) != 3:
        raise ValueError(f"{field} must contain exactly three values")
    return result


def _launch_adapter(context):
    profile_name = LaunchConfiguration("profile_name").perform(context).strip()
    if not profile_name:
        profile_name = LaunchConfiguration("profile").perform(context)
    robot_name = LaunchConfiguration("robot_name").perform(context)
    nav_hub_id = LaunchConfiguration("nav_hub_id").perform(context)
    profiles_file = LaunchConfiguration("profiles_file").perform(context)
    config = load_profile(
        profiles_file,
        profile_name,
        robot_name=robot_name,
        nav_hub_id=nav_hub_id,
        overrides={
            "cloud_input": LaunchConfiguration("cloud_input").perform(context),
            "odometry_input": LaunchConfiguration("odometry_input").perform(context),
        },
    )
    xyz = _vector_override(
        LaunchConfiguration("base_to_lidar_xyz").perform(context), "base_to_lidar_xyz"
    )
    rpy = _vector_override(
        LaunchConfiguration("base_to_lidar_rpy").perform(context), "base_to_lidar_rpy"
    )
    if xyz is not None:
        config["base_to_lidar_xyz"] = xyz
    if rpy is not None:
        config["base_to_lidar_rpy"] = rpy
    config["use_sim_time"] = True
    python = os.environ.get("EAI_NAV_PRODUCT_ROS_PYTHON", SYSTEM_ROS_PYTHON)
    return [
        Node(
            executable=python,
            arguments=[str(ADAPTER_NODE)],
            name=f"{config['nav_hub_id']}_nav_product_adapter",
            parameters=[config],
            output="screen",
        )
    ]


def generate_launch_description():
    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "profile",
                default_value="octagon_aio_c01",
                description=(
                    "Adapter profile: octagon_aio_c01, octagon_aio_c01_carter, mid360, lidar_carter, or orsus_carter"
                ),
            ),
            DeclareLaunchArgument(
                "profile_name",
                default_value="",
                description="Optional adapter profile alias; takes precedence over profile",
            ),
            DeclareLaunchArgument(
                "robot_name",
                default_value="simple_car_1",
                description="EAI robot instance used by the internal topic contract",
            ),
            DeclareLaunchArgument(
                "nav_hub_id",
                default_value="nav_hub_1",
                description="Single ROS name component used by product topics and frames",
            ),
            DeclareLaunchArgument(
                "profiles_file",
                default_value=str(DEFAULT_PROFILES),
                description="Version 1 adapter profile YAML",
            ),
            DeclareLaunchArgument(
                "cloud_input",
                default_value="",
                description="Optional absolute override for the EAI PointCloud2 input",
            ),
            DeclareLaunchArgument(
                "odometry_input",
                default_value="",
                description="Optional absolute override for the EAI Odometry input",
            ),
            DeclareLaunchArgument(
                "base_to_lidar_xyz",
                default_value="",
                description="Optional measured T_base_lidar translation x,y,z in metres",
            ),
            DeclareLaunchArgument(
                "base_to_lidar_rpy",
                default_value="",
                description="Optional measured T_base_lidar roll,pitch,yaw in radians",
            ),
            OpaqueFunction(function=_launch_adapter),
        ]
    )
