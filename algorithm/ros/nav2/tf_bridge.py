#!/usr/bin/env python3
"""
EAI 仿真器 → Nav2 TF 桥接节点

GSHub 只发布 odometry（frame_id=mapping_init, child=base_link）和点云/相机，
但**不发布任何 TF**，且所有传感器 frame_id 都写死成 "mapping_init"。
Nav2 遵循 REP-105，需要完整 TF 链：map → odom → base_link → <sensor>。

本节点负责补齐（在系统 ROS2 环境运行，与仿真器并行）：
  1. 订阅 /<robot>/odometry，广播动态 TF: odom → base_link
     （把 mapping_init 语义重命名为标准的 odom 帧）
  2. 广播静态 TF: base_link → lidar_link（雷达安装偏移和下倾姿态）
  3. 重发布点云到 /<robot>/scan_cloud，frame_id 改写为 lidar_link
     （GSHub 点云数值是 Mid360 传感器坐标；pointcloud_to_laserscan 再按 TF 转到水平 base_link）
  4. map → odom 由 AMCL 提供（本节点不发），避免与 AMCL 冲突。

用法：
    source /opt/ros/humble/setup.bash
    /usr/bin/python3 algorithm/ros/nav2/tf_bridge.py --ros-args -p robot:=carter_1
    /usr/bin/python3 algorithm/ros/nav2/tf_bridge.py --robot carter_1 --lidar-xyz 0.026,0.0,0.418 --lidar-rpy 0.0,0.339,0.0
"""

from __future__ import annotations

import os
import sys
import math
from typing import Sequence


SYSTEM_ROS_PYTHON = "/usr/bin/python3"


def _flag_value(arg: str, argv: Sequence[str], index: int) -> tuple[str, int]:
    if "=" in arg:
        return arg.split("=", 1)[1], index + 1
    next_index = index + 1
    if next_index >= len(argv):
        raise SystemExit(f"{arg} 需要一个参数值")
    return argv[next_index], index + 2


def _format_lidar_xyz(value: str) -> str:
    text = value.strip().strip("[]")
    try:
        parts = [float(part.strip()) for part in text.split(",")]
    except ValueError as exc:
        raise SystemExit("--lidar-xyz 需要格式 x,y,z，例如 0.026,0.0,0.418") from exc
    if len(parts) != 3:
        raise SystemExit("--lidar-xyz 需要 3 个逗号分隔的数字")
    return f"[{parts[0]}, {parts[1]}, {parts[2]}]"


def _format_rpy(value: str) -> str:
    text = value.strip().strip("[]")
    try:
        parts = [float(part.strip()) for part in text.split(",")]
    except ValueError as exc:
        raise SystemExit("--lidar-rpy 需要格式 roll,pitch,yaw，例如 0.0,0.339,0.0") from exc
    if len(parts) != 3:
        raise SystemExit("--lidar-rpy 需要 3 个逗号分隔的数字")
    return f"[{parts[0]}, {parts[1]}, {parts[2]}]"


def quaternion_from_rpy(roll: float, pitch: float, yaw: float) -> tuple[float, float, float, float]:
    cr = math.cos(roll * 0.5)
    sr = math.sin(roll * 0.5)
    cp = math.cos(pitch * 0.5)
    sp = math.sin(pitch * 0.5)
    cy = math.cos(yaw * 0.5)
    sy = math.sin(yaw * 0.5)
    return (
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
        cr * cp * cy + sr * sp * sy,
    )


def normalize_cli_args(argv: Sequence[str]) -> list[str]:
    """Accept convenient flags and translate them to ROS parameter arguments."""
    args = list(argv)
    if "--ros-args" in args:
        return args

    param_flags = {
        "--robot": "robot",
        "--odom-frame": "odom_frame",
        "--base-frame": "base_frame",
        "--lidar-frame": "lidar_frame",
        "--lidar-xyz": "lidar_xyz",
        "--lidar-rpy": "lidar_rpy",
    }
    passthrough: list[str] = []
    ros_params: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        flag = arg.split("=", 1)[0]
        param = param_flags.get(flag)
        if param is None:
            passthrough.append(arg)
            i += 1
            continue

        value, i = _flag_value(arg, args, i)
        if param == "lidar_xyz":
            value = _format_lidar_xyz(value)
        elif param == "lidar_rpy":
            value = _format_rpy(value)
        ros_params.extend(["-p", f"{param}:={value}"])

    if not ros_params:
        return args
    return passthrough + ["--ros-args"] + ros_params


