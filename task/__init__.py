"""Task-level orchestration for HeteroCoord.

The task package is intentionally independent from Isaac Sim.  It owns task
contracts, algorithm decision records, execution delegation, replay metadata,
and evidence aggregation.  EAI remains the only owner of scene construction,
controllers, skills, and simulator lifecycle.
"""

from .contracts import (
    AlgorithmDecision,
    EaiTaskPackage,
    RobotState,
    TaskRunRequest,
    TaskRunResult,
)
from .runner import TaskRunner

__all__ = [
    "AlgorithmDecision",
    "EaiTaskPackage",
    "RobotState",
    "TaskRunRequest",
    "TaskRunResult",
    "TaskRunner",
]
