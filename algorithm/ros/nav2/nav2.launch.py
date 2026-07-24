#!/usr/bin/env python3
"""
EAI simulator + Nav2 unified navigation launch.

This launch entrypoint is robot/scene parameterized.  It first runs
nav2_setup.py to generate concrete params/pc2scan/rviz files, then starts:
  1. tf_bridge
  2. pointcloud_to_laserscan
  3. map_server and amcl
  4. Nav2 planner/controller/behavior/bt/smoother/waypoint nodes
  5. lifecycle_manager
  6. rviz, optionally

Usage:
    source /opt/ros/humble/setup.bash
    ros2 launch algorithm/ros/nav2/nav2.launch.py robot_name:=carter_1 robot_type:=Carter scene:=factory
    ros2 launch algorithm/ros/nav2/nav2.launch.py robot_name:=go2_1 robot_type:=Go2 scene:=plane rviz:=true
"""

import os
import subprocess
import sys

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction, TimerAction
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(THIS_DIR, "..", "..", ".."))
NAV2_SETUP = os.path.join(THIS_DIR, "nav2_setup.py")
TF_BRIDGE = os.path.join(THIS_DIR, "tf_bridge.py")
DEFAULT_RUNTIME_SNAPSHOT = os.path.join(REPO_ROOT, "tmp", "runtime_interfaces.json")

LIFECYCLE_NODES = [
    "map_server", "amcl", "controller_server", "smoother_server",
    "planner_server", "behavior_server", "bt_navigator",
    "waypoint_follower", "velocity_smoother",
]


