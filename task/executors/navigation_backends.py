"""Controller adapters for the unified node-route navigation Skill.

The examples here intentionally own native command compilation.  A new robot
or motion controller registers another adapter without changing the Provider
or :class:`NavigationExecutor`.
"""
from __future__ import annotations

import math
import inspect
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from algorithm.skills.contracts import SkillCommand, SkillContractError, SkillRequest
from algorithm.skills.navigation import (
    NavigationFeedback, NavigationObservation, NavigationPose, NavigationProfile,
    NavigationTarget, aerial_navigation_profile, ground_navigation_profile,
)


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SkillContractError(f"{label} must be finite")
    return float(value)


def _vector(value: Any, size: int | tuple[int, ...]) -> tuple[float, ...]:
    for method_name in ("detach", "cpu", "tolist"):
        method = getattr(value, method_name, None)
        if callable(method):
            value = method()
    if isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], (list, tuple)):
        value = value[0]
    sizes = (size,) if isinstance(size, int) else size
    if not isinstance(value, (list, tuple)) or len(value) not in sizes:
        raise SkillContractError(f"controller feedback must contain {size} values")
    return tuple(_finite(item, "controller feedback") for item in value)


def _call(reader: Callable[..., Any], robot_id: str) -> Any:
    # Bind before invoking: a TypeError raised inside a callback must never
    # trigger a second call with different arguments (or repeat a side effect).
    try:
        signature = inspect.signature(reader)
    except (TypeError, ValueError):
        return reader(robot_id)
    try:
        signature.bind(robot_id)
    except TypeError:
        signature.bind()
        return reader()
    return reader(robot_id)


def _pose(value: Any) -> NavigationPose:
    if isinstance(value, NavigationPose):
        return value
    if isinstance(value, (list, tuple)) and len(value) == 3:
        return NavigationPose(tuple(_finite(item, "pose") for item in value))
    position = getattr(value, "position_m", None)
    yaw = getattr(value, "yaw_rad", 0.0)
    if isinstance(value, Mapping):
        position = value.get("position_m", value.get("position", value.get("position_xyz", position)))
        yaw = value.get("yaw_rad", value.get("yaw", yaw))
    if not isinstance(position, (list, tuple)) or len(position) != 3:
        raise SkillContractError("controller pose must contain XYZ")
    return NavigationPose(tuple(_finite(item, "pose") for item in position), _finite(yaw, "yaw"))


@runtime_checkable
class NavigationControllerAdapter(Protocol):
    robot_id: str
    profile: NavigationProfile

    def validate(self, request: SkillRequest) -> None: ...
    def observe(self) -> NavigationObservation: ...
    def apply_navigation_target(self, target: NavigationTarget,
                                observation: NavigationObservation) -> None: ...
    def hold(self, observation: NavigationObservation | None = None) -> None: ...
    def before_step(self) -> None: ...
    def after_step(self) -> NavigationFeedback: ...
    def request_stop(self, reason: str) -> None: ...


