"""HeteroCoord registration and Fire Rescue bridge for TeamWeaver.

The full paper-aligned API is :class:`TeamWeaverPipeline` (semantic
decomposition -> validation -> phase scheduling -> MIQP/Hungarian allocation
-> execution feedback -> replan).  The Fire Rescue CLI has a stateless
``module:assign`` seam, so its callback uses the *caller-supplied Fire Rescue
task contract* as the decomposition and runs the real dynamic allocator.  The
manifest labels that narrower scope explicitly; it is not silently advertised
as a DeepSeek semantic-decomposition run.
"""

from __future__ import annotations

import math
from typing import Mapping

try:  # package import from repository root
    from algorithm.common_coordination import AlgorithmCondition, Assignment, EventLedger
except ModuleNotFoundError:  # package import with ``PYTHONPATH=algorithm``
    from common_coordination import AlgorithmCondition, Assignment, EventLedger

from .dynamic_miqp import DynamicMIQPAllocator
from .task_models import (
    CapabilityRequirement,
    RobotSnapshot,
    SemanticTask,
    SymbolicWorldState,
    TargetSnapshot,
    TaskType,
)


ALGORITHM_ID = "TeamWeaver-adapted-to-EAI"
SOURCE_REFERENCE = "TeamWeaver/southking372-TeamWeaver"


def condition_record(
    *,
    model_or_api: str = "not_invoked_for_stateless_fire_rescue_hook",
    solver: str = "gurobi-or-scipy-hungarian-fallback",
    execution_scope: str = "allocation_only_fire_rescue_hook",
) -> AlgorithmCondition:
    """Describe the selected TeamWeaver condition.

    The module-level callback used by ``--assignment-policy`` receives a
    frozen Fire Rescue task contract and therefore does not invoke the
    semantic decomposer or retain feedback across rounds.  Callers using the
    stateful :class:`TeamWeaverPipeline` must pass
    ``execution_scope="full_pipeline"`` and the actual model/API value.
    """
    allocation_only = execution_scope == "allocation_only_fire_rescue_hook"
    planner = (
        f"caller_supplied_fire_rescue_contract_plus_dynamic_MIQP[{solver}]"
        if allocation_only
        else f"DeepSeek_semantic_decomposition_plus_dynamic_MIQP[{solver}]"
    )
    return AlgorithmCondition(
        algorithm_id=ALGORITHM_ID,
        source_reference=SOURCE_REFERENCE,
        implementation="adapted",
        execution_scope=execution_scope,
        planner=planner,
        model_or_api=model_or_api,
        checkpoint="not_applicable_no_learned_weights",
        prompt_revision="TeamWeaver.semantic_decomposer.v1",
        supported_primitives=("P1", "P2", "P4"),
        unsupported_dimensions=(
            "P3_coupled_joint_action_without_coalition_executor",
            "D2_paired_communication_retention",
            "D4_member_exit_recovery_without_registered_branch",
            "D5_certified_cross_domain_transfer",
        ),
        physical_executor_owner="eai_fire_rescue_runtime",
        serial_action_loop=False,
        notes=(
            "The EAI adapter preserves task DAG, phase scheduling, capability tracking, "
            "dynamic allocation, and feedback/replan. The stateless Fire Rescue hook "
            "covers only the allocation phase and records that limitation."
        ),
    )


def condition_manifest(**kwargs: object) -> dict[str, object]:
    return condition_record(**kwargs).to_dict()


def _capabilities(request: object, robot_id: str) -> dict[str, float]:
    scenario = request.scenario  # type: ignore[attr-defined]
    profile = scenario.robot_profiles.get(robot_id)
    text = f"{robot_id} {getattr(profile, 'robot_type', '')} {' '.join(getattr(profile, 'capabilities', ()))}".lower()
    values = {"navigation": 1.0}
    if any(token in text for token in ("m20", "quadruped", "四足", "payload", "extinguisher")):
        values.update({"payload": 1.0, "manipulation": 0.8, "extinguisher_handling": 0.9, "agility": 0.9})
    if any(token in text for token in ("scout", "ur5", "arm", "机械臂", "button")):
        values.update({"manipulation": 1.0, "button_press": 1.0, "sensing": 0.8, "payload": 0.7})
    if any(token in text for token in ("carter", "sensor", "传感", "camera", "雷达")):
        values.update({"sensing": 1.0, "relay": 1.0})
    return values


def _task_type(subtask_id: str) -> TaskType:
    return {
        "red": TaskType.INSPECT,
        "green": TaskType.PRESS_RESCUE_BUTTON,
        "blue": TaskType.ESTABLISH_RELAY,
        "yellow": TaskType.PICK_EXTINGUISHER,
    }.get(subtask_id, TaskType.NAVIGATE)


def _requirements(task_type: TaskType) -> dict[str, CapabilityRequirement]:
    common = {"navigation": CapabilityRequirement(0.6, 1.0, True)}
    if task_type is TaskType.INSPECT:
        common["sensing"] = CapabilityRequirement(0.5, 0.8, False)
    elif task_type is TaskType.PRESS_RESCUE_BUTTON:
        common.update({"manipulation": CapabilityRequirement(0.7, 1.0, True), "button_press": CapabilityRequirement(0.7, 1.0, True)})
    elif task_type is TaskType.ESTABLISH_RELAY:
        common["relay"] = CapabilityRequirement(0.6, 1.0, True)
    elif task_type is TaskType.PICK_EXTINGUISHER:
        common.update({"manipulation": CapabilityRequirement(0.7, 1.0, True), "payload": CapabilityRequirement(0.7, 1.0, True), "extinguisher_handling": CapabilityRequirement(0.7, 1.0, True)})
    return common


