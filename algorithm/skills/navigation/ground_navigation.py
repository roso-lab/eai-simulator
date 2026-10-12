"""Pure authored-node routing and heading PID command compilation.

Native state reads and command application belong to the task executor.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import heapq
import json
import math
from typing import Any, Mapping, Sequence

from ..contracts import SkillCommand, SkillContractError, SkillKind, SkillPlan, SkillRequest
from .topology import (LEVEL_HEIGHT_TOLERANCE_M, edge_capabilities, topology_fields,
                       route_graph_digest, validate_ground_topology)
from ..simulator_schema import (
    GROUND_NAVIGATION_SKILL_ID, GROUND_NAVIGATION_SKILL_IDS,
    SimulatorSchemaError, validate_simulator_skill_request,
)


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SkillContractError(f"{label} must be finite")
    return float(value)


def _xyz(value: Sequence[float]) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise SkillContractError("position must contain XYZ")
    return tuple(_number(item, "position") for item in value)


def normalize_angle(value: float) -> float:
    return math.atan2(math.sin(value), math.cos(value))


@dataclass(frozen=True)
class GroundPose:
    position_m: tuple[float, float, float]
    yaw_rad: float
    support_z_m: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "position_m", _xyz(self.position_m))
        object.__setattr__(self, "yaw_rad", normalize_angle(_number(self.yaw_rad, "yaw")))
        if self.support_z_m is not None:
            object.__setattr__(self, "support_z_m", _number(self.support_z_m, "support_z_m"))


@dataclass(frozen=True)
class GroundTrackingConfig:
    cruise_speed_m_s: float = 0.5
    kp: float = 2.0
    ki: float = 0.0
    kd: float = 0.1
    max_yaw_rate_rad_s: float = 1.0
    turn_threshold_rad: float = math.pi / 6.0
    node_radius_m: float = 0.30
    integral_limit_rad_s: float = 1.0

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = _number(getattr(self, name), name)
            if value < 0 or (name not in {"kp", "ki", "kd"} and value == 0):
                raise SkillContractError(f"{name} is invalid")
        if self.turn_threshold_rad > math.pi:
            raise SkillContractError("turn threshold must not exceed pi")


@dataclass
class NodeRouteTracker:
    """One task's ordered node index and PID memory."""
    route_points_m: Sequence[Sequence[float]]
    config: GroundTrackingConfig = field(default_factory=GroundTrackingConfig)
    height_aware: bool = False
    node_index: int = field(default=0, init=False)
    integral: float = field(default=0.0, init=False)
    previous_error: float | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.route_points_m = tuple(_xyz(point) for point in self.route_points_m)
        if not self.route_points_m:
            raise SkillContractError("node route must not be empty")

    @property
    def arrived(self) -> bool:
        return self.node_index == len(self.route_points_m)

    def reset_pid(self) -> None:
        self.integral = 0.0
        self.previous_error = None

    def advance_nodes(self, pose: GroundPose) -> bool:
        if self.height_aware and pose.support_z_m is None:
            raise SkillContractError("multilevel tracking requires measured support_z_m")
        while not self.arrived:
            target = self.route_points_m[self.node_index]
            if math.dist(pose.position_m[:2], target[:2]) > self.config.node_radius_m:
                break
            if self.height_aware and abs(pose.support_z_m - target[2]) > LEVEL_HEIGHT_TOLERANCE_M:
                break
            self.node_index += 1
            self.reset_pid()
        return self.arrived

    def step(self, pose: GroundPose, *, dt_s: float = 0.1) -> list[float]:
        dt = _number(dt_s, "dt_s")
        if dt <= 0:
            raise SkillContractError("dt_s must be positive")
        if self.advance_nodes(pose):
            return [0.0, 0.0, 0.0]
        target = self.route_points_m[self.node_index]
        if (self.height_aware and math.dist(pose.position_m[:2], target[:2]) < 0.05
                and abs(pose.support_z_m - target[2]) > LEVEL_HEIGHT_TOLERANCE_M):
            raise SkillContractError("target XY reached at the wrong support height")
        error = normalize_angle(math.atan2(target[1] - pose.position_m[1],
                                            target[0] - pose.position_m[0]) - pose.yaw_rad)
        limit = self.config.integral_limit_rad_s
        self.integral = max(-limit, min(limit, self.integral + error * dt))
        derivative = (0.0 if self.previous_error is None
                      else normalize_angle(error - self.previous_error) / dt)
        self.previous_error = error
        angular = self.config.kp * error + self.config.ki * self.integral + self.config.kd * derivative
        angular = max(-self.config.max_yaw_rate_rad_s,
                      min(self.config.max_yaw_rate_rad_s, angular))
        linear = (0.0 if abs(error) > self.config.turn_threshold_rad
                  else self.config.cruise_speed_m_s)
        return [linear, 0.0, angular]


