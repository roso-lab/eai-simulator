"""Replayable single-case results without pretending a full paper panel exists.

This module is the deliberately narrow bridge between a sealed real case and
the fixed-matrix production pipeline.  It validates the same evaluator-owned
receipt, case-manifest, artifact and oracle boundaries as the strict adapter,
but retains only support that is actually present.  Missing C1--C3 conditions,
P3/P4 relations, adaptation families, or calibration rows remain explicit
``incomplete``/``na`` states.  No missing weight is redistributed, no composite
is computed, and no paper or physical qualification gate can be opened.

The ordinary :mod:`benchmark.receipt_adapter_v3` remains the only route to a
complete D1--D6 paper input.  This projection exists so the first real case can
be inspected, replayed and scientifically debugged before the registered
matrix is complete.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from .case_manifest_v1 import CaseManifestError, validate_case_manifest
from .metrics_v3 import d1_score, d2_score, d3_score, d4_score, d5_score, d6_score
from .records_v3 import canonical_digest
from .receipt_adapter_v3 import (
    ADAPTER_VERSION,
    ReceiptAdapterError,
    _finite,
    _mapping,
    _obligation_rows,
    _oracle_summary,
    _require_exact,
    _validate_case_observation_bindings,
    _validate_oracle_obligation_consistency,
    _validate_oracle_success_consistency,
    _validate_receipt,
    _verify_external_evidence,
)
from .scorebook_v3 import PAPER_COMMIT, default_scorebook_v3


PARTIAL_INPUT_SCHEMA = "heterocoord.case-partial-input.v1"
PARTIAL_REPORT_SCHEMA = "heterocoord.case-partial-report.v1"
PARTIAL_OUTPUT_MANIFEST_SCHEMA = "heterocoord.case-partial-output-manifest.v1"
PARTIAL_VERSION = "case-partial-results-v1"
FORMAL_GATES = {
    "rankable": False,
    "paper_score_eligible": False,
    "physical_qualified": False,
}
DIMENSIONS = ("D1", "D2", "D3", "D4", "D5", "D6")
CONDITIONS = ("C0", "C1", "C2", "C3")
RELATIONS = ("P2", "P3J", "P3S", "P4")


class CasePartialResultsError(ValueError):
    """Raised when a partial case cannot be validated or replayed."""


def _digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
        char not in "0123456789abcdef" for char in value
    ):
        raise CasePartialResultsError(f"{name} must be a lowercase SHA-256")
    return value


def _canonical_receipt_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _input_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "input_digest"}


def _validate_calibration(
    calibration: Mapping[str, Any] | None,
    *,
    measured_instances: set[str],
) -> dict[str, Any] | None:
    """Validate an optional per-instance D6 table without inventing a scale."""

    if calibration is None:
        return None
    if not isinstance(calibration, Mapping):
        raise CasePartialResultsError("calibration must be an object")
    if set(calibration) != {"calibration", "calibration_digest"}:
        raise CasePartialResultsError(
            "partial case calibration must contain calibration and calibration_digest"
        )
    _digest(calibration["calibration_digest"], "calibration.calibration_digest")
    rows = calibration["calibration"]
    if not isinstance(rows, list) or not rows:
        raise CasePartialResultsError("calibration.calibration must be a non-empty list")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows):
        if not isinstance(raw, Mapping) or set(raw) != {
            "instance_id",
            "reference_bits",
            "reference_time",
        }:
            raise CasePartialResultsError(f"calibration row {index} schema mismatch")
        instance_id = raw["instance_id"]
        if not isinstance(instance_id, str) or not instance_id.strip():
            raise CasePartialResultsError(f"calibration row {index} instance_id is invalid")
        if instance_id in seen:
            raise CasePartialResultsError(f"duplicate calibration instance: {instance_id}")
        seen.add(instance_id)
        bits = _finite(
            raw["reference_bits"],
            f"calibration[{index}].reference_bits",
            minimum=0.0,
        )
        time = _finite(
            raw["reference_time"],
            f"calibration[{index}].reference_time",
            minimum=0.0,
        )
        if bits == 0.0 or time == 0.0:
            raise CasePartialResultsError(
                "per-instance calibration references must be positive"
            )
        normalized.append(
            {
                "instance_id": instance_id,
                "reference_bits": bits,
                "reference_time": time,
            }
        )
    if seen != measured_instances:
        raise CasePartialResultsError(
            "calibration instance set must exactly match measured instances "
            f"(missing={sorted(measured_instances - seen)}, "
            f"unknown={sorted(seen - measured_instances)})"
        )
    return {
        "calibration": normalized,
        "calibration_digest": calibration["calibration_digest"],
    }


def normalize_partial_case_receipts(
    receipts: Sequence[Mapping[str, Any]],
    *,
    case_manifest: Mapping[str, Any],
    artifact_manifest: Mapping[str, Mapping[str, Any]],
    artifact_root: str | Path,
    calibration: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate a sealed case and retain only its observed support.

    Unlike ``normalize_receipts``, this function does not require absent panel
    cells.  It is stricter in another respect: a case manifest and externally
    verified evidence are always mandatory.
    """

    if not isinstance(receipts, Sequence) or isinstance(receipts, (str, bytes)) or not receipts:
        raise CasePartialResultsError("receipts must be a non-empty sequence")
    try:
        case = validate_case_manifest(case_manifest)
        rows = [
            _validate_receipt(_mapping(item, f"receipts[{index}]"))
            for index, item in enumerate(receipts)
        ]
        artifact_manifest_digest = _verify_external_evidence(
            rows,
            artifact_manifest=artifact_manifest,
            artifact_root=artifact_root,
            require_receipt_binding=True,
        )
    except (CaseManifestError, ReceiptAdapterError, OSError, TypeError, ValueError) as exc:
        raise CasePartialResultsError(str(exc)) from exc

    if len({row["receipt_id"] for row in rows}) != len(rows):
        raise CasePartialResultsError("receipt_id values must be unique")
    catalog = rows[0]["catalog"]
    plan = rows[0]["plan"]
    parameters = rows[0]["parameters"]
    for row in rows[1:]:
        if row["catalog"] != catalog or row["plan"] != plan or row["parameters"] != parameters:
            raise CasePartialResultsError(
                "all receipts must share catalog, plan, and parameters"
            )

    expected_identity = {
        "task_id": case["task_id"],
        "anchor_id": case["anchor_id"],
        "domain": case["domain"],
        "relation_signature": case["relation_signature"],
        "base_instance_id": case["base_instance_id"],
    }
    try:
        for index, row in enumerate(rows):
            for key, expected in expected_identity.items():
                if row["identity"][key] != expected:
                    raise CasePartialResultsError(
                        f"receipt[{index}] identity.{key} does not match case manifest"
                    )
            if canonical_digest(row["catalog"]) != case["catalog_digest"]:
                raise CasePartialResultsError(
                    f"receipt[{index}] catalog digest does not match case manifest"
                )
            if canonical_digest(row["plan"]) != case["plan_digest"]:
                raise CasePartialResultsError(
                    f"receipt[{index}] plan digest does not match case manifest"
                )
            if (
                row["evaluator"]["evaluator_version"]
                != case["evaluator"]["evaluator_version"]
            ):
                raise CasePartialResultsError(
                    f"receipt[{index}] evaluator version does not match case manifest"
                )
        _validate_case_observation_bindings(rows, case)
        for row in rows:
            _validate_oracle_success_consistency(row)
            _validate_oracle_obligation_consistency(row)
    except ReceiptAdapterError as exc:
        raise CasePartialResultsError(str(exc)) from exc

    invalid = [
        row["receipt_id"]
        for row in rows
        if row["evaluator"]["status"]
        not in {"valid_terminal", "safe_stop", "team_state_infeasible"}
    ]
    if invalid:
        raise CasePartialResultsError(
            "partial export accepts terminal scientific outcomes only; "
            f"paired method reduction is required for {invalid}"
        )

    d1_rows: list[dict[str, Any]] = []
    condition_values: dict[str, list[float]] = {key: [] for key in CONDITIONS}
    relation_values: dict[str, list[float]] = {key: [] for key in RELATIONS}
    d4_items: list[dict[str, Any]] = []
    d5_families: dict[str, dict[str, dict[str, list[float]]]] = {}
    d6_runs: list[dict[str, Any]] = []
    oracle_summaries: list[dict[str, Any]] = []

    try:
        for row in rows:
            facts = row["facts"]
            d1 = _mapping(facts.get("d1"), "facts.d1")
            _require_exact(
                d1,
                {"validity", "organization", "infeasible", "complete_plan"},
                "facts.d1",
            )
            if type(d1["complete_plan"]) is not bool:
                raise ReceiptAdapterError("facts.d1.complete_plan must be boolean")
            d1_rows.append(
                {
                    "validity": _finite(d1["validity"], "D1.validity", minimum=0.0, maximum=1.0),
                    "organization": _finite(d1["organization"], "D1.organization", minimum=0.0, maximum=1.0),
                    "infeasible": _finite(d1["infeasible"], "D1.infeasible", minimum=0.0, maximum=1.0),
                    "complete_plan": d1["complete_plan"],
                }
            )
            condition = row["identity"]["condition_id"]
            if condition not in condition_values:
                raise ReceiptAdapterError(f"unsupported D2 condition: {condition}")
            if type(facts.get("success")) is not bool:
                raise ReceiptAdapterError("facts.success must be an evaluator-owned boolean")
            condition_values[condition].append(1.0 if facts["success"] else 0.0)
            for obligation in _obligation_rows(row):
                relation_values[obligation["key"]].append(obligation["value"])
            raw_d4 = facts.get("d4_items", [])
            if not isinstance(raw_d4, list):
                raise ReceiptAdapterError("facts.d4_items must be a list")
            for item in raw_d4:
                item = dict(_mapping(item, "facts.d4_items item"))
                if item.get("kind") not in {
                    "deadline",
                    "time_valued",
                    "continuous_supply",
                }:
                    raise ReceiptAdapterError("unsupported D4 item kind")
                item["e"] = _finite(item.get("e"), "D4.e", minimum=0.0, maximum=1.0)
                if item["kind"] != "deadline":
                    item["phi"] = _finite(
                        item.get("phi"), "D4.phi", minimum=0.0, maximum=1.0
                    )
                d4_items.append(item)
            attempt = facts.get("d5_attempt")
            if attempt is not None:
                attempt = _mapping(attempt, "facts.d5_attempt")
                _require_exact(
                    attempt,
                    {"family_id", "case_id", "unit_id", "success"},
                    "facts.d5_attempt",
                )
                if type(attempt["success"]) is not bool:
                    raise ReceiptAdapterError("D5 success must be boolean")
                d5_families.setdefault(str(attempt["family_id"]), {}).setdefault(
                    str(attempt["case_id"]), {}
                ).setdefault(str(attempt["unit_id"]), []).append(
                    1.0 if attempt["success"] else 0.0
                )
            d6 = _mapping(facts.get("d6"), "facts.d6")
            _require_exact(d6, {"success", "bits", "time"}, "facts.d6")
            if type(d6["success"]) is not bool:
                raise ReceiptAdapterError("D6 success must be boolean")
            d6_runs.append(
                {
                    "instance_id": row["identity"]["base_instance_id"],
                    "success": d6["success"],
                    "bits": _finite(d6["bits"], "D6.bits", minimum=0.0),
                    "time": _finite(d6["time"], "D6.time", minimum=0.0),
                }
            )
            oracle_summaries.append(_oracle_summary(row))
    except ReceiptAdapterError as exc:
        raise CasePartialResultsError(str(exc)) from exc

    normalized_calibration = _validate_calibration(
        calibration,
        measured_instances={row["instance_id"] for row in d6_runs},
    )
    mean_d1 = {
        key: sum(float(row[key]) for row in d1_rows) / len(d1_rows)
        for key in ("validity", "organization", "infeasible")
    }
    mean_d1["complete_plan"] = all(row["complete_plan"] for row in d1_rows)
    condition_rates = {
        key: sum(values) / len(values)
        for key, values in condition_values.items()
        if values
    }
    relation_rates = {
        key: sum(values) / len(values)
        for key, values in relation_values.items()
        if values
    }
    payload: dict[str, Any] = {
        "schema": PARTIAL_INPUT_SCHEMA,
        "version": PARTIAL_VERSION,
        "scope": "measured_unqualified",
        "paper_commit": PAPER_COMMIT,
        "scorebook_digest": default_scorebook_v3().digest(),
        "formal_gates": dict(FORMAL_GATES),
        "case": {
            "case_id": case["case_id"],
            "task_id": case["task_id"],
            "anchor_id": case["anchor_id"],
            "base_instance_id": case["base_instance_id"],
            "manifest_digest": case["manifest_digest"],
        },
        "provenance": {
            "catalog_digest": canonical_digest(catalog),
            "plan_digest": canonical_digest(plan),
            "scene_digest": case["scene"]["scene_digest"],
            "semantic_map_digest": case["scene"]["semantic_map_digest"],
            "topology_digest": case["scene"]["topology_digest"],
            "roster_digest": case["robot_pool"]["roster_digest"],
            "contract_digest": case["contract"]["contract_digest"],
            "oracle_id": case["contract"]["oracle_id"],
            "evaluator_id": case["evaluator"]["evaluator_id"],
            "artifact_manifest_digest": artifact_manifest_digest,
            "receipt_digests": [_canonical_receipt_digest(row) for row in rows],
            "receipt_adapter_version": ADAPTER_VERSION,
        },
        "parameters": {
            "alpha": float(parameters.get("alpha", 0.05)),
            "seed": int(parameters.get("seed", plan["seed"])),
            "resamples": int(parameters.get("resamples", 0)),
            "d5_min_cases": int(parameters.get("d5_min_cases", 30)),
        },
        "observed": {
            "D1": mean_d1,
            "D2": {
                "condition_rates": condition_rates,
                "condition_counts": {
                    key: len(values)
                    for key, values in condition_values.items()
                    if values
                },
            },
            "D3": {
                "relation_rates": relation_rates,
                "relation_counts": {
                    key: len(values)
                    for key, values in relation_values.items()
                    if values
                },
            },
            "D4": {"items": d4_items},
            "D5": {"families": d5_families},
            "D6": {
                "runs": d6_runs,
                "calibration": normalized_calibration,
            },
        },
        "coverage": {
            "receipt_count": len(rows),
            "conditions_present": sorted(condition_rates),
            "conditions_missing": [key for key in CONDITIONS if key not in condition_rates],
            "relations_present": [key for key in RELATIONS if key in relation_rates],
            "relations_missing": [key for key in RELATIONS if key not in relation_rates],
            "d4_item_count": len(d4_items),
            "d5_family_count": len(d5_families),
            "d6_run_count": len(d6_runs),
            "d6_calibrated": normalized_calibration is not None,
        },
        "oracle_summaries": oracle_summaries,
    }
    payload["input_digest"] = canonical_digest(_input_identity(payload))
    _validate_partial_input(payload)
    return payload


