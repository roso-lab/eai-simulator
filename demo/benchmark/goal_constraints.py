"""Declarative constraints for conditionally reliable benchmark skills.

These checks use public, benchmark-defined parameters, not hardware ratings or
simulation. Routes are directed; width and height use a fixed envelope with the
public clearance margin on *each* side. A handling envelope is authored for the
whole formation and is never allowed to shrink an object or member's body.
``reach_m`` bounds the norm of a grasp point in the declared object-local frame;
this is an ideal attachment proxy, not inverse kinematics or a base pose check.

Resource capacity counts concurrent actions (a joint formation is one action).
Each action reserves every listed route resource for its entire half-open time
window. Region operations reserve their operation regions for the same window.
Idle robot occupancy, turning geometry, speed, contact dynamics, observations,
and business-goal completeness are outside this module's validation scope.

For a replan, an authoritative caller may supply ``context.object_states`` as
an object-id mapping. A state has ``region_id`` and, when held, both
``handling_mode_id`` and ``carrier_bindings``. Absent states use public initial
regions. ``context.robot_regions`` optionally seeds known robot region IDs;
otherwise the first spatial action infers its starting region. Subsequent
spatial actions must maintain causal position continuity. This module does not
establish the provenance of runtime state or verify initial scene placement.
"""

from __future__ import annotations

from itertools import permutations
import math
from typing import Any, Mapping

from .skill_ontology import skill_names


CONSTRAINT_SCHEMA = "eai.benchmark.reliable_skills"
CONSTRAINT_SCOPE = "route_envelope_resource_model"
_NAVIGATION = {"ground_navigation", "aerial_navigation", "stair_traversal"}
_TRANSPORT = {"pick", "place", "handoff", "cooperative_carry"}
_DESTINATION = {
    "ground_navigation": "region_id", "aerial_navigation": "region_id",
    "stair_traversal": "exit_region_id", "pick": "pickup_region_id",
    "place": "placement_region_id", "handoff": "receiving_region_id",
    "cooperative_carry": "destination_region_id",
}


