"""Compile the pending Factory F-VERT-01 motion probe without starting Isaac.

Usage (with repository packages on PYTHONPATH)::

    python -m demo.factory_benchmark.vertical_challenge --output-dir <new-directory>

Only ``release/`` is participant-visible; ``evaluator/`` stays with the author.
V2 requires numeric poses, so the scenario carries explicitly unmeasured
candidates. Compilation validates their declarations, never their geometry.
``navigate_goal`` is a route intent for a benchmark-managed controller, not
participant control of joint actions. The Isaac intent adapter and calibrated
reference traversal are pending. No compiler option promotes this candidate
to an executable/scored release or accepts self-reported climbing success.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

from EAI.scenario_spec import load_scenario
from demo.benchmark.challenge_contract import (
    BASELINE_MANIFEST_SCHEMA, CONTRACT_VERSION, HIDDEN_EVALUATOR_SCHEMA,
    PARTICIPANT_API_SCHEMA, PUBLIC_TASK_SCHEMA, validate_baseline_manifest,
    validate_hidden_evaluator, validate_participant_action, validate_participant_api,
    validate_public_task_spec, write_challenge_bundle,
)
from demo.benchmark.provenance import canonical_sha256, file_sha256
from demo.benchmark.semantic_contract import (
    objective_semantics_for, validate_semantic_task_contract,
)
from demo.factory_benchmark.prim_bindings import FACTORY_PRIM_BINDINGS
from demo.factory_benchmark.physx_vertical_evidence import (
    pending_physx_evidence, unknown_physx_evidence, validate_physx_evidence,
)


DEFAULT_SCENARIO = Path(__file__).parent / "scenarios" / "vertical_stair_01.json"
PROBE_SCOPE = "benchmark_managed_single_robot_stair_probe_route_intent_only"
_ACTION_FIELDS = {
    "allocate": ["node_id", "agent_ids"],
    "navigate_goal": ["node_id", "agent_id", "target_ref"],
    "wait": ["node_id", "agent_id", "duration_s"],
    "reassign": ["node_id", "agent_ids", "reason"],
    "consume_message": ["message_id", "consumer_id"],
}


def _load_static_stair_measurement(path: Path | None) -> dict | None:
    """Validate and compact a private authored-geometry measurement.

    This binds the selected flight and every USD layer hash.  It deliberately
    leaves collision support, connectivity and traversal acceptance false.
    """
    if path is None:
        return None
    path = Path(path).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    if (data.get("schema") != "eai.factory_stair_static_measurement"
            or data.get("version") != 1 or data.get("status") != "measured"
            or data.get("prim_path") != FACTORY_PRIM_BINDINGS["main_stair"]["prim_path"]):
        raise ValueError("stair measurement must be a measured main_stair v1 report")
    provenance = data.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("status") != "measured":
        raise ValueError("stair measurement requires complete layer provenance")
    root = provenance.get("root_layer")
    dependencies = provenance.get("dependency_layers")
    if (not isinstance(root, dict) or root.get("status") != "measured"
            or not isinstance(root.get("sha256"), str)
            or len(root["sha256"]) != 64
            or not isinstance(dependencies, list)
            or any(not isinstance(row, dict) or row.get("status") != "measured"
                   or not isinstance(row.get("sha256"), str)
                   or len(row["sha256"]) != 64 for row in dependencies)):
        raise ValueError("stair measurement layer hashes are incomplete")
    flights = (data.get("treads") or {}).get("flights")
    selected = [row for row in flights or [] if row.get("flight_id") == "flight_02"]
    if (len(selected) != 1 or selected[0].get("status") != "measured"
            or len(selected[0].get("treads") or []) < 2):
        raise ValueError("stair measurement requires measured flight_02")
    support_rows = (data.get("support_regions") or {}).get("by_flight")
    supports = [row for row in support_rows or [] if row.get("flight_id") == "flight_02"]
    if (len(supports) != 1 or supports[0].get("status") != "measured"
            or supports[0].get("start", {}).get("status") != "measured"
            or supports[0].get("end", {}).get("status") != "measured"):
        raise ValueError("flight_02 requires authored start and end support candidates")
    treads = selected[0]["treads"]
    if not treads[0]["support_z_m"] < treads[-1]["support_z_m"]:
        raise ValueError("flight_02 must ascend from ground toward the mezzanine")
    dependency_records = [{"identifier": row.get("identifier"),
                           "sha256": row["sha256"]} for row in dependencies]
    return {
        "status": "static_authored_geometry_measured",
        "selected_flight_id": "flight_02",
        "measurement_report_sha256": file_sha256(path),
        "root_layer_sha256": root["sha256"],
        "dependency_layers": dependency_records,
        "dependency_set_sha256": canonical_sha256(dependency_records),
        "flight": deepcopy(selected[0]),
        "supports": deepcopy(supports[0]),
        "net_width_m": deepcopy(data.get("net_width_m")),
        "physical_support_validated": False,
        "connectivity_validated": False,
        "traversal_validated": False,
        "limitations": [
            "authored polygons do not establish PhysX collision/contact support",
            "support candidates do not establish a connected robot-specific route",
            "the measurement is evaluator input and not a traversal result",
        ],
    }


def _load_physx_evidence(path: Path | None, *, scenario_sha256: str,
                         asset_sha256: str | None, robot_id: str) -> dict:
    """Load evaluator-only live evidence without promoting physical acceptance."""
    if path is None:
        return pending_physx_evidence()
    path = Path(path).resolve()
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return unknown_physx_evidence(
            f"physx_evidence_read_error:{type(exc).__name__}:{exc}",
            path=str(path))
    result = validate_physx_evidence(
        evidence, expected_scenario_sha256=scenario_sha256,
        expected_asset_sha256=asset_sha256, expected_robot_id=robot_id)
    return {"source_path": str(path), "validation": result, "record": deepcopy(evidence)}


def _admit_candidate(scenario):
    """Narrow the generic V2 vocabulary to this deliberately unverified probe."""
    if (scenario["scene_key"] != "factory"
            or scenario.get("benchmark_task_id") != "F-VERT-01"):
        raise ValueError("vertical challenge requires Factory F-VERT-01")
    if len(scenario["robots"]) != 1 or scenario["robots"][0]["type"] not in {"go2", "b2"}:
        raise ValueError("vertical challenge requires one Go2 or B2 candidate")
    if scenario.get("fault_events") or scenario["composition"]["entities"]:
        raise ValueError("vertical probe does not support interventions or composed props")
    robot = scenario["robots"][0]
    if robot["attachments"]:
        raise ValueError("vertical probe requires an unladen robot")
    nodes = scenario["phase_graph"]["nodes"]
    if len(nodes) != 1 or len(scenario["composition"]["targets"]) != 1:
        raise ValueError("vertical probe requires exactly one objective and target")
    node = nodes[0]
    vertical = node.get("vertical") or {}
    if (node["id"] != "stair_access_check" or node["skill_key"] != "navigate"
            or node["target_ref"] != "mezzanine_stair_exit"
            or node["preferred_agent"] != robot["id"] or node["prerequisites"]
            or node.get("agents") or node.get("observation_ref") or node.get("recovery_for")
            or node.get("resources") != ["main_stair"]):
        raise ValueError("expected an independent stair_access_check navigation objective")
    if any(vertical.get(key) != value for key, value in {
        "link_id": "main_stair", "kind": "stair", "start_level": "ground",
        "goal_level": "mezzanine", "support_probe": "required",
    }.items()):
        raise ValueError("main_stair requires ground-to-mezzanine stair support_probe=required")
    semantic = scenario.get("semantic_task")
    validate_semantic_task_contract(semantic, scene_key="factory")
    declaration = semantic["objective_semantics"]
    if declaration.get("execution_validated") is not False or declaration.get("release_status") != "pending":
        raise ValueError("candidate must declare execution_validated=false and release_status=pending")
    geometry = declaration.get("geometry_declaration")
    if not isinstance(geometry, dict) or geometry.get("coordinate_frame") != "factory_world_m":
        raise ValueError("candidate requires geometry_declaration in factory_world_m")
    for name, field in (("approach", "measured_waypoints_xy_m"),
                        ("start", "measured_support_xyz_m"),
                        ("goal", "measured_support_xyz_m")):
        row = geometry.get(name)
        if (not isinstance(row, dict) or row.get("status") != "unknown"
                or field not in row or row[field] is not None
                or not isinstance(row.get("source"), str) or not row["source"].strip()):
            raise ValueError(f"{name}: only explicitly unknown geometry is supported by this compiler")
    approach = vertical.get("approach_waypoints")
    if not approach or geometry["approach"].get("candidate_waypoints_xy_m") != approach:
        raise ValueError("approach candidates must match explicit vertical approach_waypoints")
    if geometry["start"].get("candidate_root_xyz_m") != robot["spawn_pose"]["position"]:
        raise ValueError("start candidate must match the scenario robot spawn")
    entry = geometry["start"].get("candidate_surface_entry_xy_m")
    # This is the current executor's candidate, NOT surveyed geometry. Changing
    # it needs an executor change, outside this compiler's compatibility scope.
    if entry != [1.5, -2.25]:
        raise ValueError("surface-entry candidate differs from the current probe executor")
    target = scenario["composition"]["targets"][0]
    if geometry["goal"].get("candidate_support_xyz_m") != target["position"]:
        raise ValueError("goal candidate must match the scenario target")
    if target["position"][2] != 1.57 or robot["spawn_pose"]["position"][2] not in {0.4, 0.58}:
        raise ValueError("candidate must use the declared ground and mezzanine levels")
    if (semantic["team_lifecycle"].get("enabled_events") != []
            or semantic["communication"].get("enabled_conditions") != []):
        raise ValueError("lifecycle and communication variations are disabled in this probe")
    return node, robot, target, geometry


def _public_declarations(scenario, geometry):
    """Project open semantic dictionaries; private extensions stay private."""
    public_geometry = {"coordinate_frame": geometry["coordinate_frame"]}
    for name, fields in {
        "approach": ("candidate_waypoints_xy_m", "measured_waypoints_xy_m"),
        "start": ("candidate_root_xyz_m", "candidate_surface_entry_xy_m", "measured_support_xyz_m"),
        "goal": ("candidate_support_xyz_m", "measured_support_xyz_m"),
    }.items():
        public_geometry[name] = {key: deepcopy(geometry[name][key])
                                 for key in ("status", "source", *fields)}
    public_geometry["limitations"] = [
        "All numeric poses are unmeasured candidates required by V2 schema.",
        "The stair AABB maximum is not a walkable floor or proof of connectivity.",
    ]
    source = scenario["semantic_task"]
    semantic = {key: deepcopy(source[key]) for key in (
        "task_family", "domain", "mission_brief", "artifacts", "factors", "success_evidence")}
    semantic["objective_semantics"] = {key: deepcopy(source["objective_semantics"][key])
                                       for key in ("purpose", "deliverable", "acceptance_evidence",
                                                   "allowed_alternatives")}
    semantic["objective_semantics"].update(
        execution_validated=False, release_status="pending",
        geometry_declaration=deepcopy(public_geometry))
    semantic["team_lifecycle"] = {
        "events": ["member_join", "member_exit", "capability_change"],
        "enabled_events": [], "status": "design_vocabulary_only_fixed_single_robot_probe",
    }
    semantic["communication"] = {
        "conditions": ["immediate", "delayed", "dropped", "bandwidth_limited", "stale"],
        "enabled_conditions": [], "status": "design_vocabulary_only_no_message_delivery_experiment",
    }
    return semantic, public_geometry


def compile_challenge(scenario_path: Path = DEFAULT_SCENARIO, *,
                      stair_measurement_path: Path | None = None,
                      physx_evidence_path: Path | None = None) -> dict[str, dict]:
    """Return four hash-bound artifacts; all physical release gates stay pending."""
    scenario_path = Path(scenario_path)
    scenario = load_scenario(scenario_path)
    node, robot, target, geometry = _admit_candidate(scenario)
    static_measurement = _load_static_stair_measurement(stair_measurement_path)
    physx_evidence = _load_physx_evidence(
        physx_evidence_path, scenario_sha256=file_sha256(scenario_path),
        asset_sha256=(static_measurement["root_layer_sha256"]
                      if static_measurement is not None else None),
        robot_id=robot["id"])
    physx_validation = physx_evidence.get("validation", physx_evidence)
    semantic, public_geometry = _public_declarations(scenario, geometry)
    meaning = objective_semantics_for("factory", node)
    objective = {key: deepcopy(node[key]) for key in (
        "id", "target_ref", "prerequisites", "timeout_s", "resources", "vertical")}
    objective["skill"] = node["skill_key"]
    objective.update({key: deepcopy(meaning[key]) for key in (
        "role", "purpose", "deliverable", "acceptance_evidence", "allowed_alternatives")})
    objective["alternative_scope"] = "Catalog lifecycle alternatives are disabled in this single-robot probe."
    public = {
        "schema": PUBLIC_TASK_SCHEMA, "version": CONTRACT_VERSION,
        "task_id": "F-VERT-01", "task_name": scenario["task_name"],
        "execution_validated": False, "probe_scope": PROBE_SCOPE,
        "release_status": "pending",
        "release_gate": {
            "contract_publishable": True, "benchmark_ready": False,
            "authoring_status": "passed",
            "authoring_scope": "vertical_probe_route_intent_contract",
            "execution_status": "external_pending",
            "executable": False, "scoreable": False,
            "completed": ["four_layer_contract_compilation",
                          "public_private_information_boundary",
                          "explicit_stair_route_intent", *(
                              ["hash_bound_static_stair_geometry"]
                              if static_measurement is not None else [])],
            "blockers": (["physx_support_and_connectivity"] if static_measurement is not None
                         else ["measured_stair_geometry_and_connectivity",
                               "immutable_asset_binding"])
                        + ["calibrated_support_observation",
                           "reference_physical_acceptance", "isaac_route_intent_adapter"],
            "missing_evidence_result": "unknown",
        },
        "scene": {
            "scene_key": "factory", "scene_asset": "scene/factory/factory.usd",
            "scenario_sha256": file_sha256(scenario_path),
            "asset_sha256": (static_measurement["root_layer_sha256"]
                             if static_measurement is not None else None),
            "asset_binding_status": (
                "static_geometry_hash_bound_physics_unvalidated"
                if static_measurement is not None else "pending"),
        },
        "initial_fleet": [{"id": robot["id"], "type": robot["type"],
                           "controller": deepcopy(robot["controller"]),
                           "attachments": [], "initial_pose": deepcopy(robot["spawn_pose"]),
                           "initial_pose_status": "unmeasured_candidate"}],
        "targets": [{**deepcopy(target), "position_status": "unmeasured_candidate"}],
        "objectives": [objective], "resources": ["main_stair"],
        "geometry_declaration": public_geometry,
        "semantic_task": semantic,
        "participant": {
            "observation_mode": "public_geometry_declarations_and_probe_status",
            "api_schema": PARTICIPANT_API_SCHEMA,
            "runtime_status": "pending_isaac_route_intent_adapter",
            "controlled_objectives": [node["id"]],
            "allowed_action_types": ["navigate_goal", "wait"],
        },
        "model_io": {
            "inputs": ["public_task.json: scene, fixed fleet, vertical objective and unknown geometry",
                       "participant_api.json: route-intent envelopes and implementation limits"],
            "runtime_inputs_status": "pending_adapter; no live events are supplied by this compiler",
            "model_decides": ["submit the named stair route intent for the supplied robot",
                              "defer the intent using wait within the objective timeout"],
            "benchmark_manages": ["ground-route admission and support calibration",
                                  "bounded velocity controller and post-step physics sampling",
                                  "support failure stop and independent hidden acceptance"],
            "not_measured": ["autonomous task decomposition", "heterogeneous team cooperation",
                             "dynamic membership", "communication adaptation"],
        },
        "scoring": {
            "status": "pending_reference_physical_acceptance",
            "metrics": ["route_intent_validity", "supported_vertical_traversal"],
            "missing_evidence_result": "unknown",
            "rule": "Intent validation and reported completion never establish physical success.",
        },
    }
    validate_public_task_spec(public)
    binding = {"version": CONTRACT_VERSION, "task_id": public["task_id"],
               "public_task_sha256": canonical_sha256(public)}
    api = {
        "schema": PARTICIPANT_API_SCHEMA, **binding,
        "execution_validated": False, "release_status": "pending",
        "probe_scope": PROBE_SCOPE,
        "action_envelope": {"schema": "eai.benchmark.participant_action", "version": 1,
                            "fields": ["action_id", "submitted_at_s", "type", "payload"]},
        # The shared v1 API requires all five vocabulary entries. Authorization
        # is narrower and is enforced by public.participant.allowed_action_types.
        "action_types": {name: {"payload_fields": fields,
                                "allowed": name in {"navigate_goal", "wait"}}
                         for name, fields in _ACTION_FIELDS.items()},
        "event_envelope": {"schema": "eai.benchmark.participant_event", "version": 1,
                           "fields": ["sequence", "time_s", "type", "payload"]},
        "event_types": {"action_result": {"payload_fields": ["action_id", "status", "reason"]}},
        "route_intent": {
            "action_type": "navigate_goal", "node_id": node["id"],
            "link_id": "main_stair", "target_ref": target["id"],
            "meaning": "Request the declared approach/start/goal sequence; no custom waypoints, joint commands or pose writes.",
            "validation_entrypoint": "demo.factory_benchmark.vertical_challenge.validate_route_intent",
            "on_valid_submission": "validated_intent_only; no motion is executed",
            "on_missing_support": "zero_command_stop; outcome unknown",
        },
        "implementation_status": {
            "contract_validation": "available",
            "isaac_participant_adapter": "pending",
            "executed_action_types": [], "events_emitted_by_compiler": [],
        },
    }
    hidden = {
        "schema": HIDDEN_EVALUATOR_SCHEMA, **binding,
        "execution_validated": False, "release_status": "pending",
        "scenario_snapshot": deepcopy(scenario),
        "truth_sources": {
            "support_truth": "evaluator.support_truth; calibrated live foot/contact observations",
            "pose_trace": "post-env.step root XYZ/WXYZ with simulation timestamps",
            "reference_physics": "evaluator.reference_physical_acceptance",
            "intent_log": "validated participant submissions, not self-reported completion",
        },
        "support_truth": {
            "status": ("observed" if physx_validation.get("status") == "observed"
                        else "unknown"),
            "calibration_id": physx_validation.get("calibration_id"),
            "measured_start_support_xyz_m": None, "measured_goal_support_xyz_m": None,
            "measured_surface_profile": None,
            "candidate_prim_bindings": {key: deepcopy(FACTORY_PRIM_BINDINGS[key])
                                        for key in ("main_stair", "mezzanine_surface")},
            "binding_validation": (
                "static_geometry_hash_bound_collision_support_pending"
                if static_measurement is not None
                else "pending_asset_hash_and_collision_support_measurement"),
            "static_geometry": deepcopy(static_measurement),
            "physx_evidence": physx_evidence,
        },
        "reference_physical_acceptance": {
            "status": "pending", "record": None, "reference_trajectory": None,
            "physx_evidence_status": physx_validation.get("status", "unknown"),
            "requirements": ["hash-bound scene, robot and controller configuration",
                             "measured stair treads, clear width and connected walkable endpoints",
                             "independently calibrated foot/contact stream",
                             "continuous post-step XYZ/WXYZ, support, health and progress trace",
                             "support-relative ascent and healthy target dwell",
                             "no teleport, reset recovery, target-Z or bbox-as-floor shortcut"],
            "candidate_probe_entrypoint": "tools.scenario_generation.vertical_task",
            "acceptance_evaluator": None,
            "candidate_report_is_acceptance": False,
        },
        "success_predicates": [
            {"id": "allowed_route_intent", "evaluator": api["route_intent"]["validation_entrypoint"],
             "requires": ["intent_log"]},
            {"id": "supported_vertical_traversal", "evaluator": None,
             "status": "pending_independent_physics_evaluator",
             "requires": ["support_truth", "pose_trace", "reference_physics"]},
        ],
        "score_outputs": deepcopy(public["scoring"]["metrics"]),
        "missing_evidence_result": "unknown",
    }
    baseline = {
        "schema": BASELINE_MANIFEST_SCHEMA, **binding,
        "execution_validated": False, "release_status": "pending",
        "probe_scope": PROBE_SCOPE,
        "role": "benchmark-managed candidate motion baseline; not a participant solution",
        "solvers": [{"id": "bounded_stair_surface_probe", "version": 1,
                     "entrypoint": "tools.scenario_generation.vertical_task",
                     "status": ("static_geometry_selected_support_calibration_pending"
                                if static_measurement is not None
                                else "pending_calibrated_support_and_geometry")}],
        "limitations": ["No validated traversal or optimality claim.",
                        "Route-intent execution adapter is pending."],
    }
    validate_participant_api(api, task_spec=public)
    validate_hidden_evaluator(hidden, task_spec=public)
    validate_baseline_manifest(baseline, task_spec=public)
    return {"public_task": public, "participant_api": api,
            "hidden_evaluator": hidden, "baseline_manifest": baseline}


def validate_route_intent(action, *, api, task_spec):
    """Validate a submission without authorizing motion or accepting an outcome."""
    validate_participant_api(api, task_spec=task_spec)
    if task_spec.get("task_id") != "F-VERT-01" or task_spec.get("probe_scope") != PROBE_SCOPE:
        raise ValueError("route intent requires the F-VERT-01 probe contract")
    if not isinstance(action, dict) or set(action) != {
        "schema", "version", "action_id", "submitted_at_s", "type", "payload",
    }:
        raise ValueError("route intent must contain only the declared action envelope fields")
    # Check IDs before the shared generic validator, which permits a null agent
    # for action kinds that do not have an agent_id field.
    payload = action.get("payload")
    if not isinstance(payload, dict) or not isinstance(payload.get("agent_id"), str):
        raise ValueError("route intent requires an explicit agent_id")
    result = validate_participant_action(action, api=api, task_spec=task_spec)
    if result["type"] == "wait":
        timeout = task_spec["objectives"][0]["timeout_s"]
        if result["payload"]["duration_s"] > timeout:
            raise ValueError("wait exceeds the objective timeout")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--stair-measurement", type=Path,
                        help="private hash-bound output from measure_factory_stair.py")
    parser.add_argument("--physx-evidence", type=Path,
                        help="private post-step PhysX evidence from an Isaac reference capture")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    paths = write_challenge_bundle(
        compile_challenge(args.scenario, stair_measurement_path=args.stair_measurement,
                          physx_evidence_path=args.physx_evidence),
        args.output_dir)
    print("F-VERT-01: pending; execution_validated=false; route-intent contract only")
    for name, path in paths.items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
