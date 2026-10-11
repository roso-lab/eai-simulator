"""Cooperative multi-robot execution on one caller-owned simulator step loop.

Handles provide actions and observe ticks; they never call ``env.step``.  This
module owns only scheduler/runtime protocol state.  Physical success remains an
external evaluator concern.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import threading
from typing import Any, Protocol

from algorithm.common_coordination.contract import (
    Assignment,
    EventLedger,
    ExecutionFeedback,
    FailureCode,
)
from algorithm.skills.contracts import SkillPlan, SkillProvider, SkillRequest
from task.contracts import (
    DependencyOutcome,
    ExecutionUpdate,
    TaskRunRequest,
)
from task.resources import (
    ResourceBusyError,
    ResourceLease,
    ResourceManager,
    ResourceQuarantinedError,
)

from ..observation import WorldSnapshot
from .skills import ExecutionReceipt


class CooperativeHandle(Protocol):
    """One non-stepping cooperative runtime handle."""

    def command(self) -> Any:
        """Return the action for this handle's assigned robot."""

    def after_step(self) -> ExecutionReceipt | None:
        """Observe the shared step and return a terminal receipt or ``None``."""

    def cancel(self, reason: str) -> None:
        """Request stopping; return value is never treated as confirmation."""


@dataclass(frozen=True)
class CooperativeBinding:
    """Pure request/plan binding plus the caller-owned runtime start callback."""

    request: SkillRequest
    provider: SkillProvider
    start: Callable[..., CooperativeHandle]
    validate: Callable[..., None]
    resources: tuple[str, ...] = ()


class _ProtocolError(ValueError):
    """A malformed runtime response that must remain terminal."""


@dataclass
class _Prepared:
    index: int
    assignment: Assignment
    task: Any
    binding: CooperativeBinding
    request: SkillRequest
    plan: SkillPlan
    request_digest: str
    lease: ResourceLease | None = None


@dataclass
class _Live:
    prepared: _Prepared
    handle: CooperativeHandle
    cancelling: bool = False
    cancel_reason: str = ""
    cancel_attempts: int = 0
    failure_outcome: DependencyOutcome = DependencyOutcome.RETRYABLE_FAILURE
    failure_code: FailureCode = FailureCode.EXECUTION_FAILURE
    failure_message: str = ""


