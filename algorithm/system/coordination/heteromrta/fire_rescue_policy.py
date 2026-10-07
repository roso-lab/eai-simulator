"""Fire Rescue dispatch policy for the HeteroMRTA comparison condition."""

from __future__ import annotations

from typing import Mapping

from algorithm.system.coordination.emos.policy import AssignmentRequest, assignment_tasks


def assign(request: AssignmentRequest) -> Mapping[str, str]:
    """Return the scenario mapping used by the physical EAI track.

    HeteroMRTA's official checkpoint is retained for native decision-only
    evaluation.  Since its trained species/task distribution does not match
    Fire Rescue, this adapter deliberately does not pad and invoke it here.
    """
    if not isinstance(request, AssignmentRequest):
        raise TypeError("HeteroMRTA Fire Rescue policy expects an AssignmentRequest")
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
        raise ValueError("HeteroMRTA comparison has no declared default Fire Rescue mapping")
    assignment_tasks(request, mapping)
    return mapping


__all__ = ["assign"]
