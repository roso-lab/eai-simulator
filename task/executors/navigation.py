"""Host-ticked node navigation; controller commands belong to adapters."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
import json
import math
from threading import RLock
from typing import Any, Callable, Mapping
from uuid import uuid4

from algorithm.skills.contracts import SkillContractError, SkillPlan, SkillRequest
from algorithm.skills.navigation import (
    NavigationFeedback, NavigationObservation, NavigationProfile, NavigationProvider,
    NavigationResponse, NavigationTarget, normalize_navigation_request,
)
from algorithm.skills.navigation.topology import (
    FrozenNavigationTopology, route_graph_digest, topology_fields,
)
from task.resources import ResourceLease, ResourceManager, ResourceQuarantinedError
from .navigation_backends import NavigationControllerAdapter


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SkillContractError(f"{label} must be finite")
    return float(value)


def _quiescent(feedback: NavigationFeedback) -> bool:
    return feedback.status != "failed" and feedback.stopped and feedback.settled


@dataclass
class _Task:
    request: SkillRequest
    provider: NavigationProvider
    adapter: NavigationControllerAdapter
    lease: ResourceLease
    response: NavigationResponse
    topology: Any
    last_time: float
    world_state_seq: int
    deadline: float | None
    prepared: bool = False
    prepared_intent: str | None = None
    stop_pending: bool = False
    stop_since: float | None = None
    stop_ticks: int = 0
    retired: bool = False
    quarantined: bool = False
    samples: list[dict[str, Any]] = field(default_factory=list)


class NavigationExecutor:
    """Prepare all robots, advance one shared host tick, then confirm each.

    The host owns physics and supplies live pose, velocity, sequence and clock
    through registered adapters. A lease is released only after measured stop
    and stability; uncertain stopping retains the robot in quarantine.
    """

    def __init__(self, *, topology: Any, advance: Callable[[], Any] | None = None,
                 simulation_time: Callable[[], Any] | None = None,
                 resources: ResourceManager | None = None,
                 snapshot_validator: Callable[[int, bool], Any] | None = None,
                 source_reader: Callable[[], Any] | None = None,
                 timeout_s: float | None = None, stop_timeout_s: float = 5.0,
                 max_stop_ticks: int = 240, owner_id: str | None = None):
        if topology is None:
            raise SkillContractError("navigation topology is required")
        for name, callback in (("advance", advance), ("simulation_time", simulation_time),
                               ("snapshot_validator", snapshot_validator), ("source_reader", source_reader)):
            if callback is not None and not callable(callback):
                raise SkillContractError(f"{name} must be callable")
        if timeout_s is not None and _finite(timeout_s, "timeout_s") <= 0:
            raise SkillContractError("timeout_s must be positive")
        if _finite(stop_timeout_s, "stop_timeout_s") <= 0:
            raise SkillContractError("stop_timeout_s must be positive")
        if type(max_stop_ticks) is not int or max_stop_ticks <= 0:
            raise SkillContractError("max_stop_ticks must be a positive integer")
        if owner_id is not None and (not isinstance(owner_id, str) or not owner_id.strip()):
            raise SkillContractError("owner_id must be a nonempty string")
        self.topology = topology
        self._advance, self._time = advance, simulation_time
        self._resources = resources if resources is not None else ResourceManager()
        self._snapshot_validator, self._source_reader = snapshot_validator, source_reader
        self._timeout_s, self._stop_timeout_s = timeout_s, stop_timeout_s
        self._max_stop_ticks = max_stop_ticks
        self._adapters: dict[str, NavigationControllerAdapter] = {}
        self._tasks: dict[str, _Task] = {}
        self._owner = owner_id if owner_id is not None else "navigation:" + uuid4().hex
        self._lock = RLock()

    @property
    def resources(self) -> ResourceManager:
        return self._resources

    @property
    def active_task_ids(self) -> tuple[str, ...]:
        return tuple(sorted(key for key, task in self._tasks.items() if not task.retired))

    def measurement_snapshot(self) -> dict[str, Any]:
        """Return declared runtime state without observing or commanding robots.

        The snapshot is an exact-reexecution measurement surface, not a
        checkpoint or restore API.  External callback internals, controller
        hidden state and simulator state remain outside this declaration.
        """

        with self._lock:
            topology_callable = callable(self.topology)
            topology_binding: dict[str, Any]
            if topology_callable:
                topology_binding = {
                    "kind": "callable",
                    "callable": True,
                    "topology": None,
                    "topology_digest": None,
                    "topology_by_robot": None,
                }
            elif isinstance(self.topology, Mapping) and "nodes" not in self.topology:
                by_robot = {}
                for robot_id in sorted(self.topology):
                    topology = self._document(self.topology[robot_id])
                    by_robot[robot_id] = {
                        "topology": deepcopy(topology),
                        "topology_digest": route_graph_digest(topology),
                    }
                topology_binding = {
                    "kind": "static_by_robot",
                    "callable": False,
                    "topology": None,
                    "topology_digest": None,
                    "topology_by_robot": by_robot,
                }
            else:
                topology = self._document(self.topology)
                topology_binding = {
                    "kind": "static",
                    "callable": False,
                    "topology": deepcopy(topology),
                    "topology_digest": route_graph_digest(topology),
                    "topology_by_robot": None,
                }
            adapters = {}
            for robot_id in sorted(self._adapters):
                adapter = self._adapters[robot_id]
                reader = getattr(adapter, "measurement_snapshot", None)
                adapters[robot_id] = {
                    "measurement_supported": callable(reader),
                    "measurement": reader() if callable(reader) else None,
                }
            tasks = {}
            for task_id in sorted(self._tasks):
                task = self._tasks[task_id]
                topology = self._document(task.topology)
                tasks[task_id] = {
                    "request": task.request.to_mapping(),
                    "provider": task.provider.measurement_snapshot(),
                    "adapter_robot_id": task.adapter.robot_id,
                    "lease": {
                        "lease_id": task.lease.lease_id,
                        "owner": task.lease.owner,
                        "resources": list(task.lease.resources),
                    },
                    "response": {
                        "state": deepcopy(dict(task.response.state)),
                        "plan": task.response.plan.to_mapping(),
                        "private_evidence": deepcopy(dict(task.response.private_evidence)),
                    },
                    "topology": deepcopy(topology),
                    "topology_digest": route_graph_digest(topology),
                    "last_time": task.last_time,
                    "world_state_seq": task.world_state_seq,
                    "deadline": task.deadline,
                    "prepared": task.prepared,
                    "prepared_intent": task.prepared_intent,
                    "stop_pending": task.stop_pending,
                    "stop_since": task.stop_since,
                    "stop_ticks": task.stop_ticks,
                    "retired": task.retired,
                    "quarantined": task.quarantined,
                    "execution_samples": deepcopy(task.samples),
                }
            snapshot = {
                "schema_version": "navigation.executor.measurement.v1",
                "owner_id": self._owner,
                "configuration": {
                    "timeout_s": self._timeout_s,
                    "stop_timeout_s": self._stop_timeout_s,
                    "max_stop_ticks": self._max_stop_ticks,
                    "advance_callback_configured": self._advance is not None,
                    "simulation_time_callback_configured": self._time is not None,
                    "snapshot_validator_configured": self._snapshot_validator is not None,
                    "source_reader_configured": self._source_reader is not None,
                },
                "topology_binding": topology_binding,
                "active_task_ids": list(self.active_task_ids),
                "adapters": adapters,
                "tasks": tasks,
                "resources": self._resources.measurement_snapshot(),
            }
            return json.loads(json.dumps(snapshot, sort_keys=True, allow_nan=False))

    def needs_host_tick(self, task_id: str) -> bool:
        return self._task(task_id).prepared

    def adapter_for(self, robot_id: str) -> NavigationControllerAdapter:
        try:
            return self._adapters[robot_id]
        except KeyError as exc:
            raise SkillContractError("no navigation adapter is registered for robot") from exc

    def register_adapter(self, adapter: NavigationControllerAdapter) -> None:
        robot_id = getattr(adapter, "robot_id", None)
        if not isinstance(robot_id, str) or not robot_id.strip():
            raise SkillContractError("navigation adapter requires robot_id")
        required = ("validate", "observe", "apply_navigation_target", "hold",
                    "before_step", "after_step", "request_stop")
        if any(not callable(getattr(adapter, name, None)) for name in required):
            raise TypeError("navigation adapter does not implement NavigationControllerAdapter")
        if not isinstance(getattr(adapter, "profile", None), NavigationProfile):
            raise TypeError("navigation adapter must expose a NavigationProfile")
        with self._lock:
            if robot_id in self._adapters:
                raise SkillContractError("navigation adapter is already registered")
            self._adapters[robot_id] = adapter

    def topology_for(self, robot_id: str) -> Any:
        value = self.topology(robot_id) if callable(self.topology) else self.topology
        if isinstance(value, Mapping) and "nodes" not in value:
            value = value.get(robot_id)
        if value is None:
            raise SkillContractError("robot has no bound navigation topology")
        return value if isinstance(value, FrozenNavigationTopology) else deepcopy(topology_fields(value))

    @staticmethod
    def _document(topology: Any) -> Mapping[str, Any]:
        return topology.snapshot() if isinstance(topology, FrozenNavigationTopology) else topology_fields(topology)

    def _source_validate(self, topology: Any) -> None:
        if self._source_reader is None:
            return
        source = self._source_reader()
        if not isinstance(source, (tuple, list)) or len(source) != 2:
            raise SkillContractError("source_reader must return (scene_key, source_digests)")
        document = self._document(topology)
        if source[0] != document.get("scene_key") or source[1] != document.get("source", {}):
            raise SkillContractError("navigation topology scene/source identity changed")

    def _observe(self, adapter: NavigationControllerAdapter, *, seq: int | None = None,
                 motion: bool = False) -> NavigationObservation:
        observation = adapter.observe()
        if not isinstance(observation, NavigationObservation) or not observation.valid:
            raise SkillContractError("adapter observation is invalid")
        if seq is not None and observation.world_state_seq != seq:
            raise SkillContractError("adapter observation sequence differs from request")
        if motion and not observation.fresh:
            raise SkillContractError("navigation requires a fresh observation")
        if self._time is not None:
            observation = replace(observation, simulation_time_s=_finite(self._time(), "simulation_time_s"))
        return observation

    def _admit(self, request: SkillRequest) -> tuple[NavigationControllerAdapter, Any, NavigationObservation]:
        adapter = self.adapter_for(request.robot_id)
        adapter.validate(request)
        topology = self.topology_for(request.robot_id)
        self._source_validate(topology)
        digest = route_graph_digest(self._document(topology))
        if request.parameters.get("route_graph_digest", digest) != digest:
            raise SkillContractError("request route_graph_digest differs from topology")
        observation = self._observe(adapter, seq=request.parameters["world_state_seq"])
        self._validate_snapshot(observation, motion=False)
        return adapter, topology, observation

    def _validate_snapshot(self, observation: NavigationObservation, *, motion: bool) -> None:
        if self._snapshot_validator is not None:
            if self._snapshot_validator(observation.world_state_seq, motion) is not True:
                raise SkillContractError("host rejected the navigation snapshot")

    def plan(self, value: SkillRequest | Mapping[str, Any]) -> SkillPlan:
        """Pure planning: no lease, command write or simulator advance."""
        request = normalize_navigation_request(value)
        adapter, topology, observation = self._admit(request)
        return NavigationProvider(topology, profile=adapter.profile).plan(request, observation=observation)

    def dispatch(self, value: SkillRequest | Mapping[str, Any]) -> NavigationResponse:
        request = normalize_navigation_request(value)
        key = request.task_id or request.request_id
        with self._lock:
            if key in self._tasks:
                raise SkillContractError("navigation task already exists")
            adapter, topology, observation = self._admit(request)
            # All fallible clock/source validation precedes lease acquisition.
            lease = self._resources.acquire(self._owner + ":" + key, ("robot:" + request.robot_id,))
            try:
                provider = NavigationProvider(topology, profile=adapter.profile)
                response = provider.dispatch(request, observation=observation)
                adapter.hold(observation)
            except Exception:
                try:
                    adapter.request_stop("dispatch failed")
                    feedback = adapter.after_step()
                    if not _quiescent(feedback):
                        raise SkillContractError("dispatch stop was not confirmed")
                    self._resources.release(lease)
                except Exception:
                    self._resources.quarantine(lease, "navigation dispatch stop was not confirmed")
                raise
            now = observation.simulation_time_s
            deadline = None if self._timeout_s is None else now + self._timeout_s
            task = _Task(request, provider, adapter, lease, response, topology,
                         now, observation.world_state_seq, deadline)
            self._tasks[key] = task
            return self._result(task)

    def _task(self, task_id: str) -> _Task:
        try:
            return self._tasks[task_id]
        except KeyError as exc:
            raise SkillContractError("unknown navigation task") from exc

    def _record(self, value: SkillRequest | Mapping[str, Any]) -> tuple[str, _Task, SkillRequest]:
        request = normalize_navigation_request(value)
        key = request.task_id or request.request_id
        task = self._task(key)
        if task.retired or task.response.state["status"] != "pending":
            raise SkillContractError("navigation task is not active")
        if request.robot_id != task.request.robot_id or request.target_ref != task.request.target_ref:
            raise SkillContractError("navigation request does not match task")
        return key, task, request

    def _check_binding(self, task: _Task) -> None:
        self._resources.validate_lease(task.lease, required_resources=("robot:" + task.request.robot_id,))
        self._source_validate(task.topology)
        current = self.topology_for(task.request.robot_id)
        if route_graph_digest(self._document(current)) != route_graph_digest(self._document(task.topology)):
            raise SkillContractError("navigation route graph identity changed")

    @staticmethod
    def _target(response: NavigationResponse) -> NavigationTarget:
        if len(response.plan.commands) != 1 or response.plan.commands[0].command_type != "navigation_target":
            raise SkillContractError("moving Provider must return one topology node")
        payload = response.plan.commands[0].payload
        return NavigationTarget(payload["node_id"], tuple(payload["position_xyz"]),
                                payload["node_index"], payload.get("target_yaw"))

    def _begin_stop(self, task: _Task, now: float) -> None:
        task.adapter.request_stop("arrived" if task.response.state["status"] == "completed" else "terminal")
        task.adapter.before_step()
        task.stop_pending, task.prepared = True, True
        task.prepared_intent = "stop"
        if task.stop_since is None:
            task.stop_since, task.stop_ticks = now, 0

    @staticmethod
    def _sample(task: _Task, observation: NavigationObservation) -> None:
        task.samples.append({"world_state_seq": observation.world_state_seq,
                             "simulation_time_s": observation.simulation_time_s,
                             "position_m": list(observation.pose.position_m),
                             "target_node_id": task.response.state.get("target_node_id"),
                             "node_index": task.response.state.get("node_index")})

    def prepare(self, value: SkillRequest | Mapping[str, Any]) -> NavigationResponse:
        key, task, request = self._record(value)
        if task.prepared:
            raise SkillContractError("navigation task already has a prepared tick")
        intent = request.parameters.get("intent", "start")
        motion = intent in {"start", "continue", "replan"}
        try:
            self._check_binding(task)
            observation = self._observe(task.adapter, seq=request.parameters["world_state_seq"], motion=motion)
            self._validate_snapshot(observation, motion=motion)
            if observation.world_state_seq < task.world_state_seq or observation.simulation_time_s < task.last_time:
                raise SkillContractError("navigation snapshot moved backwards")
            if task.deadline is not None and motion and observation.simulation_time_s >= task.deadline:
                task.response = task.provider.fail(key, "navigation timeout", reason_code="timeout")
            else:
                task.response = task.provider.control(request, observation=observation)
            task.last_time, task.world_state_seq = observation.simulation_time_s, observation.world_state_seq
            if task.response.state["status"] != "pending":
                self._begin_stop(task, observation.simulation_time_s)
            else:
                if task.response.state["safe_action"] == "track":
                    task.adapter.apply_navigation_target(self._target(task.response), observation)
                else:
                    task.adapter.hold(observation)
                task.adapter.before_step()
                task.prepared, task.prepared_intent = True, intent
            self._sample(task, observation)
            return self._result(task)
        except Exception as exc:
            return self._abort(task, str(exc))

    def after_step(self, value: str | SkillRequest | Mapping[str, Any]) -> NavigationResponse:
        key = value if isinstance(value, str) else normalize_navigation_request(value).task_id
        if key is None and not isinstance(value, str):
            key = normalize_navigation_request(value).request_id
        task = self._task(key)
        if not task.prepared or task.retired:
            raise SkillContractError("navigation task has no prepared tick")
        try:
            self._check_binding(task)
            observation = self._observe(task.adapter, motion=True)
            # One after_step must correspond to a new real host physics tick.
            if observation.world_state_seq <= task.world_state_seq or observation.simulation_time_s <= task.last_time:
                raise SkillContractError("after_step requires advancing host sequence and simulation time")
            self._validate_snapshot(observation, motion=task.prepared_intent != "stop")
            feedback = task.adapter.after_step()
            if not isinstance(feedback, NavigationFeedback):
                raise SkillContractError("adapter returned invalid navigation feedback")
            task.last_time, task.world_state_seq = observation.simulation_time_s, observation.world_state_seq
            if feedback.status == "failed":
                raise SkillContractError(feedback.reason or "controller failed")
            if task.stop_pending:
                self._feedback(task, feedback)
                self._sample(task, observation)
                endpoint_ok = True
                if task.response.state["status"] == "completed":
                    target = self._target(task.response)
                    dimensions = 2 if task.adapter.profile.metric == "xy" else 3
                    error = math.dist(observation.pose.position_m[:dimensions], target.position_m[:dimensions])
                    heading_error = 0.0 if target.heading_rad is None else abs(math.atan2(
                        math.sin(target.heading_rad - observation.pose.yaw_rad),
                        math.cos(target.heading_rad - observation.pose.yaw_rad)))
                    endpoint_ok = error <= task.adapter.profile.arrival_radius_m and heading_error <= .10
                if _quiescent(feedback) and endpoint_ok:
                    self._retire(task, feedback)
                else:
                    # A position controller can pass through a low-speed
                    # overshoot before converging. Keep its authored endpoint
                    # active until BOTH arrival and stability are measured.
                    task.stop_ticks += 1
                    if (task.stop_ticks >= self._max_stop_ticks or
                            observation.simulation_time_s - task.stop_since >= self._stop_timeout_s):
                        code = "endpoint_drift" if not endpoint_ok else "stop_timeout"
                        task.response = task.provider.fail(key, "navigation stop/endpoint was not confirmed", reason_code=code)
                        self._feedback(task, feedback)
                        self._retire(task, feedback)
                    else:
                        task.adapter.before_step()
                return self._result(task)
            intent = task.prepared_intent
            task.prepared, task.prepared_intent = False, None
            if intent in {"start", "continue", "replan"}:
                continuation = replace(task.request, task_id=key, parameters={
                    **task.request.parameters, "intent": "continue", "world_state_seq": observation.world_state_seq})
                if task.deadline is not None and observation.simulation_time_s >= task.deadline:
                    task.response = task.provider.fail(key, "navigation timeout", reason_code="timeout")
                else:
                    task.response = task.provider.control(continuation, observation=observation)
                if task.response.state["status"] != "pending":
                    self._begin_stop(task, observation.simulation_time_s)
            self._feedback(task, feedback)
            self._sample(task, observation)
            return self._result(task)
        except Exception as exc:
            # An invalid clock/pose/tick cannot certify quiescence even if a
            # controller claims it is stopped.
            return self._abort(task, str(exc), force_quarantine=True)

    def step(self, value: SkillRequest | Mapping[str, Any]) -> NavigationResponse:
        if self._advance is None:
            raise SkillContractError("step requires an injected host advance callback")
        response = self.prepare(value)
        task = self._task(response.state["task_id"])
        if task.prepared:
            try:
                self._advance()
            except Exception as exc:
                return self._abort(task, str(exc), force_quarantine=True)
            return self.after_step(response.state["task_id"])
        return response

    def cancel(self, task_id: str, reason: str = "cancelled") -> NavigationResponse:
        task = self._task(task_id)
        if task.retired:
            return self._result(task)
        return self._abort(task, reason, reason_code="cancelled")

    def _abort(self, task: _Task, reason: str, *, reason_code: str = "execution_failure",
               force_quarantine: bool = False) -> NavigationResponse:
        key = task.request.task_id or task.request.request_id
        task.response = task.provider.fail(key, reason, reason_code=reason_code)
        try:
            task.adapter.request_stop(reason)
            task.adapter.before_step()
            feedback = task.adapter.after_step()
            if not isinstance(feedback, NavigationFeedback):
                raise SkillContractError("adapter returned invalid stop feedback")
        except Exception as exc:
            feedback = NavigationFeedback("failed", False, False, str(exc))
        self._feedback(task, feedback)
        self._retire(task, feedback, force_quarantine=force_quarantine)
        return self._result(task)

    def _retire(self, task: _Task, feedback: NavigationFeedback, *, force_quarantine: bool = False) -> None:
        if task.retired:
            return
        task.prepared, task.stop_pending, task.prepared_intent = False, False, None
        task.retired = True
        if _quiescent(feedback) and not force_quarantine:
            try:
                self._resources.release(task.lease)
                return
            except Exception:
                pass
        task.quarantined = True
        try:
            self._resources.quarantine(task.lease, feedback.reason or "navigation stop was not confirmed")
        except ResourceQuarantinedError:
            # A host recovery action may have quarantined this authentic lease
            # first. Preserve that occupancy and its original reason.
            pass

    @staticmethod
    def _feedback(task: _Task, feedback: NavigationFeedback) -> None:
        task.response = replace(task.response, private_evidence={**task.response.private_evidence,
            "controller_feedback": {"status": feedback.status, "stopped": feedback.stopped,
                                    "settled": feedback.settled, "reason": feedback.reason}})

    def _result(self, task: _Task) -> NavigationResponse:
        state = {**task.response.state, "stop_confirmed": task.retired and not task.quarantined,
                 "quarantined": task.quarantined}
        evidence = {**deepcopy(task.response.private_evidence), "execution_samples": deepcopy(task.samples),
                    "execution_sample_count": len(task.samples), "lease_id": task.lease.lease_id}
        return replace(task.response, state=state, private_evidence=evidence)

    def response(self, task_id: str) -> NavigationResponse:
        return self._result(self._task(task_id))


__all__ = ["NavigationControllerAdapter", "NavigationExecutor"]
