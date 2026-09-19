"""Factory task requirements independent of concrete robot instances."""

from __future__ import annotations

from copy import deepcopy
import math

from .scene_contract import FACTORY_TASK_CONTRACTS


_TASK_REQUIREMENTS = {
    "F-INS-01": [
        {"id": "aerial_view", "domain": "aerial", "navigation": True, "skill": "aerial_scan",
         "aerial_scan": True, "sensors": ["camera"],
         "required_protocols": [{"protocol_id": "factory_inventory_detection_v2"}],
         "required_evidence_tier": "E6", "resources": ["storage_west"]},
        {"id": "ground_approach", "domain": "ground", "navigation": True,
         "required_evidence_tier": "E6", "required_protocols": [{"protocol_id": "factory_navigation_visit_v2"}],
         "resources": ["storage_west"], "prerequisites": ["aerial_view"]},
        {"id": "ground_view", "domain": "ground", "navigation": False, "skill": "wait",
         "sensors": ["camera"], "required_protocols": [{"protocol_id": "factory_inventory_detection_v2"}], "required_evidence_tier": "E6",
         "resources": ["storage_west"],
         "prerequisites": ["ground_approach"], "same_team_as": "ground_approach"},
        {"id": "route_confirmation", "domain": "ground", "navigation": True,
         "required_protocols": [{"protocol_id": "factory_navigation_visit_v2"}],
         "required_evidence_tier": "E6",
         "resources": ["midband"], "prerequisites": ["ground_view"]},
    ],
    "F-TRAFFIC-01": [
        {"id": "route_west", "domain": "ground", "navigation": True,
         "required_evidence_tier": "E6", "required_protocols": [{"protocol_id": "factory_navigation_visit_v2"}],
         "resources": ["midband", "dock_north"]},
        {"id": "route_south", "domain": "ground", "navigation": True,
         "required_evidence_tier": "E6", "required_protocols": [{"protocol_id": "factory_navigation_visit_v2"}],
         "resources": ["midband", "yard_south"]},
    ],
    "F-COV-01": [
        {"id": "storage_arrive", "domain": "ground", "navigation": True,
         "required_evidence_tier": "E6", "required_protocols": [{"protocol_id": "factory_navigation_visit_v2"}],
         "resources": ["storage_west"]},
        {"id": "storage_watch", "domain": "ground", "navigation": False, "skill": "wait",
         "sensors": ["camera"], "required_protocols": [{"protocol_id": "factory_camera_capture_v2"}], "required_evidence_tier": "E6",
         "resources": ["storage_west"],
         "prerequisites": ["storage_arrive"], "same_team_as": "storage_arrive"},
        {"id": "assembly_arrive", "domain": "ground", "navigation": True,
         "required_evidence_tier": "E6", "required_protocols": [{"protocol_id": "factory_navigation_visit_v2"}],
         "resources": ["assembly_east"]},
        {"id": "assembly_watch", "domain": "ground", "navigation": False, "skill": "wait",
         "sensors": ["camera"], "required_protocols": [{"protocol_id": "factory_camera_capture_v2"}], "required_evidence_tier": "E6",
         "resources": ["assembly_east"],
         "prerequisites": ["assembly_arrive"], "same_team_as": "assembly_arrive"},
        {"id": "dock_arrive", "domain": "ground", "navigation": True,
         "required_evidence_tier": "E6", "required_protocols": [{"protocol_id": "factory_navigation_visit_v2"}],
         "resources": ["dock_north"]},
        {"id": "dock_watch", "domain": "ground", "navigation": False, "skill": "wait",
         "sensors": ["camera"], "required_protocols": [{"protocol_id": "factory_camera_capture_v2"}], "required_evidence_tier": "E6",
         "resources": ["dock_north"],
         "prerequisites": ["dock_arrive"], "same_team_as": "dock_arrive"},
    ],
    "F-VERT-01": [
        {"id": "stair_access_check", "domain": "vertical_access", "navigation": True,
         "required_evidence_tier": "E6",
         "required_protocols": [{"protocol_id": "factory_stair_support_v1"}],
         "resources": ["main_stair"]},
    ],
}


def factory_task_requirements(task_id: str) -> list[dict]:
    """Return release-QA requirements used to admit reference configurations."""
    if task_id not in FACTORY_TASK_CONTRACTS:
        raise ValueError(f"unknown Factory task {task_id!r}")
    return deepcopy(_TASK_REQUIREMENTS[task_id])


def factory_participant_requirements(task_id: str) -> list[dict]:
    """Return participant-facing interface constraints without evidence gates."""
    requirements = factory_task_requirements(task_id)
    for requirement in requirements:
        requirement.pop("required_protocols", None)
        requirement.pop("required_evidence_tier", None)
    return requirements