def _same_executable(left: str, right: str) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return os.path.realpath(left) == os.path.realpath(right)


def _should_reexec_system_python() -> bool:
    if os.environ.get("EAI_NAV2_NO_REEXEC") == "1":
        return False
    target = os.environ.get("EAI_NAV2_ROS_PYTHON", SYSTEM_ROS_PYTHON)
    if not os.path.exists(target) or _same_executable(sys.executable, target):
        return False
    in_conda = bool(os.environ.get("CONDA_PREFIX")) or "conda" in sys.executable
    wrong_ros_abi = sys.version_info[:2] != (3, 10)
    return in_conda or wrong_ros_abi


def _reexec_system_python_if_needed() -> None:
    if not _should_reexec_system_python():
        return
    target = os.environ.get("EAI_NAV2_ROS_PYTHON", SYSTEM_ROS_PYTHON)
    os.execv(target, [target, os.path.abspath(__file__), *sys.argv[1:]])


def _load_ros_symbols():
    try:
        import rclpy
        from geometry_msgs.msg import TransformStamped
        from nav_msgs.msg import Odometry
        from rclpy.executors import ExternalShutdownException
        from rclpy.node import Node
        from rclpy.parameter import Parameter
        from rclpy.qos import qos_profile_sensor_data
        from sensor_msgs.msg import PointCloud2
        from tf2_ros import StaticTransformBroadcaster, TransformBroadcaster
    except Exception as exc:
        raise SystemExit(
            "无法导入 ROS2 rclpy。请先执行：\n"
            "  conda deactivate\n"
            "  source /opt/ros/humble/setup.bash\n"
            "  /usr/bin/python3 algorithm/ros/nav2/tf_bridge.py "
            "--robot carter_1 --lidar-xyz 0.026,0.0,0.418\n"
            f"原始错误: {exc}"
        ) from exc

    return {
        "rclpy": rclpy,
        "Node": Node,
        "Parameter": Parameter,
        "qos_profile_sensor_data": qos_profile_sensor_data,
        "Odometry": Odometry,
        "PointCloud2": PointCloud2,
        "TransformStamped": TransformStamped,
        "TransformBroadcaster": TransformBroadcaster,
        "StaticTransformBroadcaster": StaticTransformBroadcaster,
        "ExternalShutdownException": ExternalShutdownException,
    }


