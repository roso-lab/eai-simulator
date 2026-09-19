"""Translate a participant-selected subset into the existing local city hook.

This is a configuration adapter, not runtime activation. The caller still owns
admission, controller readiness and the single open_simulator_session lifecycle.
"""
from __future__ import annotations

from copy import deepcopy
import math

from demo.city_benchmark.contracts import candidate_pool


def build_selected_selection(agent_ids: list[str], *, seed: int = 7, max_active_agents: int = 3,
                             spawn_positions: dict | None = None):
    if (not isinstance(agent_ids, list) or not agent_ids
            or any(not isinstance(a, str) for a in agent_ids)
            or len(agent_ids) != len(set(agent_ids))):
        raise ValueError("select a nonempty list of unique agent ids")
    if type(max_active_agents) is not int or not 1 <= max_active_agents <= 3:
        raise ValueError("invalid team budget")
    if len(agent_ids) > max_active_agents:
        raise ValueError("selected team exceeds public budget")
    pool = candidate_pool()
    if set(agent_ids) - {r["id"] for r in pool}:
        raise ValueError("selected agent is outside the public candidate pool")
    from demo.city.main import build_selection

    selection = deepcopy(build_selection(seed))
    robots_by_type = {robot["type"]: robot for robot in selection["robots"]}
    # Preserve builder type order and hence type-occurrence instance names.
    selection["robots"] = [robots_by_type[r["type"]] for r in pool if r["id"] in agent_ids]
    if spawn_positions is not None:
        if set(spawn_positions) != set(agent_ids):
            raise ValueError("spawn_positions must exactly cover the selected team")
        selected = [r for r in pool if r["id"] in agent_ids]
        for robot, candidate in zip(selection["robots"], selected):
            position = spawn_positions[candidate["id"]]
            if (not isinstance(position, (list, tuple)) or len(position) != 3
                    or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                           for v in position)):
                raise ValueError("spawn position must be three finite metre coordinates")
            robot["spawn_pose"]["position"] = list(position)
    selection["task_name"] = "city_benchmark"
    return selection


def navigation_handoff(grid, metadata: dict, semantics: dict):
    """Audit legacy starts and suggest map-clear starts, without deploying them."""
    import numpy as np
    from shapely.geometry import Point, shape

    selection = build_selected_selection([r["id"] for r in candidate_pool()])
    res = metadata["resolution_m"]
    x0, y0, _ = metadata["origin_xyz_m"]
    height, width = grid.shape
    free_y, free_x = np.where(grid == 254)
    world_x, world_y = x0 + (free_x + .5) * res, y0 + (height - free_y - .5) * res
    sidewalk = shape(next(r["geometry"] for r in semantics["bindings"] if r["id"] == "sidewalk_surface"))
    reviewed, selected_xy = [], []
    for row, robot in zip(candidate_pool(), selection["robots"]):
        start = robot["spawn_pose"]["position"]
        col, r = math.floor((start[0] - x0) / res), height - 1 - math.floor((start[1] - y0) / res)
        occupancy = int(grid[r, col]) if 0 <= col < width and 0 <= r < height else 205
        def overlapping(p):
            return [o["id"] for o in semantics["static_obstacles"]
                    if all(o["bounds_m"][0][k] - metadata["inflation_m"] <= p[k]
                           <= o["bounds_m"][1][k] + metadata["inflation_m"] for k in range(3))]
        proposal = None
        distance2 = (world_x - start[0]) ** 2 + (world_y - start[1]) ** 2
        for idx in np.argsort(distance2, kind="stable"):
            if distance2[idx] > 15 ** 2:
                break
            x, y = float(world_x[idx]), float(world_y[idx])
            if any(math.dist((x, y), other) < 3.0 for other in selected_xy):
                continue
            floor = semantics["surface_heights_m"].get("sidewalks", .3) if sidewalk.covers(Point(x, y)) else semantics["surface_heights_m"].get("roads", .2)
            z = start[2] if row["mobility"] == "aerial" else start[2] + floor - .22
            if row["mobility"] == "aerial" and overlapping([x, y, z]):
                continue
            proposal = [x, y, z]
            selected_xy.append((x, y))
            break
        reviewed.append({"agent_id": row["id"], "demo_spawn_xyz_m": start,
                         "static_ground_cell": occupancy,
                         "body_reference_overlaps": overlapping(start),
                         "candidate_spawn_xyz_m": proposal,
                         "candidate_status": "static_geometry_only" if proposal else "no_local_candidate",
                         "physical_spawn_validated": False})
    return {"schema": "eai.city.navigation_handoff", "version": 1,
            "ground_map": "release/maps/occupancy.yaml", "coordinate_frame": "city_world",
            "yaw_units": "radians", "quaternion_order": "wxyz",
            "start_reviews": reviewed, "minimum_candidate_spacing_m": 3.0,
            "aerial_static_bounds": "release/maps/semantic_map.json:static_obstacles",
            "dynamic_layers_required": ["traffic", "people", "hazard_exclusion", "other_robots"],
            "unknown_space_policy": "blocked", "navigation_ready": False,
            "limits": ["AABB overlaps are conservative, not verified collisions",
                       "candidate starts require support/contact and actor-layout checks",
                       "2D route connectivity and 3D aerial navigation still require runtime admission"]}
