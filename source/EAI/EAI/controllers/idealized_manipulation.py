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
from .environment_view import SingleEnvironmentView, environment_path, validate_env_id


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
DEFAULT_CARTESIAN_SPEED_M_S = 0.05
DEFAULT_GRIPPER_ANIMATION_SPEED_RAD_S = 0.8
DEFAULT_CARTESIAN_ARRIVAL_DWELL_S = 0.51
DEFAULT_CARTESIAN_STABLE_LINEAR_SPEED_M_S = 0.01
DEFAULT_UR5_GRIPPER_OPEN_POSITION_RAD = 0.0
DEFAULT_UR5_GRIPPER_CLOSED_POSITION_RAD = 0.8
DEFAULT_Z1_GRIPPER_OPEN_POSITION_RAD = -0.8
DEFAULT_Z1_GRIPPER_CLOSED_POSITION_RAD = 0.0
DEFAULT_Z1_GRIPPER_COLLISION_BODY_NAMES = ("gripperStator", "gripperMover")
# D1's gripper is a mirrored prismatic finger pair (Joint7_1 in [0, 0.03] m,
# Joint7_2 in [-0.03, 0] m), so the single radian-valued gripper fields on
# PlanarIkSpec cannot describe both finger targets.  The D1 branch therefore
# drives the fingers with the closure mapping in
# ``EAI.hmrs_ros.d1_omnigraph.d1_gripper_targets`` (0.0 open, 1.0 closed).
DEFAULT_D1_GRIPPER_OPEN_CLOSURE = 0.0
DEFAULT_D1_GRIPPER_CLOSED_CLOSURE = 1.0
DEFAULT_D1_GRIPPER_STROKE_M = 0.03
DEFAULT_D1_GRIPPER_POSITION_TOLERANCE_M = 0.005
# Finger-centre TCP on Link6 (asset-measured): pads span local z 0.064..0.126 m
# around a ~0.077 m opening and the wrist housing ends at local z 0.077 m, so
# 0.105 m keeps a 5 cm cube clear of the housing while both pads straddle it.
DEFAULT_D1_TCP_OFFSET_M = 0.105
DEFAULT_D1_MAX_OPENING_M = 0.077


def _d1_gripper_targets(closure01: float) -> tuple[float, float]:
    """Per-finger stroke targets for D1's mirrored prismatic fingers.

    Delegates to ``EAI.hmrs_ros.d1_omnigraph.d1_gripper_targets`` (0.0 open,
    1.0 closed).  The import stays lazy because this module also loads inside
    lightweight non-Isaac test harnesses; the mirrored fallback below keeps the
    documented mapping when the ROS package is unavailable.
    """
    try:
        from EAI.hmrs_ros.d1_omnigraph import d1_gripper_targets
    except ImportError:
        closure = float(closure01)
        stroke = DEFAULT_D1_GRIPPER_STROKE_M * (1.0 - closure)
        return (stroke, -stroke)
    return d1_gripper_targets(closure01)


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
            gripper_open_position_rad=(
                DEFAULT_UR5_GRIPPER_OPEN_POSITION_RAD if model == "ur5" else None
            ),
            gripper_closed_position_rad=(
                DEFAULT_UR5_GRIPPER_CLOSED_POSITION_RAD if model == "ur5" else None
            ),
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
    if model == "d1":
        # D1 mirrors the Z1 chain layout: Joint1 is the base yaw, Joint6 is
        # the wrist roll, and Joint2..Joint5 carry the planar pitch geometry
        # (0.27 m upper arm, 0.14 m forearm).  The gripper fields stay unset
        # because D1's mirrored meter-valued fingers need the closure mapping
        # documented next to DEFAULT_D1_GRIPPER_OPEN_CLOSURE.
        return PlanarIkSpec(
            model=model,
            yaw_joint_name="Joint1",
            ik_joint_names=("Joint2", "Joint3", "Joint4", "Joint5"),
            fixed_joint_names=("Joint6",),
            plane_offset_m=0.0,
            tool_orientation_plane_wxyz=(math.sqrt(0.5), 0.0, math.sqrt(0.5), 0.0),
        )
    names = tuple(str(name) for name in getattr(model_spec, "joint_names", ()))
    return PlanarIkSpec(model=model or "generic", yaw_joint_name=None, ik_joint_names=names)


from EAI.manipulation_geometry import (
    CylindricalPose, _finite_float, _finite_vector, _quat_conjugate,
    _quat_multiply, _normalize_quaternion, _quat_rotate, wrap_angle_rad,
    feasible_yaw_joint_target, normalize_cylindrical_pose, parse_cylindrical_pose,
    cylindrical_to_local_position, local_position_to_cylindrical,
    cylindrical_to_world_position,
    _quat_angle_rad, _quat_slerp, _shortest_arc_quaternion,
    cartesian_place_contact_pose,
)


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
    PREPARE = "prepare"
    ROTATE = "rotate"
    RADIAL_APPROACH = "radial_approach"
    VERTICAL_APPROACH = "vertical_approach"
    PREGRASP = "pregrasp"
    APPROACH = "approach"
    GRASP = "grasp"
    LIFT = "lift"
    RETRACT = "retract"
    ATTACHED_HOLD = "attached_hold"
    RELEASE = "release"
    FAILED = "failed"


