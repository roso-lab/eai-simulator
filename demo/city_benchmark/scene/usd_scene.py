"""OpenUSD city adapter for the pure accident planner.

All edits are authored in the session layer.  Source USD files are only
referenced and never rewritten.  The adapter assumes a metre, Z-up stage.
"""
from __future__ import annotations

import json
import math
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from demo.city_benchmark.scene.scenario import (AccidentPlan, BuildingCandidate, CityScenarioConfig,
                                Polyline, choose_accident_plan, load_vehicle_lanes)

CITY_ROOT = Path(__file__).resolve().parents[3] / "usd" / "scene" / "city"
CITY_STATIC_USD = CITY_ROOT / "city_static.usd"
CITY_SCENARIO_JSON = CITY_ROOT / "city_scenario.json"
VEHICLE_ROOT = Path(__file__).resolve().parents[3] / "usd" / "demo" / "city" / "vehicles"
SUPPORTED_VEHICLES = (
    ("car_e4087350c4d84d8391de09b405c96488.usd", 4.6),
    # The sports-car export has thousands of normals opposite its face winding;
    # keep the default fleet on the two geometrically consistent exports.
    ("car_03a6d639648540e3ad45ed38c9b19d1f.usd", 4.1),
)
TARGET_ASSET = "Building_2ed8530340894145a235e98e5145061a.usd"


def _stage_units(stage) -> None:
    """Author unit metadata in the session layer when possible."""
    from pxr import UsdGeom
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)


def disable_rigid_motion(root) -> int:
    """Disable every rigid body below a city root before a physics reset."""
    from pxr import Usd, UsdPhysics
    disabled = 0
    for prim in Usd.PrimRange(root):
        if prim.HasAPI(UsdPhysics.RigidBodyAPI):
            api = UsdPhysics.RigidBodyAPI(prim)
            api.CreateRigidBodyEnabledAttr(False).Set(False)
            disabled += 1
    return disabled


def compose_city(stage, path: str = "/World/City", asset: str | Path | None = None, *,
                 spawn_hook: Callable | None = None, physics_reset: Callable | None = None):
    """Reference a city asset, disable rigid bodies, then invoke parent hooks.

    ``spawn_hook`` is called after rigid bodies are disabled and before
    ``physics_reset``.  This ordering lets a parent place robots and reset
    physics without scenery becoming dynamic during the reset.
    """
    from pxr import Usd, UsdGeom
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        _stage_units(stage)
        city = UsdGeom.Xform.Define(stage, path)
        if asset is not None:
            city.GetPrim().GetReferences().AddReference(str(Path(asset).resolve()))
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        disabled = disable_rigid_motion(stage.GetPrimAtPath(path))
        if spawn_hook is not None:
            spawn_hook(stage, path)
    if physics_reset is not None:
        physics_reset()
    return {"path": path, "disabled_rigid_bodies": disabled, "meters_per_unit": 1.0, "up_axis": "Z"}


def _has_visible_mesh(prim) -> bool:
    from pxr import Usd, UsdGeom
    for child in Usd.PrimRange(prim):
        if not child.IsA(UsdGeom.Mesh) or not child.IsActive():
            continue
        if UsdGeom.Imageable(child).ComputeVisibility() == UsdGeom.Tokens.invisible:
            continue
        points = UsdGeom.Mesh(child).GetPointsAttr().Get() or ()
        if points:
            return True
    return False


def _bounds(stage, prim, root):
    from pxr import Usd, UsdGeom
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy])
    box = cache.ComputeRelativeBound(prim, root).ComputeAlignedRange()
    lo, hi = box.GetMin(), box.GetMax()
    return tuple(float(v) for v in lo), tuple(float(v) for v in hi)


def _collider_count(prim) -> int:
    from pxr import Usd, UsdPhysics
    return sum(1 for child in Usd.PrimRange(prim) if child.HasAPI(UsdPhysics.CollisionAPI))


