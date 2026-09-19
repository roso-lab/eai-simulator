#!/usr/bin/env python3
"""ROS-independent geometry, interpolation, and PointCloud2 helpers."""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from fractions import Fraction
import math
import struct
from typing import Iterable, Sequence


Vector3 = tuple[float, float, float]
Quaternion = tuple[float, float, float, float]
XYZI = tuple[float, float, float, float]


@dataclass(frozen=True)
class Transform:
    """Rigid transform whose translation and xyzw rotation map child into parent."""

    translation: Vector3
    rotation: Quaternion

    def __post_init__(self) -> None:
        if len(self.translation) != 3 or not all(math.isfinite(v) for v in self.translation):
            raise ValueError("transform translation must contain three finite values")
        object.__setattr__(self, "translation", tuple(float(v) for v in self.translation))
        object.__setattr__(self, "rotation", normalize_quaternion(self.rotation))


@dataclass(frozen=True)
class TimedTransform:
    stamp_ns: int
    transform: Transform


@dataclass(frozen=True)
class BufferUpdate:
    accepted: bool
    reset: bool


class SimulationTimestampRateGate:
    """Rate-limit source timestamps on an epoch-anchored rational schedule."""

    _NANOSECONDS_PER_SECOND = 1_000_000_000

    def __init__(self, rate_hz: float) -> None:
        rate = float(rate_hz)
        if not math.isfinite(rate) or rate <= 0.0:
            raise ValueError("rate_hz must be positive and finite")
        fraction = Fraction(str(rate))
        self._rate_numerator = fraction.numerator
        self._period_scale = self._NANOSECONDS_PER_SECOND * fraction.denominator
        self.reset()

    def reset(self) -> None:
        self._anchor_stamp_ns: int | None = None
        self._last_stamp_ns: int | None = None
        self._next_tick = 1

    def should_publish(self, stamp_ns: int) -> bool:
        stamp_ns = int(stamp_ns)
        if self._anchor_stamp_ns is None:
            self._anchor_stamp_ns = stamp_ns
            self._last_stamp_ns = stamp_ns
            return True
        if self._last_stamp_ns is not None and stamp_ns < self._last_stamp_ns:
            raise ValueError("source timestamp moved backward; reset the rate gate for the new epoch")
        self._last_stamp_ns = stamp_ns
        elapsed_ns = stamp_ns - self._anchor_stamp_ns
        scaled_elapsed = elapsed_ns * self._rate_numerator
        if scaled_elapsed < self._next_tick * self._period_scale:
            return False
        self._next_tick = scaled_elapsed // self._period_scale + 1
        return True


@dataclass(frozen=True)
class PointFieldSpec:
    name: str
    offset: int
    datatype: int
    count: int = 1


def normalize_quaternion(value: Sequence[float]) -> Quaternion:
    if len(value) != 4:
        raise ValueError("quaternion must contain four values")
    quaternion = tuple(float(v) for v in value)
    if not all(math.isfinite(v) for v in quaternion):
        raise ValueError("quaternion must contain finite values")
    norm = math.sqrt(sum(v * v for v in quaternion))
    if norm <= 1.0e-15:
        raise ValueError("quaternion must be non-zero")
    return tuple(v / norm for v in quaternion)  # type: ignore[return-value]


IDENTITY_TRANSFORM = Transform((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0))


def quaternion_conjugate(value: Quaternion) -> Quaternion:
    x, y, z, w = value
    return (-x, -y, -z, w)


def quaternion_multiply(left: Quaternion, right: Quaternion) -> Quaternion:
    lx, ly, lz, lw = left
    rx, ry, rz, rw = right
    return normalize_quaternion(
        (
            lw * rx + lx * rw + ly * rz - lz * ry,
            lw * ry - lx * rz + ly * rw + lz * rx,
            lw * rz + lx * ry - ly * rx + lz * rw,
            lw * rw - lx * rx - ly * ry - lz * rz,
        )
    )


