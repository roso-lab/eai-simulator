"""Author geometry that lets a second mission use the formal House skill backend.

These centres are proposals taken from the scene's existing anchors. They let
``HouseNativeGoalBackend`` resolve a region when a live session is bound.
They are not a measured USD binding and they do not open the score gate.
"""

from __future__ import annotations

import math
from typing import Any, Mapping


# Factory fire sites sit on the same anchors as the frozen inspection overlay.
_FACTORY_FIRE_XYZ = {
    "fire_overhead": (-7.95, -6.35, 2.5),
    "fire_staging": (-1.95, 3.4, 0.0),
    "rescue_button": (4.2, 3.4, 0.0),
    "rescue_channel": (4.9, 3.4, 0.0),
    "extinguisher_cache": (-1.95, 2.0, 0.0),
    # The inspection cell (5.55, 3.4) stays on the frozen overlay. Carter's
    # live run stalled at x≈2.76 on y=3.4 and the stall flip drove it west.
    # This centre keeps the padded box on the stretch Carter already crossed.
    "fire_site": (3.20, 3.40, 0.0),
}

# City dismissal uses the published core spawns, then short author offsets.
_CITY_EXIT_XYZ = {
    "exit_overhead": (-55.0, 20.0, 8.0),
    "exit_staging": (-40.0, 55.0, 0.0),
    "dismissal_gathering": (-42.0, 50.0, 0.0),
    # The authored point (-44, 46) is off the sidewalk. Go2's map snaps it to a
    # dead-end cell 3 m short of the box, then stops. This centre is the
    # reachable sidewalk cell west of the gathering.
    "dismissal_assembly": (-49.75, 50.25, 0.0),
    # Carter's sidewalk snap from (-69, 25) lands at (-64, 25) and does not
    # connect to that cell. North of the snap, the live deck stops the robot
    # near y=29, so the apron stays on the sidewalk it actually reaches.
    # The corridor is the short stretch between the spawn and that apron.
    "station_corridor": (-63.75, 26.20, 0.0),
    "station_apron": (-63.75, 28.80, 0.0),
}

_HOUSE_DELIVERY_EXTRA_XYZ = {
    # North of the door, x=3.67 is the wide corridor the occupancy map keeps
    # clear at a 0.40 m body. The old (4.0, 11.5) centre left that corridor
    # and a laden M20 cannot yaw onto it after the joint.
    "indoor_holding": (3.67, 11.20, 0.0),
    "delivery_signoff": (4.6, 4.2, 0.0),
}
# Parcel anchor sits inside the entry hall box and on that same corridor.
_HOUSE_PARCEL_ANCHOR_XY = (3.67, 4.20)
_SUPPORT_STANDOFF_M = 0.70
_SUPPORT_BODY_RADIUS_M = 0.40
_MEASURED_PAYLOADS = {
    "MODERN_HOUSE-DELIVERY-01": ("doorstep_parcel_01", "delivery_signoff_marker"),
    "FACTORY-FIRE-01": ("factory_extinguisher_01", "rescue_channel_button"),
}
_KINEMATIC_PAYLOADS = frozenset({"delivery_signoff_marker", "rescue_channel_button"})

# Match RobotOption.default_z. A lower root puts the M20 wheels in the floor.
# Modern House's walkable slab top is z=0.25. The indoor spawn table already
# places M20 at 0.77 and Carter at 0.45; a z=0.52 root starts inside that slab.
_HOUSE_FLOOR_Z_M = 0.25
_STANDING_Z_M = {
    "carter": 0.2,
    "pepper": 0.0,
    "go2": 0.4,
    "b2": 0.58,
    "m20": 0.52,
    "scout": 0.24,
    "coco": 0.3,
    "g1": 0.74,
    "lite3": 0.35,
    "iris": 1.0,
    "cf2x": 1.0,
    "pegasus": 1.0,
}


def _city_surface_z(xy: tuple[float, float]) -> float:
    """Sidewalk or road deck height at one city point. People stand on this surface."""
    from shapely.geometry import Point, shape

    from demo.city_benchmark.maps import load_sources
    from EAI_assets.asset_resolver import usd_root

    layout, _, _ = load_sources(usd_root() / "scene" / "city")
    sidewalk = shape(layout["geometry"]["sidewalk_surface"])
    heights = layout["surface_heights"]
    key = "sidewalks" if sidewalk.covers(Point(float(xy[0]), float(xy[1]))) else "roads"
    return float(heights[key])


def _city_ground_z(kind: str, xy: tuple[float, float]) -> float:
    """Stand on the sidewalk or road deck. A z=0.4 root puts Go2 feet under the 0.30 m deck."""
    return _city_surface_z(xy) + _STANDING_Z_M.get(kind, 0.35)


