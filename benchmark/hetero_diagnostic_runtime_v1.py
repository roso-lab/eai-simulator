"""Reusable Isaac-free diagnostic runtime for heterogeneous robot teams.

The runtime is intentionally a logical track: it evaluates graph motion,
task/resource/communication causality and replay evidence on the registered
``0.02 s`` clock.  It does not provide Isaac physics, collision or sensor
qualification.  A scenario is described by :class:`EpisodeSpec`; adapters
only need to construct that data and algorithms implement
:class:`AlgorithmAdapter`.
"""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import heapq
import json
import math
from pathlib import Path
import random
from typing import Any, Mapping, Protocol, Sequence

from .checkpoint_store_v3 import read_registry_checkpoint, write_registry_checkpoint
from .replay_state_v3 import ReplayStateRegistryV3, SnapshotParticipantV3
from .transport_v3 import SharedChannelV3


CONTROL_DT_S = 0.02


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


@dataclass(frozen=True)
class SemanticNode:
    node_id: str
    domain: str = ""
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    semantics: tuple[str, ...] = ()


@dataclass(frozen=True)
class SemanticEdge:
    source: str
    target: str
    distance_m: float
    bidirectional: bool = True


class SemanticGraph:
    """Typed graph with deterministic Dijkstra shortest paths and bindings."""

    def __init__(self, nodes: Sequence[SemanticNode] = (), edges: Sequence[SemanticEdge] = ()):
        self.nodes: dict[str, SemanticNode] = {}
        self.adjacency: dict[str, list[tuple[str, float]]] = {}
        self.bindings: dict[str, list[str]] = {}
        for node in nodes:
            self.add_node(node)
        for edge in edges:
            self.add_edge(edge)

    def add_node(self, node: SemanticNode) -> None:
        if not node.node_id or node.node_id in self.nodes:
            raise ValueError("node_id must be unique and nonempty")
        if not node.domain:
            raise ValueError("node domain is required")
        if len(node.position) != 3 or not all(math.isfinite(float(x)) for x in node.position):
            raise ValueError("node position must contain three finite numbers")
        self.nodes[node.node_id] = node
        self.adjacency[node.node_id] = []
        for label in node.semantics:
            if label:
                self.bindings.setdefault(label, []).append(node.node_id)

    def add_edge(self, edge: SemanticEdge) -> None:
        if edge.source not in self.nodes or edge.target not in self.nodes or edge.distance_m <= 0:
            raise ValueError("edge endpoints must exist and distance must be positive")
        self.adjacency[edge.source].append((edge.target, float(edge.distance_m)))
        if edge.bidirectional:
            self.adjacency[edge.target].append((edge.source, float(edge.distance_m)))

    def bind(self, label: str, node_id: str) -> None:
        if node_id not in self.nodes or not label:
            raise ValueError("semantic binding references an unknown node")
        if node_id not in self.bindings.setdefault(label, []):
            self.bindings[label].append(node_id)

    def resolve(self, target: str, domain: str | None = None) -> tuple[str, ...]:
        if target in self.nodes:
            candidates = (target,)
        else:
            candidates = tuple(self.bindings.get(target, ()))
        if domain is not None:
            candidates = tuple(item for item in candidates if self.nodes[item].domain == domain)
        if not candidates:
            raise ValueError(f"unknown semantic target {target!r}")
        return tuple(sorted(candidates))

    def shortest(self, source: str, target: str) -> tuple[float, list[str]]:
        if source not in self.nodes or target not in self.nodes:
            raise ValueError("shortest path endpoints must exist")
        if source == target:
            return 0.0, [source]
        distances = {source: 0.0}
        previous: dict[str, str] = {}
        queue: list[tuple[float, str]] = [(0.0, source)]
        while queue:
            distance, node = heapq.heappop(queue)
            if distance != distances[node]:
                continue
            if node == target:
                break
            for neighbour, weight in sorted(self.adjacency[node]):
                candidate = distance + weight
                if candidate < distances.get(neighbour, math.inf):
                    distances[neighbour] = candidate
                    previous[neighbour] = node
                    heapq.heappush(queue, (candidate, neighbour))
        if target not in distances:
            raise ValueError(f"no route from {source!r} to {target!r}")
        path = [target]
        while path[-1] != source:
            path.append(previous[path[-1]])
        path.reverse()
        return distances[target], path


@dataclass(frozen=True)
class RobotSpec:
    robot_id: str
    domain: str
    start_node: str
    speed_mps: float = 1.0
    battery_capacity_m: float | None = None
    battery_per_meter: float = 1.0
    capabilities: tuple[str, ...] = ()
    battery_reserve_m: float = 0.0


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    kind: str
    targets: tuple[str, ...]
    prerequisites: tuple[str, ...] = ()
    required_domain: str | None = None
    resource_id: str | None = None
    service_ticks: int = 0
    deadline_tick: int | None = None
    distinct_group: str | None = None
    hold_on_completion: bool = False
    activation_tasks: tuple[str, ...] = ()
    activation_count: int = 0
    report_required: bool = False
    # Optional capability and object-protocol constraints.  These fields keep
    # the base runtime scene agnostic while allowing scenarios such as city_02
    # to model capability-gated pick/place and same-robot continuation.
    required_capabilities: tuple[str, ...] = ()
    continuation_group: str | None = None
    object_id: str | None = None
    object_action: str | None = None
    hold_release_task: str | None = None
    required_for_success: bool = True
    # Public announcement controls.  Hidden tasks remain in the evaluator's
    # ledger but are not exposed to an algorithm until their release
    # predicates are satisfied.  ``public_group``/``public_rank`` serialize
    # announcements within a stage, preventing future task counts from being
    # disclosed all at once.
    public: bool = True
    announce_after: tuple[str, ...] = ()
    public_group: str | None = None
    public_rank: int | None = None


@dataclass(frozen=True)
class ResourceSpec:
    resource_id: str
    node_id: str
    capacity: int = 1
    service_ticks: int = 1
    recharges: bool = False


@dataclass(frozen=True)
class EpisodeSpec:
    graph: SemanticGraph
    robots: tuple[RobotSpec, ...]
    tasks: tuple[TaskSpec, ...]
    resources: tuple[ResourceSpec, ...] = ()
    horizon_ticks: int = 10000
    dt_s: float = CONTROL_DT_S
    seed: int = 0
    communication_condition: str = "C0"
    availability_loss_task: str | None = None
    # Arrival/service reports are part of the task protocol. A bounded retry
    # budget keeps a lossy channel from silently becoming an out-of-band
    # oracle: the robot remains blocked while reports are pending, and the
    # episode closes fail-closed after the final failed attempt.
    report_retry_limit: int = 8
    report_retry_interval_ticks: int = 10
    # Initial object records are optional.  Each record is a mapping with an
    # ``object_id``, ``source`` and ``destination`` semantic labels.
    objects: tuple[Mapping[str, Any], ...] = ()


