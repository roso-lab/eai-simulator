"""Compile the Factory F-INS dispatch challenge into public/private artifacts."""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

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
from demo.benchmark.semantic_contract import objective_semantics_for, semantic_for_scene, validate_semantic_task_contract
from demo.benchmark.domain_evaluator import build_domain_contract, public_domain_requirements
from demo.benchmark.factor_matrix import build_hidden_factor_policy, build_public_factor_manifest
from demo.benchmark.authoring import build_public_task_skeleton
from demo.benchmark.scene_challenge import pending_release_gate
from demo.factory_benchmark.inventory_dispatch import PUBLIC_RULES, validate_dispatch_task
from demo.factory_benchmark.scene_contract import FACTORY_TASK_CONTRACTS
from demo.factory_benchmark.tasks import (
    factory_benchmark_qa_requirements,
    factory_participant_requirements, review_factory_task_design,
)


DEFAULT_SCENARIO = Path(__file__).parent / "scenarios" / "inspection_dispatch_01.json"


def _public_robot(robot):
    return {
        "id": robot["id"],
        "type": robot["type"],
        "controller": deepcopy(robot["controller"]),
        "attachments": deepcopy(robot["attachments"]),
        "initial_pose": deepcopy(robot["spawn_pose"]),
    }


def _public_objective(node, semantic_task=None):
    objective = {
        "id": node["id"],
        "skill": node["skill_key"],
        "prerequisites": list(node["prerequisites"]),
        "timeout_s": node["timeout_s"],
        "duration_s": node.get("duration_s"),
        "resources": list(node["resources"]),
    }
    for key in ("target_ref", "observation_ref", "scan_height_m", "scan_yaw_rad", "wait_yaw_rad", "vertical"):
        if node.get(key) is not None:
            objective[key] = deepcopy(node[key])
    if node["id"] == "route_confirmation":
        objective.pop("target_ref", None)
        objective["target_candidates"] = sorted(PUBLIC_RULES["destinations"])
        objective["selection_rule_ref"] = "inventory_destination_rule"
    if semantic_task is not None:
        meaning = objective_semantics_for("factory", objective)
        objective.update({
            "role": meaning["role"],
            "purpose": meaning["purpose"],
            "deliverable": meaning["deliverable"],
            "acceptance_evidence": list(meaning["acceptance_evidence"]),
            "allowed_alternatives": list(meaning["allowed_alternatives"]),
        })
    return objective


def _public_business_rules():
    rules = {
        "inventory_destination_rule": {
            "destinations_by_marker_id": deepcopy(PUBLIC_RULES["destinations_by_marker_id"]),
            "destinations": deepcopy(PUBLIC_RULES["destinations"]),
            "message_valid_for_s": PUBLIC_RULES["message_valid_for_s"],
            "operation": PUBLIC_RULES["operation"],
            "participant_choice": (
                "The participant chooses whether to wait, consume messages, or navigate."
            ),
        }
    }
    rules["task_skeleton"] = build_public_task_skeleton("factory")
    return rules


