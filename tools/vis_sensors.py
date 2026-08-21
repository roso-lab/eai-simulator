#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections.abc import Iterable, Sequence
import os
import sys
from typing import Any

import numpy as np


ROS_IMAGE_TYPES = frozenset({"sensor_msgs/msg/Image", "sensor_msgs/Image"})


def _load_cv2():
    try:
        import cv2
    except Exception as exc:
        raise RuntimeError("OpenCV Python bindings are required for sensor visualization.") from exc
    return cv2


def _load_ros():
    try:
        import rclpy
        from cv_bridge import CvBridge
        from rclpy.node import Node
        from rclpy.qos import qos_profile_sensor_data
        from sensor_msgs.msg import Image, PointCloud2
    except Exception as exc:
        ros_distro = os.environ.get("ROS_DISTRO", "humble")
        raise RuntimeError(
            f"ROS2 Python modules are unavailable. Source /opt/ros/{ros_distro}/setup.bash "
            "and run this tool with the selected system ROS Python."
        ) from exc
    return rclpy, CvBridge, Node, qos_profile_sensor_data, Image, PointCloud2


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


def sensor_topics_for_namespace(namespace: str | None, sensor: str = "orsus") -> tuple[str, ...]:
    prefix = normalize_namespace(namespace)
    if sensor == "lidar":
        return (f"{prefix}/cloud",)
    if sensor == "camera":
        return (f"{prefix}/camera/image_raw",)
    if sensor == "realsense":
        return (
            f"{prefix}/RealsenseD455_rgb",
            f"{prefix}/RealsenseD455_depth",
        )
    return (
        f"{prefix}/Orsus_L_cam",
        f"{prefix}/Orsus_R_cam",
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
        choices=("auto", "camera", "orsus", "realsense", "lidar"),
        help=(
            "auto discovers every Image topic; camera discovers Image topics below --namespace; "
            "orsus subscribes its stereo cameras and cloud; realsense subscribes the RealSense "
            "D455 RGB and depth images; lidar subscribes cloud only."
        ),
    )
    parser.add_argument(
        "--namespace",
        default=None,
        help=(
            "Optional robot namespace, such as /iris_1 or /carter_1. Explicit "
            "orsus/realsense/lidar mode "
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
        parsed_args.namespace = "/isaac" if parsed_args.sensor in {"orsus", "realsense", "lidar"} else ""
    parsed_args.ros_args = ros_args
    return parsed_args


def resize_to_fit(image: np.ndarray, maximum_edge: int) -> np.ndarray:
    height, width = image.shape[:2]
    if height <= 0 or width <= 0:
        raise ValueError("Cannot display an empty image")
    scale = float(maximum_edge) / float(max(height, width))
    target = (max(1, round(width * scale)), max(1, round(height * scale)))
    try:
        cv2 = _load_cv2()
    except RuntimeError:
        target_width, target_height = target
        rows = np.minimum((np.arange(target_height) / scale).astype(np.intp), height - 1)
        columns = np.minimum((np.arange(target_width) / scale).astype(np.intp), width - 1)
        return np.asarray(image)[rows][:, columns]
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    return cv2.resize(image, target, interpolation=interpolation)


def scale_to_uint8(image: np.ndarray) -> np.ndarray:
    """Scale finite pixels to uint8 and render non-finite pixels black."""
    array = np.asarray(image, dtype=np.float64)
    finite_mask = np.isfinite(array)
    output = np.zeros(array.shape, dtype=np.uint8)
    finite = array[finite_mask]
    if finite.size == 0:
        return output

    lower, upper = np.percentile(finite, (1.0, 99.0))
    if not np.isfinite(lower) or not np.isfinite(upper) or upper <= lower:
        lower, upper = float(finite.min()), float(finite.max())
    if upper <= lower:
        return output

    scaled = np.clip((finite - lower) / (upper - lower), 0.0, 1.0)
    output[finite_mask] = (scaled * 255.0).astype(np.uint8)
    return output


class SensorVisualizer:
    def __init__(
        self,
        namespace: str = "",
        sensor: str = "auto",
        window_size: int = 600,
        discovery_interval: float = 1.0,
        *,
        ros_types=None,
        ros_node=None,
        cv2_module=None,
    ) -> None:
        (
            self._rclpy,
            CvBridge,
            Node,
            self._qos_profile_sensor_data,
            self._Image,
            self._PointCloud2,
        ) = ros_types or _load_ros()
        self._cv2 = cv2_module or _load_cv2()
        self.ros_node = ros_node or Node("eai_sensor_visualizer")
        self.bridge = CvBridge()
        self.sensor = sensor
        self.namespace = normalize_namespace(namespace)
        self.lidar_view_range = 10.0
        self.window_size = int(window_size)
        self.image_subscriptions: dict[str, Any] = {}
        self.image_windows: dict[str, str] = {}
        self.cloud_subscription = None
        self.discovery_timer = None

        if self.sensor == "orsus":
            topic_left, topic_right, topic_cloud = sensor_topics_for_namespace(self.namespace, "orsus")
            self._subscribe_image(topic_left, f"Orsus Left: {topic_left}")
            self._subscribe_image(topic_right, f"Orsus Right: {topic_right}")
            self._subscribe_cloud(topic_cloud)
        elif self.sensor == "realsense":
            topic_rgb, topic_depth = sensor_topics_for_namespace(self.namespace, "realsense")
            self._subscribe_image(topic_rgb, f"RealSense RGB: {topic_rgb}")
            self._subscribe_image(topic_depth, f"RealSense Depth: {topic_depth}")
            print(f"RealSense IMU topic (not visualized): {normalize_namespace(self.namespace)}/RealsenseD455_imu")
        elif self.sensor == "lidar":
            self._subscribe_cloud(sensor_topics_for_namespace(self.namespace, "lidar")[0])
        else:
            self._discover_images()
            self.discovery_timer = self.ros_node.create_timer(float(discovery_interval), self._discover_images)
            scope = f" below {self.namespace}" if self.namespace else ""
            print(f"Discovering all sensor_msgs/msg/Image topics{scope}...")

    def _subscribe_image(self, topic: str, window_name: str | None = None) -> None:
        if topic in self.image_subscriptions:
            return
        title = window_name or f"Camera: {topic}"

        def callback(message: Any, *, source_topic: str = topic) -> None:
            self.show_image(message, self.image_windows[source_topic])

        self.image_windows[topic] = title
        self.image_subscriptions[topic] = self.ros_node.create_subscription(
            self._Image,
            topic,
            callback,
            self._qos_profile_sensor_data,
        )
        print(f"Subscribed to image: {topic}")

    def _subscribe_cloud(self, topic: str) -> None:
        if self.cloud_subscription is not None:
            return
        self.cloud_subscription = self.ros_node.create_subscription(
            self._PointCloud2,
            topic,
            self.cb_cloud,
            self._qos_profile_sensor_data,
        )
        print(f"Subscribed to pointcloud: {topic}")

    def _discover_images(self) -> None:
        try:
            topics = discover_image_topics(self.ros_node.get_topic_names_and_types(), self.namespace)
        except Exception as exc:
            self.ros_node.get_logger().warning(f"Failed to inspect ROS topics: {exc}")
            return
        for topic in topics:
            self._subscribe_image(topic)

    _scale_to_uint8 = staticmethod(scale_to_uint8)

    def _message_to_bgr(self, message: Any) -> np.ndarray:
        try:
            return self.bridge.imgmsg_to_cv2(message, desired_encoding="bgr8")
        except Exception:
            image = np.asarray(self.bridge.imgmsg_to_cv2(message, desired_encoding="passthrough"))
            if image.dtype != np.uint8:
                image = scale_to_uint8(image)
            if image.ndim == 2:
                return self._cv2.cvtColor(image, self._cv2.COLOR_GRAY2BGR)
            if image.ndim != 3:
                raise ValueError(f"Unsupported image shape: {image.shape}")
            if image.shape[2] == 4:
                code = (
                    self._cv2.COLOR_RGBA2BGR
                    if message.encoding.lower().startswith("rgba")
                    else self._cv2.COLOR_BGRA2BGR
                )
                return self._cv2.cvtColor(image, code)
            if image.shape[2] == 3 and message.encoding.lower().startswith("rgb"):
                return self._cv2.cvtColor(image, self._cv2.COLOR_RGB2BGR)
            if image.shape[2] == 3:
                return image
            raise ValueError(f"Unsupported image shape: {image.shape}")

    def show_image(self, message: Any, window_name: str) -> None:
        try:
            image = resize_to_fit(self._message_to_bgr(message), self.window_size)
            self._cv2.imshow(window_name, image)
            self._cv2.waitKey(1)
        except Exception as exc:
            self.ros_node.get_logger().error(f"Failed to display {window_name}: {exc}")

    def cb_cloud(self, message: Any) -> None:
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
            self._cv2.circle(image, (center, center), 5, (0, 0, 255), -1)
            self._cv2.arrowedLine(image, (center, center), (center, center - 30), (0, 255, 0), 2)
            self._cv2.imshow(f"Lidar BEV: {self.namespace or '/'}", image)
            self._cv2.waitKey(1)
        except Exception as exc:
            self.ros_node.get_logger().error(f"Failed to display pointcloud: {exc}")

    def destroy_node(self) -> None:
        self.ros_node.destroy_node()


def main(args: Sequence[str] | None = None) -> int:
    parsed_args = parse_args(args)
    try:
        ros_types = _load_ros()
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 2
    rclpy = ros_types[0]
    Node = ros_types[2]
    initialized = False
    ros_node = None
    cv2 = None
    primary_error: Exception | None = None
    cleanup_errors: list[tuple[str, Exception]] = []
    try:
        rclpy.init(args=parsed_args.ros_args)
        initialized = True
        cv2 = _load_cv2()
        ros_node = Node("eai_sensor_visualizer")
        visualizer = SensorVisualizer(
            namespace=parsed_args.namespace,
            sensor=parsed_args.sensor,
            window_size=parsed_args.window_size,
            discovery_interval=parsed_args.discovery_interval,
            ros_types=ros_types,
            ros_node=ros_node,
            cv2_module=cv2,
        )
        try:
            rclpy.spin(ros_node)
        except KeyboardInterrupt:
            pass
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        primary_error = exc
    finally:
        if ros_node is not None:
            try:
                ros_node.destroy_node()
            except Exception as exc:
                cleanup_errors.append(("node destruction", exc))
        if initialized:
            try:
                rclpy.try_shutdown()
            except Exception as exc:
                cleanup_errors.append(("ROS2 shutdown", exc))
        if cv2 is not None:
            try:
                cv2.destroyAllWindows()
            except Exception as exc:
                cleanup_errors.append(("OpenCV window cleanup", exc))

    if primary_error is not None:
        print(f"Failed to initialize ROS2 sensor visualization: {primary_error}", file=sys.stderr)
    for phase, exc in cleanup_errors:
        print(f"Sensor visualization cleanup failed during {phase}: {exc}", file=sys.stderr)
    return 2 if primary_error is not None or cleanup_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
