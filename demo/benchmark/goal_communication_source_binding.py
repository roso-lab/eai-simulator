"""Fail-closed raw source binding for communication experiment episodes.

The evaluator supplies a pre-registered expected-cell manifest separately
from the observed rows. This prevents post-result filtering and lets the
validator distinguish missing, unexpected, duplicate and rejected cells.
Each expected cell is keyed by

``task_id × base_instance_id × family × condition_id × channel_seed``.

Observed rows additionally carry a real ``episode_id`` and native shared-
transport provenance. Source-bound task performance remains raw-only: this
module neither aggregates a communication score nor changes release gates.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from hashlib import sha256
import json
import math
from typing import Any

from .communication_analysis import CAPACITY_V2_METHOD_ID
from .goal_communication_metrics import (
    COMMUNICATION_METRICS_SCHEMA,
    COMMUNICATION_METRICS_VERSION,
)


COMMUNICATION_SOURCE_BINDING_SCHEMA = (
    "eai.benchmark.goal_communication_source_binding")
COMMUNICATION_SOURCE_BINDING_VERSION = 1
EXPECTED_MANIFEST_SCHEMA = "eai.benchmark.communication_expected_cells"
EXPECTED_MANIFEST_VERSION = 1

COMMUNICATION_FAMILIES = frozenset({
    "fixed_delay", "iid_loss", "capacity", "jitter", "burst_loss",
})
TASK_PERFORMANCE_METRICS = frozenset({
    "SR_comm", "MCR_comm", "AUP_comm", "e_step",
})
TRUSTED_TERMINATION_ANCHOR_SOURCES = frozenset({
    "host_event", "native_receipt",
})

EXPECTED_CELL_FIELDS = (
    "task_id", "base_instance_id", "family", "condition_id",
    "channel_seed",
)
OBSERVED_IDENTITY_FIELDS = ("episode_id", *EXPECTED_CELL_FIELDS)
SOURCE_BINDING_FIELDS = (
    "contract_sha256", "expected_manifest_sha256",
    *OBSERVED_IDENTITY_FIELDS, "metric_name", "condition_spec_sha256",
    "excluded_message_ids_sha256", "evaluator_ref",
    "termination_anchor_source", "communication_report_sha256",
    "progress_source_sha256", "archive_evidence_ref", "archive_sha256",
    "archive_schema", "archive_version",
    "transport_report_schema", "transport_report_version",
    "transport_replay_validated", "shared_transport_runtime_ref",
    "endpoint_pool_binding_sha256",
)

RAW_ONLY_DIMENSION_STATUS = "na_dimension_source_not_wired"
_RELEASE = {
    "release": False,
    "scoreable": False,
    "execution_validated": False,
    "business_outcomes_validated": False,
}
_HEXDIGITS = frozenset("0123456789abcdef")


def _nonempty_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_sha256(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and set(value.lower()) <= _HEXDIGITS)


def _finite(value: Any) -> bool:
    return (not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(float(value)))


def communication_expected_manifest_sha256(manifest: Mapping[str, Any]) -> str:
    """Return the canonical digest rows bind before execution."""
    if not isinstance(manifest, Mapping):
        raise ValueError("expected_manifest must be a mapping")
    try:
        payload = json.dumps(
            manifest, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("expected_manifest must be canonical JSON data") from error
    return sha256(payload).hexdigest()


def _canonical_json_sha256(value: Any) -> str:
    try:
        payload = json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ValueError("value must be canonical JSON data") from error
    return sha256(payload).hexdigest()


def _expected_key(row: Mapping[str, Any]) -> tuple[Any, ...] | None:
    values = tuple(row.get(field) for field in EXPECTED_CELL_FIELDS)
    if all(_nonempty_text(value) for value in values):
        return values
    return None


def _validate_capacity_cell(cell: Mapping[str, Any], index: int) -> None:
    if cell.get("capacity_method_id") != CAPACITY_V2_METHOD_ID:
        raise ValueError(
            f"expected cell {index} capacity_method_id must be v2")
    if cell.get("capacity_unit") != "bits_per_second":
        raise ValueError(
            f"expected cell {index} capacity_unit must be bits_per_second")
    mode = cell.get("capacity_mode")
    value = cell.get("capacity_bits_per_s")
    if mode == "unlimited":
        if value is not None:
            raise ValueError(
                f"expected cell {index} unlimited capacity must be null")
    elif mode == "finite":
        if not _finite(value) or float(value) <= 0:
            raise ValueError(
                f"expected cell {index} finite capacity must be positive")
    else:
        raise ValueError(
            f"expected cell {index} capacity_mode must be finite/unlimited")


def _validate_expected_manifest(
    manifest: Mapping[str, Any],
) -> tuple[dict[tuple[Any, ...], dict[str, Any]], str]:
    if not isinstance(manifest, Mapping):
        raise ValueError("expected_manifest must be a mapping")
    if manifest.get("schema") != EXPECTED_MANIFEST_SCHEMA:
        raise ValueError("expected_manifest schema is unknown")
    if manifest.get("version") != EXPECTED_MANIFEST_VERSION:
        raise ValueError("expected_manifest version is unknown")
    if manifest.get("frozen_before_execution") is not True:
        raise ValueError("expected_manifest must be frozen before execution")
    for flag in ("release", "scoreable", "execution_validated",
                 "business_outcomes_validated"):
        if flag in manifest and manifest.get(flag) is not False:
            raise ValueError(f"expected_manifest {flag} must be false")
    for field in ("task_id", "evaluator_ref", "frozen_at", "archive_schema",
                  "transport_report_schema", "shared_transport_runtime_ref"):
        if not _nonempty_text(manifest.get(field)):
            raise ValueError(f"expected_manifest {field} must be nonempty")
    transport_version = manifest.get("transport_report_version")
    if (isinstance(transport_version, bool)
            or not isinstance(transport_version, int)
            or transport_version < 1):
        raise ValueError(
            "expected_manifest transport_report_version must be positive")
    archive_version = manifest.get("archive_version")
    if (isinstance(archive_version, bool)
            or not isinstance(archive_version, int)
            or archive_version < 1):
        raise ValueError("expected_manifest archive_version must be positive")
    if manifest.get("metric_name") not in TASK_PERFORMANCE_METRICS:
        raise ValueError("expected_manifest metric_name is not task performance")
    excluded = manifest.get("excluded_message_ids")
    if (isinstance(excluded, (str, bytes, Mapping))
            or not isinstance(excluded, Sequence)
            or any(not _nonempty_text(value) for value in excluded)
            or len(set(excluded)) != len(excluded)):
        raise ValueError(
            "expected_manifest excluded_message_ids must be unique strings")
    if not _is_sha256(manifest.get("contract_sha256")):
        raise ValueError("expected_manifest contract_sha256 is invalid")
    cells = manifest.get("cells")
    if (isinstance(cells, (str, bytes, Mapping))
            or not isinstance(cells, Sequence) or not cells):
        raise ValueError("expected_manifest cells must be a nonempty sequence")
    expected: dict[tuple[Any, ...], dict[str, Any]] = {}
    for index, raw in enumerate(cells):
        if not isinstance(raw, Mapping):
            raise ValueError(f"expected cell {index} must be a mapping")
        cell = dict(raw)
        key = _expected_key(cell)
        if key is None:
            raise ValueError(f"expected cell {index} identity is incomplete")
        if cell["task_id"] != manifest["task_id"]:
            raise ValueError(f"expected cell {index} task_id conflicts with manifest")
        if cell["family"] not in COMMUNICATION_FAMILIES:
            raise ValueError(f"expected cell {index} family is unknown")
        if not _is_sha256(cell.get("condition_spec_sha256")):
            raise ValueError(f"expected cell {index} condition spec digest is invalid")
        for flag in ("release", "scoreable", "execution_validated",
                     "business_outcomes_validated"):
            if flag in cell and cell.get(flag) is not False:
                raise ValueError(f"expected cell {index} {flag} must be false")
        if key in expected:
            raise ValueError(f"duplicate expected cell: {key!r}")
        if cell["family"] in {"jitter", "burst_loss"} and not _nonempty_text(
                cell.get("comparison_level")):
            raise ValueError(
                f"expected cell {index} requires comparison_level")
        if cell["family"] == "capacity":
            _validate_capacity_cell(cell, index)
        expected[key] = cell

    # Jitter and burst rows are paired diagnostics. Their preregistration
    # must include the corresponding same-instance, same-seed level.
    values = list(expected.values())
    for cell in values:
        if cell["family"] not in {"jitter", "burst_loss"}:
            continue
        counterpart = "fixed_delay" if cell["family"] == "jitter" else "iid_loss"
        found = any(
            other["task_id"] == cell["task_id"]
            and other["base_instance_id"] == cell["base_instance_id"]
            and other["channel_seed"] == cell["channel_seed"]
            and other["family"] == counterpart
            and other.get("comparison_level") == cell["comparison_level"]
            for other in values)
        if not found:
            raise ValueError(
                f"expected {cell['family']} cell lacks paired {counterpart} cell")
    return expected, communication_expected_manifest_sha256(manifest)


def _source_reasons(
    row: Mapping[str, Any], expected: Mapping[str, Any] | None,
    *, manifest: Mapping[str, Any], manifest_sha256: str,
    duplicate_key: bool, reused_episode: bool,
) -> list[str]:
    reasons: list[str] = []
    for flag in ("release", "scoreable", "execution_validated",
                 "business_outcomes_validated"):
        if flag in row and row.get(flag) is not False:
            reasons.append(f"row_{flag}_must_be_false")
    for field in OBSERVED_IDENTITY_FIELDS:
        if not _nonempty_text(row.get(field)):
            reasons.append(f"{field}_missing")
    family = row.get("family")
    if _nonempty_text(family) and family not in COMMUNICATION_FAMILIES:
        reasons.append("family_unknown")
    if expected is None:
        reasons.append("unexpected_cell")
    if duplicate_key:
        reasons.append("duplicate_matrix_cell")
    if reused_episode:
        reasons.append("episode_id_reused_across_cells")

    bindings = row.get("source_bindings")
    if not isinstance(bindings, Mapping):
        reasons.append("source_bindings_missing")
        bindings = {}
    for flag in ("release", "scoreable", "execution_validated",
                 "business_outcomes_validated"):
        if flag in bindings and bindings.get(flag) is not False:
            reasons.append(f"source_bindings_{flag}_must_be_false")
    for field in OBSERVED_IDENTITY_FIELDS:
        value = bindings.get(field)
        if not _nonempty_text(value):
            reasons.append(f"source_{field}_missing")
        elif _nonempty_text(row.get(field)) and value != row.get(field):
            reasons.append(f"source_{field}_conflict")

    for field in ("contract_sha256", "expected_manifest_sha256",
                  "condition_spec_sha256", "communication_report_sha256",
                  "progress_source_sha256", "archive_sha256",
                  "endpoint_pool_binding_sha256"):
        if not _is_sha256(bindings.get(field)):
            reasons.append(f"{field}_missing_or_invalid")
    if bindings.get("contract_sha256") != manifest.get("contract_sha256"):
        reasons.append("contract_sha256_not_expected")
    if bindings.get("expected_manifest_sha256") != manifest_sha256:
        reasons.append("expected_manifest_sha256_mismatch")
    if bindings.get("evaluator_ref") != manifest.get("evaluator_ref"):
        reasons.append("evaluator_ref_not_expected")
    if bindings.get("metric_name") != row.get("metric_name"):
        reasons.append("source_metric_name_conflict")
    if row.get("metric_name") != manifest.get("metric_name"):
        reasons.append("metric_name_not_expected")
    expected_exclusions_sha = _canonical_json_sha256(
        manifest.get("excluded_message_ids"))
    if not _is_sha256(bindings.get("excluded_message_ids_sha256")):
        reasons.append("excluded_message_ids_sha256_missing_or_invalid")
    elif bindings.get("excluded_message_ids_sha256") != expected_exclusions_sha:
        reasons.append("excluded_message_ids_sha256_mismatch")
    if expected is not None:
        if bindings.get("condition_spec_sha256") != expected.get(
                "condition_spec_sha256"):
            reasons.append("condition_spec_sha256_mismatch")
        expected_level = expected.get("comparison_level")
        if expected_level is not None:
            if row.get("comparison_level") != expected_level:
                reasons.append("comparison_level_not_expected")
            if bindings.get("comparison_level") != expected_level:
                reasons.append("source_comparison_level_conflict")

    if not _nonempty_text(bindings.get("archive_evidence_ref")):
        reasons.append("archive_evidence_ref_missing")
    if not _nonempty_text(bindings.get("archive_schema")):
        reasons.append("archive_schema_missing")
    elif bindings.get("archive_schema") != manifest.get("archive_schema"):
        reasons.append("archive_schema_not_expected")
    archive_version = bindings.get("archive_version")
    if (isinstance(archive_version, bool)
            or not isinstance(archive_version, int) or archive_version < 1):
        reasons.append("archive_version_missing_or_invalid")
    elif archive_version != manifest.get("archive_version"):
        reasons.append("archive_version_not_expected")
    if not _nonempty_text(bindings.get("shared_transport_runtime_ref")):
        reasons.append("shared_transport_runtime_ref_missing")
    if not _nonempty_text(bindings.get("transport_report_schema")):
        reasons.append("transport_report_schema_missing")
    version = bindings.get("transport_report_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        reasons.append("transport_report_version_missing_or_invalid")
    elif version != manifest.get("transport_report_version"):
        reasons.append("transport_report_version_not_expected")
    if (bindings.get("transport_report_schema")
            != manifest.get("transport_report_schema")):
        reasons.append("transport_report_schema_not_expected")
    if (bindings.get("shared_transport_runtime_ref")
            != manifest.get("shared_transport_runtime_ref")):
        reasons.append("shared_transport_runtime_ref_not_expected")
    if bindings.get("transport_replay_validated") is not True:
        reasons.append("transport_replay_not_validated")
    anchor = bindings.get("termination_anchor_source")
    if not _nonempty_text(anchor):
        reasons.append("termination_anchor_source_missing")
    elif anchor not in TRUSTED_TERMINATION_ANCHOR_SOURCES:
        reasons.append("termination_anchor_source_not_trusted")
    return sorted(set(reasons))


def _measurement_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    metric = row.get("metric_name")
    if metric not in TASK_PERFORMANCE_METRICS:
        return {"status": "invalid_metric", "metric_name": metric,
                "metric_value": None, "qualified_metric_value": None,
                "reasons": ["metric_not_preregistered_task_performance"]}
    condition = row.get("condition_metrics")
    if not isinstance(condition, Mapping):
        return {"status": "invalid_condition_metrics", "metric_name": metric,
                "metric_value": None, "qualified_metric_value": None,
                "reasons": ["condition_metrics_missing"]}
    reasons: list[str] = []
    if condition.get("schema") != COMMUNICATION_METRICS_SCHEMA:
        reasons.append("condition_metrics_schema_unknown")
    if condition.get("version") != COMMUNICATION_METRICS_VERSION:
        reasons.append("condition_metrics_version_unknown")
    if condition.get("condition_id") != row.get("condition_id"):
        reasons.append("condition_id_conflict")
    if condition.get("status") != "computed":
        reasons.append("condition_metrics_status_not_computed")
    for flag in ("release", "scoreable", "execution_validated",
                 "business_outcomes_validated"):
        if flag in condition and condition.get(flag) is not False:
            reasons.append(f"condition_metrics_{flag}_must_be_false")

    task = condition.get("task_performance")
    if not isinstance(task, Mapping):
        reasons.append("task_performance_missing")
        return {"status": "invalid_condition_metrics", "metric_name": metric,
                "metric_value": None, "qualified_metric_value": None,
                "reasons": sorted(set(reasons))}
    value = task.get(metric)
    for flag in ("release", "scoreable", "execution_validated",
                 "business_outcomes_validated"):
        if flag in task and task.get(flag) is not False:
            reasons.append(f"task_performance_{flag}_must_be_false")
    task_status = task.get("status")
    if not _nonempty_text(task_status):
        reasons.append("task_performance_status_missing")
    if value is not None and not _finite(value):
        reasons.append("task_performance_metric_not_finite")
        value = None
    if value is not None and not 0.0 <= float(value) <= 1.0:
        reasons.append("task_performance_metric_out_of_range")
    if value is not None and task.get("status") != "computed":
        reasons.append("task_performance_status_not_computed")
    if (value is None and metric in {"SR_comm", "MCR_comm", "AUP_comm"}
            and task_status == "computed"):
        reasons.append("task_performance_metric_missing_from_computed")
    companion = condition.get("transport_companion")
    cost = condition.get("communication_cost")
    for label, block in (("transport_companion", companion),
                         ("communication_cost", cost)):
        if not isinstance(block, Mapping):
            continue
        for flag in ("release", "scoreable", "execution_validated",
                     "business_outcomes_validated"):
            if flag in block and block.get(flag) is not False:
                reasons.append(f"{label}_{flag}_must_be_false")
    companion_status = (companion.get("status")
                        if isinstance(companion, Mapping) else None)
    cost_status = cost.get("status") if isinstance(cost, Mapping) else None
    if companion_status != "computed":
        reasons.append("transport_companion_not_computed")
    if cost_status != "computed":
        reasons.append("communication_cost_not_computed")
    offer_bits = cost.get("B_offer_bits") if isinstance(cost, Mapping) else None
    link_bits = cost.get("B_link_bits") if isinstance(cost, Mapping) else None
    if cost_status == "computed":
        for label, number in (("B_offer_bits", offer_bits),
                              ("B_link_bits", link_bits)):
            if not _finite(number) or float(number) < 0:
                reasons.append(f"communication_cost_{label}_invalid")

    if metric == "SR_comm":
        if not (task.get("sr_basis") == "Q(H)"
                and task.get("proxy_only") is True
                and task.get("global_termination_predicate_available") is False
                and task.get("SR_comm_formal") is None):
            reasons.append("sr_proxy_boundary_missing")
        status = ("proxy_only" if value is not None and not reasons
                  else "na_task_performance" if value is None and not reasons
                  else "invalid_condition_metrics")
    else:
        status = ("qualified_raw_only" if value is not None and not reasons
                  else "na_task_performance" if value is None and not reasons
                  else "invalid_condition_metrics")
    return {
        "status": status,
        "metric_name": metric,
        "metric_value": None if value is None else float(value),
        "qualified_metric_value": (
            float(value) if status == "qualified_raw_only" else None),
        "task_performance_status": task_status,
        "transport_companion_status": companion_status,
        "communication_cost_status": cost_status,
        "B_offer_bits": (float(offer_bits) if _finite(offer_bits) else None),
        "B_link_bits": (float(link_bits) if _finite(link_bits) else None),
        "reasons": sorted(set(reasons)),
    }


def build_communication_source_matrix(
    expected_manifest: Mapping[str, Any],
    observed_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate preregistered communication cells without scoring them."""
    expected, manifest_sha = _validate_expected_manifest(expected_manifest)
    if (isinstance(observed_rows, (str, bytes, Mapping))
            or not isinstance(observed_rows, Sequence)):
        raise ValueError("observed_rows must be a sequence of mappings")
    rows: list[Mapping[str, Any]] = []
    for index, row in enumerate(observed_rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"observed row {index} must be a mapping")
        rows.append(row)

    keys = [_expected_key(row) for row in rows]
    key_counts = Counter(key for key in keys if key is not None)
    episode_counts = Counter(
        row.get("episode_id") for row in rows
        if _nonempty_text(row.get("episode_id")))
    records: list[dict[str, Any]] = []
    seen_expected: set[tuple[Any, ...]] = set()
    accepted = diagnostic = rejected = 0
    unexpected: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    for index, (row, key) in enumerate(zip(rows, keys)):
        expected_cell = expected.get(key) if key is not None else None
        if key in expected:
            seen_expected.add(key)
        source_reasons = _source_reasons(
            row, expected_cell, manifest=expected_manifest,
            manifest_sha256=manifest_sha,
            duplicate_key=key is not None and key_counts[key] > 1,
            reused_episode=(
                _nonempty_text(row.get("episode_id"))
                and episode_counts[row.get("episode_id")] > 1))
        measurement = _measurement_summary(row)
        if expected_cell is None:
            unexpected.append({"row_index": index, "identity": list(key)
                               if key is not None else None})
        if key is not None and key_counts[key] > 1:
            duplicates.append({"row_index": index, "identity": list(key)})
        if source_reasons or measurement["status"].startswith("invalid"):
            row_status = "rejected"
            rejected += 1
        elif measurement["status"] == "qualified_raw_only":
            row_status = "accepted_raw_only"
            accepted += 1
        else:
            row_status = "diagnostic_only"
            diagnostic += 1
        records.append({
            "row_index": index,
            **{field: row.get(field) for field in OBSERVED_IDENTITY_FIELDS},
            "row_status": row_status,
            "source_reasons": source_reasons,
            "measurement": measurement,
            # Never echo an untrusted raw payload into the validated result:
            # rejected inputs may contain forged release flags. The digest
            # plus archive ref keeps them locatable without granting trust.
            "raw_input_sha256": _canonical_json_sha256(row),
            "raw_input_archive_ref": (
                row.get("source_bindings", {}).get("archive_evidence_ref")
                if isinstance(row.get("source_bindings"), Mapping) else None),
            "raw_input_embedded": False,
        })

    missing_keys = [key for key in expected if key not in seen_expected]
    missing_cells = [
        {field: expected[key][field] for field in EXPECTED_CELL_FIELDS}
        for key in missing_keys
    ]
    expected_families = {cell["family"] for cell in expected.values()}
    missing_manifest_families = sorted(
        COMMUNICATION_FAMILIES - expected_families)
    structural_gap = bool(
        missing_cells or unexpected or duplicates or rejected
        or missing_manifest_families)
    status = ("raw_only_no_observed_rows" if not records else
              "raw_only_incomplete" if structural_gap else
              "raw_only_with_na_or_proxy" if diagnostic else
              "raw_only_validation_complete")
    families_observed = sorted({
        row.get("family") for row in rows
        if row.get("family") in COMMUNICATION_FAMILIES})
    return {
        "schema": COMMUNICATION_SOURCE_BINDING_SCHEMA,
        "version": COMMUNICATION_SOURCE_BINDING_VERSION,
        "status": status,
        "dimension_status": RAW_ONLY_DIMENSION_STATUS,
        "formal_scoring_wired": False,
        "not_protocol_score": True,
        "expected_manifest_sha256": manifest_sha,
        "expected_cell_count": len(expected),
        "observed_row_count": len(records),
        "accepted_raw_only_count": accepted,
        "diagnostic_only_count": diagnostic,
        "rejected_count": rejected,
        "missing_cell_count": len(missing_cells),
        "unexpected_row_count": len(unexpected),
        "duplicate_row_count": len(duplicates),
        "required_families": sorted(COMMUNICATION_FAMILIES),
        "manifest_families": sorted(expected_families),
        "missing_manifest_families": missing_manifest_families,
        "manifest_complete_family_coverage": not missing_manifest_families,
        "families_observed": families_observed,
        "missing_cells": missing_cells,
        "unexpected_rows": unexpected,
        "duplicate_rows": duplicates,
        "required_source_bindings": list(SOURCE_BINDING_FIELDS),
        "expected_cell_key": list(EXPECTED_CELL_FIELDS),
        "rows": records,
        "missing_is_not_zero": True,
        "unexpected_rows_do_not_expand_manifest": True,
        "task_performance_and_transport_companion_separate": True,
        "synthetic_delivery_is_not_task_performance": True,
        "no_imputation": True,
        "no_aggregation_performed": True,
        **_RELEASE,
    }


__all__ = [
    "COMMUNICATION_FAMILIES", "COMMUNICATION_SOURCE_BINDING_SCHEMA",
    "COMMUNICATION_SOURCE_BINDING_VERSION", "EXPECTED_CELL_FIELDS",
    "EXPECTED_MANIFEST_SCHEMA", "EXPECTED_MANIFEST_VERSION",
    "OBSERVED_IDENTITY_FIELDS", "RAW_ONLY_DIMENSION_STATUS",
    "SOURCE_BINDING_FIELDS", "TASK_PERFORMANCE_METRICS",
    "TRUSTED_TERMINATION_ANCHOR_SOURCES",
    "build_communication_source_matrix",
    "communication_expected_manifest_sha256",
]
