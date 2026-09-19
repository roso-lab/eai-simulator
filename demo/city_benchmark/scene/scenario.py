"""Pure, serialisable city accident planning.

The planner consumes bounds and route centerlines supplied by an adapter.  It
never imports USD/Kit, opens a stage, or depends on the legacy demo adapter.
Coordinates are metres in a Z-up world; route geometry may be 2-D or 3-D.
"""
from __future__ import annotations

import bisect
import json
import math
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


IGNITION_TIME = 2.5
IMPACT_SPEED_MPS = 18.0
BUILDING_IGNITION_DELAY = 1.5
FIRE_SPREAD_DURATION = 4.5
SCENARIO_END_TIME = 20.0

Vec3 = tuple[float, float, float]
Bounds = tuple[Vec3, Vec3]


def _vec(value: Sequence[float], z: float = 0.0) -> Vec3:
    values = tuple(float(v) for v in value)
    if len(values) == 2:
        return values[0], values[1], z
    if len(values) >= 3:
        return values[0], values[1], values[2]
    raise ValueError("A point must have two or three coordinates")


def lerp(a: Sequence[float], b: Sequence[float], fraction: float) -> Vec3:
    return tuple(float(x) + (float(y) - float(x)) * fraction for x, y in zip(a, b))  # type: ignore[return-value]


def smoothstep(value: float) -> float:
    value = max(0.0, min(1.0, float(value)))
    return value * value * (3.0 - 2.0 * value)


class Polyline:
    """Sample an authored route by arc length, preserving corner headings."""

    def __init__(self, points: Iterable[Sequence[float]]):
        values = list(points)
        if len(values) < 2:
            raise ValueError("A vehicle route requires at least two points")
        self.points = [_vec(values[0])]
        self.distances = [0.0]
        for point in values[1:]:
            point = _vec(point)
            length = math.dist(self.points[-1], point)
            if length > 1e-8:
                self.points.append(point)
                self.distances.append(self.distances[-1] + length)
        if len(self.points) < 2 or self.distances[-1] <= 1e-8:
            raise ValueError("A vehicle route requires two distinct points")
        self.length = self.distances[-1]

    def sample(self, distance: float, *, loop: bool = False) -> tuple[Vec3, float]:
        distance = float(distance)
        distance = distance % self.length if loop else max(0.0, min(self.length, distance))
        index = min(len(self.points) - 2, max(0, bisect.bisect_right(self.distances, distance) - 1))
        segment = self.distances[index + 1] - self.distances[index]
        fraction = (distance - self.distances[index]) / segment
        a, b = self.points[index:index + 2]
        point = lerp(a, b, fraction)
        yaw = math.atan2(b[1] - a[1], b[0] - a[0])
        return point, yaw

    def as_list(self) -> list[list[float]]:
        return [[float(x), float(y), float(z)] for x, y, z in self.points]


@dataclass(frozen=True)
class AccidentTiming:
    ignition_time: float = IGNITION_TIME
    building_ignition_delay: float = BUILDING_IGNITION_DELAY
    fire_spread_duration: float = FIRE_SPREAD_DURATION
    scenario_end_time: float = SCENARIO_END_TIME

    def __post_init__(self) -> None:
        if any(float(v) < 0 for v in (self.ignition_time, self.building_ignition_delay, self.fire_spread_duration)):
            raise ValueError("Accident timing values must be non-negative")


@dataclass(frozen=True)
class AccidentSpec:
    """JSON-safe immutable description of one reproducible accident."""

    route: tuple[Vec3, ...]
    facade_point: Vec3
    seed: int = 0
    vehicle_length: float = 4.6
    road_height: float = 0.2
    impact_speed_mps: float = IMPACT_SPEED_MPS
    timing: AccidentTiming = field(default_factory=AccidentTiming)

    def __post_init__(self) -> None:
        if self.vehicle_length <= 0 or self.impact_speed_mps <= 0:
            raise ValueError("Vehicle length and impact speed must be positive")
        if len(self.route) < 2:
            raise ValueError("Accident route requires at least two points")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["route"] = [list(point) for point in self.route]
        data["facade_point"] = list(self.facade_point)
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AccidentSpec":
        timing = AccidentTiming(**dict(data.get("timing", {})))
        return cls(
            route=tuple(_vec(point) for point in data["route"]),
            facade_point=_vec(data["facade_point"]), seed=int(data.get("seed", 0)),
            vehicle_length=float(data.get("vehicle_length", 4.6)),
            road_height=float(data.get("road_height", 0.2)),
            impact_speed_mps=float(data.get("impact_speed_mps", IMPACT_SPEED_MPS)),
            timing=timing,
        )

    @classmethod
    def from_json(cls, value: str) -> "AccidentSpec":
        return cls.from_dict(json.loads(value))


