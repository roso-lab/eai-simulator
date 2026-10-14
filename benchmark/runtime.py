"""Dependency-light tick runtime shared by offline and live session episodes."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import json
from typing import Any, Mapping, Protocol, Sequence

from algorithm.common_coordination.contract import Assignment, ExecutionFeedback, FailureCode, RobotCapability
from algorithm.skills.contracts import SkillPlan, SkillRequest
from task.contracts import EaiTaskPackage, RobotState

from .adapters import DecisionProvider, EvidenceEvaluator, SkillAdapter, SkillRequestFactory, TaskExecutor
from .evidence import BranchLedger, EvaluationReport, EvaluationStatus
from .fleet import FleetRuntime
from .fleet import FleetMessage, LockRequest, LockStatus
from .protocol import BranchSpec, ProtocolContract, RunKey


@dataclass(frozen=True)
class StepBoundary:
    """The single clock boundary shared by executor, evaluator and ledger."""

    tick: int
    sim_time_s: float
    phase: str
    observations: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.tick < 0:
            raise ValueError("tick cannot be negative")
        if self.sim_time_s < 0:
            raise ValueError("sim_time_s cannot be negative")
        if not str(self.phase).strip():
            raise ValueError("phase is required")
        object.__setattr__(self, "observations", dict(self.observations))


class StepRuntime(Protocol):
    """Minimal runtime surface needed by :class:`EpisodeRunner`."""

    def step(self, actions: Mapping[str, Any]) -> Any:
        ...


def _json_safe(value: Any) -> Any:
    """Return a deterministic, JSON-safe representation for session metadata."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "tolist"):
        try:
            return _json_safe(value.tolist())
        except Exception:  # pragma: no cover - defensive for device tensors
            pass
    if hasattr(value, "__dict__"):
        fields = {
            str(key): _json_safe(item)
            for key, item in vars(value).items()
            if not str(key).startswith("_") and isinstance(item, (str, int, float, bool, type(None), Mapping, list, tuple))
        }
        if fields:
            return {"type": f"{type(value).__module__}.{type(value).__name__}", "fields": fields}
    return f"{type(value).__module__}.{type(value).__name__}"


def _digest(value: Any) -> str:
    encoded = json.dumps(_json_safe(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _vector(value: Any) -> list[float] | None:
    """Read a small vector from torch/numpy-like values without importing either."""

    if value is None:
        return None
    try:
        if hasattr(value, "detach"):
            value = value.detach()
        if hasattr(value, "cpu"):
            value = value.cpu()
        if hasattr(value, "tolist"):
            value = value.tolist()
        while isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], (list, tuple)):
            value = value[0]
        # Isaac environments commonly expose batched state as ``(N, D)``;
        # this benchmark binds env 0 because RuntimeTaskBinding is a single
        # task/session identity, not a vectorized rollout contract.
        if isinstance(value, (list, tuple)) and value and isinstance(value[0], (list, tuple)):
            value = value[0]
        values = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    return values


def _world_position(base_env: Any, robot_name: str) -> tuple[float, float, float] | None:
    articulations = getattr(getattr(base_env, "scene", None), "articulations", {}) or {}
    robot = articulations.get(robot_name) if hasattr(articulations, "get") else None
    data = getattr(robot, "data", None)
    position = _vector(getattr(data, "root_pos_w", None))
    if position is None or len(position) != 3:
        return None
    return tuple(position)  # type: ignore[return-value]


def _controller_profile(env_cfg: Any, robot_name: str) -> dict[str, Any]:
    controllers = getattr(env_cfg, "controllers", {}) or {}
    entry = controllers.get(robot_name) if hasattr(controllers, "get") else None
    configs = entry if isinstance(entry, (list, tuple)) else ((entry,) if entry is not None else ())
    profile: dict[str, Any] = {"robot_id": robot_name, "configs": []}
    for config in configs:
        item = _json_safe(config)
        if isinstance(item, Mapping):
            item = dict(item)
            for key in ("command_name", "command", "control_mode", "action_dim", "robot_type", "yaw_command_name"):
                if hasattr(config, key):
                    item[key] = _json_safe(getattr(config, key))
        profile["configs"].append(item)
    if not profile["configs"]:
        profile["configs"] = [{"missing": True}]
    return profile


