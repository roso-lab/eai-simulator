"""Map-based distance estimates for independent Factory navigation roots."""

import math

from EAI.scenario_spec.geometry import ground_surface_exclusions, provider_map, static_blocked_grid
from EAI.scenario_spec.oracle import InflatedGrid
from EAI.scenario_spec.schema import ROBOT_PROFILES
from demo.benchmark.runtime import planning_radius, route_waypoints


def estimate_route_costs(data, requirements, *, occupancy=None):
    if data["scene_key"] != "factory" or data.get("fault_events"):
        raise ValueError("route cost model requires Factory without fault interventions")
    nodes = {node["id"]: node for node in data["phase_graph"]["nodes"]}
    by_id = {item["id"]: item for item in requirements}
    targets = {target["id"]: target for target in data["composition"]["targets"]}
    if set(nodes) != set(by_id):
        raise ValueError("route cost requirements must cover exactly the phase graph")
    for node in nodes.values():
        if node.get("agents") or node["target_ref"] not in targets:
            raise ValueError("route cost model requires single-agent target-region tasks")
        if node["skill_key"] == "navigate":
            if node["prerequisites"]:
                raise ValueError("route cost model cannot infer assignment-dependent intermediate origins")
        elif node["skill_key"] == "wait":
            parent_id = by_id[node["id"]].get("same_team_as")
            parent = nodes.get(parent_id, {})
            if (parent.get("skill_key") != "navigate" or node["prerequisites"] != [parent_id]
                    or node["target_ref"] != parent["target_ref"]):
                raise ValueError("wait cost requires its arriving team at the same target")
        else:
            raise ValueError("route cost model supports independent navigation and attached waits")
    if occupancy is None:
        occupancy = provider_map(scene_key=data["scene_key"])
    blocked = static_blocked_grid(occupancy, data)
    grids, costs, unavailable, routes = {}, {}, {}, {}
    for identifier, node in nodes.items():
        costs[identifier], unavailable[identifier], routes[identifier] = {}, {}, {}
        for robot in data["robots"]:
            agent = robot["id"]
            if node["skill_key"] == "wait":
                costs[identifier][agent] = 0.0
                continue
            if ROBOT_PROFILES[robot["type"]]["mobility"] == "aerial":
                unavailable[identifier][agent] = "route:ground_map_does_not_model_aerial_space"
                continue
            radius = planning_radius(robot)
            if radius not in grids:
                grids[radius] = InflatedGrid(occupancy, radius, blocked)
            grid = grids[radius]
            start = tuple(robot["spawn_pose"]["position"][:2])
            target = targets[node["target_ref"]]
            goal = tuple(target["position"][:2])
            path = grid.path(start, goal)
            if path is None:
                unavailable[identifier][agent] = "route:no_passable_map_path_from_spawn_to_target"
                continue
            points = [start, *route_waypoints(grid, path, goal, preserve_goal=True)]
            length = sum(math.dist(first, second) for first, second in zip(points, points[1:]))
            costs[identifier][agent] = length
            routes[identifier][agent] = {
                "start_xy_m": list(start), "goal_xy_m": list(goal), "planning_radius_m": radius,
                "route_length_m": length, "waypoints_xy_m": points,
            }
    return {
        "schema": "eai.factory_route_costs", "version": 1, "model": "map_route_length",
        "unit": "m", "objective": "sum_of_independent_planned_route_lengths",
        "occupancy_sha256": occupancy.sha256, "estimated_costs": costs,
        "ground_surface_exclusions": ground_surface_exclusions(data),
        "unavailable_assignments": unavailable, "routes": routes,
        "limitations": [
            "Map disk paths are geometric estimates, not measured travel times or globally shortest paths",
            "Wait phases add zero travel distance; freshness and total completion still require execution",
            "No intermediate-origin, curvature, dynamic collision, energy, or manipulation model",
            "Blocked endpoints are rejected without snapping them to another position",
            "Eligibility-only reallocation certificates do not include these route estimates",
        ],
    }
