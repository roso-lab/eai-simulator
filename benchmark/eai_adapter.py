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
from algorithm.skills.contracts import SkillCommand, SkillContractError, SkillPlan, SkillRequest
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


def command_navigation_request_factory(
    assignment: Assignment, task: Any, *, request_id: str,
) -> SkillRequest:
    """Build a request for the diagnostic direct-command compatibility seam."""

    metadata = dict(getattr(task, "metadata", {}) or {})
    pose = metadata.get("target_pose", metadata.get("goal_pose"))
    parameters = dict(metadata.get("skill_parameters", {}) or {})
    if "horizon_ticks" in metadata:
        parameters.setdefault("horizon_ticks", metadata["horizon_ticks"])
    return SkillRequest(
        request_id=request_id,
        skill_id=str(metadata.get("skill_id", "eai.navigation.goal_position")),
        robot_id=assignment.robot_id,
        target_ref=getattr(task, "target_ref", None),
        target_pose=tuple(pose) if pose is not None else None,
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


class CommandNavigationSkillAdapter:
    """Compatibility adapter for the legacy direct controller-command seam.

    Formal HeteroCoord navigation uses :class:`NavigationSkillAdapter` and an
    authored topology reference.  Some diagnostic and controller-integration
    callers still submit a bounded world-frame goal directly.  Keeping that
    seam explicit is preferable to pretending it is the formal navigation
    protocol: its output is a controller command only and can never establish
    task or physical success.
    """

    skill_id = "eai.navigation.goal_position"

    def __init__(self, *, skill_id: str | None = None,
                 command_type: str = "goal_position") -> None:
        self.skill_id = str(skill_id or self.skill_id)
        self.command_type = str(command_type)

    def plan(self, request: SkillRequest, *,
             observation: Mapping[str, Any] | None = None) -> SkillPlan:
        if request.skill_id != self.skill_id:
            return self._invalid(
                request, "unsupported_skill",
                {"expected_skill_id": self.skill_id},
            )
        payload = dict(request.parameters)
        if request.target_pose is not None:
            target = _as_vector(request.target_pose)
            if target is None or len(target) != 3:
                return self._invalid(
                    request, "invalid_request",
                    {"reason": "target_pose must contain finite XYZ"},
                )
            payload.setdefault("target_pose", target)
        if request.target_ref is not None:
            if not isinstance(request.target_ref, str) or not request.target_ref.strip():
                return self._invalid(
                    request, "invalid_request",
                    {"reason": "target_ref must be a nonempty string"},
                )
            payload.setdefault("target_ref", request.target_ref)
        if "target_pose" not in payload and "target_ref" not in payload:
            return self._invalid(
                request, "invalid_request",
                {"reason": "direct goal command requires target_pose or target_ref"},
            )
        if "horizon_ticks" in payload:
            horizon = payload["horizon_ticks"]
            if type(horizon) is not int or horizon <= 0:
                return self._invalid(
                    request, "invalid_request",
                    {"reason": "horizon_ticks must be a positive integer"},
                )
        if "target_yaw" in payload:
            yaw = payload["target_yaw"]
            if isinstance(yaw, bool) or not isinstance(yaw, (int, float)) or not math.isfinite(yaw):
                return self._invalid(
                    request, "invalid_request",
                    {"reason": "target_yaw must be finite radians"},
                )
        return SkillPlan(
            request_id=request.request_id,
            skill_id=request.skill_id,
            robot_id=request.robot_id,
            commands=(SkillCommand(self.command_type, payload),),
            metadata={
                "provider": "benchmark.eai_adapter.CommandNavigationSkillAdapter",
                "provider_id": "benchmark.command_navigation.diagnostic",
                "provider_version": "2.0",
                "protocol_scope": "diagnostic_controller_integration_only",
                "request_digest": _digest(request.to_mapping()),
                "observation_digest": _digest(observation) if observation is not None else None,
                "task_id": request.task_id,
                "branch_context": request.branch_context,
                "expected_external_outcome": "measured target pose arrival",
            },
        )

    @staticmethod
    def _invalid(request: SkillRequest, status: str,
                 metadata: Mapping[str, Any]) -> SkillPlan:
        return SkillPlan(
            request_id=request.request_id,
            skill_id=request.skill_id,
            robot_id=request.robot_id,
            status=status,
            metadata=dict(metadata),
        )


class EaiTaskExecutor:
    """Execute a bounded diagnostic command through an EAI session.

    This compatibility executor deliberately returns ``command_applied``.  A
    separate evaluator must inspect physical state before any benchmark score
    can use the run.  Optional quiescence evidence only proves controller
    release; it is not an arrival or task-success verdict.
    """

    def __init__(
        self,
        session: Any | None = None,
        *,
        max_steps: int = 1,
        command_name: str | None = None,
        target_resolver: Callable[[str], Sequence[float]] | None = None,
        tensor_factory: Callable[[Sequence[float], str], Any] | None = None,
        quiescence_adapter: Callable[..., Mapping[str, Any]] | None = None,
    ) -> None:
        if type(max_steps) is not int or max_steps <= 0:
            raise ValueError("max_steps must be a positive integer")
        self.session = session
        self.max_steps = max_steps
        self.command_name = command_name
        self.target_resolver = target_resolver
        self.tensor_factory = tensor_factory
        self.quiescence_adapter = quiescence_adapter

    def execute(self, assignment: Assignment, task: Any, *, scene: Any,
                plan: SkillPlan,
                step: Callable[[Mapping[str, Any]], Any]) -> ExecutionFeedback:
        try:
            session = self._resolve_session(scene)
        except RuntimeError as exc:
            return self._failure(assignment, str(exc), FailureCode.EXECUTION_FAILURE)
        base_env = getattr(session, "base_env", None)
        if base_env is None:
            return self._failure(
                assignment, "session.base_env is unavailable", FailureCode.EXECUTION_FAILURE)
        if plan.status not in {"planned", "ready"}:
            return self._failure(
                assignment, f"skill plan status is {plan.status}", FailureCode.INVALID_PLAN)
        if not plan.commands:
            return self._failure(
                assignment, "skill plan has no commands", FailureCode.INVALID_PLAN)
        if plan.robot_id != assignment.robot_id:
            return self._failure(
                assignment, "plan robot differs from assignment", FailureCode.INVALID_PLAN)
        set_command = getattr(base_env, "set_command", None)
        if not callable(set_command):
            return self._failure(
                assignment, "base_env.set_command is unavailable", FailureCode.EXECUTION_FAILURE)

        try:
            prepared = [self._prepare(command, assignment.robot_id, task, session)
                        for command in plan.commands]
            if sum(item[3] for item in prepared) > self.max_steps:
                raise _UnsupportedCommand(
                    f"plan exceeds total step budget {self.max_steps}")
        except _UnsupportedCommand as exc:
            return self._failure(assignment, str(exc), FailureCode.INVALID_PLAN)

        total_steps = 0
        last_time = 0.0
        try:
            for command, command_name, value, horizon, yaw_name, yaw in prepared:
                encoded = None
                if command.command_type != "noop":
                    encoded = self._to_tensor(
                        value, str(getattr(session, "device", "cpu")),
                        int(getattr(session, "num_envs", 1) or 1))
                if command.command_type in {"velocity", "command", "rotor_velocity"}:
                    step_actions = {assignment.robot_id: encoded}
                else:
                    step_actions = self._neutral_actions(session)
                if command.command_type not in {
                    "noop", "velocity", "command", "rotor_velocity",
                }:
                    if yaw_name is not None:
                        set_command(
                            assignment.robot_id, yaw_name,
                            self._to_tensor(
                                [yaw], str(getattr(session, "device", "cpu")),
                                int(getattr(session, "num_envs", 1) or 1)),
                        )
                    set_command(assignment.robot_id, command_name, encoded)
                for _ in range(horizon):
                    boundary = step(step_actions)
                    total_steps += 1
                    last_time = float(getattr(boundary, "sim_time_s", last_time))
        except Exception as exc:
            return self._failure(
                assignment, f"controller execution failed: {exc}",
                FailureCode.EXECUTION_FAILURE)

        world_changes: dict[str, Any] = {
            "commands_applied": len(plan.commands), "steps": total_steps,
        }
        if self.quiescence_adapter is not None:
            try:
                receipt = self.quiescence_adapter(
                    assignment=assignment, task=task, session=session,
                    commands_applied=len(plan.commands), steps=total_steps,
                )
                if not isinstance(receipt, Mapping):
                    raise TypeError("quiescence_adapter must return a mapping")
                evidence_hash = receipt.get("evidence_hash")
                if receipt.get("quiescent") is not True or not str(evidence_hash or "").strip():
                    raise ValueError(
                        "quiescence_adapter must return quiescent=True and evidence_hash")
                world_changes.update({
                    "quiescent": True,
                    "quiescence_evidence_hash": str(evidence_hash),
                })
            except Exception as exc:
                return self._failure(
                    assignment, f"controller quiescence readback failed: {exc}",
                    FailureCode.EXECUTION_FAILURE)
        return ExecutionFeedback(
            assignment.task_id, assignment.robot_id,
            outcome="command_applied", failure_code=FailureCode.NONE,
            message="command applied; physical outcome requires external evaluator",
            timestamp_s=last_time, world_changes=world_changes,
        )

    def _prepare(self, command: SkillCommand, robot_id: str, task: Any,
                 session: Any) -> tuple[SkillCommand, str, list[float] | None, int, str | None, float | int | None]:
        command_name, value = self._encode_command(command, robot_id, session)
        horizon = self._horizon(command, task)
        yaw = command.payload.get("target_yaw")
        yaw_name = None
        if yaw is not None:
            yaw_name = self._yaw_command(session, robot_id)
            if command.command_type != "goal_position" or not yaw_name:
                raise _UnsupportedCommand(
                    "target_yaw requires an explicitly configured yaw_command_name")
            if isinstance(yaw, bool) or not isinstance(yaw, (float, int)) or not math.isfinite(yaw):
                raise _UnsupportedCommand("target_yaw must be finite radians")
        return command, command_name, value, horizon, yaw_name, yaw

    def _resolve_session(self, scene: Any) -> Any:
        if scene is not None and hasattr(scene, "base_env") and hasattr(scene, "env"):
            return scene
        if self.session is not None:
            return self.session
        raise RuntimeError("an EAI SimulatorSession is required")

    def _horizon(self, command: SkillCommand, task: Any) -> int:
        value = command.payload.get("horizon_ticks")
        if value is None:
            value = (getattr(task, "metadata", {}) or {}).get(
                "horizon_ticks", self.max_steps)
        if type(value) is not int or not 1 <= value <= self.max_steps:
            raise _UnsupportedCommand(
                f"horizon_ticks must be an integer in [1, {self.max_steps}]")
        return value

    def _encode_command(self, command: SkillCommand, robot_id: str,
                        session: Any) -> tuple[str, list[float] | None]:
        if command.command_type == "noop":
            return "", None
        payload = dict(command.payload)
        configured = (
            payload.get("command_name") or self.command_name
            or self._configured_command(session, robot_id)
        )
        if command.command_type == "rotor_velocity":
            configured = configured or "rotor_velocity"
        elif not configured:
            raise _UnsupportedCommand(
                f"no controller command is configured for {robot_id}")
        if command.command_type in {"goal_position", "goal_pose", "position"}:
            if payload.get("frame", "world") != "world":
                raise _UnsupportedCommand("goal position requires world frame")
            value = payload.get(
                "target_pose", payload.get("position", payload.get("goal_position")))
            if value is None and payload.get("target_ref") and self.target_resolver is not None:
                value = self.target_resolver(str(payload["target_ref"]))
            if value is None:
                raise _UnsupportedCommand(
                    "goal command requires target_pose or a target_resolver")
        elif command.command_type in {"velocity", "command", "rotor_velocity"}:
            value = payload.get(
                "value", payload.get("velocity", payload.get("rotor_velocity")))
        else:
            raise _UnsupportedCommand(
                f"unsupported command_type: {command.command_type}")
        vector = _as_vector(value)
        if vector is None:
            raise _UnsupportedCommand(
                "controller payload must be a finite numeric vector")
        if command.command_type in {
            "goal_position", "goal_pose", "position", "velocity",
        } and len(vector) != 3:
            raise _UnsupportedCommand(
                "position/velocity requires three components")
        if command.command_type == "rotor_velocity":
            entry = getattr(getattr(session, "env_cfg", None), "controllers", {}).get(robot_id)
            cfg = entry[0] if isinstance(entry, (tuple, list)) and entry else entry
            dim = cfg.get("action_dim") if isinstance(cfg, Mapping) else getattr(cfg, "action_dim", None)
            mode = cfg.get("control_mode") if isinstance(cfg, Mapping) else getattr(cfg, "control_mode", None)
            if mode != "rotor_velocity" or type(dim) is not int or dim != len(vector):
                raise _UnsupportedCommand(
                    "rotor_velocity requires matching controller mode and action_dim")
        return str(configured), vector

    @staticmethod
    def _yaw_command(session: Any, robot_id: str) -> str | None:
        entry = getattr(getattr(session, "env_cfg", None), "controllers", {}).get(robot_id)
        configs = entry if isinstance(entry, (tuple, list)) else (entry,)
        for config in configs:
            name = config.get("yaw_command_name") if isinstance(config, Mapping) else getattr(config, "yaw_command_name", None)
            if isinstance(name, str) and name.strip():
                return name
        return None

    @staticmethod
    def _configured_command(session: Any, robot_id: str) -> str | None:
        controllers = getattr(getattr(session, "env_cfg", None), "controllers", {}) or {}
        entry = controllers.get(robot_id) if hasattr(controllers, "get") else None
        configs = entry if isinstance(entry, (tuple, list)) else ((entry,) if entry is not None else ())
        for config in configs:
            for key in (
                "command_name", "position_command_name",
                "goal_position_command", "command",
            ):
                value = config.get(key) if isinstance(config, Mapping) else getattr(config, key, None)
                if str(value or "").strip():
                    return str(value).strip()
            names = config.get("command_names") if isinstance(config, Mapping) else getattr(config, "command_names", None)
            if isinstance(names, (tuple, list)) and names:
                return str(names[0])
        return None

    def _to_tensor(self, values: Sequence[float], device: str,
                   num_envs: int = 1) -> Any:
        if self.tensor_factory is not None:
            return _batch_value(
                self.tensor_factory(values, device), max(1, num_envs))
        try:
            import torch

            return torch.tensor(
                [list(values)] * max(1, num_envs),
                dtype=torch.float32, device=device,
            )
        except (ImportError, RuntimeError, TypeError):
            return [list(values) for _ in range(max(1, num_envs))]

    def _neutral_actions(self, session: Any) -> Mapping[str, Any]:
        return EaiSessionRuntime(
            session, tensor_factory=self.tensor_factory).neutral_actions()

    @staticmethod
    def _failure(assignment: Assignment, message: str,
                 code: FailureCode) -> ExecutionFeedback:
        return ExecutionFeedback(
            assignment.task_id, assignment.robot_id,
            outcome="execution_failed", failure_code=code, message=str(message),
        )


class _UnsupportedCommand(RuntimeError):
    pass


__all__ = [
    "CommandNavigationSkillAdapter",
    "EaiSessionRuntime",
    "EaiTaskExecutor",
    "NavigationSkillAdapter",
    "NavigationTaskExecutor",
    "bind_navigation_request_factory",
    "command_navigation_request_factory",
    "navigation_request_factory",
]
