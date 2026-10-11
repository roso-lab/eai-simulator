"""Generic task-level orchestration for external EAI task packages.

The task package is intentionally independent from Isaac Sim.  It owns task
contracts, algorithm decision records, execution delegation, replay metadata,
and evidence aggregation.  EAI remains the only owner of scene construction,
controllers, skills, and simulator lifecycle; evaluators own physical verdicts.
"""

from .contracts import (
    AlgorithmDecision,
    DependencyOutcome,
    EaiTaskPackage,
    ExecutionUpdate,
    OrchestratedTaskRunResult,
    OrchestrationLimits,
    OrchestrationState,
    RobotState,
    TaskRunRequest,
    TaskRunResult,
)
from .runner import TaskRunner

__all__ = [
    "AlgorithmDecision",
    "DependencyOutcome",
    "EaiTaskPackage",
    "ExecutionUpdate",
    "OrchestratedTaskRunResult",
    "OrchestrationLimits",
    "OrchestrationState",
    "RobotState",
    "TaskRunRequest",
    "TaskRunResult",
    "TaskRunner",
]
