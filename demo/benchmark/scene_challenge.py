"""Scene-neutral challenge publication for benchmark demos.

Scene applications provide task semantics and release-QA metadata. This module
owns the artifact boundary shared by Factory, Warehouse, and Modern House.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from EAI.scenario_spec import load_scenario
from demo.benchmark.challenge_contract import (
    BASELINE_MANIFEST_SCHEMA,
    CONTRACT_VERSION,
    HIDDEN_EVALUATOR_SCHEMA,
    PARTICIPANT_API_SCHEMA,
    PUBLIC_TASK_SCHEMA,
    validate_baseline_manifest,
    validate_hidden_evaluator,
    validate_participant_api,
    validate_public_task_spec,
    write_challenge_bundle,
)
from demo.benchmark.provenance import canonical_sha256, file_sha256
from demo.benchmark.domain_evaluator import (
    build_domain_contract, evaluate_domain_evidence, public_domain_requirements,
)
from demo.benchmark.factor_matrix import (
    build_hidden_factor_policy, build_public_factor_manifest, validate_factor_manifest,
)
from demo.benchmark.semantic_contract import (
    build_semantic_design_catalog, objective_semantics_for, semantic_for_scene,
    validate_semantic_task_contract,
)


_RELEASE_BLOCKERS = {
    "factory": [
        "immutable_scene_and_voxel_provider_release",
        "full_reference_business_episode",
        "isaac_member_join_and_capability_change_adapter",
        "communication_factor_replay_and_effect_validation",
    ],
    "warehouse_nvd": [
        "immutable_scene_prop_and_voxel_provider_release",
        "live_rack_identity_and_dock_service_adapter",
        "physical_transport_and_handoff_reference_episode",
        "isaac_member_join_and_capability_change_adapter",
        "communication_factor_replay_and_effect_validation",
    ],
    "modern_house_nvd": [
        "immutable_scene_prop_and_voxel_provider_release",
        "live_household_identity_and_access_path_adapter",
        "physical_furniture_and_belonging_reference_episode",
        "isaac_member_join_and_capability_change_adapter",
        "communication_factor_replay_and_effect_validation",
    ],
}


def pending_release_gate(scene_key: str) -> dict[str, Any]:
    """Describe why a valid candidate package is not a released benchmark.

    Contract publication is useful for participant integration and review.  It
    must remain separate from a scoreable release, which needs immutable
    assets, live domain evidence and reference execution for the selected
    scene.
    """
    try:
        blockers = _RELEASE_BLOCKERS[scene_key]
    except KeyError as exc:
        raise ValueError(f"unsupported release-gate scene: {scene_key}") from exc
    return {
        "contract_publishable": True,
        "authoring_status": "passed",
        "authoring_scope": "static_task_semantics_and_package_contract",
        "execution_status": "external_pending",
        "benchmark_ready": False,
        "scoreable": False,
        "completed": [
            "four_layer_contract_compilation",
            "public_private_information_boundary",
            "offline_schema_geometry_and_planning_admission",
        ],
        "blockers": list(blockers),
        "missing_evidence_result": "unknown",
    }


def generate_scene_scenario(*, scene_key: str, task_name: str, seed: int,
                            origin: tuple[float, float], target_offset: tuple[float, float] = (5.0, 0.0),
                            secondary_ground_offset: tuple[float, float] | None = None) -> dict[str, Any]:
    """Build a substantive task template owned by a scene demo.

    Native prop declarations preserve audited dimensions and estimated masses.
    Availability is checked by scenario admission, never by substituting a box.
    Transport remains a kinematic coordination baseline, without a payload or
    grasping claim for the selected robots.
    """
    ox, oy = origin

    def target(name, x, y):
        return {"id": name, "position": [x, y, 0.0], "radius_m": 0.5}

    def prop(identifier, asset_key, x, y):
        from EAI_assets.scenario_assets import get_declared_asset
        asset = get_declared_asset(asset_key)
        if asset.size_m is None or asset.suggested_mass_kg is None:
            raise ValueError(f"{asset_key}: native task prop requires audited size and mass")
        return {"id": identifier, "asset_key": asset_key, "size_m": list(asset.size_m),
                "spawn_pose": {"position": [x, y, asset.size_m[2] / 2], "rotation": [1, 0, 0, 0]},
                "physics": {"mode": "dynamic", "mass_kg": asset.suggested_mass_kg,
                            "static_friction": 0.8, "dynamic_friction": 0.6, "restitution": 0.0}}

    def node(node_id, skill, target_ref, prerequisites, *, preferred=None, agents=None, object_ref=None, resources=None, scan_height=None):
        timeout_s = {
            "aerial_scan": 120.0,
            "navigate": 180.0,
            "joint_transport": 300.0,
            "transport_object": 240.0,
            "wait": 120.0,
        }[skill]
        item = {"id": node_id, "skill_key": skill, "preferred_agent": preferred, "agents": agents,
                "target_ref": target_ref, "object_ref": object_ref, "prerequisites": prerequisites,
                "timeout_s": timeout_s, "duration_s": 3.0 if skill == "wait" else None,
                "resources": resources or []}
        if scan_height is not None:
            item["scan_height_m"] = scan_height
        return item

    aerial_evidence_attachments = [
        {"type": "camera", "controller": None},
    ]

    if scene_key == "warehouse_nvd":
        tx, ty = ox + target_offset[0], oy + target_offset[1]
        carter_offset = secondary_ground_offset or (3.2, 0.0)
        robots = [
            {"id": "iris_1", "type": "iris", "controller": {"mode": "default", "cfg": "PEGASUS_IRIS_POSITION_CFG"}, "attachments": deepcopy(aerial_evidence_attachments), "spawn_pose": {"position": [ox - 3.5, oy, 1.0], "rotation": [1, 0, 0, 0]}},
            {"id": "m20_1", "type": "m20", "controller": {"mode": "default", "cfg": "M20_ROUGH_RSL_CFG"}, "attachments": [{"type": "z1", "controller": {"mode": "default", "cfg": "Z1_IK_CFG"}}], "spawn_pose": {"position": [ox + 1.0, oy, 0.52], "rotation": [1, 0, 0, 0]}},
            {"id": "carter_1", "type": "carter", "controller": {"mode": "default", "cfg": "CARTER_DIFF_CFG"}, "attachments": [], "spawn_pose": {"position": [ox + carter_offset[0], oy + carter_offset[1], 0.2], "rotation": [1, 0, 0, 0]}},
        ]
        entities = [
            prop("oversized_crate_proxy", "wooden_crate_a", ox + 3.0, oy + 3.0),
            prop("priority_carton_proxy", "cardbox_c", ox + 3.0, oy + 1.7),
        ]
        targets = [
            target("rack_scan_zone", ox - 2.5, oy),
            target("aisle_entry_zone", ox - 0.6, oy),
            target("consolidation_zone", tx, ty),
            target("outbound_dock_zone", tx, ty + 1.0),
        ]
        nodes = [
            node("rack_overview_scan", "aerial_scan", "rack_scan_zone", [], preferred="iris_1", resources=["rack_aisle"], scan_height=2.5),
            # The two staging legs park at opposite ends of the task area, so
            # they do not share a physical lane: serializing them on
            # rack_aisle would leave one robot idling (and drifting) beside
            # the other's parking spot.
            node("aisle_left_access", "navigate", "aisle_entry_zone", ["rack_overview_scan"], preferred="m20_1", resources=[]),
            node("aisle_right_access", "navigate", "outbound_dock_zone", ["rack_overview_scan"], preferred="carter_1", resources=[]),
            node("oversized_crate_transfer", "joint_transport", "consolidation_zone", ["aisle_left_access", "aisle_right_access"], preferred="m20_1", agents=["m20_1", "carter_1"], object_ref="oversized_crate_proxy", resources=["transport_lane"]),
            node("priority_carton_stage", "transport_object", "consolidation_zone", ["oversized_crate_transfer"], preferred="m20_1", object_ref="priority_carton_proxy", resources=["transport_lane"]),
            node("dock_lane_check", "navigate", "outbound_dock_zone", ["priority_carton_stage"], preferred="carter_1", resources=["outbound_dock"]),
            node("priority_carton_dispatch", "transport_object", "outbound_dock_zone", ["dock_lane_check"], preferred="m20_1", object_ref="priority_carton_proxy", resources=["outbound_dock"]),
            node("outbound_inventory_scan", "aerial_scan", "outbound_dock_zone", ["priority_carton_dispatch"], preferred="iris_1", resources=["outbound_dock"], scan_height=2.5),
            node("dock_confirmation_hold", "wait", "outbound_dock_zone", ["outbound_inventory_scan"], preferred="carter_1", resources=["outbound_dock"]),
            node("aisle_closeout", "navigate", "rack_scan_zone", ["dock_confirmation_hold"], preferred="m20_1", resources=["rack_aisle"]),
        ]
    elif scene_key == "modern_house_nvd":
        robots = [
            {"id": "iris_1", "type": "iris", "controller": {"mode": "default", "cfg": "PEGASUS_IRIS_POSITION_CFG"}, "attachments": deepcopy(aerial_evidence_attachments), "spawn_pose": {"position": [ox - 3.5, oy, 0.85], "rotation": [1, 0, 0, 0]}},
            {"id": "m20_1", "type": "m20", "controller": {"mode": "default", "cfg": "M20_ROUGH_RSL_CFG"}, "attachments": [{"type": "z1", "controller": {"mode": "default", "cfg": "Z1_IK_CFG"}}], "spawn_pose": {"position": [ox - 1.0, oy - 2.0, 0.52], "rotation": [1, 0, 0, 0]}},
            {"id": "carter_1", "type": "carter", "controller": {"mode": "default", "cfg": "CARTER_DIFF_CFG"}, "attachments": [], "spawn_pose": {"position": [ox + 1.6, oy - 3.0, 0.2], "rotation": [1, 0, 0, 0]}},
        ]
        entities = [
            prop("armchair_proxy", "mh_armchair", ox, oy + 1.3),
            prop("keepsake_box_proxy", "mh_bookstack", ox + 0.6, oy - 1.0),
        ]
        targets = [
            # Keep the completed entry scan outside the subsequent furniture
            # pickup corridor while preserving a low, doorway-side viewpoint.
            target("entry_scan_zone", ox - 1.5, oy),
            target("furniture_access_zone", ox + 0.6, oy),
            target("service_side_zone", ox + 1.6, oy),
            target("furniture_parking_zone", ox + 1.6, oy + 1.3),
            target("furniture_review_zone", ox + 0.2, oy),
            target("keepsake_staging_zone", ox + 1.6, oy - 1.0),
            target("room_review_zone", ox - 1.0, oy + 2.5),
            target("primary_standby_zone", ox - 2.0, oy + 1.0),
            target("reviewer_standby_zone", ox + 3.0, oy),
        ]
        nodes = [
            node("room_entry_scan", "aerial_scan", "entry_scan_zone", [], preferred="iris_1", resources=["hall_access"], scan_height=0.85),
            node("furniture_side_access", "navigate", "furniture_access_zone", ["room_entry_scan"], preferred="m20_1", resources=["hall_access"]),
            node("doorway_access_check", "navigate", "service_side_zone", ["room_entry_scan"], preferred="carter_1", resources=["hall_access"]),
            node("coordinated_furniture_reposition", "joint_transport", "furniture_parking_zone", ["furniture_side_access", "doorway_access_check"], preferred="m20_1", agents=["m20_1", "carter_1"], object_ref="armchair_proxy", resources=["furniture_route"]),
            node("furniture_clearance_scan", "aerial_scan", "furniture_review_zone", ["coordinated_furniture_reposition"], preferred="iris_1", resources=["furniture_route"], scan_height=0.85),
            node("keepsake_relocation", "transport_object", "keepsake_staging_zone", ["furniture_clearance_scan"], preferred="m20_1", object_ref="keepsake_box_proxy", resources=["room_staging"]),
            node("room_entry_verification", "navigate", "room_review_zone", ["keepsake_relocation"], preferred="carter_1", resources=["room_review"]),
            node("reviewer_standby", "navigate", "reviewer_standby_zone", ["room_entry_verification"], preferred="carter_1", resources=["hall_access"]),
            node("hall_release", "navigate", "primary_standby_zone", ["reviewer_standby"], preferred="m20_1", resources=["hall_access"]),
            node("room_condition_scan", "aerial_scan", "room_review_zone", ["hall_release"], preferred="iris_1", resources=["room_review"], scan_height=0.85),
            node("placement_confirmation_hold", "wait", "reviewer_standby_zone", ["room_condition_scan"], preferred="carter_1", resources=["room_review"]),
        ]
    else:
        raise ValueError(f"unsupported scene challenge generator: {scene_key}")

    semantic_task = semantic_for_scene(scene_key)
    return {
        "version": 2, "task_name": task_name, "scene_key": scene_key, "seed": seed,
        "robots": robots,
        "composition": {"entities": entities, "targets": targets},
        "phase_graph": {"nodes": nodes},
        "semantic_task": semantic_task,
    }


def audit_generated_scene_scenario(scenario: Mapping[str, Any]) -> dict[str, Any]:
    """Run schema, geometry, connectivity, and executor-plan admission gates."""
    from EAI.scenario_spec.geometry import provider_map, validate_connectivity, validate_geometry
    from EAI.scenario_spec.validation import validate_scenario
    from demo.benchmark.runtime import build_plan, planning_template

    candidate = deepcopy(dict(scenario))
    semantic = candidate.get("semantic_task") or semantic_for_scene(candidate["scene_key"])
    semantic_review = validate_semantic_task_contract(semantic, scene_key=candidate["scene_key"])
    candidate["semantic_task"] = semantic
    validated = validate_scenario(candidate)
    occupancy = provider_map(scene_key=validated["scene_key"])
    geometry = validate_geometry(validated, occupancy)
    connectivity = validate_connectivity(validated, occupancy)
    plan = build_plan(
        validated, occupancy, planning_template(validated["scene_key"], occupancy))
    from demo.benchmark.aerial_navigation import audit_aerial_plan
    aerial = audit_aerial_plan(validated, plan)
    if not aerial["validated"]:
        blocked = [row["node_id"] for row in aerial["checks"] if not row["admitted"]]
        raise ValueError(f"generated aerial route failed continuous clearance: {blocked}")
    return {
        "validated": True,
        "semantic_task": semantic_review,
        "schema": "validated",
        "geometry": geometry,
        "connectivity": connectivity,
        "aerial_admission": aerial,
        "executor_plan": {
            "validated": True,
            "node_ids": [entry["node"] for entry in plan],
        },
    }


def _json_ready(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_json_ready(item) for item in value)
    return value


def evaluate_objectives(report: Mapping[str, Any], *, scenario: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate scene-neutral objective completion from a runtime report.

    The evaluator consumes scheduler truth produced by the benchmark runtime. It
    deliberately scores task state and scheduling evidence only; geometry is
    reported as execution evidence and is not promoted to a physical-safety or
    manipulation claim.
    """
    if not isinstance(report, Mapping):
        raise ValueError("runtime report must be an object")
    scheduling = report.get("scheduling") or {}
    states = scheduling.get("states") or {}
    if not isinstance(states, Mapping):
        raise ValueError("runtime report scheduling.states must be an object")
    if scenario is None:
        scenario = report.get("scenario") or {}
    nodes = (scenario.get("phase_graph") or {}).get("nodes") or []
    required_ids = [node["id"] for node in nodes if isinstance(node, Mapping) and node.get("id")]
    if not required_ids:
        required_ids = list(states)
    objectives = []
    completed = 0
    for node_id in required_ids:
        state = states.get(node_id) or {}
        status = state.get("status", "missing")
        succeeded = status == "succeeded"
        completed += int(succeeded)
        objectives.append({
            "id": node_id,
            "status": status,
            "completed": succeeded,
            "started_at_s": state.get("started_at_s"),
            "finished_at_s": state.get("finished_at_s"),
            "failure_reason": state.get("failure_reason") or state.get("reason"),
        })
    total = len(required_ids)
    conflicts = report.get("resource_conflicts", scheduling.get("resource_conflicts", []))
    deadlocks = report.get("deadlocks", [])
    replans = report.get("replans", report.get("replan_count", 0))
    waiting = report.get("wait_time_s", scheduling.get("waiting_time_s", 0.0))
    terminal = scheduling.get("time_s", report.get("makespan_s"))
    result = {
        "task_success": total > 0 and completed == total,
        "partial_completion": (completed / total) if total else 0.0,
        "completed_objectives": completed,
        "required_objectives": total,
        "objectives": objectives,
        "makespan_s": terminal,
        "wait_time_s": waiting,
        "resource_conflicts": len(conflicts) if isinstance(conflicts, (list, tuple, set)) else conflicts,
        "deadlocks": len(deadlocks) if isinstance(deadlocks, (list, tuple, set)) else deadlocks,
        "replans": replans,
        "physical_claim": "logic_and_scheduling_evaluation_only",
    }
    return result


