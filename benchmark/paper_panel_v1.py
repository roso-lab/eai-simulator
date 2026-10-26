"""Fixed-cell paper aggregation for the HeteroCoord D1--D6 protocol.

This module is deliberately a seam *before* ``results_pipeline_v3``.  It turns
evaluator-owned repeat records into the paper's fixed-cell estimands without
silently changing support when records are missing.  It is CPU-only and does
not open qualification gates.

The input is a small, JSON-friendly directory:

``{
  "schema": "heterocoord.paper-panel-directory.v1",
  "endpoint": "d1.validity",
  "repeat_ids": ["r1", "r2", "r3"],
  "cells": {
    "cell-a": {
      "weight": 0.5,
      "planned_instances": 30,
      "infrastructure_valid_instances": ["i0", ...],
      "records": [
        {"base_instance_id": "i0", "repeat_id": "r1",
         "status": "valid", "value": 1.0}
      ]
    }
  }
}
```

``infrastructure_valid_instances`` is evaluator/release-owned and is kept
separate from method outcomes.  A missing or ``unknown`` repeat contributes a
fixed-weight identification range; it is never dropped or reweighted.  A cell
that fails its predeclared coverage floor is ``incomplete`` and makes the
whole fixed panel non-reportable.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import random
from typing import Any, Mapping, Sequence

from .receipt_adapter_v3 import ReceiptAdapterError, _validate_receipt


PANEL_SCHEMA = "heterocoord.paper-panel-directory.v1"
PANEL_VERSION = "paper-panel-v1"
PANEL_BINDING_SCHEMA = "heterocoord.paper-panel-binding.v1"
DEFAULT_REPEAT_IDS = ("r1", "r2", "r3")
_STATUSES = frozenset({"valid", "method_failure", "unknown"})


class PanelAggregationError(ValueError):
    """Raised when a fixed panel directory is malformed or ambiguous."""


def _binding_filter_matches(identity: Mapping[str, Any], value: Any, name: str) -> bool:
    """Match a binding filter against receipt identity without inference."""

    if not isinstance(value, Mapping) or not value:
        raise PanelAggregationError(f"{name} must be a non-empty object")
    allowed = {
        "task_id", "anchor_id", "domain", "difficulty", "condition_id", "system_id",
        "base_instance_id", "repeat_id", "branch", "checkpoint_id",
    }
    if set(value) - allowed:
        raise PanelAggregationError(f"{name} contains an unsupported identity field")
    for key, expected in value.items():
        if not isinstance(expected, str) or not expected.strip():
            raise PanelAggregationError(f"{name}.{key} must be non-empty text")
        if identity.get(key) != expected:
            return False
    return True


def _extract_panel_value(receipt: Mapping[str, Any], value_path: str) -> tuple[str, float | None]:
    """Extract one registered [0,1] endpoint from evaluator-owned facts.

    The grammar is intentionally finite.  It prevents a caller from naming an
    arbitrary JSON path and accidentally treating a diagnostic field as a
    paper endpoint.  A recorded ``unreached`` obligation is a known zero
    (binding record V2); ``unknown``/``incomplete`` remain unidentified.
    Neither disposition changes the evaluator's process-status record.
    """

    facts = receipt["facts"]
    if not isinstance(facts, Mapping):
        raise PanelAggregationError("receipt facts must be an object")
    value: Any
    if value_path == "facts.success":
        value = facts.get("success")
    elif value_path == "facts.d5_attempt.success":
        attempt = facts.get("d5_attempt")
        value = attempt.get("success") if isinstance(attempt, Mapping) else None
    elif value_path == "facts.d6.success":
        d6 = facts.get("d6")
        value = d6.get("success") if isinstance(d6, Mapping) else None
    elif value_path.startswith("facts.d1."):
        field = value_path.removeprefix("facts.d1.")
        if field not in {"validity", "organization", "infeasible", "complete_plan"}:
            raise PanelAggregationError(f"unsupported panel value_path: {value_path}")
        d1 = facts.get("d1")
        value = d1.get(field) if isinstance(d1, Mapping) else None
    elif value_path.startswith("facts.obligations:"):
        key = value_path.removeprefix("facts.obligations:")
        pieces = key.split(":")
        if pieces not in (["P2"], ["P3", "J"], ["P3", "S"], ["P4"]):
            raise PanelAggregationError(f"unsupported panel value_path: {value_path}")
        obligations = facts.get("obligations")
        if not isinstance(obligations, list):
            raise PanelAggregationError("facts.obligations is required for a panel endpoint")
        matches = []
        for row in obligations:
            if not isinstance(row, Mapping):
                raise PanelAggregationError("facts.obligations contains a non-object")
            relation = row.get("relation")
            subtype = row.get("subtype")
            if pieces == ["P2",] and (relation, subtype) == ("P2", None):
                matches.append(row.get("outcome"))
            elif pieces == ["P3", "J"] and (relation, subtype) == ("P3", "J"):
                matches.append(row.get("outcome"))
            elif pieces == ["P3", "S"] and (relation, subtype) == ("P3", "S"):
                matches.append(row.get("outcome"))
            elif pieces == ["P4",] and (relation, subtype) == ("P4", None):
                matches.append(row.get("outcome"))
        if len(matches) != 1:
            raise PanelAggregationError(f"panel obligation endpoint requires exactly one match: {value_path}")
        value = matches[0]
        if value in {"unknown", "incomplete"}:
            return "unknown", None
        if value not in {"completed", "violated", "unreached"}:
            raise PanelAggregationError(f"unsupported obligation outcome for {value_path}")
        return "valid", 1.0 if value == "completed" else 0.0
    elif value_path.startswith("facts.d4_items[") and "]." in value_path:
        prefix, field = value_path.split("].", 1)
        index_text = prefix.removeprefix("facts.d4_items[")
        if not index_text.isdigit() or field not in {"e", "phi"}:
            raise PanelAggregationError(f"unsupported panel value_path: {value_path}")
        items = facts.get("d4_items")
        index = int(index_text)
        if not isinstance(items, list) or index >= len(items) or not isinstance(items[index], Mapping):
            raise PanelAggregationError(f"missing D4 item for {value_path}")
        value = items[index].get(field)
    else:
        raise PanelAggregationError(f"unsupported panel value_path: {value_path}")

    if type(value) is bool:
        return "valid", 1.0 if value else 0.0
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) and 0.0 <= float(value) <= 1.0:
        return "valid", float(value)
    raise PanelAggregationError(f"panel value_path does not resolve to a [0,1] fact: {value_path}")


def build_panel_directory_from_receipts(
    receipts: Sequence[Mapping[str, Any]],
    binding: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a fixed-cell directory from individually validated raw receipts.

    ``binding`` is the registered statistical design.  It owns cell weights,
    infrastructure support, and optional identity filters; receipts only own
    the observed endpoint facts.  A missing repeat is intentionally omitted so
    the fixed-panel reducer emits an identification range.  Unexpected,
    duplicate, malformed, or multiply matched receipts fail closed.
    """

    if not isinstance(receipts, Sequence) or isinstance(receipts, (str, bytes)) or not receipts:
        raise PanelAggregationError("receipts must be a non-empty sequence")
    if not isinstance(binding, Mapping) or set(binding) != {"schema", "endpoint", "repeat_ids", "value_path", "cells"}:
        raise PanelAggregationError("panel binding schema mismatch")
    if binding["schema"] != PANEL_BINDING_SCHEMA or not isinstance(binding["endpoint"], str) or not binding["endpoint"].strip():
        raise PanelAggregationError("panel binding schema/endpoint is invalid")
    repeats = binding["repeat_ids"]
    if not isinstance(repeats, list) or not repeats or any(not isinstance(item, str) or not item.strip() for item in repeats) or len(set(repeats)) != len(repeats):
        raise PanelAggregationError("panel binding repeat_ids must be unique non-empty text")
    value_path = binding["value_path"]
    if not isinstance(value_path, str) or not value_path.strip():
        raise PanelAggregationError("panel binding value_path is required")
    source_cells = binding["cells"]
    if not isinstance(source_cells, Mapping) or not source_cells:
        raise PanelAggregationError("panel binding cells must be a non-empty mapping")

    cells: dict[str, dict[str, Any]] = {}
    for cell_id, source in source_cells.items():
        if not isinstance(cell_id, str) or not cell_id.strip() or not isinstance(source, Mapping):
            raise PanelAggregationError("panel binding cell identity/value is invalid")
        expected = {"weight", "planned_instances", "infrastructure_valid_instances"}
        optional = {"min_valid_instances", "min_coverage_fraction", "receipt_filter"}
        if set(source) - expected - optional or not expected.issubset(source):
            raise PanelAggregationError(f"panel binding cell {cell_id} schema mismatch")
        row = {key: source[key] for key in expected}
        for key in ("min_valid_instances", "min_coverage_fraction"):
            if key in source:
                row[key] = source[key]
        if "receipt_filter" in source:
            _binding_filter_matches({}, source["receipt_filter"], f"cell {cell_id}.receipt_filter")
            row["receipt_filter"] = dict(source["receipt_filter"])
        row["records"] = []
        cells[cell_id] = row

    seen: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(receipts):
        if not isinstance(raw, Mapping):
            raise PanelAggregationError(f"receipts[{index}] must be an object")
        try:
            receipt = _validate_receipt(raw)
        except (ReceiptAdapterError, TypeError, ValueError) as exc:
            raise PanelAggregationError(f"receipts[{index}] is not a valid raw receipt: {exc}") from exc
        identity = receipt["identity"]
        base = identity["base_instance_id"]
        repeat = identity["repeat_id"]
        if repeat not in repeats:
            raise PanelAggregationError(f"receipt[{index}] uses an unregistered repeat: {repeat}")
        matches = []
        for cell_id, cell in cells.items():
            if base not in set(cell["infrastructure_valid_instances"]):
                continue
            if "receipt_filter" not in cell or _binding_filter_matches(identity, cell["receipt_filter"], f"cell {cell_id}.receipt_filter"):
                matches.append(cell_id)
        if len(matches) != 1:
            raise PanelAggregationError(f"receipt[{index}] maps to {len(matches)} panel cells; expected exactly one")
        cell_id = matches[0]
        key = (cell_id, base, repeat)
        if key in seen:
            raise PanelAggregationError(f"duplicate panel receipt for {cell_id}/{base}/{repeat}")
        seen.add(key)
        status, value = _extract_panel_value(receipt, value_path)
        record: dict[str, Any] = {"base_instance_id": base, "repeat_id": repeat, "status": status}
        if value is not None:
            record["value"] = value
        cells[cell_id]["records"].append(record)

    output_cells: dict[str, Any] = {}
    for cell_id, cell in cells.items():
        output_cells[cell_id] = {key: value for key, value in cell.items() if key != "receipt_filter"}
        output_cells[cell_id]["records"] = sorted(
            output_cells[cell_id]["records"],
            key=lambda row: (row["base_instance_id"], repeats.index(row["repeat_id"])),
        )
    directory = {
        "schema": PANEL_SCHEMA,
        "endpoint": binding["endpoint"],
        "repeat_ids": list(repeats),
        "cells": output_cells,
    }
    try:
        return validate_panel_directory(directory)
    except PanelAggregationError:
        raise


