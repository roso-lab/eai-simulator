"""Formal House goal episode entry, copied from the City driver shape.

The witness plan is the episode plan. Skills that do not yet have a House
physical adapter fail with a factual receipt after the session has stepped;
they are not reported as business success. ``scoreable`` stays false.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from .goal_driven_contract import validate_goal_plan
from .goal_driven_tasks import build_goal_task_package
from .goal_episode_runner import FormalGoalEpisodeRunner


HOUSE_EPISODE_PLAN_ID = "house_native_goal_episode_v1"
HOUSE_BACKEND_ID = "house_native_goal_backend"
_PENDING_REASON = "house_native_skill_adapter_pending"
INDOOR_EPISODE_SCENES = ("factory", "warehouse_nvd", "modern_house_nvd")
_APPROACH_GATE_M = 1.25
# Second-mission carriers rally outside the support face. The band is coarse
# on purpose: the gait is not asked to finish inside the 0.8 m connector.
_RALLY_STANDOFF_M = 1.35
_RALLY_TOLERANCE_M = 0.45


def _yaw(state: list[float]) -> float:
    import math
    w, qx, qy, qz = state[3:7]
    return math.atan2(2 * (w * qz + qx * qy), 1 - 2 * (qy * qy + qz * qz))


def _root_state(session: Any, runtime_id: str) -> list[float]:
    return session.base_env.scene.articulations[runtime_id].data.root_state_w[0].detach().cpu().tolist()


def _pepper_body_index(session: Any, runtime_id: str) -> int | None:
    """Pepper's moving base is base_link. The articulation root stays fixed."""
    robot = session.base_env.scene.articulations[runtime_id]
    find_bodies = getattr(robot, "find_bodies", None)
    if not callable(find_bodies):
        return None
    body_ids, _names = find_bodies("base_link", preserve_order=True)
    if len(body_ids) != 1:
        return None
    return int(body_ids[0])


def _navigation_pose(session: Any, runtime_id: str, body_index: int | None) -> list[float]:
    robot = session.base_env.scene.articulations[runtime_id]
    state = (robot.data.root_state_w[0] if body_index is None
             else robot.data.body_state_w[0, body_index])
    return state.detach().cpu().tolist()


def moving_base_pose(session: Any, runtime_id: str, robot_type: str | None) -> list[float]:
    """Pose of the body that navigation actually drives.

    Pepper's articulation root stays at the spawn. Orders and receipts have to
    use base_link, the same body the table approach already follows.
    """
    body_index = _pepper_body_index(session, runtime_id) if robot_type == "pepper" else None
    return _navigation_pose(session, runtime_id, body_index)


def _pepper_world_command(state: list[float], target_xy: tuple[float, float],
                          speed_limit: float, stop_radius: float,
                          frame_yaw: float = 0.0) -> list[float]:
    """Velocity for Pepper's XDisp/YDisp, expressed in the fixed root frame.

    The prismatic joints stay on the articulation root. That root is fixed at
    the spawn yaw, while ZRot turns base_link. A world-axis command is therefore
    rotated by the spawn yaw before it moves the base. ``frame_yaw`` is that
    spawn yaw, captured before any ZRot command. Yaw rate still uses the live
    base_link heading.
    """
    import math
    dx = float(target_xy[0]) - state[0]
    dy = float(target_xy[1]) - state[1]
    distance = math.hypot(dx, dy)
    if distance <= stop_radius:
        return [0.0, 0.0, 0.0]
    heading = math.atan2(dy, dx)
    error = math.atan2(math.sin(heading - _yaw(state)), math.cos(heading - _yaw(state)))
    scale = min(1.0, distance / 0.8)
    world_vx = speed_limit * scale * dx / distance
    world_vy = speed_limit * scale * dy / distance
    cosine = math.cos(frame_yaw)
    sine = math.sin(frame_yaw)
    return [cosine * world_vx + sine * world_vy,
            -sine * world_vx + cosine * world_vy,
            max(-1.0, min(1.0, error))]


_GRID_CACHE: dict[tuple[str, float], tuple[Any, Any]] = {}
# Pepper base_link is 0.50 m across. The 0.25 m inflation used by the gaits
# left a restaurant chord 0.22 m from the wall, and the base stopped there.
_PEPPER_OCCUPANCY_RADIUS_M = 0.45
_GROUND_SPEED_MPS = 0.55
# City navigator and Warehouse route worker. Both were measured against the
# RSL policies: yaw until the heading is close, then forward only. A reverse
# command and a ±1 rad/s yaw command stall these gaits.
# City velocity_to_target: never reverse, yaw-only above 0.9 rad, then
# forward 0.3–1.2 m/s with yaw capped at 0.8 rad/s. Go2 tracks that yaw-only
# phase. An M20 with a fixed joint does not: formal28 commanded [0, 0, 0.8]
# and the heading moved 0.04 rad in 4 s, then the carton stalled. The holder
# is aligned before the joint so the carry enters this profile's forward
# branch. The carton uses this profile, not the M20 _TRACKED_GAIT band.
_CITY_VELOCITY_TO_TARGET = {
    "max_speed": 1.20, "min_speed": 0.30, "max_yaw": 0.80,
    "yaw_gain": 1.6, "turn_gate": 0.9, "speed_gain": 0.65,
}
_TRACKED_GAIT = {
    "go2": {"max_speed": 1.20, "min_speed": 0.30, "max_yaw": 0.80,
            "yaw_gain": 1.6, "turn_gate": 1.0, "speed_gain": 0.65},
    "m20": {"max_speed": 1.80, "min_speed": 0.55, "max_yaw": 0.60,
            "yaw_gain": 1.5, "turn_gate": 0.9, "speed_gain": 0.70},
    "b2": {"max_speed": 1.80, "min_speed": 0.55, "max_yaw": 0.60,
           "yaw_gain": 1.5, "turn_gate": 0.9, "speed_gain": 0.70},
    "lite3": {"max_speed": 1.80, "min_speed": 0.55, "max_yaw": 0.60,
              "yaw_gain": 1.5, "turn_gate": 0.9, "speed_gain": 0.70},
}


def _rally_walk_command(state: list[float], target_xy: tuple[float, float],
                        stop_radius: float) -> list[float]:
    """Walk the rally at the gait's startup speed. This policy does not yaw in place."""
    import math
    dx = float(target_xy[0]) - state[0]
    dy = float(target_xy[1]) - state[1]
    if math.hypot(dx, dy) <= stop_radius:
        return [0.0, 0.0, 0.0]
    heading = math.atan2(dy, dx)
    error = math.atan2(math.sin(heading - _yaw(state)), math.cos(heading - _yaw(state)))
    forward = -0.55 if abs(error) > 1.0 else 0.55
    yaw_rate = max(-0.6, min(0.6, 1.5 * error))
    return [forward, 0.0, yaw_rate]


def _formation_walk_command(state: list[float], direction_xy: tuple[float, float],
                            at_stop: bool) -> list[float]:
    """Walk a fixed pair on one heading. Both bases yaw toward that heading together."""
    import math
    if at_stop:
        return [0.0, 0.0, 0.0]
    travel = math.atan2(direction_xy[1], direction_xy[0])
    error = math.atan2(math.sin(travel - _yaw(state)), math.cos(travel - _yaw(state)))
    # Same direction for both members, so the joint yaws the crate instead of stretching.
    yaw_rate = max(-0.35, min(0.35, 0.8 * error))
    return [0.55 if math.cos(error) >= 0.0 else -0.55, 0.0, yaw_rate]


def _mirror_physx_bodies_to_usd(stage: Any, samples: Mapping[str, Mapping[str, Any]]) -> None:
    """Copy live PhysX roots onto USD so a new fixed joint is parsed where the bodies already are."""
    from pxr import Gf, UsdGeom
    cache = UsdGeom.XformCache()
    for path, sample in samples.items():
        prim = stage.GetPrimAtPath(path)
        if not prim:
            raise ValueError(f"attachment body missing from the stage: {path}")
        parent = prim.GetParent()
        parent_world = (cache.GetLocalToWorldTransform(parent)
                        if parent and parent.IsValid() else Gf.Matrix4d(1.0))
        rotation = sample["rotation_wxyz"]
        world = Gf.Matrix4d(1.0)
        world.SetRotate(Gf.Quatd(
            float(rotation[0]),
            Gf.Vec3d(float(rotation[1]), float(rotation[2]), float(rotation[3]))))
        world.SetTranslateOnly(Gf.Vec3d(*(float(value) for value in sample["position_m"])))
        local = world * parent_world.GetInverse()
        translation = local.ExtractTranslation()
        orientation = local.ExtractRotationQuat()
        wrote = False
        for op in UsdGeom.Xformable(prim).GetOrderedXformOps():
            type_name = str(op.GetAttr().GetTypeName())
            if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
                if "float3" in type_name:
                    op.Set(Gf.Vec3f(translation))
                else:
                    op.Set(Gf.Vec3d(translation))
                wrote = True
            elif op.GetOpType() == UsdGeom.XformOp.TypeOrient:
                imaginary = orientation.GetImaginary()
                if "quatf" in type_name:
                    op.Set(Gf.Quatf(
                        float(orientation.GetReal()),
                        Gf.Vec3f(float(imaginary[0]), float(imaginary[1]), float(imaginary[2]))))
                else:
                    op.Set(orientation)
                wrote = True
        if not wrote:
            raise ValueError(f"attachment body has no translate/orient op: {path}")


def _tracked_gait_command(state: list[float], target_xy: tuple[float, float],
                          stop_radius: float, profile: Mapping[str, float]) -> list[float]:
    """Body-frame command copied from the City and Warehouse navigators."""
    import math
    dx = float(target_xy[0]) - state[0]
    dy = float(target_xy[1]) - state[1]
    distance = math.hypot(dx, dy)
    if distance <= stop_radius:
        return [0.0, 0.0, 0.0]
    heading = math.atan2(dy, dx)
    error = math.atan2(math.sin(heading - _yaw(state)), math.cos(heading - _yaw(state)))
    speed = min(profile["max_speed"], max(profile["min_speed"], profile["speed_gain"] * distance))
    forward = speed * max(0.0, math.cos(error)) if abs(error) < profile["turn_gate"] else 0.0
    yaw_rate = max(-profile["max_yaw"], min(profile["max_yaw"], profile["yaw_gain"] * error))
    return [forward, 0.0, yaw_rate]


def _seek_command(state: list[float], target_xy: tuple[float, float],
                  speed_limit: float, stop_radius: float, yaw_limit: float = 1.0,
                  *, creep: bool = False) -> list[float]:
    """Body-frame seek used by the measured M20 probe.

    Lateral velocity is ignored. A large heading error is yaw-only until the
    body faces the waypoint; a target directly behind is reverse, which the
    wheel gait does track. ``creep`` adds a small forward command when yaw
    alone has not changed the heading.
    """
    import math
    dx, dy = float(target_xy[0]) - state[0], float(target_xy[1]) - state[1]
    distance = math.hypot(dx, dy)
    if distance <= stop_radius:
        return [0.0, 0.0, 0.0]
    heading = math.atan2(dy, dx)
    error = math.atan2(math.sin(heading - _yaw(state)), math.cos(heading - _yaw(state)))
    if abs(error) > 2.2:
        return [-speed_limit, 0.0, max(-0.1, min(0.1, error))]
    # Keep turning until the body is within about 15 degrees. A simultaneous
    # forward command of similar size stalls the policy against the wall.
    if abs(error) > 0.25 and not creep:
        return [0.0, 0.0, math.copysign(yaw_limit, error)]
    return [speed_limit, 0.0, max(-0.1, min(0.1, error))]


def _scene_occupancy(scene_key: str):
    """Load the scene's own ground map. Unknown scenes do not borrow the factory map."""
    from EAI.scenario_spec.geometry import FactoryMap, provider_map

    if scene_key == "restaurant":
        import os
        root = os.environ.get("EAI_RESTAURANT_BENCHMARK_ROOT")
        if not root:
            raise RuntimeError("restaurant occupancy requires EAI_RESTAURANT_BENCHMARK_ROOT")
        return FactoryMap(Path(root) / "restaurant_benchmark_map.yaml")
    if scene_key == "city":
        return _city_occupancy()
    if scene_key in {"factory", "warehouse_nvd", "modern_house_nvd"}:
        return provider_map(scene_key=scene_key)
    raise RuntimeError(f"no occupancy map for {scene_key}")


def _city_occupancy():
    """Rasterize the city layout once. Buildings are included when OpenUSD is already loaded."""
    import sys
    from EAI.scenario_spec.geometry import FactoryMap
    from EAI_assets.asset_resolver import usd_root
    from demo.city_benchmark.maps import inspect_geometry, load_sources, rasterize

    cache = Path("tmp/second-mission-isaac/city-occupancy")
    yaml_path = cache / "occupancy.yaml"
    if not yaml_path.is_file():
        layout, _, _ = load_sources(usd_root() / "scene" / "city")
        obstacles = []
        if "pxr" in sys.modules:
            try:
                obstacles, _hashes = inspect_geometry(usd_root() / "scene" / "city")
            except (OSError, RuntimeError, ValueError) as exc:
                print(f"city_occupancy_buildings_skipped {type(exc).__name__}: {exc}", flush=True)
        grid, metadata = rasterize(layout, obstacles, resolution_m=0.5, inflation_m=0.25)
        cache.mkdir(parents=True, exist_ok=True)
        from PIL import Image
        import yaml
        Image.fromarray(grid).save(cache / "occupancy.png")
        nav = {
            "image": "occupancy.png",
            "resolution": metadata["resolution_m"],
            "origin": metadata["origin_xyz_m"],
            "negate": 0,
            "occupied_thresh": 0.65,
            "free_thresh": 0.196,
        }
        yaml_path.write_text(yaml.safe_dump(nav, sort_keys=False), encoding="utf-8")
    return FactoryMap(yaml_path)


