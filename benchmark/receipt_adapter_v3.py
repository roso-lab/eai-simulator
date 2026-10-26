"""Evaluator receipt -> normalized D1--D6 input adapter.

This module is the production seam between a simulator/evaluator and
``results_pipeline_v3``.  It deliberately accepts evaluator-owned facts only:
process exit, assignment completion, or a caller supplied score cannot create a
paper result.  The adapter is CPU-only and does not import Isaac or a submitted
method.

The output is still ``measured_unqualified`` until the physical, statistical,
and release gates are satisfied.  Missing, unknown, or incomplete evidence
raises :class:`ReceiptAdapterError` instead of being recoded as failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from tools.benchmark_branch.oracle_predicates import (
    HoldInterval,
    MilestoneEvent,
    PredicateStreams,
    TaskEvent,
    ZoneInterval,
    dedupe_predicate_verdicts,
    judge_anchor,
    VerdictStatus,
    CauseCode,
    predicate_scoring_role,
)

from .records_v3 import canonical_digest
from .results_pipeline_v3 import PIPELINE_SCHEMA, PIPELINE_VERSION, compute_results
from .scorebook_v3 import PAPER_COMMIT, default_scorebook_v3
from .case_manifest_v1 import CaseManifestError, validate_case_manifest
from .oracle_obligation_v1 import ObligationReductionError, reduce_obligations
from .artifact_verifier_v3 import ArtifactVerificationStatus, verify_artifact_manifest_v3


RAW_RECEIPT_SCHEMA = "heterocoord.raw-evaluator-receipt.v1"
ADAPTER_VERSION = "receipt-adapter-v3"
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_BRANCHES = {"fact", "reference", "sham", "intervention"}
_D2_CONDITIONS = ("C0", "C1", "C2", "C3")
_D3_RELATIONS = ("P2", "P3J", "P3S", "P4")


class ReceiptAdapterError(ValueError):
    """Raised when raw evaluator evidence cannot support normalization."""


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReceiptAdapterError(f"{name} must be an object")
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReceiptAdapterError(f"{name} must be non-empty text")
    return value


def _digest(value: Any, name: str) -> str:
    text = _text(value, name)
    if not _DIGEST_RE.fullmatch(text):
        raise ReceiptAdapterError(f"{name} must be a lowercase SHA-256")
    return text


def _finite(value: Any, name: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ReceiptAdapterError(f"{name} must be a finite real number")
    result = float(value)
    if minimum is not None and result < minimum:
        raise ReceiptAdapterError(f"{name} is below its lower bound")
    if maximum is not None and result > maximum:
        raise ReceiptAdapterError(f"{name} is above its upper bound")
    return result


def _tick(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ReceiptAdapterError(f"{name} must be a non-negative integer tick")
    return value


def _require_exact(value: Mapping[str, Any], required: set[str], name: str, optional: set[str] | None = None) -> None:
    allowed = required | (optional or set())
    if not required.issubset(value) or not set(value).issubset(allowed):
        raise ReceiptAdapterError(f"{name} schema mismatch")


def _stream(row: Mapping[str, Any]) -> PredicateStreams:
    _require_exact(
        row,
        {"zone_intervals", "hold_intervals", "milestones", "task_events", "identity_records", "integrity_confirmed"},
        "streams",
    )
    zones = []
    for index, item in enumerate(row["zone_intervals"]):
        item = _mapping(item, f"streams.zone_intervals[{index}]")
        _require_exact(item, {"zone_id", "holder_id", "enter_tick", "exit_tick"}, f"streams.zone_intervals[{index}")
        zones.append(ZoneInterval(_text(item["zone_id"], "zone_id"), _text(item["holder_id"], "holder_id"), _tick(item["enter_tick"], "enter_tick"), _tick(item["exit_tick"], "exit_tick")))
    holds = []
    for index, item in enumerate(row["hold_intervals"]):
        item = _mapping(item, f"streams.hold_intervals[{index}]")
        _require_exact(item, {"object_id", "robot_id", "start_tick", "end_tick"}, f"streams.hold_intervals[{index}")
        holds.append(HoldInterval(_text(item["object_id"], "object_id"), _text(item["robot_id"], "robot_id"), _tick(item["start_tick"], "start_tick"), _tick(item["end_tick"], "end_tick")))
    milestones = []
    for index, item in enumerate(row["milestones"]):
        item = _mapping(item, f"streams.milestones[{index}]")
        _require_exact(item, {"label", "tick"}, f"streams.milestones[{index}")
        milestones.append(MilestoneEvent(_text(item["label"], "milestone.label"), _tick(item["tick"], "milestone.tick")))
    events = []
    for index, item in enumerate(row["task_events"]):
        item = _mapping(item, f"streams.task_events[{index}]")
        _require_exact(item, {"name", "subject", "tick"}, f"streams.task_events[{index}")
        events.append(TaskEvent(_text(item["name"], "task_event.name"), _text(item["subject"], "task_event.subject"), _tick(item["tick"], "task_event.tick")))
    if not isinstance(row["identity_records"], Mapping) or type(row["integrity_confirmed"]) is not bool:
        raise ReceiptAdapterError("streams identity_records/integrity_confirmed are invalid")
    return PredicateStreams(
        zone_intervals=zones,
        hold_intervals=holds,
        milestones=milestones,
        task_events=events,
        identity_records=dict(row["identity_records"]),
        integrity_confirmed=row["integrity_confirmed"],
    )


def _validate_receipt(row: Mapping[str, Any]) -> dict[str, Any]:
    _require_exact(
        row,
        {"schema", "receipt_id", "identity", "catalog", "plan", "parameters", "evaluator", "streams", "facts"},
        "raw receipt",
    )
    if row["schema"] != RAW_RECEIPT_SCHEMA:
        raise ReceiptAdapterError("unsupported raw receipt schema")
    receipt_id = _text(row["receipt_id"], "receipt_id")
    identity = _mapping(row["identity"], "identity")
    required_identity = {"task_id", "anchor_id", "domain", "relation_signature", "difficulty", "condition_id", "system_id", "base_instance_id", "repeat_id", "branch", "checkpoint_id"}
    _require_exact(identity, required_identity, "identity")
    for key in required_identity - {"relation_signature", "branch"}:
        _text(identity[key], f"identity.{key}")
    if not isinstance(identity["relation_signature"], list) or not identity["relation_signature"] or any(not isinstance(item, str) or not item for item in identity["relation_signature"]):
        raise ReceiptAdapterError("identity.relation_signature must be a non-empty list")
    if identity["branch"] not in _BRANCHES:
        raise ReceiptAdapterError("identity.branch is invalid")
    catalog = _mapping(row["catalog"], "catalog")
    plan = _mapping(row["plan"], "plan")
    parameters = _mapping(row["parameters"], "parameters")
    if not isinstance(catalog.get("anchors"), list) or not catalog["anchors"]:
        raise ReceiptAdapterError("catalog.anchors is required")
    anchor_ids = []
    for index, item in enumerate(catalog["anchors"]):
        if isinstance(item, str) and item:
            anchor_ids.append(item)
        elif isinstance(item, Mapping) and isinstance(item.get("anchor_id"), str) and item["anchor_id"]:
            anchor_ids.append(item["anchor_id"])
        else:
            raise ReceiptAdapterError(f"catalog.anchors[{index}] must contain an anchor ID")
    if len(set(anchor_ids)) != len(anchor_ids):
        raise ReceiptAdapterError("catalog.anchors must be unique")
    if identity["anchor_id"] not in set(anchor_ids):
        raise ReceiptAdapterError("identity.anchor_id is absent from catalog.anchors")
    if type(plan.get("seed")) is not int:
        raise ReceiptAdapterError("plan.seed must be an integer")
    for key in ("alpha", "d6_reference_bits", "d6_reference_time"):
        if key in parameters:
            _finite(parameters[key], f"parameters.{key}", minimum=0.0)
    evaluator = _mapping(row["evaluator"], "evaluator")
    _require_exact(
        evaluator,
        {"status", "evaluator_version", "evidence_hash", "evidence_refs"},
        "evaluator",
        optional={"artifact_manifest_digest"},
    )
    status = _text(evaluator["status"], "evaluator.status")
    if status not in {"valid_terminal", "safe_stop", "team_state_infeasible", "method_timeout", "malformed_action", "crashed", "infrastructure_invalid"}:
        raise ReceiptAdapterError(f"unsupported evaluator status: {status}")
    _text(evaluator["evaluator_version"], "evaluator.evaluator_version")
    evidence_hash = _digest(evaluator["evidence_hash"], "evaluator.evidence_hash")
    if not isinstance(evaluator["evidence_refs"], list) or not evaluator["evidence_refs"]:
        raise ReceiptAdapterError("evaluator.evidence_refs must be non-empty")
    # The v1 receipt hash binds the complete receipt envelope, while leaving
    # the hash field itself out of the preimage.  This catches a valid-looking
    # but substituted 64-hex digest before any score is produced.  The
    # referenced evidence bytes are checked separately by the artifact
    # verifier; this check binds the receipt identity to those references.
    envelope = dict(row)
    envelope_evaluator = dict(evaluator)
    envelope_evaluator.pop("evidence_hash", None)
    envelope["evaluator"] = envelope_evaluator
    expected_evidence_hash = hashlib.sha256(
        json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    if evidence_hash != expected_evidence_hash:
        raise ReceiptAdapterError("evaluator.evidence_hash does not bind the receipt envelope")
    _stream(_mapping(row["streams"], "streams"))
    facts = _mapping(row["facts"], "facts")
    if "artifact_manifest_digest" in evaluator:
        _digest(evaluator["artifact_manifest_digest"], "evaluator.artifact_manifest_digest")
    if status == "safe_stop":
        if facts.get("safety_predicate") is not True:
            raise ReceiptAdapterError("safe_stop requires certified safety_predicate=true")
        _digest(facts.get("safe_stop_certificate_digest"), "facts.safe_stop_certificate_digest")
        if facts.get("success") is not False:
            raise ReceiptAdapterError("safe_stop cannot be recorded as task success")
    elif status == "team_state_infeasible":
        _digest(facts.get("infeasibility_proof_hash"), "facts.infeasibility_proof_hash")
        if facts.get("success") is not False:
            raise ReceiptAdapterError("team_state_infeasible cannot be recorded as task success")
    return {"receipt_id": receipt_id, **dict(row)}


def _verify_external_evidence(
    rows: Sequence[Mapping[str, Any]],
    *,
    artifact_manifest: Mapping[str, Mapping[str, Any]],
    artifact_root: str | Path,
    require_receipt_binding: bool = False,
) -> str:
    """Verify every receipt evidence reference against immutable artifact bytes.

    The receipt envelope hash proves that the evaluator identity and streams
    were not edited in transit; it does not prove that the referenced raw
    observation files still exist or contain the bytes claimed by the run.
    This second digest boundary is therefore opt-in for legacy fixtures but is
    required by the production case path.
    """

    if not isinstance(artifact_manifest, Mapping) or not artifact_manifest:
        raise ReceiptAdapterError("artifact_manifest must be a non-empty mapping")
    try:
        verifications = verify_artifact_manifest_v3(artifact_manifest, artifact_root)
    except (OSError, TypeError, ValueError) as exc:
        raise ReceiptAdapterError(f"artifact manifest verification failed: {exc}") from exc
    invalid = [item for item in verifications if item.status is not ArtifactVerificationStatus.VALID]
    if invalid:
        summary = ", ".join(f"{item.label}:{item.status.value}" for item in invalid)
        raise ReceiptAdapterError(f"artifact manifest contains invalid evidence: {summary}")

    by_label = {str(label): dict(entry) for label, entry in artifact_manifest.items()}
    by_path = {str(entry.get("path")): entry for entry in by_label.values()}
    verified_artifact_hashes = {
        str(entry.get("sha256")) for entry in by_label.values()
    }
    manifest_digest = canonical_digest(artifact_manifest)
    for index, row in enumerate(rows):
        refs = row["evaluator"]["evidence_refs"]
        for ref in refs:
            if not isinstance(ref, str) or not ref.strip():
                raise ReceiptAdapterError(f"receipt[{index}] evidence_refs must contain non-empty strings")
            entry = by_label.get(ref) or by_path.get(ref)
            if entry is None:
                raise ReceiptAdapterError(f"receipt[{index}] evidence reference is absent from artifact manifest: {ref}")
            if require_receipt_binding:
                bound_manifest = row["evaluator"].get("artifact_manifest_digest")
                source_hash = row["facts"].get("source_evidence_hash")
                if bound_manifest is not None:
                    if bound_manifest != manifest_digest:
                        raise ReceiptAdapterError(
                            f"receipt[{index}] artifact manifest digest does not match supplied manifest"
                        )
                elif source_hash != entry.get("sha256"):
                    raise ReceiptAdapterError(
                        f"receipt[{index}] evidence bytes are not bound to the sealed receipt"
                    )
        status = row["evaluator"]["status"]
        proof_field = {
            "safe_stop": "safe_stop_certificate_digest",
            "team_state_infeasible": "infeasibility_proof_hash",
        }.get(status)
        if proof_field is not None:
            proof_digest = row["facts"][proof_field]
            if proof_digest not in verified_artifact_hashes:
                raise ReceiptAdapterError(
                    f"receipt[{index}] {proof_field} does not match any verified artifact"
                )
    return canonical_digest(artifact_manifest)


def _oracle_summary(receipt: Mapping[str, Any]) -> dict[str, Any]:
    identity = receipt["identity"]
    streams = _stream(receipt["streams"])
    verdicts = dedupe_predicate_verdicts(judge_anchor(identity["anchor_id"], streams))
    return {
        "receipt_id": receipt["receipt_id"],
        "anchor_id": identity["anchor_id"],
        "verdicts": [
            {
                "predicate_id": item.predicate_id,
                "status": item.status.value,
                "cause": item.cause.value if item.cause else None,
                "evidence": dict(item.evidence),
                "violations": [{"tick": v.tick, "detail": v.detail} for v in item.violations],
            }
            for item in verdicts
        ],
    }


def _validate_oracle_success_consistency(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Enforce registered safety and deadline success conditions at ingress.

    Oracle verdicts remain evaluator-owned observations.  This check only
    prevents a contradictory caller fact (``success=true`` despite a safety
    violation, missed deadline or missing success evidence) from reaching
    D2/D6. Ordinary timing and resource predicates remain D3 obligations.
    """
    # Check success conditions before D3 source deduplication: an occupancy
    # group can have both service and safety consumers.  D3 deduplication
    # must not erase the safety predicate's identity.
    verdicts = judge_anchor(receipt["identity"]["anchor_id"], _stream(receipt["streams"]))
    # Classify by the registered contract, not verdict evidence: missing-event
    # verdicts need not repeat scoring metadata.  Pilot V2 has one safety rule.
    anchor = receipt["identity"]["anchor_id"]
    def is_success_condition(item):
        return predicate_scoring_role(anchor, item.predicate_id) in {
            "safety_success", "success_condition"
        }
    safety = [item for item in verdicts if is_success_condition(item)]
    violated = [item for item in safety if item.status is VerdictStatus.VIOLATED
                or (item.status is VerdictStatus.DATA_INSUFFICIENT and item.cause is CauseCode.UNREACHED)]
    insufficient = [item for item in safety if item.status is VerdictStatus.DATA_INSUFFICIENT
                    and item.cause is not CauseCode.UNREACHED]
    if type(receipt["facts"].get("success")) is not bool:
        raise ReceiptAdapterError("facts.success must be an evaluator-owned boolean")
    d6 = receipt["facts"].get("d6")
    if d6 is not None:
        d6 = _mapping(d6, "facts.d6")
        if type(d6.get("success")) is not bool or d6["success"] != receipt["facts"]["success"]:
            raise ReceiptAdapterError("D6 success must equal the same receipt's task success")
    if violated and receipt["facts"]["success"]:
        raise ReceiptAdapterError(
            "facts.success=true contradicts unmet oracle success condition(s): "
            + ", ".join(item.predicate_id for item in violated)
        )
    if insufficient:
        causes = sorted({item.cause.value for item in insufficient if item.cause})
        raise ReceiptAdapterError(
            "oracle success evidence is insufficient: " + ", ".join(causes or ["unknown"])
        )
    return {
        "success_conditions": [item.predicate_id for item in safety],
        "unmet_conditions": [item.predicate_id for item in violated],
        "status": "unmet" if violated else "satisfied" if safety else "not_applicable",
    }


