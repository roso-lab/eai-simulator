"""Case-level receipt readiness without pretending to be a paper package.

One real anchor case can be semantically complete while still being unable to
fill the paper-wide fixed directory.  This module validates that distinction:
it verifies a bound case and its evaluator receipts all the way through the
oracle/obligation seam, then reports the remaining paper-support gaps without
manufacturing D1--D6 dimensions or opening a qualification gate.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .case_manifest_v1 import CaseManifestError, validate_case_manifest
from .records_v3 import canonical_digest
from .receipt_adapter_v3 import (
    ReceiptAdapterError,
    _obligation_rows,
    _validate_case_observation_bindings,
    _validate_oracle_obligation_consistency,
    _validate_oracle_success_consistency,
    _validate_receipt,
    _verify_external_evidence,
)
from .d4_first_event_v1 import (
    D4FirstEventError,
    reduce_receipts as reduce_d4_first_events,
    registration_condition_ids,
)


READINESS_SCHEMA = "heterocoord.case-receipt-readiness.v1"
PAPER_CONDITIONS = ("C0", "C1", "C2", "C3")
PAPER_RELATIONS = ("P2", "P3J", "P3S", "P4")


class CaseReadinessError(ValueError):
    """Raised when a case cannot be audited at the receipt boundary."""


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CaseReadinessError(f"{name} must be an object")
    return value


def _receipt_identity_matches_case(receipt: Mapping[str, Any], case: Mapping[str, Any], index: int) -> None:
    identity = receipt["identity"]
    expected = {
        "task_id": case["task_id"],
        "anchor_id": case["anchor_id"],
        "domain": case["domain"],
        "relation_signature": case["relation_signature"],
        "base_instance_id": case["base_instance_id"],
    }
    for key, value in expected.items():
        if identity[key] != value:
            raise CaseReadinessError(f"receipt[{index}] identity.{key} does not match case manifest")


def audit_case_receipts(
    receipts: Sequence[Mapping[str, Any]],
    *,
    case_manifest: Mapping[str, Any],
    artifact_manifest: Mapping[str, Mapping[str, Any]],
    artifact_root: str | Path,
    required_paper_conditions: Sequence[str] = PAPER_CONDITIONS,
    required_paper_relations: Sequence[str] = PAPER_RELATIONS,
) -> dict[str, Any]:
    """Audit one bound case and report case-vs-paper support separately.

    The function is intentionally a validator/report, not a normalizer.  It
    performs the same digest, entity-binding, success-condition and frozen
    obligation checks used by production normalization, but it does not
    require a case to impersonate unrelated paper relation classes.
    """

    try:
        case = validate_case_manifest(_mapping(case_manifest, "case_manifest"))
    except (CaseManifestError, TypeError, ValueError) as exc:
        raise CaseReadinessError(f"invalid case manifest: {exc}") from exc
    if not isinstance(receipts, Sequence) or isinstance(receipts, (str, bytes)) or not receipts:
        raise CaseReadinessError("receipts must be a non-empty sequence")
    rows = []
    try:
        rows = [_validate_receipt(_mapping(item, f"receipts[{index}]")) for index, item in enumerate(receipts)]
        _verify_external_evidence(
            rows,
            artifact_manifest=artifact_manifest,
            artifact_root=artifact_root,
            require_receipt_binding=True,
        )
    except (ReceiptAdapterError, TypeError, ValueError, KeyError) as exc:
        raise CaseReadinessError(f"receipt evidence validation failed: {exc}") from exc

    if len({row["receipt_id"] for row in rows}) != len(rows):
        raise CaseReadinessError("receipt_id values must be unique")
    catalog = rows[0]["catalog"]
    plan = rows[0]["plan"]
    parameters = rows[0]["parameters"]
    for index, row in enumerate(rows):
        _receipt_identity_matches_case(row, case, index)
        if row["catalog"] != catalog or row["plan"] != plan or row["parameters"] != parameters:
            raise CaseReadinessError("all case receipts must share catalog, plan, and parameters")
        if row["evaluator"]["evaluator_version"] != case["evaluator"]["evaluator_version"]:
            raise CaseReadinessError(f"receipt[{index}] evaluator version does not match case manifest")
        if row["evaluator"]["status"] in {"infrastructure_invalid", "crashed"}:
            raise CaseReadinessError(f"receipt[{index}] is infrastructure-invalid")
        if row["evaluator"]["status"] not in {
            "valid_terminal", "safe_stop", "team_state_infeasible"
        }:
            raise CaseReadinessError(
                f"receipt[{index}] method outcome requires a registered paired reducer"
            )
        # The case manifest binds catalog/plan bytes, not merely their IDs.
        if canonical_digest(row["catalog"]) != case["catalog_digest"]:
            raise CaseReadinessError(f"receipt[{index}] catalog digest does not match case manifest")
        if canonical_digest(row["plan"]) != case["plan_digest"]:
            raise CaseReadinessError(f"receipt[{index}] plan digest does not match case manifest")

    try:
        _validate_case_observation_bindings(rows, case)
        for row in rows:
            if row["identity"]["condition_id"] in PAPER_CONDITIONS:
                _validate_oracle_success_consistency(row)
                _validate_oracle_obligation_consistency(row)
    except (ReceiptAdapterError, TypeError, ValueError, KeyError) as exc:
        raise CaseReadinessError(f"case oracle receipt validation failed: {exc}") from exc

    try:
        d4_conditions = registration_condition_ids(plan)
        d4_first_event = reduce_d4_first_events(rows)
    except D4FirstEventError as exc:
        raise CaseReadinessError(f"D4 first-event evidence is invalid: {exc}") from exc

    allowed_conditions = set(PAPER_CONDITIONS) | set(d4_conditions)
    unknown_conditions = sorted({row["identity"]["condition_id"] for row in rows} - allowed_conditions)
    if unknown_conditions:
        raise CaseReadinessError(f"unregistered receipt conditions: {unknown_conditions}")

    conditions = sorted({row["identity"]["condition_id"] for row in rows})
    repeats = sorted({row["identity"]["repeat_id"] for row in rows})
    branches = sorted({row["identity"]["branch"] for row in rows})
    primary_rows = [row for row in rows if row["identity"]["condition_id"] in PAPER_CONDITIONS]
    relations = sorted({item["key"] for row in primary_rows for item in _obligation_rows(row)})
    d4_count = sum(len(row["facts"].get("d4_items", [])) for row in primary_rows)
    d5_count = sum(1 for row in primary_rows if row["facts"].get("d5_attempt") is not None)
    d6_count = sum(1 for row in primary_rows if row["facts"].get("d6") is not None)
    required_conditions = list(dict.fromkeys(required_paper_conditions))
    required_relations = list(dict.fromkeys(required_paper_relations))
    missing_conditions = [item for item in required_conditions if item not in conditions]
    missing_relations = [item for item in required_relations if item not in relations]
    paper_gaps = []
    if missing_conditions:
        paper_gaps.append({"code": "PAPER_D2_CONDITION_SUPPORT_MISSING", "values": missing_conditions})
    if missing_relations:
        paper_gaps.append({"code": "PAPER_D3_RELATION_SUPPORT_MISSING", "values": missing_relations})
    if d4_count == 0:
        paper_gaps.append({"code": "PAPER_D4_CLOSURE_SUPPORT_MISSING"})
    if d4_first_event is None:
        paper_gaps.append({"code": "PAPER_D4_FIRST_EVENT_DIAGNOSTICS_MISSING"})
    if d5_count == 0:
        paper_gaps.append({"code": "PAPER_D5_ATTEMPT_SUPPORT_MISSING"})
    if d6_count == 0:
        paper_gaps.append({"code": "PAPER_D6_RUN_SUPPORT_MISSING"})

    # Raw receipts can show that the necessary measurement surfaces exist,
    # but they cannot establish the paper estimand by themselves.  The fixed
    # base-instance/repeat/cell panel and its projection digest are a separate
    # artifact boundary.  Keep that distinction explicit so even a caller
    # supplying all condition/relation labels cannot turn this case audit into
    # a paper-support qualification claim.
    receipt_support_surface_present = not paper_gaps
    paper_gaps.append({"code": "PAPER_FIXED_PANEL_PACKAGE_MISSING"})

    return {
        "schema": READINESS_SCHEMA,
        "case_id": case["case_id"],
        "task_id": case["task_id"],
        "anchor_id": case["anchor_id"],
        "case_receipt_ready": True,
        "receipt_support_surface_present": receipt_support_surface_present,
        "paper_support_ready": False,
        "formal_gates": {
            "rankable": False,
            "paper_score_eligible": False,
            "physical_qualified": False,
        },
        "receipt_count": len(rows),
        "conditions": conditions,
        "repeats": repeats,
        "branches": branches,
        "case_relation_signature": list(case["relation_signature"]),
        "observed_obligation_relations": relations,
        "d4_item_count": d4_count,
        "d4_first_event_registered": d4_first_event is not None,
        "d4_first_event_eligible_trajectory_count": (
            sum(
                cell["eligible_trajectory_count"]
                for panel in d4_first_event["panels"]
                for cell in panel["cells"]
            )
            if d4_first_event is not None else 0
        ),
        "d4_first_event_excluded_trajectory_count": (
            sum(
                cell["excluded_trajectory_count"]
                for panel in d4_first_event["panels"]
                for cell in panel["cells"]
            )
            if d4_first_event is not None else 0
        ),
        "d5_attempt_count": d5_count,
        "d6_run_count": d6_count,
        "paper_gaps": paper_gaps,
        "artifact_manifest_digest": _artifact_digest(artifact_manifest),
        "case_manifest_digest": case["manifest_digest"],
    }


def _artifact_digest(value: Mapping[str, Any]) -> str:
    return canonical_digest(value)


__all__ = [
    "READINESS_SCHEMA",
    "PAPER_CONDITIONS",
    "PAPER_RELATIONS",
    "CaseReadinessError",
    "audit_case_receipts",
]