def discover_buildings(stage, city_root_path: str = "/World/City") -> list[BuildingCandidate]:
    """Return visible, non-empty Building_* roots and obstacle metadata."""
    root = stage.GetPrimAtPath(city_root_path)
    if not root or not root.IsValid():
        raise RuntimeError(f"City root is missing: {city_root_path}")
    candidates = []
    for prim in root.GetChildren():
        if not prim.IsActive() or not prim.GetName().startswith("Building_") or not _has_visible_mesh(prim):
            continue
        bounds = _bounds(stage, prim, root)
        values = (*bounds[0], *bounds[1])
        if not all(math.isfinite(v) for v in values) or any(bounds[1][i] <= bounds[0][i] for i in range(3)):
            continue
        candidates.append(BuildingCandidate(str(prim.GetPath()), bounds,
            {"visible": True, "collider_count": _collider_count(prim), "seated": False}))
    return sorted(candidates, key=lambda item: item.path)


def _ensure_triangle_colliders(building) -> int:
    """Add triangle mesh collision APIs only to static meshes lacking one."""
    from pxr import Usd, UsdPhysics, UsdGeom
    added = 0
    for prim in Usd.PrimRange(building):
        if not prim.IsA(UsdGeom.Mesh) or not prim.IsActive() or prim.HasAPI(UsdPhysics.CollisionAPI):
            continue
        points = UsdGeom.Mesh(prim).GetPointsAttr().Get() or ()
        counts = UsdGeom.Mesh(prim).GetFaceVertexCountsAttr().Get() or ()
        if not points or not counts:
            continue
        UsdPhysics.CollisionAPI.Apply(prim)
        mesh_api = UsdPhysics.MeshCollisionAPI.Apply(prim)
        mesh_api.CreateApproximationAttr().Set("none")
        added += 1
    return added


def seat_building(stage, path: str, *, city_root_path: str = "/World/City", ground_height: float = 0.22,
                 add_triangle_colliders: bool = True) -> BuildingCandidate:
    """Seat one selected target in the session layer and preserve authored rotation."""
    from pxr import Gf, Usd, UsdGeom
    prim = stage.GetPrimAtPath(path)
    root = stage.GetPrimAtPath(city_root_path)
    if not prim or not prim.IsValid():
        raise RuntimeError(f"Building target is missing: {path}")
    if not root or not root.IsValid():
        raise RuntimeError(f"City root is missing: {city_root_path}")
    with Usd.EditContext(stage, stage.GetSessionLayer()):
        before = _bounds(stage, prim, root)
        xform = UsdGeom.Xformable(prim)
        translate = prim.GetAttribute("xformOp:translate")
        if not translate:
            translate = xform.AddTranslateOp()
            translate.Set(Gf.Vec3d(0.0))
        position = translate.Get() or Gf.Vec3d(0.0)
        translate.Set(Gf.Vec3d(position[0], position[1], position[2] + ground_height - before[0][2]))
        added = _ensure_triangle_colliders(prim) if add_triangle_colliders else 0
    after = _bounds(stage, prim, root)
    return BuildingCandidate(path, after, {"visible": True, "seated": True,
        "ground_height": ground_height, "collider_count": _collider_count(prim),
        "triangle_colliders_added": added, "source_preserved": True})


def discover_obstacles(stage, city_root_path: str = "/World/City") -> list[tuple[Bounds, dict[str, Any]]]:
    """Capture bounds for obvious trees/lamps so planners reject them conservatively."""
    from pxr import Usd
    root = stage.GetPrimAtPath(city_root_path)
    if not root or not root.IsValid():
        raise RuntimeError(f"City root is missing: {city_root_path}")
    result = []
    for prim in Usd.PrimRange(root):
        name = str(prim.GetName()).lower()
        if not any(token in name for token in ("tree", "lamp", "lightpole", "streetlight")):
            continue
        # Aggregate containers (for example /Trees or /Lamps) have children
        # with the same token and must not become one city-sized obstacle.
        children = list(prim.GetChildren())
        if children and (name.rstrip("s") in {"tree", "lamp", "streetlight", "lightpole"}
                         and len(children) > 4 or name in {"trees", "lamps", "streetlights", "lightpoles"}):
            continue
        try:
            bounds = _bounds(stage, prim, root)
        except Exception:
            continue
        if all(bounds[1][i] > bounds[0][i] for i in range(3)):
            result.append((bounds, {"path": str(prim.GetPath()), "kind": name, "conservative": True,
                                    "collider_count": _collider_count(prim)}))
    return result


