"""Authority-backed producer of heterocoord.statistical-report.v1 artifacts.

This module assembles paper statistical reports by feeding frozen
evaluator-owned inputs to the authoritative D1--D6 reducers and packaging
their outputs.  It never re-implements a formula, never grants a formal gate,
and never consumes ``metrics.csv``, ``results_pipeline`` output, caller
self-reported point/interval values, or synthetic/diagnostic-scoped inputs.

Adjudications recorded by the coordinator (verified against the code, both
perspectives retained):

(a) Scorebook digest dual convention.  The release-manifest layer computes
    ``sha256(canonical_json_bytes(scorebook).rstrip(b"\\n"))``
    (paper_release_manifest_v1._validate_dependency_semantics, the L229
    expression), while the reducers embed ``default_scorebook_v3().digest()``,
    which hashes canonical JSON *without* a trailing newline
    (scorebook_v3.ScorebookV3.digest).  Because ``canonical_json_bytes`` is
    exactly ``json.dumps(..., sort_keys=True, separators=(",", ":"),
    ensure_ascii=False)`` plus one ``"\\n"``, the two conventions coincide on
    any canonical scorebook; this is asserted on the default scorebook by
    ``test_scorebook_digest_conventions_agree``.  This producer writes the
    release-side digest into every report and binding and *fails closed* if
    the recomputed release-side digest differs from the authoritative
    reducer-side digest instead of silently picking one.

(b) Reducer ``status="na"`` handling.  Verified directly in
    paper_result_index_v1._validate_statistical_report (L671--685): the raw
    estimate status is accepted only for ``valid``, ``range``, ``unstable``
    and ``incomplete``; every other value, including ``na``, hits the
    ``else: raise`` branch ("statistical report status is invalid").  There is
    no na->na mapping downstream.  Therefore this producer fail-closes on
    reducer ``status="na"``: NA semantics belong to the required-result
    directory's ``unsupported``/``structural_na`` dispositions and must never
    be fabricated as a report.  Accepted statuses map verbatim:
    valid->valid (entry_state observed), range->range (observed_range),
    unstable/incomplete->incomplete after index-layer normalization.

(c) Support mapping (13 validator fields, "prefer omission to invention":
    a count field is written as 0 exactly when the authority reducer output
    publishes no authoritative count of that kind for the dimension -- the 0
    records "no authoritative number exists to copy", not an observed zero;
    every number below is read off the authority reducer output or the frozen
    request roster itself, never invented):

    D1 (organization_report): coverage table summed over feasibility classes
      and registered cells: generated_base_instances=sum(planned),
      infrastructure_valid_base_instances=sum(infrastructure_valid),
      metric_eligible_base_instances=sum(retained_instances),
      repeated_trajectories=scheduled_runs.  D1 runs do carry evaluator event
      logs (OrganizationEventV3: candidate/commit/declaration/dispatch/
      safe_stop) that the first-commit scorer consumes, but the reducer
      output publishes no event, competing-event or censoring counts (and the
      roster has no paired-block or common-support mechanism), so
      paired_instances=common_support_instances=event_count=
      competing_event_count=censoring_count=0: no authoritative number exists
      to copy.
      planned_coverage_fraction=infra/generated,
      observed_coverage_fraction=eligible/generated, numerator=eligible,
      denominator=generated.

    D2/D4 (analyze_dimension): base instances are the frozen three-repeat
      roster groups: generated=#instances in the request run directory,
      infrastructure_valid=#instances with no run status in
      UNKNOWN_RUN_STATUSES, metric_eligible=#instances whose repeats are all
      ``observed``, paired_instances=#instances carrying pair-invalid runs,
      repeated_trajectories=scheduled_runs.  The D2/D4 reducer output
      publishes no common-support or event/censoring counts -> those fields
      are 0 (absent, not observed).  Coverage fractions and
      numerator/denominator follow the D1 convention (infra/generated,
      eligible/generated).

    D3 (d3_relation_report): the reducer exposes no infrastructure/metric
      split, only known-vs-unknown relation values, so
      infrastructure_valid=metric_eligible=#instances whose repeats have no
      unknown (None) relation value, generated=#instances,
      repeated_trajectories=scheduled_runs; the D3 reducer output publishes
      no paired/common/event counts -> 0 (absent, not observed); same
      coverage-fraction convention.

    D5/D6 (analyze_dimension + FixedCatalogV3.analyze): generated=#distinct
      instance identities in the frozen catalog, infrastructure_valid=
      metric_eligible=#instances with no unknown value across their three
      catalog repeats, repeated_trajectories=scheduled_runs (equal to the
      catalog's scheduled_attempts); the D5/D6 reducer output publishes no
      paired/common/event counts -> 0 (absent, not observed); same
      coverage-fraction convention.

Fail-closed contract: every check below runs before a single output byte is
written; a rejection leaves the tree untouched.  Writes are O_EXCL
no-overwrite, canonical JSON, fsync'd, with rollback of anything already
created if a later write fails.
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
from typing import Any, Mapping, Sequence

from .analysis_v3 import UNKNOWN_RUN_STATUSES
from .paper_result_index_v1 import (
    BINDING_ENTRY_FIELDS,
    FORMAL_GATES_FALSE,
    STATISTICAL_BINDING_SCHEMA,
    STATISTICAL_REPORT_FIELDS,
    STATISTICAL_REPORT_SCHEMA,
    PaperResultIndexError,
    canonical_json_bytes,
    result_key_digest,
    validate_condition_register,
    validate_identity,
    validate_implementation_register,
    validate_required_directory,
    validate_statistical_report_binding,
)
from .scorebook_v3 import PAPER_COMMIT, default_scorebook_v3


REQUEST_SCHEMA = "heterocoord.statistical-report-request.v1"
PRODUCE_RESULT_SCHEMA = "heterocoord.statistical-report-produce-result.v1"
REPORT_SET_SCHEMA = "heterocoord.statistical-report-set.v1"
SCORE_IDS = frozenset(f"D{i}" for i in range(1, 7))
REDUCER_STATUSES = ("valid", "range", "unstable", "incomplete")
REQUEST_FIELDS = {
    "schema",
    "paper_commit",
    "scorebook_ref",
    "implementation_register_ref",
    "condition_register_ref",
    "required_directory_ref",
    "entries",
    "binding_manifest_path",
    "report_set_path",
}
ENTRY_FIELDS = {
    "identity",
    "score_id",
    "implementation_id",
    "comparison_condition_id",
    "outcome_id",
    "estimator_version",
    "expected_input_digest",
    "reducer_payload",
    "input_ref",
    "event_ledger_ref",
    "report_path",
}
FILE_REF_FIELDS = {"path", "size", "sha256"}
SHA256_RE = re.compile(r"[0-9a-f]{64}")
FORBIDDEN_PATH_TOKENS = ("metrics.csv", "results_pipeline")
FORBIDDEN_SCOPE_PREFIXES = ("synthetic", "measured_")
FORBIDDEN_SCOPE_SUBSTRING = "diagnostic"


class StatisticalReportProducerError(ValueError):
    """Raised when a statistical-report request cannot be honoured."""


def _fail(message: str) -> None:
    raise StatisticalReportProducerError(message)


def _exact_keys(value: Any, expected: set[str], name: str) -> None:
    if not isinstance(value, Mapping) or set(value) != expected:
        actual = sorted(value) if isinstance(value, Mapping) else type(value).__name__
        _fail(f"{name} schema mismatch: {actual}")


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        _fail(f"{name} must be non-empty trimmed text")
    return value


def _sha256(value: Any, name: str) -> str:
    value = _text(value, name)
    if SHA256_RE.fullmatch(value) is None:
        _fail(f"{name} must be a lowercase SHA-256")
    return value


def _safe_relative(path: Any, name: str) -> Path:
    path = _text(path, name)
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or path in {"", "."}:
        _fail(f"{name} must be a root-relative path without traversal")
    if candidate.as_posix() != path:
        _fail(f"{name} must already be in canonical posix form: {path}")
    return candidate


def _read_verified(root: Path, reference: Any, name: str) -> tuple[Path, bytes]:
    _exact_keys(reference, FILE_REF_FIELDS, name)
    relative = _safe_relative(reference["path"], f"{name}.path")
    if type(reference["size"]) is not int or reference["size"] < 0:
        _fail(f"{name}.size must be a non-negative integer")
    _sha256(reference["sha256"], f"{name}.sha256")
    lowered = relative.as_posix()
    for token in FORBIDDEN_PATH_TOKENS:
        if token in lowered:
            _fail(f"refusing to consume {token} output as paper evidence: {lowered}")
    root = root.resolve(strict=True)
    path = root / relative
    if path.is_symlink() or not path.is_file():
        _fail(f"{name} is not a regular non-symlink file: {lowered}")
    resolved = path.resolve(strict=True)
    if resolved != path.absolute() or root not in resolved.parents:
        _fail(f"{name} escapes the declared root: {lowered}")
    data = path.read_bytes()
    if reference["size"] != len(data) or reference["sha256"] != hashlib.sha256(data).hexdigest():
        _fail(f"{name} reference does not match bytes: {lowered}")
    return path, data


def _json_object(data: bytes, name: str) -> Mapping[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"), parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        _fail(f"{name} is not strict JSON: {exc}")
    if not isinstance(value, Mapping):
        _fail(f"{name} must contain a JSON object")
    return value


def _screen_scope(value: Any, name: str) -> None:
    """Reject synthetic/diagnostic/measured scopes wherever a scope key hides."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            if key == "scope" and isinstance(item, str):
                lowered = item.strip().lower()
                if lowered.startswith(FORBIDDEN_SCOPE_PREFIXES) or FORBIDDEN_SCOPE_SUBSTRING in lowered:
                    _fail(f"{name} carries forbidden scope {item!r}: synthetic, measured_* or diagnostic evidence cannot become a paper statistical report")
            _screen_scope(item, name)
    elif isinstance(value, list):
        for item in value:
            _screen_scope(item, name)


