"""Deterministic feasibility oracle: navigation reachability and team necessity.

This is the Feasibility oracle of the gate pipeline -- an existence
certificate, not the Performance oracle and not a probe. Navigation legs are
solved with A* on per-robot inflated occupancy grids, so a route exists only
when the agent's full disk fits along it (the "0.8 m doorway" dead layout is
rejected here, deterministically, without RL). Manipulation phases are checked
at the capability level; 3-D arm collision-free trajectories (CuRobo) attach
at the documented hook below once installed, and their absence is reported
rather than silently skipped.
"""

import heapq
import math

from .schema import ROBOT_PROFILES, SKILLS
from .validation import ScenarioError, robot_capabilities


class InflatedGrid:
    """Free grid whose passable cells keep a clearance of at least `radius`."""

    _NEIGHBORS = ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
                  (1, 1, math.sqrt(2)), (1, -1, math.sqrt(2)), (-1, 1, math.sqrt(2)), (-1, -1, math.sqrt(2)))

    def __init__(self, occupancy, radius, blocked=None):
        import numpy as np
        from scipy.ndimage import distance_transform_edt

        import numpy as np

        base = occupancy.free if blocked is None else blocked
        # Treat outside-the-map as occupied so a route disk can never leave
        # the mapped area (the raw EDT would happily run off the array).
        padded = np.pad(base, 1, constant_values=False)
        clearance = distance_transform_edt(padded) * occupancy.resolution
        self.free = clearance[1:-1, 1:-1] > radius
        self.width = occupancy.width
        self.height = occupancy.height
        self.resolution = occupancy.resolution
        self._local = occupancy.local
        self._occupancy = occupancy

    def cell(self, x, y):
        lx, ly = self._local(x, y)
        return math.floor(lx / self.resolution), math.floor(ly / self.resolution)

    def passable(self, ix, iy):
        return 0 <= ix < self.width and 0 <= iy < self.height and self.free[self.height - 1 - iy, ix]

    def nearest_passable(self, cell_xy, max_ring=40):
        """Nearest passable cell to a world point (spiral ring search)."""
        from collections import deque

        target = self.cell(*cell_xy)
        if self.passable(*target):
            return target
        seen = {target}
        queue = deque([target])
        while queue:
            ix, iy = queue.popleft()
            for nx, ny in ((ix + 1, iy), (ix - 1, iy), (ix, iy + 1), (ix, iy - 1)):
                if (nx, ny) in seen:
                    continue
                seen.add((nx, ny))
                if self.passable(nx, ny):
                    return (nx, ny)
                if abs(nx - target[0]) + abs(ny - target[1]) > max_ring:
                    continue
                queue.append((nx, ny))
        return None

    def path(self, start_xy, goal_xy, max_expansions=400_000):
        """Deterministic first-touch A* between world points; cell path or None.

        Nodes freeze on first discovery (no reopen), so returned paths are
        valid and reproducible but may be slightly suboptimal (~3% observed);
        this oracle certifies existence, not optimality.
        """
        start = self.cell(*start_xy)
        goal = self.cell(*goal_xy)
        if not self.passable(*start) or not self.passable(*goal):
            return None

        def heuristic(ix, iy):
            dx, dy = abs(ix - goal[0]), abs(iy - goal[1])
            return (dx + dy) + (math.sqrt(2) - 2) * min(dx, dy)

        counter = 0
        open_heap = [(heuristic(*start), counter, start)]
        came_from = {start: None}
        cost = {start: 0.0}
        expansions = 0
        while open_heap:
            _, _, current = heapq.heappop(open_heap)
            if current == goal:
                break
            expansions += 1
            if expansions > max_expansions:
                raise ScenarioError("oracle A* exceeded its expansion budget")
            cx, cy = current
            for dx, dy, step in self._NEIGHBORS:
                nxt = (cx + dx, cy + dy)
                if not self.passable(*nxt) or nxt in came_from:
                    continue
                if dx and dy and not (self.passable(cx + dx, cy) and self.passable(cx, cy + dy)):
                    continue  # no corner cutting
                cost[nxt] = cost[current] + step
                came_from[nxt] = current
                counter += 1
                heapq.heappush(open_heap, (cost[nxt] + heuristic(*nxt), counter, nxt))
        else:
            return None
        if goal not in came_from:
            return None
        path = []
        node = goal
        while node is not None:
            path.append(node)
            node = came_from[node]
        return path[::-1]