def factory_benchmark_qa_requirements(task_id: str) -> list[dict]:
    """Return task-publication QA, including scoped simulator evidence."""
    return factory_task_requirements(task_id)


def validate_task_requirements(task_id: str, requirements: list[dict]) -> None:
    expected = {item["id"] for item in _TASK_REQUIREMENTS.get(task_id, ())}
    actual = {item.get("id") for item in requirements}
    if expected != actual:
        raise ValueError(f"{task_id}: task requirements do not match the registered contract")
    if any("agent" in item or "robot_id" in item for item in requirements):
        raise ValueError(f"{task_id}: requirements must not bind concrete robot ids")
    for item in requirements:
        if not item.get("domain") or not isinstance(item.get("navigation"), bool):
            raise ValueError(f"{task_id}:{item['id']}: domain and navigation flag are required")


def validate_factory_scenario(data: dict, task_id: str) -> dict:
    """Validate task-family semantics after generic V2 schema validation."""
    if data.get("scene_key") != "factory":
        raise ValueError("Factory task scenarios require scene_key='factory'")
    if data.get("benchmark_task_id") != task_id:
        raise ValueError(f"scenario benchmark_task_id must be {task_id!r}")
    requirements = factory_task_requirements(task_id)
    validate_task_requirements(task_id, requirements)
    nodes = {node.get("id"): node for node in data.get("phase_graph", {}).get("nodes", ())}
    missing = sorted(item["id"] for item in requirements if item["id"] not in nodes)
    if missing:
        raise ValueError(f"{task_id}: missing phase nodes {missing}")
    for requirement in requirements:
        node = nodes[requirement["id"]]
        expected_skill = requirement.get("skill", "navigate")
        if node.get("skill_key") != expected_skill:
            raise ValueError(f"{task_id}:{requirement['id']}: expected skill {expected_skill!r}")
        if not node.get("resources") or not set(requirement.get("resources", ())) <= set(node["resources"]):
            raise ValueError(f"{task_id}:{requirement['id']}: required shared resources are missing")
        if not set(requirement.get("prerequisites", ())) <= set(node["prerequisites"]):
            raise ValueError(f"{task_id}:{requirement['id']}: required prerequisites are missing")
        same_team_as = requirement.get("same_team_as")
        if same_team_as and node.get("preferred_agent") != nodes[same_team_as].get("preferred_agent"):
            raise ValueError(f"{task_id}:{requirement['id']}: observation must use its arriving agent")
    if task_id == "F-INS-01" and not any(node.get("skill_key") == "aerial_scan" for node in nodes.values()):
        raise ValueError("F-INS-01 requires an aerial_scan producer")
    if task_id == "F-INS-01":
        if nodes["ground_view"].get("observation_ref") != "inspection_site":
            raise ValueError("F-INS-01 ground_view must observe inspection_site")
        if nodes["ground_view"].get("duration_s") is None:
            raise ValueError("F-INS-01 ground_view requires a real camera observation window")
    if task_id == "F-TRAFFIC-01":
        if len({node.get("preferred_agent") for node in nodes.values()}) < 2:
            raise ValueError("F-TRAFFIC-01 requires at least two assigned agents")
        if any(node.get("skill_key") in {"transport_object", "joint_transport"} for node in nodes.values()):
            raise ValueError("F-TRAFFIC-01 must remain a traffic task without transport skills")
    if task_id == "F-COV-01":
        wait_nodes = {item["id"] for item in requirements if item.get("skill") == "wait"}
        if any(nodes[node_id].get("duration_s") is None for node_id in wait_nodes):
            raise ValueError("F-COV-01 requires duration_s for every coverage wait")
        recovery_nodes = [node for node in nodes.values() if node.get("recovery_for")]
        recovered_sources = {node["recovery_for"] for node in recovery_nodes}
        if recovery_nodes:
            if len(recovered_sources) != 1 or not recovered_sources <= wait_nodes:
                raise ValueError("F-COV-01 recovery must replace one coverage wait")
            original = nodes[next(iter(recovered_sources))]
            approaches = [node for node in recovery_nodes if node["skill_key"] == "navigate"]
            windows = [node for node in recovery_nodes if node["skill_key"] == "wait"]
            if len(recovery_nodes) != 2 or len(approaches) != 1 or len(windows) != 1:
                raise ValueError("F-COV-01 recovery requires a navigation and an observation window")
            approach, window = approaches[0], windows[0]
            replacement = window.get("preferred_agent")
            if (not replacement or replacement == original.get("preferred_agent")
                    or approach.get("preferred_agent") != replacement):
                raise ValueError("F-COV-01 recovery requires one distinct replacement_agent")
            if approach["id"] not in window["prerequisites"]:
                raise ValueError("F-COV-01 recovery window must depend on navigation")
            if window["target_ref"] != approach["target_ref"] or (
                    window.get("observation_ref") or window["target_ref"]) != (
                        original.get("observation_ref") or original["target_ref"]):
                raise ValueError("F-COV-01 recovery must observe the original subject from its arrival site")
            if any(not set(original["resources"]) <= set(node.get("resources", ()))
                   for node in recovery_nodes):
                raise ValueError("F-COV-01 recovery must reserve the original shared resources")
            robots = {robot["id"]: robot for robot in data["robots"]}
            if "camera" not in {attachment["type"] for attachment in robots[replacement]["attachments"]}:
                raise ValueError("F-COV-01 replacement_agent requires a camera")
            targets = {target["id"]: target for target in data["composition"]["targets"]}
            old_site, new_site = targets[original["target_ref"]], targets[window["target_ref"]]
            from EAI.scenario_spec.schema import ROBOT_PROFILES
            clearance = sum(ROBOT_PROFILES[robots[agent]["type"]]["radius_m"]
                            for agent in (original["preferred_agent"], replacement))
            clearance += old_site["radius_m"] + new_site["radius_m"]
            if math.dist(old_site["position"][:2], new_site["position"][:2]) < clearance:
                raise ValueError("F-COV-01 recovery viewpoint overlaps the retained exited member")
        faults = data.get("fault_events", [])
        if faults:
            if len(faults) != 1 or faults[0].get("kind") != "member_exit":
                raise ValueError("F-COV-01 allows one member_exit fault in its recovery variant")
            fault = faults[0]
            if not recovery_nodes:
                raise ValueError("F-COV-01 member_exit requires a recovery node")
            if original["preferred_agent"] != fault["agent"]:
                raise ValueError("F-COV-01 fault must target the original coverage member")
            if any(node.get("preferred_agent") != fault.get("replacement_agent")
                   for node in recovery_nodes):
                raise ValueError("F-COV-01 recovery nodes must use replacement_agent")
    if task_id == "F-VERT-01":
        node = nodes["stair_access_check"]
        vertical = node.get("vertical") or {}
        expected = {
            "link_id": "main_stair", "kind": "stair", "start_level": "ground",
            "goal_level": "mezzanine", "support_probe": "required",
        }
        if any(vertical.get(key) != value for key, value in expected.items()):
            raise ValueError("F-VERT-01 requires the open main_stair ground-to-mezzanine support probe")
        if node.get("target_ref") != "mezzanine_stair_exit":
            raise ValueError("F-VERT-01 must target mezzanine_stair_exit")
        if node.get("preferred_agent") is None:
            raise ValueError("F-VERT-01 requires a concrete legged probe agent")
        from EAI.scenario_spec.schema import ROBOT_PROFILES
        robots = {robot["id"]: robot for robot in data["robots"]}
        mobility = ROBOT_PROFILES[robots[node["preferred_agent"]]["type"]]["mobility"]
        if mobility not in {"quadruped", "biped"}:
            raise ValueError("F-VERT-01 requires quadruped or biped stair mobility")
        if len(nodes) != 1 or data.get("fault_events"):
            raise ValueError("F-VERT-01 is a single-objective probe without lifecycle faults")
    return {"task_id": task_id, "validated": True, "node_ids": sorted(nodes)}


