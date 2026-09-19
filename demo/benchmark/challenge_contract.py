"""Pure-Python contracts for publishing and scoring benchmark challenges."""

from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
from typing import Any, Mapping

from demo.benchmark.provenance import canonical_sha256


PUBLIC_TASK_SCHEMA = "eai.benchmark.public_task_spec"
PARTICIPANT_API_SCHEMA = "eai.benchmark.participant_api"
HIDDEN_EVALUATOR_SCHEMA = "eai.benchmark.hidden_evaluator"
BASELINE_MANIFEST_SCHEMA = "eai.benchmark.baseline_manifest"
CONTRACT_VERSION = 1

_PUBLIC_FORBIDDEN_KEYS = {
    "baseline_assignment",
    "baseline_decision",
    "evaluator_fixture",
    "expected_marker_id",
    "ground_truth",
    "hidden_fault_time_s",
    "inventory_label_id",
    "preferred_agent",
    "reference_assignment",
    "reference_decision",
    "true_marker_id",
}
_ACTION_FIELDS = {
    "allocate": {"node_id", "agent_ids"},
    "navigate_goal": {"node_id", "agent_id", "target_ref"},
    "wait": {"node_id", "agent_id", "duration_s"},
    "reassign": {"node_id", "agent_ids", "reason"},
    "consume_message": {"message_id", "consumer_id"},
}


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _finite_nonnegative(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite nonnegative number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{label} must be a finite nonnegative number")
    return result


def _find_forbidden_keys(value: Any, *, path: str = "$") -> list[str]:
    found = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            child = f"{path}.{key}"
            if key in _PUBLIC_FORBIDDEN_KEYS:
                found.append(child)
            found.extend(_find_forbidden_keys(item, path=child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_find_forbidden_keys(item, path=f"{path}[{index}]"))
    return found


def validate_public_task_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    spec = _require_mapping(spec, "public task spec")
    if spec.get("schema") != PUBLIC_TASK_SCHEMA or spec.get("version") != CONTRACT_VERSION:
        raise ValueError("unsupported public task spec schema")
    task_id = _require_string(spec.get("task_id"), "public task id")
    scene = _require_mapping(spec.get("scene"), "public scene")
    _require_string(scene.get("scene_key"), "public scene key")
    _require_string(scene.get("scenario_sha256"), "public scenario hash")
    fleet = spec.get("initial_fleet")
    if not isinstance(fleet, list) or not fleet:
        raise ValueError("public task requires a nonempty initial_fleet")
    robot_ids = [_require_string(robot.get("id"), "public robot id")
                 for robot in map(lambda value: _require_mapping(value, "public robot"), fleet)]
    if len(robot_ids) != len(set(robot_ids)):
        raise ValueError("public robot ids must be unique")
    objectives = spec.get("objectives")
    if not isinstance(objectives, list) or not objectives:
        raise ValueError("public task requires objectives")
    objective_ids = [_require_string(item.get("id"), "public objective id")
                     for item in map(lambda value: _require_mapping(value, "public objective"), objectives)]
    if len(objective_ids) != len(set(objective_ids)):
        raise ValueError("public objective ids must be unique")
    known_objectives = set(objective_ids)
    for objective in objectives:
        unknown = set(objective.get("prerequisites", ())) - known_objectives
        if unknown:
            raise ValueError(f"{objective['id']}: unknown prerequisites {sorted(unknown)}")
    participant = _require_mapping(spec.get("participant"), "participant declaration")
    for key in ("observation_mode", "api_schema", "allowed_action_types"):
        if not participant.get(key):
            raise ValueError(f"participant declaration requires {key}")
    scoring = _require_mapping(spec.get("scoring"), "public scoring declaration")
    if not isinstance(scoring.get("metrics"), list) or not scoring["metrics"]:
        raise ValueError("public scoring declaration requires metrics")
    semantic = _require_mapping(spec.get("semantic_task"), "semantic task contract")
    from demo.benchmark.semantic_contract import validate_semantic_task_contract
    validate_semantic_task_contract(semantic, scene_key=scene["scene_key"])
    # Archived v1 bundles predate factor metadata. Keep them readable, without
    # granting them any factor-experiment validation.
    if "factor_manifest" in spec:
        from demo.benchmark.factor_matrix import validate_factor_manifest
        validate_factor_manifest(spec["factor_manifest"], scene_key=scene["scene_key"])
    forbidden = _find_forbidden_keys(spec)
    if forbidden:
        raise ValueError(f"public task leaks evaluator or baseline fields: {forbidden}")
    return {"task_id": task_id, "validated": True, "sha256": canonical_sha256(spec)}


def validate_participant_api(api: Mapping[str, Any], *, task_spec: Mapping[str, Any]) -> dict[str, Any]:
    api = _require_mapping(api, "participant API")
    validate_public_task_spec(task_spec)
    if api.get("schema") != PARTICIPANT_API_SCHEMA or api.get("version") != CONTRACT_VERSION:
        raise ValueError("unsupported participant API schema")
    if api.get("task_id") != task_spec.get("task_id"):
        raise ValueError("participant API task id differs from public task")
    if api.get("public_task_sha256") != canonical_sha256(task_spec):
        raise ValueError("participant API is not bound to the public task")
    action_types = api.get("action_types")
    if not isinstance(action_types, Mapping) or set(action_types) != set(_ACTION_FIELDS):
        raise ValueError("participant API must declare the complete action vocabulary")
    event_types = api.get("event_types")
    if not isinstance(event_types, Mapping) or not event_types:
        raise ValueError("participant API requires event types")
    return {"task_id": api["task_id"], "validated": True, "sha256": canonical_sha256(api)}


def validate_hidden_evaluator(evaluator: Mapping[str, Any], *, task_spec: Mapping[str, Any]) -> dict[str, Any]:
    evaluator = _require_mapping(evaluator, "hidden evaluator")
    validate_public_task_spec(task_spec)
    if evaluator.get("schema") != HIDDEN_EVALUATOR_SCHEMA or evaluator.get("version") != CONTRACT_VERSION:
        raise ValueError("unsupported hidden evaluator schema")
    if evaluator.get("task_id") != task_spec.get("task_id"):
        raise ValueError("hidden evaluator task id differs from public task")
    if evaluator.get("public_task_sha256") != canonical_sha256(task_spec):
        raise ValueError("hidden evaluator is not bound to the public task")
    truth = _require_mapping(evaluator.get("truth_sources"), "hidden evaluator truth sources")
    success = evaluator.get("success_predicates")
    if not truth or not isinstance(success, list) or not success:
        raise ValueError("hidden evaluator requires truth sources and success predicates")
    if "domain_contract" in evaluator:
        from demo.benchmark.domain_evaluator import build_domain_contract, public_domain_requirements
        domain = build_domain_contract(_require_mapping(evaluator.get("scenario_snapshot"), "scenario snapshot"))
        if evaluator["domain_contract"] != domain:
            raise ValueError("hidden domain contract differs from scenario binding")
        if task_spec.get("domain_requirements") != public_domain_requirements(domain):
            raise ValueError("public domain requirements differ from hidden acceptance contract")
    if "factor_manifest" in task_spec or "factor_policy" in evaluator:
        from demo.benchmark.factor_matrix import build_hidden_factor_policy
        if "factor_manifest" not in task_spec or evaluator.get("factor_policy") != build_hidden_factor_policy(task_spec["scene"]["scene_key"]):
            raise ValueError("hidden factor policy differs from the reviewed public factor manifest")
    return {"task_id": evaluator["task_id"], "validated": True,
            "sha256": canonical_sha256(evaluator)}


def validate_baseline_manifest(manifest: Mapping[str, Any], *, task_spec: Mapping[str, Any]) -> dict[str, Any]:
    manifest = _require_mapping(manifest, "baseline manifest")
    validate_public_task_spec(task_spec)
    if manifest.get("schema") != BASELINE_MANIFEST_SCHEMA or manifest.get("version") != CONTRACT_VERSION:
        raise ValueError("unsupported baseline manifest schema")
    if manifest.get("task_id") != task_spec.get("task_id"):
        raise ValueError("baseline manifest task id differs from public task")
    if manifest.get("public_task_sha256") != canonical_sha256(task_spec):
        raise ValueError("baseline manifest is not bound to the public task")
    solvers = manifest.get("solvers")
    if not isinstance(solvers, list) or not solvers:
        raise ValueError("baseline manifest requires solvers")
    if any("assignment" in solver or "decision" in solver for solver in solvers):
        raise ValueError("baseline manifest must describe solvers, not reference answers")
    return {"task_id": manifest["task_id"], "validated": True,
            "sha256": canonical_sha256(manifest)}


def validate_participant_event(event: Mapping[str, Any], *, api: Mapping[str, Any]) -> dict[str, Any]:
    event = _require_mapping(event, "participant event")
    if event.get("schema") != "eai.benchmark.participant_event" or event.get("version") != 1:
        raise ValueError("unsupported participant event schema")
    if event.get("type") not in api.get("event_types", {}):
        raise ValueError("participant event type is not declared")
    sequence = event.get("sequence")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise ValueError("participant event sequence must be a nonnegative integer")
    _finite_nonnegative(event.get("time_s"), "participant event time")
    payload = _require_mapping(event.get("payload"), "participant event payload")
    required = set(api["event_types"][event["type"]].get("payload_fields", ()))
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"{event['type']} event is missing {sorted(missing)}")
    extra = payload.keys() - required
    if extra:
        raise ValueError(f"{event['type']} event has undeclared fields {sorted(extra)}")
    if event["type"] in {"member_join", "capability_change"}:
        _require_string(payload.get("agent_id"), "lifecycle agent id")
        declaration = _require_mapping(payload.get("capability_declaration"), "capability declaration")
        if not declaration:
            raise ValueError("capability declaration must not be empty")
        version = payload.get("capability_version")
        if type(version) is not int or version < 1:
            raise ValueError("capability version must be a positive integer")
    canonical_sha256(event)
    return deepcopy(dict(event))


def validate_participant_action(action: Mapping[str, Any], *, api: Mapping[str, Any],
                                task_spec: Mapping[str, Any]) -> dict[str, Any]:
    action = _require_mapping(action, "participant action")
    if action.get("schema") != "eai.benchmark.participant_action" or action.get("version") != 1:
        raise ValueError("unsupported participant action schema")
    action_type = action.get("type")
    if action_type not in api.get("action_types", {}):
        raise ValueError("participant action type is not declared")
    allowed = set(task_spec.get("participant", {}).get("allowed_action_types", ()))
    if action_type not in allowed:
        raise ValueError("participant action type is not allowed by this task")
    _require_string(action.get("action_id"), "participant action id")
    _finite_nonnegative(action.get("submitted_at_s"), "participant action submission time")
    payload = _require_mapping(action.get("payload"), "participant action payload")
    required = _ACTION_FIELDS[action_type]
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"{action_type} action is missing {sorted(missing)}")
    extra = payload.keys() - required
    if extra:
        raise ValueError(f"{action_type} action has undeclared fields {sorted(extra)}")
    known_agents = {robot["id"] for robot in task_spec["initial_fleet"]}
    known_nodes = {objective["id"] for objective in task_spec["objectives"]}
    agent_ids = payload.get("agent_ids", [payload.get("agent_id")])
    if not isinstance(agent_ids, list) or not agent_ids or len(agent_ids) != len(set(agent_ids)):
        raise ValueError("participant action agent_ids must be a nonempty unique list")
    if any(agent not in known_agents for agent in agent_ids if agent is not None):
        raise ValueError("participant action names an unknown agent")
    if payload.get("node_id") is not None and payload["node_id"] not in known_nodes:
        raise ValueError("participant action names an unknown objective")
    controlled_nodes = set(task_spec.get("participant", {}).get("controlled_objectives", ()))
    if payload.get("node_id") is not None and payload["node_id"] not in controlled_nodes:
        raise ValueError("participant action targets an objective outside the enabled runtime scope")
    if action_type == "navigate_goal":
        known_targets = {target["id"] for target in task_spec.get("targets", ())}
        if payload["target_ref"] not in known_targets:
            raise ValueError("participant action names an unknown target")
        objective = next(item for item in task_spec["objectives"]
                         if item["id"] == payload["node_id"])
        allowed_targets = set(objective.get("target_candidates", ()))
        if not allowed_targets and objective.get("target_ref") is not None:
            allowed_targets.add(objective["target_ref"])
        if payload["target_ref"] not in allowed_targets:
            raise ValueError("participant navigation target is not allowed for this objective")
    if action_type == "wait":
        _finite_nonnegative(payload["duration_s"], "wait duration")
    if action_type == "consume_message":
        _require_string(payload.get("message_id"), "message id")
        if payload.get("consumer_id") not in controlled_nodes:
            raise ValueError("message consumer must be a participant-controlled objective")
    canonical_sha256(action)
    return deepcopy(dict(action))


