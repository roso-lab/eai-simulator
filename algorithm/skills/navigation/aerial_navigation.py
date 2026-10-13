"""Pure authored XYZ routing and navigation lifecycle.

The host supplies measured poses and applies the returned world goal. Edges
describe connectivity; polyline decoration never becomes a flight waypoint.
"""
from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import heapq
import json
import math
from typing import Mapping

from ..contracts import SkillCommand, SkillContractError, SkillKind, SkillPlan, SkillRequest
from ..simulator_schema import AERIAL_NAVIGATION_SKILL_ID, SimulatorSchemaError, validate_simulator_skill_request
from ..aerial_transport.geometry import number, vector


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def invalid_request_plan(value, skill_id, provider_id, provider_version, error):
    """Keep usable request provenance even when normalization fails."""
    def get(name, default=None):
        return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)
    def identifier(name, default):
        item = get(name)
        return item if isinstance(item, str) and item.strip() else default
    context = get("branch_context")
    try:
        context = json.loads(json.dumps(context, allow_nan=False))
    except (TypeError, ValueError):
        context = None
    return SkillPlan(identifier("request_id", "invalid"), skill_id, identifier("robot_id", "unknown"),
        status="invalid_request", metadata={"schema_version": identifier("skill_version", "simulator.skill.v1"),
            "provider_id": provider_id, "provider_version": provider_version, "task_id": identifier("task_id", identifier("request_id", "invalid")),
            "branch_context": context, "reason": str(error), "physical_outcome": "unknown"})


@dataclass(frozen=True)
class AerialPose:
    position_m: tuple[float, float, float]
    yaw_rad: float = 0.

    def __post_init__(self):
        object.__setattr__(self, "position_m", vector(self.position_m))
        object.__setattr__(self, "yaw_rad", number(self.yaw_rad))


def select_aerial_node_route(document, pose, *, target_refs=(), target_pose=None, node_radius_m=.30):
    """Dijkstra on authored directed edges, weighted by XYZ Euclidean length."""
    if not isinstance(document, Mapping):
        raise SkillContractError("aerial topology must be an object")
    if number(node_radius_m) <= 0:
        raise SkillContractError("node_radius_m must be positive")
    for name in ("nodes", "edges"):
        if not isinstance(document.get(name, ()), (list, tuple)) or any(not isinstance(row, Mapping) for row in document.get(name, ())):
            raise SkillContractError(f"aerial topology {name} must contain objects")
    for name, expected in (("frame", "scene_world"), ("coordinate_frame", "scene_world"),
                           ("meters_per_unit", 1), ("up_axis", "Z")):
        if document.get(name, expected) != expected:
            raise SkillContractError(f"invalid aerial topology {name}")
    if isinstance(document.get("meters_per_unit"), bool):
        raise SkillContractError("invalid aerial topology meters_per_unit")
    if (not isinstance(target_refs, (list, tuple, set))
            or any(not isinstance(ref, str) or not ref.strip() for ref in target_refs)):
        raise SkillContractError("invalid aerial target references")
    nodes, refs = {}, {}
    for row in document.get("nodes", ()):
        node = row.get("id")
        if not isinstance(node, str) or not node.strip() or node in nodes:
            raise SkillContractError("aerial node ids must be unique strings")
        nodes[node] = vector(row.get("position_xyz"))
        bindings = row.get("target_refs", [])
        if not isinstance(bindings, (list, tuple)) or any(not isinstance(x, str) or not x.strip() for x in bindings):
            raise SkillContractError("invalid aerial target refs")
        refs[node] = sorted(set(bindings))
    edges, ids = [], set()
    adjacency = {node: [] for node in nodes}
    for row in document.get("edges", ()):
        edge, first, last = row.get("id"), row.get("from"), row.get("to")
        directed = row.get("directed", False)
        if (not isinstance(edge, str) or not edge.strip() or edge in ids
                or not isinstance(first, str) or not isinstance(last, str)
                or first not in nodes or last not in nodes or first == last or type(directed) is not bool):
            raise SkillContractError("invalid aerial edge")
        distance = math.dist(nodes[first], nodes[last])
        if distance <= 0:
            raise SkillContractError("aerial edge must have nonzero XYZ length")
        ids.add(edge)
        edges.append({"id": edge, "from": first, "to": last, "directed": directed})
        adjacency[first].append((last, edge, distance))
        if not directed:
            adjacency[last].append((first, edge, distance))
    graph_digest = digest({"nodes": [{"id": node, "position_xyz": nodes[node], "target_refs": refs[node]} for node in sorted(nodes)], "edges": sorted(edges, key=lambda x: x["id"])})
    failure = {"admitted": False, "route_graph_digest": graph_digest, "reason": "route_unreachable"}
    if not nodes:
        return failure
    if target_refs:
        targets = {node for node in nodes if set(refs[node]).intersection(target_refs)}
    elif target_pose is not None:
        point = vector(target_pose)
        nearest = min(nodes, key=lambda x: (math.dist(point, nodes[x]), x))
        targets = {nearest} if math.dist(point, nodes[nearest]) <= node_radius_m else set()
    else:
        targets = set()
    start = min(nodes, key=lambda x: (math.dist(pose.position_m, nodes[x]), x))
    # Sorting the queue by node ID and adjacency by node/edge ID keeps ties
    # stable without copying whole paths for every explored edge.
    queue, settled, previous = [(0., start)], set(), {}
    distances = {start: 0.}
    while queue:
        length, node = heapq.heappop(queue)
        if node in settled:
            continue
        settled.add(node)
        if node in targets:
            path, traversed, current = [node], [], node
            while current != start:
                current, edge = previous[current]
                path.append(current)
                traversed.append(edge)
            path.reverse()
            traversed.reverse()
            return {"admitted": True, "route_points_m": [list(nodes[x]) for x in path],
                    "topology_node_ids": path, "topology_edge_ids": traversed,
                    "route_length_m": length, "route_graph_digest": graph_digest}
        for neighbor, edge, cost in sorted(adjacency[node]):
            if neighbor not in settled and length+cost < distances.get(neighbor, math.inf):
                distances[neighbor] = length+cost
                previous[neighbor] = node, edge
                heapq.heappush(queue, (length+cost, neighbor))
    return failure