def _map(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _fields(value: Any, names: set[str], label: str) -> Mapping[str, Any]:
    row = _map(value, label)
    if set(row) != names:
        raise ValueError(f"{label} fields mismatch: missing={sorted(names - set(row))}, "
                         f"extra={sorted(set(row) - names)}")
    return row


def _number(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(number) or number < 0 or (positive and number == 0):
        raise ValueError(f"{label} must be finite and {'positive' if positive else 'nonnegative'}")
    return number


def _capacity(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _strings(value: Any, label: str, *, empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not empty):
        raise ValueError(f"{label} must be a {'possibly empty' if empty else 'nonempty'} list")
    result = [_identifier(item, label) for item in value]
    if len(result) != len(set(result)):
        raise ValueError(f"{label} must contain unique values")
    return result


def _vector(value: Any, label: str, *, length: int = 3, position: bool = False) -> list[float]:
    if not isinstance(value, list) or len(value) != length:
        raise ValueError(f"{label} must have {length} components")
    if not position:
        return [_number(v, label, positive=True) for v in value]
    result = []
    for v in value:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"{label} must contain finite coordinates")
        try:
            coord = float(v)
        except OverflowError as exc:
            raise ValueError(f"{label} must contain finite coordinates") from exc
        if not math.isfinite(coord):
            raise ValueError(f"{label} must contain finite coordinates")
        result.append(coord)
    return result


def _indexed(value: Any, label: str, *, empty: bool = True) -> dict[str, Mapping[str, Any]]:
    if not isinstance(value, list) or (not value and not empty):
        raise ValueError(f"{label} must be a {'possibly empty' if empty else 'nonempty'} list")
    result = {}
    for raw in value:
        row = _map(raw, label)
        identifier = _identifier(row.get("id"), f"{label} id")
        if identifier in result:
            raise ValueError(f"{label} contains duplicate id {identifier}")
        result[identifier] = row
    return result


def _profile(robot: Mapping[str, Any]) -> None:
    label = f"robot {robot.get('id')} physical_profile"
    profile = _fields(robot.get("physical_profile"), {
        "configuration_id", "body_size_m", "effective_payload_kg", "reach_m",
        "grasp_interfaces", "max_step_height_m",
    }, label)
    _identifier(profile["configuration_id"], f"{label} configuration_id")
    _vector(profile["body_size_m"], f"{label} body_size_m")
    for key in ("effective_payload_kg", "reach_m", "max_step_height_m"):
        _number(profile[key], f"{label} {key}")
    _strings(profile["grasp_interfaces"], f"{label} grasp_interfaces", empty=True)
    if robot.get("mobility") not in {"ground", "aerial"}:
        raise ValueError(f"robot {robot.get('id')} needs ground or aerial mobility")
    _strings(robot.get("public_capabilities"), "robot public_capabilities", empty=True)
    _strings(robot.get("interfaces"), "robot interfaces", empty=True)


def validate_robot_profile(robot: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a full public robot declaration, including a joined member.

    Lifecycle/capability callers can use this before publishing a replacement
    declaration. It checks numerical profile completeness, not runtime binding
    or whether a real robot has the advertised configuration.
    """
    robot = _map(robot, "robot")
    identifier = _identifier(robot.get("id"), "robot id")
    _profile(robot)
    return {"validated": True, "robot_id": identifier,
            "configuration_id": robot["physical_profile"]["configuration_id"],
            "execution_validated": False}


def _rows(task_spec: Mapping[str, Any], override: Any = None) -> dict[str, Mapping[str, Any]]:
    source = task_spec["candidate_robot_pool"] if override is None else override
    if isinstance(source, Mapping):
        result = dict(source)
        for key, row in result.items():
            row = _map(row, "robot row")
            if key != row.get("id"):
                raise ValueError("robot_rows keys must match robot ids")
            _identifier(key, "robot id")
    else:
        result = _indexed(source, "robot rows")
    for row in result.values():
        _profile(row)
    return result


def validate_constraint_model(task_spec: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the public numerical model; raise ValueError on invalid input.

    Other public task fields remain the responsibility of the goal contract.
    Physical substructures are closed schemas to reject unmodelled semantics.
    Empty handling modes describe an object with no declared transport method.
    """
    spec = _map(task_spec, "task_spec")
    contract = _fields(spec.get("execution_contract"), {
        "schema", "version", "parameter_basis", "scene_binding_status", "guarantee",
        "collision_policy", "observation_policy", "skill_durations_s", "clearance_margin_m",
    }, "execution_contract")
    expected = {
        "schema": CONSTRAINT_SCHEMA, "version": 1,
        "parameter_basis": "benchmark_defined", "scene_binding_status": "pending",
        "guarantee": "conditional_deterministic", "collision_policy": "hold_and_report",
        "observation_policy": "exact_within_declared_visibility",
    }
    for key, value in expected.items():
        if contract[key] != value or (key == "version" and type(contract[key]) is not int):
            raise ValueError(f"execution_contract {key} must be {value!r}")
    _number(contract["clearance_margin_m"], "clearance_margin_m")
    durations = _fields(contract["skill_durations_s"], set(skill_names()), "skill_durations_s")
    for key, duration in durations.items():
        _number(duration, f"skill_durations_s {key}", positive=True)
    _indexed(spec.get("candidate_robot_pool"), "candidate_robot_pool", empty=False)
    robots = _rows(spec)
    world = _map(spec.get("public_world"), "public_world")
    regions = _indexed(world.get("regions"), "regions", empty=False)
    for region in regions.values():
        _capacity(region.get("resource_capacity"), f"region {region['id']} resource_capacity")
        modes = _strings(region.get("access_modes"), f"region {region['id']} access_modes")
        if not set(modes) <= _NAVIGATION:
            raise ValueError(f"region {region['id']} has unknown access_modes")
    objects = _indexed(world.get("objects"), "objects")
    for obj in objects.values():
        label = f"object {obj['id']} physical_properties"
        props = _fields(obj.get("physical_properties"), {
            "mass_kg", "size_m", "initial_region_id", "grasp_points", "handling_modes",
        }, label)
        _number(props["mass_kg"], f"{label} mass_kg", positive=True)
        size = _vector(props["size_m"], f"{label} size_m")
        if _identifier(props["initial_region_id"], label) not in regions:
            raise ValueError(f"{label} initial_region_id is unknown")
        points = _indexed(props["grasp_points"], f"{label} grasp_points")
        for point in points.values():
            _fields(point, {"id", "position_m", "interface"}, "grasp point")
            _vector(point["position_m"], "grasp point position_m", position=True)
            _identifier(point["interface"], "grasp point interface")
        modes = _indexed(props["handling_modes"], f"{label} handling_modes")
        for mode in modes.values():
            _fields(mode, {"id", "slots", "envelope_m"}, "handling mode")
            envelope = _vector(mode["envelope_m"], "handling mode envelope_m")
            if any(a < b for a, b in zip(envelope, size)):
                raise ValueError(f"{label}: handling envelope cannot shrink object size")
            if not isinstance(mode["slots"], list) or not mode["slots"]:
                raise ValueError("handling mode slots must be a nonempty list")
            fractions, used = [], set()
            for slot in mode["slots"]:
                _fields(slot, {"point_id", "load_fraction"}, "handling slot")
                point_id = _identifier(slot["point_id"], "slot point_id")
                if point_id not in points or point_id in used:
                    raise ValueError("handling slots need unique, known grasp points")
                used.add(point_id)
                fraction = _number(slot["load_fraction"], "load_fraction", positive=True)
                if fraction > 1:
                    raise ValueError("load_fraction must be at most one")
                fractions.append(fraction)
            if not math.isclose(math.fsum(fractions), 1.0, rel_tol=0, abs_tol=1e-9):
                raise ValueError("handling mode load fractions must sum to one")
    links = _indexed(world.get("vertical_links", []), "vertical_links")
    for link in links.values():
        for key in ("from_region_id", "to_region_id"):
            if _identifier(link.get(key), f"link {key}") not in regions:
                raise ValueError("vertical link references unknown region")
        if link["from_region_id"] == link["to_region_id"]:
            raise ValueError("vertical link endpoints must differ")
        _strings(link.get("traversal_modes"), "vertical link traversal_modes")
    routes = _indexed(world.get("routes"), "routes")
    capacities: dict[str, int] = {}
    for route in routes.values():
        _fields(route, {"id", "from_region_id", "to_region_id", "access_modes", "length_m",
                        "clearance_m", "max_step_height_m", "resource_id", "capacity"}, "route")
        for key in ("from_region_id", "to_region_id"):
            if _identifier(route[key], f"route {key}") not in regions:
                raise ValueError(f"route {route['id']} references unknown region")
        modes = _strings(route["access_modes"], "route access_modes")
        if not set(modes) <= _NAVIGATION:
            raise ValueError("route has unknown access_modes")
        _number(route["length_m"], "route length_m", positive=True)
        _number(route["max_step_height_m"], "route max_step_height_m")
        _vector(route["clearance_m"], "route clearance_m", length=2)
        resource = _identifier(route["resource_id"], "route resource_id")
        capacity = _capacity(route["capacity"], "route capacity")
        if resource in capacities and capacities[resource] != capacity:
            raise ValueError(f"route resource {resource} has inconsistent capacity")
        capacities[resource] = capacity
    return {"validated": True, "scope": CONSTRAINT_SCOPE, "execution_validated": False,
            "robot_count": len(robots), "object_count": len(objects), "route_count": len(routes)}


def _binding_option(obj: Mapping[str, Any], mode: Mapping[str, Any],
                    assigned: tuple[Mapping[str, Any], ...], *,
                    existing_attachment: bool = False) -> dict[str, Any] | None:
    props = obj["physical_properties"]
    points = {point["id"]: point for point in props["grasp_points"]}
    skill = "pick" if len(mode["slots"]) == 1 else "cooperative_carry"
    interface = "manipulator" if skill == "pick" else "cooperative_transport"
    bindings, loads = [], []
    envelope = list(mode["envelope_m"])
    for robot, slot in zip(assigned, mode["slots"]):
        profile = robot["physical_profile"]
        point = points[slot["point_id"]]
        load = props["mass_kg"] * slot["load_fraction"]
        if (robot["mobility"] != "ground"
                or (not existing_attachment and skill not in robot["public_capabilities"])
                or interface not in robot["interfaces"]
                or point["interface"] not in profile["grasp_interfaces"]
                or load > profile["effective_payload_kg"]
                or math.hypot(*point["position_m"]) > profile["reach_m"]):
            return None
        bindings.append({"agent_id": robot["id"], "point_id": point["id"]})
        loads.append({"agent_id": robot["id"], "load_kg": load})
        envelope = [max(a, b) for a, b in zip(envelope, profile["body_size_m"])]
    return {"object_id": obj["id"], "handling_mode_id": mode["id"], "skill": skill,
            "assigned_agents": [r["id"] for r in assigned], "carrier_bindings": bindings,
            "member_loads_kg": loads, "envelope_m": envelope}


def transport_options(task_spec: Mapping[str, Any], object_id: str, *,
                      robot_rows: Any = None) -> list[dict[str, Any]]:
    """Enumerate every feasible member/mode/point assignment, without a plan.

    ``robot_rows`` may be a list or an id-keyed mapping, including an empty
    counterfactual pool. Returned options establish attachment/load feasibility
    only; they do not establish route, schedule, or business feasibility.
    """
    validate_constraint_model(task_spec)
    rows = _rows(task_spec, robot_rows)
    objects = {row["id"]: row for row in task_spec["public_world"]["objects"]}
    if not isinstance(object_id, str) or object_id not in objects:
        raise ValueError(f"unknown object_id {object_id!r}")
    obj = objects[object_id]
    result = []
    for mode in obj["physical_properties"]["handling_modes"]:
        for assigned in permutations([rows[key] for key in sorted(rows)], len(mode["slots"])):
            option = _binding_option(obj, mode, assigned)
            if option is not None:
                result.append(option)
    return result


def _binding(obj: Mapping[str, Any], arguments: Mapping[str, Any], agents: list[str],
             rows: Mapping[str, Mapping[str, Any]], label: str, *,
             existing_attachment: bool = False) -> dict[str, Any]:
    mode_id = _identifier(arguments.get("handling_mode_id"), f"{label} handling_mode_id")
    modes = {mode["id"]: mode for mode in obj["physical_properties"]["handling_modes"]}
    if mode_id not in modes:
        raise ValueError(f"{label}: unknown handling_mode_id")
    mode = modes[mode_id]
    raw = arguments.get("carrier_bindings")
    if not isinstance(raw, list) or len(raw) != len(mode["slots"]):
        raise ValueError(f"{label}: carrier_bindings must match handling slots")
    by_point: dict[str, str] = {}
    for binding in raw:
        _fields(binding, {"agent_id", "point_id"}, f"{label} carrier binding")
        agent = _identifier(binding["agent_id"], f"{label} carrier agent")
        point = _identifier(binding["point_id"], f"{label} carrier point")
        if point in by_point or agent in by_point.values():
            raise ValueError(f"{label}: carrier bindings need distinct agents and points")
        by_point[point] = agent
    if set(by_point.values()) != set(agents) or set(by_point) != {s["point_id"] for s in mode["slots"]}:
        raise ValueError(f"{label}: carrier bindings must match assigned agents and mode points")
    assigned = tuple(rows[by_point[slot["point_id"]]] for slot in mode["slots"])
    option = _binding_option(obj, mode, assigned, existing_attachment=existing_attachment)
    if option is None:
        if existing_attachment:
            for robot, slot in zip(assigned, mode["slots"]):
                load = obj["physical_properties"]["mass_kg"] * slot["load_fraction"]
                capacity = robot["physical_profile"]["effective_payload_kg"]
                if load > capacity:
                    raise ValueError(f"{label}: existing attachment payload_exceeded for {robot['id']}: "
                                     f"load {load} kg exceeds current capacity {capacity} kg; "
                                     "runtime safe-state recovery required")
            raise ValueError(f"{label}: existing attachment violates current interface, reach, "
                             "or mobility constraints; runtime safe-state recovery required")
        raise ValueError(f"{label}: infeasible carrier payload, interface, reach, or capability")
    return option


def _resource_conflicts(entries: list[tuple[float, float, str]], capacity: int, label: str) -> None:
    # End events sort before starts: [a,b) and [b,c) do not overlap.
    events = sorted([(start, 1, tid) for start, _, tid in entries]
                    + [(end, -1, tid) for _, end, tid in entries])
    active: set[str] = set()
    for time, delta, tid in events:
        if delta < 0:
            active.remove(tid)
        else:
            active.add(tid)
            if len(active) > capacity:
                raise ValueError(f"{label} capacity exceeded at {time}: {sorted(active)}")


def _path(arguments: Mapping[str, Any], routes: Mapping[str, Mapping[str, Any]],
          destination: str | None, label: str, *, required: bool = False) -> list[Mapping[str, Any]]:
    if "route_ids" not in arguments:
        if required:
            raise ValueError(f"{label}: nonempty route_ids are required")
        return []
    identifiers = _strings(arguments["route_ids"], f"{label} route_ids")
    if any(identifier not in routes for identifier in identifiers):
        raise ValueError(f"{label}: unknown route_id")
    path = [routes[identifier] for identifier in identifiers]
    if any(a["to_region_id"] != b["from_region_id"] for a, b in zip(path, path[1:])):
        raise ValueError(f"{label}: route path is disconnected")
    if path[-1]["to_region_id"] != destination:
        raise ValueError(f"{label}: route endpoint does not match destination")
    return path


def _region_access(region: Mapping[str, Any], robot: Mapping[str, Any], label: str) -> None:
    modes = set(region["access_modes"])
    capabilities = set(robot["public_capabilities"])
    if robot["mobility"] == "aerial":
        allowed = "aerial_navigation" in modes
    else:
        allowed = bool(modes & capabilities & {"ground_navigation", "stair_traversal"})
    if not allowed:
        raise ValueError(f"{label}: {robot['id']} cannot access region {region['id']}")


def _route_fit(path: list[Mapping[str, Any]], robots: list[Mapping[str, Any]],
               envelope: list[float], margin: float, skill: str, label: str,
               regions: Mapping[str, Mapping[str, Any]]) -> None:
    for route in path:
        if envelope[1] + 2 * margin > route["clearance_m"][0] or envelope[2] + 2 * margin > route["clearance_m"][1]:
            raise ValueError(f"{label}: route {route['id']} clearance cannot fit transport/body envelope")
        for robot in robots:
            capabilities = set(robot["public_capabilities"])
            modes = set(route["access_modes"])
            mobility_modes = {"aerial_navigation"} if robot["mobility"] == "aerial" else {"ground_navigation", "stair_traversal"}
            if not modes & capabilities & mobility_modes:
                raise ValueError(f"{label}: route {route['id']} access capability unavailable to {robot['id']}")
            if skill in {"ground_navigation", "aerial_navigation"} and skill not in modes:
                raise ValueError(f"{label}: route {route['id']} requires a different navigation skill")
            if robot["mobility"] == "ground" and route["max_step_height_m"] > robot["physical_profile"]["max_step_height_m"]:
                raise ValueError(f"{label}: route {route['id']} exceeds max_step_height_m")
            _region_access(regions[route["from_region_id"]], robot, label)
            _region_access(regions[route["to_region_id"]], robot, label)


def validate_plan_constraints(plan: Mapping[str, Any], *, task_spec: Mapping[str, Any],
                              robot_rows: Any = None, context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Check timed actions, ownership, attachment feasibility and resources.

    Every navigation action needs a directed route chain. Picks acquire one
    object, place/handoff release it, and cooperative_carry is atomic
    grab/carry/release. Holding an object persists across idle time and other
    actions, so ordinary navigation cannot bypass its transport envelope.
    Object-state transitions must be causally connected by plan prerequisites.
    Existing attachments do not require the skill to acquire a new object, but
    must still satisfy current load, interface, binding and reach constraints.
    An invalid held state requires runtime safe-state recovery before admission;
    this validator cannot authorize an overloaded release or invent that state.
    Unseeded robot starting positions are inferred from their first spatial
    action. Later positions must follow causally from preceding spatial actions.
    """
    validate_constraint_model(task_spec)
    plan = _map(plan, "plan")
    context = {} if context is None else _map(context, "context")
    rows = _rows(task_spec, robot_rows)
    if robot_rows is None:
        for row in _indexed(context.get("joined_agents", []), "joined_agents").values():
            if row["id"] in rows:
                raise ValueError("joined agent id collides with public pool")
            _profile(row)
            rows[row["id"]] = row
    current = set(rows)
    if "current_agents" in context:
        current = set(_strings(context["current_agents"], "current_agents", empty=True))
        if not current <= set(rows):
            raise ValueError("current_agents references an unknown robot")
    team = _strings(plan.get("assigned_team"), "assigned_team")
    if not set(team) <= current:
        raise ValueError("assigned_team contains unknown or inactive members")
    budgets = _map(task_spec.get("budgets", {}), "budgets")
    if "max_active_agents" in budgets and len(team) > _capacity(budgets["max_active_agents"], "max_active_agents"):
        raise ValueError("assigned_team exceeds max_active_agents")
    contract = task_spec["execution_contract"]
    world = task_spec["public_world"]
    regions = {row["id"]: row for row in world["regions"]}
    objects = {row["id"]: row for row in world["objects"]}
    routes = {row["id"]: row for row in world["routes"]}
    links = {row["id"]: row for row in world.get("vertical_links", [])}
    raw_tasks = plan.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise ValueError("plan tasks must be a nonempty list")
    now = _number(context.get("current_time_s", 0), "current_time_s")
    tasks: dict[str, dict[str, Any]] = {}
    reservations: dict[tuple[str, str], list[tuple[float, float, str]]] = {}
    capacities: dict[tuple[str, str], int] = {}

    def reserve(kind: str, identifier: str, capacity: int, task: Mapping[str, Any]) -> None:
        key = (kind, identifier)
        interval = (task["start"], task["end"], task["id"])
        entries = reservations.setdefault(key, [])
        if interval not in entries:
            entries.append(interval)
        capacities[key] = capacity

    for raw in raw_tasks:
        row = _map(raw, "plan task")
        tid = _identifier(row.get("task_id"), "task_id")
        if tid in tasks:
            raise ValueError("plan contains duplicate task_id")
        skill = _identifier(row.get("skill"), f"{tid} skill")
        if skill not in contract["skill_durations_s"]:
            raise ValueError(f"{tid}: unknown skill")
        args = _map(row.get("arguments"), f"{tid} arguments")
        assigned = _strings(row.get("assigned_agents"), f"{tid} assigned_agents")
        if not set(assigned) <= set(team):
            raise ValueError(f"{tid}: assigned agents must belong to assigned_team")
        if (skill in _NAVIGATION | {"pick", "place", "handoff"}) and len(assigned) != 1:
            raise ValueError(f"{tid}: {skill} requires exactly one assigned agent")
        if skill in {"cooperative_carry", "synchronize"} and len(assigned) < 2:
            raise ValueError(f"{tid}: {skill} requires at least two assigned agents")
        for agent in assigned:
            if skill not in rows[agent]["public_capabilities"]:
                raise ValueError(f"{tid}: {agent} lacks skill capability {skill}")
            if skill == "aerial_navigation" and rows[agent]["mobility"] != "aerial":
                raise ValueError(f"{tid}: aerial navigation requires aerial mobility")
            if skill in {"ground_navigation", "stair_traversal"} | _TRANSPORT and rows[agent]["mobility"] != "ground":
                raise ValueError(f"{tid}: {skill} requires ground mobility")
        execution = _fields(row.get("execution"), {"start_s", "duration_s"}, f"{tid} execution")
        start = _number(execution["start_s"], f"{tid} start_s")
        duration = _number(execution["duration_s"], f"{tid} duration_s", positive=True)
        end = start + duration
        if not math.isfinite(end) or end <= start:
            raise ValueError(f"{tid}: execution window must have a finite increasing end")
        if start < now:
            raise ValueError(f"{tid}: task starts before current_time_s")
        if duration < contract["skill_durations_s"][skill]:
            raise ValueError(f"{tid}: duration is below skill minimum")
        if "simulation_s" in budgets and end > _number(budgets["simulation_s"], "simulation_s", positive=True):
            raise ValueError(f"{tid}: task exceeds simulation_s budget")
        dependencies = _strings(row.get("prerequisites"), f"{tid} prerequisites", empty=True)
        destination = args.get(_DESTINATION.get(skill, "region_id"))
        if destination is not None:
            if _identifier(destination, f"{tid} destination") not in regions:
                raise ValueError(f"{tid}: unknown region")
        elif skill in _DESTINATION or skill == "observe":
            raise ValueError(f"{tid}: missing destination/operation region")
        if "route_ids" in args and skill not in _NAVIGATION | {"cooperative_carry", "place", "handoff"}:
            raise ValueError(f"{tid}: route_ids are not supported by {skill}")
        path = _path(args, routes, destination, tid, required=skill in _NAVIGATION | {"cooperative_carry"})
        if skill == "stair_traversal":
            link_id = _identifier(args.get("link_id"), f"{tid} link_id")
            if link_id not in links:
                raise ValueError(f"{tid}: unknown stair link")
            link = links[link_id]
            ends = {link["from_region_id"], link["to_region_id"]}
            if ({path[0]["from_region_id"], path[-1]["to_region_id"]} != ends
                    or "stair_traversal" not in link["traversal_modes"]
                    or not any("stair_traversal" in route["access_modes"] for route in path)):
                raise ValueError(f"{tid}: stair route must match link endpoints and traversal mode")
        task = {"id": tid, "skill": skill, "args": args, "assigned": assigned,
                "start": start, "end": end, "deps": dependencies,
                "destination": destination, "path": path}
        tasks[tid] = task
        for agent in assigned:
            reserve("agent", agent, 1, task)
        for route in path:
            reserve("route", route["resource_id"], route["capacity"], task)

    ancestors: dict[str, set[str]] = {}
    pending = set(tasks)
    while pending:
        ready = [tid for tid in sorted(pending) if set(tasks[tid]["deps"]) <= set(ancestors)]
        if not ready:
            raise ValueError("prerequisites contain unknown tasks or a cycle")
        for tid in ready:
            task = tasks[tid]
            inherited: set[str] = set(task["deps"])
            for dep in task["deps"]:
                if tasks[dep]["end"] > task["start"]:
                    raise ValueError(f"{tid}: prerequisite {dep} has not ended before start")
                inherited.update(ancestors[dep])
            ancestors[tid] = inherited
            pending.remove(tid)
    for key, entries in reservations.items():
        _resource_conflicts(entries, capacities[key], f"{key[0]} {key[1]}")

    states = {oid: {"region": obj["physical_properties"]["initial_region_id"], "option": None,
                    "last_task": None} for oid, obj in objects.items()}
    initial_states = _map(context.get("object_states", {}), "object_states")
    for oid, raw in initial_states.items():
        if oid not in objects:
            raise ValueError("object_states references an unknown object")
        state = _map(raw, "object state")
        has_holding = "handling_mode_id" in state or "carrier_bindings" in state
        _fields(state, {"region_id", "handling_mode_id", "carrier_bindings"} if has_holding else {"region_id"}, "object state")
        region_id = _identifier(state["region_id"], "object state region_id")
        if region_id not in regions:
            raise ValueError("object state has unknown region")
        option = None
        if has_holding:
            raw_bindings = state["carrier_bindings"]
            if not isinstance(raw_bindings, list):
                raise ValueError("object state carrier_bindings must be a list")
            assigned = [_identifier(_map(b, "carrier binding").get("agent_id"), "carrier agent") for b in raw_bindings]
            if not set(assigned) <= current:
                raise ValueError("object state has unknown or inactive carriers")
            option = _binding(objects[oid], state, assigned, rows, f"object state {oid}",
                              existing_attachment=True)
            if option["skill"] != "pick":
                raise ValueError("object state cannot resume an atomic cooperative_carry mid-action")
        states[oid] = {"region": region_id, "option": option, "last_task": None}
    initial_carriers = [a for state in states.values() if state["option"]
                        for a in state["option"]["assigned_agents"]]
    if len(initial_carriers) != len(set(initial_carriers)):
        raise ValueError("one carrier cannot hold multiple objects")

    positions: dict[str, dict[str, Any]] = {}
    for agent, region_id in _map(context.get("robot_regions", {}), "robot_regions").items():
        _identifier(agent, "robot_regions agent")
        _identifier(region_id, "robot_regions region")
        if agent not in rows or region_id not in regions:
            raise ValueError("robot_regions references unknown robot or region")
        _region_access(regions[region_id], rows[agent], "robot_regions")
        positions[agent] = {"region": region_id, "last_task": None}
    for state in states.values():
        if state["option"] is None:
            continue
        for agent in state["option"]["assigned_agents"]:
            if agent in positions and positions[agent]["region"] != state["region"]:
                raise ValueError("held object state and robot_regions disagree")
            positions[agent] = {"region": state["region"], "last_task": None}

    locked: dict[str, str] = {}
    effects: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    position_effects: dict[str, dict[str, dict[str, Any]]] = {}
    events = sorted([(t["start"], 1, tid) for tid, t in tasks.items()]
                    + [(t["end"], -1, tid) for tid, t in tasks.items()])
    for _, phase, tid in events:
        task = tasks[tid]
        if phase == -1:
            for oid, state in effects.pop(tid, []):
                states[oid] = state
                locked.pop(oid)
            positions.update(position_effects.pop(tid, {}))
            continue
        assigned = task["assigned"]
        skill, args, destination = task["skill"], task["args"], task["destination"]
        actor_rows = [rows[a] for a in assigned]
        envelope = [max(r["physical_profile"]["body_size_m"][i] for r in actor_rows) for i in range(3)]
        operation_regions = set()
        if destination is not None and skill not in _NAVIGATION:
            operation_regions.add(destination)
        held = [oid for oid, state in states.items() if state["option"]
                and set(state["option"]["assigned_agents"]) & set(assigned)]
        updates: dict[str, dict[str, Any]] = {}

        def causal(oid: str) -> None:
            previous = states[oid]["last_task"]
            if previous is not None and previous not in ancestors[tid]:
                raise ValueError(f"{tid}: object {oid} ownership/location needs causal prerequisite {previous}")
            if oid in locked:
                raise ValueError(f"{tid}: object {oid} is already occupied by {locked[oid]}")

        if skill in _TRANSPORT:
            oid = _identifier(args.get("object_id"), f"{tid} object_id")
            if oid not in objects:
                raise ValueError(f"{tid}: unknown object")
            causal(oid)
            state = states[oid]
            path = task["path"]
            if path and path[0]["from_region_id"] != state["region"]:
                raise ValueError(f"{tid}: transport route must start at the object's current region")
            if skill in {"pick", "cooperative_carry"}:
                if state["option"] is not None or held:
                    raise ValueError(f"{tid}: object/carriers must be available before acquisition")
                option = _binding(objects[oid], args, assigned, rows, tid)
                if option["skill"] != skill:
                    raise ValueError(f"{tid}: handling mode does not match {skill} member count")
                if skill == "pick" and destination != state["region"]:
                    raise ValueError(f"{tid}: pick must use object's current region")
                operation_regions.add(state["region"])
                updates[oid] = {"region": destination, "option": option if skill == "pick" else None,
                                "last_task": tid}
            else:
                option = state["option"]
                if option is None or set(option["assigned_agents"]) != set(assigned):
                    raise ValueError(f"{tid}: {skill} requires matching object ownership")
                if not path and destination != state["region"]:
                    raise ValueError(f"{tid}: object cannot change region without route_ids")
                updates[oid] = {"region": destination, "option": None, "last_task": tid}
            envelope = [max(a, b) for a, b in zip(envelope, option["envelope_m"])]
        elif skill in _NAVIGATION:
            for oid in held:
                causal(oid)
                state = states[oid]
                if task["path"][0]["from_region_id"] != state["region"]:
                    raise ValueError(f"{tid}: loaded route must start at held object's current region")
                envelope = [max(a, b) for a, b in zip(envelope, state["option"]["envelope_m"])]
                updates[oid] = {"region": destination, "option": state["option"], "last_task": tid}

        _route_fit(task["path"], actor_rows, envelope, contract["clearance_margin_m"], skill, tid, regions)
        origin = task["path"][0]["from_region_id"] if task["path"] else destination
        if origin is not None:
            for agent in assigned:
                previous = positions.get(agent)
                if previous is not None:
                    if previous["region"] != origin:
                        raise ValueError(f"{tid}: robot {agent} region continuity violated: "
                                         f"at {previous['region']}, action starts at {origin}")
                    if previous["last_task"] is not None and previous["last_task"] not in ancestors[tid]:
                        raise ValueError(f"{tid}: robot {agent} position needs causal prerequisite "
                                         f"{previous['last_task']}")
                position_effects.setdefault(tid, {})[agent] = {"region": destination, "last_task": tid}
        for region_id in operation_regions:
            for robot in actor_rows:
                _region_access(regions[region_id], robot, tid)
            reserve("region", region_id, regions[region_id]["resource_capacity"], task)
        for oid, state in updates.items():
            locked[oid] = tid
            reserve("object", oid, 1, task)
            effects.setdefault(tid, []).append((oid, state))

    for key, entries in reservations.items():
        _resource_conflicts(entries, capacities[key], f"{key[0]} {key[1]}")
    final_states = {}
    for oid, state in states.items():
        final = {"region_id": state["region"]}
        if state["option"] is not None:
            final.update({"handling_mode_id": state["option"]["handling_mode_id"],
                          "carrier_bindings": state["option"]["carrier_bindings"]})
        final_states[oid] = final
    return {"validated": True, "scope": CONSTRAINT_SCOPE, "execution_validated": False,
            "task_count": len(tasks), "makespan_s": max(t["end"] for t in tasks.values()),
            "resource_capacity_unit": "concurrent_action", "object_states": final_states,
            "robot_regions": {agent: state["region"] for agent, state in positions.items()},
            "observation_visibility_validated": False,
            "external_validation_requirements": ["declared_observation_visibility", "scene_physical_binding"],
            "limitations": ["unseeded_robot_initial_positions_inferred", "idle_occupancy_not_modelled",
                            "no_turning_geometry_or_speed_model", "no_contact_or_kinematic_validation",
                            "no_observation_or_business_outcome_validation"]}
