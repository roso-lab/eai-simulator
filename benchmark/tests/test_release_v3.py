from __future__ import annotations

from dataclasses import replace
import hashlib
import json

from benchmark.metrics_v3 import ScoreResultV3
from benchmark.release_v3 import EvidenceBundleV3, ReleaseV3Error, assess_release, read_bundle, write_bundle
from benchmark.dynamic_v3 import DynamicDispositionV3, DynamicEventDispositionV3
from benchmark.physical_evidence_v3 import PhysicalEvidenceCertificateV3
from benchmark.artifact_verifier_v3 import ArtifactVerificationReceiptV3, verify_artifact_manifest_v3
from benchmark.analysis_v3 import ScheduledRunV3, analyze_dimension
from benchmark.statistical_evidence_v3 import StatisticalEvidenceV3
from benchmark.scorebook_v3 import default_scorebook_v3
from functools import lru_cache
import pytest


def _bundle(status="valid", physical_evidence=None, artifacts=None):
    results = tuple(ScoreResultV3(item, status, 50.0) if status == "valid" else ScoreResultV3(item, status, None, lower=20, upper=80, reason="missing") for item in ("D1", "D2", "D3", "D4", "D5", "D6"))
    return EvidenceBundleV3("bundle.v3", "protocol.v3", default_scorebook_v3().digest(), "composition", ({"event": "success", "event_id": "loss", "closure_digest": "closure"},), results, artifacts or {"operation": "operation", "feasibility": "feasibility", "guard": "guard", "world": "world", "transport": "transport"}, "commit", physical_evidence)


@lru_cache
def _statistics():
    reports = []
    for score in ("D1", "D2", "D3", "D4", "D5", "D6"):
        if score == "D3":
            from benchmark.relations_v3 import d3_relation_report, relation_run_from_obligations
            from benchmark.records_v3 import IdentityV3, ObligationOutcomeV3
            rows = []
            for i in range(30):
                for s in range(3):
                    identity = IdentityV3(
                        "v3", "score", "catalog", "design", "task", "anchor", "production",
                        ("P2", "P3", "P4"), "L1", "nominal", "system", str(i), str(s), "fact", "checkpoint")
                    obligations = tuple(
                        ObligationOutcomeV3(
                            identity, obligation_id=obligation_id, relation=relation,
                            p3_subtype=subtype, participants=("robot",), opportunity=True,
                            accepted_fraction=.5)
                        for obligation_id, relation, subtype in (
                            ("p2", "P2", None), ("p3j", "P3", "J"),
                            ("p3s", "P3", "S"), ("p4", "P4", None)))
                    rows.append(relation_run_from_obligations(obligations))
            reports.append(d3_relation_report(rows, resamples=100, partition_mode="domain_difficulty_condition",
                                              registered_cells={key: [("production", "L1", "nominal")] for key in ("P2", "P3J", "P3S", "P4")}))
            continue
        values = (.5, .25, .25, .25) if score == "D2" else (.5,)
        rows = [ScheduledRunV3(f"{i}/{s}", str(i), "cell", str(s), "cp", "evidence", values, weight=1/90) for i in range(30) for s in range(3)]
        if score == "D2":
            rows = [replace(r, values=(1., float(int(r.instance_id)%2), float(int(r.instance_id)%2), float(int(r.instance_id)%2))) for r in rows]
        if score == "D5":
            rows = [replace(r, values=(float(int(r.instance_id)%2),)) for r in rows]
        catalog = None
        if score in {"D5", "D6"}:
            from benchmark.fixed_catalog_v3 import FixedCatalogV3, FixedRunSpecV3
            catalog = FixedCatalogV3(score, tuple(FixedRunSpecV3(r.run_id, r.instance_id, r.stratum, r.repeat_id, r.checkpoint_digest, r.weight, r.run_id, "family", r.instance_id, "unit") for r in rows))
        reports.append(analyze_dimension(score, rows, stratum_weights={"cell": 1}, resamples=100, fixed_catalog=catalog))
    return StatisticalEvidenceV3("commit", default_scorebook_v3().digest(), tuple(reports))