GROUND_LEVEL_Z_BAND = 0.5  # points above this sit on an elevated level


def _robot_mobility(robot):
    return ROBOT_PROFILES.get(robot["type"], {}).get("mobility", "wheeled")


def _can_use_link(mobility, link):
    """Stairs are legged/aerial only; ramps are open to every ground robot."""
    if link.kind == "stair":
        return mobility in {"quadruped", "aerial"}
    return True


def validate_level_routing(data, template=None):
    """Gate cross-level routes on mobility: a route whose start and goal sit
    on different levels must traverse a link the robot can use.

    Aerial robots ignore levels. Points carry their z; a level is resolved by
    nearest floor height. This is the H1 morphology axis made measurable:
    wheeled bases cannot follow stair links, quadrupeds can.
    """
    if template is None or not template.stairs:
        return {"status": "single_level"}

    floors = [level.floor_z for level in template.levels] or [0.0]

    def level_of(z):
        return min(range(len(floors)), key=lambda i: abs(floors[i] - z))

    robots = {robot["id"]: robot for robot in data["robots"]}
    nodes = {node["id"]: node for node in data["phase_graph"]["nodes"]}
    for node in _kahn_order(data["phase_graph"]["nodes"]):
        agent = robots.get(node.get("preferred_agent") or "")
        if agent is None:
            continue
        mobility = _robot_mobility(agent)
        if mobility == "aerial":
            continue
        refs = [ref for ref in (node.get("target_ref"), node.get("object_ref")) if ref]
        requested_link = (node.get("vertical") or {}).get("link_id")
        for ref in refs:
            for entity in data["composition"]["entities"]:
                if entity["id"] == ref:
                    goal_z = entity["spawn_pose"]["position"][2]
                    start_z = agent["spawn_pose"]["position"][2]
                    start_level = level_of(start_z)
                    goal_level = level_of(goal_z)
                    if start_level == goal_level:
                        continue
                    usable = [link for link in template.stairs
                              if _can_use_link(mobility, link)
                              and (requested_link is None or link.id == requested_link)
                              and min(link.low_z, link.high_z) <= max(start_z, goal_z)
                              and max(link.low_z, link.high_z) >= min(start_z, goal_z)]
                    if not usable:
                        raise ScenarioError(
                            f"{node['id']}: '{agent['id']}' ({mobility}) must change level "
                            f"{start_level}->{goal_level} but no usable stair/ramp link exists")
    vertical_nodes = [node["id"] for node in data["phase_graph"]["nodes"] if node.get("vertical")]
    return {"status": "levels_gated", "links": [link.id for link in template.stairs],
            "explicit_vertical_nodes": vertical_nodes,
            "support_evidence": "required_by_vertical_objective" if vertical_nodes else "not_requested"}


def _robot_radius(robot):
    radius = ROBOT_PROFILES[robot["type"]]["radius_m"]
    if any(a["type"] in {"ur5", "z1"} for a in robot["attachments"]):
        radius = max(radius, 0.9)
    return radius


def _kahn_order(nodes):
    pending = {node["id"]: set(node["prerequisites"]) for node in nodes}
    ordered = []
    while pending:
        ready = sorted(key for key, deps in pending.items() if not deps & pending.keys())
        if not ready:
            raise ScenarioError("phase_graph contains a cycle")
        for key in ready:
            ordered.append(next(node for node in nodes if node["id"] == key))
            del pending[key]
    return ordered


def _transport_mass(data, node):
    for entity in data.get("composition", {}).get("entities", ()):
        if entity["id"] == node["object_ref"]:
            return entity["physics"]["mass_kg"]
    return None