@dataclass
class AerialNodeRouteTracker:
    route_points_m: object
    node_radius_m: float = .30
    node_index: int = field(default=0, init=False)

    def __post_init__(self):
        self.route_points_m = tuple(vector(x) for x in self.route_points_m)
        if not self.route_points_m or number(self.node_radius_m) <= 0:
            raise SkillContractError("invalid aerial route tracker")

    @property
    def arrived(self):
        return self.node_index == len(self.route_points_m)

    def target(self, pose):
        while not self.arrived and math.dist(pose.position_m, self.route_points_m[self.node_index]) <= self.node_radius_m:
            self.node_index += 1
        return None if self.arrived else self.route_points_m[self.node_index]


def normalize_request(value, skill_id):
    request = SkillRequest.from_mapping(value.to_mapping() if isinstance(value, SkillRequest) else value)
    if request.skill_id != skill_id:
        raise SkillContractError("incorrect aerial skill_id")
    try:
        validate_simulator_skill_request(request)
    except SimulatorSchemaError as exc:
        raise SkillContractError(str(exc)) from exc
    return request


def validate_observation(request, observation, *, motion):
    if (not isinstance(observation, Mapping) or observation.get("valid") is not True
            or type(observation.get("world_state_seq")) is not int
            or observation["world_state_seq"] != request.parameters["world_state_seq"]
            or (motion and observation.get("fresh") is not True)):
        raise SkillContractError("validated matching fresh world state is required")


@dataclass(frozen=True)
class AerialNavigationResponse:
    state: Mapping
    plan: SkillPlan
    private_evidence: Mapping

    def to_mapping(self):
        return {"navigation_state": dict(self.state), "skill_plan": self.plan.to_mapping()}


