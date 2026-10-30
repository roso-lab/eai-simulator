from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence

from TeamWeaver.eai_adapter.task_models import (
    CapabilityRequirement,
    RobotSnapshot,
    SemanticTask,
    SymbolicWorldState,
    TaskType,
)


CAPABILITIES = frozenset(
    {
        "navigation",
        "sensing",
        "relay",
        "payload",
        "agility",
        "manipulation",
        "button_press",
        "extinguisher_handling",
        "obstacle_handling",
    }
)

_LEGACY_SKILL_REQUIREMENTS: Mapping[TaskType, Mapping[str, CapabilityRequirement]] = {
    TaskType.NAVIGATE: {
        "navigation": CapabilityRequirement(0.6, 1.0, True),
    },
    TaskType.INSPECT: {
        "navigation": CapabilityRequirement(0.6, 1.0, True),
        "sensing": CapabilityRequirement(0.5, 1.0, False),
    },
    TaskType.ESTABLISH_RELAY: {
        "navigation": CapabilityRequirement(0.6, 1.0, True),
        "relay": CapabilityRequirement(0.6, 1.0, True),
    },
    TaskType.PICK_EXTINGUISHER: {
        "navigation": CapabilityRequirement(0.6, 1.0, True),
        "manipulation": CapabilityRequirement(0.7, 1.0, True),
        "payload": CapabilityRequirement(0.7, 0.8, True),
        "extinguisher_handling": CapabilityRequirement(0.7, 1.0, True),
    },
    TaskType.DELIVER_EXTINGUISHER: {
        "navigation": CapabilityRequirement(0.6, 1.0, True),
        "manipulation": CapabilityRequirement(0.7, 1.0, True),
        "payload": CapabilityRequirement(0.7, 0.8, True),
        "extinguisher_handling": CapabilityRequirement(0.7, 1.0, True),
    },
    TaskType.PRESS_RESCUE_BUTTON: {
        "navigation": CapabilityRequirement(0.6, 1.0, True),
        "manipulation": CapabilityRequirement(0.7, 1.0, True),
        "button_press": CapabilityRequirement(0.7, 1.0, True),
    },
    TaskType.REMOVE_OBSTACLE: {
        "navigation": CapabilityRequirement(0.6, 1.0, True),
        "manipulation": CapabilityRequirement(0.7, 1.0, True),
        "obstacle_handling": CapabilityRequirement(0.7, 1.0, True),
        "payload": CapabilityRequirement(0.7, 0.8, True),
    },
    TaskType.WAIT: {},
}

_LEGACY_SKILL_EQUIPMENT: Mapping[TaskType, frozenset[str]] = {
    TaskType.PICK_EXTINGUISHER: frozenset({"manipulator", "gripper"}),
    TaskType.DELIVER_EXTINGUISHER: frozenset({"manipulator", "gripper"}),
    TaskType.PRESS_RESCUE_BUTTON: frozenset({"manipulator"}),
    TaskType.REMOVE_OBSTACLE: frozenset({"manipulator", "gripper"}),
}

_TASK_FIELDS = frozenset(
    {
        "task_id",
        "task_type",
        "description",
        "target_ref",
        "priority",
        "requirements",
        "prerequisites",
        "can_parallel",
        "estimated_duration_s",
        "preferred_agent",
    }
)
_REQUIREMENT_FIELDS = frozenset({"minimum", "weight", "hard"})
_TASK_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_SYSTEM_TASK_ID_PREFIX = "system_fire_return_"
_MAX_TASK_DURATION_S = 600.0
_DELIVERY_HANDLING_MARGIN_S = 30.0
_DELIVERY_ROUTE_FACTOR = 1.25
_LOADED_DELIVERY_SPEED_MPS = 0.05


class TaskValidationError(ValueError):
    def __init__(self, errors: str | Sequence[str]) -> None:
        self.errors = (
            (str(errors),) if isinstance(errors, str) else tuple(str(item) for item in errors)
        )
        super().__init__("; ".join(self.errors))


class PlanValidationError(ValueError):
    def __init__(self, errors: str | Sequence[str]) -> None:
        self.errors = (
            (str(errors),) if isinstance(errors, str) else tuple(str(item) for item in errors)
        )
        super().__init__("; ".join(self.errors))


def required_equipment(task_type: TaskType) -> frozenset[str]:
    """Return the hardware a robot must carry for *task_type*.

    Consults the active :class:`~.scenario_config.TeamWeaverScenarioConfig`
    first; falls back to the legacy ``SKILL_EQUIPMENT`` table.
    """
    from TeamWeaver.eai_adapter.scenario_config import get_active_scenario

    scenario = get_active_scenario()
    if scenario is not None:
        spec = scenario.task_specs.get(task_type.value)
        if spec is not None and spec.equipment:
            return spec.equipment
    return _LEGACY_SKILL_EQUIPMENT.get(TaskType(task_type), frozenset())


