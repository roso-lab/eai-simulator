"""City response actors and services used by the benchmark preparation layer.

The module is deliberately split from ``demo.city_benchmark.scene``.  It provides a pure
episode model for authoring and a small adapter for the existing USD human
runtime.  The adapter changes a human actor's visible locomotion to ``jog``
only after an acknowledged guidance command; it does not turn background
animation into evidence by itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
import random
from typing import Any, Iterable, Mapping, Sequence


def _point(value: Sequence[float]) -> tuple[float, float, float]:
    if len(value) not in (2, 3):
        raise ValueError("a response point must contain two or three values")
    values = tuple(float(v) for v in value)
    if not all(math.isfinite(v) for v in values):
        raise ValueError("response points must be finite")
    return (values[0], values[1], values[2] if len(values) == 3 else 0.0)


class _Polyline:
    def __init__(self, points: Iterable[Sequence[float]]):
        self.points = tuple(_point(point) for point in points)
        if len(self.points) < 2:
            raise ValueError("a response route requires at least two points")
        self.lengths = tuple(math.dist(a, b) for a, b in zip(self.points, self.points[1:]))
        if not any(length > 1e-9 for length in self.lengths):
            raise ValueError("a response route requires non-zero length")
        self.total = sum(self.lengths)

    def sample(self, distance: float) -> tuple[float, float, float]:
        remaining = max(0.0, min(float(distance), self.total))
        for start, end, length in zip(self.points, self.points[1:], self.lengths):
            if length <= 1e-9:
                continue
            if remaining <= length:
                fraction = remaining / length
                return tuple(a + (b - a) * fraction for a, b in zip(start, end))  # type: ignore[return-value]
            remaining -= length
        return self.points[-1]


@dataclass(frozen=True)
class ResponsePersonSpec:
    person_id: str
    asset_id: str
    start_xyz: tuple[float, float, float]
    evacuation_route: tuple[tuple[float, float, float], ...]
    assembly_region_id: str
    walk_speed_mps: float = 0.8
    jog_speed_mps: float = 1.8
    affected: bool = True

    def __post_init__(self) -> None:
        if not self.person_id or not self.asset_id or not self.assembly_region_id:
            raise ValueError("response person identifiers must be non-empty")
        if any(not math.isfinite(v) for v in (*self.start_xyz, self.walk_speed_mps, self.jog_speed_mps)):
            raise ValueError("response person values must be finite")
        if self.walk_speed_mps <= 0 or self.jog_speed_mps <= self.walk_speed_mps:
            raise ValueError("jog speed must be greater than walking speed")
        _Polyline(self.evacuation_route)


@dataclass
class ResponsePersonState:
    spec: ResponsePersonSpec
    position_xyz: tuple[float, float, float]
    status: str = "awaiting_guidance"
    motion: str = "walk"
    guidance_acknowledged: bool = False
    guidance_time_s: float | None = None
    arrival_time_s: float | None = None
    distance_m: float = 0.0


class PeopleResponseRuntime:
    """Deterministic, evidence-producing people response model.

    It models only the service boundary needed by the benchmark: observation,
    guidance acknowledgement, movement, and arrival accounting.  It does not
    claim that a robot can force a person to comply.
    """

    def __init__(self, people: Iterable[ResponsePersonSpec]):
        specs = tuple(people)
        ids = [person.person_id for person in specs]
        if len(ids) != len(set(ids)):
            raise ValueError("response person ids must be unique")
        self._routes = {person.person_id: _Polyline(person.evacuation_route) for person in specs}
        self._states = {
            person.person_id: ResponsePersonState(person, person.start_xyz)
            for person in specs
        }
        self.time_s = 0.0
        self._observation_counter = 0
        self._observations: dict[str, tuple[str, ...]] = {}
        self.events: list[dict[str, Any]] = []

    @property
    def person_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._states))

    def observe(self) -> list[dict[str, Any]]:
        """Return current dynamic actor observations for a participant adapter."""
        self._observation_counter += 1
        observation_ref = f"people_observation_{self._observation_counter:03d}"
        person_ids = self.person_ids
        self._observations[observation_ref] = person_ids
        rows = [{
            "person_id": state.spec.person_id,
            "position_xyz": list(state.position_xyz),
            "status": state.status,
            "guidance_acknowledged": state.guidance_acknowledged,
            "assembly_region_id": state.spec.assembly_region_id,
            "observation_ref": observation_ref,
        } for state in (self._states[person_id] for person_id in self.person_ids)]
        self.events.append({"type": "people_observation", "observation_ref": observation_ref,
                            "person_ids": list(person_ids), "time_s": self.time_s})
        return rows

    def guide(self, person_ids: Iterable[str], *, observation_refs: Iterable[str] | None = None,
              now_s: float | None = None) -> list[dict[str, Any]]:
        """Issue guidance for IDs present in a delivered observation snapshot."""
        return self._guide(person_ids, observation_refs=observation_refs, now_s=now_s)

    def _guide(self, person_ids: Iterable[str], *, observation_refs: Iterable[str] | None,
               now_s: float | None) -> list[dict[str, Any]]:
        timestamp = self.time_s if now_s is None else float(now_s)
        if timestamp < self.time_s:
            raise ValueError("guidance time cannot move backwards")
        selected = tuple(str(person_id) for person_id in person_ids)
        if not selected:
            raise ValueError("guidance requires at least one person")
        if observation_refs is None:
            raise ValueError("guidance requires delivered observation references")
        refs = tuple(str(ref) for ref in observation_refs)
        if not refs or any(ref not in self._observations for ref in refs):
            raise ValueError("guidance references an unknown observation")
        observed_ids = {person_id for ref in refs for person_id in self._observations[ref]}
        if not set(selected) <= observed_ids:
            raise ValueError("guidance may reference only people in delivered observations")
        changes = []
        for person_id in selected:
            try:
                state = self._states[person_id]
            except KeyError as exc:
                raise KeyError(f"unknown observed person: {person_id}") from exc
            if not state.spec.affected:
                raise ValueError(f"person '{person_id}' is not in the affected roster")
            if state.status == "arrived":
                continue
            state.status = "evacuating"
            state.motion = "jog"
            state.guidance_acknowledged = True
            state.guidance_time_s = timestamp
            event = {"type": "guidance_acknowledged", "person_id": person_id, "time_s": timestamp,
                     "assembly_region_id": state.spec.assembly_region_id, "motion": "jog"}
            self.events.append(event)
            changes.append(event)
        self.time_s = timestamp
        return changes

    def guide_observed(self, person_ids: Iterable[str], observation_refs: Iterable[str], *,
                       now_s: float | None = None) -> list[dict[str, Any]]:
        """Public adapter entry point requiring the evidence used for guidance."""
        return self.guide(person_ids, observation_refs=observation_refs, now_s=now_s)

    def validate_assembly(self, person_ids: Iterable[str], assembly_region_id: str) -> None:
        """Check a requested assembly binding without creating a new observation."""
        for person_id in person_ids:
            state = self._state(person_id)
            if state.spec.assembly_region_id != assembly_region_id:
                raise ValueError("selected person does not belong to the requested assembly region")

    def mark_unresponsive(self, person_id: str) -> None:
        state = self._state(person_id)
        if state.status != "arrived":
            state.status = "unresponsive"
            state.motion = "walk"
            self.events.append({"type": "guidance_unresponsive", "person_id": person_id, "time_s": self.time_s})

    def step(self, dt: float) -> tuple[dict[str, Any], ...]:
        dt = float(dt)
        if not math.isfinite(dt) or dt < 0:
            raise ValueError("dt must be non-negative and finite")
        self.time_s += dt
        emitted = []
        for state in self._states.values():
            if state.status != "evacuating":
                continue
            state.distance_m += state.spec.jog_speed_mps * dt
            route = self._routes[state.spec.person_id]
            state.position_xyz = route.sample(state.distance_m)
            if state.distance_m >= route.total - 1e-9:
                state.distance_m = route.total
                state.position_xyz = route.sample(route.total)
                state.status = "arrived"
                state.motion = "walk"
                state.arrival_time_s = self.time_s
                event = {"type": "assembly_arrival", "person_id": state.spec.person_id,
                         "time_s": self.time_s, "assembly_region_id": state.spec.assembly_region_id}
                self.events.append(event)
                emitted.append(event)
        return tuple(emitted)

    def _state(self, person_id: str) -> ResponsePersonState:
        try:
            return self._states[str(person_id)]
        except KeyError as exc:
            raise KeyError(f"unknown response person: {person_id}") from exc

    def accounting(self) -> dict[str, Any]:
        states = tuple(self._states.values())
        return {
            "observed_count": len(states),
            "affected_count": sum(state.spec.affected for state in states),
            "acknowledged_count": sum(state.guidance_acknowledged for state in states if state.spec.affected),
            "arrived_count": sum(state.status == "arrived" for state in states if state.spec.affected),
            "unresponsive_count": sum(state.status == "unresponsive" for state in states if state.spec.affected),
            "all_affected_arrived": all(state.status == "arrived" for state in states if state.spec.affected),
        }


class HumanEvacuationBinding:
    """Bridge ``PeopleResponseRuntime`` decisions to USD human animation."""

    def __init__(self, stage_runtime: Any, people: Mapping[str, ResponsePersonSpec]):
        self.stage_runtime = stage_runtime
        self.people = dict(people)

    def start(self, person_ids: Iterable[str], *, playback_speed: float = 1.0) -> tuple[dict[str, Any], ...]:
        selected = tuple(str(person_id) for person_id in person_ids)
        if not selected:
            raise ValueError("human evacuation binding requires at least one person")
        events = []
        for person_id in selected:
            try:
                spec = self.people[person_id]
            except KeyError as exc:
                raise KeyError(f"no human binding for person '{person_id}'") from exc
            # The registry validates that this asset supports jog and that a
            # retarget cache exists when play_action is sampled.
            self.stage_runtime.play_action(person_id, "jog", loop=True, playback_speed=playback_speed)
            self.stage_runtime.set_path_speed(person_id, spec.jog_speed_mps)
            events.append({"type": "human_motion_switched", "person_id": person_id,
                           "motion": "jog", "speed_mps": spec.jog_speed_mps})
        return tuple(events)

    def finish(self, person_ids: Iterable[str]) -> tuple[dict[str, Any], ...]:
        events = []
        for person_id in tuple(str(value) for value in person_ids):
            spec = self.people[person_id]
            self.stage_runtime.cancel_action(person_id)
            self.stage_runtime.set_path_speed(person_id, spec.walk_speed_mps)
            events.append({"type": "human_motion_restored", "person_id": person_id,
                           "motion": "walk", "speed_mps": spec.walk_speed_mps})
        return tuple(events)


class CityResponseServices:
    """Explicit adapter used by a later participant runtime integration."""

    def __init__(self, people_runtime: PeopleResponseRuntime, traffic_runtime: Any,
                 incident_runtime: Any):
        self.people_runtime = people_runtime
        self.traffic_runtime = traffic_runtime
        self.incident_runtime = incident_runtime

    def guide_people(self, *, person_ids: Iterable[str], observation_refs: Iterable[str],
                     assembly_region_id: str | None = None, now_s: float | None = None):
        selected = tuple(str(person_id) for person_id in person_ids)
        if assembly_region_id is not None:
            self.people_runtime.validate_assembly(selected, assembly_region_id)
        return self.people_runtime.guide_observed(selected, observation_refs, now_s=now_s)

    def control_following_traffic(self, vehicle_ids: Iterable[str], mode: str,
                                  *, observation_refs: Iterable[str]):
        """Submit a control request for following traffic; acknowledgement is separate."""
        return self.traffic_runtime.request(vehicle_ids, mode, observation_refs=observation_refs)

    def report_incident(self, observation_refs: Iterable[str], recipient_service_id: str, *, time_s: float):
        return self.incident_runtime.submit(observation_refs, recipient_service_id, time_s=time_s)


def spawn_response_people(stage: Any, people: Sequence[ResponsePersonSpec], *, root_path: str = "/World/CityResponsePeople"):
    """Spawn affected people on a USD stage for later benchmark integration.

    This is intentionally an explicit opt-in deployment helper; the original
    city demo continues to spawn scenery pedestrians through ``demo.city_benchmark.scene``.
    """
    from pxr import Usd, UsdPhysics
    from EAI_assets.humans.asset_placement import grounding_world_range
    from EAI_assets.humans.registry import HumanAssetRegistry
    from EAI_assets.humans.stage_runtime import HumanActorConfig, UsdHumanStageRuntime

    registry = HumanAssetRegistry.load_default()
    runtime = UsdHumanStageRuntime(stage, registry, cache_root=registry.human_root / "motions/cache")
    records = []
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        for spec in people:
            if not spec.evacuation_route:
                raise ValueError(f"response person '{spec.person_id}' has no route")
            first, second = spec.evacuation_route[:2]
            yaw = math.atan2(second[1] - first[1], second[0] - first[0])
            prim_path = f"{root_path}/{spec.person_id}"
            runtime.spawn(HumanActorConfig(
                actor_id=spec.person_id, asset_id=spec.asset_id, prim_path=prim_path,
                initial_pose=(*spec.start_xyz, yaw), waypoints=spec.evacuation_route,
                speed=spec.walk_speed_mps, loop=False, locomotion_motion_id="walk"))
            for prim in Usd.PrimRange(stage.GetPrimAtPath(prim_path)):
                if prim.HasAPI(UsdPhysics.RigidBodyAPI):
                    UsdPhysics.RigidBodyAPI(prim).CreateRigidBodyEnabledAttr(False)
                if prim.HasAPI(UsdPhysics.CollisionAPI):
                    UsdPhysics.CollisionAPI(prim).CreateCollisionEnabledAttr(False)
            bounds = grounding_world_range(stage.GetPrimAtPath(prim_path))
            records.append({"person_id": spec.person_id, "asset_id": spec.asset_id,
                            "prim_path": prim_path, "motion": "walk",
                            "walk_speed_mps": spec.walk_speed_mps,
                            "jog_speed_mps": spec.jog_speed_mps,
                            "assembly_region_id": spec.assembly_region_id,
                            "height_m": float(bounds.GetMax()[2] - bounds.GetMin()[2])})
    return runtime, HumanEvacuationBinding(runtime, {spec.person_id: spec for spec in people}), records

def _obstacle_grid(obstacles: Sequence[Mapping[str, Any]], *, half_xy: float,
                   z_window: tuple[float, float]) -> frozenset[tuple[int, int]]:
    """把静态障碍 bbox 栅格化（0.5 m 格，含 half_xy 膨胀），供段级清障检查。

    z 窗口覆盖行人行走高度带的障碍才入栅。返回被占用格集合（格索引 =
    (int(x*2), int(y*2))）。
    """
    cells: set[tuple[int, int]] = set()
    for obstacle in obstacles:
        lo, hi = obstacle.get("bounds_m") or ((), ())
        if len(lo) < 3 or len(hi) < 3:
            continue
        if hi[2] < z_window[0] or lo[2] > z_window[1]:
            continue
        # 分级膨胀：细障碍（灯杆等 <1.5 m 包络）的 bbox≈实物，膨胀 0.05
        # 即满足 0.3 人形半宽；一律 0.2 会把沿街人行道封成与主通行域隔离
        # 的口袋（阶段19 C1 实测：出生点被迫重定位 71 m，路线 188 m）。
        extent = max(hi[0] - lo[0], hi[1] - lo[1])
        margin = 0.05 if extent < 1.5 else half_xy
        x0 = int(math.floor((lo[0] - margin) * 2))
        x1 = int(math.ceil((hi[0] + margin) * 2))
        y0 = int(math.floor((lo[1] - margin) * 2))
        y1 = int(math.ceil((hi[1] + margin) * 2))
        for ix in range(x0, x1 + 1):
            for iy in range(y0, y1 + 1):
                cells.add((ix, iy))
    return frozenset(cells)


def _segment_obstruction(points: Sequence[Sequence[float]], *, grid,
                         half_xy: float = 0.0, z_window=None) -> bool:
    """True when the polyline crosses any occupied grid cell (0.25 m dense sampling)."""
    if not grid:
        return False
    for index in range(len(points) - 1):
        ax, ay = float(points[index][0]), float(points[index][1])
        bx, by = float(points[index + 1][0]), float(points[index + 1][1])
        length = math.hypot(bx - ax, by - ay)
        steps = max(1, int(length / 0.25))
        for step in range(steps + 1):
            t = step / steps
            px, py = ax + (bx - ax) * t, ay + (by - ay) * t
            if (int(px * 2), int(py * 2)) in grid:
                return True
    return False


def _astar_leg(grid, start_xy, goal_xy, *, max_expand=200000):
    """在 0.5 m 障碍栅格上从 start 到 goal 的 8 连通 A*，返回世界坐标航点。

    无解或超限返回 None。航点为栅格中心，够稀疏（折点保留）。
    """
    import heapq
    start = (int(start_xy[0] * 2), int(start_xy[1] * 2))
    goal = (int(goal_xy[0] * 2), int(goal_xy[1] * 2))
    if start in grid or goal in grid:
        return None
    heap = [(0.0, start)]
    came: dict[tuple[int, int], tuple[int, int]] = {start: start}
    cost = {start: 0.0}
    expanded = 0
    while heap:
        _, current = heapq.heappop(heap)
        if current == goal:
            break
        expanded += 1
        if expanded > max_expand:
            return None
        # 4 连通（无对角）：对角步会切角，吃掉膨胀余量 ~0.1 m，实测致灯
        # Mesh 命中（阶段19 C1 复盘）。
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nxt = (current[0] + dx, current[1] + dy)
                if nxt in grid or nxt in came:
                    continue
                step = 0.5
                cost[nxt] = cost[current] + step
                heuristic = math.hypot(nxt[0] - goal[0], nxt[1] - goal[1]) * 0.5
                heapq.heappush(heap, (cost[nxt] + heuristic, nxt))
                came[nxt] = current
    if goal not in came:
        return None
    path, node = [goal], goal
    while node != start:
        node = came[node]
        path.append(node)
    path.reverse()
    # 折点简化：只保留方向变化处
    simplified = [path[0]]
    for index in range(1, len(path) - 1):
        d0 = (path[index][0] - path[index - 1][0], path[index][1] - path[index - 1][1])
        d1 = (path[index + 1][0] - path[index][0], path[index + 1][1] - path[index][1])
        if d0 != d1:
            simplified.append(path[index])
    simplified.append(path[-1])
    return [(ix * 0.5 + 0.25, iy * 0.5 + 0.25) for ix, iy in simplified]


def _nearest_clear_point(grid, xy, *, max_radius_m: float = 15.0):
    """从 xy 起环形搜索最近的未占用格中心（0.5 m 栅格）。"""
    import itertools
    cx, cy = int(xy[0] * 2), int(xy[1] * 2)
    if (cx, cy) not in grid:
        return (float(xy[0]), float(xy[1]))
    for radius in range(1, int(max_radius_m * 2) + 1):
        best = None
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                if max(abs(dx), abs(dy)) != radius:
                    continue
                cell = (cx + dx, cy + dy)
                if cell not in grid:
                    dist = dx * dx + dy * dy
                    if best is None or dist < best[0]:
                        best = (dist, cell)
        if best:
            return (best[1][0] * 0.5 + 0.25, best[1][1] * 0.5 + 0.25)
    return None


def _nearest_clear_in_goal_component(grid, xy, goal_xy, *,
                                     max_radius_m: float = 60.0,
                                     max_cells: int = 300000):
    """目标连通域内、离 xy 不超过 max_radius_m 的最近空格（世界坐标）。

    人行道口袋可能被膨胀障碍完全封闭（与主通行域不连通）；此时把出生点
    重定位到目标连通域内、原点半径内的最近可达格——语义为该员从可通行
    位置出现。半径界防止在空旷栅格上无界扩张把出生点搬到目标旁。
    """
    goal = (int(goal_xy[0] * 2), int(goal_xy[1] * 2))
    if goal in grid:
        return None
    origin = (float(xy[0]), float(xy[1]))
    best, best_dist = None, None
    seen = {goal}
    stack = [goal]
    limit = max_radius_m * 2
    while stack:
        if len(seen) > max_cells:
            break
        cx, cy = stack.pop()
        wx, wy = cx * 0.5 + 0.25, cy * 0.5 + 0.25
        dist = math.hypot(wx - origin[0], wy - origin[1])
        if best is None or dist < best_dist:
            best, best_dist = (wx, wy), dist
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nxt = (cx + dx, cy + dy)
            if nxt not in grid and nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return best


def _clear_route(points: Sequence[Sequence[float]], *, grid, route_z: float,
                 assembly_point=None) -> tuple[tuple[tuple[float, float, float], ...], bool]:
    """清障：环路平移 + 末段 A* 绕障。

    人行道折线贴路缘（扫掠命中路灯），且到集合区的末段可能是横穿全城的
    直线（必中建筑）。策略（确定性）：先整体横向平移（±0.25..2.5 m）清
    环路；末段（折线终点→集合点）用 0.5 m 障碍栅格上的 A* 绕障航点替换
    （集合点保持原位）。全程以栅格密集采样复检；无解如实返回 (原线, False)。
    """
    original = tuple((float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else route_z)
                     for p in points)
    if _segment_obstruction(original, grid=grid):
        if len(original) >= 2:
            ax, ay = original[0][0], original[0][1]
            bx, by = original[-1][0], original[-1][1]
            dx, dy = bx - ax, by - ay
            norm = math.hypot(dx, dy) or 1.0
            nx, ny = -dy / norm, dx / norm
            for sign in (1.0, -1.0):
                for step in range(1, 11):
                    offset = sign * 0.25 * step
                    shifted = tuple((px + offset * nx, py + offset * ny, pz)
                                    for px, py, pz in original)
                    if not _segment_obstruction(shifted, grid=grid):
                        original = shifted
                        break
                else:
                    continue
                break
    loop_clear = not _segment_obstruction(original, grid=grid)
    if assembly_point is None:
        return original, loop_clear
    leg = _astar_leg(grid, (original[-1][0], original[-1][1]),
                     (assembly_point[0], assembly_point[1]))
    if leg is None:
        return (*original, tuple(assembly_point)), False
    waypoints = (*original,
                 *((px, py, route_z) for px, py in leg[:-1]),
                 tuple(assembly_point))
    ok = loop_clear and not _segment_obstruction(waypoints, grid=grid)
    return waypoints, ok


def build_response_people(semantic_map: Mapping[str, Any], *, count: int = 4, seed: int = 7,
                          asset_ids: Sequence[str] = ("rp-aaron-rigged-001", "rp-amit-rigged-008-yup-t",
                                                       "synbody-0000001", "synbody-0000020")) -> list[ResponsePersonSpec]:
    """Create a deterministic private episode roster from authored city routes."""
    routes = tuple(semantic_map.get("pedestrian_routes", ()))
    assemblies = tuple(row for row in semantic_map.get("bindings", ())
                       if str(row.get("id", "")).startswith("assembly_candidate_"))
    if count < 0 or count > len(routes) or not assemblies:
        raise ValueError("city response roster requires enough pedestrian routes and assembly candidates")
    rng = random.Random(seed)
    selected_routes = list(routes)
    rng.shuffle(selected_routes)
    # 就近配对（小规模全排列最优匹配）：随机 shuffle 会把人员派往 150 m 外
    # 的集合区（阶段19 C1 实测：长路线任意时刻都会与站等人员/行驶车辆包络
    # 相交，admission 永久拒绝）；疏散语义本身也是就近集结。人数超过集合
    # 区数时循环复用（合成测试图；真实图为 4 人对 4 区）。
    import itertools
    surface_heights = semantic_map.get("surface_heights_m", {})
    route_z = float(surface_heights.get("sidewalks", surface_heights.get("ground", 0.3)))
    people = []
    origins = []
    for route in selected_routes[:count]:
        points = tuple((*_point(point)[:2], route_z) for point in route["points"])
        origins.append(points)
    def _assembly_xy(row):
        return _point(row.get("geometry", {}).get("coordinates", (0, 0)))[:2]
    pool = list(assemblies)
    reuse = len(pool) < len(origins)
    if reuse:
        # 合成图集合区少于人数：逐人取最近并允许复用（不消耗）。
        pairing = tuple(min(range(len(pool)),
                            key=lambda j: (math.dist(origins[i][0][:2], _assembly_xy(pool[j])),
                                           str(pool[j].get("id", ""))))
                        for i in range(len(origins)))
    else:
        best_pairing, best_cost = None, None
        for indices in itertools.permutations(range(len(pool)), len(origins)):
            cost = sum(math.dist(origins[i][0][:2], _assembly_xy(pool[j]))
                       for i, j in enumerate(indices))
            if best_cost is None or cost < best_cost:
                best_cost, best_pairing = cost, indices
        pairing = best_pairing
    for index, route in enumerate(selected_routes[:count]):
        points = origins[index]
        assembly = pool[pairing[index]]
        geometry = assembly.get("geometry", {})
        if geometry.get("type") != "Point":
            raise ValueError(f"assembly candidate '{assembly.get('id')}' is not a point")
        assembly_xy = _point(geometry["coordinates"])
        assembly_point = (assembly_xy[0], assembly_xy[1], route_z)
        # 阶段19 C1（终版）：人行道环路贴路缘且被灯杆/建筑 bbox 膨胀栅格
        # 覆盖——环路的语义只是出生点来源。疏散路线重定义为：出生点取环路
        # 首点附近最近可通行格，路线 = 0.5 m 障碍栅格上的 A* 绕障航点
        # （集合点保持原位）；无解时如实保留原线（fail-closed 由审核层拒绝）。
        static_obstacles = tuple(semantic_map.get("static_obstacles", ()))
        # 膨胀 0.2：灯杆 bbox≈杆径，加 0.2 后对真实 Mesh 仍 ≥0.3 人形半宽；
        # 0.35 会把街道走廊封死（起点落入与主通行域不连通的口袋）。
        grid = _obstacle_grid(static_obstacles, half_xy=0.2,
                              z_window=(route_z - 1.5, route_z + 1.8))
        origin_xy = (points[0][0], points[0][1])
        if (int(origin_xy[0] * 2), int(origin_xy[1] * 2)) not in grid:
            start_clear = origin_xy  # 原格空闲：不吸附不搬动
        else:
            start_clear = (_nearest_clear_in_goal_component(
                grid, origin_xy, (assembly_point[0], assembly_point[1]))
                or _nearest_clear_point(grid, origin_xy))
        leg = (_astar_leg(grid, start_clear,
                          (assembly_point[0], assembly_point[1]))
               if start_clear else None)
        if leg:
            spawn_xyz = (start_clear[0], start_clear[1], route_z)
            evacuation_route = ((spawn_xyz,)
                                + tuple((px, py, route_z) for px, py in leg[1:-1])
                                + (tuple(assembly_point),))
            route_ok = not _segment_obstruction(evacuation_route, grid=grid)
        else:
            spawn_xyz = points[0]
            evacuation_route = points + (tuple(assembly_point),)
            route_ok = False
        people.append(ResponsePersonSpec(
            person_id=f"affected_person_{index:02d}", asset_id=asset_ids[index % len(asset_ids)],
            start_xyz=spawn_xyz, evacuation_route=evacuation_route,
            assembly_region_id=str(assembly["id"]), affected=True))
    return people


__all__ = ["CityResponseServices", "HumanEvacuationBinding", "PeopleResponseRuntime",
           "ResponsePersonSpec", "build_response_people", "spawn_response_people"]
