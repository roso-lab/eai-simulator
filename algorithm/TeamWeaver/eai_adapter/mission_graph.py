from __future__ import annotations

import hashlib
import math
from typing import Sequence

from TeamWeaver.eai_adapter.task_models import (
    CapabilityRequirement,
    SemanticTask,
    SymbolicWorldState,
    TaskType,
)


FIRE_RETURN_RADIUS_M = 3.0
SYSTEM_FIRE_RALLY_KIND = "system_fire_rally"
SYSTEM_FIRE_RETURN_PREFIX = "system_fire_return_"
_RETURN_DURATION_S = 180.0


def append_fire_return_tasks(
    tasks: Sequence[SemanticTask],
    world: SymbolicWorldState,
) -> tuple[SemanticTask, ...]:
    task_list = tuple(tasks)
    rally_targets = tuple(
        sorted(
            (
                target
                for target in world.targets
                if target.available
                and target.kind == SYSTEM_FIRE_RALLY_KIND
                and target.position is not None
            ),
            key=lambda target: target.ref,
        )
    )
    if not rally_targets:
        return task_list

    functional_tasks = tuple(
        task for task in task_list if task.continuation_of is None
    )
    functional_prerequisites = {
        prerequisite
        for task in functional_tasks
        for prerequisite in task.prerequisites
    }
    existing_continuations = {
        task.continuation_of
        for task in task_list
        if task.continuation_of is not None
    }
    remote_tasks = tuple(
        sorted(
            (
                task
                for task in functional_tasks
                if task.task_id not in existing_continuations
                and (
                    task.task_id not in functional_prerequisites
                    or task.task_type is TaskType.REMOVE_OBSTACLE
                )
                and (
                    task.task_type is TaskType.DELIVER_EXTINGUISHER
                    or not _ends_within_fire_radius(task, world)
                )
            ),
            key=lambda task: task.task_id,
        )
    )
    if not remote_tasks:
        return task_list

    used_rally_refs = {
        task.target_ref
        for task in task_list
        if task.continuation_of is not None
    }
    unused_rally_targets = tuple(
        target
        for target in rally_targets
        if target.ref not in used_rally_refs
    )
    additions: list[SemanticTask] = []
    for index, leaf in enumerate(remote_tasks):
        if index < len(unused_rally_targets):
            rally_target = unused_rally_targets[index]
        else:
            rally_target = rally_targets[
                (index - len(unused_rally_targets)) % len(rally_targets)
            ]
        additions.append(
            SemanticTask(
                task_id=_return_task_id(leaf.task_id),
                task_type=TaskType.NAVIGATE,
                description=(
                    f"Return the robot that completed {leaf.task_id} "
                    "to the fire rally area"
                ),
                target_ref=rally_target.ref,
                priority=leaf.priority,
                requirements={
                    "navigation": CapabilityRequirement(0.6, 1.0, True),
                },
                prerequisites=(leaf.task_id,),
                can_parallel=True,
                estimated_duration_s=_RETURN_DURATION_S,
                preferred_agent=None,
                continuation_of=leaf.task_id,
            )
        )
    return task_list + tuple(additions)


def _ends_within_fire_radius(
    task: SemanticTask,
    world: SymbolicWorldState,
) -> bool:
    target = world.target_by_ref(task.target_ref)
    if target.position is None:
        return False
    return math.dist(target.position[:2], world.hazard_position[:2]) <= (
        FIRE_RETURN_RADIUS_M
    )


def _return_task_id(predecessor_task_id: str) -> str:
    digest = hashlib.sha1(predecessor_task_id.encode("utf-8")).hexdigest()[:16]
    return f"{SYSTEM_FIRE_RETURN_PREFIX}{digest}"
