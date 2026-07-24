# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause

"""
ROS2 Subscribe Twist Graph Node Creator

通过纯代码创建和管理 ROS2 Subscribe Twist Graph 节点，用于订阅 geometry_msgs/Twist 消息。
"""

import omni.usd
from pxr import Sdf
import omni.graph.core as og
from typing import Optional, Tuple
import time


class ROS2TwistSubscriber:
    """ROS2 Subscribe Twist Graph 节点管理器。
    
    用于创建和管理 ROS2 Subscribe Twist 节点，订阅 geometry_msgs/Twist 消息。
    提供接口读取节点的输出属性（linear.x/y/z, angular.z）。
    """
    
    def __init__(
        self,
        graph_path: str,
        topic_name: str,
        node_name: str = "ros2_twist_subscriber",
    ):
        """
        初始化 ROS2 Subscribe Twist Graph 节点。
        
        Args:
            graph_path: Graph 的路径，例如 "/World/ros_graph"
            topic_name: 订阅的 ROS2 话题名称，例如 "/HUB_ID/cmd_vel"
            node_name: 节点名称，默认 "ros2_twist_subscriber"
        """
        self.graph_path = graph_path
        self.topic_name = topic_name
        self.node_name = node_name
        self.node_path = f"{graph_path}/{node_name}"
        self._controller = og.Controller()
        self._keys = og.Controller.Keys
        self._created = False
        
    def create(self) -> bool:
        """
        创建 Action Graph 和 ROS2 Subscribe Twist 节点。

        构建完整可运行的图：
            OnPlaybackTick.tick → ROS2SubscribeTwist.execIn
        并将 ROS2 Context 连接到订阅节点，确保节点每帧执行、真正订阅话题。

        Returns:
            是否成功创建
        """
        tick_node = "on_playback_tick"
        context_node = "ros2_context"
        try:
            stage = omni.usd.get_context().get_stage()

            # 若订阅节点已存在，仅更新话题名并复用
            node_prim = stage.GetPrimAtPath(self.node_path)
            if node_prim.IsValid():
                print(f"[ROS2Graph] ⚠️  Node already exists at {self.node_path}, reusing it")
                try:
                    self._controller.set(f"{self.node_path}.inputs:topicName", self.topic_name)
                except Exception:
                    topic_attr = node_prim.GetAttribute("inputs:topicName")
                    if not topic_attr:
                        topic_attr = node_prim.CreateAttribute("inputs:topicName", Sdf.ValueTypeNames.String)
                    if topic_attr:
                        topic_attr.Set(self.topic_name)
                self._created = True
                return True

            # 若已存在同名 Graph 但类型不对，删除后重建
            graph_prim = stage.GetPrimAtPath(self.graph_path)
            if graph_prim.IsValid() and graph_prim.GetTypeName() != "OmniGraph":
                stage.RemovePrim(self.graph_path)

            keys = og.Controller.Keys
            twist_type = "isaacsim.ros2.bridge.ROS2SubscribeTwist"
            context_type = "isaacsim.ros2.bridge.ROS2Context"

            og.Controller.edit(
                {"graph_path": self.graph_path, "evaluator_name": "execution"},
                {
                    keys.CREATE_NODES: [
                        (tick_node, "omni.graph.action.OnPlaybackTick"),
                        (context_node, context_type),
                        (self.node_name, twist_type),
                    ],
                    keys.CONNECT: [
                        (f"{tick_node}.outputs:tick", f"{self.node_name}.inputs:execIn"),
                        (f"{context_node}.outputs:context", f"{self.node_name}.inputs:context"),
                    ],
                    keys.SET_VALUES: [
                        (f"{self.node_name}.inputs:topicName", self.topic_name),
                    ],
                },
            )

            node_prim = stage.GetPrimAtPath(self.node_path)
            if not node_prim.IsValid():
                print(f"[ROS2Graph] ❌ Created node path is invalid: {self.node_path}")
                return False

            self._created = True
            print(f"[ROS2Graph] ✅ ROS2 Subscribe Twist graph ready at {self.graph_path} (topic: {self.topic_name})")
            return True

        except Exception as e:
            print(f"[ROS2Graph] ❌ Error creating ROS2 Subscribe Twist node: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def get_linear_x(self) -> Optional[float]:
        """
        获取线速度 x 分量。

        Returns:
            线速度 x (m/s)，如果无法读取则返回 None
        """
        linear_x, _ = self.get_velocities()
        return linear_x

    def get_angular_z(self) -> Optional[float]:
        """
        获取角速度 z 分量。

        Returns:
            角速度 z (rad/s)，如果无法读取则返回 None
        """
        _, angular_z = self.get_velocities()
        return angular_z

    def get_velocities(self) -> Tuple[Optional[float], Optional[float]]:
        """
        获取线速度和角速度。

        通过 OmniGraph Controller API 读取节点运行时计算出的输出值
        （USD GetAttribute 只能读到 authored/default 值，读不到运行时结果）。

        Returns:
            (linear_x, angular_z) 元组，如果无法读取则为 (None, None)
        """
        if not self._created:
            return (None, None)

        try:
            linear_attr = og.Controller.attribute(f"{self.node_path}.outputs:linearVelocity")
            angular_attr = og.Controller.attribute(f"{self.node_path}.outputs:angularVelocity")

            linear_value = og.Controller.get(linear_attr)
            angular_value = og.Controller.get(angular_attr)

            linear_x = None
            angular_z = None
            if linear_value is not None and len(linear_value) >= 1:
                linear_x = float(linear_value[0])
            if angular_value is not None and len(angular_value) >= 3:
                angular_z = float(angular_value[2])

            return (linear_x, angular_z)
        except Exception as e:
            print(f"[ROS2Graph] ⚠️  Warning: Failed to read velocities: {e}")
            return (None, None)

    def get_twist_command(self) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
        """
        获取完整 Twist 控制量。

        Returns:
            (linear_x, linear_y, linear_z, angular_z) 元组，无法读取的分量为 None。
        """
        if not self._created:
            return (None, None, None, None)

        try:
            linear_attr = og.Controller.attribute(f"{self.node_path}.outputs:linearVelocity")
            angular_attr = og.Controller.attribute(f"{self.node_path}.outputs:angularVelocity")

            linear_value = og.Controller.get(linear_attr)
            angular_value = og.Controller.get(angular_attr)

            linear_x = linear_y = linear_z = angular_z = None
            if linear_value is not None:
                if len(linear_value) >= 1:
                    linear_x = float(linear_value[0])
                if len(linear_value) >= 2:
                    linear_y = float(linear_value[1])
                if len(linear_value) >= 3:
                    linear_z = float(linear_value[2])
            if angular_value is not None and len(angular_value) >= 3:
                angular_z = float(angular_value[2])

            return (linear_x, linear_y, linear_z, angular_z)
        except Exception as e:
            print(f"[ROS2Graph] ⚠️  Warning: Failed to read twist command: {e}")
            return (None, None, None, None)
    
    def delete(self) -> bool:
        """
        删除节点和 Graph。
        
        Returns:
            是否成功删除
        """
        try:
            if self._created:
                stage = omni.usd.get_context().get_stage()
                
                # 删除节点
                node_prim = stage.GetPrimAtPath(self.node_path)
                if node_prim.IsValid():
                    stage.RemovePrim(self.node_path)
                    print(f"[ROS2Graph] ✅ Deleted node at {self.node_path}")
                
                # 可选：删除 Graph（如果为空）
                graph_prim = stage.GetPrimAtPath(self.graph_path)
                if graph_prim.IsValid() and len(list(graph_prim.GetChildren())) == 0:
                    stage.RemovePrim(self.graph_path)
                    print(f"[ROS2Graph] ✅ Deleted graph at {self.graph_path}")
                
                self._created = False
                return True
            return False
        except Exception as e:
            print(f"[ROS2Graph] ❌ Error deleting node: {e}")
            return False
    
    def is_created(self) -> bool:
        """
        检查节点是否已创建。
        
        Returns:
            节点是否已创建
        """
        return self._created
