from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Callable, Mapping, Sequence

from TeamWeaver.eai_adapter.capability_ontology import required_equipment
from TeamWeaver.eai_adapter.capability_tracker import CapabilityTracker
from TeamWeaver.eai_adapter.phase_scheduler import PhaseScheduler, TaskStateError
from TeamWeaver.eai_adapter.task_models import (
    ExecutionFeedback,
    FailureKind,
    SemanticTask,
    SymbolicWorldState,
    TaskStatus,
    TaskType,
)


_ACTIVE_STATUSES = frozenset(
    {TaskStatus.ASSIGNED, TaskStatus.NAVIGATING, TaskStatus.OPERATING}
)
_RETRYABLE_STATUSES = frozenset({TaskStatus.FAILED, TaskStatus.TIMED_OUT})
_LOCAL_PROGRESS_REASONS = frozenset(
    {"phase_complete", "world_state_change", "deferred_task_ready"}
)


@dataclass(frozen=True)
class TeamWeaverPlan:
    decomposition_source: str
    dynamic_task_count: int
    dag_valid: bool
    phase_index: int
    phase_total: int
    tasks: tuple[SemanticTask, ...]
    allocation: AllocationResult
    completed_task_ids: tuple[str, ...]
    failed_task_ids: tuple[str, ...]
    replan_reason: str