def _eligible_agents(data, node, payload_policy, robots=None):
    """Robots allowed to execute a node under capabilities and the payload policy."""
    pool = data["robots"] if robots is None else robots
    needed = set(SKILLS[node["skill_key"]]["capabilities"])
    mass = _transport_mass(data, node) if node["skill_key"] in {"transport_object", "handoff_object"} else None
    eligible = []
    for robot in pool:
        if not needed <= robot_capabilities(robot):
            continue
        if mass is not None and payload_policy is not None:
            capacity = payload_policy.get(robot["type"])
            # Once a payload policy is enabled, an omitted profile is unknown,
            # not unlimited capacity.  Fail closed so a partial policy cannot
            # silently certify a heavy transport.
            if capacity is None or mass > capacity:
                continue
        eligible.append(robot["id"])
    return eligible, mass

def _joint_team_feasible(data, node, payload_policy):
    """A declared joint_transport team must jointly lift the object.

    Capacity is the SUM of member capacities under the declared policy, so
    removing any member can make the lift infeasible -- the structural
    forced-cooperation certificate for heavy loads (H2).
    """
    mass = _transport_mass(data, node)
    team_ids = list(node.get("agents") or [])
    if len(team_ids) != SKILLS["joint_transport"]["team_size"]:
        raise ScenarioError(f"{node['id']}: joint_transport needs a declared team")
    robots = {r["id"]: r for r in data["robots"]}
    total = 0.0
    for agent_id in team_ids:
        robot = robots.get(agent_id)
        if robot is None:
            raise ScenarioError(f"{node['id']}: unknown team member '{agent_id}'")
        if "navigation" not in robot_capabilities(robot):
            raise ScenarioError(f"{node['id']}: '{agent_id}' cannot navigate to a joint carry")
        if payload_policy is not None:
            cap = payload_policy.get(robot["type"])
            if cap is None:
                raise ScenarioError(
                    f"{node['id']}: no declared capacity for '{robot['type']}' -- fail closed")
            total += cap
    if payload_policy is not None and mass > total:
        raise ScenarioError(
            f"{node['id']}: joint capacity {total:.0f} kg cannot lift {mass:.0f} kg "
            "under the declared payload policy")



def minimum_team_certificate(data, payload_policy=None):
    """Return an exact capability/payload set-cover certificate.

    ``forced_cooperation`` means that no single robot is eligible for every
    phase.  The previous remove-one test confused a uniquely capable robot
    with a multi-robot requirement: a carrier that can also navigate was
    labelled as forced cooperation even when it could execute the whole DAG
    alone.  Exhaustive search is cheap here because V2 permits at most eight
    robots.

    This remains a capability certificate, not a timing/concurrency or
    cooperative-manipulation proof. Payload capacities are caller-declared
    experimental parameters, never inferred ground truth.
    """
    from itertools import combinations

    robots = data["robots"]
    robot_ids = sorted(robot["id"] for robot in robots)
    eligible_by_node = {
        node["id"]: set(_eligible_agents(data, node, payload_policy)[0])
        for node in data["phase_graph"]["nodes"]
    }
    # joint_transport nodes bind their declared team: a candidate team must
    # contain every declared member (joint force application).
    joint_teams = {
        node["id"]: set(node.get("agents") or [])
        for node in data["phase_graph"]["nodes"] if node["skill_key"] == "joint_transport"
    }
    minimum_teams = []
    for size in range(1, len(robot_ids) + 1):
        for candidate in combinations(robot_ids, size):
            team = set(candidate)
            if not all(team & eligible for eligible in eligible_by_node.values()):
                continue
            if all(members <= team for members in joint_teams.values()):
                minimum_teams.append(list(candidate))
        if minimum_teams:
            break
    minimum_size = len(minimum_teams[0]) if minimum_teams else None
    necessary = (sorted(set.intersection(*(set(team) for team in minimum_teams)))
                 if minimum_teams else [])
    single_agent_feasible = [team[0] for team in minimum_teams if len(team) == 1]
    robot_type = {robot["id"]: robot["type"] for robot in robots}
    forced_heterogeneity = bool(minimum_teams) and minimum_size > 1 and all(
        len({robot_type[robot_id] for robot_id in team}) > 1
        for team in minimum_teams
    )
    return {
        "necessary_robots": necessary,
        "minimum_team_size": minimum_size,
        "minimum_teams": minimum_teams,
        "single_agent_feasible": single_agent_feasible,
        "forced_cooperation": minimum_size is not None and minimum_size > 1,
        "forced_heterogeneity": forced_heterogeneity,
        "basis": ("capability + declared payload policy + joint-team binding"
                  if payload_policy is not None else
                  "capability set cover + joint-team binding; timing and task scale not modelled"),
        "payload_policy": payload_policy,
        "checked_team": sorted(robot["id"] for robot in robots),
        "eligible_agents_by_node": {
            node: sorted(eligible) for node, eligible in eligible_by_node.items()
        },
    }


