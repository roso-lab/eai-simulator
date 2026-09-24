"""Offline capacity experiments for the three-pool communication contract.

Drives ``CommunicationRuntime`` deterministically to produce the STAGE22
acceptance evidence offline: paired fixed-delay vs jitter (JP) and IID-loss
vs burst-loss (BP) comparisons, per-pool capacity sweeps with C90/C50
brackets, and direction attribution that fails closed instead of guessing.

Time-varying delay and loss go through the sanctioned
``set_conditions(..., pool=...)`` intervention: every change is recorded as
a ``channel_condition_changed`` ledger event and only affects later
publications, so cells remain replayable.  Missing direction on any served
row keeps the direction breakdown ``null`` while total/pool bytes survive.
Bootstrap intervals are computed only inside valid samples (the analysis
helpers never extrapolate).  Every report is development-track evidence:
release flags stay false and a development marker is attached.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Any

from .communication import CommunicationRuntime
from .communication_analysis import (
    summarize_capacity_curve,
    summarize_paired_distribution_effect,
)
from .development_track_marker import build_development_marker, stamp_evidence_record

CAPACITY_SCHEMA = "eai.benchmark.communication_capacity_experiments"
CAPACITY_VERSION = 1
COMMUNICATION_FORMAL_BLOCKER = "communication_formal_episode_pending"
CAPACITY_POOLS = ("backhaul_uplink", "p2p")
_METRIC_DELIVERY = "delivery_rate"
_METRIC_OUTAGE = "max_inter_delivery_gap_s"


def _pattern_value(seed: int, index: int, salt: str) -> float:
    """Deterministic per-cell scalar in [0, 1) without importing numpy."""
    material = f"{seed}:{salt}:{index}".encode("utf-8")
    return int.from_bytes(sha256(material).digest()[:8], "big") / float(1 << 64)


def _jitter_delays(seed: int, count: int, mean_s: float, amplitude_s: float) -> list[float]:
    """Alternating mean±amplitude delays with exactly the requested mean."""
    return [mean_s + (amplitude_s if index % 2 == 0 else -amplitude_s)
            for index in range(count)]


def _iid_drop_indices(seed: int, count: int, rate: float) -> set[int]:
    """Scattered loss with exactly ``round(rate*10)`` drops per block of ten.

    Drop positions are spread evenly and rotated per block by a seeded
    offset, so consecutive drops (the burst signature) never occur.
    """
    if count % 10:
        raise ValueError("IID loss cells require message counts in blocks of ten")
    block_drops = max(0, round(rate * 10))
    if block_drops == 0:
        return set()
    spread = [round((index + 0.5) * 10 / block_drops) % 10
              for index in range(block_drops)]
    dropped: set[int] = set()
    for block in range(count // 10):
        offset = int(_pattern_value(seed, block, "iid-offset") * 10)
        dropped.update(block * 10 + (position + offset) % 10
                       for position in spread)
    return dropped


def _burst_drop_indices(seed: int, count: int, rate: float) -> set[int]:
    """Consecutive loss blocks with the same exact drops-per-ten rate."""
    if count % 10:
        raise ValueError("burst loss cells require message counts in blocks of ten")
    block_drops = max(0, round(rate * 10))
    dropped: set[int] = set()
    for block in range(count // 10):
        offset = int(_pattern_value(seed, block, "burst-offset") * (10 - block_drops + 1))
        dropped.update(range(block * 10 + offset,
                             block * 10 + offset + block_drops))
    return dropped


def _publish_load(runtime: CommunicationRuntime, *, base_instance: str, count: int,
                  interval_s: float, ttl_s: float, producer: str, recipient: str,
                  direction: str | None, pool: str | None):
    for index in range(count):
        estimate = {"base": base_instance, "index": index,
                    "bytes_hint": 120 + (index % 5) * 40}
        runtime.publish(
            message_id=f"{base_instance}-m{index}",
            producer_id=producer,
            recipients=[recipient],
            channel="observation",
            estimate=estimate,
            valid_until_s=runtime.time_s + ttl_s,
            pool=pool,
            direction=direction,
        )
        if index + 1 < count:
            runtime.advance(runtime.time_s + interval_s)


def _cell_metrics(runtime: CommunicationRuntime, *, count: int, interval_s: float,
                  horizon_s: float) -> dict[str, float]:
    runtime.advance(runtime.time_s + horizon_s)
    report = runtime.report()
    ledger = report["ledger"]
    delivered = [row for row in ledger if row["event"] == "delivered"]
    published = [row for row in ledger if row["event"] == "published"]
    delivery_rate = len(delivered) / count if count else 0.0
    times = sorted(row["time_s"] for row in delivered)
    gap = 0.0
    for previous, current in zip(times, times[1:]):
        gap = max(gap, current - previous)
    return {"delivery_rate": delivery_rate,
            "delivered_count": float(len(delivered)),
            "published_count": float(len(published)),
            _METRIC_OUTAGE: round(gap, 6)}


def run_delay_cell(*, condition: str, mean_delay_s: float, amplitude_s: float = 0.9,
                   seed: int, base_instance: str, count: int = 12,
                   interval_s: float = 0.5, ttl_s: float | None = None,
                   capacity_bps: float = 20000.0) -> dict[str, Any]:
    """One JP cell: fixed or jittered pool delay with the same exact mean.

    The default TTL sits strictly between the fixed arrival (mean plus
    queueing) and the jitter high sample (mean plus amplitude), so the fixed
    cell delivers everything while jitter high samples expire — the paired
    difference isolates distribution, not the mean.
    """
    if amplitude_s >= mean_delay_s - 0.05:
        raise ValueError("jitter amplitude must stay below the mean delay")
    effective_ttl = mean_delay_s + amplitude_s / 2.0 if ttl_s is None else ttl_s
    runtime = CommunicationRuntime({"pools": {
        "backhaul_uplink": {"capacity_bps": capacity_bps, "fixed_delay_s": mean_delay_s,
                            "ttl_s": effective_ttl * 2}}})
    delays = ([mean_delay_s] * count if condition == "fixed_delay"
              else _jitter_delays(seed, count, mean_delay_s, amplitude_s))
    for index, delay in enumerate(delays):
        runtime.set_conditions({"fixed_delay_s": delay}, pool="backhaul_uplink")
        estimate = {"base": base_instance, "index": index, "cell": "jp"}
        runtime.publish(
            message_id=f"{base_instance}-jp{index}", producer_id="robot_1",
            recipients=["edge_1"], channel="observation", estimate=estimate,
            valid_until_s=runtime.time_s + effective_ttl,
            pool="backhaul_uplink", direction="uplink")
        if index + 1 < count:
            runtime.advance(runtime.time_s + interval_s)
    metrics = _cell_metrics(runtime, count=count, interval_s=interval_s,
                            horizon_s=effective_ttl * 2)
    metrics["condition"] = condition
    metrics["mean_configured_delay_s"] = sum(delays) / len(delays)
    return metrics


def run_loss_cell(*, condition: str, rate: float, seed: int, base_instance: str,
                  count: int = 20, interval_s: float = 0.5, ttl_s: float = 4.0,
                  capacity_bps: float = 20000.0) -> dict[str, Any]:
    """One BP cell: scattered IID loss or consecutive burst loss, same rate."""
    runtime = CommunicationRuntime({"pools": {
        "p2p": {"capacity_bps": capacity_bps, "ttl_s": ttl_s * 2}}})
    dropped = (_iid_drop_indices(seed, count, rate) if condition == "iid_loss"
               else _burst_drop_indices(seed, count, rate))
    for index in range(count):
        runtime.set_conditions({"drop": index in dropped}, pool="p2p")
        estimate = {"base": base_instance, "index": index, "cell": "bp"}
        runtime.publish(
            message_id=f"{base_instance}-bp{index}", producer_id="robot_1",
            recipients=["robot_2"], channel="p2p_relay", estimate=estimate,
            valid_until_s=runtime.time_s + ttl_s,
            pool="p2p", direction="p2p")
        if index + 1 < count:
            runtime.advance(runtime.time_s + interval_s)
    metrics = _cell_metrics(runtime, count=count, interval_s=interval_s,
                            horizon_s=ttl_s * 2)
    metrics["condition"] = condition
    metrics["configured_loss_rate"] = len(dropped) / count
    return metrics


def run_capacity_cell(*, pool: str, capacity_bps: float | None, base_instance: str,
                      seed: int, count: int = 16, interval_s: float = 0.5,
                      ttl_s: float = 3.0, reference_capacity_bps: float = 20000.0,
                      direction: str | None = None) -> dict[str, Any]:
    """One capacity-sweep cell; ``None`` capacity is the perfect reference."""
    effective = reference_capacity_bps if capacity_bps is None else capacity_bps
    recipient = "edge_1" if pool == "backhaul_uplink" else "robot_2"
    runtime = CommunicationRuntime({"pools": {
        pool: {"capacity_bps": effective, "ttl_s": ttl_s * 2}}})
    _publish_load(runtime, base_instance=base_instance, count=count,
                  interval_s=interval_s, ttl_s=ttl_s, producer="robot_1",
                  recipient=recipient, direction=direction, pool=pool)
    metrics = _cell_metrics(runtime, count=count, interval_s=interval_s,
                            horizon_s=ttl_s * 2)
    metrics["capacity_bps"] = capacity_bps
    metrics["pool"] = pool
    return metrics


def bytes_by_direction(ledger: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Attribute served bytes by pool+direction, failing closed on gaps."""
    total = 0
    by_pool: dict[str, int] = {}
    by_direction: dict[str, int] = {}
    missing_direction = False
    for row in ledger:
        if row.get("event") != "bytes_served":
            continue
        size = row.get("bytes") or 0
        total += size
        by_pool[row.get("pool", "unknown")] = by_pool.get(row.get("pool", "unknown"), 0) + size
        direction = row.get("direction")
        if direction is None:
            missing_direction = True
        else:
            by_direction[direction] = by_direction.get(direction, 0) + size
    return {"total_bytes": total, "bytes_by_pool": by_pool,
            # Missing direction is a gap, never guessed from channel names.
            "bytes_by_direction": None if missing_direction else by_direction,
            "direction_complete": not missing_direction}


