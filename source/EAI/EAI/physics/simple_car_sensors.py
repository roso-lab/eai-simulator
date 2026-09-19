# Copyright (c) 2026, EAI Simulator contributors.
# SPDX-License-Identifier: BSD-3-Clause
"""Pure OCTAGON C01 contracts shared by SimpleCar and detachable hosts."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping, Sequence

CARTER_ROBOT_TYPE = "carter"
SIMPLE_CAR_ROBOT_TYPE = "simple_car"
OCTAGON_ATTACHMENT_TYPE = "octagon_aio_c01"
OCTAGON_PROFILE_NAME = "OCTAGON_AIO_C01"
OCTAGON_PROFILE_RELATIVE_PATH = (
    "payloads/sensors/octagon_aio_c01/OCTAGON_AIO_C01.json"
)
OCTAGON_SCAN_RATE_HZ = 30.0
OCTAGON_IMU_RATE_HZ = 200.0
OCTAGON_POINT_RATE_HZ = 200_000
OCTAGON_HORIZONTAL_FOV_DEG = 360.0
OCTAGON_VERTICAL_FOV_DEG = (-15.0, 45.0)
OCTAGON_RANGE_M = (0.05, 50.0)
# X follows the 2026-07-31 board decision (gazebo-sim lidar_offset_x octagon
# default 0.20 and the basic_components robot_tf_publisher octagon fragment):
# trunk centre line, 0.10 m behind the head front edge (x=+0.30).
OCTAGON_MOUNT_BASE_FOOTPRINT_M = (0.2, 0.0, 0.61459)
OCTAGON_MOUNT_RPY_RAD = (0.0, 0.0, 0.0)
OCTAGON_IMU_VENDOR_OFFSET_M = (0.01890, 0.02464, -0.0535)
# The supplied top-view diagram places the IMU on sensor-right. In REP-103
# (+Y left), the default Y therefore changes sign. This remains explicit and
# configurable until the manufacturer confirms the signed transform direction.
OCTAGON_IMU_ROS_OFFSET_M = (0.01890, -0.02464, -0.0535)

# Optional SimpleCar lidar variant. The default keeps the OCTAGON-AIO-C01 contract;
# the "mid360" variant approximates the production Mid-360 guest with the same
# idealized rotary-grid approach the Gazebo overlay uses (10 Hz, 16 x 350 rays,
# 0.1-130 m, -7..53 deg vertical window, 20 deg forward pitch).
SIMPLE_CAR_LIDAR_VARIANT_ENV = "EAI_SIMPLE_CAR_LIDAR_VARIANT"
SIMPLE_CAR_LIDAR_VARIANTS = (OCTAGON_ATTACHMENT_TYPE, "mid360")
MID360_PROFILE_NAME = "MID360"
MID360_PROFILE_RELATIVE_PATH = "payloads/sensors/mid360/MID360.json"
MID360_SCAN_RATE_HZ = 10.0
MID360_REPORT_RATE_HZ = 3_500.0
MID360_EMITTERS = 16
MID360_POINT_RATE_HZ = int(MID360_EMITTERS * MID360_REPORT_RATE_HZ)
MID360_HORIZONTAL_FOV_DEG = 360.0
MID360_VERTICAL_FOV_DEG = (-7.0, 53.0)
MID360_RANGE_M = (0.10, 130.0)
MID360_MOUNT_BASE_FOOTPRINT_M = (0.0, 0.0, 0.61459)
MID360_MOUNT_PITCH_RAD = 0.349066


def resolve_simple_car_lidar_variant(env: Mapping[str, str] | None = None) -> str:
    """Resolve the SimpleCar lidar variant, defaulting to the C01 contract."""
    import os

    source = os.environ if env is None else env
    variant = str(source.get(SIMPLE_CAR_LIDAR_VARIANT_ENV, OCTAGON_ATTACHMENT_TYPE)).strip()
    if not variant:
        variant = OCTAGON_ATTACHMENT_TYPE
    if variant not in SIMPLE_CAR_LIDAR_VARIANTS:
        raise ValueError(
            f"Unsupported SimpleCar lidar variant {variant!r}; "
            f"expected one of {SIMPLE_CAR_LIDAR_VARIANTS}"
        )
    return variant


def simple_car_lidar_profile_relative_path(variant: str) -> str:
    if variant == "mid360":
        return MID360_PROFILE_RELATIVE_PATH
    return OCTAGON_PROFILE_RELATIVE_PATH


def simple_car_lidar_profile_name(variant: str) -> str:
    if variant == "mid360":
        return MID360_PROFILE_NAME
    return OCTAGON_PROFILE_NAME


def simple_car_lidar_scan_rate_hz(variant: str) -> float:
    if variant == "mid360":
        return MID360_SCAN_RATE_HZ
    return OCTAGON_SCAN_RATE_HZ


def simple_car_lidar_mount_pitch_rad(variant: str) -> float:
    if variant == "mid360":
        return MID360_MOUNT_PITCH_RAD
    return 0.0

CAMERA_RESOLUTION = (1920, 1080)
CAMERA_RATE_HZ = 30.0
CAMERA_FOCAL_LENGTH_MM = 3.0
CAMERA_HORIZONTAL_FOV_DEG = 120.0
CAMERA_VERTICAL_FOV_DEG = 65.0
CAMERA_DIAGONAL_FOV_DEG = 144.0
CAMERA_DEFAULT_BASELINE_M = 0.12
CAMERA_DEFAULT_MOUNT_X_M = 0.326
CAMERA_DEFAULT_MOUNT_Z_M = 0.40


def aperture_for_fov(focal_length_mm: float, fov_deg: float) -> float:
    """Return the aperture which preserves a stated angular field of view."""
    return 2.0 * focal_length_mm * math.tan(math.radians(fov_deg) / 2.0)


CAMERA_HORIZONTAL_APERTURE_MM = aperture_for_fov(
    CAMERA_FOCAL_LENGTH_MM, CAMERA_HORIZONTAL_FOV_DEG
)
CAMERA_VERTICAL_APERTURE_MM = aperture_for_fov(
    CAMERA_FOCAL_LENGTH_MM, CAMERA_VERTICAL_FOV_DEG
)


@dataclass(frozen=True)
class OctagonAioC01SensorSpec:
    robot_name: str
    camera: bool
    navigation_io: bool
    lidar_frame: str
    imu_frame: str
    base_frame: str
    robot_type: str = SIMPLE_CAR_ROBOT_TYPE
    # 声明式雷达变体（env JSON attachment "mid360" → mid360；缺省 c01）。
    lidar_variant: str = "octagon_aio_c01"
    # ``None`` lets the runtime resolve the mount from the spawned
    # ``OctagonAioC01`` prim (generic attachment hosts); SimpleCar's built-in
    # suite keeps the fixed ``lidar_link`` frame.
    lidar_mount_relative_path: str | None = "lidar_link"
    # IsaacComputeOdometry must address a rigid body; the articulation root
    # triggers per-step ``getArticulationData`` PhysX errors on GPU. ``None``
    # resolves to the articulation root link (body_names[0]) at runtime.
    odometry_source_relative_path: str | None = "base_footprint"
    camera_left_mount_relative_path: str | None = "camera_left_link"
    camera_right_mount_relative_path: str | None = "camera_right_link"

    @property
    def namespace(self) -> str:
        return self.robot_name


# Compatibility name for the built-in SimpleCar integration.
SimpleCarSensorSpec = OctagonAioC01SensorSpec


def _selection_payload(selection: Any) -> Mapping[str, Any]:
    if isinstance(selection, Mapping):
        return selection
    return {}


# Odometry chassis source per known host: ``IsaacComputeOdometry`` must
# address a rigid body. Pointing it at the articulation ROOT makes the node
# call ``PxDirectGPUAPI::getArticulationData`` inside the physics-step
# callback, which GPU PhysX rejects every step until it stops the simulation.
# Carter's zero-transform Orsus_chassis_link is its payload/base-footprint
# frame; SimpleCar authors a literal base_footprint rigid body. Other hosts
# resolve their articulation root link at runtime (body_names[0]).
_OCTAGON_ODOMETRY_CHILD_FRAME_REL = {
    CARTER_ROBOT_TYPE: "Orsus_chassis_link",
    SIMPLE_CAR_ROBOT_TYPE: "base_footprint",
}
_OCTAGON_ODOMETRY_SOURCE_REL = {
    CARTER_ROBOT_TYPE: "Orsus_chassis_link",
    SIMPLE_CAR_ROBOT_TYPE: "base_footprint",
}


def octagon_aio_c01_sensor_specs_from_selection(
    selection_data: Mapping[str, Any] | None,
    possible_agents: Sequence[str],
) -> dict[str, OctagonAioC01SensorSpec]:
    """Resolve SimpleCar's built-in suite and generic C01 attachment hosts.

    Any robot that selected the ``octagon_aio_c01`` attachment gets a spec;
    its lidar mount and odometry source are resolved from the spawned stage
    at runtime (``lidar_mount_relative_path``/``odometry_source_relative_path``
    stay ``None``).
    """
    payload = _selection_payload(selection_data)
    robots = payload.get("robots", ())
    if not isinstance(robots, Sequence) or isinstance(robots, (str, bytes)):
        return {}
    type_counters: dict[str, int] = {}
    specs: dict[str, OctagonAioC01SensorSpec] = {}
    for robot in robots:
        if not isinstance(robot, Mapping):
            continue
        robot_type = str(robot.get("type", "")).strip().casefold()
        type_counters[robot_type] = type_counters.get(robot_type, 0) + 1
        attachments = robot.get("attachments", ())
        types = {
            str(item.get("type", "")).strip().casefold()
            for item in attachments
            if isinstance(item, Mapping)
        }
        is_builtin = robot_type == SIMPLE_CAR_ROBOT_TYPE
        if not is_builtin and OCTAGON_ATTACHMENT_TYPE not in types:
            continue
        candidates = [
            str(name) for name in possible_agents if str(name).startswith(f"{robot_type}_")
        ]
        occurrence = type_counters[robot_type]
        if occurrence > len(candidates):
            continue
        robot_name = candidates[occurrence - 1]
        base_rel = _OCTAGON_ODOMETRY_CHILD_FRAME_REL.get(robot_type, "")
        common = {
            "robot_name": robot_name,
            "camera": is_builtin and "camera" in types,
            "navigation_io": "navigation_io" in types,
            "lidar_frame": f"{robot_name}/lidar_link",
            "imu_frame": f"{robot_name}/imu_link",
            "base_frame": f"{robot_name}/{base_rel}" if base_rel else robot_name,
            "robot_type": robot_type,
            # attachment 声明优先于环境变量：mid360 是唯一可声明变体，
            # 其余情况（含显式 octagon_aio_c01 声明或缺省）一律 C01。
            "lidar_variant": "mid360" if "mid360" in types else OCTAGON_ATTACHMENT_TYPE,
        }
        if is_builtin:
            specs[robot_name] = OctagonAioC01SensorSpec(**common)
        else:
            specs[robot_name] = OctagonAioC01SensorSpec(
                **common,
                lidar_mount_relative_path=None,
                odometry_source_relative_path=_OCTAGON_ODOMETRY_SOURCE_REL.get(
                    robot_type
                ),
                camera_left_mount_relative_path=None,
                camera_right_mount_relative_path=None,
            )
    return specs


def simple_car_sensor_specs_from_selection(
    selection_data: Mapping[str, Any] | None,
    possible_agents: Sequence[str],
) -> dict[str, OctagonAioC01SensorSpec]:
    """Resolve only the built-in SimpleCar suite for compatibility callers."""
    return {
        name: spec
        for name, spec in octagon_aio_c01_sensor_specs_from_selection(
            selection_data, possible_agents
        ).items()
        if spec.robot_type == SIMPLE_CAR_ROBOT_TYPE
    }


class PeriodicRateGate:
    """Deterministic fractional-rate gate for simulator update loops."""

    def __init__(self, rate_hz: float) -> None:
        if rate_hz <= 0.0:
            raise ValueError("rate_hz must be positive")
        self.rate_hz = float(rate_hz)
        self._phase = 0.0

    def advance(self, dt: float) -> int:
        if dt < 0.0:
            raise ValueError("dt must be non-negative")
        self._phase += float(dt) * self.rate_hz
        count = int(self._phase + 1.0e-12)
        self._phase -= count
        return count

    def reset(self) -> None:
        self._phase = 0.0


__all__ = [
    "CAMERA_DEFAULT_BASELINE_M",
    "CAMERA_DIAGONAL_FOV_DEG",
    "CAMERA_FOCAL_LENGTH_MM",
    "CAMERA_HORIZONTAL_APERTURE_MM",
    "CAMERA_HORIZONTAL_FOV_DEG",
    "CAMERA_RATE_HZ",
    "CAMERA_RESOLUTION",
    "CAMERA_VERTICAL_APERTURE_MM",
    "CAMERA_VERTICAL_FOV_DEG",
    "CARTER_ROBOT_TYPE",
    "SIMPLE_CAR_ROBOT_TYPE",
    "SimpleCarSensorSpec",
    "OCTAGON_ATTACHMENT_TYPE",
    "OCTAGON_HORIZONTAL_FOV_DEG",
    "OCTAGON_IMU_RATE_HZ",
    "OCTAGON_IMU_ROS_OFFSET_M",
    "SIMPLE_CAR_LIDAR_VARIANT_ENV",
    "SIMPLE_CAR_LIDAR_VARIANTS",
    "MID360_PROFILE_NAME",
    "MID360_PROFILE_RELATIVE_PATH",
    "MID360_SCAN_RATE_HZ",
    "MID360_POINT_RATE_HZ",
    "MID360_MOUNT_BASE_FOOTPRINT_M",
    "MID360_MOUNT_PITCH_RAD",
    "resolve_simple_car_lidar_variant",
    "simple_car_lidar_profile_relative_path",
    "simple_car_lidar_profile_name",
    "simple_car_lidar_scan_rate_hz",
    "simple_car_lidar_mount_pitch_rad",
    "OCTAGON_IMU_VENDOR_OFFSET_M",
    "OCTAGON_MOUNT_BASE_FOOTPRINT_M",
    "OCTAGON_MOUNT_RPY_RAD",
    "OCTAGON_POINT_RATE_HZ",
    "OCTAGON_PROFILE_NAME",
    "OCTAGON_PROFILE_RELATIVE_PATH",
    "OCTAGON_RANGE_M",
    "OCTAGON_SCAN_RATE_HZ",
    "OCTAGON_VERTICAL_FOV_DEG",
    "OctagonAioC01SensorSpec",
    "PeriodicRateGate",
    "aperture_for_fov",
    "simple_car_sensor_specs_from_selection",
    "octagon_aio_c01_sensor_specs_from_selection",
]