def _physical_evidence():
    event = DynamicEventDispositionV3("loss", DynamicDispositionV3.RECOVERED, 4, "closure", ("operation",), physical_status="valid")
    return PhysicalEvidenceCertificateV3("composition", "commit", "closure", event, "operation", "feasibility", "guard", "world", "transport")


def _strict_bundle():
    def digest(label):
        return hashlib.sha256(label.encode()).hexdigest()
    event = DynamicEventDispositionV3("loss", DynamicDispositionV3.RECOVERED, 4, "closure", (digest("operation"),), physical_status="valid")
    evidence = PhysicalEvidenceCertificateV3("composition", "commit", "closure", event, digest("operation"), digest("feasibility"), digest("guard"), digest("world"), digest("transport"))
    artifacts = {label: digest(label) for label in ("operation", "feasibility", "guard", "world", "transport")}
    return _bundle(physical_evidence=evidence, artifacts=artifacts)


def test_release_requires_physical_and_statistical_gates():
    gate = assess_release(_bundle(), physical_qualified=False, statistical_ready=True)
    assert not gate.rankable and gate.reason == "physical_qualification_missing"
    gate = assess_release(_bundle(), physical_qualified=True, statistical_ready=True)
    assert not gate.rankable and gate.reason == "physical_evidence_missing"
    gate = assess_release(_bundle(), physical_qualified=True, statistical_ready=True, physical_evidence=_physical_evidence(), statistical_evidence=_statistics())
    assert gate.rankable


def test_bundle_embedded_physical_evidence_is_self_contained():
    evidence = _physical_evidence()
    gate = assess_release(_bundle(physical_evidence=evidence), physical_qualified=True, statistical_ready=True, statistical_evidence=_statistics())
    assert gate.rankable


def test_ready_boolean_cannot_replace_computed_statistical_reports():
    bundle = _bundle(physical_evidence=_physical_evidence())
    gate = assess_release(bundle, physical_qualified=True, statistical_ready=True)
    assert not gate.rankable and gate.reason == "statistical_evidence_missing_or_mismatched"
    foreign = replace(_statistics(), source_commit="other")
    gate = assess_release(bundle, physical_qualified=True, statistical_ready=True, statistical_evidence=foreign)
    assert not gate.rankable


def test_unknown_d2_finite_sample_report_is_accepted_as_statistical_evidence():
    evidence = _statistics()
    rows = [ScheduledRunV3(
        f"{i}/{s}", str(i), "cell", str(s), "cp", "evidence",
        ((None if i == 0 and s == 0 else 1.), float(i % 2), float(i % 2), float(i % 2)),
        "unknown-record" if i == 0 and s == 0 else "observed",
        paired_block_id="block" if i == 0 and s == 0 else "",
        paired_attempt_id="attempt" if i == 0 and s == 0 else "")
        for i in range(30) for s in range(3)]
    d2 = analyze_dimension("D2", rows, stratum_weights={"cell": 1}, resamples=100)
    reports = tuple(d2 if report["score_id"] == "D2" else report for report in evidence.reports)
    rebuilt = StatisticalEvidenceV3(evidence.source_commit, evidence.scorebook_digest, reports)
    assert next(report for report in rebuilt.reports if report["score_id"] == "D2")["status"] == "range"


def test_bundle_and_explicit_physical_evidence_must_match():
    evidence = _physical_evidence()
    foreign = PhysicalEvidenceCertificateV3("composition", "other-commit", evidence.closure_digest, evidence.event_disposition, evidence.operation_receipt_digest, evidence.remaining_feasibility_digest, evidence.guard_digest, evidence.world_preservation_digest, evidence.transport_trace_digest)
    gate = assess_release(_bundle(physical_evidence=evidence), physical_qualified=True, statistical_ready=True, physical_evidence=foreign)
    assert not gate.rankable and gate.reason == "physical_evidence_identity_mismatch"


def test_bundle_digest_covers_embedded_physical_evidence():
    evidence = _physical_evidence()
    base = _bundle()
    embedded = _bundle(physical_evidence=evidence)
    assert base.digest() != embedded.digest()


