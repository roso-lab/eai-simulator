"""Public Factory dispatch rules and a timed sensor-to-dispatcher channel."""

from copy import deepcopy
import math

from demo.benchmark.marker_detection import fuse_marker_views
from demo.benchmark.provenance import canonical_sha256


DISPATCH_NODE = "route_confirmation"
VIEW_NODES = ("aerial_view", "ground_view")
MODES = ("shared", "withheld", "delayed")
PUBLIC_RULES = {
    "schema": "eai.factory_inventory_dispatch", "version": 2,
    "destinations_by_marker_id": {"17": "assembly_site", "23": "dock_site"},
    "fallback_target_ref": "assembly_site", "message_valid_for_s": 120.0,
    "destinations": {
        "assembly_site": {"position": [5.55, 3.4, 0.0], "radius_m": 0.5},
        "dock_site": {"position": [-1.95, 3.4, 0.0], "radius_m": 0.5},
    },
    "policy": "one distinct known decoded ID among available messages; otherwise fallback",
    "decision": "commit once under the selected public policy; late messages do not change a committed route",
    "operation": "dispatch to a Factory observation station; no object delivery or equipment operation",
}


def _time(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("dispatch times must be finite and nonnegative")
    return float(value)


def validate_dispatch_task(scenario):
    from demo.factory_benchmark.tasks import validate_factory_scenario

    validate_factory_scenario(scenario, "F-INS-01")
    nodes = {node["id"]: node for node in scenario["phase_graph"]["nodes"]}
    if set(nodes) != {*VIEW_NODES, "ground_approach", DISPATCH_NODE} or scenario.get("fault_events"):
        raise ValueError("dispatch baseline requires the four inspection stages without member faults")
    dispatch = nodes[DISPATCH_NODE]
    if (dispatch["target_ref"] != PUBLIC_RULES["fallback_target_ref"]
            or dispatch.get("agents")
            or any(node["preferred_agent"] == dispatch["preferred_agent"]
                   for name, node in nodes.items() if name != DISPATCH_NODE)):
        raise ValueError("dispatch requires its fallback target and a separately assigned stationary recipient")
    if not {"midband", "assembly_east", "dock_north"} <= set(dispatch["resources"]):
        raise ValueError("dispatch baseline must reserve both candidate destination resources")
    targets = {target["id"]: target for target in scenario["composition"]["targets"]}
    for name, expected in PUBLIC_RULES["destinations"].items():
        actual = targets.get(name, {})
        if any(actual.get(key) != value for key, value in expected.items()):
            raise ValueError(f"dispatch destination {name} differs from the public Factory station")


def resolved_scenario(scenario, target_ref):
    if target_ref not in PUBLIC_RULES["destinations"]:
        raise ValueError("dispatch destination is not in the public rules")
    resolved = deepcopy(scenario)
    node = next(node for node in resolved["phase_graph"]["nodes"] if node["id"] == DISPATCH_NODE)
    node["target_ref"] = target_ref
    return resolved


def validate_dispatch_scene_bindings(scenario):
    from EAI.scenario_spec.geometry import ground_surface_exclusions
    from EAI_assets.asset_resolver import usd_root
    from demo.factory_benchmark.prim_bindings import audit_factory_prim_bindings

    bindings = audit_factory_prim_bindings(usd_root() / "scene/factory/factory.usd")
    if not bindings["validated"]:
        raise ValueError("dispatch requires current Factory geometry bindings")
    bounds = {item["id"]: item["world_bounds_m"] for item in bindings["bindings"]}
    for exclusion in ground_surface_exclusions(scenario):
        actual = bounds[exclusion["id"]]
        low_x, low_y, high_x, high_y = exclusion["aabb_xy_m"]
        if not (low_x <= actual["min"][0] <= actual["max"][0] <= high_x
                and low_y <= actual["min"][1] <= actual["max"][1] <= high_y):
            raise ValueError(f"dispatch exclusion does not enclose current {exclusion['id']} geometry")
    return bindings


def preflight_dispatch(scenario, occupancy, template):
    from EAI.scenario_spec.geometry import validate_connectivity, validate_geometry
    from demo.benchmark.runtime import build_plan

    validate_dispatch_task(scenario)
    routes = {}
    for target in PUBLIC_RULES["destinations"]:
        candidate = resolved_scenario(scenario, target)
        validate_geometry(candidate, occupancy)
        validate_connectivity(candidate, occupancy)
        plan = build_plan(candidate, occupancy, template)
        routes[target] = next(entry for entry in plan if entry["node"] == DISPATCH_NODE)
    return routes


def view_messages(frame_results, observations, *, node, published_at_s):
    published = _time(published_at_s)
    by_id = {observation["observation_id"]: observation for observation in observations}
    messages = []
    for estimate in fuse_marker_views(frame_results, observations):
        if estimate["view_node"] != node:
            continue
        if published < estimate["timestamp_end_s"]:
            raise ValueError("dispatch message cannot precede its source frames")
        producers = {by_id[identifier]["producer"] for identifier in estimate["source_observation_ids"]}
        if len(producers) != 1:
            raise ValueError("one view message must have exactly one producer")
        messages.append({
            "message_id": f"{node}:marker:{estimate['marker_id']}", "producer": next(iter(producers)),
            "published_at_s": published,
            "valid_until_s": estimate["timestamp_start_s"] + PUBLIC_RULES["message_valid_for_s"],
            "estimate": estimate,
        })
    return messages


def select_destination(messages):
    """Only delivered detection messages enter this policy; no scene or true ID."""
    identifiers = {str(message["estimate"]["marker_id"]) for message in messages}
    known = PUBLIC_RULES["destinations_by_marker_id"]
    informed = len(identifiers) == 1 and identifiers <= known.keys()
    return {"target_ref": known[next(iter(identifiers))] if informed else PUBLIC_RULES["fallback_target_ref"],
            "reason": "decoded_identity" if informed else "no_unambiguous_known_identity",
            "used_message_ids": sorted(message["message_id"] for message in messages) if informed else [],
            "available_message_ids": sorted(message["message_id"] for message in messages)}


def delivered_messages(messages, *, mode, delay_s, time_s):
    """Return participant-visible messages and evaluator-side channel state."""
    if mode not in MODES:
        raise ValueError("unknown dispatch information mode")
    now, delay = _time(time_s), _time(delay_s)
    identifiers = [message["message_id"] for message in messages]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("duplicate dispatch message")
    available, ledger = [], []
    for message in sorted(messages, key=lambda item: item["message_id"]):
        published, expiry = _time(message["published_at_s"]), _time(message["valid_until_s"])
        arrival = published + (delay if mode == "delayed" else 0.0)
        status = ("withheld" if mode == "withheld" else "not_yet_available" if arrival > now
                  else "expired" if expiry < now else "available")
        ledger.append({"message_id": message["message_id"], "status": status,
                       "available_at_s": None if mode == "withheld" else arrival})
        if status == "available":
            available.append(deepcopy(message))
    return available, ledger


def decide_dispatch(messages, *, mode, delay_s, time_s):
    now, delay = _time(time_s), _time(delay_s)
    available, ledger = delivered_messages(
        messages, mode=mode, delay_s=delay, time_s=now)
    return {**select_destination(available), "time_s": now, "mode": mode,
            "delay_s": delay, "channel_ledger": ledger}


def retarget_entry(entry, scenario, target_ref):
    if entry["node"] != DISPATCH_NODE or entry["skill"] != "navigate" or entry["phase"] != "wait":
        raise ValueError("dispatch can retarget only its unstarted navigation stage")
    target = next(target for target in scenario["composition"]["targets"] if target["id"] == target_ref)
    if target_ref not in PUBLIC_RULES["destinations"]:
        raise ValueError("undeclared dispatch destination")
    entry.update(target_ref=target_ref, observation_ref=target_ref, goal=tuple(target["position"]),
                 arrival_predicate={"fn": "robot_visited_zone", "args": [entry["agent"], target_ref]})


def dispatch_trajectory(scenario, samples, decision):
    from EAI.scenario_spec.evaluation import json_key
    from EAI.scenario_spec.runtime_evaluator import PredicateEvaluator

    effective = resolved_scenario(scenario, decision["target_ref"])
    agent = next(node["preferred_agent"] for node in effective["phase_graph"]["nodes"] if node["id"] == DISPATCH_NODE)
    predicate_key = json_key({"fn": "robot_visited_zone", "args": [agent, decision["target_ref"]]})
    evaluator = PredicateEvaluator(effective)
    trajectory = []
    for sample in samples:
        result = evaluator.trajectory_step(sample["step"], sample["positions"])
        if sample["step"] < decision["step"]:
            result["satisfied"][predicate_key] = False
            values = list(result["satisfied"].values())
            result["percent"] = round(100 * sum(values) / len(values), 1)
        trajectory.append(result)
    return effective, trajectory


def dispatch_report(messages, decision, *, participant=None):
    report = {"schema": "eai.factory_inventory_dispatch", "version": 3 if participant else 2,
            "public_rules": deepcopy(PUBLIC_RULES), "public_rules_sha256": canonical_sha256(PUBLIC_RULES),
            "messages": deepcopy(messages), "decision": deepcopy(decision),
            "policy_inputs": ("participant-visible events and public task rules only"
                              if participant else
                              "delivered unexpired fused detection messages and public rules only"),
            "intervention_scope": "sensor-to-dispatcher link; producer acquisition and inspection schedule unchanged",
            "business_task_validated": False}
    if participant is not None:
        report["participant"] = deepcopy(participant)
    return report


def dispatch_policy_decision(*args, **kwargs):
    """Compatibility wrapper for the reference baseline policy."""
    from demo.factory_benchmark.baseline_solver import dispatch_policy_decision as decide
    return decide(*args, **kwargs)
