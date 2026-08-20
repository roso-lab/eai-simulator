#!/usr/bin/env python3
"""
发送 Nav2 导航目标（NavigateToPose action）到仿真中的 Carter。

用法（系统 ROS2 环境）：
    source /opt/ros/$ROS_DISTRO/setup.bash
    /usr/bin/python3 algorithm/ros/nav2/send_goal.py --x 0.0 --y 0.0
    /usr/bin/python3 algorithm/ros/nav2/send_goal.py --x 2.0 --y -3.0 --yaw 1.57
"""

import argparse
import math
import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from nav2_msgs.action import NavigateToPose
from geometry_msgs.msg import PoseStamped


def yaw_to_quat(yaw):
    return (0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0))


class GoalSender(Node):
    def __init__(self, x, y, yaw):
        super().__init__("eai_nav2_goal_sender")
        self.client = ActionClient(self, NavigateToPose, "navigate_to_pose")
        self.x, self.y, self.yaw = x, y, yaw

    def send(self):
        self.get_logger().info("等待 navigate_to_pose action server...")
        if not self.client.wait_for_server(timeout_sec=10.0):
            self.get_logger().error("❌ action server 未就绪（Nav2 是否已启动并激活？）")
            return False

        goal = NavigateToPose.Goal()
        goal.pose.header.frame_id = "map"
        goal.pose.header.stamp = self.get_clock().now().to_msg()
        goal.pose.pose.position.x = self.x
        goal.pose.pose.position.y = self.y
        qx, qy, qz, qw = yaw_to_quat(self.yaw)
        goal.pose.pose.orientation.x = qx
        goal.pose.pose.orientation.y = qy
        goal.pose.pose.orientation.z = qz
        goal.pose.pose.orientation.w = qw

        self.get_logger().info(f"发送目标: x={self.x}, y={self.y}, yaw={self.yaw}")
        future = self.client.send_goal_async(goal, feedback_callback=self.on_feedback)
        rclpy.spin_until_future_complete(self, future)
        handle = future.result()
        if not handle.accepted:
            self.get_logger().error("❌ 目标被拒绝")
            return False
        self.get_logger().info("✅ 目标已接受，导航中...")
        result_future = handle.get_result_async()
        rclpy.spin_until_future_complete(self, result_future)
        self.get_logger().info(f"✅ 导航结束，状态码: {result_future.result().status}")
        return True

    def on_feedback(self, feedback_msg):
        fb = feedback_msg.feedback
        self.get_logger().info(
            f"  剩余距离: {fb.distance_remaining:.2f}m", throttle_duration_sec=2.0
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--x", type=float, required=True)
    parser.add_argument("--y", type=float, required=True)
    parser.add_argument("--yaw", type=float, default=0.0)
    args = parser.parse_args()

    rclpy.init()
    node = GoalSender(args.x, args.y, args.yaw)
    try:
        node.send()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