# AccidentConfig is the public spelling used by scenario callers.
AccidentConfig = AccidentSpec


@dataclass(frozen=True)
class AccidentState:
    phase: str
    car_position: tuple[float, float]
    car_yaw: float
    effect_position: Vec3
    fire_radius: float
    smoke_radius: float
    burning: bool
    car_speed_mps: float


class AccidentPlan:
    """Sample one car that reaches a measured facade and stops at impact."""

    def __init__(self, route: Iterable[Sequence[float]], facade_point: Sequence[float], *, vehicle_length: float = 4.6,
                 road_height: float = 0.2, impact_speed_mps: float = IMPACT_SPEED_MPS,
                 timing: AccidentTiming | None = None, seed: int = 0):
        self.route = Polyline(route)
        self.facade_point = tuple(_vec(facade_point)[:2])
        self.vehicle_length = float(vehicle_length)
        self.road_height = float(road_height)
        self.impact_speed_mps = float(impact_speed_mps)
        self.timing = timing or AccidentTiming()
        self.seed = int(seed)
        self.acceleration_start = self.timing.ignition_time + 0.7
        self.initial_speed = min(2.0, self.route.length / max(self.acceleration_start * 4.0, 1e-6))
        self.lead_distance = self.initial_speed * self.acceleration_start
        remaining = max(self.route.length - self.lead_distance, 1e-6)
        self.acceleration = (self.impact_speed_mps ** 2 - self.initial_speed ** 2) / (2 * remaining)
        if self.acceleration <= 0:
            raise ValueError("Accident route is too short for the requested impact speed")
        self.impact_time = self.acceleration_start + (self.impact_speed_mps - self.initial_speed) / self.acceleration
        self.building_ignition_time = self.impact_time + self.timing.building_ignition_delay
        self.spread_complete_time = self.building_ignition_time + self.timing.fire_spread_duration
        self.spec = AccidentSpec(
            tuple(self.route.points), (*self.facade_point, self.road_height), seed=self.seed,
            vehicle_length=self.vehicle_length, road_height=self.road_height,
            impact_speed_mps=self.impact_speed_mps, timing=self.timing,
        )

    def sample(self, elapsed: float) -> AccidentState:
        elapsed = max(0.0, float(elapsed))
        if elapsed < self.acceleration_start:
            distance, speed = self.initial_speed * elapsed, self.initial_speed
        elif elapsed < self.impact_time:
            accelerating_time = elapsed - self.acceleration_start
            speed = self.initial_speed + self.acceleration * accelerating_time
            distance = self.lead_distance + self.initial_speed * accelerating_time + 0.5 * self.acceleration * accelerating_time ** 2
        else:
            distance, speed = self.route.length, 0.0
        position, yaw = self.route.sample(distance)
        bonnet = (position[0] + math.cos(yaw) * self.vehicle_length * 0.30,
                  position[1] + math.sin(yaw) * self.vehicle_length * 0.30,
                  self.road_height + 1.1)
        spread = smoothstep((elapsed - self.building_ignition_time) / max(self.timing.fire_spread_duration, 1e-6))
        wall_fire = (self.facade_point[0], self.facade_point[1], self.road_height + 3.8)
        effect = lerp(bonnet, wall_fire, spread)
        burning = elapsed >= self.timing.ignition_time
        growth = smoothstep((elapsed - self.timing.ignition_time) / 1.0)
        phase = ("traffic" if elapsed < self.timing.ignition_time else
                 "vehicle_fire" if elapsed < self.impact_time else
                 "impact" if elapsed < self.building_ignition_time else "building_fire")
        return AccidentState(phase, (position[0], position[1]), yaw, effect,
                             (0.65 + 1.75 * spread) * growth,
                             (0.55 + 1.45 * spread) * growth, burning, speed)


@dataclass(frozen=True)
class BuildingCandidate:
    path: str
    bounds: Bounds
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CityScenarioConfig:
    seed: int = 0
    vehicle_length: float = 4.6
    road_height: float = 0.2
    ground_height: float = 0.22
    max_background_cars: int = 6
    central_target_candidates: int = 4
    traffic_spawn_gap: float = 18.0
    impact_speed_mps: float = IMPACT_SPEED_MPS
    add_triangle_colliders: bool = True
    validate_obstacles: bool = True
    seat_buildings: bool = True
    use_wall_probe: bool = True
    timing: AccidentTiming = field(default_factory=AccidentTiming)