def raycast_building_wall(stage, building, city_root, origin, direction):
    """Return nearest vertical visible mesh triangle hit in city coordinates."""
    import numpy as np
    from pxr import Usd, UsdGeom
    ray_origin = np.asarray(origin, dtype=float)
    ray_direction = np.asarray(direction, dtype=float)
    inverse_city = UsdGeom.Xformable(city_root).ComputeLocalToWorldTransform(Usd.TimeCode.Default()).GetInverse()
    nearest = None
    for prim in Usd.PrimRange(building):
        if not prim.IsA(UsdGeom.Mesh) or UsdGeom.Imageable(prim).ComputeVisibility() == UsdGeom.Tokens.invisible:
            continue
        mesh = UsdGeom.Mesh(prim)
        points = np.asarray(mesh.GetPointsAttr().Get() or (), dtype=float)
        counts = np.asarray(mesh.GetFaceVertexCountsAttr().Get() or (), dtype=int)
        indices = np.asarray(mesh.GetFaceVertexIndicesAttr().Get() or (), dtype=int)
        if not len(points) or not len(indices) or not len(counts):
            continue
        matrix = np.asarray(UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default()) * inverse_city)
        points = np.c_[points, np.ones(len(points))] @ matrix
        offsets = np.r_[0, np.cumsum(counts)]
        triangles = np.asarray([(indices[offsets[i]], indices[offsets[i] + j], indices[offsets[i] + j + 1])
            for i, count in enumerate(counts) for j in range(1, count - 1)], dtype=int)
        if not len(triangles):
            continue
        vertices = points[triangles, :3]
        a, b, c = vertices[:, 0], vertices[:, 1], vertices[:, 2]
        e1, e2 = b - a, c - a
        normal = np.cross(e1, e2)
        norm = np.linalg.norm(normal, axis=1)
        valid = norm > 1e-10
        valid &= np.abs(normal[:, 2]) < 0.3 * norm
        valid &= np.abs(normal @ ray_direction) > 0.5 * norm
        h = np.cross(np.broadcast_to(ray_direction, e2.shape), e2)
        det = np.einsum("ij,ij->i", e1, h)
        valid &= np.abs(det) > 1e-10
        inv = np.divide(1.0, det, out=np.zeros_like(det), where=valid)
        delta = ray_origin - a
        u = inv * np.einsum("ij,ij->i", delta, h)
        q = np.cross(delta, e1)
        v = inv * (q @ ray_direction)
        distance = inv * np.einsum("ij,ij->i", e2, q)
        valid &= (u >= -1e-7) & (v >= -1e-7) & (u + v <= 1 + 1e-7) & (distance >= 0)
        if valid.any():
            candidate = float(distance[valid].min())
            nearest = candidate if nearest is None else min(nearest, candidate)
    if nearest is None:
        raise RuntimeError("No actual building wall intersects the incident car approach")
    return tuple(ray_origin + nearest * ray_direction)


