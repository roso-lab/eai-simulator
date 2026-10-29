"""Task runner that composes algorithms with injected EAI boundaries."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any, Protocol

from algorithm.common_coordination.contract import (
    Assignment,
    EventLedger,
    ExecutionFeedback,
    FailureCode,
)

from .contracts import (
    AlgorithmDecision,
    DependencyOutcome,
    ExecutionUpdate,
    OrchestratedTaskRunResult,
    OrchestrationLimits,
    OrchestrationState,
    TaskRunRequest,
    TaskRunResult,
)


class DecisionProvider(Protocol):
    def __call__(self, task: Any, robots: Sequence[Any]) -> AlgorithmDecision | Iterable[Assignment]:
        ...


class CoalitionScopeError(ValueError):
    """The current single-owner runner cannot execute a coalition decision."""

    na_reason = "task_runner_single_owner_contract_cannot_execute_coalition"

    def __init__(self, decision: AlgorithmDecision):
        self.decision = decision
        super().__init__(
            "TaskRunner single-owner contract cannot execute coalition assignments; "
            "the complete decision is retained on exception.decision"
        )


class ReplayBackend(Protocol):
    def fork(self, checkpoint: str, branch_id: str) -> None:
        ...


class SkillExecutor(Protocol):
    def execute(self, assignment: Assignment, task: Any, *, scene: Any) -> ExecutionFeedback:
        ...


class OrchestrationExecutor(Protocol):
    def execute(
        self,
        assignment: Assignment,
        task: Any,
        *,
        scene: Any,
    ) -> ExecutionUpdate:
        ...


class BatchOrchestrationExecutor(Protocol):
    """Optional batch boundary; the implementation still owns Kit stepping."""

    def execute_batch(
        self,
        assignments: Sequence[Assignment],
        tasks: Mapping[str, Any],
        *,
        scene: Any,
    ) -> Sequence[ExecutionUpdate]:
        ...


class EvidenceEvaluator(Protocol):
    def evaluate(self, *, task: Any, decision: AlgorithmDecision, feedback: Sequence[ExecutionFeedback], scene: Any) -> Mapping[str, Any]:
        ...


class ReplanProvider(Protocol):
    def __call__(
        self,
        task: Any,
        robots: Sequence[Any],
        state: OrchestrationState,
    ) -> AlgorithmDecision | Iterable[Assignment] | None:
        ...


def _normalise_decision(
    output: AlgorithmDecision | Iterable[Assignment],
    *,
    condition: Any | None,
) -> AlgorithmDecision:
    if isinstance(output, AlgorithmDecision):
        return output
    if condition is None:
        raise TypeError("an iterable assignment output requires an AlgorithmCondition")
    return AlgorithmDecision(condition=condition, assignments=tuple(output))


def _validate_decision(
    decision: AlgorithmDecision,
    *,
    task_by_id: Mapping[str, Any],
    robot_by_id: Mapping[str, Any],
    allowed_task_ids: set[str] | None = None,
) -> None:
    if decision.coalition_assignments:
        raise CoalitionScopeError(decision)
    assigned_task_ids = [item.task_id for item in decision.assignments]
    if len(set(assigned_task_ids)) != len(assigned_task_ids):
        raise ValueError("algorithm decision assigns a task more than once")
    unassigned_task_ids = list(decision.unassigned_tasks)
    if len(set(unassigned_task_ids)) != len(unassigned_task_ids):
        raise ValueError("algorithm decision contains duplicate unassigned tasks")
    if set(assigned_task_ids) & set(unassigned_task_ids):
        raise ValueError("a task cannot be both assigned and unassigned")

    mentioned = set(assigned_task_ids) | set(unassigned_task_ids)
    unknown = mentioned - set(task_by_id)
    if unknown:
        raise ValueError(f"algorithm decision contains unknown tasks: {sorted(unknown)}")
    if allowed_task_ids is not None:
        unavailable = mentioned - allowed_task_ids
        if unavailable:
            raise ValueError(
                "replan decision mentions non-pending tasks: "
                f"{sorted(unavailable)}"
            )

    for assignment in decision.assignments:
        robot = robot_by_id.get(assignment.robot_id)
        if robot is None:
            raise ValueError(
                "algorithm decision contains an unknown or inactive robot: "
                f"{assignment.robot_id}"
            )
        required = task_by_id[assignment.task_id].required_capabilities
        missing = set(required) - set(robot.capability.capabilities)
        if missing:
            raise ValueError(
                f"robot {assignment.robot_id} lacks capabilities for "
                f"{assignment.task_id}: {sorted(missing)}"
            )


def _append_decision_event(
    ledger: EventLedger,
    decision: AlgorithmDecision,
    *,
    round_index: int | None = None,
) -> None:
    payload: dict[str, Any] = {
        "algorithm_id": decision.condition.algorithm_id,
        "assignments": [item.__dict__ for item in decision.assignments],
        "unassigned_tasks": list(decision.unassigned_tasks),
        "provenance": dict(decision.provenance),
    }
    if round_index is not None:
        payload["round_index"] = round_index
    ledger.append("algorithm_decision", **payload)


class TaskRunner:
    """Run one task package without constructing an Isaac application.

    The caller injects scene loading, replay, EAI skill execution, and evidence
    evaluation.  This keeps the task layer testable on a normal Python
    interpreter and prevents an algorithm from claiming physical success.
    """

    def __init__(
        self,
        *,
        scene_provider: Callable[[str], Any] | None = None,
        replay: ReplayBackend | None = None,
        executor: SkillExecutor | None = None,
        orchestration_executor: OrchestrationExecutor | BatchOrchestrationExecutor | None = None,
        evaluator: EvidenceEvaluator | None = None,
        observation_provider: Callable[[Any], Any] | None = None,
    ) -> None:
        self.scene_provider = scene_provider or (lambda scene_id: {"scene_id": scene_id})
        self.replay = replay
        self.executor = executor
        self.orchestration_executor = orchestration_executor
        self.evaluator = evaluator
        self.observation_provider = observation_provider

    def run(
        self,
        request: TaskRunRequest,
        provider: DecisionProvider,
        *,
        condition=None,
    ) -> TaskRunResult:
        task = request.task
        if request.checkpoint is not None and self.replay is None:
            raise ValueError("checkpoint requested but no replay backend was injected")
        scene = self.scene_provider(task.scene_id)
        ledger = EventLedger()
        if request.checkpoint is not None:
            if self.replay is None:
                raise ValueError("checkpoint requested but no replay backend was injected")
            self.replay.fork(request.checkpoint, request.branch_id)
            ledger.append("replay_fork", checkpoint=request.checkpoint, branch_id=request.branch_id)

        output = provider(task, task.robots)
        decision = _normalise_decision(output, condition=condition)
        task_by_id = {item.task_id: item for item in task.graph.tasks}
        robot_by_id = {
            item.capability.robot_id: item
            for item in task.robots
            if item.capability.active
        }
        _validate_decision(
            decision,
            task_by_id=task_by_id,
            robot_by_id=robot_by_id,
        )
        _append_decision_event(ledger, decision)

        feedback: list[ExecutionFeedback] = []
        if self.executor is None:
            for item in decision.assignments:
                feedback.append(
                    ExecutionFeedback(
                        task_id=item.task_id,
                        robot_id=item.robot_id,
                        outcome="not_executed",
                        failure_code=FailureCode.MILESTONE_UNVERIFIED,
                        message="No EAI SkillExecutor was injected; physical evidence is unavailable.",
                    )
                )
        else:
            for item in decision.assignments:
                ledger.append("assignment_applied", task_id=item.task_id, robot_id=item.robot_id)
                item_feedback = self.executor.execute(
                    item,
                    task_by_id[item.task_id],
                    scene=scene,
                )
                if not isinstance(item_feedback, ExecutionFeedback):
                    raise TypeError("executor must return an ExecutionFeedback record")
                if (item_feedback.task_id, item_feedback.robot_id) != (
                    item.task_id,
                    item.robot_id,
                ):
                    raise ValueError(
                        "executor feedback identity does not match dispatched assignment"
                    )
                feedback.append(item_feedback)

        if self.executor is None or self.evaluator is None:
            verdict: Mapping[str, Any] = {
                "status": "N/A",
                "reason": "physical_evidence_required",
                "unassigned_tasks": list(decision.unassigned_tasks),
            }
        else:
            verdict = self.evaluator.evaluate(
                task=task,
                decision=decision,
                feedback=tuple(feedback),
                scene=scene,
            )
        ledger.append("verdict", status=verdict.get("status", "N/A"), reason=verdict.get("reason"))
        return TaskRunResult(request, decision, tuple(feedback), verdict, ledger)

    def run_orchestrated(
        self,
        request: TaskRunRequest,
        provider: DecisionProvider,
        *,
        condition=None,
        replan: ReplanProvider | None = None,
        limits: OrchestrationLimits | None = None,
    ) -> OrchestratedTaskRunResult:
        """Run a bounded feedback/DAG schedule without owning simulator steps.

        The initial provider may assign a whole DAG or only its first ready
        subset.  After each dispatch round, ``replan`` may replace the pending
        assignment plan.  A retryable failure is never retried unless that
        callback explicitly assigns it again.  If an executor implements
        ``execute_batch``, the runner calls it once with the ready set; the
        callback, not this runner, owns serial Isaac/Kit stepping.
        """

        limits = limits or OrchestrationLimits()
        task = request.task
        if request.checkpoint is not None and self.replay is None:
            raise ValueError("checkpoint requested but no replay backend was injected")
        bind_run = getattr(self.orchestration_executor, "bind_run", None)
        if callable(bind_run):
            bind_run(request)
        scene = self.scene_provider(task.scene_id)
        ledger = EventLedger(schema_version="task.orchestration.v1")
        if request.checkpoint is not None:
            if self.replay is None:
                raise ValueError("checkpoint requested but no replay backend was injected")
            self.replay.fork(request.checkpoint, request.branch_id)
            ledger.append(
                "replay_fork",
                checkpoint=request.checkpoint,
                branch_id=request.branch_id,
            )

        from .observation import WorldSnapshot

        previous_observation: WorldSnapshot | None = None

        def refresh_observation(*, after_dispatch: bool = False) -> bool:
            nonlocal task, previous_observation
            if self.observation_provider is None:
                return False
            snapshot = self.observation_provider(scene)
            if not isinstance(snapshot, WorldSnapshot):
                raise TypeError("observation provider must return WorldSnapshot")
            if snapshot.scene_id != request.task.scene_id:
                raise ValueError("observation does not belong to requested scene")
            if previous_observation is not None:
                snapshot.validate_successor(previous_observation)
                if after_dispatch and snapshot.revision <= previous_observation.revision:
                    raise ValueError("post-dispatch observation requires a fresh revision")
            changed = previous_observation is None or snapshot.digest != previous_observation.digest
            task = request.task.bind_runtime(
                robots=snapshot.robots,
                metadata={"runtime_observation": snapshot.to_dict()},
            )
            if changed:
                ledger.append("world_observed", revision=snapshot.revision,
                              tick=snapshot.tick, digest=snapshot.digest)
            previous_observation = snapshot
            return changed

        refresh_observation()
        task_order = tuple(item.task_id for item in task.graph.tasks)
        task_by_id = {item.task_id: item for item in task.graph.tasks}
        robot_by_id = {
            item.capability.robot_id: item
            for item in task.robots
            if item.capability.active
        }
        initial = _normalise_decision(
            provider(task, task.robots),
            condition=condition,
        )
        _validate_decision(
            initial,
            task_by_id=task_by_id,
            robot_by_id=robot_by_id,
        )
        decisions = [initial]
        _append_decision_event(ledger, initial, round_index=0)

        states = {task_id: "pending" for task_id in task_order}
        active_assignments = {
            assignment.task_id: assignment for assignment in initial.assignments
        }
        feedback: list[ExecutionFeedback] = []
        rounds = 0
        dispatch_count = 0
        termination_reason = "no_ready_assignments"

        while True:
            self._propagate_blocked_dependencies(states, task_by_id)
            if all(state == "completed" for state in states.values()):
                termination_reason = "all_dependencies_completed"
                break
            if rounds >= limits.max_rounds:
                termination_reason = "max_rounds_reached"
                break

            changed = refresh_observation()
            robot_by_id = {
                item.capability.robot_id: item for item in task.robots
                if item.capability.active
            }
            if changed and replan is not None:
                replacement = self._call_replan(
                    replan, task=task, task_order=task_order, states=states,
                    feedback=feedback, last_decision=decisions[-1], rounds=rounds,
                    condition=initial.condition, task_by_id=task_by_id,
                    robot_by_id=robot_by_id,
                )
                rounds += 1
                if replacement is None:
                    termination_reason = "planner_stopped"
                    break
                decisions.append(replacement)
                active_assignments = {item.task_id: item for item in replacement.assignments}
                _append_decision_event(ledger, replacement, round_index=rounds)
                if rounds >= limits.max_rounds:
                    termination_reason = "max_rounds_reached"
                    break

            ready = self._ready_assignments(
                task_order,
                task_by_id,
                states,
                active_assignments,
            )
            if not ready:
                if replan is None:
                    termination_reason = (
                        "partial_failure"
                        if any(state in {"failed", "blocked"} for state in states.values())
                        else "no_ready_assignments"
                    )
                    break
                replacement = self._call_replan(
                    replan,
                    task=task,
                    task_order=task_order,
                    states=states,
                    feedback=feedback,
                    last_decision=decisions[-1],
                    rounds=rounds,
                    condition=initial.condition,
                    task_by_id=task_by_id,
                    robot_by_id=robot_by_id,
                )
                rounds += 1
                if replacement is None:
                    termination_reason = "planner_stopped"
                    break
                decisions.append(replacement)
                active_assignments = {
                    assignment.task_id: assignment
                    for assignment in replacement.assignments
                }
                _append_decision_event(ledger, replacement, round_index=rounds)
                continue

            if dispatch_count + len(ready) > limits.max_dispatches:
                termination_reason = "max_dispatches_reached"
                break
            # A previously valid assignment may lose its live capability before
            # dispatch. Never execute it against an obsolete roster.
            _validate_decision(
                AlgorithmDecision(condition=initial.condition, assignments=ready),
                task_by_id=task_by_id, robot_by_id=robot_by_id,
            )
            if self.orchestration_executor is None:
                for assignment in ready:
                    feedback.append(
                        ExecutionFeedback(
                            task_id=assignment.task_id,
                            robot_id=assignment.robot_id,
                            outcome="not_executed",
                            failure_code=FailureCode.MILESTONE_UNVERIFIED,
                            message=(
                                "No OrchestrationExecutor was injected; "
                                "dependency progress is unavailable."
                            ),
                        )
                    )
                ledger.append(
                    "executor_unavailable",
                    assignments=[item.__dict__ for item in ready],
                )
                termination_reason = "executor_unavailable"
                break

            for assignment in ready:
                ledger.append(
                    "assignment_applied",
                    round_index=rounds,
                    task_id=assignment.task_id,
                    robot_id=assignment.robot_id,
                )
                # A dispatched assignment is consumed.  Retry requires the
                # replan callback to assign it again explicitly.
                active_assignments.pop(assignment.task_id, None)
            updates = self._execute_ready(ready, task_by_id, scene)
            dispatch_count += len(ready)
            rounds += 1
            for assignment, update in zip(ready, updates, strict=True):
                feedback.append(update.feedback)
                if update.dependency_outcome is DependencyOutcome.COMPLETED:
                    states[assignment.task_id] = "completed"
                elif update.dependency_outcome is DependencyOutcome.TERMINAL_FAILURE:
                    states[assignment.task_id] = "failed"
                else:
                    states[assignment.task_id] = "pending"
                ledger.append(
                    "execution_update",
                    round_index=rounds,
                    task_id=assignment.task_id,
                    robot_id=assignment.robot_id,
                    dependency_outcome=update.dependency_outcome.value,
                    feedback_outcome=update.feedback.outcome,
                    failure_code=update.feedback.failure_code.value,
                )

            self._propagate_blocked_dependencies(states, task_by_id)
            refresh_observation(after_dispatch=True)
            robot_by_id = {
                item.capability.robot_id: item for item in task.robots
                if item.capability.active
            }
            if all(state == "completed" for state in states.values()):
                termination_reason = "all_dependencies_completed"
                break
            if replan is not None:
                if rounds >= limits.max_rounds:
                    termination_reason = "max_rounds_reached"
                    break
                replacement = self._call_replan(
                    replan,
                    task=task,
                    task_order=task_order,
                    states=states,
                    feedback=feedback,
                    last_decision=decisions[-1],
                    rounds=rounds,
                    condition=initial.condition,
                    task_by_id=task_by_id,
                    robot_by_id=robot_by_id,
                )
                if replacement is None:
                    termination_reason = "planner_stopped"
                    break
                decisions.append(replacement)
                active_assignments = {
                    assignment.task_id: assignment
                    for assignment in replacement.assignments
                }
                _append_decision_event(ledger, replacement, round_index=rounds)

        self._propagate_blocked_dependencies(states, task_by_id)
        if self.orchestration_executor is None:
            verdict: Mapping[str, Any] = {
                "status": "N/A",
                "reason": "physical_evidence_required",
            }
        elif self.evaluator is None:
            verdict = {
                "status": "N/A",
                "reason": "physical_evidence_required",
            }
        else:
            verdict = self.evaluator.evaluate(
                task=task,
                decision=decisions[-1],
                feedback=tuple(feedback),
                scene=scene,
            )
        verdict = {
            **dict(verdict),
            "scheduler_termination": termination_reason,
            "task_states": dict(states),
        }
        # Executor-owned runtime provenance must travel with the scheduler
        # ledger.  This is an opaque, digest-bound snapshot: the runner never
        # interprets executor events as evaluator facts or success evidence.
        executor_ledger = getattr(self.orchestration_executor, "ledger", None)
        if executor_ledger is not None:
            events = getattr(executor_ledger, "events", None)
            digest = getattr(executor_ledger, "digest", None)
            schema_version = getattr(executor_ledger, "schema_version", None)
            if not isinstance(events, tuple) or not callable(digest):
                raise TypeError("executor ledger must expose events, digest(), and schema_version")
            ledger.append(
                "executor_ledger",
                schema_version=str(schema_version),
                digest=str(digest()),
                events=[dict(event) for event in events],
            )
        ledger.append(
            "scheduler_terminated",
            reason=termination_reason,
            rounds=rounds,
            dispatch_count=dispatch_count,
            task_states=dict(states),
        )
        ledger.append(
            "verdict",
            status=verdict.get("status", "N/A"),
            reason=verdict.get("reason"),
        )
        return OrchestratedTaskRunResult(
            request=request,
            decisions=tuple(decisions),
            feedback=tuple(feedback),
            verdict=verdict,
            ledger=ledger,
            task_states=states,
            termination_reason=termination_reason,
            rounds=rounds,
            dispatch_count=dispatch_count,
        )

    @staticmethod
    def _propagate_blocked_dependencies(
        states: dict[str, str],
        task_by_id: Mapping[str, Any],
    ) -> None:
        changed = True
        while changed:
            changed = False
            for task_id, state in tuple(states.items()):
                if state != "pending":
                    continue
                prerequisites = task_by_id[task_id].prerequisites
                if any(states[item] in {"failed", "blocked"} for item in prerequisites):
                    states[task_id] = "blocked"
                    changed = True

    @staticmethod
    def _ready_assignments(
        task_order: Sequence[str],
        task_by_id: Mapping[str, Any],
        states: Mapping[str, str],
        active_assignments: Mapping[str, Assignment],
    ) -> tuple[Assignment, ...]:
        ready: list[Assignment] = []
        busy_robots: set[str] = set()
        for task_id in task_order:
            assignment = active_assignments.get(task_id)
            if assignment is None or states[task_id] != "pending":
                continue
            if assignment.robot_id in busy_robots:
                continue
            if all(states[item] == "completed" for item in task_by_id[task_id].prerequisites):
                ready.append(assignment)
                busy_robots.add(assignment.robot_id)
        return tuple(ready)

    def _execute_ready(
        self,
        assignments: Sequence[Assignment],
        task_by_id: Mapping[str, Any],
        scene: Any,
    ) -> tuple[ExecutionUpdate, ...]:
        executor = self.orchestration_executor
        assert executor is not None
        execute_batch = getattr(executor, "execute_batch", None)
        if callable(execute_batch):
            raw_updates = tuple(
                execute_batch(assignments, task_by_id, scene=scene)
            )
        else:
            raw_updates = tuple(
                executor.execute(
                    assignment,
                    task_by_id[assignment.task_id],
                    scene=scene,
                )
                for assignment in assignments
            )
        if any(not isinstance(item, ExecutionUpdate) for item in raw_updates):
            raise TypeError("orchestration executor must return ExecutionUpdate records")
        expected = {(item.task_id, item.robot_id) for item in assignments}
        by_identity: dict[tuple[str, str], ExecutionUpdate] = {}
        for update in raw_updates:
            if (update.dependency_outcome is DependencyOutcome.COMPLETED) != (
                update.feedback.failure_code is FailureCode.NONE
            ):
                raise ValueError("dependency outcome contradicts execution failure code")
            identity = (update.feedback.task_id, update.feedback.robot_id)
            if identity in by_identity:
                raise ValueError(f"executor returned duplicate feedback: {identity}")
            by_identity[identity] = update
        if set(by_identity) != expected:
            raise ValueError(
                "executor feedback identities do not match dispatched assignments"
            )
        return tuple(
            by_identity[(item.task_id, item.robot_id)] for item in assignments
        )

    @staticmethod
    def _call_replan(
        replan: ReplanProvider,
        *,
        task: Any,
        task_order: Sequence[str],
        states: Mapping[str, str],
        feedback: Sequence[ExecutionFeedback],
        last_decision: AlgorithmDecision,
        rounds: int,
        condition: Any,
        task_by_id: Mapping[str, Any],
        robot_by_id: Mapping[str, Any],
    ) -> AlgorithmDecision | None:
        state = OrchestrationState(
            round_index=rounds,
            completed_task_ids=tuple(
                item for item in task_order if states[item] == "completed"
            ),
            failed_task_ids=tuple(
                item for item in task_order if states[item] == "failed"
            ),
            blocked_task_ids=tuple(
                item for item in task_order if states[item] == "blocked"
            ),
            pending_task_ids=tuple(
                item for item in task_order if states[item] == "pending"
            ),
            feedback=tuple(feedback),
            last_decision=last_decision,
        )
        output = replan(task, task.robots, state)
        if output is None:
            return None
        decision = _normalise_decision(output, condition=condition)
        if decision.condition != condition:
            raise ValueError("replan must preserve the initial AlgorithmCondition")
        _validate_decision(
            decision,
            task_by_id=task_by_id,
            robot_by_id=robot_by_id,
            allowed_task_ids=set(state.pending_task_ids),
        )
        return decision


__all__ = [
    "BatchOrchestrationExecutor",
    "DecisionProvider",
    "EvidenceEvaluator",
    "OrchestrationExecutor",
    "ReplayBackend",
    "ReplanProvider",
    "SkillExecutor",
    "TaskRunner",
]
