#!/usr/bin/env python3
import argparse
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image, PointCloud2
from cv_bridge import CvBridge
import cv2
import numpy as np
import struct
import math


def normalize_namespace(namespace):
    text = str(namespace or "").strip().strip("/")
    if not text:
        return ""
    return "/" + "/".join(part for part in text.split("/") if part)


def sensor_topics_for_namespace(namespace, sensor="gshub"):
    prefix = normalize_namespace(namespace)
    if sensor == "lidar":
        return (f"{prefix}/cloud",)
    return (
        f"{prefix}/GS_Hub_L_cam",
        f"{prefix}/GS_Hub_R_cam",
        f"{prefix}/cloud",
    )


def parse_args(args=None):
    parser = argparse.ArgumentParser(description="Visualize ROS2 camera and point cloud sensor topics.")
    parser.add_argument(
        "--sensor",
        default="gshub",
        choices=("gshub", "lidar"),
        help="Sensor topic set to visualize. gshub subscribes cameras and cloud; lidar subscribes cloud only.",
    )
    parser.add_argument(
        "--namespace",
        default="/isaac",
        help="ROS2 namespace for one GSHub instance, e.g. /isaac, /go2_1, /b2_1, /m20_1.",
    )
    parsed_args, ros_args = parser.parse_known_args(args)
    parsed_args.ros_args = ros_args
    return parsed_args


class SensorVisualizer(Node):
    def __init__(self, namespace="/isaac", sensor="gshub"):
        super().__init__('isaac_sensor_vis')
        self.bridge = CvBridge()

        # --- 配置 ---
        self.sensor = sensor
        self.namespace = normalize_namespace(namespace)
        topics = sensor_topics_for_namespace(self.namespace, sensor=self.sensor)
        if self.sensor == "lidar":
            self.topic_cam_l = None
            self.topic_cam_r = None
            self.topic_cloud = topics[0]
        else:
            self.topic_cam_l, self.topic_cam_r, self.topic_cloud = topics

        # 视图配置
        self.lidar_view_range = 10.0  # 显示半径 10米
        self.window_size = 600  # 🔥 修改点：统一所有窗口大小为 600

        # --- 订阅者 ---
        self.sub_l = None
        self.sub_r = None
        if self.sensor == "gshub":
            self.sub_l = self.create_subscription(
                Image,
                self.topic_cam_l,
                self.cb_cam_l,
                qos_profile_sensor_data
            )

            self.sub_r = self.create_subscription(
                Image,
                self.topic_cam_r,
                self.cb_cam_r,
                qos_profile_sensor_data
            )

        self.sub_cloud = self.create_subscription(
            PointCloud2,
            self.topic_cloud,
            self.cb_cloud,
            qos_profile_sensor_data
        )

        if self.sensor == "gshub":
            print(f"Waiting for images on {self.topic_cam_l} & {self.topic_cam_r}...")
        print(f"Waiting for pointcloud on {self.topic_cloud}...")

    def cb_cam_l(self, msg):
        self.show_image(msg, "Left Camera")

    def cb_cam_r(self, msg):
        self.show_image(msg, "Right Camera")

    def show_image(self, msg, window_name):
        try:
            # ROS Image -> OpenCV Image
            if msg.encoding == "rgb8":
                cv_img = self.bridge.imgmsg_to_cv2(msg, "rgb8")
                cv_img = cv2.cvtColor(cv_img, cv2.COLOR_RGB2BGR)
            else:
                cv_img = self.bridge.imgmsg_to_cv2(msg, "bgr8")

            # 🔥 修改点：强制调整图像大小为 (600, 600)
            # 注意：这可能会改变图像的长宽比，如果你介意拉伸，可以只指定宽度
            cv_img = cv2.resize(cv_img, (self.window_size, self.window_size))

            # 显示
            cv2.imshow(window_name, cv_img)
            cv2.waitKey(1)
        except Exception as e:
            self.get_logger().error(f"Cam Error: {e}")

    def cb_cloud(self, msg):
        """
        简单的点云转图像可视化 (俯视图)
        """
        try:
            # 1. 解析 PointCloud2
            offset_x = -1
            offset_y = -1
            for field in msg.fields:
                if field.name == 'x': offset_x = field.offset
                if field.name == 'y': offset_y = field.offset

            if offset_x == -1 or offset_y == -1:
                return

            points_data = np.frombuffer(msg.data, dtype=np.float32)
            step = msg.point_step // 4
            if step < 3: return

            points = points_data.reshape(-1, step)
            x = points[:, 0]
            y = points[:, 1]

            mask = np.isfinite(x) & np.isfinite(y)
            x = x[mask]
            y = y[mask]

            # 2. 绘制俯视图
            # 🔥 修改点：使用统一的 self.window_size
            img = np.zeros((self.window_size, self.window_size, 3), dtype=np.uint8)

            scale = (self.window_size / 2) / self.lidar_view_range

            u = (self.window_size / 2 - y * scale).astype(np.int32)
            v = (self.window_size / 2 - x * scale).astype(np.int32)

            valid_idx = (u >= 0) & (u < self.window_size) & (v >= 0) & (v < self.window_size)
            u = u[valid_idx]
            v = v[valid_idx]

            img[v, u] = (255, 255, 255)

            c = self.window_size // 2
            cv2.circle(img, (c, c), 5, (0, 0, 255), -1)
            cv2.arrowedLine(img, (c, c), (c, c - 30), (0, 255, 0), 2)

            cv2.imshow("Lidar BEV", img)
            cv2.waitKey(1)

        except Exception as e:
            pass


def main(args=None):
    parsed_args = parse_args(args)
    rclpy.init(args=parsed_args.ros_args)
    node = SensorVisualizer(namespace=parsed_args.namespace, sensor=parsed_args.sensor)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()
    cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