class AlgorithmAdapter(Protocol):
    algorithm_id: str

    def initialize(self, observation: Mapping[str, Any]) -> None: ...

    def decide(self, observation: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def snapshot(self) -> Mapping[str, Any]: ...

    def restore(self, snapshot: Mapping[str, Any]) -> None: ...


class GreedyGoalAlgorithm:
    """Deterministic nearest-goal baseline suitable for any graph scenario."""

    algorithm_id = "greedy_goal_v1"
    uses_distance = True

    def __init__(self) -> None:
        self.assignments: dict[str, str] = {}

    def initialize(self, observation: Mapping[str, Any]) -> None:
        self.assignments = {}

    def decide(self, observation: Mapping[str, Any]) -> Mapping[str, Any]:
        available = dict(observation.get("available_robots", {}))
        goals = list(observation.get("pending_goals", ()))
        result: dict[str, str] = {}
        used: set[str] = set()
        for goal in sorted(goals, key=lambda item: (item.get("task_id", ""), item.get("target", ""))):
            candidates = [r for r in available.values() if r["robot_id"] not in used
                          and (not goal.get("required_domain") or r["domain"] == goal["required_domain"])]
            if not candidates:
                continue
            selected = min(candidates, key=lambda r: (float(r.get("distance", math.inf)), r["robot_id"]))
            result[selected["robot_id"]] = str(goal["goal_key"])
            used.add(selected["robot_id"])
        self.assignments.update(result)
        return result

    def snapshot(self) -> Mapping[str, Any]:
        return {"assignments": dict(sorted(self.assignments.items()))}

    def restore(self, snapshot: Mapping[str, Any]) -> None:
        self.assignments = dict(snapshot.get("assignments", {}))


class TaskLedger:
    def __init__(self, tasks: Sequence[TaskSpec]):
        self.specs = {task.task_id: task for task in tasks}
        if len(self.specs) != len(tasks):
            raise ValueError("task IDs must be unique")
        self.status = {task.task_id: "pending" for task in tasks}
        self.remaining = {task.task_id: list(task.targets) for task in tasks}
        self.assigned = {}
        self.completed_tick: dict[str, int] = {}
        self.failed: dict[str, str] = {}
        self.group_members: dict[str, set[str]] = {}
        self.continuation_members: dict[str, str] = {}

    def eligible(self, task_id: str) -> bool:
        task = self.specs[task_id]
        return (self.status[task_id] == "pending"
                and all(self.status.get(item) == "completed" for item in task.prerequisites)
                and sum(self.status.get(item) == "completed" for item in task.activation_tasks) >= task.activation_count)

    @property
    def required_task_ids(self) -> tuple[str, ...]:
        return tuple(sorted(task_id for task_id, spec in self.specs.items() if spec.required_for_success))

    def required_complete(self) -> bool:
        return all(self.status[task_id] == "completed" for task_id in self.required_task_ids)

    def snapshot(self) -> dict[str, Any]:
        return {"status": dict(self.status), "remaining": deepcopy(self.remaining), "assigned": dict(self.assigned), "completed_tick": dict(self.completed_tick), "failed": dict(self.failed), "group_members": {k: sorted(v) for k, v in self.group_members.items()}, "continuation_members": dict(self.continuation_members)}

    def restore(self, state: Mapping[str, Any]) -> None:
        self.status = dict(state["status"])
        self.remaining = {k: list(v) for k, v in state["remaining"].items()}
        self.assigned = dict(state["assigned"])
        self.completed_tick = dict(state["completed_tick"])
        self.failed = dict(state["failed"])
        self.group_members = {k: set(v) for k, v in state.get("group_members", {}).items()}
        self.continuation_members = dict(state.get("continuation_members", {}))


class ResourceLedger:
    """Capacity-constrained resources with FIFO wait queues and accounting."""

    def __init__(self, resources: Sequence[ResourceSpec]):
        self.specs = {r.resource_id: r for r in resources}
        if len(self.specs) != len(resources):
            raise ValueError("resource IDs must be unique")
        for resource in resources:
            if resource.capacity <= 0 or resource.service_ticks <= 0:
                raise ValueError("resource capacity/service_ticks must be positive")
        self.active: dict[str, list[dict[str, Any]]] = {r.resource_id: [] for r in resources}
        self.queues: dict[str, deque[dict[str, Any]]] = {r.resource_id: deque() for r in resources}
        self.wait_ticks = 0
        self.contention_requests = 0
        self.total_services = 0

    def request(self, resource_id: str, robot_id: str, task_id: str, tick: int, service_ticks: int | None = None) -> bool:
        if resource_id not in self.specs:
            raise ValueError(f"unknown resource {resource_id!r}")
        item = {"robot_id": robot_id, "task_id": task_id, "requested_tick": tick,
                "service_ticks": int(service_ticks or self.specs[resource_id].service_ticks), "resource_id": resource_id}
        if len(self.active[resource_id]) < self.specs[resource_id].capacity:
            item["start_tick"] = tick
            item["end_tick"] = tick + item["service_ticks"]
            self.active[resource_id].append(item)
            self.total_services += 1
            return True
        self.queues[resource_id].append(item)
        self.contention_requests += 1
        return False

    def advance(self, tick: int) -> tuple[dict[str, Any], ...]:
        released: list[dict[str, Any]] = []
        for resource_id, active in self.active.items():
            for item in list(active):
                if item["end_tick"] <= tick:
                    active.remove(item)
                    item = dict(item)
                    item["release_tick"] = tick
                    released.append(item)
            while self.queues[resource_id] and len(active) < self.specs[resource_id].capacity:
                item = self.queues[resource_id].popleft()
                item["start_tick"] = tick
                item["end_tick"] = tick + item["service_ticks"]
                item["wait_ticks"] = tick - item["requested_tick"]
                self.wait_ticks += item["wait_ticks"]
                active.append(item)
                self.total_services += 1
        return tuple(released)

    def release(self, resource_id: str, robot_id: str, task_id: str, tick: int) -> bool:
        """Explicitly release one active lease and admit the next waiter."""
        if resource_id not in self.specs:
            raise ValueError(f"unknown resource {resource_id!r}")
        active = self.active[resource_id]
        for item in list(active):
            if item["robot_id"] == robot_id and item["task_id"] == task_id:
                active.remove(item)
                while self.queues[resource_id] and len(active) < self.specs[resource_id].capacity:
                    queued = self.queues[resource_id].popleft()
                    queued["start_tick"] = tick
                    queued["end_tick"] = tick + queued["service_ticks"]
                    queued["wait_ticks"] = tick - queued["requested_tick"]
                    self.wait_ticks += queued["wait_ticks"]
                    active.append(queued)
                    self.total_services += 1
                return True
        return False

    def snapshot(self) -> dict[str, Any]:
        return {"active": deepcopy(self.active), "queues": {k: list(v) for k, v in self.queues.items()}, "wait_ticks": self.wait_ticks, "contention_requests": self.contention_requests, "total_services": self.total_services}

    def restore(self, state: Mapping[str, Any]) -> None:
        self.active = {k: list(v) for k, v in state["active"].items()}
        self.queues = {k: deque(v) for k, v in state["queues"].items()}
        self.wait_ticks = int(state["wait_ticks"])
        self.contention_requests = int(state["contention_requests"])
        self.total_services = int(state["total_services"])


class CommunicationModel:
    """Shared-medium C0--C3 communication model backed by SharedChannelV3."""

    POLICIES = {
        "C0": dict(bits_per_tick=10**9, max_queue_bits=10**12, delay_ticks=0, loss_probability=0.0),
        "C1": dict(bits_per_tick=10**9, max_queue_bits=10**12, delay_ticks=2, loss_probability=0.0),
        "C2": dict(bits_per_tick=10**6, max_queue_bits=10**8, delay_ticks=2, loss_probability=0.15),
        "C3": dict(bits_per_tick=256, max_queue_bits=4096, delay_ticks=4, loss_probability=0.25),
    }

    def __init__(self, condition: str = "C0", *, seed: int = 0, **overrides: Any):
        if condition not in self.POLICIES:
            raise ValueError(f"unknown communication condition {condition!r}")
        policy = dict(self.POLICIES[condition]); policy.update(overrides)
        self.condition = condition
        self.channel = SharedChannelV3(seed=seed, **policy)
        self.delays: list[int] = []

    def send(self, *, message_id: str, sender_id: str, receiver_id: str, kind: str, payload: Mapping[str, Any], tick: int) -> Any:
        return self.channel.submit(message_id=message_id, sender_id=sender_id, receiver_id=receiver_id, kind=kind, payload=payload, tick=tick)

    def advance(self, tick: int) -> tuple[Any, ...]:
        frames = self.channel.advance(tick)
        for frame in frames:
            if frame.delivered_tick is not None:
                self.delays.append(frame.delivered_tick - frame.submitted_tick)
        return frames

    def receive(self, receiver_id: str, tick: int) -> tuple[Any, ...]:
        return self.channel.receive(receiver_id=receiver_id, tick=tick)

    def snapshot(self) -> dict[str, Any]:
        return {"condition": self.condition, "channel": self.channel.snapshot(), "delays": list(self.delays)}

    @classmethod
    def restore(cls, state: Mapping[str, Any]) -> "CommunicationModel":
        result = cls.__new__(cls)
        result.condition = state["condition"]
        result.channel = SharedChannelV3.restore(state["channel"])
        result.delays = list(state.get("delays", []))
        return result

    def metrics(self) -> dict[str, Any]:
        accounting = self.channel.accounting()
        accounting.update({"delay_ticks_total": sum(self.delays), "delay_ticks_mean": (sum(self.delays) / len(self.delays) if self.delays else 0.0), "loss_bits": accounting["dropped_bits"]})
        return accounting


@dataclass
class _RobotState:
    spec: RobotSpec
    node_id: str
    battery: float | None
    distance_m: float = 0.0
    route: list[str] = field(default_factory=list)
    route_index: int = 0
    edge_progress_m: float = 0.0
    task_id: str | None = None
    target_key: str | None = None
    status: str = "idle"
    resource_id: str | None = None
    report_frame_id: str | None = None
    report_attempts: int = 0
    report_next_tick: int | None = None
    hold_release_task: str | None = None


class DiagnosticRuntime:
    """Deterministic multi-robot logical runtime with replay and branching."""

    schema_version = "hetero.diagnostic.run.v1"

    def __init__(self, episode: EpisodeSpec, algorithm: AlgorithmAdapter | None = None):
        if abs(float(episode.dt_s) - CONTROL_DT_S) > 1e-12:
            raise ValueError("diagnostic runtime requires the registered 0.02 s clock")
        self.episode = episode
        if episode.report_retry_limit < 1 or episode.report_retry_interval_ticks < 1:
            raise ValueError("report retry limit/interval must be positive")
        self.algorithm = algorithm or GreedyGoalAlgorithm()
        self.rng = random.Random(episode.seed)
        self.clock_tick = 0
        for spec in episode.robots:
            if not spec.robot_id or not spec.domain or spec.speed_mps <= 0 or not math.isfinite(spec.speed_mps):
                raise ValueError("robot id/domain/speed must be valid")
            if spec.battery_per_meter < 0 or (spec.battery_capacity_m is not None and spec.battery_capacity_m < 0):
                raise ValueError("battery limits must be nonnegative")
        self.robot_states = {r.robot_id: _RobotState(r, r.start_node, r.battery_capacity_m) for r in episode.robots}
        for state in self.robot_states.values():
            if state.node_id not in episode.graph.nodes:
                raise ValueError(f"robot start node {state.node_id!r} is unknown")
        self.tasks = TaskLedger(episode.tasks)
        self.resources = ResourceLedger(episode.resources)
        self.communication = CommunicationModel(episode.communication_condition, seed=episode.seed)
        self.events: list[dict[str, Any]] = []
        self.reallocation_count = 0
        self._message_counter = 0
        self.terminal = False
        self.success = False
        self.failure_reason = ""
        self.lost_robots: set[str] = set()
        self.availability_events: list[dict[str, Any]] = []
        self.report_wait_ticks = 0
        self.notification_submitted = 0
        self.notification_delivered = 0
        self.object_state: dict[str, dict[str, Any]] = {}
        # Public task announcements are separate from the evaluator ledger.
        # This prevents hidden future obligations from entering an algorithm's
        # observation while retaining a complete internal oracle state.
        self._announced_tasks: set[str] = set()
        for item in episode.objects:
            object_id = str(item.get("object_id", ""))
            if not object_id:
                raise ValueError("object records require object_id")
            if object_id in self.object_state:
                raise ValueError(f"duplicate object_id {object_id!r}")
            self.object_state[object_id] = {
                "object_id": object_id,
                "source": str(item.get("source", "")),
                "destination": str(item.get("destination", "")),
                "status": "available",
                "held_by": None,
            }
        # This becomes true only after an explicit paired replay comparison;
        # a normal run must never imply that replay was checked.
        self._replay_equivalent = False
        self.registry = self._build_registry()
        self.algorithm.initialize(self._observation())
        self._initial_snapshot = self.registry.capture()

    @property
    def tick(self) -> int:
        """Current deterministic control tick (one tick is 20 ms)."""
        return self.clock_tick

    @property
    def sim_time_s(self) -> float:
        return self.clock_tick * CONTROL_DT_S

    def _build_registry(self) -> ReplayStateRegistryV3:
        registry = ReplayStateRegistryV3()
        for participant, capture, restore in (
            ("world", self._world_snapshot, self._world_restore),
            ("clock", lambda: {"tick": self.clock_tick}, lambda s: setattr(self, "clock_tick", int(s["tick"]))),
            ("controller", lambda: {"robots": self._robot_snapshot()}, lambda s: self._robot_restore(s["robots"])),
            ("skill", lambda: {"reallocation_count": self.reallocation_count, "message_counter": self._message_counter, "replay_equivalent": self._replay_equivalent, "lost_robots": sorted(self.lost_robots), "availability_events": deepcopy(self.availability_events), "report_wait_ticks": self.report_wait_ticks, "notification_submitted": self.notification_submitted, "notification_delivered": self.notification_delivered, "object_state": deepcopy(self.object_state), "announced_tasks": sorted(self._announced_tasks)}, self._skill_restore),
            ("task", self.tasks.snapshot, self.tasks.restore),
            ("algorithm", self.algorithm.snapshot, self.algorithm.restore),
            ("transport", lambda: self.communication.snapshot(), self._transport_restore),
            ("locks", self.resources.snapshot, self.resources.restore),
            ("events", lambda: {"events": deepcopy(self.events)}, lambda s: setattr(self, "events", deepcopy(s["events"]))),
            ("rng", lambda: {"state": self.rng.getstate()}, lambda s: self.rng.setstate(self._tupleize(s["state"]))),
        ):
            registry.register(SnapshotParticipantV3(participant, "v1", capture, restore, capture))
        return registry

    @staticmethod
    def _tupleize(value: Any) -> Any:
        return tuple(DiagnosticRuntime._tupleize(v) for v in value) if isinstance(value, list) else value

    def _transport_restore(self, state: Mapping[str, Any]) -> None:
        self.communication = CommunicationModel.restore(state)

    def _skill_restore(self, state: Mapping[str, Any]) -> None:
        self.reallocation_count = int(state["reallocation_count"])
        self._message_counter = int(state.get("message_counter", 0))
        self._replay_equivalent = bool(state.get("replay_equivalent", False))
        self.lost_robots = set(state.get("lost_robots", ()))
        self.availability_events = deepcopy(state.get("availability_events", ()))
        self.report_wait_ticks = int(state.get("report_wait_ticks", 0))
        self.notification_submitted = int(state.get("notification_submitted", 0))
        self.notification_delivered = int(state.get("notification_delivered", 0))
        self.object_state = deepcopy(state.get("object_state", self.object_state))
        self._announced_tasks = set(state.get("announced_tasks", ()))

    def _world_snapshot(self) -> dict[str, Any]:
        return {"terminal": self.terminal, "success": self.success, "failure_reason": self.failure_reason}

    def _world_restore(self, state: Mapping[str, Any]) -> None:
        self.terminal = bool(state["terminal"]); self.success = bool(state["success"]); self.failure_reason = str(state["failure_reason"])

    def _robot_snapshot(self) -> dict[str, Any]:
        return {rid: {"node_id": s.node_id, "battery": s.battery, "distance_m": s.distance_m, "route": list(s.route), "route_index": s.route_index, "edge_progress_m": s.edge_progress_m, "task_id": s.task_id, "target_key": s.target_key, "status": s.status, "resource_id": s.resource_id, "report_frame_id": s.report_frame_id, "report_attempts": s.report_attempts, "report_next_tick": s.report_next_tick, "hold_release_task": s.hold_release_task} for rid, s in sorted(self.robot_states.items())}

    def _robot_restore(self, state: Mapping[str, Any]) -> None:
        for rid, payload in state.items():
            robot = self.robot_states[rid]
            for key, value in payload.items():
                setattr(robot, key, value)

    def _observation(self) -> dict[str, Any]:
        pending = []
        for task_id in sorted(self.tasks.specs):
            task = self.tasks.specs[task_id]
            if not self._task_public(task_id):
                continue
            if not self.tasks.eligible(task_id):
                continue
            if task.kind == "event":
                continue
            if task_id not in self._announced_tasks:
                self._announced_tasks.add(task_id)
                self.events.append({"tick": self.clock_tick, "kind": "task_announced", "task_id": task_id,
                                    "stage": task.public_group or task.kind})
            for target in self.tasks.remaining[task_id]:
                target_nodes = self.episode.graph.resolve(target, task.required_domain)
                target_position = self.episode.graph.nodes[target_nodes[0]].position
                target_domains = {self.episode.graph.nodes[node_id].domain for node_id in target_nodes}
                eligible = []
                continuation_robot = self.tasks.continuation_members.get(task.continuation_group or "") if task.continuation_group else None
                for state in self.robot_states.values():
                    if task.required_domain and state.spec.domain != task.required_domain:
                        continue
                    # A semantic target may be bound only in one domain (for
                    # example an aerial-only coverage area).  Keep allocation
                    # heterogeneous, but never advertise such a goal to a
                    # robot that cannot resolve a route to it.
                    if task.kind == "coverage" and state.spec.domain not in target_domains:
                        continue
                    if task.required_capabilities and not set(task.required_capabilities).issubset(state.spec.capabilities):
                        continue
                    if task.distinct_group and state.spec.robot_id in self.tasks.group_members.get(task.distinct_group, set()):
                        continue
                    if continuation_robot and state.spec.robot_id != continuation_robot:
                        continue
                    eligible.append(state.spec.robot_id)
                pending.append({"task_id": task_id, "target": target, "goal_key": f"{task_id}:{target}", "required_domain": task.required_domain, "target_position": tuple(target_position), "kind": task.kind, "distinct_group": task.distinct_group, "required_capabilities": tuple(task.required_capabilities), "continuation_group": task.continuation_group, "object_id": task.object_id, "object_action": task.object_action, "eligible_robots": sorted(eligible)})
        # Hidden obligations are represented as repeatable action opportunities.
        # The evaluator retains their exact count and IDs, while the algorithm
        # only learns that another service/response action is currently
        # available.  This prevents future quota leakage and still permits
        # several robots to contend for one capacity-one resource.
        if self._has_hidden_action("recover") and self._has_idle_domain("ground"):
            pending.append({"task_id": "resource", "target": "recover", "goal_key": "resource:recover",
                            "required_domain": "ground", "target_position": self._resource_position("recover"),
                            "kind": "resource_action", "distinct_group": None,
                            "required_capabilities": (), "continuation_group": None,
                            "object_id": None, "object_action": None})
        if self._has_hidden_action("response") and self._has_idle_domain("ground"):
            pending.append({"task_id": "response", "target": "accident", "goal_key": "response:accident",
                            "required_domain": "ground", "target_position": self._target_position("accident", "ground"),
                            "kind": "response_action", "distinct_group": None,
                            "required_capabilities": (), "continuation_group": None,
                            "object_id": None, "object_action": None})
        available = {}
        for state in self.robot_states.values():
            if state.status != "idle":
                continue
            distance = math.inf
            if pending and bool(getattr(self.algorithm, "uses_distance", False)):
                for goal in pending:
                    try:
                        targets = self.episode.graph.resolve(goal["target"], goal["required_domain"] or state.spec.domain)
                        distance = min(self.episode.graph.shortest(state.node_id, node)[0] for node in targets)
                    except ValueError:
                        continue
            available[state.spec.robot_id] = {"robot_id": state.spec.robot_id, "domain": state.spec.domain, "distance": distance, "position": tuple(self.episode.graph.nodes[state.node_id].position), "capabilities": tuple(state.spec.capabilities),
                                              "battery_remaining_m": state.battery,
                                              "battery_reserve_m": state.spec.battery_reserve_m}
        resources = []
        for resource_id, spec in sorted(self.resources.specs.items()):
            node = self.episode.graph.nodes[spec.node_id]
            resources.append({"resource_id": resource_id, "position": tuple(node.position),
                              "capacity": spec.capacity,
                              "occupied": len(self.resources.active[resource_id]),
                              "queued": len(self.resources.queues[resource_id])})
        return {"tick": self.clock_tick, "pending_goals": pending, "available_robots": available,
                "resources": resources,
                "announcement_log": sorted(self._announced_tasks)}

    def _target_position(self, target: str, domain: str | None = None) -> tuple[float, float, float]:
        nodes = self.episode.graph.resolve(target, domain)
        return tuple(self.episode.graph.nodes[nodes[0]].position)

    def _resource_position(self, label: str) -> tuple[float, float, float]:
        resources = [r for r in self.resources.specs.values()
                     if label in self.episode.graph.nodes[r.node_id].semantics
                     or label in self.episode.graph.bindings and r.node_id in self.episode.graph.bindings[label]]
        if resources:
            return tuple(self.episode.graph.nodes[sorted(resources, key=lambda r: r.resource_id)[0].node_id].position)
        return self._target_position(label, "ground")

    def _has_hidden_action(self, kind: str) -> bool:
        candidates = [task for task in self.tasks.specs.values()
                      if task.kind == kind and not task.public and self._task_released(task)
                      and self.tasks.eligible(task.task_id)]
        return bool(candidates)

    def _has_idle_domain(self, domain: str) -> bool:
        return any(state.status == "idle" and state.spec.domain == domain
                   for state in self.robot_states.values())

    def _task_public(self, task_id: str) -> bool:
        """Return whether a task is currently announced to the algorithm.

        ``announce_after`` gates stage transitions.  A ``public_group`` with
        ranks reveals only the next member, which keeps serialized resource
        service obligations from disclosing their future count.  Unranked
        groups are announced together once their gate is complete.
        """
        task = self.tasks.specs[task_id]
        if not task.public or not self._task_released(task):
            return False
        if task.public_group and task.public_rank is not None:
            completed = sum(1 for other in self.tasks.specs.values()
                            if other.public_group == task.public_group
                            and other.public_rank is not None
                            and self.tasks.status.get(other.task_id) == "completed")
            return task.public_rank <= completed + 1
        return True

    def _task_released(self, task: TaskSpec) -> bool:
        return all(self.tasks.status.get(item) == "completed" for item in task.announce_after)

    def _update_event_triggers(self) -> None:
        """Close evaluator-owned triggers without assigning them to robots."""
        for task_id, task in self.tasks.specs.items():
            if task.kind != "event" or self.tasks.status[task_id] == "completed":
                continue
            completed = sum(self.tasks.status.get(item) == "completed" for item in task.activation_tasks)
            if completed >= task.activation_count:
                self.tasks.status[task_id] = "completed"
                self.tasks.completed_tick[task_id] = self.clock_tick
                self.events.append({"tick": self.clock_tick, "kind": "incident_triggered", "task_id": task_id,
                                    "completed_activation_count": completed})

    def _submit_report(self, robot: _RobotState) -> None:
        """Send the arrival/service report; task completion waits for delivery."""
        if robot.report_attempts >= self.episode.report_retry_limit:
            self._fail(f"notification_timeout:{robot.task_id}:{robot.spec.robot_id}")
            return
        self._message_counter += 1
        message_id = f"report-{self._message_counter:06d}"
        frame = self.communication.send(
            message_id=message_id,
            sender_id=robot.spec.robot_id,
            receiver_id="evaluator",
            kind="task_report",
            payload={"task_id": robot.task_id, "target": robot.target_key,
                     "robot_id": robot.spec.robot_id, "attempt": robot.report_attempts + 1},
            tick=self.clock_tick,
        )
        robot.report_frame_id = message_id
        robot.report_attempts += 1
        robot.report_next_tick = self.clock_tick + self.episode.report_retry_interval_ticks
        self.notification_submitted += 1
        self.events.append({"tick": self.clock_tick, "kind": "notification_submitted",
                            "robot_id": robot.spec.robot_id, "task_id": robot.task_id,
                            "message_id": message_id, "attempt": robot.report_attempts,
                            "transport_status": frame.status})

    def _complete_report(self, robot: _RobotState) -> None:
        task_id, target = robot.task_id, robot.target_key
        if task_id is None or target is None:
            return
        task = self.tasks.specs[task_id]
        remaining = self.tasks.remaining[task_id]
        if target in remaining:
            remaining.remove(target)
        if not remaining:
            self.tasks.status[task_id] = "completed"
            self.tasks.completed_tick[task_id] = self.clock_tick
            self.events.append({"tick": self.clock_tick, "kind": "task_completed", "robot_id": robot.spec.robot_id, "task_id": task_id, "target": target})
        robot.task_id = robot.target_key = robot.resource_id = robot.report_frame_id = None
        robot.report_attempts = 0
        robot.report_next_tick = None
        robot.route = []
        robot.route_index = 0
        robot.edge_progress_m = 0.0
        if self._should_hold(robot, task):
            robot.hold_release_task = task.hold_release_task
            robot.status = "hold"
            self.events.append({"tick": self.clock_tick, "kind": "robot_hold_started", "robot_id": robot.spec.robot_id, "release_task": task.hold_release_task})
        else:
            robot.status = "idle"
        self.notification_delivered += 1
        self._release_holds(task_id)
        if task_id == self.episode.availability_loss_task and robot.spec.domain == "aerial":
            robot.status = "lost"
            self.lost_robots.add(robot.spec.robot_id)
            event = {"tick": self.clock_tick, "kind": "availability_loss", "robot_id": robot.spec.robot_id,
                     "lost_function": "communication", "trigger_task": task_id}
            self.availability_events.append(event)
            self.events.append(event)

    def _process_reports(self, frames: Sequence[Any]) -> None:
        for frame in frames:
            if frame.receiver_id != "evaluator" or frame.kind != "task_report":
                continue
            robot_id = str(frame.payload.get("robot_id", ""))
            robot = self.robot_states.get(robot_id)
            if robot is None or robot.status != "report_wait" or robot.report_frame_id != frame.message_id:
                continue
            self.events.append({"tick": self.clock_tick, "kind": "notification_delivered",
                                "robot_id": robot_id, "task_id": robot.task_id,
                                "message_id": frame.message_id,
                                "delay_ticks": frame.delivered_tick - frame.submitted_tick})
            self._complete_report(robot)

    def _assign(self) -> None:
        decision = self.algorithm.decide(self._observation())
        for robot_id, goal_key in sorted(dict(decision).items()):
            if robot_id not in self.robot_states or self.robot_states[robot_id].status != "idle":
                continue
            if ":" not in goal_key:
                continue
            task_id, target = goal_key.split(":", 1)
            # Resolve an abstract public action to one hidden evaluator task.
            # The algorithm never receives the concrete future ID or quota.
            if task_id == "resource" and target == "recover":
                candidates = [task for task in self.tasks.specs.values()
                              if task.kind == "recover" and not task.public
                              and self.tasks.eligible(task.task_id)]
                if not candidates:
                    continue
                task_id = sorted(candidates, key=lambda t: t.task_id)[0].task_id
                target = "recover"
            elif task_id == "response" and target == "accident":
                candidates = [task for task in self.tasks.specs.values()
                              if task.kind == "response" and not task.public
                              and self.tasks.eligible(task.task_id)]
                if not candidates:
                    continue
                task_id = sorted(candidates, key=lambda t: t.task_id)[0].task_id
                target = "accident"
            if task_id not in self.tasks.specs or target not in self.tasks.remaining[task_id] or not self.tasks.eligible(task_id):
                continue
            robot = self.robot_states[robot_id]; task = self.tasks.specs[task_id]
            if task.distinct_group and robot_id in self.tasks.group_members.setdefault(task.distinct_group, set()):
                self.events.append({"tick": self.clock_tick, "kind": "invalid_assignment", "robot_id": robot_id, "task_id": task_id, "reason": "distinct_group_robot_reuse"})
                continue
            if task.required_capabilities and not set(task.required_capabilities).issubset(robot.spec.capabilities):
                self.events.append({"tick": self.clock_tick, "kind": "invalid_assignment", "robot_id": robot_id, "task_id": task_id, "reason": "missing_required_capability"})
                continue
            if task.continuation_group:
                owner = self.tasks.continuation_members.get(task.continuation_group)
                if owner is not None and owner != robot_id:
                    self.events.append({"tick": self.clock_tick, "kind": "invalid_assignment", "robot_id": robot_id, "task_id": task_id, "reason": "continuation_owner_mismatch"})
                    continue
            try:
                candidates = self.episode.graph.resolve(target, task.required_domain or robot.spec.domain)
                candidates = [node for node in candidates if self.episode.graph.nodes[node].domain == robot.spec.domain]
            except ValueError:
                # A shared semantic task can be bound in another domain (for
                # example an aerial-only coverage region).  Treat an
                # incompatible assignment as a rejected proposal so another
                # eligible robot can claim it; reserve terminal failure for a
                # genuinely unknown semantic target.
                try:
                    self.episode.graph.resolve(target)
                except ValueError:
                    self._fail(f"unreachable_target:{task_id}:{target}")
                    continue
                self.events.append({"tick": self.clock_tick, "kind": "invalid_assignment",
                                    "robot_id": robot_id, "task_id": task_id,
                                    "reason": "no_eligible_robot_domain"})
                continue
            if not candidates:
                self.events.append({"tick": self.clock_tick, "kind": "invalid_assignment",
                                    "robot_id": robot_id, "task_id": task_id,
                                    "reason": "no_eligible_robot_domain"})
                continue
            try:
                destination = min(candidates, key=lambda node: (self.episode.graph.shortest(robot.node_id, node)[0], node))
                _, route = self.episode.graph.shortest(robot.node_id, destination)
            except ValueError:
                self._fail(f"no_route:{robot_id}:{task_id}:{target}")
                continue
            robot.task_id, robot.target_key, robot.route, robot.route_index = task_id, target, route, 0
            robot.edge_progress_m, robot.status = 0.0, "moving"
            self.tasks.status[task_id] = "running"
            self.tasks.assigned[task_id] = robot_id
            if task.continuation_group:
                self.tasks.continuation_members.setdefault(task.continuation_group, robot_id)
            if task.distinct_group:
                self.tasks.group_members.setdefault(task.distinct_group, set()).add(robot_id)
            self.events.append({"tick": self.clock_tick, "kind": "task_assigned", "robot_id": robot_id, "task_id": task_id, "target": target})
            # A robot can already occupy a shared semantic node (for example,
            # multiple response obligations at one accident point).  Complete
            # that zero-distance goal in the same deterministic tick instead
            # of leaving a one-node route permanently in ``moving`` state.
            if len(route) == 1:
                self.events.append({"tick": self.clock_tick, "kind": "goal_reached", "robot_id": robot_id, "target": target})
                self._arrive(robot)

    def _arrive(self, robot: _RobotState) -> None:
        task_id, target = robot.task_id, robot.target_key
        if not task_id or target is None:
            robot.status = "idle"; return
        task = self.tasks.specs[task_id]
        if task.resource_id:
            accepted = self.resources.request(task.resource_id, robot.spec.robot_id, task_id, self.clock_tick, task.service_ticks or None)
            robot.resource_id, robot.status = task.resource_id, ("servicing" if accepted else "resource_wait")
            self.events.append({"tick": self.clock_tick, "kind": "resource_request", "robot_id": robot.spec.robot_id, "resource_id": task.resource_id, "accepted": accepted})
        else:
            self._complete_target(robot)

    def _complete_target(self, robot: _RobotState) -> None:
        task_id, target = robot.task_id, robot.target_key
        if task_id is None or target is None:
            return
        task = self.tasks.specs[task_id]
        if task.object_id and task.object_action:
            obj = self.object_state.get(task.object_id)
            if obj is None:
                self._fail(f"unknown_object:{task.object_id}"); return
            if task.object_action == "pick":
                if obj["status"] != "available" or obj["source"] != target:
                    self._fail(f"invalid_pick:{task.object_id}:{target}"); return
                obj["status"], obj["held_by"] = "held", robot.spec.robot_id
                self.events.append({"tick": self.clock_tick, "kind": "object_picked", "robot_id": robot.spec.robot_id, "object_id": task.object_id, "target": target})
            elif task.object_action == "place":
                if obj["status"] != "held" or obj["held_by"] != robot.spec.robot_id or obj["destination"] != target:
                    self._fail(f"invalid_place:{task.object_id}:{target}"); return
                obj["status"], obj["held_by"] = "placed", None
                self.events.append({"tick": self.clock_tick, "kind": "object_placed", "robot_id": robot.spec.robot_id, "object_id": task.object_id, "target": target})
            else:
                self._fail(f"unknown_object_action:{task.object_action}"); return
        if task.report_required:
            robot.status = "report_wait"
            self._submit_report(robot)
            return
        remaining = self.tasks.remaining[task_id]
        if target in remaining:
            remaining.remove(target)
        if not remaining:
            self.tasks.status[task_id] = "completed"; self.tasks.completed_tick[task_id] = self.clock_tick
            self.events.append({"tick": self.clock_tick, "kind": "task_completed", "robot_id": robot.spec.robot_id, "task_id": task_id, "target": target})
        else:
            # A multi-target coverage/inspection task exposes the next
            # obligation only after the current robot has released its goal.
            self.tasks.status[task_id] = "pending"
        robot.task_id = robot.target_key = robot.resource_id = None
        robot.route = []; robot.route_index = 0; robot.edge_progress_m = 0.0
        if self._should_hold(robot, task):
            robot.hold_release_task = task.hold_release_task
            robot.status = "hold"
            self.events.append({"tick": self.clock_tick, "kind": "robot_hold_started", "robot_id": robot.spec.robot_id, "release_task": task.hold_release_task})
        else:
            robot.status = "idle"
        self._release_holds(task_id)

    def _release_holds(self, completed_task_id: str) -> None:
        for held in self.robot_states.values():
            if held.status == "hold" and held.hold_release_task == completed_task_id:
                held.status = "idle"
                held.hold_release_task = None
                self.events.append({"tick": self.clock_tick, "kind": "robot_hold_released", "robot_id": held.spec.robot_id, "release_task": completed_task_id})

    def _should_hold(self, robot: _RobotState, task: TaskSpec) -> bool:
        """A discoverer waits only when it cannot perform the response itself."""
        if not task.hold_on_completion:
            return False
        if not task.hold_release_task:
            return True
        response = self.tasks.specs.get(task.hold_release_task)
        if response is None:
            return True
        return not set(response.required_capabilities).issubset(robot.spec.capabilities)

    def _move(self, robot: _RobotState) -> None:
        if robot.status != "moving":
            return
        remaining_m = robot.spec.speed_mps * CONTROL_DT_S
        while remaining_m > 0 and robot.route_index + 1 < len(robot.route):
            source, target = robot.route[robot.route_index], robot.route[robot.route_index + 1]
            edge_length, _ = self.episode.graph.shortest(source, target)
            # shortest(source,target) is the direct edge in a valid route; use
            # the adjacency edge when parallel edges exist.
            edge_length = next(weight for node, weight in self.episode.graph.adjacency[source] if node == target)
            left = edge_length - robot.edge_progress_m
            travelled = min(left, remaining_m)
            robot.edge_progress_m += travelled; robot.distance_m += travelled; remaining_m -= travelled
            if robot.battery is not None:
                robot.battery -= travelled * robot.spec.battery_per_meter
                if robot.battery < -1e-9:
                    self._fail("battery_exhausted"); return
            if robot.edge_progress_m + 1e-12 >= edge_length:
                robot.node_id = target; robot.route_index += 1; robot.edge_progress_m = 0.0
                if robot.route_index + 1 == len(robot.route):
                    self.events.append({"tick": self.clock_tick, "kind": "goal_reached", "robot_id": robot.spec.robot_id, "target": robot.target_key})
                    self._arrive(robot); return

    def _fail(self, reason: str) -> None:
        self.terminal = True; self.success = False; self.failure_reason = reason
        self.events.append({"tick": self.clock_tick, "kind": "episode_failed", "reason": reason})

    def step(self) -> None:
        if self.terminal:
            return
        self._update_event_triggers()
        for task_id, task in self.tasks.specs.items():
            if task.deadline_tick is not None and self.tasks.status[task_id] != "completed" and self.clock_tick > task.deadline_tick:
                self._fail(f"deadline_exceeded:{task_id}")
                return
        # Complete resource services that ended this tick and release robots.
        for item in self.resources.advance(self.clock_tick):
            robot = self.robot_states[item["robot_id"]]
            if robot.status in {"servicing", "resource_wait"} and robot.task_id == item["task_id"]:
                resource_id = robot.resource_id
                self._complete_target(robot)
                resource = self.resources.specs.get(resource_id or "")
                if resource is not None and (resource.recharges or str(resource_id).startswith("recover")):
                    robot.battery = robot.spec.battery_capacity_m
                self.events.append({"tick": self.clock_tick, "kind": "resource_released", "robot_id": robot.spec.robot_id, "resource_id": resource_id})
        for robot in self.robot_states.values():
            if robot.status == "resource_wait":
                for active in self.resources.active.get(robot.resource_id or "", ()):
                    if active["robot_id"] == robot.spec.robot_id and active["task_id"] == robot.task_id:
                        robot.status = "servicing"
            self._move(robot)
        # Allocation decisions only change when an idle robot exists.  Avoid
        # rebuilding task packages and running graph searches on every 20 ms
        # tick while the fleet is in transit.
        if any(state.status == "idle" for state in self.robot_states.values()):
            self._assign()
        delivered = self.communication.advance(self.clock_tick)
        self._process_reports(delivered)
        for robot in self.robot_states.values():
            if robot.status == "report_wait":
                self.report_wait_ticks += 1
                if robot.report_next_tick is not None and self.clock_tick >= robot.report_next_tick:
                    self._submit_report(robot)
                    if self.terminal:
                        return
        self.clock_tick += 1
        if self.tasks.required_complete():
            self.terminal = True; self.success = True
            self.events.append({"tick": self.clock_tick, "kind": "episode_succeeded"})
        elif self.clock_tick >= self.episode.horizon_ticks:
            self._fail("horizon_exceeded")

    def reset(self) -> None:
        """Restore the deterministic post-initialization episode state."""
        self.registry.restore(deepcopy(self._initial_snapshot))

    def run(self, *, max_ticks: int | None = None) -> dict[str, Any]:
        limit = self.episode.horizon_ticks if max_ticks is None else min(self.episode.horizon_ticks, self.clock_tick + max_ticks)
        while not self.terminal and self.clock_tick < limit:
            self.step()
        return self.metrics()

    def send_message(self, sender_id: str, receiver_id: str, kind: str, payload: Mapping[str, Any]) -> Any:
        self._message_counter += 1
        return self.communication.send(message_id=f"m{self._message_counter:06d}", sender_id=sender_id, receiver_id=receiver_id, kind=kind, payload=payload, tick=self.clock_tick)

    def snapshot(self) -> dict[str, Any]:
        return self.registry.capture()

    def checkpoint(self, path: str | Path | None = None, *, metadata: Mapping[str, str] | None = None) -> dict[str, Any]:
        snapshot = self.snapshot(); receipt = {"schema_version": self.schema_version, "tick": self.clock_tick, "snapshot": snapshot, "digest": _digest(snapshot)}
        if path is not None:
            frozen = dict(metadata or {"schema": self.schema_version, "tick": str(self.clock_tick)})
            receipt["artifact_digest"] = write_registry_checkpoint(path, snapshot, metadata=frozen)
            receipt["metadata"] = frozen
        return receipt

    def restore(self, snapshot: Mapping[str, Any]) -> None:
        self.registry.restore(snapshot)

    def restore_checkpoint(self, path: str | Path, *, digest: str, metadata: Mapping[str, str]) -> None:
        self.restore(read_registry_checkpoint(path, expected_digest=digest, expected_metadata=metadata))

    def fork(self, checkpoint: Mapping[str, Any] | None = None) -> "DiagnosticRuntime":
        # Adapters may carry constructor configuration; deepcopy preserves the
        # deterministic policy object without imposing a zero-argument ctor.
        clone = DiagnosticRuntime(self.episode, algorithm=deepcopy(self.algorithm))
        clone.restore((checkpoint or self.snapshot()).get("snapshot", checkpoint or self.snapshot()))
        return clone

    def replay(self, checkpoint: Mapping[str, Any], *, ticks: int | None = None) -> dict[str, Any]:
        first = self.fork(checkpoint)
        second = self.fork(checkpoint)
        first_result = first.run(max_ticks=ticks)
        second_result = second.run(max_ticks=ticks)
        first_digest = _digest(first.events)
        second_digest = _digest(second.events)
        # Compare the complete deterministic result, including event ledger,
        # after removing the self-referential replay marker.
        first_result["replay_digest"] = first_digest
        second_result["replay_digest"] = second_digest
        equivalent = first_digest == second_digest and _digest(first_result) == _digest(second_result)
        self._replay_equivalent = equivalent
        first_result["replay_equivalent"] = equivalent
        return first_result

    # Explicit aliases keep the public vocabulary close to the benchmark
    # protocol (``branch``/``replay_digest``) while retaining the concise
    # ``fork``/``replay`` methods used by Python callers.
    def branch(self, checkpoint: Mapping[str, Any] | None = None) -> "DiagnosticRuntime":
        return self.fork(checkpoint)

    def replay_digest(self, checkpoint: Mapping[str, Any], *, ticks: int | None = None) -> str:
        return str(self.replay(checkpoint, ticks=ticks)["replay_digest"])

    def metrics(self) -> dict[str, Any]:
        required_ids = self.tasks.required_task_ids
        completed = sum(self.tasks.status[item] == "completed" for item in required_ids)
        coverage_tasks = [task_id for task_id, spec in self.tasks.specs.items() if spec.kind in {"coverage", "inspection"}]
        coverage_done = sum(self.tasks.status[item] == "completed" for item in coverage_tasks)
        comm = self.communication.metrics()
        replay_digest = _digest(self.events)
        batteries = {rid: state.battery for rid, state in sorted(self.robot_states.items()) if state.battery is not None}
        # Submitted frames include dropped and superseded retries.  Pending is
        # a live protocol state and therefore counts robots still blocked in
        # report_wait, rather than subtracting historical transport totals.
        notification_pending = sum(state.status == "report_wait" for state in self.robot_states.values())
        return {"schema_version": self.schema_version, "success": self.success, "failure_reason": self.failure_reason, "tick": self.clock_tick, "makespan_s": self.clock_tick * CONTROL_DT_S, "task_completion_rate": completed / len(required_ids) if required_ids else 1.0, "coverage_fraction": coverage_done / len(coverage_tasks) if coverage_tasks else 1.0, "completed_task_ids": sorted(task_id for task_id, status in self.tasks.status.items() if status == "completed"), "task_status": dict(sorted(self.tasks.status.items())), "completed_ticks": dict(sorted(self.tasks.completed_tick.items())), "resource_wait_ticks": self.resources.wait_ticks, "resource_contention_requests": self.resources.contention_requests, "recover_services": self.resources.total_services, "battery_remaining_m": batteries, "min_battery_remaining_m": min(batteries.values()) if batteries else None, "communication_bits": comm["transmitted_bits"], "communication_delay_ticks": comm["delay_ticks_total"], "communication_loss_bits": comm["loss_bits"], "notification_submitted": self.notification_submitted, "notification_delivered": self.notification_delivered, "notification_pending": notification_pending, "report_wait_ticks": self.report_wait_ticks, "lost_robots": sorted(self.lost_robots), "availability_events": deepcopy(self.availability_events), "reallocation_count": self.reallocation_count, "replay_digest": replay_digest, "replay_equivalent": self._replay_equivalent, "paper_score_eligible": False, "D1_eligible": bool(self.success), "D2_eligible": False, "D3_eligible": bool(self.success and self.resources.contention_requests >= 0), "D4_eligible": False, "D5_eligible": False, "D6_eligible": False, "eligibility_reasons": {"D2": "paired C0-C3 retention directory required", "D4": "registered availability-loss branch required", "D5": "certified cross-scenario family required", "D6": "independent calibration reference required"}, "events": deepcopy(self.events), "assignment": dict(self.tasks.assigned)}


__all__ = ["CONTROL_DT_S", "SemanticNode", "SemanticEdge", "SemanticGraph", "RobotSpec", "TaskSpec", "ResourceSpec", "EpisodeSpec", "AlgorithmAdapter", "GreedyGoalAlgorithm", "TaskLedger", "ResourceLedger", "CommunicationModel", "DiagnosticRuntime"]