def _validate_case_observation_bindings(
    rows: Sequence[Mapping[str, Any]],
    case: Mapping[str, Any],
) -> None:
    """Ensure observed entities, zones, and robots belong to the bound case.

    The manifest digest binds the map and roster bytes, but does not by itself
    prove that an evaluator receipt referenced IDs from those products.  Keep
    this check at the receipt boundary so a digest-correct receipt cannot
    smuggle an unrelated zone, object, or robot into oracle reduction.
    """

    scene = case["scene"]
    allowed_zones = set(scene["zone_ids"])
    allowed_entities = set(scene["entity_ids"])
    allowed_robots = {
        robot["robot_id"] for robot in case["robot_pool"]["robots"]
    }
    for index, row in enumerate(rows):
        streams = _stream(row["streams"])
        unknown_zones = {
            interval.zone_id for interval in streams.zone_intervals
            if interval.zone_id not in allowed_zones
        }
        unknown_entities = {
            interval.object_id for interval in streams.hold_intervals
            if interval.object_id not in allowed_entities
        }
        # ``ZoneInterval.holder_id`` is the oracle's evaluator-internal
        # occupancy ledger.  It may identify a lot, request, load, package,
        # or robot, and is intentionally independent of the submitted
        # system's robot roster.  V3 logical-hold observations carry the
        # holder robot identity that must bind to ``case.robot_pool``; no
        # physical-contact channel participates in this oracle.
        observed_robots = {
            interval.robot_id for interval in streams.hold_intervals
        }
        unknown_holders = {
            interval.holder_id for interval in streams.zone_intervals
        } - (allowed_entities | allowed_robots)
        unknown_robots = observed_robots - allowed_robots
        if unknown_zones:
            raise ReceiptAdapterError(
                f"receipt[{index}] references zones absent from case manifest: "
                f"{sorted(unknown_zones)}"
            )
        if unknown_entities:
            raise ReceiptAdapterError(
                f"receipt[{index}] references entities absent from case manifest: "
                f"{sorted(unknown_entities)}"
            )
        if unknown_robots:
            raise ReceiptAdapterError(
                f"receipt[{index}] references robots absent from case manifest: "
                f"{sorted(unknown_robots)}"
            )
        if unknown_holders:
            raise ReceiptAdapterError(
                f"receipt[{index}] references occupancy holders absent from case manifest: "
                f"{sorted(unknown_holders)}"
            )


