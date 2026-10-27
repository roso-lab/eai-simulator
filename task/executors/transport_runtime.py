"""Production task-runner seam for semantic transport commands.

This adapter owns command compilation, authoritative object/robot admission,
and scheduler feedback.  It does not claim physical success or manufacture
evaluator facts; receipt material remains evaluator-owned.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
import hashlib
import json
from typing import Any

from algorithm.common_coordination.contract import Assignment, EventLedger, ExecutionFeedback, FailureCode

from ..contracts import DependencyOutcome, ExecutionUpdate, TaskRunRequest
from ..transport import TransportAdmissionResolver, TransportCommand, compile_transport_command


def _stable_request_id(assignment: Assignment, task: Any) -> str:
    metadata = getattr(task, "metadata", {})
    transport = metadata.get("transport", {}) if isinstance(metadata, Mapping) else {}
    explicit = transport.get("request_id") if isinstance(transport, Mapping) else None
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    raw = json.dumps(
        {"task_id": assignment.task_id, "robot_id": assignment.robot_id, "plan_version": assignment.plan_version},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return "transport:" + hashlib.sha256(raw).hexdigest()[:24]


class TransportOrchestrationExecutor:
    """Adapt semantic transport admission to ``TaskRunner`` orchestration."""

    def __init__(
        self,
        admission: TransportAdmissionResolver,
        *,
        execute_command: Callable[[TransportCommand, Any], bool | ExecutionFeedback] | None = None,
        navigate: Callable[[TransportCommand, Any], bool] | None = None,
        ledger: EventLedger | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not isinstance(admission, TransportAdmissionResolver):
            raise TypeError("admission must be a TransportAdmissionResolver")
        for name, callback in (("execute_command", execute_command), ("navigate", navigate), ("clock", clock)):
            if callback is not None and not callable(callback):
                raise TypeError(f"{name} must be callable")
        self.admission = admission
        self.execute_command = execute_command
        self.navigate = navigate
        self.ledger = ledger or EventLedger(schema_version="task.transport_runtime.v1")
        self.clock = clock or (lambda: 0.0)
        self._run_bound = False

    @classmethod
    def from_json_documents(cls, scene_document: Mapping[str, Any], robot_document: Mapping[str, Any], **kwargs: Any) -> "TransportOrchestrationExecutor":
        return cls(TransportAdmissionResolver.from_json_documents(scene_document, robot_document), **kwargs)

    def bind_run(self, request: TaskRunRequest) -> None:
        if self._run_bound:
            raise ValueError("create a fresh transport executor for each task run/branch")
        self._run_bound = True
        self.ledger.append("run_bound", task_id=request.task.task_id, scene_id=request.task.scene_id,
                           branch_id=request.branch_id, checkpoint=request.checkpoint, repeat_id=request.repeat_id)

    def _feedback(self, assignment: Assignment, *, outcome: str, code: FailureCode,
                  message: str, world_changes: Mapping[str, Any] | None = None) -> ExecutionFeedback:
        return ExecutionFeedback(
            task_id=assignment.task_id, robot_id=assignment.robot_id, outcome=outcome,
            failure_code=code, message=message, timestamp_s=float(self.clock()),
            world_changes=dict(world_changes or {}),
        )

    @staticmethod
    def _validate_callback_feedback(feedback: ExecutionFeedback, assignment: Assignment) -> ExecutionFeedback:
        if not isinstance(feedback, ExecutionFeedback):
            raise TypeError("execute_command must return bool or ExecutionFeedback")
        if (feedback.task_id, feedback.robot_id) != (assignment.task_id, assignment.robot_id):
            raise ValueError("execute_command returned feedback for another assignment")
        return feedback

    def execute(self, assignment: Assignment, task: Any, *, scene: Any) -> ExecutionUpdate:
        if not self._run_bound:
            raise RuntimeError("bind_run must be called before transport execution")
        if not isinstance(assignment, Assignment):
            raise TypeError("assignment must be an Assignment")
        request_id = _stable_request_id(assignment, task)
        try:
            command = compile_transport_command(assignment, task, request_id=request_id)
        except Exception as exc:
            self.ledger.append("transport_command_failed", task_id=assignment.task_id,
                               robot_id=assignment.robot_id, request_id=request_id, reason=str(exc))
            return ExecutionUpdate(
                self._feedback(assignment, outcome=DependencyOutcome.TERMINAL_FAILURE.value,
                               code=FailureCode.INVALID_PLAN, message=str(exc)),
                DependencyOutcome.TERMINAL_FAILURE,
            )

        self.ledger.append("transport_command_bound", **command.to_mapping())
        admission = self.admission.check(command)
        self.ledger.append("transport_admission", **admission.to_mapping(), request_id=request_id)
        provenance = {"transport_command": command.to_mapping(), "transport_admission": admission.to_mapping()}
        if not admission.admitted:
            feedback = admission.to_execution_feedback(task_id=assignment.task_id, robot_id=assignment.robot_id,
                                                       timestamp_s=float(self.clock()))
            feedback = replace(feedback, world_changes={**provenance, **feedback.world_changes})
            return ExecutionUpdate(feedback, DependencyOutcome.TERMINAL_FAILURE)

        if self.navigate is not None:
            try:
                navigated = self.navigate(command, scene)
            except Exception as exc:
                navigated = False
                navigation_error = str(exc)
            else:
                navigation_error = ""
            self.ledger.append("transport_navigation", request_id=request_id,
                               status="completed" if navigated is True else "failed",
                               reason=navigation_error or None)
            if navigated is not True:
                return ExecutionUpdate(
                    self._feedback(assignment, outcome=DependencyOutcome.TERMINAL_FAILURE.value,
                                   code=FailureCode.EXECUTION_FAILURE,
                                   message=navigation_error or "navigation did not reach the authored semantic target",
                                   world_changes=provenance),
                    DependencyOutcome.TERMINAL_FAILURE,
                )

        self.ledger.append("transport_started", request_id=request_id)
        if self.execute_command is None:
            self.ledger.append("transport_failed", request_id=request_id, reason="execution callback is unavailable")
            return ExecutionUpdate(
                self._feedback(assignment, outcome=DependencyOutcome.TERMINAL_FAILURE.value,
                               code=FailureCode.EXECUTION_FAILURE,
                               message="execution callback is unavailable; transport remains unexecuted",
                               world_changes=provenance),
                DependencyOutcome.TERMINAL_FAILURE,
            )
        try:
            raw = self.execute_command(command, scene)
            if isinstance(raw, ExecutionFeedback):
                feedback = self._validate_callback_feedback(raw, assignment)
                feedback = replace(feedback, world_changes={**provenance, **feedback.world_changes})
            elif type(raw) is bool:
                feedback = self._feedback(
                    assignment,
                    outcome=DependencyOutcome.COMPLETED.value if raw else DependencyOutcome.TERMINAL_FAILURE.value,
                    code=FailureCode.NONE if raw else FailureCode.EXECUTION_FAILURE,
                    message="transport execution completed" if raw else "transport execution failed",
                    world_changes=provenance,
                )
            else:
                raise TypeError("execute_command must return bool or ExecutionFeedback")
        except Exception as exc:
            self.ledger.append("transport_failed", request_id=request_id, reason=str(exc))
            return ExecutionUpdate(
                self._feedback(assignment, outcome=DependencyOutcome.TERMINAL_FAILURE.value,
                               code=FailureCode.EXECUTION_FAILURE, message=f"transport execution failed: {exc}",
                               world_changes=provenance),
                DependencyOutcome.TERMINAL_FAILURE,
            )

        completed = feedback.failure_code is FailureCode.NONE and feedback.outcome == DependencyOutcome.COMPLETED.value
        self.ledger.append("transport_returned" if completed else "transport_failed", request_id=request_id,
                           outcome=feedback.outcome, failure_code=feedback.failure_code.value)
        dependency = DependencyOutcome.COMPLETED if completed else DependencyOutcome.TERMINAL_FAILURE
        return ExecutionUpdate(feedback, dependency)

    def execute_batch(self, assignments: Sequence[Assignment], tasks: Mapping[str, Any], *, scene: Any) -> tuple[ExecutionUpdate, ...]:
        return tuple(self.execute(item, tasks[item.task_id], scene=scene) for item in assignments)


__all__ = ["TransportOrchestrationExecutor"]
