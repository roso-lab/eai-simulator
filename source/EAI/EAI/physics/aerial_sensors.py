# Copyright (c) 2026, EAI Simulator contributors.
# SPDX-License-Identifier: BSD-3-Clause

"""Pure sensor models and selection helpers for aerial and built-in cameras."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np


AERIAL_SENSOR_TYPES = frozenset({"cf2x", "iris", "pegasus"})
# Robots that carry a built-in monocular camera published by the sensor suite.
# This is aerial robots plus MuSHR (whose USD already contains the camera housing).
BUILTIN_CAMERA_TYPES = frozenset({"cf2x", "iris", "pegasus", "mushr_v2"})
# Public compatibility name retained for callers introduced with the Pegasus
# sensor suite. It now covers every aerial robot supported by that runtime.
PEGASUS_AERIAL_TYPES = AERIAL_SENSOR_TYPES
_BASE_SENSOR_TYPES = AERIAL_SENSOR_TYPES
_AERIAL_LIDAR_OFFSETS = {
    "cf2x": (0.0, 0.0, 0.04),
    "iris": (0.0, 0.0, 0.12),
    "pegasus": (0.0, 0.0, 0.10),
}
_CAMERA_MOUNT_LINKS = {
    "cf2x": "body",
    "iris": "body",
    "pegasus": "body",
    # MuSHR USD 中相机与 camera_link 已被删除，幸存的挂架为
    # camera_bottom_screw_frame（含 camera_link_joint 固定关节）。
    "mushr_v2": "mushr_nano/camera_bottom_screw_frame",
}


@dataclass(frozen=True)
class FirstOrderBiasConfig:
    noise_density: float
    random_walk: float
    correlation_time: float
    turn_on_bias_sigma: float = 0.0


@dataclass(frozen=True)
class AerialSensorModelConfig:
    gravity: float = 9.80665
    origin_latitude_deg: float = 38.736832
    origin_longitude_deg: float = -9.137977
    origin_altitude_m: float = 90.0
    gyroscope: FirstOrderBiasConfig = field(
        default_factory=lambda: FirstOrderBiasConfig(
            noise_density=0.0003393695767766752,
            random_walk=3.878509448876288e-05,
            correlation_time=1.0e3,
            turn_on_bias_sigma=0.008726646259971648,
        )
    )
    accelerometer: FirstOrderBiasConfig = field(
        default_factory=lambda: FirstOrderBiasConfig(
            noise_density=0.004,
            random_walk=0.006,
            correlation_time=300.0,
            turn_on_bias_sigma=0.196,
        )
    )
    gps_position_noise_std_m: tuple[float, float, float] = (1.0, 1.0, 1.5)
    gps_random_walk_m_sqrt_s: tuple[float, float, float] = (0.02, 0.02, 0.04)
    gps_bias_correlation_time_s: float = 60.0
    magnetic_field_enu_t: tuple[float, float, float] = (0.8e-6, 23.0e-6, -41.0e-6)
    magnetometer: FirstOrderBiasConfig = field(
        default_factory=lambda: FirstOrderBiasConfig(
            noise_density=0.4e-7,
            random_walk=6.4e-10,
            correlation_time=6.0e2,
        )
    )
    barometer_noise_std_pa: float = 1.0
    barometer_drift_pa_per_s: float = 0.0
    pressure_msl_pa: float = 101325.0
    temperature_msl_k: float = 288.15
    lapse_rate_k_per_m: float = 0.0065


@dataclass(frozen=True)
class AerialSensorReading:
    orientation_wxyz: tuple[float, float, float, float]
    angular_velocity_body: tuple[float, float, float]
    linear_acceleration_body: tuple[float, float, float]
    latitude_deg: float
    longitude_deg: float
    altitude_m: float
    magnetic_field_body_t: tuple[float, float, float]
    absolute_pressure_pa: float
    pressure_variance: float


@dataclass(frozen=True)
class AerialSensorRobotSpec:
    """Sensor resources and their independently gated ROS publishers.

    Aerial robots own the complete physical/model suite, so their booleans
    describe ROS publication only. For MuSHR, ``camera`` also identifies its
    optional built-in camera prim and publisher; aerial-only resources remain
    disabled.
    """

    robot_name: str
    robot_type: str
    camera: bool = False
    lidar: bool = False
    base_sensors: bool = False
    camera_mount_link: str = "body"
    lidar_offset: tuple[float, float, float] = (0.0, 0.0, 0.10)


def _normalize_vector(values: Sequence[Any], count: int, label: str) -> np.ndarray:
    vector = np.asarray(values, dtype=np.float64)
    if vector.shape != (count,) or not np.isfinite(vector).all():
        raise ValueError(f"{label} must contain {count} finite values")
    return vector


def quaternion_wxyz_to_matrix(quaternion: Sequence[Any]) -> np.ndarray:
    quat = _normalize_vector(quaternion, 4, "quaternion")
    norm = float(np.linalg.norm(quat))
    if norm <= 1.0e-12:
        raise ValueError("quaternion cannot be zero")
    w, x, y, z = quat / norm
    return np.asarray(
        [
            [1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w)],
            [2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w)],
            [2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


class _FirstOrderBias:
    def __init__(self, config: FirstOrderBiasConfig, rng: np.random.Generator) -> None:
        self.config = config
        self._rng = rng
        self.value = self._rng.normal(0.0, config.turn_on_bias_sigma, size=3)

    def sample(self, true_value: np.ndarray, dt: float) -> np.ndarray:
        config = self.config
        phi = math.exp(-dt / max(config.correlation_time, 1.0e-9))
        bias_sigma = config.random_walk * math.sqrt(
            max(config.correlation_time * (1.0 - phi * phi) * 0.5, 0.0)
        )
        self.value = phi * self.value + self._rng.normal(0.0, bias_sigma, size=3)
        white_sigma = config.noise_density / math.sqrt(dt)
        return true_value + self.value + self._rng.normal(0.0, white_sigma, size=3)


class AerialSensorModel:
    """Pegasus-compatible inertial, navigation, magnetic and pressure model."""

    def __init__(
        self,
        config: AerialSensorModelConfig | None = None,
        *,
        seed: int | None = None,
    ) -> None:
        self.config = config or AerialSensorModelConfig()
        self._seed = seed
        self.reset()

    def reset(self) -> None:
        self._rng = np.random.default_rng(self._seed)
        self._gyro = _FirstOrderBias(self.config.gyroscope, self._rng)
        self._accelerometer = _FirstOrderBias(self.config.accelerometer, self._rng)
        self._magnetometer = _FirstOrderBias(self.config.magnetometer, self._rng)
        self._gps_bias = np.zeros(3, dtype=np.float64)
        self._previous_velocity_w: np.ndarray | None = None
        self._barometer_drift_pa = 0.0

    @property
    def gyroscope_bias(self) -> tuple[float, float, float]:
        return tuple(float(value) for value in self._gyro.value)

    @property
    def accelerometer_bias(self) -> tuple[float, float, float]:
        return tuple(float(value) for value in self._accelerometer.value)

    def sample(
        self,
        *,
        position_w: Sequence[Any],
        orientation_wxyz: Sequence[Any],
        linear_velocity_w: Sequence[Any],
        angular_velocity_b: Sequence[Any],
        dt: float,
    ) -> AerialSensorReading:
        dt = float(dt)
        if not math.isfinite(dt) or dt <= 0.0:
            raise ValueError("dt must be positive and finite")
        position = _normalize_vector(position_w, 3, "position_w")
        orientation = _normalize_vector(orientation_wxyz, 4, "orientation_wxyz")
        velocity = _normalize_vector(linear_velocity_w, 3, "linear_velocity_w")
        angular_velocity = _normalize_vector(angular_velocity_b, 3, "angular_velocity_b")
        rotation_body_to_world = quaternion_wxyz_to_matrix(orientation)

        if self._previous_velocity_w is None:
            acceleration_w = np.zeros(3, dtype=np.float64)
        else:
            acceleration_w = (velocity - self._previous_velocity_w) / dt
        self._previous_velocity_w = velocity.copy()
        gravity_w = np.asarray((0.0, 0.0, -self.config.gravity), dtype=np.float64)
        specific_force_b = rotation_body_to_world.T @ (acceleration_w - gravity_w)

        measured_angular_velocity = self._gyro.sample(angular_velocity, dt)
        measured_specific_force = self._accelerometer.sample(specific_force_b, dt)

        gps_position = position + self._gps_measurement_error(dt)
        latitude, longitude, altitude = self._local_enu_to_geodetic(gps_position)

        magnetic_field_b = rotation_body_to_world.T @ np.asarray(
            self.config.magnetic_field_enu_t, dtype=np.float64
        )
        measured_magnetic_field = self._magnetometer.sample(magnetic_field_b, dt)
        pressure = self._barometer(position[2], dt)

        orientation = orientation / np.linalg.norm(orientation)
        return AerialSensorReading(
            orientation_wxyz=tuple(float(value) for value in orientation),
            angular_velocity_body=tuple(float(value) for value in measured_angular_velocity),
            linear_acceleration_body=tuple(float(value) for value in measured_specific_force),
            latitude_deg=latitude,
            longitude_deg=longitude,
            altitude_m=altitude,
            magnetic_field_body_t=tuple(float(value) for value in measured_magnetic_field),
            absolute_pressure_pa=pressure,
            pressure_variance=self.config.barometer_noise_std_pa**2,
        )

    def _gps_measurement_error(self, dt: float) -> np.ndarray:
        config = self.config
        tau = max(config.gps_bias_correlation_time_s, 1.0e-9)
        phi = math.exp(-dt / tau)
        random_walk = np.asarray(config.gps_random_walk_m_sqrt_s, dtype=np.float64)
        bias_sigma = random_walk * math.sqrt(max(tau * (1.0 - phi * phi) * 0.5, 0.0))
        self._gps_bias = phi * self._gps_bias + self._rng.normal(0.0, bias_sigma, size=3)
        white_noise = self._rng.normal(
            0.0,
            np.asarray(config.gps_position_noise_std_m, dtype=np.float64),
            size=3,
        )
        return self._gps_bias + white_noise

    def _local_enu_to_geodetic(self, position_enu: np.ndarray) -> tuple[float, float, float]:
        earth_radius_m = 6_378_137.0
        origin_lat_rad = math.radians(self.config.origin_latitude_deg)
        latitude = self.config.origin_latitude_deg + math.degrees(position_enu[1] / earth_radius_m)
        longitude = self.config.origin_longitude_deg + math.degrees(
            position_enu[0] / (earth_radius_m * max(math.cos(origin_lat_rad), 1.0e-9))
        )
        altitude = self.config.origin_altitude_m + position_enu[2]
        return float(latitude), float(longitude), float(altitude)

    def _barometer(self, local_altitude_m: float, dt: float) -> float:
        config = self.config
        altitude_amsl = config.origin_altitude_m + float(local_altitude_m)
        temperature = max(config.temperature_msl_k - config.lapse_rate_k_per_m * altitude_amsl, 1.0)
        pressure = config.pressure_msl_pa * (temperature / config.temperature_msl_k) ** 5.2561
        self._barometer_drift_pa += config.barometer_drift_pa_per_s * dt
        return float(
            pressure
            + self._barometer_drift_pa
            + self._rng.normal(0.0, config.barometer_noise_std_pa)
        )


def sanitize_ros_component(value: str) -> str:
    component = re.sub(r"[^A-Za-z0-9_]", "_", str(value).strip())
    component = re.sub(r"_+", "_", component).strip("_") or "robot"
    return f"robot_{component}" if component[0].isdigit() else component


def aerial_sensor_topic_names(robot_name: str) -> dict[str, str]:
    component = sanitize_ros_component(robot_name)
    namespace = f"/{component}"
    return {
        "imu": f"{namespace}/sensors/imu",
        "gps": f"{namespace}/sensors/gps",
        "magnetometer": f"{namespace}/sensors/mag",
        "barometer": f"{namespace}/sensors/barometer",
        "camera_image": f"{namespace}/camera/image_raw",
        "camera_info": f"{namespace}/camera/camera_info",
        "lidar": f"{namespace}/lidar/pointcloud",
        "lidar_pointcloud": f"{namespace}/lidar/pointcloud",
    }


def aerial_sensor_specs_from_selection(
    selection_data: Mapping[str, Any] | None,
    possible_agents: Sequence[str],
) -> tuple[AerialSensorRobotSpec, ...]:
    """Resolve independently gated built-in sensors for robots with cameras."""
    if not selection_data:
        return ()
    agents = tuple(str(agent) for agent in possible_agents)
    agent_set = set(agents)
    type_counts: dict[str, int] = {}
    specs: list[AerialSensorRobotSpec] = []
    for index, robot in enumerate(selection_data.get("robots", ())):
        if not isinstance(robot, Mapping):
            continue
        robot_type = str(robot.get("type", "")).strip().lower()
        type_counts[robot_type] = type_counts.get(robot_type, 0) + 1
        if robot_type not in BUILTIN_CAMERA_TYPES:
            continue
        attachments = {
            str(item.get("type", "")).strip().lower()
            for item in robot.get("attachments", ())
            if isinstance(item, Mapping)
        }
        ros_enabled = "ros" in attachments
        camera_enabled = "camera" in attachments
        # MuSHR 改装 RealSense D455 后，D455 即机器人的相机：内置单目相机
        # 不再由 env_builder 合成（见 env_builder 的 has_realsense 分支），
        # 因此 aerial 套件也不应为其建图，图像发布完全由 D455 载荷负责。
        d455_replaces_mushr_camera = (
            robot_type == "mushr_v2" and "realsense_d455" in attachments
        )
        spec_camera = camera_enabled and not d455_replaces_mushr_camera
        default_name = f"{robot_type}_{type_counts[robot_type]}"
        robot_name = (
            default_name
            if default_name in agent_set
            else agents[index]
            if index < len(agents)
            else default_name
        )
        specs.append(
            AerialSensorRobotSpec(
                robot_name=robot_name,
                robot_type=robot_type,
                base_sensors=ros_enabled and robot_type in _BASE_SENSOR_TYPES,
                camera=spec_camera,
                lidar=ros_enabled and robot_type in AERIAL_SENSOR_TYPES,
                camera_mount_link=_CAMERA_MOUNT_LINKS[robot_type],
                lidar_offset=_AERIAL_LIDAR_OFFSETS.get(robot_type, (0.0, 0.0, 0.10)),
            )
        )
    return tuple(specs)


def selection_requires_aerial_camera(selection_data: Mapping[str, Any] | None) -> bool:
    """Return whether the selection needs sensor rendering at app launch.

    Aerial robots always carry their camera and RTX LiDAR resources.  Ground
    robots need this launcher flag when a built-in camera (MuSHR) or the Orsus
    camera publisher is selected.
    """
    if not selection_data:
        return False
    return any(
        isinstance(robot, Mapping)
        and (
            str(robot.get("type", "")).strip().lower() in AERIAL_SENSOR_TYPES
            or any(
                isinstance(item, Mapping)
                and str(item.get("type", "")).strip().lower() == "camera"
                for item in robot.get("attachments", ())
            )
        )
        for robot in selection_data.get("robots", ())
    )


__all__ = [
    "AERIAL_SENSOR_TYPES",
    "AerialSensorModel",
    "AerialSensorModelConfig",
    "AerialSensorReading",
    "AerialSensorRobotSpec",
    "FirstOrderBiasConfig",
    "PEGASUS_AERIAL_TYPES",
    "aerial_sensor_specs_from_selection",
    "aerial_sensor_topic_names",
    "quaternion_wxyz_to_matrix",
    "sanitize_ros_component",
    "selection_requires_aerial_camera",
]
