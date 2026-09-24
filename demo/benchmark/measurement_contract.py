"""Machine-readable collaboration-measurement contracts.

The goal-driven track freezes a business objective and its public world, while
the participant remains free to decompose, allocate and re-plan.  This module
describes what an author intends to measure without turning a witness plan into
an oracle.  It is deliberately pure Python and does not import Isaac or start
an executor.

The four nested records are validated independently so task generators can
adopt them incrementally.  ``validate_measurement_contract`` is the strict
cross-record entry point used by release tooling.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from typing import Any, Mapping


MEASUREMENT_CONTRACT_SCHEMA = "eai.benchmark.collaboration_measurement_contract"
MEASUREMENT_CONTRACT_VERSION = 1

SCENE_KEYS = ("factory", "warehouse_nvd", "modern_house_nvd", "city", "restaurant")
AUTHORING_ONLY_SCENE_KEYS = ()
_SUPPORTED_SCENE_KEYS = (*SCENE_KEYS, *AUTHORING_ONLY_SCENE_KEYS)
INTERVENTION_KINDS = ("I-skill", "I-comm", "I-role", "I-timing")

MECHANISM_FAMILIES = (
    "decomposition",
    "role_allocation",
    "communication_adaptation",
    "role_churn_recovery",
    "timing_synchronization",
    "resource_contention",
    "recovery_replanning",
    "multi_bottleneck_calibration",
)
COLLABORATION_PRIMITIVES = {
    "decomposition": "task_decomposition",
    "role_allocation": "capability_matched_assignment",
    "communication_adaptation": "information_delivery_and_recovery",
    "role_churn_recovery": "join_exit_capability_reassignment",
    "timing_synchronization": "deadline_and_barrier_coordination",
    "resource_contention": "shared_resource_arbitration",
    "recovery_replanning": "fault_triggered_replanning",
    "multi_bottleneck_calibration": "ambiguous_intervention_diagnosis",
}

FAILURE_MECHANISMS = {
    "hidden_dependency", "incomplete_decomposition", "invalid_merge",
    "stale_assignment", "responsibility_vacuum", "capability_mismatch",
    "stale_observation", "message_loss", "inconsistent_world_model",
    "unacknowledged_exit", "duplicate_work", "premature_commit",
    "barrier_deadlock", "deadline_miss", "resource_conflict", "unsafe_overlap",
    "priority_inversion", "plan_staleness", "partial_commit_loss",
    "repeated_failure", "multiple_explanations", "non_identifiable_cause",
    "invalid_intervention",
}

# This set intentionally follows the first coverage matrix.  A new metric must
# be registered there before a task can claim it, preventing typo-shaped claims.
ELIGIBLE_METRICS = {
    "uncovered_objective", "dependency_violation", "replan_count",
    "assignment_latency", "duplicate_work", "message_recovery_latency",
    "active_team_size", "activation_cost_proxy",
    "stale_consumption", "coordination_wait", "reassignment_latency",
    "uncovered_duration", "recovery_completion", "deadline_slack",
    "barrier_release_latency", "conflict_duration", "resource_wait",
    "recovery_count", "replan_latency", "probe_precision", "probe_recall",
    "identifiability_status",
}
REFERENCE_METRICS = ELIGIBLE_METRICS | {
    "completion_time_s", "makespan_s", "message_count", "cost", "energy_j",
    "recovery_time_s",
}

ALLOWED_CLAIMS = {
    "declared_task_template_coverage",
    "factor_condition_coverage",
    "diagnostic_track_inventory",
    "protocol_level_role_recovery",
    "coordination_cost_observation",
    "mechanism_association",
    "reference_feasibility",
    "finite_candidate_set_optimality",
}
FORBIDDEN_CLAIMS = {
    "global_optimality", "whole_task_necessity", "physical_causality",
    "physical_necessity", "causal_attribution", "statistical_generalization",
    "runtime_execution_validation",
}

CAUSAL_STATUSES = ("unknown", "non_identifiable", "external_pending")
REFERENCE_STATUSES = (
    "feasible_reference_only", "bounded_reference_only",
    "proven_optimal_finite_scope",
)
CALIBRATION_ROLES = (
    "no_fault_baseline", "known_fault", "invalid_intervention",
    "single_factor_control", "multi_bottleneck", "jointly_rescuable",
    "multiply_rescuable", "non_identifiable",
)
CALIBRATION_CONDITIONS = (
    "no_fault", "known_fault", "invalid_intervention", "multi_bottleneck",
    "joint_intervention", "multiple_rescue_paths", "non_identifiable",
)
CALIBRATION_ROLE_CONDITIONS = {
    "no_fault_baseline": "no_fault",
    "known_fault": "known_fault",
    "invalid_intervention": "invalid_intervention",
    "single_factor_control": "no_fault",
    "multi_bottleneck": "multi_bottleneck",
    "jointly_rescuable": "joint_intervention",
    "multiply_rescuable": "multiple_rescue_paths",
    "non_identifiable": "non_identifiable",
}
EVIDENCE_TYPES = {
    "membership_event", "reassignment_event", "communication_event",
    "message_receipt", "skill_receipt", "timing_event", "deadline_event",
    "barrier_event", "business_evidence", "decision_event", "failure_event",
    "intervention_applied", "intervention_rejected", "ledger_event",
    "capability_event",
}
INVALID_BRANCHES = {
    "post_failure_injection", "outside_decision_window", "changes_frozen_field",
    "changes_task_goal", "changes_low_level_controller", "changes_world_seed",
    "missing_expected_evidence", "unknown_target", "multiple_factors_changed",
}
FROZEN_FIELDS = {
    "task_goal", "business_outcomes", "low_level_controller", "world_seed",
    "scene_geometry", "initial_state", "participant_api",
}
MUTABLE_FIELDS_BY_KIND = {
    "I-skill": {"skill_backend", "skill_parameters", "skill_outcome"},
    "I-comm": {"communication_quality", "message_delay", "drop_policy", "bandwidth_limit"},
    "I-role": {"active_agents", "capability_version", "role_assignments", "team_membership"},
    "I-timing": {"deadline", "wait_window", "barrier", "decision_time"},
}

_TARGET_FIELDS = {
    "scene_key", "mechanism_family", "collaboration_primitive", "failure_mechanisms",
    "eligible_metrics", "allowed_claims", "forbidden_claims",
}
_REFERENCE_FIELDS = {
    "status", "strategy_id", "proves_optimality", "lower_bound", "upper_bound",
    "reference_metrics", "normative", "optimality_scope",
}
_INTERVENTION_FIELDS = {
    "scene_key", "intervention_id", "kind", "decision_window", "mutable_fields",
    "frozen_fields", "expected_evidence", "invalid_branches", "causal_status",
}
_WINDOW_FIELDS = {"start_event", "end_event", "phase"}
_CALIBRATION_FIELDS = {
    "scene_key", "calibration_id", "role", "expected_mechanisms",
    "control_condition", "expected_evidence", "causal_status",
}


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _exact(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    missing = expected - set(value)
    extra = set(value) - expected
    if missing or extra:
        raise ValueError(f"{label} fields mismatch; missing={sorted(missing)}, extra={sorted(extra)}")


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _enum(value: Any, allowed: set[str] | tuple[str, ...], label: str) -> str:
    value = _text(value, label)
    if value not in allowed:
        raise ValueError(f"{label} has unsupported value {value!r}")
    return value


def _unique_strings(value: Any, label: str, *, allowed: set[str] | None = None,
                    allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError(f"{label} must be a {'possibly empty' if allow_empty else 'nonempty'} string list")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{label} must contain nonempty strings")
    if len(set(value)) != len(value):
        raise ValueError(f"{label} must contain unique values")
    if allowed is not None:
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"{label} contains unsupported values {sorted(unknown)}")
    return sorted(value)


def _number(value: Any, label: str, *, allow_none: bool = False) -> float | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _json_safe(value: Any, label: str = "contract") -> Any:
    """Return a JSON-safe copy and reject NaN, sets, tuples and non-string keys."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{label} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{label} object keys must be strings")
            result[key] = _json_safe(item, f"{label}.{key}")
        return result
    if isinstance(value, list):
        return [_json_safe(item, f"{label}[{index}]") for index, item in enumerate(value)]
    raise ValueError(f"{label} contains a non-JSON value")


