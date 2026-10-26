"""Project a fixed-cell paper panel into the normalized D1--D6 input.

``paper_panel_v1`` builds and validates the registered fixed-cell directory.
This module is the next, deliberately explicit seam: it turns that directory
into the shape consumed by ``results_pipeline_v3``.  The projection is never
allowed to fall back to receipt-row means.  A panel endpoint that is unknown,
an identification range, or incomplete therefore makes the projection
``not_ready`` and carries a machine-readable reason.

The binding is intentionally verbose.  It is a statistical registration, not
an inference mechanism.  D1--D4 use scalar endpoint IDs; D5 keeps its
family/case/unit/repeat hierarchy; D6 keeps run success and resource values
in an explicit structured section.  The latter two sections are rejected if
their required structure is absent rather than being approximated from a
single aggregate endpoint.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from typing import Any, Mapping, Sequence

from .records_v3 import canonical_digest


PROJECTION_SCHEMA = "heterocoord.paper-projection.v1"
PROJECTION_BINDING_SCHEMA = "heterocoord.paper-projection-binding.v1"
_DIMENSIONS = ("D1", "D2", "D3", "D4", "D5", "D6")


class PaperProjectionError(ValueError):
    """Raised when a registered panel cannot be projected safely."""


class PaperProjectionNotReady(PaperProjectionError):
    """The panel is valid but cannot yet identify a paper input."""


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _finite(value: Any, name: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise PaperProjectionError(f"{name} must be a finite real")
    result = float(value)
    if minimum is not None and result < minimum:
        raise PaperProjectionError(f"{name} is below its lower bound")
    if maximum is not None and result > maximum:
        raise PaperProjectionError(f"{name} is above its upper bound")
    return result


def _exact_keys(value: Mapping[str, Any], required: set[str], name: str, optional: set[str] = set()) -> None:
    if not isinstance(value, Mapping) or set(value) - required - optional or not required.issubset(value):
        raise PaperProjectionError(f"{name} schema mismatch")


def _validate_package(package: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(package, Mapping):
        raise PaperProjectionError("paper panel package must be an object")
    required = {"schema", "panel_version", "status", "endpoints", "package_digest"}
    _exact_keys(package, required, "paper panel package", {"formal_gates", "structured"})
    if package["schema"] != "heterocoord.paper-panel-package.v1":
        raise PaperProjectionError("unsupported paper panel package schema")
    if package["status"] not in {"ready", "incomplete"}:
        raise PaperProjectionError("unsupported paper panel package status")
    if not isinstance(package["endpoints"], Mapping) or not package["endpoints"]:
        raise PaperProjectionError("paper panel package endpoints are required")
    stored = package["package_digest"]
    if not isinstance(stored, str) or _digest({k: v for k, v in package.items() if k != "package_digest"}) != stored:
        raise PaperProjectionError("paper panel package digest mismatch")
    return deepcopy(dict(package))


def _estimate(package: Mapping[str, Any], endpoint_id: str, name: str) -> Mapping[str, Any]:
    if not isinstance(endpoint_id, str) or not endpoint_id.strip():
        raise PaperProjectionError(f"{name} endpoint ID is required")
    endpoints = package["endpoints"]
    if endpoint_id not in endpoints or not isinstance(endpoints[endpoint_id], Mapping):
        raise PaperProjectionError(f"unknown panel endpoint: {endpoint_id}")
    entry = endpoints[endpoint_id]
    estimate = entry.get("estimate")
    if not isinstance(estimate, Mapping):
        raise PaperProjectionError(f"{endpoint_id} is missing an estimate")
    status = estimate.get("status")
    if status != "valid":
        raise PaperProjectionNotReady(f"{endpoint_id}:{status or 'missing'}")
    point = _finite(estimate.get("point"), f"{endpoint_id}.estimate.point", minimum=0.0, maximum=1.0)
    if estimate.get("lower") != point or estimate.get("upper") != point:
        raise PaperProjectionNotReady(f"{endpoint_id}:non-degenerate-identification")
    return entry


def _point(package: Mapping[str, Any], endpoint_id: str, name: str) -> float:
    estimate = _estimate(package, endpoint_id, name)["estimate"]
    return _finite(estimate["point"], f"{name}.point", minimum=0.0, maximum=1.0)


def _project_d1(package: Mapping[str, Any], binding: Mapping[str, Any]) -> dict[str, Any]:
    _exact_keys(binding, {"validity", "organization", "infeasible", "complete_plan"}, "D1 binding")
    values = {key: _point(package, binding[key], f"D1.{key}") for key in ("validity", "organization", "infeasible")}
    complete = binding["complete_plan"]
    if type(complete) is bool:
        plan = complete
    else:
        value = _point(package, complete, "D1.complete_plan")
        if value not in {0.0, 1.0}:
            raise PaperProjectionNotReady("D1.complete_plan:non-binary-panel-result")
        plan = bool(value)
    return {**values, "complete_plan": plan}


def _project_d2(package: Mapping[str, Any], binding: Mapping[str, Any]) -> dict[str, Any]:
    _exact_keys(binding, {"nominal", "restricted"}, "D2 binding")
    if not isinstance(binding["restricted"], list) or len(binding["restricted"]) != 3:
        raise PaperProjectionError("D2.restricted binding must contain three endpoints")
    return {"nominal": _point(package, binding["nominal"], "D2.nominal"),
            "restricted": [_point(package, item, f"D2.restricted[{i}]") for i, item in enumerate(binding["restricted"])]}


def _project_d3(package: Mapping[str, Any], binding: Mapping[str, Any]) -> dict[str, Any]:
    _exact_keys(binding, {"p2", "p3j", "p3s", "p4"}, "D3 binding")
    return {key: _point(package, binding[key], f"D3.{key}") for key in ("p2", "p3j", "p3s", "p4")}


def _project_d4(package: Mapping[str, Any], binding: Mapping[str, Any]) -> dict[str, Any]:
    _exact_keys(binding, {"items"}, "D4 binding")
    items = binding["items"]
    if not isinstance(items, list) or not items:
        raise PaperProjectionError("D4.items binding must be non-empty")
    projected = []
    for index, item in enumerate(items):
        if not isinstance(item, Mapping) or "kind" not in item or "e" not in item:
            raise PaperProjectionError(f"D4.items[{index}] binding is invalid")
        kind = item["kind"]
        if kind not in {"deadline", "time_valued", "continuous_supply"}:
            raise PaperProjectionError(f"unsupported D4 item kind: {kind}")
        row = {"kind": kind, "e": _point(package, item["e"], f"D4.items[{index}].e")}
        if kind != "deadline":
            if "phi" not in item:
                raise PaperProjectionError(f"D4.items[{index}].phi endpoint is required")
            row["phi"] = _point(package, item["phi"], f"D4.items[{index}].phi")
        projected.append(row)
    return {"expected_count": len(projected), "items": projected}


def _repeat_values(package: Mapping[str, Any], endpoint_id: str, name: str) -> list[float]:
    entry = _estimate(package, endpoint_id, name)
    directory = entry.get("directory")
    if not isinstance(directory, Mapping):
        raise PaperProjectionNotReady(f"{endpoint_id}:directory-required-for-repeat-projection")
    repeats = directory.get("repeat_ids")
    cells = directory.get("cells")
    if not isinstance(repeats, list) or len(repeats) != 3 or not isinstance(cells, Mapping) or len(cells) != 1:
        raise PaperProjectionNotReady(f"{endpoint_id}:D5-unit-must-have-one-cell-and-three-repeats")
    cell = next(iter(cells.values()))
    if not isinstance(cell, Mapping) or len(cell.get("infrastructure_valid_instances", ())) != 1:
        raise PaperProjectionNotReady(f"{endpoint_id}:D5-unit-must-have-one-base-instance")
    records = cell.get("records") if isinstance(cell, Mapping) else None
    if not isinstance(records, list):
        raise PaperProjectionNotReady(f"{endpoint_id}:D5-unit-records-missing")
    by_repeat = {row.get("repeat_id"): row for row in records if isinstance(row, Mapping)}
    values = []
    for repeat in repeats:
        row = by_repeat.get(repeat)
        if not isinstance(row, Mapping) or row.get("status") != "valid":
            raise PaperProjectionNotReady(f"{endpoint_id}:repeat-{repeat}-not-valid")
        values.append(_finite(row.get("value"), f"{endpoint_id}.{repeat}", minimum=0.0, maximum=1.0))
    return values


def _project_d5(package: Mapping[str, Any], binding: Mapping[str, Any]) -> dict[str, Any]:
    _exact_keys(binding, {"min_cases_per_family", "families"}, "D5 binding")
    if type(binding["min_cases_per_family"]) is not int or binding["min_cases_per_family"] < 1:
        raise PaperProjectionError("D5.min_cases_per_family is invalid")
    families = binding["families"]
    if not isinstance(families, Mapping) or not families:
        raise PaperProjectionError("D5.families binding is required")
    projected = {}
    for family_id, family in families.items():
        if not isinstance(family, Mapping) or not isinstance(family.get("cases"), Mapping):
            raise PaperProjectionError(f"D5 family {family_id} binding is invalid")
        cases_out = {}
        for case_id, case in family["cases"].items():
            units = case.get("units") if isinstance(case, Mapping) else None
            if not isinstance(units, Mapping) or not units:
                raise PaperProjectionError(f"D5 case {family_id}/{case_id} units are required")
            cases_out[case_id] = {unit_id: _repeat_values(package, endpoint_id, f"D5.{family_id}/{case_id}/{unit_id}")
                                  for unit_id, endpoint_id in units.items()}
        projected[family_id] = cases_out
    return {"min_cases_per_family": binding["min_cases_per_family"], "families": projected}


def _project_d6(package: Mapping[str, Any], binding: Mapping[str, Any]) -> dict[str, Any]:
    new_schema = "calibration" in binding
    if new_schema:
        _exact_keys(binding, {"calibration", "runs"}, "D6 binding")
    else:
        _exact_keys(binding, {"reference_bits", "reference_time", "runs"}, "D6 binding")
    structured = package.get("structured")
    if not isinstance(structured, Mapping) or not isinstance(structured.get("d6"), Mapping):
        raise PaperProjectionNotReady("D6:structured-runs-required")
    source = structured["d6"]
    if source.get("binding_digest") != _digest(binding):
        raise PaperProjectionError("D6 structured section is not bound to the projection")
    runs = source.get("runs")
    if not isinstance(runs, list) or len(runs) != len(binding["runs"]):
        raise PaperProjectionNotReady("D6:run-directory-missing-or-count-mismatch")
    if new_schema:
        calibration = binding["calibration"]
        if not isinstance(calibration, list) or not calibration:
            raise PaperProjectionError("D6 calibration table must be a non-empty list")
        by_instance: dict[str, Mapping[str, Any]] = {}
        for index, row in enumerate(calibration):
            if not isinstance(row, Mapping) or set(row) != {"instance_id", "reference_bits", "reference_time"}:
                raise PaperProjectionError(f"D6 calibration row {index} is invalid")
            instance_id = row["instance_id"]
            if not isinstance(instance_id, str) or not instance_id.strip():
                raise PaperProjectionError(f"D6 calibration row {index} has invalid instance_id")
            if instance_id in by_instance:
                raise PaperProjectionError(f"D6 calibration duplicate instance_id: {instance_id}")
            by_instance[instance_id] = row
            _finite(row["reference_bits"], f"D6.calibration[{index}].reference_bits", minimum=0.0)
            _finite(row["reference_time"], f"D6.calibration[{index}].reference_time", minimum=0.0)
        source_calibration = source.get("calibration")
        if source_calibration != calibration:
            raise PaperProjectionError("D6 structured calibration is not bound to the projection")
        binding_run_instances = []
        for index, row in enumerate(binding["runs"]):
            if not isinstance(row, Mapping) or set(row) != {"instance_id"}:
                raise PaperProjectionError(f"D6 binding run {index} is invalid")
            instance_id = row["instance_id"]
            if not isinstance(instance_id, str) or not instance_id.strip():
                raise PaperProjectionError(f"D6 binding run {index} has invalid instance_id")
            binding_run_instances.append(instance_id)
        projected_runs = []
        for index, row in enumerate(runs):
            if not isinstance(row, Mapping) or set(row) != {"instance_id", "success", "bits", "time"} or type(row["success"]) is not bool:
                raise PaperProjectionError(f"D6 structured run {index} is invalid")
            if row["instance_id"] != binding_run_instances[index]:
                raise PaperProjectionError(f"D6 structured run {index} instance_id does not match binding")
            if row["instance_id"] not in by_instance:
                raise PaperProjectionError(f"D6 run {index} has no calibration for instance {row['instance_id']}")
            _finite(row["bits"], f"D6.runs[{index}].bits", minimum=0.0)
            _finite(row["time"], f"D6.runs[{index}].time", minimum=0.0)
            projected_runs.append(deepcopy(dict(row)))
        if {row["instance_id"] for row in projected_runs} != set(by_instance):
            raise PaperProjectionError("D6 running instances do not match calibration table")
        return {"expected_count": len(projected_runs), "calibration": deepcopy(calibration), "runs": projected_runs}
    for index, row in enumerate(runs):
        if not isinstance(row, Mapping) or set(row) != {"success", "bits", "time"} or type(row["success"]) is not bool:
            raise PaperProjectionError(f"D6 structured run {index} is invalid")
        _finite(row["bits"], f"D6.runs[{index}].bits", minimum=0.0)
        _finite(row["time"], f"D6.runs[{index}].time", minimum=0.0)
    reference = source.get("reference")
    if not isinstance(reference, Mapping) or set(reference) != {"bits", "time"}:
        raise PaperProjectionNotReady("D6:independent-reference-missing")
    return {"expected_count": len(runs), "reference_bits": _finite(reference["bits"], "D6.reference_bits", minimum=0.0),
            "reference_time": _finite(reference["time"], "D6.reference_time", minimum=0.0), "runs": deepcopy(runs)}


def project_panel_package(package: Mapping[str, Any], binding: Mapping[str, Any]) -> dict[str, Any]:
    """Project a complete fixed-panel package into normalized dimensions.

    The returned object is suitable for the ``dimensions`` field of a
    normalized results package.  ``status=not_ready`` is returned only for a
    valid package whose declared support is not yet sufficient; malformed or
    digest-invalid inputs raise immediately.
    """

    package = _validate_package(package)
    if not isinstance(binding, Mapping):
        raise PaperProjectionError("projection binding must be an object")
    _exact_keys(binding, {"schema", "paper_panel_package_digest", "dimensions"}, "projection binding")
    if binding["schema"] != PROJECTION_BINDING_SCHEMA:
        raise PaperProjectionError("unsupported projection binding schema")
    if binding["paper_panel_package_digest"] != package["package_digest"]:
        raise PaperProjectionError("projection binding does not match paper panel package")
    dimensions = binding["dimensions"]
    if not isinstance(dimensions, Mapping) or set(dimensions) != set(_DIMENSIONS):
        raise PaperProjectionError("projection binding must declare exactly D1 through D6")
    try:
        projected = {
            "D1": _project_d1(package, dimensions["D1"]),
            "D2": _project_d2(package, dimensions["D2"]),
            "D3": _project_d3(package, dimensions["D3"]),
            "D4": _project_d4(package, dimensions["D4"]),
            "D5": _project_d5(package, dimensions["D5"]),
            "D6": _project_d6(package, dimensions["D6"]),
        }
    except PaperProjectionNotReady as exc:
        return {"schema": PROJECTION_SCHEMA, "status": "not_ready", "reason": str(exc),
                "paper_panel_package_digest": package["package_digest"],
                "projection_binding_digest": _digest(binding)}
    result = {"schema": PROJECTION_SCHEMA, "status": "ready", "dimensions": projected,
              "paper_panel_package_digest": package["package_digest"],
              "projection_binding_digest": _digest(binding)}
    result["projection_digest"] = _digest(result)
    return result


def apply_panel_projection(normalized: Mapping[str, Any], projection: Mapping[str, Any]) -> dict[str, Any]:
    """Replace normalized dimensions with a ready panel projection.

    This is intentionally a separate operation from ``normalize_receipts``:
    the receipt adapter remains a strict raw-fact validator, while this
    function makes the registered paper estimand the authoritative input to
    ``compute_results``.  It also recomputes the normalized input digest.
    """

    if not isinstance(normalized, Mapping):
        raise PaperProjectionError("normalized package must be an object")
    if not isinstance(projection, Mapping) or projection.get("schema") != PROJECTION_SCHEMA or projection.get("status") != "ready":
        raise PaperProjectionNotReady("panel projection is not ready")
    projected_dimensions = projection.get("dimensions")
    if not isinstance(projected_dimensions, Mapping) or set(projected_dimensions) != set(_DIMENSIONS):
        raise PaperProjectionError("ready projection must contain all D1 through D6 dimensions")
    result = deepcopy(dict(normalized))
    result["dimensions"] = deepcopy(dict(projected_dimensions))
    result["paper_projection_digest"] = projection.get("projection_digest")
    identity = {
        "catalog": result["catalog"],
        "plan": result["plan"],
        "parameters": result["parameters"],
        "dimensions": result["dimensions"],
        "pipeline_version": result.get("pipeline_version", "results-pipeline-v3"),
    }
    # Keep this identity list exactly aligned with results_pipeline_v3._input_payload.
    # The projection digest is provenance for the registered estimand and must
    # participate in the same digest calculation as every other real-case
    # provenance field.
    for key in ("case_manifest_digest", "scene_digest", "semantic_map_digest", "topology_digest", "roster_digest", "contract_digest", "artifact_manifest_digest", "paper_projection_digest", "oracle_id", "evaluator_id"):
        if key in result:
            identity[key] = result[key]
    result["input_digest"] = canonical_digest(identity)
    return result


__all__ = ["PROJECTION_SCHEMA", "PROJECTION_BINDING_SCHEMA", "PaperProjectionError", "PaperProjectionNotReady", "project_panel_package", "apply_panel_projection"]
