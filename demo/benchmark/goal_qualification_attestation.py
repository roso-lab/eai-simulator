"""Versioned native qualification/attestation entry for the Goal track (R5).

A :class:`QualificationClaim` is a *claim*, never a pass.  Review re-derives
every gate from evidence bytes: each evidence file must exist below the
caller-supplied evidence root, must match its manifest SHA-256, and the single
claimed gate must pass a machine-verifiable checker that reuses the existing
evaluator tooling (``audit_native_oracle_registry``, the communication replay
validator, and the native-result ``oracle_status``/alternative-path certificate
gate).  Missing files, hash drift, unreadable payloads, release flags asserted
or found ``true`` in evidence, and unsatisfied gates all produce an attestation
with ``attestation_status == "blocked"`` and explicit reason codes; nothing
passes silently.

There is deliberately **no approved state**.  ``pending`` means "every gate
that this entry can check has been re-derived from matching evidence";
``blocked`` means at least one check failed.  Formal promotion of a scene or
capability is a separate release action outside this module, which is why the
three release flags (``execution_validated``, ``business_outcomes_validated``,
``scoreable``) are always emitted ``False`` and why a claim that itself asserts
one of them ``true`` -- or claims the ``formal_scoring`` level -- is blocked:
promotion cannot be reached by flipping existing booleans.

``pending_manifest`` aggregates many claims into an evaluator-only manifest
that carries only ``pending``/``blocked`` per claim plus the consolidated
list of evidence that still has to be supplied.  It is an input to the
independent review that precedes any release decision; it is not that
decision.  This first block is single-scene and single-evidence: one claim
consumes exactly one JSON evidence document whose role is bound to its gate.
The module is pure Python and offline; it never starts Isaac, ROS, or a
network transfer.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
from typing import Any

from .goal_native_oracle_registry import (
    EXPECTED_SCENES,
    audit_native_oracle_registry,
)
from .provenance import canonical_sha256, file_sha256
from .protocol_runner import DIMENSIONS as _PROTOCOL_DIMENSIONS


ATTESTATION_SCHEMA = "eai.benchmark.goal_qualification_attestation"
ATTESTATION_VERSION = 1
PENDING_MANIFEST_SCHEMA = "eai.benchmark.goal_qualification_pending_manifest"
PENDING_MANIFEST_VERSION = 1
CLAIM_SCHEMA = "eai.benchmark.goal_qualification_claim"
CLAIM_VERSION = 1

#: The only attestation states this entry can emit.  ``approved`` is absent on
#: purpose: formal promotion is a separate release action, not a review result.
ATTESTATION_STATUSES = ("pending", "blocked")

#: Release flags that must stay ``False`` everywhere this module touches.  A
#: claim or evidence document that asserts one of them ``true`` is blocked.
RELEASE_FLAGS = ("execution_validated", "business_outcomes_validated", "scoreable")

#: Claim levels distinguish what a claim is about, mirroring the R5 exit
#: wording (runnable candidate / native diagnostic passed / some statistic
#: valid / formally scoreable).  ``formal_scoring`` can never be attested here.
QUALIFICATION_LEVELS = (
    "runnable_candidate",
    "native_diagnostic",
    "statistical_validity",
    "formal_scoring",
)

#: The shared six-capability vocabulary (protocol DIMENSIONS).  A claim may
#: only name a nonempty subset of it.
CAPABILITY_KEYS = tuple(_PROTOCOL_DIMENSIONS)

#: Each claimed gate consumes exactly one JSON evidence document with the
#: bound role below.
GATE_ROLES = {
    "oracle_release_ready": "native_oracle_registry",
    "communication_replay_validated": "communication_report",
    "business_outcomes_validated": "native_oracle_results",
}

#: Marker recorded in every attestation/manifest: promotion is not issued by
#: this entry, so consumers cannot mistake pending for a release decision.
FORMAL_PROMOTION_STATUS = "separate_release_action_not_issued_here"

_BINARY_LABELS = {"deployable", "not_deployable"}
_HEX = frozenset("0123456789abcdef")


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and not (set(value) - _HEX)


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


@dataclass(frozen=True)
class EvidenceEntry:
    """One evidence file: a root-relative POSIX path, its SHA-256, and role."""

    path: str
    sha256: str
    role: str


@dataclass(frozen=True)
class QualificationClaim:
    """The data contract of one qualification claim (a claim, not a pass).

    ``claimed_release_flags`` is the claimant's own asserted release status.
    Pending semantics require every asserted flag to be ``False`` (or absent);
    a ``True`` value is recorded as a blocked reason, because promotion is a
    separate release action that this entry never performs.
    """

    claim_id: str
    scene: str
    task_variant: str
    factor: str
    level: str
    claimed_capabilities: tuple[str, ...]
    evidence_manifest: tuple[EvidenceEntry, ...]
    claimed_gate: str
    claimed_release_flags: tuple[tuple[str, bool], ...] = ()


def claim_to_dict(claim: QualificationClaim) -> dict[str, Any]:
    """Serialize a claim to the versioned JSON-ready payload."""
    return {
        "schema": CLAIM_SCHEMA,
        "version": CLAIM_VERSION,
        "claim_id": claim.claim_id,
        "scene": claim.scene,
        "task_variant": claim.task_variant,
        "factor": claim.factor,
        "level": claim.level,
        "claimed_capabilities": list(claim.claimed_capabilities),
        "evidence_manifest": [
            {"path": entry.path, "sha256": entry.sha256, "role": entry.role}
            for entry in claim.evidence_manifest
        ],
        "claimed_gate": claim.claimed_gate,
        "claimed_release_flags": dict(claim.claimed_release_flags),
    }


def claim_from_dict(payload: Mapping[str, Any]) -> QualificationClaim:
    """Strictly parse a serialized claim; malformed payloads raise ValueError."""
    if not isinstance(payload, Mapping):
        raise ValueError("claim payload must be a mapping")
    known = {
        "schema", "version", "claim_id", "scene", "task_variant", "factor",
        "level", "claimed_capabilities", "evidence_manifest", "claimed_gate",
        "claimed_release_flags",
    }
    unknown = sorted(set(payload) - known)
    if unknown:
        raise ValueError(f"unknown claim fields: {unknown}")
    if payload.get("schema") != CLAIM_SCHEMA or payload.get("version") != CLAIM_VERSION:
        raise ValueError("unsupported claim schema/version")
    strings = ("claim_id", "scene", "task_variant", "factor", "level", "claimed_gate")
    values = {}
    for name in strings:
        value = payload.get(name)
        if not _is_nonempty_string(value):
            raise ValueError(f"claim field {name} must be a nonempty string")
        values[name] = value
    capabilities = payload.get("claimed_capabilities")
    if (not isinstance(capabilities, Sequence) or isinstance(capabilities, (str, bytes))
            or not capabilities):
        raise ValueError("claimed_capabilities must be a nonempty sequence")
    parsed_capabilities = []
    for capability in capabilities:
        if not _is_nonempty_string(capability):
            raise ValueError("claimed_capabilities entries must be nonempty strings")
        parsed_capabilities.append(capability)
    manifest = payload.get("evidence_manifest")
    if (not isinstance(manifest, Sequence) or isinstance(manifest, (str, bytes))
            or not manifest):
        raise ValueError("evidence_manifest must be a nonempty sequence")
    parsed_manifest = []
    for entry in manifest:
        if not isinstance(entry, Mapping):
            raise ValueError("evidence_manifest entries must be mappings")
        if set(entry) != {"path", "sha256", "role"}:
            raise ValueError("evidence_manifest entries need exactly path/sha256/role")
        path, digest, role = entry["path"], entry["sha256"], entry["role"]
        if not _is_nonempty_string(path):
            raise ValueError("evidence path must be a nonempty string")
        if not _is_sha256(digest):
            raise ValueError(f"evidence sha256 is not a sha256: {path}")
        if not _is_nonempty_string(role):
            raise ValueError(f"evidence role must be a nonempty string: {path}")
        parsed_manifest.append(EvidenceEntry(path=path, sha256=digest, role=role))
    flags = payload.get("claimed_release_flags", {})
    if flags is None:
        flags = {}
    if not isinstance(flags, Mapping):
        raise ValueError("claimed_release_flags must be a mapping")
    parsed_flags = []
    for name, value in flags.items():
        if not _is_nonempty_string(name) or not _is_bool(value):
            raise ValueError("claimed_release_flags entries must map names to booleans")
        parsed_flags.append((name, value))
    return QualificationClaim(
        claim_id=values["claim_id"],
        scene=values["scene"],
        task_variant=values["task_variant"],
        factor=values["factor"],
        level=values["level"],
        claimed_capabilities=tuple(parsed_capabilities),
        evidence_manifest=tuple(parsed_manifest),
        claimed_gate=values["claimed_gate"],
        claimed_release_flags=tuple(parsed_flags),
    )


def _safe_evidence_path(path: Any) -> bool:
    """Accept only normalized, root-relative POSIX paths without traversal."""
    if not isinstance(path, str) or not path or path != path.strip():
        return False
    if path.startswith("/") or "\\" in path or ":" in path:
        return False
    pure = PurePosixPath(path)
    if pure.is_absolute():
        return False
    parts = pure.parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        return False
    return PurePosixPath(*parts).as_posix() == path


def _structural_reasons(claim: QualificationClaim) -> list[str]:
    """Fail-closed structural validation of one claim."""
    reasons: list[str] = []
    if not _is_nonempty_string(claim.claim_id):
        reasons.append("claim_id_empty")
    if claim.scene not in EXPECTED_SCENES:
        reasons.append(f"scene_unknown:{claim.scene}")
    if not _is_nonempty_string(claim.task_variant):
        reasons.append("task_variant_empty")
    if not _is_nonempty_string(claim.factor):
        reasons.append("factor_empty")
    if claim.level not in QUALIFICATION_LEVELS:
        reasons.append(f"level_unknown:{claim.level}")
    elif claim.level == "formal_scoring":
        # Formal scoreability is produced by the separate release action; this
        # entry can never attest it, so such a claim cannot even be pending.
        reasons.append("formal_scoring_level_unattestable")
    capabilities = claim.claimed_capabilities
    if not capabilities:
        reasons.append("claimed_capabilities_empty")
    else:
        seen: set[str] = set()
        for capability in capabilities:
            if capability not in CAPABILITY_KEYS:
                reasons.append(f"claimed_capability_unknown:{capability}")
            if capability in seen:
                reasons.append(f"claimed_capability_duplicate:{capability}")
            seen.add(capability)
    if claim.claimed_gate not in GATE_ROLES:
        reasons.append(f"claimed_gate_unknown:{claim.claimed_gate}")
    manifest = claim.evidence_manifest
    if not manifest:
        reasons.append("evidence_manifest_empty")
    elif len(manifest) != 1:
        reasons.append("evidence_manifest_not_single_entry")
    if claim.claimed_gate in GATE_ROLES and len(manifest) == 1:
        required_role = GATE_ROLES[claim.claimed_gate]
        entry = manifest[0]
        if not _is_nonempty_string(entry.path):
            reasons.append("evidence_path_empty")
        elif not _safe_evidence_path(entry.path):
            reasons.append(f"evidence_path_unsafe:{entry.path}")
        if not _is_sha256(entry.sha256):
            reasons.append(f"evidence_sha256_invalid:{entry.path}")
        if entry.role != required_role:
            reasons.append(f"evidence_role_mismatch:{entry.path}:{entry.role}")
    for name, value in claim.claimed_release_flags:
        if name not in RELEASE_FLAGS:
            reasons.append(f"claimed_release_flag_unknown:{name}")
        elif not _is_bool(value):
            reasons.append(f"claimed_release_flag_not_boolean:{name}")
        elif value is True:
            reasons.append(f"claimed_release_flag_true:{name}")
    return sorted(set(reasons))


def _verify_evidence(claim: QualificationClaim, evidence_root: Path) -> list[dict[str, Any]]:
    """Existence + SHA-256 verification; never reads paths outside the root."""
    rows: list[dict[str, Any]] = []
    for entry in claim.evidence_manifest:
        row = {"path": entry.path, "sha256": entry.sha256, "role": entry.role,
               "status": "not_checked"}
        target = evidence_root / entry.path
        try:
            resolved = target.resolve(strict=True)
            root_resolved = evidence_root.resolve()
            if (not _safe_evidence_path(entry.path)
                    or not resolved.is_relative_to(root_resolved)
                    or not resolved.is_file()):
                row["status"] = "missing"
                row["reason"] = f"evidence_unresolvable:{entry.path}"
                rows.append(row)
                continue
            if file_sha256(resolved) != entry.sha256:
                row["status"] = "hash_mismatch"
                row["reason"] = f"evidence_hash_mismatch:{entry.path}"
            else:
                row["status"] = "verified"
        except OSError:
            row["status"] = "missing"
            row["reason"] = f"evidence_missing:{entry.path}"
        rows.append(row)
    return rows


def _release_flag_violations(node: Any, label: str) -> list[str]:
    """Recursively reject any release flag asserted ``true`` in evidence."""
    violations: list[str] = []
    if isinstance(node, Mapping):
        for key, value in node.items():
            if key in RELEASE_FLAGS and value is True:
                violations.append(f"evidence_release_flag_true:{label}:{key}")
            violations.extend(_release_flag_violations(value, label))
    elif isinstance(node, (list, tuple)):
        for item in node:
            violations.extend(_release_flag_violations(item, label))
    return violations


def _evidence_refs(value: Any) -> bool:
    return (isinstance(value, list) and bool(value)
            and all(_is_nonempty_string(item) for item in value)
            and len(set(value)) == len(value))


def _registry_audit_arguments(payload: Any):
    """Accept bundled records or the three-map registry input form."""
    if isinstance(payload, list):
        return payload, None, None
    if not isinstance(payload, Mapping):
        return None
    if all(name in payload for name in ("registrations", "state_contracts", "release_reviews")):
        return (payload["registrations"], payload["state_contracts"],
                payload["release_reviews"])
    records = payload.get("records")
    if isinstance(records, list):
        return records, None, None
    return None


def _check_oracle_release_ready(claim: QualificationClaim, path: Path) -> list[str]:
    """Reuse the fail-closed registry audit as the oracle release gate."""
    arguments = _registry_audit_arguments(json.loads(path.read_text(encoding="utf-8")))
    if arguments is None:
        return ["oracle_registry_payload_unrecognized"]
    registrations, contracts, reviews = arguments
    if contracts is None and reviews is None:
        report = audit_native_oracle_registry(registrations,
                                              expected_scenes=(claim.scene,))
    else:
        report = audit_native_oracle_registry(registrations, contracts, reviews,
                                              expected_scenes=(claim.scene,))
    reasons: list[str] = []
    if report.get("oracle_release_ready") is not True:
        reasons.append("oracle_registry_not_release_ready")
        reasons.extend(f"oracle_registry_missing_scene:{scene}"
                       for scene in report.get("missing_scenes", []))
        reasons.extend(f"oracle_registry_invalid:{row.get('kind')}:{row.get('scene_key')}:"
                       f"{row.get('reason')}" for row in report.get("invalid", []))
        reasons.extend(f"oracle_registry_failed:{row.get('kind')}:{row.get('scene_key')}:"
                       f"{row.get('reason')}" for row in report.get("failed", []))
    return sorted(set(reasons))


def _check_communication_replay_validated(claim: QualificationClaim, path: Path) -> list[str]:
    """Replay the archived communication report through the shared validator."""
    from .communication import validate_communication_report

    report = json.loads(path.read_text(encoding="utf-8"))
    result = validate_communication_report(report)
    if result.get("validated") is True:
        return []
    reasons = ["communication_replay_not_validated"]
    reasons.extend(f"communication_replay_mismatch:{item}"
                   for item in result.get("mismatches", []))
    return sorted(set(reasons))


def _check_business_outcomes_validated(claim: QualificationClaim, path: Path) -> list[str]:
    """Apply the native-result oracle_status / alternative-path certificate gate."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, Mapping):
        rows = payload.get("rows", payload.get("results"))
    else:
        rows = payload
    if not isinstance(rows, list) or not rows:
        return ["native_oracle_rows_missing"]
    reasons: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            reasons.append("native_oracle_row_not_mapping")
            continue
        measurement_id = row.get("measurement_id")
        if not _is_nonempty_string(measurement_id):
            reasons.append("native_oracle_row_missing_id")
            measurement_id = "?"
        if row.get("oracle_status") != "verified":
            reasons.append(f"native_oracle_row_not_verified:{measurement_id}")
        label = row.get("feasibility_label")
        if label not in _BINARY_LABELS:
            reasons.append(f"native_oracle_row_non_binary:{measurement_id}")
        elif label == "not_deployable":
            digest = row.get("certificate_sha256")
            if (row.get("alternative_path_gate") != "verified" or not _is_sha256(digest)):
                reasons.append(
                    f"native_oracle_row_not_deployable_without_certificate:{measurement_id}")
        if not _evidence_refs(row.get("evidence_refs")):
            reasons.append(f"native_oracle_row_missing_evidence:{measurement_id}")
    return sorted(set(reasons))


