"""Opt-in capacity diagnostics with explicit experiment and runtime units.

V1 remains unchanged: its historical ``capacity_bps`` values are interpreted
by :class:`CommunicationRuntime` as bytes/second.  V2 accepts bit/s only at the
experiment boundary, converts exactly once to bytes/second for that legacy
runtime field, and gives an unlimited reference to the runtime as ``None``.

The measured delivery fraction belongs to a synthetic fixed-message link load.
It is infrastructure evidence, not participant adaptation or business success.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from typing import Any

from .communication import CommunicationRuntime
from .communication_analysis import (
    CAPACITY_V2_ANALYSIS_SCHEMA,
    CAPACITY_V2_METRIC,
    CAPACITY_V2_METHOD_ID,
    CAPACITY_V2_OBSERVATION_SCHEMA,
    CAPACITY_V2_VERSION,
    summarize_capacity_curve_v2,
)
from .communication_capacity_experiments import (
    CAPACITY_POOLS,
    COMMUNICATION_FORMAL_BLOCKER,
)
from .development_track_marker import build_development_marker, stamp_evidence_record


CAPACITY_V2_REPORT_SCHEMA = "eai.benchmark.communication_capacity_experiments_v2"
CAPACITY_UNIT = "bits_per_second"
RUNTIME_CAPACITY_UNIT = "bytes_per_second"
METRIC = CAPACITY_V2_METRIC
_POOL_DIRECTIONS = {"backhaul_uplink": "uplink", "p2p": "p2p"}


def _positive_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{field} must be finite and positive")
    return result


def bits_per_second_to_runtime_bytes(capacity_bits_per_s: Any) -> float:
    """Perform the only v2 bit/s -> runtime byte/s conversion."""
    return _positive_number(capacity_bits_per_s, "capacity_bits_per_s") / 8.0


def _runtime_capacity(capacity_bits_per_s: float | None) -> float | None:
    return (None if capacity_bits_per_s is None
            else bits_per_second_to_runtime_bytes(capacity_bits_per_s))


def run_capacity_cell_v2(
    *, pool: str, capacity_bits_per_s: float | None,
    base_instance: str, seed: int, count: int = 16,
    interval_s: float = 0.5, ttl_s: float = 3.0,
    direction: str | None = None, c0_bits_per_s: float | None = None,
) -> dict[str, Any]:
    """Run one synthetic link-delivery cell under an explicit v2 identity."""
    if pool not in CAPACITY_POOLS:
        raise ValueError(f"pool must be one of {CAPACITY_POOLS}")
    if not isinstance(base_instance, str) or not base_instance.strip():
        raise ValueError("base_instance must be a nonempty string")
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    if type(count) is not int or count < 1:
        raise ValueError("count must be a positive integer")
    interval = _positive_number(interval_s, "interval_s")
    ttl = _positive_number(ttl_s, "ttl_s")
    runtime_capacity = _runtime_capacity(capacity_bits_per_s)
    normalized_bits = (None if capacity_bits_per_s is None
                       else _positive_number(capacity_bits_per_s,
                                             "capacity_bits_per_s"))
    normalized_c0 = (None if c0_bits_per_s is None
                     else _positive_number(c0_bits_per_s, "c0_bits_per_s"))
    expected_direction = _POOL_DIRECTIONS[pool]
    effective_direction = expected_direction if direction is None else direction
    if effective_direction != expected_direction:
        raise ValueError(
            f"pool {pool!r} requires direction {expected_direction!r}")
    recipient = "edge_1" if pool == "backhaul_uplink" else "robot_2"
    runtime = CommunicationRuntime({"pools": {
        # Historical runtime spelling; value is bytes/second by arithmetic.
        pool: {"capacity_bps": runtime_capacity, "ttl_s": ttl * 2},
    }})
    for index in range(count):
        runtime.publish(
            message_id=f"{base_instance}-v2-m{index}",
            producer_id="robot_1", recipients=[recipient],
            channel="observation",
            estimate={"base": base_instance, "index": index,
                      "probe": "capacity_v2"},
            valid_until_s=runtime.time_s + ttl,
            pool=pool, direction=effective_direction,
        )
        if index + 1 < count:
            runtime.advance(runtime.time_s + interval)
    runtime.advance(runtime.time_s + ttl * 2)
    report = runtime.report()
    delivered = sum(row.get("event") == "delivered" for row in report["ledger"])
    published = sum(row.get("event") == "published" for row in report["ledger"])
    policy_capacity = report["contract"]["pools"][pool]["capacity_bps"]
    if policy_capacity != runtime_capacity:
        raise RuntimeError("runtime capacity differs from the v2 boundary conversion")
    first = report["messages"][f"{base_instance}-v2-m0"]
    first_delivery = first["recipients"][recipient]
    wire_size = first.get("size_bytes")
    if type(wire_size) is not int or wire_size < 1:
        raise RuntimeError("runtime did not record a positive wire size")
    serialization_times = (
        first_delivery.get("scheduled_transmit_at_s"),
        first_delivery.get("scheduled_finish_at_s"),
    )
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(float(value)) for value in serialization_times):
        raise RuntimeError(
            "runtime did not record finite scheduled serialization timestamps")
    serialization_s = float(serialization_times[1]) - float(serialization_times[0])
    if serialization_s < 0:
        raise RuntimeError("runtime recorded a negative serialization duration")
    expected_serialization_s = (
        0.0 if runtime_capacity is None else wire_size / runtime_capacity)
    if not math.isclose(serialization_s, expected_serialization_s,
                        rel_tol=0.0, abs_tol=1e-12):
        raise RuntimeError("runtime serialization duration differs from v2 units")
    return {
        "schema": CAPACITY_V2_OBSERVATION_SCHEMA,
        "version": CAPACITY_V2_VERSION,
        "method_id": CAPACITY_V2_METHOD_ID,
        "measurement_scope": "synthetic_link_delivery_diagnostic",
        "base_instance_id": base_instance,
        "channel_seed": f"s{seed}",
        "pool": pool,
        "direction": effective_direction,
        "workload_design": "deterministic_fixed_message_sequence",
        "seed_role": "identity_only_no_random_draws",
        "capacity_mode": "unlimited" if normalized_bits is None else "finite",
        "capacity_unit": CAPACITY_UNIT,
        "capacity_bits_per_s": normalized_bits,
        "runtime_capacity_unit": RUNTIME_CAPACITY_UNIT,
        "runtime_capacity_bytes_per_s": runtime_capacity,
        "c0_bits_per_s": normalized_c0,
        "metric": METRIC,
        "metric_value": delivered / count,
        "published_count": published,
        "delivered_count": delivered,
        "wire_size_bytes": wire_size,
        "serialization_duration_s": serialization_s,
        "metric_interpretation": (
            "synthetic messages delivered divided by published messages; "
            "not system business performance or participant adaptation"),
    }


def capacity_observations_v2(
    *, pool: str,
    bases: Sequence[str] = ("cap-a", "cap-b", "cap-c", "cap-d"),
    seeds: Sequence[int] = (31, 32),
    capacities_bits_per_s: Sequence[float | None] = (
        None, 32000.0, 16000.0, 8000.0, 4000.0),
    direction: str | None = None,
    c0_bits_per_s: float | None = None,
) -> list[dict[str, Any]]:
    """Materialize v2 cells; defaults are the v1 diagnostic points in bit/s."""
    if pool not in CAPACITY_POOLS:
        raise ValueError(f"pool must be one of {CAPACITY_POOLS}")
    return [
        run_capacity_cell_v2(
            pool=pool, capacity_bits_per_s=capacity,
            base_instance=base, seed=seed, direction=direction,
            c0_bits_per_s=c0_bits_per_s)
        for base in bases for seed in seeds for capacity in capacities_bits_per_s
    ]


def summarize_capacity_experiments_v2(
    *, pools: Sequence[str] = CAPACITY_POOLS,
    capacity_rows_by_pool: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    thresholds: Sequence[float] = (.9, .5), seed: int = 0,
) -> dict[str, Any]:
    """Build a v2 diagnostic report without creating a capability score."""
    rows_by_pool = (dict(capacity_rows_by_pool)
                    if capacity_rows_by_pool is not None
                    else {pool: capacity_observations_v2(pool=pool) for pool in pools})
    if set(rows_by_pool) != set(pools):
        raise ValueError("capacity_rows_by_pool must match the requested pools")
    curves = {}
    for pool, rows in rows_by_pool.items():
        if any(row.get("pool") != pool for row in rows):
            raise ValueError("capacity v2 rows cannot mix pool identities")
        curves[pool] = summarize_capacity_curve_v2(
            rows, metric=METRIC, thresholds=thresholds, seed=seed)
        if curves[pool].get("schema") != CAPACITY_V2_ANALYSIS_SCHEMA:
            raise RuntimeError("capacity v2 analysis identity was not preserved")
        if curves[pool].get("status") != "ok":
            raise ValueError(
                f"capacity v2 pool {pool!r} requires an actual unlimited reference")
    report: dict[str, Any] = {
        "schema": CAPACITY_V2_REPORT_SCHEMA,
        "version": CAPACITY_V2_VERSION,
        "visibility": "evaluator_only",
        "status": "ok",
        "method_id": CAPACITY_V2_METHOD_ID,
        "capacity_unit": CAPACITY_UNIT,
        "runtime_capacity_unit": RUNTIME_CAPACITY_UNIT,
        "unit_conversion": "runtime_bytes_per_second=bits_per_second/8",
        "reference": "actual_unlimited_runtime_capacity_none",
        "capacity_curves": curves,
        "measurement_scope": "synthetic_link_delivery_diagnostic",
        "diagnostic_identity": "transport_diagnostic",
        "diagnostic_identity_is_task_bundle_scene_key": False,
        "workload_design": "deterministic_fixed_message_sequence",
        "seed_interpretation": (
            "identity label only; the capacity workload draws no randomness, "
            "so repeated seed labels are not independent stochastic evidence"),
        "metric_interpretation": (
            "delivery fraction for the fixed synthetic message stream; "
            "not a system business score or communication-adaptation score"),
        "legacy_probe_labels": {
            "jitter": "deterministic_alternating_delay_diagnostic_not_random_jitter",
            "iid_loss": "seeded_scattered_fixed_count_loss_not_bernoulli_iid",
        },
        "formal_episode_hookup": "pending",
        "infrastructure_blockers": [COMMUNICATION_FORMAL_BLOCKER],
        "execution_validated": False,
        "business_outcomes_validated": False,
        "scoreable": False,
    }
    return stamp_evidence_record(
        report, build_development_marker(
            "transport_diagnostic", asset_status="development_unverified",
            evidence_class="diagnostic",
            infrastructure_blockers=[COMMUNICATION_FORMAL_BLOCKER]))


__all__ = [
    "CAPACITY_UNIT", "CAPACITY_V2_REPORT_SCHEMA", "METRIC",
    "RUNTIME_CAPACITY_UNIT", "bits_per_second_to_runtime_bytes",
    "capacity_observations_v2", "run_capacity_cell_v2",
    "summarize_capacity_experiments_v2",
]