def occupancy_waypoints(scene_key: str, start_xy: tuple[float, float],
                        goal_xy: tuple[float, float], radius: float = 0.25
                        ) -> list[tuple[float, float]] | None:
    """A* on the scene occupancy map. Straight lines cross indoor walls."""
    key = (scene_key, radius)
    if key not in _GRID_CACHE:
        from EAI.scenario_spec.oracle import InflatedGrid
        occupancy = _scene_occupancy(scene_key)
        _GRID_CACHE[key] = (occupancy, InflatedGrid(occupancy, radius))
    occupancy, grid = _GRID_CACHE[key]
    from demo.benchmark.goal_indoor_deployment import _world_path
    start_cell = grid.nearest_passable(start_xy)
    goal_cell = grid.nearest_passable(goal_xy)
    if start_cell is None or goal_cell is None:
        return None

    def _world(cell: tuple[int, int]) -> tuple[float, float]:
        ix, iy = cell
        point = occupancy.world((ix + 0.5) * occupancy.resolution,
                                (iy + 0.5) * occupancy.resolution)
        return float(point[0]), float(point[1])

    cells = grid.path(_world(start_cell), _world(goal_cell))
    if not cells:
        return None
    return [(float(point[0]), float(point[1])) for point in _world_path(occupancy, cells)]


_SECOND_MISSION_TASK_IDS = frozenset({
    "FACTORY-FIRE-01",
    "WAREHOUSE-INBOUND-01",
    "MODERN_HOUSE-DELIVERY-01",
    "CITY-EXIT-01",
    "RESTAURANT-SERVICE-01",
})
_GROUND_SKILLS = frozenset({
    "ground_navigation", "stair_traversal", "pick", "place", "handoff", "cooperative_carry",
})
_REGION_ARGUMENT_KEYS = (
    "region_id", "pickup_region_id", "placement_region_id",
    "receiving_region_id", "destination_region_id", "exit_region_id",
)


def second_mission_constraint_violation(public_task: Mapping[str, Any],
                                        task: Mapping[str, Any]) -> str | None:
    """Return a recorded organization failure, or None when the assignment may run.

    Capability, mobility and region access are execution measurements. This
    does not reject the plan and does not replace the skill kernel.
    """
    from demo.benchmark.goal_constraints import _region_access
    from demo.benchmark.skill_ontology import minimum_agents

    pool = {row["id"]: row for row in public_task["candidate_robot_pool"]}
    assigned = [agent for agent in task.get("assigned_agents") or [] if isinstance(agent, str)]
    skill = str(task.get("skill") or "")
    needed = minimum_agents(skill) if skill else 1
    if len(assigned) < needed:
        return f"constraint_violation:agent_count:{skill}:{len(assigned)}:{needed}"
    world = public_task["public_world"]
    regions = {row["id"]: row for row in world["regions"]}
    routes = {row["id"]: row for row in world.get("routes") or []}
    arguments = task.get("arguments") or {}
    region_ids: list[str] = []
    for key in _REGION_ARGUMENT_KEYS:
        value = arguments.get(key)
        if isinstance(value, str):
            region_ids.append(value)
    for route_id in arguments.get("route_ids") or []:
        route = routes.get(route_id) if isinstance(route_id, str) else None
        if route is None:
            continue
        for endpoint in ("from_region_id", "to_region_id"):
            value = route.get(endpoint)
            if isinstance(value, str):
                region_ids.append(value)
    required = {item for item in task.get("required_capabilities") or [] if isinstance(item, str)}
    for agent in assigned:
        row = pool.get(agent)
        if row is None:
            return f"constraint_violation:unknown_agent:{agent}"
        if required and not required <= set(row["public_capabilities"]):
            return f"constraint_violation:capability:{agent}:{skill}"
        if skill in _GROUND_SKILLS and row["mobility"] != "ground":
            return f"constraint_violation:mobility:{agent}:{skill}"
        if skill == "aerial_navigation" and row["mobility"] != "aerial":
            return f"constraint_violation:mobility:{agent}:{skill}"
        for region_id in region_ids:
            region = regions.get(region_id)
            if region is None:
                continue
            try:
                _region_access(region, row, str(task.get("task_id") or skill))
            except ValueError:
                return f"constraint_violation:region_access:{agent}:{region_id}"
    return None
_CITY_EXIT_OBSERVE_MODES = frozenset({
    "dismissal_roster",
    "dismissal_headcount",
    "corridor_clearance",
})
_CITY_EXIT_OBSERVE_SUBJECTS = {
    "dismissal_roster": "dismissal_gathering",
    "dismissal_headcount": "dismissal_assembly",
    "corridor_clearance": "station_corridor",
}
_DEPLOY_SUPPORT_TASKS = frozenset({
    "MODERN_HOUSE-DELIVERY-01",
    "FACTORY-FIRE-01",
})
_PAYLOAD_PRESENCE_MODES = frozenset({
    "static_signoff",
    "fixed_joint_button_strike",
})


def _quat_rotate_wxyz(rotation: list[float], vector: list[float]) -> tuple[float, float, float]:
    w, x, y, z = rotation
    vx, vy, vz = vector
    tx = 2.0 * (y * vz - z * vy)
    ty = 2.0 * (z * vx - x * vz)
    tz = 2.0 * (x * vy - y * vx)
    return (
        vx + w * tx + (y * tz - z * ty),
        vy + w * ty + (z * tx - x * tz),
        vz + w * tz + (x * ty - y * tx),
    )


def _second_mission_sockets(public_task: Mapping[str, Any], object_id: str,
                            pose: Mapping[str, Any],
                            point_ids: Mapping[str, str] | None) -> dict[str, list[float]] | None:
    """World support/grip points for a second mission, or None for GOAL-01.

    An empty dict means the task named a point that the public object does not
    declare. GOAL-01 keeps the existing center gate.
    """
    if public_task.get("task_id") not in _SECOND_MISSION_TASK_IDS or not point_ids:
        return None
    objects = {row.get("id"): row for row in public_task.get("public_world", {}).get("objects", [])}
    points = {
        point.get("id"): point.get("position_m")
        for point in (objects.get(object_id) or {}).get("physical_properties", {}).get("grasp_points", [])
    }
    origin = [float(value) for value in pose["position_m"]]
    rotation = [float(value) for value in (pose.get("rotation_wxyz") or [1.0, 0.0, 0.0, 0.0])]
    sockets: dict[str, list[float]] = {}
    for public_id, point_id in point_ids.items():
        local = points.get(point_id)
        if not isinstance(local, list) or len(local) != 3:
            return {}
        delta = _quat_rotate_wxyz(rotation, [float(value) for value in local])
        sockets[public_id] = [origin[index] + delta[index] for index in range(3)]
    return sockets


def _object_world_half_xy(public_task: Mapping[str, Any], object_id: str,
                          rotation: list[float]) -> tuple[float, float] | None:
    """World XY half-extents of a possibly yawed box."""
    import math
    objects = {row.get("id"): row for row in public_task.get("public_world", {}).get("objects", [])}
    size = ((objects.get(object_id) or {}).get("physical_properties") or {}).get("size_m")
    if not isinstance(size, list) or len(size) < 2:
        return None
    hx, hy = abs(float(size[0])) / 2.0, abs(float(size[1])) / 2.0
    w, x, y, z = (list(rotation) + [1.0, 0.0, 0.0, 0.0])[:4]
    yaw = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    cosine, sine = abs(math.cos(yaw)), abs(math.sin(yaw))
    return (hx * cosine + hy * sine, hx * sine + hy * cosine)


def _support_approach_paths(starts: Mapping[str, tuple[float, float]],
                            sockets: Mapping[str, list[float]],
                            origin: list[float],
                            half_xy: tuple[float, float],
                            *,
                            stand_m: float = 0.62,
                            margin_m: float = 1.80,
                            final_heading_xy: tuple[float, float] = (-1.0, 0.0),
                            ) -> dict[str, list[tuple[float, float]]]:
    """Cardinal route to a rally outside each support face.

    The last leg runs along the shelving direction so both carriers finish
    facing the aisle. A fixed joint cannot yaw them afterwards.
    """
    import math
    south = origin[1] - half_xy[1] - margin_m
    north = origin[1] + half_xy[1] + margin_m
    west = origin[0] - half_xy[0] - margin_m
    east = origin[0] + half_xy[0] + margin_m
    by_face = sorted(starts, key=lambda runtime_id: sockets[runtime_id][1])
    ends: dict[str, float] = {}
    if len(by_face) == 1:
        runtime_id = by_face[0]
        ends[runtime_id] = west if abs(starts[runtime_id][0] - west) <= abs(starts[runtime_id][0] - east) else east
    else:
        ends[by_face[0]] = west
        ends[by_face[-1]] = east
    paths: dict[str, list[tuple[float, float]]] = {}
    for runtime_id, start in starts.items():
        ox, oy = sockets[runtime_id][0] - origin[0], sockets[runtime_id][1] - origin[1]
        flat = math.hypot(ox, oy)
        if flat < 0.05:
            dx, dy = start[0] - origin[0], start[1] - origin[1]
            away = math.hypot(dx, dy) or 1.0
            paths[runtime_id] = [(origin[0] + dx / away * stand_m,
                                  origin[1] + dy / away * stand_m)]
            continue
        ux, uy = ox / flat, oy / flat
        stand_xy = (sockets[runtime_id][0] + ux * stand_m,
                    sockets[runtime_id][1] + uy * stand_m)
        heading = math.hypot(*final_heading_xy) or 1.0
        hx, hy = final_heading_xy[0] / heading, final_heading_xy[1] / heading
        approach = (stand_xy[0] - hx * 1.6, stand_xy[1] - hy * 1.6)
        face_y = north if uy >= 0.0 else south
        own_y = north if start[1] >= origin[1] else south
        end_x = ends[runtime_id]
        if abs(own_y - face_y) < 1e-6:
            route = [(start[0], own_y), approach, stand_xy]
        else:
            route = [
                (start[0], own_y),
                (end_x, own_y),
                (end_x, approach[1]),
                approach,
                stand_xy,
            ]
        points: list[tuple[float, float]] = []
        for point in route:
            if not points or math.dist(points[-1], point) > 0.2:
                points.append((float(point[0]), float(point[1])))
        paths[runtime_id] = points
    return paths


def _city_person_positions(runtime: Any, person_ids: Any) -> dict[str, tuple[float, float]] | None:
    """Read one finite XY for every roster id. A missing actor fails the whole read."""
    import math
    if runtime is None or not isinstance(person_ids, (list, tuple)) or not person_ids:
        return None
    positions: dict[str, tuple[float, float]] = {}
    for person_id in person_ids:
        try:
            raw = runtime.position(person_id)
            xy = (float(raw[0]), float(raw[1]))
        except (TypeError, ValueError, KeyError, RuntimeError, IndexError, AttributeError):
            return None
        if not all(math.isfinite(value) for value in xy):
            return None
        positions[str(person_id)] = xy
    return positions


def _city_exit_observation(task: Mapping[str, Any], deployment: Mapping[str, Any] | None,
                           agent_xy: tuple[float, float] | None) -> tuple[dict[str, Any] | None, str | None]:
    """Admit a dismissal receipt only from measured people and a robot in its region.

    Roster and headcount require every roster person inside the named region.
    Corridor clearance requires none of them inside the corridor.
    """
    arguments = task.get("arguments") or {}
    mode = arguments.get("observation_mode")
    if mode not in _CITY_EXIT_OBSERVE_MODES:
        return None, None
    people = (deployment or {}).get("city_exit_people") or {}
    positions = _city_person_positions(people.get("runtime"), people.get("person_ids"))
    if positions is None:
        return None, "observe_subject_unmeasured"
    if arguments.get("subject_ref") != _CITY_EXIT_OBSERVE_SUBJECTS[mode]:
        return None, "observe_subject_unmeasured"
    region_id = arguments.get("region_id")
    target, bounds = (_region_geometry(deployment, region_id)
                      if isinstance(region_id, str) else (None, None))
    if (agent_xy is None or target is None
            or not _inside_region(agent_xy, target, bounds, "ground_navigation")):
        return None, "observe_agent_not_in_region"
    subject = arguments.get("subject_ref")
    subject_target, subject_bounds = _region_geometry(deployment, subject)
    if subject_target is None:
        return None, "observe_subject_unmeasured"
    inside = [
        person_id for person_id, xy in positions.items()
        if _inside_region(xy, subject_target, subject_bounds, "ground_navigation")
    ]
    if mode == "corridor_clearance":
        if inside:
            return None, "observe_subject_not_measured"
    elif len(inside) != len(positions):
        return None, "observe_subject_not_measured"
    return {
        "people": [
            {"public_id": person_id, "status": "measured", "position_m": [xy[0], xy[1]]}
            for person_id, xy in positions.items()
        ],
    }, None


