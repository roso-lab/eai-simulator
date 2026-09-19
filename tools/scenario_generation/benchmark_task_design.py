"""Strong heterogeneous long-horizon task-design contract.

Capability eligibility alone can accept a short task.  This module adds the
benchmark-design requirements for the release candidates: several substantive
stages, a real prerequisite chain, and explicit information/force coupling.
It remains symbolic and does not claim physical execution.
"""

from __future__ import annotations

from typing import Any

from demo.benchmark.registry import SCENARIOS
STRONG_TASK_BLUEPRINTS = {key: scenario.TASK_BLUEPRINT for key, scenario in SCENARIOS.items()}

SCENE_TASK_PROFILES = {key: scenario.TASK_PROFILE for key, scenario in SCENARIOS.items()}


def _depth(nodes: list[dict[str, Any]]) -> int:
    by_id = {node.get("id"): node for node in nodes}
    memo: dict[str, int] = {}

    def visit(identifier: str, active: set[str]) -> int:
        if identifier in memo:
            return memo[identifier]
        if identifier in active:
            return 0
        node = by_id.get(identifier)
        if node is None:
            return 0
        deps = [dep for dep in node.get("prerequisites") or () if dep in by_id]
        value = 1 + max((visit(dep, active | {identifier}) for dep in deps), default=0)
        memo[identifier] = value
        return value

    return max((visit(node.get("id"), set()) for node in nodes), default=0)


def validate_long_horizon_task(data: dict[str, Any], *, scene_key: str | None = None) -> dict[str, Any]:
    """Return structured findings for the strong task-flow requirements."""
    scene = scene_key or data.get("scene_key")
    profile = SCENE_TASK_PROFILES.get(scene)
    if profile is None:
        return {"status": "fail", "eligible": False, "scene_key": scene,
                "findings": [{"check": "scene_profile", "status": "fail",
                              "message": f"no strong task profile for scene {scene!r}"}]}
    nodes = list(data.get("phase_graph", {}).get("nodes", ()))
    skills = {node.get("skill_key") for node in nodes}
    substantive = [node for node in nodes if node.get("skill_key") not in {"navigate", "wait"}]
    depth = _depth(nodes)
    findings = [
        {"check": "minimum_nodes", "status": "pass" if len(nodes) >= profile["minimum_nodes"] else "fail",
         "message": f"requires at least {profile['minimum_nodes']} phase nodes", "value": len(nodes)},
        {"check": "substantive_nodes", "status": "pass" if len(substantive) >= profile["minimum_substantive_nodes"] else "fail",
         "message": f"requires at least {profile['minimum_substantive_nodes']} substantive nodes", "value": len(substantive)},
        {"check": "dependency_depth", "status": "pass" if depth >= profile["minimum_dependency_depth"] else "fail",
         "message": f"requires prerequisite depth >= {profile['minimum_dependency_depth']}", "value": depth},
        {"check": "required_skills", "status": "pass" if profile["required_skills"] <= skills else "fail",
         "message": "requires information asymmetry and force/capacity coupling",
         "missing": sorted(profile["required_skills"] - skills)},
    ]
    # Aerial scan must feed a later node, and joint transport must have a
    # declared two-agent team. These checks prevent decorative skill nodes.
    scan_nodes = [node for node in nodes if node.get("skill_key") == "aerial_scan"]
    joint_nodes = [node for node in nodes if node.get("skill_key") == "joint_transport"]
    joint_objects = {node.get("object_ref") for node in joint_nodes if node.get("object_ref")}
    solo_transport_objects = {
        node.get("object_ref") for node in nodes
        if node.get("skill_key") == "transport_object" and node.get("object_ref")
    }
    payload_role_separation = not (joint_objects & solo_transport_objects)
    findings.append({"check": "payload_role_separation",
                     "status": "pass" if payload_role_separation else "fail",
                     "message": "solo transport must use a separate light object, or the joint cargo must remain team-bound"})
    scan_ids = {node.get("id") for node in scan_nodes}
    children: dict[str, set[str]] = {node.get("id"): set() for node in nodes}
    for node in nodes:
        for prerequisite in node.get("prerequisites") or ():
            if prerequisite in children:
                children[prerequisite].add(node.get("id"))
    downstream: set[str] = set()
    frontier = list(scan_ids)
    while frontier:
        current = frontier.pop()
        for child in children.get(current, ()):
            if child not in downstream:
                downstream.add(child)
                frontier.append(child)
    scan_consumed = bool(downstream)
    scan_agents = {node.get("preferred_agent") for node in scan_nodes if node.get("preferred_agent")}
    joint_agents = {agent for node in joint_nodes for agent in (node.get("agents") or ())}
    role_separation = bool(scan_agents) and scan_agents.isdisjoint(joint_agents)
    supported_aerial_types = {"cf2x", "iris", "pegasus"}
    aerial_type_ok = all(
        next((robot.get("type") for robot in data.get("robots", ())
              if robot.get("id") == node.get("preferred_agent")), None) in supported_aerial_types
        for node in scan_nodes
    ) if scan_nodes else False
    findings.append({"check": "information_dependency", "status": "pass" if scan_ids and scan_consumed else "fail",
                     "message": "aerial information must unlock a later phase"})
    findings.append({"check": "aerial_role", "status": "pass" if aerial_type_ok else "fail",
                     "message": "aerial_scan must use a connected EAI aerial configuration"})
    findings.append({"check": "role_separation", "status": "pass" if role_separation else "fail",
                     "message": "scout and joint carrier roles must be disjoint"})
    findings.append({"check": "joint_team_binding", "status": "pass" if any(
        len(set(node.get("agents") or ())) == 2 for node in joint_nodes) else "fail",
        "message": "joint transport must bind two distinct agents"})
    passed = all(item["status"] == "pass" for item in findings)
    return {"status": "pass" if passed else "fail", "eligible": passed,
            "scene_key": scene, "profile": profile, "findings": findings,
            "dependency_depth": depth, "skills": sorted(skills)}


__all__ = ["SCENE_TASK_PROFILES", "STRONG_TASK_BLUEPRINTS", "validate_long_horizon_task"]