def _get_skill_requirements(
    task_type: TaskType,
) -> Mapping[str, CapabilityRequirement]:
    """Return invariant capability requirements for *task_type*.

    Consults the active scenario first; falls back to the legacy table.
    """
    from TeamWeaver.eai_adapter.scenario_config import get_active_scenario

    scenario = get_active_scenario()
    if scenario is not None:
        spec = scenario.task_specs.get(task_type.value)
        if spec is not None and spec.invariant_requirements:
            return MappingProxyType(
                {
                    name: (
                        req
                        if isinstance(req, CapabilityRequirement)
                        else CapabilityRequirement(**req)
                    )
                    for name, req in spec.invariant_requirements.items()
                }
            )
    return _LEGACY_SKILL_REQUIREMENTS.get(TaskType(task_type), {})


def _get_capabilities() -> frozenset[str]:
    """Return the closed set of capability names for the active scenario."""
    from TeamWeaver.eai_adapter.scenario_config import get_active_scenario

    scenario = get_active_scenario()
    if scenario is not None:
        return scenario.capabilities
    return CAPABILITIES



def validate_task_payload(
    payload: Mapping[str, Any],
    world: SymbolicWorldState,
) -> SemanticTask:
    if not isinstance(payload, Mapping):
        raise TaskValidationError("task must be an object")

    errors: list[str] = []
    fields = frozenset(str(key) for key in payload)
    missing = sorted(_TASK_FIELDS - fields)
    extra = sorted(fields - _TASK_FIELDS)
    if missing:
        errors.append(f"missing fields: {', '.join(missing)}")
    if extra:
        errors.append(f"unknown fields: {', '.join(extra)}")

    task_id = payload.get("task_id")
    if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id.strip()):
        errors.append("task_id must be a non-empty identifier up to 64 characters")
        normalized_task_id = str(task_id or "")
    else:
        normalized_task_id = task_id.strip()
        if normalized_task_id.startswith(_SYSTEM_TASK_ID_PREFIX):
            errors.append("task_id uses a reserved system task prefix")
        if normalized_task_id in world.completed_task_ids:
            errors.append(
                f"task_id is already completed: {normalized_task_id}"
            )

    raw_task_type = payload.get("task_type")
    try:
        task_type = TaskType(raw_task_type)
    except (TypeError, ValueError):
        errors.append(f"unknown task_type: {raw_task_type!r}")
        task_type = None

    description = payload.get("description")
    if not isinstance(description, str) or not description.strip():
        errors.append("description must be a non-empty string")
        normalized_description = str(description or "")
    else:
        normalized_description = description.strip()

    target_ref = payload.get("target_ref")
    target = None
    if not isinstance(target_ref, str) or not target_ref.strip():
        errors.append("target_ref must be a non-empty string")
        normalized_target_ref = str(target_ref or "")
    else:
        normalized_target_ref = target_ref.strip()
        try:
            target = world.target_by_ref(normalized_target_ref)
        except KeyError:
            errors.append(f"unknown target: {normalized_target_ref}")
        else:
            if not target.available:
                errors.append(f"target is unavailable: {normalized_target_ref}")
            if target.kind.startswith("system_"):
                errors.append(
                    f"target is reserved for system tasks: {normalized_target_ref}"
                )
            if task_type is not None and task_type not in target.compatible_task_types:
                errors.append(
                    f"target {normalized_target_ref} is not compatible with "
                    f"task type {task_type.value}"
                )

    priority = _bounded_integer(payload.get("priority"), 1, 5, "priority", errors)
    duration = _bounded_number(
        payload.get("estimated_duration_s"),
        1.0,
        _MAX_TASK_DURATION_S,
        "estimated_duration_s",
        errors,
    )

    raw_parallel = payload.get("can_parallel")
    if not isinstance(raw_parallel, bool):
        errors.append("can_parallel must be a boolean")
    can_parallel = raw_parallel if isinstance(raw_parallel, bool) else False

    raw_preferred = payload.get("preferred_agent")
    robot_names = {robot.name for robot in world.robots}
    if raw_preferred is not None and (
        not isinstance(raw_preferred, str) or raw_preferred not in robot_names
    ):
        errors.append(f"unknown preferred_agent: {raw_preferred!r}")
    preferred_agent = raw_preferred if isinstance(raw_preferred, str) else None

    prerequisites = _validate_prerequisites(
        payload.get("prerequisites"), normalized_task_id, errors
    )
    requirements = _validate_requirements(payload.get("requirements"), errors)
    if task_type is not None:
        requirements = _merge_skill_requirements(task_type, requirements)
    duration = _apply_physical_duration_floor(
        task_type,
        duration,
        target,
        world,
        errors,
    )

    if errors:
        raise TaskValidationError(errors)
    assert task_type is not None
    assert priority is not None
    assert duration is not None
    return SemanticTask(
        task_id=normalized_task_id,
        task_type=task_type,
        description=normalized_description,
        target_ref=normalized_target_ref,
        priority=priority,
        requirements=requirements,
        prerequisites=prerequisites,
        can_parallel=can_parallel,
        estimated_duration_s=duration,
        preferred_agent=preferred_agent,
    )