def advance_city_guidance(runtime: Any, person_ids: list[str], deployment: Mapping[str, Any] | None,
                          assembly_region_id: str | None, *, dt: float,
                          max_steps: int) -> tuple[dict[str, tuple[float, float]] | None, str | None]:
    """Walk measured people from the gathering into the assembly region."""
    if assembly_region_id != "dismissal_assembly" or dt <= 0.0 or max_steps < 1:
        return None, "guide_people_not_at_assembly"
    gathering, gathering_bounds = _region_geometry(deployment, "dismissal_gathering")
    assembly, assembly_bounds = _region_geometry(deployment, assembly_region_id)
    if gathering is None or assembly is None:
        return None, "guide_people_not_at_assembly"
    start = _city_person_positions(runtime, person_ids)
    if start is None:
        return None, "guide_people_unmeasured"
    if any(not _inside_region(xy, gathering, gathering_bounds, "ground_navigation")
           for xy in start.values()):
        return None, "guide_people_not_at_gathering"
    for _ in range(max_steps):
        runtime.update(float(dt), actor_ids=list(person_ids))
        current = _city_person_positions(runtime, person_ids)
        if current is None:
            return None, "guide_people_unmeasured"
        if all(_inside_region(xy, assembly, assembly_bounds, "ground_navigation")
               for xy in current.values()):
            return current, None
    return None, "guide_people_not_at_assembly"


def _payload_presence_observation(task: Mapping[str, Any], deployment: Mapping[str, Any] | None,
                                   agent_xy: tuple[float, float] | None,
                                   object_xy: tuple[float, float] | None,
                                   ) -> tuple[dict[str, Any] | None, str | None]:
    """Admit a sign-off or button receipt from a measured body and a robot in the region.

    The button selection has no UR5, so the strike receipt is the scout and the
    button measured together in the button region. The button is not carried.
    """
    arguments = task.get("arguments") or {}
    if arguments.get("observation_mode") not in _PAYLOAD_PRESENCE_MODES:
        return None, None
    subject = arguments.get("subject_ref")
    region_id = arguments.get("region_id")
    if not isinstance(subject, str) or not isinstance(region_id, str) or object_xy is None:
        return None, "observe_subject_unmeasured"
    target, bounds = _region_geometry(deployment, region_id)
    if (agent_xy is None or target is None
            or not _inside_region(agent_xy, target, bounds, "ground_navigation")):
        return None, "observe_agent_not_in_region"
    if not _inside_region(object_xy, target, bounds, "ground_navigation"):
        return None, "observe_subject_not_measured"
    return {
        "objects": [{
            "public_id": subject,
            "status": "measured",
            "physx": {"status": "measured", "position_m": [object_xy[0], object_xy[1]]},
        }],
    }, None


def _fire_scene_observation(task: Mapping[str, Any], deployment: Mapping[str, Any] | None,
                            agent_xy: tuple[float, float] | None,
                            ) -> tuple[dict[str, Any] | None, str | None]:
    """Admit fire sensing when Carter is measured inside the fire site.

    This is not the object-clearance closeout. The extinguisher is supposed
    to arrive in that same region later.
    """
    arguments = task.get("arguments") or {}
    if arguments.get("observation_mode") != "fire_scene_sense":
        return None, None
    region_id = arguments.get("region_id")
    if arguments.get("subject_ref") != "fire_site" or region_id != "fire_site":
        return None, "observe_subject_unmeasured"
    target, bounds = _region_geometry(deployment, "fire_site")
    if (agent_xy is None or target is None
            or not _inside_region(agent_xy, target, bounds, "ground_navigation")):
        return None, "observe_agent_not_in_region"
    return {
        "region": {
            "public_id": "fire_site",
            "status": "measured",
            "position_m": [agent_xy[0], agent_xy[1]],
        },
    }, None


def _customer_order_observation(task: Mapping[str, Any], deployment: Mapping[str, Any] | None,
                                agent_xy: tuple[float, float] | None) -> tuple[dict[str, Any] | None, str | None]:
    """Admit a static-customer order only from a measured subject and a robot in the region."""
    import math
    arguments = task.get("arguments") or {}
    if arguments.get("observation_mode") != "customer_order":
        return None, None
    subject = arguments.get("subject_ref")
    subjects = (deployment or {}).get("service_subjects") or {}
    row = subjects.get(subject) if isinstance(subject, str) else None
    position = row.get("position_m") if isinstance(row, Mapping) else None
    finite = False
    parsed: list[float] = []
    if isinstance(position, (list, tuple)) and len(position) >= 2:
        try:
            parsed = [float(value) for value in position[:3]]
            finite = all(math.isfinite(value) for value in parsed[:2])
        except (TypeError, ValueError):
            finite = False
    if not finite:
        return None, "observe_subject_unmeasured"
    region_id = arguments.get("region_id")
    target, bounds = (_region_geometry(deployment, region_id)
                      if isinstance(region_id, str) else (None, None))
    if (agent_xy is None or target is None
            or not _inside_region(agent_xy, target, bounds, "ground_navigation")):
        return None, "observe_agent_not_in_region"
    return {
        "objects": [{
            "public_id": subject,
            "status": "measured",
            "physx": {"status": "measured", "position_m": parsed},
        }],
    }, None


def route_inflation_radius_m(robot_type: str | None) -> float:
    """Occupancy inflation for one robot type.

    The radius follows the public body size. Pepper keeps the measured 0.45 m
    floor: half of its declared body still left the base against a wall.
    """
    if not robot_type:
        return 0.25
    from demo.benchmark.goal_driven_fixtures import robot_physical_profile

    body = robot_physical_profile(robot_type)["body_size_m"]
    half = 0.5 * max(float(body[0]), float(body[1]))
    if robot_type == "pepper":
        return max(half, _PEPPER_OCCUPANCY_RADIUS_M)
    return max(0.25, min(half, 0.45))


def restaurant_pepper_stand(task_id: str, region_id: str,
                            agent_ids: list[str],
                            robot_types: Mapping[str, str] | None = None,
                            ) -> tuple[float, float] | None:
    """A pepper-type robot meets a diner at the 0.8 m stand.

    The stand follows the assigned robot's type. Any pepper in the public
    pool uses it; a different robot id of that type is the same skill.
    """
    if task_id != "RESTAURANT-SERVICE-01":
        return None
    types = robot_types or {}
    if not any(types.get(agent) == "pepper" for agent in agent_ids):
        return None
    if region_id not in {"dining_table_a", "dining_table_b"}:
        return None
    from demo.restaurant_benchmark.service_layout import service_layout
    point = service_layout()["pepper_interaction"][region_id]["position_xy_m"]
    return float(point[0]), float(point[1])


def _second_mission_subject_measured(public_task: Mapping[str, Any], task: Mapping[str, Any],
                                     observed: Mapping[str, Any],
                                     deployment: Mapping[str, Any] | None = None) -> bool:
    """Second-mission observe counts only the named object, and only when measured.

    A region subject is the closeout case: every public object must still be
    measured, and none of those poses may remain inside the named region.
    GOAL-01 keeps its existing readback admission.
    """
    if public_task.get("task_id") not in _SECOND_MISSION_TASK_IDS:
        return True
    mode = task.get("arguments", {}).get("observation_mode")
    if public_task.get("task_id") == "CITY-EXIT-01" and mode in _CITY_EXIT_OBSERVE_MODES:
        people = observed.get("people") or []
        return bool(people) and all(
            isinstance(row, Mapping) and row.get("status") == "measured"
            for row in people)
    if mode == "fire_scene_sense":
        region = observed.get("region") or {}
        return (isinstance(region, Mapping) and region.get("public_id") == "fire_site"
                and region.get("status") == "measured")
    if mode in _PAYLOAD_PRESENCE_MODES:
        subject = task.get("arguments", {}).get("subject_ref")
        rows = {row.get("public_id"): row for row in observed.get("objects") or []}
        return rows.get(subject, {}).get("status") == "measured"
    subject = task.get("arguments", {}).get("subject_ref")
    object_ids = {row["id"] for row in public_task["public_world"]["objects"]}
    rows = {row.get("public_id"): row for row in observed.get("objects") or []}
    if subject in object_ids:
        return rows.get(subject, {}).get("status") == "measured"
    region_id = task.get("arguments", {}).get("region_id")
    regions = {row.get("id") for row in public_task.get("public_world", {}).get("regions", [])}
    if subject != region_id or subject not in regions or not object_ids:
        return False
    center, bounds = _region_geometry(deployment, subject)
    if center is None:
        return False
    for object_id in object_ids:
        row = rows.get(object_id) or {}
        # Closeout admits only a live PhysX sample. A top-level or USD root
        # pose stays at spawn while Fabric is on, and that pose must not make
        # an unmeasured object look clear of the region.
        physx = row.get("physx") if isinstance(row.get("physx"), Mapping) else {}
        position = physx.get("position_m") if physx.get("status") == "measured" else None
        if (row.get("status") != "measured" or physx.get("status") != "measured"
                or not isinstance(position, (list, tuple)) or len(position) < 2):
            return False
        try:
            xy = (float(position[0]), float(position[1]))
        except (TypeError, ValueError):
            return False
        if not all(value == value and abs(value) != float("inf") for value in xy):
            return False
        if _inside_region(xy, center, bounds, "ground_navigation"):
            return False
    return True


def _region_geometry(deployment: Mapping[str, Any] | None,
                     region_id: str) -> tuple[tuple[float, float] | None, list[float] | None]:
    if deployment is None:
        return None, None
    for row in deployment.get("region_bindings") or []:
        if row.get("id") != region_id:
            continue
        geometry = row.get("candidate_geometry") or {}
        center = geometry.get("center_m")
        point = None
        if isinstance(center, (list, tuple)) and len(center) >= 2:
            point = (float(center[0]), float(center[1]))
        raw_bounds = geometry.get("aabb_xy_m")
        bounds = None
        if isinstance(raw_bounds, (list, tuple)) and len(raw_bounds) == 4:
            bounds = [float(value) for value in raw_bounds]
        return point, bounds
    return None, None


def _inside_region(xy: tuple[float, float], center: tuple[float, float],
                   bounds: list[float] | None, skill: str) -> bool:
    import math
    if bounds is not None:
        # A carried prop often fills a 1 m cell. The root can be in the region
        # while remaining a body-radius outside the published box.
        margin = 0.4
        return (bounds[0] - margin <= xy[0] <= bounds[2] + margin
                and bounds[1] - margin <= xy[1] <= bounds[3] + margin)
    limit = 2.0 if skill == "aerial_navigation" else 1.2
    return math.hypot(xy[0] - center[0], xy[1] - center[1]) <= limit


def _action(identifier: str, kind: str, payload: Mapping[str, Any],
            submitted_at_s: float) -> dict[str, Any]:
    return {"schema": "eai.benchmark.goal_action", "version": 1,
            "action_id": identifier, "submitted_at_s": float(submitted_at_s),
            "type": kind, "payload": deepcopy(dict(payload))}


def build_house_episode_plan(package: Mapping[str, Any]) -> dict[str, Any]:
    """Use the first authored witness as the serial native plan."""
    witnesses = package["hidden_evaluator"]["witness_plans"]
    if not witnesses:
        raise ValueError("house package has no witness plan")
    plan = deepcopy(witnesses[0]["plan"])
    plan["plan_id"] = HOUSE_EPISODE_PLAN_ID
    validate_goal_plan(plan, task_spec=package["public_task"])
    return plan