def write_challenge_bundle(bundle: Mapping[str, Mapping[str, Any]], output_dir: Path) -> dict[str, Path]:
    """Write public and evaluator artifacts to separate directory boundaries."""
    expected = {"public_task", "participant_api", "hidden_evaluator", "baseline_manifest"}
    if set(bundle) != expected:
        raise ValueError(f"challenge bundle requires exactly {sorted(expected)}")
    public_task = bundle["public_task"]
    validate_public_task_spec(public_task)
    validate_participant_api(bundle["participant_api"], task_spec=public_task)
    validate_hidden_evaluator(bundle["hidden_evaluator"], task_spec=public_task)
    validate_baseline_manifest(bundle["baseline_manifest"], task_spec=public_task)
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise FileExistsError(f"challenge output already exists: {output_dir}")
    public_dir = output_dir / "release"
    evaluator_dir = output_dir / "evaluator"
    public_dir.mkdir(parents=True)
    evaluator_dir.mkdir()
    from tools.scenario_generation.common import write_json

    paths = {
        "public_task": public_dir / "public_task.json",
        "participant_api": public_dir / "participant_api.json",
        "baseline_manifest": public_dir / "baseline_manifest.json",
        "hidden_evaluator": evaluator_dir / "hidden_evaluator.json",
    }
    for key, path in paths.items():
        write_json(path, bundle[key])
    return paths


__all__ = [
    "BASELINE_MANIFEST_SCHEMA", "CONTRACT_VERSION", "HIDDEN_EVALUATOR_SCHEMA",
    "PARTICIPANT_API_SCHEMA", "PUBLIC_TASK_SCHEMA", "validate_baseline_manifest",
    "validate_hidden_evaluator", "validate_participant_action", "validate_participant_api",
    "validate_participant_event", "validate_public_task_spec", "write_challenge_bundle",
]