def _validate_partial_input(payload: Mapping[str, Any]) -> None:
    required = {
        "schema",
        "version",
        "scope",
        "paper_commit",
        "scorebook_digest",
        "formal_gates",
        "case",
        "provenance",
        "parameters",
        "observed",
        "coverage",
        "oracle_summaries",
        "input_digest",
    }
    if not isinstance(payload, Mapping) or set(payload) != required:
        raise CasePartialResultsError("partial input schema mismatch")
    if payload["schema"] != PARTIAL_INPUT_SCHEMA or payload["version"] != PARTIAL_VERSION:
        raise CasePartialResultsError("unsupported partial input version")
    if payload["scope"] != "measured_unqualified":
        raise CasePartialResultsError("partial input scope must be measured_unqualified")
    if payload["paper_commit"] != PAPER_COMMIT:
        raise CasePartialResultsError("partial input binds a different paper version")
    if payload["scorebook_digest"] != default_scorebook_v3().digest():
        raise CasePartialResultsError("partial input binds a different scorebook")
    if payload["formal_gates"] != FORMAL_GATES:
        raise CasePartialResultsError("partial input formal gates must remain closed")
    if payload["input_digest"] != canonical_digest(_input_identity(payload)):
        raise CasePartialResultsError("partial input digest mismatch")
    if not isinstance(payload["observed"], Mapping) or set(payload["observed"]) != set(DIMENSIONS):
        raise CasePartialResultsError("partial input must declare D1 through D6 observations")
    if not isinstance(payload["coverage"], Mapping):
        raise CasePartialResultsError("partial input coverage must be an object")
    provenance = payload["provenance"]
    if not isinstance(provenance, Mapping):
        raise CasePartialResultsError("partial input provenance must be an object")
    for key in (
        "catalog_digest",
        "plan_digest",
        "scene_digest",
        "semantic_map_digest",
        "topology_digest",
        "roster_digest",
        "contract_digest",
        "artifact_manifest_digest",
    ):
        _digest(provenance.get(key), f"provenance.{key}")


