"""Unified controller independent node-route navigation Skill.

The Provider owns target-reference resolution, deterministic Dijkstra, node
progress, timing and lifecycle state.  It only emits one ``navigation_target``
node at a time.  Controllers are deliberately outside this module.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
import hashlib
import heapq
import json
import math
from typing import Any, Mapping, Sequence

from ..contracts import SkillCommand, SkillContractError, SkillKind, SkillPlan, SkillRequest
from ..simulator_schema import SimulatorSchemaError, validate_simulator_skill_request
from .topology import FrozenNavigationTopology, route_graph_digest, topology_fields, validate_topology


NODE_ROUTE_SKILL_ID = "eai.navigation.node_route"


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SkillContractError(f"{label} must be finite")
    return float(value)


def _xyz(value: Sequence[float], label: str = "position") -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise SkillContractError(f"{label} must contain XYZ")
    return tuple(_finite(item, label) for item in value)


def normalize_angle(value: Any) -> float:
    value = _finite(value, "yaw")
    return math.atan2(math.sin(value), math.cos(value))


@dataclass(frozen=True)
class NavigationPose:
    position_m: tuple[float, float, float]
    yaw_rad: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "position_m", _xyz(self.position_m))
        object.__setattr__(self, "yaw_rad", normalize_angle(self.yaw_rad))


@dataclass(frozen=True)
class NavigationProfile:
    """Geometry semantics and node arrival policy for one adapter."""

    metric: str
    arrival_radius_m: float = 0.30
    supports_final_heading: bool = False
    controller_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.metric not in {"xy", "xyz"}:
            raise SkillContractError("navigation metric must be xy or xyz")
        radius = _finite(self.arrival_radius_m, "arrival_radius_m")
        if radius <= 0:
            raise SkillContractError("arrival_radius_m must be positive")
        if type(self.supports_final_heading) is not bool:
            raise SkillContractError("supports_final_heading must be boolean")
        capabilities = self.controller_capabilities
        if isinstance(capabilities, (str, bytes)):
            raise SkillContractError("controller_capabilities must be a string sequence")
        try:
            capabilities = tuple(capabilities)
        except TypeError as exc:
            raise SkillContractError("controller_capabilities must be a string sequence") from exc
        if any(not isinstance(item, str) or not item.strip() for item in capabilities):
            raise SkillContractError("controller_capabilities must contain nonempty strings")
        if len(set(capabilities)) != len(capabilities):
            raise SkillContractError("controller_capabilities must be unique")
        object.__setattr__(self, "controller_capabilities", tuple(sorted(capabilities)))
        object.__setattr__(self, "arrival_radius_m", radius)


def ground_navigation_profile(*, arrival_radius_m: float = 0.30,
                               supports_final_heading: bool = True,
                               controller_capabilities: Sequence[str] = ()) -> NavigationProfile:
    return NavigationProfile("xy", arrival_radius_m, supports_final_heading,
                             tuple(controller_capabilities))


def aerial_navigation_profile(*, arrival_radius_m: float = 0.30,
                               supports_final_heading: bool = False,
                               controller_capabilities: Sequence[str] = ()) -> NavigationProfile:
    return NavigationProfile("xyz", arrival_radius_m, supports_final_heading,
                             tuple(controller_capabilities))


@dataclass(frozen=True)
class NavigationTarget:
    node_id: str
    position_m: tuple[float, float, float]
    node_index: int
    heading_rad: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "position_m", _xyz(self.position_m, "target_pose"))
        if not isinstance(self.node_id, str) or not self.node_id.strip():
            raise SkillContractError("navigation target node_id must be nonempty")
        if type(self.node_index) is not int or self.node_index < 0:
            raise SkillContractError("navigation target node_index must be nonnegative")
        if self.heading_rad is not None:
            object.__setattr__(self, "heading_rad", normalize_angle(self.heading_rad))


@dataclass(frozen=True)
class NavigationObservation:
    pose: NavigationPose
    world_state_seq: int
    simulation_time_s: float = 0.0
    valid: bool = True
    fresh: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.pose, NavigationPose):
            raise SkillContractError("navigation observation pose is invalid")
        if type(self.world_state_seq) is not int or self.world_state_seq < 0:
            raise SkillContractError("world_state_seq must be a nonnegative integer")
        value = _finite(self.simulation_time_s, "simulation_time_s")
        if value < 0:
            raise SkillContractError("simulation_time_s must be nonnegative")
        object.__setattr__(self, "simulation_time_s", value)
        if type(self.valid) is not bool or type(self.fresh) is not bool:
            raise SkillContractError("observation validity flags must be boolean")


@dataclass(frozen=True)
class NavigationFeedback:
    status: str = "running"  # running | completed | failed | stopped
    stopped: bool = False
    settled: bool = False
    reason: str = ""

    def __post_init__(self) -> None:
        if self.status not in {"running", "completed", "failed", "stopped"}:
            raise SkillContractError("invalid navigation feedback status")
        if type(self.stopped) is not bool or type(self.settled) is not bool:
            raise SkillContractError("navigation feedback flags must be boolean")
        if not isinstance(self.reason, str):
            raise SkillContractError("navigation feedback reason must be a string")


def _pose_from(value: Any) -> NavigationPose:
    if isinstance(value, NavigationPose):
        return value
    position = getattr(value, "position_m", None)
    yaw = getattr(value, "yaw_rad", 0.0)
    if isinstance(value, Mapping):
        position = value.get("position_m", value.get("position", value.get("position_xyz", position)))
        yaw = value.get("yaw_rad", value.get("yaw", yaw))
    return NavigationPose(_xyz(position), yaw)


def _metric_distance(left: Sequence[float], right: Sequence[float], profile: NavigationProfile) -> float:
    return math.dist(left[:2], right[:2]) if profile.metric == "xy" else math.dist(left, right)


def _rows(document: Any) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    data = topology_fields(document)
    return list(data.get("nodes", ())), list(data.get("edges", ()))


def _target_candidates(nodes: Mapping[str, Mapping[str, Any]], target_ref: str) -> set[str]:
    if target_ref in nodes:
        return {target_ref}
    return {node_id for node_id, row in nodes.items()
            if target_ref in set(row.get("target_refs", ())) }


def _semantic_binding(document: Any, query: str, *, role: str) -> Mapping[str, Any] | None:
    """Resolve a semantic-map name without making the core graph depend on UI code."""
    data = topology_fields(document)
    if data.get("semantics") is None:
        return None
    try:
        from .landmarks import resolve_landmark_binding
        return resolve_landmark_binding(document, query, role=role)
    except SkillContractError:
        raise
    except (ImportError, AttributeError, KeyError, TypeError, ValueError) as exc:
        raise SkillContractError(f"invalid semantic navigation binding: {exc}") from exc


def select_node_route(document: Any, pose: NavigationPose | Any, *,
                      profile: NavigationProfile,
                      target_ref: str,
                      origin_ref: str | None = None,
                      controller_capabilities: Sequence[str] | None = None,
                      target_node_ids: Sequence[str] | None = None,
                      origin_node_ids: Sequence[str] | None = None,
                      allowed_edge_ids: Sequence[str] | None = None,
                      target_heading_rad: float | None = None,
                      target_is_semantic: bool | None = None,
                      origin_is_semantic: bool | None = None) -> dict[str, Any]:
    """Resolve a target reference and select a deterministic node route."""
    if not isinstance(profile, NavigationProfile):
        raise SkillContractError("navigation profile is required")
    if not isinstance(target_ref, str) or not target_ref.strip():
        raise SkillContractError("target_ref must be a nonempty string")
    errors = validate_topology(document, metric=profile.metric)
    if errors:
        raise SkillContractError("invalid navigation topology: " + "; ".join(errors))
    pose = _pose_from(pose)
    rows, edges = _rows(document)
    node_rows = {row["id"]: row for row in rows}
    nodes = {node_id: _xyz(row["position_xyz"], "node position")
             for node_id, row in node_rows.items()}
    digest = route_graph_digest(document)
    base = {"admitted": False, "route_graph_digest": digest, "metric": profile.metric}
    if not nodes:
        return {**base, "reason": "topology_start_unconnected"}
    target_binding = None
    if target_node_ids is None:
        try:
            target_binding = _semantic_binding(document, target_ref, role="target")
        except SkillContractError as exc:
            if "ambiguous" in str(exc).casefold():
                return {**base, "reason": "target_ambiguous", "target_ref": target_ref}
            # An unresolved semantic query is a normal admission failure; raw
            # target_refs still get the legacy resolver below.
            target_binding = None
        candidates = set(target_binding.get("node_ids", ())) if target_binding else _target_candidates(node_rows, target_ref)
    else:
        candidates = set(target_node_ids)
    candidates &= set(nodes)
    if not candidates:
        return {**base, "reason": "target_unresolved", "target_ref": target_ref}
    semantic_target = (target_binding is not None and target_binding.get("collection") != "nodes")
    if target_is_semantic is not None:
        semantic_target = bool(target_is_semantic)
    if len(candidates) > 1 and not semantic_target:
        return {**base, "reason": "target_ambiguous", "target_ref": target_ref,
                "candidate_node_ids": sorted(candidates)}

    # A named semantic route is an authored corridor, not merely a label for
    # its final node.  Preserve that contract for direct Provider calls as
    # well as for the higher-level landmark route helper.
    if (allowed_edge_ids is None and target_binding is not None
            and target_binding.get("collection") == "routes"):
        route_edges = target_binding.get("record", {}).get("edge_ids", ())
        if isinstance(route_edges, (list, tuple)):
            allowed_edge_ids = tuple(route_edges)

    origin_binding = None
    if origin_ref is not None:
        if origin_node_ids is None:
            try:
                origin_binding = _semantic_binding(document, origin_ref, role="origin")
            except SkillContractError as exc:
                if "ambiguous" in str(exc).casefold():
                    return {**base, "reason": "origin_unresolved", "origin_ref": origin_ref}
                origin_binding = None
            starts = set(origin_binding.get("node_ids", ())) if origin_binding else _target_candidates(node_rows, origin_ref)
        else:
            starts = set(origin_node_ids)
        starts &= set(nodes)
        if not starts:
            return {**base, "reason": "origin_unresolved", "origin_ref": origin_ref}
        semantic_origin = (origin_binding is not None and origin_binding.get("collection") != "nodes")
        if origin_is_semantic is not None:
            semantic_origin = bool(origin_is_semantic)
        if len(starts) > 1 and not semantic_origin:
            return {**base, "reason": "origin_unresolved", "origin_ref": origin_ref}
        start = min(starts, key=lambda node_id: (_metric_distance(pose.position_m, nodes[node_id], profile), node_id))
        if _metric_distance(pose.position_m, nodes[start], profile) > profile.arrival_radius_m:
            return {**base, "reason": "origin_not_reached", "start_node_id": start}
    else:
        start = min(nodes, key=lambda node_id: (_metric_distance(pose.position_m, nodes[node_id], profile), node_id))
        if _metric_distance(pose.position_m, nodes[start], profile) > profile.arrival_radius_m:
            return {**base, "reason": "origin_not_reached", "start_node_id": start}

    capabilities = (tuple(controller_capabilities)
                    if controller_capabilities is not None
                    else profile.controller_capabilities)
    if isinstance(capabilities, (str, bytes)):
        raise SkillContractError("controller_capabilities must be a string sequence")
    if any(not isinstance(item, str) or not item.strip() for item in capabilities):
        raise SkillContractError("controller_capabilities must contain nonempty strings")
    if len(set(capabilities)) != len(capabilities):
        raise SkillContractError("controller_capabilities must be unique")
    capability_set = set(capabilities)
    allowed = set(allowed_edge_ids) if allowed_edge_ids is not None else None
    traversal_by_id = {edge.get("id"): edge.get("traversal_mode", "walk")
                       for edge in edges if isinstance(edge, Mapping)}
    adjacency: dict[str, list[tuple[str, str, float, str, tuple[str, ...]]]] = {node_id: [] for node_id in nodes}
    for edge in edges:
        first, last, edge_id = edge.get("from"), edge.get("to"), edge.get("id")
        if first not in nodes or last not in nodes:
            continue
        if allowed is not None and edge_id not in allowed:
            continue
        mode = edge.get("traversal_mode", "walk")
        required = tuple(dict.fromkeys((mode, *edge.get("required_controller_capabilities", ()))))
        if controller_capabilities is not None and not set(required).issubset(capability_set):
            continue
        distance = _metric_distance(nodes[first], nodes[last], profile)
        adjacency[first].append((last, edge_id, distance, mode, required))
        if not edge.get("directed", False):
            adjacency[last].append((first, edge_id, distance, mode, required))

    queue: list[tuple[float, tuple[str, ...], str, tuple[str, ...]]] = [(0.0, (start,), start, ())]
    best: dict[str, tuple[float, tuple[str, ...]]] = {}
    while queue:
        distance, path, node_id, traversed = heapq.heappop(queue)
        prior = best.get(node_id)
        if prior is not None and (distance > prior[0] + 1e-12 or
                                  (abs(distance - prior[0]) <= 1e-12 and path >= prior[1])):
            continue
        best[node_id] = (distance, path)
        if node_id in candidates:
            heading = node_rows[node_id].get("heading_rad")
            if target_heading_rad is None and target_binding is not None:
                target_heading_rad = target_binding.get("record", {}).get("heading_rad")
            return {
                **base, "admitted": True, "target_ref": target_ref,
                "route_points_m": [list(nodes[item]) for item in path],
                "topology_node_ids": list(path), "topology_edge_ids": list(traversed),
                "route_length_m": distance,
                "target_heading_rad": target_heading_rad if target_heading_rad is not None else heading,
                "height_aware": profile.metric == "xyz",
                "controller_capabilities": sorted(capability_set),
                "traversal_modes": [traversal_by_id.get(edge_id, "walk")
                                    for edge_id in traversed],
            }
        for neighbor, edge_id, cost, _mode, _required in sorted(adjacency[node_id], key=lambda item: (item[0], item[1])):
            if neighbor in path:
                continue
            candidate_path = (*path, neighbor)
            candidate_distance = distance + cost
            old = best.get(neighbor)
            if old is None or candidate_distance < old[0] - 1e-12 or (
                    abs(candidate_distance - old[0]) <= 1e-12 and candidate_path < old[1]):
                heapq.heappush(queue, (candidate_distance, candidate_path, neighbor,
                                       (*traversed, edge_id)))
    return {**base, "reason": "route_unreachable", "target_ref": target_ref}


@dataclass
class NodeRouteTracker:
    route_points_m: Sequence[Sequence[float]]
    profile: NavigationProfile
    node_ids: Sequence[str] = ()
    final_heading_rad: float | None = None
    heading_tolerance_rad: float = 0.10
    node_index: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self.route_points_m = tuple(_xyz(point, "route point") for point in self.route_points_m)
        if not self.route_points_m:
            raise SkillContractError("node route must not be empty")
        self.node_ids = tuple(self.node_ids)
        if len(self.node_ids) != len(self.route_points_m):
            raise SkillContractError("node_ids must match route_points_m")
        if self.final_heading_rad is not None:
            if not self.profile.supports_final_heading:
                raise SkillContractError("navigation profile does not support final heading")
            self.final_heading_rad = normalize_angle(self.final_heading_rad)
        self.heading_tolerance_rad = _finite(self.heading_tolerance_rad, "heading_tolerance_rad")
        if not 0 <= self.heading_tolerance_rad <= math.pi:
            raise SkillContractError("heading_tolerance_rad must be in [0, pi]")

    @property
    def arrived(self) -> bool:
        return self.node_index >= len(self.route_points_m)

    @property
    def target(self) -> NavigationTarget | None:
        if self.arrived:
            return None
        index = self.node_index
        heading = self.final_heading_rad if index == len(self.route_points_m) - 1 else None
        node_id = self.node_ids[index]
        return NavigationTarget(node_id, self.route_points_m[index], index, heading)

    def advance(self, pose: NavigationPose | Any) -> NavigationTarget | None:
        pose = _pose_from(pose)
        while not self.arrived and _metric_distance(pose.position_m, self.route_points_m[self.node_index], self.profile) <= self.profile.arrival_radius_m:
            if self.node_index == len(self.route_points_m) - 1 and self.final_heading_rad is not None:
                if abs(normalize_angle(self.final_heading_rad - pose.yaw_rad)) > self.heading_tolerance_rad:
                    return self.target
            self.node_index += 1
        return self.target


def _request_value(value: SkillRequest | Mapping[str, Any]) -> SkillRequest:
    request = value if isinstance(value, SkillRequest) else SkillRequest.from_mapping(value)
    if request.skill_id != NODE_ROUTE_SKILL_ID:
        raise SkillContractError("request is not eai.navigation.node_route")
    if request.target_pose is not None:
        raise SkillContractError("node-route navigation accepts target_ref only")
    if not isinstance(request.target_ref, str) or not request.target_ref.strip():
        raise SkillContractError("node-route navigation requires target_ref")
    try:
        validate_simulator_skill_request(request)
    except SimulatorSchemaError as exc:
        raise SkillContractError(str(exc)) from exc
    return deepcopy(request)


def normalize_navigation_request(value: SkillRequest | Mapping[str, Any]) -> SkillRequest:
    return _request_value(value)


def _request_identity(request: SkillRequest) -> str:
    value = request.to_mapping()
    for name in ("request_id", "task_id", "simulator_snapshot"):
        value.pop(name, None)
    value["parameters"].pop("intent", None)
    value["parameters"].pop("world_state_seq", None)
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _profile_snapshot(profile: NavigationProfile | None) -> dict[str, Any] | None:
    if profile is None:
        return None
    return {
        "metric": profile.metric,
        "arrival_radius_m": profile.arrival_radius_m,
        "supports_final_heading": profile.supports_final_heading,
        "controller_capabilities": list(profile.controller_capabilities),
    }


def _pose_snapshot(pose: NavigationPose | None) -> dict[str, Any] | None:
    if pose is None:
        return None
    return {"position_m": list(pose.position_m), "yaw_rad": pose.yaw_rad}


def _target_snapshot(target: NavigationTarget | None) -> dict[str, Any] | None:
    if target is None:
        return None
    return {
        "node_id": target.node_id,
        "position_m": list(target.position_m),
        "node_index": target.node_index,
        "heading_rad": target.heading_rad,
    }


def _observation(value: NavigationObservation | Mapping[str, Any] | None) -> NavigationObservation:
    if isinstance(value, NavigationObservation):
        if not value.valid:
            raise SkillContractError("navigation observation is invalid")
        return value
    if not isinstance(value, Mapping) or value.get("valid") is not True:
        raise SkillContractError("navigation observation is invalid")
    pose = _pose_from(value.get("pose"))
    seq = value.get("world_state_seq")
    if type(seq) is not int or seq < 0:
        raise SkillContractError("world_state_seq must be a nonnegative integer")
    return NavigationObservation(pose, seq, value.get("simulation_time_s", 0.0),
                                 value.get("valid") is True, value.get("fresh") is True)


@dataclass
class _NavigationTask:
    request: SkillRequest
    profile: NavigationProfile
    task_id: str
    world_state_seq: int
    identity: str
    status: str = "pending"
    execution_state: str = "holding"
    safe_action: str = "hold"
    reason_code: str = "awaiting_start"
    tracker: NodeRouteTracker | None = None
    route_graph_digest: str | None = None
    current_target: NavigationTarget | None = None
    last_pose: NavigationPose | None = None
    private_evidence: dict[str, Any] = field(default_factory=dict)
    terminal: bool = False
    started_s: float | None = None
    last_time_s: float | None = None


@dataclass(frozen=True)
class NavigationResponse:
    state: Mapping[str, Any]
    plan: SkillPlan
    private_evidence: Mapping[str, Any]

    def to_mapping(self) -> dict[str, Any]:
        return {"navigation_state": dict(self.state), "skill_plan": self.plan.to_mapping()}


class NavigationProvider:
    skill_id = NODE_ROUTE_SKILL_ID
    kind = SkillKind.NAVIGATION
    provider_id = "node_route.provider"
    provider_version = "2.0"

    def __init__(self, topology: Any | None = None, *, profile: NavigationProfile | None = None):
        self.topology = (topology if isinstance(topology, FrozenNavigationTopology)
                         else deepcopy(topology_fields(topology)) if topology is not None else None)
        self.profile = profile
        self._tasks: dict[str, _NavigationTask] = {}

    @property
    def active_task_ids(self) -> tuple[str, ...]:
        return tuple(sorted(key for key, task in self._tasks.items() if not task.terminal))

    def measurement_snapshot(self) -> dict[str, Any]:
        """Return detached, JSON-safe declared state without providing restore."""

        topology = None if self.topology is None else self._topology()
        tasks = {}
        for task_id in sorted(self._tasks):
            task = self._tasks[task_id]
            tracker = None
            if task.tracker is not None:
                tracker = {
                    "route_points_m": [list(point) for point in task.tracker.route_points_m],
                    "node_ids": list(task.tracker.node_ids),
                    "final_heading_rad": task.tracker.final_heading_rad,
                    "heading_tolerance_rad": task.tracker.heading_tolerance_rad,
                    "node_index": task.tracker.node_index,
                    "arrived": task.tracker.arrived,
                    "target": _target_snapshot(task.tracker.target),
                }
            tasks[task_id] = {
                "request": task.request.to_mapping(),
                "profile": _profile_snapshot(task.profile),
                "task_id": task.task_id,
                "world_state_seq": task.world_state_seq,
                "request_identity": task.identity,
                "status": task.status,
                "execution_state": task.execution_state,
                "safe_action": task.safe_action,
                "reason_code": task.reason_code,
                "tracker": tracker,
                "route_graph_digest": task.route_graph_digest,
                "current_target": _target_snapshot(task.current_target),
                "last_pose": _pose_snapshot(task.last_pose),
                "private_evidence": deepcopy(task.private_evidence),
                "terminal": task.terminal,
                "started_s": task.started_s,
                "last_time_s": task.last_time_s,
            }
        snapshot = {
            "schema_version": "navigation.provider.measurement.v1",
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "profile": _profile_snapshot(self.profile),
            "topology": deepcopy(topology),
            "topology_digest": None if topology is None else route_graph_digest(topology),
            "active_task_ids": list(self.active_task_ids),
            "tasks": tasks,
        }
        return json.loads(json.dumps(snapshot, sort_keys=True, allow_nan=False))

    def _topology(self, supplied: Any | None = None) -> Any:
        value = supplied if supplied is not None else self.topology
        if isinstance(value, FrozenNavigationTopology):
            return value.snapshot()
        if value is None:
            raise SkillContractError("navigation provider requires a bound topology")
        return value

    def _profile(self, profile: NavigationProfile | None) -> NavigationProfile:
        selected = profile or self.profile
        if not isinstance(selected, NavigationProfile):
            raise SkillContractError("navigation provider requires a profile")
        return selected

    def _response(self, task: _NavigationTask) -> NavigationResponse:
        target = task.current_target
        commands = ()
        if target is not None and task.safe_action in {"track", "arrived"}:
            payload = {"node_id": target.node_id, "node_index": target.node_index,
                       "position_xyz": list(target.position_m)}
            if target.heading_rad is not None:
                payload["target_yaw"] = target.heading_rad
            commands = (SkillCommand("navigation_target", payload),)
        state = {
            "task_id": task.task_id, "status": task.status,
            "execution_state": task.execution_state, "safe_action": task.safe_action,
            "reason_code": task.reason_code,
            "world_state_seq": task.world_state_seq,
            "route_graph_digest": task.route_graph_digest,
            "node_index": None if task.tracker is None else task.tracker.node_index,
            "target_node_id": None if target is None else target.node_id,
            "metric": task.profile.metric,
        }
        plan = SkillPlan(task.request.request_id, self.skill_id, task.request.robot_id,
                         commands, status="planned" if task.status == "pending" else task.status,
                         metadata={"provider_id": self.provider_id, "provider_version": self.provider_version})
        return NavigationResponse(state, plan, deepcopy(task.private_evidence))

    def dispatch(self, value: SkillRequest | Mapping[str, Any], *,
                 observation: NavigationObservation | Mapping[str, Any] | None = None,
                 profile: NavigationProfile | None = None) -> NavigationResponse:
        request = _request_value(value)
        task_id = request.task_id or request.request_id
        if task_id in self._tasks:
            raise SkillContractError("navigation task already exists")
        selected = self._profile(profile)
        document = self._topology()
        errors = validate_topology(document)
        if errors:
            raise SkillContractError("invalid navigation topology: " + "; ".join(errors))
        obs = _observation(observation) if observation is not None else None
        if obs is not None and obs.world_state_seq != request.parameters["world_state_seq"]:
            raise SkillContractError("navigation observation sequence differs from request")
        if request.parameters.get("target_yaw") is not None and not selected.supports_final_heading:
            raise SkillContractError("navigation profile does not support final heading")
        task = _NavigationTask(request, selected, task_id, request.parameters["world_state_seq"], _request_identity(request))
        task.route_graph_digest = route_graph_digest(document)
        if request.parameters.get("route_graph_digest", task.route_graph_digest) != task.route_graph_digest:
            raise SkillContractError("topology differs from requested route_graph_digest")
        if obs is not None:
            task.last_pose, task.last_time_s = obs.pose, obs.simulation_time_s
        self._tasks[task_id] = task
        return self._response(task)

    def _get_task(self, request: SkillRequest) -> _NavigationTask:
        key = request.task_id or request.request_id
        task = self._tasks.get(key)
        if task is None and request.task_id is None:
            matches = [item for item in self._tasks.values() if not item.terminal
                       and item.request.robot_id == request.robot_id
                       and item.request.target_ref == request.target_ref]
            if len(matches) == 1:
                task = matches[0]
        if task is None or task.terminal:
            raise SkillContractError("navigation task is not active")
        return task

    @staticmethod
    def _timeout(task: _NavigationTask, request: SkillRequest, obs: NavigationObservation) -> bool:
        now = obs.simulation_time_s
        if task.last_time_s is not None and now < task.last_time_s:
            raise SkillContractError("simulation time moved backwards")
        if task.started_s is None:
            task.started_s = now
        task.last_time_s = now
        timing, budget = request.timing or {}, request.budget or {}
        if "deadline_s" in timing and now >= timing["deadline_s"]:
            return True
        durations = [item["duration_s"] for item in (timing, budget) if "duration_s" in item]
        return bool(durations) and now - task.started_s >= min(durations)

    def control(self, value: SkillRequest | Mapping[str, Any], *,
                observation: NavigationObservation | Mapping[str, Any] | None = None,
                topology: Any | None = None) -> NavigationResponse:
        request = _request_value(value)
        task = self._get_task(request)
        try:
            return self._control(request, task, observation=observation, topology=topology)
        except (SkillContractError, ValueError, TypeError) as exc:
            return self.fail(task.task_id, str(exc), reason_code="invalid_state")

    def _control(self, request: SkillRequest, task: _NavigationTask, *,
                 observation: NavigationObservation | Mapping[str, Any] | None,
                 topology: Any | None) -> NavigationResponse:
        if request.robot_id != task.request.robot_id or request.target_ref != task.request.target_ref:
            raise SkillContractError("navigation request does not match task")
        if _request_identity(request) != task.identity:
            raise SkillContractError("navigation identity, parameters and budget are immutable")
        seq = request.parameters["world_state_seq"]
        if seq < task.world_state_seq:
            raise SkillContractError("navigation world_state_seq is expired")
        obs = _observation(observation)
        if obs.world_state_seq != seq:
            raise SkillContractError("navigation observation sequence differs from request")
        intent = request.parameters.get("intent", "start")
        if intent in {"start", "continue", "replan"} and not obs.fresh:
            raise SkillContractError("navigation motion requires a fresh observation")
        if task.last_time_s is not None and obs.simulation_time_s < task.last_time_s:
            raise SkillContractError("simulation time moved backwards")
        task.last_pose, task.world_state_seq = obs.pose, seq
        if intent in {"hold", "stop"}:
            task.last_time_s = obs.simulation_time_s
        if task.route_graph_digest is not None and route_graph_digest(self._topology(topology)) != task.route_graph_digest:
            raise SkillContractError("navigation route graph identity changed")
        if intent in {"start", "continue", "replan"} and self._timeout(task, request, obs):
            return self.fail(task.task_id, "navigation timeout", reason_code="timeout")
        if intent == "hold":
            task.current_target = None
            task.execution_state, task.safe_action, task.reason_code = "holding", "hold", "system_hold"
            return self._response(task)
        if intent == "stop":
            task.current_target = None
            task.execution_state, task.safe_action, task.reason_code = "holding", "stop", "cancelled"
            task.status, task.terminal = "cancelled", True
            return self._response(task)
        if intent == "continue" and task.tracker is None:
            task.current_target = None
            task.execution_state, task.safe_action, task.reason_code = "holding", "hold", "awaiting_start"
            return self._response(task)
        if intent in {"start", "replan"}:
            selected = select_node_route(self._topology(topology), obs.pose, profile=task.profile,
                                         target_ref=request.target_ref,
                                         origin_ref=request.parameters.get("origin_ref") if intent == "start" else None,
                                         controller_capabilities=(task.profile.controller_capabilities
                                                                  if task.profile.controller_capabilities else None))
            expected = request.parameters.get("route_graph_digest")
            if expected is not None and expected != selected["route_graph_digest"]:
                raise SkillContractError("topology differs from requested route_graph_digest")
            if not selected.get("admitted"):
                return self.fail(task.task_id, selected.get("reason", "route_unreachable"),
                                 reason_code=selected.get("reason", "route_unreachable"), evidence=selected)
            if task.route_graph_digest is not None and task.route_graph_digest != selected["route_graph_digest"]:
                raise SkillContractError("navigation route graph identity changed")
            task.route_graph_digest = selected["route_graph_digest"]
            task.private_evidence["route_selection"] = deepcopy(selected)
            final_heading = request.parameters.get("target_yaw")
            if final_heading is None and task.profile.supports_final_heading:
                final_heading = selected.get("target_heading_rad")
            task.tracker = NodeRouteTracker(selected["route_points_m"], task.profile,
                                            selected["topology_node_ids"], final_heading)
        if task.tracker is None:
            raise SkillContractError("navigation route has not been started")
        target = task.tracker.advance(obs.pose)
        task.current_target = target
        if target is None:
            last = len(task.tracker.route_points_m) - 1
            task.current_target = NavigationTarget(task.tracker.node_ids[last],
                task.tracker.route_points_m[last], last, task.tracker.final_heading_rad)
        if task.tracker.arrived:
            task.status, task.execution_state, task.safe_action, task.reason_code = "completed", "holding", "arrived", "arrived"
            task.terminal = True
        else:
            task.execution_state, task.safe_action, task.reason_code = "moving", "track", "tracking_node"
        return self._response(task)

    def plan(self, value: SkillRequest | Mapping[str, Any], *,
             observation: NavigationObservation | Mapping[str, Any] | None = None) -> SkillPlan:
        request = _request_value(value)
        response = self.dispatch(request, observation=observation)
        if request.parameters.get("intent", "start") in {"start", "replan"} and observation is not None:
            response = self.control(request, observation=observation)
        return response.plan

    def fail(self, task_id: str, reason: str | Exception, *, reason_code: str = "execution_failure",
             evidence: Mapping[str, Any] | None = None) -> NavigationResponse:
        task = self._tasks.get(task_id)
        if task is None:
            raise SkillContractError("unknown navigation task")
        task.status, task.execution_state, task.safe_action, task.reason_code = "failed", "holding", "stop", reason_code
        task.current_target = None
        task.terminal = True
        task.private_evidence["failure"] = {"reason": str(reason), "reason_code": reason_code}
        if evidence is not None:
            task.private_evidence["route_selection"] = deepcopy(dict(evidence))
        return self._response(task)

    def response(self, task_id: str) -> NavigationResponse:
        task = self._tasks.get(task_id)
        if task is None:
            raise SkillContractError("unknown navigation task")
        return self._response(task)


__all__ = [
    "NODE_ROUTE_SKILL_ID", "NavigationFeedback", "NavigationObservation",
    "NavigationPose", "NavigationProfile", "NavigationProvider",
    "NavigationResponse", "NavigationTarget", "NodeRouteTracker",
    "aerial_navigation_profile", "ground_navigation_profile",
    "normalize_navigation_request", "normalize_angle", "select_node_route",
]