def quaternion_from_rpy(roll: float, pitch: float, yaw: float) -> Quaternion:
    cr, sr = math.cos(roll * 0.5), math.sin(roll * 0.5)
    cp, sp = math.cos(pitch * 0.5), math.sin(pitch * 0.5)
    cy, sy = math.cos(yaw * 0.5), math.sin(yaw * 0.5)
    return normalize_quaternion(
        (
            sr * cp * cy - cr * sp * sy,
            cr * sp * cy + sr * cp * sy,
            cr * cp * sy - sr * sp * cy,
            cr * cp * cy + sr * sp * sy,
        )
    )


def rotate_vector(rotation: Quaternion, vector: Sequence[float]) -> Vector3:
    x, y, z = (float(v) for v in vector)
    qx, qy, qz, qw = rotation
    tx = 2.0 * (qy * z - qz * y)
    ty = 2.0 * (qz * x - qx * z)
    tz = 2.0 * (qx * y - qy * x)
    return (
        x + qw * tx + qy * tz - qz * ty,
        y + qw * ty + qz * tx - qx * tz,
        z + qw * tz + qx * ty - qy * tx,
    )


def compose(parent_from_middle: Transform, middle_from_child: Transform) -> Transform:
    rotated = rotate_vector(parent_from_middle.rotation, middle_from_child.translation)
    return Transform(
        tuple(parent_from_middle.translation[i] + rotated[i] for i in range(3)),
        quaternion_multiply(parent_from_middle.rotation, middle_from_child.rotation),
    )


def inverse(transform: Transform) -> Transform:
    rotation = quaternion_conjugate(transform.rotation)
    translation = rotate_vector(rotation, tuple(-v for v in transform.translation))
    return Transform(translation, rotation)


def slerp(left: Quaternion, right: Quaternion, ratio: float) -> Quaternion:
    ratio = min(1.0, max(0.0, float(ratio)))
    dot = sum(left[i] * right[i] for i in range(4))
    if dot < 0.0:
        right = tuple(-v for v in right)  # type: ignore[assignment]
        dot = -dot
    dot = min(1.0, max(-1.0, dot))
    if dot > 0.9995:
        return normalize_quaternion(tuple(left[i] + ratio * (right[i] - left[i]) for i in range(4)))
    angle = math.acos(dot)
    scale = math.sin(angle)
    left_scale = math.sin((1.0 - ratio) * angle) / scale
    right_scale = math.sin(ratio * angle) / scale
    return normalize_quaternion(
        tuple(left_scale * left[i] + right_scale * right[i] for i in range(4))
    )


def interpolate(left: Transform, right: Transform, ratio: float) -> Transform:
    ratio = min(1.0, max(0.0, float(ratio)))
    return Transform(
        tuple(
            left.translation[i] + ratio * (right.translation[i] - left.translation[i])
            for i in range(3)
        ),
        slerp(left.rotation, right.rotation, ratio),
    )


class OdometryBuffer:
    """Bounded monotonic pose buffer supporting exact or bracketed interpolation."""

    def __init__(
        self,
        *,
        duration_sec: float,
        max_interpolation_gap_sec: float,
        backward_jump_threshold_sec: float,
    ) -> None:
        if duration_sec <= 0.0:
            raise ValueError("duration_sec must be positive")
        if max_interpolation_gap_sec <= 0.0:
            raise ValueError("max_interpolation_gap_sec must be positive")
        if backward_jump_threshold_sec < 0.0:
            raise ValueError("backward_jump_threshold_sec cannot be negative")
        self.duration_ns = int(duration_sec * 1_000_000_000)
        self.max_interpolation_gap_ns = int(max_interpolation_gap_sec * 1_000_000_000)
        self.backward_jump_threshold_ns = int(backward_jump_threshold_sec * 1_000_000_000)
        self._samples: list[TimedTransform] = []

    @property
    def newest_stamp_ns(self) -> int | None:
        return self._samples[-1].stamp_ns if self._samples else None

    @property
    def oldest_stamp_ns(self) -> int | None:
        return self._samples[0].stamp_ns if self._samples else None

    def clear(self) -> None:
        self._samples.clear()

    def add(self, stamp_ns: int, transform: Transform) -> BufferUpdate:
        stamp_ns = int(stamp_ns)
        if stamp_ns < 0:
            raise ValueError("timestamp cannot be negative")
        reset = False
        if self._samples:
            newest = self._samples[-1].stamp_ns
            if stamp_ns < newest:
                if newest - stamp_ns > self.backward_jump_threshold_ns:
                    self.clear()
                    reset = True
                else:
                    return BufferUpdate(accepted=False, reset=False)
            elif stamp_ns == newest:
                self._samples[-1] = TimedTransform(stamp_ns, transform)
                return BufferUpdate(accepted=True, reset=False)
        self._samples.append(TimedTransform(stamp_ns, transform))
        cutoff = stamp_ns - self.duration_ns
        first_retained = bisect_left([sample.stamp_ns for sample in self._samples], cutoff)
        if first_retained:
            del self._samples[:first_retained]
        return BufferUpdate(accepted=True, reset=reset)

    def sample(self, stamp_ns: int) -> Transform | None:
        if not self._samples:
            return None
        stamps = [sample.stamp_ns for sample in self._samples]
        index = bisect_left(stamps, int(stamp_ns))
        if index < len(self._samples) and self._samples[index].stamp_ns == stamp_ns:
            return self._samples[index].transform
        if index == 0 or index == len(self._samples):
            return None
        left = self._samples[index - 1]
        right = self._samples[index]
        gap = right.stamp_ns - left.stamp_ns
        if gap <= 0 or gap > self.max_interpolation_gap_ns:
            return None
        return interpolate(left.transform, right.transform, (stamp_ns - left.stamp_ns) / gap)