def build_public_task_spec(scenario: dict, *, scenario_sha256: str) -> dict:
    validate_dispatch_task(scenario)
    task_id = scenario["benchmark_task_id"]
    participant_requirements = factory_participant_requirements(task_id)
    semantic_task = semantic_for_scene("factory")
    validate_semantic_task_contract(semantic_task, scene_key="factory")
    factor_manifest = build_public_factor_manifest("factory")
    public = {
        "schema": PUBLIC_TASK_SCHEMA,
        "version": CONTRACT_VERSION,
        "task_id": task_id,
        "task_name": scenario["task_name"],
        "release_status": "pending",
        "execution_validated": False,
        "release_gate": pending_release_gate("factory"),
        "scene": {
            "scene_key": scenario["scene_key"],
            "scene_asset": "scene/factory/factory.usd",
            "scenario_sha256": scenario_sha256,
            "task_contract_sha256": canonical_sha256(FACTORY_TASK_CONTRACTS[task_id]),
        },
        "initial_fleet": [_public_robot(robot) for robot in scenario["robots"]],
        "targets": deepcopy(scenario["composition"]["targets"]),
        "objectives": [_public_objective(node, semantic_task) for node in scenario["phase_graph"]["nodes"]],
        "semantic_task": semantic_task,
        "factor_manifest": factor_manifest,
        "domain_requirements": public_domain_requirements(build_domain_contract(scenario)),
        "participant_requirements": participant_requirements,
        "resources": sorted({resource for node in scenario["phase_graph"]["nodes"]
                             for resource in node["resources"]}),
        "business_rules": _public_business_rules(),
        "participant": {
            "observation_mode": "fused_detection_messages",
            "api_schema": PARTICIPANT_API_SCHEMA,
            "runtime_status": "f_ins_dispatch_programmatic_adapter_v1",
            "controlled_objectives": ["route_confirmation"],
            "benchmark_managed_objectives": ["aerial_view", "ground_approach", "ground_view"],
            "allowed_action_types": [
                "navigate_goal", "wait", "consume_message",
            ],
        },
        "model_io": {
            "protocol": "evidence_based_station_dispatch_v1",
            "inputs": [
                "public_task.json: factory scene, public fleet, candidate stations, objective scope and information rules",
                "participant_api.json: event/action envelopes and dispatch implementation status",
                "runtime events: visible observation/message/lifecycle events delivered by the benchmark",
            ],
            "given_by_benchmark": [
                "the managed evidence phases and the route_confirmation decision point",
                "public candidate station references and robot interfaces",
            ],
            "model_decides": [
                "whether to wait for or consume a currently visible message",
                "which public candidate station to select and how to navigate there",
            ],
            "model_does_not_decide_in_this_version": [
                "the hidden marker identity, future message schedule, managed evidence phases, or a production recovery plan",
            ],
            "outputs": [
                "participant_action envelopes for route_confirmation, validated by participant_api.json",
                "optional explanatory text kept outside the machine score",
            ],
            "claims_not_accepted_as_evidence": [
                "self-reported station correctness, marker identity, pose, equipment safety, or production recovery",
            ],
        },
        "scoring": {
            "primary": "correct_station_arrival",
            "metrics": [
                "task_success", "partial_completion", "makespan_s", "wait_time_s",
                "resource_conflicts", "deadlocks", "replans", "observation_freshness_s",
                "minimum_robot_distance_m",
            ],
            "geometric_constraints": [
                "forbidden_ground_regions", "route_inflation", "resource_reservations",
            ],
        },
        "physical_claim": {
            "validated_scope": "logic_scoring_and_navigation_camera_scheduling_execution",
            "limitations": [
                "Geometric clearance and spacing metrics are not collision-safety margins.",
                "No physical manipulation, rated payload, grasp force, wind resistance, or load-dynamics claim.",
            ],
        },
    }
    validate_public_task_spec(public)
    return public


def build_participant_api(public_task: dict) -> dict:
    api = {
        "schema": PARTICIPANT_API_SCHEMA,
        "version": CONTRACT_VERSION,
        "task_id": public_task["task_id"],
        "public_task_sha256": canonical_sha256(public_task),
        "release_status": public_task["release_status"],
        "execution_validated": False,
        "event_envelope": {
            "schema": "eai.benchmark.participant_event",
            "version": 1,
            "fields": ["sequence", "time_s", "type", "payload"],
        },
        "event_types": {
            "episode_started": {"payload_fields": ["fleet", "objectives", "resources"]},
            "phase_ready": {"payload_fields": ["node_id", "eligible_agent_ids"]},
            "observation_message": {
                "payload_fields": ["message_id", "producer_id", "published_at_s", "valid_until_s", "estimate"],
                "visibility": "participant_visible",
            },
            "member_exit": {"payload_fields": ["agent_id", "affected_node_ids"]},
            "member_join": {"payload_fields": ["agent_id", "capability_declaration", "capability_version"]},
            "capability_change": {"payload_fields": ["agent_id", "capability_declaration", "capability_version", "affected_node_ids"]},
            "action_result": {"payload_fields": ["action_id", "status", "reason"]},
            "tick": {"payload_fields": ["robot_states", "phase_states", "resource_states"]},
        },
        "action_envelope": {
            "schema": "eai.benchmark.participant_action",
            "version": 1,
            "fields": ["action_id", "submitted_at_s", "type", "payload"],
        },
        "action_types": {
            "allocate": {"payload_fields": ["node_id", "agent_ids"]},
            "navigate_goal": {"payload_fields": ["node_id", "agent_id", "target_ref"]},
            "wait": {"payload_fields": ["node_id", "agent_id", "duration_s"]},
            "reassign": {"payload_fields": ["node_id", "agent_ids", "reason"]},
            "consume_message": {"payload_fields": ["message_id", "consumer_id"]},
        },
        "ordering": "sequence is strictly increasing; submitted_at_s uses simulation time",
        "truth_boundary": (
            "No evaluator truth, future intervention time, reference assignment, or baseline decision is exposed."
        ),
        "implementation_status": {
            "contract_validation": "available",
            "isaac_participant_adapter": "f_ins_dispatch_programmatic_v1",
            "supported_action_types": ["consume_message", "wait", "navigate_goal"],
            "pending_action_types": ["allocate", "reassign"],
            "lifecycle_events": "protocol_session_available; F-INS Isaac faults still unsupported",
        },
    }
    validate_participant_api(api, task_spec=public_task)
    return api