def _run_setup(context):
    """Run nav2_setup.py at launch time, then build the Nav2 node graph."""
    robot_name_arg = LaunchConfiguration("robot_name").perform(context)
    robot_arg = LaunchConfiguration("robot").perform(context)
    robot = robot_name_arg if robot_name_arg else robot_arg

    robot_type = LaunchConfiguration("robot_type").perform(context)
    scene = LaunchConfiguration("scene").perform(context)
    map_arg = LaunchConfiguration("map").perform(context)
    pose_arg = LaunchConfiguration("pose").perform(context)
    runtime_snapshot_arg = LaunchConfiguration("runtime_snapshot").perform(context)
    use_rviz = LaunchConfiguration("rviz")

    cmd = [
        sys.executable,
        NAV2_SETUP,
        "--robot",
        robot,
        "--scene",
        scene,
        "--runtime-snapshot",
        runtime_snapshot_arg,
    ]
    if robot_type:
        cmd += ["--robot-type", robot_type]
    if map_arg:
        cmd += ["--map", map_arg]
    if pose_arg:
        cmd += ["--pose", pose_arg]

    result = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(result.stdout)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise RuntimeError(f"nav2_setup.py failed with exit code {result.returncode}")

    kv = {}
    for line in result.stdout.strip().splitlines():
        if "=" in line and line.split("=", 1)[0] in (
            "PARAMS", "PC2SCAN", "RVIZ", "LIDAR_XYZ", "LIDAR_RPY", "MAP"
        ):
            k, v = line.split("=", 1)
            kv[k] = v

    params_file = kv["PARAMS"]
    pc2scan_file = kv["PC2SCAN"]
    rviz_file = kv["RVIZ"]
    lidar_xyz = kv.get("LIDAR_XYZ", "0.0,0.0,0.4")
    lidar_rpy = kv.get("LIDAR_RPY", "0.0,0.0,0.0")
    map_path = kv.get("MAP", "")

    if not map_path:
        raise RuntimeError(
            f"Scene '{scene}' has no registered map. Use map:=/abs/path.yaml, "
            "or register/generate a scene map in nav2_profiles.yaml/nav2_setup.py."
        )

    lx, ly, lz = lidar_xyz.split(",")
    lr, lp, lyaw = lidar_rpy.split(",")

    tf_bridge = Node(
        executable=os.environ.get("EAI_NAV2_ROS_PYTHON", "/usr/bin/python3"),
        arguments=[TF_BRIDGE, "--ros-args",
                   "-p", f"robot:={robot}",
                   "-p", "use_sim_time:=true",
                   "-p", f"lidar_xyz:=[{lx}, {ly}, {lz}]",
                   "-p", f"lidar_rpy:=[{lr}, {lp}, {lyaw}]"],
        name="eai_nav2_tf_bridge",
        output="screen",
    )

    pc2scan = Node(
        package="pointcloud_to_laserscan",
        executable="pointcloud_to_laserscan_node",
        name="pointcloud_to_laserscan",
        parameters=[pc2scan_file, {"use_sim_time": True}],
        remappings=[
            ("cloud_in", f"/{robot}/scan_cloud"),
            ("scan", f"/{robot}/scan"),
        ],
        output="screen",
    )

    map_server = Node(
        package="nav2_map_server", executable="map_server", name="map_server",
        output="screen",
        parameters=[params_file, {"yaml_filename": map_path, "use_sim_time": True}],
    )

    amcl = Node(
        package="nav2_amcl", executable="amcl", name="amcl",
        output="screen", parameters=[params_file],
    )

    controller = Node(
        package="nav2_controller", executable="controller_server",
        name="controller_server", output="screen", parameters=[params_file],
        remappings=[("cmd_vel", "cmd_vel_nav")],
    )
    smoother = Node(
        package="nav2_smoother", executable="smoother_server",
        name="smoother_server", output="screen", parameters=[params_file],
    )
    planner = Node(
        package="nav2_planner", executable="planner_server",
        name="planner_server", output="screen", parameters=[params_file],
    )
    behavior = Node(
        package="nav2_behaviors", executable="behavior_server",
        name="behavior_server", output="screen", parameters=[params_file],
        remappings=[("cmd_vel", "cmd_vel_nav")],
    )
    bt_navigator = Node(
        package="nav2_bt_navigator", executable="bt_navigator",
        name="bt_navigator", output="screen", parameters=[params_file],
    )
    waypoint_follower = Node(
        package="nav2_waypoint_follower", executable="waypoint_follower",
        name="waypoint_follower", output="screen", parameters=[params_file],
    )
    velocity_smoother = Node(
        package="nav2_velocity_smoother", executable="velocity_smoother",
        name="velocity_smoother", output="screen", parameters=[params_file],
        remappings=[
            ("cmd_vel", "cmd_vel_nav"),
            ("cmd_vel_smoothed", f"/{robot}/cmd_vel"),
        ],
    )

    lifecycle_manager = Node(
        package="nav2_lifecycle_manager", executable="lifecycle_manager",
        name="lifecycle_manager_navigation", output="screen",
        parameters=[{"use_sim_time": True, "autostart": True,
                     "node_names": LIFECYCLE_NODES}],
    )

    rviz = Node(
        package="rviz2", executable="rviz2", name="rviz2",
        arguments=["-d", rviz_file],
        parameters=[{"use_sim_time": True}],
        output="screen",
        condition=IfCondition(use_rviz),
    )

    return [
        tf_bridge, pc2scan, map_server, amcl,
        controller, smoother, planner, behavior, bt_navigator,
        waypoint_follower, velocity_smoother, rviz,
        TimerAction(period=3.0, actions=[lifecycle_manager]),
    ]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("robot_name", default_value="",
                              description="Robot instance name, preferred over robot"),
        DeclareLaunchArgument("robot", default_value="carter_1",
                              description="Legacy robot instance name alias"),
        DeclareLaunchArgument("robot_type", default_value="",
                              description="Robot profile type, such as Carter/Go2/B2/Scout"),
        DeclareLaunchArgument("scene", default_value="factory",
                              description="Scene name used for map and default pose lookup"),
        DeclareLaunchArgument("map", default_value="",
                              description="Explicit map yaml, overriding scene lookup"),
        DeclareLaunchArgument("pose", default_value="",
                              description="Explicit initial pose x,y,yaw"),
        DeclareLaunchArgument("runtime_snapshot", default_value=DEFAULT_RUNTIME_SNAPSHOT,
                              description="Active simulator snapshot used for AMCL initial pose"),
        DeclareLaunchArgument("rviz", default_value="false",
                              description="Whether to start RViz"),
        OpaqueFunction(function=_run_setup),
    ])