class RegistrationEngine:
    """Maintain a level-anchored mapping_init origin and derive lidar poses.

    The product contract (mirrors the validated Gazebo external bridge) defines
    ``mapping_init`` as a level frame anchored at the lidar's birth position with
    the base's birth orientation. The mount rotation must therefore NOT be folded
    into the origin: at spawn ``mapping_from_lidar`` equals the mount rotation
    alone, so the raw pitched-sensor cloud comes out level (floor at
    ``-lidar_height``) and the product odometry orientation carries the mount
    pitch that the Docker ``robot_tf_publisher`` inverts. Folding the mount
    rotation into the origin instead yielded an identity transform at spawn —
    the cloud kept its sensor pitch tilt (invisible for pitch=0 mounts, a 20 deg
    tilt for Mid-360) and the Docker base frame came out pitched.
    """

    def __init__(self, buffer: OdometryBuffer, base_from_lidar: Transform) -> None:
        self.buffer = buffer
        self.base_from_lidar = base_from_lidar
        self.origin_odom_from_lidar: Transform | None = None

    def reset(self) -> None:
        self.buffer.clear()
        self.origin_odom_from_lidar = None

    def add_odometry(self, stamp_ns: int, odom_from_base: Transform) -> BufferUpdate:
        update = self.buffer.add(stamp_ns, odom_from_base)
        if update.reset:
            self.origin_odom_from_lidar = None
        if update.accepted and self.origin_odom_from_lidar is None:
            birth_pos = compose(
                odom_from_base,
                Transform(self.base_from_lidar.translation, (0.0, 0.0, 0.0, 1.0)),
            )
            self.origin_odom_from_lidar = Transform(
                birth_pos.translation, odom_from_base.rotation
            )
        return update

    def mapping_from_lidar_at(self, stamp_ns: int) -> Transform | None:
        if self.origin_odom_from_lidar is None:
            return None
        odom_from_base = self.buffer.sample(stamp_ns)
        if odom_from_base is None:
            return None
        odom_from_lidar = compose(odom_from_base, self.base_from_lidar)
        return compose(inverse(self.origin_odom_from_lidar), odom_from_lidar)


_POINT_FIELD_FORMATS = {
    1: "b",   # INT8
    2: "B",   # UINT8
    3: "h",   # INT16
    4: "H",   # UINT16
    5: "i",   # INT32
    6: "I",   # UINT32
    7: "f",   # FLOAT32
    8: "d",   # FLOAT64
}


def _field_reader(field: PointFieldSpec, point_step: int, endian: str):
    if field.count < 1:
        raise ValueError(f"PointCloud2 field {field.name!r} has invalid count {field.count}")
    format_char = _POINT_FIELD_FORMATS.get(field.datatype)
    if format_char is None:
        raise ValueError(f"PointCloud2 field {field.name!r} has unsupported datatype {field.datatype}")
    unpacker = struct.Struct(endian + format_char)
    if field.offset < 0 or field.offset + unpacker.size > point_step:
        raise ValueError(f"PointCloud2 field {field.name!r} exceeds point_step")
    return field.offset, unpacker


