#!/usr/bin/env python3
"""
发送 Nav2 导航目标（NavigateToPose action）到仿真中的 Carter。

用法（系统 ROS2 环境）：
    source /opt/ros/$ROS_DISTRO/setup.bash
    export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
    /usr/bin/python3 algorithm/nav2/send_goal.py --x 0.0 --y 0.0
    /usr/bin/python3 algorithm/nav2/send_goal.py --x 2.0 --y -3.0 --yaw 1.57
"""

import argparse
import math
import os
import sys
import rclpy
from action_msgs.msg import GoalStatus
from action_msgs.srv import CancelGoal
from rclpy.action import ActionClient
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from nav2_msgs.action import NavigateToPose


NAV2_RMW_IMPLEMENTATION = "rmw_cyclonedds_cpp"
DEFAULT_GOAL_RESPONSE_TIMEOUT = 10.0
DEFAULT_RESULT_TIMEOUT = 300.0
CANCEL_RESPONSE_TIMEOUT = 5.0


def finite_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError("value must be a number") from exc
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError("value must be finite")
    return number


def positive_finite_float(value):
    number = finite_float(value)
    if number <= 0.0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return number


def configure_rmw_implementation():
    configured = os.environ.get("RMW_IMPLEMENTATION")
    if configured and configured != NAV2_RMW_IMPLEMENTATION:
        print(
            "错误: EAI Nav2 使用 "
            f"RMW_IMPLEMENTATION={NAV2_RMW_IMPLEMENTATION}，"
            f"当前环境设置为 {configured}。请取消该变量或改为 CycloneDDS。",
            file=sys.stderr,
        )
        return False
    os.environ["RMW_IMPLEMENTATION"] = NAV2_RMW_IMPLEMENTATION
    return True


def yaw_to_quat(yaw):
    return (0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0))


class GoalSender(Node):
    def __init__(
        self,
        x,
        y,
        yaw,
        goal_response_timeout=DEFAULT_GOAL_RESPONSE_TIMEOUT,
        result_timeout=DEFAULT_RESULT_TIMEOUT,
    ):
        super().__init__("eai_nav2_goal_sender")
        self.client = ActionClient(self, NavigateToPose, "navigate_to_pose")
        self.x, self.y, self.yaw = x, y, yaw
        self.goal_response_timeout = goal_response_timeout
        self.result_timeout = result_timeout

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
        rclpy.spin_until_future_complete(
            self, future, timeout_sec=self.goal_response_timeout
        )
        if not future.done():
            self.get_logger().error(
                "❌ 目标响应超时；服务端是否接收目标未知，请先检查 Nav2 日志，"
                "不要立即重复发送"
            )
            return False
        handle = future.result()
        if handle is None:
            self.get_logger().error("❌ 目标响应不可用")
            return False
        if not handle.accepted:
            self.get_logger().error("❌ 目标被拒绝")
            return False
        self.get_logger().info("✅ 目标已接受，导航中...")
        result_future = handle.get_result_async()
        rclpy.spin_until_future_complete(
            self, result_future, timeout_sec=self.result_timeout
        )
        if not result_future.done():
            self.get_logger().error("❌ 导航超时，正在请求取消目标")
            try:
                cancel_future = handle.cancel_goal_async()
                rclpy.spin_until_future_complete(
                    self, cancel_future, timeout_sec=CANCEL_RESPONSE_TIMEOUT
                )
                if not cancel_future.done():
                    self.get_logger().error("❌ 取消请求响应超时，请检查 Nav2 状态")
                    return False
                cancel_response = cancel_future.result()
            except ExternalShutdownException:
                raise
            except Exception as exc:
                self.get_logger().error(f"❌ 取消请求失败: {exc}")
                return False

            goal_is_canceling = cancel_response is not None and any(
                goal_info.goal_id == handle.goal_id
                for goal_info in cancel_response.goals_canceling
            )
            if (
                cancel_response is None
                or cancel_response.return_code != CancelGoal.Response.ERROR_NONE
                or not goal_is_canceling
            ):
                return_code = getattr(cancel_response, "return_code", "unavailable")
                self.get_logger().error(
                    f"❌ 取消请求未被接受（返回码: {return_code}），"
                    "目标可能仍在执行，请检查 Nav2 状态"
                )
                return False
            self.get_logger().info("✅ 取消请求已接受")
            return False
        result = result_future.result()
        if result is None:
            self.get_logger().error("❌ 导航结果不可用")
            return False
        if result.status != GoalStatus.STATUS_SUCCEEDED:
            self.get_logger().error(f"❌ 导航未成功，状态码: {result.status}")
            return False
        self.get_logger().info(f"✅ 导航成功，状态码: {result.status}")
        return True

    def on_feedback(self, feedback_msg):
        fb = feedback_msg.feedback
        self.get_logger().info(
            f"  剩余距离: {fb.distance_remaining:.2f}m", throttle_duration_sec=2.0
        )


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--x", type=finite_float, required=True)
    parser.add_argument("--y", type=finite_float, required=True)
    parser.add_argument("--yaw", type=finite_float, default=0.0)
    parser.add_argument(
        "--goal-response-timeout",
        type=positive_finite_float,
        default=DEFAULT_GOAL_RESPONSE_TIMEOUT,
        help="等待 Nav2 接受或拒绝目标的秒数（默认: 10）",
    )
    parser.add_argument(
        "--result-timeout",
        type=positive_finite_float,
        default=DEFAULT_RESULT_TIMEOUT,
        help="等待导航结果的秒数，超时后请求取消（默认: 300）",
    )
    args = parser.parse_args(argv)

    if not configure_rmw_implementation():
        return 2

    initialized = False
    node = None
    try:
        rclpy.init()
        initialized = True
        node = GoalSender(
            args.x,
            args.y,
            args.yaw,
            args.goal_response_timeout,
            args.result_timeout,
        )
        return 0 if node.send() else 2
    except (KeyboardInterrupt, ExternalShutdownException):
        return 130
    finally:
        try:
            if node is not None:
                node.destroy_node()
        finally:
            if initialized:
                rclpy.try_shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