class CooperativeBatchExecutor:
    """Drive several handles with exactly one shared step per cooperative tick."""

    def __init__(
        self,
        resolve: Callable[..., CooperativeBinding],
        *,
        step: Callable[..., Any],
        neutral_actions: Callable[[Any], Mapping[str, Any]],
        resources: ResourceManager | None = None,
        observation_provider: Callable[[Any], WorldSnapshot] | None = None,
        max_ticks: int,
        max_cancel_ticks: int,
        expected_scene_id: str,
    ) -> None:
        for name, value in (
            ("max_ticks", max_ticks),
            ("max_cancel_ticks", max_cancel_ticks),
        ):
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not isinstance(expected_scene_id, str) or not expected_scene_id.strip():
            raise ValueError("expected_scene_id must be a nonempty string")
        self.resolve = resolve
        self.step = step
        self.neutral_actions = neutral_actions
        self.resources = resources if resources is not None else ResourceManager()
        self.observation_provider = observation_provider
        self.max_ticks = max_ticks
        self.max_cancel_ticks = max_cancel_ticks
        self.expected_scene_id = expected_scene_id

        self._owner_thread = threading.get_ident()
        self._run_bound = False
        self._scene_bound = False
        self._scene: Any = None
        self._executing = False
        self._consumed: set[str] = set()
        self._quarantined: list[ResourceLease] = []
        self._live: list[_Live] = []
        self._external_cancel_reason: str | None = None
        self._last_observation: WorldSnapshot | None = None
        self.ledger = EventLedger(schema_version="task.cooperative_runtime.v1")

    @property
    def quarantined_leases(self) -> tuple[ResourceLease, ...]:
        return tuple(self._quarantined)

    def bind_run(self, request: TaskRunRequest) -> None:
        """Bind this executor to exactly one task run before runtime effects."""

        self._require_owner()
        if not isinstance(request, TaskRunRequest):
            raise TypeError("run binding requires TaskRunRequest")
        if self._run_bound or self._scene_bound or self._executing:
            raise ValueError("create a fresh cooperative executor for each task run")
        if request.task.scene_id != self.expected_scene_id:
            raise ValueError("task run does not belong to cooperative executor scene")
        self._run_bound = True
        self.ledger.append(
            "run_bound",
            task_id=request.task.task_id,
            scene_id=request.task.scene_id,
            branch_id=request.branch_id,
            checkpoint=request.checkpoint,
            repeat_id=request.repeat_id,
        )

    def cancel(self, reason: str) -> None:
        """Request bounded cancellation from the owner thread.

        The request is applied at the next safe scheduler boundary.  A callback
        return never confirms stopping; only a matching quiescent receipt does.
        """

        self._require_owner()
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("cancel reason must be a nonempty string")
        if not self._executing or not self._live:
            raise RuntimeError("cooperative cancellation requires live handles")
        if self._external_cancel_reason is None:
            self._external_cancel_reason = reason
            self.ledger.append("cancel_requested", reason=reason)

    def execute(
        self,
        assignment: Assignment,
        task: Any,
        *,
        scene: Any,
    ) -> ExecutionUpdate:
        return self.execute_batch((assignment,), {assignment.task_id: task}, scene=scene)[0]

    def execute_batch(
        self,
        assignments: Sequence[Assignment],
        tasks: Mapping[str, Any],
        *,
        scene: Any,
    ) -> tuple[ExecutionUpdate, ...]:
        self._require_owner()
        if self._has_quarantine():
            raise RuntimeError("scene has quarantined runtime ownership; reconcile recovery before stepping")
        if not self._run_bound:
            raise RuntimeError("bind_run must be called before cooperative execution")
        if self._executing:
            raise RuntimeError("cooperative executor is non-reentrant")
        if self._scene_bound and scene is not self._scene:
            raise ValueError("cooperative executor cannot cross scene/session objects")

        batch = tuple(assignments)
        if not batch:
            return ()
        self._validate_batch_identities(batch, tasks)
        self._scene, self._scene_bound = scene, True
        self._executing = True
        self._external_cancel_reason = None
        try:
            return self._execute_batch(batch, tasks, scene=scene)
        finally:
            self._live = []
            self._external_cancel_reason = None
            self._executing = False

    def _execute_batch(
        self,
        assignments: tuple[Assignment, ...],
        tasks: Mapping[str, Any],
        *,
        scene: Any,
    ) -> tuple[ExecutionUpdate, ...]:
        results: dict[int, ExecutionUpdate] = {}
        prepared: list[_Prepared] = []
        snapshot: WorldSnapshot | None = None
        observation: Mapping[str, Any] | None = None
        if self.observation_provider is not None:
            snapshot = self.observation_provider(scene)
            if not isinstance(snapshot, WorldSnapshot):
                raise TypeError("observation provider must return WorldSnapshot")
            if snapshot.scene_id != self.expected_scene_id:
                raise ValueError("observation does not belong to cooperative executor scene")
            if self._last_observation is not None:
                snapshot.validate_successor(self._last_observation)
            self._last_observation = snapshot
            observation = snapshot.to_dict()
            self.ledger.append(
                "world_observed",
                revision=snapshot.revision,
                tick=snapshot.tick,
                digest=snapshot.digest,
            )

        # Resolve, validate and plan the entire batch before any handle starts.
        for index, assignment in enumerate(assignments):
            task = tasks[assignment.task_id]
            try:
                prepared.append(
                    self._prepare(
                        index,
                        assignment,
                        task,
                        scene=scene,
                        snapshot=snapshot,
                        observation=observation,
                    )
                )
            except Exception as exc:
                results[index] = self._update(
                    assignment,
                    DependencyOutcome.TERMINAL_FAILURE,
                    FailureCode.INVALID_PLAN,
                    f"{type(exc).__name__}: {exc}",
                )
                self.ledger.append(
                    "binding_rejected",
                    task_id=assignment.task_id,
                    robot_id=assignment.robot_id,
                    reason=f"{type(exc).__name__}: {exc}",
                )

        request_ids = [item.request.request_id for item in prepared]
        duplicates = sorted(
            {request_id for request_id in request_ids if request_ids.count(request_id) > 1}
        )
        if duplicates:
            raise ValueError(f"duplicate cooperative request ids: {duplicates}")

        try:
            self._admit_and_drive(prepared, results, scene=scene)
        except BaseException as exc:
            self._abort_prepared(prepared, reason=f"batch interrupted: {type(exc).__name__}")
            raise
        return tuple(results[index] for index in range(len(assignments)))

    def _admit_and_drive(
        self,
        prepared: list[_Prepared],
        results: dict[int, ExecutionUpdate],
        *,
        scene: Any,
    ) -> None:
        eligible: list[_Prepared] = []
        for item in prepared:
            if item.request.request_id in self._consumed:
                results[item.index] = self._update(
                    item.assignment,
                    DependencyOutcome.TERMINAL_FAILURE,
                    FailureCode.INVALID_PLAN,
                    "request already consumed; explicit retries require a new request id",
                )
                continue
            keys = ("robot:" + item.request.robot_id, *item.binding.resources)
            try:
                item.lease = self.resources.acquire(item.request.request_id, keys)
            except ResourceBusyError as exc:
                results[item.index] = self._update(
                    item.assignment,
                    DependencyOutcome.RETRYABLE_FAILURE,
                    FailureCode.EXECUTION_FAILURE,
                    str(exc),
                )
                self.ledger.append(
                    "resource_busy",
                    task_id=item.assignment.task_id,
                    request_id=item.request.request_id,
                    reason=str(exc),
                )
            except Exception as exc:
                results[item.index] = self._update(
                    item.assignment,
                    DependencyOutcome.TERMINAL_FAILURE,
                    FailureCode.INVALID_PLAN,
                    f"{type(exc).__name__}: {exc}",
                )
                self.ledger.append(
                    "resource_request_rejected",
                    task_id=item.assignment.task_id,
                    request_id=item.request.request_id,
                    reason=f"{type(exc).__name__}: {exc}",
                )
            else:
                eligible.append(item)
                self.ledger.append(
                    "resources_acquired",
                    task_id=item.assignment.task_id,
                    request_id=item.request.request_id,
                    lease_id=item.lease.lease_id,
                    resources=list(item.lease.resources),
                )

        # Revalidate all acquired bindings under ownership before any start.
        startable: list[_Prepared] = []
        for item in eligible:
            assert item.lease is not None
            try:
                item.binding.validate(
                    scene=scene,
                    request=deepcopy(item.request),
                )
            except Exception as exc:
                self.resources.release(item.lease)
                self.ledger.append(
                    "binding_rejected_before_start",
                    request_id=item.request.request_id,
                    lease_id=item.lease.lease_id,
                    reason=f"{type(exc).__name__}: {exc}",
                )
                results[item.index] = self._update(
                    item.assignment,
                    DependencyOutcome.TERMINAL_FAILURE,
                    FailureCode.INVALID_PLAN,
                    f"{type(exc).__name__}: {exc}",
                )
            else:
                startable.append(item)

        live: list[_Live] = []
        self._live = live
        for item in startable:
            assert item.lease is not None
            if self._has_quarantine():
                self.resources.release(item.lease)
                results[item.index] = self._update(item.assignment, DependencyOutcome.TERMINAL_FAILURE,
                    FailureCode.SAFE_STOP, "scene quarantined during batch start; handle not started")
                continue
            self._consumed.add(item.request.request_id)
            self.ledger.append(
                "start_requested",
                task_id=item.assignment.task_id,
                request_id=item.request.request_id,
                lease_id=item.lease.lease_id,
            )
            handle: Any = None
            try:
                handle = item.binding.start(item.plan, scene=scene)
                self._validate_handle(handle)
            except BaseException as exc:
                cancel_callback = getattr(handle, "cancel", None)
                if callable(cancel_callback):
                    try:
                        cancel_callback(
                            f"cooperative start protocol failed: {type(exc).__name__}"
                        )
                    except BaseException:
                        pass
                self._quarantine(
                    item.lease,
                    reason=f"start failed without quiescent receipt: {type(exc).__name__}",
                )
                results[item.index] = self._update(
                    item.assignment,
                    DependencyOutcome.TERMINAL_FAILURE,
                    FailureCode.SAFE_STOP,
                    f"{type(exc).__name__}: {exc}",
                )
                self.ledger.append(
                    "start_failed",
                    request_id=item.request.request_id,
                    lease_id=item.lease.lease_id,
                    reason=f"{type(exc).__name__}: {exc}",
                )
                if not isinstance(exc, Exception):
                    raise
            else:
                live.append(_Live(prepared=item, handle=handle))
                self.ledger.append(
                    "handle_started",
                    request_id=item.request.request_id,
                    lease_id=item.lease.lease_id,
                )

        if live:
            self._drive(live, results, scene=scene)

    def _prepare(
        self,
        index: int,
        assignment: Assignment,
        task: Any,
        *,
        scene: Any,
        snapshot: WorldSnapshot | None,
        observation: Mapping[str, Any] | None,
    ) -> _Prepared:
        if task.task_id != assignment.task_id:
            raise ValueError("assignment and task identities disagree")
        if snapshot is not None:
            robot = next(
                (
                    item
                    for item in snapshot.robots
                    if item.capability.robot_id == assignment.robot_id
                ),
                None,
            )
            if robot is None or not robot.capability.active:
                raise ValueError(
                    "assigned robot is absent or inactive in live observation"
                )
            if not task.required_capabilities <= robot.capability.capabilities:
                raise ValueError("assigned robot lacks live capabilities")
        binding = self.resolve(assignment, task, scene=scene)
        if not isinstance(binding, CooperativeBinding):
            raise TypeError("resolver must return CooperativeBinding")
        if not isinstance(binding.request, SkillRequest):
            raise TypeError("cooperative binding requires SkillRequest")
        if not callable(binding.start) or not callable(binding.validate):
            raise TypeError("cooperative binding callbacks must be callable")
        if not isinstance(binding.resources, tuple):
            raise TypeError("cooperative binding resources must be a tuple")
        request = deepcopy(binding.request)
        if (
            request.robot_id != assignment.robot_id
            or getattr(binding.provider, "skill_id", None) != request.skill_id
        ):
            raise ValueError("assignment/request/provider identities disagree")
        digest = self._request_digest(request)
        binding.validate(scene=scene, request=deepcopy(request))
        plan = binding.provider.plan(
            request,
            observation=deepcopy(observation),
        )
        if self._request_digest(request) != digest:
            raise ValueError("provider mutated its request")
        if not isinstance(plan, SkillPlan) or plan.status != "planned" or not plan.commands:
            raise ValueError("provider did not produce an executable cooperative plan")
        if (plan.request_id, plan.skill_id, plan.robot_id) != (
            request.request_id,
            request.skill_id,
            request.robot_id,
        ):
            raise ValueError("cooperative plan identity does not match request")
        self.ledger.append(
            "skill_planned",
            task_id=assignment.task_id,
            request_id=request.request_id,
            robot_id=request.robot_id,
            skill_id=request.skill_id,
            request_digest=digest,
        )
        return _Prepared(
            index=index,
            assignment=assignment,
            task=task,
            binding=binding,
            request=request,
            plan=plan,
            request_digest=digest,
        )

    def _drive(
        self,
        live: list[_Live],
        results: dict[int, ExecutionUpdate],
        *,
        scene: Any,
    ) -> None:
        normal_ticks = 0
        shared_tick = 0
        while live:
            if self._has_quarantine():
                self._halt_quarantined(live, results)
                return
            if normal_ticks >= self.max_ticks:
                for state in tuple(live):
                    if not state.cancelling:
                        self._begin_cancel(
                            state,
                            reason="cooperative tick budget exhausted",
                            outcome=DependencyOutcome.RETRYABLE_FAILURE,
                            code=FailureCode.EXECUTION_FAILURE,
                            message="cooperative tick budget exhausted; safe cancellation required",
                        )

            actions: dict[str, Any] | None = None
            commands_valid = True
            tick_succeeded = False
            try:
                neutral = self.neutral_actions(scene)
                if not isinstance(neutral, Mapping):
                    raise TypeError("neutral_actions must return a mapping")
                actions = dict(neutral)
            except Exception as exc:
                for state in tuple(live):
                    self._begin_cancel_for_exception(state, "neutral action", exc)

            if self._has_quarantine():
                self._halt_quarantined(live, results)
                return

            if actions is not None:
                for state in tuple(live):
                    try:
                        action = state.handle.command()
                    except Exception as exc:
                        self._begin_cancel_for_exception(state, "command", exc)
                        commands_valid = False
                    else:
                        actions[state.prepared.assignment.robot_id] = action
                    if self._has_quarantine():
                        self._halt_quarantined(live, results)
                        return

            # A failed command may leave a stale controller target. Rebuild the
            # whole action map on the next bounded cancellation attempt.
            if actions is not None and commands_valid:
                try:
                    self.step(scene, actions)
                except Exception as exc:
                    for state in tuple(live):
                        self._begin_cancel_for_exception(state, "shared step", exc)
                else:
                    tick_succeeded = True
                    shared_tick += 1
                    self.ledger.append(
                        "shared_tick",
                        tick=shared_tick,
                        robots=[state.prepared.assignment.robot_id for state in live],
                    )

            if self._external_cancel_reason is not None:
                reason = self._external_cancel_reason
                self._external_cancel_reason = None
                for state in tuple(live):
                    if not state.cancelling:
                        self._begin_cancel(
                            state,
                            reason=reason,
                            outcome=DependencyOutcome.RETRYABLE_FAILURE,
                            code=FailureCode.EXECUTION_FAILURE,
                            message=f"external cancellation requested: {reason}",
                        )

            if self._has_quarantine():
                self._halt_quarantined(live, results)
                return

            for state in tuple(live):
                receipt = None
                # after_step advances measured settling windows. Failed or
                # skipped steps must never fabricate fresh physical samples.
                if tick_succeeded:
                    try:
                        receipt = state.handle.after_step()
                    except Exception as exc:
                        self._begin_cancel_for_exception(state, "after_step", exc)
                    if self._has_quarantine():
                        self._halt_quarantined(live, results)
                        return

                if receipt is not None:
                    try:
                        self._validate_receipt(state, receipt)
                    except _ProtocolError as exc:
                        self._begin_cancel(
                            state,
                            reason=str(exc),
                            outcome=DependencyOutcome.TERMINAL_FAILURE,
                            code=FailureCode.INVALID_PLAN,
                            message=f"{type(exc).__name__}: {exc}",
                            replace_failure=True,
                        )
                        if self._has_quarantine():
                            self._halt_quarantined(live, results)
                            return
                    else:
                        self._finish_with_receipt(state, receipt, results)
                        live.remove(state)
                        continue

                if state.cancelling:
                    state.cancel_attempts += 1
                    if state.cancel_attempts >= self.max_cancel_ticks:
                        self._finish_unconfirmed(state, results)
                        live.remove(state)
                        self._halt_quarantined(live, results)
                        return

            if not any(not state.cancelling for state in live):
                # All remaining work is bounded by per-handle cancel attempts.
                pass
            else:
                normal_ticks += 1

    def _begin_cancel_for_exception(
        self,
        state: _Live,
        operation: str,
        exc: BaseException,
    ) -> None:
        self._begin_cancel(
            state,
            reason=f"{operation} failed: {type(exc).__name__}",
            outcome=DependencyOutcome.RETRYABLE_FAILURE,
            code=FailureCode.EXECUTION_FAILURE,
            message=f"{operation} failed: {type(exc).__name__}: {exc}",
        )

    def _begin_cancel(
        self,
        state: _Live,
        *,
        reason: str,
        outcome: DependencyOutcome,
        code: FailureCode,
        message: str,
        replace_failure: bool = False,
    ) -> None:
        if state.cancelling and not replace_failure:
            return
        if replace_failure or not state.cancelling:
            state.failure_outcome = outcome
            state.failure_code = code
            state.failure_message = message
        if state.cancelling:
            return
        state.cancelling = True
        state.cancel_reason = reason
        self.ledger.append(
            "handle_cancel_requested",
            request_id=state.prepared.request.request_id,
            reason=reason,
        )
        try:
            state.handle.cancel(reason)
        except Exception as exc:
            state.failure_message += (
                f"; cancel callback failed: {type(exc).__name__}: {exc}"
            )
            self.ledger.append(
                "handle_cancel_callback_failed",
                request_id=state.prepared.request.request_id,
                reason=f"{type(exc).__name__}: {exc}",
            )

    def _finish_with_receipt(
        self,
        state: _Live,
        receipt: ExecutionReceipt,
        results: dict[int, ExecutionUpdate],
    ) -> None:
        lease = state.prepared.lease
        assert lease is not None
        self.resources.release(lease)
        if state.cancelling:
            outcome = state.failure_outcome
            code = state.failure_code
            message = state.failure_message
        else:
            outcome = receipt.outcome
            code = receipt.failure_code
            message = receipt.message
        results[state.prepared.index] = self._update(
            state.prepared.assignment,
            outcome,
            code,
            message,
        )
        self.ledger.append(
            "handle_stopped",
            request_id=state.prepared.request.request_id,
            lease_id=lease.lease_id,
            receipt_outcome=receipt.outcome.value,
            scheduler_outcome=outcome.value,
            resources_released=True,
            cancelled=state.cancelling,
        )

    def _finish_unconfirmed(
        self,
        state: _Live,
        results: dict[int, ExecutionUpdate],
    ) -> None:
        lease = state.prepared.lease
        assert lease is not None
        self._quarantine(
            lease,
            reason=f"cancellation unconfirmed: {state.cancel_reason}",
        )
        message = state.failure_message or state.cancel_reason
        results[state.prepared.index] = self._update(
            state.prepared.assignment,
            DependencyOutcome.TERMINAL_FAILURE,
            FailureCode.SAFE_STOP,
            f"{message}; no matching quiescent receipt within cancel budget",
        )
        self.ledger.append(
            "handle_stop_unconfirmed",
            request_id=state.prepared.request.request_id,
            lease_id=lease.lease_id,
            cancel_attempts=state.cancel_attempts,
        )

    def _quarantine(self, lease: ResourceLease, *, reason: str) -> None:
        try:
            self.resources.quarantine(lease, reason)
        except ResourceQuarantinedError:
            # A checkpoint failure may already have quarantined this lease.
            pass
        if lease not in self._quarantined:
            self._quarantined.append(lease)

    def _has_quarantine(self) -> bool:
        return any(record['state'] == 'quarantined' for record in self.resources.snapshot()['leases'])

    def _halt_quarantined(self, live, results) -> None:
        # An unconfirmed controller may still move on any shared env.step.
        # Stop the entire shared loop, retaining every live owner's resources.
        for state in tuple(live):
            try:
                state.handle.cancel('shared scene quarantined')
            except BaseException:
                pass
            results[state.prepared.index] = self._update(state.prepared.assignment,
                DependencyOutcome.TERMINAL_FAILURE, FailureCode.SAFE_STOP,
                'shared scene quarantined; further physical stepping prohibited')
        leases = self.resources.quarantine_active('shared scene contains an unconfirmed controller')
        for lease in leases:
            if lease not in self._quarantined:
                self._quarantined.append(lease)
        live.clear()

    def _validate_receipt(self, state: _Live, receipt: Any) -> None:
        if not isinstance(receipt, ExecutionReceipt):
            raise _ProtocolError("cooperative handle must return ExecutionReceipt or None")
        request = state.prepared.request
        if (receipt.request_id, receipt.skill_id, receipt.robot_id) != (
            request.request_id,
            request.skill_id,
            request.robot_id,
        ):
            raise _ProtocolError("cooperative receipt identity does not match request")
        if not receipt.quiescent:
            raise _ProtocolError("terminal cooperative receipt is not quiescent")

    @staticmethod
    def _validate_handle(handle: Any) -> None:
        for name in ("command", "after_step", "cancel"):
            if not callable(getattr(handle, name, None)):
                raise _ProtocolError(
                    "cooperative start must return a command/after_step/cancel handle"
                )

    @staticmethod
    def _validate_batch_identities(
        assignments: tuple[Assignment, ...],
        tasks: Mapping[str, Any],
    ) -> None:
        task_ids = [item.task_id for item in assignments]
        robot_ids = [item.robot_id for item in assignments]
        if len(set(task_ids)) != len(task_ids):
            raise ValueError("cooperative batch contains duplicate task identities")
        if len(set(robot_ids)) != len(robot_ids):
            raise ValueError("cooperative batch contains duplicate robot identities")
        missing = sorted(set(task_ids) - set(tasks))
        if missing:
            raise ValueError(f"cooperative batch is missing tasks: {missing}")

    @staticmethod
    def _request_digest(request: SkillRequest) -> str:
        return hashlib.sha256(
            json.dumps(
                {
                    "request_id": request.request_id,
                    "skill_id": request.skill_id,
                    "robot_id": request.robot_id,
                    "target_ref": request.target_ref,
                    "target_pose": request.target_pose,
                    "parameters": dict(request.parameters),
                },
                sort_keys=True,
                allow_nan=False,
            ).encode()
        ).hexdigest()

    @staticmethod
    def _update(
        assignment: Assignment,
        outcome: DependencyOutcome,
        code: FailureCode,
        message: str,
    ) -> ExecutionUpdate:
        return ExecutionUpdate(
            ExecutionFeedback(
                assignment.task_id,
                assignment.robot_id,
                outcome.value,
                failure_code=code,
                message=message,
            ),
            outcome,
        )

    def _require_owner(self) -> None:
        if threading.get_ident() != self._owner_thread:
            raise RuntimeError("cooperative executor requires its session owner thread")

    def _abort_prepared(
        self,
        prepared: Sequence[_Prepared],
        *,
        reason: str,
    ) -> None:
        # Never step or accept receipts during fatal cleanup. Cancellation is
        # best effort; preserve the original interrupt even if it fails too.
        for state in self._live:
            if not state.cancelling:
                try:
                    self._begin_cancel(
                        state, reason=reason,
                        outcome=DependencyOutcome.TERMINAL_FAILURE,
                        code=FailureCode.SAFE_STOP, message=reason,
                    )
                except BaseException:
                    pass
        occupied = {record["lease_id"]: record["state"]
                    for record in self.resources.snapshot()["leases"]}
        for item in prepared:
            lease = item.lease
            if lease is None or lease.lease_id not in occupied:
                continue
            if (item.request.request_id in self._consumed
                    or occupied[lease.lease_id] == "quarantined"):
                self._quarantine(lease, reason=reason)
            else:
                self.resources.release(lease)
        self.ledger.append("batch_aborted", reason=reason)


__all__ = [
    "CooperativeBatchExecutor",
    "CooperativeBinding",
    "CooperativeHandle",
]
