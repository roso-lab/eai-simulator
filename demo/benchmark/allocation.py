"""Explainable task-to-fleet allocation primitives.

Tasks express requirements and the fleet expresses available configuration
instances.  Robot ids are used only in the returned assignment, never as task
requirements.  This baseline is intentionally deterministic and is a control
condition for later optimizers.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
from itertools import combinations
import json
import math
from typing import Any, Mapping


def _profile_supports(profile: Mapping[str, Any], requirement: Mapping[str, Any]) -> tuple[bool, list[str]]:
    constraints = profile.get("hard_constraints") or {}
    reasons = []
    domain = requirement.get("domain")
    if domain is not None and constraints.get("domain") != domain:
        reasons.append(f"domain:{constraints.get('domain')} != {domain}")
    if requirement.get("navigation") and not constraints.get("supports_navigation"):
        reasons.append("navigation_unverified")
    if requirement.get("aerial_scan") and not constraints.get("supports_aerial_scan"):
        reasons.append("aerial_scan_unverified")
    sensors = constraints.get("sensor_capabilities") or {}
    for sensor in requirement.get("sensors", ()):
        if sensors.get(sensor) is not True:
            reasons.append(f"sensor:{sensor}_unverified")
    attachments = set(constraints.get("attachments") or ())
    for attachment in requirement.get("attachments", ()):
        if attachment not in attachments:
            reasons.append(f"attachment:{attachment}_unavailable")
    if requirement.get("measured_axes"):
        measured = {axis["axis"] for axis in profile.get("radar", [])
                    if axis.get("status") == "measured"}
        for axis in requirement["measured_axes"]:
            if axis not in measured:
                reasons.append(f"radar:{axis}_unknown")
    for required in requirement.get("required_protocols", ()):
        protocol_id = required["protocol_id"]
        scope = required.get("scope")
        if not isinstance(scope, Mapping) or not scope:
            reasons.append(f"protocol:{protocol_id}_scope_missing")
            continue
        matches = [check for check in profile.get("protocol_checks", ())
                   if check.get("protocol_id") == protocol_id and check.get("status") == "passed"
                   and check.get("evidence_tier") == "E6" and check.get("scope") == scope
                   and check.get("configuration_id") == profile.get("identity", {}).get("configuration_id")]
        if not matches:
            reasons.append(f"protocol:{protocol_id}_scope_unverified")
    required_tier = requirement.get("required_evidence_tier")
    if required_tier is not None:
        order = {f"E{i}": i for i in range(8)}
        if required_tier not in order:
            raise ValueError(f"unsupported required evidence tier {required_tier!r}")
        actual_tier = constraints.get("passing_evidence_tier", "E0")
        if order.get(actual_tier, -1) < order[required_tier]:
            reasons.append(f"evidence:{actual_tier}_below_{required_tier}")
    return not reasons, reasons


def eligible_fleet(fleet: list[Mapping[str, Any]], profiles: Mapping[str, Mapping[str, Any]],
                   requirement: Mapping[str, Any]) -> tuple[list[Mapping[str, Any]], list[dict[str, Any]]]:
    eligible = []
    rejected = []
    for robot in fleet:
        identifier = robot.get("id")
        profile = profiles.get(robot.get("profile_key") or robot.get("robot_key"))
        reasons = []
        if robot.get("available", True) is not True:
            reasons.append("unavailable")
        if profile is None:
            reasons.append("missing_profile")
        else:
            supported, capability_reasons = _profile_supports(profile, requirement)
            if not supported:
                reasons.extend(capability_reasons)
        if reasons:
            rejected.append({"robot_id": identifier, "reasons": reasons})
        else:
            eligible.append(robot)
    return eligible, rejected


def enumerate_teams(fleet: list[Mapping[str, Any]], profiles: Mapping[str, Mapping[str, Any]],
                    requirement: Mapping[str, Any]) -> tuple[list[list[str]], list[dict[str, Any]]]:
    size = requirement.get("team_size", 1)
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise ValueError("team_size must be positive")
    eligible, rejected = eligible_fleet(fleet, profiles, requirement)
    teams = []
    for members in combinations(eligible, size):
        if not requirement.get("allow_same_type", True) and len({item.get("robot_key") for item in members}) < size:
            continue
        teams.append([item["id"] for item in members])
    return teams, rejected


def _canonical_requirements(requirements):
    canonical_requirements = []
    for requirement in requirements:
        item = dict(requirement)
        if "shared_resources" in item:
            if "resources" in item and item["resources"] != item["shared_resources"]:
                raise ValueError(f"{item.get('id')}: resources and shared_resources disagree")
            item["resources"] = item.pop("shared_resources")
        canonical_requirements.append(item)
    by_id = {item.get("id"): item for item in canonical_requirements}
    if len(by_id) != len(requirements) or any(not isinstance(identifier, str) or not identifier for identifier in by_id):
        raise ValueError("requirement ids must be distinct nonempty strings")
    pending = dict(by_id)
    ordered, ancestors = [], {}
    while pending:
        ready = [identifier for identifier, item in pending.items()
                 if set(item.get("prerequisites", ())) <= ancestors.keys()]
        if not ready:
            raise ValueError("requirement prerequisites contain a cycle or unknown id")
        for identifier in ready:
            item = pending.pop(identifier)
            dependencies = set(item.get("prerequisites", ()))
            ancestors[identifier] = dependencies | set().union(*(ancestors[dep] for dep in dependencies))
            same = item.get("same_team_as")
            if same is not None and same not in ancestors[identifier]:
                raise ValueError(f"{identifier}: same_team_as must name a preceding requirement")
            if set(item.get("different_team_from", ())) - ancestors[identifier]:
                raise ValueError(f"{identifier}: different_team_from must name preceding requirements")
            ordered.append(item)
    return canonical_requirements, ordered, ancestors


def _allocate(requirements, fleet, profiles, *, method, fixed_assignments=None,
              estimated_costs=None, max_candidate_evaluations=10000, unavailable_assignments=None):
    canonical, ordered, ancestors = _canonical_requirements(requirements)
    if (isinstance(max_candidate_evaluations, bool) or not isinstance(max_candidate_evaluations, int)
            or max_candidate_evaluations < 1):
        raise ValueError("max_candidate_evaluations must be a positive integer")
    fleet_ids = [robot.get("id") for robot in fleet]
    if len(set(fleet_ids)) != len(fleet_ids) or any(not isinstance(identifier, str) or not identifier for identifier in fleet_ids):
        raise ValueError("fleet ids must be distinct nonempty strings")
    unavailable = unavailable_assignments or {}
    if set(unavailable) - {item["id"] for item in canonical}:
        raise ValueError("unavailable assignments contain an unknown requirement")
    for rejected in unavailable.values():
        if (not isinstance(rejected, Mapping) or set(rejected) - set(fleet_ids)
                or any(not isinstance(reason, str) or not reason for reason in rejected.values())):
            raise ValueError("unavailable assignments require known robots and explicit reasons")
    candidates, rejections, team_costs = {}, {}, {}
    for requirement in ordered:
        identifier = requirement["id"]
        teams, rejected = enumerate_teams(fleet, profiles, requirement)
        excluded = unavailable.get(identifier, {})
        teams = [team for team in teams if not set(team) & excluded.keys()]
        rejected.extend({"robot_id": agent, "reasons": [reason]} for agent, reason in sorted(excluded.items()))
        candidates[identifier] = sorted(tuple(sorted(team)) for team in teams)
        rejections[identifier] = rejected
        for team in candidates[identifier]:
            values = [1.0] * len(team) if estimated_costs is None else [
                estimated_costs.get(identifier, {}).get(agent) for agent in team
            ]
            if any(isinstance(value, bool) or not isinstance(value, (float, int))
                   or not math.isfinite(value) or value < 0 for value in values):
                raise ValueError(f"{identifier}:{team}: finite nonnegative estimated costs are required")
            team_costs[identifier, team] = sum(values)
    if fixed_assignments is not None and set(fixed_assignments) != {item["id"] for item in ordered}:
        raise ValueError("fixed assignments must cover exactly the requirement ids")

    def compatible(requirement, team, assigned):
        identifier = requirement["id"]
        same = requirement.get("same_team_as")
        if same is not None and tuple(assigned.get(same, ())) != team:
            return False
        if any(set(team) & set(assigned.get(other, ())) for other in requirement.get("different_team_from", ())):
            return False
        return all(not set(team) & set(other_team) or other in ancestors[identifier]
                   or identifier in ancestors[other] for other, other_team in assigned.items())

    def objective(assigned):
        cost = sum(team_costs[identifier, team] for identifier, team in assigned.items())
        count = len({agent for team in assigned.values() for agent in team})
        return cost, count, tuple(sorted(assigned.items()))

    evaluated, limited = 0, False
    incumbent, best_partial = None, {}

    def search(index, assigned):
        nonlocal evaluated, limited, incumbent, best_partial
        if len(assigned) > len(best_partial):
            best_partial = dict(assigned)
        if index == len(ordered):
            if incumbent is None or objective(assigned) < objective(incumbent):
                incumbent = dict(assigned)
            return
        requirement = ordered[index]
        identifier = requirement["id"]
        options = candidates[identifier]
        if method == "fixed":
            options = [tuple(sorted(fixed_assignments[identifier]))]
        options = sorted(options, key=lambda team: (team_costs.get((identifier, team), math.inf), team))
        for team in options:
            if evaluated >= max_candidate_evaluations:
                limited = True
                return
            evaluated += 1
            if team not in candidates[identifier] or not compatible(requirement, team, assigned):
                continue
            assigned[identifier] = team
            search(index + 1, assigned)
            del assigned[identifier]
            if method != "optimized" or limited:
                return

    search(0, {})
    selected = incumbent if incumbent is not None else best_partial
    assignments = [{"requirement_id": identifier, "agents": list(team), "team_size": len(team),
                    "allocation_method": method} for identifier, team in selected.items()]
    return {
        "schema": "eai.assignment_plan",
        "version": 1,
        "status": "feasible" if incumbent is not None else "unknown" if limited else "infeasible",
        "method": method,
        "requirements": canonical,
        "assignments": assignments,
        "decisions": [{"requirement_id": item["id"], "candidate_teams": [list(team) for team in candidates[item["id"]]],
                       "rejected_robots": rejections[item["id"]], "selected": list(selected.get(item["id"], ())),
                       "status": "assigned" if item["id"] in selected else "unassigned"} for item in ordered],
        "unassigned_requirements": [item["id"] for item in canonical if item["id"] not in selected],
        "candidate_evaluations": evaluated,
        "candidate_evaluation_budget": max_candidate_evaluations,
        "budget_exhausted": limited,
        "optimality_proven": method == "optimized" and incumbent is not None and not limited,
        "infeasibility_proven": method == "optimized" and incumbent is None and not limited,
        "objective": {"cost": objective(incumbent)[0], "distinct_robot_count": objective(incumbent)[1]}
        if incumbent is not None else None,
        "cost_model": "caller_supplied_estimate" if estimated_costs is not None else "unit_assignment_count",
        "limitations": [
            "precedence permits robot reuse; unordered phases reserve disjoint teams",
            "optimality applies only to the finite candidate set and declared cost model",
            "wall time, physical execution and dynamic travel costs are not predicted here",
            "unknown radar axes are ineligible only when explicitly required",
            "payload and manipulation physics are not inferred by this allocator",
        ],
    }


def greedy_allocate(requirements, fleet, profiles, *, estimated_costs=None, max_candidate_evaluations=10000):
    return _allocate(requirements, fleet, profiles, method="greedy", estimated_costs=estimated_costs,
                     max_candidate_evaluations=max_candidate_evaluations)


def fixed_allocate(requirements, fleet, profiles, fixed_assignments, *, estimated_costs=None,
                   max_candidate_evaluations=10000):
    return _allocate(requirements, fleet, profiles, method="fixed", fixed_assignments=fixed_assignments,
                     estimated_costs=estimated_costs, max_candidate_evaluations=max_candidate_evaluations)


def optimized_allocate(requirements, fleet, profiles, *, estimated_costs=None, max_candidate_evaluations=10000):
    return _allocate(requirements, fleet, profiles, method="optimized", estimated_costs=estimated_costs,
                     max_candidate_evaluations=max_candidate_evaluations)


def compare_allocators(requirements, fleet, profiles, fixed_assignments, *, estimated_costs=None,
                       max_candidate_evaluations=10000, unavailable_assignments=None):
    inputs = {"requirements": requirements, "fleet": fleet, "profiles": profiles,
              "fixed_assignments": fixed_assignments, "estimated_costs": estimated_costs,
              "max_candidate_evaluations": max_candidate_evaluations,
              "unavailable_assignments": unavailable_assignments}
    digest = hashlib.sha256(json.dumps(inputs, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return {"schema": "eai.allocation_comparison", "version": 1, "input_sha256": digest,
            "methods": {method: _allocate(requirements, fleet, profiles, method=method,
                                           fixed_assignments=fixed_assignments if method == "fixed" else None,
                                           estimated_costs=estimated_costs,
                                           unavailable_assignments=unavailable_assignments,
                                           max_candidate_evaluations=max_candidate_evaluations)
                        for method in ("fixed", "greedy", "optimized")},
            "scope": "offline allocation under shared eligibility, precedence and candidate budgets"}


def reallocation_certificate(requirements, fleet, profiles, *, max_candidate_evaluations=10000):
    """Exhaustively test single-agent and homogeneous alternatives after removal.

    This certificate keeps the task requirements fixed while allowing every
    phase to be reassigned.  It proves only the declared eligibility,
    precedence and unordered-phase reservation model; it says nothing about
    physical manipulation, timing beyond the encoded graph, or hardware
    capability.
    """
    canonical, ordered, ancestors = _canonical_requirements(requirements)
    if (isinstance(max_candidate_evaluations, bool)
            or not isinstance(max_candidate_evaluations, int)
            or max_candidate_evaluations < 1):
        raise ValueError("max_candidate_evaluations must be a positive integer")
    candidates, rejections = {}, {}
    for requirement in ordered:
        teams, rejected = enumerate_teams(fleet, profiles, requirement)
        candidates[requirement["id"]] = sorted(tuple(sorted(team)) for team in teams)
        rejections[requirement["id"]] = rejected

    evaluated = 0
    limited = False
    feasible = []

    def compatible(requirement, team, assigned):
        identifier = requirement["id"]
        same = requirement.get("same_team_as")
        if same is not None and tuple(assigned.get(same, ())) != team:
            return False
        if any(set(team) & set(assigned.get(other, ()))
               for other in requirement.get("different_team_from", ())):
            return False
        for other, other_team in assigned.items():
            if set(team) & set(other_team) and other not in ancestors[identifier] \
                    and identifier not in ancestors[other]:
                return False
        return True

    def search(index, assigned):
        nonlocal evaluated, limited
        if index == len(ordered):
            feasible.append({key: list(value) for key, value in assigned.items()})
            return
        requirement = ordered[index]
        for team in candidates[requirement["id"]]:
            if evaluated >= max_candidate_evaluations:
                limited = True
                return
            evaluated += 1
            if compatible(requirement, team, assigned):
                assigned[requirement["id"]] = team
                search(index + 1, assigned)
                del assigned[requirement["id"]]
                if limited:
                    return

    search(0, {})
    assignments = []
    for assignment in feasible:
        members = sorted({agent for team in assignment.values() for agent in team})
        assignments.append({
            "assignments": assignment,
            "distinct_robot_count": len(members),
            "robot_types": sorted({next(robot["robot_key"] for robot in fleet
                                          if robot["id"] == agent) for agent in members}),
        })
    minimum = min((item["distinct_robot_count"] for item in assignments), default=None)
    minimum_assignments = [item for item in assignments if item["distinct_robot_count"] == minimum]
    single = [item for item in minimum_assignments if item["distinct_robot_count"] == 1]
    homogeneous = [item for item in minimum_assignments if len(item["robot_types"]) == 1]
    return {
        "schema": "eai.reallocation_certificate", "version": 1,
        "status": "unknown" if limited else "feasible" if assignments else "infeasible",
        "requirements": canonical, "candidate_rejections": rejections,
        "candidate_evaluations": evaluated, "candidate_evaluation_budget": max_candidate_evaluations,
        "budget_exhausted": limited, "infeasibility_proven": not limited and not assignments,
        "minimum_distinct_robot_count": minimum,
        "minimum_assignments": minimum_assignments,
        "single_agent_feasible": bool(single),
        "homogeneous_alternative_feasible": bool(homogeneous),
        "heterogeneity_necessary": bool(minimum_assignments)
        and not single and not homogeneous and not limited,
        "interpretation": [
            "Robot assignments are re-planned over the unchanged task requirements.",
            "Unordered phases reserve disjoint teams; prerequisite phases may reuse a team.",
            "A positive necessity result is a finite allocation certificate, not a physical or hardware result.",
        ],
    }


def compile_assignment_plan(data: Mapping[str, Any], assignment_plan: Mapping[str, Any]) -> dict[str, Any]:
    """Freeze an allocation into the V2 scenario fields consumed by runtime.

    The allocator may reason over abstract requirement records, but a replayable
    execution must contain an explicit agent for every phase node.  This
    compiler is deliberately strict so an incomplete allocation cannot become a
    partial, falsely scored rollout.
    """
    if assignment_plan.get("schema") != "eai.assignment_plan":
        raise ValueError("unsupported assignment plan schema")
    if assignment_plan.get("status") != "feasible":
        raise ValueError("only a feasible assignment plan can be compiled")
    assignments = assignment_plan.get("assignments") or []
    by_requirement = {item.get("requirement_id"): item for item in assignments}
    if len(by_requirement) != len(assignments):
        raise ValueError("assignment plan contains duplicate requirement ids")
    compiled = deepcopy(data)
    nodes = compiled.get("phase_graph", {}).get("nodes", [])
    robot_ids = {robot.get("id") for robot in compiled.get("robots", [])}
    for node in nodes:
        identifier = node["id"]
        assignment = by_requirement.get(identifier)
        if assignment is None:
            raise ValueError(f"assignment plan has no assignment for phase node '{identifier}'")
        agents = list(assignment.get("agents") or [])
        if not agents or any(agent not in robot_ids for agent in agents):
            raise ValueError(f"{identifier}: assignment contains an unknown or empty agent set")
        if node["skill_key"] == "joint_transport":
            if len(agents) != 2 or len(set(agents)) != 2:
                raise ValueError(f"{identifier}: joint_transport requires two distinct agents")
            node["agents"] = agents
        elif len(agents) != 1:
            raise ValueError(f"{identifier}: non-joint phase requires exactly one agent")
        else:
            node["agents"] = None
        node["preferred_agent"] = agents[0]
        requirement = next((item for item in assignment_plan.get("requirements", [])
                            if item.get("id") == identifier), None)
        if requirement is not None:
            if "prerequisites" in requirement and set(requirement["prerequisites"]) != set(node["prerequisites"]):
                raise ValueError(f"{identifier}: assignment prerequisites disagree with the phase graph")
            same_team_as = requirement.get("same_team_as")
            if same_team_as is not None and set(agents) != set(by_requirement.get(same_team_as, {}).get("agents", ())):
                raise ValueError(f"{identifier}: same_team_as binding is not satisfied")
            if any(set(agents) & set(by_requirement.get(other, {}).get("agents", ()))
                   for other in requirement.get("different_team_from", ())):
                raise ValueError(f"{identifier}: different_team_from binding is not satisfied")
            resources = requirement.get("resources")
            shared_resources = requirement.get("shared_resources")
            if resources is not None and shared_resources is not None and resources != shared_resources:
                raise ValueError(f"{identifier}: resources and shared_resources disagree")
            if resources is None:
                resources = shared_resources
            if resources and not node.get("resources"):
                node["resources"] = list(resources)
    assigned_ids = set(by_requirement)
    node_ids = {node["id"] for node in nodes}
    extra = sorted(assigned_ids - node_ids)
    if extra:
        raise ValueError(f"assignment plan refers to unknown phase nodes: {extra}")
    return compiled


__all__ = ["compile_assignment_plan", "eligible_fleet", "enumerate_teams", "greedy_allocate",
           "fixed_allocate", "optimized_allocate", "compare_allocators"]
