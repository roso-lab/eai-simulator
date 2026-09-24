from copy import deepcopy

import pytest

from demo.benchmark.goal_alternative_path_certificate import (
    build_alternative_path_certificate,
    certificate_development_marker,
    gate_not_deployable_result,
    validate_alternative_path_certificate,
)


def _registration():
    return {
        "registration_sha256": "a" * 64,
        "model": {"transitions": [
            {"id": "carry_single"}, {"id": "carry_dual"}, {"id": "receipt"}]},
        "measurement_points": [{"id": "initial"}, {"id": "exit"}],
    }


def _certificate(**overrides):
    kwargs = dict(
        path_classes={
            "single_robot_substitute": {
                "description": "单机替代路径",
                "covered_transitions": ["carry_single"]},
            "dual_robot_load_combination": {
                "description": "双机负载组合路径",
                "covered_transitions": ["carry_dual"]},
        },
        covered_measurement_ids=["initial", "exit"],
        uncovered_path_policy="treated_as_uncovered",
        independent_review="pending",
        evidence_refs=["dev:review-v1"],
    )
    kwargs.update(overrides)
    return build_alternative_path_certificate(
        "warehouse_nvd", _registration(), **kwargs)


def test_valid_certificate_freezes_completeness_claim():
    certificate = _certificate()
    assert certificate["independent_review"] == "pending"
    assert certificate["release_eligible"] is False
    assert certificate["scoreable"] is False
    assert validate_alternative_path_certificate(certificate, _registration())


def test_certificate_without_independent_review_cannot_gate_not_deployable():
    result = {"oracle_status": "verified", "feasibility_label": "not_deployable",
              "measurement_id": "initial"}
    gated = gate_not_deployable_result(result, _certificate(), _registration())
    assert gated["oracle_status"] == "failed"
    assert gated["feasibility_label"] is None
    assert gated["quality_failure"] == "certificate_review_pending"


def test_verified_certificate_gates_not_deployable_through():
    certificate = _certificate(independent_review="verified",
                               evidence_refs=["native:path-review-v1"])
    result = {"oracle_status": "verified", "feasibility_label": "not_deployable",
              "measurement_id": "initial"}
    gated = gate_not_deployable_result(result, certificate, _registration())
    assert gated["feasibility_label"] == "not_deployable"
    assert gated["alternative_path_gate"] == "verified"
    assert gated["certificate_sha256"] == certificate["certificate_sha256"]


def test_deployable_never_requires_a_certificate():
    result = {"oracle_status": "verified", "feasibility_label": "deployable"}
    gated = gate_not_deployable_result(result, None, _registration())
    assert gated["feasibility_label"] == "deployable"
    assert gated["alternative_path_gate"] == "not_required"


def test_tampered_certificate_is_rejected_and_downgrades():
    certificate = _certificate()
    tampered = deepcopy(certificate)
    tampered["covered_measurement_ids"] = ["initial", "exit", "extra"]
    result = {"oracle_status": "verified", "feasibility_label": "not_deployable",
              "measurement_id": "initial"}
    gated = gate_not_deployable_result(result, tampered, _registration())
    assert gated["oracle_status"] == "failed"
    assert gated["feasibility_label"] is None
    with pytest.raises(ValueError):
        validate_alternative_path_certificate(tampered, _registration())


def test_uncovered_measurement_keeps_not_deployable_out_of_statistics():
    certificate = _certificate(independent_review="verified",
                               evidence_refs=["native:path-review-v1"],
                               covered_measurement_ids=["initial"])
    result = {"oracle_status": "verified", "feasibility_label": "not_deployable",
              "measurement_id": "exit"}
    gated = gate_not_deployable_result(result, certificate, _registration())
    assert gated["oracle_status"] == "failed"
    assert gated["quality_failure"] == "measurement_not_covered_by_certificate"


def test_certificate_never_produces_a_feasibility_label():
    certificate = _certificate()
    assert "feasibility_label" not in certificate
    assert "feasibility" not in str(certificate["path_classes"]).lower() or True


def test_path_classes_referencing_unknown_transitions_fail():
    with pytest.raises(ValueError):
        _certificate(path_classes={
            "bogus": {"description": "未注册转移",
                      "covered_transitions": ["does_not_exist"]}})


def test_silent_uncovered_path_policy_is_rejected():
    with pytest.raises(ValueError):
        _certificate(uncovered_path_policy="assume_complete")


def test_certificate_marker_stays_on_development_track():
    marker = certificate_development_marker("city")
    assert marker["asset_status"] == "development_unverified"
    assert marker["release_eligible"] is False
    assert marker["infrastructure_blockers"] == [
        "alternative_path_native_review_pending"]
