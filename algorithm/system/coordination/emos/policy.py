"""Data-only assignment callback boundary for task runners using EMOS orchestration."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import importlib
import inspect
import math
from typing import Callable, Mapping

from .types import EMOSScenarioConfig, RobotTask, resolve_subtask_target_xy


@dataclass(frozen=True)
class AssignmentRequest:
    task_description: str
    scenario: EMOSScenarioConfig
    hazard_id: int
    hazard_pos: tuple[float, float, float]
    robot_positions: dict[str, tuple[float, float, float]]
    subtask_positions: dict[str, tuple[float, float]]
    completed_extinguisher: tuple[str, ...]
    completed_rescue: tuple[str, ...]
    rescue_constraints: str | None
    excluded_subtask_ids: tuple[str, ...] = ()
    # Label only: ``native_root_state`` vs ``public_spawn_anchor`` etc. Never
    # treat a public spawn map as a live PhysX sample.
    position_source: str | None = None


def validate_assignment_policy(callback: Callable) -> Callable:
    if not inspect.isfunction(callback) or callback.__closure__ or vars(callback):
        raise ValueError("assignment policy must be a stateless module-level function without a closure")
    try:
        inspect.signature(callback).bind(object())
    except TypeError as exc:
        raise ValueError("assignment policy must accept one request argument") from exc
    return callback


def load_assignment_policy(specification: str) -> Callable | None:
    if specification == "emos":
        return None
    module_name, separator, attribute = specification.partition(":")
    if not separator or not module_name or not attribute or ":" in attribute:
        raise ValueError("assignment policy must be emos or a trusted module:assign function")
    try:
        callback = getattr(importlib.import_module(module_name), attribute)
    except (ImportError, AttributeError) as exc:
        raise ValueError(f"cannot load assignment policy {specification}: {exc}") from exc
    return validate_assignment_policy(callback)


def make_assignment_request(manager, task_description, subtask_positions) -> AssignmentRequest:
    from .assignment import excluded_subtask_ids_for_completed

    robot_positions = {}
    for name in manager._agents:
        position = tuple(float(value) for value in manager._get_pos(name))
        if len(position) != 3 or not all(math.isfinite(value) for value in position):
            raise ValueError(f"invalid robot position for assignment: {name}")
        robot_positions[name] = position
    constraints = None
    if manager._rescue_constraint_fn is not None:
        constraints = manager._rescue_constraint_fn()
    elif manager._active_rescue_manager is not None:
        from .scene_builder import rescue_manager_constraint_text

        constraints = rescue_manager_constraint_text(manager._active_rescue_manager)
    excluded = tuple(sorted(
        excluded_subtask_ids_for_completed(
            manager._scenario,
            manager._agents,
            manager._robots_completed_extinguisher,
            manager._robots_completed_rescue,
        )
    ))
    return AssignmentRequest(
        task_description=task_description, scenario=deepcopy(manager._scenario),
        hazard_id=manager._hazard_id, hazard_pos=tuple(manager._hazard_pos),
        robot_positions=robot_positions, subtask_positions=deepcopy(subtask_positions),
        completed_extinguisher=tuple(sorted(manager._robots_completed_extinguisher)),
        completed_rescue=tuple(sorted(manager._robots_completed_rescue)),
        rescue_constraints=constraints,
        excluded_subtask_ids=excluded,
        position_source=getattr(manager, "position_source", None),
    )


def assignment_tasks(request: AssignmentRequest, assignments: Mapping[str, str]) -> dict[str, RobotTask]:
    if not isinstance(assignments, Mapping) or not assignments:
        raise ValueError("assignment policy must return a non-empty robot-name to subtask-id mapping")
    scenario = request.scenario
    excluded = frozenset(request.excluded_subtask_ids)
    result = {}
    for robot_name, subtask_id in assignments.items():
        if not isinstance(robot_name, str) or not isinstance(subtask_id, str):
            raise ValueError("assignment robot names and subtask ids must be strings")
        if robot_name not in request.robot_positions or subtask_id not in scenario.subtasks:
            raise ValueError(f"unknown robot/subtask assignment: {robot_name!r} -> {subtask_id!r}")
        if subtask_id in excluded:
            raise ValueError(f"excluded subtask cannot be assigned: {subtask_id!r}")
        if subtask_id == scenario.green_subtask_id and scenario.green_robot_name and robot_name != scenario.green_robot_name:
            raise ValueError(f"{subtask_id} requires {scenario.green_robot_name}, not {robot_name}")
        if subtask_id == scenario.yellow_subtask_id and scenario.yellow_robot_prefix and not robot_name.startswith(scenario.yellow_robot_prefix):
            raise ValueError(f"{subtask_id} requires a {scenario.yellow_robot_prefix} robot, not {robot_name}")
        subtask = scenario.subtasks[subtask_id]
        target_xy = resolve_subtask_target_xy(
            scenario, subtask_id, request.subtask_positions)
        result[robot_name] = RobotTask(
            robot_name=robot_name, subtask_colour=subtask_id, subtask_name=subtask.name,
            subtask_desc=subtask.description, target_xy=target_xy,
        )
    return result