def jitter_paired_observations(*, bases: Sequence[str] = ("jp-a", "jp-b", "jp-c"),
                               seeds: Sequence[int] = (11, 12, 13),
                               levels: Sequence[str] = ("mean_1.0s", "mean_1.6s"),
                               mean_by_level: Mapping[str, float] | None = None,
                               amplitude_s: float = 0.9) -> list[dict[str, Any]]:
    means = dict(mean_by_level or {"mean_1.0s": 1.0, "mean_1.6s": 1.6})
    rows = []
    for base in bases:
        for seed in seeds:
            for level in levels:
                for condition in ("fixed_delay", "jitter"):
                    cell = run_delay_cell(
                        condition=condition, mean_delay_s=means[level],
                        amplitude_s=amplitude_s, seed=seed, base_instance=base)
                    rows.append({
                        "base_instance_id": base, "channel_seed": f"s{seed}",
                        "comparison_level": level, "condition": condition,
                        "metric": _METRIC_DELIVERY,
                        "metric_value": cell[_METRIC_DELIVERY],
                        "method_id": "jp_v1",
                    })
    return rows


def loss_paired_observations(*, bases: Sequence[str] = ("bp-a", "bp-b", "bp-c"),
                             seeds: Sequence[int] = (21, 22, 23),
                             levels: Sequence[str] = ("loss_0.2", "loss_0.3"),
                             rate_by_level: Mapping[str, float] | None = None,
                             ) -> list[dict[str, Any]]:
    rates = dict(rate_by_level or {"loss_0.2": 0.2, "loss_0.3": 0.3})
    rows = []
    for base in bases:
        for seed in seeds:
            for level in levels:
                for condition in ("iid_loss", "burst_loss"):
                    cell = run_loss_cell(condition=condition, rate=rates[level],
                                         seed=seed, base_instance=base)
                    rows.append({
                        "base_instance_id": base, "channel_seed": f"s{seed}",
                        "comparison_level": level, "condition": condition,
                        "metric": _METRIC_OUTAGE,
                        "metric_value": cell[_METRIC_OUTAGE],
                        "method_id": "bp_v1",
                    })
    return rows


