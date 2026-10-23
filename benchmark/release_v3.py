"""Portable evidence bundle and publication/ranking gates for v3."""

from __future__ import annotations

from dataclasses import dataclass, asdict
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .metrics_v3 import ScoreResultV3
from .dynamic_v3 import DynamicDispositionV3, DynamicEventDispositionV3
from .physical_evidence_v3 import PhysicalEvidenceCertificateV3
from .artifact_verifier_v3 import ArtifactVerificationReceiptV3
from .statistical_evidence_v3 import StatisticalEvidenceV3


class ReleaseV3Error(ValueError):
    pass


@dataclass(frozen=True)
class EvidenceBundleV3:
    schema_version: str
    protocol_version: str
    scorebook_digest: str
    composition_digest: str
    records: tuple[Mapping[str, Any], ...]
    results: tuple[ScoreResultV3, ...]
    artifacts: Mapping[str, str]
    source_commit: str
    physical_evidence: PhysicalEvidenceCertificateV3 | None = None

    def __post_init__(self) -> None:
        if self.schema_version != "bundle.v3":
            raise ReleaseV3Error("unsupported bundle schema")
        identity_values = (self.protocol_version, self.scorebook_digest, self.composition_digest, self.source_commit)
        if any(not isinstance(value, str) or not value.strip() for value in identity_values):
            raise ReleaseV3Error("bundle identity fields are required")
        if not isinstance(self.records, (list, tuple)) or any(not isinstance(item, Mapping) for item in self.records):
            raise ReleaseV3Error("bundle records must be mappings")
        if not isinstance(self.artifacts, Mapping) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in self.artifacts.items()):
            raise ReleaseV3Error("bundle artifacts must be string-to-string mappings")
        if not isinstance(self.results, (list, tuple)) or any(not isinstance(item, ScoreResultV3) for item in self.results):
            raise ReleaseV3Error("bundle results must be ScoreResultV3 values")
        score_ids = tuple(item.score_id for item in self.results)
        if score_ids != ("D1", "D2", "D3", "D4", "D5", "D6"):
            raise ReleaseV3Error("bundle must contain exactly D1 through D6 in order")
        if self.physical_evidence is not None and not isinstance(self.physical_evidence, PhysicalEvidenceCertificateV3):
            raise ReleaseV3Error("physical_evidence must be PhysicalEvidenceCertificateV3 or None")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "protocol_version": self.protocol_version,
            "scorebook_digest": self.scorebook_digest,
            "composition_digest": self.composition_digest,
            "records": [dict(item) for item in self.records],
            "results": [asdict(item) for item in self.results],
            "artifacts": dict(self.artifacts),
            "source_commit": self.source_commit,
            "physical_evidence": self.physical_evidence.to_dict() if self.physical_evidence is not None else None,
        }

    def digest(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any], *, verify_digest: bool = True) -> "EvidenceBundleV3":
        """Rehydrate a bundle and, by default, verify its persisted digest."""
        if not isinstance(payload, Mapping):
            raise ReleaseV3Error("bundle payload must be a mapping")
        try:
            root_fields = {
                "schema_version", "protocol_version", "scorebook_digest", "composition_digest",
                "records", "results", "artifacts", "source_commit", "physical_evidence", "bundle_digest",
            }
            unknown_root = set(payload) - root_fields
            if unknown_root:
                raise ReleaseV3Error(f"unknown bundle fields: {sorted(unknown_root)}")
            raw_results = payload["results"]
            results = tuple(ScoreResultV3(**dict(item)) for item in raw_results)
            raw_evidence = payload.get("physical_evidence")
            evidence = None
            if raw_evidence is not None:
                if not isinstance(raw_evidence, Mapping):
                    raise ReleaseV3Error("physical evidence must be a mapping")
                certificate_fields = {
                    "schema_version", "composition_digest", "source_commit", "closure_digest",
                    "event_disposition", "operation_receipt_digest", "remaining_feasibility_digest",
                    "guard_digest", "world_preservation_digest", "transport_trace_digest", "qualified",
                }
                unknown_certificate = set(raw_evidence) - certificate_fields
                if unknown_certificate:
                    raise ReleaseV3Error(f"unknown physical evidence fields: {sorted(unknown_certificate)}")
                if raw_evidence.get("schema_version") != "physical-evidence.v3":
                    raise ReleaseV3Error("unsupported physical evidence schema")
                event_payload = raw_evidence["event_disposition"]
                if not isinstance(event_payload, Mapping):
                    raise ReleaseV3Error("event disposition must be a mapping")
                event_fields = {
                    "event_id", "disposition", "tick", "closure_digest", "evidence_digests",
                    "reason", "physical_status", "safe_stop_certificate_digest",
                }
                unknown_event = set(event_payload) - event_fields
                if unknown_event:
                    raise ReleaseV3Error(f"unknown event disposition fields: {sorted(unknown_event)}")
                event = DynamicEventDispositionV3(
                    event_id=event_payload["event_id"],
                    disposition=event_payload["disposition"],
                    tick=event_payload["tick"],
                    closure_digest=event_payload["closure_digest"],
                    evidence_digests=tuple(event_payload.get("evidence_digests", ())),
                    reason=event_payload.get("reason", ""),
                    physical_status=event_payload.get("physical_status", "unknown"),
                    safe_stop_certificate_digest=event_payload.get("safe_stop_certificate_digest"),
                )
                evidence = PhysicalEvidenceCertificateV3(
                    composition_digest=raw_evidence["composition_digest"],
                    source_commit=raw_evidence["source_commit"],
                    closure_digest=raw_evidence["closure_digest"],
                    event_disposition=event,
                    operation_receipt_digest=raw_evidence["operation_receipt_digest"],
                    remaining_feasibility_digest=raw_evidence["remaining_feasibility_digest"],
                    guard_digest=raw_evidence["guard_digest"],
                    world_preservation_digest=raw_evidence["world_preservation_digest"],
                    transport_trace_digest=raw_evidence.get("transport_trace_digest"),
                )
                persisted_qualified = raw_evidence.get("qualified")
                if type(persisted_qualified) is not bool or persisted_qualified != evidence.qualified:
                    raise ReleaseV3Error("physical evidence qualified flag mismatch")
            bundle = cls(
                schema_version=payload["schema_version"],
                protocol_version=payload["protocol_version"],
                scorebook_digest=payload["scorebook_digest"],
                composition_digest=payload["composition_digest"],
                records=tuple(payload["records"]),
                results=results,
                artifacts=dict(payload["artifacts"]),
                source_commit=payload["source_commit"],
                physical_evidence=evidence,
            )
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise ReleaseV3Error(f"malformed bundle payload: {exc}") from exc
        if verify_digest and payload.get("bundle_digest") != bundle.digest():
            raise ReleaseV3Error("bundle digest mismatch")
        return bundle