def _apply_physical_duration_floor(
    task_type: TaskType | None,
    duration_s: float | None,
    target: Any,
    world: SymbolicWorldState,
    errors: list[str],
) -> float | None:
    if task_type is not TaskType.DELIVER_EXTINGUISHER or duration_s is None:
        return duration_s
    if target is None or target.position is None:
        errors.append("delivery target position is unavailable")
        return duration_s

    if world.extinguisher_carrier is not None:
        try:
            start = world.robot_by_name(world.extinguisher_carrier).position
        except KeyError:
            errors.append(
                f"unknown extinguisher carrier: {world.extinguisher_carrier}"
            )
            return duration_s
    else:
        try:
            pickup = world.target_by_ref("fire_extinguisher_pickup")
        except KeyError:
            errors.append("missing fire_extinguisher_pickup target")
            return duration_s
        if pickup.position is None:
            errors.append("fire_extinguisher_pickup position is unavailable")
            return duration_s
        start = pickup.position[:2]

    distance_m = math.dist(start[:2], target.position[:2])
    physical_floor_s = (
        _DELIVERY_HANDLING_MARGIN_S
        + _DELIVERY_ROUTE_FACTOR * distance_m / _LOADED_DELIVERY_SPEED_MPS
    )
    return min(_MAX_TASK_DURATION_S, max(duration_s, physical_floor_s))


def validate_plan_payload(
    payload: Mapping[str, Any],
    world: SymbolicWorldState,
) -> tuple[SemanticTask, ...]:
    if not isinstance(payload, Mapping):
        raise PlanValidationError("response must be a JSON object")
    if set(payload) != {"tasks"}:
        raise PlanValidationError(
            "response must contain exactly one top-level tasks field"
        )
    raw_tasks = payload.get("tasks")
    if not isinstance(raw_tasks, list):
        raise PlanValidationError("tasks must be a list")

    errors: list[str] = []
    if not 1 <= len(raw_tasks) <= 16:
        errors.append("tasks must contain between 1 and 16 items")

    tasks: list[SemanticTask] = []
    for index, raw_task in enumerate(raw_tasks):
        try:
            tasks.append(validate_task_payload(raw_task, world))
        except TaskValidationError as exc:
            errors.extend(f"tasks[{index}]: {item}" for item in exc.errors)

    raw_ids = [
        item.get("task_id").strip()
        for item in raw_tasks
        if isinstance(item, Mapping) and isinstance(item.get("task_id"), str)
    ]
    duplicates = sorted({task_id for task_id in raw_ids if raw_ids.count(task_id) > 1})
    if duplicates:
        errors.append(f"duplicate task ids: {', '.join(duplicates)}")

    returned_ids = set(raw_ids)
    allowed_prerequisites = returned_ids | set(world.completed_task_ids)
    graph: dict[str, tuple[str, ...]] = {}
    for index, raw_task in enumerate(raw_tasks):
        if not isinstance(raw_task, Mapping):
            continue
        task_id = raw_task.get("task_id")
        prerequisites = raw_task.get("prerequisites")
        if not isinstance(task_id, str) or not isinstance(prerequisites, list):
            continue
        task_id = task_id.strip()
        string_prerequisites = tuple(
            item.strip() for item in prerequisites if isinstance(item, str)
        )
        graph[task_id] = tuple(
            item for item in string_prerequisites if item in returned_ids
        )
        unknown = sorted(set(string_prerequisites) - allowed_prerequisites)
        if unknown:
            errors.append(
                f"tasks[{index}]: unknown prerequisites: {', '.join(unknown)}"
            )
    if _has_cycle(graph):
        errors.append("task prerequisite graph contains a cycle")

    _validate_obstacle_plan(tasks, world, errors)

    if errors:
        raise PlanValidationError(errors)
    return tuple(tasks)


