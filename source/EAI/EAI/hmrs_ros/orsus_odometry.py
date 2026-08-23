# Copyright (c) 2026, EAI Simulator contributors.
"""Instance-safe Orsus ROS 2 odometry publishing."""

from __future__ import annotations

from typing import Any, Mapping


class OrsusOdometryManager:
    """Publish each Orsus host robot's root state as ROS 2 odometry."""

    def __init__(self, env: Any, instances: Mapping[str, str]) -> None:
        self._env = env
        self._instances = dict(instances)
        self._elapsed = 0.0
        self._node = None
        self._publishers: dict[str, Any] = {}
        self._owns_context = False
        if not self._instances:
            return

        import rclpy
        from nav_msgs.msg import Odometry

        try:
            # Isaac Sim provides the rclpy build bundled with its ROS2 Bridge.
            if not rclpy.ok():
                rclpy.init()
                self._owns_context = True
            self._node = rclpy.create_node("eai_orsus_odometry")
            for robot_name, namespace in self._instances.items():
                topic = f"/{namespace.strip('/')}/odometry"
                self._publishers[robot_name] = self._node.create_publisher(
                    Odometry, topic, 10
                )
        except Exception:
            self.close()
            raise

    @property
    def registered_instances(self) -> tuple[str, ...]:
        return tuple(self._instances)

    def update(self, dt: float | None = None) -> None:
        if self._node is None:
            return

        from nav_msgs.msg import Odometry

        step_dt = float(
            dt if dt is not None else getattr(self._env, "step_dt", 0.02)
        )
        self._elapsed += step_dt
        stamp_ns = max(0, int(round(self._elapsed * 1_000_000_000)))
        articulations = getattr(
            getattr(self._env, "scene", None), "articulations", {}
        )
        for robot_name, publisher in self._publishers.items():
            robot = articulations.get(robot_name)
            if robot is None:
                continue
            data = robot.data
            position = _tensor_values(data.root_pos_w[0])
            quaternion = _tensor_values(data.root_quat_w[0])
            linear_velocity = _tensor_values(data.root_lin_vel_b[0])
            angular_velocity = _tensor_values(data.root_ang_vel_b[0])

            message = Odometry()
            message.header.stamp.sec = stamp_ns // 1_000_000_000
            message.header.stamp.nanosec = stamp_ns % 1_000_000_000
            message.header.frame_id = "mapping_init"
            namespace = self._instances[robot_name].strip("/")
            message.child_frame_id = f"{namespace}/base_link"
            _set_vector(message.pose.pose.position, position)
            _set_quaternion(message.pose.pose.orientation, quaternion)
            _set_vector(message.twist.twist.linear, linear_velocity)
            _set_vector(message.twist.twist.angular, angular_velocity)
            publisher.publish(message)

    def reset(self, _env_ids=None) -> None:
        # ROS timestamps must stay monotonic across partial and full env resets.
        return None

    def close(self) -> None:
        env = self._env
        self._publishers.clear()
        node, self._node = self._node, None
        if node is not None:
            try:
                node.destroy_node()
            except Exception:
                pass
        if self._owns_context:
            try:
                import rclpy

                if rclpy.ok():
                    rclpy.shutdown()
            except Exception:
                pass
            self._owns_context = False
        if env is not None and getattr(env, "_orsus_odometry_manager", None) is self:
            delattr(env, "_orsus_odometry_manager")
        self._instances.clear()
        self._env = None


def _tensor_values(value: Any) -> list[float]:
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "tolist"):
        value = value.tolist()
    return [float(item) for item in value]


def _set_vector(target: Any, values: list[float]) -> None:
    target.x, target.y, target.z = values


def _set_quaternion(target: Any, values_wxyz: list[float]) -> None:
    target.w, target.x, target.y, target.z = values_wxyz


def attach_orsus_odometry_manager(env: Any, manager: OrsusOdometryManager) -> None:
    env._orsus_odometry_manager = manager


def orsus_odometry_instance_registry() -> dict[str, str]:
    from EAI_assets.sensor.high_sensor.orsus import _orsus_odometry_instances

    return dict(_orsus_odometry_instances)


__all__ = [
    "OrsusOdometryManager",
    "attach_orsus_odometry_manager",
    "orsus_odometry_instance_registry",
]