class PlanarVelocityNavigationAdapter:
    """Compile nodes to planar velocity; certify stop from measured motion.

    A three-value reader supplies planar vx/vy/yaw-rate. A six-value reader
    supplies XYZ linear and angular velocity and also rules out vertical or
    tilting motion when certifying stability. Routing always uses XY.
    """

    def __init__(self, robot_id: str, *, pose_reader: Callable[..., Any],
                 command_sink: Callable[[SkillCommand], Any] | None = None,
                 sequence_reader: Callable[..., Any] | None = None,
                 time_reader: Callable[..., Any] | None = None,
                 velocity_reader: Callable[..., Any] | None = None,
                 stopped_reader: Callable[..., Any] | None = None,
                 profile: NavigationProfile | None = None,
                 cruise_speed_m_s: float = 0.5,
                 max_yaw_rate_rad_s: float = 1.0,
                 turn_threshold_rad: float = math.pi / 6.0,
                 stable_ticks: int = 3):
        if not isinstance(robot_id, str) or not robot_id.strip() or not callable(pose_reader):
            raise SkillContractError("planar adapter requires robot_id and pose_reader")
        self.robot_id = robot_id
        self.profile = profile or ground_navigation_profile()
        if self.profile.metric != "xy":
            raise SkillContractError("planar velocity adapter requires an XY profile")
        self._pose_reader = pose_reader
        self._command_sink = command_sink
        self._sequence_reader = sequence_reader or (lambda *_: 0)
        self._time_reader = time_reader or (lambda *_: 0.0)
        self._velocity_reader = velocity_reader
        self._stopped_reader = stopped_reader
        self._last_command: SkillCommand | None = None
        self._stop_requested = False
        if type(stable_ticks) is not int or stable_ticks <= 0:
            raise SkillContractError("stable_ticks must be a positive integer")
        self._stable_ticks = stable_ticks
        self._stable_count = 0
        self.cruise_speed_m_s = _finite(cruise_speed_m_s, "cruise_speed_m_s")
        self.max_yaw_rate_rad_s = _finite(max_yaw_rate_rad_s, "max_yaw_rate_rad_s")
        self.turn_threshold_rad = _finite(turn_threshold_rad, "turn_threshold_rad")
        if self.cruise_speed_m_s <= 0 or self.max_yaw_rate_rad_s <= 0 or self.turn_threshold_rad < 0:
            raise SkillContractError("invalid planar adapter limits")

    @property
    def last_command(self) -> SkillCommand | None:
        return self._last_command

    def _write(self, command: SkillCommand) -> None:
        self._last_command = command
        if self._command_sink is not None:
            self._command_sink(command)

    def validate(self, request: SkillRequest) -> None:
        if self._velocity_reader is None and self._stopped_reader is None:
            raise SkillContractError("adapter requires measured stop feedback")
        if request.parameters.get("target_yaw") is not None and not self.profile.supports_final_heading:
            raise SkillContractError("adapter profile does not support target_yaw")

    def observe(self) -> NavigationObservation:
        seq = _call(self._sequence_reader, self.robot_id)
        if type(seq) is not int or seq < 0:
            raise SkillContractError("world_state_seq must be a nonnegative integer")
        return NavigationObservation(
            _pose(_call(self._pose_reader, self.robot_id)), seq,
            _finite(_call(self._time_reader, self.robot_id), "simulation_time_s"),
            True, True,
        )

    def apply_navigation_target(self, target: NavigationTarget,
                                observation: NavigationObservation) -> None:
        dx = target.position_m[0] - observation.pose.position_m[0]
        dy = target.position_m[1] - observation.pose.position_m[1]
        at_target = math.hypot(dx, dy) <= self.profile.arrival_radius_m
        desired = target.heading_rad if at_target and target.heading_rad is not None else math.atan2(dy, dx)
        error = math.atan2(math.sin(desired - observation.pose.yaw_rad), math.cos(desired - observation.pose.yaw_rad))
        angular = max(-self.max_yaw_rate_rad_s, min(self.max_yaw_rate_rad_s, 2.0 * error))
        # Reduce speed as the node approaches.  This keeps a command-driven
        # chassis inside the arrival radius long enough for the Provider to
        # advance to the next authored node instead of overshooting it.
        remaining = math.hypot(dx, dy)
        approach_scale = min(1.0, remaining / max(2.0 * self.profile.arrival_radius_m, 1e-9))
        linear = 0.0 if (at_target and target.heading_rad is not None) or abs(error) > self.turn_threshold_rad else self.cruise_speed_m_s * approach_scale
        self._stop_requested = False
        self._stable_count = 0
        self._write(SkillCommand("base_velocity", {
            "velocity": [linear, 0.0, angular], "frame": "base_link",
        }))

    def hold(self, observation: NavigationObservation | None = None) -> None:
        del observation
        if not self._stop_requested:
            self._stable_count = 0
        self._stop_requested = True
        self._write(SkillCommand("base_velocity", {
            "velocity": [0.0, 0.0, 0.0], "frame": "base_link",
        }))

    def before_step(self) -> None:
        return None

    def _stopped(self) -> bool:
        if self._stopped_reader is not None:
            return _call(self._stopped_reader, self.robot_id) is True
        if self._velocity_reader is not None:
            velocity = _vector(_call(self._velocity_reader, self.robot_id), (3, 6))
            if len(velocity) == 6:
                return (math.sqrt(sum(item * item for item in velocity[:3])) <= 0.04
                        and math.sqrt(sum(item * item for item in velocity[3:])) <= 0.08)
            return math.hypot(velocity[0], velocity[1]) <= 0.04 and abs(velocity[2]) <= 0.08
        return False

    def after_step(self) -> NavigationFeedback:
        stopped = self._stopped()
        self._stable_count = self._stable_count + 1 if stopped and self._stop_requested else 0
        settled = self._stable_count >= self._stable_ticks
        return NavigationFeedback("stopped" if settled else "running", stopped, settled)

    def request_stop(self, reason: str) -> None:
        del reason
        self.hold()


