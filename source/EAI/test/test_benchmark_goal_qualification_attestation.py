"""Offline tests for the versioned qualification/attestation entry (R5).

Fixtures manufacture minimal evidence documents inside ``tmp_path`` only.
Nothing here certifies native execution, starts Isaac, loads ROS, or performs
network access; the tests lock the pending/blocked semantics, the gate
checkers, and the separation from the formal release action.
"""

import dataclasses
import json
from copy import deepcopy

import pytest

from demo.benchmark import goal_qualification_attestation as attestation
from demo.benchmark.communication import CommunicationRuntime
from demo.benchmark.goal_native_oracle_registry import EXPECTED_SCENES
from demo.benchmark.goal_qualification_attestation import (
    ATTESTATION_STATUSES,
    CAPABILITY_KEYS,
    GATE_ROLES,
    RELEASE_FLAGS,
    QualificationClaim,
    build_attestation,
    claim_from_dict,
    claim_to_dict,
    pending_manifest,
    review_qualification_claim,
)
from demo.benchmark.provenance import canonical_sha256
from demo.benchmark.protocol_runner import DIMENSIONS


def _sealed(value, field):
    value[field] = canonical_sha256({key: item for key, item in value.items()
                                     if key != field})
    return value


def _registry_bundle(scene="factory"):
    """Minimal single-scene registry audit input that audits release-ready."""
    task = f"task-{scene}"
    registration = {
        "schema": "eai.benchmark.remaining_feasibility_registration",
        "version": 1,
        "visibility": "evaluator_only",
        "task_sha256": task,
        "model": {"fact_ids": ["done"], "terminal_all": ["done"],
                  "resource_bounds": {}, "transitions": []},
        "model_sha256": "model",
        "measurement_points": [{"id": f"{scene}:initial",
                                "trigger": "initial_deployment",
                                "required_true": [], "required_false": [],
                                "varying_facts": ["done"]}],
        "validation": {"status": "verified", "scope": "native_remaining_task",
                       "evidence_refs": [f"native://{scene}/certificate"]},
    }
    _sealed(registration, "registration_sha256")
    contract = {
        "schema": "eai.benchmark.trusted_state_contract",
        "version": 1,
        "visibility": "evaluator_only",
        "scene_key": scene,
        "task_sha256": task,
        "registration_sha256": registration["registration_sha256"],
        "predicate_bindings": {}, "outcome_ids": [], "outcome_predicates": {},
        "source_versions": {"reader": "v1"}, "entity_ids": ["episode"],
        "asset_manifest_sha256": "a" * 64, "skill_contract_sha256": "b" * 64,
        "execution_validated": False, "business_outcomes_validated": False,
        "scoreable": False,
    }
    _sealed(contract, "contract_sha256")
    measurement_id = registration["measurement_points"][0]["id"]
    review = {
        "task_sha256": task,
        "registration_sha256": registration["registration_sha256"],
        "contract_sha256": contract["contract_sha256"],
        "oracle_release_ready": True,
        "results": [{
            "measurement_id": measurement_id,
            "feasibility_label": "deployable",
            "oracle_status": "verified",
            "validation_scope": "native_remaining_task",
            "evidence_refs": [f"native://{scene}/{measurement_id}"],
            "registration_sha256": registration["registration_sha256"],
            "contract_sha256": contract["contract_sha256"],
        }],
    }
    return {"scene_key": scene, "registration": registration,
            "state_contract": contract, "release_review": review}


def _communication_report():
    runtime = CommunicationRuntime({"delay_s": 1.0})
    runtime.publish(message_id="m", producer_id="carter_1", recipients=["carter_2"],
                    channel="observations", estimate={}, valid_until_s=10.0)
    runtime.advance(2.0)
    return runtime.report()


def _write_evidence(root, name, payload):
    path = root / name
    path.write_text(json.dumps(payload, sort_keys=True, allow_nan=False),
                    encoding="utf-8")
    return name


