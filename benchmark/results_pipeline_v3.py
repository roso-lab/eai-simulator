"""Reproducible D1--D6 calculation and export entry point.

The existing metric modules intentionally expose small pure estimators.  This
module supplies the deployable boundary around them: one versioned normalized
input package, explicit provenance/digests, fail-closed validation, and stable
JSON/CSV/Markdown exports.  The synthetic fixture is diagnostic only and is
never treated as qualification evidence.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Mapping

from .composite_v3 import composite_profile_range, composite_score
from .metrics_v3 import ScoreResultV3, d1_score, d2_score, d3_score, d4_score, d5_score, d6_score
from .records_v3 import canonical_digest
from .scorebook_v3 import PAPER_COMMIT, default_scorebook_v3


PIPELINE_SCHEMA = "heterocoord.results-pipeline.v1"
PIPELINE_VERSION = "results-pipeline-v3"
DIMENSIONS = ("D1", "D2", "D3", "D4", "D5", "D6")
ALLOWED_SCOPES = frozenset({"synthetic_diagnostic_fixture", "measured_diagnostic", "measured_unqualified"})
REQUIRED_KEYS = frozenset({
    "schema", "pipeline_version", "scope", "paper_commit", "scorebook_digest",
    "catalog", "plan", "catalog_digest", "plan_digest", "parameters", "dimensions",
    "input_digest",
})


class ResultsPipelineError(ValueError):
    """Raised when a normalized results package cannot be trusted."""


def _input_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        "catalog": payload["catalog"],
        "plan": payload["plan"],
        "parameters": payload["parameters"],
        "dimensions": payload["dimensions"],
        "pipeline_version": payload.get("pipeline_version", PIPELINE_VERSION),
    }
    # Real-case provenance is part of the calculation identity when present;
    # synthetic fixtures remain backward-compatible because these keys are
    # absent and therefore do not alter their input digest.
    for key in ("case_manifest_digest", "scene_digest", "semantic_map_digest", "topology_digest", "roster_digest", "contract_digest", "artifact_manifest_digest", "paper_projection_digest", "oracle_id", "evaluator_id"):
        if key in payload:
            result[key] = payload[key]
    return result


def _result_dict(result, *, support_count: int, denominator: int) -> dict[str, Any]:
    value = asdict(result)
    value["components"] = dict(value["components"])
    value["excluded_families"] = list(value["excluded_families"])
    value["excluded_family_case_counts"] = dict(value["excluded_family_case_counts"])
    value["support"] = {"count": int(support_count), "denominator": int(denominator)}
    return value


def _validate_payload(payload: Mapping[str, Any]) -> None:
    if not isinstance(payload, Mapping):
        raise ResultsPipelineError("input package must be an object")
    missing = REQUIRED_KEYS - set(payload)
    if missing:
        raise ResultsPipelineError(f"input package missing required keys: {sorted(missing)}")
    if payload["schema"] != PIPELINE_SCHEMA:
        raise ResultsPipelineError("unsupported results pipeline schema")
    if payload["pipeline_version"] != PIPELINE_VERSION:
        raise ResultsPipelineError("unsupported results pipeline version")
    if payload["paper_commit"] != PAPER_COMMIT:
        raise ResultsPipelineError("input binds a different paper version")
    scorebook_digest = default_scorebook_v3().digest()
    if payload["scorebook_digest"] != scorebook_digest:
        raise ResultsPipelineError("input binds a different scorebook")
    if not isinstance(payload["catalog"], Mapping) or not isinstance(payload["plan"], Mapping):
        raise ResultsPipelineError("catalog and plan must be structured mappings")
    if not str(payload["catalog"].get("schema", "")).strip() or not isinstance(payload["catalog"].get("anchors"), list) or not payload["catalog"]["anchors"]:
        raise ResultsPipelineError("catalog schema/anchors are required")
    if len(set(payload["catalog"]["anchors"])) != len(payload["catalog"]["anchors"]):
        raise ResultsPipelineError("catalog anchors must be unique")
    if not str(payload["plan"].get("schema", "")).strip() or type(payload["plan"].get("seed")) is not int:
        raise ResultsPipelineError("plan schema/seed are required")
    if payload["catalog_digest"] != canonical_digest(payload["catalog"]):
        raise ResultsPipelineError("catalog digest mismatch")
    if payload["plan_digest"] != canonical_digest(payload["plan"]):
        raise ResultsPipelineError("plan digest mismatch")
    if payload["input_digest"] != canonical_digest(_input_payload(payload)):
        raise ResultsPipelineError("input digest mismatch")
    if not isinstance(payload["parameters"], Mapping):
        raise ResultsPipelineError("parameters must be a mapping")
    _validate_parameters(payload["parameters"])
    dimensions = payload["dimensions"]
    if not isinstance(dimensions, Mapping) or set(dimensions) != set(DIMENSIONS):
        raise ResultsPipelineError("dimensions must contain exactly D1 through D6")
    _validate_dimensions(dimensions, scope=payload["scope"])
    params = payload["parameters"]
    if dimensions["D5"]["min_cases_per_family"] != params["d5_min_cases"]:
        raise ResultsPipelineError("D5 case floor is not bound to parameters")
    d6 = dimensions["D6"]
    # The frozen synthetic fixture predates the paper's per-instance
    # calibration table.  Keep its global pair bound to parameters for
    # deterministic backwards compatibility; measured/new D6 dimensions are
    # validated against their explicit instance table below instead.
    if "calibration" not in d6:
        if "d6_reference_bits" not in params or "d6_reference_time" not in params:
            raise ResultsPipelineError("legacy D6 dimensions require global calibration parameters")
        if not math.isclose(float(d6["reference_bits"]), float(params["d6_reference_bits"]), rel_tol=0.0, abs_tol=0.0):
            raise ResultsPipelineError("D6 reference_bits is not bound to parameters")
        if not math.isclose(float(d6["reference_time"]), float(params["d6_reference_time"]), rel_tol=0.0, abs_tol=0.0):
            raise ResultsPipelineError("D6 reference_time is not bound to parameters")
    if payload.get("scope") not in ALLOWED_SCOPES:
        raise ResultsPipelineError(f"scope must be one of {sorted(ALLOWED_SCOPES)}")
    provenance_digests = ("case_manifest_digest", "scene_digest", "semantic_map_digest", "topology_digest", "roster_digest", "contract_digest", "artifact_manifest_digest", "paper_projection_digest")
    for key in provenance_digests:
        if key in payload and (not isinstance(payload[key], str) or not re.fullmatch(r"[0-9a-f]{64}", payload[key])):
            raise ResultsPipelineError(f"{key} must be a lowercase SHA-256")
    for key in ("oracle_id", "evaluator_id"):
        if key in payload and (not isinstance(payload[key], str) or not payload[key].strip()):
            raise ResultsPipelineError(f"{key} must be non-empty text")


def _real(value: Any, name: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ResultsPipelineError(f"{name} must be a finite real number")
    value = float(value)
    if minimum is not None and value < minimum:
        raise ResultsPipelineError(f"{name} is below its lower bound")
    if maximum is not None and value > maximum:
        raise ResultsPipelineError(f"{name} is above its upper bound")
    return value


def _exact_keys(value: Mapping[str, Any], required: set[str], name: str, optional: set[str] | None = None) -> None:
    allowed = required | (optional or set())
    if set(value) - allowed or not required.issubset(value):
        raise ResultsPipelineError(f"{name} schema mismatch")


def _validate_parameters(parameters: Mapping[str, Any]) -> None:
    required = {"alpha", "seed", "resamples", "d5_min_cases"}
    optional = {"d6_reference_bits", "d6_reference_time"}
    _exact_keys(parameters, required, "parameters", optional=optional)
    alpha = _real(parameters["alpha"], "parameters.alpha", minimum=0.0, maximum=1.0)
    if alpha in (0.0, 1.0) or type(parameters["seed"]) is not int or type(parameters["resamples"]) is not int or parameters["resamples"] < 0:
        raise ResultsPipelineError("parameters seed/resamples are invalid")
    if type(parameters["d5_min_cases"]) is not int or parameters["d5_min_cases"] < 1:
        raise ResultsPipelineError("parameters.d5_min_cases is invalid")
    if ("d6_reference_bits" in parameters) != ("d6_reference_time" in parameters):
        raise ResultsPipelineError("legacy D6 parameters must provide both global references")
    if "d6_reference_bits" in parameters:
        _real(parameters["d6_reference_bits"], "parameters.d6_reference_bits", minimum=0.0)
        _real(parameters["d6_reference_time"], "parameters.d6_reference_time", minimum=0.0)


def _validate_dimensions(dimensions: Mapping[str, Any], *, scope: str = "synthetic_diagnostic_fixture") -> None:
    d1 = dimensions["D1"]
    if not isinstance(d1, Mapping):
        raise ResultsPipelineError("D1 must be an object")
    _exact_keys(d1, {"validity", "organization", "infeasible", "complete_plan"}, "D1")
    for key in ("validity", "organization", "infeasible"):
        _real(d1[key], f"D1.{key}", minimum=0.0, maximum=1.0)
    if type(d1["complete_plan"]) is not bool:
        raise ResultsPipelineError("D1.complete_plan must be boolean")

    d2 = dimensions["D2"]
    if not isinstance(d2, Mapping):
        raise ResultsPipelineError("D2 must be an object")
    _exact_keys(d2, {"nominal", "restricted"}, "D2")
    _real(d2["nominal"], "D2.nominal", minimum=0.0, maximum=1.0)
    if not isinstance(d2["restricted"], list) or len(d2["restricted"]) != 3:
        raise ResultsPipelineError("D2.restricted must contain exactly three rates")
    for index, value in enumerate(d2["restricted"]):
        _real(value, f"D2.restricted[{index}]", minimum=0.0, maximum=1.0)

    d3 = dimensions["D3"]
    if not isinstance(d3, Mapping):
        raise ResultsPipelineError("D3 must be an object")
    _exact_keys(d3, {"p2", "p3j", "p3s", "p4"}, "D3")
    for key in ("p2", "p3j", "p3s", "p4"):
        _real(d3[key], f"D3.{key}", minimum=0.0, maximum=1.0)

    d4 = dimensions["D4"]
    if not isinstance(d4, Mapping):
        raise ResultsPipelineError("D4 must be an object")
    _exact_keys(d4, {"expected_count", "items"}, "D4")
    if type(d4["expected_count"]) is not int or d4["expected_count"] < 0 or not isinstance(d4["items"], list):
        raise ResultsPipelineError("D4 expected_count/items are invalid")
    for index, row in enumerate(d4["items"]):
        if not isinstance(row, Mapping):
            raise ResultsPipelineError(f"D4.items[{index}] must be an object")
        kind = row.get("kind")
        if kind == "deadline":
            _exact_keys(row, {"kind", "e"}, f"D4.items[{index}]")
        elif kind in {"time_valued", "continuous_supply"}:
            _exact_keys(row, {"kind", "e", "phi"}, f"D4.items[{index}]")
            _real(row["phi"], f"D4.items[{index}].phi", minimum=0.0, maximum=1.0)
        else:
            raise ResultsPipelineError(f"D4.items[{index}] has unsupported kind")
        _real(row["e"], f"D4.items[{index}].e", minimum=0.0, maximum=1.0)

    d5 = dimensions["D5"]
    if not isinstance(d5, Mapping):
        raise ResultsPipelineError("D5 must be an object")
    _exact_keys(d5, {"min_cases_per_family", "families"}, "D5")
    if type(d5["min_cases_per_family"]) is not int or d5["min_cases_per_family"] < 1 or not isinstance(d5["families"], Mapping):
        raise ResultsPipelineError("D5 case floor/families are invalid")
    for family_id, cases in d5["families"].items():
        if not isinstance(family_id, str) or not isinstance(cases, Mapping):
            raise ResultsPipelineError("D5 family identity/cases are invalid")
        for case_id, units in cases.items():
            if not isinstance(case_id, str) or not isinstance(units, Mapping):
                raise ResultsPipelineError("D5 case identity/units are invalid")
            for unit_id, repeats in units.items():
                if not isinstance(unit_id, str) or not isinstance(repeats, list) or len(repeats) != 3:
                    raise ResultsPipelineError("D5 units require exactly three repeats")
                for index, value in enumerate(repeats):
                    _real(value, f"D5.{family_id}/{case_id}/{unit_id}[{index}]", minimum=0.0, maximum=1.0)

    d6 = dimensions["D6"]
    if not isinstance(d6, Mapping):
        raise ResultsPipelineError("D6 must be an object")
    if "calibration" not in d6:
        if scope != "synthetic_diagnostic_fixture":
            raise ResultsPipelineError("D6 global calibration is diagnostic-only; measured inputs require per-instance calibration")
        _exact_keys(d6, {"expected_count", "reference_bits", "reference_time", "runs"}, "D6")
        if type(d6["expected_count"]) is not int or d6["expected_count"] < 0 or not isinstance(d6["runs"], list):
            raise ResultsPipelineError("D6 expected_count/runs are invalid")
        _real(d6["reference_bits"], "D6.reference_bits", minimum=0.0)
        _real(d6["reference_time"], "D6.reference_time", minimum=0.0)
        for index, row in enumerate(d6["runs"]):
            if not isinstance(row, Mapping) or set(row) != {"success", "bits", "time"} or type(row["success"]) is not bool:
                raise ResultsPipelineError(f"D6.runs[{index}] schema is invalid")
            _real(row["bits"], f"D6.runs[{index}].bits", minimum=0.0)
            _real(row["time"], f"D6.runs[{index}].time", minimum=0.0)
        return
    _exact_keys(d6, {"expected_count", "calibration", "runs"}, "D6")
    if type(d6["expected_count"]) is not int or d6["expected_count"] < 0 or not isinstance(d6["runs"], list):
        raise ResultsPipelineError("D6 expected_count/runs are invalid")
    calibration = d6["calibration"]
    if not isinstance(calibration, list) or not calibration:
        raise ResultsPipelineError("D6.calibration must be a non-empty instance table")
    calibration_ids: set[str] = set()
    for index, row in enumerate(calibration):
        if not isinstance(row, Mapping) or set(row) != {"instance_id", "reference_bits", "reference_time"}:
            raise ResultsPipelineError(f"D6.calibration[{index}] schema is invalid")
        instance_id = row["instance_id"]
        if not isinstance(instance_id, str) or not instance_id.strip():
            raise ResultsPipelineError(f"D6.calibration[{index}].instance_id is invalid")
        if instance_id in calibration_ids:
            raise ResultsPipelineError(f"D6 calibration duplicate instance_id: {instance_id}")
        calibration_ids.add(instance_id)
        _real(row["reference_bits"], f"D6.calibration[{index}].reference_bits", minimum=0.0)
        _real(row["reference_time"], f"D6.calibration[{index}].reference_time", minimum=0.0)
    run_ids: set[str] = set()
    for index, row in enumerate(d6["runs"]):
        if not isinstance(row, Mapping) or set(row) != {"instance_id", "success", "bits", "time"} or type(row["success"]) is not bool:
            raise ResultsPipelineError(f"D6.runs[{index}] schema is invalid")
        instance_id = row["instance_id"]
        if not isinstance(instance_id, str) or not instance_id.strip():
            raise ResultsPipelineError(f"D6.runs[{index}].instance_id is invalid")
        run_ids.add(instance_id)
        _real(row["bits"], f"D6.runs[{index}].bits", minimum=0.0)
        _real(row["time"], f"D6.runs[{index}].time", minimum=0.0)
    if run_ids != calibration_ids:
        missing = sorted(calibration_ids - run_ids)
        unknown = sorted(run_ids - calibration_ids)
        raise ResultsPipelineError(f"D6 running instances do not match calibration (missing={missing}, unknown={unknown})")


def _synthetic_dimensions() -> dict[str, Any]:
    family_cases = {
        "eligible": {
            f"case-{index:02d}": {"unit-0": [float(index % 4 != 0), 1.0, 1.0]}
            for index in range(30)
        },
        "short-below-floor": {
            "case-00": {"unit-0": [1.0, 1.0, 1.0]},
            "case-01": {"unit-0": [0.0, 1.0, 0.0]},
        },
    }
    return {
        "D1": {"validity": 0.80, "organization": 0.75, "infeasible": 0.90, "complete_plan": True},
        "D2": {"nominal": 0.80, "restricted": [0.80, 0.60, 0.40]},
        "D3": {"p2": 0.90, "p3j": 0.80, "p3s": 0.60, "p4": 0.75},
        "D4": {"expected_count": 3, "items": [
            {"kind": "deadline", "e": 1.0},
            {"kind": "time_valued", "e": 0.80, "phi": 0.60},
            {"kind": "continuous_supply", "e": 0.50, "phi": 0.90},
        ]},
        "D5": {"min_cases_per_family": 30, "families": family_cases},
        "D6": {"expected_count": 3, "reference_bits": 100.0, "reference_time": 1.0, "runs": [
            {"success": True, "bits": 100.0, "time": 1.0},
            {"success": True, "bits": 200.0, "time": 2.0},
            {"success": False, "bits": 0.0, "time": 0.0},
        ]},
    }


def build_synthetic_fixture() -> dict[str, Any]:
    """Build a deterministic, diagnostic-only package exercising all six scores."""

    scorebook_digest = default_scorebook_v3().digest()
    payload: dict[str, Any] = {
        "schema": PIPELINE_SCHEMA,
        "pipeline_version": PIPELINE_VERSION,
        "scope": "synthetic_diagnostic_fixture",
        "paper_commit": PAPER_COMMIT,
        "scorebook_digest": scorebook_digest,
        "catalog": {
            "schema": "synthetic-catalog.v1",
            "anchors": ["syn-anchor-01", "syn-anchor-02"],
            "semantic_map_status": "diagnostic_placeholder",
        },
        "plan": {
            "schema": "synthetic-plan.v1",
            "seed": 20261025,
            "repeat_ids": ["r1", "r2", "r3"],
            "formal_qualification": False,
        },
        "parameters": {
            "alpha": 0.05,
            "seed": 20261025,
            "resamples": 200,
            "d5_min_cases": 30,
            "d6_reference_bits": 100.0,
            "d6_reference_time": 1.0,
        },
        "dimensions": _synthetic_dimensions(),
    }
    payload["catalog_digest"] = canonical_digest(payload["catalog"])
    payload["plan_digest"] = canonical_digest(payload["plan"])
    payload["input_digest"] = canonical_digest(_input_payload(payload))
    return payload


def build_negative_fixtures() -> dict[str, dict[str, Any]]:
    """Return explicit negative cases used to test status propagation."""

    base = build_synthetic_fixture()
    cases: dict[str, dict[str, Any]] = {}
    zero_nominal = json.loads(json.dumps(base))
    zero_nominal["dimensions"]["D2"] = {"nominal": 0.0, "restricted": [0.2, 0.2, 0.2]}
    cases["d2_nominal_zero_na"] = zero_nominal
    empty_closure = json.loads(json.dumps(base))
    empty_closure["dimensions"]["D4"] = {"expected_count": 0, "items": []}
    cases["d4_empty_closure_na"] = empty_closure
    no_floor = json.loads(json.dumps(base))
    no_floor["dimensions"]["D5"] = {"min_cases_per_family": 30, "families": {"short": {"c": {"u": [1.0, 1.0, 1.0]}}}}
    cases["d5_no_family_floor_na"] = no_floor
    incomplete = json.loads(json.dumps(base))
    incomplete["dimensions"]["D6"]["runs"] = incomplete["dimensions"]["D6"]["runs"][:-1]
    cases["d6_fixed_directory_incomplete"] = incomplete
    for value in cases.values():
        value["catalog_digest"] = canonical_digest(value["catalog"])
        value["plan_digest"] = canonical_digest(value["plan"])
        value["input_digest"] = canonical_digest(_input_payload(value))
    return cases


def compute_results(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Compute all dimensions from one validated normalized input package."""

    _validate_payload(payload)
    dimensions = payload["dimensions"]
    params = payload["parameters"]
    d1 = d1_score(**dimensions["D1"])
    d2 = d2_score(**dimensions["D2"])
    d3 = d3_score(**dimensions["D3"])
    d4_spec = dimensions["D4"]
    d4 = d4_score(d4_spec["items"], expected_count=d4_spec.get("expected_count"))
    d5_spec = dimensions["D5"]
    d5 = d5_score(d5_spec["families"], min_cases_per_family=d5_spec.get("min_cases_per_family", params.get("d5_min_cases", 30)))
    d6_spec = dimensions["D6"]
    d6 = _compute_d6(d6_spec)
    scores = (d1, d2, d3, d4, d5, d6)
    d5_families = dimensions["D5"]["families"]
    support_meta = {
        "D1": (1 if d1.status == "valid" else 0, 1),
        "D2": (1 if d2.status == "valid" else 0, 1),
        "D3": (1 if d3.status == "valid" else 0, 1),
        "D4": (len(d4_spec["items"]), d4_spec.get("expected_count", len(d4_spec["items"]))),
        "D5": (int(d5.components.get("family_count", 0.0)), len(d5_families)),
        "D6": (len(d6_spec["runs"]), d6_spec.get("expected_count", len(d6_spec["runs"]))),
    }
    score_dicts = [_result_dict(item, support_count=support_meta[item.score_id][0], denominator=support_meta[item.score_id][1]) for item in scores]
    composite = composite_score(scores)
    profile_range = composite_profile_range(scores)
    sanity_checks = _sanity_checks(scores, composite, profile_range, dimensions)
    source_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report = {
        "schema": "heterocoord.results-report.v1",
        "pipeline_version": PIPELINE_VERSION,
        "scope": payload["scope"],
        "qualification": "diagnostic_only",
        "formal_gates": {"rankable": False, "paper_score_eligible": False, "physical_qualified": False},
        "provenance": {
            "paper_commit": payload["paper_commit"],
            "scorebook_digest": payload["scorebook_digest"],
            "catalog_digest": payload["catalog_digest"],
            "plan_digest": payload["plan_digest"],
            "input_digest": payload["input_digest"],
            "estimator": {"module": "benchmark.metrics_v3", "version": PIPELINE_VERSION,
                          "pipeline_source_sha256": source_digest,
                          "python": sys.version.split()[0]},
            "parameters": dict(params),
        },
        "dimensions": score_dicts,
        "composite": composite,
        "profile_range": profile_range,
        "sanity_checks": sanity_checks,
    }
    for key in ("case_manifest_digest", "scene_digest", "semantic_map_digest", "topology_digest", "roster_digest", "contract_digest", "artifact_manifest_digest", "paper_projection_digest", "oracle_id", "evaluator_id"):
        if key in payload:
            report["provenance"][key] = payload[key]
    return report


