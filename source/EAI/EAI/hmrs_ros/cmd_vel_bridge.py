#!/usr/bin/env python3
# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause

"""ROS2 cmd_vel input bridge for EAI Simulator."""

import torch
from typing import Optional, Tuple
from .twist_subscriber import ROS2TwistSubscriber


class ROS2CmdVelBridge:
    """
    单机器人 ROS2 cmd_vel 控制输入桥接器。

    功能：
    - 订阅 /{robot_name}/cmd_vel 话题获取导航控制命令
    - 提供统一接口读取速度命令

    注意：
    - 传感器发布由 Orsus 自动处理，无需此类管理
    - 里程计发布由 Orsus 自动处理，无需此类管理
    """

    def __init__(
        self,
        robot_name: str,
        device: str = "cuda:0",
        cmd_vel_topic: Optional[str] = None,
    ):
        """
        初始化 cmd_vel 桥接器。

        Args:
            robot_name: 机器人实例名称（例如 "carter_1"）
            device: 设备（cuda:0 或 cpu）
            cmd_vel_topic: cmd_vel 话题名称（默认为 /{robot_name}/cmd_vel）
        """
        self.robot_name = robot_name
        self.device = device

        # 设置话题名称
        if cmd_vel_topic is None:
            cmd_vel_topic = f"/{robot_name}/cmd_vel"
        self.cmd_vel_topic = cmd_vel_topic

        # 创建 ROS2 订阅节点
        self.ros_twist_node: Optional[ROS2TwistSubscriber] = None
        self._is_setup = False

    def setup(self) -> bool:
        """
        创建 ROS2 订阅节点

        Returns:
            是否成功创建
        """
        if self._is_setup:
            print(f"[ROS2CmdVelBridge] ⚠️  Already setup for {self.robot_name}")
            return True

        try:
            graph_path = f"/World/ROS2_CmdVel/{self.robot_name}_graph"

            self.ros_twist_node = ROS2TwistSubscriber(
                graph_path=graph_path,
                topic_name=self.cmd_vel_topic,
                node_name=f"{self.robot_name}_cmd_vel_sub",
            )

            if self.ros_twist_node.create():
                self._is_setup = True
                print(f"[ROS2CmdVelBridge] ✅ Created subscriber: {self.cmd_vel_topic}")
                return True
            else:
                print("[ROS2CmdVelBridge] ❌ Failed to create cmd_vel subscriber")
                return False

        except Exception as e:
            print(f"[ROS2CmdVelBridge] ❌ Error during setup: {e}")
            import traceback
            traceback.print_exc()
            return False

    def get_cmd_vel(self) -> Tuple[float, float, float]:
        """
        获取当前的 cmd_vel 命令

        Returns:
            (linear_x, linear_y, angular_z) 元组
            如果无法读取，返回 (0.0, 0.0, 0.0)
        """
        if not self._is_setup or self.ros_twist_node is None:
            return (0.0, 0.0, 0.0)

        try:
            linear_x, angular_z = self.ros_twist_node.get_velocities()

            if linear_x is None or angular_z is None:
                return (0.0, 0.0, 0.0)

            # 对于差速机器人，linear_y 始终为 0
            return (float(linear_x), 0.0, float(angular_z))

        except Exception as e:
            # 静默失败，返回零速度
            return (0.0, 0.0, 0.0)

    def get_twist_command(self) -> Tuple[float, float, float, float]:
        """
        获取当前完整 Twist 命令。

        Returns:
            (linear_x, linear_y, linear_z, angular_z) 元组
            如果无法读取，返回 0。
        """
        if not self._is_setup or self.ros_twist_node is None:
            return (0.0, 0.0, 0.0, 0.0)

        try:
            if hasattr(self.ros_twist_node, "get_twist_command"):
                linear_x, linear_y, linear_z, angular_z = self.ros_twist_node.get_twist_command()
            else:
                linear_x, angular_z = self.ros_twist_node.get_velocities()
                linear_y = linear_z = 0.0

            return (
                0.0 if linear_x is None else float(linear_x),
                0.0 if linear_y is None else float(linear_y),
                0.0 if linear_z is None else float(linear_z),
                0.0 if angular_z is None else float(angular_z),
            )
        except Exception:
            return (0.0, 0.0, 0.0, 0.0)

    def get_cmd_vel_tensor(self, num_envs: int = 1) -> torch.Tensor:
        """
        获取 cmd_vel 命令的 torch tensor 格式

        Args:
            num_envs: 环境数量（通常为 1）

        Returns:
            形状为 (num_envs, 3) 的 tensor: [vx, vy, wz]
        """
        vx, vy, wz = self.get_cmd_vel()
        cmd_tensor = torch.zeros((num_envs, 3), device=self.device)
        cmd_tensor[:, 0] = vx
        cmd_tensor[:, 1] = vy
        cmd_tensor[:, 2] = wz
        return cmd_tensor

    def cleanup(self):
        """清理 ROS2 节点"""
        if self.ros_twist_node is not None:
            try:
                self.ros_twist_node.delete()
                print(f"[ROS2CmdVelBridge] ✅ Cleaned up {self.robot_name}")
            except Exception as e:
                print(f"[ROS2CmdVelBridge] ⚠️  Cleanup warning: {e}")

        self._is_setup = False
        self.ros_twist_node = None
