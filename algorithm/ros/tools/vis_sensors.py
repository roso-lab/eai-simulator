#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections.abc import Iterable, Sequence
from typing import Any

import cv2
import numpy as np
import rclpy
from cv_bridge import CvBridge
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image, PointCloud2


ROS_IMAGE_TYPES = frozenset({"sensor_msgs/msg/Image", "sensor_msgs/Image"})


def normalize_namespace(namespace: str | None) -> str:
    text = str(namespace or "").strip().strip("/")
    if not text:
        return ""
    return "/" + "/".join(part for part in text.split("/") if part)


def topic_is_in_namespace(topic: str, namespace: str | None) -> bool:
    prefix = normalize_namespace(namespace)
    normalized_topic = "/" + str(topic).strip().strip("/")
    return not prefix or normalized_topic == prefix or normalized_topic.startswith(f"{prefix}/")


def discover_image_topics(
    topics_and_types: Iterable[tuple[str, Sequence[str]]],
    namespace: str | None = None,
) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                "/" + topic.strip().strip("/")
                for topic, topic_types in topics_and_types
                if ROS_IMAGE_TYPES.intersection(topic_types) and topic_is_in_namespace(topic, namespace)
            }
        )
    )


def sensor_topics_for_namespace(namespace: str | None, sensor: str = "gshub") -> tuple[str, ...]:
    prefix = normalize_namespace(namespace)
    if sensor == "lidar":
        return (f"{prefix}/cloud",)
    if sensor == "camera":
        return (f"{prefix}/camera/image_raw",)
    return (
        f"{prefix}/GS_Hub_L_cam",
        f"{prefix}/GS_Hub_R_cam",
        f"{prefix}/cloud",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Visualize EAI ROS2 camera and point-cloud topics. Without arguments, "
            "all sensor_msgs/msg/Image topics are discovered automatically."
        )
    )
    parser.add_argument(
        "--sensor",
        default="auto",
        choices=("auto", "camera", "gshub", "lidar"),
        help=(
            "auto discovers every Image topic; camera discovers Image topics below --namespace; "
            "gshub subscribes its stereo cameras and cloud; lidar subscribes cloud only."
        ),
    )
    parser.add_argument(
        "--namespace",
        default=None,
        help=(
            "Optional robot namespace, such as /iris_1 or /carter_1. Explicit gshub/lidar mode "
            "defaults to the legacy /isaac namespace."
        ),
    )
    parser.add_argument(
        "--window-size",
        type=int,
        default=600,
        help="Maximum image edge and point-cloud window size in pixels (default: 600).",
    )
    parser.add_argument(
        "--discovery-interval",
        type=float,
        default=1.0,
        help="Seconds between ROS graph scans in auto/camera mode (default: 1.0).",
    )
    return parser


def parse_args(args: Sequence[str] | None = None) -> argparse.Namespace:
    parsed_args, ros_args = build_parser().parse_known_args(args)
    if parsed_args.window_size <= 0:
        raise SystemExit("--window-size must be greater than zero")
    if parsed_args.discovery_interval <= 0.0:
        raise SystemExit("--discovery-interval must be greater than zero")
    if parsed_args.namespace is None:
        parsed_args.namespace = "/isaac" if parsed_args.sensor in {"gshub", "lidar"} else ""
    parsed_args.ros_args = ros_args
    return parsed_args


def resize_to_fit(image: np.ndarray, maximum_edge: int) -> np.ndarray:
    height, width = image.shape[:2]
    if height <= 0 or width <= 0:
        raise ValueError("Cannot display an empty image")
    scale = float(maximum_edge) / float(max(height, width))
    target = (max(1, round(width * scale)), max(1, round(height * scale)))
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    return cv2.resize(image, target, interpolation=interpolation)