def decode_finite_xyzi(
    *,
    data: bytes,
    width: int,
    height: int,
    point_step: int,
    row_step: int,
    is_bigendian: bool,
    fields: Sequence[PointFieldSpec],
    intensity_field: str = "intensity",
    default_intensity: float = 0.0,
) -> list[XYZI]:
    """Decode finite points from an arbitrary numeric PointCloud2 layout."""

    width, height, point_step, row_step = map(int, (width, height, point_step, row_step))
    if width < 0 or height < 0 or point_step <= 0:
        raise ValueError("invalid PointCloud2 dimensions")
    minimum_row_step = width * point_step
    if row_step < minimum_row_step:
        raise ValueError("PointCloud2 row_step is smaller than width * point_step")
    required_size = 0 if height == 0 else (height - 1) * row_step + minimum_row_step
    if len(data) < required_size:
        raise ValueError("PointCloud2 data is shorter than its declared layout")

    by_name = {field.name: field for field in fields}
    missing = [name for name in ("x", "y", "z") if name not in by_name]
    if missing:
        raise ValueError(f"PointCloud2 is missing required fields: {', '.join(missing)}")
    endian = ">" if is_bigendian else "<"
    readers = {name: _field_reader(by_name[name], point_step, endian) for name in ("x", "y", "z")}
    intensity_reader = (
        _field_reader(by_name[intensity_field], point_step, endian)
        if intensity_field and intensity_field in by_name
        else None
    )
    default_intensity = float(default_intensity)
    if not math.isfinite(default_intensity):
        raise ValueError("default_intensity must be finite")

    result: list[XYZI] = []
    for row in range(height):
        row_offset = row * row_step
        for column in range(width):
            base_offset = row_offset + column * point_step
            xyz = tuple(
                float(unpacker.unpack_from(data, base_offset + offset)[0])
                for offset, unpacker in (readers[name] for name in ("x", "y", "z"))
            )
            intensity = (
                float(intensity_reader[1].unpack_from(data, base_offset + intensity_reader[0])[0])
                if intensity_reader is not None
                else default_intensity
            )
            point = (xyz[0], xyz[1], xyz[2], intensity)
            if all(math.isfinite(value) for value in point):
                result.append(point)
    return result


def transform_and_encode_xyzi(points: Iterable[XYZI], parent_from_lidar: Transform) -> tuple[bytes, int]:
    """Transform lidar points and encode dense little-endian float32 XYZI."""

    output = bytearray()
    count = 0
    packer = struct.Struct("<ffff")
    for x, y, z, intensity in points:
        tx, ty, tz = rotate_vector(parent_from_lidar.rotation, (x, y, z))
        transformed = (
            tx + parent_from_lidar.translation[0],
            ty + parent_from_lidar.translation[1],
            tz + parent_from_lidar.translation[2],
            float(intensity),
        )
        if not all(math.isfinite(value) for value in transformed):
            continue
        try:
            output.extend(packer.pack(*transformed))
        except (OverflowError, struct.error):
            continue
        count += 1
    return bytes(output), count


def transform_twist_base_to_lidar(
    linear_base: Sequence[float],
    angular_base: Sequence[float],
    base_from_lidar: Transform,
) -> tuple[Vector3, Vector3]:
    """Move a base-frame twist to the lidar origin and express it in lidar axes."""

    vx, vy, vz = (float(v) for v in linear_base)
    wx, wy, wz = (float(v) for v in angular_base)
    tx, ty, tz = base_from_lidar.translation
    shifted = (
        vx + wy * tz - wz * ty,
        vy + wz * tx - wx * tz,
        vz + wx * ty - wy * tx,
    )
    lidar_from_base_rotation = quaternion_conjugate(base_from_lidar.rotation)
    return (
        rotate_vector(lidar_from_base_rotation, shifted),
        rotate_vector(lidar_from_base_rotation, (wx, wy, wz)),
    )
