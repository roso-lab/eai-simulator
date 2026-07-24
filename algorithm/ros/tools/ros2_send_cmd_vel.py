#!/usr/bin/env python3
"""
ROS2 端测试脚本 - 发送 cmd_vel 命令到仿真器

使用方法：
    # 前进
    python algorithm/ros/tools/ros2_send_cmd_vel.py --robot carter_1 --linear 0.5

    # 左转
    python algorithm/ros/tools/ros2_send_cmd_vel.py --robot carter_1 --angular 0.5

    # 前进 + 左转
    python algorithm/ros/tools/ros2_send_cmd_vel.py --robot carter_1 --linear 0.5 --angular 0.3

    # 持续发布（10Hz）
    python algorithm/ros/tools/ros2_send_cmd_vel.py --robot carter_1 --linear 0.5 --rate 10
"""

import argparse
import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist


class CmdVelPublisher(Node):
    def __init__(self, robot_name, linear_x, angular_z, rate_hz):
        super().__init__('cmd_vel_test_publisher')

        topic = f'/{robot_name}/cmd_vel'
        self.publisher = self.create_publisher(Twist, topic, 10)

        self.linear_x = linear_x
        self.angular_z = angular_z

        if rate_hz > 0:
            # 持续发布模式
            self.timer = self.create_timer(1.0 / rate_hz, self.timer_callback)
            self.get_logger().info(f'持续发布到 {topic} ({rate_hz} Hz)')
            self.get_logger().info(f'linear.x={linear_x:.2f}, angular.z={angular_z:.2f}')
            self.get_logger().info('按 Ctrl+C 停止')
        else:
            # 单次发布模式
            self.timer_callback()
            self.get_logger().info(f'单次发布到 {topic}')
            self.get_logger().info(f'linear.x={linear_x:.2f}, angular.z={angular_z:.2f}')

    def timer_callback(self):
        msg = Twist()
        msg.linear.x = self.linear_x
        msg.linear.y = 0.0
        msg.linear.z = 0.0
        msg.angular.x = 0.0
        msg.angular.y = 0.0
        msg.angular.z = self.angular_z
        self.publisher.publish(msg)


def main():
    parser = argparse.ArgumentParser(description='发送 cmd_vel 命令到仿真器')
    parser.add_argument(
        '--robot',
        type=str,
        default='carter_1',
        help='机器人名称（默认: carter_1）',
    )
    parser.add_argument(
        '--linear',
        type=float,
        default=0.0,
        help='线速度 linear.x (m/s, 默认: 0.0)',
    )
    parser.add_argument(
        '--angular',
        type=float,
        default=0.0,
        help='角速度 angular.z (rad/s, 默认: 0.0)',
    )
    parser.add_argument(
        '--rate',
        type=float,
        default=0.0,
        help='发布频率 (Hz, 0=单次发布, 默认: 0)',
    )

    args = parser.parse_args()

    rclpy.init()

    publisher = CmdVelPublisher(
        args.robot,
        args.linear,
        args.angular,
        args.rate,
    )

    try:
        if args.rate > 0:
            rclpy.spin(publisher)
        else:
            # 单次发布，等待一下确保消息发送
            import time
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        publisher.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