GATE_CHECKERS = {
    "oracle_release_ready": _check_oracle_release_ready,
    "communication_replay_validated": _check_communication_replay_validated,
    "business_outcomes_validated": _check_business_outcomes_validated,
}


def review_qualification_claim(claim: QualificationClaim, evidence_root: str | Path):
    """Re-derive every gate of one claim from evidence bytes; never approve.

    Returns a JSON-ready review dictionary.  Content faults (bad structure,
    missing/tampered evidence, release-flag assertions, unsatisfied gates) are
    reported as ``blocked`` reason codes; the function does not raise for them.
    """
    root = Path(evidence_root)
    structural = _structural_reasons(claim)
    review: dict[str, Any] = {
        "claim_id": claim.claim_id,
        "scene": claim.scene,
        "claimed_gate": claim.claimed_gate,
        "structural": {"status": "failed" if structural else "ok",
                       "reasons": structural},
        "evidence": [],
        "gate": {"name": claim.claimed_gate, "status": "not_run", "reasons": []},
        "reasons": [],
        "review_status": "blocked",
    }
    reasons = list(structural)
    evidence_rows = [{"path": entry.path, "sha256": entry.sha256, "role": entry.role,
                      "status": "not_checked"}
                     for entry in claim.evidence_manifest] if structural else (
        _verify_evidence(claim, root))
    review["evidence"] = evidence_rows
    evidence_ok = bool(evidence_rows) and all(row["status"] == "verified"
                                              for row in evidence_rows)
    if not structural and not evidence_ok:
        reasons.extend(row.get("reason", f"evidence_not_verified:{row.get('path')}")
                       for row in evidence_rows if row["status"] != "verified")
    if structural or not evidence_ok:
        review["gate"]["reasons"] = ["gate_not_run:prerequisite_failed"]
        review["reasons"] = sorted(set(reasons))
        return review

    entry = claim.evidence_manifest[0]
    payload_path = root / entry.path
    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        reasons.append(f"evidence_unreadable:{entry.path}")
        review["gate"]["reasons"] = ["gate_not_run:prerequisite_failed"]
        review["reasons"] = sorted(set(reasons))
        return review
    reasons.extend(_release_flag_violations(payload, entry.path))
    if any(reason.startswith("evidence_release_flag_true") for reason in reasons):
        review["gate"]["reasons"] = ["gate_not_run:release_flag_asserted_true"]
        review["reasons"] = sorted(set(reasons))
        return review

    checker = GATE_CHECKERS[claim.claimed_gate]
    try:
        gate_reasons = checker(claim, payload_path)
    except (OSError, ValueError, KeyError, TypeError) as error:
        gate_reasons = [f"gate_checker_error:{claim.claimed_gate}:"
                        f"{type(error).__name__}"]
    review["gate"]["status"] = "passed" if not gate_reasons else "failed"
    review["gate"]["reasons"] = sorted(set(gate_reasons))
    reasons.extend(gate_reasons)
    review["reasons"] = sorted(set(reasons))
    review["review_status"] = "pending" if not reasons else "blocked"
    return review


