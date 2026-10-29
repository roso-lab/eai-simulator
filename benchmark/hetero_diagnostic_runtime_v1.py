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


class AlgorithmAdapter(Protocol):
    algorithm_id: str

    def initialize(self, observation: Mapping[str, Any]) -> None: ...

    def decide(self, observation: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def snapshot(self) -> Mapping[str, Any]: ...

    def restore(self, snapshot: Mapping[str, Any]) -> None: ...


class GreedyGoalAlgorithm:
    """Deterministic nearest-goal baseline suitable for any graph scenario."""

    algorithm_id = "greedy_goal_v1"

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

    def eligible(self, task_id: str) -> bool:
        task = self.specs[task_id]
        return (self.status[task_id] == "pending"
                and all(self.status.get(item) == "completed" for item in task.prerequisites)
                and sum(self.status.get(item) == "completed" for item in task.activation_tasks) >= task.activation_count)

    def snapshot(self) -> dict[str, Any]:
        return {"status": dict(self.status), "remaining": deepcopy(self.remaining), "assigned": dict(self.assigned), "completed_tick": dict(self.completed_tick), "failed": dict(self.failed), "group_members": {k: sorted(v) for k, v in self.group_members.items()}}

    def restore(self, state: Mapping[str, Any]) -> None:
        self.status = dict(state["status"])
        self.remaining = {k: list(v) for k, v in state["remaining"].items()}
        self.assigned = dict(state["assigned"])
        self.completed_tick = dict(state["completed_tick"])
        self.failed = dict(state["failed"])
        self.group_members = {k: set(v) for k, v in state.get("group_members", {}).items()}


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
    service_resume: bool = False


class DiagnosticRuntime:
    """Deterministic multi-robot logical runtime with replay and branching."""

    schema_version = "hetero.diagnostic.run.v1"

    def __init__(self, episode: EpisodeSpec, algorithm: AlgorithmAdapter | None = None):
        if abs(float(episode.dt_s) - CONTROL_DT_S) > 1e-12:
            raise ValueError("diagnostic runtime requires the registered 0.02 s clock")
        self.episode = episode
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
            ("skill", lambda: {"reallocation_count": self.reallocation_count, "message_counter": self._message_counter, "replay_equivalent": self._replay_equivalent}, self._skill_restore),
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

    def _world_snapshot(self) -> dict[str, Any]:
        return {"terminal": self.terminal, "success": self.success, "failure_reason": self.failure_reason}

    def _world_restore(self, state: Mapping[str, Any]) -> None:
        self.terminal = bool(state["terminal"]); self.success = bool(state["success"]); self.failure_reason = str(state["failure_reason"])

    def _robot_snapshot(self) -> dict[str, Any]:
        return {rid: {"node_id": s.node_id, "battery": s.battery, "distance_m": s.distance_m, "route": list(s.route), "route_index": s.route_index, "edge_progress_m": s.edge_progress_m, "task_id": s.task_id, "target_key": s.target_key, "status": s.status, "resource_id": s.resource_id} for rid, s in sorted(self.robot_states.items())}

    def _robot_restore(self, state: Mapping[str, Any]) -> None:
        for rid, payload in state.items():
            robot = self.robot_states[rid]
            for key, value in payload.items():
                setattr(robot, key, value)

    def _observation(self) -> dict[str, Any]:
        pending = []
        for task_id in sorted(self.tasks.specs):
            if not self.tasks.eligible(task_id):
                continue
            task = self.tasks.specs[task_id]
            for target in self.tasks.remaining[task_id]:
                target_nodes = self.episode.graph.resolve(target, task.required_domain)
                target_position = self.episode.graph.nodes[target_nodes[0]].position
                pending.append({"task_id": task_id, "target": target, "goal_key": f"{task_id}:{target}", "required_domain": task.required_domain, "target_position": tuple(target_position), "kind": task.kind, "distinct_group": task.distinct_group, "eligible_robots": sorted(set(state.spec.robot_id for state in self.robot_states.values() if not task.distinct_group or state.spec.robot_id not in self.tasks.group_members.get(task.distinct_group, set())))})
        available = {}
        for state in self.robot_states.values():
            if state.status != "idle":
                continue
            distance = math.inf
            if pending:
                for goal in pending:
                    try:
                        targets = self.episode.graph.resolve(goal["target"], goal["required_domain"] or state.spec.domain)
                        distance = min(self.episode.graph.shortest(state.node_id, node)[0] for node in targets)
                    except ValueError:
                        continue
            available[state.spec.robot_id] = {"robot_id": state.spec.robot_id, "domain": state.spec.domain, "distance": distance, "position": tuple(self.episode.graph.nodes[state.node_id].position), "capabilities": tuple(state.spec.capabilities)}
        return {"tick": self.clock_tick, "pending_goals": pending, "available_robots": available, "tasks": self.tasks.snapshot()}

    def _assign(self) -> None:
        decision = self.algorithm.decide(self._observation())
        for robot_id, goal_key in sorted(dict(decision).items()):
            if robot_id not in self.robot_states or self.robot_states[robot_id].status != "idle":
                continue
            if ":" not in goal_key:
                continue
            task_id, target = goal_key.split(":", 1)
            if task_id not in self.tasks.specs or target not in self.tasks.remaining[task_id] or not self.tasks.eligible(task_id):
                continue
            robot = self.robot_states[robot_id]; task = self.tasks.specs[task_id]
            if task.distinct_group and robot_id in self.tasks.group_members.setdefault(task.distinct_group, set()):
                self.events.append({"tick": self.clock_tick, "kind": "invalid_assignment", "robot_id": robot_id, "task_id": task_id, "reason": "distinct_group_robot_reuse"})
                continue
            try:
                candidates = self.episode.graph.resolve(target, task.required_domain or robot.spec.domain)
                candidates = [node for node in candidates if self.episode.graph.nodes[node].domain == robot.spec.domain]
            except ValueError:
                self._fail(f"unreachable_target:{task_id}:{target}")
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
        remaining = self.tasks.remaining[task_id]
        if target in remaining:
            remaining.remove(target)
        if not remaining:
            self.tasks.status[task_id] = "completed"; self.tasks.completed_tick[task_id] = self.clock_tick
            self.events.append({"tick": self.clock_tick, "kind": "task_completed", "robot_id": robot.spec.robot_id, "task_id": task_id})
        else:
            # A multi-target coverage/inspection task exposes the next
            # obligation only after the current robot has released its goal.
            self.tasks.status[task_id] = "pending"
        robot.task_id = robot.target_key = robot.resource_id = None
        robot.route = []; robot.route_index = 0; robot.edge_progress_m = 0.0; robot.status = "idle"

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
        self._assign()
        self.communication.advance(self.clock_tick)
        self.clock_tick += 1
        if all(status == "completed" for status in self.tasks.status.values()):
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
        completed = sum(value == "completed" for value in self.tasks.status.values())
        coverage_tasks = [task_id for task_id, spec in self.tasks.specs.items() if spec.kind in {"coverage", "inspection"}]
        coverage_done = sum(self.tasks.status[item] == "completed" for item in coverage_tasks)
        comm = self.communication.metrics()
        replay_digest = _digest(self.events)
        batteries = {rid: state.battery for rid, state in sorted(self.robot_states.items()) if state.battery is not None}
        return {"schema_version": self.schema_version, "success": self.success, "failure_reason": self.failure_reason, "tick": self.clock_tick, "makespan_s": self.clock_tick * CONTROL_DT_S, "task_completion_rate": completed / len(self.tasks.specs) if self.tasks.specs else 1.0, "coverage_fraction": coverage_done / len(coverage_tasks) if coverage_tasks else 1.0, "resource_wait_ticks": self.resources.wait_ticks, "resource_contention_requests": self.resources.contention_requests, "recover_services": self.resources.total_services, "battery_remaining_m": batteries, "min_battery_remaining_m": min(batteries.values()) if batteries else None, "communication_bits": comm["transmitted_bits"], "communication_delay_ticks": comm["delay_ticks_total"], "communication_loss_bits": comm["loss_bits"], "reallocation_count": self.reallocation_count, "replay_digest": replay_digest, "replay_equivalent": self._replay_equivalent, "paper_score_eligible": False, "D1_eligible": bool(self.success), "D2_eligible": False, "D3_eligible": bool(self.success and self.resources.contention_requests >= 0), "D4_eligible": False, "D5_eligible": False, "D6_eligible": False, "eligibility_reasons": {"D2": "paired C0-C3 retention directory required", "D4": "registered availability-loss branch required", "D5": "certified cross-scenario family required", "D6": "independent calibration reference required"}, "events": deepcopy(self.events), "assignment": dict(self.tasks.assigned)}


__all__ = ["CONTROL_DT_S", "SemanticNode", "SemanticEdge", "SemanticGraph", "RobotSpec", "TaskSpec", "ResourceSpec", "EpisodeSpec", "AlgorithmAdapter", "GreedyGoalAlgorithm", "TaskLedger", "ResourceLedger", "CommunicationModel", "DiagnosticRuntime"]