def canonical_json(value: Any) -> str:
    """Serialize a contract deterministically after strict JSON checks."""
    safe = _json_safe(value)
    return json.dumps(safe, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def contract_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def validate_measurement_target(value: Mapping[str, Any]) -> dict[str, Any]:
    value = _object(value, "measurement_target")
    _exact(value, _TARGET_FIELDS, "measurement_target")
    scene = _enum(value["scene_key"], set(_SUPPORTED_SCENE_KEYS), "measurement_target.scene_key")
    mechanism = _enum(value["mechanism_family"], set(MECHANISM_FAMILIES), "measurement_target.mechanism_family")
    primitive = _text(value["collaboration_primitive"], "measurement_target.collaboration_primitive")
    if primitive != COLLABORATION_PRIMITIVES[mechanism]:
        raise ValueError("measurement_target.collaboration_primitive does not match mechanism_family")
    failures = _unique_strings(value["failure_mechanisms"], "measurement_target.failure_mechanisms",
                                allowed=FAILURE_MECHANISMS)
    metrics = _unique_strings(value["eligible_metrics"], "measurement_target.eligible_metrics",
                              allowed=ELIGIBLE_METRICS)
    allowed = _unique_strings(value["allowed_claims"], "measurement_target.allowed_claims",
                              allowed=ALLOWED_CLAIMS)
    forbidden = _unique_strings(value["forbidden_claims"], "measurement_target.forbidden_claims",
                                allowed=FORBIDDEN_CLAIMS)
    if set(allowed) & set(forbidden):
        raise ValueError("measurement_target claims cannot be both allowed and forbidden")
    normalized = {
        "scene_key": scene, "mechanism_family": mechanism,
        "collaboration_primitive": primitive, "failure_mechanisms": failures,
        "eligible_metrics": metrics, "allowed_claims": allowed,
        "forbidden_claims": forbidden,
    }
    _json_safe(normalized)
    return {"validated": True, "normalized": normalized, "sha256": contract_sha256(normalized)}


def validate_reference_solution(value: Mapping[str, Any]) -> dict[str, Any]:
    value = _object(value, "reference_solution")
    _exact(value, _REFERENCE_FIELDS, "reference_solution")
    status = _enum(value["status"], set(REFERENCE_STATUSES), "reference_solution.status")
    strategy = _text(value["strategy_id"], "reference_solution.strategy_id")
    if type(value["proves_optimality"]) is not bool or type(value["normative"]) is not bool:
        raise ValueError("reference_solution optimality flags must be booleans")
    lower = _number(value["lower_bound"], "reference_solution.lower_bound", allow_none=True)
    upper = _number(value["upper_bound"], "reference_solution.upper_bound", allow_none=True)
    if lower is not None and upper is not None and lower > upper:
        raise ValueError("reference_solution lower_bound cannot exceed upper_bound")
    metrics = _object(value["reference_metrics"], "reference_solution.reference_metrics")
    if not metrics:
        raise ValueError("reference_solution.reference_metrics cannot be empty")
    for name, metric in metrics.items():
        _enum(name, REFERENCE_METRICS, "reference_solution.reference_metrics key")
        _number(metric, f"reference_solution.reference_metrics.{name}")
    scope = value["optimality_scope"]
    if scope is not None:
        _text(scope, "reference_solution.optimality_scope")
    if status == "feasible_reference_only":
        if value["proves_optimality"] or value["normative"] or lower is not None or upper is not None:
            raise ValueError("feasible_reference_only cannot make an optimality or bound claim")
        if scope is not None:
            raise ValueError("feasible_reference_only cannot declare an optimality scope")
    elif status == "bounded_reference_only":
        if value["proves_optimality"] or value["normative"] or lower is None or upper is None:
            raise ValueError("bounded_reference_only requires finite non-optimal bounds")
        if scope is not None:
            raise ValueError("bounded_reference_only cannot declare an optimality scope")
    else:
        if not value["proves_optimality"] or not value["normative"]:
            raise ValueError("proven_optimal_finite_scope requires both optimality flags")
        if lower is None or upper is None or scope != "declared_finite_candidate_set":
            raise ValueError("optimality must be bounded to a declared finite candidate set")
    normalized = {
        "status": status, "strategy_id": strategy,
        "proves_optimality": value["proves_optimality"], "lower_bound": lower,
        "upper_bound": upper, "reference_metrics": {
            key: float(metrics[key]) for key in sorted(metrics)
        }, "normative": value["normative"], "optimality_scope": scope,
    }
    _json_safe(normalized)
    return {"validated": True, "normalized": normalized, "sha256": contract_sha256(normalized)}


def validate_intervention_contract(value: Mapping[str, Any]) -> dict[str, Any]:
    value = _object(value, "intervention_contract")
    _exact(value, _INTERVENTION_FIELDS, "intervention_contract")
    scene = _enum(value["scene_key"], set(_SUPPORTED_SCENE_KEYS), "intervention_contract.scene_key")
    identifier = _text(value["intervention_id"], "intervention_contract.intervention_id")
    kind = _enum(value["kind"], set(INTERVENTION_KINDS), "intervention_contract.kind")
    window = _object(value["decision_window"], "intervention_contract.decision_window")
    _exact(window, _WINDOW_FIELDS, "decision_window")
    start = _text(window["start_event"], "decision_window.start_event")
    end = _text(window["end_event"], "decision_window.end_event")
    phase = _enum(window["phase"], {"pre_decision", "decision_window"}, "decision_window.phase")
    failure_words = ("failure", "failed", "terminal", "business_failure", "post_failure")
    if any(word in f"{start} {end}".lower() for word in failure_words):
        raise ValueError("intervention decision window cannot be opened after failure")
    if phase == "post_failure":  # defensive even though _enum rejects it
        raise ValueError("post-failure intervention is not admissible")
    mutable = _unique_strings(value["mutable_fields"], "intervention_contract.mutable_fields",
                              allowed=MUTABLE_FIELDS_BY_KIND[kind])
    frozen = _unique_strings(value["frozen_fields"], "intervention_contract.frozen_fields",
                             allowed=FROZEN_FIELDS)
    if not {"task_goal", "low_level_controller", "world_seed"} <= set(frozen):
        raise ValueError("intervention must freeze task_goal, low_level_controller and world_seed")
    if set(mutable) & set(frozen):
        raise ValueError("intervention mutable_fields and frozen_fields must be disjoint")
    evidence = _unique_strings(value["expected_evidence"], "intervention_contract.expected_evidence",
                               allowed=EVIDENCE_TYPES)
    invalid = _unique_strings(value["invalid_branches"], "intervention_contract.invalid_branches",
                              allowed=INVALID_BRANCHES)
    if "post_failure_injection" not in invalid:
        raise ValueError("intervention contract must explicitly reject post_failure_injection")
    causal = _enum(value["causal_status"], set(CAUSAL_STATUSES), "intervention_contract.causal_status")
    normalized = {
        "scene_key": scene, "intervention_id": identifier, "kind": kind,
        "decision_window": {"start_event": start, "end_event": end, "phase": phase},
        "mutable_fields": mutable, "frozen_fields": frozen,
        "expected_evidence": evidence, "invalid_branches": invalid,
        "causal_status": causal,
    }
    _json_safe(normalized)
    return {"validated": True, "normalized": normalized, "sha256": contract_sha256(normalized)}


def validate_calibration_profile(value: Mapping[str, Any]) -> dict[str, Any]:
    value = _object(value, "calibration_profile")
    _exact(value, _CALIBRATION_FIELDS, "calibration_profile")
    scene = _enum(value["scene_key"], set(_SUPPORTED_SCENE_KEYS), "calibration_profile.scene_key")
    identifier = _text(value["calibration_id"], "calibration_profile.calibration_id")
    role = _enum(value["role"], set(CALIBRATION_ROLES), "calibration_profile.role")
    mechanisms = _unique_strings(value["expected_mechanisms"], "calibration_profile.expected_mechanisms",
                                 allowed=set(MECHANISM_FAMILIES))
    condition = _enum(value["control_condition"], set(CALIBRATION_CONDITIONS),
                      "calibration_profile.control_condition")
    evidence = _unique_strings(value["expected_evidence"], "calibration_profile.expected_evidence",
                               allowed=EVIDENCE_TYPES)
    causal = _enum(value["causal_status"], set(CAUSAL_STATUSES), "calibration_profile.causal_status")
    expected_condition = CALIBRATION_ROLE_CONDITIONS[role]
    if condition != expected_condition:
        raise ValueError(f"{role} must use the {expected_condition} control condition")
    if role == "non_identifiable" and causal != "non_identifiable":
        raise ValueError("non_identifiable calibration must declare non_identifiable causal status")
    normalized = {
        "scene_key": scene, "calibration_id": identifier, "role": role,
        "expected_mechanisms": mechanisms, "control_condition": condition,
        "expected_evidence": evidence, "causal_status": causal,
    }
    _json_safe(normalized)
    return {"validated": True, "normalized": normalized, "sha256": contract_sha256(normalized)}


def validate_measurement_contract(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and canonically normalize all four records as one task contract."""
    bundle = _object(bundle, "measurement contract")
    expected = {"schema", "version", "measurement_target", "reference_solution",
                "intervention_contract", "calibration_profile"}
    _exact(bundle, expected, "measurement contract")
    if bundle["schema"] != MEASUREMENT_CONTRACT_SCHEMA or bundle["version"] != MEASUREMENT_CONTRACT_VERSION:
        raise ValueError("unsupported measurement contract schema")
    target = validate_measurement_target(bundle["measurement_target"])["normalized"]
    reference = validate_reference_solution(bundle["reference_solution"])["normalized"]
    intervention = validate_intervention_contract(bundle["intervention_contract"])["normalized"]
    calibration = validate_calibration_profile(bundle["calibration_profile"])["normalized"]
    if target["scene_key"] != intervention["scene_key"] or target["scene_key"] != calibration["scene_key"]:
        raise ValueError("measurement contract records must use one scene")
    if reference["status"] == "proven_optimal_finite_scope" and \
            "finite_candidate_set_optimality" not in target["allowed_claims"]:
        raise ValueError("finite optimality reference requires the matching allowed claim")
    if reference["status"] != "proven_optimal_finite_scope" and \
            "finite_candidate_set_optimality" in target["allowed_claims"]:
        raise ValueError("finite_candidate_set_optimality requires a proven finite-scope reference")
    normalized = {
        "schema": MEASUREMENT_CONTRACT_SCHEMA, "version": MEASUREMENT_CONTRACT_VERSION,
        "measurement_target": target, "reference_solution": reference,
        "intervention_contract": intervention, "calibration_profile": calibration,
    }
    _json_safe(normalized)
    return {"validated": True, "normalized": normalized, "sha256": contract_sha256(normalized)}


def build_measurement_target(*, scene_key: str, mechanism_family: str,
                             failure_mechanisms: list[str], eligible_metrics: list[str],
                             allowed_claims: list[str], forbidden_claims: list[str]) -> dict[str, Any]:
    value = {
        "scene_key": scene_key, "mechanism_family": mechanism_family,
        "collaboration_primitive": COLLABORATION_PRIMITIVES.get(mechanism_family, ""),
        "failure_mechanisms": failure_mechanisms, "eligible_metrics": eligible_metrics,
        "allowed_claims": allowed_claims, "forbidden_claims": forbidden_claims,
    }
    return validate_measurement_target(value)["normalized"]


def build_reference_solution(*, status: str, strategy_id: str, reference_metrics: Mapping[str, Any],
                             proves_optimality: bool = False, lower_bound: float | None = None,
                             upper_bound: float | None = None, normative: bool = False,
                             optimality_scope: str | None = None) -> dict[str, Any]:
    value = {
        "status": status, "strategy_id": strategy_id, "proves_optimality": proves_optimality,
        "lower_bound": lower_bound, "upper_bound": upper_bound,
        "reference_metrics": dict(reference_metrics), "normative": normative,
        "optimality_scope": optimality_scope,
    }
    return validate_reference_solution(value)["normalized"]


def build_intervention_contract(*, scene_key: str, intervention_id: str, kind: str,
                                start_event: str, end_event: str, phase: str,
                                mutable_fields: list[str], frozen_fields: list[str],
                                expected_evidence: list[str], invalid_branches: list[str],
                                causal_status: str = "unknown") -> dict[str, Any]:
    value = {
        "scene_key": scene_key, "intervention_id": intervention_id, "kind": kind,
        "decision_window": {"start_event": start_event, "end_event": end_event, "phase": phase},
        "mutable_fields": mutable_fields, "frozen_fields": frozen_fields,
        "expected_evidence": expected_evidence, "invalid_branches": invalid_branches,
        "causal_status": causal_status,
    }
    return validate_intervention_contract(value)["normalized"]


def build_calibration_profile(*, scene_key: str, calibration_id: str, role: str,
                              expected_mechanisms: list[str], control_condition: str,
                              expected_evidence: list[str], causal_status: str = "unknown") -> dict[str, Any]:
    value = {
        "scene_key": scene_key, "calibration_id": calibration_id, "role": role,
        "expected_mechanisms": expected_mechanisms, "control_condition": control_condition,
        "expected_evidence": expected_evidence, "causal_status": causal_status,
    }
    return validate_calibration_profile(value)["normalized"]
