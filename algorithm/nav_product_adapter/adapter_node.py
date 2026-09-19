#!/usr/bin/env python3
"""System-ROS Humble node adapting EAI navigation data to product topics."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
import os
from pathlib import Path
import sys
import time
from typing import Any, Sequence


THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from core import (  # noqa: E402
    OdometryBuffer,
    PointFieldSpec,
    RegistrationEngine,
    SimulationTimestampRateGate,
    Transform,
    decode_finite_xyzi,
    quaternion_from_rpy,
    transform_and_encode_xyzi,
    transform_twist_base_to_lidar,
)


SYSTEM_ROS_PYTHON = "/usr/bin/python3"
UNKNOWN_PUBLISHER_FQN = "/_NODE_NAMESPACE_UNKNOWN_/_NODE_NAME_UNKNOWN_"


@dataclass(frozen=True)
class QosContract:
    reliability: str
    durability: str
    depth: int


INTERNAL_SENSOR_QOS = QosContract("best_effort", "volatile", 10)
PRODUCT_OUTPUT_QOS = QosContract("reliable", "volatile", 10)
COMMAND_QOS = QosContract("reliable", "volatile", 10)


class DiagnosticChangeTracker:
    """Report a diagnostic once per distinct bad value until recovery."""

    def __init__(self) -> None:
        self._last_values: dict[str, tuple[str, ...]] = {}

    def should_report(self, key: str, value: Sequence[str]) -> bool:
        normalized = tuple(str(item) for item in value)
        if self._last_values.get(key) == normalized:
            return False
        self._last_values[key] = normalized
        return True

    def clear(self, key: str) -> None:
        self._last_values.pop(key, None)


@dataclass
class PendingCloud:
    stamp_ns: int
    message: Any


def stamp_to_ns(stamp: Any) -> int:
    return int(stamp.sec) * 1_000_000_000 + int(stamp.nanosec)


def fully_qualified_node_name(namespace: str, name: str) -> str:
    namespace = str(namespace).strip()
    prefix = "/" if namespace in ("", "/") else f"/{namespace.strip('/')}" + "/"
    return f"{prefix}{str(name).strip('/')}"


@dataclass(frozen=True)
class PublisherSourceConflict:
    node_name: str
    observed_count: int
    expected_count: int
    expectation: str


@dataclass(frozen=True)
class PublisherSourceClassification:
    endpoint_counts: tuple[tuple[str, int], ...]
    own_endpoint_count: int
    allowlisted_passive: tuple[str, ...]
    conflicts: tuple[PublisherSourceConflict, ...]


def classify_publisher_sources(
    endpoints: Sequence[Any],
    own_fully_qualified_name: str,
    *,
    expected_own_endpoints: int,
    passive_allowlist: Sequence[str] = (),
) -> PublisherSourceClassification:
    """Classify publisher endpoints without discarding duplicate node names."""

    if expected_own_endpoints < 0:
        raise ValueError("expected_own_endpoints must be non-negative")
    counts = Counter(
        fully_qualified_node_name(endpoint.node_namespace, endpoint.node_name)
        for endpoint in endpoints
    )
    endpoint_counts = tuple(sorted(counts.items()))
    own_count = counts.pop(own_fully_qualified_name, 0)
    conflicts: list[PublisherSourceConflict] = []
    if own_count != expected_own_endpoints:
        conflicts.append(
            PublisherSourceConflict(
                own_fully_qualified_name,
                own_count,
                expected_own_endpoints,
                "exactly",
            )
        )

    allowed_names = {str(name) for name in passive_allowlist}
    allowlisted_passive: list[str] = []
    for node_name, count in sorted(counts.items()):
        permitted_count = 1 if node_name in allowed_names else 0
        if permitted_count:
            allowlisted_passive.append(node_name)
        if count > permitted_count:
            conflicts.append(
                PublisherSourceConflict(
                    node_name,
                    count,
                    permitted_count,
                    "at_most",
                )
            )
    return PublisherSourceClassification(
        endpoint_counts=endpoint_counts,
        own_endpoint_count=own_count,
        allowlisted_passive=tuple(allowlisted_passive),
        conflicts=tuple(conflicts),
    )


def publisher_conflicts_only_unknown(
    conflicts: dict[str, tuple[PublisherSourceConflict, ...]],
) -> bool:
    """Return whether every conflict is transiently missing node metadata."""

    return bool(conflicts) and all(
        item.node_name == UNKNOWN_PUBLISHER_FQN
        for items in conflicts.values()
        for item in items
    )


def _same_executable(left: str, right: str) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return os.path.realpath(left) == os.path.realpath(right)


def _reexec_system_python_if_needed() -> None:
    target = os.environ.get("EAI_NAV_PRODUCT_ROS_PYTHON", SYSTEM_ROS_PYTHON)
    if os.environ.get("EAI_NAV_PRODUCT_NO_REEXEC") == "1":
        return
    if not os.path.exists(target) or _same_executable(sys.executable, target):
        return
    if os.environ.get("CONDA_PREFIX") or "conda" in sys.executable:
        os.execv(target, [target, str(Path(__file__).resolve()), *sys.argv[1:]])


def _load_ros_symbols() -> dict[str, Any]:
    try:
        import rclpy
        from geometry_msgs.msg import Twist
        from nav_msgs.msg import Odometry
        from rclpy.executors import ExternalShutdownException
        from rclpy.node import Node
        from rclpy.qos import (
            DurabilityPolicy,
            HistoryPolicy,
            QoSProfile,
            ReliabilityPolicy,
        )
        from rosgraph_msgs.msg import Clock
        from sensor_msgs.msg import PointCloud2, PointField
    except Exception as exc:
        distro = os.environ.get("ROS_DISTRO", "humble")
        raise SystemExit(
            "Unable to import system ROS 2 Python modules. Run this adapter outside "
            "the Isaac/Conda interpreter after sourcing system ROS:\n"
            f"  conda deactivate\n  source /opt/ros/{distro}/setup.bash\n"
            "  ros2 launch algorithm/nav_product_adapter/nav_product_adapter.launch.py ...\n"
            f"Original error: {exc}"
        ) from exc
    return locals()


def _build_node_class(ros: dict[str, Any]):
    rclpy = ros["rclpy"]
    Node = ros["Node"]
    Twist = ros["Twist"]
    Odometry = ros["Odometry"]
    Clock = ros["Clock"]
    PointCloud2 = ros["PointCloud2"]
    PointField = ros["PointField"]
    QoSProfile = ros["QoSProfile"]
    ReliabilityPolicy = ros["ReliabilityPolicy"]
    DurabilityPolicy = ros["DurabilityPolicy"]
    HistoryPolicy = ros["HistoryPolicy"]

    class NavProductAdapter(Node):
        def __init__(self) -> None:
            super().__init__("nav_product_adapter")
            defaults = {
                "profile_name": "mid360",
                "robot_name": "robot_1",
                "nav_hub_id": "nav_hub_1",
                "cloud_input_topic": "/robot_1/cloud",
                "odometry_input_topic": "/robot_1/odometry",
                "cmd_vel_input_topic": "/nav_hub_1/cmd_vel",
                "cloud_output_topic": "/nav_hub_1/tower/mapping/cloud",
                "odometry_output_topic": "/nav_hub_1/tower/mapping/odometry",
                "cmd_vel_output_topic": "/robot_1/cmd_vel",
                "cloud_input_frame": "robot_1/lidar_link",
                "odometry_input_frame": "robot_1/odom",
                "odometry_child_frame": "robot_1/base_footprint",
                "mapping_frame": "nav_hub_1/mapping_init",
                "lidar_frame": "nav_hub_1/lidar_link",
                "base_to_lidar_xyz": [0.0, 0.0, 0.0],
                "base_to_lidar_rpy": [0.0, 0.0, 0.0],
                "expected_cloud_rate_hz": 10.0,
                "product_odometry_rate_hz": 50.0,
                "odom_buffer_sec": 2.0,
                "max_interpolation_gap_sec": 0.1,
                "backward_jump_threshold_sec": 0.5,
                "max_pending_clouds": 8,
                "intensity_field": "intensity",
                "default_intensity": 0.0,
                "clock_owner": "external",
                "clock_topic": "/clock",
                "duplicate_publisher_action": "fail",
                "passive_product_publisher_allowlist": ["/topic_publisher"],
                "publisher_guard_period_sec": 2.0,
                "use_sim_time": True,
            }
            for name, default in defaults.items():
                if not self.has_parameter(name):
                    self.declare_parameter(name, default)
            values = {name: self.get_parameter(name).value for name in defaults}
            if not bool(values["use_sim_time"]):
                raise ValueError("use_sim_time must be true; EAI owns the external simulation clock")
            if values["clock_owner"] != "external":
                raise ValueError("clock_owner must remain 'external'")

            self._topics = {
                key: str(values[f"{key}_topic"])
                for key in (
                    "cloud_input",
                    "odometry_input",
                    "cmd_vel_input",
                    "cloud_output",
                    "odometry_output",
                    "cmd_vel_output",
                )
            }
            if len(set(self._topics.values())) != len(self._topics):
                raise ValueError("adapter input and output topics must all be distinct")
            self._expected_input_frames = {
                "cloud": (str(values["cloud_input_frame"]),),
                "odometry": (
                    str(values["odometry_input_frame"]),
                    str(values["odometry_child_frame"]),
                ),
            }
            self._input_frame_diagnostics = DiagnosticChangeTracker()
            self._product_output_topics = (
                self._topics["cloud_output"],
                self._topics["odometry_output"],
            )
            self._guarded_output_topics = (*self._product_output_topics, self._topics["cmd_vel_output"])
            self._duplicate_publisher_action = str(values["duplicate_publisher_action"]).lower()
            if self._duplicate_publisher_action not in {"fail", "warn"}:
                raise ValueError("duplicate_publisher_action must be 'fail' or 'warn'")
            self._passive_product_publisher_allowlist = tuple(
                str(name) for name in (values["passive_product_publisher_allowlist"] or ())
            )
            self._publisher_guard_period_sec = float(values["publisher_guard_period_sec"])
            if self._passive_product_publisher_allowlist:
                self.get_logger().warning(
                    "passive product publisher allowlist enabled for "
                    f"{', '.join(self._passive_product_publisher_allowlist)}; this requires "
                    "no WebSocket listener/server on port 9089. Do not run the direct-DDS "
                    "adapter when 9089 is enabled because node-name allowlisting cannot "
                    "detect later WebSocket activation"
                )
            self._reported_passive_publishers: set[tuple[str, str]] = set()
            self._reported_publisher_conflicts: tuple[
                tuple[str, tuple[PublisherSourceConflict, ...]], ...
            ] = ()
            self._mapping_frame = str(values["mapping_frame"]).strip("/")
            self._lidar_frame = str(values["lidar_frame"]).strip("/")
            xyz = tuple(float(v) for v in values["base_to_lidar_xyz"])
            rpy = tuple(float(v) for v in values["base_to_lidar_rpy"])
            if len(xyz) != 3 or len(rpy) != 3:
                raise ValueError("base_to_lidar_xyz and base_to_lidar_rpy require three values")
            self._base_from_lidar = Transform(xyz, quaternion_from_rpy(*rpy))
            backward_jump_sec = float(values["backward_jump_threshold_sec"])
            self._backward_jump_ns = int(backward_jump_sec * 1_000_000_000)
            self._engine = RegistrationEngine(
                OdometryBuffer(
                    duration_sec=float(values["odom_buffer_sec"]),
                    max_interpolation_gap_sec=float(values["max_interpolation_gap_sec"]),
                    backward_jump_threshold_sec=backward_jump_sec,
                ),
                self._base_from_lidar,
            )
            self._max_pending_clouds = int(values["max_pending_clouds"])
            self._intensity_field = str(values["intensity_field"])
            self._default_intensity = float(values["default_intensity"])
            self._expected_cloud_rate_hz = float(values["expected_cloud_rate_hz"])
            self._cloud_output_gate = SimulationTimestampRateGate(
                self._expected_cloud_rate_hz
            )
            self._odometry_output_gate = SimulationTimestampRateGate(
                float(values["product_odometry_rate_hz"])
            )
            self._pending_clouds: list[PendingCloud] = []
            self._cloud_periods_sec: deque[float] = deque(maxlen=64)
            self._last_cloud_stamp_ns: int | None = None
            self._last_clock_ns: int | None = None
            self._published_cloud_count = 0
            self._publisher_guard_tripped = False

            def qos_profile(contract: QosContract) -> Any:
                reliability = {
                    "best_effort": ReliabilityPolicy.BEST_EFFORT,
                    "reliable": ReliabilityPolicy.RELIABLE,
                }[contract.reliability]
                durability = {"volatile": DurabilityPolicy.VOLATILE}[contract.durability]
                return QoSProfile(
                    history=HistoryPolicy.KEEP_LAST,
                    depth=contract.depth,
                    reliability=reliability,
                    durability=durability,
                )

            internal_sensor_qos = qos_profile(INTERNAL_SENSOR_QOS)
            product_output_qos = qos_profile(PRODUCT_OUTPUT_QOS)
            command_qos = qos_profile(COMMAND_QOS)
            clock_qos = QoSProfile(
                history=HistoryPolicy.KEEP_LAST,
                depth=1,
                reliability=ReliabilityPolicy.BEST_EFFORT,
                durability=DurabilityPolicy.VOLATILE,
            )
            self._own_fqn = fully_qualified_node_name(self.get_namespace(), self.get_name())
            self._reject_existing_product_publishers()
            self._cloud_publisher = self.create_publisher(
                PointCloud2, self._topics["cloud_output"], product_output_qos
            )
            self._odometry_publisher = self.create_publisher(
                Odometry, self._topics["odometry_output"], product_output_qos
            )
            self._cmd_vel_publisher = self.create_publisher(
                Twist, self._topics["cmd_vel_output"], command_qos
            )
            self.create_subscription(
                PointCloud2, self._topics["cloud_input"], self._on_cloud, internal_sensor_qos
            )
            self.create_subscription(
                Odometry, self._topics["odometry_input"], self._on_odometry, internal_sensor_qos
            )
            self.create_subscription(
                Twist, self._topics["cmd_vel_input"], self._cmd_vel_publisher.publish, command_qos
            )
            self.create_subscription(
                Clock, str(values["clock_topic"]), self._on_clock, clock_qos
            )
            self.create_timer(
                self._publisher_guard_period_sec, self._guard_product_publishers
            )
            self.get_logger().info(
                f"profile={values['profile_name']} robot={values['robot_name']} "
                f"nav_hub={values['nav_hub_id']} clock=external:{values['clock_topic']} "
                f"product_cloud_rate={values['expected_cloud_rate_hz']}Hz "
                f"product_odometry_rate={values['product_odometry_rate_hz']}Hz"
            )
            self.get_logger().info(
                f"cloud {self._topics['cloud_input']} -> {self._topics['cloud_output']}; "
                f"odometry {self._topics['odometry_input']} -> {self._topics['odometry_output']}; "
                f"cmd_vel {self._topics['cmd_vel_input']} -> {self._topics['cmd_vel_output']}"
            )
            self.get_logger().info("TF publication disabled: the Docker product owns the external TF tree")

        def _publisher_sources(
            self, topic: str, *, expected_own_endpoints: int
        ) -> PublisherSourceClassification:
            passive_allowlist = (
                self._passive_product_publisher_allowlist
                if topic in self._product_output_topics
                else ()
            )
            classification = classify_publisher_sources(
                self.get_publishers_info_by_topic(topic),
                self._own_fqn,
                expected_own_endpoints=expected_own_endpoints,
                passive_allowlist=passive_allowlist,
            )
            for node_name in classification.allowlisted_passive:
                marker = (topic, node_name)
                if marker not in self._reported_passive_publishers:
                    self._reported_passive_publishers.add(marker)
                    self.get_logger().info(
                        f"allowing configured passive publisher {node_name} on {topic}"
                    )
            return classification

        def _publisher_conflicts(
            self, *, expected_own_endpoints: int
        ) -> dict[str, tuple[PublisherSourceConflict, ...]]:
            conflicts = {
                topic: self._publisher_sources(
                    topic, expected_own_endpoints=expected_own_endpoints
                ).conflicts
                for topic in self._guarded_output_topics
            }
            return {topic: items for topic, items in conflicts.items() if items}

        @staticmethod
        def _format_publisher_conflicts(
            conflicts: dict[str, tuple[PublisherSourceConflict, ...]],
        ) -> str:
            return "; ".join(
                f"{topic}: "
                + ", ".join(
                    f"{item.node_name} (observed {item.observed_count} endpoints; "
                    f"expected {item.expectation.replace('_', ' ')} {item.expected_count})"
                    for item in items
                )
                for topic, items in conflicts.items()
            )

        def _reject_existing_product_publishers(self) -> None:
            deadline = time.monotonic() + self._publisher_guard_period_sec
            waiting_for_metadata = False
            while True:
                conflicts = self._publisher_conflicts(expected_own_endpoints=0)
                if not conflicts:
                    return
                remaining = deadline - time.monotonic()
                if not publisher_conflicts_only_unknown(conflicts) or remaining <= 0.0:
                    break
                if not waiting_for_metadata:
                    waiting_for_metadata = True
                    self.get_logger().info(
                        "waiting for anonymous DDS publisher metadata to converge before "
                        "evaluating product output ownership"
                    )
                time.sleep(min(0.1, remaining))

            details = self._format_publisher_conflicts(conflicts)
            if self._duplicate_publisher_action == "warn":
                self._reported_publisher_conflicts = tuple(sorted(conflicts.items()))
                self.get_logger().warning(
                    f"adapter output publisher ownership conflict; continuing by policy ({details})"
                )
                return
            raise RuntimeError(f"adapter output publisher ownership conflict ({details})")

        def _guard_product_publishers(self) -> None:
            if self._publisher_guard_tripped:
                return
            conflicts = self._publisher_conflicts(expected_own_endpoints=1)
            snapshot = tuple(sorted(conflicts.items()))
            if not conflicts:
                self._reported_publisher_conflicts = ()
                return
            details = self._format_publisher_conflicts(conflicts)
            if self._duplicate_publisher_action == "warn":
                if snapshot != self._reported_publisher_conflicts:
                    self._reported_publisher_conflicts = snapshot
                    self.get_logger().warning(
                        f"adapter output publisher ownership conflict; continuing by policy ({details})"
                    )
                return
            self._publisher_guard_tripped = True
            self.get_logger().fatal(
                f"adapter output publisher ownership conflict; stopping adapter ({details})"
            )
            self.destroy_publisher(self._cloud_publisher)
            self.destroy_publisher(self._odometry_publisher)
            self.destroy_publisher(self._cmd_vel_publisher)
            rclpy.shutdown()

        def _input_frames_valid(self, stream: str, observed: Sequence[str]) -> bool:
            expected = self._expected_input_frames[stream]
            observed = tuple(str(value) for value in observed)
            if observed == expected:
                self._input_frame_diagnostics.clear(stream)
                return True
            if self._input_frame_diagnostics.should_report(stream, observed):
                field_names = (
                    ("header.frame_id",)
                    if stream == "cloud"
                    else ("header.frame_id", "child_frame_id")
                )
                details = ", ".join(
                    f"{field}={actual!r}, expected {wanted!r}"
                    for field, actual, wanted in zip(field_names, observed, expected)
                    if actual != wanted
                )
                self.get_logger().error(f"dropping {stream}: input frame mismatch ({details})")
            return False

        def _reset_epoch(self, reason: str, *, preserve_current_odometry: bool = False) -> None:
            if not preserve_current_odometry:
                self._engine.reset()
            self._pending_clouds.clear()
            self._cloud_periods_sec.clear()
            self._last_cloud_stamp_ns = None
            self._cloud_output_gate.reset()
            self._odometry_output_gate.reset()
            self.get_logger().warning(f"reset mapping_init epoch: {reason}")

        def _on_clock(self, message: Any) -> None:
            stamp_ns = stamp_to_ns(message.clock)
            if (
                self._last_clock_ns is not None
                and stamp_ns < self._last_clock_ns
                and self._last_clock_ns - stamp_ns > self._backward_jump_ns
            ):
                previous = self._last_clock_ns
                self._reset_epoch(f"/clock moved backward from {previous} to {stamp_ns} ns")
            self._last_clock_ns = stamp_ns

        def _on_odometry(self, message: Any) -> None:
            if not self._input_frames_valid(
                "odometry", (message.header.frame_id, message.child_frame_id)
            ):
                return
            stamp_ns = stamp_to_ns(message.header.stamp)
            pose = message.pose.pose
            try:
                odom_from_base = Transform(
                    (pose.position.x, pose.position.y, pose.position.z),
                    (pose.orientation.x, pose.orientation.y, pose.orientation.z, pose.orientation.w),
                )
                update = self._engine.add_odometry(stamp_ns, odom_from_base)
            except ValueError as exc:
                self.get_logger().error(f"dropping invalid odometry at {stamp_ns} ns: {exc}")
                return
            if update.reset:
                self._reset_epoch(
                    f"odometry moved backward to {stamp_ns} ns", preserve_current_odometry=True
                )
            if not update.accepted:
                self.get_logger().warning(f"dropping out-of-order odometry at {stamp_ns} ns")
                return
            mapping_from_lidar = self._engine.mapping_from_lidar_at(stamp_ns)
            if (
                mapping_from_lidar is not None
                and self._odometry_output_gate.should_publish(stamp_ns)
            ):
                self._publish_odometry(message, mapping_from_lidar)
            self._drain_pending_clouds()

        def _publish_odometry(self, source: Any, mapping_from_lidar: Transform) -> None:
            output = Odometry()
            output.header.stamp = source.header.stamp
            output.header.frame_id = self._mapping_frame
            output.child_frame_id = self._lidar_frame
            output.pose.pose.position.x, output.pose.pose.position.y, output.pose.pose.position.z = (
                mapping_from_lidar.translation
            )
            (
                output.pose.pose.orientation.x,
                output.pose.pose.orientation.y,
                output.pose.pose.orientation.z,
                output.pose.pose.orientation.w,
            ) = mapping_from_lidar.rotation
            output.pose.covariance = source.pose.covariance
            source_twist = source.twist.twist
            linear, angular = transform_twist_base_to_lidar(
                (source_twist.linear.x, source_twist.linear.y, source_twist.linear.z),
                (source_twist.angular.x, source_twist.angular.y, source_twist.angular.z),
                self._base_from_lidar,
            )
            output.twist.twist.linear.x, output.twist.twist.linear.y, output.twist.twist.linear.z = linear
            output.twist.twist.angular.x, output.twist.twist.angular.y, output.twist.twist.angular.z = angular
            output.twist.covariance = source.twist.covariance
            self._odometry_publisher.publish(output)

        def _on_cloud(self, message: Any) -> None:
            if not self._input_frames_valid("cloud", (message.header.frame_id,)):
                return
            stamp_ns = stamp_to_ns(message.header.stamp)
            if (
                self._last_cloud_stamp_ns is not None
                and stamp_ns < self._last_cloud_stamp_ns
                and self._last_cloud_stamp_ns - stamp_ns > self._backward_jump_ns
            ):
                previous = self._last_cloud_stamp_ns
                self._reset_epoch(f"cloud time moved backward from {previous} to {stamp_ns} ns")
            if self._last_cloud_stamp_ns is not None and stamp_ns > self._last_cloud_stamp_ns:
                self._cloud_periods_sec.append((stamp_ns - self._last_cloud_stamp_ns) / 1_000_000_000)
            self._last_cloud_stamp_ns = stamp_ns
            try:
                if not self._cloud_output_gate.should_publish(stamp_ns):
                    return
            except ValueError as exc:
                self.get_logger().warning(
                    f"dropping out-of-order cloud at {stamp_ns} ns: {exc}"
                )
                return
            self._pending_clouds.append(PendingCloud(stamp_ns, message))
            if len(self._pending_clouds) > self._max_pending_clouds:
                dropped = self._pending_clouds.pop(0)
                self.get_logger().warning(
                    f"dropping cloud at {dropped.stamp_ns} ns: odometry did not bracket it in time"
                )
            self._drain_pending_clouds()

        def _drain_pending_clouds(self) -> None:
            retained: list[PendingCloud] = []
            newest = self._engine.buffer.newest_stamp_ns
            oldest = self._engine.buffer.oldest_stamp_ns
            for pending in self._pending_clouds:
                mapping_from_lidar = self._engine.mapping_from_lidar_at(pending.stamp_ns)
                if mapping_from_lidar is not None:
                    self._publish_cloud(pending.message, mapping_from_lidar)
                elif newest is not None and pending.stamp_ns < newest:
                    reason = "outside retained odometry" if oldest is not None and pending.stamp_ns < oldest else "odometry gap exceeds interpolation limit"
                    self.get_logger().warning(
                        f"dropping cloud at {pending.stamp_ns} ns: {reason}"
                    )
                else:
                    retained.append(pending)
            self._pending_clouds = retained

        def _publish_cloud(self, source: Any, mapping_from_lidar: Transform) -> None:
            try:
                points = decode_finite_xyzi(
                    data=bytes(source.data),
                    width=source.width,
                    height=source.height,
                    point_step=source.point_step,
                    row_step=source.row_step,
                    is_bigendian=source.is_bigendian,
                    fields=[
                        PointFieldSpec(field.name, field.offset, field.datatype, field.count)
                        for field in source.fields
                    ],
                    intensity_field=self._intensity_field,
                    default_intensity=self._default_intensity,
                )
                data, count = transform_and_encode_xyzi(points, mapping_from_lidar)
            except ValueError as exc:
                self.get_logger().error(f"dropping malformed PointCloud2: {exc}")
                return
            output = PointCloud2()
            output.header.stamp = source.header.stamp
            output.header.frame_id = self._mapping_frame
            output.height = 1
            output.width = count
            output.fields = [
                PointField(name="x", offset=0, datatype=PointField.FLOAT32, count=1),
                PointField(name="y", offset=4, datatype=PointField.FLOAT32, count=1),
                PointField(name="z", offset=8, datatype=PointField.FLOAT32, count=1),
                PointField(name="intensity", offset=12, datatype=PointField.FLOAT32, count=1),
            ]
            output.is_bigendian = False
            output.point_step = 16
            output.row_step = count * 16
            output.data = data
            output.is_dense = True
            self._cloud_publisher.publish(output)
            self._published_cloud_count += 1
            self._check_cloud_rate()

        def _check_cloud_rate(self) -> None:
            if self._published_cloud_count % 60 or len(self._cloud_periods_sec) < 8:
                return
            measured = len(self._cloud_periods_sec) / sum(self._cloud_periods_sec)
            relative_error = abs(measured - self._expected_cloud_rate_hz) / self._expected_cloud_rate_hz
            if relative_error > 0.2:
                self.get_logger().warning(
                    f"cloud rate {measured:.2f} Hz differs from profile expectation "
                    f"{self._expected_cloud_rate_hz:.2f} Hz"
                )

    return NavProductAdapter


def main(args: Sequence[str] | None = None) -> int:
    _reexec_system_python_if_needed()
    ros = _load_ros_symbols()
    rclpy = ros["rclpy"]
    ExternalShutdownException = ros["ExternalShutdownException"]
    node = None
    rclpy.init(args=list(args) if args is not None else None)
    try:
        node = _build_node_class(ros)()
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
