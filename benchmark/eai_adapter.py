"""Small concrete adapters for the public EAI/Isaac session surface.

The module intentionally avoids importing Isaac, Torch or ROS at import time.
It can therefore be tested with fakes, while a real ``SimulatorSession`` is
accepted at runtime.  The adapter applies controller commands and advances the
environment through the common navigation executor; it never labels an
assignment as physically successful.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any, Callable, Mapping, Sequence

from algorithm.common_coordination.contract import Assignment, ExecutionFeedback, FailureCode
from algorithm.skills.contracts import SkillContractError, SkillPlan, SkillRequest
from algorithm.skills.navigation.node_route import NODE_ROUTE_SKILL_ID
from task.executors.navigation import NavigationExecutor
from task.resources import ResourceBusyError
import hashlib
import json


def _digest(value: Any) -> str:
    """Hash benchmark metadata without depending on a legacy navigation module."""

    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         allow_nan=False, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _as_vector(value: Any) -> list[float] | None:
    if value is None:
        return None
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "tolist"):
        value = value.tolist()
    while isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], (list, tuple)):
        value = value[0]
    if isinstance(value, (list, tuple)) and value and isinstance(value[0], (list, tuple)):
        value = value[0]
    try:
        values = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    if not values or not all(math.isfinite(item) for item in values):
        return None
    return values


def _robot_position(base_env: Any, robot_name: str) -> list[float] | None:
    articulations = getattr(getattr(base_env, "scene", None), "articulations", {}) or {}
    robot = articulations.get(robot_name) if hasattr(articulations, "get") else None
    return _as_vector(getattr(getattr(robot, "data", None), "root_pos_w", None))


def _batch_value(value: Any, num_envs: int) -> Any:
    """Expand a one-environment custom tensor/list to the EAI batch shape."""

    if num_envs <= 1:
        return value
    shape = getattr(value, "shape", None)
    if shape is not None and len(shape) == 1:
        try:
            return value.unsqueeze(0).repeat(num_envs, 1)
        except (AttributeError, TypeError, RuntimeError):
            pass
    if isinstance(value, (list, tuple)) and value and not isinstance(value[0], (list, tuple)):
        return [list(value) for _ in range(num_envs)]
    return value


class NavigationSkillAdapter:
    """Plan benchmark navigation through the shared topology-bound executor."""

    skill_id = NODE_ROUTE_SKILL_ID

    def __init__(self, executor: NavigationExecutor) -> None:
        if not isinstance(executor, NavigationExecutor):
            raise TypeError("NavigationSkillAdapter requires NavigationExecutor")
        self.executor = executor
        self._requests: dict[str, SkillRequest] = {}
        self._plan_digests: dict[str, str] = {}

    def plan(self, request: SkillRequest, *, observation: Mapping[str, Any] | None = None) -> SkillPlan:
        del observation  # The registered adapter supplies the authoritative pose/sequence.
        try:
            plan = self.executor.plan(request)
        except (SkillContractError, TypeError, ValueError) as exc:
            return SkillPlan(
                request.request_id, request.skill_id, request.robot_id,
                status="invalid_request",
                metadata={"reason": str(exc), "provider_id": "node_route.provider"},
            )
        self._requests[request.request_id] = SkillRequest.from_mapping(request.to_mapping())
        self._plan_digests[request.request_id] = _digest(plan.to_mapping())
        return plan

    def request_for(self, plan: SkillPlan) -> SkillRequest:
        try:
            request = self._requests[plan.request_id]
        except KeyError as exc:
            raise SkillContractError("navigation plan was not produced by this adapter") from exc
        if (plan.skill_id, plan.robot_id) != (request.skill_id, request.robot_id):
            raise SkillContractError("navigation plan identity differs from request")
        if _digest(plan.to_mapping()) != self._plan_digests[plan.request_id]:
            raise SkillContractError("navigation plan differs from provider output")
        return SkillRequest.from_mapping(request.to_mapping())


def navigation_request_factory(assignment: Assignment, task: Any, *, request_id: str) -> SkillRequest:
    """Build the concrete navigation request for a task graph assignment.

    Task contracts must carry a symbolic ``target_ref``.  The topology-bound
    Provider resolves it; benchmark task metadata never contains a coordinate
    goal.
    """

    metadata = dict(getattr(task, "metadata", {}) or {})
    target_ref = getattr(task, "target_ref", None) or metadata.get("target_ref")
    if not isinstance(target_ref, str) or not target_ref.strip():
        raise ValueError("navigation task requires target_ref")
    if metadata.get("target_pose") is not None or metadata.get("goal_pose") is not None:
        raise ValueError("node-route tasks cannot contain target_pose")
    parameters = dict(metadata.get("skill_parameters", {}) or {})
    if "horizon_ticks" in metadata or "horizon_ticks" in parameters:
        raise ValueError("node-route tasks cannot contain horizon_ticks")
    skill_id = str(metadata.get("skill_id", NODE_ROUTE_SKILL_ID))
    if skill_id != NODE_ROUTE_SKILL_ID:
        raise ValueError(f"unsupported navigation skill id: {skill_id}")
    parameters.setdefault("world_state_seq", 0)
    parameters.setdefault("intent", "start")
    return SkillRequest(
        request_id=request_id,
        skill_id=NODE_ROUTE_SKILL_ID,
        robot_id=assignment.robot_id,
        target_ref=target_ref,
        task_id=assignment.task_id,
        parameters=parameters,
    )


def bind_navigation_request_factory(
    executor: NavigationExecutor,
    factory: Callable[..., SkillRequest] = navigation_request_factory,
) -> Callable[..., SkillRequest]:
    """Bind request snapshots to each registered adapter's current sequence."""

    def bound(assignment: Assignment, task: Any, *, request_id: str) -> SkillRequest:
        request = factory(assignment, task, request_id=request_id)
        observation = executor.adapter_for(request.robot_id).observe()
        sequence = getattr(observation, "world_state_seq", None)
        if type(sequence) is not int or sequence < 0:
            raise SkillContractError("navigation adapter returned an invalid world_state_seq")
        return replace(request, parameters={**request.parameters, "world_state_seq": sequence})

    return bound