@dataclass(frozen=True)
class ExecutionRecord:
    """Provenance for one assignment without promoting feedback to success."""

    assignment: Assignment
    request: SkillRequest
    plan: SkillPlan
    feedback: ExecutionFeedback
    start_tick: int
    end_tick: int


@dataclass(frozen=True)
class EpisodeResult:
    """Complete offline/nominal episode artifact."""

    decision: Any
    executions: tuple[ExecutionRecord, ...]
    evaluation: EvaluationReport
    observations: tuple[Mapping[str, Any], ...]
    ledger_digest: str
    ledger_events: tuple[Any, ...] = ()
    ledger_schema_version: str = "benchmark.ledger.v1"
    run_key: RunKey | None = None
    fleet_audit: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class RuntimeTaskBinding:
    """Immutable task/session metadata; it does not own the live session."""

    task: EaiTaskPackage
    contract: ProtocolContract
    scene_id: str
    robots: tuple[RobotState, ...]
    observation_schema: str
    session_identity: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.scene_id).strip() or not str(self.session_identity).strip():
            raise ValueError("scene_id and session_identity are required")
        object.__setattr__(self, "robots", tuple(self.robots))
        object.__setattr__(self, "metadata", dict(self.metadata))

    @classmethod
    def from_session(
        cls,
        task: EaiTaskPackage,
        session: Any,
        *,
        contract: ProtocolContract,
        robots: Sequence[RobotState] | None = None,
        session_identity: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "RuntimeTaskBinding":
        """Bind a static task to a session without importing Isaac types.

        The binding is intentionally a snapshot.  It reads the public
        ``SimulatorSession`` surface (environment name, selected robots,
        controller configuration and public articulation state) but never
        starts/stops the simulator or retains mutable Isaac objects.  A task
        package may use generic robot ids; in that case selected session
        agents are matched by selection order and inherit the corresponding
        task capabilities.
        """

        scene_id = str(getattr(session, "env_name", task.scene_id) or task.scene_id)
        possible_agents = tuple(str(item) for item in (getattr(session, "possible_agents", ()) or ()) if str(item).strip())
        selection = getattr(session, "selection_data", None)
        if selection is not None and not isinstance(selection, Mapping):
            raise TypeError("session.selection_data must be a mapping or None")
        selected = tuple((selection or {}).get("robots", ()))
        env_cfg = getattr(session, "env_cfg", None)
        base_env = getattr(session, "base_env", None)

        if robots is not None:
            roster = tuple(robots)
            if len({item.capability.robot_id for item in roster}) != len(roster):
                raise ValueError("runtime robot ids must be unique")
            if possible_agents and not {item.capability.robot_id for item in roster}.issubset(set(possible_agents)):
                raise ValueError("explicit runtime roster does not match session.possible_agents")
        else:
            source_roster = tuple(task.robots)
            roster_items: list[RobotState] = []
            type_counts: dict[str, int] = {}
            for index, agent_name in enumerate(possible_agents):
                selected_item = selected[index] if index < len(selected) and isinstance(selected[index], Mapping) else {}
                robot_type = str(selected_item.get("type", "")).strip().lower()
                if robot_type:
                    type_counts[robot_type] = type_counts.get(robot_type, 0) + 1
                source = next((item for item in source_roster if item.capability.robot_id == agent_name), None)
                if source is None and index < len(source_roster):
                    source = source_roster[index]
                if source is not None:
                    # Session agent ids are authoritative for live execution;
                    # retain only the static task capabilities/active flag.
                    capability = RobotCapability(
                        agent_name,
                        source.capability.capabilities,
                        source.capability.active,
                    )
                else:
                    capability = RobotCapability(agent_name, frozenset(), True)
                position = _world_position(base_env, agent_name)
                inherited_metadata = dict(source.metadata) if source is not None else {}
                if robot_type:
                    inherited_metadata.setdefault("robot_type", robot_type)
                attachments = tuple(
                    str(item.get("type", "")).strip()
                    for item in selected_item.get("attachments", ())
                    if isinstance(item, Mapping) and str(item.get("type", "")).strip()
                )
                if attachments:
                    inherited_metadata["attachments"] = attachments
                roster_items.append(RobotState(capability=capability, position=position, metadata=inherited_metadata))
            if possible_agents:
                roster = tuple(roster_items)
            else:
                roster = tuple(task.robots)

        profiles = tuple(_controller_profile(env_cfg, item.capability.robot_id) for item in roster)
        selection_digest = _digest(selection or {})
        controller_digest = _digest(profiles)
        identity_payload = {
            "env_name": scene_id,
            "possible_agents": possible_agents,
            "num_envs": int(getattr(session, "num_envs", 1) or 1),
            "device": str(getattr(session, "device", "")),
            "selection_digest": selection_digest,
            "controller_digest": controller_digest,
        }
        identity = session_identity or f"session:{_digest(identity_payload)[:24]}"
        runtime_metadata = dict(metadata or {})
        runtime_metadata.update(
            {
                "env_name": scene_id,
                "possible_agents": list(possible_agents),
                "num_envs": int(getattr(session, "num_envs", 1) or 1),
                "device": str(getattr(session, "device", "")),
                "selection_digest": selection_digest,
                "controller_profiles": _json_safe(profiles),
                "controller_profiles_digest": controller_digest,
                "session_identity_digest": _digest(identity_payload),
            }
        )
        return cls(
            task=task.bind_runtime(robots=roster, scene_id=scene_id, metadata=runtime_metadata),
            contract=contract,
            scene_id=scene_id,
            robots=roster,
            observation_schema=task.observation_schema,
            session_identity=identity,
            metadata=runtime_metadata,
        )


class EpisodeRunner:
    """Small stateful step shell; policy/executor/evaluator stay injected.

    This class deliberately does not attempt to implement the full paper
    protocol yet.  It owns only monotonic tick creation and branch-local
    provenance, leaving controller actions and physical verdicts to adapters.
    """

    def __init__(self, runtime: StepRuntime, *, run_key: RunKey, fleet: FleetRuntime | None = None, event_scheduler: Any = None, assignment_wait_ticks: int = 0) -> None:
        self.runtime = runtime
        self.run_key = run_key
        self.fleet = fleet
        self.event_scheduler = event_scheduler
        if type(assignment_wait_ticks) is not int or assignment_wait_ticks < 0:
            raise ValueError("assignment wait budget must be nonnegative ticks")
        self.assignment_wait_ticks = assignment_wait_ticks
        self._dispatch_sequence = 0
        self.ledger = fleet.ledger if fleet is not None else BranchLedger(run_key)
        if self.fleet is not None and self.fleet.run_key != self.run_key:
            raise ValueError("fleet runtime must use the EpisodeRunner branch ledger")
        self._tick = 0
        self._sim_time_s = 0.0
        self._boundaries: list[StepBoundary] = []
        self._started = False

    @property
    def tick(self) -> int:
        return self._tick

    def step(
        self,
        actions: Mapping[str, Any],
        *,
        sim_time_s: float | None = None,
        phase: str = "execution",
        observations: Mapping[str, Any] | None = None,
    ) -> StepBoundary:
        """Advance one runtime step and record only provenance metadata."""

        if self.event_scheduler is not None:
            if self.fleet is None:
                raise RuntimeError("dynamic events require the branch fleet")
            self.event_scheduler.advance(self._tick, self.fleet)
        result = self.runtime.step(actions)
        del result  # Concrete adapters decide how to decode environment output.
        if sim_time_s is not None:
            if sim_time_s < self._sim_time_s:
                raise ValueError("sim_time_s must be monotonic")
            self._sim_time_s = float(sim_time_s)
        else:
            runtime_time = getattr(self.runtime, "sim_time_s", None)
            if callable(runtime_time):
                runtime_time = runtime_time()
            if runtime_time is not None:
                runtime_time = float(runtime_time)
                if runtime_time < self._sim_time_s:
                    raise ValueError("runtime sim_time_s must be monotonic")
                self._sim_time_s = runtime_time
        sampled_observations = observations
        if sampled_observations is None:
            observe = getattr(self.runtime, "observe", None)
            sampled_observations = observe() if callable(observe) else {}
        boundary = StepBoundary(
            tick=self._tick,
            sim_time_s=self._sim_time_s,
            phase=phase,
            observations=sampled_observations or {},
        )
        self.ledger.append(
            "step_boundary",
            tick=boundary.tick,
            source="runtime",
            payload={"phase": boundary.phase, "sim_time_s": boundary.sim_time_s},
        )
        if self.fleet is not None:
            self.fleet.record_step(tick=boundary.tick, sim_time_s=boundary.sim_time_s, phase=boundary.phase)
        self._boundaries.append(boundary)
        self._tick += 1
        return boundary

    def run(
        self,
        binding: RuntimeTaskBinding,
        *,
        decision_provider: DecisionProvider,
        request_factory: SkillRequestFactory,
        skill: SkillAdapter,
        executor: TaskExecutor,
        evaluator: EvidenceEvaluator | None,
        scene: Any,
        branch: BranchSpec | None = None,
        initial_observations: Sequence[Mapping[str, Any]] = (),
    ) -> EpisodeResult:
        """Run an episode and always close the optional fleet runtime."""

        if self.fleet is None:
            result = self._run_episode(
                binding,
                decision_provider=decision_provider,
                request_factory=request_factory,
                skill=skill,
                executor=executor,
                evaluator=evaluator,
                scene=scene,
                branch=branch,
                initial_observations=initial_observations,
            )
            result = replace(
                result,
                ledger_digest=self.ledger.seal(),
                ledger_events=self.ledger.events,
            )
            return result
        self.fleet.start(scene, binding, branch)
        try:
            result = self._run_episode(
                binding,
                decision_provider=decision_provider,
                request_factory=request_factory,
                skill=skill,
                executor=executor,
                evaluator=evaluator,
                scene=scene,
                branch=branch,
                initial_observations=initial_observations,
            )
        finally:
            summary = self.fleet.close(reason="episode_complete", tick=self._tick)
            if 'result' in locals():
                result = replace(
                    result,
                    ledger_digest=self.ledger.digest(),
                    ledger_events=self.ledger.events,
                    fleet_audit=summary.to_dict(),
                )
        if 'result' not in locals():  # pragma: no cover - defensive; finally re-raises failures
            raise RuntimeError("episode did not produce a result")
        return result

    def run_orchestrated(self, binding, *, decision_provider, request_factory, skill,
                         executor, evaluator, scene, branch=None, replan=None,
                         world_observation_provider=None, limits=None,
                         dependency_resolver=None, initial_observations=()):
        """Reuse task.TaskRunner for bounded DAG/replan scheduling on this clock.

        Dependency progress needs an explicit measured resolver. Command
        application alone leaves a task pending. Batch physics stepping remains
        an executor extension; this adapter deliberately dispatches serially.
        """
        from task.runner import TaskRunner
        from task.contracts import TaskRunRequest, ExecutionUpdate, DependencyOutcome
        if self._started:
            raise RuntimeError("EpisodeRunner instances are single-use")
        if branch is not None and branch.run_key != self.run_key:
            raise ValueError("branch run_key must match the episode run_key")
        self._started = True
        if self.fleet is not None:
            self.fleet.start(scene, binding, branch)
        records = []
        owner = self

        class Dispatch:
            def execute(self, assignment, task, *, scene):
                record = owner._dispatch_assignment(
                    assignment, task, dispatch_id=len(records),
                    request_factory=request_factory, skill=skill, executor=executor, scene=scene)
                if record is None:
                    feedback = ExecutionFeedback(assignment.task_id, assignment.robot_id,
                        "not_dispatched", FailureCode.MILESTONE_UNVERIFIED,
                        "assignment unavailable or not delivered")
                    return ExecutionUpdate(feedback, DependencyOutcome.RETRYABLE_FAILURE)
                records.append(record)
                if dependency_resolver is not None:
                    update = dependency_resolver(record, scene)
                    if not isinstance(update, ExecutionUpdate) or update.feedback != record.feedback:
                        raise ValueError("dependency resolver must preserve measured execution feedback")
                    return update
                changes = record.feedback.world_changes
                completed = (record.feedback.failure_code is FailureCode.NONE and
                             changes.get("dependency_completed") is True and
                             bool(changes.get("dependency_evidence_hash")))
                outcome = (DependencyOutcome.COMPLETED if completed else
                           DependencyOutcome.TERMINAL_FAILURE if record.feedback.failure_code not in
                           {FailureCode.NONE, FailureCode.MILESTONE_UNVERIFIED} else
                           DependencyOutcome.RETRYABLE_FAILURE)
                return ExecutionUpdate(record.feedback, outcome)

        try:
            self.ledger.append("episode_start", tick=self._tick, source="runtime", payload={
                "task_id": binding.task.task_id, "contract_digest": binding.contract.digest(),
                "mode": "task_orchestrated"})
            result = TaskRunner(scene_provider=lambda _: scene,
                                orchestration_executor=Dispatch(),
                                observation_provider=world_observation_provider).run_orchestrated(
                TaskRunRequest(binding.task, branch_id=self.run_key.branch_role.value,
                               repeat_id=self.run_key.repeat_id), decision_provider,
                replan=replan, limits=limits)
            for event in result.ledger.events:
                self.ledger.append("task_orchestration", tick=self._tick, source="task.runner", payload=event)
            observations = tuple(dict(o) for o in initial_observations)
            observations += tuple(dict(b.observations) for b in self._boundaries if b.observations)
            evaluation = (evaluator.evaluate(task=binding.task, observations=observations, branch=branch)
                          if evaluator is not None else EvaluationReport(
                              EvaluationStatus.NOT_APPLICABLE, "physical_evidence_required",
                              evaluator_id="none", evaluator_version="benchmark.runtime.v1"))
            if not isinstance(evaluation, EvaluationReport):
                raise TypeError("evaluator must return EvaluationReport")
            for evidence in evaluation.evidence:
                self.ledger.append("evidence", tick=evidence.tick, source="evaluator", payload={
                    "predicate_id": evidence.predicate_id, "predicate_version": evidence.predicate_version,
                    "status": evidence.status.value, "evidence_hash": evidence.evidence_hash,
                    "reason": evidence.reason})
            self.ledger.append("orchestration_terminal", tick=self._tick, source="task.runner",
                               payload={"states": dict(result.task_states), "reason": result.termination_reason,
                                        "rounds": result.rounds, "dispatches": result.dispatch_count})
            self.ledger.append("terminal_verdict", tick=self._tick, source="evaluator",
                               payload={"status": evaluation.status.value, "reason": evaluation.reason})
        finally:
            summary = self.fleet.close(tick=self._tick) if self.fleet is not None else None
            if self.fleet is None:
                self.ledger.seal()
        return EpisodeResult(result.decision, tuple(records), evaluation, observations,
                             self.ledger.digest(), self.ledger.events, self.ledger.schema_version,
                             self.run_key, summary.to_dict() if summary else None)

    def _run_episode(
        self,
        binding: RuntimeTaskBinding,
        *,
        decision_provider: DecisionProvider,
        request_factory: SkillRequestFactory,
        skill: SkillAdapter,
        executor: TaskExecutor,
        evaluator: EvidenceEvaluator | None,
        scene: Any,
        branch: BranchSpec | None = None,
        initial_observations: Sequence[Mapping[str, Any]] = (),
    ) -> EpisodeResult:
        """Run one injected episode through the paper responsibility seams.

        This method is deliberately single-round and sequential.  It is the
        offline/nominal vertical slice used to validate contracts before
        adding parallel scheduling, replanning, checkpoint branches, or Isaac
        controller integration.
        """

        if branch is not None and branch.run_key != self.run_key:
            raise ValueError("branch run_key must match the episode run_key")
        if self._started:
            raise RuntimeError("EpisodeRunner instances are single-use")
        self._started = True
        task = binding.task
        self._boundaries.clear()
        self.ledger.append(
            "episode_start",
            tick=self._tick,
            source="runtime",
            payload={
                "task_id": task.task_id,
                "scene_id": binding.scene_id,
                "contract_id": binding.contract.contract_id,
                "contract_version": binding.contract.version,
                "contract_digest": binding.contract.digest(),
                "branch_role": self.run_key.branch_role.value,
                "checkpoint_digest": branch.checkpoint.digest if branch is not None else "",
                "treatment_id": branch.treatment_id if branch is not None else "none",
            },
        )

        decision = decision_provider(task, binding.robots)
        if not hasattr(decision, "assignments"):
            raise TypeError("decision_provider must return AlgorithmDecision")
        self._validate_decision(task, decision)
        self.ledger.append(
            "algorithm_decision",
            tick=self._tick,
            source="brain",
            payload={
                "algorithm_id": getattr(decision.condition, "algorithm_id", "unknown"),
                "assignments": [item.__dict__ for item in decision.assignments],
                "unassigned_tasks": list(decision.unassigned_tasks),
            },
        )

        task_by_id = {item.task_id: item for item in task.graph.tasks}
        executions: list[ExecutionRecord] = []
        completed = set(task.metadata.get("completed_tasks", ()))
        for index, assignment in enumerate(decision.assignments):
            task_spec = task_by_id[assignment.task_id]
            if set(task_spec.prerequisites) - completed:
                self.ledger.append("dependency_blocked", tick=self._tick, source="runtime", payload={"task_id": assignment.task_id, "missing": sorted(set(task_spec.prerequisites) - completed)})
                continue
            record = self._dispatch_assignment(assignment, task_spec, dispatch_id=index, request_factory=request_factory, skill=skill, executor=executor, scene=scene)
            if record is not None:
                executions.append(record)
                if record.feedback.failure_code is FailureCode.NONE and record.feedback.world_changes.get("dependency_completed") is True and record.feedback.world_changes.get("dependency_evidence_hash"):
                    completed.add(assignment.task_id)

        observations = tuple(dict(item) for item in initial_observations)
        observations += tuple(dict(item.observations) for item in self._boundaries if item.observations)
        if evaluator is None:
            evaluation = EvaluationReport(
                status=EvaluationStatus.NOT_APPLICABLE,
                reason="physical_evidence_required",
                evaluator_id="none",
                evaluator_version="benchmark.runtime.v1",
            )
        else:
            evaluation = evaluator.evaluate(task=task, observations=observations, branch=branch)
            if not isinstance(evaluation, EvaluationReport):
                raise TypeError("evaluator must return EvaluationReport")
        for evidence in evaluation.evidence:
            self.ledger.append(
                "evidence",
                tick=evidence.tick,
                source="evaluator",
                payload={
                    "predicate_id": evidence.predicate_id,
                    "predicate_version": evidence.predicate_version,
                    "status": evidence.status.value,
                    "evidence_hash": evidence.evidence_hash,
                    "reason": evidence.reason,
                },
            )
        self.ledger.append(
            "terminal_verdict",
            tick=self._tick,
            source="evaluator",
            payload={
                "status": evaluation.status.value,
                "reason": evaluation.reason,
                "evaluator_id": evaluation.evaluator_id,
                "evaluator_version": evaluation.evaluator_version,
            },
        )
        return EpisodeResult(
            decision=decision,
            executions=tuple(executions),
            evaluation=evaluation,
            observations=observations,
            # The wrapper seals the fleet audit first when one is attached.
            # Direct/offline runners retain the same behavior through the
            # no-fleet branch below.
            ledger_digest=self.ledger.digest(),
            ledger_events=self.ledger.events,
            ledger_schema_version=self.ledger.schema_version,
            run_key=self.run_key,
        )

    def _dispatch_assignment(self, assignment, task_spec, *, dispatch_id, request_factory, skill, executor, scene):
        """Admission, consumed assignment, controller lease and measured release."""
        if self.event_scheduler is not None and self.fleet is not None:
            self.event_scheduler.advance(self._tick, self.fleet)
        dispatch_id = self._dispatch_sequence
        self._dispatch_sequence += 1
        request = request_factory(assignment, task_spec, request_id=f"{self.run_key.as_string()}:{dispatch_id}")
        if self.fleet is not None:
            if not set(task_spec.required_capabilities).issubset(self.fleet.available_capabilities(assignment.robot_id)):
                self.ledger.append("assignment_unavailable", tick=self._tick, source="runtime", payload={"task_id": assignment.task_id, "robot_id": assignment.robot_id})
                return None
            receipt = self.fleet.send(
                FleetMessage(
                    message_id=f"assignment-{request.request_id}",
                    sender_id="brain",
                    receiver_id=assignment.robot_id,
                    kind="assignment",
                    tick=self._tick,
                    payload={"task_id": assignment.task_id, "request_id": request.request_id},
                )
            )
            delivered = ()
            for waited in range(self.assignment_wait_ticks + 1):
                delivered = self.fleet.receive(assignment.robot_id, tick=self._tick,
                                               message_id=f"assignment-{request.request_id}")
                if delivered or waited == self.assignment_wait_ticks:
                    break
                self.step({}, phase="assignment_wait")
            if not set(task_spec.required_capabilities).issubset(self.fleet.available_capabilities(assignment.robot_id)):
                return None
            if not any(item.message_id == f"assignment-{request.request_id}" for item in delivered):
                self.ledger.append("assignment_not_delivered", tick=self._tick, source="runtime", payload={"task_id": assignment.task_id, "status": receipt.status.value})
                return None
        self.ledger.append(
            "skill_request",
            tick=self._tick,
            source="runtime",
            payload={
                "request_id": request.request_id,
                "skill_id": request.skill_id,
                "robot_id": request.robot_id,
                "task_id": assignment.task_id,
            },
        )
        observe = getattr(self.runtime, "observe", None)
        legal_observe = getattr(self.runtime, "observe_for_robot", None)
        skill_observation = legal_observe(assignment.robot_id) if callable(legal_observe) else (observe() if callable(observe) else {})
        plan = skill.plan(request, observation=skill_observation)
        self._validate_plan(request, plan)
        self.ledger.append(
            "skill_plan",
            tick=self._tick,
            source="skill",
            payload={
                "request_id": plan.request_id,
                "skill_id": plan.skill_id,
                "robot_id": plan.robot_id,
                "command_count": len(plan.commands),
                "status": plan.status,
            },
        )
        generation = self.fleet.command_generation(assignment.robot_id) if self.fleet else None
        def guarded_step(actions):
            if self.event_scheduler is not None and self.fleet is not None:
                self.event_scheduler.advance(self._tick, self.fleet)
            if self.fleet is not None and (self.fleet.command_generation(assignment.robot_id) != generation or
                    not set(task_spec.required_capabilities).issubset(self.fleet.available_capabilities(assignment.robot_id))):
                raise RuntimeError("execution invalidated by current membership/function generation")
            return self.step(actions)
        start_tick = self._tick
        lease_id = ""
        if self.fleet is not None:
            lease = self.fleet.acquire(
                LockRequest(
                    request_id=f"execution-{request.request_id}",
                    resource_id=f"robot:{assignment.robot_id}",
                    owner_id=assignment.robot_id,
                    lock_type="exclusive",
                    purpose="controller_execution",
                    tick=self._tick,
                )
            )
            lease_id = lease.lease_id
            if lease.status is LockStatus.ACQUIRED:
                try:
                    feedback = executor.execute(
                        assignment,
                        task_spec,
                        scene=scene,
                        plan=plan,
                        step=guarded_step,
                    )
                finally:
                    if 'feedback' in locals() and isinstance(feedback, ExecutionFeedback) and (feedback.task_id, feedback.robot_id) == (assignment.task_id, assignment.robot_id) and feedback.world_changes.get("quiescent") is True and feedback.world_changes.get("quiescence_evidence_hash"):
                        self.fleet.release(lease_id, owner_id=assignment.robot_id, tick=self._tick)
                    else:
                        self.ledger.append("controller_quarantined", tick=self._tick, source="runtime", payload={"robot_id": assignment.robot_id, "lease_id": lease_id, "reason": "quiescence_unverified"})
            else:
                feedback = ExecutionFeedback(
                    task_id=assignment.task_id,
                    robot_id=assignment.robot_id,
                    outcome="execution_blocked",
                    failure_code=FailureCode.INFEASIBLE_TEAM,
                    message=f"fleet lease rejected: {lease.reason}",
                )
        else:
            feedback = executor.execute(
                assignment,
                task_spec,
                scene=scene,
                plan=plan,
                step=guarded_step,
            )
        if not isinstance(feedback, ExecutionFeedback):
            raise TypeError("executor must return ExecutionFeedback")
        if feedback.task_id != assignment.task_id or feedback.robot_id != assignment.robot_id:
            raise ValueError("executor feedback identity does not match assignment")
        end_tick = self._tick
        record = ExecutionRecord(assignment, request, plan, feedback, start_tick, end_tick)
        self.ledger.append(
            "execution_feedback",
            tick=end_tick,
            source="executor",
            payload={
                "task_id": feedback.task_id,
                "robot_id": feedback.robot_id,
                "outcome": feedback.outcome,
                "failure_code": feedback.failure_code.value,
            },
        )
        return record

    @staticmethod
    def _validate_decision(task: EaiTaskPackage, decision: Any) -> None:
        task_by_id = {item.task_id: item for item in task.graph.tasks}
        active = {item.capability.robot_id: item.capability for item in task.robots if item.capability.active}
        assignments = tuple(decision.assignments)
        unassigned = set(decision.unassigned_tasks)
        if not unassigned.issubset(task_by_id):
            raise ValueError("unassigned_tasks contains an unknown task")
        if len({(item.task_id, item.robot_id) for item in assignments}) != len(assignments):
            raise ValueError("duplicate task/robot assignment")
        completed = set(task.metadata.get("completed_tasks", ()))
        seen_task_ids: set[str] = set()
        for item in assignments:
            if item.task_id not in task_by_id or item.robot_id not in active:
                raise ValueError("assignment contains unknown task or inactive robot")
            task_spec = task_by_id[item.task_id]
            missing = set(task_spec.required_capabilities) - set(active[item.robot_id].capabilities)
            if missing:
                raise ValueError(f"robot {item.robot_id} lacks capabilities for {item.task_id}: {sorted(missing)}")
            if item.task_id in unassigned:
                raise ValueError(f"task {item.task_id} is both assigned and unassigned")
            missing_prereqs = set(task_spec.prerequisites) - (seen_task_ids | completed)
            if missing_prereqs:
                raise ValueError(f"assignment {item.task_id} has unmet prerequisites: {sorted(missing_prereqs)}")
            seen_task_ids.add(item.task_id)

    @staticmethod
    def _validate_plan(request: SkillRequest, plan: SkillPlan) -> None:
        if (plan.request_id, plan.skill_id, plan.robot_id) != (request.request_id, request.skill_id, request.robot_id):
            raise ValueError("skill plan identity does not match request")