def _mean(rows: Sequence[Mapping[str, Any]], key: str) -> float:
    if not rows:
        raise ReceiptAdapterError(f"no rows available for {key}")
    return sum(_finite(row[key], key, minimum=0.0, maximum=1.0) for row in rows) / len(rows)


def _obligation_rows(receipt: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    facts = receipt["facts"]
    rows = facts.get("obligations")
    if not isinstance(rows, list):
        raise ReceiptAdapterError("facts.obligations is required")
    result = []
    for index, row in enumerate(rows):
        row = _mapping(row, f"facts.obligations[{index}]")
        # ``subtype`` is required only for P3-J/P3-S.  P2 and P4 are
        # unspecialized relations and must not be forced to carry a dummy
        # subtype field.  Accept exactly the two legal shapes here so a
        # well-formed evaluator receipt is not rejected before scoring.
        allowed_shapes = ({"relation", "outcome"}, {"relation", "outcome", "subtype"})
        if set(row) not in allowed_shapes:
            raise ReceiptAdapterError(f"facts.obligations[{index}] schema mismatch")
        relation = _text(row["relation"], "obligation.relation")
        subtype = row.get("subtype")
        if relation == "P2":
            if subtype is not None:
                raise ReceiptAdapterError("P2 obligation must not declare subtype")
            key = "P2"
        elif relation == "P3" and subtype in {"J", "S"}:
            key = "P3" + subtype
        elif relation == "P4":
            if subtype is not None:
                raise ReceiptAdapterError("P4 obligation must not declare subtype")
            key = "P4"
        else:
            raise ReceiptAdapterError(f"unsupported obligation relation: {relation}/{subtype}")
        if row["outcome"] not in {"completed", "violated", "unreached"}:
            raise ReceiptAdapterError("unknown/incomplete obligation cannot enter a point estimate")
        result.append({"key": key, "value": 1.0 if row["outcome"] == "completed" else 0.0})
    return result


def _validate_oracle_obligation_consistency(receipt: Mapping[str, Any]) -> None:
    """Recompute D3 from the frozen plan; never rewrite a sealed receipt."""
    plan = _mapping(receipt["plan"], "plan")
    binding = plan.get("oracle_obligation_binding")
    if binding is None:
        raise ReceiptAdapterError("case receipt requires plan.oracle_obligation_binding")
    try:
        expected = reduce_obligations(receipt["identity"]["anchor_id"],
                                      _stream(receipt["streams"]), binding)
    except (ObligationReductionError, KeyError) as exc:
        raise ReceiptAdapterError(f"oracle obligation reduction failed: {exc}") from exc
    if receipt["facts"].get("obligations") != expected:
        raise ReceiptAdapterError("facts.obligations disagree with frozen oracle reduction")


def normalize_receipts(
    receipts: Sequence[Mapping[str, Any]],
    *,
    calibration: Mapping[str, Any] | None = None,
    case_manifest: Mapping[str, Any] | None = None,
    artifact_manifest: Mapping[str, Mapping[str, Any]] | None = None,
    artifact_root: str | Path | None = None,
    require_artifact_evidence: bool = False,
    scope: str = "measured_unqualified",
) -> dict[str, Any]:
    """Convert evaluator-owned receipts into a results-pipeline input package.

    The adapter requires a complete C0--C3 directory and a calibration reference
    for D6.  D5 may remain empty/NA until its fixed family directory is present;
    this is explicit in the resulting package.
    """

    if scope not in {"measured_diagnostic", "measured_unqualified"}:
        raise ReceiptAdapterError("receipt adapter only emits measured scopes")
    # A bound case in the unqualified measured scope is already a production
    # path.  Do not make its evidence-integrity guarantee depend on a caller
    # remembering an extra boolean flag; retain the explicit flag for callers
    # that want the same hardening in a diagnostic scope.
    production_case = scope == "measured_unqualified" and case_manifest is not None
    if (require_artifact_evidence or production_case) and (artifact_manifest is None or artifact_root is None):
        raise ReceiptAdapterError("production receipt normalization requires artifact_manifest and artifact_root")
    if (artifact_manifest is None) != (artifact_root is None):
        raise ReceiptAdapterError("artifact_manifest and artifact_root must be supplied together")
    if not isinstance(receipts, Sequence) or isinstance(receipts, (str, bytes)) or not receipts:
        raise ReceiptAdapterError("receipts must be a non-empty sequence")
    rows = [_validate_receipt(_mapping(item, f"receipts[{index}]")) for index, item in enumerate(receipts)]
    artifact_manifest_digest = None
    if artifact_manifest is not None and artifact_root is not None:
        artifact_manifest_digest = _verify_external_evidence(
            rows,
            artifact_manifest=artifact_manifest,
            artifact_root=artifact_root,
            require_receipt_binding=production_case or require_artifact_evidence,
        )
    case = None
    if case_manifest is not None:
        try:
            case = validate_case_manifest(case_manifest)
        except CaseManifestError as exc:
            raise ReceiptAdapterError(f"invalid case manifest: {exc}") from exc
    identities = [row["identity"] for row in rows]
    if len({row["receipt_id"] for row in rows}) != len(rows):
        raise ReceiptAdapterError("receipt_id values must be unique")
    catalog = rows[0]["catalog"]
    plan = rows[0]["plan"]
    parameters = rows[0]["parameters"]
    for row in rows[1:]:
        if row["catalog"] != catalog or row["plan"] != plan or row["parameters"] != parameters:
            raise ReceiptAdapterError("all receipts must share catalog, plan, and parameters")
    if case is not None:
        expected_identity = {"task_id": case["task_id"], "anchor_id": case["anchor_id"], "domain": case["domain"], "relation_signature": case["relation_signature"], "base_instance_id": case["base_instance_id"]}
        for index, row in enumerate(rows):
            identity = row["identity"]
            for key, expected in expected_identity.items():
                if identity[key] != expected:
                    raise ReceiptAdapterError(f"receipt[{index}] identity.{key} does not match case manifest")
            if canonical_digest(row["catalog"]) != case["catalog_digest"]:
                raise ReceiptAdapterError(f"receipt[{index}] catalog digest does not match case manifest")
            if canonical_digest(row["plan"]) != case["plan_digest"]:
                raise ReceiptAdapterError(f"receipt[{index}] plan digest does not match case manifest")
            if row["evaluator"]["evaluator_version"] != case["evaluator"]["evaluator_version"]:
                raise ReceiptAdapterError(f"receipt[{index}] evaluator version does not match case manifest")
        _validate_case_observation_bindings(rows, case)
        for row in rows:
            _validate_oracle_success_consistency(row)
            _validate_oracle_obligation_consistency(row)
    if any(row["evaluator"]["status"] in {"infrastructure_invalid", "crashed"} for row in rows):
        raise ReceiptAdapterError("infrastructure-invalid receipt cannot be normalized")
    if any(row["evaluator"]["status"] not in {"valid_terminal", "safe_stop", "team_state_infeasible"} for row in rows):
        raise ReceiptAdapterError("method failure remains an outcome and needs a paired reducer before normalization")

    d1_rows = []
    d3_values: dict[str, list[float]] = {key: [] for key in _D3_RELATIONS}
    d4_items: list[Mapping[str, Any]] = []
    d5_families: dict[str, dict[str, dict[str, list[float]]]] = {}
    d6_runs: list[dict[str, Any]] = []
    condition_success: dict[str, list[float]] = {key: [] for key in _D2_CONDITIONS}
    oracle_summaries = []
    for row in rows:
        facts = row["facts"]
        d1 = _mapping(facts.get("d1"), "facts.d1")
        _require_exact(d1, {"validity", "organization", "infeasible", "complete_plan"}, "facts.d1")
        if type(d1["complete_plan"]) is not bool:
            raise ReceiptAdapterError("facts.d1.complete_plan must be boolean")
        d1_rows.append(d1)
        condition = row["identity"]["condition_id"]
        if condition not in _D2_CONDITIONS:
            raise ReceiptAdapterError(f"unsupported D2 condition: {condition}")
        if type(facts.get("success")) is not bool:
            raise ReceiptAdapterError("facts.success must be an evaluator-owned boolean")
        condition_success[condition].append(1.0 if facts["success"] else 0.0)
        for item in _obligation_rows(row):
            d3_values[item["key"]].append(item["value"])
        items = facts.get("d4_items", [])
        if not isinstance(items, list):
            raise ReceiptAdapterError("facts.d4_items must be a list")
        for item in items:
            item = dict(_mapping(item, "facts.d4_items item"))
            if item.get("kind") not in {"deadline", "time_valued", "continuous_supply"}:
                raise ReceiptAdapterError("unsupported D4 item kind")
            item["e"] = _finite(item.get("e"), "D4.e", minimum=0.0, maximum=1.0)
            if item["kind"] != "deadline":
                item["phi"] = _finite(item.get("phi"), "D4.phi", minimum=0.0, maximum=1.0)
            d4_items.append(item)
        attempt = facts.get("d5_attempt")
        if attempt is not None:
            attempt = _mapping(attempt, "facts.d5_attempt")
            _require_exact(attempt, {"family_id", "case_id", "unit_id", "success"}, "facts.d5_attempt")
            if type(attempt["success"]) is not bool:
                raise ReceiptAdapterError("D5 success must be boolean")
            d5_families.setdefault(attempt["family_id"], {}).setdefault(attempt["case_id"], {}).setdefault(attempt["unit_id"], []).append(1.0 if attempt["success"] else 0.0)
        d6 = facts.get("d6")
        if d6 is not None:
            d6 = _mapping(d6, "facts.d6")
            _require_exact(d6, {"success", "bits", "time"}, "facts.d6")
            if type(d6["success"]) is not bool:
                raise ReceiptAdapterError("D6 success must be boolean")
            # Keep the base-instance identity alongside the measured values.
            # The paper-ready D6 schema uses this identity to select the
            # corresponding independent calibration median; the legacy global
            # fixture strips it again below.
            d6_runs.append({"instance_id": row["identity"]["base_instance_id"], "success": d6["success"], "bits": _finite(d6["bits"], "D6.bits", minimum=0.0), "time": _finite(d6["time"], "D6.time", minimum=0.0)})
        oracle_summaries.append(_oracle_summary(row))

    if any(not condition_success[key] for key in _D2_CONDITIONS):
        missing = [key for key in _D2_CONDITIONS if not condition_success[key]]
        raise ReceiptAdapterError(f"D2 fixed directory missing conditions: {missing}")
    if any(not d3_values[key] for key in _D3_RELATIONS):
        missing = [key for key in _D3_RELATIONS if not d3_values[key]]
        raise ReceiptAdapterError(f"D3 relation support missing: {missing}")
    if not d6_runs:
        raise ReceiptAdapterError("D6 requires at least one measured run")
    if calibration is None:
        raise ReceiptAdapterError("D6 requires an independent calibration reference")
    calibration = _mapping(calibration, "calibration")
    _digest(calibration.get("calibration_digest"), "calibration.calibration_digest")
    per_instance_calibration = "calibration" in calibration
    calibration_rows: list[dict[str, Any]] = []
    if per_instance_calibration:
        _require_exact(calibration, {"calibration", "calibration_digest"}, "calibration")
        rows_value = calibration["calibration"]
        if not isinstance(rows_value, list) or not rows_value:
            raise ReceiptAdapterError("calibration.calibration must be a non-empty instance table")
        seen_instances: set[str] = set()
        for index, item in enumerate(rows_value):
            item = _mapping(item, f"calibration.calibration[{index}]")
            _require_exact(item, {"instance_id", "reference_bits", "reference_time"}, f"calibration.calibration[{index}")
            instance_id = _text(item["instance_id"], f"calibration.calibration[{index}].instance_id")
            if instance_id in seen_instances:
                raise ReceiptAdapterError(f"duplicate D6 calibration instance_id: {instance_id}")
            seen_instances.add(instance_id)
            reference_bits = _finite(item["reference_bits"], f"calibration.calibration[{index}].reference_bits", minimum=0.0)
            reference_time = _finite(item["reference_time"], f"calibration.calibration[{index}].reference_time", minimum=0.0)
            if reference_bits == 0 or reference_time == 0:
                raise ReceiptAdapterError("D6 per-instance calibration references must be positive")
            calibration_rows.append({"instance_id": instance_id, "reference_bits": reference_bits, "reference_time": reference_time})
        measured_instances = {str(run["instance_id"]) for run in d6_runs}
        calibration_instances = {row["instance_id"] for row in calibration_rows}
        if measured_instances != calibration_instances:
            missing = sorted(calibration_instances - measured_instances)
            unknown = sorted(measured_instances - calibration_instances)
            raise ReceiptAdapterError(f"D6 runs do not match per-instance calibration (missing={missing}, unknown={unknown})")
        reference_bits = reference_time = None
    else:
        _require_exact(calibration, {"reference_bits", "reference_time", "calibration_digest"}, "calibration")
        reference_bits = _finite(calibration["reference_bits"], "calibration.reference_bits", minimum=0.0)
        reference_time = _finite(calibration["reference_time"], "calibration.reference_time", minimum=0.0)
        if reference_bits == 0 or reference_time == 0:
            raise ReceiptAdapterError("D6 calibration reference must be positive")

    anchor_ids = []
    for item in catalog["anchors"]:
        anchor_ids.append(item if isinstance(item, str) else item["anchor_id"])
    compact_catalog = {
        "schema": "heterocoord.measured-catalog.v1",
        "anchors": anchor_ids,
        "source_catalog_digest": canonical_digest(catalog),
    }
    compact_plan = {
        "schema": "heterocoord.measured-plan.v1",
        "seed": int(plan["seed"]),
        "repeat_ids": sorted({row["identity"]["repeat_id"] for row in rows}),
        "formal_qualification": False,
    }
    base_parameters = {
        "alpha": float(parameters.get("alpha", 0.05)),
        "seed": int(parameters.get("seed", plan["seed"])),
        "resamples": int(parameters.get("resamples", 2000)),
        "d5_min_cases": int(parameters.get("d5_min_cases", 30)),
    }
    if not per_instance_calibration:
        base_parameters.update({"d6_reference_bits": reference_bits, "d6_reference_time": reference_time})
    dimensions = {
        "D1": {
            "validity": _mean(d1_rows, "validity"),
            "organization": _mean(d1_rows, "organization"),
            "infeasible": _mean(d1_rows, "infeasible"),
            "complete_plan": all(row["complete_plan"] for row in d1_rows),
        },
        "D2": {"nominal": sum(condition_success["C0"]) / len(condition_success["C0"]), "restricted": [sum(condition_success[key]) / len(condition_success[key]) for key in ("C1", "C2", "C3")]},
        "D3": {key.lower(): sum(d3_values[key]) / len(d3_values[key]) for key in _D3_RELATIONS},
        "D4": {"expected_count": len(d4_items), "items": d4_items},
        "D5": {"min_cases_per_family": base_parameters["d5_min_cases"], "families": d5_families},
        "D6": (
            {"expected_count": len(d6_runs), "calibration": calibration_rows, "runs": d6_runs}
            if per_instance_calibration
            else {"expected_count": len(d6_runs), "reference_bits": reference_bits, "reference_time": reference_time,
                  "runs": [{key: value for key, value in run.items() if key != "instance_id"} for run in d6_runs]}
        ),
    }
    payload: dict[str, Any] = {
        "schema": PIPELINE_SCHEMA,
        "pipeline_version": PIPELINE_VERSION,
        "scope": scope,
        "paper_commit": PAPER_COMMIT,
        "scorebook_digest": default_scorebook_v3().digest(),
        "catalog": compact_catalog,
        "plan": compact_plan,
        "parameters": base_parameters,
        "dimensions": dimensions,
        "receipt_digests": [hashlib.sha256(json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest() for row in rows],
        "oracle_summaries": oracle_summaries,
        "calibration_digest": calibration["calibration_digest"],
        "adapter_version": ADAPTER_VERSION,
    }
    if case is not None:
        payload["case_manifest_digest"] = case["manifest_digest"]
        payload["scene_digest"] = case["scene"]["scene_digest"]
        payload["semantic_map_digest"] = case["scene"]["semantic_map_digest"]
        payload["topology_digest"] = case["scene"]["topology_digest"]
        payload["roster_digest"] = case["robot_pool"]["roster_digest"]
        payload["contract_digest"] = case["contract"]["contract_digest"]
        payload["oracle_id"] = case["contract"]["oracle_id"]
        payload["evaluator_id"] = case["evaluator"]["evaluator_id"]
    if artifact_manifest_digest is not None:
        payload["artifact_manifest_digest"] = artifact_manifest_digest
    payload["catalog_digest"] = canonical_digest(payload["catalog"])
    payload["plan_digest"] = canonical_digest(payload["plan"])
    input_identity = {
        "catalog": payload["catalog"],
        "plan": payload["plan"],
        "parameters": payload["parameters"],
        "dimensions": payload["dimensions"],
        "pipeline_version": PIPELINE_VERSION,
    }
    for key in ("case_manifest_digest", "scene_digest", "semantic_map_digest", "topology_digest", "roster_digest", "contract_digest", "artifact_manifest_digest", "oracle_id", "evaluator_id"):
        if key in payload:
            input_identity[key] = payload[key]
    payload["input_digest"] = canonical_digest(input_identity)
    compute_results(payload)
    return payload


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReceiptAdapterError(f"cannot read JSON: {path}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("receipts", type=Path, help="JSON array of raw evaluator receipts")
    parser.add_argument("--calibration", required=True, type=Path, help="independent calibration JSON")
    parser.add_argument("--case-manifest", type=Path, help="bound real-case manifest JSON")
    parser.add_argument("--artifact-manifest", type=Path, help="typed evidence artifact manifest JSON")
    parser.add_argument("--artifact-root", type=Path, help="root directory for evidence artifact bytes")
    parser.add_argument("--require-artifact-evidence", action="store_true", help="fail closed unless evidence bytes are verified")
    parser.add_argument("--output", required=True, type=Path, help="normalized package JSON")
    args = parser.parse_args(argv)
    try:
        manifest = _load_json(args.case_manifest) if args.case_manifest else None
        artifact_manifest = _load_json(args.artifact_manifest) if args.artifact_manifest else None
        payload = normalize_receipts(
            _load_json(args.receipts), calibration=_load_json(args.calibration),
            case_manifest=manifest, artifact_manifest=artifact_manifest,
            artifact_root=args.artifact_root,
            require_artifact_evidence=args.require_artifact_evidence,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (OSError, ReceiptAdapterError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    return 0


__all__ = ["ADAPTER_VERSION", "RAW_RECEIPT_SCHEMA", "ReceiptAdapterError", "normalize_receipts"]


if __name__ == "__main__":
    raise SystemExit(main())