def _claim(root, evidence_name, payload, *, gate, scene="factory", capabilities=None,
           level="native_diagnostic", claim_id="claim-1", flags=()):
    from demo.benchmark.provenance import file_sha256
    name = _write_evidence(root, evidence_name, payload)
    return QualificationClaim(
        claim_id=claim_id,
        scene=scene,
        task_variant="goal_v1",
        factor="communication:shared_fifo",
        level=level,
        claimed_capabilities=tuple(capabilities or ("task_allocation",)),
        evidence_manifest=(attestation.EvidenceEntry(
            path=name, sha256=file_sha256(root / name), role=GATE_ROLES[gate]),),
        claimed_gate=gate,
        claimed_release_flags=tuple(flags),
    )


def test_claim_serialization_round_trip_is_strict():
    claim = QualificationClaim(
        claim_id="claim-rt", scene="factory", task_variant="goal_v1",
        factor="f:1", level="native_diagnostic",
        claimed_capabilities=("task_allocation", "cost_efficiency"),
        evidence_manifest=(attestation.EvidenceEntry(
            path="evidence/registry.json", sha256="a" * 64,
            role="native_oracle_registry"),),
        claimed_gate="oracle_release_ready",
        claimed_release_flags=(("scoreable", False),),
    )
    payload = claim_to_dict(claim)
    assert payload["schema"] == attestation.CLAIM_SCHEMA
    assert payload["version"] == 1
    restored = claim_from_dict(json.loads(json.dumps(payload)))
    assert claim_to_dict(restored) == payload
    with pytest.raises(ValueError, match="unknown claim fields"):
        claim_from_dict({**payload, "extra": 1})
    with pytest.raises(ValueError, match="schema/version"):
        claim_from_dict({**payload, "version": 2})
    with pytest.raises(ValueError, match="sha256"):
        claim_from_dict({**payload, "evidence_manifest": [
            {"path": "a.json", "sha256": "nope", "role": "native_oracle_registry"}]})


def test_matching_evidence_passes_every_gate_to_pending(tmp_path):
    claim = _claim(tmp_path, "registry.json", {"records": [_registry_bundle()]},
                   gate="oracle_release_ready")
    review = review_qualification_claim(claim, tmp_path)
    assert review["structural"]["status"] == "ok"
    assert all(row["status"] == "verified" for row in review["evidence"])
    assert review["gate"]["status"] == "passed"
    assert review["review_status"] == "pending"
    assert review["reasons"] == []


def test_tampered_evidence_hash_blocks_the_attestation(tmp_path):
    payload = {"records": [_registry_bundle()]}
    claim = _claim(tmp_path, "registry.json", payload, gate="oracle_release_ready")
    (tmp_path / "registry.json").write_text(
        json.dumps({**payload, "records": [_registry_bundle("warehouse_nvd")]}),
        encoding="utf-8")
    review = review_qualification_claim(claim, tmp_path)
    assert review["review_status"] == "blocked"
    assert "evidence_hash_mismatch:registry.json" in review["reasons"]
    assert review["gate"]["status"] == "not_run"
    assert review["gate"]["reasons"] == ["gate_not_run:prerequisite_failed"]


def test_missing_evidence_file_blocks_with_explicit_reason(tmp_path):
    claim = _claim(tmp_path, "registry.json", {"records": [_registry_bundle()]},
                   gate="oracle_release_ready")
    (tmp_path / "registry.json").unlink()
    review = review_qualification_claim(claim, tmp_path)
    assert review["review_status"] == "blocked"
    assert "evidence_missing:registry.json" in review["reasons"]
    assert review["evidence"][0]["status"] == "missing"
    assert review["gate"]["status"] == "not_run"


def test_claiming_scoreable_true_is_blocked_under_pending_semantics(tmp_path):
    claim = _claim(tmp_path, "registry.json", {"records": [_registry_bundle()]},
                   gate="oracle_release_ready", flags=(("scoreable", True),))
    result = build_attestation(claim, tmp_path)
    assert result["attestation_status"] == "blocked"
    assert "claimed_release_flag_true:scoreable" in result["blocked_reasons"]
    assert result["release_flags"]["scoreable"] is False


def test_evidence_asserting_a_release_flag_true_is_blocked(tmp_path):
    bundle = _registry_bundle()
    bundle["state_contract"]["scoreable"] = True
    claim = _claim(tmp_path, "registry.json", {"records": [bundle]},
                   gate="oracle_release_ready")
    result = build_attestation(claim, tmp_path)
    assert result["attestation_status"] == "blocked"
    assert "evidence_release_flag_true:registry.json:scoreable" in result["blocked_reasons"]
    assert result["review"]["gate"]["status"] == "not_run"
    assert result["review"]["gate"]["reasons"] == ["gate_not_run:release_flag_asserted_true"]