def build_panel_estimates_from_receipts(
    receipts: Sequence[Mapping[str, Any]],
    bindings: Mapping[str, Mapping[str, Any]],
    *,
    bootstrap_resamples: int = 0,
    bootstrap_seed: int = 0,
) -> dict[str, Any]:
    """Produce replayable fixed-panel estimates for registered endpoints.

    This is the deployable bridge for future evaluator output.  It deliberately
    keeps each endpoint's binding and directory in the package so an exported
    estimate can be audited without reconstructing the registration file from
    memory.  It does not combine endpoints into a qualification decision.
    """

    if not isinstance(bindings, Mapping) or not bindings:
        raise PanelAggregationError("panel bindings must be a non-empty mapping")
    estimates: dict[str, Any] = {}
    for endpoint_id, binding in bindings.items():
        if not isinstance(endpoint_id, str) or not endpoint_id.strip():
            raise PanelAggregationError("panel binding IDs must be non-empty text")
        directory = build_panel_directory_from_receipts(receipts, binding)
        estimate = aggregate_fixed_panel(
            directory,
            bootstrap_resamples=bootstrap_resamples,
            bootstrap_seed=bootstrap_seed,
        )
        estimates[endpoint_id] = {
            "binding_digest": _digest(binding),
            "directory": directory,
            "estimate": estimate,
        }
    package = {
        "schema": "heterocoord.paper-panel-package.v1",
        "panel_version": PANEL_VERSION,
        "status": "incomplete" if any(item["estimate"]["status"] == "incomplete" for item in estimates.values()) else "ready",
        "endpoints": estimates,
    }
    package["package_digest"] = _digest(package)
    return package


