"""Stateless Fire Rescue assignment example; replace assign() with your algorithm."""

from .policy import AssignmentRequest


def assign(request: AssignmentRequest) -> dict[str, str]:
    available = set(request.robot_positions)
    scenario = request.scenario
    result = {}
    order = [scenario.green_subtask_id, scenario.yellow_subtask_id]
    order.extend(subtask for subtask in scenario.subtasks if subtask not in order)
    for subtask in order:
        candidates = sorted(available)
        if subtask == scenario.green_subtask_id and scenario.green_robot_name:
            candidates = [name for name in candidates if name == scenario.green_robot_name]
        if subtask == scenario.yellow_subtask_id and scenario.yellow_robot_prefix:
            candidates = [name for name in candidates if name.startswith(scenario.yellow_robot_prefix)]
        if not candidates:
            continue
        target = request.subtask_positions[subtask]
        selected = min(candidates, key=lambda name: sum(
            (actual - desired) ** 2 for actual, desired in zip(request.robot_positions[name][:2], target)
        ))
        result[selected] = subtask
        available.remove(selected)
    return result