@dataclass(frozen=True)
class EaiSessionRuntime:
    """``StepRuntime`` implementation backed by ``SimulatorSession``."""

    session: Any
    observation_provider: Callable[[Any], Mapping[str, Any]] | None = None
    tensor_factory: Callable[[Sequence[float], str], Any] | None = None

    def step(self, actions: Mapping[str, Any]) -> Any:
        env = getattr(self.session, "env", None)
        if env is None or not callable(getattr(env, "step", None)):
            raise RuntimeError("session.env.step is unavailable")
        # MultiRobotDirectEnv expects an action entry for every active agent.
        # Registered adapter sinks may replace individual neutral rows through
        # ``session.navigation_actions`` at the task-executor boundary.
        merged = dict(self.neutral_actions())
        merged.update(dict(actions))
        return env.step(merged)

    def neutral_actions(self) -> dict[str, Any]:
        agents = tuple(str(item) for item in (getattr(self.session, "possible_agents", ()) or ()))
        env_cfg = getattr(self.session, "env_cfg", None)
        controllers = getattr(env_cfg, "controllers", {}) or {}
        num_envs = int(getattr(self.session, "num_envs", 1) or 1)
        device = str(getattr(self.session, "device", "cpu"))
        result: dict[str, Any] = {}
        for robot_id in agents:
            entry = controllers.get(robot_id) if hasattr(controllers, "get") else None
            configs = entry if isinstance(entry, (list, tuple)) else ((entry,) if entry is not None else ())
            cfg = configs[0] if configs else None
            action_dim = getattr(cfg, "action_dim", None)
            if action_dim is None and isinstance(cfg, Mapping):
                action_dim = cfg.get("action_dim")
            control_mode = getattr(cfg, "control_mode", None)
            if control_mode is None and isinstance(cfg, Mapping):
                control_mode = cfg.get("control_mode")
            try:
                dim = int(action_dim) if action_dim is not None else (4 if control_mode == "rotor_velocity" else 3)
            except (TypeError, ValueError):
                dim = 3
            dim = max(1, dim)
            if self.tensor_factory is not None:
                result[robot_id] = _batch_value(self.tensor_factory([0.0] * dim, device), num_envs)
                continue
            try:
                import torch

                result[robot_id] = torch.zeros((num_envs, dim), dtype=torch.float32, device=device)
            except (ImportError, RuntimeError, TypeError):
                result[robot_id] = [[0.0] * dim for _ in range(num_envs)]
        return result

    def observe(self) -> Mapping[str, Any]:
        base_env = getattr(self.session, "base_env", None)
        robots: dict[str, dict[str, Any]] = {}
        for robot_id in tuple(getattr(self.session, "possible_agents", ()) or ()):
            position = _robot_position(base_env, str(robot_id))
            if position is not None:
                robots[str(robot_id)] = {"position": position}
        result: dict[str, Any] = {"robots": robots}
        sim_time = self.sim_time_s()
        if sim_time is not None:
            result["sim_time_s"] = sim_time
        if self.observation_provider is not None:
            external = dict(self.observation_provider(self.session) or {})
            external_robots = external.pop("robots", None)
            if isinstance(external_robots, Mapping):
                robots.update({str(key): value for key, value in external_robots.items()})
                result["robots"] = robots
            result.update(external)
        return result

    def observe_for_robot(self, robot_id: str) -> Mapping[str, Any]:
        """Default local skill view; global evaluator sampling stays separate.

        Additional local sensors/messages must be supplied by a registered
        information adapter. Global evaluator annotations are not inherited.
        """
        if robot_id not in tuple(getattr(self.session, "possible_agents", ())):
            raise ValueError("unknown observation recipient")
        position = _robot_position(getattr(self.session, "base_env", None), robot_id)
        return {"robots": {robot_id: {"position": position}}} if position is not None else {}

    def sim_time_s(self) -> float | None:
        """Read the public simulation clock when the environment exposes one."""

        base_env = getattr(self.session, "base_env", None)
        direct = getattr(base_env, "sim_time", None)
        if direct is not None:
            try:
                return float(direct)
            except (TypeError, ValueError):
                pass
        simulator = getattr(base_env, "sim", None)
        index = getattr(simulator, "current_time_step_index", None)
        get_dt = getattr(simulator, "get_physics_dt", None)
        if index is not None and callable(get_dt):
            try:
                return float(index) * float(get_dt())
            except (TypeError, ValueError):
                pass
        return None


