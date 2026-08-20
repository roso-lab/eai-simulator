# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause

"""
ROS2 Odometry 订阅桥接脚本：在外部 Python 进程中订阅 ROS2 /isaac/odometry 消息，并通过文件 IPC 传递给 Isaac Sim

这个脚本使用 rclpy 直接订阅 ROS2 odometry 话题，消息通过 JSON 文件传递给 Isaac Sim 脚本。

使用方法:
    # 在系统 Python 环境中运行（退出 conda 环境）
    python algorithm/ros/bridges/ros2_odometry_bridge.py --topic=/isaac/odometry --output_file=./ros2_odometry.json
"""

import argparse
import json
import os
import time
import signal
import sys
from pathlib import Path

# 检查是否在 conda 环境中
def check_conda_env():
    """检查是否在 conda 环境中运行。"""
    conda_env = os.environ.get("CONDA_DEFAULT_ENV")
    if conda_env:
        return True, conda_env
    return False, None

# 检查 conda 环境
in_conda, conda_env_name = check_conda_env()
if in_conda:
    print(f"⚠️  警告: 检测到 conda 环境: {conda_env_name}")
    print(f"   此脚本需要在系统 Python 环境中运行（退出 conda 环境）")
    print(f"   请运行: conda deactivate")
    print(f"   然后重新运行此脚本")
    sys.exit(1)

try:
    import rclpy
    from rclpy.node import Node
    from rclpy.executors import SingleThreadedExecutor
    from nav_msgs.msg import Odometry
except ImportError as e:
    error_msg = str(e)
    ros_distro = os.environ.get("ROS_DISTRO", "humble")
    print(f"❌ 错误: 无法导入 rclpy 或 nav_msgs")
    print(f"   错误详情: {error_msg}")
    print(f"\n   解决方案:")
    print(f"   1. 确保已退出 conda 环境:")
    print(f"      conda deactivate")
    print(f"   2. 确保已 source ROS2 setup.bash:")
    print(f"      source /opt/ros/{ros_distro}/setup.bash")
    print(f"   3. 然后重新运行此脚本")
    print(f"\n   当前 Python 版本: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    sys.exit(1)


class OdometrySubscriber(Node):
    """ROS2 Odometry 订阅者节点。"""
    
    def __init__(self, topic_name: str, output_file: str):
        """
        初始化订阅者。
        
        Args:
            topic_name: 订阅的 ROS2 话题名称
            output_file: 输出文件路径（JSON 格式）
        """
        super().__init__('odometry_subscriber')
        self.topic_name = topic_name
        self.output_file = output_file
        self.last_message = None
        self.message_count = 0  # 消息计数器
        
        # 创建订阅者
        self.subscription = self.create_subscription(
            Odometry,
            topic_name,
            self.listener_callback,
            10
        )
        self.subscription  # 防止未使用警告
        
        print(f"[ROS2OdometryBridge] ✅ 订阅话题: {topic_name}")
        print(f"[ROS2OdometryBridge] ✅ 输出文件: {output_file}")
    
    def listener_callback(self, msg: Odometry):
        """
        接收到消息时的回调函数。
        
        Args:
            msg: nav_msgs/Odometry 消息
        """
        # 增加消息计数
        self.message_count += 1
        
        # 提取位置和姿态
        position = msg.pose.pose.position
        orientation = msg.pose.pose.orientation
        
        data = {
            "position": {
                "x": float(position.x),
                "y": float(position.y),
                "z": float(position.z),
            },
            "orientation": {
                "x": float(orientation.x),
                "y": float(orientation.y),
                "z": float(orientation.z),
                "w": float(orientation.w),
            },
            "timestamp": time.time(),
        }
        
        self.last_message = data
        
        # 每10条消息打印一次，避免输出过多
        if self.message_count % 10 == 1:
            self.get_logger().info(
                f"收到消息 #{self.message_count}: x={data['position']['x']:.3f}, y={data['position']['y']:.3f}, z={data['position']['z']:.3f}"
            )
        
        # 写入文件
        try:
            # 确保输出目录存在
            output_path = Path(self.output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            # 使用临时文件 + 原子重命名，避免读取时文件不完整
            temp_file = f"{self.output_file}.tmp"
            with open(temp_file, 'w') as f:
                json.dump(data, f, ensure_ascii=False)
                f.flush()  # 确保数据写入缓冲区
                os.fsync(f.fileno())  # 强制同步到磁盘
            
            # 原子重命名
            os.replace(temp_file, self.output_file)
            
        except Exception as e:
            self.get_logger().error(f"Failed to write to file '{self.output_file}': {e}")
            import traceback
            self.get_logger().error(traceback.format_exc())


def main():
    """主函数。"""
    parser = argparse.ArgumentParser(
        description="ROS2 Odometry 订阅桥接脚本：订阅 ROS2 /isaac/odometry 消息并通过文件 IPC 传递",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 在系统 Python 环境中运行（退出 conda 环境）
  python algorithm/ros/bridges/ros2_odometry_bridge.py --topic=/isaac/odometry --output_file=./ros2_odometry.json
        """
    )
    parser.add_argument(
        "--topic",
        type=str,
        default="/isaac/odometry",
        help="ROS2 话题名称 (默认: /isaac/odometry)"
    )
    parser.add_argument(
        "--output_file",
        type=str,
        default="./ros2_odometry.json",
        help="输出文件路径 (JSON 格式，默认: ./ros2_odometry.json)"
    )
    
    args = parser.parse_args()
    
    # 确保输出目录存在
    output_path = Path(args.output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    print(f"\n{'='*70}")
    print(f"🚀 启动 ROS2 Odometry 订阅桥接")
    print(f"{'='*70}")
    print(f"话题: {args.topic}")
    print(f"输出文件: {args.output_file}")
    print(f"{'='*70}\n")
    
    # 初始化 ROS2
    rclpy.init()
    
    # 创建订阅者节点
    subscriber = OdometrySubscriber(args.topic, args.output_file)
    
    # 创建 executor
    executor = SingleThreadedExecutor()
    executor.add_node(subscriber)
    
    # 主循环：spin 节点
    print("[ROS2OdometryBridge] ✅ 订阅者已启动，等待消息...")
    print("[ROS2OdometryBridge] 按 Ctrl+C 退出\n")
    
    try:
        # 使用 executor 的 spin_once 配合超时，这样可以响应 KeyboardInterrupt
        while rclpy.ok():
            executor.spin_once(timeout_sec=0.1)
    except KeyboardInterrupt:
        print("\n[ROS2OdometryBridge] 收到键盘中断，正在关闭...")
    except Exception as e:
        print(f"\n[ROS2OdometryBridge] 发生错误: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("[ROS2OdometryBridge] 正在清理资源...")
        try:
            executor.shutdown()
        except Exception:
            pass
        try:
            subscriber.destroy_node()
        except Exception:
            pass
        try:
            rclpy.shutdown()
        except Exception:
            pass
        print("[ROS2OdometryBridge] ✅ 已关闭")


if __name__ == "__main__":
    main()