def build_attestation(claim: QualificationClaim, evidence_root: str | Path):
    """Wrap one claim review in the versioned attestation envelope.

    The attestation copies the full claim, seals it, records per-gate review
    results with the evidence file and its verified SHA-256, and is limited to
    ``pending``/``blocked``.  Release flags are emitted ``False`` regardless of
    review outcome.
    """
    review = review_qualification_claim(claim, evidence_root)
    payload = claim_to_dict(claim)
    body: dict[str, Any] = {
        "schema": ATTESTATION_SCHEMA,
        "version": ATTESTATION_VERSION,
        "visibility": "evaluator_only",
        "claim": payload,
        "claim_sha256": canonical_sha256(payload),
        "review": review,
        "attestation_status": review["review_status"],
        "blocked_reasons": review["reasons"],
        "release_flags": {flag: False for flag in RELEASE_FLAGS},
        "formal_promotion": FORMAL_PROMOTION_STATUS,
    }
    body["attestation_sha256"] = canonical_sha256(body)
    return body


def pending_manifest(claims: Iterable[QualificationClaim], evidence_root: str | Path):
    """Aggregate claim reviews into a pending manifest; never a promotion.

    The manifest carries only ``pending``/``blocked`` per claim plus the
    consolidated list of evidence that still has to be supplied (missing or
    hash-mismatched files).  Duplicate or absent claim ids make the manifest
    itself blocked.  Formal promotion stays a separate release action.
    """
    claim_list = list(claims)
    manifest_reasons: list[str] = []
    if not claim_list:
        manifest_reasons.append("manifest_no_claims")
    rows: list[dict[str, Any]] = []
    missing_evidence: list[dict[str, str]] = []
    seen: set[str] = set()
    for claim in claim_list:
        attestation = build_attestation(claim, evidence_root)
        review = attestation["review"]
        row_reasons = list(review["reasons"])
        status = review["review_status"]
        if not _is_nonempty_string(claim.claim_id):
            status = "blocked"
            row_reasons.append("claim_id_empty")
        elif claim.claim_id in seen:
            status = "blocked"
            reason = f"duplicate_claim_id:{claim.claim_id}"
            row_reasons.append(reason)
            manifest_reasons.append(reason)
        seen.add(claim.claim_id)
        for entry in review["evidence"]:
            if entry["status"] != "verified":
                missing_evidence.append({
                    "claim_id": claim.claim_id,
                    "path": entry["path"],
                    "reason": entry.get("reason", f"evidence_{entry['status']}:{entry['path']}"),
                })
        rows.append({
            "claim_id": claim.claim_id,
            "scene": claim.scene,
            "task_variant": claim.task_variant,
            "factor": claim.factor,
            "level": claim.level,
            "claimed_gate": claim.claimed_gate,
            "attestation_status": status,
            "blocked_reasons": sorted(set(row_reasons)),
        })
    rows.sort(key=lambda row: str(row["claim_id"]))
    missing_evidence.sort(key=lambda item: (item["claim_id"], item["path"]))
    pending_ids = sorted(row["claim_id"] for row in rows
                         if row["attestation_status"] == "pending")
    blocked_ids = sorted(row["claim_id"] for row in rows
                         if row["attestation_status"] == "blocked")
    if blocked_ids:
        manifest_reasons.append(f"blocked_claims:{len(blocked_ids)}")
    body: dict[str, Any] = {
        "schema": PENDING_MANIFEST_SCHEMA,
        "version": PENDING_MANIFEST_VERSION,
        "visibility": "evaluator_only",
        "claim_count": len(claim_list),
        "claims": rows,
        "pending_claim_ids": pending_ids,
        "blocked_claim_ids": blocked_ids,
        "missing_evidence": missing_evidence,
        "manifest_status": "pending" if not manifest_reasons else "blocked",
        "manifest_blocked_reasons": sorted(set(manifest_reasons)),
        "release_flags": {flag: False for flag in RELEASE_FLAGS},
        "formal_promotion": FORMAL_PROMOTION_STATUS,
    }
    body["manifest_sha256"] = canonical_sha256(body)
    return body