class TeamWeaverPipeline:
    def __init__(
        self,
        decomposer: Any,
        allocator: Any,
        *,
        post_decomposition: (
            Callable[[Sequence[SemanticTask], Any], tuple[SemanticTask, ...]]
            | None
        ) = None,
    ) -> None:
        self._decomposer = decomposer
        self._allocator = allocator
        self._post_decomposition = post_decomposition
        self._scheduler: PhaseScheduler | None = None
        self._capabilities: CapabilityTracker | None = None
        self._instruction: str | None = None
        self._decomposition_source: str | None = None
        self._external_completed_task_ids: tuple[str, ...] = ()
        self._previous_assignments: dict[str, str] = {}
        self._facts: list[Mapping[str, object]] = []
        self._phase_total = 0

    @property
    def scheduler(self) -> PhaseScheduler | None:
        return self._scheduler

    @property
    def capabilities(self) -> CapabilityTracker | None:
        return self._capabilities

    def plan_initial(
        self,
        instruction: str,
        world: SymbolicWorldState,
    ) -> TeamWeaverPlan:
        self._scheduler = None
        self._capabilities = CapabilityTracker.from_world(world)
        self._instruction = str(instruction)
        self._decomposition_source = None
        self._external_completed_task_ids = planning_completed = tuple(
            world.completed_task_ids
        )
        self._previous_assignments.clear()
        self._facts.clear()
        self._phase_total = 0

        planning_world = self._capabilities.overlay(world)
        decomposition = self._decomposer.decompose(
            self._instruction,
            planning_world,
            max_attempts=3,
            retry_delays=(),
        )
        tasks, source = self._validated_decomposition(
            decomposition,
            completed_task_ids=planning_completed,
        )
        tasks = self._apply_post_decomposition(tasks, planning_world)

        scheduler = PhaseScheduler(
            tasks,
            completed_task_ids=planning_world.completed_task_ids,
        )
        self._scheduler = scheduler
        self._decomposition_source = source
        allocation = self._allocate_ready_phase(planning_world)
        return self._build_plan(allocation, replan_reason="")

    def replan(
        self,
        world: SymbolicWorldState,
        reason: str,
    ) -> TeamWeaverPlan:
        scheduler = self._require_scheduler()
        capabilities = self._require_capabilities()
        if self._instruction is None:
            raise RuntimeError("initial instruction is unavailable")

        planning_world = capabilities.overlay(world)
        planning_world = self._world_with_pipeline_facts(planning_world)
        self._external_completed_task_ids = tuple(
            planning_world.completed_task_ids
        )
        progress_plan = None
        if not self._obstacle_requires_semantic_replan(
            planning_world,
            reason=str(reason),
        ):
            progress_plan = self._advance_existing_plan(
                planning_world,
                reason=str(reason),
            )
        if progress_plan is not None:
            return progress_plan
        decomposition = self._decomposer.decompose(
            self._instruction,
            planning_world,
            max_attempts=4,
            retry_delays=(1.0, 3.0, 5.0),
        )
        tasks, source = self._validated_decomposition(
            decomposition,
            completed_task_ids=self._external_completed_task_ids,
        )
        tasks = self._rekey_reused_permanently_failed_tasks(tasks)
        tasks = self._retain_locked_system_continuations(tasks)
        tasks = self._apply_post_decomposition(tasks, planning_world)

        scheduler.reconcile(
            _without_satisfied_external_prerequisites(
                tasks,
                completed_task_ids=self._external_completed_task_ids,
                retained_task_ids=scheduler.task_ids,
                locked_task_ids=tuple(
                    task_id
                    for task_id in scheduler.task_ids
                    if scheduler.status(task_id) in _ACTIVE_STATUSES
                    or scheduler.status(task_id)
                    in {
                        TaskStatus.SUCCEEDED,
                        TaskStatus.PENDING,
                        TaskStatus.READY,
                    }
                ),
            )
        )
        for task_id in scheduler.task_ids:
            if scheduler.status(task_id) in _RETRYABLE_STATUSES:
                scheduler.retry_failed(task_id)

        self._decomposition_source = source
        allocation_world = capabilities.overlay(world)
        allocation = self._allocate_ready_phase(allocation_world)
        return self._build_plan(allocation, replan_reason=str(reason))

    def mark_navigating(self, task_id: str) -> None:
        self._require_scheduler().mark_navigating(task_id)

    def mark_operating(self, task_id: str) -> None:
        self._require_scheduler().mark_operating(task_id)

    def accept_feedback(self, feedback: ExecutionFeedback) -> None:
        scheduler = self._require_scheduler()
        capabilities = self._require_capabilities()
        if feedback.failure_kind is FailureKind.RELAY_LOST:
            assigned_robot = scheduler.assigned_robot(feedback.task_id)
            if assigned_robot != feedback.robot_name:
                raise TaskStateError(
                    f"feedback robot {feedback.robot_name} does not match assigned "
                    f"robot {assigned_robot}"
                )
            scheduler.fail_relay_lease(feedback.task_id)
        else:
            scheduler.apply_feedback(feedback)
        capabilities.apply(feedback)
        self._facts.append(feedback.to_payload())

    def _apply_post_decomposition(
        self,
        tasks: Sequence[SemanticTask],
        world: Any,
    ) -> tuple[SemanticTask, ...]:
        """Apply the scenario's post-decomposition hook, or the default."""
        if self._post_decomposition is not None:
            return self._post_decomposition(tasks, world)
        # Fallback: import the legacy fire-return logic (backward compat)
        from TeamWeaver.eai_adapter.mission_graph import (  # noqa: E402
            append_fire_return_tasks,
        )
        return append_fire_return_tasks(tasks, world)

    def mission_status(self) -> str:
        if self._scheduler is None:
            return "uninitialized"
        return self._scheduler.mission_status()

    def cancel(self) -> None:
        cancel = getattr(self._decomposer, "cancel", None)
        if cancel is not None:
            cancel()

    @staticmethod
    def _validated_decomposition(
        decomposition: Any,
        *,
        completed_task_ids: Sequence[str] = (),
    ) -> tuple[tuple[SemanticTask, ...], str]:
        source = str(decomposition.source)
        if source != "deepseek":
            raise ValueError(
                f"TeamWeaver requires DeepSeek decomposition, got {source!r}"
            )
        tasks = tuple(decomposition.tasks)
        if any(not isinstance(task, SemanticTask) for task in tasks):
            raise TypeError("decomposition tasks must be SemanticTask instances")
        _task_depths(tasks, completed_task_ids=completed_task_ids)
        return tasks, source

    def _world_with_pipeline_facts(
        self,
        world: SymbolicWorldState,
    ) -> SymbolicWorldState:
        completed = _ordered_union(
            world.completed_task_ids,
            self._task_ids_with_status(TaskStatus.SUCCEEDED),
        )
        failed = _ordered_union(
            world.failed_task_ids,
            self._task_ids_with_status(TaskStatus.FAILED, TaskStatus.TIMED_OUT),
        )
        return replace(
            world,
            completed_task_ids=completed,
            failed_task_ids=failed,
            recent_feedback=tuple(world.recent_feedback) + tuple(self._facts),
        )

    def _allocate_ready_phase(
        self,
        world: SymbolicWorldState,
    ) -> AllocationResult:
        scheduler = self._require_scheduler()
        allocation_world = self._world_with_busy_locks(world)
        phase_tasks = tuple(
            self._bind_system_continuation(task)
            for task in scheduler.next_phase()
        )
        allocation = self._allocator.allocate(
            allocation_world,
            phase_tasks,
            previous_assignments=self._previous_assignments,
        )
        preemptions = tuple(
            assignment
            for assignment in allocation.assignments
            if assignment.preempted_task_id is not None
        )
        for assignment in preemptions:
            preempted_task_id = assignment.preempted_task_id
            assert preempted_task_id is not None
            if scheduler.assigned_robot(preempted_task_id) != assignment.robot_name:
                raise TaskStateError(
                    f"preemption robot {assignment.robot_name} does not own task "
                    f"{preempted_task_id}"
                )
            if not scheduler.can_suspend(preempted_task_id):
                raise TaskStateError(
                    f"preempted task {preempted_task_id} is not suspendable"
                )
        for assignment in preemptions:
            assert assignment.preempted_task_id is not None
            scheduler.suspend(assignment.preempted_task_id)
        for assignment in allocation.assignments:
            scheduler.mark_assigned(assignment.task_id, assignment.robot_name)
            self._previous_assignments[assignment.task_id] = assignment.robot_name
        for task_id in allocation.deferred_task_ids:
            scheduler.defer(task_id)
        self._record_optimizer_facts(allocation)
        return allocation

    def _advance_existing_plan(
        self,
        world: SymbolicWorldState,
        *,
        reason: str,
    ) -> TeamWeaverPlan | None:
        scheduler = self._require_scheduler()
        has_semantic_failure = any(
            scheduler.task(task_id).continuation_of is None
            and scheduler.status(task_id) in _RETRYABLE_STATUSES
            for task_id in scheduler.task_ids
        )
        if has_semantic_failure:
            return None
        failed_system_tasks = tuple(
            task_id
            for task_id in scheduler.task_ids
            if scheduler.task(task_id).continuation_of is not None
            and scheduler.status(task_id) in _RETRYABLE_STATUSES
        )
        for task_id in failed_system_tasks:
            scheduler.retry_failed(task_id)

        ready = scheduler.next_phase()
        has_ready_system_task = any(
            task.continuation_of is not None for task in ready
        )
        if (
            not failed_system_tasks
            and not has_ready_system_task
            and reason not in _LOCAL_PROGRESS_REASONS
        ):
            return None

        allocation = self._allocate_ready_phase(world)
        return self._build_plan(allocation, replan_reason=reason)

    def _obstacle_requires_semantic_replan(
        self,
        world: SymbolicWorldState,
        *,
        reason: str,
    ) -> bool:
        if reason != "world_state_change":
            return False
        obstacle = world.active_obstacle()
        if obstacle is None or obstacle.blocking_task_id is None:
            return False
        scheduler = self._require_scheduler()
        return not any(
            scheduler.task(task_id).task_type is TaskType.REMOVE_OBSTACLE
            and scheduler.task(task_id).target_ref == obstacle.obstacle_id
            for task_id in scheduler.task_ids
        )

    def _retain_locked_system_continuations(
        self,
        tasks: Sequence[SemanticTask],
    ) -> tuple[SemanticTask, ...]:
        scheduler = self._require_scheduler()
        result = list(tasks)
        result_index = {
            task.task_id: index for index, task in enumerate(result)
        }
        retained_predecessor_states = _ACTIVE_STATUSES | {
            TaskStatus.SUCCEEDED,
            TaskStatus.PENDING,
            TaskStatus.READY,
        }
        for task_id in scheduler.task_ids:
            task = scheduler.task(task_id)
            predecessor = task.continuation_of
            if predecessor is None:
                continue
            if predecessor not in scheduler.task_ids:
                continue
            if scheduler.status(predecessor) not in retained_predecessor_states:
                continue
            existing_index = result_index.get(task_id)
            if existing_index is None:
                result_index[task_id] = len(result)
                result.append(task)
            else:
                result[existing_index] = task
        return tuple(result)

    def _rekey_reused_permanently_failed_tasks(
        self,
        tasks: Sequence[SemanticTask],
    ) -> tuple[SemanticTask, ...]:
        scheduler = self._require_scheduler()
        failed_ids = scheduler.permanently_failed_task_ids
        if not failed_ids:
            return tuple(tasks)

        used_ids = set(scheduler.task_ids) | {task.task_id for task in tasks}
        replacements: dict[str, str] = {}
        for task in tasks:
            if task.task_id not in failed_ids or task.task_id in replacements:
                continue
            index = 1
            while f"{task.task_id}_retry_{index}" in used_ids:
                index += 1
            replacement_id = f"{task.task_id}_retry_{index}"
            replacements[task.task_id] = replacement_id
            used_ids.add(replacement_id)

        if not replacements:
            return tuple(tasks)
        return tuple(
            replace(
                task,
                task_id=replacements.get(task.task_id, task.task_id),
                prerequisites=tuple(
                    replacements.get(item, item) for item in task.prerequisites
                ),
                continuation_of=(
                    replacements.get(task.continuation_of, task.continuation_of)
                    if task.continuation_of is not None
                    else None
                ),
            )
            for task in tasks
        )

    def _bind_system_continuation(self, task: SemanticTask) -> SemanticTask:
        predecessor = task.continuation_of
        if predecessor is None:
            return task
        scheduler = self._require_scheduler()
        robot_name = scheduler.assigned_robot(predecessor)
        if robot_name is None:
            raise TaskStateError(
                f"system continuation {task.task_id} has no predecessor robot"
            )
        return replace(task, required_agent=robot_name)

    def _world_with_busy_locks(
        self,
        world: SymbolicWorldState,
    ) -> SymbolicWorldState:
        scheduler = self._require_scheduler()
        locked_tasks: dict[str, str] = {}
        for task_id in scheduler.task_ids:
            if scheduler.status(task_id) not in _ACTIVE_STATUSES:
                continue
            robot_name = scheduler.assigned_robot(task_id)
            if robot_name is not None:
                locked_tasks[robot_name] = task_id

        leased_robots = scheduler.leased_robot_names
        for task_id in scheduler.task_ids:
            robot_name = scheduler.assigned_robot(task_id)
            if robot_name in leased_robots:
                locked_tasks.setdefault(robot_name, task_id)

        robots = tuple(
            replace(
                robot,
                busy=True,
                current_task=locked_tasks.get(robot.name) or robot.current_task,
            )
            if robot.name in locked_tasks
            else robot
            for robot in world.robots
        )
        return replace(world, robots=robots)

    def _record_optimizer_facts(self, allocation: AllocationResult) -> None:
        self._facts.append(
            {
                "fact_type": "optimizer_allocation",
                "solver": allocation.solver,
                "assignments": [
                    {
                        "task_id": item.task_id,
                        "robot_name": item.robot_name,
                        "preempted_task_id": item.preempted_task_id,
                    }
                    for item in allocation.assignments
                ],
                "deferred_task_ids": list(allocation.deferred_task_ids),
                "hard_infeasible_task_ids": list(
                    allocation.hard_infeasible_task_ids
                ),
                "total_cost": float(allocation.total_cost),
                "objective_preemption": float(allocation.objective.preemption),
            }
        )
        scheduler = self._require_scheduler()
        for task_id in allocation.hard_infeasible_task_ids:
            task = scheduler.task(task_id)
            self._facts.append(
                {
                    "fact_type": "optimizer_failure",
                    "task_id": task_id,
                    "failure_kind": "hard_infeasible",
                    "reason": (
                        "no currently available safe robot satisfies every hard "
                        "assignment constraint"
                    ),
                    "required_capabilities": sorted(
                        name
                        for name, requirement in task.requirements.items()
                        if requirement.hard
                    ),
                    "required_equipment": sorted(required_equipment(task.task_type)),
                    "solver": allocation.solver,
                }
            )

    def _build_plan(
        self,
        allocation: AllocationResult,
        *,
        replan_reason: str,
    ) -> TeamWeaverPlan:
        scheduler = self._require_scheduler()
        tasks = tuple(
            self._bind_system_continuation(scheduler.task(task_id))
            if scheduler.task(task_id).continuation_of is not None
            and scheduler.assigned_robot(
                scheduler.task(task_id).continuation_of or ""
            )
            is not None
            else scheduler.task(task_id)
            for task_id in scheduler.task_ids
        )
        depths = _task_depths(
            tasks,
            completed_task_ids=self._external_completed_task_ids,
        )
        completed_task_ids = self._task_ids_with_status(TaskStatus.SUCCEEDED)
        completed_depth = max(
            (depths[task_id] for task_id in completed_task_ids),
            default=0,
        )
        phase_index = completed_depth + 1
        self._phase_total = max(
            self._phase_total,
            max(depths.values(), default=0),
            phase_index,
        )
        return TeamWeaverPlan(
            decomposition_source=self._decomposition_source or "deepseek",
            dynamic_task_count=len(tasks),
            dag_valid=True,
            phase_index=phase_index,
            phase_total=self._phase_total,
            tasks=tasks,
            allocation=allocation,
            completed_task_ids=completed_task_ids,
            failed_task_ids=self._task_ids_with_status(
                TaskStatus.FAILED,
                TaskStatus.TIMED_OUT,
            ),
            replan_reason=replan_reason,
        )

    def _task_ids_with_status(
        self,
        *statuses: TaskStatus,
    ) -> tuple[str, ...]:
        scheduler = self._require_scheduler()
        selected = frozenset(statuses)
        return tuple(
            task_id
            for task_id in scheduler.task_ids
            if scheduler.status(task_id) in selected
        )

    def _require_scheduler(self) -> PhaseScheduler:
        if self._scheduler is None:
            raise RuntimeError("TeamWeaver pipeline has not been initialized")
        return self._scheduler

    def _require_capabilities(self) -> CapabilityTracker:
        if self._capabilities is None:
            raise RuntimeError("TeamWeaver capability tracker is not initialized")
        return self._capabilities


