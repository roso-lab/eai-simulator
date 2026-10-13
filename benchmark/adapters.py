"""Protocols separating benchmark orchestration from concrete backends."""

from __future__ import annotations

from typing import Any, Callable, Mapping, Protocol, Sequence

from algorithm.common_coordination.contract import Assignment, ExecutionFeedback
from algorithm.skills.contracts import SkillPlan, SkillRequest
from task.contracts import AlgorithmDecision, EaiTaskPackage, RobotState

from .evidence import EvaluationReport
from .protocol import BranchSpec, CheckpointRef


class DecisionProvider(Protocol):
    """High-level brain output; it must not claim physical success."""

    def __call__(
        self,
        task: EaiTaskPackage,
        robots: Sequence[RobotState],
    ) -> AlgorithmDecision:
        ...


class SkillRequestFactory(Protocol):
    """Map a task assignment to one typed low-level skill request."""

    def __call__(self, assignment: Assignment, task: Any, *, request_id: str) -> SkillRequest:
        ...


class SkillAdapter(Protocol):
    """Translate one assignment into a provider-owned skill plan."""

    def plan(self, request: SkillRequest, *, observation: Mapping[str, Any] | None = None) -> SkillPlan:
        ...


class TaskExecutor(Protocol):
    """Execute a provider plan through a controller/runtime."""

    def execute(
        self,
        assignment: Assignment,
        task: Any,
        *,
        scene: Any,
        plan: SkillPlan,
        step: Callable[[Mapping[str, Any]], Any],
    ) -> ExecutionFeedback:
        ...


class EvidenceEvaluator(Protocol):
    """Read external runtime evidence; never infer success from a plan."""

    def evaluate(
        self,
        *,
        task: Any,
        observations: Sequence[Mapping[str, Any]],
        branch: BranchSpec | None,
    ) -> EvaluationReport:
        ...


class ReplayBackend(Protocol):
    """Restore a requested checkpoint and report backend-owned provenance."""

    def fork(self, checkpoint: CheckpointRef, branch: BranchSpec) -> Any:
        ...