def load_vehicle_lanes(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as stream:
        data = json.load(stream)
    lanes = []
    for route in data.get("vehicle_routes", ()):
        points = route.get("points") or route.get("centerline")
        if points and len(points) >= 2:
            lanes.append({"lane_id": route.get("route_id", route.get("lane_id", "route")), "centerline": points})
    if not lanes:
        raise ValueError(f"No usable vehicle routes in {path}")
    return lanes


def _xy_point_in_bounds(point: Sequence[float], bounds: Bounds, margin: float = 0.0) -> bool:
    lo, hi = bounds
    return lo[0] - margin <= point[0] <= hi[0] + margin and lo[1] - margin <= point[1] <= hi[1] + margin


def _segments_intersect_obstacle(points: Sequence[Sequence[float]], bounds: Bounds, margin: float) -> bool:
    lo, hi = bounds
    expanded = ((lo[0] - margin, lo[1] - margin), (hi[0] + margin, hi[1] + margin))
    for point in points:
        if _xy_point_in_bounds(point, bounds, margin):
            return True
    for a, b in zip(points, points[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        steps = max(2, int(length / 0.5) + 1)
        if any(_xy_point_in_bounds((a[0] + dx * i / steps, a[1] + dy * i / steps), bounds, margin) for i in range(steps + 1)):
            return True
    return False


def choose_accident_plan(bounds: Bounds, lanes: Iterable[dict[str, Any]], *, vehicle_length: float = 4.6,
                         road_height: float = 0.2, wall_probe: Callable | None = None,
                         impact_speed_mps: float = IMPACT_SPEED_MPS, timing: AccidentTiming | None = None,
                         seed: int = 0, obstacles: Iterable[Bounds] = ()) -> tuple[AccidentPlan, str, tuple[float, float]]:
    """Build a wall-contact route while rejecting conservative swept-volume hits."""
    lo, hi = bounds
    center = ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2)
    candidates = []
    for lane in lanes:
        line = list(lane.get("centerline", ()))
        for a, b in zip(line, line[1:]):
            length = math.dist(a, b)
            if length < 24.0:
                continue
            tangent = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
            along = min(length - 5.0, max(12.0, sum((center[i] - a[i]) * tangent[i] for i in range(2))))
            road = tuple(a[i] + along * tangent[i] for i in range(2))
            facade = (min(hi[0], max(lo[0], road[0])), min(hi[1], max(lo[1], road[1])))
            gap = math.dist(road, facade)
            if gap < vehicle_length / 2 + 1.0:
                continue
            lead_in = min(24.0, along - 1.0)
            candidates.append((gap + 2.0 * (24.0 - lead_in), road, tangent, facade,
                              lane.get("lane_id", "route"), lead_in))
    if not candidates:
        raise RuntimeError("No clear lane approach found near the target building")
    obstacles = tuple(obstacles)
    failures = []
    for _, road, tangent, envelope_facade, lane_id, lead_in in sorted(candidates, key=lambda item: (item[0], item[4])):
        direction_distance = math.dist(road, envelope_facade)
        if direction_distance <= 1e-8:
            continue
        direction = tuple((envelope_facade[i] - road[i]) / direction_distance for i in range(2))
        facade = envelope_facade
        if wall_probe is not None:
            origin = (*road, road_height + 0.8)
            try:
                hit = wall_probe(origin, (*direction, 0.0))
            except Exception as exc:
                failures.append(f"{lane_id}: wall probe failed ({exc})")
                continue
            facade = tuple(float(v) for v in hit[:2])
        stop = tuple(facade[i] - direction[i] * (vehicle_length / 2 - 0.02) for i in range(2))
        start = tuple(road[i] - tangent[i] * lead_in for i in range(2))
        turn_start = tuple(road[i] - tangent[i] * 6.0 for i in range(2))
        straight_distance = min(6.0, math.dist(road, stop) * 0.6)
        straight_entry = tuple(stop[i] - direction[i] * straight_distance for i in range(2))
        bend = [tuple((1-u)**2 * turn_start[i] + 2*u*(1-u)*road[i] + u*u*straight_entry[i] for i in range(2))
                for u in (index / 16.0 for index in range(1, 17))]
        route = [start, turn_start, *bend, stop]
        if any(_segments_intersect_obstacle(route, obstacle, vehicle_length * 0.55) for obstacle in obstacles):
            failures.append(f"{lane_id}: swept vehicle volume intersects an obstacle")
            continue
        try:
            plan = AccidentPlan(route, (*facade, road_height), vehicle_length=vehicle_length,
                                road_height=road_height, impact_speed_mps=impact_speed_mps,
                                timing=timing, seed=seed)
        except (ValueError, RuntimeError) as exc:
            failures.append(f"{lane_id}: {exc}")
            continue
        plan.bounds_facade_point = envelope_facade
        return plan, str(lane_id), (float(direction[0]), float(direction[1]))
    raise RuntimeError("No valid lane approach reached the measured wall: " + "; ".join(failures[-4:]))


def select_candidate(candidates: Iterable[BuildingCandidate], *, seed: int) -> BuildingCandidate:
    values = sorted(candidates, key=lambda item: item.path)
    if not values:
        raise ValueError("The city has no visible building candidates")
    return values[random.Random(seed).randrange(len(values))]


def choose_random_plan(candidates: BuildingCandidate | Iterable[BuildingCandidate], lanes: Iterable[dict[str, Any]], *, seed: int,
                       wall_probe: Callable, vehicle_length: float = 4.6, road_height: float = 0.2,
                       impact_speed_mps: float = IMPACT_SPEED_MPS, timing: AccidentTiming | None = None,
                       obstacles: Iterable[Bounds] = ()) -> tuple[AccidentPlan, str, tuple[float, float]]:
    """Try seeded building and route permutations, retaining clear diagnostics."""
    buildings = [candidates] if isinstance(candidates, BuildingCandidate) else list(candidates)
    rng = random.Random(seed)
    rng.shuffle(buildings)
    lanes = list(lanes)
    rng.shuffle(lanes)
    failures: list[str] = []
    for candidate in buildings:
        for lane in lanes:
            def probe(origin, direction, *, _candidate=candidate):
                try:
                    return wall_probe(_candidate, origin, direction)
                except TypeError:
                    return wall_probe(origin, direction)
            try:
                return choose_accident_plan(candidate.bounds, [lane], seed=seed,
                    wall_probe=probe, vehicle_length=vehicle_length, road_height=road_height,
                    impact_speed_mps=impact_speed_mps, timing=timing, obstacles=obstacles)
            except (RuntimeError, ValueError) as exc:
                failures.append(f"{candidate.path}/{lane.get('lane_id', 'route')}: {exc}")
    if not buildings:
        raise RuntimeError("No building candidates were supplied")
    raise RuntimeError("No valid accident candidate or approach: " + "; ".join(failures[-6:]))


def _distance_to_segment_xy(point: Sequence[float], a: Sequence[float], b: Sequence[float]) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    denom = dx * dx + dy * dy
    t = 0.0 if denom == 0 else max(0.0, min(1.0, ((point[0]-a[0])*dx + (point[1]-a[1])*dy) / denom))
    return math.hypot(point[0] - (a[0] + t * dx), point[1] - (a[1] + t * dy))


def choose_robot_spawn_xy(incident_xy: Sequence[float], *, buildings: Iterable[Bounds] = (), obstacles: Iterable[Bounds] = (),
                          traffic_routes: Iterable[Iterable[Sequence[float]]] = (), count: int = 3,
                          spacing: float = 3.0, seed: int = 0) -> list[tuple[float, float]]:
    """Choose three nearby XY positions clear of scenery and route envelopes."""
    if count <= 0:
        return []
    all_obstacles = tuple(buildings) + tuple(obstacles)
    routes = [list(route) for route in traffic_routes]
    rng = random.Random(seed)
    angles = [2 * math.pi * i / 32 for i in range(32)]
    rng.shuffle(angles)
    for radius in (6.0, 8.0, 11.0, 15.0):
        chosen: list[tuple[float, float]] = []
        for angle in angles:
            candidate = (float(incident_xy[0]) + radius * math.cos(angle), float(incident_xy[1]) + radius * math.sin(angle))
            if any(_xy_point_in_bounds(candidate, bounds, 1.0) for bounds in all_obstacles):
                continue
            if any(_distance_to_segment_xy(candidate, a, b) < 2.0 for route in routes for a, b in zip(route, route[1:])):
                continue
            if any(math.dist(candidate, previous) < spacing for previous in chosen):
                continue
            chosen.append(candidate)
            if len(chosen) == count:
                return chosen
    raise RuntimeError(f"Unable to place {count} robot spawns near incident without obstacle/route overlap")


# Convenient singular/plural compatibility spellings.
choose_robot_spawns = choose_robot_spawn_xy
choose_robot_spawn = choose_robot_spawn_xy


__all__ = [
    "AccidentConfig", "AccidentPlan", "AccidentSpec", "AccidentState", "AccidentTiming",
    "BuildingCandidate", "CityScenarioConfig", "Polyline", "choose_accident_plan",
    "choose_random_plan", "choose_robot_spawn", "choose_robot_spawn_xy", "choose_robot_spawns",
    "load_vehicle_lanes", "select_candidate",
]
