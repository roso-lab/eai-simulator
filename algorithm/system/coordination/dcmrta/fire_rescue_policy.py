"""Fire Rescue dispatch policy for the DCMRTA comparison condition."""

from __future__ import annotations

from typing import Mapping

from algorithm.system.coordination.emos.policy import AssignmentRequest, assignment_tasks


def default_assignment(request: AssignmentRequest) -> Mapping[str, str]:
    """Use the scenario-owned assignment for the physical EAI track.

    This keeps the DCMRTA checkpoint's native decision result separate from
    the cross-domain Fire Rescue execution. The bridge records the distinction
    in ``allocation_provider`` and the manifest condition.
    """
    if not isinstance(request, AssignmentRequest):
        raise TypeError("DCMRTA Fire Rescue policy expects an AssignmentRequest")
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
        raise ValueError("DCMRTA comparison has no declared default Fire Rescue mapping")
    assignment_tasks(request, mapping)
    return mapping


def assign(request: AssignmentRequest) -> Mapping[str, str]:
    return default_assignment(request)


__all__ = ["assign", "default_assignment"]