def spawn_vehicle(stage, path: str, asset_path: str | Path, length: float):
    """Place verified native Y-up city car geometry in metre/Z-up coordinates."""
    from pxr import Gf, Usd, UsdGeom
    vehicle = UsdGeom.Xform.Define(stage, path)
    position = vehicle.GetPrim().GetAttribute("xformOp:translate")
    if not position:
        position = vehicle.AddTranslateOp()
    heading = vehicle.GetPrim().GetAttribute("xformOp:rotateZ")
    if not heading:
        heading = vehicle.AddRotateZOp()
    model = UsdGeom.Xform.Define(stage, path + "/Model")
    model.GetPrim().GetReferences().AddReference(str(Path(asset_path).resolve()))
    bounds = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    box = bounds.ComputeLocalBound(model.GetPrim()).ComputeAlignedRange()
    minimum, maximum = box.GetMin(), box.GetMax()
    source_length = float(maximum[2] - minimum[2])
    if source_length <= 1e-8:
        raise RuntimeError(f"Vehicle asset has no measurable length: {asset_path}")
    scale = float(length) / source_length
    centre = (minimum + maximum) * 0.5
    source_to_city = Gf.Matrix4d(0, 1, 0, 0, 0, 0, 1, 0, 1, 0, 0, 0, 0, 0, 0, 1)
    matrix = (Gf.Matrix4d().SetTranslate(Gf.Vec3d(-centre[0], -minimum[1], -centre[2])) *
              Gf.Matrix4d().SetScale(scale) * source_to_city)
    model.ClearXformOpOrder()
    model.AddTransformOp().Set(matrix)
    from pxr import UsdShade
    for prim in Usd.PrimRange(model.GetPrim()):
        if prim.IsA(UsdGeom.Mesh):
            binding = UsdShade.MaterialBindingAPI.Apply(prim)
            subsets = binding.GetMaterialBindSubsets()
            if subsets:
                indices = [int(face) for subset in subsets for face in subset.GetIndicesAttr().Get()]
                count = len(UsdGeom.Mesh(prim).GetFaceVertexCountsAttr().Get())
                if len(indices) == count and len(set(indices)) == count and min(indices) == 0 and max(indices) == count - 1:
                    binding.SetMaterialBindSubsetsFamilyType(UsdGeom.Tokens.partition)
    disable_rigid_motion(vehicle.GetPrim())
    return position, heading