def _compute_d6(spec: Mapping[str, Any]) -> ScoreResultV3:
    """Compute D6 with a fixed reference median for each running instance.

    ``metrics_v3.d6_score`` remains the scalar estimator for the frozen
    diagnostic fixture.  The paper contract aggregates repeat rows within an
    instance and then gives each instance equal weight; applying a single
    global reference to all rows would silently change that estimand.
    """

    if "calibration" not in spec:
        return d6_score(
            spec["runs"],
            reference_bits=spec["reference_bits"],
            reference_time=spec["reference_time"],
            expected_count=spec.get("expected_count"),
        )
    calibration = {row["instance_id"]: row for row in spec["calibration"]}
    grouped: dict[str, list[Mapping[str, Any]]] = {instance_id: [] for instance_id in calibration}
    for row in spec["runs"]:
        grouped[row["instance_id"]].append(row)
    instance_results = []
    for instance_id, rows in grouped.items():
        result = d6_score(
            rows,
            reference_bits=calibration[instance_id]["reference_bits"],
            reference_time=calibration[instance_id]["reference_time"],
        )
        instance_results.append(result)
    if any(result.status != "valid" for result in instance_results):
        failed = next(result for result in instance_results if result.status != "valid")
        return ScoreResultV3("D6", failed.status, None, reason=failed.reason or "instance_calibration_not_ready")
    point = sum(float(result.point) for result in instance_results) / len(instance_results)
    success_rate = sum(float(result.components.get("success_rate", 0.0)) for result in instance_results) / len(instance_results)
    return ScoreResultV3(
        "D6",
        "valid",
        point,
        components={"success_rate": success_rate, "instance_count": float(len(instance_results))},
    )