def _city_sidewalk_xy(xy: tuple[float, float]) -> tuple[float, float]:
    """Move a road spawn onto the sidewalk. The gathering is not across a curb."""
    from shapely.geometry import Point, shape
    from shapely.ops import nearest_points

    from demo.city_benchmark.maps import load_sources
    from EAI_assets.asset_resolver import usd_root

    layout, _, _ = load_sources(usd_root() / "scene" / "city")
    sidewalk = shape(layout["geometry"]["sidewalk_surface"])
    point = Point(float(xy[0]), float(xy[1]))
    if sidewalk.covers(point):
        return float(xy[0]), float(xy[1])
    inner = sidewalk.buffer(-2.0)
    surface = inner if not inner.is_empty else sidewalk
    snapped = nearest_points(surface, point)[0]
    return float(snapped.x), float(snapped.y)


def _first_ground_goal(plan: Mapping[str, Any] | None, public_id: str,
                       centres: Mapping[str, tuple[float, float, float]],
                       ) -> tuple[float, float, float] | None:
    """Region centre of this robot's first ground-navigation skill."""
    for task in (plan or {}).get("tasks") or []:
        if task.get("skill") != "ground_navigation":
            continue
        if public_id not in (task.get("assigned_agents") or []):
            continue
        region_id = (task.get("arguments") or {}).get("region_id")
        if region_id in centres:
            return centres[region_id]
    return None


def _yaw_toward_xy(origin: list[float], target_xy: tuple[float, float]) -> list[float]:
    """Face a world point so the first gait command can drive forward."""
    half = 0.5 * math.atan2(target_xy[1] - origin[1], target_xy[0] - origin[0])
    return [math.cos(half), 0.0, 0.0, math.sin(half)]


def _standing_z(kind: str, authored: float | None) -> float:
    standing = _STANDING_Z_M.get(kind, 0.35)
    if authored is None or authored < standing:
        return standing
    return float(authored)


def _fix_aabb(point: tuple[float, float, float], half_m: float = 0.75) -> list[float]:
    return [point[0] - half_m, point[1] - half_m, point[0] + half_m, point[1] + half_m]


def _indoor_xyz(scene_key: str) -> dict[str, tuple[float, float, float]]:
    from demo.benchmark.goal_indoor_deployment import _REGION_PROPOSALS

    centres = {
        key: (float(value[0][0]), float(value[0][1]), float(value[0][2]))
        for key, value in _REGION_PROPOSALS[scene_key].items()
    }
    if scene_key == "factory":
        centres.update(_FACTORY_FIRE_XYZ)
    elif scene_key == "modern_house_nvd":
        centres.update(_HOUSE_DELIVERY_EXTRA_XYZ)
    return centres


def _restaurant_xyz(public_task: Mapping[str, Any]) -> dict[str, tuple[float, float, float]]:
    from demo.restaurant_benchmark.service_layout import (
        DINING_SERVICE_XY_M,
        KITCHEN_SERVICE_XY_M,
        TABLE_A_XY_M,
        TABLE_B_XY_M,
    )

    spawns = public_task["public_world"].get("robot_spawn_positions") or {}
    iris = spawns.get("restaurant_iris_1") or (14.0, 21.0, 1.6)
    return {
        "restaurant_overhead": (float(iris[0]), float(iris[1]), max(float(iris[2]), 1.6)),
        "restaurant_staging": (float(spawns["restaurant_carter_1"][0]),
                               float(spawns["restaurant_carter_1"][1]), 0.0),
        "kitchen_service": (float(KITCHEN_SERVICE_XY_M[0]), float(KITCHEN_SERVICE_XY_M[1]), 0.0),
        "dining_service": (float(DINING_SERVICE_XY_M[0]), float(DINING_SERVICE_XY_M[1]), 0.0),
        "dining_table_a": (float(TABLE_A_XY_M[0]), float(TABLE_A_XY_M[1]), 0.0),
        "dining_table_b": (float(TABLE_B_XY_M[0]), float(TABLE_B_XY_M[1]), 0.0),
    }


def _segment_free(occupancy: Any, start: tuple[float, float], goal: tuple[float, float],
                  radius: float, step: float = 0.10) -> bool:
    length = math.dist(start, goal)
    count = max(1, int(math.ceil(length / step)))
    for index in range(count + 1):
        scale = index / count
        x = start[0] + (goal[0] - start[0]) * scale
        y = start[1] + (goal[1] - start[1]) * scale
        if not occupancy.rectangle_free(x, y, radius, radius):
            return False
    return True