def _task_depths(
    tasks: Sequence[SemanticTask],
    *,
    completed_task_ids: Sequence[str] = (),
) -> dict[str, int]:
    task_by_id = {task.task_id: task for task in tasks}
    if len(task_by_id) != len(tasks):
        raise ValueError("task ids must be unique")
    task_ids = set(task_by_id)
    external_completed = {str(task_id) for task_id in completed_task_ids} - task_ids
    for task in tasks:
        unknown = set(task.prerequisites) - task_ids - external_completed
        if unknown:
            raise ValueError(
                f"task {task.task_id} has unknown prerequisites: {sorted(unknown)}"
            )

    depths: dict[str, int] = {}
    visiting: set[str] = set()

    def visit(task_id: str) -> int:
        if task_id in external_completed:
            return 0
        if task_id in depths:
            return depths[task_id]
        if task_id in visiting:
            raise ValueError("task prerequisites must form an acyclic graph")
        visiting.add(task_id)
        prerequisites = task_by_id[task_id].prerequisites
        depth = 1 + max((visit(item) for item in prerequisites), default=0)
        visiting.remove(task_id)
        depths[task_id] = depth
        return depth

    for task_id in task_by_id:
        visit(task_id)
    return depths


def _without_satisfied_external_prerequisites(
    tasks: Sequence[SemanticTask],
    *,
    completed_task_ids: Sequence[str],
    retained_task_ids: Sequence[str],
    locked_task_ids: Sequence[str],
) -> tuple[SemanticTask, ...]:
    external_completed = set(completed_task_ids) - set(retained_task_ids)
    locked = set(locked_task_ids)
    return tuple(
        replace(
            task,
            prerequisites=tuple(
                item
                for item in task.prerequisites
                if item not in external_completed
            ),
        )
        if task.task_id not in locked
        and any(item in external_completed for item in task.prerequisites)
        else task
        for task in tasks
    )


def _ordered_union(*groups: Sequence[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            normalized = str(item)
            if normalized not in seen:
                seen.add(normalized)
                result.append(normalized)
    return tuple(result)