@dataclass(frozen=True)
class ReleaseGateV3:
    computable: bool
    statistical: bool
    physical: bool
    rankable: bool
    reason: str
    score_statuses: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess_release(
    bundle: EvidenceBundleV3,
    *,
    physical_qualified: bool,
    statistical_ready: bool,
    physical_evidence: PhysicalEvidenceCertificateV3 | None = None,
    artifact_verification: ArtifactVerificationReceiptV3 | None = None,
    require_artifact_verification: bool = False,
    statistical_evidence: StatisticalEvidenceV3 | None = None,
) -> ReleaseGateV3:
    if type(physical_qualified) is not bool or type(statistical_ready) is not bool or type(require_artifact_verification) is not bool:
        statuses = {item.score_id: item.status for item in bundle.results}
        return ReleaseGateV3(False, False, False, False, "release_gate_arguments_must_be_bool", statuses)
    statuses = {item.score_id: item.status for item in bundle.results}
    if physical_evidence is not None and not isinstance(physical_evidence, PhysicalEvidenceCertificateV3):
        return ReleaseGateV3(False, bool(statistical_ready), False, False, "physical_evidence_type_invalid", statuses)
    if artifact_verification is not None and not isinstance(artifact_verification, ArtifactVerificationReceiptV3):
        return ReleaseGateV3(False, bool(statistical_ready), False, False, "artifact_verification_type_invalid", statuses)
    computable = bool(bundle.results) and all(status in {"valid", "range", "na", "incomplete", "unstable"} for status in statuses.values())
    def record_binds(closure_digest: str, event_id: str) -> bool:
        """Require closure and event identity to co-occur in one record."""
        def walk(item: Any) -> tuple[bool, bool]:
            if isinstance(item, Mapping):
                closure = any(key == "closure_digest" and closure_digest == str(candidate) for key, candidate in item.items())
                event = any(key == "event_id" and event_id == str(candidate) for key, candidate in item.items())
                for candidate in item.values():
                    child_closure, child_event = walk(candidate)
                    closure = closure or child_closure
                    event = event or child_event
                return closure, event
            if isinstance(item, (list, tuple)):
                closure = event = False
                for candidate in item:
                    child_closure, child_event = walk(candidate)
                    closure = closure or child_closure
                    event = event or child_event
                return closure, event
            return False, False
        return any(all(walk(record)) for record in bundle.records)
    def artifact_has(value: str) -> bool:
        # Artifact keys are labels; only indexed artifact values are evidence
        # digests.  Accepting keys would let a malformed index self-authorize.
        return any(value == str(candidate) for candidate in bundle.artifacts.values())
    bundled_evidence = bundle.physical_evidence
    if physical_evidence is not None and bundled_evidence is not None and physical_evidence.digest() != bundled_evidence.digest():
        return ReleaseGateV3(False, bool(statistical_ready), False, False, "physical_evidence_identity_mismatch", statuses)
    physical_evidence = physical_evidence if physical_evidence is not None else bundled_evidence
    evidence_digests = (
        *tuple(physical_evidence.event_disposition.evidence_digests),
        physical_evidence.operation_receipt_digest,
        physical_evidence.remaining_feasibility_digest,
        physical_evidence.guard_digest,
        physical_evidence.world_preservation_digest,
        *(tuple([physical_evidence.transport_trace_digest]) if physical_evidence.transport_trace_digest else ()),
        *(tuple([physical_evidence.event_disposition.safe_stop_certificate_digest]) if physical_evidence.event_disposition.safe_stop_certificate_digest else ()),
    ) if physical_evidence is not None else ()
    evidence_ready = (
        physical_evidence is not None
        and physical_evidence.qualified
        and physical_evidence.composition_digest == bundle.composition_digest
        and physical_evidence.source_commit == bundle.source_commit
        and record_binds(physical_evidence.closure_digest, physical_evidence.event_disposition.event_id)
        and all(artifact_has(value) for value in evidence_digests)
    )
    receipt_digests = set()
    artifact_receipt_ready = True
    if require_artifact_verification:
        if artifact_verification is None:
            artifact_receipt_ready = False
        else:
            receipt_digests = {item.expected_sha256 for item in artifact_verification.verifications if item.valid}
            artifact_receipt_ready = (
                artifact_verification.valid
                and artifact_verification.bundle_digest == bundle.digest()
                and artifact_verification.source_commit == bundle.source_commit
                and all(value in receipt_digests for value in evidence_digests)
            )
    physical = bool(physical_qualified) and evidence_ready
    statistical = bool(statistical_ready) and isinstance(statistical_evidence, StatisticalEvidenceV3) and statistical_evidence.matches(bundle)
    # Observed method failures remain in the primary support. Qualification
    # concerns the intervention/evaluator, not whether the method recovered.
    rankable_event = physical_evidence is not None and physical_evidence.event_disposition.disposition in {DynamicDispositionV3.RECOVERED, DynamicDispositionV3.TERMINAL_FAILURE} and physical_evidence.event_disposition.physical_status.value == "valid"
    # Ranges are publishable fixed-catalog diagnostics for D5/D6, but they do
    # not establish a single rank.  D1--D4 must be point-valid as enforced by
    # StatisticalEvidenceV3.
    rankable = computable and physical and statistical and rankable_event and artifact_receipt_ready and all(status == "valid" for status in statuses.values())
    if not computable:
        reason = "invalid_score_result"
    elif not physical_qualified:
        reason = "physical_qualification_missing"
    elif not evidence_ready:
        if physical_evidence is None:
            reason = "physical_evidence_missing"
        elif physical_evidence.composition_digest != bundle.composition_digest or physical_evidence.source_commit != bundle.source_commit:
            reason = "physical_evidence_identity_mismatch"
        else:
            reason = "physical_evidence_incomplete"
    elif require_artifact_verification and not artifact_receipt_ready:
        if artifact_verification is None:
            reason = "artifact_verification_missing"
        elif artifact_verification.bundle_digest != bundle.digest() or artifact_verification.source_commit != bundle.source_commit:
            reason = "artifact_verification_identity_mismatch"
        else:
            reason = "artifact_verification_incomplete"
    elif not rankable_event:
        reason = "physical_event_not_rankable"
    elif not statistical:
        reason = "statistical_release_not_ready" if not statistical_ready else "statistical_evidence_missing_or_mismatched"
    elif not rankable:
        reason = "one_or_more_scores_not_valid_for_ranking"
    else:
        reason = "rankable_release"
    return ReleaseGateV3(computable, statistical, physical, rankable, reason, statuses)