def review_task_design(scenario: Mapping[str, Any], profile: Mapping[str, Any], blueprint: Any) -> dict[str, Any]:
    """Return release-QA findings for a scene-owned task design."""
    nodes = list((scenario.get("phase_graph") or {}).get("nodes") or [])
    skills = {node.get("skill_key") for node in nodes}
    required = set(profile.get("required_skills") or ())
    minimum_nodes = int(profile.get("minimum_nodes", 1))
    substantive = sum(skill not in {"navigate", "wait"} for skill in (node.get("skill_key") for node in nodes))
    depths = {}
    pending = {node.get("id"): set(node.get("prerequisites") or ()) for node in nodes if node.get("id")}
    while pending:
        ready = [node_id for node_id, deps in pending.items() if not deps or deps.isdisjoint(pending)]
        if not ready:
            break
        for node_id in ready:
            deps = {item for item in next(node["prerequisites"] for node in nodes if node.get("id") == node_id) if item in depths}
            depths[node_id] = 1 + max((depths[item] for item in deps), default=0)
            pending.pop(node_id)
    dependency_depth = max(depths.values(), default=0)
    missing_skills = sorted(required - skills)
    findings = []
    if len(nodes) < minimum_nodes:
        findings.append(f"node_count_below_profile:{len(nodes)}<{minimum_nodes}")
    if substantive < int(profile.get("minimum_substantive_nodes", 0)):
        findings.append(f"substantive_node_count_below_profile:{substantive}<{profile.get('minimum_substantive_nodes')}")
    if dependency_depth < int(profile.get("minimum_dependency_depth", 0)):
        findings.append(f"dependency_depth_below_profile:{dependency_depth}<{profile.get('minimum_dependency_depth')}")
    findings.extend(f"missing_required_skill:{skill}" for skill in missing_skills)
    return {
        "validated": not findings,
        "status": "substantive_ready" if not findings else "smoke_or_incomplete",
        "node_count": len(nodes),
        "substantive_node_count": substantive,
        "dependency_depth": dependency_depth,
        "observed_skills": sorted(skill for skill in skills if skill),
        "required_skills": sorted(required),
        "blueprint_length": len(tuple(blueprint)),
        "findings": findings,
    }