def review_factory_task_design(data: dict, task_id: str) -> dict:
    """Return the authoring track for a validated Factory task.

    Factory dispatch and vertical probes intentionally have different task
    shapes from the long NVD business templates.  Keeping the track identity
    in the review artifact prevents a four-node dispatch task from being
    judged by the eight-node heterogeneous-carry profile.
    """
    admission = validate_factory_scenario(data, task_id)
    if task_id == "F-INS-01":
        from .inventory_dispatch import validate_dispatch_task
        validate_dispatch_task(data)
        track = "factory_dispatch_v1"
        model_decides = ["consume_message", "wait", "select_destination"]
    elif task_id == "F-TRAFFIC-01":
        track = "factory_shared_aisle_v1"
        model_decides = ["allocate", "navigate_goal", "reassign"]
    elif task_id == "F-COV-01":
        track = "factory_coverage_recovery_v1"
        model_decides = ["allocate", "navigate_goal", "wait", "reassign"]
    elif task_id == "F-VERT-01":
        track = "factory_vertical_probe_v1"
        model_decides = ["navigate_goal", "wait"]
    else:
        raise ValueError(f"unsupported Factory review task {task_id!r}")
    return {
        "schema": "eai.factory_task_design_review",
        "version": 1,
        "task_id": task_id,
        "track": track,
        "validated": True,
        "node_ids": admission["node_ids"],
        "model_decides": model_decides,
        "execution_validated": False,
        "release_ready": False,
    }


__all__ = [
    "factory_benchmark_qa_requirements", "factory_participant_requirements",
    "factory_task_requirements", "review_factory_task_design", "validate_factory_scenario",
    "validate_task_requirements",
]