def _write_new_json(path: Path, payload: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True,
                  allow_nan=False)
        stream.write("\n")


def main(argv=None) -> int:
    """Bounded offline CLI; output paths are new and never overwritten."""
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("review", "manifest"))
    parser.add_argument("--claim", type=Path, required=True, action="append",
                        help="serialized claim JSON; repeat for manifest")
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("output already exists")
    claims = []
    for path in args.claim:
        try:
            claims.append(claim_from_dict(
                json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError) as error:
            parser.error(f"unusable claim {path}: {error}")
    if args.operation == "review":
        if len(claims) != 1:
            parser.error("review requires exactly one --claim")
        result = build_attestation(claims[0], args.evidence_root)
        status = result["attestation_status"]
    else:
        result = pending_manifest(claims, args.evidence_root)
        status = result["manifest_status"]
    _write_new_json(args.output, result)
    return 0 if status == "pending" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ATTESTATION_SCHEMA",
    "ATTESTATION_STATUSES",
    "ATTESTATION_VERSION",
    "CAPABILITY_KEYS",
    "CLAIM_SCHEMA",
    "CLAIM_VERSION",
    "EvidenceEntry",
    "FORMAL_PROMOTION_STATUS",
    "GATE_CHECKERS",
    "GATE_ROLES",
    "PENDING_MANIFEST_SCHEMA",
    "PENDING_MANIFEST_VERSION",
    "QUALIFICATION_LEVELS",
    "RELEASE_FLAGS",
    "QualificationClaim",
    "build_attestation",
    "claim_from_dict",
    "claim_to_dict",
    "pending_manifest",
    "review_qualification_claim",
]