def _validate_obstacle_plan(
    tasks: Sequence[SemanticTask],
    world: SymbolicWorldState,
    errors: list[str],
) -> None:
    removals = tuple(
        task for task in tasks if task.task_type is TaskType.REMOVE_OBSTACLE
    )
    obstacle = world.active_obstacle()
    if obstacle is None or obstacle.blocking_task_id is None:
        if removals:
            errors.append("obstacle removal requires an active blocking obstacle")
        return
    if len(removals) != 1:
        errors.append("active blocking obstacle requires exactly one obstacle removal task")
        return

    removal = removals[0]
    if removal.target_ref != obstacle.obstacle_id:
        errors.append("obstacle removal task must target active obstacle")
    if removal.priority != 1:
        errors.append("obstacle removal task must have priority 1")
    if not removal.can_parallel:
        errors.append("obstacle removal task must allow independent parallel work")
    if removal.preferred_agent is not None:
        errors.append("obstacle removal task cannot prefer a robot")

    replacements = tuple(
        task for task in tasks if task.task_id == obstacle.blocking_task_id
    )
    if (
        len(replacements) != 1
        or removal.task_id not in replacements[0].prerequisites
    ):
        errors.append("blocked task must depend on obstacle removal")


def hard_feasible(robot: RobotSnapshot, task: SemanticTask) -> bool:
    if not robot.safe:
        return False
    if robot.busy and not (
        task.task_type is TaskType.REMOVE_OBSTACLE
        and robot.preemptible
        and robot.current_task is not None
    ):
        return False
    if task.required_agent is not None and robot.name != task.required_agent:
        return False
    if not required_equipment(task.task_type).issubset(robot.equipment):
        return False
    effective = robot.effective_capabilities
    return all(
        effective.get(name, 0.0) >= requirement.minimum
        for name, requirement in task.requirements.items()
        if requirement.hard
    )


def _validate_requirements(
    payload: Any,
    errors: list[str],
) -> dict[str, CapabilityRequirement]:
    if not isinstance(payload, Mapping):
        errors.append("requirements must be an object")
        return {}
    result: dict[str, CapabilityRequirement] = {}
    for raw_name, raw_requirement in payload.items():
        name = str(raw_name)
        if name not in _get_capabilities():
            errors.append(f"unknown capability: {name}")
            continue
        if not isinstance(raw_requirement, Mapping):
            errors.append(f"requirement {name} must be an object")
            continue
        fields = set(raw_requirement)
        if fields != _REQUIREMENT_FIELDS:
            missing = sorted(_REQUIREMENT_FIELDS - fields)
            extra = sorted(fields - _REQUIREMENT_FIELDS)
            if missing:
                errors.append(f"requirement {name} missing fields: {', '.join(missing)}")
            if extra:
                errors.append(f"requirement {name} unknown fields: {', '.join(extra)}")
        minimum = _bounded_number(
            raw_requirement.get("minimum"), 0.0, 1.0, f"{name}.minimum", errors
        )
        weight = _bounded_number(
            raw_requirement.get("weight"), 0.0, 1.0, f"{name}.weight", errors
        )
        hard = raw_requirement.get("hard")
        if not isinstance(hard, bool):
            errors.append(f"{name}.hard must be a boolean")
        if minimum is not None and weight is not None and isinstance(hard, bool):
            result[name] = CapabilityRequirement(minimum, weight, hard)
    return result


def _merge_skill_requirements(
    task_type: TaskType,
    proposed: Mapping[str, CapabilityRequirement],
) -> dict[str, CapabilityRequirement]:
    merged = dict(proposed)
    for name, invariant in _get_skill_requirements(task_type).items():
        proposal = merged.get(name)
        if proposal is None:
            merged[name] = invariant
        else:
            merged[name] = CapabilityRequirement(
                minimum=max(proposal.minimum, invariant.minimum),
                weight=max(proposal.weight, invariant.weight),
                hard=proposal.hard or invariant.hard,
            )
    return merged


def _validate_prerequisites(
    value: Any,
    task_id: str,
    errors: list[str],
) -> tuple[str, ...]:
    if not isinstance(value, list):
        errors.append("prerequisites must be a list")
        return ()
    if not all(isinstance(item, str) and item.strip() for item in value):
        errors.append("prerequisites must contain non-empty strings")
        return ()
    normalized = tuple(item.strip() for item in value)
    if len(set(normalized)) != len(normalized):
        errors.append("prerequisites must be unique")
    if task_id in normalized:
        errors.append("task cannot depend on itself")
    return normalized


def _bounded_integer(
    value: Any,
    lower: int,
    upper: int,
    name: str,
    errors: list[str],
) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{name} must be an integer from {lower} through {upper}")
        return None
    if not lower <= value <= upper:
        errors.append(f"{name} must be from {lower} through {upper}")
        return None
    return int(value)


def _bounded_number(
    value: Any,
    lower: float,
    upper: float,
    name: str,
    errors: list[str],
) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(f"{name} must be a number")
        return None
    result = float(value)
    if not math.isfinite(result) or not lower <= result <= upper:
        errors.append(f"{name} must be finite and from {lower:g} through {upper:g}")
        return None
    return result


def _has_cycle(graph: Mapping[str, Sequence[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for prerequisite in graph.get(node, ()):
            if visit(prerequisite):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in graph)