class AerialNavigationProvider:
    """Compile commands from measured poses; the caller owns native I/O.

    Observations supply a validated matching sequence and an ``AerialPose``.
    Start/replan also require topology. Optional ``simulation_time_s`` is the
    authoritative clock for duration and absolute deadline checks.
    """
    skill_id = AERIAL_NAVIGATION_SKILL_ID
    kind = SkillKind.NAVIGATION
    provider_id = "aerial_navigation.provider"
    provider_version = "1.0"

    def __init__(self, *, node_radius_m=.30):
        self.node_radius_m = number(node_radius_m, "node_radius_m")
        if self.node_radius_m <= 0:
            raise SkillContractError("node_radius_m must be positive")
        self._tasks = {}

    @property
    def active_task_ids(self):
        return tuple(sorted(key for key, task in self._tasks.items() if not task["terminal"]))

    def dispatch(self, value, *, observation=None):
        r = normalize_request(value, self.skill_id)
        validate_observation(r, observation, motion=False)
        key = r.task_id or r.request_id
        if key in self._tasks:
            raise SkillContractError("aerial navigation task already exists")
        pose = observation.get("pose")
        if not isinstance(pose, AerialPose):
            raise SkillContractError("dispatch requires a measured AerialPose")
        now = observation.get("simulation_time_s")
        if now is not None:
            now = number(now, "simulation_time_s")
            if now < 0:
                raise SkillContractError("simulation_time_s must be nonnegative")
        self._tasks[key] = dict(request=r, terminal=False, status="pending", reason="awaiting_start",
                                tracker=None, goal=pose.position_m, last_pose=pose.position_m,
                                execution_state="holding", safe_action="hold", evidence={},
                                started_s=now, last_time_s=now, elapsed_s=0.)
        return self.response(key)

    def control(self, value, *, observation=None, dt_s=.1):
        r = normalize_request(value, self.skill_id)
        key = r.task_id or r.request_id
        if key not in self._tasks or self._tasks[key]["terminal"]:
            raise SkillContractError("aerial navigation task is not active")
        task = self._tasks[key]
        old = task["request"]
        if r.robot_id != old.robot_id or r.target_ref != old.target_ref or r.target_pose != old.target_pose:
            raise SkillContractError("aerial navigation identity is immutable")
        try:
            intent = r.parameters.get("intent", "start")
            validate_observation(r, observation, motion=intent not in {"hold", "stop"})
            pose = observation.get("pose")
            if not isinstance(pose, AerialPose):
                raise SkillContractError("AerialPose is required")
            task.update(request=r, last_pose=pose.position_m, goal=pose.position_m,
                        execution_state="holding", safe_action="hold")
            if intent == "stop":
                task.update(terminal=True, status="cancelled", reason="cancelled", safe_action="stop")
            elif intent == "hold":
                task.update(status="pending", reason="system_hold")
            elif self._timed_out(task, observation, dt_s):
                return self.fail(key, TimeoutError(), reason_code="navigation_timeout")
            elif intent == "continue" and task["tracker"] is None:
                task["reason"] = "awaiting_start"
            else:
                if intent in {"start", "replan"}:
                    route = select_aerial_node_route(observation.get("topology"), pose,
                        target_refs=[r.target_ref] if r.target_ref else (), target_pose=r.target_pose,
                        node_radius_m=self.node_radius_m)
                    task["evidence"]["route_selection"] = route
                    if not route["admitted"]:
                        return self.fail(key, SkillContractError("route_unreachable"), reason_code="route_unreachable")
                    task["tracker"] = AerialNodeRouteTracker(route["route_points_m"], self.node_radius_m)
                target = task["tracker"].target(pose)
                if target is None:
                    task.update(terminal=True, status="succeeded", reason="arrived",
                                execution_state="arrived", safe_action="stop")
                else:
                    task.update(goal=target, status="pending", reason="follow_nodes",
                                execution_state="moving", safe_action="continue")
        except (SkillContractError, KeyError, TypeError, ValueError) as exc:
            return self.fail(key, exc)
        return self.response(key)

    @staticmethod
    def _timed_out(task, observation, dt_s):
        dt = number(dt_s, "dt_s")
        if dt <= 0:
            raise SkillContractError("dt_s must be positive")
        now = observation.get("simulation_time_s")
        if now is not None:
            now = number(now, "simulation_time_s")
            if now < 0 or (task["last_time_s"] is not None and now < task["last_time_s"]):
                raise SkillContractError("simulation time must be nonnegative and monotonic")
            if task["started_s"] is None:
                task["started_s"] = now - task["elapsed_s"]
            task["last_time_s"] = now
            task["elapsed_s"] = now - task["started_s"]
        else:
            if task["last_time_s"] is not None:
                raise SkillContractError("simulation_time_s is required after selecting a simulation clock")
            task["elapsed_s"] += dt
        request = task["request"]
        timing, budget = request.timing or {}, request.budget or {}
        if "deadline_s" in timing:
            if now is None:
                raise SkillContractError("deadline_s requires simulation_time_s")
            if now >= timing["deadline_s"]:
                return True
        durations = [value["duration_s"] for value in (timing, budget) if "duration_s" in value]
        return bool(durations) and task["elapsed_s"] >= min(durations)

    def fail(self, task_id, error, *, reason_code="execution_failure", hover_position=None):
        """Report a host failure using a fresh or last measured hold target."""
        task = self._tasks[task_id]
        if hover_position is not None:
            task["last_pose"] = vector(hover_position)
        task.update(terminal=True, status="failed", reason=reason_code,
                    execution_state="holding", safe_action="stop")
        task["goal"] = task["last_pose"]
        task["evidence"]["error_type"] = type(error).__name__
        return self.response(task_id)

    def response(self, task_id):
        task = self._tasks[task_id]
        r = task["request"]
        payload = {"target_pose": task["goal"], "frame": "world"}
        if "horizon_ticks" in r.parameters:
            payload["horizon_ticks"] = r.parameters["horizon_ticks"]
        commands = (SkillCommand("goal_position", payload),)
        plan = SkillPlan(r.request_id, self.skill_id, r.robot_id, commands,
                         metadata={"schema_version": r.skill_version, "provider_id": self.provider_id,
                                   "provider_version": self.provider_version, "request_digest": digest(r.to_mapping()),
                                   "command_route": "env.set_command -> EAI position controller -> env.step", "physical_outcome": "unknown"})
        evidence = deepcopy(task["evidence"])
        evidence["node_index"] = task["tracker"].node_index if task["tracker"] else 0
        if task["terminal"]:
            evidence["terminal_hold_command"] = list(task["goal"])
        state = {"task_id": task_id, "status": task["status"], "reason_code": task["reason"],
                 "execution_state": task["execution_state"], "safe_action": task["safe_action"],
                 "world_state_seq": r.parameters["world_state_seq"]}
        return AerialNavigationResponse(state, plan, evidence)

    def plan(self, request, *, observation=None):
        try:
            r = normalize_request(request, self.skill_id)
            key = r.task_id or r.request_id
            if key not in self._tasks:
                self.dispatch(r, observation=observation)
            return self.control(r, observation=observation).plan
        except (SkillContractError, KeyError, TypeError) as exc:
            return invalid_request_plan(request, self.skill_id, self.provider_id, self.provider_version, exc)
