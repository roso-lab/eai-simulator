"""Native ground-navigation I/O for an already-created EAI environment.

The provider computes commands. This adapter reads root state and applies those
commands through env.step; the host retains session and evaluator ownership.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import importlib
import math
from typing import Any, Callable, Mapping, Sequence

from algorithm.skills.contracts import SkillContractError, SkillRequest
from algorithm.skills.navigation.ground_navigation import (
    GroundNavigationProvider, GroundNavigationResponse, GroundPose,
    normalize_ground_navigation_request,
)


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SkillContractError(f"{name} must be finite numeric")
    return float(value)


def _vector(value: Any, size: int) -> tuple[float, ...]:
    for method_name in ("detach", "cpu", "tolist"):
        method = getattr(value, method_name, None)
        if callable(method):
            value = method()
    if isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], (list, tuple)):
        value = value[0]
    if not isinstance(value, (list, tuple)) or len(value) != size:
        raise SkillContractError("native root state must describe one environment")
    return tuple(_number(item, "native root state") for item in value)


def read_ground_pose(env: Any, robot_id: str) -> GroundPose:
    """Read actual root XYZ and a normalized wxyz quaternion from EAI."""
    if int(getattr(env, "num_envs", 1)) != 1:
        raise SkillContractError("ground executor requires one simulator environment")
    data = env.scene[robot_id].data
    position = _vector(data.root_pos_w, 3)
    w, x, y, z = _vector(data.root_quat_w, 4)
    norm = math.sqrt(w * w + x * x + y * y + z * z)
    if norm <= 1e-12:
        raise SkillContractError("native root quaternion must be nonzero")
    w, x, y, z = (value / norm for value in (w, x, y, z))
    yaw = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    return GroundPose(position, yaw)


def read_simulation_time(env: Any) -> float:
    sim = getattr(env, "sim", None)
    current = getattr(sim, "current_time", None)
    if current is not None:
        return _number(current, "simulation time")
    counter, step_dt = getattr(env, "common_step_counter", None), getattr(env, "step_dt", None)
    if type(counter) is not int or counter < 0:
        raise SkillContractError("a simulation_time_reader is required")
    return counter * _number(step_dt, "simulation step interval")


def _default_action(env: Any, robot_id: str, velocity: Sequence[float]) -> Any:
    del robot_id
    torch = importlib.import_module("torch")
    return torch.tensor([list(velocity)], dtype=torch.float32, device=env.device)


class GroundNavigationExecutor:
    """Drive dispatch/control ticks with injected topology and state validation.

    An advance_hook may merge the command into the host's multi-robot loop.
    Every task owns its timing and command evidence; simulator creation and
    physical verdicts remain outside this executor.
    """

    def __init__(
        self,
        env: Any,
        *,
        topology: Any,
        world_state_validator: Callable[[int, bool], None],
        provider: GroundNavigationProvider | None = None,
        pose_reader: Callable[[str], GroundPose] | None = None,
        support_height_reader: Callable[[str], float] | None = None,
        simulation_time_reader: Callable[[], float] | None = None,
        action_factory: Callable[[Any, str, Sequence[float]], Any] | None = None,
        advance_hook: Callable[[Mapping[str, Any]], Any] | None = None,
        timeout_s: float | None = None,
    ):
        if not callable(world_state_validator):
            raise SkillContractError("a world_state_validator is required")
        if timeout_s is not None and _number(timeout_s, "timeout_s") <= 0:
            raise SkillContractError("timeout_s must be positive")
        self.env, self.topology = env, topology
        self.provider = provider if provider is not None else GroundNavigationProvider()
        self._validate = world_state_validator
        self._pose = pose_reader if pose_reader is not None else lambda robot: read_ground_pose(env, robot)
        self._support_height = support_height_reader
        self._time = simulation_time_reader if simulation_time_reader is not None else lambda: read_simulation_time(env)
        self._action = action_factory if action_factory is not None else _default_action
        self._advance = advance_hook if advance_hook is not None else env.step
        self.timeout_s = timeout_s
        self._last_times: dict[str, float] = {}
        self._deadlines: dict[str, float] = {}
        self._samples: dict[str, list[dict[str, Any]]] = {}

    def _observation(self, request: SkillRequest, *, motion: bool) -> dict[str, Any]:
        sequence = request.parameters["world_state_seq"]
        self._validate(sequence, motion)
        observation = {"world_state_seq": sequence, "valid": True, "fresh": motion}
        if motion:
            pose = self._pose(request.robot_id)
            if not isinstance(pose, GroundPose):
                raise SkillContractError("pose_reader must return GroundPose")
            if self._support_height is not None:
                pose = replace(pose, support_z_m=_number(self._support_height(request.robot_id), "support height"))
            observation["pose"] = pose
            if request.parameters.get("intent", "start") in {"start", "replan"}:
                observation["topology"] = (
                    self.topology(request.robot_id) if callable(self.topology) else self.topology)
        return observation

    def dispatch(self, value: SkillRequest | Mapping[str, Any]) -> GroundNavigationResponse:
        request = normalize_ground_navigation_request(value)
        key = request.task_id or request.request_id
        observation = self._observation(request, motion=False)
        now = _number(self._time(), "simulation time")
        response = self.provider.dispatch(request, observation=observation)
        self._last_times[key] = now
        if self.timeout_s is not None:
            self._deadlines[key] = now + self.timeout_s
        self._samples[key] = []
        return self._apply(response, now=now)

    def control(self, value: SkillRequest | Mapping[str, Any]) -> GroundNavigationResponse:
        try:
            request = normalize_ground_navigation_request(value)
        except SkillContractError as exc:
            if isinstance(value, SkillRequest):
                key, robot_id = value.task_id or value.request_id, value.robot_id
            elif isinstance(value, Mapping):
                key, robot_id = value.get("task_id") or value.get("request_id"), value.get("robot_id")
            else:
                raise
            if isinstance(key, str) and key in self.provider.active_task_ids:
                if self.provider.response(key).plan.robot_id == robot_id:
                    return self._apply(self.provider.fail(key, exc), now=None)
            raise
        key = request.task_id or request.request_id
        if key not in self.provider.active_task_ids:
            raise SkillContractError("ground navigation task is not active")
        original = self.provider.response(key)
        if original.plan.robot_id != request.robot_id:
            raise SkillContractError("ground navigation robot_id does not match the task")
        now = None
        try:
            now = _number(self._time(), "simulation time")
            intent = request.parameters.get("intent", "start")
            # Explicit hold/stop stay available even after the motion deadline.
            if intent not in {"hold", "stop"} and now >= self._deadlines.get(key, math.inf):
                response = self.provider.fail(key, TimeoutError(), reason_code="timeout")
                return self._apply(response, now=now)
            dt = now - self._last_times[key]
            if intent not in {"hold", "stop"} and dt <= 0:
                raise SkillContractError("simulation time must advance between control ticks")
            motion = intent in {"start", "continue", "replan"}
            observation = self._observation(request, motion=motion)
            response = self.provider.control(request, observation=observation, dt_s=dt)
            self._last_times[key] = now
            pose = observation.get("pose")
            return self._apply(response, now=now, pose=pose)
        except Exception as exc:
            response = self.provider.fail(key, exc)
            return self._apply(response, now=now)

    def _send(self, response: GroundNavigationResponse) -> list[float]:
        command = response.plan.commands[0]
        payload = command.payload
        if command.command_type != "base_velocity" or payload.get("frame") != "base_link":
            raise SkillContractError("ground plan must use base_link base_velocity")
        velocity = list(_vector(payload["velocity"], 3))
        ticks = payload["horizon_ticks"]
        if type(ticks) is not int or ticks < 1:
            raise SkillContractError("horizon_ticks must be a positive integer")
        # A stop is applied once; no movement horizon can delay its acknowledgement.
        count = 1 if velocity == [0.0, 0.0, 0.0] else ticks
        for _ in range(count):
            if velocity != [0.0, 0.0, 0.0] and response.state["task_id"] in self._deadlines:
                if _number(self._time(), "simulation time") >= self._deadlines[response.state["task_id"]]:
                    raise TimeoutError("ground navigation execution window expired")
            self._advance({response.plan.robot_id: self._action(
                self.env, response.plan.robot_id, velocity)})
        return velocity

    def _apply(self, response: GroundNavigationResponse, *, now: float | None,
               pose: GroundPose | None = None) -> GroundNavigationResponse:
        key = response.state["task_id"]
        command = list(response.plan.commands[0].payload["velocity"])
        errors: dict[str, str] = {}
        try:
            self._send(response)
            applied = True
        except Exception as exc:
            self._samples[key].append({
                "simulation_time_s": now, "velocity": command, "command_applied": False,
                "execution_error_type": type(exc).__name__})
            response = self.provider.fail(
                key, exc, reason_code="timeout" if isinstance(exc, TimeoutError) else "execution_failure")
            command = [0.0, 0.0, 0.0]
            try:
                self._send(response)
                applied = True
            except Exception as zero_error:
                applied = False
                errors["zero_command_error_type"] = type(zero_error).__name__
        sample = {"simulation_time_s": now, "velocity": command, "command_applied": applied}
        if pose is not None:
            sample["position_m"], sample["yaw_rad"] = list(pose.position_m), pose.yaw_rad
            if pose.support_z_m is not None:
                sample["support_z_m"] = pose.support_z_m
        self._samples[key].append(sample)
        evidence = dict(response.private_evidence)
        evidence.update(errors)
        # Return the current sample while running; archive the complete trace
        # once terminal, avoiding a growing trace copy on every control tick.
        samples = self._samples[key] if response.state["status"] != "pending" else self._samples[key][-1:]
        evidence["execution_samples"] = deepcopy(samples)
        evidence["execution_sample_count"] = len(self._samples[key])
        evidence["command_applied"] = applied
        evidence["zero_command_applied"] = applied and command == [0.0, 0.0, 0.0]
        return replace(response, private_evidence=evidence)


__all__ = ["GroundNavigationExecutor", "read_ground_pose", "read_simulation_time"]
