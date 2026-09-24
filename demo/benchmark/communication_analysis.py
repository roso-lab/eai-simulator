"""Fail-closed summaries for preregistered communication capacity sweeps.

The transport runtime records facts; this module only summarizes an already
materialized experiment.  It deliberately keeps the capacity axis separate
from a method's offered load and never interpolates or extrapolates a threshold
outside tested capacity points.  Aggregation is hierarchical: channel seeds
are averaged within a base instance, then base instances are the bootstrap
units.
"""

from __future__ import annotations

from collections import defaultdict
import math
import random
from typing import Any, Mapping, Sequence


SCHEMA = "eai.benchmark.communication_analysis"
VERSION = 1
CAPACITY_V2_OBSERVATION_SCHEMA = (
    "eai.benchmark.communication_capacity_observation_v2")
CAPACITY_V2_ANALYSIS_SCHEMA = "eai.benchmark.communication_capacity_analysis_v2"
CAPACITY_V2_VERSION = 2
CAPACITY_V2_METHOD_ID = "synthetic_link_delivery_capacity_v2"
CAPACITY_V2_METRIC = "synthetic_link_delivery_rate"
_CAPACITY_V2_POOL_DIRECTIONS = {
    "backhaul_uplink": "uplink",
    "p2p": "p2p",
}
_CAPACITY_V2_OBSERVATION_FIELDS = frozenset({
    "schema", "version", "method_id", "measurement_scope",
    "base_instance_id", "channel_seed", "pool", "capacity_mode",
    "capacity_unit", "capacity_bits_per_s", "runtime_capacity_unit",
    "runtime_capacity_bytes_per_s", "c0_bits_per_s", "metric",
    "metric_value", "published_count", "delivered_count",
    "metric_interpretation", "direction", "wire_size_bytes",
    "serialization_duration_s", "workload_design", "seed_role",
})


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    return value


def _number(value: Any, field: str, *, nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result) or (nonnegative and result < 0):
        suffix = " and nonnegative" if nonnegative else ""
        raise ValueError(f"{field} must be finite{suffix}")
    return result


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("cannot compute a percentile of an empty sample")
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _bootstrap(values: Sequence[float], *, seed: int, samples: int) -> list[float]:
    if not values:
        raise ValueError("bootstrap requires at least one value")
    if type(samples) is not int or samples < 1:
        raise ValueError("bootstrap_samples must be a positive integer")
    if len(values) == 1:
        return [float(values[0]), float(values[0])]
    rng = random.Random(seed)
    result = []
    for _ in range(samples):
        result.append(sum(values[rng.randrange(len(values))] for _ in values) / len(values))
    return [_percentile(result, .025), _percentile(result, .975)]


def _normalize_rows(observations: Sequence[Mapping[str, Any]], *, metric: str,
                    method_id: str | None) -> tuple[dict[tuple[str, str, float | None], float],
                                                       set[str], dict[float | None, float]]:
    if isinstance(observations, (str, bytes)) or not isinstance(observations, Sequence):
        raise ValueError("observations must be a sequence of objects")
    _text(metric, "metric")
    if method_id is not None:
        _text(method_id, "method_id")
    values: dict[tuple[str, str, float | None], float] = {}
    methods: set[str] = set()
    c0_by_capacity: dict[float | None, set[float]] = defaultdict(set)
    for row in observations:
        if not isinstance(row, Mapping):
            raise ValueError("each observation must be an object")
        base = _text(row.get("base_instance_id"), "base_instance_id")
        channel = _text(row.get("channel_seed"), "channel_seed")
        row_method = _text(row.get("method_id", "default"), "method_id")
        methods.add(row_method)
        if method_id is not None and row_method != method_id:
            raise ValueError("observations contain a different method_id")
        if "metric_value" in row:
            value = row["metric_value"]
        else:
            metrics = row.get("metrics")
            value = metrics.get(metric) if isinstance(metrics, Mapping) else None
        value = _number(value, "metric_value")
        raw_capacity = row.get("capacity_bps")
        capacity = None if raw_capacity is None else _number(raw_capacity, "capacity_bps", nonnegative=True)
        key = (base, channel, capacity)
        if key in values:
            raise ValueError(f"duplicate observation for {key!r}")
        values[key] = value
        if row.get("c0_bps") is not None:
            c0_by_capacity[capacity].add(_number(row["c0_bps"], "c0_bps", nonnegative=True))
    if not values:
        raise ValueError("observations must not be empty")
    if len(methods) > 1:
        raise ValueError("capacity summary cannot pool multiple methods")
    c0 = {}
    for capacity, candidates in c0_by_capacity.items():
        if len(candidates) > 1:
            raise ValueError("c0_bps must be frozen within a capacity point")
        c0[capacity] = next(iter(candidates))
    return values, methods, c0