def build_hidden_evaluator(public_task: dict, scenario: dict) -> dict:
    scenario_snapshot = deepcopy(scenario)
    scenario_snapshot.setdefault("semantic_task", deepcopy(public_task["semantic_task"]))
    task_design_review = review_factory_task_design(scenario, public_task["task_id"])
    evaluator = {
        "schema": HIDDEN_EVALUATOR_SCHEMA,
        "version": CONTRACT_VERSION,
        "task_id": public_task["task_id"],
        "public_task_sha256": canonical_sha256(public_task),
        "release_status": public_task["release_status"],
        "execution_validated": False,
        "scenario_snapshot": scenario_snapshot,
        "domain_contract": build_domain_contract(scenario_snapshot),
        "factor_policy": build_hidden_factor_policy("factory"),
        "truth_sources": {
            "inventory_identity": "runtime.inventory_detection.world_variant.marker_id",
            "station_positions": "scenario_snapshot.composition.targets",
            "pose_samples": "runtime.pose_trajectory",
            "phase_states": "runtime.scheduling.states",
            "interventions": "episode_condition.hidden_interventions",
        },
        "success_predicates": [
            {
                "id": "correct_station_arrival",
                "evaluator": "demo.factory_benchmark.inspect_dispatch.evaluate_dispatch_arrival",
                "requires": ["inventory_identity", "pose_samples", "phase_states"],
            },
            {
                "id": "allowed_action_trace",
                "evaluator": "demo.benchmark.challenge_contract.validate_participant_action",
                "requires": ["participant_action_log"],
            },
        ],
        "score_outputs": public_task["scoring"]["metrics"],
        "semantic_success": {
            "task_family": public_task["semantic_task"]["task_family"],
            "required_evidence": public_task["semantic_task"]["success_evidence"],
            "evaluator_only_fields": ["fault timing", "ground truth identity", "reference assignment"],
        },
        "hidden_intervention_policy": {
            "member_exit": "A declared possibility may be revealed only when the event occurs.",
            "message_delivery": "Delivery, delay, or withholding is recorded in the evaluator condition.",
        },
        "benchmark_qa_requirements": factory_benchmark_qa_requirements(public_task["task_id"]),
        "task_design_review": task_design_review,
        "release_qa": [
            "scene_and_asset_closure", "route_reachability", "camera_window_generation",
            "pixel_scoreability", "provenance_integrity", "reference_solution_completion",
        ],
        "separation": "Release QA validates the task package and is not a participant admission requirement.",
    }
    validate_hidden_evaluator(evaluator, task_spec=public_task)
    return evaluator


def build_baseline_manifest(public_task: dict) -> dict:
    manifest = {
        "schema": BASELINE_MANIFEST_SCHEMA,
        "version": CONTRACT_VERSION,
        "task_id": public_task["task_id"],
        "public_task_sha256": canonical_sha256(public_task),
        "release_status": public_task["release_status"],
        "execution_validated": False,
        "role": "optional published reference solvers; not task requirements or normative answers",
        "entrypoint": "demo.factory_benchmark.baseline_solver.BaselineDispatchAdapter",
        "consumes": ["participant-visible events", "public business rules"],
        "solvers": [
            {
                "id": "immediate",
                "version": 1,
                "parameters": {"commit_when_phase_ready": True},
                "behavior": "commit immediately using currently delivered unexpired messages",
            },
            {
                "id": "wait_for_message",
                "version": 1,
                "parameters": {"max_wait_s": 65.0},
                "behavior": "wait within a fixed budget while a declared delayed message is pending",
            },
        ],
        "limitations": [
            "No optimality claim.",
            "No reference fleet allocation or expected route is embedded in this manifest.",
        ],
    }
    validate_baseline_manifest(manifest, task_spec=public_task)
    return manifest


def compile_challenge(scenario_path: Path = DEFAULT_SCENARIO) -> dict[str, dict]:
    scenario_path = Path(scenario_path)
    scenario = load_scenario(scenario_path)
    if scenario.get("benchmark_task_id") == "F-VERT-01":
        # The vertical probe has a different publication boundary: its route
        # intent is explicit, while physical support and scoreability are
        # still pending.  Keep it out of the F-INS dispatch compiler.
        from demo.factory_benchmark.vertical_challenge import compile_challenge as compile_vertical
        return compile_vertical(scenario_path)
    public_task = build_public_task_spec(scenario, scenario_sha256=file_sha256(scenario_path))
    return {
        "public_task": public_task,
        "participant_api": build_participant_api(public_task),
        "hidden_evaluator": build_hidden_evaluator(public_task, scenario),
        "baseline_manifest": build_baseline_manifest(public_task),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    paths = write_challenge_bundle(compile_challenge(args.scenario), args.output_dir)
    for name, path in paths.items():
        print(f"{name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_SCENARIO", "build_baseline_manifest", "build_hidden_evaluator",
    "build_participant_api", "build_public_task_spec", "compile_challenge",
]