def select_node_route(document: Any, pose: GroundPose, *,
                      target_refs: Sequence[str] = (),
                      target_pose: Sequence[float] | None = None,
                      node_radius_m: float = 0.30,
                      origin_ref: str | None = None,
                      controller_capabilities: Sequence[str] = ("walk",)) -> dict[str, Any]:
    """Directed Dijkstra on authored nodes; v2 checks floor and capabilities.

    v1 retains nearest-XY behavior. v2 requires measured support height and a
    nearby start; it never invents an unvalidated connector from a distant robot.
    """
    errors = validate_ground_topology(document)
    if errors:
        raise SkillContractError("invalid ground topology: " + "; ".join(errors))
    graph_digest = route_graph_digest(document)
    multilevel = topology_fields(document).get("version", 1) == 2
    if multilevel and pose.support_z_m is None:
        raise SkillContractError("multilevel routing requires measured support_z_m")
    if (isinstance(controller_capabilities, (str, bytes)) or
            not all(isinstance(item, str) and item for item in controller_capabilities)):
        raise SkillContractError("invalid controller capabilities")
    capabilities = frozenset(controller_capabilities)
    def at_height(point):
        return not multilevel or abs(pose.support_z_m - point[2]) <= LEVEL_HEIGHT_TOLERANCE_M
    rows = document.get("nodes", ()) if isinstance(document, Mapping) else document.nodes
    edges = document.get("edges", ()) if isinstance(document, Mapping) else document.edges
    nodes: dict[str, tuple[float, float, float]] = {}
    refs: dict[str, set[str]] = {}
    for row in rows:
        node_id = row.get("id")
        if not isinstance(node_id, str) or not node_id or node_id in nodes:
            raise SkillContractError("topology node id must be unique and nonempty")
        nodes[node_id] = _xyz(row["position_xyz"])
        refs[node_id] = set(row.get("target_refs", ()))
    if not nodes:
        return {"admitted": False, "reason": "topology_start_unconnected", "route_graph_digest": graph_digest}
    if target_refs:
        targets = {node_id for node_id in nodes if refs[node_id].intersection(target_refs)
                   or (multilevel and node_id in target_refs)}
    elif target_pose is not None:
        point = list(target_pose)
        if multilevel and len(point) != 3:
            raise SkillContractError("multilevel target_pose requires XYZ")
        if len(point) == 2:
            point.append(pose.position_m[2])
        point = _xyz(point)
        candidates = [node_id for node_id in nodes if not multilevel or
                      abs(point[2] - nodes[node_id][2]) <= LEVEL_HEIGHT_TOLERANCE_M]
        nearest = min(candidates, key=lambda node_id: (math.dist(point[:2], nodes[node_id][:2]), node_id)) if candidates else None
        targets = ({nearest} if nearest is not None and math.dist(point[:2], nodes[nearest][:2]) <= node_radius_m else set())
    else:
        targets = set()
    if not targets:
        return {"admitted": False, "reason": "target_unresolved", "route_graph_digest": graph_digest}
    starts = [node_id for node_id in nodes if at_height(nodes[node_id])]
    if origin_ref is not None:
        starts = [node_id for node_id in starts if origin_ref in refs[node_id] or origin_ref == node_id]
        if len(starts) != 1:
            return {"admitted": False, "reason": "origin_unresolved_or_wrong_level", "route_graph_digest": graph_digest}
    if multilevel or origin_ref is not None:
        starts = [node_id for node_id in starts
                  if math.dist(pose.position_m[:2], nodes[node_id][:2]) <= node_radius_m]
    if not starts:
        return {"admitted": False, "reason": "topology_start_unconnected", "route_graph_digest": graph_digest}
    start = min(starts, key=lambda node_id: (math.dist(pose.position_m[:2], nodes[node_id][:2]), node_id))
    adjacency: dict[str, list[tuple[str, str, float]]] = {node_id: [] for node_id in nodes}
    edge_ids: set[str] = set()
    for edge in edges:
        edge_id, first, last = edge.get("id"), edge.get("from"), edge.get("to")
        if (not isinstance(edge_id, str) or not edge_id or edge_id in edge_ids
                or first not in nodes or last not in nodes or first == last
                or type(edge.get("directed", False)) is not bool):
            raise SkillContractError("topology edge is invalid")
        edge_ids.add(edge_id)
        length = math.dist(nodes[first], nodes[last]) if multilevel else math.dist(nodes[first][:2], nodes[last][:2])
        if length <= 0:
            raise SkillContractError("ground topology edge must have nonzero XY length")
        if multilevel and not edge_capabilities(edge).issubset(capabilities):
            continue
        adjacency[first].append((last, edge_id, length))
        if not edge.get("directed", False):
            adjacency[last].append((first, edge_id, length))
    # Settle equal-length candidates by the next node ID first. This makes a
    # tie between bound target nodes choose the lexically smallest target,
    # while the path tuple remains a deterministic secondary tie-breaker for
    # alternate ways to reach the same node.
    queue = [(0.0, start, (start,), (),)]
    best: dict[str, tuple[float, tuple[str, ...]]] = {}
    while queue:
        length, node_id, path, traversed = heapq.heappop(queue)
        if node_id in best:
            continue
        best[node_id] = (length, path)
        if node_id in targets:
            points = [list(nodes[item]) for item in path]
            return {"admitted": True, "route_points_m": points,
                    **({"height_aware": True, "controller_capabilities": sorted(capabilities),
                        "route_level_ids": [next(row for row in rows if row["id"] == item).get("level_id") for item in path],
                        "traversal_modes": [next(edge for edge in edges if edge["id"] == item)["traversal_mode"] for item in traversed]}
                       if multilevel else {}),
                    "route_graph_digest": graph_digest,
                    "topology_node_ids": list(path), "topology_edge_ids": list(traversed),
                    "route_length_m": length,
                    "already_at_target": len(path) == 1 and
                    math.dist(pose.position_m[:2], points[-1][:2]) <= node_radius_m and at_height(points[-1])}
        for neighbor, edge_id, distance in sorted(adjacency[node_id]):
            if neighbor not in best:
                heapq.heappush(queue, (length + distance, neighbor,
                                      (*path, neighbor), (*traversed, edge_id)))
    return {"admitted": False, "reason": "topology_route_unreachable", "route_graph_digest": graph_digest}