def _point_values(values: Mapping[tuple[str, str, float | None], float], capacity: float | None):
    by_base: dict[str, list[float]] = defaultdict(list)
    for (base, _channel, point), value in values.items():
        if point == capacity:
            by_base[base].append(value)
    return {base: sum(rows) / len(rows) for base, rows in by_base.items()}


def summarize_capacity_curve(
    observations: Sequence[Mapping[str, Any]], *, metric: str = "completion_rate",
    method_id: str | None = None, bootstrap_samples: int = 2000, seed: int = 0,
    min_reference_mean: float | None = None, thresholds: Sequence[float] = (.9, .5),
) -> dict[str, Any]:
    """Summarize a finite capacity sweep against its explicit infinity point.

    ``capacity_bps=None`` is the required perfect-capacity reference.  Every
    finite point reports absolute capacity and, when supplied, ``C/C0``.  A
    threshold is ``bracketed`` when adjacent tested points straddle it; the
    result contains the enclosing points and no guessed value between them.
    """
    values, methods, c0_by_capacity = _normalize_rows(observations, metric=metric,
                                                       method_id=method_id)
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    if min_reference_mean is not None:
        min_reference_mean = _number(min_reference_mean, "min_reference_mean", nonnegative=True)
    qs = [_number(q, "threshold", nonnegative=True) for q in thresholds]
    if any(q > 1 for q in qs) or len(set(qs)) != len(qs):
        raise ValueError("thresholds must be distinct values in [0, 1]")
    if None not in {point for _base, _channel, point in values}:
        return {"schema": SCHEMA, "version": VERSION, "status": "missing_reference",
                "metric": metric, "method_ids": sorted(methods), "points": [],
                "thresholds": {str(q): {"status": "missing_reference"} for q in qs}}
    ref_by_base = _point_values(values, None)
    ref_mean = sum(ref_by_base.values()) / len(ref_by_base)
    reference_valid = min_reference_mean is None or ref_mean >= min_reference_mean
    finite = sorted({point for _base, _channel, point in values if point is not None})
    points = []
    for capacity in finite:
        by_base = _point_values(values, capacity)
        sample = list(by_base.values())
        mean = sum(sample) / len(sample)
        ratio = mean / ref_mean if ref_mean > 0 else None
        point = {"capacity_bps": capacity, "n_base_instances": len(sample),
                 "mean": mean, "bootstrap_ci95": _bootstrap(sample, seed=seed + len(points),
                                                              samples=bootstrap_samples),
                 "retention": ratio,
                 "capacity_ratio_c0": (capacity / c0_by_capacity[capacity]
                                        if c0_by_capacity.get(capacity) else None)}
        if ref_mean > 0:
            ratio_samples = []
            bases = sorted(set(ref_by_base) & set(by_base))
            if bases:
                rng = random.Random(seed + 1000 + len(points))
                for _ in range(bootstrap_samples if len(bases) > 1 else 1):
                    selected = [bases[rng.randrange(len(bases))] for _ in bases] if len(bases) > 1 else bases
                    ratio_samples.append(sum(by_base[b] for b in selected) / sum(ref_by_base[b] for b in selected))
                point["retention_bootstrap_ci95"] = [_percentile(ratio_samples, .025),
                                                      _percentile(ratio_samples, .975)]
            else:
                point["retention_bootstrap_ci95"] = None
        else:
            point["retention_bootstrap_ci95"] = None
        points.append(point)
    thresholds_out = {}
    for q in qs:
        key = str(q)
        if not reference_valid:
            thresholds_out[key] = {"status": "reference_below_minimum", "threshold": q}
            continue
        qualified = [point for point in points if point["retention"] is not None and point["retention"] >= q]
        if not qualified:
            thresholds_out[key] = {"status": "above_tested_maximum", "threshold": q,
                                   "capacity_lower_bound_bps": max(finite) if finite else None}
            continue
        high = min(qualified, key=lambda point: point["capacity_bps"])
        lower = [point for point in points if point["capacity_bps"] < high["capacity_bps"]]
        if lower and lower[-1]["retention"] < q:
            thresholds_out[key] = {"status": "bracketed", "threshold": q,
                                   "capacity_lower_bps": lower[-1]["capacity_bps"],
                                   "capacity_upper_bps": high["capacity_bps"]}
        else:
            thresholds_out[key] = {"status": "at_or_below_tested_minimum", "threshold": q,
                                   "capacity_upper_bound_bps": high["capacity_bps"]}
    return {"schema": SCHEMA, "version": VERSION, "status": "ok",
            "metric": metric, "method_ids": sorted(methods), "reference": {
                "capacity_bps": None, "n_base_instances": len(ref_by_base),
                "mean": ref_mean, "valid": reference_valid,
                "bootstrap_ci95": _bootstrap(list(ref_by_base.values()), seed=seed,
                                              samples=bootstrap_samples)},
            "points": points, "thresholds": thresholds_out,
            "no_extrapolation": True}


