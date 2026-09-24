"""Canonical collaboration-mechanism templates for the goal benchmark.

The legacy goal bundles deliberately remain available for compatibility.  This
module is the authoring registry for scene × mechanism templates.  The default
M0 grid contains twenty templates; an explicit M1 grid adds twenty more.
A template describes the measurement question and evidence boundary; it never
claims that the mechanism has been physically executed.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from .measurement_contract import (
    MEASUREMENT_CONTRACT_SCHEMA,
    MEASUREMENT_CONTRACT_VERSION,
    build_calibration_profile,
    build_intervention_contract,
    build_measurement_target,
    build_reference_solution,
    validate_measurement_contract,
)
from .provenance import canonical_sha256


MECHANISM_TEMPLATE_SCHEMA = "eai.benchmark.goal_mechanism_template"
MECHANISM_TEMPLATE_VERSION = 1
TEAM_NECESSITY_SCHEMA = "eai.benchmark.team_necessity_certificate"
TEAM_NECESSITY_VERSION = 1

SCENES = ("factory", "warehouse_nvd", "modern_house_nvd", "city", "restaurant")
M0_MECHANISMS = (
    "decomposition",
    "role_allocation",
    "communication_adaptation",
    "role_churn_recovery",
)
M1_MECHANISMS = (
    "timing_synchronization",
    "resource_contention",
    "recovery_replanning",
    "multi_bottleneck_calibration",
)
_MECHANISM_TIER = {name: "m0" for name in M0_MECHANISMS}
_MECHANISM_TIER.update({name: "m1" for name in M1_MECHANISMS})

_MECHANISM = {
    "decomposition": {
        "primitive": "task_decomposition",
        "question": "Can the team expose and sequence hidden business dependencies while preserving the public goal?",
        "failures": ["hidden_dependency", "incomplete_decomposition", "invalid_merge"],
        "metrics": ["uncovered_objective", "dependency_violation", "replan_count"],
        "kind": "I-role",
        "mutable": ["role_assignments"],
        "evidence": ["decision_event", "ledger_event", "business_evidence"],
        "calibration_role": "known_fault",
        "calibration_condition": "known_fault",
    },
    "role_allocation": {
        "primitive": "capability_matched_assignment",
        "question": "Can the team assign responsibility to agents whose public capabilities satisfy each business obligation?",
        "failures": ["stale_assignment", "responsibility_vacuum", "capability_mismatch"],
        "metrics": ["assignment_latency", "uncovered_objective", "duplicate_work",
                    "active_team_size", "activation_cost_proxy"],
        "kind": "I-role",
        "mutable": ["role_assignments"],
        "evidence": ["decision_event", "capability_event", "reassignment_event"],
        "calibration_role": "known_fault",
        "calibration_condition": "known_fault",
    },
    "communication_adaptation": {
        "primitive": "information_delivery_and_recovery",
        "question": "Can the team adapt decisions to delayed, dropped, stale or bandwidth-limited observations?",
        "failures": ["stale_observation", "message_loss", "inconsistent_world_model"],
        "metrics": ["message_recovery_latency", "stale_consumption", "coordination_wait"],
        "kind": "I-comm",
        "mutable": ["communication_quality"],
        "evidence": ["communication_event", "message_receipt", "failure_event"],
        "calibration_role": "single_factor_control",
        "calibration_condition": "no_fault",
    },
    "role_churn_recovery": {
        "primitive": "join_exit_capability_reassignment",
        "question": "Can the team restore responsibility after a member exit, join or capability change?",
        "failures": ["responsibility_vacuum", "duplicate_work", "unacknowledged_exit"],
        "metrics": ["reassignment_latency", "uncovered_duration", "recovery_completion"],
        "kind": "I-role",
        "mutable": ["active_agents"],
        "evidence": ["membership_event", "reassignment_event", "capability_event"],
        "calibration_role": "known_fault",
        "calibration_condition": "known_fault",
    },
    "timing_synchronization": {
        "primitive": "deadline_and_barrier_coordination",
        "question": "Can the team coordinate deadlines, waits and synchronization barriers without premature commit?",
        "failures": ["premature_commit", "barrier_deadlock", "deadline_miss"],
        "metrics": ["coordination_wait", "deadline_slack", "barrier_release_latency"],
        "kind": "I-timing",
        "mutable": ["deadline", "wait_window", "barrier"],
        "evidence": ["timing_event", "deadline_event", "barrier_event"],
        "calibration_role": "single_factor_control",
        "calibration_condition": "no_fault",
    },
    "resource_contention": {
        "primitive": "shared_resource_arbitration",
        "question": "Can the team arbitrate shared routes, payload supports and business resources under conflict?",
        "failures": ["resource_conflict", "unsafe_overlap", "priority_inversion"],
        "metrics": ["conflict_duration", "resource_wait", "recovery_count"],
        "kind": "I-role",
        "mutable": ["role_assignments"],
        "evidence": ["decision_event", "failure_event"],
        "calibration_role": "known_fault",
        "calibration_condition": "known_fault",
    },
    "recovery_replanning": {
        "primitive": "fault_triggered_replanning",
        "question": "Can the team revise an incomplete plan while preserving business identity and evidence?",
        "failures": ["plan_staleness", "partial_commit_loss", "repeated_failure"],
        "metrics": ["replan_latency", "recovery_completion", "duplicate_work"],
        "kind": "I-role",
        "mutable": ["role_assignments"],
        "evidence": ["decision_event", "ledger_event", "failure_event"],
        "calibration_role": "known_fault",
        "calibration_condition": "known_fault",
    },
    "multi_bottleneck_calibration": {
        "primitive": "ambiguous_intervention_diagnosis",
        "question": "Can probes distinguish a unique mechanism from multiple mechanisms that can explain or rescue an outcome?",
        "failures": ["multiple_explanations", "non_identifiable_cause", "invalid_intervention"],
        "metrics": ["probe_precision", "probe_recall", "identifiability_status"],
        "kind": "I-role",
        "mutable": ["role_assignments"],
        "evidence": ["intervention_applied", "failure_event"],
        "calibration_role": "multi_bottleneck",
        "calibration_condition": "multi_bottleneck",
    },
}

_SCENE_FOCUS = {
    "factory": "production anomaly confirmation and maintenance dispatch",
    "warehouse_nvd": "order fulfilment, inventory identity and dock closeout",
    "modern_house_nvd": "resident access restoration and home recovery",
    "city": "urban incident response and protected access",
    "restaurant": (
        "kitchen and dining service-readiness inspection with readiness-receipt closeout"
    ),
}

_ALL_FORBIDDEN = [
    "causal_attribution", "global_optimality", "physical_causality",
    "physical_necessity", "runtime_execution_validation", "statistical_generalization",
    "whole_task_necessity",
]
_FROZEN_FIELDS = ["task_goal", "low_level_controller", "world_seed"]


def canonical_template_id(scene_key: str, mechanism_family: str) -> str:
    """Return the stable, seed- and factor-independent template identifier."""
    if scene_key not in SCENES:
        raise ValueError(f"unsupported mechanism-template scene: {scene_key}")
    tier = _MECHANISM_TIER.get(mechanism_family)
    if tier is None:
        raise ValueError(f"unsupported mechanism: {mechanism_family}")
    return f"{scene_key}.{tier}.{mechanism_family}"


def registered_mechanism_families() -> tuple[str, ...]:
    """Return M0 then M1 families in registry order (40-template combined grid)."""
    return M0_MECHANISMS + M1_MECHANISMS


def _build_measurement_contract(scene_key: str, mechanism_family: str,
                                template_id: str) -> dict[str, Any]:
    definition = _MECHANISM[mechanism_family]
    target = build_measurement_target(
        scene_key=scene_key,
        mechanism_family=mechanism_family,
        failure_mechanisms=list(definition["failures"]),
        eligible_metrics=list(definition["metrics"]),
        allowed_claims=["declared_task_template_coverage", "mechanism_association",
                        "reference_feasibility", "coordination_cost_observation"],
        forbidden_claims=list(_ALL_FORBIDDEN),
    )
    reference = build_reference_solution(
        status="feasible_reference_only",
        strategy_id=f"{template_id}.feasible_reference_v1",
        reference_metrics={"completion_time_s": 0.0},
    )
    intervention = build_intervention_contract(
        scene_key=scene_key,
        intervention_id=f"{template_id}.intervention",
        kind=definition["kind"],
        start_event="first_observation_published",
        end_event="first_business_state_commit",
        phase="decision_window",
        mutable_fields=list(definition["mutable"]),
        frozen_fields=list(_FROZEN_FIELDS),
        expected_evidence=list(definition["evidence"]),
        invalid_branches=["post_failure_injection", "changes_frozen_field",
                          "changes_task_goal", "multiple_factors_changed"],
        causal_status="unknown",
    )
    calibration = build_calibration_profile(
        scene_key=scene_key,
        calibration_id=f"{template_id}.calibration",
        role=definition["calibration_role"],
        expected_mechanisms=[mechanism_family],
        control_condition=definition["calibration_condition"],
        expected_evidence=list(definition["evidence"]),
        causal_status="unknown",
    )
    contract = {
        "schema": MEASUREMENT_CONTRACT_SCHEMA,
        "version": MEASUREMENT_CONTRACT_VERSION,
        "measurement_target": target,
        "reference_solution": reference,
        "intervention_contract": intervention,
        "calibration_profile": calibration,
    }
    return validate_measurement_contract(contract)["normalized"]


def build_team_necessity_certificate(
        *, scene_key: str, template_id: str, mechanism_family: str,
        status: str = "unknown", minimum_team_size: int = 2,
        required_mobility: Sequence[str] = ("aerial", "ground"),
        evidence_refs: Sequence[str] = (),
        counterexample: Mapping[str, Any] | None = None,
        unknown_reason: str = "physical_execution_pending") -> dict[str, Any]:
    """Build a fail-closed three-state team/heterogeneity certificate.

    ``validated`` means the declared scope has direct evidence; it does not
    silently mean physical or whole-task necessity.  ``unknown`` is the only
    admissible state for the current authoring-only templates.
    """
    if scene_key not in SCENES:
        raise ValueError(f"unsupported necessity-certificate scene: {scene_key}")
    if mechanism_family not in _MECHANISM_TIER:
        raise ValueError(f"unsupported necessity-certificate mechanism: {mechanism_family}")
    if status not in {"validated", "disproven", "unknown"}:
        raise ValueError("necessity certificate status must be validated, disproven or unknown")
    if not isinstance(minimum_team_size, int) or isinstance(minimum_team_size, bool) or minimum_team_size < 1:
        raise ValueError("minimum_team_size must be a positive integer")
    mobility = sorted(set(required_mobility))
    if any(item not in {"aerial", "ground"} for item in mobility):
        raise ValueError("required_mobility contains an unsupported mobility")
    refs = sorted(set(evidence_refs))
    if any(not isinstance(item, str) or not item for item in refs):
        raise ValueError("evidence_refs must contain nonempty strings")
    if status in {"validated", "disproven"} and not refs:
        raise ValueError("validated or disproven necessity requires evidence_refs")
    if status == "unknown" and not unknown_reason:
        raise ValueError("unknown necessity requires unknown_reason")
    if status == "validated" and counterexample is not None:
        raise ValueError("validated necessity cannot carry a disproving counterexample")
    if status == "disproven" and not isinstance(counterexample, Mapping):
        raise ValueError("disproven necessity requires a counterexample")
    value = {
        "schema": TEAM_NECESSITY_SCHEMA,
        "version": TEAM_NECESSITY_VERSION,
        "scene_key": scene_key,
        "template_id": template_id,
        "mechanism_family": mechanism_family,
        "status": status,
        "scope": "declared_team_and_heterogeneity_only",
        "minimum_team_size": minimum_team_size,
        "required_mobility": mobility,
        "evidence_refs": refs,
        "counterexample": deepcopy(dict(counterexample)) if counterexample is not None else None,
        "unknown_reason": unknown_reason if status == "unknown" else None,
        "physical_execution_validated": False,
        "whole_task_necessity_validated": False,
    }
    return validate_team_necessity_certificate(value)["normalized"]


def validate_team_necessity_certificate(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "schema", "version", "scene_key", "template_id", "mechanism_family", "status",
        "scope", "minimum_team_size", "required_mobility", "evidence_refs",
        "counterexample", "unknown_reason", "physical_execution_validated",
        "whole_task_necessity_validated",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError("team necessity certificate fields mismatch")
    if value["schema"] != TEAM_NECESSITY_SCHEMA or value["version"] != TEAM_NECESSITY_VERSION:
        raise ValueError("unsupported team necessity certificate schema")
    expected_id = canonical_template_id(value["scene_key"], value["mechanism_family"])
    if value["template_id"] != expected_id:
        raise ValueError("team necessity certificate template binding mismatch")
    if value["scope"] != "declared_team_and_heterogeneity_only":
        raise ValueError("necessity certificate scope must remain declaration-level")
    status = value["status"]
    if status not in {"validated", "disproven", "unknown"}:
        raise ValueError("necessity certificate has an invalid status")
    if type(value["physical_execution_validated"]) is not bool or type(value["whole_task_necessity_validated"]) is not bool:
        raise ValueError("necessity certificate validation flags must be booleans")
    if value["physical_execution_validated"] or value["whole_task_necessity_validated"]:
        raise ValueError("authoring certificate cannot claim physical or whole-task necessity")
    refs = value["evidence_refs"]
    if not isinstance(refs, list) or len(refs) != len(set(refs)) or any(not isinstance(item, str) or not item for item in refs):
        raise ValueError("necessity certificate evidence_refs must be unique nonempty strings")
    if status in {"validated", "disproven"} and not refs:
        raise ValueError("validated or disproven necessity requires evidence_refs")
    if status == "unknown" and (not isinstance(value["unknown_reason"], str) or not value["unknown_reason"]):
        raise ValueError("unknown necessity requires unknown_reason")
    if status == "validated" and value["counterexample"] is not None:
        raise ValueError("validated necessity cannot carry a counterexample")
    if status == "disproven" and not isinstance(value["counterexample"], Mapping):
        raise ValueError("disproven necessity requires a counterexample")
    return {"validated": True, "normalized": deepcopy(dict(value)), "sha256": canonical_sha256(value)}


def validate_mechanism_template(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "schema", "version", "template_id", "scene_key", "mechanism_family",
        "collaboration_primitive", "business_focus", "measurement_contract",
        "team_necessity_certificate", "authoring_status", "execution_status", "scoreable",
    }
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ValueError("mechanism template fields mismatch")
    if value["schema"] != MECHANISM_TEMPLATE_SCHEMA or value["version"] != MECHANISM_TEMPLATE_VERSION:
        raise ValueError("unsupported mechanism template schema")
    template_id = canonical_template_id(value["scene_key"], value["mechanism_family"])
    if value["template_id"] != template_id:
        raise ValueError("mechanism template id does not match scene and mechanism")
    if value["collaboration_primitive"] != _MECHANISM[value["mechanism_family"]]["primitive"]:
        raise ValueError("mechanism template primitive mismatch")
    if value["business_focus"] != _SCENE_FOCUS[value["scene_key"]]:
        raise ValueError("mechanism template business focus mismatch")
    measurement = validate_measurement_contract(value["measurement_contract"])["normalized"]
    if measurement["measurement_target"]["mechanism_family"] != value["mechanism_family"]:
        raise ValueError("mechanism template measurement target mismatch")
    if measurement["measurement_target"]["scene_key"] != value["scene_key"]:
        raise ValueError("mechanism template measurement scene mismatch")
    necessity = validate_team_necessity_certificate(value["team_necessity_certificate"])["normalized"]
    if necessity["template_id"] != template_id:
        raise ValueError("mechanism template necessity binding mismatch")
    if value["authoring_status"] != "passed" or value["execution_status"] != "external_pending":
        raise ValueError("mechanism template must remain authoring-passed and execution-pending")
    if value["scoreable"] is not False:
        raise ValueError("mechanism template cannot claim scoreability")
    normalized = deepcopy(dict(value))
    normalized["measurement_contract"] = measurement
    normalized["team_necessity_certificate"] = necessity
    return {"validated": True, "normalized": normalized, "sha256": canonical_sha256(normalized)}


def build_mechanism_template(scene_key: str, mechanism_family: str) -> dict[str, Any]:
    template_id = canonical_template_id(scene_key, mechanism_family)
    necessity_kwargs = {}
    if scene_key == "restaurant":
        # The frozen restaurant bundle has a legal single-Carter witness.  Its
        # authoring templates therefore must not manufacture a two-agent or
        # aerial-mobility requirement while physical necessity remains unknown.
        necessity_kwargs = {
            "minimum_team_size": 1,
            "required_mobility": ("ground",),
            "unknown_reason": "single_carter_witness_legal_team_necessity_unproven",
        }
    value = {
        "schema": MECHANISM_TEMPLATE_SCHEMA,
        "version": MECHANISM_TEMPLATE_VERSION,
        "template_id": template_id,
        "scene_key": scene_key,
        "mechanism_family": mechanism_family,
        "collaboration_primitive": _MECHANISM[mechanism_family]["primitive"],
        "business_focus": _SCENE_FOCUS[scene_key],
        "measurement_contract": _build_measurement_contract(scene_key, mechanism_family, template_id),
        "team_necessity_certificate": build_team_necessity_certificate(
            scene_key=scene_key, template_id=template_id, mechanism_family=mechanism_family,
            **necessity_kwargs),
        "authoring_status": "passed",
        "execution_status": "external_pending",
        "scoreable": False,
    }
    return validate_mechanism_template(value)["normalized"]


def registered_mechanism_templates(*, scenes: Sequence[str] = SCENES,
                                   mechanisms: Sequence[str] = M0_MECHANISMS) -> list[dict[str, Any]]:
    """Build templates for the selected grid.

    The default arguments select the M0 5×4 authoring grid (20 rows).  Pass
    ``mechanisms=M1_MECHANISMS`` for the M1 grid, or
    ``mechanisms=registered_mechanism_families()`` for the combined 40-row set.
    """
    rows = []
    for scene in scenes:
        for mechanism in mechanisms:
            rows.append(build_mechanism_template(scene, mechanism))
    return rows


__all__ = [
    "M0_MECHANISMS", "M1_MECHANISMS", "MECHANISM_TEMPLATE_SCHEMA",
    "MECHANISM_TEMPLATE_VERSION", "TEAM_NECESSITY_SCHEMA", "TEAM_NECESSITY_VERSION",
    "SCENES", "build_mechanism_template", "build_team_necessity_certificate",
    "canonical_template_id", "registered_mechanism_families",
    "registered_mechanism_templates", "validate_mechanism_template",
    "validate_team_necessity_certificate",
]
