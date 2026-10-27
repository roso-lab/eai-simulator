"""Strict, content-addressed paper result index contracts.

This module deliberately does not consume ``metrics.csv`` or the diagnostic
results-pipeline output.  A paper row can only be populated by a registered
statistical-report binding whose file bytes and complete result identity are
revalidated here.  Structural completeness never opens a formal gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping, Sequence


IMPLEMENTATION_REGISTER_SCHEMA = "heterocoord.implementation-register.v1"
CONDITION_REGISTER_SCHEMA = "heterocoord.comparison-condition-register.v1"
REQUIRED_DIRECTORY_SCHEMA = "heterocoord.required-result-directory.v2"
STATISTICAL_BINDING_SCHEMA = "heterocoord.statistical-report-binding.v2"
STATISTICAL_REPORT_SCHEMA = "heterocoord.statistical-report.v2"
STATISTICAL_INPUT_SCHEMA = "heterocoord.statistical-input.v2"
STATISTICAL_EVENT_LEDGER_SCHEMA = "heterocoord.statistical-event-ledger.v1"
ROW_SCHEMA = "heterocoord.paper-result-row.v2"
INDEX_MANIFEST_SCHEMA = "heterocoord.paper-result-index-manifest.v2"
CELL_MAP_SCHEMA = "heterocoord.manuscript-cell-map.v1"

SHA256_RE = re.compile(r"[0-9a-f]{64}")
COMMIT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
FORMAL_GATES_FALSE = {
    "statistical": False,
    "physical": False,
    "difficulty": False,
    "rankable": False,
    "paper_score_eligible": False,
}

IDENTITY_FIELDS = (
    "implementation_id",
    "comparison_condition_id",
    "task_family",
    "domain",
    "primitive",
    "difficulty",
    "factor_pair",
    "factor_level_a",
    "factor_level_b",
    "checkpoint_round",
    "event_round",
    "untreated_history_id",
    "event_target",
    "availability_operation",
    "notification_policy",
    "restoration_policy",
    "event",
    "horizon",
    "source_direction",
    "target_direction",
    "oriented_endpoint_scale",
    "scale_regime",
    "team_composition",
    "branch",
    "outcome_id",
    "estimator_version",
)
MANDATORY_IDENTITY_FIELDS = {
    "implementation_id",
    "comparison_condition_id",
    "task_family",
    "domain",
    "outcome_id",
    "estimator_version",
}
TAGGED_IDENTITY_FIELDS = set(IDENTITY_FIELDS) - MANDATORY_IDENTITY_FIELDS

EXECUTION_FIELDS = {
    "episode_reset_method",
    "permitted_history_digest",
    "initialization_configuration_digest",
    "execution_audit_version",
}
BASE_SUPPORT_COUNT_FIELDS = {
    "generated_base_instances",
    "infrastructure_valid_base_instances",
    "metric_eligible_base_instances",
    "repeated_trajectories",
}
TAGGED_SUPPORT_COUNT_FIELDS = {
    "paired_instances",
    "common_support_instances",
    "event_count",
    "competing_event_count",
    "censoring_count",
}
SUPPORT_FIELDS = BASE_SUPPORT_COUNT_FIELDS | TAGGED_SUPPORT_COUNT_FIELDS | {
    "planned_coverage_fraction",
    "observed_coverage_fraction",
    "numerator",
    "denominator",
}
ESTIMATE_FIELDS = {
    "entry_state", "status", "point", "lower", "upper", "interval_method", "reason"
}
EVIDENCE_FIELDS = {"event_ledger_digest", "input_ref", "report_ref", "source_scope"}
ROW_FIELDS = {"schema", "identity", "execution", "support", "estimate", "evidence", "dimension_extension"}

IMPLEMENTATION_FIELDS = {
    "implementation_id",
    "display_name",
    "primary_order",
    "source_digest",
    "model_digest",
    "adapter_digest",
    "runtime_digest",
    "cognition",
    "comparison_group",
    "information_rights",
    "training_access",
    "decomposition_policy",
    "supported_task_subsets",
    "supported_disturbances",
}
CONDITION_FIELDS = {
    "condition_id",
    "implementation_id",
    "prompt_digest",
    "model_digest",
    "tool_digest",
    "initial_memory_digest",
    "adapter_digest",
    "observation_rights",
    "budget_policy",
    "runtime_policy",
    "reset_policy",
}
DIRECTORY_ENTRY_FIELDS = {
    "identity",
    "execution",
    "support",
    "dimension_extension",
    "disposition",
    "reason",
    "allow_range_display",
}
FILE_REF_FIELDS = {"path", "size", "sha256"}
BINDING_ENTRY_FIELDS = {
    "result_key_digest",
    "identity",
    "implementation_id",
    "comparison_condition_id",
    "score_id",
    "outcome_id",
    "estimator_version",
    "expected_input_digest",
    "report_ref",
    "input_ref",
    "event_ledger_ref",
}
STATISTICAL_REPORT_FIELDS = {
    "schema",
    "paper_commit",
    "scorebook_digest",
    "input_digest",
    "implementation_id",
    "comparison_condition_id",
    "result_key_digest",
    "score_id",
    "outcome_id",
    "estimator_version",
    "evidence_scope",
    "formal_gates",
    "estimate",
    "support",
    "event_ledger_digest",
    "reducer_method",
    "reducer_implementation_digest",
    "reducer_payload_digest",
    "reducer_input_digest",
    "reducer_output",
}

STATISTICAL_INPUT_FIELDS = {
    "schema",
    "paper_commit",
    "score_id",
    "implementation_id",
    "comparison_condition_id",
    "outcome_id",
    "estimator_version",
    "result_key_digest",
    "evidence_scope",
    "reducer_method",
    "reducer_implementation_digest",
    "reducer_payload",
    "reducer_payload_digest",
    "reducer_input_digest",
    "event_ledger_ref",
    "input_digest",
}
EVENT_LEDGER_FIELDS = {
    "schema",
    "paper_commit",
    "score_id",
    "implementation_id",
    "comparison_condition_id",
    "outcome_id",
    "estimator_version",
    "result_key_digest",
    "evidence_scope",
    "reducer_method",
    "reducer_implementation_digest",
    "reducer_payload_digest",
    "run_links",
    "support_registry",
}


class PaperResultIndexError(ValueError):
    """Raised when a paper-result artifact cannot be trusted."""


def _exact_keys(value: Mapping[str, Any], expected: set[str], name: str) -> None:
    if not isinstance(value, Mapping) or set(value) != expected:
        actual = sorted(value) if isinstance(value, Mapping) else type(value).__name__
        raise PaperResultIndexError(f"{name} schema mismatch: {actual}")


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise PaperResultIndexError(f"{name} must be non-empty trimmed text")
    return value


def _sha256(value: Any, name: str) -> str:
    value = _text(value, name)
    if SHA256_RE.fullmatch(value) is None:
        raise PaperResultIndexError(f"{name} must be a lowercase SHA-256")
    return value


def _commit(value: Any, name: str) -> str:
    value = _text(value, name)
    if COMMIT_RE.fullmatch(value) is None:
        raise PaperResultIndexError(f"{name} must be a lowercase 40- or 64-hex commit")
    return value


def _integer(value: Any, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise PaperResultIndexError(f"{name} must be an integer >= {minimum}")
    return value


def _real(value: Any, name: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise PaperResultIndexError(f"{name} must be a finite real number")
    result = float(value)
    if minimum is not None and result < minimum:
        raise PaperResultIndexError(f"{name} is below {minimum}")
    if maximum is not None and result > maximum:
        raise PaperResultIndexError(f"{name} is above {maximum}")
    return result


def _reject_nonfinite(value: Any, name: str = "value") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise PaperResultIndexError(f"{name} contains NaN or Infinity")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise PaperResultIndexError(f"{name} contains a non-string key")
            _reject_nonfinite(item, f"{name}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_nonfinite(item, f"{name}[{index}]")


def canonical_json_bytes(value: Any) -> bytes:
    _reject_nonfinite(value)
    try:
        return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PaperResultIndexError(f"value is not canonical JSON data: {exc}") from exc


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


REDUCER_METHODS = {
    "D1": "benchmark.organization_v3.organization_report_from_payload",
    "D2": "benchmark.analysis_v3.analyze_dimension",
    "D3": "benchmark.relations_v3.d3_relation_report",
    "D4": "benchmark.analysis_v3.analyze_dimension",
    "D5": "benchmark.analysis_v3.analyze_dimension+benchmark.fixed_catalog_v3.FixedCatalogV3.analyze",
    "D6": "benchmark.analysis_v3.analyze_dimension+benchmark.fixed_catalog_v3.FixedCatalogV3.analyze",
}


def reducer_method_for_score(score_id: str) -> str:
    try:
        return REDUCER_METHODS[score_id]
    except KeyError as exc:
        raise PaperResultIndexError("statistical score_id is outside D1-D6") from exc


def reducer_implementation_digest(score_id: str) -> str:
    reducer_files = {
        "D1": ("organization_v3.py",),
        "D2": ("analysis_v3.py", "statistics_v3.py"),
        "D3": ("relations_v3.py", "statistics_v3.py"),
        "D4": ("analysis_v3.py", "statistics_v3.py"),
        "D5": ("analysis_v3.py", "fixed_catalog_v3.py", "statistics_v3.py"),
        "D6": ("analysis_v3.py", "fixed_catalog_v3.py", "statistics_v3.py"),
    }
    try:
        names = ("statistical_report_producer_v1.py", *reducer_files[score_id])
    except KeyError as exc:
        raise PaperResultIndexError("statistical score_id is outside D1-D6") from exc
    root = Path(__file__).resolve().parent
    sources = []
    for name in names:
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise PaperResultIndexError(f"reducer implementation source is unavailable: {name}")
        sources.append({"path": f"benchmark/{name}", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return canonical_digest(sources)


def reducer_payload_digest(payload: Mapping[str, Any]) -> str:
    if not isinstance(payload, Mapping):
        raise PaperResultIndexError("reducer_payload must be an object")
    return canonical_digest(payload)


def statistical_input_digest(value: Mapping[str, Any]) -> str:
    if not isinstance(value, Mapping):
        raise PaperResultIndexError("statistical input must be an object")
    unsigned = dict(value)
    unsigned.pop("input_digest", None)
    return canonical_digest(unsigned)


def _encode_nonfinite(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            label = "nan"
        else:
            label = "positive_infinity" if value > 0 else "negative_infinity"
        return {"__heterocoord_nonfinite__": label}
    if isinstance(value, Mapping):
        return {key: _encode_nonfinite(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode_nonfinite(item) for item in value]
    return value


def encode_reducer_output(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PaperResultIndexError("reducer output must be an object")
    return {"encoding": "heterocoord.tagged-nonfinite-json.v1", "value": _encode_nonfinite(value)}


def _decode_nonfinite(value: Any) -> Any:
    if isinstance(value, Mapping):
        if set(value) == {"__heterocoord_nonfinite__"}:
            label = value["__heterocoord_nonfinite__"]
            if label == "nan":
                return float("nan")
            if label == "positive_infinity":
                return float("inf")
            if label == "negative_infinity":
                return float("-inf")
            raise PaperResultIndexError("stored reducer output has an invalid nonfinite tag")
        return {key: _decode_nonfinite(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode_nonfinite(item) for item in value]
    return value


def decode_reducer_output(value: Mapping[str, Any]) -> Mapping[str, Any]:
    _exact_keys(value, {"encoding", "value"}, "statistical report reducer_output")
    if value["encoding"] != "heterocoord.tagged-nonfinite-json.v1" or not isinstance(value["value"], Mapping):
        raise PaperResultIndexError("statistical report reducer_output encoding is invalid")
    decoded = _decode_nonfinite(value["value"])
    if not isinstance(decoded, Mapping):  # pragma: no cover - guarded above
        raise PaperResultIndexError("decoded reducer output must be an object")
    return decoded


def _validate_tagged(value: Any, name: str) -> None:
    if not isinstance(value, Mapping):
        raise PaperResultIndexError(f"{name} must be a tagged object")
    status = value.get("status")
    if status == "value":
        _exact_keys(value, {"status", "value"}, name)
        item = value["value"]
        if item is None or isinstance(item, (Mapping, list, tuple)):
            raise PaperResultIndexError(f"{name}.value must be a scalar")
        if isinstance(item, str):
            _text(item, f"{name}.value")
        elif isinstance(item, float):
            _real(item, f"{name}.value")
    elif status == "not_applicable":
        _exact_keys(value, {"status", "reason"}, name)
        _text(value["reason"], f"{name}.reason")
    else:
        raise PaperResultIndexError(f"{name}.status is invalid")


def validate_identity(identity: Mapping[str, Any]) -> Mapping[str, Any]:
    _exact_keys(identity, set(IDENTITY_FIELDS), "identity")
    for field in MANDATORY_IDENTITY_FIELDS:
        _text(identity[field], f"identity.{field}")
    for field in TAGGED_IDENTITY_FIELDS:
        _validate_tagged(identity[field], f"identity.{field}")
    return identity


def _validate_execution(execution: Mapping[str, Any]) -> None:
    _exact_keys(execution, EXECUTION_FIELDS, "execution")
    _text(execution["episode_reset_method"], "execution.episode_reset_method")
    _sha256(execution["permitted_history_digest"], "execution.permitted_history_digest")
    _sha256(
        execution["initialization_configuration_digest"],
        "execution.initialization_configuration_digest",
    )
    _text(execution["execution_audit_version"], "execution.execution_audit_version")


def _validate_support(support: Mapping[str, Any]) -> None:
    _exact_keys(support, SUPPORT_FIELDS, "support")
    for field in BASE_SUPPORT_COUNT_FIELDS:
        _integer(support[field], f"support.{field}")
    tagged_counts: dict[str, int | None] = {}
    for field in TAGGED_SUPPORT_COUNT_FIELDS:
        value = support[field]
        if not isinstance(value, Mapping):
            raise PaperResultIndexError(f"support.{field} must be a tagged count")
        status = value.get("status")
        if status == "observed":
            _exact_keys(value, {"status", "value"}, f"support.{field}")
            tagged_counts[field] = _integer(value["value"], f"support.{field}.value")
        elif status in {"unavailable", "not_applicable"}:
            _exact_keys(value, {"status", "reason"}, f"support.{field}")
            _text(value["reason"], f"support.{field}.reason")
            tagged_counts[field] = None
        else:
            raise PaperResultIndexError(f"support.{field}.status is invalid")
    _real(support["planned_coverage_fraction"], "support.planned_coverage_fraction", minimum=0, maximum=1)
    _real(support["observed_coverage_fraction"], "support.observed_coverage_fraction", minimum=0, maximum=1)
    numerator = _real(support["numerator"], "support.numerator", minimum=0)
    denominator = _real(support["denominator"], "support.denominator", minimum=0)
    if numerator > denominator:
        raise PaperResultIndexError("support.numerator exceeds denominator")
    generated = support["generated_base_instances"]
    infrastructure = support["infrastructure_valid_base_instances"]
    eligible = support["metric_eligible_base_instances"]
    if not 0 <= eligible <= infrastructure <= generated:
        raise PaperResultIndexError("support base-instance counts are not nested")
    for field in ("paired_instances", "common_support_instances"):
        count = tagged_counts[field]
        if count is not None and count > generated:
            raise PaperResultIndexError("support paired/common counts exceed generated count")


def _validate_estimate(estimate: Mapping[str, Any]) -> None:
    _exact_keys(estimate, ESTIMATE_FIELDS, "estimate")
    state = estimate["entry_state"]
    status = estimate["status"]
    reason = _text(estimate["reason"], "estimate.reason")
    point, lower, upper = estimate["point"], estimate["lower"], estimate["upper"]
    method = estimate["interval_method"]
    expected = {
        "observed": "valid",
        "observed_range": "range",
        "not_estimable": "na",
        "missing": "incomplete",
        "unsupported": "na",
        "incomplete": "incomplete",
        "structural_na": "na",
    }
    if state not in expected or status != expected[state]:
        raise PaperResultIndexError("estimate entry_state/status pairing is invalid")
    if state == "observed":
        _real(point, "estimate.point")
        if (lower is None) != (upper is None):
            raise PaperResultIndexError("observed estimate interval must be complete or absent")
        if lower is not None:
            lo = _real(lower, "estimate.lower")
            hi = _real(upper, "estimate.upper")
            if lo > float(point) or float(point) > hi:
                raise PaperResultIndexError("observed interval does not contain point")
            _text(method, "estimate.interval_method")
        elif method is not None:
            raise PaperResultIndexError("interval_method requires interval endpoints")
    elif state == "observed_range":
        if point is not None:
            raise PaperResultIndexError("observed_range point must be null")
        lo = _real(lower, "estimate.lower")
        hi = _real(upper, "estimate.upper")
        if lo > hi or method != "identification_range":
            raise PaperResultIndexError("observed_range requires an ordered identification_range")
    else:
        if any(item is not None for item in (point, lower, upper, method)):
            raise PaperResultIndexError(f"{state} estimates must not contain numerical fields")
    if state == "missing" and not reason.startswith("missing_"):
        raise PaperResultIndexError("missing estimate requires a missing_* reason")
    if state == "incomplete" and not reason.startswith("incomplete_"):
        raise PaperResultIndexError("incomplete estimate requires an incomplete_* reason")


def _validate_file_ref_shape(reference: Mapping[str, Any], name: str) -> None:
    _exact_keys(reference, FILE_REF_FIELDS, name)
    path = _text(reference["path"], f"{name}.path")
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or path in {".", ""}:
        raise PaperResultIndexError(f"{name}.path must be a safe root-relative path")
    _integer(reference["size"], f"{name}.size")
    _sha256(reference["sha256"], f"{name}.sha256")


def _validate_evidence(evidence: Mapping[str, Any], state: str) -> None:
    _exact_keys(evidence, EVIDENCE_FIELDS, "evidence")
    scope = evidence["source_scope"]
    if scope not in {"paper_result_candidate", "diagnostic_only", "none"}:
        raise PaperResultIndexError("evidence.source_scope is invalid")
    for field in ("input_ref", "report_ref"):
        reference = evidence[field]
        if reference is not None:
            _validate_file_ref_shape(reference, f"evidence.{field}")
    ledger = evidence["event_ledger_digest"]
    if ledger is not None:
        _sha256(ledger, "evidence.event_ledger_digest")
    if state in {"observed", "observed_range", "not_estimable"}:
        if scope != "paper_result_candidate" or ledger is None or evidence["input_ref"] is None or evidence["report_ref"] is None:
            raise PaperResultIndexError("produced evidence must be fully content-addressed paper evidence")
    elif scope == "paper_result_candidate" and evidence["report_ref"] is None:
        raise PaperResultIndexError("paper_result_candidate scope requires a report_ref")


def _validate_dimension_extension(extension: Mapping[str, Any]) -> None:
    if not isinstance(extension, Mapping):
        raise PaperResultIndexError("dimension_extension must be an object")
    kind = extension.get("kind")
    if kind == "not_applicable":
        _exact_keys(extension, {"kind", "reason"}, "dimension_extension")
        _text(extension["reason"], "dimension_extension.reason")
        return
    if kind == "D5":
        fields = {
            "kind", "family_id", "case_id", "unit_id", "source_reference_identity",
            "target_checkpoint_identity", "scheduled_attempts", "verified_attempts",
            "unknown_attempts", "score_lower", "score_upper", "case_sampling_status",
            "generalization_status",
        }
        _exact_keys(extension, fields, "dimension_extension.D5")
        for field in (
            "family_id", "case_id", "unit_id", "source_reference_identity",
            "target_checkpoint_identity", "case_sampling_status", "generalization_status",
        ):
            _text(extension[field], f"dimension_extension.{field}")
        for field in ("scheduled_attempts", "verified_attempts", "unknown_attempts"):
            _integer(extension[field], f"dimension_extension.{field}")
        if extension["verified_attempts"] + extension["unknown_attempts"] > extension["scheduled_attempts"]:
            raise PaperResultIndexError("D5 attempt counts exceed scheduled attempts")
        lo = _real(extension["score_lower"], "dimension_extension.score_lower")
        hi = _real(extension["score_upper"], "dimension_extension.score_upper")
        if lo > hi:
            raise PaperResultIndexError("D5 score range is reversed")
        return
    if kind == "D6":
        fields = {"kind", "reference_coordinator_version", "calibration_batch", "per_instance_reference_scales"}
        _exact_keys(extension, fields, "dimension_extension.D6")
        _text(extension["reference_coordinator_version"], "dimension_extension.reference_coordinator_version")
        _text(extension["calibration_batch"], "dimension_extension.calibration_batch")
        scales = extension["per_instance_reference_scales"]
        if not isinstance(scales, list) or not scales:
            raise PaperResultIndexError("D6 requires per-instance reference scales")
        seen: set[str] = set()
        for index, scale in enumerate(scales):
            name = f"dimension_extension.per_instance_reference_scales[{index}]"
            _exact_keys(scale, {"instance_id", "reference_bits", "reference_time", "median_interval_bits", "median_interval_time"}, name)
            instance = _text(scale["instance_id"], f"{name}.instance_id")
            if instance in seen:
                raise PaperResultIndexError("D6 instance IDs must be unique")
            seen.add(instance)
            _real(scale["reference_bits"], f"{name}.reference_bits", minimum=0)
            _real(scale["reference_time"], f"{name}.reference_time", minimum=0)
            for field in ("median_interval_bits", "median_interval_time"):
                interval = scale[field]
                if not isinstance(interval, list) or len(interval) != 2:
                    raise PaperResultIndexError(f"{name}.{field} must have two endpoints")
                lo = _real(interval[0], f"{name}.{field}[0]", minimum=0)
                hi = _real(interval[1], f"{name}.{field}[1]", minimum=0)
                if lo > hi:
                    raise PaperResultIndexError(f"{name}.{field} is reversed")
        return
    raise PaperResultIndexError("dimension_extension.kind is invalid")


def validate_implementation_register(register: Mapping[str, Any]) -> Mapping[str, Any]:
    _exact_keys(register, {"schema", "paper_commit", "implementations"}, "implementation register")
    if register["schema"] != IMPLEMENTATION_REGISTER_SCHEMA:
        raise PaperResultIndexError("unsupported implementation register schema")
    _commit(register["paper_commit"], "implementation register paper_commit")
    implementations = register["implementations"]
    if not isinstance(implementations, list) or not implementations:
        raise PaperResultIndexError("implementation register must not be empty")
    seen_ids: set[str] = set()
    seen_order: set[int] = set()
    for index, item in enumerate(implementations):
        name = f"implementations[{index}]"
        _exact_keys(item, IMPLEMENTATION_FIELDS, name)
        implementation_id = _text(item["implementation_id"], f"{name}.implementation_id")
        order = _integer(item["primary_order"], f"{name}.primary_order")
        if implementation_id in seen_ids or order in seen_order:
            raise PaperResultIndexError("implementation IDs and primary_order values must be unique")
        seen_ids.add(implementation_id)
        seen_order.add(order)
        for field in ("display_name", "cognition", "comparison_group", "information_rights", "training_access", "decomposition_policy"):
            _text(item[field], f"{name}.{field}")
        for field in ("source_digest", "model_digest", "adapter_digest", "runtime_digest"):
            _sha256(item[field], f"{name}.{field}")
        for field in ("supported_task_subsets", "supported_disturbances"):
            values = item[field]
            if not isinstance(values, list) or any(not isinstance(value, str) or not value.strip() for value in values) or len(values) != len(set(values)):
                raise PaperResultIndexError(f"{name}.{field} must be unique non-empty text")
    if [item["primary_order"] for item in implementations] != sorted(seen_order):
        raise PaperResultIndexError("implementations must be ordered by primary_order")
    return register


def validate_condition_register(
    register: Mapping[str, Any], *, implementation_register: Mapping[str, Any]
) -> Mapping[str, Any]:
    validate_implementation_register(implementation_register)
    _exact_keys(register, {"schema", "paper_commit", "conditions"}, "comparison condition register")
    if register["schema"] != CONDITION_REGISTER_SCHEMA:
        raise PaperResultIndexError("unsupported comparison condition register schema")
    if register["paper_commit"] != implementation_register["paper_commit"]:
        raise PaperResultIndexError("register paper commits differ")
    conditions = register["conditions"]
    if not isinstance(conditions, list) or not conditions:
        raise PaperResultIndexError("comparison condition register must not be empty")
    implementations = {item["implementation_id"] for item in implementation_register["implementations"]}
    seen: set[str] = set()
    for index, item in enumerate(conditions):
        name = f"conditions[{index}]"
        _exact_keys(item, CONDITION_FIELDS, name)
        condition_id = _text(item["condition_id"], f"{name}.condition_id")
        if condition_id in seen:
            raise PaperResultIndexError("condition IDs must be unique")
        seen.add(condition_id)
        if item["implementation_id"] not in implementations:
            raise PaperResultIndexError("condition references an unknown implementation")
        for field in ("prompt_digest", "model_digest", "tool_digest", "initial_memory_digest", "adapter_digest"):
            _sha256(item[field], f"{name}.{field}")
        for field in ("observation_rights", "budget_policy", "runtime_policy", "reset_policy"):
            _text(item[field], f"{name}.{field}")
    return register


def validate_result_row(
    row: Mapping[str, Any], *, implementation_register: Mapping[str, Any], condition_register: Mapping[str, Any]
) -> Mapping[str, Any]:
    validate_condition_register(condition_register, implementation_register=implementation_register)
    _exact_keys(row, ROW_FIELDS, "result row")
    if row["schema"] != ROW_SCHEMA:
        raise PaperResultIndexError("unsupported result row schema")
    identity = validate_identity(row["identity"])
    implementations = {item["implementation_id"] for item in implementation_register["implementations"]}
    conditions = {item["condition_id"]: item for item in condition_register["conditions"]}
    implementation_id = identity["implementation_id"]
    condition_id = identity["comparison_condition_id"]
    if implementation_id not in implementations or condition_id not in conditions:
        raise PaperResultIndexError("result identity references an unknown implementation or condition")
    if conditions[condition_id]["implementation_id"] != implementation_id:
        raise PaperResultIndexError("result implementation and comparison condition do not match")
    _validate_execution(row["execution"])
    _validate_support(row["support"])
    _validate_estimate(row["estimate"])
    _validate_evidence(row["evidence"], row["estimate"]["entry_state"])
    _validate_dimension_extension(row["dimension_extension"])
    return row


def result_key(row_or_identity: Mapping[str, Any]) -> tuple[str, ...]:
    identity = row_or_identity.get("identity", row_or_identity)
    validate_identity(identity)
    result: list[str] = []
    for field in IDENTITY_FIELDS:
        value = identity[field]
        if isinstance(value, Mapping):
            result.append(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False))
        else:
            result.append(str(value))
    return tuple(result)


def result_key_digest(row_or_identity: Mapping[str, Any]) -> str:
    identity = row_or_identity.get("identity", row_or_identity)
    validate_identity(identity)
    return canonical_digest({field: identity[field] for field in IDENTITY_FIELDS})


def validate_required_directory(
    directory: Mapping[str, Any], *, implementation_register: Mapping[str, Any], condition_register: Mapping[str, Any]
) -> Mapping[str, Any]:
    validate_condition_register(condition_register, implementation_register=implementation_register)
    _exact_keys(directory, {"schema", "paper_commit", "entries"}, "required result directory")
    if directory["schema"] != REQUIRED_DIRECTORY_SCHEMA:
        raise PaperResultIndexError("unsupported required result directory schema")
    if directory["paper_commit"] != implementation_register["paper_commit"]:
        raise PaperResultIndexError("required directory paper commit mismatch")
    entries = directory["entries"]
    if not isinstance(entries, list) or not entries:
        raise PaperResultIndexError("required result directory must not be empty")
    seen: set[tuple[str, ...]] = set()
    previous: tuple[str, ...] | None = None
    for index, entry in enumerate(entries):
        _exact_keys(entry, DIRECTORY_ENTRY_FIELDS, f"required entries[{index}]")
        identity = entry["identity"]
        template = {
            "schema": ROW_SCHEMA,
            "identity": identity,
            "execution": entry["execution"],
            "support": entry["support"],
            "estimate": {
                "entry_state": "missing", "status": "incomplete", "point": None,
                "lower": None, "upper": None, "interval_method": None,
                "reason": "missing_registered_evidence",
            },
            "evidence": {"event_ledger_digest": None, "input_ref": None, "report_ref": None, "source_scope": "none"},
            "dimension_extension": entry["dimension_extension"],
        }
        validate_result_row(template, implementation_register=implementation_register, condition_register=condition_register)
        key = result_key(identity)
        if key in seen:
            raise PaperResultIndexError("required result directory contains a duplicate key")
        if previous is not None and key <= previous:
            raise PaperResultIndexError("required result directory entries must be strictly key-sorted")
        seen.add(key)
        previous = key
        disposition = entry["disposition"]
        if disposition not in {"required", "unsupported", "structural_na"}:
            raise PaperResultIndexError("required directory disposition is invalid")
        _text(entry["reason"], f"required entries[{index}].reason")
        if type(entry["allow_range_display"]) is not bool:
            raise PaperResultIndexError("allow_range_display must be boolean")
        if disposition != "required" and entry["allow_range_display"]:
            raise PaperResultIndexError("only required entries may allow range display")
    return directory


def canonical_jsonl_bytes(rows: Iterable[Mapping[str, Any]]) -> bytes:
    row_list = list(rows)
    if not row_list:
        raise PaperResultIndexError("paper result index must not be empty")
    lines: list[bytes] = []
    seen: set[tuple[str, ...]] = set()
    for row in row_list:
        if not isinstance(row, Mapping):
            raise PaperResultIndexError("paper result index rows must be objects")
        key = result_key(row)
        if key in seen:
            raise PaperResultIndexError("paper result index contains a duplicate result key")
        seen.add(key)
        lines.append(canonical_json_bytes(row))
    return b"".join(lines)


def _safe_file(root: Path, relative: str, *, expected: Mapping[str, Any] | None = None) -> tuple[Path, bytes]:
    _text(relative, "relative path")
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts or relative in {".", ""}:
        raise PaperResultIndexError("artifact path must be root-relative without traversal")
    root = root.resolve(strict=True)
    candidate = root.joinpath(rel)
    if candidate.is_symlink() or not candidate.is_file():
        raise PaperResultIndexError(f"artifact is not a regular non-symlink file: {relative}")
    resolved = candidate.resolve(strict=True)
    if resolved != candidate.absolute() or root not in resolved.parents:
        raise PaperResultIndexError(f"artifact escapes declared root: {relative}")
    data = candidate.read_bytes()
    if expected is not None:
        _validate_file_ref_shape(expected, "artifact reference")
        if expected["path"] != relative or expected["size"] != len(data) or expected["sha256"] != hashlib.sha256(data).hexdigest():
            raise PaperResultIndexError(f"artifact reference does not match bytes: {relative}")
    return candidate, data


def _json_object(data: bytes, name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PaperResultIndexError(f"{name} is not strict JSON: {exc}") from exc
    if not isinstance(value, Mapping):
        raise PaperResultIndexError(f"{name} must contain a JSON object")
    _reject_nonfinite(value, name)
    return value


def reducer_run_links(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    runs = payload.get("runs")
    if not isinstance(runs, list) or not runs:
        raise PaperResultIndexError("reducer_payload must contain a non-empty runs list")
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, row in enumerate(runs):
        if not isinstance(row, Mapping):
            raise PaperResultIndexError(f"reducer_payload.runs[{index}] must be an object")
        instance_id = _text(row.get("instance_id"), f"reducer_payload.runs[{index}].instance_id")
        repeat_id = _text(row.get("repeat_id"), f"reducer_payload.runs[{index}].repeat_id")
        raw_run_id = row.get("run_id")
        run_id = _text(raw_run_id, f"reducer_payload.runs[{index}].run_id") if raw_run_id is not None else f"{instance_id}/{repeat_id}"
        if run_id in seen:
            raise PaperResultIndexError("reducer_payload run identities must be unique")
        seen.add(run_id)
        links.append({
            "run_id": run_id,
            "instance_id": instance_id,
            "repeat_id": repeat_id,
            "evidence_digest": _text(row.get("evidence_digest"), f"reducer_payload.runs[{index}].evidence_digest"),
        })
    return links


def _validate_registry_state(value: Any, name: str, *, available_fields: set[str]) -> str:
    if not isinstance(value, Mapping):
        raise PaperResultIndexError(f"{name} must be an object")
    status = value.get("status")
    if status == "available":
        _exact_keys(value, {"status", *available_fields}, name)
        return status
    if status in {"unavailable", "not_applicable"}:
        _exact_keys(value, {"status", "reason"}, name)
        _text(value["reason"], f"{name}.reason")
        return status
    raise PaperResultIndexError(f"{name}.status is invalid")


def _validate_id_list(value: Any, name: str, *, allowed: set[str]) -> None:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise PaperResultIndexError(f"{name} must be a list of non-empty identifiers")
    if value != sorted(set(value)):
        raise PaperResultIndexError(f"{name} must be unique and sorted")
    if not set(value) <= allowed:
        raise PaperResultIndexError(f"{name} refers to identities outside the frozen reducer payload")


def validate_statistical_input(
    value: Mapping[str, Any], *, binding: Mapping[str, Any]
) -> Mapping[str, Any]:
    _exact_keys(value, STATISTICAL_INPUT_FIELDS, "statistical input")
    if value["schema"] != STATISTICAL_INPUT_SCHEMA:
        raise PaperResultIndexError("unsupported statistical input schema")
    if value["evidence_scope"] != "paper_result_candidate":
        raise PaperResultIndexError("statistical input is not explicitly scoped as paper_result_candidate")
    expected = {
        "score_id": binding["score_id"],
        "implementation_id": binding["implementation_id"],
        "comparison_condition_id": binding["comparison_condition_id"],
        "outcome_id": binding["outcome_id"],
        "estimator_version": binding["estimator_version"],
        "result_key_digest": binding["result_key_digest"],
    }
    if any(value[field] != expected_value for field, expected_value in expected.items()):
        raise PaperResultIndexError("statistical input full result identity mismatch")
    if value["reducer_method"] != reducer_method_for_score(value["score_id"]):
        raise PaperResultIndexError("statistical input reducer method does not match score_id")
    if value["reducer_implementation_digest"] != reducer_implementation_digest(value["score_id"]):
        raise PaperResultIndexError("statistical input reducer implementation digest mismatch")
    payload_digest = reducer_payload_digest(value["reducer_payload"])
    if value["reducer_payload_digest"] != payload_digest:
        raise PaperResultIndexError("statistical input reducer_payload_digest mismatch")
    _sha256(value["reducer_input_digest"], "statistical input reducer_input_digest")
    _validate_file_ref_shape(value["event_ledger_ref"], "statistical input event_ledger_ref")
    if value["event_ledger_ref"] != binding["event_ledger_ref"]:
        raise PaperResultIndexError("statistical input event-ledger reference mismatch")
    digest = statistical_input_digest(value)
    if value["input_digest"] != digest or binding["expected_input_digest"] != digest:
        raise PaperResultIndexError("statistical input digest identity mismatch")
    return value


def validate_statistical_event_ledger(
    ledger: Mapping[str, Any], *, binding: Mapping[str, Any], input_object: Mapping[str, Any]
) -> Mapping[str, Any]:
    _exact_keys(ledger, EVENT_LEDGER_FIELDS, "statistical event ledger")
    if ledger["schema"] != STATISTICAL_EVENT_LEDGER_SCHEMA:
        raise PaperResultIndexError("unsupported statistical event-ledger schema")
    expected = {
        "paper_commit": input_object["paper_commit"],
        "score_id": binding["score_id"],
        "implementation_id": binding["implementation_id"],
        "comparison_condition_id": binding["comparison_condition_id"],
        "outcome_id": binding["outcome_id"],
        "estimator_version": binding["estimator_version"],
        "result_key_digest": binding["result_key_digest"],
        "evidence_scope": input_object["evidence_scope"],
        "reducer_method": input_object["reducer_method"],
        "reducer_implementation_digest": input_object["reducer_implementation_digest"],
        "reducer_payload_digest": input_object["reducer_payload_digest"],
    }
    if any(ledger[field] != expected_value for field, expected_value in expected.items()):
        raise PaperResultIndexError("statistical event ledger identity/input linkage mismatch")
    expected_links = reducer_run_links(input_object["reducer_payload"])
    if ledger["run_links"] != expected_links:
        raise PaperResultIndexError("statistical event ledger run links do not match reducer payload")
    registry = ledger["support_registry"]
    _exact_keys(registry, {"pairing", "common_support", "events"}, "statistical event ledger support_registry")
    instance_ids = {item["instance_id"] for item in expected_links}
    run_ids = {item["run_id"] for item in expected_links}
    pairing = registry["pairing"]
    if _validate_registry_state(
        pairing,
        "statistical event ledger support_registry.pairing",
        available_fields={"paired_instance_ids"},
    ) == "available":
        _validate_id_list(pairing["paired_instance_ids"], "paired_instance_ids", allowed=instance_ids)
    common = registry["common_support"]
    if _validate_registry_state(
        common,
        "statistical event ledger support_registry.common_support",
        available_fields={"common_support_instance_ids"},
    ) == "available":
        _validate_id_list(common["common_support_instance_ids"], "common_support_instance_ids", allowed=instance_ids)
        if pairing["status"] == "available" and not set(common["common_support_instance_ids"]) <= set(pairing["paired_instance_ids"]):
            raise PaperResultIndexError("common support must be a subset of paired support")
    events = registry["events"]
    if _validate_registry_state(
        events,
        "statistical event ledger support_registry.events",
        available_fields={"event_run_ids", "competing_event_run_ids", "censoring_run_ids"},
    ) == "available":
        for field in ("event_run_ids", "competing_event_run_ids", "censoring_run_ids"):
            _validate_id_list(events[field], field, allowed=run_ids)
        event_sets = [set(events[field]) for field in ("event_run_ids", "competing_event_run_ids", "censoring_run_ids")]
        if any(event_sets[i] & event_sets[j] for i in range(3) for j in range(i + 1, 3)):
            raise PaperResultIndexError("event, competing-event and censoring registries must be disjoint")
    return ledger


def _validate_statistical_report(
    report: Mapping[str, Any], *, binding: Mapping[str, Any], paper_commit: str,
    scorebook_digest: str, input_object: Mapping[str, Any], ledger_object: Mapping[str, Any]
) -> None:
    _exact_keys(report, STATISTICAL_REPORT_FIELDS, "statistical report")
    if report["schema"] != STATISTICAL_REPORT_SCHEMA:
        raise PaperResultIndexError("diagnostic/results-pipeline data is not a statistical paper report")
    if report.get("evidence_scope") != "paper_result_candidate":
        raise PaperResultIndexError("diagnostic-only or synthetic evidence cannot populate a paper row")
    if report["formal_gates"] != FORMAL_GATES_FALSE:
        raise PaperResultIndexError("statistical report formal gates must remain false")
    expected = {
        "paper_commit": paper_commit,
        "scorebook_digest": scorebook_digest,
        "input_digest": binding["expected_input_digest"],
        "implementation_id": binding["implementation_id"],
        "comparison_condition_id": binding["comparison_condition_id"],
        "result_key_digest": binding["result_key_digest"],
        "score_id": binding["score_id"],
        "outcome_id": binding["outcome_id"],
        "estimator_version": binding["estimator_version"],
    }
    if any(report[field] != value for field, value in expected.items()):
        raise PaperResultIndexError("statistical report identity/provenance mismatch")
    if report["reducer_method"] != input_object["reducer_method"]:
        raise PaperResultIndexError("statistical report reducer method mismatch")
    if report["reducer_implementation_digest"] != input_object["reducer_implementation_digest"]:
        raise PaperResultIndexError("statistical report reducer implementation mismatch")
    if report["reducer_payload_digest"] != input_object["reducer_payload_digest"]:
        raise PaperResultIndexError("statistical report reducer payload mismatch")
    if report["reducer_input_digest"] != input_object["reducer_input_digest"]:
        raise PaperResultIndexError("statistical report reducer input digest mismatch")
    if not isinstance(report["reducer_output"], Mapping):
        raise PaperResultIndexError("statistical report reducer_output must be an object")
    reducer_output = decode_reducer_output(report["reducer_output"])
    reducer_expected = {
        "score_id": binding["score_id"],
        "paper_commit": paper_commit,
        "scorebook_digest": scorebook_digest,
        "input_digest": report["reducer_input_digest"],
    }
    if any(reducer_output.get(field) != value for field, value in reducer_expected.items()):
        raise PaperResultIndexError("stored reducer output identity mismatch")
    _validate_support(report["support"])
    registry = ledger_object["support_registry"]
    registry_fields = {
        "paired_instances": (registry["pairing"], "paired_instance_ids"),
        "common_support_instances": (registry["common_support"], "common_support_instance_ids"),
        "event_count": (registry["events"], "event_run_ids"),
        "competing_event_count": (registry["events"], "competing_event_run_ids"),
        "censoring_count": (registry["events"], "censoring_run_ids"),
    }
    for support_field, (source, ids_field) in registry_fields.items():
        expected_count = (
            {"status": "observed", "value": len(source[ids_field])}
            if source["status"] == "available"
            else {"status": source["status"], "reason": source["reason"]}
        )
        if report["support"][support_field] != expected_count:
            raise PaperResultIndexError(f"statistical report {support_field} disagrees with authoritative ledger registry")
    raw = report["estimate"]
    _exact_keys(raw, {"status", "point", "lower", "upper", "interval_method", "reason"}, "statistical report estimate")
    status = raw["status"]
    _text(raw["reason"], "statistical report estimate.reason")
    if reducer_output.get("status") != status:
        raise PaperResultIndexError("stored reducer output status disagrees with projected estimate")
    if status == "valid":
        estimate = {"entry_state": "observed", **raw}
    elif status == "range":
        estimate = {"entry_state": "observed_range", **raw}
    elif status == "na":
        estimate = {"entry_state": "not_estimable", **raw}
    elif status in {"unstable", "incomplete"}:
        estimate = {
            "entry_state": "incomplete", "status": "incomplete", "point": None,
            "lower": None, "upper": None, "interval_method": None,
            "reason": raw["reason"] if raw["reason"].startswith("incomplete_") else f"incomplete_{status}",
        }
    else:
        raise PaperResultIndexError("statistical report status is invalid")
    _validate_estimate(estimate)
    _sha256(report["event_ledger_digest"], "statistical report event_ledger_digest")


def validate_statistical_report_binding(
    binding_manifest: Mapping[str, Any], *, root: str | Path,
    implementation_register: Mapping[str, Any], condition_register: Mapping[str, Any],
    required_directory: Mapping[str, Any]
) -> dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]]:
    validate_required_directory(
        required_directory,
        implementation_register=implementation_register,
        condition_register=condition_register,
    )
    _exact_keys(binding_manifest, {"schema", "paper_commit", "scorebook_digest", "bindings"}, "statistical binding manifest")
    if binding_manifest["schema"] != STATISTICAL_BINDING_SCHEMA:
        raise PaperResultIndexError("unsupported statistical binding schema")
    if binding_manifest["paper_commit"] != required_directory["paper_commit"]:
        raise PaperResultIndexError("statistical binding paper commit mismatch")
    scorebook_digest = _sha256(binding_manifest["scorebook_digest"], "statistical binding scorebook_digest")
    bindings = binding_manifest["bindings"]
    if not isinstance(bindings, list):
        raise PaperResultIndexError("statistical bindings must be a list")
    directory_by_digest = {result_key_digest(entry["identity"]): entry for entry in required_directory["entries"]}
    seen: set[str] = set()
    seen_reports: set[tuple[str, int, str]] = set()
    result: dict[str, tuple[Mapping[str, Any], Mapping[str, Any]]] = {}
    root_path = Path(root)
    if root_path.is_symlink() or not root_path.is_dir():
        raise PaperResultIndexError("statistical binding root must be an existing non-symlink directory")
    for index, binding in enumerate(bindings):
        _exact_keys(binding, BINDING_ENTRY_FIELDS, f"bindings[{index}]")
        key_digest = _sha256(binding["result_key_digest"], f"bindings[{index}].result_key_digest")
        validate_identity(binding["identity"])
        if key_digest != result_key_digest(binding["identity"]):
            raise PaperResultIndexError("binding result-key digest is caller-authored or stale")
        if key_digest not in directory_by_digest or directory_by_digest[key_digest]["identity"] != binding["identity"]:
            raise PaperResultIndexError("binding result key is not registered")
        if key_digest in seen:
            raise PaperResultIndexError("multiple reports bind the same result key")
        seen.add(key_digest)
        identity = binding["identity"]
        for field in ("implementation_id", "comparison_condition_id", "outcome_id", "estimator_version"):
            if binding[field] != identity[field]:
                raise PaperResultIndexError(f"binding {field} disagrees with result key")
        _text(binding["score_id"], f"bindings[{index}].score_id")
        _sha256(binding["expected_input_digest"], f"bindings[{index}].expected_input_digest")
        for field in ("report_ref", "input_ref", "event_ledger_ref"):
            _validate_file_ref_shape(binding[field], f"bindings[{index}].{field}")
        report_identity = (
            binding["report_ref"]["path"],
            binding["report_ref"]["size"],
            binding["report_ref"]["sha256"],
        )
        if report_identity in seen_reports:
            raise PaperResultIndexError("one statistical report cannot bind multiple result keys")
        seen_reports.add(report_identity)
        _, report_data = _safe_file(root_path, binding["report_ref"]["path"], expected=binding["report_ref"])
        _, input_data = _safe_file(root_path, binding["input_ref"]["path"], expected=binding["input_ref"])
        _, ledger_data = _safe_file(root_path, binding["event_ledger_ref"]["path"], expected=binding["event_ledger_ref"])
        report = _json_object(report_data, "statistical report")
        input_object = _json_object(input_data, "statistical input")
        ledger_object = _json_object(ledger_data, "statistical event ledger")
        if canonical_json_bytes(input_object) != input_data:
            raise PaperResultIndexError("statistical input is not canonical JSON")
        if canonical_json_bytes(ledger_object) != ledger_data:
            raise PaperResultIndexError("statistical event ledger is not canonical JSON")
        if input_object.get("paper_commit") != binding_manifest["paper_commit"]:
            raise PaperResultIndexError("statistical input paper commit mismatch")
        validate_statistical_input(input_object, binding=binding)
        validate_statistical_event_ledger(ledger_object, binding=binding, input_object=input_object)
        _validate_statistical_report(
            report,
            binding=binding,
            paper_commit=binding_manifest["paper_commit"],
            scorebook_digest=scorebook_digest,
            input_object=input_object,
            ledger_object=ledger_object,
        )
        if report["event_ledger_digest"] != hashlib.sha256(ledger_data).hexdigest():
            raise PaperResultIndexError("event-ledger digest does not match bound bytes")
        result[key_digest] = (binding, report)
    return result


def build_result_rows(
    *, implementation_register: Mapping[str, Any], condition_register: Mapping[str, Any],
    required_directory: Mapping[str, Any], statistical_report_binding: Mapping[str, Any], root: str | Path
) -> tuple[Mapping[str, Any], ...]:
    bound = validate_statistical_report_binding(
        statistical_report_binding,
        root=root,
        implementation_register=implementation_register,
        condition_register=condition_register,
        required_directory=required_directory,
    )
    rows: list[Mapping[str, Any]] = []
    for entry in required_directory["entries"]:
        key_digest = result_key_digest(entry["identity"])
        evidence = {"event_ledger_digest": None, "input_ref": None, "report_ref": None, "source_scope": "none"}
        bound_item = bound.get(key_digest)
        if bound_item is None:
            disposition = entry["disposition"]
            if disposition == "required":
                state, status, reason = "missing", "incomplete", "missing_registered_evidence"
            elif disposition == "unsupported":
                state, status, reason = "unsupported", "na", entry["reason"]
            else:
                state, status, reason = "structural_na", "na", entry["reason"]
            estimate = {
                "entry_state": state, "status": status, "point": None, "lower": None,
                "upper": None, "interval_method": None, "reason": reason,
            }
        else:
            if entry["disposition"] != "required":
                raise PaperResultIndexError("evidence cannot populate unsupported or structural-N/A keys")
            binding, report = bound_item
            if report["support"] != entry["support"]:
                raise PaperResultIndexError("report support differs from frozen directory support")
            raw = report["estimate"]
            if raw["status"] == "valid":
                estimate = {"entry_state": "observed", **raw}
            elif raw["status"] == "range":
                if not entry["allow_range_display"]:
                    raise PaperResultIndexError("observed_range is not allowed for this registered result key")
                estimate = {"entry_state": "observed_range", **raw}
            elif raw["status"] == "na":
                estimate = {"entry_state": "not_estimable", **raw}
            else:
                estimate = {
                    "entry_state": "incomplete", "status": "incomplete", "point": None,
                    "lower": None, "upper": None, "interval_method": None,
                    "reason": raw["reason"] if raw["reason"].startswith("incomplete_") else f"incomplete_{raw['status']}",
                }
            evidence = {
                "event_ledger_digest": report["event_ledger_digest"],
                "input_ref": dict(binding["input_ref"]),
                "report_ref": dict(binding["report_ref"]),
                "source_scope": "paper_result_candidate",
            }
        row = {
            "schema": ROW_SCHEMA,
            "identity": entry["identity"],
            "execution": entry["execution"],
            "support": entry["support"],
            "estimate": estimate,
            "evidence": evidence,
            "dimension_extension": entry["dimension_extension"],
        }
        validate_result_row(row, implementation_register=implementation_register, condition_register=condition_register)
        rows.append(row)
    return tuple(rows)


def _write_new(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o644)
    except FileExistsError as exc:
        raise PaperResultIndexError(f"refusing to overwrite existing file: {path}") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _safe_destination(root: str | Path, destination: str | Path) -> Path:
    raw_root = Path(root)
    if raw_root.is_symlink() or not raw_root.is_dir():
        raise PaperResultIndexError("output root must be an existing non-symlink directory")
    root_path = raw_root.resolve(strict=True)
    candidate = Path(destination)
    if not candidate.is_absolute():
        candidate = root_path / candidate
    if candidate.exists() and candidate.is_symlink():
        raise PaperResultIndexError("output destination must not be a symlink")
    try:
        parent = candidate.parent.resolve(strict=True)
    except FileNotFoundError as exc:
        raise PaperResultIndexError("output parent must already exist") from exc
    if parent != root_path and root_path not in parent.parents:
        raise PaperResultIndexError("output destination escapes declared root")
    if candidate.parent.absolute() != parent:
        raise PaperResultIndexError("output destination has a symlink parent")
    return candidate


def _validate_row_disposition(row: Mapping[str, Any], entry: Mapping[str, Any]) -> None:
    state = row["estimate"]["entry_state"]
    allowed_states = {
        "required": {"missing", "incomplete", "not_estimable", "observed", "observed_range"},
        "unsupported": {"unsupported"},
        "structural_na": {"structural_na"},
    }[entry["disposition"]]
    if state not in allowed_states:
        raise PaperResultIndexError("row estimate state disagrees with required-directory disposition")
    if state == "observed_range" and not entry["allow_range_display"]:
        raise PaperResultIndexError("observed_range is not allowed for this registered result key")


def write_result_index(
    rows: Sequence[Mapping[str, Any]], destination: str | Path, *,
    implementation_register: Mapping[str, Any], condition_register: Mapping[str, Any],
    required_directory: Mapping[str, Any], root: str | Path, no_overwrite: bool = True
) -> Mapping[str, Any]:
    if no_overwrite is not True:
        raise PaperResultIndexError("paper result index writes are always no-overwrite")
    validate_required_directory(
        required_directory,
        implementation_register=implementation_register,
        condition_register=condition_register,
    )
    if len(rows) != len(required_directory["entries"]):
        raise PaperResultIndexError("result index row count differs from required directory")
    for row, entry in zip(rows, required_directory["entries"], strict=True):
        validate_result_row(row, implementation_register=implementation_register, condition_register=condition_register)
        _validate_row_disposition(row, entry)
    required_keys = [result_key(entry["identity"]) for entry in required_directory["entries"]]
    if [result_key(row) for row in rows] != required_keys:
        raise PaperResultIndexError("result index keys/order differ from required directory")
    data = canonical_jsonl_bytes(rows)
    path = _safe_destination(root, destination)
    manifest_path = Path(str(path) + ".manifest.json")
    if path.exists() or manifest_path.exists():
        raise PaperResultIndexError("refusing to overwrite index or manifest")
    root_path = Path(root).resolve(strict=True)
    try:
        index_relative = path.resolve(strict=False).relative_to(root_path).as_posix()
    except ValueError as exc:
        raise PaperResultIndexError("output destination escapes declared root") from exc
    manifest = {
        "schema": INDEX_MANIFEST_SCHEMA,
        "paper_commit": required_directory["paper_commit"],
        "index": {"path": index_relative, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()},
        "row_count": len(rows),
        "required_directory_digest": canonical_digest(required_directory),
        "formal_gates": dict(FORMAL_GATES_FALSE),
    }
    manifest_data = canonical_json_bytes(manifest)
    created: list[Path] = []
    try:
        _write_new(path, data)
        created.append(path)
        _write_new(manifest_path, manifest_data)
        created.append(manifest_path)
    except BaseException:
        for created_path in reversed(created):
            try:
                created_path.unlink()
            except FileNotFoundError:
                pass
        raise
    return manifest


def read_result_index(path: str | Path) -> tuple[Mapping[str, Any], ...]:
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise PaperResultIndexError("result index must be a regular non-symlink file")
    rows: list[Mapping[str, Any]] = []
    try:
        text = source.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PaperResultIndexError("result index is not UTF-8") from exc
    if not text.endswith("\n"):
        raise PaperResultIndexError("result index must end with a newline")
    for number, line in enumerate(text.splitlines(), start=1):
        if not line:
            raise PaperResultIndexError(f"result index line {number} is empty")
        try:
            row = json.loads(line, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        except (json.JSONDecodeError, ValueError) as exc:
            raise PaperResultIndexError(f"result index line {number} is invalid JSON") from exc
        if not isinstance(row, Mapping):
            raise PaperResultIndexError(f"result index line {number} is not an object")
        if canonical_json_bytes(row).decode("utf-8").rstrip("\n") != line:
            raise PaperResultIndexError(f"result index line {number} is not canonical")
        rows.append(row)
    if not rows:
        raise PaperResultIndexError("result index is empty")
    return tuple(rows)


def verify_result_index(
    path: str | Path, manifest_path: str | Path, *, root: str | Path,
    required_directory: Mapping[str, Any], implementation_register: Mapping[str, Any],
    condition_register: Mapping[str, Any]
) -> tuple[Mapping[str, Any], ...]:
    root_path = Path(root).resolve(strict=True)
    index_path = Path(path)
    manifest_file = Path(manifest_path)
    for candidate in (index_path, manifest_file):
        resolved = candidate.resolve(strict=True)
        if candidate.is_symlink() or not candidate.is_file() or candidate.absolute() != resolved or root_path not in resolved.parents:
            raise PaperResultIndexError("index and manifest must be regular files inside root")
    manifest_data = manifest_file.read_bytes()
    manifest = _json_object(manifest_data, "result index manifest")
    if canonical_json_bytes(manifest) != manifest_data:
        raise PaperResultIndexError("result index manifest is not canonical JSON")
    _exact_keys(manifest, {"schema", "paper_commit", "index", "row_count", "required_directory_digest", "formal_gates"}, "result index manifest")
    if manifest["schema"] != INDEX_MANIFEST_SCHEMA or manifest["paper_commit"] != required_directory["paper_commit"]:
        raise PaperResultIndexError("result index manifest identity mismatch")
    _exact_keys(manifest["index"], {"path", "size", "sha256"}, "result index manifest.index")
    data = index_path.read_bytes()
    try:
        index_relative = index_path.resolve(strict=False).relative_to(root_path).as_posix()
    except ValueError as exc:
        raise PaperResultIndexError("index escapes declared root") from exc
    expected_index = {
        "path": index_relative,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    if manifest["index"] != expected_index:
        raise PaperResultIndexError("result index bytes do not match manifest")
    if manifest["required_directory_digest"] != canonical_digest(required_directory):
        raise PaperResultIndexError("required directory digest mismatch")
    if manifest["formal_gates"] != FORMAL_GATES_FALSE:
        raise PaperResultIndexError("paper result index formal gates must remain false")
    rows = read_result_index(index_path)
    if manifest["row_count"] != len(rows):
        raise PaperResultIndexError("result index row count mismatch")
    validate_required_directory(
        required_directory,
        implementation_register=implementation_register,
        condition_register=condition_register,
    )
    if [result_key(row) for row in rows] != [result_key(entry["identity"]) for entry in required_directory["entries"]]:
        raise PaperResultIndexError("result index completeness/order check failed")
    for row, entry in zip(rows, required_directory["entries"], strict=True):
        validate_result_row(row, implementation_register=implementation_register, condition_register=condition_register)
        _validate_row_disposition(row, entry)
    return rows


def validate_manuscript_cell_map(
    cell_map: Mapping[str, Any], *, required_directory: Mapping[str, Any]
) -> Mapping[str, Any]:
    _exact_keys(cell_map, {"schema", "paper_commit", "column_order", "cells"}, "manuscript cell map")
    if cell_map["schema"] != CELL_MAP_SCHEMA or cell_map["paper_commit"] != required_directory["paper_commit"]:
        raise PaperResultIndexError("manuscript cell map identity mismatch")
    columns = cell_map["column_order"]
    if not isinstance(columns, list) or not columns or any(not isinstance(value, str) or not value.strip() for value in columns) or len(columns) != len(set(columns)):
        raise PaperResultIndexError("manuscript column_order must be unique non-empty text")
    cells = cell_map["cells"]
    if not isinstance(cells, list) or not cells:
        raise PaperResultIndexError("manuscript cells must not be empty")
    seen_cells: set[str] = set()
    rows: dict[str, list[str]] = {}
    known_fields = set(IDENTITY_FIELDS)
    for index, cell in enumerate(cells):
        _exact_keys(cell, {"cell_id", "row_id", "column_id", "selector", "display_transform", "allow_range"}, f"cells[{index}]")
        cell_id = _text(cell["cell_id"], f"cells[{index}].cell_id")
        row_id = _text(cell["row_id"], f"cells[{index}].row_id")
        column_id = _text(cell["column_id"], f"cells[{index}].column_id")
        if cell_id in seen_cells or column_id not in columns:
            raise PaperResultIndexError("manuscript cell ID is duplicate or column is unknown")
        seen_cells.add(cell_id)
        rows.setdefault(row_id, []).append(column_id)
        selector = cell["selector"]
        if not isinstance(selector, Mapping) or not selector or set(selector) - known_fields:
            raise PaperResultIndexError("manuscript selector contains unknown or no fields")
        required_selector = {"implementation_id", "comparison_condition_id", "outcome_id", "estimator_version"}
        if not required_selector.issubset(selector):
            raise PaperResultIndexError("manuscript selector is too broad")
        for field, value in selector.items():
            if field in TAGGED_IDENTITY_FIELDS:
                _validate_tagged(value, f"cells[{index}].selector.{field}")
            else:
                _text(value, f"cells[{index}].selector.{field}")
        if cell["display_transform"] not in {"identity", "percent_1dp", "interval_1dp", "na_label"}:
            raise PaperResultIndexError("unregistered manuscript display transform")
        if type(cell["allow_range"]) is not bool:
            raise PaperResultIndexError("cell allow_range must be boolean")
    if any(order != columns for order in rows.values()):
        raise PaperResultIndexError("manuscript cell column order drift")
    return cell_map


def resolve_manuscript_cell(cell: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    selector = cell.get("selector")
    if not isinstance(selector, Mapping):
        raise PaperResultIndexError("manuscript cell selector is malformed")
    matches = [row for row in rows if all(row.get("identity", {}).get(field) == value for field, value in selector.items())]
    if len(matches) != 1:
        raise PaperResultIndexError(f"manuscript selector resolved to {len(matches)} rows")
    row = matches[0]
    state = row["estimate"]["entry_state"]
    if state == "observed_range" and cell.get("allow_range") is not True:
        raise PaperResultIndexError("manuscript cell does not permit identification ranges")
    return row


def verify_manuscript_cells(
    cell_map: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], *, required_directory: Mapping[str, Any]
) -> tuple[Mapping[str, Any], ...]:
    validate_manuscript_cell_map(cell_map, required_directory=required_directory)
    return tuple(resolve_manuscript_cell(cell, rows) for cell in cell_map["cells"])


def _load_json(path: str | Path, name: str) -> Mapping[str, Any]:
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise PaperResultIndexError(f"{name} must be a regular non-symlink file")
    return _json_object(source.read_bytes(), name)


def _cli_build_index(args: argparse.Namespace) -> Mapping[str, Any]:
    implementations = _load_json(args.implementations, "implementation register")
    conditions = _load_json(args.conditions, "comparison condition register")
    directory = _load_json(args.directory, "required result directory")
    bindings = _load_json(args.bindings, "statistical binding manifest")
    rows = build_result_rows(
        implementation_register=implementations,
        condition_register=conditions,
        required_directory=directory,
        statistical_report_binding=bindings,
        root=args.root,
    )
    return write_result_index(
        rows,
        args.output,
        implementation_register=implementations,
        condition_register=conditions,
        required_directory=directory,
        root=args.root,
    )


def _cli_verify_index(args: argparse.Namespace) -> Mapping[str, Any]:
    implementations = _load_json(args.implementations, "implementation register")
    conditions = _load_json(args.conditions, "comparison condition register")
    directory = _load_json(args.directory, "required result directory")
    rows = verify_result_index(
        args.index,
        args.manifest,
        root=args.root,
        required_directory=directory,
        implementation_register=implementations,
        condition_register=conditions,
    )
    return {"verified": True, "rows": len(rows), "formal_gates": dict(FORMAL_GATES_FALSE)}


def _cli_verify_cells(args: argparse.Namespace) -> Mapping[str, Any]:
    directory = _load_json(args.directory, "required result directory")
    cell_map = _load_json(args.cell_map, "manuscript cell map")
    rows = read_result_index(args.index)
    resolved = verify_manuscript_cells(cell_map, rows, required_directory=directory)
    return {"verified": True, "cells": len(resolved), "formal_gates": dict(FORMAL_GATES_FALSE)}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build-index")
    build.add_argument("--root", required=True)
    build.add_argument("--implementations", required=True)
    build.add_argument("--conditions", required=True)
    build.add_argument("--directory", required=True)
    build.add_argument("--bindings", required=True)
    build.add_argument("--output", required=True)
    build.set_defaults(handler=_cli_build_index)
    verify = sub.add_parser("verify-index")
    verify.add_argument("--root", required=True)
    verify.add_argument("--implementations", required=True)
    verify.add_argument("--conditions", required=True)
    verify.add_argument("--directory", required=True)
    verify.add_argument("--index", required=True)
    verify.add_argument("--manifest", required=True)
    verify.set_defaults(handler=_cli_verify_index)
    cells = sub.add_parser("verify-cells")
    cells.add_argument("--directory", required=True)
    cells.add_argument("--index", required=True)
    cells.add_argument("--cell-map", required=True)
    cells.set_defaults(handler=_cli_verify_cells)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        result = args.handler(args)
        sys.stdout.buffer.write(canonical_json_bytes(result))
        return 0
    except PaperResultIndexError as exc:
        print(f"paper-result-index: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