def test_oracle_gate_reuses_the_registry_audit_fail_closed(tmp_path):
    bundle = _registry_bundle()
    bundle["release_review"]["oracle_release_ready"] = False
    claim = _claim(tmp_path, "registry.json", {"records": [bundle]},
                   gate="oracle_release_ready")
    review = review_qualification_claim(claim, tmp_path)
    assert review["review_status"] == "blocked"
    assert review["gate"]["status"] == "failed"
    assert "oracle_registry_not_release_ready" in review["gate"]["reasons"]
    assert any(reason.startswith("oracle_registry_failed:")
               for reason in review["gate"]["reasons"])
    # The three-map input form reaches the same audit.
    maps = {"registrations": {"factory": _registry_bundle()["registration"]},
            "state_contracts": {"factory": _registry_bundle()["state_contract"]},
            "release_reviews": {}}
    claim_maps = _claim(tmp_path, "maps.json", maps, gate="oracle_release_ready",
                        claim_id="claim-maps")
    review_maps = review_qualification_claim(claim_maps, tmp_path)
    assert review_maps["review_status"] == "blocked"
    assert any(reason.startswith("oracle_registry_missing_scene")
               for reason in review_maps["gate"]["reasons"])


def test_communication_replay_gate_reuses_the_shared_validator(tmp_path):
    report = _communication_report()
    claim = _claim(tmp_path, "communication.json", report,
                   gate="communication_replay_validated",
                   capabilities=("communication_adaptation",))
    review = review_qualification_claim(claim, tmp_path)
    assert review["gate"]["status"] == "passed"
    assert review["review_status"] == "pending"

    tampered = deepcopy(report)
    tampered["ledger"] = tampered["ledger"][:-1]
    claim_bad = _claim(tmp_path, "tampered.json", tampered,
                       gate="communication_replay_validated", claim_id="claim-bad",
                       capabilities=("communication_adaptation",))
    review_bad = review_qualification_claim(claim_bad, tmp_path)
    assert review_bad["review_status"] == "blocked"
    assert review_bad["gate"]["status"] == "failed"
    assert "communication_replay_not_validated" in review_bad["gate"]["reasons"]


def test_business_outcomes_gate_enforces_oracle_status_and_certificate(tmp_path):
    good = {"rows": [{"measurement_id": "factory:initial",
                      "feasibility_label": "deployable",
                      "oracle_status": "verified",
                      "evidence_refs": ["native://factory/initial"]}]}
    claim = _claim(tmp_path, "results.json", good, gate="business_outcomes_validated",
                   capabilities=("task_allocation", "cross_episode_learning"))
    review = review_qualification_claim(claim, tmp_path)
    assert review["review_status"] == "pending"

    negative = {"rows": [{"measurement_id": "city:initial",
                          "feasibility_label": "not_deployable",
                          "oracle_status": "verified",
                          "evidence_refs": ["native://city/initial"]}]}
    claim_neg = _claim(tmp_path, "negative.json", negative,
                       gate="business_outcomes_validated", claim_id="claim-neg")
    review_neg = review_qualification_claim(claim_neg, tmp_path)
    assert review_neg["review_status"] == "blocked"
    assert ("native_oracle_row_not_deployable_without_certificate:city:initial"
            in review_neg["gate"]["reasons"])

    certified = {"rows": [{"measurement_id": "city:initial",
                           "feasibility_label": "not_deployable",
                           "oracle_status": "verified",
                           "alternative_path_gate": "verified",
                           "certificate_sha256": "c" * 64,
                           "evidence_refs": ["native://city/initial"]}]}
    claim_cert = _claim(tmp_path, "certified.json", certified,
                        gate="business_outcomes_validated", claim_id="claim-cert")
    assert review_qualification_claim(claim_cert, tmp_path)["review_status"] == "pending"


