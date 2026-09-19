"""Success predicates and the step-0 triviality check.

Following the benchmark protocol, success conditions are derived from the
validated instance, never written by the LLM: every transport phase becomes an
object-at-zone predicate over concrete ids. The step-0 check evaluates the
predicates on the spawn state and rejects tasks that are already satisfied
before any robot moves (PARTNR's trivial-task filter).
"""

import math

from .validation import ScenarioError

PREDICATES = {
    "is_at_zone": {
        "args": ("object_ref", "target_ref"),
        "semantics": "the object's position lies within the target's ground radius (terminal state)",
    },
    "robot_visited_zone": {
        "args": ("robot_ref", "target_ref"),
        "semantics": "the robot entered the target's ground radius at any step of the episode "
                     "(visitation, monotone over the trajectory)",
    },
    "all_at_zone": {
        "args": ("object_refs", "target_ref", "count"),
        "semantics": "at least `count` of the objects lie within the target's ground radius",
    },
}

def derive_success_predicates(data):
    """Deterministically derive the success contract from the phase DAG.

    Only the FINAL transport leg of each object becomes a terminal predicate:
    in a relay, the object ends at its last destination, so an intermediate
    handoff zone would be a permanently-false terminal goal.
    """
    from .oracle import _kahn_order

    final_leg = {}
    visits = set()
    for node in _kahn_order(data["phase_graph"]["nodes"]):
        if node["skill_key"] in {"transport_object", "joint_transport"}:
            final_leg[node["object_ref"]] = node["target_ref"]
        elif (node["skill_key"] == "navigate"
              and node.get("preferred_agent")
              and any(target["id"] == node["target_ref"]
                      for target in data["composition"]["targets"])):
            visits.add((node["preferred_agent"], node["target_ref"]))
    predicates = [{"fn": "is_at_zone", "args": [obj, zone]}
                  for obj, zone in sorted(final_leg.items())]
    predicates += [{"fn": "robot_visited_zone", "args": [robot, zone]}
                   for robot, zone in sorted(visits)]
    # Assembly contract: when several parts share one final zone, the single
    # count predicate replaces the per-part is_at_zone entries.
    by_zone = {}
    for obj, zone in sorted(final_leg.items()):
        by_zone.setdefault(zone, []).append(obj)
    for zone, objs in sorted(by_zone.items()):
        if len(objs) > 1:
            for obj in objs:
                predicates.remove({"fn": "is_at_zone", "args": [obj, zone]})
            predicates.append({"fn": "all_at_zone", "args": [objs, zone, len(objs)]})
    return predicates


def _entity_position(data, ref):
    for entity in data["composition"]["entities"]:
        if entity["id"] == ref:
            return tuple(entity["spawn_pose"]["position"][:2])
    raise ScenarioError(f"predicate references unknown object '{ref}'")


def _zone_radius(data, ref):
    """Radius of a zone reference: declared targets, or an entity's half-diagonal."""
    for target in data["composition"]["targets"]:
        if target["id"] == ref:
            return target["radius_m"]
    for entity in data["composition"]["entities"]:
        if entity["id"] == ref:
            w, d, _ = entity["size_m"]
            return math.hypot(w, d) / 2
    raise ScenarioError(f"predicate references unknown zone '{ref}'")


def _target_position(data, ref):
    for target in data["composition"]["targets"]:
        if target["id"] == ref:
            return tuple(target["position"][:2]), target["radius_m"]
    # Entity-to-entity goals use the other entity's footprint as the zone.
    return _entity_position(data, ref), _zone_radius(data, ref)


def evaluate_predicate(data, predicate, positions=None):
    """Evaluate one predicate; `positions` defaults to the spawn state."""
    fn = predicate["fn"]
    if fn == "is_at_zone":
        (obj, zone) = predicate["args"]
        xy = (positions or {}).get(obj) or _entity_position(data, obj)
        (zx, zy), radius = _target_position(data, zone)
        return math.hypot(xy[0] - zx, xy[1] - zy) <= radius
    if fn == "robot_visited_zone":
        (robot, zone) = predicate["args"]
        xy = positions.get(robot)
        if xy is None:
            raise ScenarioError(f"predicate references unsampled robot '{robot}'")
        (zx, zy), radius = _target_position(data, zone)
        return math.hypot(xy[0] - zx, xy[1] - zy) <= radius
    if fn == "all_at_zone":
        refs, zone, count = predicate["args"]
        (zx, zy), radius = _target_position(data, zone)
        inside = 0
        for ref in refs:
            xy = (positions or {}).get(ref) or _entity_position(data, ref)
            if math.hypot(xy[0] - zx, xy[1] - zy) <= radius:
                inside += 1
        return inside >= count
    raise ScenarioError(f"unknown predicate '{fn}'")


def validate_evaluation(data):
    """Derive the success contract and reject trivially-satisfied tasks."""
    predicates = derive_success_predicates(data)
    spawn_positions = {entity["id"]: tuple(entity["spawn_pose"]["position"][:2])
                       for entity in data["composition"]["entities"]}
    spawn_positions.update({robot["id"]: tuple(robot["spawn_pose"]["position"][:2])
                            for robot in data["robots"]})
    step0 = {json_key(predicate): evaluate_predicate(data, predicate, positions=spawn_positions)
             for predicate in predicates}

    targets = {target["id"]: target for target in data["composition"]["targets"]}
    entities = {entity["id"]: entity for entity in data["composition"]["entities"]}
    trivial_legs = []
    for node in data["phase_graph"]["nodes"]:
        if node["skill_key"] != "navigate":
            continue
        robot = next((r for r in data["robots"] if r["id"] == node["preferred_agent"]), None)
        if robot is None:
            continue
        ref = node["target_ref"]
        if ref in targets:
            gx, gy = targets[ref]["position"][:2]
            radius = targets[ref]["radius_m"]
        elif ref in entities:
            gx, gy = entities[ref]["spawn_pose"]["position"][:2]
            radius = _zone_radius(data, ref)
        else:
            continue
        sx, sy = robot["spawn_pose"]["position"][:2]
        if math.hypot(sx - gx, sy - gy) <= radius:
            trivial_legs.append(f"{node['id']}:{robot['id']}")

    if trivial_legs or any(step0.values()):
        raise ScenarioError(
            f"trivial task rejected: goal already satisfied at step 0 "
            f"(predicates={step0}, degenerate navigate legs={trivial_legs})")

    return {
        "status": "evaluation_validated",
        "predicates": predicates,
        "step0": step0,
        "runtime_evaluator": "state-dependent predicates; positions supplied per step by the "
                             "future evaluator, spawn state used for the step-0 check",
    }


def json_key(predicate):
    return f"{predicate['fn']}({','.join(map(str, predicate['args']))})"