class WorldPositionNavigationAdapter:
    """Compile node targets into a world-frame ``goal_position`` command."""

    def __init__(self, robot_id: str, *, pose_reader: Callable[..., Any],
                 command_sink: Callable[[SkillCommand], Any] | None = None,
                 sequence_reader: Callable[..., Any] | None = None,
                 time_reader: Callable[..., Any] | None = None,
                 velocity_reader: Callable[..., Any] | None = None,
                 stopped_reader: Callable[..., Any] | None = None,
                 profile: NavigationProfile | None = None,
                 stable_ticks: int = 3):
        if not isinstance(robot_id, str) or not robot_id.strip() or not callable(pose_reader):
            raise SkillContractError("position adapter requires robot_id and pose_reader")
        self.robot_id = robot_id
        self.profile = profile or aerial_navigation_profile()
        if self.profile.metric != "xyz":
            raise SkillContractError("world position adapter requires an XYZ profile")
        self._pose_reader = pose_reader
        self._command_sink = command_sink
        self._sequence_reader = sequence_reader or (lambda *_: 0)
        self._time_reader = time_reader or (lambda *_: 0.0)
        self._velocity_reader = velocity_reader
        self._stopped_reader = stopped_reader
        self._last_command: SkillCommand | None = None
        self._last_target: tuple[float, float, float] | None = None
        self._stop_requested = False
        if type(stable_ticks) is not int or stable_ticks <= 0:
            raise SkillContractError("stable_ticks must be a positive integer")
        self._stable_ticks = stable_ticks
        self._stable_count = 0
        if self.profile.supports_final_heading:
            raise SkillContractError("world position adapter has no heading command sink")

    @property
    def last_command(self) -> SkillCommand | None:
        return self._last_command

    def _write(self, command: SkillCommand) -> None:
        self._last_command = command
        if self._command_sink is not None:
            self._command_sink(command)

    def validate(self, request: SkillRequest) -> None:
        if request.parameters.get("target_yaw") is not None:
            raise SkillContractError("world position adapter does not support target_yaw")
        if self._velocity_reader is None and self._stopped_reader is None:
            raise SkillContractError("adapter requires measured stop feedback")

    def observe(self) -> NavigationObservation:
        seq = _call(self._sequence_reader, self.robot_id)
        if type(seq) is not int or seq < 0:
            raise SkillContractError("world_state_seq must be a nonnegative integer")
        return NavigationObservation(
            _pose(_call(self._pose_reader, self.robot_id)), seq,
            _finite(_call(self._time_reader, self.robot_id), "simulation_time_s"),
            True, True,
        )

    def apply_navigation_target(self, target: NavigationTarget,
                                observation: NavigationObservation) -> None:
        if target.heading_rad is not None:
            raise SkillContractError("world position adapter cannot compile final heading")
        self._stop_requested = False
        self._stable_count = 0
        self._last_target = target.position_m
        self._write(SkillCommand("goal_position", {
            "target_pose": list(target.position_m), "frame": "world",
        }))

    def hold(self, observation: NavigationObservation | None = None) -> None:
        if observation is None:
            observation = self.observe()
        if not self._stop_requested:
            self._stable_count = 0
        self._last_target = observation.pose.position_m
        self._stop_requested = True
        self._write(SkillCommand("goal_position", {
            "target_pose": list(self._last_target), "frame": "world",
        }))

    def before_step(self) -> None:
        return None

    def _stopped(self) -> bool:
        if self._stopped_reader is not None:
            return _call(self._stopped_reader, self.robot_id) is True
        if self._velocity_reader is not None:
            velocity = _vector(_call(self._velocity_reader, self.robot_id), (3, 6))
            linear_ok = math.sqrt(sum(item * item for item in velocity[:3])) <= self.profile.stop_linear_speed_m_s
            angular_ok = (len(velocity) == 3 or
                          math.sqrt(sum(item * item for item in velocity[3:])) <= self.profile.stop_angular_speed_rad_s)
            return linear_ok and angular_ok
        return False

    def stop_diagnostics(self) -> dict[str, float]:
        return {"linear_speed_limit_m_s": self.profile.stop_linear_speed_m_s,
                "angular_speed_limit_rad_s": self.profile.stop_angular_speed_rad_s}

    def after_step(self) -> NavigationFeedback:
        stopped = self._stopped()
        self._stable_count = self._stable_count + 1 if stopped and self._stop_requested else 0
        settled = self._stable_count >= self._stable_ticks
        return NavigationFeedback("stopped" if settled else "running", stopped, settled)

    def request_stop(self, reason: str) -> None:
        # Arrival can keep the authored endpoint as a hover goal. Cancellation
        # and failures brake at the measured pose instead of pursuing a goal.
        if reason != "arrived" or self._last_target is None:
            self.hold()
            return
        if not self._stop_requested:
            self._stable_count = 0
        self._stop_requested = True
        self._write(SkillCommand("goal_position", {
            "target_pose": list(self._last_target), "frame": "world",
        }))


__all__ = [
    "NavigationControllerAdapter", "PlanarVelocityNavigationAdapter",
    "WorldPositionNavigationAdapter",
]