def test_pending_manifest_aggregates_claims_and_missing_evidence(tmp_path):
    claim_ok = _claim(tmp_path, "registry.json",
                      {"records": [_registry_bundle()]},
                      gate="oracle_release_ready", claim_id="claim-ok")
    claim_missing = _claim(tmp_path, "gone.json",
                           {"records": [_registry_bundle()]},
                           gate="oracle_release_ready", claim_id="claim-missing")
    (tmp_path / "gone.json").unlink()
    manifest = pending_manifest([claim_ok, claim_missing], tmp_path)
    assert manifest["schema"] == attestation.PENDING_MANIFEST_SCHEMA
    assert manifest["version"] == 1
    assert manifest["manifest_status"] == "blocked"
    assert manifest["pending_claim_ids"] == ["claim-ok"]
    assert manifest["blocked_claim_ids"] == ["claim-missing"]
    assert manifest["missing_evidence"] == [{
        "claim_id": "claim-missing", "path": "gone.json",
        "reason": "evidence_missing:gone.json"}]
    assert manifest["release_flags"] == {flag: False for flag in RELEASE_FLAGS}
    assert manifest["formal_promotion"] == attestation.FORMAL_PROMOTION_STATUS
    assert manifest["manifest_sha256"] == canonical_sha256(
        {key: value for key, value in manifest.items()
         if key != "manifest_sha256"})
    only_ok = pending_manifest([claim_ok], tmp_path)
    assert only_ok["manifest_status"] == "pending"
    assert only_ok["missing_evidence"] == []


def test_duplicate_claim_ids_and_empty_manifest_are_blocked(tmp_path):
    first = _claim(tmp_path, "registry.json", {"records": [_registry_bundle()]},
                   gate="oracle_release_ready", claim_id="dup")
    second = _claim(tmp_path, "results.json",
                    {"rows": [{"measurement_id": "factory:initial",
                               "feasibility_label": "deployable",
                               "oracle_status": "verified",
                               "evidence_refs": ["native://factory/initial"]}]},
                    gate="business_outcomes_validated", claim_id="dup")
    manifest = pending_manifest([first, second], tmp_path)
    assert manifest["manifest_status"] == "blocked"
    assert "duplicate_claim_id:dup" in manifest["manifest_blocked_reasons"]
    assert manifest["blocked_claim_ids"] == ["dup"]
    empty = pending_manifest([], tmp_path)
    assert empty["manifest_status"] == "blocked"
    assert "manifest_no_claims" in empty["manifest_blocked_reasons"]


def test_attestation_never_has_an_approved_status(tmp_path):
    assert ATTESTATION_STATUSES == ("pending", "blocked")
    claim = _claim(tmp_path, "registry.json", {"records": [_registry_bundle()]},
                   gate="oracle_release_ready")
    result = build_attestation(claim, tmp_path)
    assert result["attestation_status"] == "pending"
    assert "approved" not in json.dumps(result)
    blocked = build_attestation(claim, tmp_path / "does-not-exist")
    assert blocked["attestation_status"] == "blocked"
    assert "approved" not in json.dumps(blocked)


def test_release_flags_stay_false_even_when_every_gate_passes(tmp_path):
    claim = _claim(tmp_path, "registry.json", {"records": [_registry_bundle()]},
                   gate="oracle_release_ready")
    result = build_attestation(claim, tmp_path)
    assert result["review"]["review_status"] == "pending"
    assert result["release_flags"] == {flag: False for flag in RELEASE_FLAGS}
    assert result["formal_promotion"] == attestation.FORMAL_PROMOTION_STATUS
    assert result["attestation_sha256"] == canonical_sha256(
        {key: value for key, value in result.items()
         if key != "attestation_sha256"})
    # The registry evidence itself still carries the false flags.
    bundle = json.loads((tmp_path / "registry.json").read_text(encoding="utf-8"))
    assert bundle["records"][0]["state_contract"]["scoreable"] is False


