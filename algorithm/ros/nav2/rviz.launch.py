#!/usr/bin/env python3
"""
单独启动 RViz2 查看 EAI + Nav2 导航状态（机器人可参数化）。

会先用 nav2_setup.py 生成对应机器人的 view.rviz（scan 话题带正确命名空间），再启动 RViz。

用法（系统 ROS2 环境）：
    source /opt/ros/humble/setup.bash
    ros2 launch algorithm/ros/nav2/rviz.launch.py                 # 默认 carter_1
    ros2 launch algorithm/ros/nav2/rviz.launch.py robot_name:=go2_1 robot_type:=Go2
"""

import os
import subprocess
import sys

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
NAV2_SETUP = os.path.join(THIS_DIR, "nav2_setup.py")


def _launch_rviz(context):
    robot_name_arg = LaunchConfiguration("robot_name").perform(context)
    robot_arg = LaunchConfiguration("robot").perform(context)
    robot = robot_name_arg if robot_name_arg else robot_arg
    robot_type = LaunchConfiguration("robot_type").perform(context)
    scene = LaunchConfiguration("scene").perform(context)

    cmd = [sys.executable, NAV2_SETUP, "--robot", robot, "--scene", scene]
    if robot_type:
        cmd += ["--robot-type", robot_type]
    result = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(result.stdout)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise RuntimeError("nav2_setup.py 失败")

    rviz_file = None
    for line in result.stdout.splitlines():
        if line.startswith("RVIZ="):
            rviz_file = line.split("=", 1)[1]
    if not rviz_file:
        raise RuntimeError("未能从 nav2_setup.py 获取 RVIZ 配置路径")

    return [Node(
        package="rviz2", executable="rviz2", name="rviz2",
        arguments=["-d", rviz_file],
        parameters=[{"use_sim_time": True}],
        output="screen",
    )]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("robot_name", default_value=""),
        DeclareLaunchArgument("robot", default_value="carter_1"),
        DeclareLaunchArgument("robot_type", default_value=""),
        DeclareLaunchArgument("scene", default_value="factory"),
        OpaqueFunction(function=_launch_rviz),
    ])
