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

def _segment_obstruction(points: Sequence[Sequence[float]], obstacles: Sequence[Mapping[str, Any]],
                         *, half_xy: float, z_window: tuple[float, float]) -> bool:
    """True when any polyline segment passes within half_xy of a z-overlapping obstacle bbox."""
    for obstacle in obstacles:
        lo, hi = obstacle.get("bounds_m") or ((), ())
        if len(lo) < 3 or len(hi) < 3:
            continue
        if hi[2] < z_window[0] or lo[2] > z_window[1]:
            continue
        for index in range(len(points) - 1):
            ax, ay = float(points[index][0]), float(points[index][1])
            bx, by = float(points[index + 1][0]), float(points[index + 1][1])
            cx = min(max((ax + bx) * .5, lo[0]), hi[0])
            cy = min(max((ay + by) * .5, lo[1]), hi[1])
            # 到线段的最近点（钳位到 bbox 的是近似，保守起见用段中点+端点）
            for px, py in ((ax, ay), (bx, by), ((ax + bx) * .5, (ay + by) * .5)):
                qx = min(max(px, lo[0]), hi[0])
                qy = min(max(py, lo[1]), hi[1])
                if math.hypot(px - qx, py - qy) < half_xy:
                    return True
    return False


def _clear_route(points: Sequence[Sequence[float]], obstacles: Sequence[Mapping[str, Any]],
                 *, half_xy: float, route_z: float) -> tuple[tuple[tuple[float, float, float], ...], bool]:
    """Lateral-offset search until the swept polyline clears every static obstacle.

    人行道折线通常贴路缘，扫掠人形包络会命中路灯/建筑。对整条折线沿其
    净法向做确定性横向偏移搜索（先 +后 -，步长 0.25 m 至 2.5 m），返回
    第一组全段清空的偏移点与成功标志；无解时如实返回 (原线, False)。
    """
    original = tuple((float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else route_z)
                     for p in points)
    z_window = (route_z - 1.5, route_z + 1.8)
    if not _segment_obstruction(original, obstacles, half_xy=half_xy, z_window=z_window):
        return original, True
    if len(original) >= 2:
        ax, ay = original[0][0], original[0][1]
        bx, by = original[-1][0], original[-1][1]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / norm, dx / norm
        for sign in (1.0, -1.0):
            for step in range(1, 11):
                offset = sign * 0.25 * step
                shifted = tuple((px + offset * nx, py + offset * ny, pz) for px, py, pz in original)
                if not _segment_obstruction(shifted, obstacles, half_xy=half_xy, z_window=z_window):
                    return shifted, True
    return original, False


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
    selected_assemblies = list(assemblies)
    rng.shuffle(selected_assemblies)
    surface_heights = semantic_map.get("surface_heights_m", {})
    route_z = float(surface_heights.get("sidewalks", surface_heights.get("ground", 0.3)))
    people = []
    for index, (route, assembly) in enumerate(zip(selected_routes[:count], selected_assemblies * count)):
        points = tuple((*_point(point)[:2], route_z) for point in route["points"])
        geometry = assembly.get("geometry", {})
        if geometry.get("type") != "Point":
            raise ValueError(f"assembly candidate '{assembly.get('id')}' is not a point")
        assembly_xy = _point(geometry["coordinates"])
        assembly_point = (assembly_xy[0], assembly_xy[1], route_z)
        # 阶段19 C1 实测：贴路缘的人行道折线 + 人形半宽扫掠会命中路灯/
        # 建筑（正式 admission 的 routes_clear 谓词拒绝）。生成时先做横向
        # 偏移清障；无解时如实保留原线（fail-closed 由审核层拒绝）。
        static_obstacles = tuple(semantic_map.get("static_obstacles", ()))
        person_half = 0.3
        cleared, ok = _clear_route((*points, assembly_point), static_obstacles,
                                   half_xy=person_half, route_z=route_z)
        # 集合点必须保持原位：若平移移动了它，还原末端并复检末腿
        if ok and cleared[-1] != assembly_point:
            moved_end = cleared[-1]
            fixed = (*cleared[:-1], assembly_point)
            leg_ok = not _segment_obstruction((moved_end, assembly_point), static_obstacles,
                                              half_xy=person_half,
                                              z_window=(route_z - 1.5, route_z + 1.8))
            cleared, ok = (fixed, leg_ok) if leg_ok else (points + (assembly_point,), False)
        evacuation_route = cleared
        people.append(ResponsePersonSpec(
            person_id=f"affected_person_{index:02d}", asset_id=asset_ids[index % len(asset_ids)],
            start_xyz=points[0], evacuation_route=evacuation_route,
            assembly_region_id=str(assembly["id"]), affected=True))
    return people


__all__ = ["CityResponseServices", "HumanEvacuationBinding", "PeopleResponseRuntime",
           "ResponsePersonSpec", "build_response_people", "spawn_response_people"]
