"""Focused offline tests for the collaboration measurement contract."""

from copy import deepcopy
import json

import pytest

from demo.benchmark.measurement_contract import (
    MEASUREMENT_CONTRACT_SCHEMA,
    MEASUREMENT_CONTRACT_VERSION,
    build_calibration_profile,
    build_intervention_contract,
    build_measurement_target,
    build_reference_solution,
    canonical_json,
    contract_sha256,
    validate_measurement_contract,
    validate_measurement_target,
    validate_reference_solution,
)


SCENES = ("factory", "warehouse_nvd", "modern_house_nvd", "city", "restaurant")
KINDS = ("I-skill", "I-comm", "I-role", "I-timing")
MUTABLE = {
    "I-skill": ["skill_backend"],
    "I-comm": ["communication_quality"],
    "I-role": ["active_agents"],
    "I-timing": ["deadline"],
}
EVIDENCE = {
    "I-skill": ["skill_receipt", "failure_event"],
    "I-comm": ["communication_event", "message_receipt"],
    "I-role": ["membership_event", "reassignment_event"],
    "I-timing": ["timing_event", "deadline_event"],
}


def _target(scene="factory"):
    return build_measurement_target(
        scene_key=scene,
        mechanism_family="role_allocation",
        failure_mechanisms=["stale_assignment", "responsibility_vacuum"],
        eligible_metrics=["assignment_latency", "uncovered_objective"],
        allowed_claims=["reference_feasibility", "mechanism_association"],
        forbidden_claims=["global_optimality", "physical_causality"],
    )


def _reference():
    return build_reference_solution(
        status="feasible_reference_only",
        strategy_id="author_reference_v1",
        reference_metrics={"completion_time_s": 12.0},
    )


def _intervention(scene="factory", kind="I-role"):
    return build_intervention_contract(
        scene_key=scene,
        intervention_id=f"{scene.lower()}-{kind}",
        kind=kind,
        start_event="first_observation_published",
        end_event="first_business_state_commit",
        phase="decision_window",
        mutable_fields=MUTABLE[kind],
        frozen_fields=["task_goal", "low_level_controller", "world_seed"],
        expected_evidence=EVIDENCE[kind],
        invalid_branches=["post_failure_injection", "changes_frozen_field"],
    )


def _calibration(scene="factory"):
    return build_calibration_profile(
        scene_key=scene,
        calibration_id=f"{scene}-known-fault",
        role="known_fault",
        expected_mechanisms=["role_allocation"],
        control_condition="known_fault",
        expected_evidence=["failure_event", "reassignment_event"],
    )


def _bundle(scene="factory", kind="I-role"):
    return {
        "schema": MEASUREMENT_CONTRACT_SCHEMA,
        "version": MEASUREMENT_CONTRACT_VERSION,
        "measurement_target": _target(scene),
        "reference_solution": _reference(),
        "intervention_contract": _intervention(scene, kind),
        "calibration_profile": _calibration(scene),
    }


@pytest.mark.parametrize("scene", SCENES)
@pytest.mark.parametrize("kind", KINDS)
def test_all_scenes_and_intervention_kinds_validate(scene, kind):
    report = validate_measurement_contract(_bundle(scene, kind))
    assert report["validated"] is True
    assert report["normalized"]["measurement_target"]["scene_key"] == scene
    assert report["normalized"]["intervention_contract"]["kind"] == kind
    assert len(report["sha256"]) == 64


def test_normalization_and_hash_are_deterministic():
    left = _target()
    right = deepcopy(left)
    right["eligible_metrics"] = list(reversed(right["eligible_metrics"]))
    right["failure_mechanisms"] = list(reversed(right["failure_mechanisms"]))
    normalized_right = validate_measurement_target(right)["normalized"]
    assert left == normalized_right
    # canonical_json preserves list order; contract builders normalize
    # unordered enum lists before callers hash or serialize a record.
    assert canonical_json(left) == canonical_json(normalized_right)
    assert contract_sha256(left) == contract_sha256(normalized_right)
    json.loads(canonical_json(left))


def test_unknown_target_field_and_metric_fail_closed():
    target = _target()
    target["unreviewed_field"] = True
    with pytest.raises(ValueError, match="fields mismatch"):
        validate_measurement_target(target)
    target = _target()
    target["eligible_metrics"] = ["not_a_metric"]
    with pytest.raises(ValueError, match="unsupported values"):
        validate_measurement_target(target)


def test_calibration_role_and_condition_must_match():
    bundle = _bundle()
    calibration = deepcopy(bundle["calibration_profile"])
    calibration["role"] = "known_fault"
    calibration["control_condition"] = "no_fault"
    with pytest.raises(ValueError, match="known_fault must use the known_fault"):
        validate_measurement_contract({**bundle, "calibration_profile": calibration})

    calibration = deepcopy(bundle["calibration_profile"])
    calibration["role"] = "non_identifiable"
    calibration["control_condition"] = "non_identifiable"
    with pytest.raises(ValueError, match="non_identifiable calibration"):
        validate_measurement_contract({**bundle, "calibration_profile": calibration})


def test_unknown_claim_and_forbidden_overlap_fail_closed():
    target = _target()
    target["allowed_claims"] = ["invented_claim"]
    with pytest.raises(ValueError, match="unsupported values"):
        validate_measurement_target(target)
    target = _target()
    target["allowed_claims"] = ["global_optimality"]
    with pytest.raises(ValueError, match="unsupported values"):
        validate_measurement_target(target)


def test_post_failure_injection_and_unsupported_causal_status_fail_closed():
    bundle = _bundle()
    contract = bundle["intervention_contract"]
    contract["decision_window"]["phase"] = "post_failure"
    with pytest.raises(ValueError):
        validate_measurement_contract(bundle)
    bundle = _bundle()
    bundle["intervention_contract"]["causal_status"] = "identified"
    with pytest.raises(ValueError, match="unsupported value"):
        validate_measurement_contract(bundle)


def test_failure_named_window_is_rejected_even_with_pre_decision_phase():
    contract = _intervention()
    contract["decision_window"]["end_event"] = "after_failure_observed"
    with pytest.raises(ValueError, match="after failure"):
        validate_measurement_contract({**_bundle(), "intervention_contract": contract})


def test_optimality_overclaim_requires_finite_declared_scope():
    reference = {
        "status": "feasible_reference_only",
        "strategy_id": "bad-oracle",
        "proves_optimality": True,
        "lower_bound": 1,
        "upper_bound": 1,
        "reference_metrics": {"completion_time_s": 1},
        "normative": True,
        "optimality_scope": "global",
    }
    with pytest.raises(ValueError, match="optimality"):
        validate_reference_solution(reference)


def test_cross_record_scene_binding_and_optimality_claim_gate():
    bundle = _bundle()
    bundle["calibration_profile"] = _calibration("city")
    with pytest.raises(ValueError, match="one scene"):
        validate_measurement_contract(bundle)
    bundle = _bundle()
    bundle["measurement_target"]["allowed_claims"].append("finite_candidate_set_optimality")
    with pytest.raises(ValueError, match="requires a proven"):
        validate_measurement_contract(bundle)