def _sanity_checks(scores, composite: Mapping[str, Any], profile_range: Mapping[str, Any], dimensions: Mapping[str, Any]) -> dict[str, Any]:
    """Expose scientific invariants checked on every calculated report.

    These checks are intentionally derived from the already validated score
    objects and input package.  They are diagnostics for auditability; they
    do not turn missing evidence into a pass or open a qualification gate.
    """

    checks: list[dict[str, Any]] = []

    def add(check_id: str, passed: bool, detail: str) -> None:
        checks.append({"id": check_id, "passed": bool(passed), "detail": detail})

    bounded = all(
        item.point is None or 0.0 <= float(item.point) <= 100.0
        for item in scores
    )
    add("dimension_points_bounded", bounded, "valid dimension points lie in [0,100]")

    d2 = next(item for item in scores if item.score_id == "D2")
    delivered = composite.get("values", {}).get("D2_delivered")
    conditional = d2.point is None or delivered is None or float(delivered) <= float(d2.point) + 1e-12
    add("d2_delivery_cannot_exceed_conditional", conditional, "p0*S_D2 cannot exceed S_D2")

    point = composite.get("point")
    minimum = profile_range.get("minimum")
    maximum = profile_range.get("maximum")
    in_profile = point is None or minimum is None or maximum is None or float(minimum) <= float(point) <= float(maximum)
    add("composite_inside_profile_range", in_profile, "equal-mass composite lies inside the 16-vertex profile range")

    d5 = next(item for item in scores if item.score_id == "D5")
    d5_spec = dimensions["D5"]
    excluded_count = len(d5.excluded_families)
    d5_denominator = len(d5_spec["families"])
    disclosure = d5_denominator == int(d5.components.get("family_count", 0.0)) + excluded_count
    add("d5_exclusions_disclosed", disclosure, "D5 eligible and excluded family counts cover the fixed catalog")

    support_ok = all(
        int(item.support_weight >= 0.0) == 1 and int(item.support_weight <= 1.0) == 1
        for item in scores
    )
    add("support_weights_bounded", support_ok, "support weights remain in [0,1]")
    return {"status": "pass" if all(item["passed"] for item in checks) else "fail", "checks": checks}