COMMAND_ROUTE = "env.step -> EAI velocity controller"


def normalize_ground_navigation_request(value: SkillRequest | Mapping[str, Any]) -> SkillRequest:
    request = value if isinstance(value, SkillRequest) else SkillRequest.from_mapping(value)
    if request.skill_id not in GROUND_NAVIGATION_SKILL_IDS:
        raise SkillContractError("request is not a ground navigation skill")
    try:
        validate_simulator_skill_request(request)
    except SimulatorSchemaError as exc:
        raise SkillContractError(str(exc)) from exc
    return request


def _validate_observation(request: SkillRequest, observation: Mapping[str, Any] | None,
                          *, motion: bool) -> None:
    if not isinstance(observation, Mapping) or observation.get("valid") is not True:
        raise SkillContractError("validated world state is required")
    sequence = observation.get("world_state_seq")
    if type(sequence) is not int or sequence != request.parameters["world_state_seq"]:
        raise SkillContractError("world state sequence does not match the request")
    if motion and observation.get("fresh") is not True:
        raise SkillContractError("fresh world state is required for motion")


@dataclass
class _GroundTask:
    request: SkillRequest
    status: str = "pending"
    execution_state: str = "holding"
    safe_action: str = "hold"
    world_state_seq: int = 0
    reason_code: str = "control_queued"
    tracker: NodeRouteTracker | None = None
    private_evidence: dict[str, Any] = field(default_factory=dict)
    previous_command: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    terminal: bool = False