@dataclass
class CartesianPlan:
    """World-frame SE(3) waypoints for one arbitrary-approach operation.

    ``approach_direction_w`` is the unit vector from the pregrasp point toward
    the contact point.  The tool axis convention is end-effector local +Z
    pointing away from the object, so at contact ``R_ee @ ez == -approach``.
    The reference pose is the rate-limited waypoint the differential IK
    tracks; it is advanced once per physics tick.
    """

    approach_direction_w: tuple[float, float, float]
    contact_position_w: tuple[float, float, float]
    contact_orientation_wxyz: tuple[float, float, float, float]
    pregrasp_position_w: tuple[float, float, float]
    retreat_position_w: tuple[float, float, float]
    approach_speed_m_s: float = DEFAULT_CARTESIAN_SPEED_M_S
    motion_speed_scale: float = 1.0
    arrival_dwell_s: float = DEFAULT_CARTESIAN_ARRIVAL_DWELL_S
    orientation_tolerance_rad: float = DEFAULT_TOOL_ORIENTATION_TOLERANCE_RAD
    reference_position_w: tuple[float, float, float] = (0.0, 0.0, 0.0)
    reference_orientation_wxyz: tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0)
    phase_start_position_w: tuple[float, float, float] = (0.0, 0.0, 0.0)
    phase_start_orientation_wxyz: tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0)
    phase_distance_m: float = 0.0


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
    arrival_tcp_position_w: tuple[float, float, float] | None = None
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
    cartesian: CartesianPlan | None = None
    cartesian_goal_position_w: tuple[float, float, float] | None = None
    cartesian_goal_orientation_wxyz: tuple[float, float, float, float] | None = None
    gripper_command_rad: float | None = None
    gripper_goal_rad: float | None = None
    grasp_tool: Any = None
    payload_collision_filter: Any = None


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
        env_count = int(getattr(env, "num_envs", 1))
        if env_count < 1:
            raise ValueError("env.num_envs must be positive")
        self._env = env
        self._environment_runtimes: tuple[IdealizedManipulationRuntime, ...] = ()
        self._environment_count = env_count
        self._dispatcher = env_count > 1
        if self._dispatcher:
            options = dict(
                attachment_mode=attachment_mode,
                radial_speed_m_s=radial_speed_m_s,
                vertical_speed_m_s=vertical_speed_m_s,
                yaw_speed_rad_s=yaw_speed_rad_s,
                max_delta_rad=max_delta_rad,
                dls_lambda=dls_lambda,
            )
            self._environment_runtimes = tuple(
                IdealizedManipulationRuntime(
                    SingleEnvironmentView(env, env_id), model_specs, **options,
                )
                for env_id in range(env_count)
            )
            self._model_specs = self._normalize_model_specs(model_specs)
            self._attachment_mode = str(attachment_mode).strip().lower()
            self._radial_speed_m_s = _finite_float(radial_speed_m_s, "radial_speed_m_s")
            self._vertical_speed_m_s = _finite_float(vertical_speed_m_s, "vertical_speed_m_s")
            self._yaw_speed_rad_s = _finite_float(yaw_speed_rad_s, "yaw_speed_rad_s")
            self._max_delta_rad = _finite_float(max_delta_rad, "max_delta_rad")
            self._dls_lambda = _finite_float(dls_lambda, "dls_lambda")
            self._enabled = all(runtime.enabled for runtime in self._environment_runtimes)
            self._states = {}
            self._bindings = {}
            self._proxy_sequence = 0
            self._owned_proxy_paths = set()
            self._object_owners = {}
            self._physics_views = {}
            self._gripper_collision_snapshots = {}
            self._closed = False
            return

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
        self._retain_on_failure: set[str] = set()

    def preserve_holding_on_failure(self, robot_name: str, *, env_id: int = 0) -> None:
        """Opt in for local pick/place; legacy cleanup semantics stay intact."""
        if self._dispatcher:
            self._runtime_for_env(env_id).preserve_holding_on_failure(robot_name)
            return
        self._retain_on_failure.add(str(robot_name).strip())

    def stop_retaining_object(self, robot_name: str, reason: str, *, env_id: int = 0) -> bool:
        """Stop the trajectory, retaining a measured local carry pose.

        A partial release or failed restoration is not a confirmed safe stop.
        Its ownership records remain available for host recovery/reset.
        """
        if self._dispatcher:
            return self._runtime_for_env(env_id).stop_retaining_object(robot_name, reason)
        name = str(robot_name).strip()
        state = self._states.setdefault(name, RobotManipulationState())
        state.last_error = str(reason)
        if state.release_started:
            state.phase = ManipulationPhase.FAILED
            state.operation = None
            state.phase_goal = None
            state.phase_axis = None
            return False
        binding = self._bindings.get(name)
        if binding is not None and callable(getattr(binding.arm, "set_joint_position_target", None)):
            # Stopping the planner alone leaves its last drive targets live.
            # Freeze measured joints before relinquishing trajectory ownership.
            ids, _ = binding.arm.find_joints(list(binding.model_spec.joint_names), preserve_order=True)
            measured = binding.arm.data.joint_pos[:, list(ids)]
            targets = measured.clone() if hasattr(measured, "clone") else measured.copy()
            binding.arm.set_joint_position_target(targets, joint_ids=list(ids))
            cache = getattr(self._env, "_eai_planar_ik_states", {})
            cache.pop((name, str(binding.model_spec.model)), None)
        if state.held is not None:
            binding = self._binding(name)
            position, orientation = self._end_effector_pose(binding)
            if state.cartesian is not None:
                # An arbitrary-approach hold may be folded anywhere in the
                # workspace; freeze the measured pose instead of projecting it
                # onto the cylindrical plane.
                plan = state.cartesian
                orientation = _normalize_quaternion(orientation)
                plan.reference_position_w = tuple(position)
                plan.reference_orientation_wxyz = orientation
                plan.phase_start_position_w = tuple(position)
                plan.phase_start_orientation_wxyz = orientation
                plan.phase_distance_m = 0.0
                plan.retreat_position_w = tuple(position)
                plan.contact_orientation_wxyz = orientation
                state.cartesian_goal_position_w = tuple(position)
                state.cartesian_goal_orientation_wxyz = orientation
            else:
                base_position, base_orientation = self._base_pose(binding)
                del base_position
                pose = self._measured_plane_pose(binding, position)
                state.command_pose = pose
                state.initial_plane_yaw_rad = pose.yaw_rad
                state.ee_orientation_relative_b_wxyz = _quat_multiply(_quat_conjugate(base_orientation), orientation)
                if binding.yaw_joint_id is not None:
                    state.yaw_joint_target_rad = float(binding.arm.data.joint_pos[0, binding.yaw_joint_id])
                cache = getattr(self._env, "_eai_planar_ik_states", {})
                cache.pop((name, str(binding.model_spec.model)), None)
            self._sync_follow_proxy(state.held, binding)
            state.phase = ManipulationPhase.ATTACHED_HOLD
        else:
            if state.pending_isolation is not None:
                self._restore_object_isolation(state.pending_isolation, remove_proxy=True)
                self._object_owners.pop(state.pending_isolation.object_body_path, None)
                state.pending_isolation = None
            self._restore_gripper_collisions(name)
            self._restore_payload_contact(state)
            state.phase = ManipulationPhase.FAILED
        state.operation = None
        state.phase_goal = None
        state.phase_axis = None
        state.phase_steps = 0
        state.settle_elapsed_s = 0.0
        return True

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
        if self._dispatcher:
            return not self._closed and all(runtime.enabled for runtime in self._environment_runtimes)
        return self._enabled and int(getattr(self._env, "num_envs", 1)) == 1 and not self._closed

    def _runtime_for_env(self, env_id: int) -> IdealizedManipulationRuntime:
        validate_env_id(self._env, env_id)
        if self._dispatcher:
            return self._environment_runtimes[env_id]
        return self

    def state(self, robot_name: str, *, env_id: int = 0) -> ManipulationPhase:
        runtime = self._runtime_for_env(env_id)
        if runtime is not self:
            return runtime.state(robot_name)
        return self._states.get(str(robot_name).strip(), RobotManipulationState()).phase

    def last_error(self, robot_name: str, *, env_id: int = 0) -> str | None:
        runtime = self._runtime_for_env(env_id)
        if runtime is not self:
            return runtime.last_error(robot_name)
        return self._states.get(str(robot_name).strip(), RobotManipulationState()).last_error

    def held_object(self, robot_name: str, *, env_id: int = 0) -> HeldObject | None:
        runtime = self._runtime_for_env(env_id)
        if runtime is not self:
            return runtime.held_object(robot_name)
        return self._states.get(str(robot_name).strip(), RobotManipulationState()).held

    def start_move(
        self,
        robot_name: str,
        target_pose: CylindricalPose | Sequence[float],
        *,
        tolerance_m: float = DEFAULT_POSITION_TOLERANCE_M,
        yaw_tolerance_rad: float = DEFAULT_YAW_TOLERANCE_RAD,
        phase_timeout_steps: int = DEFAULT_PHASE_TIMEOUT_STEPS,
        env_id: int = 0,
    ) -> bool:
        """Move an empty arm through yaw, radius and height in that order.

        Invalid argument values raise ``ValueError``; runtime rejections
        (busy, holding, unsupported environment) return ``False`` and record
        the reason in ``last_error``.
        """

        runtime = self._runtime_for_env(env_id)
        if runtime is not self:
            return runtime.start_move(
                robot_name, target_pose, tolerance_m=tolerance_m,
                yaw_tolerance_rad=yaw_tolerance_rad,
                phase_timeout_steps=phase_timeout_steps,
            )
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
        env_id: int = 0,
    ) -> bool:
        """Pick a body, lift above it, retract, then report ATTACHED_HOLD.

        Invalid argument values (malformed poses, absolute-path violations,
        recovery-pose constraint violations) raise ``ValueError``; runtime
        rejections return ``False`` and record the reason in ``last_error``.
        """

        runtime = self._runtime_for_env(env_id)
        if runtime is not self:
            row_path = environment_path(object_body_path, env_id)
            return runtime.start_pick(
                robot_name, row_path, grasp_pose, recovery_pose=recovery_pose,
                tolerance_m=tolerance_m, yaw_tolerance_rad=yaw_tolerance_rad,
                settle_duration_s=settle_duration_s,
                phase_timeout_steps=phase_timeout_steps,
            )
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
        if name in self._retain_on_failure and self._binding(name).yaw_joint_id is not None:
            # Local backends may enter from a normal controller's arbitrary
            # wrist posture. Its current position may be unreachable with a
            # downward tool (notably the high folded Z1 defaults). Establish
            # the reference at the elevated recovery pose before ROTATE
            # freezes the non-yaw joints; ordinary joint slew limits apply.
            prepared = CylindricalPose(recovery.x_m, recovery.z_m, current.yaw_rad)
            state.command_pose = prepared
            self._set_phase(state, ManipulationPhase.PREPARE, prepared, None)
            return True
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
        env_id: int = 0,
    ) -> bool:
        """Place the held body, release once, retract and return to recovery.

        Invalid argument values raise ``ValueError``; runtime rejections
        return ``False`` and record the reason in ``last_error``.
        """

        runtime = self._runtime_for_env(env_id)
        if runtime is not self:
            return runtime.start_place(
                robot_name, place_pose, recovery_pose=recovery_pose,
                tolerance_m=tolerance_m, yaw_tolerance_rad=yaw_tolerance_rad,
                phase_timeout_steps=phase_timeout_steps,
            )
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

    def start_pick_cartesian(
        self,
        robot_name: str,
        object_body_path: str,
        *,
        contact_position_w: Sequence[float],
        contact_orientation_wxyz: Sequence[float],
        approach_direction_w: Sequence[float],
        pregrasp_distance_m: float,
        retreat_distance_m: float,
        approach_speed_m_s: float = DEFAULT_CARTESIAN_SPEED_M_S,
        motion_speed_scale: float = 1.0,
        arrival_dwell_s: float = DEFAULT_CARTESIAN_ARRIVAL_DWELL_S,
        tolerance_m: float = DEFAULT_POSITION_TOLERANCE_M,
        orientation_tolerance_rad: float = DEFAULT_TOOL_ORIENTATION_TOLERANCE_RAD,
        settle_duration_s: float = DEFAULT_ATTACHMENT_SETTLE_DURATION_S,
        phase_timeout_steps: int = DEFAULT_PHASE_TIMEOUT_STEPS,
        env_id: int = 0,
    ) -> bool:
        """Pick along an arbitrary world-frame approach direction.

        The waypoints are pregrasp → contact → retreat, all expressed in world
        coordinates.  Invalid argument values raise ``ValueError``; runtime
        rejections return ``False`` and record the reason in ``last_error``.
        """

        runtime = self._runtime_for_env(env_id)
        if runtime is not self:
            row_path = environment_path(object_body_path, env_id)
            return runtime.start_pick_cartesian(
                robot_name, row_path,
                contact_position_w=contact_position_w,
                contact_orientation_wxyz=contact_orientation_wxyz,
                approach_direction_w=approach_direction_w,
                pregrasp_distance_m=pregrasp_distance_m,
                retreat_distance_m=retreat_distance_m,
                approach_speed_m_s=approach_speed_m_s,
                motion_speed_scale=motion_speed_scale,
                arrival_dwell_s=arrival_dwell_s,
                tolerance_m=tolerance_m,
                orientation_tolerance_rad=orientation_tolerance_rad,
                settle_duration_s=settle_duration_s,
                phase_timeout_steps=phase_timeout_steps,
            )
        plan = self._validate_cartesian_geometry(
            contact_position_w, contact_orientation_wxyz, approach_direction_w,
            pregrasp_distance_m, retreat_distance_m, approach_speed_m_s,
            orientation_tolerance_rad, motion_speed_scale, arrival_dwell_s,
        )
        name = str(robot_name).strip()
        state = self._states.setdefault(name, RobotManipulationState())
        if not self.enabled:
            return self._reject(state, "idealized manipulation requires num_envs=1")
        if state.held is not None:
            return self._reject(state, "pick rejected: robot already holds an object")
        if state.phase not in (ManipulationPhase.IDLE, ManipulationPhase.FAILED):
            return self._reject(state, "manipulation command rejected: robot is busy")
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
        started = self._begin_cartesian(
            "pick", name, plan, object_body_path=object_path,
            tolerance_m=tolerance_m, phase_timeout_steps=phase_timeout_steps,
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
            state.phase = ManipulationPhase.FAILED
            state.last_error = f"pick rejected: follow isolation failed: {exc}"
            return False
        self._set_cartesian_phase(
            state, ManipulationPhase.PREGRASP,
            plan.pregrasp_position_w, plan.contact_orientation_wxyz,
        )
        try:
            self._prepare_folded_cartesian_pick(name, state)
        except Exception as exc:
            self._fail(state, f"pick preparation failed: {exc}")
            return False
        return True

    def _prepare_folded_cartesian_pick(self, robot_name: str, state: RobotManipulationState) -> None:
        """Unfold Z1 in its measured shoulder plane before full-pose IK.

        A folded wrist starts behind the shoulder. A direct lateral Cartesian
        path can select the opposite yaw branch and stick at the joint limit.
        The nominal forward ready pose is local to the articulation base;
        ordinary planar joint drives and measured arrival gates perform it.
        """
        binding = self._binding(robot_name)
        if binding.model_spec.model != "z1" or binding.yaw_joint_id is None:
            return
        position, _ = self._end_effector_pose(binding)
        base_position, base_orientation = self._base_pose(binding)
        local = _quat_rotate(_quat_conjugate(base_orientation),
                             tuple(position[i] - base_position[i] for i in range(3)))
        yaw_joint = float(binding.arm.data.joint_pos[0, binding.yaw_joint_id])
        spec = binding.planar_spec
        yaw = spec.yaw_zero_rad + spec.yaw_sign * yaw_joint
        if math.cos(yaw) * local[0] + math.sin(yaw) * local[1] > 0.0:
            return
        prepared = CylindricalPose(0.25, 0.20, yaw)
        state.command_pose = prepared
        state.initial_plane_yaw_rad = yaw
        state.initial_yaw_joint_rad = yaw_joint
        state.yaw_joint_target_rad = yaw_joint
        state.yaw_tolerance_rad = DEFAULT_YAW_TOLERANCE_RAD
        yaw_quat = (math.cos(yaw / 2), 0.0, 0.0, math.sin(yaw / 2))
        state.ee_orientation_relative_b_wxyz = _quat_multiply(yaw_quat, spec.tool_orientation_plane_wxyz)
        self._set_phase(state, ManipulationPhase.PREPARE, prepared, None)

    def start_place_cartesian(
        self,
        robot_name: str,
        *,
        target_object_position_w: Sequence[float],
        approach_direction_w: Sequence[float],
        pregrasp_distance_m: float,
        retreat_distance_m: float,
        approach_speed_m_s: float = DEFAULT_CARTESIAN_SPEED_M_S,
        motion_speed_scale: float = 1.0,
        arrival_dwell_s: float = DEFAULT_CARTESIAN_ARRIVAL_DWELL_S,
        tolerance_m: float = DEFAULT_POSITION_TOLERANCE_M,
        orientation_tolerance_rad: float = DEFAULT_TOOL_ORIENTATION_TOLERANCE_RAD,
        phase_timeout_steps: int = DEFAULT_PHASE_TIMEOUT_STEPS,
        env_id: int = 0,
    ) -> bool:
        """Place the held body's origin via an arbitrary approach direction.

        The contact orientation keeps the carried object relative pose and
        swings the tool axis onto ``-approach`` with the shortest rotation.
        Invalid argument values raise ``ValueError``; runtime rejections
        return ``False`` and record the reason in ``last_error``.
        """

        runtime = self._runtime_for_env(env_id)
        if runtime is not self:
            return runtime.start_place_cartesian(
                robot_name,
                target_object_position_w=target_object_position_w,
                approach_direction_w=approach_direction_w,
                pregrasp_distance_m=pregrasp_distance_m,
                retreat_distance_m=retreat_distance_m,
                approach_speed_m_s=approach_speed_m_s,
                motion_speed_scale=motion_speed_scale,
                arrival_dwell_s=arrival_dwell_s,
                tolerance_m=tolerance_m,
                orientation_tolerance_rad=orientation_tolerance_rad,
                phase_timeout_steps=phase_timeout_steps,
            )
        plan = self._validate_cartesian_geometry(
            None, None, approach_direction_w,
            pregrasp_distance_m, retreat_distance_m, approach_speed_m_s,
            orientation_tolerance_rad, motion_speed_scale, arrival_dwell_s,
        )
        name = str(robot_name).strip()
        state = self._states.setdefault(name, RobotManipulationState())
        if not self.enabled:
            return self._reject(state, "idealized manipulation requires num_envs=1")
        if state.held is None:
            return self._reject(state, "place rejected: robot does not hold an object")
        if state.phase != ManipulationPhase.ATTACHED_HOLD:
            return self._reject(state, "place rejected: robot is not in ATTACHED_HOLD")
        target = _finite_vector(target_object_position_w, 3, "target_object_position_w")
        try:
            binding = self._binding(name)
            _, ee_orientation = self._end_effector_pose(binding)
        except Exception as exc:
            return self._reject(state, f"place rejected: {exc}")
        contact_position, contact_orientation = cartesian_place_contact_pose(
            state.held.relative_object_position_ee, ee_orientation,
            plan.approach_direction_w, target,
            tool_axis_ee=((0., 0., 1.) if state.grasp_tool is None else
                          _quat_rotate(state.grasp_tool.orientation_ee_wxyz, (0., 0., 1.))),
        )
        plan.contact_position_w = contact_position
        plan.contact_orientation_wxyz = contact_orientation
        pregrasp = tuple(
            contact_position[index] - pregrasp_distance_m * plan.approach_direction_w[index]
            for index in range(3)
        )
        retreat = tuple(
            contact_position[index] - retreat_distance_m * plan.approach_direction_w[index]
            for index in range(3)
        )
        plan.pregrasp_position_w = pregrasp
        plan.retreat_position_w = retreat
        started = self._begin_cartesian(
            "place", name, plan, object_body_path=state.held.object_body_path,
            tolerance_m=tolerance_m, phase_timeout_steps=phase_timeout_steps,
        )
        if not started:
            return False
        self._set_cartesian_phase(
            state, ManipulationPhase.PREGRASP,
            plan.pregrasp_position_w, plan.contact_orientation_wxyz,
        )
        return True

    @staticmethod
    def _validate_cartesian_geometry(
        contact_position_w: Sequence[float] | None,
        contact_orientation_wxyz: Sequence[float] | None,
        approach_direction_w: Sequence[float],
        pregrasp_distance_m: float,
        retreat_distance_m: float,
        approach_speed_m_s: float,
        orientation_tolerance_rad: float,
        motion_speed_scale: float = 1.0,
        arrival_dwell_s: float = DEFAULT_CARTESIAN_ARRIVAL_DWELL_S,
    ) -> CartesianPlan:
        direction = _finite_vector(approach_direction_w, 3, "approach_direction_w")
        norm = math.sqrt(sum(value * value for value in direction))
        if norm <= 1.0e-9:
            raise ValueError("approach_direction_w must be nonzero")
        direction = tuple(value / norm for value in direction)
        pregrasp_distance = _finite_float(pregrasp_distance_m, "pregrasp_distance_m")
        retreat_distance = _finite_float(retreat_distance_m, "retreat_distance_m")
        speed = _finite_float(approach_speed_m_s, "approach_speed_m_s")
        speed_scale = _finite_float(motion_speed_scale, "motion_speed_scale")
        dwell = _finite_float(arrival_dwell_s, "arrival_dwell_s")
        orientation_tolerance = _finite_float(
            orientation_tolerance_rad, "orientation_tolerance_rad",
        )
        if pregrasp_distance <= 0.0 or retreat_distance < pregrasp_distance:
            raise ValueError(
                "cartesian distances require 0 < pregrasp_distance_m <= retreat_distance_m"
            )
        if speed <= 0.0:
            raise ValueError("approach_speed_m_s must be positive")
        if speed_scale <= 0.0:
            raise ValueError("motion_speed_scale must be positive")
        if dwell < 0.5:
            raise ValueError("arrival_dwell_s must be at least 0.5 seconds")
        if orientation_tolerance <= 0.0:
            raise ValueError("orientation_tolerance_rad must be positive")
        plan = CartesianPlan(
            approach_direction_w=direction,
            contact_position_w=(0.0, 0.0, 0.0),
            contact_orientation_wxyz=(1.0, 0.0, 0.0, 0.0),
            pregrasp_position_w=(0.0, 0.0, 0.0),
            retreat_position_w=(0.0, 0.0, 0.0),
            approach_speed_m_s=speed * speed_scale,
            motion_speed_scale=speed_scale,
            arrival_dwell_s=dwell,
            orientation_tolerance_rad=orientation_tolerance,
        )
        if contact_position_w is not None:
            plan.contact_position_w = _finite_vector(contact_position_w, 3, "contact_position_w")
            plan.contact_orientation_wxyz = _normalize_quaternion(
                contact_orientation_wxyz, name="contact_orientation_wxyz",
            )
            plan.pregrasp_position_w = tuple(
                plan.contact_position_w[index] - pregrasp_distance * direction[index]
                for index in range(3)
            )
            plan.retreat_position_w = tuple(
                plan.contact_position_w[index] - retreat_distance * direction[index]
                for index in range(3)
            )
        return plan

    def _begin_cartesian(
        self,
        operation: str,
        robot_name: str,
        plan: CartesianPlan,
        *,
        object_body_path: str | None = None,
        tolerance_m: float,
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
        timeout = _positive_steps(phase_timeout_steps)
        settle = _finite_float(settle_duration_s, "settle_duration_s")
        if tolerance <= 0.0:
            raise ValueError("tolerance_m must be positive")
        if settle < 0.0:
            raise ValueError("settle_duration_s must be non-negative")
        try:
            binding = self._binding(robot_name)
            ee_position, ee_orientation = self._end_effector_pose(binding)
        except Exception as exc:
            return self._reject(state, f"{operation} rejected: {exc}")
        # Trajectory ownership moves to the full-pose differential IK; drop any
        # stale planar integrator state so a later cylindrical command re-seeds.
        cache = getattr(self._env, "_eai_planar_ik_states", {})
        cache.pop((robot_name, str(binding.model_spec.model)), None)
        state.phase = ManipulationPhase.IDLE
        state.operation = operation
        if operation == "pick":
            state.grasp_tool = None
        state.target_pose = None
        state.recovery_pose = None
        state.phase_goal = None
        state.phase_axis = None
        state.rotation_stage = "initial"
        state.command_pose = None
        state.ee_orientation_relative_b_wxyz = None
        state.initial_plane_yaw_rad = None
        state.initial_yaw_joint_rad = None
        state.yaw_joint_target_rad = None
        state.yaw_joint_goal_rad = None
        state.arrival_joint_sample = None
        state.arrival_tcp_position_w = None
        state.tolerance_m = tolerance
        state.phase_timeout_steps = timeout
        state.phase_steps = 0
        state.object_body_path = object_body_path
        state.settle_duration_s = settle
        state.settle_elapsed_s = 0.0
        state.release_started = False
        state.last_error = None
        state.pending_isolation = None
        state.cartesian = plan
        state.cartesian_goal_position_w = None
        state.cartesian_goal_orientation_wxyz = None
        plan.reference_position_w = tuple(ee_position)
        plan.reference_orientation_wxyz = tuple(_normalize_quaternion(ee_orientation))
        plan.phase_start_position_w = tuple(ee_position)
        plan.phase_start_orientation_wxyz = plan.reference_orientation_wxyz
        plan.phase_distance_m = 0.0
        return True

    def _set_cartesian_phase(
        self,
        state: RobotManipulationState,
        phase: ManipulationPhase,
        goal_position_w: Sequence[float],
        goal_orientation_wxyz: Sequence[float],
    ) -> None:
        plan = state.cartesian
        if plan is None:
            raise RuntimeError("cartesian phase requires a cartesian plan")
        goal_position = _finite_vector(goal_position_w, 3, "cartesian_goal_position_w")
        goal_orientation = _normalize_quaternion(goal_orientation_wxyz, name="cartesian_goal_orientation_wxyz")
        state.phase = phase
        state.cartesian_goal_position_w = goal_position
        state.cartesian_goal_orientation_wxyz = goal_orientation
        plan.phase_start_position_w = tuple(plan.reference_position_w)
        plan.phase_start_orientation_wxyz = tuple(plan.reference_orientation_wxyz)
        plan.phase_distance_m = math.dist(plan.reference_position_w, goal_position)
        state.phase_steps = 0
        state.settle_elapsed_s = 0.0
        state.arrival_joint_sample = None
        state.arrival_tcp_position_w = None

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
                initial = self._measured_plane_pose(
                    binding, ee_position,
                    allow_folded=operation == "pick" and robot_name in self._retain_on_failure,
                )
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
        if operation == "pick":
            state.grasp_tool = None
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
        *, allow_folded: bool = False,
    ) -> CylindricalPose:
        base_pos, base_quat = self._base_pose(binding)
        if binding.yaw_joint_id is None:
            return self._cylindrical_from_world(position_w, base_pos, base_quat)
        spec = binding.planar_spec
        q = float(binding.arm.data.joint_pos[0, binding.yaw_joint_id])
        yaw = spec.yaw_zero_rad + spec.yaw_sign * q
        local = _quat_rotate(_quat_conjugate(base_quat), tuple(position_w[i] - base_pos[i] for i in range(3)))
        x = math.cos(yaw) * local[0] + math.sin(yaw) * local[1]
        if x < -1.0e-6 and not allow_folded:
            raise RuntimeError("arm is folded behind the positive plane; prepare it before starting a skill")
        # PREPARE tolerates a folded wrist while it moves to the recovery pose.
        # Absolute radius is only a temporary measurement; measured world
        # position still gates arrival at the positive plane.
        return CylindricalPose(abs(x) if allow_folded else max(0.0, x), local[2], yaw)

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
        if state.grasp_tool is not None:
            actual_tcp, _ = state.grasp_tool.tcp_pose(position, orientation)
            target_tcp, _ = state.grasp_tool.tcp_pose(target_position, target_orientation)
            if math.dist(actual_tcp, target_tcp) > state.tolerance_m:
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
        if self._dispatcher:
            if env is not None and env is not self._env:
                raise ValueError("multi-environment manipulation is bound to its shared environment")
            for runtime in self._environment_runtimes:
                runtime.run_post_apply()
            return
        if env is not None:
            self._env = env
        if not self.enabled:
            return
        dt = self._physics_step_duration_s()
        for robot_name, state in tuple(self._states.items()):
            if state.phase in (ManipulationPhase.IDLE, ManipulationPhase.FAILED):
                binding = self._bindings.get(robot_name)
                if binding is not None and binding.model_spec.model in {"z1", "ur5"}:
                    try:
                        self._hold_fixed_joints(robot_name, binding, state)
                        self._hold_gripper(binding, state)
                    except Exception as exc:
                        self._fail(state, f"gripper hold failed: {exc}")
                continue
            try:
                binding = self._binding(robot_name)
                if state.phase == ManipulationPhase.ATTACHED_HOLD:
                    if state.cartesian is not None:
                        plan = state.cartesian
                        self._drive(robot_name, binding,
                                    plan.reference_position_w, plan.reference_orientation_wxyz, state)
                    else:
                        self._drive_state(robot_name, binding, state)
                    if state.held is not None:
                        self._sync_follow_proxy(state.held, binding)
                    continue
                if state.cartesian is not None and state.phase != ManipulationPhase.PREPARE:
                    self._step_cartesian(robot_name, binding, state, dt)
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
                measured = self._measured_plane_pose(
                    binding, measured_position, allow_folded=state.phase == ManipulationPhase.PREPARE,
                )
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
            integration_gain_s=1.0 if robot_name in self._retain_on_failure else 6.0,
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
        if state.phase == ManipulationPhase.PREPARE:
            if state.cartesian is not None:
                plan = state.cartesian
                position, orientation = self._end_effector_pose(binding)
                plan.reference_position_w = tuple(position)
                plan.reference_orientation_wxyz = tuple(_normalize_quaternion(orientation))
                cache = getattr(self._env, "_eai_planar_ik_states", {})
                cache.pop((robot_name, str(binding.model_spec.model)), None)
                state.command_pose = None
                state.phase_goal = None
                state.ee_orientation_relative_b_wxyz = None
                self._set_cartesian_phase(state, ManipulationPhase.PREGRASP,
                                          plan.pregrasp_position_w, plan.contact_orientation_wxyz)
                return
            current, target = state.command_pose, state.target_pose
            if current is None or target is None:
                self._fail(state, "prepare is missing a cylindrical target")
                return
            self._set_phase(state, ManipulationPhase.ROTATE,
                            CylindricalPose(current.x_m, current.z_m, target.yaw_rad), "yaw")
            return
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

    def _step_cartesian(self, robot_name: str, binding: _ManipulatorBinding, state: RobotManipulationState, dt: float) -> None:
        plan = state.cartesian
        if plan is None or state.cartesian_goal_position_w is None or state.cartesian_goal_orientation_wxyz is None:
            self._fail(state, "cartesian phase is missing its waypoint")
            return
        try:
            goal_position = state.cartesian_goal_position_w
            goal_orientation = state.cartesian_goal_orientation_wxyz
            if state.phase in (ManipulationPhase.PREGRASP, ManipulationPhase.APPROACH, ManipulationPhase.LIFT):
                remaining = math.dist(plan.reference_position_w, goal_position)
                if remaining > 0.0:
                    step = min(remaining, plan.approach_speed_m_s * dt)
                    scale = step / remaining
                    plan.reference_position_w = tuple(
                        plan.reference_position_w[index]
                        + (goal_position[index] - plan.reference_position_w[index]) * scale
                        for index in range(3)
                    )
                    fraction = 1.0 - (remaining - step) / max(plan.phase_distance_m, 1.0e-9)
                    plan.reference_orientation_wxyz = _quat_slerp(
                        plan.phase_start_orientation_wxyz, goal_orientation, fraction,
                    )
            self._drive(robot_name, binding, plan.reference_position_w, plan.reference_orientation_wxyz, state)
            if state.held is not None:
                self._sync_follow_proxy(state.held, binding)
            measured_position, measured_orientation = self._end_effector_pose(binding)
            state.phase_steps += 1
            reached = self._cartesian_reached(state, binding, measured_position, measured_orientation, dt)
            if state.phase == ManipulationPhase.GRASP:
                if reached and self._gripper_at_target(binding, state):
                    state.settle_elapsed_s += dt
                    if state.settle_elapsed_s + 1.0e-12 >= state.settle_duration_s:
                        self._attach_after_grasp(robot_name, binding, state)
                elif state.settle_elapsed_s > 0.0:
                    state.settle_elapsed_s = 0.0
            elif state.phase == ManipulationPhase.RELEASE:
                if reached and self._gripper_at_target(binding, state):
                    state.settle_elapsed_s += dt
                    if state.settle_elapsed_s + 1.0e-12 >= state.settle_duration_s:
                        self._release_after_place(robot_name, binding, state)
                elif state.settle_elapsed_s > 0.0:
                    state.settle_elapsed_s = 0.0
            elif reached:
                self._advance_cartesian_phase(robot_name, state)
            if state.phase not in (ManipulationPhase.IDLE, ManipulationPhase.ATTACHED_HOLD, ManipulationPhase.FAILED) and state.phase_steps >= state.phase_timeout_steps:
                self._fail(state, f"{state.operation or 'manipulation'} timed out in {state.phase.value}")
        except Exception as exc:
            self._fail(state, f"{state.operation or 'manipulation'} failed: {exc}")

    def _cartesian_reached(
        self,
        state: RobotManipulationState,
        binding: _ManipulatorBinding,
        measured_position_w: Sequence[float],
        measured_orientation_wxyz: Sequence[float],
        dt: float,
    ) -> bool:
        plan = state.cartesian
        goal = state.cartesian_goal_position_w
        goal_orientation = state.cartesian_goal_orientation_wxyz
        if plan is None or goal is None or goal_orientation is None:
            return False
        # The rate-limited reference must finish before the measured arm is
        # judged; otherwise the residual distance becomes an instantaneous
        # target jump at GRASP/RELEASE.
        if math.dist(plan.reference_position_w, goal) > 1.0e-9:
            state.settle_elapsed_s = 0.0
            return False
        if math.dist(measured_position_w, goal) > state.tolerance_m:
            state.settle_elapsed_s = 0.0
            return False
        if _quat_angle_rad(measured_orientation_wxyz, goal_orientation) > plan.orientation_tolerance_rad:
            state.settle_elapsed_s = 0.0
            return False
        if state.grasp_tool is not None:
            actual_tcp, _ = state.grasp_tool.tcp_pose(measured_position_w, measured_orientation_wxyz)
            target_tcp, _ = state.grasp_tool.tcp_pose(goal, goal_orientation)
            if math.dist(actual_tcp, target_tcp) > state.tolerance_m:
                state.settle_elapsed_s = 0.0
                return False
        else:
            actual_tcp = tuple(measured_position_w)
        previous_tcp = state.arrival_tcp_position_w
        state.arrival_tcp_position_w = tuple(actual_tcp)
        if previous_tcp is None:
            state.settle_elapsed_s = 0.0
            return False
        if math.dist(previous_tcp, actual_tcp) / max(dt, 1.0e-12) > DEFAULT_CARTESIAN_STABLE_LINEAR_SPEED_M_S:
            state.settle_elapsed_s = 0.0
            return False
        sample = self._cartesian_joint_sample(binding)
        if sample is not None:
            previous = state.arrival_joint_sample
            state.arrival_joint_sample = sample
            if previous is None or len(previous) != len(sample):
                state.settle_elapsed_s = 0.0
                return False
            step_limit = 0.02 * dt
            if any(abs(a - b) > step_limit for a, b in zip(sample, previous)):
                state.settle_elapsed_s = 0.0
                return False
        if state.phase in (ManipulationPhase.PREGRASP, ManipulationPhase.LIFT):
            state.settle_elapsed_s += dt
            return state.settle_elapsed_s + 1.0e-12 >= plan.arrival_dwell_s
        return True

    @staticmethod
    def _cartesian_joint_sample(binding: _ManipulatorBinding) -> tuple[float, ...] | None:
        """Measured arm joints, or None for lightweight doubles without data."""
        joint_pos = getattr(getattr(binding.arm, "data", None), "joint_pos", None)
        if joint_pos is None:
            return None
        ids = []
        for joint_id in (binding.yaw_joint_id,) + binding.ik_joint_ids + binding.fixed_joint_ids:
            if joint_id is not None and joint_id not in ids:
                ids.append(joint_id)
        try:
            return tuple(float(joint_pos[0, joint_id]) for joint_id in ids)
        except (AttributeError, IndexError, TypeError, ValueError):
            return None

    def _advance_cartesian_phase(self, robot_name: str, state: RobotManipulationState) -> None:
        plan = state.cartesian
        if plan is None:
            self._fail(state, "cartesian phase transition is missing its plan")
            return
        del robot_name
        if state.operation == "pick":
            if state.phase == ManipulationPhase.PREGRASP:
                self._set_cartesian_phase(
                    state, ManipulationPhase.APPROACH,
                    plan.contact_position_w, plan.contact_orientation_wxyz,
                )
            elif state.phase == ManipulationPhase.APPROACH:
                self._set_cartesian_phase(
                    state, ManipulationPhase.GRASP,
                    plan.contact_position_w, plan.contact_orientation_wxyz,
                )
            elif state.phase == ManipulationPhase.LIFT:
                state.phase = ManipulationPhase.ATTACHED_HOLD
                state.cartesian_goal_position_w = plan.retreat_position_w
                state.cartesian_goal_orientation_wxyz = plan.contact_orientation_wxyz
                state.phase_steps = 0
                state.arrival_joint_sample = None
                state.arrival_tcp_position_w = None
            return
        if state.operation == "place":
            if state.phase == ManipulationPhase.PREGRASP:
                self._set_cartesian_phase(
                    state, ManipulationPhase.APPROACH,
                    plan.contact_position_w, plan.contact_orientation_wxyz,
                )
            elif state.phase == ManipulationPhase.APPROACH:
                self._set_cartesian_phase(
                    state, ManipulationPhase.RELEASE,
                    plan.contact_position_w, plan.contact_orientation_wxyz,
                )
            elif state.phase == ManipulationPhase.LIFT:
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
        if state.cartesian is not None:
            self._set_cartesian_phase(
                state, ManipulationPhase.LIFT,
                state.cartesian.retreat_position_w, state.cartesian.contact_orientation_wxyz,
            )
            return
        target = state.target_pose
        recovery = state.recovery_pose
        if target is None or recovery is None:
            self._fail(state, "pick cannot lift without target and recovery pose")
            return
        self._set_phase(state, ManipulationPhase.LIFT, CylindricalPose(target.x_m, recovery.z_m, target.yaw_rad), "z")

    def _release_after_place(self, robot_name: str, binding: _ManipulatorBinding, state: RobotManipulationState) -> None:
        held = state.held
        if held is None:
            self._fail(state, "place cannot release without a held object")
            return
        state.release_started = True
        self._filter_payload_during_retreat(binding, state)
        self._release_follow_attachment(held, binding)
        state.release_started = False
        if self._object_owners.get(held.object_body_path) == robot_name:
            self._object_owners.pop(held.object_body_path, None)
        state.held = None
        if state.cartesian is not None:
            self._set_cartesian_phase(
                state, ManipulationPhase.LIFT,
                state.cartesian.retreat_position_w, state.cartesian.contact_orientation_wxyz,
            )
            return
        recovery = state.recovery_pose
        target = state.target_pose
        if recovery is None or target is None:
            self._fail(state, "place cannot release without held and recovery state")
            return
        self._set_phase(state, ManipulationPhase.LIFT, CylindricalPose(target.x_m, recovery.z_m, target.yaw_rad), "z")

    def _finish_idle(self, state: RobotManipulationState) -> None:
        # Both reachable call sites (move completion, place completion after
        # RELEASE cleared the held object) arrive with nothing carried; a held
        # object left in IDLE would freeze its proxy updates.
        assert state.held is None
        self._restore_payload_contact(state)
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
        state.cartesian = None
        state.cartesian_goal_position_w = None
        state.cartesian_goal_orientation_wxyz = None

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
        model = str(getattr(binding.model_spec, "model", "")).strip().lower()
        if model not in {"ur5", "z1", "d1"}:
            return None
        if model == "d1":
            if not getattr(binding.model_spec, "gripper_joint_names", None):
                return None
        elif not getattr(binding.model_spec, "gripper_joint_name", None):
            return None
        spec = binding.planar_spec
        if model == "d1":
            # The single radian-valued fields cannot describe D1's mirrored
            # meter-valued fingers, so open/closed carry the closure
            # coefficient (0.0 open, 1.0 closed) on this branch.
            open_position = DEFAULT_D1_GRIPPER_OPEN_CLOSURE
            closed_position = DEFAULT_D1_GRIPPER_CLOSED_CLOSURE
        else:
            open_position = spec.gripper_open_position_rad
            closed_position = spec.gripper_closed_position_rad
        if state.grasp_tool is not None:
            open_position = state.grasp_tool.open_position_rad
            closed_position = state.grasp_tool.closed_position_rad
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
        if state.held is not None:
            return closed_position
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
        if str(getattr(binding.model_spec, "model", "")).strip().lower() == "d1":
            names = tuple(getattr(binding.model_spec, "gripper_joint_names", ()) or ())
            if not names:
                return False
            try:
                gripper_ids, _ = binding.arm.find_joints(list(names), preserve_order=True)
                if len(gripper_ids) != len(names):
                    return False
                measured = getattr(getattr(binding.arm, "data", None), "joint_pos", None)
                if measured is None:
                    return True
                wanted = _d1_gripper_targets(target)
                return all(
                    abs(float(measured[0, int(gripper_id)]) - goal)
                    <= DEFAULT_D1_GRIPPER_POSITION_TOLERANCE_M
                    for gripper_id, goal in zip(gripper_ids, wanted)
                )
            except (AttributeError, IndexError, TypeError, ValueError):
                return False
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
        if str(getattr(binding.model_spec, "model", "")).strip().lower() == "d1":
            self._hold_d1_gripper(binding, state, gripper_position)
            return
        if gripper_position is not None:
            # Use the same stage-driven open/close sequence as Z1, with a
            # physics-rate target ramp so Robotiq's fingers visibly animate.
            # The measured arrival gate still checks the final open/closed
            # angle, never an intermediate animation target.
            if state.gripper_goal_rad != gripper_position or state.gripper_command_rad is None:
                ids, _ = binding.arm.find_joints(
                    [str(binding.model_spec.gripper_joint_name)], preserve_order=True)
                measured = getattr(getattr(binding.arm, "data", None), "joint_pos", None)
                if measured is not None and ids:
                    state.gripper_command_rad = float(measured[0, int(ids[0])])
                else:
                    state.gripper_command_rad = gripper_position
                state.gripper_goal_rad = gripper_position
            scale = 1.0 if state.cartesian is None else state.cartesian.motion_speed_scale
            step = (DEFAULT_GRIPPER_ANIMATION_SPEED_RAD_S * scale
                    * self._physics_step_duration_s())
            state.gripper_command_rad += max(-step, min(step, gripper_position - state.gripper_command_rad))
            gripper_position = state.gripper_command_rad
        if not hold_gripper_position(
            binding.arm, binding.model_spec, position_rad=gripper_position,
        ):
            raise RuntimeError("configured gripper joint is unavailable")

    def _hold_d1_gripper(self, binding: _ManipulatorBinding, state: RobotManipulationState, closure_target: float | None) -> None:
        """Animate D1's mirrored prismatic fingers by closure coefficient.

        ``gripper_command_rad`` carries the 0..1 closure (not radians) on this
        branch; the ramp therefore seeds from the measured stroke of the first
        finger instead of the meter-valued joint itself.  The measured arrival
        gate in :meth:`_gripper_at_target` still checks final finger strokes.
        """
        names = tuple(getattr(binding.model_spec, "gripper_joint_names", ()) or ())
        if not names:
            raise RuntimeError("configured gripper joint is unavailable")
        gripper_ids, _ = binding.arm.find_joints(list(names), preserve_order=True)
        if not gripper_ids or len(gripper_ids) != len(names):
            raise RuntimeError("configured gripper joint is unavailable")
        measured = getattr(getattr(binding.arm, "data", None), "joint_pos", None)
        if closure_target is None:
            if not hold_gripper_position(binding.arm, binding.model_spec):
                raise RuntimeError("configured gripper joint is unavailable")
            return
        if state.gripper_goal_rad != closure_target or state.gripper_command_rad is None:
            current = float(closure_target)
            if measured is not None:
                stroke = float(measured[0, int(gripper_ids[0])])
                current = min(1.0, max(0.0, 1.0 - stroke / DEFAULT_D1_GRIPPER_STROKE_M))
            state.gripper_command_rad = current
            state.gripper_goal_rad = closure_target
        scale = 1.0 if state.cartesian is None else state.cartesian.motion_speed_scale
        step = (DEFAULT_GRIPPER_ANIMATION_SPEED_RAD_S * scale
                * self._physics_step_duration_s())
        state.gripper_command_rad += max(-step, min(step, closure_target - state.gripper_command_rad))
        stroke, mirrored = _d1_gripper_targets(state.gripper_command_rad)
        if measured is None:
            binding.arm.set_joint_position_target((stroke, mirrored), joint_ids=list(gripper_ids))
            return
        import torch

        wanted = torch.tensor([[stroke, mirrored]], device=measured.device, dtype=measured.dtype)
        limits = binding.arm.data.soft_joint_pos_limits[:, list(gripper_ids)]
        wanted = torch.maximum(torch.minimum(wanted, limits[..., 1]), limits[..., 0])
        binding.arm.set_joint_position_target(wanted, joint_ids=list(gripper_ids))

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
        self._restore_payload_contact(state)

    def _filter_payload_during_retreat(self, binding, state):
        """Restore table support at release without the opening jaw kicking it.

        Only source-object/robot pairs are temporarily excluded. Environment
        contacts remain enabled and the original relationship is restored
        after retreat, on empty stop, or reset.
        """
        if state.grasp_tool is None or state.held is None or state.payload_collision_filter is not None:
            return
        from pxr import UsdPhysics, Sdf
        prim = self._stage().GetPrimAtPath(state.held.object_body_path)
        had_api = prim.HasAPI(UsdPhysics.FilteredPairsAPI)
        relation = prim.GetRelationship('physics:filteredPairs')
        had_opinion = bool(relation and relation.HasAuthoredTargets())
        old_targets = list(relation.GetTargets()) if relation else []
        api = UsdPhysics.FilteredPairsAPI.Apply(prim)
        relation = api.CreateFilteredPairsRel()
        state.payload_collision_filter = (str(prim.GetPath()), had_api, had_opinion, old_targets)
        paths = binding.arm.root_physx_view.link_paths[0]
        relation.SetTargets(list(dict.fromkeys(old_targets + [Sdf.Path(p) for p in paths])))
        self._flush_physx_transforms()

    def _restore_payload_contact(self, state):
        saved = state.payload_collision_filter
        if saved is None:
            return
        from pxr import UsdPhysics
        path, had_api, had_opinion, targets = saved
        prim = self._stage().GetPrimAtPath(path)
        if not prim or not prim.IsValid():
            raise RuntimeError('payload disappeared before contact-filter restoration')
        relation = prim.GetRelationship('physics:filteredPairs')
        if had_opinion:
            relation.SetTargets(targets)
        else:
            relation.ClearTargets(True)
        if not had_api:
            prim.RemoveAPI(UsdPhysics.FilteredPairsAPI)
        self._flush_physx_transforms()
        state.payload_collision_filter = None

    def _fail(self, state: RobotManipulationState, reason: str) -> None:
        name = next((robot for robot, candidate in self._states.items() if candidate is state), "")
        if name in self._retain_on_failure:
            try:
                self.stop_retaining_object(name, reason)
            except Exception as exc:
                state.last_error = f"{reason}; safe stop pending: {exc}"
                state.phase = ManipulationPhase.FAILED
            return
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
        state.cartesian = None
        state.cartesian_goal_position_w = None
        state.cartesian_goal_orientation_wxyz = None


    def grasp_tool_for_object(self, robot_name, object_path, tcp_orientation_wxyz):
        """Compute finger geometry from the live object's projected bounds."""
        from EAI.manipulation_geometry import GraspToolFrame, gripper_grasp_tool
        binding = self._binding(robot_name, disable_collisions=False)
        model = binding.model_spec.model
        joint = getattr(binding.model_spec, 'gripper_joint_name', None)
        if model == 'd1':
            from pxr import UsdGeom
            stage = self._stage()
            prim = stage.GetPrimAtPath(object_path)
            cache = UsdGeom.BBoxCache(0, ['default', 'render', 'proxy'])
            bounds = cache.ComputeUntransformedBound(prim).ComputeAlignedRange()
            if bounds.IsEmpty():
                raise ValueError('cannot determine object dimensions for gripper opening')
            matrix = UsdGeom.XformCache().GetLocalToWorldTransform(prim)
            points = [_quat_rotate(_quat_conjugate(tcp_orientation_wxyz),
                      tuple(matrix.Transform(bounds.GetCorner(i)))) for i in range(8)]
            extents = [max(p[i] for p in points) - min(p[i] for p in points) for i in range(3)]
            # At contact the tool axis opposes the approach, so the opening is
            # the object's TCP-frame Y extent and the depth its Z extent.
            width, depth = extents[1], extents[2]
            if not 0.005 <= width <= DEFAULT_D1_MAX_OPENING_M:
                raise ValueError('object dimensions exceed the D1 gripper opening')
            return GraspToolFrame(
                (0.0, 0.0, DEFAULT_D1_TCP_OFFSET_M), (0., 1., 0., 0.),
                DEFAULT_D1_GRIPPER_OPEN_CLOSURE, DEFAULT_D1_GRIPPER_CLOSED_CLOSURE,
                width, depth,
            )
        if (model, joint) not in (('ur5', 'finger_joint'), ('z1', 'jointGripper')):
            return GraspToolFrame()
        from pxr import UsdGeom
        stage = self._stage()
        prim = stage.GetPrimAtPath(object_path)
        cache = UsdGeom.BBoxCache(0, ['default', 'render', 'proxy'])
        bounds = cache.ComputeUntransformedBound(prim).ComputeAlignedRange()
        if bounds.IsEmpty():
            raise ValueError('cannot determine object dimensions for gripper opening')
        matrix = UsdGeom.XformCache().GetLocalToWorldTransform(prim)
        points = [_quat_rotate(_quat_conjugate(tcp_orientation_wxyz),
                  tuple(matrix.Transform(bounds.GetCorner(i)))) for i in range(8)]
        extents = [max(p[i] for p in points) - min(p[i] for p in points) for i in range(3)]
        return gripper_grasp_tool(model, extents[1 if model == 'ur5' else 0], extents[2])

    def configure_grasp_tool(self, robot_name, tool):
        state = self._states[robot_name]
        if state.operation != 'pick' or state.held is not None:
            raise RuntimeError('grasp tool can only be configured before attaching a pick')
        state.grasp_tool = tool if tool.open_position_rad is not None else None

    def _binding(self, robot_name: str, *, disable_collisions: bool = True) -> _ManipulatorBinding:
        cached = self._bindings.get(robot_name)
        if cached is not None:
            if disable_collisions:
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
            if disable_collisions:
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

        # The unified backend isolates the source object before approach.
        # It must retain finger/environment contacts, including the table.
        # Preserve the old scalar-controller behaviour for other callers.
        if robot_name in self._retain_on_failure:
            return
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

        if self._dispatcher:
            if _env_ids is None:
                selected = range(self._environment_count)
            else:
                values = _env_ids.tolist() if hasattr(_env_ids, "tolist") else _env_ids
                if isinstance(values, int) and not isinstance(values, bool):
                    values = [values]
                if not isinstance(values, (tuple, list, set, range)):
                    raise ValueError("env_ids must be a sequence of environment indices")
                selected = []
                for env_id in values:
                    validate_env_id(self._env, env_id)
                    if env_id not in selected:
                        selected.append(env_id)
            for env_id in selected:
                self._environment_runtimes[env_id].reset()
            return

        if _env_ids is not None:
            values = _env_ids.tolist() if hasattr(_env_ids, "tolist") else _env_ids
            if isinstance(values, int) and not isinstance(values, bool):
                values = [values]
            if not isinstance(values, (tuple, list, set, range)):
                raise ValueError("env_ids must be a sequence of environment indices")
            for env_id in values:
                validate_env_id(self._env, env_id)
            if 0 not in values:
                return

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
        if self._dispatcher:
            for runtime in self._environment_runtimes:
                runtime.close()
            self._closed = True
            return
        self.reset()
        self._closed = True


__all__ = [
    "CylindricalPose",
    "CartesianPlan",
    "DEFAULT_ATTACHMENT_MODE",
    "DEFAULT_ATTACHMENT_SETTLE_DURATION_S",
    "DEFAULT_CARTESIAN_SPEED_M_S",
    "DEFAULT_GRIPPER_POSITION_TOLERANCE_RAD",
    "DEFAULT_UR5_GRIPPER_CLOSED_POSITION_RAD",
    "DEFAULT_UR5_GRIPPER_OPEN_POSITION_RAD",
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
    "cartesian_place_contact_pose",
    "cylindrical_to_local_position",
    "cylindrical_to_world_position",
    "local_position_to_cylindrical",
    "planar_ik_spec_for_model",
    "feasible_yaw_joint_target",
    "normalize_cylindrical_pose",
    "parse_cylindrical_pose",
    "wrap_angle_rad",
]
