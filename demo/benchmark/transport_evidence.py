"""Ordered evidence for an interrupted idealized joint carry and recovery.

Scheduler recovery alone cannot establish that a dropped object was recovered.
This validates logical ownership and measured positions, never a physical grasp.
"""

import math


def joint_recovery_evidence(entry, plan, events, recovered_by):
    node, cargo = entry["node"], entry.get("object")
    team = list(entry.get("team") or entry.get("agents") or ())
    violations = []
    replacements = recovered_by.get(node, [])
    candidates = [row for row in plan if row["node"] in replacements
                  and row.get("recovery_for") == node and row.get("object") == cargo
                  and row.get("skill") == "transport_object"]
    if len(candidates) != 1 or len(replacements) != 1:
        return [f"{node}: recovery requires one completed same-object transport replacement"]
    recovery = candidates[0]
    actor = recovery["agent"]
    cursor = -1

    def take(kind, target_node):
        nonlocal cursor
        for index in range(cursor + 1, len(events)):
            event = events[index]
            if event.get("event") == kind and event.get("node") == target_node:
                cursor = index
                return event
        violations.append(f"{node}: missing ordered {kind} for {target_node}")
        return {}

    pickup = take("pickup", node)
    drop = take("forced_release", node)
    fault = next((event for event in events[cursor + 1:]
                  if event.get("event") == "member_exit"
                  and event.get("fault_id") == drop.get("fault_id")
                  and node in event.get("affected_nodes", [])), None)
    if (not fault or fault.get("agent") not in team or actor == fault.get("agent")):
        violations.append(f"{node}: missing member-exit evidence or unavailable recovery agent")
    elif fault is not None:
        cursor = next(index for index in range(cursor + 1, len(events)) if events[index] is fault)
    rebuilt = take("participant_transport_routes_rebuilt", recovery["node"])
    recovered = take("pickup", recovery["node"])
    release = take("release", recovery["node"])
    complete = take("node_complete", recovery["node"])

    def owners(event, before, after):
        return event.get("owner_before") == before and event.get("owner_after") == after

    if not owners(pickup, None, team) or pickup.get("agents") != team:
        violations.append(f"{node}: invalid joint pickup ownership")
    if not owners(drop, team, None) or not drop.get("fault_id"):
        violations.append(f"{node}: invalid forced-release ownership")
    if not owners(recovered, None, actor) or not owners(release, actor, None):
        violations.append(f"{node}: invalid recovery ownership")
    for event in (pickup, drop, recovered, release):
        if event.get("object") != cargo:
            violations.append(f"{node}: recovery object mismatch")
    if any(event.get("agent") != actor for event in (recovered, release, complete)):
        violations.append(f"{node}: recovery actor mismatch")
    if rebuilt.get("team") != [actor]:
        violations.append(f"{node}: recovery route is not bound to replacement")

    def same_position(left, right):
        try:
            a, b = tuple(left[:2]), tuple(right[:2])
            return (len(a) == len(b) == 2 and all(math.isfinite(x) for x in a + b)
                    and math.dist(a, b) <= .05)
        except (TypeError, ValueError):
            return False

    if not same_position(drop.get("position"), rebuilt.get("pickup_position_m")):
        violations.append(f"{node}: recovery route does not start at the drop")
    # A released dynamic body may settle or slide. Require measured proximity
    # to its actual pickup pose, rather than pretending the initial drop is fixed.
    try:
        measured_distance = math.dist(recovered["position"][:2], recovered["object_position"][:2])
        recorded_distance = recovered["interaction_distance_m"]
        standoff = recovered["interaction_standoff_m"]
        reachable = (all(math.isfinite(value) for value in
                         (measured_distance, recorded_distance, standoff))
                     and 0 <= recorded_distance <= standoff and standoff > 0
                     and abs(measured_distance - recorded_distance) <= .003
                     and recovered.get("confirmed_after_sim_step") is True)
    except (KeyError, TypeError, ValueError):
        reachable = False
    if not reachable:
        violations.append(f"{node}: recovery pickup lacks measured live-object proximity")
    if (not same_position(release.get("position"), entry.get("goal"))
            or not same_position(recovery.get("goal"), entry.get("goal"))
            or release.get("confirmed_after_sim_step") is not True):
        violations.append(f"{node}: recovery delivery lacks measured goal confirmation")
    return violations