class CitySceneAdapter:
    """Compose city traffic and choose an actual visible building target."""

    def __init__(self, stage, *, city_root_path: str = "/World/City", config: CityScenarioConfig | None = None,
                 spawn_hook: Callable | None = None, physics_reset: Callable | None = None):
        self.stage, self.city_root_path = stage, city_root_path
        self.config = config or CityScenarioConfig()
        self.spawn_hook, self.physics_reset = spawn_hook, physics_reset
        self.target: BuildingCandidate | None = None
        self.plan: AccidentPlan | None = None
        self.lane_id = self.direction = None
        self.traffic = []
        self.traffic_records: list[dict[str, Any]] = []
        self.incident_vehicle = None
        self.buildings: list[BuildingCandidate] = []
        self.obstacle_metadata: list[dict[str, Any]] = []
        self.disabled_rigid_bodies = 0

    def prepare(self, target_path: str | None = None):
        from pxr import Usd
        city_root = self.stage.GetPrimAtPath(self.city_root_path)
        if not city_root or not city_root.IsValid():
            raise RuntimeError(f"City USD was not composed at {self.city_root_path}")
        with Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
            self.disabled_rigid_bodies = disable_rigid_motion(city_root)
            if self.spawn_hook is not None:
                self.spawn_hook(self.stage, self.city_root_path)
            if self.physics_reset is not None:
                self.physics_reset()
        self.buildings = discover_buildings(self.stage, self.city_root_path)
        if self.config.seat_buildings:
            self.buildings = [seat_building(
                self.stage, item.path, city_root_path=self.city_root_path,
                ground_height=self.config.ground_height,
                add_triangle_colliders=self.config.add_triangle_colliders,
            ) for item in self.buildings]
        candidates = list(self.buildings)
        center = tuple((min(item.bounds[0][axis] for item in candidates) +
                        max(item.bounds[1][axis] for item in candidates)) / 2
                       for axis in range(2)) if candidates else (0.0, 0.0)
        if not target_path and self.config.central_target_candidates > 0:
            candidates.sort(key=lambda item: math.dist(center, tuple(
                (item.bounds[0][axis] + item.bounds[1][axis]) / 2 for axis in range(2))))
            candidates = candidates[:self.config.central_target_candidates]
        if target_path:
            candidates = [item for item in candidates if item.path == target_path]
            if not candidates:
                raise ValueError(f"Requested target is not a usable building: {target_path}")
        obstacles = discover_obstacles(self.stage, self.city_root_path)
        self.obstacle_metadata = [meta for _, meta in obstacles]
        lanes = load_vehicle_lanes(CITY_SCENARIO_JSON)
        import random
        rng = random.Random(self.config.seed)
        rng.shuffle(candidates)
        rng.shuffle(lanes)
        failures: list[str] = []
        selected_plan = None
        selected_lane = None
        selected_direction = None
        selected_candidate = None
        city_root = self.stage.GetPrimAtPath(self.city_root_path)
        for candidate in candidates:
            building = self.stage.GetPrimAtPath(candidate.path)
            # Render bounds include roofs and courtyards; the actual target
            # wall is handled by raycast, so only roadside object bounds apply.
            obstacle_bounds = [bounds for bounds, _ in obstacles] if self.config.validate_obstacles else []
            for lane in lanes:
                try:
                    plan, lane_id, direction = choose_accident_plan(
                        candidate.bounds, [lane], seed=self.config.seed,
                        wall_probe=(lambda origin, ray, _building=building: raycast_building_wall(
                            self.stage, _building, city_root, origin, ray)) if self.config.use_wall_probe else None,
                        vehicle_length=self.config.vehicle_length, road_height=self.config.road_height,
                        impact_speed_mps=self.config.impact_speed_mps, timing=self.config.timing,
                        obstacles=obstacle_bounds)
                except (RuntimeError, ValueError) as exc:
                    failures.append(f"{candidate.path}/{lane.get('lane_id', 'route')}: {exc}")
                    continue
                selected_plan, selected_lane, selected_direction, selected_candidate = plan, lane_id, direction, candidate
                break
            if selected_plan is not None:
                break
        if selected_plan is None or selected_candidate is None:
            detail = "; ".join(failures[-6:])
            raise RuntimeError(f"No valid city building/approach candidate: {detail}")
        self.target = selected_candidate
        self.plan, self.lane_id, self.direction = selected_plan, selected_lane, selected_direction
        return self

    def spawn_traffic(self):
        from pxr import Usd, UsdGeom
        if self.plan is None:
            raise RuntimeError("prepare() must run before spawn_traffic()")
        root = "/World/CityTraffic"
        with Usd.EditContext(self.stage, self.stage.GetSessionLayer()):
            UsdGeom.Xform.Define(self.stage, root)
            self.incident_vehicle = spawn_vehicle(
                self.stage, root + "/IncidentCar", VEHICLE_ROOT / SUPPORTED_VEHICLES[0][0], self.config.vehicle_length)
            scenario = json.loads(CITY_SCENARIO_JSON.read_text(encoding="utf-8"))
            routes = scenario.get("vehicle_routes", ())
            traffic = []
            records = []
            desired = max(0, int(self.config.max_background_cars))
            occupied = [self.plan.sample(0).car_position, self.plan.facade_point]
            for index in range(desired):
                route_index = (index * len(routes) // min(desired, len(routes))) % len(routes)
                cycle = index // len(routes)
                route_data = routes[route_index]
                route = Polyline(route_data.get("points") or route_data["centerline"])
                offset = None
                for attempt in range(32):
                    distance = route.length * ((0.18 + index * 0.381966 + cycle * 0.5 + attempt / 32) % 1)
                    point, _ = route.sample(distance, loop=True)
                    if all(math.dist(point[:2], other) >= self.config.traffic_spawn_gap for other in occupied):
                        offset = distance
                        occupied.append(point[:2])
                        break
                if offset is None:
                    continue  # Keep the scene sparse rather than force overlapping cars.
                asset, length = SUPPORTED_VEHICLES[index % len(SUPPORTED_VEHICLES)]
                vehicle_id = f"following_vehicle_{index:02d}"
                prim_path = f"{root}/Vehicle_{index:02d}"
                pose = spawn_vehicle(self.stage, prim_path, VEHICLE_ROOT / asset, length)
                traffic.append((*pose, route, offset, 3.0 + 0.2 * (route_index % 3)))
                records.append({
                    "id": vehicle_id, "prim_path": prim_path, "position": pose[0],
                    "heading": pose[1], "route": route, "offset": offset,
                    "speed_mps": 3.0 + 0.2 * (route_index % 3), "length_m": length,
                })
            self.traffic = traffic
            self.traffic_records = records
        return self.incident_vehicle, self.traffic


def world_envelopes(stage: Any, entities: Mapping[str, str]) -> dict[str, dict[str, Any]]:
    """Read current world AABBs for explicitly named dynamic scene entities."""
    from pxr import Usd, UsdGeom
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(),
                              [UsdGeom.Tokens.default_, UsdGeom.Tokens.render])
    result = {}
    for entity_id, prim_path in entities.items():
        prim = stage.GetPrimAtPath(prim_path)
        if not prim or not prim.IsValid():
            raise ValueError(f"dynamic entity prim is missing: {prim_path}")
        bounds = cache.ComputeWorldBound(prim).ComputeAlignedRange()
        low = [float(value) for value in bounds.GetMin()]
        high = [float(value) for value in bounds.GetMax()]
        if not all(math.isfinite(value) for value in (*low, *high)) or \
                any(high[index] <= low[index] for index in range(3)):
            raise ValueError(f"dynamic entity has invalid world envelope: {entity_id}")
        result[str(entity_id)] = {"prim_path": str(prim_path), "min": low, "max": high}
    return result