def write_bundle(bundle: EvidenceBundleV3, destination: str | Path) -> Path:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**bundle.to_dict(), "bundle_digest": bundle.digest()}, ensure_ascii=False, indent=2, default=str) + "\n")
    return path


def read_bundle(source: str | Path, *, verify_digest: bool = True) -> EvidenceBundleV3:
    path = Path(source)
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseV3Error(f"unable to read bundle: {exc}") from exc
    return EvidenceBundleV3.from_dict(payload, verify_digest=verify_digest)


def assess_formal_release(bundle: EvidenceBundleV3, *, registration, case_report, baseline_report, calibration_report, statistical_evidence, artifact_verification, physical_qualified: bool):
    """Formal public entry: numerical/evidence gates AND registered readiness.
    Lower-level assess_release is useful for bundle diagnostics; it does not
    certify a catalog, establish baseline fidelity or freeze experiment inputs.
    """
    from .registration_v3 import formal_readiness
    readiness = formal_readiness(registration, case_report=case_report, baseline_report=baseline_report, calibration_report=calibration_report)
    if isinstance(statistical_evidence, StatisticalEvidenceV3):
        from .organization_v3 import organization_support_errors
        d1 = next(r for r in statistical_evidence.reports if r["score_id"] == "D1")
        d1_errors = organization_support_errors(d1, minimum_instances=registration.minimum_instances,
                                                minimum_coverage=registration.infrastructure_coverage)
        if d1_errors:
            readiness["blockers"].extend(d1_errors)
            readiness["ready"] = False
        d3 = next(r for r in statistical_evidence.reports if r["score_id"] == "D3")
        if d3.get("status") != "valid" or d3.get("minimum_instances", 0) < registration.minimum_instances:
            readiness["blockers"].append("d3_support_below_registered_floor")
            readiness["ready"] = False
    gate = assess_release(bundle, physical_qualified=physical_qualified, statistical_ready=True, statistical_evidence=statistical_evidence, artifact_verification=artifact_verification, require_artifact_verification=True)
    return {"rankable": gate.rankable and readiness["ready"], "bundle_gate": gate.to_dict(), "registration": readiness}


__all__ = ["ReleaseV3Error", "EvidenceBundleV3", "ReleaseGateV3", "assess_release", "assess_formal_release", "write_bundle", "read_bundle"]
