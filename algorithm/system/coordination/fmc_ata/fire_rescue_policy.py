"""Fire Rescue dispatch boundary for FMC-ATA.

FMC-ATA allocates divisible workload, while the benchmark's colour-coded
Fire Rescue skills are indivisible physical actions.  The market result is
therefore reported as ``N/A`` for this scene.  The callback still returns the
scenario-owned fallback mapping so the rest of the EAI execution, feedback,
checkpoint and replay path can be exercised without relabelling that mapping
as an FMC-ATA decision.
"""

from __future__ import annotations

from typing import Mapping

from algorithm.system.coordination.emos.policy import AssignmentRequest
from algorithm.system.coordination.emos.policy import assignment_tasks


class FMCATAFireRescueNotApplicable(ValueError):
    """The current Fire Rescue task graph has no declared divisible workload."""


def default_assignment(request: AssignmentRequest) -> Mapping[str, str]:
    """Return the scenario-owned mapping used to keep FMC-ATA executable.

    FMC-ATA remains ``N/A`` as an allocation result for indivisible Fire Rescue
    skills.  The benchmark may nevertheless evaluate its downstream execution
    contract by supplying the declared scenario fallback mapping.  Validation
    is delegated to the shared EAI assignment contract so green/yellow safety
    constraints and completed-task exclusions are still enforced.
    """
    if not isinstance(request, AssignmentRequest):
        raise TypeError("FMC-ATA Fire Rescue policy expects an AssignmentRequest")
    preferred = dict(getattr(request.scenario, "preferred_fallback", {}) or {})
    excluded = frozenset(request.excluded_subtask_ids)
    mapping = {
        str(robot_id): str(task_id)
        for robot_id, task_id in preferred.items()
        if str(robot_id) in request.robot_positions
        and str(task_id) in request.scenario.subtasks
        and str(task_id) not in excluded
    }
    if not mapping:
        raise FMCATAFireRescueNotApplicable(
            "FMC-ATA has no declared default Fire Rescue mapping for this request"
        )
    assignment_tasks(request, mapping)
    return mapping


def assign(request: AssignmentRequest) -> Mapping[str, str]:
    # The allocation itself is explicitly marked N/A by the bridge.  Returning
    # the declared fallback lets the benchmark exercise execution, feedback,
    # timeout and replay end to end without pretending a fractional share is a
    # physical robot task.
    return default_assignment(request)


__all__ = ["FMCATAFireRescueNotApplicable", "assign", "default_assignment"]