def evaluate_challenge_report(bundle: Mapping[str, Mapping[str, Any]], report: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate a runtime report against a private challenge bundle."""
    public = bundle["public_task"]
    hidden = bundle["hidden_evaluator"]
    validate_public_task_spec(public)
    validate_hidden_evaluator(hidden, task_spec=public)
    if hidden["public_task_sha256"] != canonical_sha256(public):
        raise ValueError("hidden evaluator is not bound to the supplied public task")
    report_task = report.get("task_id")
    if report_task is not None and report_task != public["task_id"]:
        raise ValueError("runtime report task id differs from challenge")
    report_scene = report.get("scene_key")
    if report_scene is not None and report_scene != public["scene"]["scene_key"]:
        raise ValueError("runtime report scene differs from challenge")
    result = evaluate_objectives(report, scenario=hidden["scenario_snapshot"])
    result["scheduler_task_success"] = result["task_success"]
    domain = evaluate_domain_evidence(
        report, scenario=hidden["scenario_snapshot"], contract=hidden.get("domain_contract"))
    result["domain_evaluation"] = domain
    result["business_task_success"] = domain["business_success"]
    result["domain_predicate_fraction"] = domain["predicate_fraction"]
    if domain["required"]:
        result["task_success"] = result["scheduler_task_success"] and domain["business_success"]
    if public["scene"]["scene_key"] == "factory" and public["task_id"] == "F-INS-01":
        from demo.factory_benchmark.inspect_dispatch import evaluate_dispatch
        try:
            dispatch = evaluate_dispatch(report, scenario=hidden["scenario_snapshot"])
        except (ValueError, KeyError, TypeError, OSError) as exc:
            dispatch = {"dispatch_success": False, "reason": f"missing_or_invalid_dispatch_evidence: {exc}"}
        result["dispatch_evaluation"] = dispatch
        result["task_success"] = result["scheduler_task_success"] and bool(dispatch.get("dispatch_success"))
    result["task_id"] = public["task_id"]
    result["scene_key"] = public["scene"]["scene_key"]
    result["evaluator_contract_sha256"] = canonical_sha256(hidden)
    return result


def _public_robot(robot: Mapping[str, Any]) -> dict[str, Any]:
    from EAI.scenario_spec.schema import ROBOT_PROFILES

    profile = ROBOT_PROFILES[robot["type"]]
    domain = "aerial" if profile["mobility"] == "aerial" else "ground"
    return {
        "id": robot["id"],
        "type": robot["type"],
        "controller": deepcopy(robot["controller"]),
        "attachments": deepcopy(robot["attachments"]),
        "initial_pose": deepcopy(robot["spawn_pose"]),
        "capability_declaration": {
            "domain": domain,
            "mobility": profile["mobility"],
            "navigation_interface": True,
            "attachment_types": [item["type"] for item in robot["attachments"]],
            "rated_payload_kg": None,
            "physical_manipulation_evidence": "not_established",
        },
    }


def _public_objective(node: Mapping[str, Any]) -> dict[str, Any]:
    objective = {
        "id": node["id"],
        "skill": node["skill_key"],
        "prerequisites": list(node.get("prerequisites") or []),
        "timeout_s": node["timeout_s"],
        "duration_s": node.get("duration_s"),
        "resources": list(node.get("resources") or []),
    }
    for key in ("target_ref", "observation_ref", "scan_height_m", "scan_yaw_rad", "wait_yaw_rad", "vertical"):
        if node.get(key) is not None:
            objective[key] = deepcopy(node[key])
    return objective


def _public_objective_semantics(scene_key: str, node: Mapping[str, Any],
                                mission: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve exact author-side meaning without exposing evaluator truth."""
    try:
        mapped = objective_semantics_for(scene_key, node)
    except ValueError:
        known_ids = set(build_semantic_design_catalog(scene_key)["objective_contracts"])
        if node.get("id") in known_ids or node.get("id") == "stair_access_check":
            raise
        mapped = {
            "role": node["skill_key"],
            "purpose": mission["objective_semantics"]["purpose"],
            "deliverable": mission["objective_semantics"]["deliverable"],
            "acceptance_evidence": list(mission["objective_semantics"]["acceptance_evidence"]),
            "allowed_alternatives": list(mission["objective_semantics"]["allowed_alternatives"]),
            "mapping_status": "scene_mission_fallback_unmapped_objective",
        }
    return {
        "role": mapped["role"],
        "purpose": mapped["purpose"],
        "deliverable": mapped["deliverable"],
        "acceptance_evidence": list(mapped["acceptance_evidence"]),
        "allowed_alternatives": list(mapped["allowed_alternatives"]),
        "semantic_mapping_status": (
            "catalog_compiler_integrated_domain_pending"
            if mapped.get("mapping_status") == "catalog_only_compiler_integration_pending"
            else mapped.get("mapping_status", "catalog_compiler_integrated")
        ),
    }


def _public_task(
    scenario: Mapping[str, Any],
    *,
    scenario_sha256: str,
    scene_asset: str,
    task_contract: Mapping[str, Any],
    participant_requirements: list[dict[str, Any]],
    business_rules: Mapping[str, Any] | None,
    scoring_metrics: list[str],
    physical_limitations: list[str],
) -> dict[str, Any]:
    nodes = scenario["phase_graph"]["nodes"]
    semantic_task = deepcopy(scenario.get("semantic_task") or semantic_for_scene(scenario["scene_key"]))
    validate_semantic_task_contract(semantic_task, scene_key=scenario["scene_key"])
    domain_contract = build_domain_contract(scenario)
    factor_manifest = build_public_factor_manifest(scenario["scene_key"])
    validate_factor_manifest(factor_manifest, scene_key=scenario["scene_key"])
    participant_controlled_skills = {
        "navigate", "aerial_scan", "wait", "transport_object", "joint_transport"}
    public = {
        "schema": PUBLIC_TASK_SCHEMA,
        "version": CONTRACT_VERSION,
        "task_id": scenario.get("benchmark_task_id", scenario["task_name"]),
        "task_name": scenario["task_name"],
        "release_status": "pending",
        "execution_validated": False,
        "release_gate": pending_release_gate(scenario["scene_key"]),
        "scene": {
            "scene_key": scenario["scene_key"],
            "scene_asset": scene_asset,
            "scenario_sha256": scenario_sha256,
        "task_contract_sha256": canonical_sha256(_json_ready(task_contract)),
        },
        "initial_fleet": [
            _public_robot(robot) for robot in scenario["robots"]
            if robot["id"] in set((scenario.get("runtime_lifecycle") or {}).get(
                "initial_agent_ids", [item["id"] for item in scenario["robots"]]))
        ],
        "targets": deepcopy(scenario["composition"]["targets"]),
        # Public geometry is useful for planning, but the evaluator's object
        # id -> order/SKU binding must remain private. Stable opaque labels
        # prevent the semantic node name from becoming the answer key.
        "scene_objects": [{"id": f"scene_object_{index + 1}",
                           "asset_key": entity["asset_key"],
                           "size_m": deepcopy(entity["size_m"]),
                           "mass_kg": entity["physics"]["mass_kg"]}
                          for index, entity in enumerate(scenario["composition"]["entities"])],
        "objectives": [
            {**_public_objective(node),
             **_public_objective_semantics(scenario["scene_key"], node, semantic_task)}
            for node in nodes
        ],
        "semantic_task": semantic_task,
        "factor_manifest": factor_manifest,
        "domain_requirements": public_domain_requirements(domain_contract),
        "participant_requirements": deepcopy(participant_requirements),
        "resources": sorted({resource for node in nodes for resource in node.get("resources") or []}),
        "business_rules": _json_ready(deepcopy(dict(business_rules or {}))),
        "participant": {
            "observation_mode": "declared_events_and_state_updates",
            "api_schema": PARTICIPANT_API_SCHEMA,
            "runtime_status": "scene_transport_allocation_v2",
            "controlled_objectives": [
                node["id"] for node in nodes
                if node["skill_key"] in participant_controlled_skills
            ],
            "allowed_action_types": [
                "allocate", "reassign", "navigate_goal", "consume_message"],
        },
        "model_io": {
            "protocol": "fixed_business_dag_coordination_v1",
            "inputs": [
                "public_task.json: scene, initial_fleet, opaque scene_objects, targets, objective DAG, resources, factor manifest and business rules",
                "participant_api.json: event/action envelopes and implementation status",
                "runtime events: only events delivered at or before the current simulation time",
            ],
            "given_by_benchmark": [
                "the reviewed objective IDs and prerequisite graph",
                "the public robot roster and declared interfaces",
                "the task object and destination references exposed by the packet",
            ],
            "model_decides": [
                "which eligible public agent or team executes a ready objective",
                "when to reassign or submit a legal navigation action",
                "how to react to visible lifecycle and communication events",
            ],
            "model_does_not_decide_in_this_version": [
                "a new objective graph, hidden object identity, future intervention time, or reference answer",
            ],
            "outputs": [
                "participant_action envelopes validated by participant_api.json",
                "optional explanatory text kept outside the machine score",
            ],
            "claims_not_accepted_as_evidence": [
                "self-reported success, pose, object identity, dock state, resident safety, or payload capacity",
            ],
        },
        "scoring": {
            "primary": "domain_evidence_and_objective_completion" if domain_contract["required"] else "objective_completion_and_constraint_compliance",
            "metrics": list(dict.fromkeys([*scoring_metrics, "scheduler_task_success", "business_task_success", "domain_predicate_fraction"])),
            "geometric_constraints": ["forbidden_ground_regions", "route_inflation", "resource_reservations"],
        },
        "physical_claim": {
            "validated_scope": "logic_scoring_and_navigation_scheduling_execution",
            "limitations": list(physical_limitations),
        },
    }
    validate_public_task_spec(public)
    return public


def _participant_api(public_task: Mapping[str, Any]) -> dict[str, Any]:
    api = {
        "schema": PARTICIPANT_API_SCHEMA,
        "version": CONTRACT_VERSION,
        "task_id": public_task["task_id"],
        "public_task_sha256": canonical_sha256(public_task),
        "release_status": public_task["release_status"],
        "execution_validated": False,
        "event_envelope": {"schema": "eai.benchmark.participant_event", "version": 1,
                           "fields": ["sequence", "time_s", "type", "payload"]},
        "event_types": {
            "episode_started": {"payload_fields": ["fleet", "objectives", "resources"]},
            "phase_ready": {"payload_fields": ["node_id", "eligible_agent_ids"]},
            "observation_message": {"payload_fields": ["message_id", "producer_id", "published_at_s", "valid_until_s", "estimate"]},
            "member_exit": {"payload_fields": ["agent_id", "affected_node_ids"]},
            "member_join": {"payload_fields": ["agent_id", "capability_declaration", "capability_version"]},
            "capability_change": {"payload_fields": ["agent_id", "capability_declaration", "capability_version", "affected_node_ids"]},
            "action_result": {"payload_fields": ["action_id", "status", "reason"]},
            "tick": {"payload_fields": ["robot_states", "phase_states", "resource_states"]},
        },
        "action_envelope": {"schema": "eai.benchmark.participant_action", "version": 1,
                            "fields": ["action_id", "submitted_at_s", "type", "payload"]},
        "action_types": {
            key: {"payload_fields": list(fields)} for key, fields in {
                "allocate": ("node_id", "agent_ids"),
                "navigate_goal": ("node_id", "agent_id", "target_ref"),
                "wait": ("node_id", "agent_id", "duration_s"),
                "reassign": ("node_id", "agent_ids", "reason"),
                "consume_message": ("message_id", "consumer_id"),
            }.items()
        },
        "ordering": "sequence is strictly increasing; submitted_at_s uses simulation time",
        "truth_boundary": "No evaluator truth, future intervention time, or reference decision is exposed.",
        "implementation_status": {
            "contract_validation": "available",
            "isaac_participant_adapter": "scene_public_greedy_v2",
            "supported_action_types": list(public_task["participant"]["allowed_action_types"]),
            "isaac_supported_action_types": [
                "allocate", "reassign", "navigate_goal", "consume_message"],
            "protocol_supported_action_types": ["allocate", "reassign", "navigate_goal", "wait", "consume_message"],
            "pending_action_types": ([] if public_task["participant"]["runtime_status"] == "protocol_components_no_motion"
                                     else ["wait"]),
            "lifecycle_events": (
                "Preloaded reserve parking, controller command admission, activation and "
                "navigation-interface versioned readback are bound to MultiRobotDirectEnv; maintained scene "
                "reserve layouts and physical reference episodes remain pending"
            ),
            "communication_runtime": (
                "participant observation delivery/consumption queue integrated; "
                "scene semantic detector/service backends pending"
            ),
        },
    }
    validate_participant_api(api, task_spec=public_task)
    return api


def build_scene_runtime_contract(scenario: Mapping[str, Any], *, scenario_sha256: str = "runtime",
                                 protocol_components: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the public task and participant API directly from loaded V2 data."""
    public = _public_task(
        scenario,
        scenario_sha256=scenario_sha256,
        scene_asset=f"scene/{scenario['scene_key']}",
        task_contract={"scene_key": scenario["scene_key"], "runtime": "shared_scene_runtime_v1"},
        participant_requirements=[
            {"id": node["id"], "skill": node["skill_key"],
             "domain": "aerial" if node["skill_key"] == "aerial_scan" else "ground",
             "navigation": node["skill_key"] in {
                 "navigate", "aerial_scan", "transport_object", "joint_transport"},
             "manipulation_interface": node["skill_key"] == "transport_object"}
            for node in scenario["phase_graph"]["nodes"]
        ],
        business_rules={
            "participant_choice": (
                "Participant controls navigation and kinematic-transport objectives. "
                "Transport keeps its declared delivery target; the participant selects "
                "and may reassign the executing agent or team."
            ),
            "object_ownership": (
                "Object ownership changes only through runtime pickup/release events. "
                "Reassignment is rejected while an object is carried; a member exit "
                "during a carry drops the object at the carrier's position."
            ),
        },
        scoring_metrics=["task_success", "partial_completion", "makespan_s", "wait_time_s"],
        physical_limitations=["Navigation geometry is not a collision-safety margin.", "No physical manipulation or payload claim."],
    )
    if protocol_components:
        public["participant"]["runtime_status"] = "protocol_components_no_motion"
        public["participant"]["allowed_action_types"] += ["wait", "consume_message"]
        public["physical_claim"]["validated_scope"] = "protocol_components_only_no_motion"
    return public, _participant_api(public)


def _hidden_evaluator(public_task: Mapping[str, Any], scenario: Mapping[str, Any], *, qa: Mapping[str, Any]) -> dict[str, Any]:
    scenario_snapshot = _json_ready(deepcopy(dict(scenario)))
    scenario_snapshot.setdefault("semantic_task", deepcopy(public_task["semantic_task"]))
    evaluator = {
        "schema": HIDDEN_EVALUATOR_SCHEMA,
        "version": CONTRACT_VERSION,
        "task_id": public_task["task_id"],
        "public_task_sha256": canonical_sha256(public_task),
        "release_status": public_task["release_status"],
        "execution_validated": False,
        "scenario_snapshot": scenario_snapshot,
        "domain_contract": build_domain_contract(scenario_snapshot),
        "factor_policy": build_hidden_factor_policy(scenario_snapshot["scene_key"]),
        "truth_sources": {
            "scenario_targets": "scenario_snapshot.composition.targets",
            "phase_states": "runtime.scheduling.states",
            "pose_samples": "runtime.pose_trajectory",
            "interventions": "episode_condition.hidden_interventions",
            "domain_records": "runtime.domain_evidence + runtime.domain_service_events",
            "identity_evidence": "runtime.observation_store.detections + observations + consumptions",
        },
        "success_predicates": [
            {"id": "objective_completion", "evaluator": "demo.benchmark.scene_challenge.evaluate_objectives",
             "requires": ["phase_states", "pose_samples"]},
            {"id": "allowed_action_trace", "evaluator": "demo.benchmark.challenge_contract.validate_participant_action",
             "requires": ["participant_action_log"]},
            {"id": "domain_business_outcome", "evaluator": "demo.benchmark.domain_evaluator.evaluate_domain_evidence",
             "requires": ["domain_records", "identity_evidence", "pose_samples", "phase_states"],
             "missing_evidence": "fail_business_success_keep_scheduler_partial_credit"},
        ],
        "score_outputs": public_task["scoring"]["metrics"],
        "semantic_success": {
            "task_family": public_task["semantic_task"]["task_family"],
            "required_evidence": public_task["semantic_task"]["success_evidence"],
            "evaluator_only_fields": ["intervention schedule", "ground truth identity", "reference solution"],
        },
        "hidden_intervention_policy": {"member_exit": "A declared possibility may be revealed only when it occurs."},
        "benchmark_qa_requirements": _json_ready(deepcopy(dict(qa))),
        "release_qa": ["scene_and_asset_closure", "route_reachability", "provenance_integrity", "reference_solution_completion"],
        "separation": "Release QA validates the task package and is not a participant admission requirement.",
    }
    validate_hidden_evaluator(evaluator, task_spec=public_task)
    return evaluator


def _baseline_manifest(public_task: Mapping[str, Any], *, entrypoint: str) -> dict[str, Any]:
    manifest = {
        "schema": BASELINE_MANIFEST_SCHEMA,
        "version": CONTRACT_VERSION,
        "task_id": public_task["task_id"],
        "public_task_sha256": canonical_sha256(public_task),
        "release_status": public_task["release_status"],
        "execution_validated": False,
        "role": "optional published reference solvers; not task requirements or normative answers",
        "entrypoint": entrypoint,
        "consumes": ["participant-visible events", "public objectives and rules"],
        "solvers": [
            {"id": "scene_public_greedy_v2", "version": 2, "parameters": {},
             "behavior": "rank eligible agents from participant-visible state, allocate or reassign phase-ready objectives, and submit legal navigation goals"},
        ],
        "limitations": ["No optimality claim.", "No reference assignment or expected route is embedded."],
    }
    validate_baseline_manifest(manifest, task_spec=public_task)
    return manifest


def compile_scene_challenge(
    scenario_path: str | Path,
    *,
    scene_asset: str,
    task_contract: Mapping[str, Any],
    participant_requirements: list[dict[str, Any]],
    qa_requirements: Mapping[str, Any],
    business_rules: Mapping[str, Any] | None = None,
    scoring_metrics: list[str] | None = None,
    physical_limitations: list[str] | None = None,
    baseline_entrypoint: str = "demo.benchmark.scene_challenge",
) -> dict[str, dict[str, Any]]:
    path = Path(scenario_path)
    scenario = load_scenario(path)
    public = _public_task(
        scenario, scenario_sha256=file_sha256(path), scene_asset=scene_asset,
        task_contract=_json_ready(task_contract), participant_requirements=_json_ready(participant_requirements),
        business_rules=business_rules, scoring_metrics=scoring_metrics or [
            "task_success", "partial_completion", "makespan_s", "wait_time_s", "resource_conflicts", "deadlocks", "replans",
        ], physical_limitations=physical_limitations or [
            "Geometric clearance is not a collision-safety margin.",
            "No physical manipulation, rated payload, grasp force, wind resistance, or load-dynamics claim.",
        ],
    )
    return {
        "public_task": public,
        "participant_api": _participant_api(public),
        "hidden_evaluator": _hidden_evaluator(public, scenario, qa=qa_requirements),
        "baseline_manifest": _baseline_manifest(public, entrypoint=baseline_entrypoint),
    }


def write_scene_challenge(bundle: Mapping[str, Mapping[str, Any]], output_dir: str | Path) -> dict[str, Path]:
    return write_challenge_bundle(bundle, Path(output_dir))


__all__ = [
    "audit_generated_scene_scenario", "build_scene_runtime_contract",
    "compile_scene_challenge", "evaluate_challenge_report", "evaluate_objectives",
    "generate_scene_scenario", "pending_release_gate", "review_task_design",
    "write_scene_challenge",
]
