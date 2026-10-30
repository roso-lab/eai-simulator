from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from TeamWeaver.eai_adapter.task_models import (
    ExecutionFeedback,
    FailureKind,
    FeedbackOutcome,
    SemanticTask,
    TaskStatus,
    TaskType,
)


_ACTIVE_STATES = frozenset(
    {TaskStatus.ASSIGNED, TaskStatus.NAVIGATING, TaskStatus.OPERATING}
)
_ATTEMPT_FAILURE_STATES = frozenset({TaskStatus.FAILED, TaskStatus.TIMED_OUT})
_RELAY_RELEASE_STATES = frozenset(
    {
        TaskStatus.SUCCEEDED,
        TaskStatus.CANCELLED,
    }
)
_PERMANENT_FAILURE_KINDS = frozenset(
    {
        FailureKind.CONSTRAINT_VIOLATION,
        FailureKind.WORLD_STATE_CONFLICT,
        FailureKind.RELAY_LOST,
    }
)
_PREEMPTIBLE_TASK_TYPES = frozenset({TaskType.NAVIGATE, TaskType.INSPECT})


class TaskStateError(RuntimeError):
    pass


@dataclass
class _TaskRecord:
    task: SemanticTask
    status: TaskStatus = TaskStatus.PENDING
    assigned_robot: str | None = None
    attempts: int = 0
    last_failure_kind: FailureKind = FailureKind.NONE
    permanent_failure: bool = False