def _metric_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for metric in report["dimensions"]:
        rows.append({
            "metric_id": metric["score_id"], "status": metric["status"], "point": metric["point"],
            "lower": metric["lower"], "upper": metric["upper"], "reason": metric["reason"],
            "support_weight": metric["support_weight"], "support_count": metric["support"]["count"],
            "denominator": metric["support"]["denominator"],
        })
    return rows


def export_results(
    report: Mapping[str, Any],
    destination: str | Path,
    *,
    input_payload: Mapping[str, Any] | None = None,
    additional_json: Mapping[str, Any] | None = None,
) -> tuple[Path, ...]:
    """Write stable machine and human-readable outputs for one report.

    ``input_payload`` is optional for callers that already archive their input
    package.  The CLI supplies it so a synthetic run is fully replayable.
    """

    _validate_report(report)
    destination = Path(destination)
    if destination.exists() and any(destination.iterdir()):
        raise ResultsPipelineError("export destination must be empty")
    destination.mkdir(parents=True, exist_ok=True)
    report_path = destination / "report.json"
    metrics_path = destination / "metrics.csv"
    markdown_path = destination / "metrics.md"
    coverage_path = destination / "coverage_report.json"
    provenance_path = destination / "provenance.json"
    manifest_path = destination / "input_manifest.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    rows = _metric_rows(report)
    with metrics_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    lines = ["# D1–D6 diagnostic results", "", "| metric | status | point | lower | upper | reason |", "|---|---|---:|---:|---:|---|"]
    for row in rows:
        lines.append("| {metric_id} | {status} | {point} | {lower} | {upper} | {reason} |".format(
            **{key: ("N/A" if row[key] is None else row[key]) for key in row}))
    lines.extend(["", "Composite status: **{status}**".format(**report["composite"]), "", "Formal gates: `rankable=false`, `paper_score_eligible=false`, `physical_qualified=false`."])
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    coverage = {
        "scope": report["scope"],
        "qualification": report["qualification"],
        "dimension_status": {row["metric_id"]: row["status"] for row in rows},
        "support": {row["metric_id"]: {"count": row["support_count"], "denominator": row["denominator"], "weight": row["support_weight"]} for row in rows},
        "composite_status": report["composite"]["status"],
    }
    coverage_path.write_text(json.dumps(coverage, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    provenance_path.write_text(json.dumps(report["provenance"], ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest_path.write_text(json.dumps({"schema": PIPELINE_SCHEMA, **report["provenance"], "scope": report["scope"]}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    outputs = [report_path, metrics_path, markdown_path, coverage_path, provenance_path, manifest_path]
    if input_payload is not None:
        input_path = destination / "input_package.json"
        input_path.write_text(json.dumps(input_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        outputs.append(input_path)
    if additional_json is not None:
        if not isinstance(additional_json, Mapping):
            raise ResultsPipelineError("additional_json must be a mapping")
        for filename, value in additional_json.items():
            if not isinstance(filename, str) or not filename.endswith(".json") or not filename or "/" in filename or "\\" in filename or filename in {".", ".."}:
                raise ResultsPipelineError(f"unsafe additional JSON filename: {filename}")
            path = destination / filename
            if path.exists():
                raise ResultsPipelineError(f"additional JSON output already exists: {filename}")
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            outputs.append(path)
    file_manifest = {
        "schema": "heterocoord.results-output-manifest.v1",
        "report_digest": hashlib.sha256(report_path.read_bytes()).hexdigest(),
        "files": {path.name: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size_bytes": path.stat().st_size} for path in outputs},
    }
    output_manifest_path = destination / "output_manifest.json"
    output_manifest_path.write_text(json.dumps(file_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    outputs.append(output_manifest_path)
    return tuple(outputs)


def _validate_report(report: Mapping[str, Any], *, require_sanity: bool = True) -> None:
    if not isinstance(report, Mapping) or report.get("schema") != "heterocoord.results-report.v1":
        raise ResultsPipelineError("report schema mismatch")
    if report.get("pipeline_version") != PIPELINE_VERSION:
        raise ResultsPipelineError("report pipeline version mismatch")
    if report.get("qualification") != "diagnostic_only":
        raise ResultsPipelineError("only diagnostic reports can be exported by this pipeline")
    if report.get("formal_gates") != {"rankable": False, "paper_score_eligible": False, "physical_qualified": False}:
        raise ResultsPipelineError("report formal gates must remain closed")
    provenance = report.get("provenance")
    if not isinstance(provenance, Mapping) or not all(str(provenance.get(key, "")).strip() for key in ("paper_commit", "scorebook_digest", "catalog_digest", "plan_digest", "input_digest")):
        raise ResultsPipelineError("report provenance is incomplete")
    if provenance.get("paper_commit") != PAPER_COMMIT or provenance.get("scorebook_digest") != default_scorebook_v3().digest():
        raise ResultsPipelineError("report provenance is stale")
    dimensions = report.get("dimensions")
    if not isinstance(dimensions, list) or {item.get("score_id") for item in dimensions if isinstance(item, Mapping)} != set(DIMENSIONS):
        raise ResultsPipelineError("report dimension set is incomplete")
    sanity = report.get("sanity_checks")
    if require_sanity and (not isinstance(sanity, Mapping) or sanity.get("status") != "pass" or not isinstance(sanity.get("checks"), list) or not sanity["checks"] or not all(isinstance(item, Mapping) and item.get("passed") is True for item in sanity["checks"])):
        raise ResultsPipelineError("report scientific sanity checks are incomplete")


def verify_export(destination: str | Path) -> dict[str, Any]:
    """Verify an exported directory without trusting copied hashes."""

    destination = Path(destination)
    manifest_path = destination / "output_manifest.json"
    if not manifest_path.is_file():
        raise ResultsPipelineError("output manifest is missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResultsPipelineError("output manifest is unreadable") from exc
    if manifest.get("schema") != "heterocoord.results-output-manifest.v1" or not isinstance(manifest.get("files"), Mapping):
        raise ResultsPipelineError("output manifest schema mismatch")
    required_files = {"report.json", "metrics.csv", "metrics.md", "coverage_report.json", "provenance.json", "input_manifest.json"}
    if not required_files.issubset(set(manifest["files"])):
        raise ResultsPipelineError("output manifest omits required files")
    for name, expected in manifest["files"].items():
        name = str(name)
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            raise ResultsPipelineError(f"unsafe output manifest path: {name}")
        path = destination / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size != expected.get("size_bytes"):
            raise ResultsPipelineError(f"output file missing or size mismatch: {name}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected.get("sha256"):
            raise ResultsPipelineError(f"output file digest mismatch: {name}")
    report_path = destination / "report.json"
    if hashlib.sha256(report_path.read_bytes()).hexdigest() != manifest.get("report_digest"):
        raise ResultsPipelineError("report digest mismatch")
    input_path = destination / "input_package.json"
    if input_path.is_file():
        try:
            input_payload = json.loads(input_path.read_text(encoding="utf-8"))
            persisted_report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ResultsPipelineError("replay input or report is unreadable") from exc
        _validate_report(persisted_report, require_sanity=False)
        recomputed = compute_results(input_payload)
        # Reports emitted before the sanity-check field was introduced remain
        # verifiable as historical artifacts; all new exports include it.
        if "sanity_checks" not in persisted_report:
            recomputed.pop("sanity_checks", None)
        # Interpreter version is provenance, not a scientific input.  Permit
        # replay in a different supported environment while requiring every
        # paper value, status, gate and digest to remain identical.
        persisted_for_compare = json.loads(json.dumps(persisted_report))
        recomputed_for_compare = json.loads(json.dumps(recomputed))
        for value in (persisted_for_compare, recomputed_for_compare):
            estimator = value.get("provenance", {}).get("estimator", {})
            if isinstance(estimator, dict):
                estimator.pop("python", None)
        if recomputed_for_compare != persisted_for_compare:
            raise ResultsPipelineError("report does not match replay input")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", type=Path, help="normalized results package JSON")
    parser.add_argument("--synthetic", action="store_true", help="use the deterministic diagnostic fixture")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.synthetic == bool(args.input):
        parser.error("choose exactly one of --synthetic or input JSON")
    try:
        payload = build_synthetic_fixture() if args.synthetic else json.loads(args.input.read_text(encoding="utf-8"))
        report = compute_results(payload)
        export_results(report, args.output, input_payload=payload)
    except (OSError, json.JSONDecodeError, ResultsPipelineError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["PIPELINE_SCHEMA", "PIPELINE_VERSION", "ResultsPipelineError", "build_synthetic_fixture", "build_negative_fixtures", "compute_results", "export_results", "verify_export"]
