"""Idealized offset-plane manipulation skills.

The benchmark runtime deliberately keeps manipulation simple: the end effector
moves in the manipulator articulation root's rotating plane. The shoulder
turns directly; reduced damped-least-squares IK controls reach, height and the
downward tool orientation. Pick and place use a visual follow proxy; the source rigid body is
isolated while it is carried and restored exactly once on release.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from .ik_controller import (
    _physics_step_dt,
    apply_end_effector_pose,
    apply_planar_end_effector_pose,
    hold_gripper_position,
    resolve_end_effector_body_id,
)


DEFAULT_POSITION_TOLERANCE_M = 0.05
DEFAULT_YAW_TOLERANCE_RAD = 0.05
DEFAULT_PHASE_TIMEOUT_STEPS = 300
DEFAULT_ATTACHMENT_SETTLE_DURATION_S = 1.0
DEFAULT_RADIAL_SPEED_M_S = 0.35
DEFAULT_VERTICAL_SPEED_M_S = 0.10
DEFAULT_YAW_SPEED_RAD_S = 1.0
DEFAULT_MAX_DELTA_RAD = 0.10
DEFAULT_DLS_LAMBDA = 0.02
DEFAULT_ATTACHMENT_MODE = "follow"
DEFAULT_TOOL_ORIENTATION_TOLERANCE_RAD = 0.05
DEFAULT_GRIPPER_POSITION_TOLERANCE_RAD = 0.02
DEFAULT_Z1_GRIPPER_OPEN_POSITION_RAD = -0.8
DEFAULT_Z1_GRIPPER_CLOSED_POSITION_RAD = 0.0
DEFAULT_Z1_GRIPPER_COLLISION_BODY_NAMES = ("gripperStator", "gripperMover")


@dataclass(frozen=True)
class PlanarIkSpec:
    """Model-specific joints used by the idealized plane controller.

    ``yaw_joint_name`` is driven directly during ROTATE.  The remaining
    ``ik_joint_names`` are the only columns passed to DLS during radial and
    vertical motion.  ``plane_offset_m`` describes the fixed lateral wrist
    offset in the rotating plane (UR5 has one; Z1 is centered).
    """

    model: str
    yaw_joint_name: str | None
    ik_joint_names: tuple[str, ...]
    fixed_joint_names: tuple[str, ...] = ()
    plane_offset_m: float = 0.0
    yaw_sign: float = 1.0
    yaw_zero_rad: float = 0.0
    tool_orientation_plane_wxyz: tuple[float, float, float, float] | None = None
    gripper_open_position_rad: float | None = None
    gripper_closed_position_rad: float | None = None
    gripper_collision_body_names: tuple[str, ...] = ()
    gripper_position_tolerance_rad: float = DEFAULT_GRIPPER_POSITION_TOLERANCE_RAD

    def __post_init__(self) -> None:
        model = str(self.model).strip().lower()
        if not model:
            raise ValueError("planar IK model cannot be empty")
        yaw_name = None if self.yaw_joint_name is None else str(self.yaw_joint_name).strip()
        ik_names = tuple(str(name).strip() for name in self.ik_joint_names)
        fixed_names = tuple(str(name).strip() for name in self.fixed_joint_names)
        if any(not name for name in ik_names + fixed_names):
            raise ValueError("planar IK joint names cannot be empty")
        if len(set(ik_names + fixed_names + ((yaw_name,) if yaw_name else ()))) != len(
            ik_names + fixed_names + ((yaw_name,) if yaw_name else ())
        ):
            raise ValueError("planar IK joint names must be unique")
        offset = _finite_float(self.plane_offset_m, "plane_offset_m")
        sign = _finite_float(self.yaw_sign, "yaw_sign")
        if abs(sign) <= 1.0e-12:
            raise ValueError("yaw_sign cannot be zero")
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "yaw_joint_name", yaw_name or None)
        object.__setattr__(self, "ik_joint_names", ik_names)
        object.__setattr__(self, "fixed_joint_names", fixed_names)
        object.__setattr__(self, "plane_offset_m", offset)
        object.__setattr__(self, "yaw_sign", 1.0 if sign > 0.0 else -1.0)
        object.__setattr__(self, "yaw_zero_rad", _finite_float(self.yaw_zero_rad, "yaw_zero_rad"))
        if self.tool_orientation_plane_wxyz is not None:
            object.__setattr__(self, "tool_orientation_plane_wxyz", _normalize_quaternion(
                self.tool_orientation_plane_wxyz, name="tool_orientation_plane_wxyz",
            ))
        for field_name in ("gripper_open_position_rad", "gripper_closed_position_rad"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _finite_float(value, field_name))
        collision_names = tuple(str(name).strip() for name in self.gripper_collision_body_names)
        if any(not name for name in collision_names):
            raise ValueError("gripper collision body names cannot be empty")
        tolerance = _finite_float(
            self.gripper_position_tolerance_rad, "gripper_position_tolerance_rad",
        )
        if tolerance <= 0.0:
            raise ValueError("gripper_position_tolerance_rad must be positive")
        object.__setattr__(self, "gripper_collision_body_names", collision_names)
        object.__setattr__(self, "gripper_position_tolerance_rad", tolerance)


def planar_ik_spec_for_model(model_spec: Any) -> PlanarIkSpec:
    """Return the idealized plane-control contract for a model spec.

    The fallback keeps lightweight/custom test models on the existing full
    pose path.  Production UR5 and Z1 specs use explicit reduced joint sets.
    """

    model = str(getattr(model_spec, "model", "")).strip().lower()
    if model in ("ur5", "ur5_bare"):
        return PlanarIkSpec(
            model=model,
            yaw_joint_name="shoulder_pan_joint",
            ik_joint_names=(
                "shoulder_lift_joint", "elbow_joint", "wrist_1_joint",
                "wrist_2_joint", "wrist_3_joint",
            ),
            plane_offset_m=0.10915,
            tool_orientation_plane_wxyz=(0.0, math.sqrt(0.5), -math.sqrt(0.5), 0.0),
        )
    if model == "z1":
        return PlanarIkSpec(
            model=model,
            yaw_joint_name="joint1",
            ik_joint_names=("joint2", "joint3", "joint4", "joint5"),
            fixed_joint_names=("joint6",),
            plane_offset_m=0.0,
            tool_orientation_plane_wxyz=(math.sqrt(0.5), 0.0, math.sqrt(0.5), 0.0),
            gripper_open_position_rad=DEFAULT_Z1_GRIPPER_OPEN_POSITION_RAD,
            gripper_closed_position_rad=DEFAULT_Z1_GRIPPER_CLOSED_POSITION_RAD,
            gripper_collision_body_names=DEFAULT_Z1_GRIPPER_COLLISION_BODY_NAMES,
        )
    names = tuple(str(name) for name in getattr(model_spec, "joint_names", ()))
    return PlanarIkSpec(model=model or "generic", yaw_joint_name=None, ik_joint_names=names)


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


@dataclass(frozen=True)
class _AttributeSnapshot:
    prim_path: str
    attribute_name: str
    value: Any
    had_authored_value: bool


@dataclass(frozen=True)
class _ObjectIsolation:
    object_body_path: str
    proxy_path: str
    object_position_w: tuple[float, float, float]
    object_orientation_wxyz: tuple[float, float, float, float]
    object_scale_w: tuple[float, float, float]
    attributes: tuple[_AttributeSnapshot, ...]


class _FollowIsolationError(RuntimeError):
    def __init__(self, message: str, isolation: _ObjectIsolation) -> None:
        super().__init__(message)
        self.isolation = isolation


@dataclass(frozen=True)
class HeldObject:
    object_body_path: str
    hold_position_w: tuple[float, float, float]
    hold_orientation_wxyz: tuple[float, float, float, float]
    relative_object_position_ee: tuple[float, float, float]
    relative_object_orientation_wxyz: tuple[float, float, float, float]
    proxy_path: str | None = None
    isolation: _ObjectIsolation | None = None
    attachment_mode: str = "follow"


class ManipulationPhase(str, Enum):
    IDLE = "idle"
    ROTATE = "rotate"
    RADIAL_APPROACH = "radial_approach"
    VERTICAL_APPROACH = "vertical_approach"
    GRASP = "grasp"
    LIFT = "lift"
    RETRACT = "retract"
    ATTACHED_HOLD = "attached_hold"
    RELEASE = "release"
    FAILED = "failed"


@dataclass
class RobotManipulationState:
    phase: ManipulationPhase = ManipulationPhase.IDLE
    operation: str | None = None
    target_pose: CylindricalPose | None = None
    recovery_pose: CylindricalPose | None = None
    phase_goal: CylindricalPose | None = None
    phase_axis: str | None = None
    rotation_stage: str = "initial"
    command_pose: CylindricalPose | None = None
    ee_orientation_relative_b_wxyz: tuple[float, float, float, float] | None = None
    initial_plane_yaw_rad: float | None = None
    initial_yaw_joint_rad: float | None = None
    yaw_joint_target_rad: float | None = None
    yaw_joint_goal_rad: float | None = None
    arrival_joint_sample: tuple[float, ...] | None = None
    tolerance_m: float = DEFAULT_POSITION_TOLERANCE_M
    yaw_tolerance_rad: float = DEFAULT_YAW_TOLERANCE_RAD
    phase_timeout_steps: int = DEFAULT_PHASE_TIMEOUT_STEPS
    phase_steps: int = 0
    held: HeldObject | None = None
    object_body_path: str | None = None
    pending_isolation: _ObjectIsolation | None = None
    settle_duration_s: float = DEFAULT_ATTACHMENT_SETTLE_DURATION_S
    settle_elapsed_s: float = 0.0
    release_started: bool = False
    last_error: str | None = None


@dataclass(frozen=True)
class _ManipulatorBinding:
    arm: Any
    model_spec: Any
    ee_body_id: int
    planar_spec: PlanarIkSpec
    yaw_joint_id: int | None
    ik_joint_ids: tuple[int, ...]
    fixed_joint_ids: tuple[int, ...]
    robot_name: str


def _sanitize_path_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", str(value).strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    cleaned = cleaned or "robot"
    return cleaned if not cleaned[0].isdigit() else f"robot_{cleaned}"


def _proxy_scope_for_body(body_path: str) -> str:
    normalized = str(body_path).replace("\\", "/")
    marker = "/envs/"
    marker_index = normalized.find(marker)
    if marker_index >= 0:
        remainder = normalized[marker_index + len(marker):]
        env_name = remainder.split("/", 1)[0]
        if env_name:
            return f"{normalized[:marker_index + len(marker)]}{env_name}/EAI_IdealizedManipulation"
    return "/World/EAI/IdealizedManipulation"


def _follow_object_pose(
    ee_position_w: Sequence[float],
    ee_orientation_wxyz: Sequence[float],
    relative_position_ee: Sequence[float],
    relative_orientation_wxyz: Sequence[float],
) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
    ee_position = _finite_vector(ee_position_w, 3, "ee_position_w")
    ee_orientation = _normalize_quaternion(ee_orientation_wxyz, name="ee_orientation_wxyz")
    relative_position = _finite_vector(relative_position_ee, 3, "relative_position_ee")
    relative_orientation = _normalize_quaternion(relative_orientation_wxyz, name="relative_object_orientation_wxyz")
    rotated = _quat_rotate(ee_orientation, relative_position)
    object_position = tuple(ee_position[index] + rotated[index] for index in range(3))
    object_orientation = _normalize_quaternion(
        _quat_multiply(ee_orientation, relative_orientation),
        name="object_orientation_wxyz",
    )
    return object_position, object_orientation


class IdealizedManipulationRuntime:
    """Advance one simple cylindrical manipulation command per physics step."""

    def __init__(
        self,
        env: Any,
        model_specs: Mapping[str, Any | Sequence[Any]],
        *,
        attachment_mode: str = DEFAULT_ATTACHMENT_MODE,
        radial_speed_m_s: float = DEFAULT_RADIAL_SPEED_M_S,
        vertical_speed_m_s: float = DEFAULT_VERTICAL_SPEED_M_S,
        yaw_speed_rad_s: float = DEFAULT_YAW_SPEED_RAD_S,
        max_delta_rad: float = DEFAULT_MAX_DELTA_RAD,
        dls_lambda: float = DEFAULT_DLS_LAMBDA,
    ) -> None:
        self._env = env
        self._model_specs = self._normalize_model_specs(model_specs)
        mode = str(attachment_mode).strip().lower()
        if mode != "follow":
            raise ValueError("attachment_mode must be 'follow'")
        self._attachment_mode = mode
        self._radial_speed_m_s = _finite_float(radial_speed_m_s, "radial_speed_m_s")
        self._vertical_speed_m_s = _finite_float(vertical_speed_m_s, "vertical_speed_m_s")
        self._yaw_speed_rad_s = _finite_float(yaw_speed_rad_s, "yaw_speed_rad_s")
        self._max_delta_rad = _finite_float(max_delta_rad, "max_delta_rad")
        self._dls_lambda = _finite_float(dls_lambda, "dls_lambda")
        if self._radial_speed_m_s <= 0.0 or self._vertical_speed_m_s <= 0.0 or self._yaw_speed_rad_s <= 0.0:
            raise ValueError("cylindrical motion speeds must be positive")
        if self._max_delta_rad <= 0.0 or self._dls_lambda <= 0.0:
            raise ValueError("IK limits must be positive")
        self._enabled = int(getattr(env, "num_envs", 1)) == 1
        self._states: dict[str, RobotManipulationState] = {}
        self._bindings: dict[str, _ManipulatorBinding] = {}
        self._proxy_sequence = 0
        self._owned_proxy_paths: set[str] = set()
        self._object_owners: dict[str, str] = {}
        self._physics_views: dict[tuple[str, str], tuple[Any | None, Any]] = {}
        self._gripper_collision_snapshots: dict[str, tuple[_AttributeSnapshot, ...]] = {}
        self._closed = False

    @staticmethod
    def _normalize_model_specs(model_specs: Mapping[str, Any | Sequence[Any]]) -> dict[str, tuple[Any, ...]]:
        normalized: dict[str, tuple[Any, ...]] = {}
        for robot_name, value in dict(model_specs).items():
            if all(hasattr(value, name) for name in ("model", "joint_names", "ee_body_names")):
                candidates = (value,)
            elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                candidates = tuple(value)
            else:
                candidates = ()
            candidates = tuple(
                candidate for candidate in candidates
                if all(hasattr(candidate, name) for name in ("model", "joint_names", "ee_body_names"))
            )
            if candidates:
                normalized[str(robot_name)] = candidates
        return normalized

    @property
    def enabled(self) -> bool:
        return self._enabled and int(getattr(self._env, "num_envs", 1)) == 1 and not self._closed

    def state(self, robot_name: str) -> ManipulationPhase:
        return self._states.get(str(robot_name).strip(), RobotManipulationState()).phase

    def last_error(self, robot_name: str) -> str | None:
        return self._states.get(str(robot_name).strip(), RobotManipulationState()).last_error

    def held_object(self, robot_name: str) -> HeldObject | None:
        return self._states.get(str(robot_name).strip(), RobotManipulationState()).held

    def start_move(
        self,
        robot_name: str,
        target_pose: CylindricalPose | Sequence[float],
        *,
        tolerance_m: float = DEFAULT_POSITION_TOLERANCE_M,
        yaw_tolerance_rad: float = DEFAULT_YAW_TOLERANCE_RAD,
        phase_timeout_steps: int = DEFAULT_PHASE_TIMEOUT_STEPS,
    ) -> bool:
        """Move an empty arm through yaw, radius and height in that order.

        Invalid argument values raise ``ValueError``; runtime rejections
        (busy, holding, unsupported environment) return ``False`` and record
        the reason in ``last_error``.
        """

        name = str(robot_name).strip()
        state = self._states.setdefault(name, RobotManipulationState())
        if state.held is not None or state.phase == ManipulationPhase.ATTACHED_HOLD:
            return self._reject(state, "move rejected: a carried object is in ATTACHED_HOLD")
        pose = normalize_cylindrical_pose(target_pose, name="target_pose")
        started = self._begin(
            "move", name, pose, tolerance_m=tolerance_m,
            yaw_tolerance_rad=yaw_tolerance_rad,
            phase_timeout_steps=phase_timeout_steps,
        )
        if not started:
            return False
        current = state.command_pose
        assert current is not None
        self._set_phase(state, ManipulationPhase.ROTATE,
                        CylindricalPose(current.x_m, current.z_m, pose.yaw_rad), "yaw")
        return True

    def start_pick(
        self,
        robot_name: str,
        object_body_path: str,
        grasp_pose: CylindricalPose | Sequence[float],
        *,
        recovery_pose: CylindricalPose | Sequence[float],
        tolerance_m: float = DEFAULT_POSITION_TOLERANCE_M,
        yaw_tolerance_rad: float = DEFAULT_YAW_TOLERANCE_RAD,
        settle_duration_s: float = DEFAULT_ATTACHMENT_SETTLE_DURATION_S,
        phase_timeout_steps: int = DEFAULT_PHASE_TIMEOUT_STEPS,
    ) -> bool:
        """Pick a body, lift above it, retract, then report ATTACHED_HOLD.

        Invalid argument values (malformed poses, absolute-path violations,
        recovery-pose constraint violations) raise ``ValueError``; runtime
        rejections return ``False`` and record the reason in ``last_error``.
        """

        name = str(robot_name).strip()
        state = self._states.setdefault(name, RobotManipulationState())
        if not self.enabled:
            return self._reject(state, "idealized manipulation requires num_envs=1")
        if state.held is not None:
            return self._reject(state, "pick rejected: robot already holds an object")
        if state.phase not in (ManipulationPhase.IDLE, ManipulationPhase.FAILED):
            return self._reject(state, "manipulation command rejected: robot is busy")
        grasp = normalize_cylindrical_pose(grasp_pose, name="grasp_pose")
        recovery = normalize_cylindrical_pose(recovery_pose, name="recovery_pose")
        self._validate_recovery(recovery, grasp)
        object_path = str(object_body_path).strip()
        if not object_path.startswith("/"):
            raise ValueError("object_body_path must be an absolute USD prim path")
        if self._object_owners.get(object_path) not in (None, name):
            return self._reject(state, "pick rejected: object is already held by another robot")
        try:
            stage = self._stage()
            prim = stage.GetPrimAtPath(object_path)
            if not prim or not prim.IsValid():
                return self._reject(state, f"pick rejected: object does not exist: {object_path}")
        except (RuntimeError, AttributeError, TypeError):
            # Lightweight unit-test doubles may not provide a USD stage; the
            # follow isolation step performs the authoritative live check.
            pass
        try:
            object_position, object_orientation = self._rigid_body_pose(object_path)
        except Exception as exc:
            return self._reject(state, f"pick rejected: object pose is unavailable: {exc}")
        started = self._begin(
            "pick", name, grasp, recovery_pose=recovery, object_body_path=object_path,
            tolerance_m=tolerance_m, yaw_tolerance_rad=yaw_tolerance_rad,
            phase_timeout_steps=phase_timeout_steps,
            settle_duration_s=settle_duration_s,
        )
        if not started:
            return False
        try:
            isolation = self._isolate_object_for_follow(name, object_path, object_position, object_orientation)
            state.pending_isolation = isolation
            self._object_owners[object_path] = name
        except Exception as exc:
            retained = getattr(exc, "isolation", None)
            if isinstance(retained, _ObjectIsolation):
                state.pending_isolation = retained
                self._object_owners[object_path] = name
            self._fail(state, f"pick failed while isolating object: {exc}")
            return False
        current = state.command_pose
        assert current is not None
        self._set_phase(state, ManipulationPhase.ROTATE,
                        CylindricalPose(current.x_m, current.z_m, grasp.yaw_rad), "yaw")
        return True

    def start_place(
        self,
        robot_name: str,
        place_pose: CylindricalPose | Sequence[float],
        *,
        recovery_pose: CylindricalPose | Sequence[float] | None = None,
        tolerance_m: float = DEFAULT_POSITION_TOLERANCE_M,
        yaw_tolerance_rad: float = DEFAULT_YAW_TOLERANCE_RAD,
        phase_timeout_steps: int = DEFAULT_PHASE_TIMEOUT_STEPS,
    ) -> bool:
        """Place the held body, release once, retract and return to recovery.

        Invalid argument values raise ``ValueError``; runtime rejections
        return ``False`` and record the reason in ``last_error``.
        """

        name = str(robot_name).strip()
        state = self._states.setdefault(name, RobotManipulationState())
        if not self.enabled:
            return self._reject(state, "idealized manipulation requires num_envs=1")
        if state.held is None:
            return self._reject(state, "place rejected: robot does not hold an object")
        if state.phase != ManipulationPhase.ATTACHED_HOLD:
            return self._reject(state, "place rejected: robot is not in ATTACHED_HOLD")
        target = normalize_cylindrical_pose(place_pose, name="place_pose")
        recovery = state.recovery_pose if recovery_pose is None else normalize_cylindrical_pose(recovery_pose, name="recovery_pose")
        if recovery is None:
            return self._reject(state, "place rejected: no recovery pose is available")
        self._validate_recovery(recovery, target)
        started = self._begin(
            "place", name, target, recovery_pose=recovery,
            object_body_path=state.held.object_body_path,
            tolerance_m=tolerance_m, yaw_tolerance_rad=yaw_tolerance_rad,
            phase_timeout_steps=phase_timeout_steps,
        )
        if not started:
            return False
        current = state.command_pose
        assert current is not None
        self._set_phase(state, ManipulationPhase.ROTATE,
                        CylindricalPose(current.x_m, current.z_m, target.yaw_rad), "yaw")
        return True

    @staticmethod
    def _validate_recovery(recovery: CylindricalPose, target: CylindricalPose) -> None:
        if recovery.z_m <= target.z_m:
            raise ValueError("recovery_pose.z_m must be greater than the target z_m")
        if recovery.x_m > target.x_m:
            raise ValueError("recovery_pose.x_m must be less than or equal to the target x_m")

    def _begin(
        self,
        operation: str,
        robot_name: str,
        target_pose: CylindricalPose,
        *,
        recovery_pose: CylindricalPose | None = None,
        object_body_path: str | None = None,
        tolerance_m: float,
        yaw_tolerance_rad: float,
        phase_timeout_steps: int,
        settle_duration_s: float = DEFAULT_ATTACHMENT_SETTLE_DURATION_S,
    ) -> bool:
        if not robot_name:
            raise ValueError("robot_name cannot be empty")
        state = self._states.setdefault(robot_name, RobotManipulationState())
        if not self.enabled:
            return self._reject(state, "idealized manipulation requires num_envs=1")
        can_start_place = operation == "place" and state.phase == ManipulationPhase.ATTACHED_HOLD and state.held is not None
        if state.phase not in (ManipulationPhase.IDLE, ManipulationPhase.FAILED) and not can_start_place:
            return self._reject(state, "manipulation command rejected: robot is busy")
        tolerance = _finite_float(tolerance_m, "tolerance_m")
        yaw_tolerance = _finite_float(yaw_tolerance_rad, "yaw_tolerance_rad")
        timeout = _positive_steps(phase_timeout_steps)
        settle = _finite_float(settle_duration_s, "settle_duration_s")
        if tolerance <= 0.0 or yaw_tolerance <= 0.0:
            raise ValueError("tolerances must be positive")
        if settle < 0.0:
            raise ValueError("settle_duration_s must be non-negative")
        try:
            binding = self._binding(robot_name)
            ee_position, ee_orientation = self._end_effector_pose(binding)
            base_position, base_orientation = self._base_pose(binding)
        except Exception as exc:
            return self._reject(state, f"{operation} rejected: {exc}")
        if not can_start_place:
            cache = getattr(self._env, "_eai_planar_ik_states", {})
            key = (robot_name, str(binding.model_spec.model))
            owner = cache.get(key)
            if owner is not None:
                ids, _ = binding.arm.find_joints(list(binding.model_spec.joint_names), preserve_order=True)
                current_targets = binding.arm.data.joint_pos_target[:, list(ids)]
                if bool(((current_targets - owner.targets).abs() > 1.0e-6).any()):
                    # A normal controller may have repositioned the empty arm
                    # during IDLE. Capture its targets when ownership resumes.
                    cache.pop(key, None)
        # While holding, the command reference is the recovered local pose.
        # Re-seeding place from the measured IK pose would reintroduce its
        # tracking error as a sudden z jump at the start of the place motion.
        try:
            if operation == "place" and state.phase == ManipulationPhase.ATTACHED_HOLD and state.command_pose is not None:
                initial = state.command_pose
            else:
                initial = self._measured_plane_pose(binding, ee_position)
        except (ValueError, RuntimeError) as exc:
            return self._reject(state, f"{operation} rejected: {exc}")
        plane_reference = binding.planar_spec.tool_orientation_plane_wxyz
        if plane_reference is None:
            relative_orientation = _normalize_quaternion(
                _quat_multiply(_quat_conjugate(base_orientation), ee_orientation),
                name="ee_orientation_relative_b_wxyz",
            )
        else:
            yaw_quat = (math.cos(initial.yaw_rad / 2), 0.0, 0.0, math.sin(initial.yaw_rad / 2))
            relative_orientation = _quat_multiply(yaw_quat, plane_reference)
        yaw_id = binding.yaw_joint_id
        yaw_joint = None if yaw_id is None else float(binding.arm.data.joint_pos[0, yaw_id])
        if can_start_place and state.yaw_joint_target_rad is not None:
            yaw_joint = state.yaw_joint_target_rad
        if yaw_id is not None:
            limits = binding.arm.data.soft_joint_pos_limits[0, yaw_id]
            try:
                for pose in (target_pose, recovery_pose):
                    if pose is not None:
                        feasible_yaw_joint_target(
                            pose.yaw_rad, yaw_joint, float(limits[0]), float(limits[1]),
                            yaw_zero_rad=binding.planar_spec.yaw_zero_rad,
                            yaw_sign=binding.planar_spec.yaw_sign,
                        )
            except ValueError as exc:
                return self._reject(state, f"{operation} rejected: {exc}")
        state.phase = ManipulationPhase.IDLE
        state.operation = operation
        state.target_pose = target_pose
        state.recovery_pose = recovery_pose
        state.phase_goal = None
        state.phase_axis = None
        state.rotation_stage = "initial"
        state.command_pose = initial
        state.ee_orientation_relative_b_wxyz = relative_orientation
        state.initial_plane_yaw_rad = initial.yaw_rad
        state.initial_yaw_joint_rad = yaw_joint
        state.yaw_joint_target_rad = yaw_joint
        state.yaw_joint_goal_rad = None
        state.arrival_joint_sample = None
        state.tolerance_m = tolerance
        state.yaw_tolerance_rad = yaw_tolerance
        state.phase_timeout_steps = timeout
        state.phase_steps = 0
        state.object_body_path = object_body_path
        state.settle_duration_s = settle
        state.settle_elapsed_s = 0.0
        state.release_started = False
        state.last_error = None
        state.pending_isolation = None
        return True

    @staticmethod
    def _reject(state: RobotManipulationState, reason: str) -> bool:
        state.last_error = reason
        return False

    @staticmethod
    def _set_phase(state: RobotManipulationState, phase: ManipulationPhase, goal: CylindricalPose, axis: str | None) -> None:
        state.phase = phase
        state.phase_goal = normalize_cylindrical_pose(goal, name="phase_goal")
        state.phase_axis = axis
        state.phase_steps = 0
        state.settle_elapsed_s = 0.0
        state.yaw_joint_goal_rad = None
        state.arrival_joint_sample = None

    def _physics_step_duration_s(self) -> float:
        dt = _physics_step_dt(self._env)
        if dt is None or dt <= 0.0:
            return 1.0 / 60.0
        return float(dt)

    @staticmethod
    def _base_pose(binding: _ManipulatorBinding) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
        data = binding.arm.data
        root_pos = getattr(data, "root_pos_w", None)
        root_quat = getattr(data, "root_quat_w", None)
        if root_pos is not None and root_quat is not None:
            return _tensor_values(root_pos[0], 3), _normalize_quaternion(_tensor_values(root_quat[0], 4), name="base_orientation_world_wxyz")
        return _tensor_values(data.body_pos_w[0, 0], 3), _normalize_quaternion(_tensor_values(data.body_quat_w[0, 0], 4), name="base_orientation_world_wxyz")

    @staticmethod
    def _cylindrical_from_world(
        position_w: Sequence[float],
        base_position_w: Sequence[float],
        base_orientation_wxyz: Sequence[float],
        *,
        fallback_yaw: float = 0.0,
        plane_offset_m: float = 0.0,
    ) -> CylindricalPose:
        position = _finite_vector(position_w, 3, "position_w")
        base = _finite_vector(base_position_w, 3, "base_position_w")
        local = _quat_rotate(
            _quat_conjugate(base_orientation_wxyz),
            tuple(position[i] - base[i] for i in range(3)),
        )
        return local_position_to_cylindrical(
            local,
            plane_offset_m,
            fallback_yaw=fallback_yaw,
        )

    def _measured_plane_pose(
        self, binding: _ManipulatorBinding, position_w: Sequence[float],
    ) -> CylindricalPose:
        base_pos, base_quat = self._base_pose(binding)
        if binding.yaw_joint_id is None:
            return self._cylindrical_from_world(position_w, base_pos, base_quat)
        spec = binding.planar_spec
        q = float(binding.arm.data.joint_pos[0, binding.yaw_joint_id])
        yaw = spec.yaw_zero_rad + spec.yaw_sign * q
        local = _quat_rotate(_quat_conjugate(base_quat), tuple(position_w[i] - base_pos[i] for i in range(3)))
        x = math.cos(yaw) * local[0] + math.sin(yaw) * local[1]
        if x < -1.0e-6:
            raise RuntimeError("arm is folded behind the positive plane; prepare it before starting a skill")
        return CylindricalPose(max(0.0, x), local[2], yaw)

    def _step_yaw_joint(self, binding: _ManipulatorBinding, state: RobotManipulationState, dt: float) -> None:
        current = state.yaw_joint_target_rad
        goal = state.phase_goal
        command = state.command_pose
        if current is None or goal is None or command is None or binding.yaw_joint_id is None:
            raise RuntimeError("yaw stage is missing its shoulder target")
        spec = binding.planar_spec
        if state.yaw_joint_goal_rad is None:
            limits = binding.arm.data.soft_joint_pos_limits[0, binding.yaw_joint_id]
            state.yaw_joint_goal_rad = feasible_yaw_joint_target(
                goal.yaw_rad, current, float(limits[0]), float(limits[1]),
                yaw_zero_rad=spec.yaw_zero_rad, yaw_sign=spec.yaw_sign,
            )
        delta = state.yaw_joint_goal_rad - current
        velocity_limit = float(binding.arm.data.soft_joint_vel_limits[0, binding.yaw_joint_id])
        step = min(abs(delta), self._yaw_speed_rad_s * dt, abs(velocity_limit) * dt)
        current += math.copysign(step, delta)
        state.yaw_joint_target_rad = current
        state.command_pose = CylindricalPose(
            command.x_m, command.z_m, spec.yaw_zero_rad + spec.yaw_sign * current,
        )

    @staticmethod
    def _step_pose(current: CylindricalPose, goal: CylindricalPose, axis: str, dt: float, *, radial_speed: float, vertical_speed: float, yaw_speed: float) -> CylindricalPose:
        if axis == "x":
            delta = goal.x_m - current.x_m
            step = min(abs(delta), radial_speed * dt)
            x = goal.x_m if abs(delta) <= step else current.x_m + math.copysign(step, delta)
            return CylindricalPose(x, current.z_m, current.yaw_rad)
        if axis == "z":
            delta = goal.z_m - current.z_m
            step = min(abs(delta), vertical_speed * dt)
            z = goal.z_m if abs(delta) <= step else current.z_m + math.copysign(step, delta)
            return CylindricalPose(current.x_m, z, current.yaw_rad)
        delta = wrap_angle_rad(goal.yaw_rad - current.yaw_rad)
        step = min(abs(delta), yaw_speed * dt)
        yaw = goal.yaw_rad if abs(delta) <= step else wrap_angle_rad(current.yaw_rad + math.copysign(step, delta))
        return CylindricalPose(current.x_m, current.z_m, yaw)

    def _phase_reached(
        self, state: RobotManipulationState, measured: CylindricalPose,
        binding: _ManipulatorBinding,
    ) -> bool:
        goal = state.phase_goal
        command = state.command_pose
        if goal is None or command is None:
            return False
        # Arrival tolerance applies to the measured arm, not the rate-limited
        # reference. Finish the reference before changing phase; otherwise the
        # remaining tolerance distance becomes an instantaneous target jump at
        # GRASP/RELEASE (or the next motion axis).
        if (
            abs(command.x_m - goal.x_m) > 1.0e-12
            or abs(command.z_m - goal.z_m) > 1.0e-12
            or abs(wrap_angle_rad(command.yaw_rad - goal.yaw_rad)) > 1.0e-12
        ):
            return False
        actual_yaw = measured.yaw_rad
        if binding.yaw_joint_id is None and measured.x_m <= 1.0e-9:
            actual_yaw = command.yaw_rad
        if not (
            abs(measured.x_m - goal.x_m) <= state.tolerance_m
            and abs(measured.z_m - goal.z_m) <= state.tolerance_m
            and abs(wrap_angle_rad(actual_yaw - goal.yaw_rad)) <= state.yaw_tolerance_rad
        ):
            return False
        if binding.yaw_joint_id is not None:
            # Check the unwrapped joint target as well: equivalent angles are
            # insufficient when the shoulder has a bounded physical interval.
            actual = float(binding.arm.data.joint_pos[0, binding.yaw_joint_id])
            if abs(actual - state.yaw_joint_target_rad) > state.yaw_tolerance_rad:
                return False
            if state.phase_axis == "yaw" and abs(state.yaw_joint_target_rad - state.yaw_joint_goal_rad) > 1.0e-9:
                return False
            for joint_id in binding.fixed_joint_ids:
                if abs(float(binding.arm.data.joint_pos[0, joint_id])) > 0.02:
                    return False
            # Freeze the non-yaw targets only after the position drives have
            # settled. Crossing the pose tolerance while still moving can
            # otherwise leave a drifting wrist throughout the next ROTATE.
            ids = (binding.yaw_joint_id,) + binding.ik_joint_ids + binding.fixed_joint_ids
            sample = tuple(float(binding.arm.data.joint_pos[0, i]) for i in ids)
            owner = getattr(self._env, "_eai_planar_ik_states", {}).get(
                (binding.robot_name, str(binding.model_spec.model)),
            )
            if owner is not None:
                sample += _tensor_values(owner.targets[0], len(binding.model_spec.joint_names))
            previous = state.arrival_joint_sample
            state.arrival_joint_sample = sample
            if previous is None or len(previous) != len(sample):
                return False
            step_limit = 0.02 * self._physics_step_duration_s()
            if any(abs(a - b) > step_limit for a, b in zip(sample, previous)):
                return False
        target_position, target_orientation = self._target_world_pose(binding, state)
        position, orientation = self._end_effector_pose(binding)
        if math.dist(position, target_position) > state.tolerance_m:
            return False
        dot = abs(sum(a * b for a, b in zip(_normalize_quaternion(orientation), target_orientation)))
        return 2.0 * math.acos(min(1.0, dot)) <= DEFAULT_TOOL_ORIENTATION_TOLERANCE_RAD

    def _target_world_pose(self, binding: _ManipulatorBinding, state: RobotManipulationState) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
        command = state.command_pose
        relative = state.ee_orientation_relative_b_wxyz
        if command is None or relative is None:
            raise RuntimeError("manipulation command is missing its pose state")
        base_position, base_orientation = self._base_pose(binding)
        target_position = cylindrical_to_world_position(
            command,
            base_position,
            base_orientation,
            binding.planar_spec.plane_offset_m,
        )
        initial_yaw = state.initial_plane_yaw_rad
        if initial_yaw is None:
            initial_yaw = command.yaw_rad
        yaw_delta = wrap_angle_rad(command.yaw_rad - initial_yaw)
        yaw_quat = (
            math.cos(0.5 * yaw_delta),
            0.0,
            0.0,
            math.sin(0.5 * yaw_delta),
        )
        target_relative = _normalize_quaternion(
            _quat_multiply(yaw_quat, relative),
            name="target_ee_relative_orientation_wxyz",
        )
        target_orientation = _normalize_quaternion(
            _quat_multiply(base_orientation, target_relative),
            name="target_ee_orientation_wxyz",
        )
        return target_position, target_orientation

    def run_post_apply(self, env: Any | None = None) -> None:
        if env is not None:
            self._env = env
        if not self.enabled:
            return
        dt = self._physics_step_duration_s()
        for robot_name, state in tuple(self._states.items()):
            if state.phase in (ManipulationPhase.IDLE, ManipulationPhase.FAILED):
                binding = self._bindings.get(robot_name)
                if binding is not None and binding.model_spec.model == "z1":
                    try:
                        self._hold_fixed_joints(robot_name, binding, state)
                        self._hold_gripper(binding, state)
                    except Exception as exc:
                        self._fail(state, f"gripper hold failed: {exc}")
                continue
            try:
                binding = self._binding(robot_name)
                if state.phase == ManipulationPhase.ATTACHED_HOLD:
                    self._drive_state(robot_name, binding, state)
                    if state.held is not None:
                        self._sync_follow_proxy(state.held, binding)
                    continue
                if state.command_pose is None or state.phase_goal is None:
                    self._fail(state, "manipulation phase is missing cylindrical pose state")
                    continue
                if state.phase_axis == "yaw" and binding.yaw_joint_id is not None:
                    self._step_yaw_joint(binding, state, dt)
                elif state.phase_axis in ("x", "z", "yaw"):
                    state.command_pose = self._step_pose(
                        state.command_pose, state.phase_goal, state.phase_axis, dt,
                        radial_speed=self._radial_speed_m_s,
                        vertical_speed=self._vertical_speed_m_s,
                        yaw_speed=self._yaw_speed_rad_s,
                    )
                self._drive_state(robot_name, binding, state)
                if state.held is not None:
                    self._sync_follow_proxy(state.held, binding)
                measured_position, _ = self._end_effector_pose(binding)
                measured = self._measured_plane_pose(binding, measured_position)
                state.phase_steps += 1
                if state.phase == ManipulationPhase.GRASP:
                    if self._phase_reached(state, measured, binding) and self._gripper_at_target(binding, state):
                        state.settle_elapsed_s += dt
                        if state.settle_elapsed_s + 1.0e-12 >= state.settle_duration_s:
                            self._attach_after_grasp(robot_name, binding, state)
                    elif state.settle_elapsed_s > 0.0:
                        state.settle_elapsed_s = 0.0
                elif state.phase == ManipulationPhase.RELEASE:
                    if self._phase_reached(state, measured, binding) and self._gripper_at_target(binding, state):
                        state.settle_elapsed_s += dt
                        if state.settle_elapsed_s + 1.0e-12 >= state.settle_duration_s:
                            self._release_after_place(robot_name, binding, state)
                    elif state.settle_elapsed_s > 0.0:
                        state.settle_elapsed_s = 0.0
                elif self._phase_reached(state, measured, binding):
                    self._advance_phase(robot_name, binding, state)
                if state.phase not in (ManipulationPhase.IDLE, ManipulationPhase.ATTACHED_HOLD, ManipulationPhase.FAILED) and state.phase_steps >= state.phase_timeout_steps:
                    self._fail(state, f"{state.operation or 'manipulation'} timed out in {state.phase.value}")
            except Exception as exc:
                self._fail(state, f"{state.operation or 'manipulation'} failed: {exc}")

    def _drive_state(self, robot_name: str, binding: _ManipulatorBinding, state: RobotManipulationState) -> None:
        target_position, target_orientation = self._target_world_pose(binding, state)
        if binding.yaw_joint_id is None:
            self._drive(robot_name, binding, target_position, target_orientation, state)
            return
        max_delta, damping = self._ik_parameters(robot_name, binding)
        if not apply_planar_end_effector_pose(
            self._env, binding.arm, robot_name, binding.model_spec,
            target_position, target_orientation,
            plane_yaw_rad=state.command_pose.yaw_rad,
            yaw_joint_id=binding.yaw_joint_id,
            yaw_joint_target_rad=state.yaw_joint_target_rad,
            ik_joint_ids=binding.ik_joint_ids,
            fixed_joint_ids=binding.fixed_joint_ids,
            rotate_only=state.phase == ManipulationPhase.ROTATE,
            max_delta_rad=max_delta, dls_lambda=damping,
        ):
            raise RuntimeError("planar IK is unavailable for the selected manipulator")
        self._hold_gripper(binding, state)

    def _ik_parameters(self, robot_name: str, binding: _ManipulatorBinding) -> tuple[float, float]:
        for auxiliary in getattr(self._env, "_auxiliary_controllers", {}).get(robot_name, ()):
            if getattr(auxiliary, "model_spec", None) == binding.model_spec:
                return (float(getattr(auxiliary, "max_delta_rad", self._max_delta_rad)),
                        float(getattr(auxiliary, "dls_lambda", self._dls_lambda)))
        return self._max_delta_rad, self._dls_lambda

    def _hold_fixed_joints(self, robot_name: str, binding: _ManipulatorBinding, state: RobotManipulationState) -> None:
        del state
        if not binding.fixed_joint_ids:
            return
        import torch

        ids = list(binding.fixed_joint_ids)
        data = binding.arm.data
        owner = getattr(self._env, "_eai_planar_ik_states", {}).get((robot_name, str(binding.model_spec.model)))
        arm_ids, _ = binding.arm.find_joints(list(binding.model_spec.joint_names), preserve_order=True)
        columns = [list(arm_ids).index(i) for i in ids]
        previous = data.joint_pos[:, ids] if owner is None else owner.targets[:, columns]
        max_delta, _ = self._ik_parameters(robot_name, binding)
        step = torch.clamp(data.soft_joint_vel_limits[:, ids].abs() * self._physics_step_duration_s(), max=max_delta)
        targets = previous + torch.clamp(-previous, -step, step)
        limits = data.soft_joint_pos_limits[:, ids]
        targets = torch.maximum(torch.minimum(targets, limits[..., 1]), limits[..., 0])
        if owner is not None:
            owner.targets[:, columns] = targets.detach().clone()
        # In IDLE only the fixed wrist is owned. Leave ordinary ROS/auxiliary
        # targets for the shoulder and IK joints available to their controller.
        binding.arm.set_joint_position_target(targets, joint_ids=ids)

    def _advance_phase(self, robot_name: str, binding: _ManipulatorBinding, state: RobotManipulationState) -> None:
        del robot_name, binding
        target = state.target_pose
        recovery = state.recovery_pose
        current = state.command_pose
        if target is None or current is None:
            self._fail(state, "phase transition is missing target pose")
            return
        if state.operation == "move":
            if state.phase == ManipulationPhase.ROTATE:
                self._set_phase(state, ManipulationPhase.RADIAL_APPROACH, CylindricalPose(target.x_m, current.z_m, target.yaw_rad), "x")
            elif state.phase == ManipulationPhase.RADIAL_APPROACH:
                self._set_phase(state, ManipulationPhase.VERTICAL_APPROACH, CylindricalPose(target.x_m, target.z_m, target.yaw_rad), "z")
            elif state.phase == ManipulationPhase.VERTICAL_APPROACH:
                self._finish_idle(state)
            return
        if recovery is None:
            self._fail(state, "manipulation phase is missing recovery pose")
            return
        if state.operation == "pick":
            if state.phase == ManipulationPhase.ROTATE and state.rotation_stage == "initial":
                self._set_phase(state, ManipulationPhase.RADIAL_APPROACH, CylindricalPose(target.x_m, current.z_m, target.yaw_rad), "x")
            elif state.phase == ManipulationPhase.RADIAL_APPROACH:
                self._set_phase(state, ManipulationPhase.VERTICAL_APPROACH, CylindricalPose(target.x_m, target.z_m, target.yaw_rad), "z")
            elif state.phase == ManipulationPhase.VERTICAL_APPROACH:
                self._set_phase(state, ManipulationPhase.GRASP, CylindricalPose(target.x_m, target.z_m, target.yaw_rad), None)
            elif state.phase == ManipulationPhase.LIFT:
                self._set_phase(state, ManipulationPhase.RETRACT, CylindricalPose(recovery.x_m, recovery.z_m, target.yaw_rad), "x")
            elif state.phase == ManipulationPhase.RETRACT:
                state.rotation_stage = "recovery"
                self._set_phase(state, ManipulationPhase.ROTATE, CylindricalPose(recovery.x_m, recovery.z_m, recovery.yaw_rad), "yaw")
            elif state.phase == ManipulationPhase.ROTATE and state.rotation_stage == "recovery":
                state.phase = ManipulationPhase.ATTACHED_HOLD
                state.phase_goal = recovery
                state.phase_axis = None
                state.phase_steps = 0
            return
        # place
        if state.phase == ManipulationPhase.ROTATE and state.rotation_stage == "initial":
            self._set_phase(state, ManipulationPhase.RADIAL_APPROACH, CylindricalPose(target.x_m, current.z_m, target.yaw_rad), "x")
        elif state.phase == ManipulationPhase.RADIAL_APPROACH:
            self._set_phase(state, ManipulationPhase.VERTICAL_APPROACH, CylindricalPose(target.x_m, target.z_m, target.yaw_rad), "z")
        elif state.phase == ManipulationPhase.VERTICAL_APPROACH:
            self._set_phase(state, ManipulationPhase.RELEASE, CylindricalPose(target.x_m, target.z_m, target.yaw_rad), None)
        elif state.phase == ManipulationPhase.LIFT:
            self._set_phase(state, ManipulationPhase.RETRACT, CylindricalPose(recovery.x_m, recovery.z_m, target.yaw_rad), "x")
        elif state.phase == ManipulationPhase.RETRACT:
            state.rotation_stage = "recovery"
            self._set_phase(state, ManipulationPhase.ROTATE, CylindricalPose(recovery.x_m, recovery.z_m, recovery.yaw_rad), "yaw")
        elif state.phase == ManipulationPhase.ROTATE and state.rotation_stage == "recovery":
            self._finish_idle(state)

    def _attach_after_grasp(self, robot_name: str, binding: _ManipulatorBinding, state: RobotManipulationState) -> None:
        if state.object_body_path is None or state.pending_isolation is None:
            self._fail(state, "pick cannot attach without isolated object state")
            return
        ee_position, ee_orientation = self._end_effector_pose(binding)
        held = self._create_follow_attachment(
            state.object_body_path, ee_position, ee_orientation, state.pending_isolation
        )
        state.held = held
        state.pending_isolation = None
        self._object_owners[held.object_body_path] = robot_name
        target = state.target_pose
        recovery = state.recovery_pose
        if target is None or recovery is None:
            self._fail(state, "pick cannot lift without target and recovery pose")
            return
        self._set_phase(state, ManipulationPhase.LIFT, CylindricalPose(target.x_m, recovery.z_m, target.yaw_rad), "z")

    def _release_after_place(self, robot_name: str, binding: _ManipulatorBinding, state: RobotManipulationState) -> None:
        held = state.held
        recovery = state.recovery_pose
        target = state.target_pose
        if held is None or recovery is None or target is None:
            self._fail(state, "place cannot release without held and recovery state")
            return
        state.release_started = True
        self._release_follow_attachment(held, binding)
        state.release_started = False
        if self._object_owners.get(held.object_body_path) == robot_name:
            self._object_owners.pop(held.object_body_path, None)
        state.held = None
        self._set_phase(state, ManipulationPhase.LIFT, CylindricalPose(target.x_m, recovery.z_m, target.yaw_rad), "z")

    @staticmethod
    def _finish_idle(state: RobotManipulationState) -> None:
        # Both reachable call sites (move completion, place completion after
        # RELEASE cleared the held object) arrive with nothing carried; a held
        # object left in IDLE would freeze its proxy updates.
        assert state.held is None
        state.phase = ManipulationPhase.IDLE
        state.operation = None
        state.phase_goal = None
        state.phase_axis = None
        state.phase_steps = 0
        state.target_pose = None
        state.recovery_pose = None
        state.object_body_path = None
        state.ee_orientation_relative_b_wxyz = None
        state.settle_elapsed_s = 0.0
        state.release_started = False
        state.last_error = None

    def _drive(
        self,
        robot_name: str,
        binding: _ManipulatorBinding,
        target_position_w: Sequence[float],
        target_orientation_wxyz: Sequence[float],
        state: RobotManipulationState,
    ) -> None:
        applied = apply_end_effector_pose(
            self._env, binding.arm, robot_name, binding.model_spec,
            target_position_w, target_orientation_wxyz,
            max_delta_rad=self._max_delta_rad,
            dls_lambda=self._dls_lambda,
            target_state_key=f"idealized_manipulation:{robot_name}:{getattr(binding.model_spec, 'model', 'manipulator')}",
        )
        if not applied:
            raise RuntimeError("differential IK is unavailable for the selected manipulator")
        self._hold_gripper(binding, state)

    @staticmethod
    def _gripper_target(binding: _ManipulatorBinding, state: RobotManipulationState) -> float | None:
        if str(getattr(binding.model_spec, "model", "")).strip().lower() != "z1":
            return None
        if not getattr(binding.model_spec, "gripper_joint_name", None):
            return None
        spec = binding.planar_spec
        open_position = spec.gripper_open_position_rad
        closed_position = spec.gripper_closed_position_rad
        if open_position is None:
            open_position = DEFAULT_Z1_GRIPPER_OPEN_POSITION_RAD
        if closed_position is None:
            closed_position = DEFAULT_Z1_GRIPPER_CLOSED_POSITION_RAD
        # Keep the fingers closed after the logical grasp and throughout the
        # carrying/place approach. Opening is performed only in RELEASE and
        # remains the post-release idle target.
        phase = state.phase
        if phase == ManipulationPhase.RELEASE:
            return open_position
        if state.operation == "pick" and phase in (
            ManipulationPhase.GRASP,
            ManipulationPhase.LIFT,
            ManipulationPhase.RETRACT,
            ManipulationPhase.ATTACHED_HOLD,
        ):
            return closed_position
        if state.operation == "place" and phase in (
            ManipulationPhase.ROTATE,
            ManipulationPhase.RADIAL_APPROACH,
            ManipulationPhase.VERTICAL_APPROACH,
        ):
            return closed_position
        return open_position

    def _gripper_at_target(self, binding: _ManipulatorBinding, state: RobotManipulationState) -> bool:
        target = self._gripper_target(binding, state)
        if target is None:
            return True
        gripper_name = getattr(binding.model_spec, "gripper_joint_name", None)
        if not gripper_name:
            return False
        try:
            gripper_ids, _ = binding.arm.find_joints([str(gripper_name)], preserve_order=True)
            if not gripper_ids:
                return False
            measured = getattr(getattr(binding.arm, "data", None), "joint_pos", None)
            if measured is None:
                # Lightweight controller doubles do not expose measured joint
                # state; their hold_gripper_position hook remains authoritative.
                return True
            measured_value = float(measured[0, int(gripper_ids[0])])
        except (AttributeError, IndexError, TypeError, ValueError):
            return False
        return abs(measured_value - target) <= binding.planar_spec.gripper_position_tolerance_rad

    def _hold_gripper(self, binding: _ManipulatorBinding, state: RobotManipulationState) -> None:
        gripper_position = self._gripper_target(binding, state)
        if not hold_gripper_position(
            binding.arm, binding.model_spec, position_rad=gripper_position,
        ):
            raise RuntimeError("configured gripper joint is unavailable")

    def _create_follow_attachment(
        self,
        object_body_path: str,
        hold_position_w: Sequence[float],
        hold_orientation_wxyz: Sequence[float],
        isolation: _ObjectIsolation,
    ) -> HeldObject:
        if isolation.object_body_path != object_body_path or isolation.proxy_path not in self._owned_proxy_paths:
            raise RuntimeError("follow attachment is missing its owned proxy")
        ee_position = _finite_vector(hold_position_w, 3, "hold_position_w")
        ee_orientation = _normalize_quaternion(hold_orientation_wxyz, name="hold_orientation_wxyz")
        delta = tuple(isolation.object_position_w[i] - ee_position[i] for i in range(3))
        relative_position = _quat_rotate(_quat_conjugate(ee_orientation), delta)
        relative_orientation = _normalize_quaternion(
            _quat_multiply(_quat_conjugate(ee_orientation), isolation.object_orientation_wxyz),
            name="relative_object_orientation_wxyz",
        )
        held = HeldObject(
            object_body_path=object_body_path,
            hold_position_w=tuple(ee_position),
            hold_orientation_wxyz=ee_orientation,
            relative_object_position_ee=relative_position,
            relative_object_orientation_wxyz=relative_orientation,
            proxy_path=isolation.proxy_path,
            isolation=isolation,
            attachment_mode="follow",
        )
        self._sync_follow_proxy_pose(held, ee_position, ee_orientation)
        return held

    def _sync_follow_proxy_pose(
        self,
        held: HeldObject,
        ee_position_w: Sequence[float],
        ee_orientation_wxyz: Sequence[float],
    ) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
        if held.proxy_path is None or held.isolation is None or held.proxy_path not in self._owned_proxy_paths:
            raise RuntimeError("follow attachment state is incomplete")
        object_position, object_orientation = _follow_object_pose(
            ee_position_w, ee_orientation_wxyz,
            held.relative_object_position_ee,
            held.relative_object_orientation_wxyz,
        )
        self._set_proxy_object_pose_values(self._stage(), held.proxy_path, object_position, object_orientation, held.isolation.object_scale_w)
        return object_position, object_orientation

    def _sync_follow_proxy(self, held: HeldObject, binding: _ManipulatorBinding) -> None:
        ee_position, ee_orientation = self._end_effector_pose(binding)
        self._sync_follow_proxy_pose(held, ee_position, ee_orientation)

    def _release_follow_attachment(self, held: HeldObject, binding: _ManipulatorBinding) -> None:
        if held.isolation is None:
            raise RuntimeError("follow attachment is missing isolation state")
        ee_position, ee_orientation = self._end_effector_pose(binding)
        object_position, object_orientation = self._sync_follow_proxy_pose(held, ee_position, ee_orientation)
        self._write_rigid_body_pose(held.object_body_path, object_position, object_orientation)
        self._restore_object_isolation(held.isolation, remove_proxy=True)
        try:
            self._zero_rigid_body_velocity(held.object_body_path)
        except Exception:
            pass

    def _cleanup_object(self, robot_name: str, state: RobotManipulationState) -> None:
        held = state.held
        if held is not None:
            # Whether or not RELEASE was reached, the source body gets its
            # temporary isolation restored: place already wrote the final pose,
            # while a reset or failed command never moved the source body.
            try:
                if held.isolation is not None:
                    self._restore_object_isolation(held.isolation, remove_proxy=True)
            except Exception:
                if held.isolation is not None:
                    try:
                        self._restore_object_isolation(held.isolation, remove_proxy=True)
                    except Exception:
                        self._owned_proxy_paths.discard(held.isolation.proxy_path)
            self._object_owners.pop(held.object_body_path, None)
            state.held = None
            state.release_started = False
        pending = state.pending_isolation
        if pending is not None:
            try:
                self._restore_object_isolation(pending, remove_proxy=True)
            except Exception:
                self._owned_proxy_paths.discard(pending.proxy_path)
            self._object_owners.pop(pending.object_body_path, None)
            state.pending_isolation = None

    def _fail(self, state: RobotManipulationState, reason: str) -> None:
        name = next((robot for robot, candidate in self._states.items() if candidate is state), "")
        self._cleanup_object(name, state)
        self._restore_gripper_collisions(name)
        state.phase = ManipulationPhase.FAILED
        state.operation = None
        state.phase_goal = None
        state.phase_axis = None
        state.target_pose = None
        state.recovery_pose = None
        state.command_pose = None
        state.object_body_path = None
        state.ee_orientation_relative_b_wxyz = None
        state.phase_steps = 0
        state.settle_elapsed_s = 0.0
        state.release_started = False
        state.last_error = str(reason)


    def _binding(self, robot_name: str) -> _ManipulatorBinding:
        cached = self._bindings.get(robot_name)
        if cached is not None:
            self._disable_gripper_collisions(robot_name, cached)
            return cached
        articulations = getattr(getattr(self._env, "scene", None), "articulations", {})
        arm = articulations.get(f"{robot_name}_arm")
        if arm is None:
            raise RuntimeError(f"manipulator articulation '{robot_name}_arm' was not found")

        candidates = list(self._model_specs.get(robot_name, ()))
        for auxiliary in getattr(self._env, "_auxiliary_controllers", {}).get(robot_name, ()):
            model_spec = getattr(auxiliary, "model_spec", None)
            if model_spec is not None and model_spec not in candidates:
                candidates.append(model_spec)
        for model_spec in candidates:
            try:
                joint_ids, _ = arm.find_joints(
                    list(model_spec.joint_names), preserve_order=True
                )
                if len(joint_ids) != len(model_spec.joint_names):
                    continue
                ee_id = resolve_end_effector_body_id(arm, model_spec.ee_body_names)
                if ee_id is None:
                    continue
            except (AttributeError, TypeError, ValueError):
                continue
            planar_spec = getattr(model_spec, "idealized_planar_ik_spec", None) or planar_ik_spec_for_model(model_spec)
            def resolve(names):
                ids, _ = arm.find_joints(list(names), preserve_order=True)
                if len(ids) != len(names):
                    raise RuntimeError(f"missing planar joints: {names}")
                return tuple(int(i) for i in ids)
            yaw_ids = resolve((planar_spec.yaw_joint_name,)) if planar_spec.yaw_joint_name else ()
            binding = _ManipulatorBinding(
                arm, model_spec, ee_id, planar_spec,
                yaw_ids[0] if yaw_ids else None,
                resolve(planar_spec.ik_joint_names), resolve(planar_spec.fixed_joint_names),
                robot_name,
            )
            self._disable_gripper_collisions(robot_name, binding)
            self._bindings[robot_name] = binding
            return binding
        raise RuntimeError(f"no compatible manipulator model spec for '{robot_name}'")

    @staticmethod
    def _end_effector_pose(
        binding: _ManipulatorBinding,
    ) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
        data = binding.arm.data
        return (
            _tensor_values(data.body_pos_w[0, binding.ee_body_id], 3),
            _tensor_values(data.body_quat_w[0, binding.ee_body_id], 4),
        )

    def _stage(self) -> Any:
        simulation = getattr(self._env, "sim", None)
        for name in ("get_current_stage", "get_initial_stage"):
            getter = getattr(simulation, name, None)
            if callable(getter):
                stage = getter()
                if stage is not None:
                    return stage
        try:
            import omni.usd

            stage = omni.usd.get_context().get_stage()
        except Exception as exc:
            raise RuntimeError("USD stage is unavailable") from exc
        if stage is None:
            raise RuntimeError("USD stage is unavailable")
        return stage

    def _gripper_body_prim_paths(self, binding: _ManipulatorBinding) -> tuple[str, ...]:
        """Resolve model gripper link paths without assuming a fixed world prefix."""

        names = binding.planar_spec.gripper_collision_body_names
        if not names:
            return ()
        paths: list[str] = []
        link_paths = getattr(getattr(binding.arm, "root_physx_view", None), "link_paths", None)
        if link_paths is not None:
            try:
                first_instance = link_paths[0] if len(link_paths) else ()
                if isinstance(first_instance, (str, bytes)):
                    first_instance = link_paths
                for name in names:
                    suffix = "/" + str(name)
                    for path in first_instance:
                        text = str(path)
                        if text == str(name) or text.endswith(suffix):
                            paths.append(text)
                            break
            except (IndexError, TypeError, ValueError):
                pass
        root_path = getattr(getattr(binding.arm, "cfg", None), "prim_path", None)
        if root_path:
            root_text = str(root_path).replace("{ENV_REGEX_NS}", "")
            while "*" in root_text:
                root_text = root_text.replace("*", "")
            root_text = root_text.rstrip("/")
            paths.extend(f"{root_text}/{name}" for name in names)
        # The binding's stage is authoritative; discard unresolved guesses and
        # preserve a stable order for snapshots and restoration.
        stage = self._stage()
        resolved: list[str] = []
        for path in paths:
            prim = stage.GetPrimAtPath(path)
            if prim and prim.IsValid() and path not in resolved:
                resolved.append(path)
        return tuple(resolved)

    def _disable_gripper_collisions(self, robot_name: str, binding: _ManipulatorBinding) -> None:
        """Disable Z1 gripper collision geometry for this skill instance."""

        if not binding.planar_spec.gripper_collision_body_names:
            return
        if robot_name in self._gripper_collision_snapshots:
            return
        try:
            from pxr import Usd, UsdPhysics

            stage = self._stage()
            snapshots: list[_AttributeSnapshot] = []
            for body_path in self._gripper_body_prim_paths(binding):
                for prim in Usd.PrimRange(stage.GetPrimAtPath(body_path)):
                    if not prim.HasAPI(UsdPhysics.CollisionAPI):
                        continue
                    collision = UsdPhysics.CollisionAPI(prim)
                    attr = collision.GetCollisionEnabledAttr()
                    if not attr or not attr.IsValid():
                        attr = collision.CreateCollisionEnabledAttr()
                    snapshots.append(self._snapshot_and_set_attribute(attr, False))
            if snapshots:
                self._gripper_collision_snapshots[robot_name] = tuple(snapshots)
                self._flush_physx_transforms()
        except (ImportError, RuntimeError, AttributeError, TypeError, ValueError):
            # Unit-test doubles and non-USD callers may not expose a stage;
            # the production articulation path retries only when a real stage
            # is available during binding.
            return

    def _restore_gripper_collisions(self, robot_name: str) -> None:
        snapshots = self._gripper_collision_snapshots.get(robot_name)
        if not snapshots:
            return
        try:
            self._restore_attribute_snapshots(self._stage(), snapshots)
            self._flush_physx_transforms()
            self._gripper_collision_snapshots.pop(robot_name, None)
        except Exception:
            # Keep the snapshot for a later reset/close retry.  Setting all
            # collision attributes to False here would silently fail to
            # restore a prim whose original collision was enabled.
            return

    def _isolate_object_for_follow(
        self,
        robot_name: str,
        object_body_path: str,
        object_position_w: Sequence[float],
        object_orientation_wxyz: Sequence[float],
    ) -> _ObjectIsolation:
        from pxr import Gf, Usd, UsdGeom, UsdPhysics

        try:
            from pxr import PhysxSchema
        except ImportError:
            PhysxSchema = None

        stage = self._stage()
        object_prim = stage.GetPrimAtPath(object_body_path)
        if not object_prim or not object_prim.IsValid():
            raise RuntimeError(f"object rigid body does not exist: {object_body_path}")
        if not object_prim.HasAPI(UsdPhysics.RigidBodyAPI):
            raise RuntimeError(
                f"follow mode requires UsdPhysics.RigidBodyAPI: {object_body_path}"
            )

        object_position = _finite_vector(object_position_w, 3, "object_position_w")
        object_orientation = _normalize_quaternion(
            object_orientation_wxyz,
            name="object_orientation_wxyz",
        )
        object_world = UsdGeom.XformCache().GetLocalToWorldTransform(object_prim)
        object_scale = Gf.Transform(object_world).GetScale()
        scale = tuple(float(object_scale[index]) for index in range(3))
        # Clear motion while the actor is still dynamic. PhysX rejects
        # velocity writes after a body has been switched to kinematic.
        self._zero_rigid_body_velocity(object_body_path)
        proxy_path = self._create_visual_proxy(
            stage,
            robot_name,
            object_body_path,
            object_position,
            object_orientation,
            scale,
        )

        snapshots: list[_AttributeSnapshot] = []
        try:
            for prim in Usd.PrimRange(object_prim):
                if prim.HasAPI(UsdPhysics.CollisionAPI):
                    collision = UsdPhysics.CollisionAPI(prim)
                    attr = collision.GetCollisionEnabledAttr()
                    if not attr or not attr.IsValid():
                        attr = collision.CreateCollisionEnabledAttr()
                    snapshots.append(self._snapshot_and_set_attribute(attr, False))
                # Removing a live rigid actor (rigidBodyEnabled=False) can
                # invalidate Isaac Lab's shared tensor simulation view.  Keep
                # the actor registered, but remove contact, gravity, and
                # velocity so it is simulation-inert while hidden.
                if (
                    PhysxSchema is not None
                    and prim.HasAPI(PhysxSchema.PhysxRigidBodyAPI)
                ):
                    physx_rigid_body = PhysxSchema.PhysxRigidBodyAPI(prim)
                    attr = physx_rigid_body.GetDisableGravityAttr()
                    if not attr or not attr.IsValid():
                        attr = physx_rigid_body.CreateDisableGravityAttr()
                    snapshots.append(self._snapshot_and_set_attribute(attr, True))
                if prim.HasAPI(UsdPhysics.RigidBodyAPI):
                    rigid_body = UsdPhysics.RigidBodyAPI(prim)
                    attr = rigid_body.GetKinematicEnabledAttr()
                    if not attr or not attr.IsValid():
                        attr = rigid_body.CreateKinematicEnabledAttr()
                    snapshots.append(self._snapshot_and_set_attribute(attr, True))

            imageable = UsdGeom.Imageable(object_prim)
            if not imageable or not imageable.GetPrim().IsValid():
                raise RuntimeError(f"object cannot be hidden: {object_body_path}")
            visibility = imageable.GetVisibilityAttr()
            snapshots.append(
                self._snapshot_and_set_attribute(
                    visibility,
                    UsdGeom.Tokens.invisible,
                )
            )
            # The referenced visual root receives an explicit visible opinion
            # so hiding the source prim cannot make the proxy disappear.
            proxy_visual = stage.GetPrimAtPath(f"{proxy_path}/Visual")
            UsdGeom.Imageable(proxy_visual).MakeVisible()
            self._flush_physx_transforms()
        except Exception as isolation_exc:
            partial_isolation = _ObjectIsolation(
                object_body_path=object_body_path,
                proxy_path=proxy_path,
                object_position_w=object_position,
                object_orientation_wxyz=object_orientation,
                object_scale_w=scale,
                attributes=tuple(snapshots),
            )
            try:
                self._restore_object_isolation(partial_isolation, remove_proxy=True)
            except Exception as cleanup_exc:
                raise _FollowIsolationError(
                    f"{isolation_exc}; rollback failed: {cleanup_exc}",
                    partial_isolation,
                ) from isolation_exc
            raise

        return _ObjectIsolation(
            object_body_path=object_body_path,
            proxy_path=proxy_path,
            object_position_w=object_position,
            object_orientation_wxyz=object_orientation,
            object_scale_w=scale,
            attributes=tuple(snapshots),
        )

    @staticmethod
    def _snapshot_and_set_attribute(attr: Any, value: Any) -> _AttributeSnapshot:
        spec = attr.GetStage().GetEditTarget().GetSpecForScenePath(attr.GetPath())
        had_local_default = bool(spec is not None and spec.HasInfo("default"))
        snapshot = _AttributeSnapshot(
            prim_path=str(attr.GetPrimPath()),
            attribute_name=str(attr.GetName()),
            value=spec.GetInfo("default") if had_local_default else None,
            had_authored_value=had_local_default,
        )
        if not attr.Set(value):
            raise RuntimeError(f"failed to set USD attribute {attr.GetPath()}")
        return snapshot

    @staticmethod
    def _restore_attribute_snapshots(
        stage: Any,
        snapshots: Sequence[_AttributeSnapshot],
    ) -> None:
        errors: list[str] = []
        for snapshot in reversed(tuple(snapshots)):
            try:
                prim = stage.GetPrimAtPath(snapshot.prim_path)
                attr = prim.GetAttribute(snapshot.attribute_name) if prim else None
                if not attr or not attr.IsValid():
                    raise RuntimeError(
                        f"attribute disappeared: {snapshot.prim_path}.{snapshot.attribute_name}"
                    )
                if snapshot.had_authored_value:
                    if not attr.Set(snapshot.value):
                        raise RuntimeError("Set returned false")
                else:
                    attr.ClearDefault()
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            raise RuntimeError("; ".join(errors))

    @staticmethod
    def _apply_isolated_attribute_values(
        stage: Any,
        snapshots: Sequence[_AttributeSnapshot],
    ) -> None:
        from pxr import UsdGeom

        for snapshot in snapshots:
            prim = stage.GetPrimAtPath(snapshot.prim_path)
            attr = prim.GetAttribute(snapshot.attribute_name) if prim else None
            if not attr or not attr.IsValid():
                continue
            if snapshot.attribute_name == "visibility":
                value = UsdGeom.Tokens.invisible
            elif snapshot.attribute_name.endswith("disableGravity"):
                value = True
            elif snapshot.attribute_name.endswith("kinematicEnabled"):
                value = True
            else:
                value = False
            attr.Set(value)

    def _restore_object_isolation(
        self,
        isolation: _ObjectIsolation,
        *,
        remove_proxy: bool,
    ) -> None:
        stage = self._stage()
        try:
            self._restore_attribute_snapshots(stage, isolation.attributes)
        except Exception:
            self._apply_isolated_attribute_values(stage, isolation.attributes)
            self._flush_physx_transforms()
            raise
        self._flush_physx_transforms()
        if not remove_proxy:
            return
        try:
            self._remove_proxy(isolation.proxy_path)
        except Exception:
            # Keep the original isolated when cleanup is incomplete, so a
            # still-live proxy cannot overlap a re-enabled rigid body.
            self._apply_isolated_attribute_values(stage, isolation.attributes)
            self._flush_physx_transforms()
            raise

    def _create_visual_proxy(
        self,
        stage: Any,
        robot_name: str,
        object_body_path: str,
        object_position_w: Sequence[float],
        object_orientation_wxyz: Sequence[float],
        object_scale_w: Sequence[float],
    ) -> str:
        from pxr import Sdf, Usd, UsdGeom

        object_prim = stage.GetPrimAtPath(object_body_path)
        scope = f"{_proxy_scope_for_body(object_body_path)}/VisualFollow"
        self._define_scope_chain(stage, scope)
        component = _sanitize_path_component(robot_name)
        while True:
            self._proxy_sequence += 1
            proxy_path = f"{scope}/{component}_object_{self._proxy_sequence}"
            existing = stage.GetPrimAtPath(proxy_path)
            if not existing or not existing.IsValid():
                break

        try:
            wrapper = UsdGeom.Xform.Define(stage, proxy_path).GetPrim()
            visual_path = f"{proxy_path}/Visual"
            visual = stage.DefinePrim(visual_path)
            referenced = bool(
                visual.GetReferences().AddInternalReference(object_prim.GetPath())
            )
            if not referenced:
                referenced = self._add_external_visual_reference(visual, object_prim, Sdf)
            if not referenced:
                raise RuntimeError(
                    f"unable to reference the object's real visual: {object_body_path}"
                )
            visual = stage.GetPrimAtPath(visual_path)
            if not visual or not visual.IsValid():
                raise RuntimeError("visual proxy reference did not compose")
            # Expand instanceable assets so physics APIs on referenced
            # descendants can be overridden on this proxy only.
            if visual.IsInstanceable():
                visual.SetInstanceable(False)
            self._strip_proxy_physics(visual)
            has_visual = any(
                prim.IsA(UsdGeom.Gprim) or prim.IsA(UsdGeom.PointInstancer)
                for prim in Usd.PrimRange(visual)
                if prim.IsActive()
            )
            if not has_visual:
                raise RuntimeError(
                    f"referenced object contains no renderable geometry: {object_body_path}"
                )
            self._set_proxy_object_pose_values(
                stage,
                proxy_path,
                object_position_w,
                object_orientation_wxyz,
                object_scale_w,
            )
            if not wrapper or not wrapper.IsValid():
                raise RuntimeError("visual proxy wrapper is invalid")
        except Exception:
            prim = stage.GetPrimAtPath(proxy_path)
            if prim and prim.IsValid():
                stage.RemovePrim(proxy_path)
            raise
        self._owned_proxy_paths.add(proxy_path)
        return proxy_path

    @staticmethod
    def _add_external_visual_reference(visual: Any, object_prim: Any, sdf: Any) -> bool:
        for prim_spec in object_prim.GetPrimStack():
            candidates = []
            candidates.extend(prim_spec.referenceList.GetAddedOrExplicitItems())
            candidates.extend(prim_spec.payloadList.GetAddedOrExplicitItems())
            for item in candidates:
                if not item.assetPath:
                    continue
                asset_path = prim_spec.layer.ComputeAbsolutePath(item.assetPath)
                prim_path = item.primPath
                if not prim_path or prim_path == sdf.Path.emptyPath:
                    prim_path = sdf.Path.emptyPath
                try:
                    return bool(
                        visual.GetReferences().AddReference(
                            assetPath=asset_path,
                            primPath=prim_path,
                        )
                    )
                except Exception:
                    continue
        return False

    @staticmethod
    def _strip_proxy_physics(proxy_visual: Any) -> None:
        from pxr import Usd, UsdPhysics

        try:
            from pxr import PhysxSchema
        except ImportError:
            PhysxSchema = None

        api_classes = [
            UsdPhysics.RigidBodyAPI,
            UsdPhysics.CollisionAPI,
            UsdPhysics.MeshCollisionAPI,
            UsdPhysics.MassAPI,
            UsdPhysics.ArticulationRootAPI,
        ]
        if PhysxSchema is not None:
            for name in (
                "PhysxRigidBodyAPI",
                "PhysxArticulationAPI",
                "PhysxCollisionAPI",
            ):
                api_class = getattr(PhysxSchema, name, None)
                if api_class is not None:
                    api_classes.append(api_class)

        joints = []
        prims = list(Usd.PrimRange(proxy_visual))
        for prim in prims:
            if prim.IsA(UsdPhysics.Joint):
                joint = UsdPhysics.Joint(prim)
                joint.CreateJointEnabledAttr().Set(False)
                joints.append(prim)
            for api_class in api_classes:
                if prim.HasAPI(api_class):
                    prim.RemoveAPI(api_class)
        for joint_prim in joints:
            joint_prim.SetActive(False)

        for prim in Usd.PrimRange(proxy_visual):
            if prim.IsA(UsdPhysics.Joint):
                raise RuntimeError(f"visual proxy retained a joint: {prim.GetPath()}")
            for api_class in api_classes:
                if prim.HasAPI(api_class):
                    raise RuntimeError(
                        f"visual proxy retained {api_class.__name__}: {prim.GetPath()}"
                    )

    @staticmethod
    def _set_proxy_object_pose_values(
        stage: Any,
        proxy_path: str,
        object_position_w: Sequence[float],
        object_orientation_wxyz: Sequence[float],
        object_scale_w: Sequence[float],
    ) -> None:
        from pxr import Gf, UsdGeom

        wrapper = stage.GetPrimAtPath(proxy_path)
        visual = stage.GetPrimAtPath(f"{proxy_path}/Visual")
        if not wrapper or not wrapper.IsValid() or not visual or not visual.IsValid():
            raise RuntimeError(f"visual proxy is unavailable: {proxy_path}")
        position = _finite_vector(object_position_w, 3, "object_position_w")
        orientation = _normalize_quaternion(
            object_orientation_wxyz,
            name="object_orientation_wxyz",
        )
        scale = _finite_vector(object_scale_w, 3, "object_scale_w")

        desired = Gf.Transform()
        desired.SetTranslation(Gf.Vec3d(*position))
        desired.SetRotation(
            Gf.Rotation(Gf.Quatd(orientation[0], Gf.Vec3d(*orientation[1:])))
        )
        desired.SetScale(Gf.Vec3d(*scale))
        desired_world = desired.GetMatrix()

        visual_xform = UsdGeom.Xformable(visual)
        source_local = (
            visual_xform.GetLocalTransformation()
            if visual_xform and visual_xform.GetPrim().IsValid()
            else Gf.Matrix4d(1.0)
        )
        parent_world = UsdGeom.XformCache().GetLocalToWorldTransform(wrapper.GetParent())
        # USD composes row-vector transforms as local * parent.  The child
        # keeps the referenced source root's local transform, so compensate it
        # on the wrapper before solving for the wrapper's parent-local matrix.
        wrapper_local = (
            source_local.GetInverse()
            * desired_world
            * parent_world.GetInverse()
        )
        wrapper_xform = UsdGeom.Xformable(wrapper)
        wrapper_xform.ClearXformOpOrder()
        wrapper_xform.AddTransformOp(UsdGeom.XformOp.PrecisionDouble).Set(wrapper_local)

    def _write_rigid_body_pose(
        self,
        body_path: str,
        position_w: Sequence[float],
        orientation_wxyz: Sequence[float],
    ) -> None:
        position = _finite_vector(position_w, 3, "position_w")
        orientation = _normalize_quaternion(
            orientation_wxyz,
            name="orientation_wxyz",
        )
        view = self._rigid_body_view(body_path)
        transforms = view.get_transforms()
        if hasattr(transforms, "clone"):
            import torch

            values = transforms.clone()
            values[0, :3] = torch.as_tensor(
                position,
                device=values.device,
                dtype=values.dtype,
            )
            values[0, 3:7] = torch.as_tensor(
                (orientation[1], orientation[2], orientation[3], orientation[0]),
                device=values.device,
                dtype=values.dtype,
            )
            indices = torch.as_tensor([0], device=values.device, dtype=torch.int32)
        else:
            import numpy as np

            values = np.array(transforms, copy=True)
            values[0, :3] = position
            values[0, 3:7] = (
                orientation[1],
                orientation[2],
                orientation[3],
                orientation[0],
            )
            indices = np.asarray([0], dtype=np.int32)
        view.set_transforms(values, indices)
        self._flush_physx_transforms()

    def _zero_rigid_body_velocity(self, body_path: str) -> None:
        try:
            from pxr import UsdPhysics

            prim = self._stage().GetPrimAtPath(body_path)
            if prim and prim.IsValid() and prim.HasAPI(UsdPhysics.RigidBodyAPI):
                kinematic = UsdPhysics.RigidBodyAPI(prim).GetKinematicEnabledAttr().Get()
                if bool(kinematic):
                    return
        except Exception:
            # The tensor view below remains the source of truth when a stage
            # wrapper is unavailable (for example in a lightweight test).
            pass
        view = self._rigid_body_view(body_path)
        velocities = view.get_velocities()
        if hasattr(velocities, "clone"):
            import torch

            values = torch.zeros_like(velocities)
            indices = torch.as_tensor([0], device=values.device, dtype=torch.int32)
        else:
            import numpy as np

            values = np.zeros_like(velocities)
            indices = np.asarray([0], dtype=np.int32)
        view.set_velocities(values, indices)

    def _remove_proxy(self, proxy_path: str) -> None:
        if proxy_path not in self._owned_proxy_paths:
            raise RuntimeError("refusing to remove a visual proxy not owned by this runtime")
        stage = self._stage()
        prim = stage.GetPrimAtPath(proxy_path)
        if prim and prim.IsValid():
            stage.RemovePrim(proxy_path)
            remaining = stage.GetPrimAtPath(proxy_path)
            if remaining and remaining.IsValid():
                raise RuntimeError(f"failed to remove visual proxy: {proxy_path}")
        self._owned_proxy_paths.discard(proxy_path)

    @staticmethod
    def _define_scope_chain(stage: Any, path: str) -> None:
        components = [component for component in str(path).split("/") if component]
        current = ""
        for component in components:
            current += f"/{component}"
            prim = stage.GetPrimAtPath(current)
            if prim and prim.IsValid():
                continue
            stage.DefinePrim(current, "Scope")

    def _physics_frontend(self) -> str:
        """Select a PhysX tensor frontend compatible with the active device."""

        device = getattr(self._env, "device", None)
        if device is None:
            sim_cfg = getattr(getattr(self._env, "cfg", None), "sim", None)
            device = getattr(sim_cfg, "device", "cpu")
        return "torch" if str(device).startswith("cuda") else "numpy"

    def _rigid_body_view(self, body_path: str) -> Any:
        key = (self._physics_frontend(), str(body_path))
        cached = self._physics_views.get(key)
        if cached is not None:
            return cached[1]

        # Isaac Lab already owns a live tensor view for configured rigid
        # objects.  Reuse it when the caller supplies that object's root body
        # path; constructing a second low-level simulation view can fail on
        # some Isaac Sim 5.x CPU backends while the scene-owned view remains
        # fully functional.
        scene = getattr(self._env, "scene", None)
        for rigid_object in getattr(scene, "rigid_objects", {}).values():
            view = getattr(rigid_object, "root_physx_view", None)
            if view is None:
                continue
            configured_paths: list[str] = []

            def _append_paths(source: Any) -> None:
                if source is None:
                    return
                if isinstance(source, (str, bytes)):
                    configured_paths.append(str(source))
                    return
                try:
                    values = list(source)
                except TypeError:
                    configured_paths.append(str(source))
                    return
                for value in values:
                    if isinstance(value, (str, bytes)):
                        configured_paths.append(str(value))
                    else:
                        _append_paths(value)

            for source in (
                getattr(view, "prim_paths", None),
                getattr(getattr(rigid_object, "cfg", None), "prim_path", None),
            ):
                _append_paths(source)
            if str(body_path) in configured_paths:
                self._physics_views[key] = (None, view)
                return view

        import omni.physics.tensors

        simulation_view = omni.physics.tensors.create_simulation_view(key[0])
        view = simulation_view.create_rigid_body_view(str(body_path))
        if view is None or int(getattr(view, "count", 0)) != 1:
            raise RuntimeError(f"PhysX rigid body view is unavailable: {body_path}")
        self._physics_views[key] = (simulation_view, view)
        return view

    @staticmethod
    def _pose_is_finite(pose: Any) -> bool:
        if hasattr(pose, "detach"):
            import torch

            return bool(torch.isfinite(pose).all())
        import numpy as np

        return bool(np.isfinite(pose).all())

    def _rigid_body_pose(
        self, body_path: str
    ) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
        try:
            view = self._rigid_body_view(body_path)
            pose = view.get_transforms()[0]
            if self._pose_is_finite(pose):
                if hasattr(pose, "detach"):
                    pose = pose.detach().cpu().tolist()
                else:
                    pose = pose.tolist()
                return (
                    (float(pose[0]), float(pose[1]), float(pose[2])),
                    (float(pose[6]), float(pose[3]), float(pose[4]), float(pose[5])),
                )
        except Exception:
            pass

        stage = self._stage()
        from pxr import UsdGeom

        transform = UsdGeom.XformCache().GetLocalToWorldTransform(
            stage.GetPrimAtPath(body_path)
        )
        position = transform.ExtractTranslation()
        quaternion = transform.ExtractRotationQuat()
        imaginary = quaternion.GetImaginary()
        return (
            (float(position[0]), float(position[1]), float(position[2])),
            (
                float(quaternion.GetReal()),
                float(imaginary[0]),
                float(imaginary[1]),
                float(imaginary[2]),
            ),
        )

    @staticmethod
    def _flush_physx_transforms() -> None:
        """Synchronize USD and the live PhysX scene when the API is present."""

        try:
            import omni.physx

            simulation_interface = omni.physx.get_physx_simulation_interface()
            flush = getattr(simulation_interface, "flush_changes", None)
            if callable(flush):
                flush()
            # Isaac's older bridge exposes this on the general PhysX
            # interface.  Calling it after a tensor write makes the authored
            # USD pose visible to joint parsing on both API variants.
            update = getattr(omni.physx.get_physx_interface(), "update_transformations", None)
            if callable(update):
                update(False, True, False, False)
        except Exception:
            # Tensor writes already update the solver; this is only a best
            # effort bridge synchronization for versions that expose it.
            pass

    def reset(self, _env_ids: Any | None = None) -> None:
        """Restore isolated source bodies, remove proxies and forget commands."""

        for robot_name, state in tuple(self._states.items()):
            self._cleanup_object(robot_name, state)
            self._restore_gripper_collisions(robot_name)
        for proxy_path in tuple(self._owned_proxy_paths):
            try:
                self._remove_proxy(proxy_path)
            except Exception:
                self._owned_proxy_paths.discard(proxy_path)
        self._states.clear()
        self._bindings.clear()
        self._object_owners.clear()
        self._physics_views.clear()
        for robot_name in tuple(self._gripper_collision_snapshots):
            self._restore_gripper_collisions(robot_name)
        cache = getattr(self._env, "_eai_planar_ik_states", {})
        for key in tuple(cache):
            if key[0] in self._model_specs:
                cache.pop(key, None)

    def close(self) -> None:
        if self._closed:
            return
        self.reset()
        self._closed = True


__all__ = [
    "CylindricalPose",
    "DEFAULT_ATTACHMENT_MODE",
    "DEFAULT_ATTACHMENT_SETTLE_DURATION_S",
    "DEFAULT_GRIPPER_POSITION_TOLERANCE_RAD",
    "DEFAULT_PHASE_TIMEOUT_STEPS",
    "DEFAULT_POSITION_TOLERANCE_M",
    "DEFAULT_RADIAL_SPEED_M_S",
    "DEFAULT_VERTICAL_SPEED_M_S",
    "DEFAULT_YAW_SPEED_RAD_S",
    "DEFAULT_YAW_TOLERANCE_RAD",
    "DEFAULT_TOOL_ORIENTATION_TOLERANCE_RAD",
    "DEFAULT_Z1_GRIPPER_CLOSED_POSITION_RAD",
    "DEFAULT_Z1_GRIPPER_COLLISION_BODY_NAMES",
    "DEFAULT_Z1_GRIPPER_OPEN_POSITION_RAD",
    "HeldObject",
    "IdealizedManipulationRuntime",
    "ManipulationPhase",
    "RobotManipulationState",
    "PlanarIkSpec",
    "cylindrical_to_local_position",
    "cylindrical_to_world_position",
    "local_position_to_cylindrical",
    "planar_ik_spec_for_model",
    "feasible_yaw_joint_target",
    "normalize_cylindrical_pose",
    "parse_cylindrical_pose",
    "wrap_angle_rad",
]