@dataclass(frozen=True)
class GroundNavigationResponse:
    state: Mapping[str, Any]
    plan: SkillPlan
    private_evidence: Mapping[str, Any]

    def to_mapping(self) -> dict[str, Any]:
        return {"navigation_state": dict(self.state), "skill_plan": self.plan.to_mapping()}


class GroundNavigationProvider:
    """Compile one task's node tracking commands without accessing a simulator."""

    skill_id = GROUND_NAVIGATION_SKILL_ID
    kind = SkillKind.NAVIGATION
    provider_id = "ground_navigation.provider"
    provider_version = "1.0"

    def __init__(self, *, tracking_config: GroundTrackingConfig | None = None,
                 controller_capabilities: Mapping[str, Sequence[str]] | None = None):
        self.tracking_config = tracking_config or GroundTrackingConfig()
        self.controller_capabilities = {robot: tuple(values) for robot, values in
                                        (controller_capabilities or {}).items()}
        self._tasks: dict[str, _GroundTask] = {}

    @property
    def active_task_ids(self) -> tuple[str, ...]:
        return tuple(sorted(key for key, task in self._tasks.items() if not task.terminal))

    def dispatch(self, value: SkillRequest | Mapping[str, Any], *,
                 observation: Mapping[str, Any] | None = None) -> GroundNavigationResponse:
        request = normalize_ground_navigation_request(value)
        task_id = request.task_id or request.request_id
        if task_id in self._tasks:
            raise SkillContractError("ground navigation task already exists")
        _validate_observation(request, observation, motion=False)
        self._tasks[task_id] = _GroundTask(
            request=request, world_state_seq=request.parameters["world_state_seq"])
        return self.response(task_id)

    def control(self, value: SkillRequest | Mapping[str, Any], *,
                observation: Mapping[str, Any] | None = None,
                dt_s: float = 0.1) -> GroundNavigationResponse:
        request = normalize_ground_navigation_request(value)
        task_id = request.task_id or request.request_id
        task = self._tasks.get(task_id)
        if task is None or task.terminal:
            raise SkillContractError("ground navigation task is not active")
        if request.robot_id != task.request.robot_id:
            raise SkillContractError("ground navigation robot_id does not match the task")
        if request.target_ref != task.request.target_ref or request.target_pose != task.request.target_pose:
            raise SkillContractError("ground navigation target does not match the task")
        for key in ("origin_ref", "route_graph_digest"):
            if request.parameters.get(key) != task.request.parameters.get(key):
                raise SkillContractError(f"ground navigation {key} does not match the task")
        intent = request.parameters.get("intent", "start")
        try:
            _validate_observation(request, observation, motion=intent in {"start", "continue", "replan"})
            task.request = request
            task.world_state_seq = request.parameters["world_state_seq"]
            if intent in {"hold", "stop"}:
                self._zero(task)
                task.execution_state = "holding"
                task.safe_action = "stop" if intent == "stop" else "hold"
                task.reason_code = "cancelled" if intent == "stop" else "system_hold"
                if intent == "stop":
                    task.status, task.terminal = "cancelled", True
            elif intent == "continue" and task.tracker is None:
                self._zero(task)
                task.reason_code = "awaiting_start"
            else:
                pose = observation.get("pose")
                if not isinstance(pose, GroundPose):
                    raise SkillContractError("observation must contain GroundPose with XYZ and yaw")
                if intent in {"start", "replan"}:
                    selected = select_node_route(
                        observation["topology"], pose,
                        target_refs=(request.target_ref,) if request.target_ref else (),
                        target_pose=request.target_pose,
                        node_radius_m=self.tracking_config.node_radius_m,
                        origin_ref=request.parameters.get("origin_ref") if intent == "start" else None,
                        controller_capabilities=self.controller_capabilities.get(request.robot_id, ("walk",)))
                    expected_digest = request.parameters.get("route_graph_digest")
                    if expected_digest is not None and expected_digest != selected["route_graph_digest"]:
                        raise SkillContractError("topology differs from resolved route graph")
                    task.private_evidence["route_selection"] = selected
                    if selected.get("admitted") is not True:
                        self._finish(task, "failed", "route_unreachable")
                        return self.response(task_id)
                    task.tracker = NodeRouteTracker(selected["route_points_m"], self.tracking_config,
                                                   height_aware=selected.get("height_aware", False))
                command = task.tracker.step(pose, dt_s=dt_s)
                if task.tracker.arrived:
                    self._finish(task, "succeeded", "arrived")
                    task.execution_state = "arrived"
                else:
                    task.previous_command = command
                    task.execution_state, task.safe_action, task.reason_code = (
                        "moving", "continue", "pid_follow_nodes")
        except Exception as exc:
            return self.fail(task_id, exc)
        return self.response(task_id)

    def plan(self, request: SkillRequest, *,
             observation: Mapping[str, Any] | None = None) -> SkillPlan:
        """Implement the shared SkillProvider API; compile one lifecycle tick."""
        try:
            normal = normalize_ground_navigation_request(request)
            task_id = normal.task_id or normal.request_id
            if task_id not in self._tasks:
                self.dispatch(normal, observation=observation)
            dt_s = observation.get("dt_s", 0.1) if isinstance(observation, Mapping) else 0.1
            return self.control(normal, observation=observation, dt_s=dt_s).plan
        except SkillContractError:
            return SkillPlan(
                request_id=request.request_id, skill_id=request.skill_id, robot_id=request.robot_id,
                status="invalid_request",
                metadata={"reason_code": "invalid_request", "schema_version": "simulator.skill.v1",
                          "provider_id": self.provider_id, "provider_version": self.provider_version,
                          "physical_outcome": "external_evaluator_required"})

    def fail(self, task_id: str, error: Exception, *,
             reason_code: str = "execution_failure") -> GroundNavigationResponse:
        """Allow the executor to invalidate a command whose application failed."""
        if reason_code not in {"execution_failure", "timeout"}:
            raise SkillContractError("invalid failure reason")
        task = self._tasks[task_id]
        self._finish(task, "failed", reason_code)
        task.private_evidence["execution_error_type"] = type(error).__name__
        return self.response(task_id)

    @staticmethod
    def _zero(task: _GroundTask) -> None:
        if task.tracker is not None:
            task.tracker.reset_pid()
        task.previous_command = [0.0, 0.0, 0.0]

    def _finish(self, task: _GroundTask, status: str, reason_code: str) -> None:
        self._zero(task)
        task.status, task.reason_code, task.terminal = status, reason_code, True
        task.execution_state, task.safe_action = "holding", "stop"

    def response(self, task_id: str) -> GroundNavigationResponse:
        task = self._tasks[task_id]
        state = {key: getattr(task, key) for key in (
            "status", "execution_state", "safe_action", "world_state_seq", "reason_code")}
        state["task_id"] = task_id
        # Canonical JSON keeps provenance stable across dictionary insertion order.
        digest = hashlib.sha256(json.dumps(task.request.to_mapping(), sort_keys=True,
                                          separators=(",", ":")).encode("utf-8")).hexdigest()
        plan = SkillPlan(
            request_id=task.request.request_id, skill_id=task.request.skill_id,
            robot_id=task.request.robot_id,
            commands=(SkillCommand("base_velocity", {
                "velocity": list(task.previous_command), "frame": "base_link",
                "horizon_ticks": task.request.parameters.get("horizon_ticks", 1)}),),
            metadata={"schema_version": task.request.skill_version,
                      "provider_id": self.provider_id, "provider_version": self.provider_version,
                      "request_digest": digest, "command_route": COMMAND_ROUTE,
                      "physical_outcome": "external_evaluator_required"})
        evidence = deepcopy(task.private_evidence)
        if task.tracker is not None:
            evidence["node_index"] = task.tracker.node_index
        return GroundNavigationResponse(state, plan, evidence)


__all__ = [
    "GroundNavigationProvider", "GroundNavigationResponse", "GroundPose",
    "GroundTrackingConfig", "NodeRouteTracker", "select_node_route",
]