class PhaseScheduler:
    def __init__(
        self,
        tasks: Sequence[SemanticTask],
        *,
        completed_task_ids: Iterable[str] = (),
    ) -> None:
        task_list = tuple(tasks)
        if len({task.task_id for task in task_list}) != len(task_list):
            raise ValueError("task ids must be unique")
        self._external_completed = set(str(item) for item in completed_task_ids)
        self._records = {
            task.task_id: _TaskRecord(
                task=task,
                status=(
                    TaskStatus.SUCCEEDED
                    if task.task_id in self._external_completed
                    else TaskStatus.PENDING
                ),
            )
            for task in task_list
        }
        self._relay_leases: dict[str, str] = {}
        self._refresh_ready()

    @property
    def task_ids(self) -> tuple[str, ...]:
        return tuple(self._records)

    @property
    def leased_robot_names(self) -> frozenset[str]:
        self._release_finished_relay_leases()
        return frozenset(self._relay_leases.values())

    @property
    def permanently_failed_task_ids(self) -> frozenset[str]:
        return frozenset(
            task_id
            for task_id, record in self._records.items()
            if record.permanent_failure
        )

    def task(self, task_id: str) -> SemanticTask:
        return self._record(task_id).task

    def status(self, task_id: str) -> TaskStatus:
        return self._record(task_id).status

    def assigned_robot(self, task_id: str) -> str | None:
        return self._record(task_id).assigned_robot

    def attempts(self, task_id: str) -> int:
        return self._record(task_id).attempts

    def can_suspend(self, task_id: str) -> bool:
        record = self._record(task_id)
        return (
            record.status in {TaskStatus.ASSIGNED, TaskStatus.NAVIGATING}
            and record.task.task_type in _PREEMPTIBLE_TASK_TYPES
            and record.task.continuation_of is None
        )

    def suspend(self, task_id: str) -> None:
        record = self._record(task_id)
        if not self.can_suspend(task_id):
            raise TaskStateError(f"task {task_id} is not preemptible")
        record.status = TaskStatus.PENDING
        record.assigned_robot = None
        record.last_failure_kind = FailureKind.NONE
        record.attempts = max(0, record.attempts - 1)
        self._refresh_ready()

    def ready_tasks(self) -> tuple[SemanticTask, ...]:
        self._refresh_ready()
        return tuple(
            record.task
            for record in sorted(
                self._records.values(),
                key=lambda item: (item.task.priority, item.task.task_id),
            )
            if record.status is TaskStatus.READY
        )

    def next_phase(self) -> tuple[SemanticTask, ...]:
        ready = self.ready_tasks()
        serial = tuple(task for task in ready if not task.can_parallel)
        if serial:
            serial_task = serial[0]
            continuations = tuple(
                task
                for task in ready
                if task.continuation_of is not None
                and task.task_id != serial_task.task_id
            )
            return (serial_task,) + continuations
        return ready

    def mark_assigned(self, task_id: str, robot_name: str) -> None:
        record = self._require_status(task_id, TaskStatus.READY)
        normalized_robot = str(robot_name).strip()
        if not normalized_robot:
            raise ValueError("robot_name must be non-empty")
        record.status = TaskStatus.ASSIGNED
        record.assigned_robot = normalized_robot
        record.attempts += 1
        record.last_failure_kind = FailureKind.NONE

    def mark_navigating(self, task_id: str) -> None:
        self._require_status(task_id, TaskStatus.ASSIGNED).status = TaskStatus.NAVIGATING

    def mark_operating(self, task_id: str) -> None:
        self._require_status(task_id, TaskStatus.NAVIGATING).status = TaskStatus.OPERATING

    def defer(self, task_id: str) -> None:
        self._require_status(task_id, TaskStatus.READY)

    def apply_feedback(self, feedback: ExecutionFeedback) -> None:
        record = self._record(feedback.task_id)
        if record.status not in _ACTIVE_STATES:
            raise TaskStateError(
                f"task {feedback.task_id} cannot accept feedback from {record.status.value}"
            )
        if record.assigned_robot != feedback.robot_name:
            raise TaskStateError(
                f"feedback robot {feedback.robot_name} does not match assigned robot "
                f"{record.assigned_robot}"
            )

        if feedback.outcome is FeedbackOutcome.SUCCEEDED:
            record.status = TaskStatus.SUCCEEDED
            record.last_failure_kind = FailureKind.NONE
            record.permanent_failure = False
            if record.task.task_type is TaskType.ESTABLISH_RELAY:
                assert record.assigned_robot is not None
                self._relay_leases[record.task.task_id] = record.assigned_robot
        elif feedback.outcome is FeedbackOutcome.TIMED_OUT:
            record.status = TaskStatus.TIMED_OUT
            record.last_failure_kind = FailureKind.TIMEOUT
            record.permanent_failure = record.attempts >= 2
        elif feedback.outcome is FeedbackOutcome.CANCELLED:
            record.status = TaskStatus.CANCELLED
            record.assigned_robot = None
            record.last_failure_kind = feedback.failure_kind
            record.permanent_failure = False
            if feedback.failure_kind is FailureKind.WORLD_STATE_CONFLICT:
                record.attempts = max(0, record.attempts - 1)
        else:
            record.status = TaskStatus.FAILED
            record.last_failure_kind = feedback.failure_kind
            record.permanent_failure = (
                feedback.failure_kind in _PERMANENT_FAILURE_KINDS
                or record.attempts >= 2
            )
        self._refresh_ready()
        self._release_finished_relay_leases()

    def retry_failed(self, task_id: str) -> bool:
        record = self._record(task_id)
        if record.status not in _ATTEMPT_FAILURE_STATES:
            raise TaskStateError(
                f"task {task_id} is {record.status.value}, not failed or timed out"
            )
        if record.permanent_failure or record.attempts >= 2:
            return False
        record.assigned_robot = None
        record.last_failure_kind = FailureKind.NONE
        record.status = TaskStatus.PENDING
        self._refresh_ready()
        return record.status is TaskStatus.READY

    def cancel(self, task_id: str) -> None:
        record = self._record(task_id)
        if record.status not in _ACTIVE_STATES | {TaskStatus.READY, TaskStatus.PENDING}:
            raise TaskStateError(f"task {task_id} cannot be cancelled from {record.status.value}")
        record.status = TaskStatus.CANCELLED
        record.assigned_robot = None
        self._relay_leases.pop(task_id, None)
        self._refresh_ready()

    def fail_relay_lease(self, task_id: str) -> None:
        if task_id not in self._relay_leases:
            raise TaskStateError(f"task {task_id} has no active relay lease")
        record = self._record(task_id)
        record.status = TaskStatus.FAILED
        record.last_failure_kind = FailureKind.RELAY_LOST
        record.permanent_failure = True
        self._relay_leases.pop(task_id, None)
        self._refresh_ready()

    def force_succeeded(self, task_id: str) -> None:
        record = self._record(task_id)
        if record.permanent_failure:
            raise TaskStateError(
                f"task {task_id} is permanently failed and cannot be forced succeeded"
            )
        if record.status in _ACTIVE_STATES | {
            TaskStatus.READY,
            TaskStatus.PENDING,
            TaskStatus.FAILED,
            TaskStatus.TIMED_OUT,
        }:
            record.status = TaskStatus.SUCCEEDED
            record.permanent_failure = False
            record.last_failure_kind = FailureKind.NONE
            self._refresh_ready()
            self._release_finished_relay_leases()
            return
        if record.status is not TaskStatus.SUCCEEDED:
            raise TaskStateError(
                f"task {task_id} cannot be forced succeeded from {record.status.value}"
            )

    def reconcile(self, tasks: Sequence[SemanticTask]) -> None:
        revised = {task.task_id: task for task in tasks}
        if len(revised) != len(tuple(tasks)):
            raise ValueError("revised task ids must be unique")

        for task_id, record in tuple(self._records.items()):
            replacement = revised.pop(task_id, None)
            if record.status in _ACTIVE_STATES | {TaskStatus.SUCCEEDED}:
                if (
                    replacement is not None
                    and replacement.prerequisites != record.task.prerequisites
                ):
                    raise TaskStateError(
                        f"revised prerequisites conflict with locked task {task_id}"
                    )
                continue
            if replacement is None:
                del self._records[task_id]
                self._relay_leases.pop(task_id, None)
                continue
            if record.permanent_failure:
                raise TaskStateError(
                    f"permanently failed task {task_id} must be replaced with a new task_id"
                )
            if (
                record.last_failure_kind is FailureKind.WORLD_STATE_CONFLICT
                and (
                    replacement.task_type != record.task.task_type
                    or replacement.target_ref != record.task.target_ref
                )
            ):
                raise TaskStateError(
                    f"revised blocked task {task_id} must preserve task_type and target_ref"
                )
            record.task = replacement
            if record.status is TaskStatus.CANCELLED:
                record.status = TaskStatus.PENDING

        for task_id, task in revised.items():
            self._records[task_id] = _TaskRecord(task=task)
        self._refresh_ready()
        self._release_finished_relay_leases()

    def mission_status(self) -> str:
        self._refresh_ready()
        records = tuple(self._records.values())
        if records and all(record.status is TaskStatus.SUCCEEDED for record in records):
            return "succeeded"
        if any(record.permanent_failure for record in records):
            return "failed"
        if any(
            record.status in _ACTIVE_STATES | {TaskStatus.READY}
            for record in records
        ):
            return "running"
        return "blocked"

    def _record(self, task_id: str) -> _TaskRecord:
        try:
            return self._records[task_id]
        except KeyError as exc:
            raise KeyError(f"unknown task: {task_id}") from exc

    def _require_status(self, task_id: str, status: TaskStatus) -> _TaskRecord:
        record = self._record(task_id)
        if record.status is not status:
            raise TaskStateError(
                f"task {task_id} must be {status.value}, got {record.status.value}"
            )
        return record

    def _refresh_ready(self) -> None:
        succeeded = self._external_completed | {
            task_id
            for task_id, record in self._records.items()
            if record.status is TaskStatus.SUCCEEDED
        }
        permanently_failed = {
            task_id
            for task_id, record in self._records.items()
            if record.permanent_failure
        }
        for record in self._records.values():
            if record.status not in {TaskStatus.PENDING, TaskStatus.READY}:
                continue
            prerequisites = set(record.task.prerequisites)
            if prerequisites & permanently_failed:
                record.status = TaskStatus.PENDING
            elif prerequisites.issubset(succeeded):
                record.status = TaskStatus.READY
            else:
                record.status = TaskStatus.PENDING

    def _release_finished_relay_leases(self) -> None:
        for relay_task_id in tuple(self._relay_leases):
            descendants = self._descendants(relay_task_id)
            if all(
                self._records[task_id].status in _RELAY_RELEASE_STATES
                for task_id in descendants
            ):
                self._relay_leases.pop(relay_task_id, None)

    def _descendants(self, task_id: str) -> set[str]:
        result: set[str] = set()
        frontier = [task_id]
        while frontier:
            current = frontier.pop()
            for child_id, record in self._records.items():
                if current in record.task.prerequisites and child_id not in result:
                    result.add(child_id)
                    frontier.append(child_id)
        return result