def make_nav2_tf_bridge_class(ros):
    rclpy = ros["rclpy"]
    Node = ros["Node"]
    Parameter = ros["Parameter"]
    qos_profile_sensor_data = ros["qos_profile_sensor_data"]
    Odometry = ros["Odometry"]
    PointCloud2 = ros["PointCloud2"]
    TransformStamped = ros["TransformStamped"]
    TransformBroadcaster = ros["TransformBroadcaster"]
    StaticTransformBroadcaster = ros["StaticTransformBroadcaster"]

    class Nav2TfBridge(Node):
        def __init__(self):
            super().__init__("eai_nav2_tf_bridge")

            # 参数
            self.declare_parameter("robot", "carter_1")
            self.declare_parameter("odom_frame", "odom")
            self.declare_parameter("base_frame", "base_link")
            self.declare_parameter("lidar_frame", "lidar_link")
            # 雷达相对底盘的安装偏移（Carter GSHub init_state.pos = 0.026, 0, 0.418）
            self.declare_parameter("lidar_xyz", [0.026, 0.0, 0.418])
            # 雷达相对底盘的安装姿态。Carter GSHub 前向 Mid360 约下倾 19.4°。
            self.declare_parameter("lidar_rpy", [0.0, 0.339, 0.0])

            # 使用仿真时间（关键）：与 /clock 对齐，否则 TF 时间戳与传感器不匹配，
            # costmap 会报 "timestamp earlier than transform cache" 并丢弃扫描
            if not self.has_parameter("use_sim_time"):
                self.declare_parameter("use_sim_time", True)
            self.set_parameters([Parameter("use_sim_time", value=True)])

            self.robot = self.get_parameter("robot").value
            self.odom_frame = self.get_parameter("odom_frame").value
            self.base_frame = self.get_parameter("base_frame").value
            self.lidar_frame = self.get_parameter("lidar_frame").value
            self.lidar_xyz = list(self.get_parameter("lidar_xyz").value)
            self.lidar_rpy = list(self.get_parameter("lidar_rpy").value)

            # TF 广播器
            self.tf_broadcaster = TransformBroadcaster(self)
            self.static_broadcaster = StaticTransformBroadcaster(self)
            self._publish_static_tf()

            # 订阅仿真里程计 → 广播 odom→base_link
            odom_topic = f"/{self.robot}/odometry"
            self.odom_sub = self.create_subscription(
                Odometry, odom_topic, self.on_odom, qos_profile_sensor_data
            )

            # 点云重发布（GSHub/Mid360 点云数值是传感器坐标，frame_id 改成 lidar_link）
            cloud_in = f"/{self.robot}/cloud"
            cloud_out = f"/{self.robot}/scan_cloud"
            self.cloud_pub = self.create_publisher(PointCloud2, cloud_out, qos_profile_sensor_data)
            self.cloud_sub = self.create_subscription(
                PointCloud2, cloud_in, self.on_cloud, qos_profile_sensor_data
            )

            self.get_logger().info(
                f"TF Bridge 启动: robot={self.robot}\n"
                f"  订阅里程计: {odom_topic} → 广播 TF {self.odom_frame}→{self.base_frame}\n"
                f"  静态 TF: {self.base_frame}→{self.lidar_frame} @ xyz={self.lidar_xyz}, rpy={self.lidar_rpy}\n"
                f"  点云重发布: {cloud_in} → {cloud_out} (frame_id={self.lidar_frame})"
            )

        def _publish_static_tf(self):
            """base_link → lidar_link 静态变换"""
            t = TransformStamped()
            t.header.stamp = self.get_clock().now().to_msg()
            t.header.frame_id = self.base_frame
            t.child_frame_id = self.lidar_frame
            t.transform.translation.x = float(self.lidar_xyz[0])
            t.transform.translation.y = float(self.lidar_xyz[1])
            t.transform.translation.z = float(self.lidar_xyz[2])
            qx, qy, qz, qw = quaternion_from_rpy(
                float(self.lidar_rpy[0]),
                float(self.lidar_rpy[1]),
                float(self.lidar_rpy[2]),
            )
            t.transform.rotation.x = qx
            t.transform.rotation.y = qy
            t.transform.rotation.z = qz
            t.transform.rotation.w = qw
            self.static_broadcaster.sendTransform(t)

        def on_odom(self, msg):
            """把 odometry 的位姿广播为 odom→base_link TF"""
            t = TransformStamped()
            t.header.stamp = msg.header.stamp
            t.header.frame_id = self.odom_frame
            t.child_frame_id = self.base_frame
            t.transform.translation.x = msg.pose.pose.position.x
            t.transform.translation.y = msg.pose.pose.position.y
            t.transform.translation.z = msg.pose.pose.position.z
            t.transform.rotation = msg.pose.pose.orientation
            self.tf_broadcaster.sendTransform(t)

        def on_cloud(self, msg):
            """重发布点云，frame_id 改写为 lidar_link。"""
            msg.header.frame_id = self.lidar_frame
            self.cloud_pub.publish(msg)

    return Nav2TfBridge


def main(argv=None):
    _reexec_system_python_if_needed()
    ros_args = normalize_cli_args(sys.argv[1:] if argv is None else argv)
    ros = _load_ros_symbols()
    rclpy = ros["rclpy"]
    Nav2TfBridge = make_nav2_tf_bridge_class(ros)

    rclpy.init(args=ros_args)
    node = Nav2TfBridge()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ros["ExternalShutdownException"]):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