class NavigationTaskExecutor:
    """Drive the shared navigation executor through benchmark host ticks."""

    def __init__(self, skill: NavigationSkillAdapter, *, max_steps: int = 1,
                 stop_steps: int = 8,
                 tensor_factory: Callable[[Sequence[float], str], Any] | None = None,
                 host_actions: Callable[[], Mapping[str, Any]] | Mapping[str, Any] | None = None) -> None:
        if not isinstance(skill, NavigationSkillAdapter):
            raise TypeError("NavigationTaskExecutor requires NavigationSkillAdapter")
        if type(max_steps) is not int or max_steps <= 0:
            raise ValueError("max_steps must be a positive integer")
        if type(stop_steps) is not int or stop_steps <= 0:
            raise ValueError("stop_steps must be a positive integer")
        self.skill = skill
        self.navigation = skill.executor
        self.max_steps = max_steps
        self.stop_steps = stop_steps
        self.tensor_factory = tensor_factory
        self.host_actions = host_actions

    def _host_actions(self, scene: Any) -> Mapping[str, Any]:
        source = self.host_actions
        if source is None:
            source = getattr(scene, "navigation_actions", None)
        value = source() if callable(source) else source
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise TypeError("navigation host actions must be a mapping")
        return dict(value)

    def execute(self, assignment: Assignment, task: Any, *, scene: Any,
                plan: SkillPlan, step: Callable[[Mapping[str, Any]], Any]) -> ExecutionFeedback:
        del task
        if plan.status not in {"planned", "completed"}:
            return self._failure(assignment, plan.metadata.get("reason", f"skill plan status is {plan.status}"),
                                 FailureCode.INVALID_PLAN)
        if plan.robot_id != assignment.robot_id:
            return self._failure(assignment, "plan robot differs from assignment", FailureCode.INVALID_PLAN)
        try:
            request = self.skill.request_for(plan)
            if request.task_id is not None and request.task_id != assignment.task_id:
                raise SkillContractError("navigation request task differs from assignment")
            response = self.navigation.dispatch(request)
        except ResourceBusyError as exc:
            return self._failure(assignment, str(exc), FailureCode.EXECUTION_FAILURE)
        except (SkillContractError, TypeError, ValueError) as exc:
            return self._failure(assignment, str(exc), FailureCode.INVALID_PLAN)

        key = str(response.state["task_id"])
        ticks = 0
        last_time = 0.0
        try:
            while ticks < self.max_steps + self.stop_steps:
                terminal = response.state["status"] != "pending"
                if terminal and not self.navigation.needs_host_tick(key):
                    break
                if not self.navigation.needs_host_tick(key):
                    if ticks >= self.max_steps:
                        response = self.navigation.cancel(key, "benchmark navigation tick budget exhausted")
                        break
                    intent = "start" if ticks == 0 else "continue"
                    request = replace(
                        request,
                        parameters={**request.parameters, "intent": intent,
                                    "world_state_seq": response.state["world_state_seq"]},
                    )
                    response = self.navigation.prepare(request)
                    if not self.navigation.needs_host_tick(key):
                        continue
                actions = EaiSessionRuntime(scene, tensor_factory=self.tensor_factory).neutral_actions()
                actions.update(self._host_actions(scene))
                boundary = step(actions)
                ticks += 1
                last_time = float(getattr(boundary, "sim_time_s", last_time))
                response = self.navigation.after_step(key)
                if ticks >= self.max_steps and response.state["status"] == "pending":
                    response = self.navigation.cancel(key, "benchmark navigation tick budget exhausted")
                    break
            else:
                response = self.navigation.cancel(key, "benchmark navigation stop tick budget exhausted")
        except Exception as exc:
            try:
                response = self.navigation.cancel(key, f"benchmark navigation failed: {exc}")
            except Exception:
                pass
            return self._failure(assignment, f"navigation execution failed: {exc}", FailureCode.EXECUTION_FAILURE)

        evidence = dict(response.private_evidence)
        controller = dict(evidence.get("controller_feedback", {}) or {})
        quiescent = (
            response.state.get("stop_confirmed") is True
            and response.state.get("quarantined") is False
            and controller.get("stopped") is True
            and controller.get("settled") is True
        )
        completed = response.state["status"] == "completed" and quiescent
        world_changes: dict[str, Any] = {
            "steps": ticks,
            "navigation_status": response.state["status"],
            "route_graph_digest": response.state.get("route_graph_digest"),
        }
        if quiescent:
            world_changes.update({
                "quiescent": True,
                "quiescence_evidence_hash": _digest({
                    "request_id": request.request_id,
                    "controller_feedback": controller,
                    "execution_samples": evidence.get("execution_samples", ()),
                }),
            })
        return ExecutionFeedback(
            assignment.task_id, assignment.robot_id,
            outcome="command_applied" if completed else "execution_failed",
            failure_code=FailureCode.NONE if completed else FailureCode.EXECUTION_FAILURE,
            message=("navigation completed; physical outcome requires external evaluator"
                     if completed else f"navigation ended with status {response.state['status']}"),
            timestamp_s=last_time,
            world_changes=world_changes,
        )

    @staticmethod
    def _failure(assignment: Assignment, message: str, code: FailureCode) -> ExecutionFeedback:
        return ExecutionFeedback(assignment.task_id, assignment.robot_id,
                                 outcome="execution_failed", failure_code=code,
                                 message=str(message))


__all__ = [
    "EaiSessionRuntime",
    "NavigationSkillAdapter",
    "NavigationTaskExecutor",
    "bind_navigation_request_factory",
    "navigation_request_factory",
]