def _inside_published_box(xy: tuple[float, float], centre: tuple[float, float, float],
                          half_m: float = 0.75) -> bool:
    return abs(xy[0] - centre[0]) <= half_m and abs(xy[1] - centre[1]) <= half_m


def resolve_support_deploy(public_task: Mapping[str, Any]) -> dict[str, Any] | None:
    """Place a single holder on a free forward ray before the support joint.

    The holder is written onto this stand with its heading already locked
    toward the carry region. The object stays one standoff ahead, inside the
    pickup box. A laden M20 then only has to drive forward.
    """
    task_id = public_task.get("task_id")
    if task_id not in _MEASURED_PAYLOADS:
        return None
    centres = region_centres(public_task)
    if task_id == "MODERN_HOUSE-DELIVERY-01":
        anchor = _HOUSE_PARCEL_ANCHOR_XY
        pickup_id, destination_id, object_id = (
            "entry_hall", "indoor_holding", "doorstep_parcel_01")
    else:
        anchor = (centres["extinguisher_cache"][0], centres["extinguisher_cache"][1])
        pickup_id, destination_id, object_id = (
            "extinguisher_cache", "fire_site", "factory_extinguisher_01")
    destination = centres[destination_id]
    pickup = centres[pickup_id]
    dx = destination[0] - anchor[0]
    dy = destination[1] - anchor[1]
    length = math.hypot(dx, dy)
    if length < 1e-4:
        raise ValueError(f"support deploy has no travel: {task_id}")
    ux, uy = dx / length, dy / length
    from demo.benchmark.goal_house_episode import _scene_occupancy

    occupancy = _scene_occupancy(public_task["mission"]["scene_key"])
    radius = _SUPPORT_BODY_RADIUS_M
    goal = (destination[0], destination[1])
    for shift in (index * 0.05 for index in range(41)):
        stand = (anchor[0] + ux * (shift - _SUPPORT_STANDOFF_M),
                 anchor[1] + uy * (shift - _SUPPORT_STANDOFF_M))
        obj = (stand[0] + ux * _SUPPORT_STANDOFF_M, stand[1] + uy * _SUPPORT_STANDOFF_M)
        robot_goal = (goal[0] - ux * _SUPPORT_STANDOFF_M, goal[1] - uy * _SUPPORT_STANDOFF_M)
        if (not _inside_published_box(obj, pickup)
                or not occupancy.rectangle_free(stand[0], stand[1], radius, radius)
                or not occupancy.rectangle_free(obj[0], obj[1], radius, radius)
                or not _segment_free(occupancy, stand, robot_goal, radius)
                or not _segment_free(occupancy, obj, goal, radius)):
            continue
        return {
            "object_id": object_id,
            "object_xy": [obj[0], obj[1]],
            "stand_xy": [stand[0], stand[1]],
            "yaw_rad": math.atan2(uy, ux),
            "standoff_m": _SUPPORT_STANDOFF_M,
            "pickup_region_id": pickup_id,
            "destination_region_id": destination_id,
        }
    raise ValueError(f"support deploy has no free forward stand: {task_id}")


def payload_spawn_positions(public_task: Mapping[str, Any]) -> dict[str, tuple[float, float, float]]:
    """World positions for the measured cuboids. The carried object uses the deploy ray."""
    from demo.benchmark.goal_driven_fixtures import object_physical_properties

    task_id = public_task.get("task_id")
    names = _MEASURED_PAYLOADS.get(task_id)
    if not names:
        return {}
    deploy = resolve_support_deploy(public_task)
    if deploy is None:
        return {}
    centres = region_centres(public_task)
    scene_key = public_task["mission"]["scene_key"]
    floor = _HOUSE_FLOOR_Z_M if scene_key == "modern_house_nvd" else 0.0
    positions: dict[str, tuple[float, float, float]] = {}
    for public_id in names:
        props = object_physical_properties(public_id)
        half_z = float(props["size_m"][2]) * 0.5
        if public_id == deploy["object_id"]:
            xy = (float(deploy["object_xy"][0]), float(deploy["object_xy"][1]))
        else:
            region_id = props["initial_region_id"]
            centre = centres[region_id]
            xy = (centre[0], centre[1])
        positions[public_id] = (xy[0], xy[1], floor + half_z)
    return positions