def summarize_capacity_curve_v2(
    observations: Sequence[Mapping[str, Any]], *,
    metric: str = CAPACITY_V2_METRIC,
    bootstrap_samples: int = 2000, seed: int = 0,
    min_reference_mean: float | None = None,
    thresholds: Sequence[float] = (.9, .5),
) -> dict[str, Any]:
    """Summarize opt-in v2 rows with explicit bit/s experiment units.

    The legacy runtime field named ``capacity_bps`` is bytes/second in actual
    service-time arithmetic.  V2 therefore accepts only rows carrying both
    the experiment-facing bit/s value and its one-time bytes/s conversion.
    Legacy capacity/C0 spellings, mixed schemas or methods, and partially
    specified C0 values fail closed before the v1 numerical aggregator is
    reused internally.
    """
    if isinstance(observations, (str, bytes)) or not isinstance(observations, Sequence):
        raise ValueError("observations must be a sequence of objects")
    if not observations:
        raise ValueError("observations must not be empty")
    if metric != CAPACITY_V2_METRIC:
        raise ValueError(
            f"capacity v2 metric is fixed as {CAPACITY_V2_METRIC!r}")
    forbidden = {
        "capacity_bps", "c0_bps", "capacity_bytes_per_s",
        "c0_bytes_per_s", "baseline_capacity_bps",
    }
    normalized: list[dict[str, Any]] = []
    c0_presence: set[bool] = set()
    c0_values: set[float] = set()
    pools: set[str] = set()
    for raw in observations:
        if not isinstance(raw, Mapping):
            raise ValueError("each observation must be an object")
        mixed = sorted(forbidden & set(raw))
        if mixed:
            raise ValueError(f"v2 observations contain legacy or byte-unit fields: {mixed}")
        if set(raw) != _CAPACITY_V2_OBSERVATION_FIELDS:
            raise ValueError("capacity v2 observation fields are incomplete or unknown")
        if (raw.get("schema") != CAPACITY_V2_OBSERVATION_SCHEMA
                or type(raw.get("version")) is not int
                or raw.get("version") != CAPACITY_V2_VERSION):
            raise ValueError("capacity v2 observation schema/version mismatch")
        if raw.get("method_id") != CAPACITY_V2_METHOD_ID:
            raise ValueError("capacity v2 observations contain a different method_id")
        if raw.get("capacity_unit") != "bits_per_second":
            raise ValueError("capacity v2 observation unit must be bits_per_second")
        if raw.get("runtime_capacity_unit") != "bytes_per_second":
            raise ValueError("runtime capacity unit must be bytes_per_second")
        if raw.get("measurement_scope") != "synthetic_link_delivery_diagnostic":
            raise ValueError("capacity v2 observation measurement scope is invalid")
        if raw.get("workload_design") != "deterministic_fixed_message_sequence":
            raise ValueError("capacity v2 workload design is invalid")
        if raw.get("seed_role") != "identity_only_no_random_draws":
            raise ValueError("capacity v2 seed role is invalid")
        if raw.get("metric") != metric:
            raise ValueError("capacity v2 observations contain a different metric")
        pool = _text(raw.get("pool"), "pool")
        expected_direction = _CAPACITY_V2_POOL_DIRECTIONS.get(pool)
        if expected_direction is None:
            raise ValueError("capacity v2 observation pool is invalid")
        if raw.get("direction") != expected_direction:
            raise ValueError("capacity v2 observation direction conflicts with its pool")
        pools.add(pool)
        published = raw.get("published_count")
        delivered = raw.get("delivered_count")
        if (type(published) is not int or published < 1
                or type(delivered) is not int or not 0 <= delivered <= published):
            raise ValueError("capacity v2 delivery counts are invalid")
        metric_value = _number(raw.get("metric_value"), "metric_value")
        if not math.isclose(metric_value, delivered / published,
                            rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("capacity v2 delivery metric differs from its counts")
        if (raw.get("metric_interpretation") !=
                "synthetic messages delivered divided by published messages; "
                "not system business performance or participant adaptation"):
            raise ValueError("capacity v2 metric interpretation is invalid")
        wire_size = raw.get("wire_size_bytes")
        if type(wire_size) is not int or wire_size < 1:
            raise ValueError("wire_size_bytes must be a positive integer")
        serialization_s = _number(
            raw.get("serialization_duration_s"),
            "serialization_duration_s", nonnegative=True)
        mode = raw.get("capacity_mode")
        bits = raw.get("capacity_bits_per_s")
        runtime_bytes = raw.get("runtime_capacity_bytes_per_s")
        if mode == "unlimited":
            if bits is not None or runtime_bytes is not None:
                raise ValueError("unlimited capacity must use null bit/s and runtime capacity")
            if serialization_s != 0.0:
                raise ValueError("unlimited capacity must have zero serialization duration")
            normalized_capacity = None
        elif mode == "finite":
            normalized_capacity = _number(
                bits, "capacity_bits_per_s", nonnegative=True)
            if normalized_capacity <= 0:
                raise ValueError("capacity_bits_per_s must be positive")
            converted = _number(
                runtime_bytes, "runtime_capacity_bytes_per_s", nonnegative=True)
            if converted <= 0 or not math.isclose(
                    converted, normalized_capacity / 8.0,
                    rel_tol=0.0, abs_tol=1e-12):
                raise ValueError("runtime capacity is not the unique bit/s to byte/s conversion")
            if not math.isclose(
                    serialization_s, wire_size / converted,
                    rel_tol=0.0, abs_tol=1e-12):
                raise ValueError(
                    "serialization duration differs from wire size and runtime capacity")
        else:
            raise ValueError("capacity_mode must be finite or unlimited")
        c0_present = raw.get("c0_bits_per_s") is not None
        c0_presence.add(c0_present)
        c0_value = None
        if c0_present:
            c0_value = _number(raw["c0_bits_per_s"], "c0_bits_per_s", nonnegative=True)
            if c0_value <= 0:
                raise ValueError("c0_bits_per_s must be positive")
            c0_values.add(c0_value)
        normalized.append({
            "base_instance_id": raw.get("base_instance_id"),
            "channel_seed": raw.get("channel_seed"),
            "method_id": CAPACITY_V2_METHOD_ID,
            # The private v1 aggregator treats this scalar generically.  In
            # this call it is explicitly bit/s; the public result is renamed.
            "capacity_bps": normalized_capacity,
            **({"c0_bps": c0_value} if c0_present else {}),
            "metric_value": raw.get("metric_value"),
        })
    if len(pools) != 1:
        raise ValueError("capacity v2 curve cannot mix pool identities")
    if len(c0_presence) > 1:
        raise ValueError("c0_bits_per_s must be supplied for every v2 row or none")
    if len(c0_values) > 1:
        raise ValueError("c0_bits_per_s must be frozen across the v2 experiment")
    legacy = summarize_capacity_curve(
        normalized, metric=metric, method_id=CAPACITY_V2_METHOD_ID,
        bootstrap_samples=bootstrap_samples, seed=seed,
        min_reference_mean=min_reference_mean, thresholds=thresholds)

    def rename_bounds(row: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(row)
        names = {
            "capacity_bps": "capacity_bits_per_s",
            "capacity_lower_bps": "capacity_lower_bits_per_s",
            "capacity_upper_bps": "capacity_upper_bits_per_s",
            "capacity_lower_bound_bps": "capacity_lower_bound_bits_per_s",
            "capacity_upper_bound_bps": "capacity_upper_bound_bits_per_s",
        }
        for old, new in names.items():
            if old in result:
                result[new] = result.pop(old)
        return result

    result = {
        **legacy,
        "schema": CAPACITY_V2_ANALYSIS_SCHEMA,
        "version": CAPACITY_V2_VERSION,
        "method_ids": [CAPACITY_V2_METHOD_ID],
        "capacity_unit": "bits_per_second",
        "runtime_capacity_unit": "bytes_per_second",
        "points": [rename_bounds(point) for point in legacy["points"]],
        "thresholds": {
            key: rename_bounds(value) for key, value in legacy["thresholds"].items()
        },
        "measurement_scope": "synthetic_link_delivery_diagnostic",
        "not_system_business_performance": True,
    }
    if "reference" in legacy:
        result["reference"] = rename_bounds(legacy["reference"])
    return result


def summarize_paired_distribution_effect(
    observations: Sequence[Mapping[str, Any]], *, left_condition: str,
    right_condition: str, metric: str = "completion_rate", method_id: str | None = None,
    bootstrap_samples: int = 2000, seed: int = 0,
) -> dict[str, Any]:
    """Compare same-mean distribution conditions without pooling unmatched pairs.

    Rows must contain ``condition`` and ``comparison_level`` (for example a
    fixed-vs-jitter mean delay or IID-vs-burst mean loss).  Differences are
    paired by base instance, channel seed and comparison level, then averaged
    over channel seeds within each base instance before bootstrap resampling.
    """
    left_condition, right_condition = (
        _text(left_condition, "condition"), _text(right_condition, "condition"))
    if left_condition == right_condition:
        raise ValueError("left_condition and right_condition must differ")
    _text(metric, "metric")
    values: dict[tuple[str, str, str, str], float] = {}
    methods: set[str] = set()
    for row in observations:
        if not isinstance(row, Mapping):
            raise ValueError("each observation must be an object")
        base = _text(row.get("base_instance_id"), "base_instance_id")
        channel = _text(row.get("channel_seed"), "channel_seed")
        level = _text(row.get("comparison_level"), "comparison_level")
        condition = _text(row.get("condition"), "condition")
        if condition not in {left_condition, right_condition}:
            raise ValueError("observation condition is outside the requested pair")
        row_method = _text(row.get("method_id", "default"), "method_id")
        methods.add(row_method)
        if method_id is not None and row_method != method_id:
            raise ValueError("observations contain a different method_id")
        if "metric_value" in row:
            value = row["metric_value"]
        else:
            metrics = row.get("metrics")
            value = metrics.get(metric) if isinstance(metrics, Mapping) else None
        key = (base, channel, level, condition)
        if key in values:
            raise ValueError(f"duplicate observation for {key!r}")
        values[key] = _number(value, "metric_value")
    if len(methods) > 1:
        raise ValueError("distribution summary cannot pool multiple methods")
    levels = sorted({key[2] for key in values})
    effects = []
    for level in levels:
        by_base: dict[str, list[float]] = defaultdict(list)
        pair_keys = {(base, channel) for base, channel, current_level, _condition in values
                     if current_level == level}
        for base, channel in sorted(pair_keys):
            left = values.get((base, channel, level, left_condition))
            right = values.get((base, channel, level, right_condition))
            if left is not None and right is not None:
                by_base[base].append(right - left)
        per_base = {base: sum(deltas) / len(deltas) for base, deltas in by_base.items()}
        sample = list(per_base.values())
        if not sample:
            effects.append({"comparison_level": level, "status": "missing_pair",
                            "n_base_instances": 0, "n_pairs": 0,
                            "mean_difference_right_minus_left": None,
                            "bootstrap_ci95": None})
            continue
        effects.append({"comparison_level": level, "status": "ok",
                        "n_base_instances": len(sample),
                        "n_pairs": sum(len(deltas) for deltas in by_base.values()),
                        "mean_difference_right_minus_left": sum(sample) / len(sample),
                        "bootstrap_ci95": _bootstrap(sample, seed=seed + len(effects),
                                                      samples=bootstrap_samples)})
    return {"schema": SCHEMA, "version": VERSION, "status": "ok",
            "metric": metric, "method_ids": sorted(methods),
            "left_condition": left_condition, "right_condition": right_condition,
            "effects": effects, "no_unmatched_pair_inference": True}


__all__ = [
    "CAPACITY_V2_ANALYSIS_SCHEMA", "CAPACITY_V2_METHOD_ID",
    "CAPACITY_V2_METRIC", "CAPACITY_V2_OBSERVATION_SCHEMA",
    "CAPACITY_V2_VERSION", "SCHEMA", "VERSION",
    "summarize_capacity_curve", "summarize_capacity_curve_v2",
    "summarize_paired_distribution_effect",
]