def build_fire_rescue_world(request: object) -> tuple[SymbolicWorldState, tuple[SemanticTask, ...]]:
    """Translate the EAI assignment request into TeamWeaver's symbolic model."""

    positions = request.robot_positions  # type: ignore[attr-defined]
    subtask_positions = request.subtask_positions  # type: ignore[attr-defined]
    scenario = request.scenario  # type: ignore[attr-defined]
    excluded = set(getattr(request, "excluded_subtask_ids", ()))
    open_subtasks = tuple(
        subtask_id for subtask_id in scenario.subtasks if subtask_id not in excluded
    )
    if not open_subtasks:
        raise ValueError("TeamWeaver Fire Rescue policy has no open subtasks")
    completed_robots = set(getattr(request, "completed_extinguisher", ())) | set(
        getattr(request, "completed_rescue", ())
    )
    robots = []
    for robot_id, position in sorted(positions.items()):
        if len(position) != 3 or not all(math.isfinite(float(value)) for value in position):
            raise ValueError(f"invalid robot pose for {robot_id!r}")
        robots.append(
            RobotSnapshot(
                name=robot_id,
                position=(float(position[0]), float(position[1])),
                base_capabilities=_capabilities(request, robot_id),
                reliability={name: 1.0 for name in _capabilities(request, robot_id)},
                equipment=frozenset({"manipulator", "gripper"}) if "m20" in robot_id.lower() or "scout" in robot_id.lower() else frozenset(),
                busy=robot_id in completed_robots,
                current_task=None,
                current_load=0,
                safe=True,
            )
        )
    targets = []
    for subtask_id in open_subtasks:
        xy = subtask_positions.get(subtask_id)
        if xy is None:
            raise ValueError(f"TeamWeaver requires a target for {subtask_id!r}")
        task_type = _task_type(subtask_id)
        targets.append(TargetSnapshot(subtask_id, (float(xy[0]), float(xy[1]), 0.0), True, "physical", (task_type,)))
    world = SymbolicWorldState(
        hazard_id=int(request.hazard_id),
        hazard_position=tuple(float(value) for value in request.hazard_pos),
        hazard_active=True,
        targets=tuple(targets),
        robots=tuple(robots),
        extinguisher_available=True,
        extinguisher_carrier=None,
        extinguisher_delivered=False,
        rescue_channel_open=False,
        completed_task_ids=(),
        failed_task_ids=(),
        recent_feedback=(),
        observations={"adapter": ALGORITHM_ID},
    )
    task_rows = []
    for subtask_id in open_subtasks:
        task_type = _task_type(subtask_id)
        prerequisites = ("red",) if subtask_id == "green" and "red" in scenario.subtasks else ()
        task_rows.append(
            SemanticTask(
                task_id=subtask_id,
                task_type=task_type,
                description=scenario.subtasks[subtask_id].description or scenario.subtasks[subtask_id].name,
                target_ref=subtask_id,
                priority=1,
                requirements=_requirements(task_type),
                prerequisites=prerequisites,
                can_parallel=not prerequisites,
                estimated_duration_s=60.0,
                preferred_agent=scenario.preferred_fallback.get(subtask_id),
            )
        )
    return world, tuple(task_rows)


def assign(request: object) -> Mapping[str, str]:
    """Run TeamWeaver's real allocation stage on the EAI Fire Rescue contract.

    The CLI callback cannot retain a live pipeline between triggers.  It
    therefore consumes the supplied scenario as a frozen decomposition and
    records this scope in :func:`condition_manifest`; callers needing the full
    LLM+DAG+feedback loop should use ``TeamWeaverPipeline`` directly.
    """

    world, tasks = build_fire_rescue_world(request)
    allocator = DynamicMIQPAllocator(prefer_miqp=True)
    allocation = allocator.allocate(world, tasks)
    if allocation.hard_infeasible_task_ids:
        raise ValueError(f"TeamWeaver allocation infeasible: {allocation.hard_infeasible_task_ids}")
    result: dict[str, str] = {}
    for item in allocation.assignments:
        if item.task_id == "green":
            owner = getattr(request.scenario, "green_robot_name", "")  # type: ignore[attr-defined]
            if owner and item.robot_name != owner:
                raise ValueError(f"green requires {owner}, got {item.robot_name}")
        if item.task_id == "yellow":
            prefix = getattr(request.scenario, "yellow_robot_prefix", "")  # type: ignore[attr-defined]
            if prefix and not item.robot_name.startswith(prefix):
                raise ValueError(f"yellow requires {prefix} robot, got {item.robot_name}")
        result[item.robot_name] = item.task_id
    if set(result.values()) != {task.task_id for task in tasks}:
        raise ValueError("TeamWeaver assignment did not cover every open task")
    return result


def record_plan(ledger: EventLedger, assignments: Mapping[str, str], *, solver: str) -> tuple[Assignment, ...]:
    records = tuple(Assignment(task_id=task_id, robot_id=robot_id, source=ALGORITHM_ID) for robot_id, task_id in sorted(assignments.items()))
    ledger.append("proposal", algorithm_id=ALGORITHM_ID, solver=solver, assignments=[{"robot_id": item.robot_id, "task_id": item.task_id} for item in records])
    return records


__all__ = ["ALGORITHM_ID", "SOURCE_REFERENCE", "assign", "build_fire_rescue_world", "condition_manifest", "condition_record", "record_plan"]
