"""Shared geometry helpers for idealized manipulation."""
from __future__ import annotations
from dataclasses import dataclass
import math
from typing import Any, Sequence


@dataclass(frozen=True)
class CylindricalPose:
    """A base-local target: forward reach, height and offset-plane direction."""

    x_m: float
    z_m: float
    yaw_rad: float

    def __post_init__(self) -> None:
        x_m = _finite_float(self.x_m, "x_m")
        z_m = _finite_float(self.z_m, "z_m")
        yaw_rad = _finite_float(self.yaw_rad, "yaw_rad")
        if x_m < 0.0:
            raise ValueError("x_m must be non-negative")
        object.__setattr__(self, "x_m", x_m)
        object.__setattr__(self, "z_m", z_m)
        object.__setattr__(self, "yaw_rad", (yaw_rad + math.pi) % (2.0 * math.pi) - math.pi)


def _finite_vector(value: Sequence[float], length: int, name: str) -> tuple[float, ...]:
    try:
        value_length = len(value)
    except (TypeError, AttributeError):
        value_length = -1
    if isinstance(value, (str, bytes)) or value_length != length:
        raise ValueError(f"{name} must contain exactly {length} finite values")
    try:
        if any(isinstance(item, bool) for item in value):
            raise ValueError(f"{name} must contain exactly {length} finite values")
    except TypeError:
        raise ValueError(f"{name} must contain exactly {length} finite values")
    try:
        parsed = tuple(float(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain exactly {length} finite values") from exc
    if any(not math.isfinite(item) for item in parsed):
        raise ValueError(f"{name} must contain exactly {length} finite values")
    return parsed


def _finite_float(value: float, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{name} must be finite")
    return parsed


def _positive_steps(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("phase_timeout_steps must be a positive integer")
    return value


def _tensor_values(value: Any, length: int) -> tuple[float, ...]:
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "tolist"):
        value = value.tolist()
    return _finite_vector(value, length, "physics pose")


def _quat_conjugate(quaternion: Sequence[float]) -> tuple[float, float, float, float]:
    w, x, y, z = _finite_vector(quaternion, 4, "quaternion")
    norm_sq = w * w + x * x + y * y + z * z
    if norm_sq <= 1.0e-18:
        raise ValueError("quaternion must be non-zero")
    return (w / norm_sq, -x / norm_sq, -y / norm_sq, -z / norm_sq)


def _quat_multiply(left: Sequence[float], right: Sequence[float]) -> tuple[float, float, float, float]:
    lw, lx, ly, lz = _finite_vector(left, 4, "left quaternion")
    rw, rx, ry, rz = _finite_vector(right, 4, "right quaternion")
    return (
        lw * rw - lx * rx - ly * ry - lz * rz,
        lw * rx + lx * rw + ly * rz - lz * ry,
        lw * ry - lx * rz + ly * rw + lz * rx,
        lw * rz + lx * ry - ly * rx + lz * rw,
    )


def _normalize_quaternion(quaternion: Sequence[float], *, name: str = "quaternion") -> tuple[float, float, float, float]:
    parsed = _finite_vector(quaternion, 4, name)
    length = math.sqrt(sum(value * value for value in parsed))
    if length <= 1.0e-12:
        raise ValueError(f"{name} must be non-zero")
    return tuple(value / length for value in parsed)


def _quat_rotate(quaternion: Sequence[float], vector: Sequence[float]) -> tuple[float, float, float]:
    q = _normalize_quaternion(quaternion, name="quaternion")
    vx, vy, vz = _finite_vector(vector, 3, "vector")
    rotated = _quat_multiply(_quat_multiply(q, (0.0, vx, vy, vz)), _quat_conjugate(q))
    return (rotated[1], rotated[2], rotated[3])


def wrap_angle_rad(angle_rad: float) -> float:
    """Wrap a finite angle to ``[-pi, pi)``."""

    angle = _finite_float(angle_rad, "yaw_rad")
    return (angle + math.pi) % (2.0 * math.pi) - math.pi


def feasible_yaw_joint_target(
    plane_yaw_rad: float, current_joint_rad: float, lower_rad: float, upper_rad: float,
    *, yaw_zero_rad: float = 0.0, yaw_sign: float = 1.0,
) -> float:
    """Choose the nearest equivalent yaw inside the actual joint interval.

    A bounded Z1 shoulder may need the long route through zero. Wrapping its
    error to the shortest angle would instead cross a physical joint stop.
    """
    values = _finite_vector(
        (plane_yaw_rad, current_joint_rad, lower_rad, upper_rad, yaw_zero_rad, yaw_sign),
        6, "yaw joint mapping",
    )
    yaw, current, low, high, zero, sign = values
    if low > high or abs(sign) != 1.0:
        raise ValueError("invalid yaw joint interval or direction")
    raw = (wrap_angle_rad(yaw) - zero) / sign
    period = 2.0 * math.pi
    k_min = math.ceil((low - raw - 1.0e-8) / period)
    k_max = math.floor((high - raw + 1.0e-8) / period)
    if k_min > k_max:
        raise ValueError("target plane yaw is outside the shoulder joint limits")
    options = [min(high, max(low, raw + k * period)) for k in range(k_min, k_max + 1)]
    return min(options, key=lambda q: (abs(q - current), abs(q)))


def normalize_cylindrical_pose(value: CylindricalPose | Sequence[float], *, name: str = "pose") -> CylindricalPose:
    """Validate a pose and normalize its azimuth."""

    if isinstance(value, CylindricalPose):
        raw = (value.x_m, value.z_m, value.yaw_rad)
    else:
        raw = _finite_vector(value, 3, name)
    x_m, z_m, yaw_rad = _finite_vector(raw, 3, name)
    if x_m < 0.0:
        raise ValueError(f"{name}.x_m must be non-negative")
    return CylindricalPose(x_m=x_m, z_m=z_m, yaw_rad=wrap_angle_rad(yaw_rad))


# A descriptive alias is useful to callers that treat command parsing as a
# separate interface boundary.
parse_cylindrical_pose = normalize_cylindrical_pose


def cylindrical_to_local_position(
    pose: CylindricalPose | Sequence[float],
    plane_offset_m: float,
) -> tuple[float, float, float]:
    """Convert a plane pose to a point in the articulation root frame."""

    normalized = normalize_cylindrical_pose(pose)
    offset = _finite_float(plane_offset_m, "plane_offset_m")
    cos_yaw = math.cos(normalized.yaw_rad)
    sin_yaw = math.sin(normalized.yaw_rad)
    return (
        normalized.x_m * cos_yaw - offset * sin_yaw,
        normalized.x_m * sin_yaw + offset * cos_yaw,
        normalized.z_m,
    )


def local_position_to_cylindrical(
    position_base: Sequence[float],
    plane_offset_m: float,
    *,
    fallback_yaw: float = 0.0,
) -> CylindricalPose:
    """Convert a root-local point to the offset-plane representation."""

    px, py, pz = _finite_vector(position_base, 3, "position_base")
    offset = _finite_float(plane_offset_m, "plane_offset_m")
    radius_sq = px * px + py * py - offset * offset
    if radius_sq < -1.0e-8:
        raise ValueError("position is inside the plane-offset exclusion radius")
    x = math.sqrt(max(0.0, radius_sq))
    if x <= 1.0e-9 and abs(offset) <= 1.0e-9:
        yaw = wrap_angle_rad(fallback_yaw)
    else:
        yaw = math.atan2(py, px) - math.atan2(offset, x)
    return CylindricalPose(x, pz, yaw)


def cylindrical_to_world_position(
    pose: CylindricalPose | Sequence[float],
    base_position_world: Sequence[float],
    base_orientation_world_wxyz: Sequence[float],
    plane_offset_m: float,
) -> tuple[float, float, float]:
    """Convert a root-local cylindrical pose to world coordinates.

    ``base_orientation_world_wxyz`` is the manipulator articulation root's
    world orientation as a ``(w, x, y, z)`` quaternion.
    """

    base = _finite_vector(base_position_world, 3, "base_position_world")
    base_orientation_world = _finite_vector(
        base_orientation_world_wxyz, 4, "base_orientation_world_wxyz"
    )
    local = cylindrical_to_local_position(pose, plane_offset_m)
    rotated = _quat_rotate(base_orientation_world, local)
    return tuple(base[index] + rotated[index] for index in range(3))


def _quat_angle_rad(left: Sequence[float], right: Sequence[float]) -> float:
    left_q = _normalize_quaternion(left)
    right_q = _normalize_quaternion(right)
    dot = abs(sum(a * b for a, b in zip(left_q, right_q)))
    return 2.0 * math.acos(min(1.0, dot))


def _quat_slerp(
    start: Sequence[float],
    goal: Sequence[float],
    fraction: float,
) -> tuple[float, float, float, float]:
    """Shortest-arc spherical interpolation between wxyz quaternions."""
    start_q = _normalize_quaternion(start)
    goal_q = _normalize_quaternion(goal)
    dot = sum(a * b for a, b in zip(start_q, goal_q))
    if dot < 0.0:
        goal_q = tuple(-value for value in goal_q)
        dot = -dot
    fraction = min(1.0, max(0.0, float(fraction)))
    if dot >= 1.0 - 1.0e-9:
        blended = tuple(
            start_q[index] + fraction * (goal_q[index] - start_q[index])
            for index in range(4)
        )
        return _normalize_quaternion(blended)
    theta_0 = math.acos(min(1.0, dot))
    sin_theta_0 = math.sin(theta_0)
    weight_start = math.sin((1.0 - fraction) * theta_0) / sin_theta_0
    weight_goal = math.sin(fraction * theta_0) / sin_theta_0
    return _normalize_quaternion(tuple(
        weight_start * start_q[index] + weight_goal * goal_q[index]
        for index in range(4)
    ))


def _shortest_arc_quaternion(
    from_axis: Sequence[float], to_axis: Sequence[float],
) -> tuple[float, float, float, float]:
    """Unit wxyz quaternion rotating unit vector ``from_axis`` onto ``to_axis``."""
    dot = sum(from_axis[index] * to_axis[index] for index in range(3))
    if dot >= 1.0 - 1.0e-12:
        return (1.0, 0.0, 0.0, 0.0)
    if dot <= -1.0 + 1.0e-12:
        # Antiparallel: rotate pi about any axis orthogonal to ``from_axis``.
        seed = (1.0, 0.0, 0.0) if abs(from_axis[0]) < 0.9 else (0.0, 1.0, 0.0)
        ortho = (
            seed[1] * from_axis[2] - seed[2] * from_axis[1],
            seed[2] * from_axis[0] - seed[0] * from_axis[2],
            seed[0] * from_axis[1] - seed[1] * from_axis[0],
        )
        norm = math.sqrt(sum(value * value for value in ortho))
        ortho = tuple(value / norm for value in ortho)
        return (0.0, ortho[0], ortho[1], ortho[2])
    axis = (
        from_axis[1] * to_axis[2] - from_axis[2] * to_axis[1],
        from_axis[2] * to_axis[0] - from_axis[0] * to_axis[2],
        from_axis[0] * to_axis[1] - from_axis[1] * to_axis[0],
    )
    return _normalize_quaternion((1.0 + dot, axis[0], axis[1], axis[2]))


def cartesian_place_contact_pose(
    relative_object_position_ee: Sequence[float],
    current_ee_orientation_wxyz: Sequence[float],
    approach_direction_w: Sequence[float],
    target_object_position_w: Sequence[float],
    *, tool_axis_ee: Sequence[float] = (0.0, 0.0, 1.0),
) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
    """Solve the end-effector contact pose that lands the object origin.

    The outward tool axis (local +Z by default) is swung onto ``-approach`` with the
    shortest rotation from the measured grasp orientation, so the free yaw
    about the approach axis stays as close to the carried pose as possible.
    """
    approach = _finite_vector(approach_direction_w, 3, "approach_direction_w")
    norm = math.sqrt(sum(value * value for value in approach))
    if norm <= 1.0e-9:
        raise ValueError("approach_direction_w must be nonzero")
    approach = tuple(value / norm for value in approach)
    target = _finite_vector(target_object_position_w, 3, "target_object_position_w")
    current = _normalize_quaternion(current_ee_orientation_wxyz, name="current_ee_orientation_wxyz")
    relative = _finite_vector(relative_object_position_ee, 3, "relative_object_position_ee")
    tool_axis = tuple(-value for value in approach)
    current_tool = _quat_rotate(current, tool_axis_ee)
    swing = _shortest_arc_quaternion(current_tool, tool_axis)
    contact_orientation = _normalize_quaternion(
        _quat_multiply(swing, current), name="contact_orientation_wxyz",
    )
    offset = _quat_rotate(contact_orientation, relative)
    contact_position = tuple(target[index] - offset[index] for index in range(3))
    return contact_position, contact_orientation


@dataclass(frozen=True)
class GraspToolFrame:
    """Finger grasp centre relative to the IK body; TCP +Z points outward."""

    position_ee_m: tuple = (0.0, 0.0, 0.0)
    orientation_ee_wxyz: tuple = (1.0, 0.0, 0.0, 0.0)
    open_position_rad: float | None = None
    closed_position_rad: float | None = None
    width_m: float = 0.0
    depth_m: float = 0.0

    def wrist_pose(self, tcp_position_w, tcp_orientation_wxyz):
        orientation = _normalize_quaternion(_quat_multiply(
            tcp_orientation_wxyz, _quat_conjugate(self.orientation_ee_wxyz)))
        offset = _quat_rotate(orientation, self.position_ee_m)
        return tuple(tcp_position_w[i] - offset[i] for i in range(3)), orientation

    def tcp_pose(self, wrist_position_w, wrist_orientation_wxyz):
        offset = _quat_rotate(wrist_orientation_wxyz, self.position_ee_m)
        return (tuple(wrist_position_w[i] + offset[i] for i in range(3)),
                _normalize_quaternion(_quat_multiply(wrist_orientation_wxyz, self.orientation_ee_wxyz)))


def gripper_grasp_tool(model: str, width_m: float = .05, depth_m: float = .05) -> GraspToolFrame:
    """Asset-derived geometry, not a hardware calibration.

    Robotiq pads extend along wrist +Z; Z1 fingers extend along link06 +X.
    Robotiq's aperture curve is from the unscaled mounted asset. Z1's moving
    pad rotates around (0.100, 0, 0), above the fixed pad at z=-0.009 m.
    """
    if model not in ('ur5', 'z1'):
        return GraspToolFrame()
    width = _finite_float(width_m, 'grasp width')
    depth = _finite_float(depth_m, 'grasp depth')
    if not 0.005 <= width <= .083 or not 0.005 <= depth <= .10:
        raise ValueError('object dimensions exceed the calibrated gripper opening')
    if model == 'ur5':
        def gap(q):
            return .01277649 - .08685988 * math.sin(q) + .07432034 * math.cos(q)
        low, high = 0.0, .8
        for _ in range(40):
            middle = (low + high) / 2
            if gap(middle) > width:
                low = middle
            else:
                high = middle
        return GraspToolFrame((0., 0., .15), (0., 1., 0., 0.), 0., (low + high) / 2, width, depth)
    # Stop at the nearest object corner, rather than driving the curved jaw
    # through the object to its empty-gripper mechanical limit.
    # The fixed finger ends at x=.200 m. A centre at .180 m inserts the
    # thick underside too deeply when tilted toward a support plane. Use a
    # tip grip 5 mm behind the end; recompute aperture at the actual corner.
    grasp_x = .195
    lever = grasp_x - depth / 2 - .10
    low, high = -1.5, 0.0
    for _ in range(40):
        middle = (low + high) / 2
        gap = .009 - lever * math.tan(middle) - .009 / math.cos(middle)
        if gap > width:
            low = middle
        else:
            high = middle
    closed = (low + high) / 2
    opened = min(-.8, closed - .25)
    if opened < -1.55:
        raise ValueError('object leaves insufficient Z1 opening clearance')
    half = math.sqrt(.5)
    return GraspToolFrame((grasp_x, 0., -.009 + width / 2), (half, 0., -half, 0.),
                          opened, closed, width, depth)
