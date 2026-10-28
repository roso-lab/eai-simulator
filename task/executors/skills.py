"""Route existing skill contracts through a single session owner thread.

The binding resolver supplies business request mapping, measured live resource
validation, provider, runtime and optional stop callback. This module does not
interpret task metadata, construct controllers, or decide physical success.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import threading
from typing import Any

from algorithm.common_coordination.contract import Assignment, EventLedger, ExecutionFeedback, FailureCode
from algorithm.skills.contracts import SkillPlan, SkillProvider, SkillRequest
from algorithm.skills.simulator_schema import PICK_PLACE_SKILL_ID
from task.contracts import DependencyOutcome, ExecutionUpdate, TaskRunRequest
from task.observation import WorldSnapshot
from task.resources import ResourceBusyError, ResourceLease, ResourceManager, ResourceQuarantinedError


class _ReceiptError(ValueError):
    """A malformed runtime response is not a retryable execution failure."""


@dataclass(frozen=True)
class ExecutionReceipt:
    """Runtime progress only; quiescent means it is safe to release ownership.

    Runtime adapters must measure quiescence; returning from a Python call is
    not evidence that a ROS action or controller has stopped. A completed
    receipt unlocks DAG dependencies, never certifies physical success.
    """

    request_id: str
    skill_id: str
    robot_id: str
    outcome: DependencyOutcome
    quiescent: bool
    failure_code: FailureCode = FailureCode.NONE
    message: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome", DependencyOutcome(self.outcome))
        object.__setattr__(self, "failure_code", FailureCode(self.failure_code))
        if type(self.quiescent) is not bool:
            raise ValueError("runtime quiescent must be an explicit boolean")
        if any(not isinstance(v, str) or not v.strip()
               for v in (self.request_id, self.skill_id, self.robot_id)):
            raise ValueError("runtime receipt identity must be nonempty")
        if (self.outcome is DependencyOutcome.COMPLETED) != (self.failure_code is FailureCode.NONE):
            raise ValueError("runtime outcome and failure code disagree")


@dataclass(frozen=True)
class BoundSkill:
    """Resolved runtime callbacks, kept outside the formal task definition.

    validate(scene, request) must verify live object/controller/capability
    bindings and must raise if invalid. All planning/validation callbacks must
    be side-effect free. execute/stop alone may touch the controller.
    """

    request: SkillRequest
    provider: SkillProvider
    execute: Callable[..., ExecutionReceipt]
    validate: Callable[..., None]
    resources: tuple[str, ...] = ()
    stop: Callable[..., bool] | None = None


class SkillTaskExecutor:
    """Synchronous orchestration executor with session-local ownership.

    A batch is serial, not simultaneous robot stepping. One executor belongs
    to one branch/session; use unique request ids for explicit retries. The
    consumed-id guard is in-memory and makes no crash-recovery claim.
    """

    def __init__(self, resolve: Callable[..., BoundSkill], *,
                 resources: ResourceManager | None = None,
                 observation_provider: Callable[[Any], WorldSnapshot] | None = None,
                 expected_scene_id: str | None = None) -> None:
        if observation_provider is not None and (
            not isinstance(expected_scene_id, str) or not expected_scene_id.strip()
        ):
            raise ValueError("observation provider requires expected_scene_id")
        self.resolve = resolve
        self.resources = resources if resources is not None else ResourceManager()
        self._owner_thread = threading.get_ident()
        self._executing = False
        self._consumed: set[str] = set()
        self._quarantined: list[ResourceLease] = []
        self.observation_provider = observation_provider
        self.expected_scene_id = expected_scene_id
        self._last_observation: WorldSnapshot | None = None
        self._scene_bound = False
        self._scene: Any = None
        self.ledger = EventLedger(schema_version="task.skill_runtime.v1")
        self._run_bound = False

    def bind_run(self, request: TaskRunRequest) -> None:
        """Called before scene/replay effects; one instance serves one run."""
        if threading.get_ident() != self._owner_thread:
            raise RuntimeError("run binding requires the session owner thread")
        if self._run_bound or self._scene_bound:
            raise ValueError("create a fresh skill executor for each task run/branch")
        if self.expected_scene_id is not None and request.task.scene_id != self.expected_scene_id:
            raise ValueError("task run does not belong to executor scene")
        self._run_bound = True
        self.ledger.append("run_bound", task_id=request.task.task_id,
                           scene_id=request.task.scene_id, branch_id=request.branch_id,
                           checkpoint=request.checkpoint, repeat_id=request.repeat_id)

    @property
    def quarantined_leases(self) -> tuple[ResourceLease, ...]:
        return tuple(self._quarantined)

    def execute_batch(self, assignments: Sequence[Assignment], tasks: Mapping[str, Any],
                      *, scene: Any) -> tuple[ExecutionUpdate, ...]:
        return tuple(self.execute(item, tasks[item.task_id], scene=scene) for item in assignments)

    def execute(self, assignment: Assignment, task: Any, *, scene: Any) -> ExecutionUpdate:
        if any(record['state'] == 'quarantined' for record in self.resources.snapshot()['leases']):
            raise RuntimeError('scene has quarantined runtime ownership; reconcile recovery before stepping')
        if threading.get_ident() != self._owner_thread or self._executing:
            raise RuntimeError("skill executor must run non-reentrantly on its session owner thread")
        if not self._run_bound:
            raise RuntimeError("bind_run must be called before skill execution")
        if self._scene_bound and scene is not self._scene:
            raise ValueError("skill executor cannot be reused across sessions/branches")
        self._scene, self._scene_bound = scene, True
        self._executing = True
        try:
            return self._execute(assignment, task, scene=scene)
        finally:
            self._executing = False

    @staticmethod
    def _update(assignment: Assignment, outcome: DependencyOutcome,
                code: FailureCode = FailureCode.NONE, message: str = "") -> ExecutionUpdate:
        return ExecutionUpdate(
            ExecutionFeedback(assignment.task_id, assignment.robot_id, outcome.value,
                              failure_code=code, message=message), outcome,
        )

    @staticmethod
    def _request_digest(request: SkillRequest) -> str:
        return hashlib.sha256(json.dumps(
            request.to_mapping(), sort_keys=True, allow_nan=False,
        ).encode()).hexdigest()

    def _execute(self, assignment: Assignment, task: Any, *, scene: Any) -> ExecutionUpdate:
        fail = DependencyOutcome.TERMINAL_FAILURE
        retry = DependencyOutcome.RETRYABLE_FAILURE
        try:
            if task.task_id != assignment.task_id:
                raise ValueError("assignment and task identities disagree")
            binding = self.resolve(assignment, task, scene=scene)
            if not isinstance(binding, BoundSkill) or not isinstance(binding.request, SkillRequest):
                raise TypeError("resolver must return BoundSkill with a SkillRequest")
            request = deepcopy(binding.request)
            if request.robot_id != assignment.robot_id or binding.provider.skill_id != request.skill_id:
                raise ValueError("assignment/request/provider identities disagree")
            if request.task_id is not None and request.task_id != assignment.task_id:
                raise ValueError("request task identity does not match assignment")
            if request.skill_version != "simulator.skill.v1":
                raise ValueError("unsupported skill request version")
            if request.request_id in self._consumed:
                raise ValueError("request already consumed; explicit retries require a new request id")
            observation = None
            if self.observation_provider is not None:
                snapshot = self.observation_provider(scene)
                if not isinstance(snapshot, WorldSnapshot):
                    raise TypeError("observation provider must return WorldSnapshot")
                if snapshot.scene_id != self.expected_scene_id:
                    raise ValueError("observation does not belong to executor scene")
                if self._last_observation is not None:
                    snapshot.validate_successor(self._last_observation)
                robot = next((item for item in snapshot.robots
                              if item.capability.robot_id == request.robot_id), None)
                if robot is None or not robot.capability.active:
                    raise ValueError("assigned robot is absent or inactive in live observation")
                if not task.required_capabilities <= robot.capability.capabilities:
                    raise ValueError("assigned robot lacks live capabilities")
                self._last_observation = snapshot
                observation = snapshot.to_dict()
            digest = self._request_digest(request)
            binding.validate(scene=scene, request=deepcopy(request))
            plan = binding.provider.plan(request, observation=observation)
            if self._request_digest(request) != digest:
                raise ValueError("provider mutated its request")
            if not isinstance(plan, SkillPlan) or plan.status != "planned" or not plan.commands:
                raise ValueError("provider did not produce an executable skill plan")
            identity = (request.request_id, request.skill_id, request.robot_id)
            if (plan.request_id, plan.skill_id, plan.robot_id) != identity:
                raise ValueError("skill plan identity does not match request")
            self.ledger.append("skill_planned", task_id=assignment.task_id,
                               request_id=request.request_id, robot_id=request.robot_id,
                               skill_id=request.skill_id, request_digest=digest)
            keys = ("robot:" + request.robot_id, *binding.resources)
            # Runtime-specific bindings are checked before any resource or
            # command is committed; resources are canonical keys from resolver.
            lease = self.resources.acquire(request.request_id, keys)
        except ResourceBusyError as exc:
            self.ledger.append("resource_busy", task_id=assignment.task_id, reason=str(exc))
            return self._update(assignment, retry, FailureCode.EXECUTION_FAILURE, str(exc))
        except Exception as exc:
            self.ledger.append("binding_rejected", task_id=assignment.task_id, reason=str(exc))
            return self._update(assignment, fail, FailureCode.INVALID_PLAN, str(exc))

        started = False
        try:
            self.ledger.append("resources_acquired", request_id=request.request_id,
                               lease_id=lease.lease_id, resources=list(lease.resources))
            # Validate again while holding ownership, before invoking runtime.
            binding.validate(scene=scene, request=deepcopy(request))
            if self._has_quarantine():
                raise ResourceQuarantinedError("scene quarantined before runtime start")
            self.ledger.append("runtime_started", request_id=request.request_id)
            self._consumed.add(request.request_id)
            started = True
            receipt = binding.execute(plan, scene=scene)
            if self._has_quarantine():
                raise ResourceQuarantinedError("scene quarantined during runtime")
            if not isinstance(receipt, ExecutionReceipt):
                raise _ReceiptError("runtime must return ExecutionReceipt")
            if (receipt.request_id, receipt.skill_id, receipt.robot_id) != identity:
                raise _ReceiptError("runtime receipt identity does not match request")
            if not receipt.quiescent and request.skill_id != PICK_PLACE_SKILL_ID:
                raise RuntimeError("runtime did not confirm quiescence")
        except BaseException as exc:
            stopped = not started
            if started and binding.stop is not None:
                try:
                    stopped = binding.stop(scene=scene, request=deepcopy(request)) is True
                except BaseException:
                    stopped = False
            quarantined = self._has_quarantine()
            if stopped and not quarantined:
                self.resources.release(lease)
            else:
                stopped = False
                self._quarantine(lease, reason=f"unconfirmed stop: {type(exc).__name__}")
            try:
                self.ledger.append("runtime_aborted" if started else "binding_rejected_before_command",
                                   request_id=request.request_id, lease_id=lease.lease_id,
                                   stop_confirmed=stopped, resources_released=stopped, reason=str(exc))
            except BaseException:
                # Recording failure must not replace the original interrupt or
                # prevent ownership cleanup. Propagate the original error.
                raise exc
            if not isinstance(exc, Exception):
                raise
            if not started and stopped:
                return self._update(assignment, fail, FailureCode.INVALID_PLAN, str(exc))
            return self._update(
                assignment, retry if stopped and not isinstance(exc, _ReceiptError) else fail,
                (FailureCode.INVALID_PLAN if isinstance(exc, _ReceiptError)
                 else FailureCode.EXECUTION_FAILURE) if stopped else FailureCode.SAFE_STOP,
                f"{type(exc).__name__}: {exc}; stop_confirmed={stopped}",
            )
        self.resources.release(lease)
        self.ledger.append("runtime_returned", request_id=request.request_id,
                           lease_id=lease.lease_id, outcome=receipt.outcome.value,
                           quiescent=True, resources_released=True)
        return self._update(assignment, receipt.outcome, receipt.failure_code, receipt.message)

    def _has_quarantine(self) -> bool:
        return any(record["state"] == "quarantined" for record in self.resources.snapshot()["leases"])

    def _quarantine(self, lease: ResourceLease, *, reason: str) -> None:
        try:
            self.resources.quarantine(lease, reason)
        except ResourceQuarantinedError:
            pass
        if lease not in self._quarantined:
            self._quarantined.append(lease)