class SensorVisualizer(Node):
    def __init__(
        self,
        namespace: str = "",
        sensor: str = "auto",
        window_size: int = 600,
        discovery_interval: float = 1.0,
    ) -> None:
        super().__init__("eai_sensor_visualizer")
        self.bridge = CvBridge()
        self.sensor = sensor
        self.namespace = normalize_namespace(namespace)
        self.lidar_view_range = 10.0
        self.window_size = int(window_size)
        self.image_subscriptions: dict[str, Any] = {}
        self.image_windows: dict[str, str] = {}
        self.cloud_subscription = None
        self.discovery_timer = None

        if self.sensor == "gshub":
            topic_left, topic_right, topic_cloud = sensor_topics_for_namespace(self.namespace, "gshub")
            self._subscribe_image(topic_left, f"GS-Hub Left: {topic_left}")
            self._subscribe_image(topic_right, f"GS-Hub Right: {topic_right}")
            self._subscribe_cloud(topic_cloud)
        elif self.sensor == "lidar":
            self._subscribe_cloud(sensor_topics_for_namespace(self.namespace, "lidar")[0])
        else:
            self._discover_images()
            self.discovery_timer = self.create_timer(float(discovery_interval), self._discover_images)
            scope = f" below {self.namespace}" if self.namespace else ""
            print(f"Discovering all sensor_msgs/msg/Image topics{scope}...")

    def _subscribe_image(self, topic: str, window_name: str | None = None) -> None:
        if topic in self.image_subscriptions:
            return
        title = window_name or f"Camera: {topic}"

        def callback(message: Image, *, source_topic: str = topic) -> None:
            self.show_image(message, self.image_windows[source_topic])

        self.image_windows[topic] = title
        self.image_subscriptions[topic] = self.create_subscription(
            Image,
            topic,
            callback,
            qos_profile_sensor_data,
        )
        print(f"Subscribed to image: {topic}")

    def _subscribe_cloud(self, topic: str) -> None:
        if self.cloud_subscription is not None:
            return
        self.cloud_subscription = self.create_subscription(
            PointCloud2,
            topic,
            self.cb_cloud,
            qos_profile_sensor_data,
        )
        print(f"Subscribed to pointcloud: {topic}")

    def _discover_images(self) -> None:
        try:
            topics = discover_image_topics(self.get_topic_names_and_types(), self.namespace)
        except Exception as exc:
            self.get_logger().warning(f"Failed to inspect ROS topics: {exc}")
            return
        for topic in topics:
            self._subscribe_image(topic)

    def _message_to_bgr(self, message: Image) -> np.ndarray:
        try:
            return self.bridge.imgmsg_to_cv2(message, desired_encoding="bgr8")
        except Exception:
            image = np.asarray(self.bridge.imgmsg_to_cv2(message, desired_encoding="passthrough"))
            if image.dtype != np.uint8:
                image = cv2.normalize(image, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
            if image.ndim == 2:
                return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
            if image.ndim != 3:
                raise ValueError(f"Unsupported image shape: {image.shape}")
            if image.shape[2] == 4:
                code = cv2.COLOR_RGBA2BGR if message.encoding.lower().startswith("rgba") else cv2.COLOR_BGRA2BGR
                return cv2.cvtColor(image, code)
            if image.shape[2] == 3 and message.encoding.lower().startswith("rgb"):
                return cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
            if image.shape[2] == 3:
                return image
            raise ValueError(f"Unsupported image shape: {image.shape}")

    def show_image(self, message: Image, window_name: str) -> None:
        try:
            image = resize_to_fit(self._message_to_bgr(message), self.window_size)
            cv2.imshow(window_name, image)
            cv2.waitKey(1)
        except Exception as exc:
            self.get_logger().error(f"Failed to display {window_name}: {exc}")

    def cb_cloud(self, message: PointCloud2) -> None:
        try:
            offsets = {field.name: field.offset for field in message.fields}
            if "x" not in offsets or "y" not in offsets or message.point_step < 4:
                return
            count = len(message.data) // message.point_step
            points = np.ndarray(
                shape=(count,),
                dtype=np.dtype(
                    {
                        "names": ("x", "y"),
                        "formats": ("<f4", "<f4"),
                        "offsets": (offsets["x"], offsets["y"]),
                        "itemsize": message.point_step,
                    }
                ),
                buffer=message.data,
            )
            x = points["x"]
            y = points["y"]
            finite = np.isfinite(x) & np.isfinite(y)
            x = x[finite]
            y = y[finite]

            image = np.zeros((self.window_size, self.window_size, 3), dtype=np.uint8)
            scale = (self.window_size / 2.0) / self.lidar_view_range
            u = (self.window_size / 2.0 - y * scale).astype(np.int32)
            v = (self.window_size / 2.0 - x * scale).astype(np.int32)
            visible = (u >= 0) & (u < self.window_size) & (v >= 0) & (v < self.window_size)
            image[v[visible], u[visible]] = (255, 255, 255)

            center = self.window_size // 2
            cv2.circle(image, (center, center), 5, (0, 0, 255), -1)
            cv2.arrowedLine(image, (center, center), (center, center - 30), (0, 255, 0), 2)
            cv2.imshow(f"Lidar BEV: {self.namespace or '/'}", image)
            cv2.waitKey(1)
        except Exception as exc:
            self.get_logger().error(f"Failed to display pointcloud: {exc}")


def main(args: Sequence[str] | None = None) -> int:
    parsed_args = parse_args(args)
    rclpy.init(args=parsed_args.ros_args)
    node = SensorVisualizer(
        namespace=parsed_args.namespace,
        sensor=parsed_args.sensor,
        window_size=parsed_args.window_size,
        discovery_interval=parsed_args.discovery_interval,
    )
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