def capacity_observations(*, pool: str,
                          bases: Sequence[str] = ("cap-a", "cap-b", "cap-c", "cap-d"),
                          seeds: Sequence[int] = (31, 32),
                          capacities: Sequence[float | None] = (None, 4000.0, 2000.0,
                                                                1000.0, 500.0),
                          direction: str | None = "uplink") -> list[dict[str, Any]]:
    if pool not in CAPACITY_POOLS:
        raise ValueError(f"pool must be one of {CAPACITY_POOLS}")
    rows = []
    for base in bases:
        for seed in seeds:
            for capacity in capacities:
                cell = run_capacity_cell(pool=pool, capacity_bps=capacity,
                                         base_instance=base, seed=seed,
                                         direction=direction)
                rows.append({
                    "base_instance_id": base, "channel_seed": f"s{seed}",
                    "capacity_bps": capacity, "method_id": f"capacity_{pool}_v1",
                    "metric": _METRIC_DELIVERY,
                    "metric_value": cell[_METRIC_DELIVERY],
                })
    return rows


def summarize_capacity_experiments(
    *, pools: Sequence[str] = CAPACITY_POOLS,
    jitter_rows: Sequence[Mapping[str, Any]] | None = None,
    loss_rows: Sequence[Mapping[str, Any]] | None = None,
    capacity_rows_by_pool: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    thresholds: Sequence[float] = (0.9, 0.5),
    seed: int = 0,
) -> dict[str, Any]:
    """Assemble the offline capacity report; bootstrap stays within samples."""
    jitter_rows = list(jitter_rows if jitter_rows is not None
                       else jitter_paired_observations())
    loss_rows = list(loss_rows if loss_rows is not None
                     else loss_paired_observations())
    capacity_rows_by_pool = dict(capacity_rows_by_pool or {
        pool: capacity_observations(pool=pool) for pool in pools})
    report: dict[str, Any] = {
        "schema": CAPACITY_SCHEMA, "version": CAPACITY_VERSION,
        "visibility": "evaluator_only",
        "status": "ok",
        "jp_paired_effect": summarize_paired_distribution_effect(
            jitter_rows, left_condition="fixed_delay", right_condition="jitter",
            metric=_METRIC_DELIVERY, seed=seed),
        "bp_paired_effect": summarize_paired_distribution_effect(
            loss_rows, left_condition="iid_loss", right_condition="burst_loss",
            metric=_METRIC_OUTAGE, seed=seed),
        "capacity_curves": {
            pool: summarize_capacity_curve(rows, metric=_METRIC_DELIVERY,
                                           thresholds=thresholds, seed=seed)
            for pool, rows in capacity_rows_by_pool.items()},
        "formal_episode_hookup": "pending",
        "infrastructure_blockers": [COMMUNICATION_FORMAL_BLOCKER],
        "execution_validated": False,
        "business_outcomes_validated": False,
        "scoreable": False,
    }
    return stamp_evidence_record(
        report, build_development_marker(
            "city", asset_status="development_unverified",
            evidence_class="diagnostic",
            infrastructure_blockers=[COMMUNICATION_FORMAL_BLOCKER]))


__all__ = [
    "CAPACITY_POOLS", "CAPACITY_SCHEMA", "CAPACITY_VERSION",
    "COMMUNICATION_FORMAL_BLOCKER", "bytes_by_direction",
    "capacity_observations", "jitter_paired_observations", "loss_paired_observations",
    "run_capacity_cell", "run_delay_cell", "run_loss_cell",
    "summarize_capacity_experiments",
]