@dataclass(frozen=True)
class _CellResult:
    cell_id: str
    weight: float
    planned_instances: int
    infrastructure_valid_count: int
    coverage_fraction: float
    reportable: bool
    status: str
    point: float | None
    lower: float | None
    upper: float | None
    known_instance_count: int
    unknown_instance_count: int
    instance_values: tuple[float, ...]
    lower_instance_values: tuple[float, ...]
    upper_instance_values: tuple[float, ...]
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "cell_id": self.cell_id,
            "weight": self.weight,
            "planned_instances": self.planned_instances,
            "infrastructure_valid_count": self.infrastructure_valid_count,
            "coverage_fraction": self.coverage_fraction,
            "reportable": self.reportable,
            "status": self.status,
            "point": self.point,
            "lower": self.lower,
            "upper": self.upper,
            "known_instance_count": self.known_instance_count,
            "unknown_instance_count": self.unknown_instance_count,
            "reason": self.reason,
        }


def _real(value: Any, name: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise PanelAggregationError(f"{name} must be a finite real")
    value = float(value)
    if minimum is not None and value < minimum:
        raise PanelAggregationError(f"{name} is below its lower bound")
    if maximum is not None and value > maximum:
        raise PanelAggregationError(f"{name} is above its upper bound")
    return value


def _int(value: Any, name: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise PanelAggregationError(f"{name} must be an integer >= {minimum}")
    return int(value)


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_record(row: Mapping[str, Any], name: str) -> tuple[str, str, str, float | None]:
    if not isinstance(row, Mapping):
        raise PanelAggregationError(f"{name} must be an object")
    required = {"base_instance_id", "repeat_id", "status"}
    if not required.issubset(row) or set(row) - required - {"value"}:
        raise PanelAggregationError(f"{name} schema mismatch")
    base = row["base_instance_id"]
    repeat = row["repeat_id"]
    status = row["status"]
    if not isinstance(base, str) or not base.strip() or not isinstance(repeat, str) or not repeat.strip():
        raise PanelAggregationError(f"{name} identities are required")
    if status not in _STATUSES:
        raise PanelAggregationError(f"{name}.status is unsupported")
    value = row.get("value")
    if status in {"valid", "method_failure"}:
        value = _real(value, f"{name}.value", minimum=0.0, maximum=1.0)
    elif value is not None:
        raise PanelAggregationError(f"{name}.value must be absent for unknown records")
    return base, repeat, str(status), value


def validate_panel_directory(directory: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and detach a fixed-cell directory.

    The returned object is a deep JSON detachment, so callers cannot mutate
    the input after validation and change the calculation identity.
    """

    if not isinstance(directory, Mapping):
        raise PanelAggregationError("panel directory must be an object")
    required = {"schema", "endpoint", "repeat_ids", "cells"}
    if set(directory) != required:
        raise PanelAggregationError("panel directory schema mismatch")
    if directory["schema"] != PANEL_SCHEMA or not isinstance(directory["endpoint"], str) or not directory["endpoint"].strip():
        raise PanelAggregationError("panel schema/endpoint is invalid")
    repeat_ids = directory["repeat_ids"]
    if not isinstance(repeat_ids, list) or not repeat_ids or any(not isinstance(item, str) or not item.strip() for item in repeat_ids):
        raise PanelAggregationError("repeat_ids must be a non-empty list of text")
    if len(set(repeat_ids)) != len(repeat_ids):
        raise PanelAggregationError("repeat_ids must be unique")
    cells = directory["cells"]
    if not isinstance(cells, Mapping) or not cells:
        raise PanelAggregationError("cells must be a non-empty mapping")
    detached: dict[str, Any] = {"schema": PANEL_SCHEMA, "endpoint": directory["endpoint"], "repeat_ids": list(repeat_ids), "cells": {}}
    weight_sum = 0.0
    for cell_id, cell in cells.items():
        if not isinstance(cell_id, str) or not cell_id.strip() or not isinstance(cell, Mapping):
            raise PanelAggregationError("cell identity/value is invalid")
        expected = {"weight", "planned_instances", "infrastructure_valid_instances", "records"}
        optional = {"min_valid_instances", "min_coverage_fraction"}
        if set(cell) - expected - optional or not expected.issubset(cell):
            raise PanelAggregationError(f"cell {cell_id} schema mismatch")
        weight = _real(cell["weight"], f"cell {cell_id}.weight", minimum=0.0)
        planned = _int(cell["planned_instances"], f"cell {cell_id}.planned_instances", minimum=1)
        valid_instances = cell["infrastructure_valid_instances"]
        if not isinstance(valid_instances, list) or not valid_instances or any(not isinstance(item, str) or not item.strip() for item in valid_instances):
            raise PanelAggregationError(f"cell {cell_id}.infrastructure_valid_instances is invalid")
        if len(set(valid_instances)) != len(valid_instances) or len(valid_instances) > planned:
            raise PanelAggregationError(f"cell {cell_id} infrastructure support is invalid")
        min_valid = _int(cell.get("min_valid_instances", 30), f"cell {cell_id}.min_valid_instances", minimum=1)
        min_fraction = _real(cell.get("min_coverage_fraction", 0.80), f"cell {cell_id}.min_coverage_fraction", minimum=0.0, maximum=1.0)
        records = cell["records"]
        if not isinstance(records, list):
            raise PanelAggregationError(f"cell {cell_id}.records must be a list")
        rows: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        valid_set = set(valid_instances)
        for index, record in enumerate(records):
            base, repeat, status, value = _validate_record(record, f"cell {cell_id}.records[{index}]")
            if base not in valid_set:
                raise PanelAggregationError(f"cell {cell_id} record references non-infrastructure-valid instance")
            if repeat not in repeat_ids:
                raise PanelAggregationError(f"cell {cell_id} record references unknown repeat")
            key = (base, repeat)
            if key in seen:
                raise PanelAggregationError(f"cell {cell_id} has duplicate base/repeat record")
            seen.add(key)
            row = {"base_instance_id": base, "repeat_id": repeat, "status": status}
            if value is not None:
                row["value"] = value
            rows.append(row)
        detached["cells"][cell_id] = {
            "weight": weight,
            "planned_instances": planned,
            "infrastructure_valid_instances": list(valid_instances),
            "min_valid_instances": min_valid,
            "min_coverage_fraction": min_fraction,
            "records": rows,
        }
        weight_sum += weight
    if not math.isclose(weight_sum, 1.0, rel_tol=1e-9, abs_tol=1e-9):
        raise PanelAggregationError("fixed cell weights must sum to one")
    return detached


def _cell_result(cell_id: str, cell: Mapping[str, Any], repeat_ids: Sequence[str]) -> _CellResult:
    valid_instances = tuple(cell["infrastructure_valid_instances"])
    planned = int(cell["planned_instances"])
    valid_count = len(valid_instances)
    fraction = valid_count / planned
    reportable = valid_count >= int(cell["min_valid_instances"]) and fraction >= float(cell["min_coverage_fraction"])
    grouped: dict[str, dict[str, tuple[str, float | None]]] = {base: {} for base in valid_instances}
    for row in cell["records"]:
        grouped[row["base_instance_id"]][row["repeat_id"]] = (row["status"], row.get("value"))
    lower_values: list[float] = []
    upper_values: list[float] = []
    known_values: list[float] = []
    unknown_instances = 0
    for base in valid_instances:
        lows = 0.0
        highs = 0.0
        has_unknown = False
        for repeat in repeat_ids:
            status, value = grouped[base].get(repeat, ("unknown", None))
            if status == "unknown":
                has_unknown = True
                highs += 1.0
            else:
                assert value is not None
                lows += value
                highs += value
        denominator = float(len(repeat_ids))
        low = lows / denominator
        high = highs / denominator
        lower_values.append(low)
        upper_values.append(high)
        if has_unknown:
            unknown_instances += 1
        else:
            known_values.append(low)
    if not reportable:
        return _CellResult(cell_id, float(cell["weight"]), planned, valid_count, fraction, False, "incomplete", None, None, None,
                           len(known_values), unknown_instances, tuple(known_values), tuple(lower_values), tuple(upper_values), "coverage_floor")
    lower = sum(lower_values) / valid_count
    upper = sum(upper_values) / valid_count
    if unknown_instances:
        return _CellResult(cell_id, float(cell["weight"]), planned, valid_count, fraction, True, "range", None, lower, upper,
                           len(known_values), unknown_instances, tuple(known_values), tuple(lower_values), tuple(upper_values), "unknown_repeat_or_missing")
    point = lower
    return _CellResult(cell_id, float(cell["weight"]), planned, valid_count, fraction, True, "valid", point, point, point,
                       len(known_values), 0, tuple(known_values), tuple(lower_values), tuple(upper_values), None)


def aggregate_fixed_panel(directory: Mapping[str, Any], *, bootstrap_resamples: int = 0, bootstrap_seed: int = 0) -> dict[str, Any]:
    """Aggregate one scalar endpoint with fixed cell weights.

    ``valid`` means every required repeat is known. ``range`` means coverage
    passed but one or more fixed-weight records remain unknown. ``incomplete``
    means at least one preregistered cell failed its coverage floor; no panel
    point is emitted and no remaining-cell reweighting occurs.
    """

    if type(bootstrap_resamples) is not int or bootstrap_resamples < 0:
        raise PanelAggregationError("bootstrap_resamples must be a nonnegative integer")
    if type(bootstrap_seed) is not int:
        raise PanelAggregationError("bootstrap_seed must be an integer")
    validated = validate_panel_directory(directory)
    repeat_ids = validated["repeat_ids"]
    cells = tuple(_cell_result(cell_id, cell, repeat_ids) for cell_id, cell in validated["cells"].items())
    cell_rows = [cell.to_dict() for cell in cells]
    if any(cell.status == "incomplete" for cell in cells):
        return {"schema": "heterocoord.paper-panel-estimate.v1", "panel_version": PANEL_VERSION, "endpoint": validated["endpoint"],
                "status": "incomplete", "point": None, "lower": None, "upper": None,
                "cell_count": len(cells), "reportable_cell_count": sum(cell.reportable for cell in cells),
                "unknown_instance_count": sum(cell.unknown_instance_count for cell in cells), "cells": cell_rows,
                "reason": "coverage_floor"}
    lower = sum(cell.weight * float(cell.lower) for cell in cells)
    upper = sum(cell.weight * float(cell.upper) for cell in cells)
    unknown = sum(cell.unknown_instance_count for cell in cells)
    result: dict[str, Any] = {"schema": "heterocoord.paper-panel-estimate.v1", "panel_version": PANEL_VERSION, "endpoint": validated["endpoint"],
                              "directory_digest": _digest(validated),
                              "status": "range" if unknown else "valid", "point": lower if not unknown else None,
                              "lower": lower, "upper": upper, "cell_count": len(cells), "reportable_cell_count": len(cells),
                              "unknown_instance_count": unknown, "cells": cell_rows,
                              "reason": "unknown_repeat_or_missing" if unknown else None}
    # A cluster interval is a sampling-uncertainty object.  It cannot be
    # attached to a panel whose endpoint is still an identification range;
    # callers must resolve unknown records first and rerun the estimator.
    if bootstrap_resamples and unknown == 0:
        rng = random.Random(bootstrap_seed)
        draws: list[float] = []
        for _ in range(bootstrap_resamples):
            draw = 0.0
            for cell in cells:
                values = cell.instance_values
                if not values:
                    continue
                draw += cell.weight * sum(rng.choice(values) for _ in values) / len(values)
            draws.append(draw)
        draws.sort()
        lo_index = max(0, int(math.floor(0.025 * (len(draws) - 1))))
        hi_index = min(len(draws) - 1, int(math.ceil(0.975 * (len(draws) - 1))))
        result["cluster_bootstrap"] = {"resamples": bootstrap_resamples, "seed": bootstrap_seed,
                                       "lower": 100.0 * draws[lo_index], "upper": 100.0 * draws[hi_index],
                                       "unit": "base_instance_within_cell", "status": "valid"}
    return result


def aggregate_d3_relation_panels(relation_directories: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Combine P2/P3-J/P3-S/P4 panels using the paper's non-double-counting weights."""

    required = ("p2", "p3j", "p3s", "p4")
    if set(relation_directories) != set(required):
        raise PanelAggregationError("D3 relation panels must contain exactly p2, p3j, p3s, p4")
    estimates = {key: aggregate_fixed_panel(relation_directories[key]) for key in required}
    if any(item["status"] == "incomplete" for item in estimates.values()):
        return {"schema": "heterocoord.paper-panel-d3.v1", "status": "incomplete", "point": None, "lower": None, "upper": None,
                "relations": estimates, "reason": "relation_panel_incomplete"}
    lows = {key: float(estimates[key]["lower"]) for key in required}
    ups = {key: float(estimates[key]["upper"]) for key in required}
    def score(values: Mapping[str, float]) -> float:
        return 100.0 / 3.0 * (values["p2"] + (values["p3j"] + values["p3s"]) / 2.0 + values["p4"])
    lower, upper = score(lows), score(ups)
    unknown = any(estimates[key]["status"] == "range" for key in required)
    return {"schema": "heterocoord.paper-panel-d3.v1", "status": "range" if unknown else "valid",
            "point": None if unknown else lower, "lower": lower, "upper": upper, "relations": estimates,
            "reason": "unknown_repeat_or_missing" if unknown else None}


def fixed_catalog_hoeffding_half_width(weights: Sequence[float], *, alpha: float = 0.05) -> float:
    """Return the paper-scale Hoeffding half-width for D5/D6 fixed repeats."""

    if not 0.0 < alpha < 1.0:
        raise PanelAggregationError("alpha must be in (0,1)")
    values = tuple(_real(weight, "weight", minimum=0.0) for weight in weights)
    if not values or not math.isclose(sum(values), 1.0, rel_tol=1e-9, abs_tol=1e-9):
        raise PanelAggregationError("fixed catalog weights must be nonempty and sum to one")
    return 100.0 * math.sqrt(math.log(2.0 / alpha) / 2.0 * sum(weight * weight for weight in values))


__all__ = [
    "PANEL_SCHEMA", "PANEL_BINDING_SCHEMA", "PANEL_VERSION", "PanelAggregationError",
    "validate_panel_directory", "build_panel_directory_from_receipts", "build_panel_estimates_from_receipts", "aggregate_fixed_panel",
    "aggregate_d3_relation_panels", "fixed_catalog_hoeffding_half_width",
]
