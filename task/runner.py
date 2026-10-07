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

from .contracts import AlgorithmDecision, TaskRunRequest, TaskRunResult


class DecisionProvider(Protocol):
    def __call__(self, task: Any, robots: Sequence[Any]) -> AlgorithmDecision | Iterable[Assignment]:
        ...


class ReplayBackend(Protocol):
    def fork(self, checkpoint: str, branch_id: str) -> None:
        ...


class SkillExecutor(Protocol):
    def execute(self, assignment: Assignment, task: Any, *, scene: Any) -> ExecutionFeedback:
        ...


class EvidenceEvaluator(Protocol):
    def evaluate(self, *, task: Any, decision: AlgorithmDecision, feedback: Sequence[ExecutionFeedback], scene: Any) -> Mapping[str, Any]:
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
        evaluator: EvidenceEvaluator | None = None,
    ) -> None:
        self.scene_provider = scene_provider or (lambda scene_id: {"scene_id": scene_id})
        self.replay = replay
        self.executor = executor
        self.evaluator = evaluator

    def run(
        self,
        request: TaskRunRequest,
        provider: DecisionProvider,
        *,
        condition=None,
    ) -> TaskRunResult:
        task = request.task
        scene = self.scene_provider(task.scene_id)
        ledger = EventLedger()
        if request.checkpoint is not None:
            if self.replay is None:
                raise ValueError("checkpoint requested but no replay backend was injected")
            self.replay.fork(request.checkpoint, request.branch_id)
            ledger.append("replay_fork", checkpoint=request.checkpoint, branch_id=request.branch_id)

        output = provider(task, task.robots)
        decision = _normalise_decision(output, condition=condition)
        task_ids = {item.task_id for item in task.graph.tasks}
        robot_ids = {item.capability.robot_id for item in task.robots if item.capability.active}
        invalid = [
            item
            for item in decision.assignments
            if item.task_id not in task_ids or item.robot_id not in robot_ids
        ]
        if invalid:
            raise ValueError("algorithm decision contains an unknown task or inactive robot")
        ledger.append(
            "algorithm_decision",
            algorithm_id=decision.condition.algorithm_id,
            assignments=[item.__dict__ for item in decision.assignments],
            unassigned_tasks=list(decision.unassigned_tasks),
            provenance=dict(decision.provenance),
        )

        feedback: list[ExecutionFeedback] = []
        task_by_id = {item.task_id: item for item in task.graph.tasks}
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
                feedback.append(self.executor.execute(item, task_by_id[item.task_id], scene=scene))

        if self.evaluator is None:
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


__all__ = ["DecisionProvider", "EvidenceEvaluator", "ReplayBackend", "SkillExecutor", "TaskRunner"]