def test_range_is_computable_but_not_rankable():
    gate = assess_release(_bundle("range"), physical_qualified=True, statistical_ready=True, physical_evidence=_physical_evidence())
    assert gate.computable and not gate.rankable


@pytest.mark.parametrize("score_id", ("D1", "D2", "D3", "D4"))
def test_residual_unknown_ranges_remain_evidence_but_do_not_make_release_ready(score_id):
    evidence = _statistics()
    reports = tuple({**report, "status": "range", "identification": [20., 80.]} if report["score_id"] == score_id else report for report in evidence.reports)
    accepted = replace(evidence, reports=reports)
    assert next(r for r in accepted.reports if r["score_id"] == score_id)["status"] == "range"


@pytest.mark.parametrize("status", ("incomplete", "unstable", "na"))
def test_unresolved_report_is_retained_but_cannot_rank(status):
    evidence = _statistics()
    reports = tuple({**report, "status": status, "reason": "registered_inference_not_resolved"} if report["score_id"] == "D2" else report for report in evidence.reports)
    evidence = replace(evidence, reports=reports)
    bundle = _bundle(physical_evidence=_physical_evidence())
    bundle = replace(bundle, results=tuple(replace(score, status=status, point=None) if score.score_id == "D2" else score for score in bundle.results))
    gate = assess_release(bundle, physical_qualified=True, statistical_ready=True, statistical_evidence=evidence)
    assert gate.computable and not gate.rankable


def test_d2_operating_boundary_cannot_replace_headline_interval():
    evidence = _statistics()
    reports = tuple({**report, "endpoints": [{**end, "method": "simultaneous_retention_projection"} for end in report["endpoints"]]} if report["score_id"] == "D2" else report for report in evidence.reports)
    with pytest.raises(ValueError, match="interval construction"):
        replace(evidence, reports=reports)


@pytest.mark.parametrize("tamper", ("low_floor", "low_count", "missing_cell"))
def test_d3_diagnostic_support_cannot_qualify_statistical_release(tamper):
    evidence = _statistics()
    reports = []
    for report in evidence.reports:
        if report["score_id"] == "D3":
            report = dict(report)
            if tamper == "low_floor":
                report["minimum_instances"] = 1
            else:
                report["relation_support"] = {h: dict(cells) for h, cells in report["relation_support"].items()}
                if tamper == "low_count":
                    report["relation_support"]["P2"]["production/L1/nominal"] = 1
                else:
                    report["relation_support"]["P2"] = {}
        reports.append(report)
    with pytest.raises(ValueError, match="formal instance floor"):
        replace(evidence, reports=tuple(reports))


def test_release_rejects_physical_evidence_from_another_composition():
    foreign = _physical_evidence()
    foreign = PhysicalEvidenceCertificateV3("other-composition", foreign.source_commit, foreign.closure_digest, foreign.event_disposition, foreign.operation_receipt_digest, foreign.remaining_feasibility_digest, foreign.guard_digest, foreign.world_preservation_digest, foreign.transport_trace_digest)
    gate = assess_release(_bundle(), physical_qualified=True, statistical_ready=True, physical_evidence=foreign)
    assert not gate.rankable
    assert gate.reason == "physical_evidence_identity_mismatch"


def test_release_rejects_nonboolean_gate_arguments_and_unbound_records():
    assert assess_release(_bundle(), physical_qualified="false", statistical_ready=True, physical_evidence=_physical_evidence()).reason == "release_gate_arguments_must_be_bool"
    foreign = PhysicalEvidenceCertificateV3("composition", "commit", "other-closure", DynamicEventDispositionV3("loss", DynamicDispositionV3.RECOVERED, 4, "other-closure", ("operation",), physical_status="valid"), "operation", "feasibility", "guard", "world")
    gate = assess_release(_bundle(), physical_qualified=True, statistical_ready=True, physical_evidence=foreign)
    assert gate.reason == "physical_evidence_incomplete"