def _write_new(path: Path, data: bytes) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError as exc:
        raise StatisticalReportProducerError(f"refusing to overwrite existing file: {path}") from exc
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


def _prepare_destination(root: Path, relative: Any, name: str, seen: set[Path]) -> Path:
    candidate = _safe_relative(relative, name)
    if candidate in seen:
        _fail(f"{name} duplicates an earlier output destination: {candidate.as_posix()}")
    seen.add(candidate)
    root = root.resolve(strict=True)
    path = root / candidate
    if path.exists() and path.is_symlink():
        _fail(f"{name} must not be a symlink: {candidate.as_posix()}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _fail(f"{name} parent directory cannot be created: {exc}")
    parent = path.parent.resolve(strict=True)
    if parent != root and root not in parent.parents:
        _fail(f"{name} escapes the declared root: {candidate.as_posix()}")
    if path.parent.absolute() != parent:
        _fail(f"{name} has a symlink parent: {candidate.as_posix()}")
    return path


# --------------------------------------------------------------------------
# Authority dispatch: these functions only call the frozen reducers.
# --------------------------------------------------------------------------


def dispatch_reducer(score_id: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Feed one frozen payload to its authoritative reducer. No formulas here."""
    if score_id == "D1":
        from .organization_v3 import organization_report_from_payload

        return organization_report_from_payload(payload)
    if score_id == "D3":
        from .relations_v3 import RelationRunV3, d3_relation_report

        runs = [RelationRunV3(**run) for run in payload.get("runs", ())]
        return d3_relation_report(
            runs,
            partition_mode=payload.get("partition_mode"),
            registered_cells=payload.get("registered_cells"),
            **payload.get("inference", {}),
        )
    from .analysis_v3 import ScheduledRunV3, analyze_dimension

    runs = [ScheduledRunV3(**run) for run in payload.get("runs", ())]
    stratum_weights = payload.get("stratum_weights")
    if not isinstance(stratum_weights, Mapping):
        _fail(f"{score_id} reducer payload requires stratum_weights")
    fixed_catalog = None
    if score_id in {"D5", "D6"}:
        from .fixed_catalog_v3 import FixedCatalogV3, FixedRunSpecV3

        raw = payload.get("fixed_catalog")
        if not isinstance(raw, Mapping):
            _fail(f"{score_id} reducer payload requires a fixed_catalog")
        fixed_catalog = FixedCatalogV3(
            raw["score_id"],
            tuple(FixedRunSpecV3(**entry) for entry in raw["entries"]),
            excluded_families=tuple(raw.get("excluded_families", ())),
            excluded_family_case_counts=dict(raw.get("excluded_family_case_counts", {})),
        )
    return analyze_dimension(
        score_id, runs, stratum_weights=stratum_weights,
        fixed_catalog=fixed_catalog, **payload.get("inference", {}),
    )


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        _fail(f"{name} must be a finite real number, not {value!r}")
    return float(value)


def _interval_pair(report: Mapping[str, Any], name: str) -> tuple[float, float]:
    interval = report.get("interval")
    if not isinstance(interval, (list, tuple)) or len(interval) != 2:
        _fail(f"{name} reducer report has no two-point interval to report honestly")
    lower = _finite(interval[0], f"{name} interval lower")
    upper = _finite(interval[1], f"{name} interval upper")
    return lower, upper


def _interval_method(report: Mapping[str, Any]) -> str:
    method = report.get("method")
    if isinstance(method, str) and method.strip():
        return method
    endpoints = report.get("endpoints")
    if isinstance(endpoints, list) and endpoints and isinstance(endpoints[0], Mapping):
        method = endpoints[0].get("method")
        if isinstance(method, str) and method.strip():
            return method
    return "reducer_interval"


def _reducer_reason(report: Mapping[str, Any], status: str) -> str:
    reason = report.get("reason")
    if isinstance(reason, str) and reason.strip():
        return reason
    return f"{status}_reducer_estimate"


def estimate_from_report(score_id: str, report: Mapping[str, Any]) -> dict[str, Any]:
    """Map a reducer status onto the six raw report-estimate fields.

    Ruling (b): ``na`` fail-closes; the index layer's ``else`` branch rejects
    it, so NA must be expressed by the required-result directory instead.
    """
    status = report.get("status")
    if status == "na":
        _fail(
            f"{score_id} reducer returned status 'na': NA semantics belong to the "
            "required-result directory (unsupported/structural_na) and cannot be fabricated as a paper statistical report"
        )
    if status not in REDUCER_STATUSES:
        _fail(f"{score_id} reducer returned unsupported status {status!r}")
    if status in {"unstable", "incomplete"}:
        return {
            "status": status, "point": None, "lower": None, "upper": None,
            "interval_method": None, "reason": _reducer_reason(report, status),
        }
    if status == "range":
        lower, upper = _interval_pair(report, score_id)
        # The index layer pins observed_range to exactly this method string
        # (paper_result_index_v1._validate_estimate: ``method !=
        # "identification_range"`` raises "observed_range requires an ordered
        # identification_range"), so the authoritative reducer method (e.g.
        # d2_finite_sample_unknown_extreme_rates,
        # fixed_catalog_weighted_hoeffding) cannot be carried here verbatim;
        # it stays traceable through the reducer report bytes bound by the
        # report_ref, and the pin itself is locked by
        # test_range_interval_method_is_pinned_by_index_layer.
        return {
            "status": "range", "point": None, "lower": lower, "upper": upper,
            "interval_method": "identification_range",
            "reason": _reducer_reason(report, "range"),
        }
    point = report.get("point")
    if point is None:
        identification = report.get("identification")
        if isinstance(identification, (list, tuple)) and len(identification) == 2:
            low = _finite(identification[0], f"{score_id} identification lower")
            high = _finite(identification[1], f"{score_id} identification upper")
            if low == high:
                point = low
        if point is None:
            _fail(f"{score_id} valid reducer report exposes no honest point estimate")
    point = _finite(point, f"{score_id} point")
    lower = upper = None
    method = None
    if report.get("interval") is not None:
        lower, upper = _interval_pair(report, score_id)
        if not lower <= point <= upper:
            _fail(f"{score_id} reducer interval does not contain the honest point estimate")
        method = _text(_interval_method(report), f"{score_id} interval_method")
    return {
        "status": "valid", "point": point, "lower": lower, "upper": upper,
        "interval_method": method, "reason": _reducer_reason(report, "valid"),
    }


# --------------------------------------------------------------------------
# Support mapping (ruling c).  Counts come from the reducer output or the
# frozen request roster; a count is written as 0 only when the authority
# reducer output publishes no count of that kind ("absent", not "observed").
# --------------------------------------------------------------------------


def _fraction(numerator: int, denominator: int) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _instance_rows(runs: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for run in runs:
        grouped.setdefault(_text(run.get("instance_id"), "run instance_id"), []).append(run)
    return grouped


def _support_counts(
    *, generated: int, infrastructure: int, eligible: int, paired: int,
    trajectories: int, **_extra: int,
) -> dict[str, Any]:
    if not 0 <= eligible <= infrastructure <= generated:
        _fail("derived support counts are not nested; refusing to invent an ordering")
    return {
        "generated_base_instances": generated,
        "infrastructure_valid_base_instances": infrastructure,
        "metric_eligible_base_instances": eligible,
        "paired_instances": paired,
        "common_support_instances": 0,
        "repeated_trajectories": trajectories,
        "event_count": 0,
        "competing_event_count": 0,
        "censoring_count": 0,
        "planned_coverage_fraction": _fraction(infrastructure, generated),
        "observed_coverage_fraction": _fraction(eligible, generated),
        "numerator": eligible,
        "denominator": generated,
    }


def _d1_support(report: Mapping[str, Any]) -> dict[str, Any]:
    coverage = report.get("coverage")
    if not isinstance(coverage, Mapping) or not coverage:
        _fail("D1 reducer report has no coverage table")
    planned = infra = retained = 0
    for rows in coverage.values():
        if not isinstance(rows, Mapping):
            _fail("D1 coverage table is malformed")
        for row in rows.values():
            if not isinstance(row, Mapping):
                _fail("D1 coverage table is malformed")
            planned += int(row["planned"])
            infra += int(row["infrastructure_valid"])
            retained += int(row["retained_instances"])
    return _support_counts(
        generated=planned, infrastructure=infra, eligible=retained,
        paired=0, trajectories=int(report["scheduled_runs"]),
    )


def _scheduled_support(report: Mapping[str, Any], runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if int(report["scheduled_runs"]) != len(runs):
        _fail("reducer scheduled_runs disagrees with the frozen request roster")
    grouped = _instance_rows(runs)
    infrastructure = eligible = paired = 0
    for rows in grouped.values():
        statuses = [row.get("status", "observed") for row in rows]
        if not any(status in UNKNOWN_RUN_STATUSES for status in statuses):
            infrastructure += 1
        if all(status == "observed" for status in statuses):
            eligible += 1
        if any(status == "pair-invalid" for status in statuses):
            paired += 1
    return _support_counts(
        generated=len(grouped), infrastructure=infrastructure, eligible=eligible,
        paired=paired, trajectories=len(runs),
    )


def _relation_support(report: Mapping[str, Any], runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if int(report["scheduled_runs"]) != len(runs):
        _fail("reducer scheduled_runs disagrees with the frozen request roster")
    grouped = _instance_rows(runs)
    known = 0
    for rows in grouped.values():
        complete = True
        for row in rows:
            values = row.get("values")
            if not isinstance(values, Mapping) or any(item is None for item in values.values()):
                complete = False
        if complete:
            known += 1
    return _support_counts(
        generated=len(grouped), infrastructure=known, eligible=known,
        paired=0, trajectories=len(runs),
    )


def _fixed_catalog_support(
    report: Mapping[str, Any], runs: Sequence[Mapping[str, Any]], payload: Mapping[str, Any]
) -> dict[str, Any]:
    if int(report["scheduled_runs"]) != len(runs):
        _fail("reducer scheduled_runs disagrees with the frozen request roster")
    catalog = payload.get("fixed_catalog")
    entries = catalog.get("entries") if isinstance(catalog, Mapping) else None
    if not isinstance(entries, list):
        _fail("fixed catalog entries are required for D5/D6 support")
    generated = len({entry.get("instance_id") for entry in entries})
    grouped = _instance_rows(runs)
    known = 0
    for rows in grouped.values():
        if all(
            isinstance(row.get("values"), (list, tuple)) and all(item is not None for item in row["values"])
            for row in rows
        ):
            known += 1
    return _support_counts(
        generated=generated, infrastructure=known, eligible=known,
        paired=0, trajectories=len(runs),
    )


def support_from_report(score_id: str, report: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    """Honest 13-field support mapping; see the dimension table in the docstring."""
    if score_id == "D1":
        return _d1_support(report)
    if score_id == "D3":
        return _relation_support(report, list(payload.get("runs", ())))
    if score_id in {"D5", "D6"}:
        return _fixed_catalog_support(report, list(payload.get("runs", ())), payload)
    return _scheduled_support(report, list(payload.get("runs", ())))


# --------------------------------------------------------------------------
# Producer entry point.
# --------------------------------------------------------------------------


def _release_scorebook_digest(scorebook: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(scorebook).rstrip(b"\n")).hexdigest()


def _validated_entry(
    entry: Mapping[str, Any],
    index: int,
    *,
    context: Mapping[str, Any],
) -> dict[str, Any]:
    name = f"entries[{index}]"
    if isinstance(entry, Mapping) and "result_key_digest" in entry:
        _fail(f"{name}: caller-authored result_key_digest is not accepted; the digest is recomputed from the identity")
    _exact_keys(entry, set(ENTRY_FIELDS), name)
    identity = dict(entry["identity"])
    try:
        validate_identity(identity)
    except PaperResultIndexError as exc:
        _fail(f"{name} identity is invalid: {exc}")
    score_id = _text(entry["score_id"], f"{name}.score_id")
    if score_id not in SCORE_IDS:
        _fail(f"{name} score_id is outside D1-D6: {score_id}")
    for field in ("implementation_id", "comparison_condition_id", "outcome_id", "estimator_version"):
        if entry[field] != identity[field]:
            _fail(f"{name} {field} disagrees with the result identity")
    key_digest = result_key_digest(identity)
    if key_digest in context["seen_keys"]:
        _fail(f"{name} duplicate result_key_digest {key_digest}")
    context["seen_keys"].add(key_digest)
    implementations = context["implementations"]
    conditions = context["conditions"]
    if identity["implementation_id"] not in implementations:
        _fail(f"{name} implementation_id is not registered: {identity['implementation_id']}")
    condition = conditions.get(identity["comparison_condition_id"])
    if condition is None:
        _fail(f"{name} comparison_condition_id is not registered: {identity['comparison_condition_id']}")
    if condition["implementation_id"] != identity["implementation_id"]:
        _fail(f"{name} implementation and comparison condition do not match")
    directory_entry = context["directory_by_key"].get(key_digest)
    if directory_entry is None or directory_entry["identity"] != identity:
        _fail(f"{name} result key is not registered in the required-result directory: {key_digest}")
    expected_input_digest = _sha256(entry["expected_input_digest"], f"{name}.expected_input_digest")
    _, input_data = _read_verified(context["root"], entry["input_ref"], f"{name}.input_ref")
    _, ledger_data = _read_verified(context["root"], entry["event_ledger_ref"], f"{name}.event_ledger_ref")
    input_object = _json_object(input_data, f"{name} statistical input")
    if input_object.get("input_digest") != expected_input_digest:
        _fail(f"{name} input.json input_digest does not match expected_input_digest")
    payload = entry["reducer_payload"]
    if not isinstance(payload, Mapping):
        _fail(f"{name} reducer_payload must be an object")
    _screen_scope(payload, f"{name} reducer_payload")
    _screen_scope(input_object, f"{name} input.json")
    try:
        report = dispatch_reducer(score_id, payload)
    except StatisticalReportProducerError:
        raise
    except (ValueError, TypeError, KeyError) as exc:
        _fail(f"{name} authority reducer rejected the frozen payload ({score_id}): {exc}")
    if report.get("score_id") != score_id:
        _fail(f"{name} reducer report binds a different score_id")
    if report.get("paper_commit") != PAPER_COMMIT:
        _fail(f"{name} reducer report binds a different paper commit")
    if report.get("scorebook_digest") != context["scorebook_digest"]:
        _fail(f"{name} reducer scorebook_digest disagrees with the release-side scorebook digest")
    if report.get("input_digest") != expected_input_digest:
        _fail(f"{name} reducer input_digest disagrees with the frozen input bytes digest")
    estimate = estimate_from_report(score_id, report)
    support = support_from_report(score_id, report, payload)
    statistical_report = {
        "schema": STATISTICAL_REPORT_SCHEMA,
        "paper_commit": PAPER_COMMIT,
        "scorebook_digest": context["scorebook_digest"],
        "input_digest": expected_input_digest,
        "implementation_id": identity["implementation_id"],
        "comparison_condition_id": identity["comparison_condition_id"],
        "result_key_digest": key_digest,
        "score_id": score_id,
        "outcome_id": identity["outcome_id"],
        "estimator_version": identity["estimator_version"],
        "evidence_scope": "paper_result_candidate",
        "formal_gates": dict(FORMAL_GATES_FALSE),
        "estimate": estimate,
        "support": support,
        "event_ledger_digest": hashlib.sha256(ledger_data).hexdigest(),
    }
    _exact_keys(statistical_report, set(STATISTICAL_REPORT_FIELDS), f"{name} statistical report")
    binding = {
        "result_key_digest": key_digest,
        "identity": identity,
        "implementation_id": identity["implementation_id"],
        "comparison_condition_id": identity["comparison_condition_id"],
        "score_id": score_id,
        "outcome_id": identity["outcome_id"],
        "estimator_version": identity["estimator_version"],
        "expected_input_digest": expected_input_digest,
        "report_ref": None,
        "input_ref": dict(entry["input_ref"]),
        "event_ledger_ref": dict(entry["event_ledger_ref"]),
    }
    _exact_keys(binding, set(BINDING_ENTRY_FIELDS), f"{name} binding entry")
    return {
        "key_digest": key_digest,
        "report_path": entry["report_path"],
        "report": statistical_report,
        "binding": binding,
    }


def produce_statistical_reports(request: Mapping[str, Any], *, root: str | Path) -> Mapping[str, Any]:
    """Validate a full request, run the authority reducers, write artifacts."""
    _exact_keys(request, set(REQUEST_FIELDS), "request manifest")
    if request["schema"] != REQUEST_SCHEMA:
        _fail(f"unsupported request schema: {request['schema']!r}")
    if request["paper_commit"] != PAPER_COMMIT:
        _fail("request paper_commit does not match the authoritative scorebook paper_commit")
    raw_root = Path(root)
    if raw_root.is_symlink() or not raw_root.is_dir():
        _fail("output root must be an existing non-symlink directory")
    root_path = raw_root.resolve(strict=True)
    _, scorebook_data = _read_verified(root_path, request["scorebook_ref"], "scorebook_ref")
    _, implementations_data = _read_verified(root_path, request["implementation_register_ref"], "implementation_register_ref")
    _, conditions_data = _read_verified(root_path, request["condition_register_ref"], "condition_register_ref")
    _, directory_data = _read_verified(root_path, request["required_directory_ref"], "required_directory_ref")
    scorebook = _json_object(scorebook_data, "scorebook")
    implementation_register = _json_object(implementations_data, "implementation register")
    condition_register = _json_object(conditions_data, "comparison condition register")
    required_directory = _json_object(directory_data, "required result directory")
    try:
        validate_condition_register(condition_register, implementation_register=implementation_register)
        validate_required_directory(
            required_directory,
            implementation_register=implementation_register,
            condition_register=condition_register,
        )
    except PaperResultIndexError as exc:
        _fail(f"register or required-result directory is invalid: {exc}")
    if implementation_register["paper_commit"] != PAPER_COMMIT:
        _fail("implementation register binds a different paper commit")
    if scorebook.get("paper_commit") != PAPER_COMMIT:
        _fail("scorebook binds a different paper commit")
    # Ruling (a): recompute both scorebook digest conventions and fail on drift.
    scorebook_digest = _release_scorebook_digest(scorebook)
    if scorebook_digest != default_scorebook_v3().digest():
        _fail(
            "scorebook digest conventions disagree: "
            f"release-side {scorebook_digest} != authoritative reducer digest {default_scorebook_v3().digest()}"
        )
    entries = request["entries"]
    if not isinstance(entries, list) or not entries:
        _fail("request must contain at least one entry")
    context: dict[str, Any] = {
        "root": root_path,
        "seen_keys": set(),
        "implementations": {item["implementation_id"] for item in implementation_register["implementations"]},
        "conditions": {item["condition_id"]: item for item in condition_register["conditions"]},
        "directory_by_key": {result_key_digest(entry["identity"]): entry for entry in required_directory["entries"]},
        "scorebook_digest": scorebook_digest,
    }
    produced = [_validated_entry(entry, index, context=context) for index, entry in enumerate(entries)]
    bindings = []
    report_set_entries = []
    destinations: set[Path] = set()
    outputs: list[tuple[Path, bytes]] = []
    for item in produced:
        report_bytes = canonical_json_bytes(item["report"])
        report_path = _prepare_destination(root_path, item["report_path"], f"report for {item['key_digest']}", destinations)
        report_ref = {
            "path": item["report_path"],
            "size": len(report_bytes),
            "sha256": hashlib.sha256(report_bytes).hexdigest(),
        }
        item["binding"]["report_ref"] = report_ref
        bindings.append(item["binding"])
        report_set_entries.append({"result_key_digest": item["key_digest"], "report_ref": report_ref})
        outputs.append((report_path, report_bytes))
    binding_manifest = {
        "schema": STATISTICAL_BINDING_SCHEMA,
        "paper_commit": PAPER_COMMIT,
        "scorebook_digest": scorebook_digest,
        "bindings": bindings,
    }
    binding_bytes = canonical_json_bytes(binding_manifest)
    binding_path = _prepare_destination(root_path, request["binding_manifest_path"], "binding_manifest_path", destinations)
    outputs.append((binding_path, binding_bytes))
    report_set = {
        "schema": REPORT_SET_SCHEMA,
        "paper_commit": PAPER_COMMIT,
        "reports": report_set_entries,
    }
    report_set_bytes = canonical_json_bytes(report_set)
    report_set_path = _prepare_destination(root_path, request["report_set_path"], "report_set_path", destinations)
    outputs.append((report_set_path, report_set_bytes))
    created: list[Path] = []
    try:
        for path, data in outputs:
            _write_new(path, data)
            created.append(path)
    except BaseException:
        for path in reversed(created):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise
    # Belt and braces: the artifacts must be consumable by the index layer.
    try:
        validate_statistical_report_binding(
            binding_manifest,
            root=root_path,
            implementation_register=implementation_register,
            condition_register=condition_register,
            required_directory=required_directory,
        )
    except PaperResultIndexError as exc:  # pragma: no cover - producer bug guard
        _fail(f"produced artifacts failed downstream revalidation: {exc}")
    return {
        "schema": PRODUCE_RESULT_SCHEMA,
        "paper_commit": PAPER_COMMIT,
        "scorebook_digest": scorebook_digest,
        "report_count": len(produced),
        "binding_manifest": {
            "path": request["binding_manifest_path"],
            "size": len(binding_bytes),
            "sha256": hashlib.sha256(binding_bytes).hexdigest(),
        },
        "report_set": {
            "path": request["report_set_path"],
            "size": len(report_set_bytes),
            "sha256": hashlib.sha256(report_set_bytes).hexdigest(),
        },
        "formal_gates": dict(FORMAL_GATES_FALSE),
    }


def _load_request(path: str | Path) -> Mapping[str, Any]:
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        _fail("request manifest must be a regular non-symlink file")
    return _json_object(source.read_bytes(), "request manifest")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    produce = sub.add_parser("produce-reports")
    produce.add_argument("--root", required=True)
    produce.add_argument("--request", required=True)
    produce.set_defaults(handler=lambda args: produce_statistical_reports(_load_request(args.request), root=args.root))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        result = args.handler(args)
        sys.stdout.buffer.write(canonical_json_bytes(result))
        return 0
    except StatisticalReportProducerError as exc:
        print(f"statistical-report-producer: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        # _write_new only converts FileExistsError; sibling OS failures such
        # as ENOSPC/EACCES must still exit through the same fail-closed path.
        print(f"statistical-report-producer: writing outputs failed with OS error {exc.errno}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
