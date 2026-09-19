"""Local capability screens for goal-driven benchmark authors.

Every object handling option comes from ``goal_constraints.transport_options``.
No witness plan, witness task assignment or business-outcome dependency enters
the calculation.  A surviving handling option is not a complete recovery plan:
object state, approach routes, resource occupancy, time and information can
still prevent an episode from finishing.

``homogeneous_feasible`` means equal declared robot ``type`` values, not equal
mobility and not identical hardware configurations.  Single-carrier options
are homogeneous under this definition.  These are benchmark declarations,
not claims about the capabilities of a physical robot model.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from .goal_constraints import transport_options
from .provenance import canonical_sha256


CAPABILITY_AUDIT_SCHEMA = "eai.benchmark.goal_capability_audit"
_CRITICAL_CAPABILITIES = ("pick", "stair_traversal")


def _robot_index(task_spec: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    pool = task_spec.get("candidate_robot_pool")
    if not isinstance(pool, list):
        raise ValueError("capability audit requires a candidate_robot_pool list")
    rows: dict[str, Mapping[str, Any]] = {}
    for robot in pool:
        if not isinstance(robot, Mapping):
            raise ValueError("capability audit robot must be an object")
        for name in ("id", "type", "mobility"):
            if not isinstance(robot.get(name), str) or not robot[name]:
                raise ValueError(f"capability audit robot requires a nonempty {name}")
        if robot["id"] in rows:
            raise ValueError("capability audit robot IDs must be unique")
        capabilities = robot.get("public_capabilities")
        if (not isinstance(capabilities, list)
                or any(not isinstance(value, str) or not value for value in capabilities)):
            raise ValueError("capability audit robot requires public_capabilities")
        rows[robot["id"]] = robot
    return rows


def _handling_objects(task_spec: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    world = task_spec.get("public_world")
    if not isinstance(world, Mapping) or not isinstance(world.get("objects"), list):
        raise ValueError("capability audit requires public_world.objects")
    screened: list[str] = []
    undeclared: list[str] = []
    seen: set[str] = set()
    for obj in world["objects"]:
        if not isinstance(obj, Mapping) or not isinstance(obj.get("id"), str) or not obj["id"]:
            raise ValueError("capability audit object requires a nonempty id")
        object_id = obj["id"]
        if object_id in seen:
            raise ValueError("capability audit object IDs must be unique")
        seen.add(object_id)
        physical = obj.get("physical_properties")
        if physical is None:
            undeclared.append(object_id)
            continue
        if not isinstance(physical, Mapping):
            raise ValueError(f"{object_id}: physical_properties must be an object")
        if "handling_modes" not in physical:
            undeclared.append(object_id)
        elif not isinstance(physical["handling_modes"], list):
            raise ValueError(f"{object_id}: handling_modes must be a list")
        else:
            screened.append(object_id)
    return sorted(screened), sorted(undeclared)


def _object_screen(task_spec: Mapping[str, Any], object_id: str,
                   robots: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    # Do not convert dependency errors into an empty (apparently infeasible)
    # result, and do not infer surviving options by pruning a fixed witness.
    options = transport_options(task_spec, object_id, robot_rows=robots)
    if not isinstance(options, list):
        raise ValueError("transport_options must return a list of feasible options")
    annotated = []
    for option in options:
        if not isinstance(option, Mapping):
            raise ValueError("transport_options option must be an object")
        agents = option.get("assigned_agents")
        if (not isinstance(agents, list) or not agents
                or any(not isinstance(agent, str) for agent in agents)
                or len(agents) != len(set(agents))
                or not set(agents) <= set(robots)):
            raise ValueError("transport_options option must assign unique available assigned_agents")
        types = sorted({robots[agent]["type"] for agent in agents})
        mobilities = sorted({robots[agent]["mobility"] for agent in agents})
        annotated.append({
            "option": deepcopy(dict(option)),
            "carrier_count": len(agents),
            "robot_types": types,
            "mobilities": mobilities,
            "homogeneous": len(types) == 1,
        })
    annotated.sort(key=canonical_sha256)
    return {
        "object_id": object_id,
        "status": "feasible" if annotated else "no_declared_feasible_option",
        "feasible_options": annotated,
        "min_carriers": min((row["carrier_count"] for row in annotated), default=None),
        "homogeneous_feasible": any(row["homogeneous"] for row in annotated),
        "heterogeneous_feasible": any(not row["homogeneous"] for row in annotated),
        "cross_mobility_feasible": any(len(row["mobilities"]) > 1 for row in annotated),
    }


def _screen(task_spec: Mapping[str, Any], object_ids: list[str],
            robots: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "available_agent_ids": sorted(robots),
        "capability_supply": {
            capability: sorted(agent for agent, robot in robots.items()
                               if capability in robot["public_capabilities"])
            for capability in _CRITICAL_CAPABILITIES
        },
        "object_handling": [_object_screen(task_spec, object_id, robots) for object_id in object_ids],
    }


def _compare(baseline: Mapping[str, Any], remaining: Mapping[str, Any]) -> dict[str, Any]:
    originally_feasible = {
        row["object_id"] for row in baseline["object_handling"] if row["status"] == "feasible"
    }
    return {
        "remaining_supply": remaining,
        "lost_handling_object_ids": [
            row["object_id"] for row in remaining["object_handling"]
            if row["object_id"] in originally_feasible and row["status"] != "feasible"
        ],
        "lost_capabilities": [
            capability for capability in _CRITICAL_CAPABILITIES
            if baseline["capability_supply"][capability]
            and not remaining["capability_supply"][capability]
        ],
        "task_recovery_validated": False,
    }


def build_capability_audit(task_spec: Mapping[str, Any]) -> dict[str, Any]:
    """Enumerate local handling/capability supply under declared removals.

    The full candidate pool is screened, not a chosen or witness team.  Each
    removal re-enumerates all declared handling modes and member assignments.
    Capabilities are removed from one member at a time, without inventing
    payload or geometric changes that were not supplied by the task author.

    Missing handling declarations are reported as unscreened.  Invalid
    declarations or a failing shared constraints dependency raise an error;
    they never become a proof of impossibility or recovery.
    """
    if not isinstance(task_spec, Mapping):
        raise ValueError("capability audit task_spec must be an object")
    spec = deepcopy(dict(task_spec))
    robots = _robot_index(spec)
    object_ids, unscreened = _handling_objects(spec)
    baseline = _screen(spec, object_ids, robots)
    agent_removals = []
    for agent_id in sorted(robots):
        reduced = {key: robot for key, robot in robots.items() if key != agent_id}
        agent_removals.append({
            "removed_agent_id": agent_id,
            **_compare(baseline, _screen(spec, object_ids, reduced)),
        })
    mobility_removals = []
    for mobility in sorted({robot["mobility"] for robot in robots.values()}):
        removed_ids = sorted(agent for agent, robot in robots.items() if robot["mobility"] == mobility)
        reduced = {agent: robot for agent, robot in robots.items() if robot["mobility"] != mobility}
        mobility_removals.append({
            "removed_mobility": mobility,
            "removed_agent_ids": removed_ids,
            **_compare(baseline, _screen(spec, object_ids, reduced)),
        })
    capability_changes = []
    for agent_id in sorted(robots):
        for capability in _CRITICAL_CAPABILITIES:
            if capability not in robots[agent_id]["public_capabilities"]:
                continue
            changed = deepcopy(robots)
            robot = changed[agent_id]
            robot["public_capabilities"] = [
                value for value in robot["public_capabilities"] if value != capability
            ]
            capability_changes.append({
                "agent_id": agent_id,
                "removed_capability": capability,
                **_compare(baseline, _screen(spec, object_ids, changed)),
            })
    return {
        "schema": CAPABILITY_AUDIT_SCHEMA,
        "version": 1,
        "visibility": "author_evaluator_only",
        "task_id": spec.get("task_id"),
        "public_task_sha256": canonical_sha256(task_spec),
        "basis": "all_declared_handling_modes_and_member_assignments",
        "scope": "local_object_handling_and_declared_capability_supply",
        "team_scope": "public_candidate_pool_not_selected_team",
        "homogeneity_basis": "declared_robot_type",
        "screened_object_ids": object_ids,
        "unscreened_objects": [
            {"object_id": object_id, "reason": "handling_modes_not_declared"}
            for object_id in unscreened
        ],
        "baseline": baseline,
        "agent_removals": agent_removals,
        "mobility_removals": mobility_removals,
        "capability_changes": capability_changes,
        "task_feasibility_validated": False,
        "task_recovery_validated": False,
        "heterogeneity_necessity_validated": False,
        "execution_validated": False,
        "limitations": [
            "Independent object options do not prove one team can satisfy all business outcomes.",
            "An object with no handling option may be stationary; this alone does not invalidate a business goal.",
            "Alternative assignments do not prove recovery from the current object or robot state.",
            "Approach routes, occupied resources, time, budgets and information availability need separate review.",
            "Removing a capability changes that declaration only; it does not invent other physical changes.",
            "No witness-plan failure or sampled-plan failure establishes whole-task impossibility.",
            "Equal robot type labels do not establish identical hardware configurations or calibration.",
        ],
    }


__all__ = ["CAPABILITY_AUDIT_SCHEMA", "build_capability_audit"]