def house_runtime_state(public_task: Mapping[str, Any],
                        plan: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """A legal initial snapshot. Each robot starts where its first route begins."""
    regions = public_task["public_world"]["regions"]
    aerial = next(row["id"] for row in regions if "aerial_navigation" in row["access_modes"])
    ground = next(row["id"] for row in regions
                  if "ground_navigation" in row["access_modes"] or "stair_traversal" in row["access_modes"])
    robots = {robot["id"]: aerial if robot["mobility"] == "aerial" else ground
              for robot in public_task["candidate_robot_pool"]}
    if plan is not None:
        routes = {row["id"]: row for row in public_task["public_world"].get("routes") or []}
        seen: set[str] = set()
        for task in plan.get("tasks") or []:
            arguments = task.get("arguments") or {}
            route_ids = arguments.get("route_ids") or []
            origin = None
            if route_ids and route_ids[0] in routes:
                origin = routes[route_ids[0]].get("from_region_id")
            elif isinstance(arguments.get("region_id"), str):
                origin = arguments["region_id"]
            if not isinstance(origin, str):
                continue
            for agent in task.get("assigned_agents") or []:
                if agent in robots and agent not in seen:
                    robots[agent] = origin
                    seen.add(agent)
    objects = {}
    for item in public_task["public_world"]["objects"]:
        initial = (item.get("physical_properties") or {}).get("initial_region_id")
        objects[item["id"]] = {"region_id": initial if isinstance(initial, str) else ground}
    return {"robot_regions": robots, "object_states": objects}


class HouseWitnessParticipant:
    """Dispatch the House witness plan one skill at a time."""

    def __init__(self, plan: Mapping[str, Any]):
        self._plan = deepcopy(dict(plan))
        self._opened = False
        self._dispatched = 0
        self._terminal: set[str] = set()

    def manifest(self) -> Mapping[str, Any]:
        return {"kind": "participant", "id": "house-native-goal-witness", "version": 1,
                "interface": "eai.benchmark.goal_algorithm_adapter", "interface_version": 1,
                "evaluation_track": "goal_driven",
                "input_scope": "participant_visible_events_only",
                "control_scope": "benchmark_managed_low_level_control"}

    def initialize(self, public_task: Mapping[str, Any],
                   participant_api: Mapping[str, Any]) -> None:
        del public_task, participant_api

    def act(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not self._opened:
            if not any(item["type"] == "episode_started" for item in events):
                return []
            self._opened = True
            return [_action("house_witness_select", "select_team",
                            {"agent_ids": list(self._plan["assigned_team"])}, 0.0),
                    _action("house_witness_plan", "submit_plan",
                            {"plan": deepcopy(self._plan)}, 0.0)]
        for item in events:
            if item["type"] == "action_result" and item["payload"].get("status") in {
                    "succeeded", "failed", "cancelled"}:
                self._terminal.add(item["payload"]["action_id"])
        tasks = self._plan["tasks"]
        if self._dispatched >= len(tasks):
            return []
        previous = f"house_witness_dispatch_{self._dispatched - 1}"
        if self._dispatched and previous not in self._terminal:
            return []
        index = self._dispatched
        self._dispatched += 1
        now = max((item["time_s"] for item in events), default=0.0)
        return [_action(f"house_witness_dispatch_{index}", "dispatch_skill",
                        {"task_id": tasks[index]["task_id"], "evidence_refs": []}, now)]

    def close(self) -> None:
        pass


class HouseNativeGoalBackend:
    """Step an open House session and record an honest skill receipt.

    ``session is None`` is the offline contract test: time advances by the
    declared duration. GOAL-01 observe is a symbolic admission. Second-mission
    observe fails closed without a measurement. Other skills fail as
    adapter-pending. A live session steps physics, reads goal objects for
    ``observe``, and still fails skills whose motion adapter is not bound.
    """

    def __init__(self, public_task: Mapping[str, Any], *, session: Any = None,
                 deployment: Mapping[str, Any] | None = None,
                 plan: Mapping[str, Any] | None = None):
        self.public_task = public_task
        self.session = session
        self.deployment = deployment
        self.plan = plan
        self.time_s = 0.0
        self.evidence: list[dict[str, Any]] = []
        # Per-task host diagnostics are execution evidence only. They never
        # alter the witness plan, release gates, or scoreability.
        self._last_execution_diagnostics: dict[str, Any] = {}
        self._last_navigation_diagnostics: dict[str, Any] = {}
        # Pepper's XDisp/YDisp frame is the spawn root yaw, not the live base_link yaw.
        self._pepper_drive_frame_yaw: dict[str, float] = {}
        initial = house_runtime_state(public_task, plan)
        self.regions = initial["robot_regions"]
        self.objects = initial["object_states"]
        self.holds: dict[str, dict[str, Any]] = {}

    def execute(self, task: Mapping[str, Any], *, episode_id: str,
                trusted_report: Mapping[str, Any]) -> Mapping[str, Any]:
        ledger = trusted_report["session"]
        row = ledger["tasks"][task["task_id"]]
        if (ledger["episode_id"] != episode_id or row["status"] != "pending"
                or row["task"] != task or row["plan_id"] != ledger["current_plan_id"]):
            raise ValueError("native house task does not match the pending GoalSession dispatch")
        duration = float(task["execution"]["duration_s"])
        window_end = float(task["execution"]["start_s"]) + duration
        observed = None
        observe_error = None
        observe_detail = None
        if self.session is None:
            self.time_s = max(self.time_s, window_end)
        else:
            import torch
            dt = float(self.session.base_env.step_dt)
            steps = max(1, min(30, int(duration / dt) if dt else 1))
            mode = (task.get("arguments") or {}).get("observation_mode")
            # Pepper's holonomic target keeps integrating after a zero command.
            # An order or receipt must be judged at the pose navigation just
            # accepted, before that leftover target walks the base out of the box.
            hold_pose = (
                self.public_task.get("task_id") in _SECOND_MISSION_TASK_IDS
                and (
                    (task["skill"] == "observe" and mode in {"customer_order", "static_signoff"})
                    or task["skill"] == "relay"
                )
            )
            if not hold_pose:
                agents = list(self.session.possible_agents)
                for _ in range(steps):
                    actions = {agent: torch.zeros((1, 3), device=self.session.device) for agent in agents}
                    self.session.env.step(actions)
            # The ledger stays on the plan window. Driving can take longer than
            # the nominal duration; folding that sim time into the receipt puts
            # the next skill outside its own half-open window.
            self.time_s = max(self.time_s, window_end)
            if self.deployment is not None and task["skill"] == "observe":
                mode = (task.get("arguments") or {}).get("observation_mode")
                if (self.public_task.get("task_id") in _SECOND_MISSION_TASK_IDS
                        and mode == "customer_order"):
                    runtime_id = self._runtime_id(task["assigned_agents"][0])
                    state = self._moving_base_pose(runtime_id) if runtime_id else None
                    agent_xy = (state[0], state[1]) if state is not None else None
                    observed, observe_error = _customer_order_observation(
                        task, self.deployment, agent_xy)
                    observe_detail = observe_error
                elif (self.public_task.get("task_id") == "CITY-EXIT-01"
                        and mode in _CITY_EXIT_OBSERVE_MODES):
                    runtime_id = self._runtime_id(task["assigned_agents"][0])
                    state = self._moving_base_pose(runtime_id) if runtime_id else None
                    agent_xy = (state[0], state[1]) if state is not None else None
                    observed, observe_error = _city_exit_observation(
                        task, self.deployment, agent_xy)
                    observe_detail = observe_error
                elif (self.public_task.get("task_id") in _DEPLOY_SUPPORT_TASKS
                        and mode in _PAYLOAD_PRESENCE_MODES):
                    runtime_id = self._runtime_id(task["assigned_agents"][0])
                    state = self._moving_base_pose(runtime_id) if runtime_id else None
                    agent_xy = (state[0], state[1]) if state is not None else None
                    subject = (task.get("arguments") or {}).get("subject_ref")
                    object_xy = self._object_xy(subject) if isinstance(subject, str) else None
                    observed, observe_error = _payload_presence_observation(
                        task, self.deployment, agent_xy, object_xy)
                    observe_detail = observe_error
                elif (self.public_task.get("task_id") == "FACTORY-FIRE-01"
                        and mode == "fire_scene_sense"):
                    runtime_id = self._runtime_id(task["assigned_agents"][0])
                    state = self._moving_base_pose(runtime_id) if runtime_id else None
                    agent_xy = (state[0], state[1]) if state is not None else None
                    observed, observe_error = _fire_scene_observation(
                        task, self.deployment, agent_xy)
                    observe_detail = observe_error
                else:
                    instances = self.deployment.get("object_instances")
                    if (self.public_task.get("task_id") in _SECOND_MISSION_TASK_IDS
                            and not instances):
                        # Factory button/fire and house sign-off have no native
                        # object rows. Reading them raises KeyError and used to
                        # abort the episode before a receipt.
                        observed = None
                        observe_error = "observe_objects_unreadable"
                        observe_detail = "KeyError:'object_instances'"
                    else:
                        from demo.benchmark.goal_native_objects import read_goal_objects
                        import omni.usd
                        stage = omni.usd.get_context().get_stage()
                        try:
                            observed = read_goal_objects(
                                stage, self.session.base_env.scene, self.deployment)
                        except KeyError as exc:
                            observed = None
                            observe_error = "observe_objects_unreadable"
                            observe_detail = f"KeyError:{exc}"
            if observed is not None and not _second_mission_subject_measured(
                        self.public_task, task, observed, self.deployment):
                    subject = (task.get("arguments") or {}).get("subject_ref")
                    summary = []
                    for item in observed.get("objects") or []:
                        physx = item.get("physx") or {}
                        summary.append(
                            f"{item.get('public_id')}:{item.get('status')}:"
                            f"{physx.get('position_m')}")
                    print(f"house_observe {task.get('task_id')} subject={subject} "
                          f"{' '.join(summary)}", flush=True)
                    observed = None
                    observe_error = "observe_subject_not_measured"
                    observe_detail = None
        self._last_execution_diagnostics = {}
        self._last_navigation_diagnostics = {}
        if observe_error is not None:
            self._last_execution_diagnostics["observe_error"] = observe_detail or observe_error
        status, reason = self._skill_result(task, observed, observe_error=observe_error)
        if self._last_navigation_diagnostics:
            self._last_execution_diagnostics["navigation"] = deepcopy(self._last_navigation_diagnostics)
        self.evidence.append({
            "task_id": task["task_id"], "skill": task["skill"], "status": status,
            "reason": reason, "time_s": self.time_s,
            "execution_diagnostics": deepcopy(self._last_execution_diagnostics),
        })
        print(f"house_skill {task['task_id']} {status} {reason or ''}", flush=True)
        receipt = {
            "backend_id": HOUSE_BACKEND_ID,
            "receipt_ref": f"{HOUSE_BACKEND_ID}:{episode_id}:{task['task_id']}",
            "task_id": task["task_id"],
            "action_id": row["dispatch_action_id"],
            "status": status,
            "completed_at_s": self.time_s,
            "episode_id": episode_id,
            "evidence_refs": [f"house:{task['task_id']}:{status}"],
        }
        if status == "succeeded":
            self._advance_symbolic_state(task)
        return {"status": status, "completed_at_s": self.time_s, "receipt": receipt,
                "runtime_state": {"robot_regions": dict(self.regions),
                                  "object_states": deepcopy(self.objects)}}

    def _skill_result(self, task: Mapping[str, Any], observed: Any,
                      observe_error: str | None = None) -> tuple[str, str | None]:
        skill = task["skill"]
        if self.session is None:
            if skill == "observe":
                # GOAL-01 observe stays a symbolic admission. Second-mission
                # observe has no measurement offline, so it fails closed.
                if self.public_task.get("task_id") in _SECOND_MISSION_TASK_IDS:
                    return "failed", "observe_objects_unreadable"
                return "succeeded", None
            return "failed", _PENDING_REASON
        if self.public_task.get("task_id") in _SECOND_MISSION_TASK_IDS:
            violation = second_mission_constraint_violation(self.public_task, task)
            if violation is not None:
                return "failed", violation
        if skill == "observe":
            if observed:
                return "succeeded", None
            if (self.public_task["task_id"] in _SECOND_MISSION_TASK_IDS
                    and observe_error):
                return "failed", observe_error
            return "failed", "observe_objects_unreadable"
        if skill in {"ground_navigation", "aerial_navigation", "stair_traversal"}:
            return self._drive_to_region(task)
        if skill in {"pick", "place", "handoff", "cooperative_carry"}:
            return self._manipulate(task)
        if skill == "relay" and self.public_task.get("task_id") in _SECOND_MISSION_TASK_IDS:
            return self._relay_from_region(task)
        if skill == "guide_people" and self.public_task.get("task_id") == "CITY-EXIT-01":
            return self._guide_city_people(task)
        return "failed", _PENDING_REASON

    def _guide_city_people(self, task: Mapping[str, Any]) -> tuple[str, str | None]:
        """Move the measured roster into the assembly and admit only that arrival."""
        arguments = task.get("arguments") or {}
        person_ids = arguments.get("person_ids")
        people = (self.deployment or {}).get("city_exit_people") or {}
        roster = people.get("person_ids")
        if not isinstance(person_ids, (list, tuple)) or not isinstance(roster, (list, tuple)):
            return "failed", "guide_people_unmeasured"
        if set(person_ids) != set(roster) or not person_ids:
            return "failed", "guide_roster_mismatch"
        try:
            dt = float(self.session.base_env.step_dt)
        except (AttributeError, TypeError, ValueError):
            return "failed", "guide_people_unmeasured"
        max_steps = max(1, int(25.0 / dt)) if dt > 0.0 else 1
        arrived, error = advance_city_guidance(
            people.get("runtime"), list(person_ids), self.deployment,
            arguments.get("assembly_region_id"), dt=dt, max_steps=max_steps)
        self._last_execution_diagnostics["guide_people"] = {
            "person_ids": list(person_ids),
            "assembly_region_id": arguments.get("assembly_region_id"),
            "positions_m": None if arrived is None else {
                key: [value[0], value[1]] for key, value in arrived.items()
            },
        }
        if error:
            return "failed", error
        return "succeeded", None

    def _relay_from_region(self, task: Mapping[str, Any]) -> tuple[str, str | None]:
        """Publish a message only while the assigned robot is measured inside the region."""
        arguments = task["arguments"]
        region_id = arguments.get("region_id")
        message = arguments.get("message_kind")
        if not isinstance(region_id, str) or not isinstance(message, str) or not message:
            return "failed", "relay_arguments_incomplete"
        runtime_id = self._runtime_id(task["assigned_agents"][0])
        state = self._moving_base_pose(runtime_id) if runtime_id else None
        if state is None:
            return "failed", "relay_pose_unreadable"
        target, bounds = _region_geometry(self.deployment, region_id)
        if target is None or not _inside_region((state[0], state[1]), target, bounds, "ground_navigation"):
            return "failed", "relay_agent_not_in_region"
        self._last_execution_diagnostics["relay"] = {
            "message_kind": message,
            "region_id": region_id,
            "position_m": [float(state[0]), float(state[1]), float(state[2])],
        }
        return "succeeded", None

    def _moving_base_pose(self, runtime_id: str) -> list[float] | None:
        robot_type = None
        for row in (self.deployment or {}).get("robot_instances") or []:
            if row.get("runtime_id") == runtime_id:
                robot_type = row.get("type")
                break
        try:
            return moving_base_pose(self.session, runtime_id, robot_type)
        except (AttributeError, KeyError, RuntimeError, TypeError):
            return None

    def _advance_symbolic_state(self, task: Mapping[str, Any]) -> None:
        """Move the ledger to the region this successful skill actually ends in."""
        skill = task["skill"]
        arguments = task["arguments"]
        destination_field = {
            "ground_navigation": "region_id",
            "aerial_navigation": "region_id",
            "stair_traversal": "exit_region_id",
            "place": "placement_region_id",
            "handoff": "receiving_region_id",
            "cooperative_carry": "destination_region_id",
        }.get(skill)
        destination = arguments.get(destination_field) if destination_field else None
        if isinstance(destination, str):
            moved = []
            for agent in task["assigned_agents"]:
                if agent in self.regions:
                    self.regions[agent] = destination
                    moved.append(agent)
            if self.public_task.get("task_id") in _SECOND_MISSION_TASK_IDS:
                carried = set(moved)
                for object_id, state in self.objects.items():
                    bindings = state.get("carrier_bindings") or []
                    holders = [row.get("agent_id") for row in bindings
                               if row.get("agent_id") in carried]
                    if not holders:
                        continue
                    state["region_id"] = destination
                    measured = self._measured_hold_region(object_id, holders, destination)
                    if measured is None:
                        continue
                    state["region_id"] = measured
                    for agent in holders:
                        if agent in self.regions:
                            self.regions[agent] = measured
        object_id = arguments.get("object_id")
        if not isinstance(object_id, str) or object_id not in self.objects:
            return
        if skill == "pick":
            pickup = arguments.get("pickup_region_id")
            if isinstance(pickup, str):
                for agent in task["assigned_agents"]:
                    if agent in self.regions:
                        self.regions[agent] = pickup
                self.objects[object_id] = {
                    "region_id": pickup,
                    "handling_mode_id": arguments["handling_mode_id"],
                    "carrier_bindings": [dict(row) for row in arguments["carrier_bindings"]],
                }
        elif skill in {"place", "handoff", "cooperative_carry"} and isinstance(destination, str):
            self.objects[object_id] = {"region_id": destination}

    def _drive_to_region(self, task: Mapping[str, Any]) -> tuple[str, str | None]:
        arguments = task["arguments"]
        region_id = arguments.get("exit_region_id") or arguments.get("region_id")
        if not isinstance(region_id, str):
            return "failed", "navigation_region_missing"
        target, bounds = _region_geometry(self.deployment, region_id)
        if target is None:
            return "failed", f"navigation_region_unbound:{region_id}"
        public_ids = list(task["assigned_agents"])
        runtime_ids = [self._runtime_id(agent_id) for agent_id in public_ids]
        if any(item is None for item in runtime_ids):
            return "failed", "navigation_runtime_id_missing"
        if task["skill"] == "aerial_navigation":
            return self._drive_aerial(runtime_ids, target, task["task_id"])
        held = self._held_object_id(public_ids)
        if held is not None:
            return self._drive_held_object(runtime_ids, held, target, bounds, task["task_id"])
        # A 1 m region is smaller than the old 1.2 m center radius, so arrival
        # is the published box. Waypoint tolerance only advances the path.
        # Pepper's table visit is the interaction stand. The published box
        # edge is more than a metre from the diner, and the last formal run
        # stopped there.
        robot_types = {
            row["id"]: row["type"] for row in self.public_task["candidate_robot_pool"]
        }
        stand = restaurant_pepper_stand(
            str(self.public_task.get("task_id") or ""), region_id, public_ids,
            robot_types=robot_types)
        aim = stand or target

        def arrived(states: Mapping[str, list[float]]) -> bool:
            if stand is not None:
                import math
                return all(
                    math.dist((state[0], state[1]), stand) <= 0.25
                    and _inside_region((state[0], state[1]), target, bounds, "ground_navigation")
                    for state in states.values())
            return all(_inside_region((state[0], state[1]), target, bounds, "ground_navigation")
                       for state in states.values())
        return self._follow_ground(
            runtime_ids, {runtime_id: aim for runtime_id in runtime_ids},
            arrived=arrived, label=task["task_id"])

    def _manipulate(self, task: Mapping[str, Any]) -> tuple[str, str | None]:
        import omni.usd
        from demo.benchmark.goal_attachment_probe import LiveBodies
        from demo.benchmark.goal_native_attachment import (
            commit_attachment, prepare_attachment)
        arguments = task["arguments"]
        bindings = list(arguments.get("carrier_bindings") or [])
        carriers = {row["agent_id"]: row["point_id"] for row in bindings
                    if isinstance(row, Mapping)}
        object_id = arguments.get("object_id")
        if not isinstance(object_id, str):
            return "failed", "manipulation_arguments_incomplete"
        self._last_execution_diagnostics = {
            "object_id": object_id,
            "failure_stage": None,
            "approach_gate_m": _APPROACH_GATE_M,
            "approach_gate_passed": False,
            "reach_check_entered": False,
        }
        stage = omni.usd.get_context().get_stage()
        skill = task["skill"]
        if skill in {"place", "handoff"}:
            receipt = self.holds.get(object_id)
            if receipt is None:
                reason = ("place_requires_an_existing_hold" if skill == "place"
                          else "handoff_requires_an_existing_hold")
                return "failed", reason
            destination = (arguments.get("placement_region_id")
                           or arguments.get("receiving_region_id"))
            moved = self._release_hold(stage, object_id, receipt, task, destination)
            return moved
        if not carriers:
            return "failed", "manipulation_arguments_incomplete"
        phase = "body_binding"
        reach_checks: dict[str, list[dict[str, Any]]] = {"prepare_attachment": [], "commit_attachment": []}
        self._last_execution_diagnostics["reach_checks"] = reach_checks
        rejected = False
        try:
            bodies = LiveBodies(self.session.base_env.scene, self.deployment)
            deployed_support = self._uses_deployed_support(object_id, carriers)
            if deployed_support:
                phase = "deploy_support"
                placed = self._deploy_support_carrier(object_id, list(carriers), bodies)
                if placed[0] != "succeeded":
                    self._last_execution_diagnostics["failure_stage"] = "deploy_support"
                    return placed
            else:
                phase = "approach_object"
                approached = self._approach_object(
                    object_id, list(carriers), bodies, carriers)
                if approached[0] != "succeeded":
                    self._last_execution_diagnostics["failure_stage"] = "approach_object"
                    return approached
            rally = self.public_task.get("task_id") in _SECOND_MISSION_TASK_IDS
            if deployed_support:
                phase = "settle"
                runtime_ids = list(self._last_execution_diagnostics.get("runtime_ids") or [])
                self._settle_carriers(runtime_ids, steps=8)
            elif rally and len(carriers) > 1:
                destination = arguments.get("destination_region_id")
                target, _bounds = (_region_geometry(self.deployment, destination)
                                   if isinstance(destination, str) else (None, None))
                if target is not None:
                    object_path = bodies.bindings["objects"][object_id]["body_path"]
                    origin = bodies(object_path)["position_m"]
                    phase = "align"
                    runtime_ids = list(self._last_execution_diagnostics.get("runtime_ids") or [])
                    heading = (target[0] - float(origin[0]), target[1] - float(origin[1]))
                    self._align_carriers(runtime_ids, heading, tolerance=0.06)
                    phase = "settle"
                    self._settle_carriers(runtime_ids, steps=8)
                    self._align_carriers(runtime_ids, heading, tolerance=0.06)
            elif rally:
                runtime_ids = list(self._last_execution_diagnostics.get("runtime_ids") or [])
                object_path = bodies.bindings["objects"][object_id]["body_path"]
                origin = bodies(object_path)["position_m"]
                heading = self._single_hold_heading(task, origin)
                if heading is not None:
                    # Same pre-joint align as the crate pair. Yaw after the
                    # joint does not turn this gait, and reverse would drag the box.
                    phase = "align"
                    self._align_carriers(runtime_ids, heading, tolerance=0.06)
                    phase = "settle"
                    self._settle_carriers(runtime_ids, steps=8)
                    self._align_carriers(runtime_ids, heading, tolerance=0.06)
                else:
                    phase = "settle"
                    self._settle_carriers(runtime_ids)
            phase = "prepare_attachment"
            prepared = prepare_attachment(
                stage, self.public_task, object_id=object_id,
                mode_id=arguments.get("handling_mode_id", "single_grip"),
                carriers=carriers, active_member_ids=list(carriers),
                body_bindings=bodies.bindings, pose_provider=bodies,
                reach_diagnostics=reach_checks[phase],
                ideal_connector=rally)
            if rally:
                # Fabric does not publish the live pose back to USD. The joint
                # parser would otherwise treat the spawn pose as current and snap.
                mirrored = {}
                for public_id in carriers:
                    path = bodies.bindings["members"][public_id]["body_path"]
                    mirrored[path] = bodies(path)
                object_path = bodies.bindings["objects"][object_id]["body_path"]
                mirrored[object_path] = bodies(object_path)
                _mirror_physx_bodies_to_usd(stage, mirrored)
            phase = "commit_attachment"
            receipt = commit_attachment(
                stage, prepared, active_member_ids=list(carriers), pose_provider=bodies,
                reach_diagnostics=reach_checks[phase])
        except Exception as exc:
            rejected = True
            detail = str(exc).splitlines()[0][:160]
            self._last_execution_diagnostics["failure_reason_raw"] = str(exc)
            self._last_execution_diagnostics["failure_stage"] = phase
            return "failed", f"manipulation_rejected:{type(exc).__name__}:{detail}"
        finally:
            self._last_execution_diagnostics["reach_check_entered"] = any(reach_checks.values())
            failed_reach_members = [
                check["agent_id"] for checks in reach_checks.values()
                for check in checks if not check["within_reach"]]
            if rejected and failed_reach_members:
                self._last_execution_diagnostics["failure_agent_ids"] = list(dict.fromkeys(failed_reach_members))
            for row in self._last_execution_diagnostics.get("carriers", []):
                row["reach_check_entered"] = any(
                    check["agent_id"] == row["agent_id"]
                    for checks in reach_checks.values() for check in checks)
        self._last_execution_diagnostics["failure_stage"] = None
        self._last_execution_diagnostics["attachment_status"] = "committed"
        if skill == "pick":
            self.holds[object_id] = receipt
            return "succeeded", None
        destination = arguments.get("destination_region_id")
        result = self._release_hold(stage, object_id, receipt, task, destination)
        if result[0] != "succeeded":
            self._last_execution_diagnostics["failure_stage"] = "transport_or_release"
        return result

    def _uses_deployed_support(self, object_id: str, carriers: Mapping[str, str]) -> bool:
        if self.public_task.get("task_id") not in _DEPLOY_SUPPORT_TASKS or len(carriers) != 1:
            return False
        record = (self.deployment or {}).get("support_deploy") or {}
        return record.get("object_id") == object_id

    def _deploy_support_carrier(self, object_id: str, public_ids: list[str],
                                bodies: Any) -> tuple[str, str | None]:
        """Write the holder onto the pre-locked stand, then let the joint close there.

        The stand and yaw come from the offline ray. The live object may have
        settled, so the stand is rebuilt from that pose at the same heading.
        """
        import math
        import torch
        record = (self.deployment or {}).get("support_deploy") or {}
        if record.get("object_id") != object_id:
            return "failed", "manipulation_deploy_unbound"
        public_id = public_ids[0]
        runtime_id = self._runtime_id(public_id)
        binding = bodies.bindings["objects"].get(object_id)
        if runtime_id is None or binding is None:
            return "failed", "manipulation_object_unbound"
        pose = bodies(binding["body_path"])
        live = (float(pose["position_m"][0]), float(pose["position_m"][1]))
        yaw = float(record["yaw_rad"])
        standoff = float(record["standoff_m"])
        stand = (live[0] - math.cos(yaw) * standoff, live[1] - math.sin(yaw) * standoff)
        robot = self.session.base_env.scene.articulations[runtime_id]
        state = robot.data.root_state_w.clone()
        state[0, 0] = stand[0]
        state[0, 1] = stand[1]
        state[0, 3] = math.cos(yaw / 2.0)
        state[0, 4] = 0.0
        state[0, 5] = 0.0
        state[0, 6] = math.sin(yaw / 2.0)
        state[0, 7:] = 0.0
        ids = torch.tensor([0], device=robot.device)
        robot.write_root_pose_to_sim(state[:, :7], env_ids=ids)
        robot.write_root_velocity_to_sim(state[:, 7:13], env_ids=ids)
        self._last_execution_diagnostics["runtime_ids"] = [runtime_id]
        self._last_execution_diagnostics["deploy_stand_m"] = [stand[0], stand[1]]
        self._last_execution_diagnostics["deploy_yaw_rad"] = yaw
        self._last_execution_diagnostics["approach_gate_passed"] = True
        print(f"house_deploy {object_id} {runtime_id} "
              f"stand=({stand[0]:.2f},{stand[1]:.2f}) yaw={yaw:.2f} "
              f"object=({live[0]:.2f},{live[1]:.2f})", flush=True)
        self._settle_carriers([runtime_id], steps=25)
        return "succeeded", None

    def _align_carriers(self, runtime_ids: list[str], heading_xy: tuple[float, float],
                       *, tolerance: float = 0.12, steps: int = 800) -> None:
        """Turn both carriers onto one heading without leaving the rally.

        A point two metres along the aisle makes the gait walk away from the
        crate before the joint exists. Stay inside a 0.45 m bubble and yaw
        while the gait is above its startup speed.
        """
        import math
        import torch
        scale = math.hypot(heading_xy[0], heading_xy[1]) or 1.0
        hx, hy = heading_xy[0] / scale, heading_xy[1] / scale
        target_yaw = math.atan2(hy, hx)
        session = self.session
        anchors = {
            runtime_id: (_root_state(session, runtime_id)[0], _root_state(session, runtime_id)[1])
            for runtime_id in runtime_ids
        }
        for step in range(steps):
            states = {runtime_id: _root_state(session, runtime_id) for runtime_id in runtime_ids}
            errors = [
                abs(math.atan2(math.sin(target_yaw - _yaw(state)), math.cos(target_yaw - _yaw(state))))
                for state in states.values()
            ]
            if errors and max(errors) <= tolerance:
                sample = " ".join(
                    f"{runtime_id} xy=({state[0]:.2f},{state[1]:.2f})"
                    for runtime_id, state in states.items())
                print(f"house_align ok max_err={max(errors):.2f} {sample}", flush=True)
                return
            if step % 200 == 0:
                sample = " ".join(
                    f"{runtime_id} xy=({state[0]:.2f},{state[1]:.2f}) yaw={_yaw(state):.2f}"
                    for runtime_id, state in states.items())
                print(f"house_align target={target_yaw:.2f} {sample}", flush=True)
            actions = {agent: torch.zeros((1, 3), device=session.device)
                       for agent in session.possible_agents}
            for runtime_id, state in states.items():
                yaw = _yaw(state)
                error = math.atan2(math.sin(target_yaw - yaw), math.cos(target_yaw - yaw))
                yaw_rate = max(-0.6, min(0.6, 1.2 * error))
                anchor = anchors[runtime_id]
                offset = (state[0] - anchor[0], state[1] - anchor[1])
                forward = (math.cos(yaw), math.sin(yaw))
                if math.hypot(*offset) > 0.45:
                    toward = (-offset[0], -offset[1])
                    speed = 0.55 if forward[0] * toward[0] + forward[1] * toward[1] >= 0.0 else -0.55
                else:
                    speed = 0.55 if math.cos(error) >= 0.0 else -0.55
                values = [speed, 0.0, yaw_rate]
                command = torch.tensor(values, dtype=torch.float32, device=session.device)
                session.base_env.set_command(runtime_id, "base_velocity", command.unsqueeze(0))
                actions[runtime_id] = command.unsqueeze(0)
            session.env.step(actions)
        print(f"house_align timeout target={target_yaw:.2f}", flush=True)

    def _settle_carriers(self, runtime_ids: list[str], steps: int = 40) -> None:
        """Hold zero base velocity so the fixed joint is authored on a settled pose."""
        import torch
        session = self.session
        for _ in range(steps):
            actions = {agent: torch.zeros((1, 3), device=session.device)
                       for agent in session.possible_agents}
            for runtime_id in runtime_ids:
                command = torch.zeros((1, 3), device=session.device)
                session.base_env.set_command(runtime_id, "base_velocity", command)
                actions[runtime_id] = command
            session.env.step(actions)

    def _approach_object(self, object_id: str, public_ids: list[str],
                         bodies: Any, point_ids: Mapping[str, str] | None = None) -> tuple[str, str | None]:
        """Move each carrier next to the live object before the reach check."""
        import math
        binding = bodies.bindings["objects"].get(object_id)
        if binding is None:
            return "failed", "manipulation_object_unbound"
        pose = bodies(binding["body_path"])
        center = (float(pose["position_m"][0]), float(pose["position_m"][1]))
        sockets = _second_mission_sockets(self.public_task, object_id, pose, point_ids)
        diagnostics: dict[str, Any] = {
            "stage": "approach_object",
            "object_id": object_id,
            "object_center_m": [center[0], center[1]],
            "object_center_sample": "approach_start_physx_xy",
            "approach_gate_m": _APPROACH_GATE_M,
            "allow_reverse": False,
            "carriers": [],
            "failure_agent_ids": [],
            "failure_stage": None,
            "reach_check_entered": False,
        }
        # Stop beside the object, on the robot's own side. A goal inside the
        # prop makes the follower reverse away, which is not a collaboration result.
        targets = {}
        carrier_rows: dict[str, dict[str, Any]] = {}
        pool = {row["id"]: row for row in self.public_task.get("candidate_robot_pool", [])}
        if sockets is not None and (not sockets or any(agent not in sockets for agent in public_ids)):
            return "failed", "manipulation_support_point_unbound"
        half_xy = None if sockets is None else _object_world_half_xy(
            self.public_task, object_id, list(pose.get("rotation_wxyz") or [1.0, 0.0, 0.0, 0.0]))
        if sockets is not None and half_xy is None:
            return "failed", "manipulation_object_size_unbound"
        pending_starts: dict[str, tuple[float, float]] = {}
        pending_sockets: dict[str, list[float]] = {}
        for public_id in public_ids:
            runtime_id = self._runtime_id(public_id)
            if runtime_id is None:
                return "failed", "navigation_runtime_id_missing"
            state = _root_state(self.session, runtime_id)
            socket = None if sockets is None else sockets[public_id]
            if socket is None:
                dx, dy = center[0] - state[0], center[1] - state[1]
                distance = math.hypot(dx, dy) or 1.0
                hold = min(0.75, max(0.0, distance - 0.75))
                targets[runtime_id] = (center[0] - dx / distance * hold,
                                       center[1] - dy / distance * hold)
            else:
                pending_starts[runtime_id] = (state[0], state[1])
                pending_sockets[runtime_id] = socket
            carrier_rows[runtime_id] = {
                "agent_id": public_id,
                "runtime_id": runtime_id,
                "start_root_pose_m": list(state[:3]),
                "reach_m": pool.get(public_id, {}).get("physical_profile", {}).get("reach_m"),
                "reach_check_entered": False,
                **({} if socket is not None else {
                    "approach_target_m": [targets[runtime_id][0], targets[runtime_id][1]],
                }),
                **({} if socket is None else {"support_point_m": list(socket)}),
            }
        approach_paths = None
        if pending_starts:
            assert half_xy is not None
            approach_paths = _support_approach_paths(
                pending_starts, pending_sockets, list(pose["position_m"]), half_xy,
                stand_m=_RALLY_STANDOFF_M)
            for runtime_id, path in approach_paths.items():
                targets[runtime_id] = path[-1]
                carrier_rows[runtime_id]["approach_target_m"] = [path[-1][0], path[-1][1]]
        diagnostics["carriers"] = [carrier_rows[runtime_id] for runtime_id in targets]
        self._last_execution_diagnostics["runtime_ids"] = list(targets)

        def arrived(states: Mapping[str, list[float]]) -> bool:
            # GOAL-01 hands off at 1.25 m from the object center. A second
            # mission stops at its rally, outside the support face.
            if sockets:
                for runtime_id, state in states.items():
                    rally = carrier_rows[runtime_id]["approach_target_m"]
                    if math.hypot(state[0] - rally[0], state[1] - rally[1]) > _RALLY_TOLERANCE_M:
                        return False
                return True
            return all(math.hypot(state[0] - center[0], state[1] - center[1]) <= _APPROACH_GATE_M
                       for state in states.values())
        # Preserve the object-approach policy: disable the stall sign flip.
        # The rally route is fixed when the approach starts.
        followed = self._follow_ground(
            list(targets), targets, arrived=arrived, label=object_id, allow_reverse=False,
            **({} if not sockets else {
                "final_stop_m": 0.35,
                "preset_paths": approach_paths,
            }))
        navigation = self._last_navigation_diagnostics
        # The follower uses the initial center. A dynamic prop can move during
        # approach; report its current center separately without changing that
        # existing arrival predicate or substituting it for a reach check.
        final_object_pose = bodies(binding["body_path"])
        final_center = list(map(float, final_object_pose["position_m"][:2]))
        diagnostics["final_object_center_m"] = final_center
        diagnostics["object_center_displacement_xy_m"] = math.dist(center, final_center)
        failed_agents: list[str] = []
        for row in diagnostics["carriers"]:
            runtime_id = row["runtime_id"]
            final = (navigation.get("agents", {}).get(runtime_id, {}).get("final_root_pose_m")
                     or row["start_root_pose_m"])
            row["final_root_pose_m"] = list(final)
            row["distance_to_approach_target_m"] = math.dist(
                final[:2], row["approach_target_m"])
            row["distance_to_gate_center_m"] = math.dist(final[:2], center)
            row["distance_to_object_center_m"] = math.dist(final[:2], final_center)
            if sockets is not None:
                row["within_approach_gate"] = (
                    row["distance_to_approach_target_m"] <= _RALLY_TOLERANCE_M)
            else:
                row["within_approach_gate"] = row["distance_to_gate_center_m"] <= _APPROACH_GATE_M
            row["within_live_center_radius"] = row["distance_to_object_center_m"] <= _APPROACH_GATE_M
            row["reverse_applied"] = bool(navigation.get("agents", {}).get(runtime_id, {}).get(
                "reverse_applied", False))
            row["stall_sign_flip_applied"] = bool(navigation.get("agents", {}).get(runtime_id, {}).get(
                "stall_sign_flip_applied", False))
            if not row["within_approach_gate"]:
                failed_agents.append(row["agent_id"])
        diagnostics["failure_agent_ids"] = failed_agents
        diagnostics["all_carriers_within_gate_at_final_sample"] = not failed_agents
        diagnostics["approach_gate_passed"] = followed[0] == "succeeded"
        diagnostics["navigation"] = deepcopy(navigation)
        if followed[0] != "succeeded":
            diagnostics["failure_stage"] = "approach_object"
        self._last_execution_diagnostics.update(diagnostics)
        if followed[0] != "succeeded":
            reason = followed[1] or ""
            if reason.startswith("navigation_target_not_reached"):
                distances = [row["distance_to_approach_target_m"]
                             for row in diagnostics["carriers"]]
                distance = max(distances, default=0.0)
                return "failed", f"approach_object_not_reached:{object_id}:{distance:.2f}m"
        return followed

    def _single_hold_heading(self, task: Mapping[str, Any],
                             origin_xy: Any) -> tuple[float, float] | None:
        """Direction from the object to the holder's next ground region."""
        plan = self.plan or {}
        task_id = task.get("task_id")
        for later in plan.get("tasks") or []:
            if task_id not in (later.get("prerequisites") or []):
                continue
            if later.get("skill") != "ground_navigation":
                continue
            region_id = (later.get("arguments") or {}).get("region_id")
            if not isinstance(region_id, str):
                continue
            center, _bounds = _region_geometry(self.deployment, region_id)
            if center is None:
                continue
            return (center[0] - float(origin_xy[0]), center[1] - float(origin_xy[1]))
        return None

    def _held_object_id(self, agent_ids: list[str]) -> str | None:
        if self.public_task.get("task_id") not in _SECOND_MISSION_TASK_IDS:
            return None
        wanted = set(agent_ids)
        for object_id, state in self.objects.items():
            if any(row.get("agent_id") in wanted for row in state.get("carrier_bindings") or []):
                return object_id
        return None

    def _object_xy(self, public_id: str) -> tuple[float, float] | None:
        runtime_id = None
        for row in (self.deployment or {}).get("object_instances") or []:
            if row.get("public_id") == public_id:
                runtime_id = row.get("runtime_id")
        bodies = getattr(self.session.base_env.scene, "rigid_objects", {})
        if runtime_id not in bodies:
            return None
        state = bodies[runtime_id].data.root_state_w[0].detach().cpu().tolist()
        return float(state[0]), float(state[1])

    def _measured_hold_region(self, object_id: str, agent_ids: list[str],
                              preferred: str) -> str | None:
        """Region of the live object pose, otherwise the live carrier pose.

        The ledger region stays on the carrier's destination unless a measurement
        names one region. That region is written onto the held object and the
        moved carriers together, so their regions still match.
        """
        if self.session is None or self.deployment is None:
            return None
        xy = self._object_xy(object_id)
        if xy is None:
            for agent_id in agent_ids:
                runtime_id = self._runtime_id(agent_id)
                if runtime_id is None:
                    continue
                try:
                    pose = _root_state(self.session, runtime_id)
                except (AttributeError, KeyError, RuntimeError):
                    continue
                xy = (float(pose[0]), float(pose[1]))
                break
        if xy is None:
            return None
        matches: list[str] = []
        for row in self.deployment.get("region_bindings") or []:
            region_id = row.get("id")
            if not isinstance(region_id, str):
                continue
            center, bounds = _region_geometry(self.deployment, region_id)
            if center is None:
                continue
            if _inside_region(xy, center, bounds, "ground_navigation"):
                matches.append(region_id)
        if preferred in matches:
            return preferred
        if len(matches) == 1:
            return matches[0]
        return None

    def _drive_held_object(self, runtime_ids: list[str], object_id: str,
                           target: tuple[float, float], bounds: list[float] | None,
                           label: str) -> tuple[str, str | None]:
        """Walk a single holder until the carried object, not the base, is in the region."""
        import math
        import torch
        session = self.session
        stalled = 0
        previous = None
        print(f"house_path {label} held_object={object_id} target=({target[0]:.2f},{target[1]:.2f})",
              flush=True)
        for step in range(7000):
            cargo = self._object_xy(object_id)
            if cargo is not None and _inside_region(cargo, target, bounds, "ground_navigation"):
                return "succeeded", None
            if cargo is not None and previous is not None and math.dist(cargo, previous) < 0.002:
                stalled += 1
            else:
                stalled = 0
            if cargo is not None:
                previous = cargo
            if stalled >= 250:
                return "failed", f"navigation_target_not_reached:{label}:stalled"
            states = {runtime_id: _root_state(session, runtime_id) for runtime_id in runtime_ids}
            anchor = states[runtime_ids[0]]
            if cargo is None:
                aim = target
            else:
                aim = (target[0] + anchor[0] - cargo[0], target[1] + anchor[1] - cargo[1])
            actions = {agent: torch.zeros((1, 3), device=session.device)
                       for agent in session.possible_agents}
            for runtime_id, state in states.items():
                yaw = _yaw(state)
                travel = math.atan2(aim[1] - state[1], aim[0] - state[0])
                error = math.atan2(math.sin(travel - yaw), math.cos(travel - yaw))
                # City velocity_to_target. Pre-align puts the holder in the
                # forward branch; this law still refuses reverse and holds
                # yaw-only above 0.9 rad. Not the M20 _TRACKED_GAIT profile.
                values = _tracked_gait_command(state, aim, 0.15, _CITY_VELOCITY_TO_TARGET)
                if step % 200 == 0:
                    shown = cargo or (float("nan"), float("nan"))
                    print(f"house_pose {label} {runtime_id} "
                          f"xy=({state[0]:.2f},{state[1]:.2f}) yaw={yaw:.2f} "
                          f"cargo=({shown[0]:.2f},{shown[1]:.2f}) "
                          f"err={error:.2f} cmd={values}",
                          flush=True)
                command = torch.tensor(values, dtype=torch.float32, device=session.device)
                session.base_env.set_command(runtime_id, "base_velocity", command.unsqueeze(0))
                actions[runtime_id] = command.unsqueeze(0)
            session.env.step(actions)
        return "failed", f"navigation_target_not_reached:{label}"

    def _drive_held_formation(self, task: Mapping[str, Any],
                             destination: str) -> tuple[str, str | None]:
        """Move a fixed pair by one shared displacement. The crate stays between them."""
        target, bounds = _region_geometry(self.deployment, destination)
        if target is None:
            return "failed", f"navigation_region_unbound:{destination}"
        runtime_ids = [self._runtime_id(agent_id) for agent_id in task["assigned_agents"]]
        if any(item is None for item in runtime_ids):
            return "failed", "navigation_runtime_id_missing"
        import math
        import torch
        session = self.session
        object_id = task.get("arguments", {}).get("object_id")
        object_runtime = None
        for row in (self.deployment or {}).get("object_instances") or []:
            if row.get("public_id") == object_id:
                object_runtime = row.get("runtime_id")
        stalled = 0
        previous = None
        print(f"house_path {task['task_id']} held_pair target=({target[0]:.2f},{target[1]:.2f})",
              flush=True)
        for step in range(7000):
            states = {runtime_id: _root_state(session, runtime_id) for runtime_id in runtime_ids}
            if object_runtime in getattr(session.base_env.scene, "rigid_objects", {}):
                cargo = session.base_env.scene.rigid_objects[object_runtime].data.root_state_w[0]
                cargo = cargo.detach().cpu().tolist()
                carried = (float(cargo[0]), float(cargo[1]))
            else:
                carried = (sum(state[0] for state in states.values()) / len(states),
                           sum(state[1] for state in states.values()) / len(states))
            if _inside_region(carried, target, bounds, "ground_navigation"):
                return "succeeded", None
            if previous is not None and math.dist(carried, previous) < 0.002:
                stalled += 1
            else:
                stalled = 0
            previous = carried
            if stalled >= 250:
                return "failed", f"navigation_target_not_reached:{task['task_id']}:stalled"
            direction = (target[0] - carried[0], target[1] - carried[1])
            actions = {agent: torch.zeros((1, 3), device=session.device)
                       for agent in session.possible_agents}
            for runtime_id, state in states.items():
                yaw = _yaw(state)
                alignment = math.cos(yaw) * direction[0] + math.sin(yaw) * direction[1]
                # Heading was fixed before the joint. Extra yaw does not turn the pair.
                values = [0.55 if alignment >= 0.0 else -0.55, 0.0, 0.0]
                command = torch.tensor(values, dtype=torch.float32, device=session.device)
                session.base_env.set_command(runtime_id, "base_velocity", command.unsqueeze(0))
                actions[runtime_id] = command.unsqueeze(0)
                if step % 200 == 0:
                    print(f"house_pose {task['task_id']} {runtime_id} "
                          f"xy=({state[0]:.2f},{state[1]:.2f}) yaw={yaw:.2f} "
                          f"crate=({carried[0]:.2f},{carried[1]:.2f}) cmd={values}",
                          flush=True)
            session.env.step(actions)
        return "failed", f"navigation_target_not_reached:{task['task_id']}"

    def _drive_aerial(self, runtime_ids: list[str], target: tuple[float, float],
                      label: str) -> tuple[str, str | None]:
        import math
        import torch
        session = self.session
        last_distance = None
        for _ in range(4000):
            states = {runtime_id: _root_state(session, runtime_id) for runtime_id in runtime_ids}
            last_distance = max(math.hypot(state[0] - target[0], state[1] - target[1])
                                for state in states.values())
            if last_distance <= 2.0:
                return "succeeded", None
            actions = {agent: torch.zeros((1, 3), device=session.device)
                       for agent in session.possible_agents}
            for runtime_id, state in states.items():
                goal = torch.tensor([[target[0], target[1], state[2]]],
                                    device=session.device, dtype=torch.float32)
                session.base_env.set_command(runtime_id, "goal_position", goal)
                actions[runtime_id] = goal
            session.env.step(actions)
        return "failed", f"navigation_target_not_reached:{label}:{last_distance:.2f}m"

    def _follow_ground(self, runtime_ids: list[str],
                       goals: Mapping[str, tuple[float, float]], *,
                       arrived: Any, label: str, allow_reverse: bool = True,
                       final_stop_m: float = 0.35, refresh_goals: Any = None,
                       preset_paths: Mapping[str, list[tuple[float, float]]] | None = None,
                       final_gait: Mapping[str, float] | None = None,
                       formation: bool = False) -> tuple[str, str | None]:
        """Follow an occupancy-map path, then stop on ``arrived``."""
        import math
        import torch
        session = self.session
        scene_key = self.deployment["scene_key"]
        robot_types = {
            row["runtime_id"]: row.get("type")
            for row in (self.deployment or {}).get("robot_instances") or []
        }
        pepper_bodies = {
            runtime_id: _pepper_body_index(session, runtime_id)
            for runtime_id in runtime_ids
            if robot_types.get(runtime_id) == "pepper"
        }
        states = {
            runtime_id: _navigation_pose(session, runtime_id, pepper_bodies.get(runtime_id))
            for runtime_id in runtime_ids
        }
        for runtime_id in pepper_bodies:
            if runtime_id not in self._pepper_drive_frame_yaw:
                self._pepper_drive_frame_yaw[runtime_id] = _yaw(states[runtime_id])
                print(f"house_pepper_frame {runtime_id} "
                      f"yaw={self._pepper_drive_frame_yaw[runtime_id]:.4f}", flush=True)
        paths: dict[str, list[tuple[float, float]]] = {}
        indexes = {runtime_id: 0 for runtime_id in runtime_ids}
        yaw_stall = {runtime_id: 0 for runtime_id in runtime_ids}
        # Existing corridor stall recovery. A stationary forward command does
        # not by itself identify a controller fault versus blocked geometry.
        vx_sign = {runtime_id: 1.0 for runtime_id in runtime_ids}
        stall_anchor = {runtime_id: None for runtime_id in runtime_ids}
        stall_steps = {runtime_id: 0 for runtime_id in runtime_ids}
        self._last_navigation_diagnostics = {
            "label": label,
            "allow_reverse": bool(allow_reverse),
            "agents": {
                runtime_id: {
                    "start_root_pose_m": list(states[runtime_id][:3]),
                    "goal_m": [goals[runtime_id][0], goals[runtime_id][1]],
                    "final_root_pose_m": list(states[runtime_id][:3]),
                    "reverse_applied": False,
                    "stall_sign_flip_applied": False,
                    "negative_vx_command_steps": 0,
                    "stall_steps": 0,
                }
                for runtime_id in runtime_ids
            },
        }
        goal_robots = {
            row["runtime_id"] for row in (self.deployment or {}).get("robot_instances") or []
            if row.get("command_interface") == "goal_position"
        }
        for runtime_id, state in states.items():
            # The route bookkeeping follows the dispatched robot's machine type
            # on every path source. Rally presets skip the A* query but the
            # agent row still records the type and its route radius, so a
            # same-type partner produces the same record and a preset route
            # never reports another robot's type.
            kind = robot_types.get(runtime_id)
            radius = route_inflation_radius_m(kind)
            if preset_paths is not None:
                path = [tuple(point) for point in preset_paths[runtime_id]]
            else:
                try:
                    path = occupancy_waypoints(
                        scene_key, (state[0], state[1]), goals[runtime_id], radius)
                except (OSError, RuntimeError, ValueError) as exc:
                    self._last_navigation_diagnostics["failure"] = f"{type(exc).__name__}:{exc}"
                    return "failed", f"navigation_path_blocked:{label}"
            if not path:
                self._last_navigation_diagnostics["failure"] = "path_blocked"
                self._last_navigation_diagnostics["agents"][runtime_id]["robot_type"] = kind
                self._last_navigation_diagnostics["agents"][runtime_id]["route_radius_m"] = radius
                return "failed", f"navigation_path_blocked:{label}"
            paths[runtime_id] = path
            length = sum(math.dist(a, b) for a, b in zip(path, path[1:]))
            agent_row = self._last_navigation_diagnostics["agents"][runtime_id]
            agent_row["robot_type"] = kind
            agent_row["route_radius_m"] = radius
            agent_row["path_length_m"] = length
            agent_row["waypoint_count"] = len(path)
        first = paths[runtime_ids[0]]
        print(f"house_path {label} {runtime_ids[0]} waypoints={len(first)} "
              f"length={self._last_navigation_diagnostics['agents'][runtime_ids[0]].get('path_length_m', 0):.2f} "
              f"radius={self._last_navigation_diagnostics['agents'][runtime_ids[0]].get('route_radius_m')}",
              flush=True)
        last_distance = None
        goals = dict(goals)
        for _ in range(7000):
            states = {
                runtime_id: _navigation_pose(session, runtime_id, pepper_bodies.get(runtime_id))
                for runtime_id in runtime_ids
            }
            if refresh_goals is not None:
                fresh = refresh_goals(states)
                if fresh:
                    for runtime_id, goal in fresh.items():
                        goals[runtime_id] = goal
                        paths[runtime_id][-1] = goal
            for runtime_id, state in states.items():
                self._last_navigation_diagnostics["agents"][runtime_id]["final_root_pose_m"] = list(state[:3])
                self._last_navigation_diagnostics["agents"][runtime_id]["distance_to_goal_m"] = math.dist(
                    state[:2], goals[runtime_id])
                self._last_navigation_diagnostics["agents"][runtime_id]["final_yaw_rad"] = _yaw(state)
            last_distance = max(math.hypot(state[0] - goals[runtime_id][0],
                                            state[1] - goals[runtime_id][1])
                                for runtime_id, state in states.items())
            if arrived(states):
                self._last_navigation_diagnostics["status"] = "succeeded"
                return "succeeded", None
            actions = {agent: torch.zeros((1, 3), device=session.device)
                       for agent in session.possible_agents}
            shared_travel: tuple[float, float] | None = None
            for runtime_id, state in states.items():
                path = paths[runtime_id]
                index = indexes[runtime_id]
                # Support routes are a tight corridor around a dynamic crate.
                # The 0.45 m corridor capture skips a corner and the next chord
                # cuts through the box.
                capture = 0.25 if preset_paths is not None else 0.45
                while index < len(path) - 1 and math.hypot(
                        state[0] - path[index][0], state[1] - path[index][1]) <= capture:
                    index += 1
                indexes[runtime_id] = index
                aim = path[index]
                if runtime_id in goal_robots:
                    goal = torch.tensor(
                        [[aim[0], aim[1], float(state[2])]],
                        device=session.device, dtype=torch.float32)
                    session.base_env.set_command(runtime_id, "goal_position", goal)
                    actions[runtime_id] = goal
                    self._last_navigation_diagnostics["agents"][runtime_id][
                        "last_command_goal_position"] = [aim[0], aim[1], float(state[2])]
                    if _ % 1000 == 0:
                        print(f"house_pose {label} {runtime_id} "
                              f"xy=({state[0]:.2f},{state[1]:.2f}) yaw={_yaw(state):.2f} "
                              f"aim=({aim[0]:.2f},{aim[1]:.2f}) goal", flush=True)
                    continue
                # A support corner must be reached before the index advances.
                # Stopping at 0.4 m while the capture radius is 0.25 m freezes
                # the robot short of every corner.
                if index == len(path) - 1:
                    stop = final_stop_m
                elif preset_paths is not None:
                    stop = 0.15
                else:
                    stop = 0.4
                gait = _TRACKED_GAIT.get(robot_types.get(runtime_id))
                if final_gait is not None and index == len(path) - 1 and gait is not None:
                    gait = final_gait
                if gait is not None:
                    # M20 does not yaw in place. Second-mission routes, including
                    # the carry after the joint, keep a startup-speed walk.
                    kind = robot_types.get(runtime_id)
                    if formation:
                        if shared_travel is None:
                            travel_x = aim[0] - state[0]
                            travel_y = aim[1] - state[1]
                            if abs(travel_x) >= abs(travel_y):
                                shared_travel = (1.0 if travel_x >= 0.0 else -1.0, 0.0)
                            else:
                                shared_travel = (0.0, 1.0 if travel_y >= 0.0 else -1.0)
                        values = _formation_walk_command(
                            state, shared_travel,
                            math.hypot(state[0] - aim[0], state[1] - aim[1]) <= stop)
                    elif preset_paths is not None or (
                            kind == "m20"
                            and self.public_task.get("task_id") in _SECOND_MISSION_TASK_IDS):
                        values = _rally_walk_command(state, aim, stop)
                    else:
                        values = _tracked_gait_command(state, aim, stop, gait)
                    agent_diagnostics = self._last_navigation_diagnostics["agents"][runtime_id]
                    agent_diagnostics["last_command_base_velocity"] = list(values)
                    command = torch.tensor(values, dtype=torch.float32, device=session.device)
                    session.base_env.set_command(runtime_id, "base_velocity", command.unsqueeze(0))
                    actions[runtime_id][0] = command
                    if _ % (200 if preset_paths is not None else 1000) == 0:
                        print(f"house_pose {label} {runtime_id} "
                              f"xy=({state[0]:.2f},{state[1]:.2f}) yaw={_yaw(state):.2f} "
                              f"aim=({aim[0]:.2f},{aim[1]:.2f}) cmd={values}", flush=True)
                    continue
                if runtime_id in pepper_bodies:
                    values = _pepper_world_command(
                        state, aim, 0.45, stop,
                        frame_yaw=self._pepper_drive_frame_yaw[runtime_id])
                    agent_diagnostics = self._last_navigation_diagnostics["agents"][runtime_id]
                    agent_diagnostics["last_command_base_velocity"] = list(values)
                    command = torch.tensor(values, dtype=torch.float32, device=session.device)
                    session.base_env.set_command(runtime_id, "base_velocity", command.unsqueeze(0))
                    actions[runtime_id][0] = command
                    if _ % 1000 == 0:
                        print(f"house_pose {label} {runtime_id} "
                              f"xy=({state[0]:.2f},{state[1]:.2f}) yaw={_yaw(state):.2f} "
                              f"aim=({aim[0]:.2f},{aim[1]:.2f}) cmd={values}", flush=True)
                    continue
                plain = _seek_command(
                    state, aim, _GROUND_SPEED_MPS, stop, creep=False)
                # A pure yaw command that the gait does not track used to arm
                # creep for a single step, then clear the counter and go back
                # to yaw-only. Keep creeping until the heading is close enough
                # that the plain command already drives forward.
                if plain[0] == 0.0 and abs(plain[2]) > 0.0:
                    yaw_stall[runtime_id] += 1
                else:
                    yaw_stall[runtime_id] = 0
                values = _seek_command(
                    state, aim, _GROUND_SPEED_MPS, stop, creep=yaw_stall[runtime_id] >= 2000)
                if abs(values[0]) > 0.4:
                    anchor = stall_anchor[runtime_id]
                    if anchor is None or math.hypot(state[0] - anchor[0], state[1] - anchor[1]) > 0.08:
                        stall_anchor[runtime_id] = (state[0], state[1])
                        stall_steps[runtime_id] = 0
                    else:
                        stall_steps[runtime_id] += 1
                        if allow_reverse and stall_steps[runtime_id] >= 250:
                            # Existing corridor recovery may flip again after
                            # another stall. Object approach disables this flip.
                            vx_sign[runtime_id] *= -1.0
                            self._last_navigation_diagnostics["agents"][runtime_id]["stall_sign_flip_applied"] = True
                            stall_steps[runtime_id] = 0
                            stall_anchor[runtime_id] = (state[0], state[1])
                            print(f"house_vx_sign {label} {runtime_id} {vx_sign[runtime_id]}",
                                  flush=True)
                self._last_navigation_diagnostics["agents"][runtime_id]["stall_steps"] = stall_steps[runtime_id]
                values = [values[0] * vx_sign[runtime_id], values[1], values[2]]
                agent_diagnostics = self._last_navigation_diagnostics["agents"][runtime_id]
                agent_diagnostics["last_command_base_velocity"] = list(values)
                if values[0] < 0.0:
                    agent_diagnostics["negative_vx_command_steps"] += 1
                    agent_diagnostics["reverse_applied"] = True
                command = torch.tensor(values, dtype=torch.float32, device=session.device)
                session.base_env.set_command(runtime_id, "base_velocity", command.unsqueeze(0))
                actions[runtime_id][0] = command
                if _ % 1000 == 0:
                    print(f"house_pose {label} {runtime_id} "
                          f"xy=({state[0]:.2f},{state[1]:.2f}) yaw={_yaw(state):.2f} "
                          f"aim=({aim[0]:.2f},{aim[1]:.2f}) cmd={values}", flush=True)
            session.env.step(actions)
        self._last_navigation_diagnostics["status"] = "failed"
        self._last_navigation_diagnostics["failure"] = "target_not_reached"
        # The loop's last state precedes its final env.step. Record fresh final
        # poses without changing the legacy timeout decision/reason.
        for runtime_id in runtime_ids:
            state = _navigation_pose(session, runtime_id, pepper_bodies.get(runtime_id))
            agent_diagnostics = self._last_navigation_diagnostics["agents"][runtime_id]
            agent_diagnostics["final_root_pose_m"] = list(state[:3])
            agent_diagnostics["final_yaw_rad"] = _yaw(state)
            agent_diagnostics["distance_to_goal_m"] = math.dist(state[:2], goals[runtime_id])
        return "failed", f"navigation_target_not_reached:{label}:{last_distance:.2f}m"

    def _release_hold(self, stage: Any, object_id: str, receipt: Mapping[str, Any],
                      task: Mapping[str, Any], destination: str | None) -> tuple[str, str | None]:
        from demo.benchmark.goal_native_attachment import detach_attachment
        moved: tuple[str, str | None] = ("succeeded", None)
        if isinstance(destination, str):
            carriers = list(task.get("assigned_agents") or [])
            if (len(carriers) > 1
                    and self.public_task.get("task_id") in _SECOND_MISSION_TASK_IDS):
                moved = self._drive_held_formation(task, destination)
            else:
                moved = self._drive_to_region({
                    **task, "skill": "ground_navigation",
                    "arguments": {"region_id": destination},
                })
        if moved[0] != "succeeded":
            return moved
        try:
            detach_attachment(stage, receipt)
        except Exception as exc:
            return "failed", f"manipulation_detach_rejected:{type(exc).__name__}"
        self.holds.pop(object_id, None)
        return moved

    def _runtime_id(self, public_id: str) -> str | None:
        if self.deployment is None:
            return None
        for row in self.deployment["robot_instances"]:
            if row["public_id"] == public_id:
                return row["runtime_id"]
        return None


def run_house_episode_offline(output_root: str | Path, *,
                              roster_profile: str = "bound_native_v2",
                              scene_key: str = "modern_house_nvd") -> dict[str, Any]:
    """Run an indoor witness through GoalSession without Isaac."""
    package = build_goal_task_package(
        scene_key, indoor_roster_profile=roster_profile)
    plan = build_house_episode_plan(package)
    public = package["public_task"]
    root = Path(output_root)
    backend = HouseNativeGoalBackend(public, plan=plan)
    runner = FormalGoalEpisodeRunner(
        public, package["participant_api"], HouseWitnessParticipant(plan), backend,
        output_root=root / "goal_episode")
    result = runner.run(initial_state=house_runtime_state(public, plan))
    summary = {
        "episode_id": result["episode_id"],
        "error": result["error"],
        "turns": result["turns"],
        "skill_receipt_count": result["skill_receipt_count"],
        "task_id": public["task_id"],
        "scene_key": scene_key,
        "roster_profile": roster_profile,
        "pending_skills": [row["skill"] for row in backend.evidence if row["status"] != "succeeded"],
        "skill_results": backend.evidence,
        "scoreable": False,
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "house_episode_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def build_house_deployment(roster_profile: str = "bound_native_v2",
                           scene_key: str = "modern_house_nvd"):
    """Compile an indoor deployment, allowing the opt-in seven-robot roster."""
    from .goal_deployment import compile_goal_deployment
    from .goal_indoor_deployment import build_indoor_deployment_overlay
    if scene_key not in INDOOR_EPISODE_SCENES:
        raise ValueError(f"unsupported indoor episode scene: {scene_key}")
    package = build_goal_task_package(
        scene_key, indoor_roster_profile=roster_profile)
    extended = roster_profile != "legacy_v1"
    overlay = build_indoor_deployment_overlay(
        package["public_task"], verify_sources=False,
        allow_extended_roster=extended)
    return package, compile_goal_deployment(package["public_task"], overlay)


def house_selection(deployment: Mapping[str, Any]) -> dict[str, Any]:
    from EAI.hmrs_env.env_diy.catalog import default_controller_cfg
    from EAI.hmrs_env.env_diy.flow import interactive_selection_from_dict
    robots = []
    for row in deployment["robot_instances"]:
        directive = row["spawn_binding"]["deployment_directive"]
        robots.append({
            "type": row["type"],
            "controller": {"mode": "default", "cfg": row.get("controller_cfg") or default_controller_cfg(row["type"])},
            "spawn_pose": {"position": list(directive["position_m"]),
                           "rotation": list(directive["rotation_wxyz"])},
            "attachments": [],
        })
    selection = {"version": 1, "scene_key": deployment["scene_key"],
                 "task_name": f"goal_episode_{deployment['scene_key']}", "robots": robots}
    interactive_selection_from_dict(selection)
    return selection


def prepare_house_episode(output_root: str | Path, *, device: str = "cuda:0",
                          roster_profile: str = "bound_native_v2",
                          scene_key: str = "modern_house_nvd") -> dict[str, Any]:
    """Write the launch inputs. Isaac starts only in the worker process."""
    package, deployment = build_house_deployment(roster_profile, scene_key=scene_key)
    selection = house_selection(deployment)
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=False)
    request = {"schema": "eai.benchmark.goal_house_episode_request", "version": 1,
               "device": device, "roster_profile": roster_profile,
               "task_id": package["public_task"]["task_id"]}
    for name, value in (("request.json", request), ("deployment.json", deployment),
                        ("selection.json", selection), ("package.json", package)):
        (root / name).write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")
    return request


def _run_house_worker(root: Path) -> dict[str, Any]:
    """Open Kit in this process. Callers must not have imported pxr already."""
    import torch
    if any(name == "pxr" or name.startswith("pxr.") for name in sys.modules):
        raise RuntimeError("OpenUSD must be loaded by the simulator application, not worker preflight")
    from simulator import SimulatorLaunchConfig, open_simulator_session
    request = json.loads((root / "request.json").read_text(encoding="utf-8"))
    deployment = json.loads((root / "deployment.json").read_text(encoding="utf-8"))
    selection = json.loads((root / "selection.json").read_text(encoding="utf-8"))
    package = json.loads((root / "package.json").read_text(encoding="utf-8"))
    public = package["public_task"]
    plan = build_house_episode_plan(package)

    def configure(env_cfg):
        from demo.benchmark.goal_native_objects import configure_goal_objects
        env_cfg.sim.render_interval = max(1, round(0.1 / env_cfg.sim.dt))
        env_cfg.sim.enable_scene_query_support = True
        if str(request["device"]).startswith("cuda"):
            env_cfg.sim.use_fabric = True
        env_cfg.episode_length_s = 3600.0
        configure_goal_objects(env_cfg, deployment)

    config = SimulatorLaunchConfig(
        env=selection["task_name"], resolved_env_name="EAI-Interactive-v0",
        selection_data=selection, num_envs=1, device=request["device"], seed=7,
        headless=True, enable_ros_bridge_extension=False, disable_orsus_ros_env=True,
        env_cfg_hook=configure)
    with open_simulator_session(config) as session:
        agents = list(session.possible_agents)
        for _ in range(10):
            actions = {agent: torch.zeros((1, 3), device=session.device) for agent in agents}
            session.env.step(actions)
        backend = HouseNativeGoalBackend(
            public, session=session, deployment=deployment, plan=plan)
        runner = FormalGoalEpisodeRunner(
            public, package["participant_api"], HouseWitnessParticipant(plan), backend,
            output_root=root / "goal_episode")
        result = runner.run(initial_state=house_runtime_state(public, plan))
        from demo.benchmark.gpu_evidence_contract import admit_live_session
        geometry_admitted, geometry_readback = admit_live_session(session)
        summary = {
            "episode_id": result["episode_id"],
            "error": result["error"],
            "turns": result["turns"],
            "skill_receipt_count": result["skill_receipt_count"],
            "task_id": public["task_id"],
            "roster_profile": request["roster_profile"],
            "device": request["device"],
            "formal_geometry_admitted": geometry_admitted,
            "geometry_readback": geometry_readback,
            "clearance_status": "unknown",
            "possible_agents": agents,
            "pending_skills": [row["skill"] for row in backend.evidence
                               if row["status"] != "succeeded"],
            "skill_results": backend.evidence,
            "scoreable": False,
        }
        (root / "house_episode_summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def run_house_episode(output_root: str | Path, *, device: str = "cuda:0",
                      roster_profile: str = "bound_native_v2",
                      scene_key: str = "modern_house_nvd") -> dict[str, Any]:
    """Prepare inputs and run the indoor session in a clean worker process."""
    import subprocess
    root = Path(output_root)
    prepare_house_episode(root, device=device, roster_profile=roster_profile,
                          scene_key=scene_key)
    command = [sys.executable, "-m", "demo.benchmark.goal_house_episode",
               "--worker", "--output-root", str(root)]
    completed = subprocess.run(command, cwd=Path(__file__).resolve().parents[2], check=False)
    summary_path = root / "house_episode_summary.json"
    if completed.returncode != 0 or not summary_path.is_file():
        raise RuntimeError(f"house episode worker failed with code {completed.returncode}")
    return json.loads(summary_path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--scene", choices=INDOOR_EPISODE_SCENES, default="modern_house_nvd")
    parser.add_argument("--roster", choices=("legacy_v1", "bound_native_v2"), default="bound_native_v2")
    parser.add_argument("--device", choices=("cpu", "cuda:0"), default="cuda:0",
                        help="formal evidence uses cuda:0; cpu is exploratory only")
    parser.add_argument("--offline", action="store_true",
                        help="run the GoalSession contract without Isaac")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        _run_house_worker(args.output_root)
        return 0
    if args.offline:
        summary = run_house_episode_offline(
            args.output_root, roster_profile=args.roster, scene_key=args.scene)
    else:
        summary = run_house_episode(
            args.output_root, device=args.device, roster_profile=args.roster,
            scene_key=args.scene)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    stopped_on_unbound_skill = "prerequisite" in (summary["error"] or "")
    ran = summary["skill_receipt_count"] >= 1 and (
        summary["error"] is None or stopped_on_unbound_skill)
    return 0 if ran else 1


if __name__ == "__main__":
    raise SystemExit(main())