def test_artifact_digest_must_be_indexed_as_a_value_not_a_key():
    evidence = _physical_evidence()
    digests = {
        evidence.operation_receipt_digest,
        evidence.remaining_feasibility_digest,
        evidence.guard_digest,
        evidence.world_preservation_digest,
        evidence.transport_trace_digest,
    }
    malformed = {digest: "wrong-value" for digest in digests}
    gate = assess_release(_bundle(artifacts=malformed), physical_qualified=True, statistical_ready=True, physical_evidence=evidence)
    assert not gate.rankable and gate.reason == "physical_evidence_incomplete"


def test_event_evidence_digests_are_all_required_in_artifact_index():
    base = _physical_evidence()
    event = DynamicEventDispositionV3("loss", DynamicDispositionV3.RECOVERED, 4, "closure", ("operation", "unlisted-event"), physical_status="valid")
    evidence = PhysicalEvidenceCertificateV3("composition", "commit", "closure", event, "operation", "feasibility", "guard", "world", "transport")
    gate = assess_release(_bundle(physical_evidence=evidence), physical_qualified=True, statistical_ready=True)
    assert not gate.rankable and gate.reason == "physical_evidence_incomplete"


def test_closure_and_event_id_must_be_bound_to_one_record():
    evidence = _physical_evidence()
    bundle = replace(_bundle(physical_evidence=evidence), records=({"closure_digest": "closure"}, {"event_id": "loss"}))
    gate = assess_release(bundle, physical_qualified=True, statistical_ready=True)
    assert not gate.rankable and gate.reason == "physical_evidence_incomplete"


def test_terminal_failure_is_physical_evidence_but_not_rankable():
    event = DynamicEventDispositionV3("loss", DynamicDispositionV3.TERMINAL_FAILURE, 4, "closure", ("operation",), physical_status="unknown")
    evidence = PhysicalEvidenceCertificateV3("composition", "commit", "closure", event, "operation", "feasibility", "guard", "world", "transport")
    gate = assess_release(_bundle(physical_evidence=evidence), physical_qualified=True, statistical_ready=True)
    assert gate.physical and not gate.rankable and gate.reason == "physical_event_not_rankable"


def test_verified_method_failure_remains_in_primary_score_support():
    event = DynamicEventDispositionV3("loss", DynamicDispositionV3.TERMINAL_FAILURE, 4, "closure", ("operation",), physical_status="valid")
    evidence = PhysicalEvidenceCertificateV3("composition", "commit", "closure", event, "operation", "feasibility", "guard", "world", "transport")
    gate = assess_release(_bundle(physical_evidence=evidence), physical_qualified=True, statistical_ready=True, statistical_evidence=_statistics())
    assert gate.rankable


def test_certified_safe_stop_is_physical_evidence_but_not_rankable():
    event = DynamicEventDispositionV3("loss", DynamicDispositionV3.SAFE_STOP, 4, "closure", ("operation", "stop-cert"), physical_status="unknown", safe_stop_certificate_digest="stop-cert")
    evidence = PhysicalEvidenceCertificateV3("composition", "commit", "closure", event, "operation", "feasibility", "guard", "world", "transport")
    artifacts = {"operation": "operation", "feasibility": "feasibility", "guard": "guard", "world": "world", "transport": "transport", "stop": "stop-cert"}
    gate = assess_release(_bundle(physical_evidence=evidence, artifacts=artifacts), physical_qualified=True, statistical_ready=True)
    assert gate.physical and not gate.rankable and gate.reason == "physical_event_not_rankable"


def test_bundle_rejects_missing_composite_dimension():
    with pytest.raises(ValueError, match="exactly D1"):
        EvidenceBundleV3("bundle.v3", "protocol.v3", "scorebook", "composition", (), (ScoreResultV3("D1", "valid", 50.0),), {}, "commit")


def test_release_fails_closed_for_invalid_physical_evidence_type():
    gate = assess_release(_bundle(), physical_qualified=True, statistical_ready=True, physical_evidence={})
    assert not gate.rankable and gate.reason == "physical_evidence_type_invalid"


def test_bundle_rejects_nonstring_identity_fields():
    with pytest.raises(ReleaseV3Error, match="identity fields"):
        EvidenceBundleV3("bundle.v3", 1, "scorebook", "composition", (), tuple(ScoreResultV3(item, "valid", 50.0) for item in ("D1", "D2", "D3", "D4", "D5", "D6")), {}, "commit")