@pytest.mark.parametrize("fault", ["gate", "capability", "level", "scene", "path"])
def test_structural_faults_are_blocked_with_named_reasons(tmp_path, fault):
    claim = _claim(tmp_path, "registry.json", {"records": [_registry_bundle()]},
                   gate="oracle_release_ready")
    if fault == "gate":
        claim = dataclasses.replace(claim, claimed_gate="scoreable_now")
    elif fault == "capability":
        claim = dataclasses.replace(claim, claimed_capabilities=("teleport",))
    elif fault == "level":
        claim = dataclasses.replace(claim, level="formal_scoring")
    elif fault == "scene":
        claim = dataclasses.replace(claim, scene="mars")
    elif fault == "path":
        claim = dataclasses.replace(claim, evidence_manifest=(
            attestation.EvidenceEntry(
                path="../outside.json", sha256=claim.evidence_manifest[0].sha256,
                role=claim.evidence_manifest[0].role),))
    review = review_qualification_claim(claim, tmp_path)
    assert review["review_status"] == "blocked"
    assert review["structural"]["status"] == "failed"
    expected = {
        "gate": "claimed_gate_unknown:scoreable_now",
        "capability": "claimed_capability_unknown:teleport",
        "level": "formal_scoring_level_unattestable",
        "scene": "scene_unknown:mars",
        "path": "evidence_path_unsafe:../outside.json",
    }[fault]
    assert expected in review["reasons"]
    assert review["gate"]["status"] == "not_run"


def test_capability_vocabulary_is_the_shared_six_dimension_protocol_set():
    assert CAPABILITY_KEYS == DIMENSIONS
    assert len(CAPABILITY_KEYS) == 6
    assert len(set(CAPABILITY_KEYS)) == 6
    # The six protocol dimensions map onto the scoring adapter's split status
    # keys: four verbatim, dynamic reconfiguration split into scale/failure,
    # and the coordination-cost dimension renamed to cost_efficiency.
    from demo.benchmark.goal_scoring_adapter import DIMENSION_STATUS
    verbatim = {"task_allocation", "communication_adaptation",
                "temporal_coordination", "cross_episode_learning"}
    assert verbatim <= set(CAPABILITY_KEYS) & set(DIMENSION_STATUS)
    assert {"dynamic_restructuring_scale", "dynamic_restructuring_failure",
            "cost_efficiency"} <= set(DIMENSION_STATUS)
    assert set(GATE_ROLES) == set(attestation.GATE_CHECKERS)
    assert set(EXPECTED_SCENES) == {"factory", "warehouse_nvd", "modern_house_nvd",
                                    "city", "restaurant"}


def test_cli_review_and_manifest_write_new_outputs_only(tmp_path):
    claim = _claim(tmp_path, "registry.json", {"records": [_registry_bundle()]},
                   gate="oracle_release_ready", claim_id="claim-cli")
    claim_path = tmp_path / "claim.json"
    claim_path.write_text(json.dumps(claim_to_dict(claim), sort_keys=True),
                          encoding="utf-8")
    out = tmp_path / "attestation.json"
    assert attestation.main(["review", "--claim", str(claim_path),
                             "--evidence-root", str(tmp_path),
                             "--output", str(out)]) == 0
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["attestation_status"] == "pending"
    assert result["release_flags"]["scoreable"] is False
    manifest_out = tmp_path / "manifest.json"
    assert attestation.main(["manifest", "--claim", str(claim_path),
                             "--evidence-root", str(tmp_path),
                             "--output", str(manifest_out)]) == 0
    manifest = json.loads(manifest_out.read_text(encoding="utf-8"))
    assert manifest["manifest_status"] == "pending"
    # Existing outputs are never overwritten.
    with pytest.raises(SystemExit):
        attestation.main(["review", "--claim", str(claim_path),
                          "--evidence-root", str(tmp_path), "--output", str(out)])
    broken = tmp_path / "broken.json"
    broken.write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit):
        attestation.main(["review", "--claim", str(broken),
                          "--evidence-root", str(tmp_path),
                          "--output", str(tmp_path / "never.json")])
    assert not (tmp_path / "never.json").exists()


def test_unreadable_evidence_json_is_blocked(tmp_path):
    from demo.benchmark.provenance import file_sha256
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    claim = QualificationClaim(
        claim_id="claim-bad-json", scene="factory", task_variant="goal_v1",
        factor="f:1", level="native_diagnostic",
        claimed_capabilities=("task_allocation",),
        evidence_manifest=(attestation.EvidenceEntry(
            path="bad.json", sha256=file_sha256(path),
            role="native_oracle_registry"),),
        claimed_gate="oracle_release_ready",
    )
    review = review_qualification_claim(claim, tmp_path)
    assert review["review_status"] == "blocked"
    assert "evidence_unreadable:bad.json" in review["reasons"]
    assert review["gate"]["status"] == "not_run"