def validate_oracle(data, occupancy, payload_policy=None, template=None):
    """Run the deterministic navigation feasibility oracle over the phase DAG.

    Raises ScenarioError naming the first node without a collision-free,
    clearance-respecting route (or whose preferred agent violates the declared
    payload policy); returns the route table and team certificate.
    """
    from .geometry import static_blocked_grid

    for node in data["phase_graph"]["nodes"]:
        if node["skill_key"] not in {"transport_object", "handoff_object"} or payload_policy is None:
            continue
        eligible, mass = _eligible_agents(data, node, payload_policy)
        if node["preferred_agent"] is not None and node["preferred_agent"] not in eligible:
            raise ScenarioError(
                f"{node['id']}: preferred agent '{node['preferred_agent']}' cannot carry "
                f"{mass} kg under the declared payload policy")

    blocked = static_blocked_grid(occupancy, data)
    grids: dict[float, InflatedGrid] = {}

    def grid_for(radius):
        if radius not in grids:
            grids[radius] = InflatedGrid(occupancy, radius, blocked)
        return grids[radius]

    floors = tuple(level.floor_z for level in template.levels) if template and template.levels else ()

    def same_elevated_level(p, q):
        if not floors or len(floors) < 2:
            return False
        elevated = [f for f in floors if f >= GROUND_LEVEL_Z_BAND]
        if not elevated:
            return False
        def near(v):
            # Profile offsets (up to ~0.52 m) sit above the floor surface.
            return any(abs(v - f) <= 1.0 for f in elevated)
        zs = (p[2] if len(p) > 2 else 0.0, q[2] if len(q) > 2 else 0.0)
        return near(zs[0]) and near(zs[1])

    def segment(robot, start_xy, goal_xy):
        radius = _robot_radius(robot)
        grid = grid_for(radius)
        path = grid.path(start_xy, goal_xy)
        approach = False
        nearest_xy = None
        if path is None:
            # The goal may sit inside an obstacle footprint (navigate-to-
            # static-entity); approach the nearest passable cell instead.
            nearest = grid.nearest_passable(goal_xy)
            if nearest is not None:
                approach = True
                nearest_xy = grid._occupancy.world(
                    (nearest[0] + 0.5) * grid.resolution, (nearest[1] + 0.5) * grid.resolution)
                path = grid.path(start_xy, nearest_xy)
        if path is None:
            raise ScenarioError(
                f"{robot['id']}: no collision-free route with {radius:.2f} m clearance "
                f"from ({start_xy[0]:.2f}, {start_xy[1]:.2f}) to ({goal_xy[0]:.2f}, {goal_xy[1]:.2f})")
        end_xy = nearest_xy if approach else goal_xy
        return {"from": [round(start_xy[0], 2), round(start_xy[1], 2)],
                "to": [round(end_xy[0], 2), round(end_xy[1], 2)],
                "clearance_m": radius, "steps": len(path), "approach": approach}

    def point_of(ref):
        if ref in object_xy:
            return object_xy[ref]
        for target in data["composition"]["targets"]:
            if target["id"] == ref:
                return tuple(target["position"][:3])
        # Navigate legs may point at any entity, including static/visual ones
        # (V2 validation allows every entity as a target_ref).
        for entity in data["composition"]["entities"]:
            if entity["id"] == ref:
                return tuple(entity["spawn_pose"]["position"][:3])
        raise ScenarioError(f"unknown point reference '{ref}'")

    robots = {robot["id"]: robot for robot in data["robots"]}
    position = {robot["id"]: tuple(robot["spawn_pose"]["position"][:3]) for robot in data["robots"]}
    # Dynamic objects are tracked as the DAG executes, so later nodes see the
    # object where the previous transport actually left it.
    object_xy = {entity["id"]: tuple(entity["spawn_pose"]["position"][:3])
                 for entity in data["composition"]["entities"]
                 if entity["physics"]["mode"] in {"dynamic", "kinematic"}}
    routes = []
    for node in _kahn_order(data["phase_graph"]["nodes"]):
        if node["skill_key"] == "joint_transport":
            _joint_team_feasible(data, node, payload_policy)
        eligible_ids, _ = _eligible_agents(data, node, payload_policy)
        if node["skill_key"] != "joint_transport" and not eligible_ids:
            raise ScenarioError(f"{node['id']}: no robot can execute '{node['skill_key']}' "
                                "under the capabilities and declared payload policy")
        # An explicit preferred agent pins the executor; otherwise this is an
        # existence check -- try eligible agents in order until one has routes.
        if node["skill_key"] == "joint_transport":
            candidates = [node["agents"][0]]
        else:
            candidates = ([node["preferred_agent"]] if node["preferred_agent"] in eligible_ids
                          else eligible_ids)
        skill = node["skill_key"]
        if skill == "wait":
            continue
        last_error: ScenarioError | None = None
        executed = False
        for agent_id in candidates:
            robot = robots[agent_id]
            def leg(robot, start3, goal3):
                if same_elevated_level(start3, goal3):
                    return {"from": [round(start3[0], 2), round(start3[1], 2)],
                            "to": [round(goal3[0], 2), round(goal3[1], 2)],
                            "clearance_m": _robot_radius(robot), "steps": 1,
                            "approach": False, "note": "platform-level leg (no grid)"}
                return segment(robot, (start3[0], start3[1]), (goal3[0], goal3[1]))
            try:
                if skill in {"transport_object", "handoff_object", "joint_transport"}:
                    object_start = point_of(node["object_ref"])
                    start3 = (position[agent_id][0], position[agent_id][1], position[agent_id][2])
                    legs = [leg(robot, start3, object_start)]
                    routes.append({"node": node["id"], "agent": agent_id, "legs": legs})
                    end = legs[-1]["to"]
                    position[agent_id] = (end[0], end[1], object_start[2])
                if skill in {"transport_object", "joint_transport"}:
                    goal3 = point_of(node["target_ref"])
                    start3 = (position[agent_id][0], position[agent_id][1], position[agent_id][2])
                    legs = [leg(robot, start3, goal3)]
                    carry = {"node": node["id"] + ":carry", "agent": agent_id, "legs": legs}
                    if skill == "joint_transport":
                        carry["team"] = list(node.get("agents") or [agent_id])
                    routes.append(carry)
                    end = legs[-1]["to"]
                    position[agent_id] = (end[0], end[1], goal3[2])
                    object_xy[node["object_ref"]] = goal3
                elif skill == "navigate":
                    goal3 = point_of(node["target_ref"])
                    start3 = (position[agent_id][0], position[agent_id][1], position[agent_id][2])
                    legs = [leg(robot, start3, goal3)]
                    routes.append({"node": node["id"], "agent": agent_id, "legs": legs})
                    end = legs[-1]["to"]
                    position[agent_id] = (end[0], end[1], goal3[2])
                executed = True
                break
            except ScenarioError as exc:
                last_error = exc
        if not executed:
            assert last_error is not None
            raise last_error

    return {
        "status": "feasibility_validated",
        "oracle_kind": "navigation_kinematics_2d_inflated_astar",
        "routes": routes,
        "minimum_team": minimum_team_certificate(data, payload_policy=payload_policy),
        "arm_collision_check": {
            "backend": "curobo",
            "state": "not_installed",
            "note": "UR5/Z1 transport legs are capability-checked only; attach CuRobo IK + "
                    "collision-free trajectory solving for 3-D arm validation",
        },
    }