def test_bundle_json_round_trip_rehydrates_embedded_certificate(tmp_path):
    bundle = _bundle(physical_evidence=_physical_evidence())
    path = write_bundle(bundle, tmp_path / "bundle.json")
    restored = read_bundle(path)
    assert restored == bundle
    assert restored.digest() == bundle.digest()


def test_bundle_json_round_trip_rejects_tampered_digest(tmp_path):
    bundle = _bundle(physical_evidence=_physical_evidence())
    path = write_bundle(bundle, tmp_path / "bundle.json")
    payload = path.read_text().replace(bundle.digest(), "tampered")
    path.write_text(payload)
    with pytest.raises(ReleaseV3Error, match="digest mismatch"):
        read_bundle(path)


def test_bundle_json_round_trip_rejects_tampered_derived_certificate_flag(tmp_path):
    bundle = _bundle(physical_evidence=_physical_evidence())
    path = write_bundle(bundle, tmp_path / "bundle.json")
    payload = json.loads(path.read_text())
    payload["physical_evidence"]["qualified"] = False
    path.write_text(json.dumps(payload))
    with pytest.raises(ReleaseV3Error, match="qualified flag mismatch"):
        read_bundle(path)


def test_bundle_json_round_trip_rejects_unknown_certificate_schema(tmp_path):
    bundle = _bundle(physical_evidence=_physical_evidence())
    path = write_bundle(bundle, tmp_path / "bundle.json")
    payload = json.loads(path.read_text())
    payload["physical_evidence"]["schema_version"] = "physical-evidence.v2"
    path.write_text(json.dumps(payload))
    with pytest.raises(ReleaseV3Error, match="unsupported physical evidence schema"):
        read_bundle(path)


def test_bundle_json_round_trip_rejects_unknown_certificate_fields(tmp_path):
    bundle = _bundle(physical_evidence=_physical_evidence())
    path = write_bundle(bundle, tmp_path / "bundle.json")
    payload = json.loads(path.read_text())
    payload["physical_evidence"]["unbound_note"] = "ignored?"
    path.write_text(json.dumps(payload))
    with pytest.raises(ReleaseV3Error, match="unknown physical evidence fields"):
        read_bundle(path)


def test_bundle_rejects_untyped_records_and_artifacts_even_without_digest_check():
    valid_results = tuple(ScoreResultV3(item, "valid", 50.0) for item in ("D1", "D2", "D3", "D4", "D5", "D6"))
    with pytest.raises(ReleaseV3Error, match="records"):
        EvidenceBundleV3("bundle.v3", "protocol.v3", "scorebook", "composition", (1,), valid_results, {}, "commit")
    with pytest.raises(ReleaseV3Error, match="artifacts"):
        EvidenceBundleV3("bundle.v3", "protocol.v3", "scorebook", "composition", (), valid_results, {"label": 1}, "commit")


def test_strict_release_requires_bundle_bound_artifact_verification(tmp_path):
    bundle = _strict_bundle()
    gate = assess_release(bundle, physical_qualified=True, statistical_ready=True, require_artifact_verification=True)
    assert not gate.rankable and gate.reason == "artifact_verification_missing"
    manifest = {}
    for label, digest in bundle.artifacts.items():
        path = tmp_path / f"{label}.bin"
        path.write_bytes(label.encode())
        assert hashlib.sha256(label.encode()).hexdigest() == digest
        manifest[label] = {"path": path.name, "sha256": digest, "size_bytes": len(label)}
    verifications = verify_artifact_manifest_v3(manifest, tmp_path)
    receipt = ArtifactVerificationReceiptV3(bundle.digest(), bundle.source_commit, verifications)
    gate = assess_release(bundle, physical_qualified=True, statistical_ready=True, artifact_verification=receipt, require_artifact_verification=True, statistical_evidence=_statistics())
    assert gate.rankable


def test_strict_release_rejects_artifact_receipt_from_another_bundle():
    bundle = _strict_bundle()
    verifications = tuple()
    with pytest.raises(ValueError, match="nonempty"):
        ArtifactVerificationReceiptV3(bundle.digest(), bundle.source_commit, verifications)