def _intersects(a: tuple[tuple[float, float, float], tuple[float, float, float]],
                b: Mapping[str, Any]) -> bool:
    return all(a[0][axis] <= float(b["max"][axis])
               and float(b["min"][axis]) <= a[1][axis] for axis in range(3))


def _swept_box_intersects(start, end, half, envelope):
    """Exact translating axis-aligned box vs AABB via a Minkowski slab test."""
    enter, leave = 0.0, 1.0
    for axis in range(3):
        low = float(envelope["min"][axis]) - half[axis]
        high = float(envelope["max"][axis]) + half[axis]
        delta = end[axis] - start[axis]
        if abs(delta) <= 1e-12:
            if start[axis] < low or start[axis] > high:
                return False
            continue
        near, far = sorted(((low - start[axis]) / delta,
                            (high - start[axis]) / delta))
        enter, leave = max(enter, near), min(leave, far)
        if enter > leave:
            return False
    return True


class CityNativeRegionReader:
    """Current native occupancy for host-supplied service regions."""

    def __init__(self, *, query: Any, regions: Mapping[str, Mapping[str, Any]],
                 envelope_reader: Callable[[], Mapping[str, Mapping[str, Any]]],
                 scene_queries_enabled: bool, device: str,
                 current_gpu_geometry: bool = False,
                 ignored_collision_paths: Iterable[str] = ()):
        self.query = query
        self.envelope_reader = envelope_reader
        self.scene_queries_enabled = scene_queries_enabled is True
        self.device = str(device)
        self.current_gpu_geometry = bool(current_gpu_geometry)
        self.ignored_collision_paths = {str(path) for path in ignored_collision_paths}
        self.regions = {}
        self.region_metadata = {}
        for region_id, row in regions.items():
            low = tuple(float(value) for value in row.get("min", ()))
            high = tuple(float(value) for value in row.get("max", ()))
            if len(low) != 3 or len(high) != 3 or any(high[i] <= low[i] for i in range(3)):
                raise ValueError(f"service region has invalid AABB: {region_id}")
            self.regions[str(region_id)] = (low, high)
            self.region_metadata[str(region_id)] = {
                key: value for key, value in row.items() if key not in {"min", "max"}}

    def _ignored(self, row: Mapping[str, Any]) -> bool:
        paths = (str(row.get("collision") or ""), str(row.get("rigid_body") or ""))
        return any(path == root or path.startswith(root + "/")
                   for path in paths if path for root in self.ignored_collision_paths)

    @staticmethod
    def _hit_row(hit: Any, *, max_distance: float | None = None) -> dict[str, Any]:
        get = hit.get if isinstance(hit, Mapping) else lambda key: getattr(hit, key, None)
        collision = str(get("collision") or "")
        rigid_body = str(get("rigid_body") or "")
        if not any(path.startswith("/") for path in (collision, rigid_body)):
            raise ValueError("native hit lacks collision identity")
        row: dict[str, Any] = {"collision": collision, "rigid_body": rigid_body}
        proto_index = get("protoIndex")
        if proto_index is not None:
            row["protoIndex"] = (proto_index if type(proto_index) in (int, str, bool)
                                  else repr(proto_index))
        if max_distance is not None:
            distance = get("distance")
            if (type(distance) not in (int, float) or not math.isfinite(distance)
                    or not 0.0 <= float(distance) <= max_distance + 1e-6):
                raise ValueError("native sweep hit has invalid distance")
            row["distance"] = float(distance)
        return row

    def read(self, region_id: str, *, episode_id: str, time_s: float) -> dict[str, Any]:
        region_id = str(region_id)
        if region_id not in self.regions:
            raise ValueError(f"unknown City service region: {region_id}")
        base = {"region_id": region_id, "episode_id": str(episode_id),
                "observed_time_s": float(time_s), "source": "native_city_region_reader"}
        if not self.scene_queries_enabled or self.query is None:
            return {**base, "status": "unknown", "reason": "scene_query_unavailable"}
        if self.device.startswith("cuda") and not self.current_gpu_geometry:
            return {**base, "status": "unknown",
                    "reason": "dynamic_scene_query_pose_not_synchronized"}
        low, high = self.regions[region_id]
        query_low = (low[0], low[1], float(self.region_metadata[region_id].get("query_min_z", low[2])))
        if not low[2] <= query_low[2] < high[2]:
            raise ValueError("service region query_min_z must be inside its AABB")
        center = tuple((query_low[i] + high[i]) * 0.5 for i in range(3))
        half = tuple((high[i] - query_low[i]) * 0.5 for i in range(3))
        hits, callback_errors = [], []
        callback_count = 0

        def collect(hit: Any) -> bool:
            nonlocal callback_count
            callback_count += 1
            try:
                row = self._hit_row(hit)
                if not self._ignored(row):
                    hits.append(row)
            except (TypeError, ValueError) as exc:
                callback_errors.append(str(exc))
                return False
            return True

        try:
            returned = self.query.overlap_box(
                half, center, (0.0, 0.0, 0.0, 1.0), collect, False)
            if type(returned) is not int or returned != callback_count:
                raise ValueError("native overlap completion mismatch")
            if callback_errors:
                raise ValueError(";".join(callback_errors))
            envelopes = {str(key): dict(value) for key, value in self.envelope_reader().items()}
        except Exception as exc:
            return {**base, "status": "unknown",
                    "reason": f"native_query_failed:{type(exc).__name__}:{exc}"}
        dynamic = {key: value for key, value in envelopes.items() if _intersects((low, high), value)}
        return {**base, "status": "measured", "clear": not hits and not dynamic,
                "region_aabb_m": {"min": list(low), "max": list(high)},
                "static_query_aabb_m": {"min": list(query_low), "max": list(high)},
                "region_metadata": deepcopy(self.region_metadata[region_id]),
                "static_hits": hits, "dynamic_envelopes": dynamic,
                "all_dynamic_envelopes": envelopes,
                "query_context": {"scene_queries_enabled": True, "device": self.device,
                                  "current_gpu_geometry": self.current_gpu_geometry}}

    def read_route(self, route_id: str, waypoints: Iterable[Iterable[float]], *,
                   half_extents_m: Iterable[float], episode_id: str, time_s: float,
                   moving_entity_id: str | None = None) -> dict[str, Any]:
        points = tuple(tuple(float(value) for value in point) for point in waypoints)
        half = tuple(float(value) for value in half_extents_m)
        base = {"route_id": str(route_id), "episode_id": str(episode_id),
                "observed_time_s": float(time_s), "source": "native_city_route_reader"}
        if len(points) < 2 or any(len(point) != 3 for point in points) or len(half) != 3 \
                or any(value <= 0 or not math.isfinite(value) for value in half):
            raise ValueError("native route readback requires XYZ waypoints and positive half extents")
        if not self.scene_queries_enabled or self.query is None:
            return {**base, "status": "unknown", "reason": "scene_query_unavailable"}
        if self.device.startswith("cuda") and not self.current_gpu_geometry:
            return {**base, "status": "unknown",
                    "reason": "dynamic_scene_query_pose_not_synchronized"}
        static_hits = []
        query_checks = []

        def run_overlap(point: tuple[float, float, float]) -> None:
            callback_count, callback_errors = 0, []

            def collect(hit: Any) -> bool:
                nonlocal callback_count
                callback_count += 1
                try:
                    row = self._hit_row(hit)
                    if not self._ignored(row):
                        static_hits.append(row)
                except (TypeError, ValueError) as exc:
                    callback_errors.append(str(exc))
                    return False
                return True

            returned = self.query.overlap_box(
                half, point, (0.0, 0.0, 0.0, 1.0), collect, False)
            if type(returned) is not int or returned != callback_count:
                raise ValueError("native overlap completion mismatch")
            if callback_errors:
                raise ValueError(";".join(callback_errors))
            query_checks.append({"kind": "waypoint_overlap", "origin_m": list(point),
                                 "callback_count": callback_count})

        def run_sweep(start: tuple[float, float, float],
                      direction: tuple[float, float, float], distance: float) -> None:
            callback_count, callback_errors = 0, []

            def collect(hit: Any) -> bool:
                nonlocal callback_count
                callback_count += 1
                try:
                    row = self._hit_row(hit, max_distance=distance)
                    if not self._ignored(row):
                        static_hits.append(row)
                except (TypeError, ValueError) as exc:
                    callback_errors.append(str(exc))
                    return False
                return True

            returned = self.query.sweep_box_all(
                half, start, (0.0, 0.0, 0.0, 1.0), direction, distance, collect, False)
            if type(returned) is not bool or returned != bool(callback_count):
                raise ValueError("native sweep completion mismatch")
            if callback_errors:
                raise ValueError(";".join(callback_errors))
            query_checks.append({"kind": "segment_sweep", "origin_m": list(start),
                                 "direction": list(direction), "distance_m": distance,
                                 "callback_count": callback_count})

        try:
            for point in points:
                run_overlap(point)
            for start, end in zip(points, points[1:]):
                delta = tuple(end[index] - start[index] for index in range(3))
                distance = math.sqrt(sum(value * value for value in delta))
                if distance <= 1e-8:
                    continue
                direction = tuple(value / distance for value in delta)
                run_sweep(start, direction, distance)
            envelopes = {str(key): dict(value) for key, value in self.envelope_reader().items()}
        except Exception as exc:
            return {**base, "status": "unknown",
                    "reason": f"native_query_failed:{type(exc).__name__}:{exc}"}
        dynamic = {key: value for key, value in envelopes.items()
                   if key != moving_entity_id and any(
                       _swept_box_intersects(start, end, half, value)
                       for start, end in zip(points, points[1:]))}
        return {**base, "status": "measured", "clear": not static_hits and not dynamic,
                "waypoints_m": [list(point) for point in points],
                "half_extents_m": list(half), "static_hits": static_hits,
                "query_checks": query_checks,
                "dynamic_envelopes": dynamic, "all_dynamic_envelopes": envelopes,
                "query_context": {"scene_queries_enabled": True, "device": self.device,
                                  "current_gpu_geometry": self.current_gpu_geometry}}


__all__ = ["CITY_ROOT", "CITY_STATIC_USD", "CITY_SCENARIO_JSON", "CitySceneAdapter",
           "BuildingCandidate", "compose_city", "discover_buildings", "discover_obstacles",
           "disable_rigid_motion", "raycast_building_wall", "seat_building", "spawn_vehicle",
           "SUPPORTED_VEHICLES", "CityNativeRegionReader", "world_envelopes"]