def region_centres(public_task: Mapping[str, Any]) -> dict[str, tuple[float, float, float]]:
    """Return one proposed centre for every public region."""
    scene_key = public_task["mission"]["scene_key"]
    if scene_key == "city":
        centres = dict(_CITY_EXIT_XYZ)
    elif scene_key == "restaurant":
        centres = _restaurant_xyz(public_task)
    elif scene_key in {"factory", "warehouse_nvd", "modern_house_nvd"}:
        centres = _indoor_xyz(scene_key)
    else:
        raise ValueError(f"second mission has no formal geometry: {scene_key}")
    missing = [row["id"] for row in public_task["public_world"]["regions"] if row["id"] not in centres]
    if missing:
        raise ValueError(f"second mission regions have no formal centre: {missing}")
    return centres


def compile_second_mission_formal_deployment(public_task: Mapping[str, Any]) -> dict[str, Any] | None:
    """Compile the indoor formal deployment when this task can reuse it.

    Only warehouse inbound currently shares the frozen scene regions and the
    native crate/carton assets. The compiled hash is this task's own public
    hash. Other second missions stay on the lighter author deployment.
    """
    if public_task.get("task_id") != "WAREHOUSE-INBOUND-01":
        return None
    from demo.benchmark.goal_deployment import compile_goal_deployment
    from demo.benchmark.goal_indoor_deployment import build_indoor_deployment_overlay

    overlay = build_indoor_deployment_overlay(
        public_task, verify_sources=False, allow_extended_roster=True)
    return compile_goal_deployment(public_task, overlay)


def build_second_mission_deployment(public_task: Mapping[str, Any],
                                    plan: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Deployment view consumed by the formal House skill backend."""
    centres = region_centres(public_task)
    scene_key = public_task["mission"]["scene_key"]
    spawns = public_task["public_world"].get("robot_spawn_positions") or {}
    counters: dict[str, int] = {}
    robots = []
    ground_slot = 0
    for robot in public_task["candidate_robot_pool"]:
        kind = robot["type"]
        counters[kind] = counters.get(kind, 0) + 1
        runtime_id = f"{kind}_{counters[kind]}"
        raw = spawns.get(robot["id"])
        if raw is None:
            if robot["mobility"] == "aerial":
                aerial = next(row["id"] for row in public_task["public_world"]["regions"]
                              if "aerial_navigation" in row["access_modes"])
                point = centres[aerial]
                position = [point[0], point[1], point[2]]
            else:
                ground = next(row["id"] for row in public_task["public_world"]["regions"]
                              if "ground_navigation" in row["access_modes"])
                point = centres[ground]
                # Keep live roots apart. Stacked roots make the first physics step invalid.
                position = [point[0] + ground_slot * 1.5, point[1], _standing_z(kind, None)]
                ground_slot += 1
        else:
            position = [float(value) for value in raw]
            if len(position) == 2:
                position.append(0.0)
            if robot["mobility"] != "aerial":
                position[2] = _standing_z(kind, position[2])
        if scene_key == "modern_house_nvd" and robot["mobility"] != "aerial":
            position[2] = _HOUSE_FLOOR_Z_M + _standing_z(kind, None)
        if scene_key == "city" and robot["mobility"] != "aerial":
            snapped_x, snapped_y = _city_sidewalk_xy((position[0], position[1]))
            position[0], position[1] = snapped_x, snapped_y
            position[2] = _city_ground_z(kind, (position[0], position[1]))
        # Face the first navigation region. A heading toward a nearby robot
        # left the goal behind the body, and the reverse branch then walked
        # the gait into the curb.
        goal = _first_ground_goal(plan, robot["id"], centres)
        facing = _yaw_toward_xy(position, goal) if goal is not None else [1.0, 0.0, 0.0, 0.0]
        robots.append({
            "public_id": robot["id"],
            "runtime_id": runtime_id,
            "type": kind,
            "command_interface": "base_velocity",
            "controller_cfg": None,
            "spawn_binding": {
                "deployment_directive": {
                    "instance_id": runtime_id,
                    "position_m": position,
                    "rotation_wxyz": facing,
                },
            },
        })
    regions = []
    for row in public_task["public_world"]["regions"]:
        centre = centres[row["id"]]
        regions.append({
            "id": row["id"],
            "candidate_geometry": {
                "kind": "author_proposed_aabb",
                "center_m": [centre[0], centre[1], centre[2]],
                "aabb_xy_m": _fix_aabb(centre),
                "basis": "second_mission_formal_anchor",
            },
        })
    return {
        "schema": "eai.benchmark.second_mission_formal_deployment",
        "version": 1,
        "scene_key": scene_key,
        "task_id": public_task["task_id"],
        "robot_instances": robots,
        "region_bindings": regions,
        "physical_execution_validated": False,
        "scoreable": False,
        "blockers": [
            "author_region_centres_are_not_a_measured_usd_binding",
            "live_session_required_before_navigation_or_support_attach",
        ],
    }