def _partial_dimension(
    score_id: str,
    *,
    status: str,
    reason: str,
    case_local_point: float | None,
    components: Mapping[str, Any],
    support: Mapping[str, Any],
) -> dict[str, Any]:
    if status not in {"incomplete", "na"}:
        raise CasePartialResultsError("partial dimension status must be incomplete or na")
    if case_local_point is not None and (
        not math.isfinite(float(case_local_point)) or not 0.0 <= float(case_local_point) <= 100.0
    ):
        raise CasePartialResultsError(f"{score_id} case-local estimate is invalid")
    return {
        "score_id": score_id,
        "status": status,
        "paper_point": None,
        "case_local_point": None if case_local_point is None else float(case_local_point),
        "reason": reason,
        "components": dict(components),
        "support": dict(support),
    }


def compute_partial_results(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Compute case-local diagnostics while keeping every paper score absent."""

    _validate_partial_input(payload)
    observed = payload["observed"]
    coverage = payload["coverage"]

    d1 = d1_score(**observed["D1"])
    d1_row = _partial_dimension(
        "D1",
        status="incomplete",
        reason="single_case_not_registered_full_panel",
        case_local_point=d1.point,
        components=d1.components,
        support={"receipt_count": coverage["receipt_count"]},
    )

    condition_rates = observed["D2"]["condition_rates"]
    d2_local = None
    if set(condition_rates) == set(CONDITIONS):
        d2_local = d2_score(
            nominal=condition_rates["C0"],
            restricted=[condition_rates[key] for key in ("C1", "C2", "C3")],
        )
    d2_row = _partial_dimension(
        "D2",
        status="na" if "C0" not in condition_rates else "incomplete",
        reason=(
            "nominal_condition_absent"
            if "C0" not in condition_rates
            else "fixed_condition_directory_incomplete"
            if d2_local is None
            else "single_case_not_registered_full_panel"
        ),
        case_local_point=None if d2_local is None else d2_local.point,
        components={
            "condition_rates": dict(condition_rates),
            "conditions_missing": list(coverage["conditions_missing"]),
        },
        support={"condition_counts": dict(observed["D2"]["condition_counts"])},
    )

    relation_rates = observed["D3"]["relation_rates"]
    d3_local = None
    if set(relation_rates) == set(RELATIONS):
        d3_local = d3_score(
            p2=relation_rates["P2"],
            p3j=relation_rates["P3J"],
            p3s=relation_rates["P3S"],
            p4=relation_rates["P4"],
        )
    d3_row = _partial_dimension(
        "D3",
        status="na" if not relation_rates else "incomplete",
        reason=(
            "no_scored_obligation_relation"
            if not relation_rates
            else "fixed_relation_directory_incomplete"
            if d3_local is None
            else "single_case_not_registered_full_panel"
        ),
        case_local_point=None if d3_local is None else d3_local.point,
        components={
            "relation_rates": dict(relation_rates),
            "relations_missing": list(coverage["relations_missing"]),
        },
        support={"relation_counts": dict(observed["D3"]["relation_counts"])},
    )

    d4_items = observed["D4"]["items"]
    d4_local = d4_score(d4_items) if d4_items else None
    d4_row = _partial_dimension(
        "D4",
        status="na" if not d4_items else "incomplete",
        reason=(
            "empty_case_affected_work_closure"
            if not d4_items
            else "case_local_closure_not_registered_full_roster"
        ),
        case_local_point=None if d4_local is None else d4_local.point,
        components={} if d4_local is None else d4_local.components,
        support={"item_count": len(d4_items)},
    )

    d5_families = observed["D5"]["families"]
    d5_local = d5_score(
        d5_families,
        min_cases_per_family=payload["parameters"]["d5_min_cases"],
    )
    d5_row = _partial_dimension(
        "D5",
        status="na" if d5_local.status == "na" else "incomplete",
        reason=d5_local.reason or "single_case_not_registered_adaptation_panel",
        case_local_point=d5_local.point if d5_local.status == "valid" else None,
        components=d5_local.components,
        support={
            "family_count": len(d5_families),
            "excluded_families": list(d5_local.excluded_families),
        },
    )

    d6_spec = observed["D6"]
    d6_local_point = None
    d6_components: dict[str, Any] = {
        "run_count": len(d6_spec["runs"]),
        "runs": list(d6_spec["runs"]),
    }
    if d6_spec["calibration"] is not None:
        calibration_by_id = {
            row["instance_id"]: row
            for row in d6_spec["calibration"]["calibration"]
        }
        grouped = {key: [] for key in calibration_by_id}
        for run in d6_spec["runs"]:
            grouped[run["instance_id"]].append(run)
        scores = [
            d6_score(
                runs,
                reference_bits=calibration_by_id[instance_id]["reference_bits"],
                reference_time=calibration_by_id[instance_id]["reference_time"],
            )
            for instance_id, runs in grouped.items()
        ]
        if scores and all(score.status == "valid" for score in scores):
            d6_local_point = sum(float(score.point) for score in scores) / len(scores)
        d6_components["calibration_digest"] = d6_spec["calibration"]["calibration_digest"]
    d6_row = _partial_dimension(
        "D6",
        status="incomplete",
        reason=(
            "independent_instance_calibration_missing"
            if d6_spec["calibration"] is None
            else "single_case_not_registered_full_panel"
        ),
        case_local_point=d6_local_point,
        components=d6_components,
        support={"run_count": len(d6_spec["runs"]), "calibrated": d6_spec["calibration"] is not None},
    )

    report = {
        "schema": PARTIAL_REPORT_SCHEMA,
        "version": PARTIAL_VERSION,
        "scope": "measured_unqualified",
        "qualification": "measured_unqualified",
        "paper_support_complete": False,
        "formal_gates": dict(FORMAL_GATES),
        "provenance": {
            "paper_commit": payload["paper_commit"],
            "scorebook_digest": payload["scorebook_digest"],
            "input_digest": payload["input_digest"],
            **dict(payload["provenance"]),
        },
        "dimensions": [d1_row, d2_row, d3_row, d4_row, d5_row, d6_row],
        "composite": {
            "status": "not_computed",
            "point": None,
            "reason": "partial_case_export_forbids_composite",
        },
        "profile_range": {
            "status": "not_computed",
            "reason": "partial_case_export_forbids_profile_range",
        },
        "coverage": dict(coverage),
        "guardrails": {
            "missing_cells_reweighted": False,
            "paper_dimension_points_emitted": False,
            "composite_emitted": False,
            "formal_gates_opened": False,
        },
    }
    _validate_partial_report(report)
    return report


def _validate_partial_report(report: Mapping[str, Any]) -> None:
    if not isinstance(report, Mapping) or report.get("schema") != PARTIAL_REPORT_SCHEMA:
        raise CasePartialResultsError("partial report schema mismatch")
    if report.get("version") != PARTIAL_VERSION:
        raise CasePartialResultsError("partial report version mismatch")
    if report.get("scope") != "measured_unqualified" or report.get("qualification") != "measured_unqualified":
        raise CasePartialResultsError("partial report qualification mismatch")
    if report.get("paper_support_complete") is not False or report.get("formal_gates") != FORMAL_GATES:
        raise CasePartialResultsError("partial report must keep paper support and gates closed")
    dimensions = report.get("dimensions")
    if not isinstance(dimensions, list) or [row.get("score_id") for row in dimensions] != list(DIMENSIONS):
        raise CasePartialResultsError("partial report dimension order is invalid")
    if any(row.get("paper_point") is not None for row in dimensions):
        raise CasePartialResultsError("partial report cannot emit paper dimension points")
    composite = report.get("composite")
    if not isinstance(composite, Mapping) or composite.get("status") != "not_computed" or composite.get("point") is not None:
        raise CasePartialResultsError("partial report cannot emit a composite")
    guardrails = report.get("guardrails")
    if guardrails != {
        "missing_cells_reweighted": False,
        "paper_dimension_points_emitted": False,
        "composite_emitted": False,
        "formal_gates_opened": False,
    }:
        raise CasePartialResultsError("partial report guardrails are invalid")


def export_partial_results(
    report: Mapping[str, Any],
    destination: str | Path,
    *,
    input_payload: Mapping[str, Any],
) -> tuple[Path, ...]:
    """Write a deterministic partial package suitable for local replay."""

    _validate_partial_report(report)
    _validate_partial_input(input_payload)
    expected = compute_partial_results(input_payload)
    if expected != report:
        raise CasePartialResultsError("partial report does not match its input")
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise CasePartialResultsError("partial output destination must be empty")
    destination.mkdir(parents=True, exist_ok=True)
    report_path = destination / "report.json"
    input_path = destination / "partial_input.json"
    coverage_path = destination / "coverage_report.json"
    metrics_path = destination / "metrics.csv"
    markdown_path = destination / "metrics.md"
    provenance_path = destination / "provenance.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    input_path.write_text(json.dumps(input_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    coverage_path.write_text(json.dumps(report["coverage"], ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    provenance_path.write_text(json.dumps(report["provenance"], ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    rows = [
        {
            "metric_id": row["score_id"],
            "status": row["status"],
            "paper_point": row["paper_point"],
            "case_local_point": row["case_local_point"],
            "reason": row["reason"],
        }
        for row in report["dimensions"]
    ]
    with metrics_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    lines = [
        "# Single-case measured-unqualified results",
        "",
        "| metric | paper status | paper point | case-local estimate | reason |",
        "|---|---|---:|---:|---|",
    ]
    for row in rows:
        rendered = {key: "N/A" if value is None else value for key, value in row.items()}
        lines.append(
            "| {metric_id} | {status} | {paper_point} | {case_local_point} | {reason} |".format(**rendered)
        )
    lines.extend(
        [
            "",
            "Composite: **not computed**.",
            "",
            "Formal gates: `rankable=false`, `paper_score_eligible=false`, `physical_qualified=false`.",
        ]
    )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    outputs = [
        report_path,
        input_path,
        coverage_path,
        metrics_path,
        markdown_path,
        provenance_path,
    ]
    manifest = {
        "schema": PARTIAL_OUTPUT_MANIFEST_SCHEMA,
        "report_digest": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "files": {
            path.name: {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size_bytes": path.stat().st_size,
            }
            for path in outputs
        },
    }
    manifest_path = destination / "output_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    outputs.append(manifest_path)
    return tuple(outputs)


def verify_partial_export(destination: str | Path) -> dict[str, Any]:
    """Verify every exported byte and replay the pure calculation."""

    destination = Path(destination)
    manifest_path = destination / "output_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CasePartialResultsError("partial output manifest is unreadable") from exc
    if manifest.get("schema") != PARTIAL_OUTPUT_MANIFEST_SCHEMA or not isinstance(manifest.get("files"), Mapping):
        raise CasePartialResultsError("partial output manifest schema mismatch")
    required = {
        "report.json",
        "partial_input.json",
        "coverage_report.json",
        "metrics.csv",
        "metrics.md",
        "provenance.json",
    }
    if set(manifest["files"]) != required:
        raise CasePartialResultsError("partial output manifest file set mismatch")
    for name, expected in manifest["files"].items():
        if not isinstance(name, str) or not name or name in {".", ".."} or "/" in name or "\\" in name:
            raise CasePartialResultsError(f"unsafe partial output path: {name}")
        path = destination / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != expected.get("size_bytes"):
            raise CasePartialResultsError(f"partial output missing or size mismatch: {name}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected.get("sha256"):
            raise CasePartialResultsError(f"partial output digest mismatch: {name}")
    report_path = destination / "report.json"
    if hashlib.sha256(report_path.read_bytes()).hexdigest() != manifest.get("report_digest"):
        raise CasePartialResultsError("partial report digest mismatch")
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        payload = json.loads((destination / "partial_input.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CasePartialResultsError("partial replay material is unreadable") from exc
    _validate_partial_report(report)
    if compute_partial_results(payload) != report:
        raise CasePartialResultsError("partial report does not match replay input")
    return manifest


def run_partial_case(
    *,
    receipts: Sequence[Mapping[str, Any]],
    case_manifest: Mapping[str, Any],
    artifact_manifest: Mapping[str, Mapping[str, Any]],
    artifact_root: str | Path,
    output: str | Path,
    calibration: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate, calculate, export and replay-verify one partial real case."""

    payload = normalize_partial_case_receipts(
        receipts,
        case_manifest=case_manifest,
        artifact_manifest=artifact_manifest,
        artifact_root=artifact_root,
        calibration=calibration,
    )
    report = compute_partial_results(payload)
    files = export_partial_results(report, output, input_payload=payload)
    manifest = verify_partial_export(output)
    return {
        "schema": "heterocoord.case-partial-run.v1",
        "version": PARTIAL_VERSION,
        "scope": "measured_unqualified",
        "qualification": "measured_unqualified",
        "formal_gates": dict(FORMAL_GATES),
        "input_digest": payload["input_digest"],
        "output_manifest": manifest,
        "output_files": [str(path) for path in files],
        "report": report,
    }


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CasePartialResultsError(f"cannot read JSON: {path}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipts", required=True, type=Path)
    parser.add_argument("--case-manifest", required=True, type=Path)
    parser.add_argument("--artifact-manifest", required=True, type=Path)
    parser.add_argument("--artifact-root", required=True, type=Path)
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = run_partial_case(
            receipts=_load_json(args.receipts),
            case_manifest=_load_json(args.case_manifest),
            artifact_manifest=_load_json(args.artifact_manifest),
            artifact_root=args.artifact_root,
            calibration=_load_json(args.calibration) if args.calibration else None,
            output=args.output,
        )
        print(
            json.dumps(
                {
                    "schema": result["schema"],
                    "scope": result["scope"],
                    "qualification": result["qualification"],
                    "formal_gates": result["formal_gates"],
                    "input_digest": result["input_digest"],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    except (CasePartialResultsError, OSError, TypeError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "PARTIAL_INPUT_SCHEMA",
    "PARTIAL_REPORT_SCHEMA",
    "PARTIAL_VERSION",
    "CasePartialResultsError",
    "normalize_partial_case_receipts",
    "compute_partial_results",
    "export_partial_results",
    "verify_partial_export",
    "run_partial_case",
]
