"""Protocol 3.2 communication-adaptation task-performance metric mapping.

The offline transport track already owns the network side: the three-pool
``CommunicationRuntime`` ledger, the synthetic capacity/delay/loss sweeps in
``communication_capacity_experiments``, and the fail-closed curve summaries
in ``communication_analysis`` (whose dependent variable is, and stays, the
self-declared ``synthetic_link_delivery_diagnostic`` delivery rate).  The
protocol's formal dependent variable is different: task performance
``M`` from one preregistered higher-is-better indicator — ``SR_comm``,
``MCR_comm``, ``AUP_comm`` or the normalized single-step efficiency —
measured on episodes whose task traffic flows through that shared ledger.
This module owns exactly that mapping and nothing else:

- :func:`communication_condition_metrics` folds one communication-condition
  episode's transport report plus its milestone observation inputs (the
  batch-1 :mod:`goal_progress_curve` vocabulary) into per-condition
  SR/MCR/AUP, the protocol's process-and-diagnostic transport companion
  state, and the section 3.6 ``B_offer``/``B_link`` communication cost
  vector;
- :func:`retention_curve` computes ``R_x(x) = M(x) / M(x_ref)`` and the
  first-drop ``x_90``/``x_50`` brackets over fixed-delay or IID-loss
  severity without extrapolating beyond tested conditions;
- :func:`capacity_thresholds` computes ``C_90``/``C_50`` from task
  performance ``M`` over the absolute capacity axis with the frozen ``C/C0``
  dual axis — the same bracket semantics as
  ``communication_analysis.summarize_capacity_curve`` but on task results
  instead of synthetic delivery rate;
- :func:`communication_dependency` computes the perfect/zero ``D_comm``
  diagnostic behind its ``m_min`` reference-validity gate;
- :func:`distribution_contrast` computes the same-mean ``JP``/``BP`` paired
  difference from the capacity-experiment paired row structure.

Division of labour with the synthetic layer: the synthetic delivery curves
remain companion network state for interpreting total effects; they are
never reused here as task performance.  Hierarchical instance/channel-seed
aggregation and cross-episode bootstrap CIs stay with the goal aggregation
batch — the curve/threshold helpers here consume already-aggregated
per-condition means.  Missing milestone inputs or a missing transport
report produce explicit N/A sections, never silent zeros.  Every output is
development-track evidence: release flags stay ``False``.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from .goal_progress_curve import build_progress_curve

COMMUNICATION_METRICS_SCHEMA = "eai.benchmark.goal_communication_metrics"
COMMUNICATION_METRICS_VERSION = 1
BYTE_TO_BIT_FACTOR = 8
# Frozen per evaluation version (protocol 3.2/3.6): broadcast is charged per
# admitted recipient copy; channel loss and TTL expiry after entering the
# link still occupy the link; only interface (buffer) rejection stays in the
# offered quantity and the rejection counters.
_RELEASE = {"execution_validated": False, "business_outcomes_validated": False,
            "scoreable": False}
# Retention ratios are float quotients of measured means; qualification and
# violation tests against a threshold q tolerate this absolute slack so an
# exactly-on-the-boundary ratio is never misclassified by rounding.
_RETENTION_TOLERANCE = 1e-12


def _meets(retention: float, q: float) -> bool:
    return retention >= q - _RETENTION_TOLERANCE


def _falls_below(retention: float, q: float) -> bool:
    return retention < q - _RETENTION_TOLERANCE


NA_MISSING_COMMUNICATION_REPORT = "na_missing_communication_report"
NA_MISSING_MILESTONES = "na_missing_milestones"
_CHANNEL_LOSS_REASONS = {"active"}
_INTERFACE_REJECTION_REASONS = {"buffer_overflow"}
_EVALUATOR_TREATMENT_REASONS = {"publication_treatment"}
_JP_CONDITIONS = frozenset({"fixed_delay", "jitter"})
_BP_CONDITIONS = frozenset({"iid_loss", "burst_loss"})


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    return value


def _finite(value: Any, field: str, *, positive: bool = False,
            nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0) or (
            nonnegative and result < 0):
        qualifier = "positive" if positive else (
            "nonnegative" if nonnegative else "finite")
        raise ValueError(f"{field} must be a {qualifier} number")
    return result


def _optional_finite(value: Any, field: str) -> float | None:
    return None if value is None else _finite(value, field)


def _percentile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("cannot compute a percentile of an empty sample")
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _bootstrap(values: Sequence[float], *, seed: int,
               samples: int) -> list[float]:
    if not values:
        raise ValueError("bootstrap requires at least one value")
    if type(samples) is not int or samples < 1:
        raise ValueError("bootstrap_samples must be a positive integer")
    if len(values) == 1:
        return [float(values[0]), float(values[0])]
    rng = random.Random(seed)
    replicates = []
    for _ in range(samples):
        replicates.append(sum(values[rng.randrange(len(values))]
                              for _ in values) / len(values))
    return [_percentile(replicates, .025), _percentile(replicates, .975)]


def _validate_thresholds(thresholds: Sequence[float]) -> list[float]:
    qs = [_finite(q, "threshold", nonnegative=True) for q in thresholds]
    if any(q > 1 for q in qs) or len(set(qs)) != len(qs):
        raise ValueError("thresholds must be distinct values in [0, 1]")
    if not qs:
        raise ValueError("thresholds must not be empty")
    return qs


def _validate_excluded(value: Sequence[str]) -> frozenset[str]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise ValueError("excluded_message_ids must be a sequence of strings")
    return frozenset(_text(item, "excluded message id") for item in value)


def _task_performance(progress_inputs: Mapping[str, Any] | None) -> dict[str, Any]:
    """Map milestone observation inputs onto SR/MCR/AUP for one episode.

    ``progress_inputs`` uses the batch-1 progress-curve vocabulary:
    ``active_milestones`` and ``horizon_s`` are required, ``observations``
    defaults to none satisfied (a valid all-fail measurement), and
    ``weights``/``execution_end_s`` pass through unchanged.  Missing inputs
    are N/A rather than errors; malformed values fail closed inside
    :func:`goal_progress_curve.build_progress_curve`.
    """
    na = {"status": NA_MISSING_MILESTONES,
          "na_reason": "milestone observations are absent: SR/MCR/AUP are N/A",
          "SR_comm": None, "MCR_comm": None, "AUP_comm": None,
          "e_step": None, "e_step_note": "decision_steps_not_recorded",
          "progress_curve": None}
    if progress_inputs is None:
        return dict(na)
    if not isinstance(progress_inputs, Mapping):
        raise ValueError("progress_inputs must be a mapping or None")
    active = progress_inputs.get("active_milestones")
    horizon = progress_inputs.get("horizon_s")
    if active is None or horizon is None:
        return dict(na)
    curve = build_progress_curve(
        active, progress_inputs.get("observations") or [],
        horizon_s=horizon,
        weights=progress_inputs.get("weights"),
        execution_end_s=progress_inputs.get("execution_end_s"))
    if curve.get("status") != "computed":
        result = dict(na)
        result["status"] = curve.get("status", "na_progress_curve")
        result["na_reason"] = curve.get("na_reason", result["na_reason"])
        result["progress_curve"] = curve
        return result
    performance: dict[str, Any] = {
        "status": "computed",
        # Offline terminal-success proxy: Q(H) == 1.  The contract's full
        # final-success predicate Y_i additionally requires the frozen
        # global termination predicates, which offline archives cannot see.
        "SR_comm": 1.0 if curve["Q_H"] == 1.0 else 0.0,
        "SR_comm_proxy_scope": "offline_terminal_success_proxy_qh_only",
        "SR_comm_full_yi_note": (
            "formal Y_i additionally requires frozen global termination "
            "predicates; consumers must not treat SR_comm=1.0 as Y_i=1 "
            "without the contract's full success_predicate check"),
        "MCR_comm": curve["MCR"],
        "AUP_comm": curve["AUP"],
        "progress_curve": curve,
    }
    steps = progress_inputs.get("decision_step_count")
    if steps is None:
        performance["e_step"] = None
        performance["e_step_note"] = "decision_steps_not_recorded"
    else:
        steps = _finite(steps, "decision_step_count", nonnegative=True)
        if steps == 0:
            # Zero-decision-step success keeps its milestone results but its
            # e_i and single-step efficiency normalization are N/A.
            performance["e_step"] = None
            performance["e_step_note"] = "zero_decision_step_success_na"
        else:
            performance["e_step"] = curve["MCR"] / steps
            performance["e_step_note"] = None
    return performance


def _delay_stats(delays: Sequence[float]) -> dict[str, Any]:
    if not delays:
        return {"count": 0, "p50_s": None, "p95_s": None, "max_s": None}
    return {"count": len(delays), "p50_s": _percentile(delays, .5),
            "p95_s": _percentile(delays, .95), "max_s": max(delays)}


def _jitter_mean_absolute_successive_difference(
        rows: Sequence[Mapping[str, Any]]) -> float | None:
    """One frozen jitter diagnostic: mean |successive difference| in delivery order."""
    if len(rows) < 2:
        return None
    ordered = sorted(rows, key=lambda row: (row["delivered_at_s"], row["recipient"]))
    gaps = [abs(current["delay_s"] - previous["delay_s"])
            for previous, current in zip(ordered, ordered[1:])]
    return sum(gaps) / len(gaps)


def _scan_transport_report(communication_report: Mapping[str, Any] | None,
                           excluded: frozenset[str]) -> dict[str, Any]:
    """Aggregate the three-pool ledger snapshot once for all consumers."""
    if not isinstance(communication_report, Mapping):
        if communication_report is not None:
            raise ValueError("communication_report must be a mapping or None")
        return {"status": NA_MISSING_COMMUNICATION_REPORT}
    messages = communication_report.get("messages")
    if not isinstance(messages, Mapping):
        raise ValueError("communication report has no messages mapping")
    ledger = communication_report.get("ledger")
    if not isinstance(ledger, list):
        raise ValueError("communication report has no ledger list")
    counts = {"offer_messages": 0, "copies": 0, "delivered": 0,
              "delivered_copies_stale_at_report": 0, "in_flight": 0,
              "expired_ttl": 0, "dropped_channel_loss": 0,
              "dropped_buffer_overflow": 0, "dropped_evaluator_treatment": 0}
    bytes_out = {"offer_bytes": 0, "link_bytes": 0, "delivered_bytes": 0,
                 "interface_rejected_bytes": 0, "served_bytes": 0}
    delivered_rows: list[dict[str, Any]] = []
    queue_waits: list[float] = []
    for message_id in sorted(messages):
        if message_id in excluded:
            continue
        message = messages[message_id]
        if not isinstance(message, Mapping):
            raise ValueError(f"message {message_id!r} is not a mapping")
        size = message.get("size_bytes")
        if type(size) is not int or size <= 0:
            raise ValueError(f"message {message_id!r} has an invalid size_bytes")
        payload = message.get("payload")
        if not isinstance(payload, Mapping):
            raise ValueError(f"message {message_id!r} has no payload")
        published_at = _finite(payload.get("published_at_s"),
                               f"published_at_s of {message_id}")
        recipients = message.get("recipients")
        if not isinstance(recipients, Mapping) or not recipients:
            raise ValueError(f"message {message_id!r} has no recipients")
        counts["offer_messages"] += 1
        bytes_out["offer_bytes"] += size
        for recipient in sorted(recipients):
            delivery = recipients[recipient]
            if not isinstance(delivery, Mapping):
                raise ValueError(
                    f"delivery of {message_id!r} to {recipient!r} is not a mapping")
            status = delivery.get("status")
            reason = delivery.get("drop_reason")
            counts["copies"] += 1
            if reason in _INTERFACE_REJECTION_REASONS:
                # Rejected before entering the link: offered quantity and a
                # rejection counter only, never link occupancy.
                counts["dropped_buffer_overflow"] += 1
                bytes_out["interface_rejected_bytes"] += size
                continue
            bytes_out["link_bytes"] += size
            transmitted_at = _optional_finite(
                delivery.get("transmitted_at_s"),
                f"transmitted_at_s of {message_id!r}/{recipient!r}")
            if transmitted_at is not None:
                queue_waits.append(transmitted_at - published_at)
            delivered_at = _optional_finite(
                delivery.get("delivered_at_s"),
                f"delivered_at_s of {message_id!r}")
            if delivered_at is not None:
                # A copy delivered before the report horizon stays delivered
                # even when the half-open freshness window has since closed
                # (the runtime then relabels its status "expired").  Only
                # copies that never delivered count as TTL loss.
                counts["delivered"] += 1
                if status == "expired":
                    counts["delivered_copies_stale_at_report"] += 1
                bytes_out["delivered_bytes"] += size
                delivered_rows.append({"recipient": recipient,
                                       "delivered_at_s": delivered_at,
                                       "delay_s": delivered_at - published_at})
            elif reason in _EVALUATOR_TREATMENT_REASONS:
                counts["dropped_evaluator_treatment"] += 1
            elif reason in _CHANNEL_LOSS_REASONS:
                counts["dropped_channel_loss"] += 1
            elif status == "expired":
                # TTL timeout after entering the link still occupied it.
                counts["expired_ttl"] += 1
            else:
                counts["in_flight"] += 1
    for row in ledger:
        if (row.get("event") == "bytes_served"
                and row.get("message_id") not in excluded):
            served = row.get("bytes") or 0
            if type(served) is not int or served < 0:
                raise ValueError("bytes_served ledger rows must carry nonnegative bytes")
            bytes_out["served_bytes"] += served
    return {"status": "computed", "time_s": _optional_finite(
        communication_report.get("time_s"), "report time_s"),
        "counts": counts, "bytes": bytes_out,
        "delivered_rows": delivered_rows, "queue_waits": queue_waits}


def _transport_companion(scan: Mapping[str, Any]) -> dict[str, Any]:
    """Protocol process-and-diagnostic quantities from the transport ledger."""
    if scan["status"] != "computed":
        empty = _delay_stats([])
        return {"status": scan["status"],
                "na_reason": "transport report is absent: companion state is N/A",
                "counts": None, "throughput_delivered_bits_per_s": None,
                "actual_loss_rate": None, "end_to_end_delay_s": empty,
                "queue_wait_s": empty, "jitter_s": None, "aoi": _na_aoi()}
    counts, bytes_out = scan["counts"], scan["bytes"]
    report_time = scan["time_s"]
    delivered_bits = bytes_out["delivered_bytes"] * BYTE_TO_BIT_FACTOR
    throughput = (None if report_time is None or report_time <= 0
                  else delivered_bits / report_time)
    loss_rate = (None if counts["copies"] == 0 else
                 1.0 - counts["delivered"] / counts["copies"])
    delays = [row["delay_s"] for row in scan["delivered_rows"]]
    return {
        "status": "computed",
        "counts": counts,
        "bytes_served_bytes": bytes_out["served_bytes"],
        "delivered_bits": delivered_bits,
        "throughput_delivered_bits_per_s": throughput,
        "actual_loss_rate": loss_rate,
        "end_to_end_delay_s": _delay_stats(delays),
        "queue_wait_s": _delay_stats(scan["queue_waits"]),
        "jitter_s": _jitter_mean_absolute_successive_difference(
            scan["delivered_rows"]),
        "aoi": _passthrough_aoi(scan.get("aoi")),
    }


def _na_aoi() -> dict[str, Any]:
    return {"sample_count": None, "sample_mean_s": None, "sample_p95_s": None,
            "time_mean_s": None, "time_p95_s": None}


def _passthrough_aoi(aoi: Any) -> dict[str, Any]:
    if not isinstance(aoi, Mapping):
        return _na_aoi()
    return {"sample_count": aoi.get("sample_count"),
            "sample_mean_s": aoi.get("mean_s"),
            "sample_p95_s": aoi.get("p95_s"),
            "time_mean_s": aoi.get("time_mean_s"),
            "time_p95_s": aoi.get("time_p95_s")}


def _communication_cost(scan: Mapping[str, Any]) -> dict[str, Any]:
    """Section 3.6 communication usage vector: B_offer vs receiver-weighted B_link."""
    if scan["status"] != "computed":
        return {"status": scan["status"],
                "na_reason": "transport report is absent: cost vector is N/A",
                "byte_to_bit_factor": BYTE_TO_BIT_FACTOR,
                "B_offer_bits": None, "B_link_bits": None,
                "delivered_bits": None, "interface_rejected_copies": None,
                "interface_rejected_bits": None}
    counts, bytes_out = scan["counts"], scan["bytes"]
    return {
        "status": "computed",
        "byte_to_bit_factor": BYTE_TO_BIT_FACTOR,
        "B_offer_bits": bytes_out["offer_bytes"] * BYTE_TO_BIT_FACTOR,
        "B_link_bits": bytes_out["link_bytes"] * BYTE_TO_BIT_FACTOR,
        "delivered_bits": bytes_out["delivered_bytes"] * BYTE_TO_BIT_FACTOR,
        "interface_rejected_copies": counts["dropped_buffer_overflow"],
        "interface_rejected_bits": (
            bytes_out["interface_rejected_bytes"] * BYTE_TO_BIT_FACTOR),
        "excluded_message_count": scan.get("excluded_message_count"),
        "receiver_weighting": (
            "B_link charges each admitted recipient copy once; broadcast "
            "multiplies the payload by the admitted recipient count, channel "
            "loss and TTL expiry after admission still occupy the link, and "
            "interface (buffer) rejection stays offered-only"),
    }


def communication_condition_metrics(
    communication_report: Mapping[str, Any] | None,
    progress_inputs: Mapping[str, Any] | None,
    *,
    condition_id: str,
    excluded_message_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Map one communication-condition episode onto protocol 3.2 task metrics.

    ``communication_report`` is the evaluator-only ``CommunicationRuntime``
    report of that episode (replay validation stays with
    ``validate_communication_report``; this function only consumes a
    structurally valid snapshot).  ``progress_inputs`` carries the milestone
    observation vocabulary of :mod:`goal_progress_curve`.  The task
    performance section, the transport companion state, and the cost vector
    degrade to explicit N/A independently — a missing report or missing
    milestones never produce zeros.

    ``excluded_message_ids`` implements the protocol's source-label
    exclusion (benchmark control messages, simulator-internal sync, probe
    injections): those messages leave every count and every byte total.
    """
    _text(condition_id, "condition_id")
    excluded = _validate_excluded(excluded_message_ids)
    scan = _scan_transport_report(communication_report, excluded)
    if scan["status"] == "computed":
        scan["aoi"] = (communication_report or {}).get("aoi")
        scan["excluded_message_count"] = len(excluded)
    return {
        "schema": COMMUNICATION_METRICS_SCHEMA,
        "version": COMMUNICATION_METRICS_VERSION,
        "status": ("computed" if scan["status"] == "computed" else
                   NA_MISSING_COMMUNICATION_REPORT),
        "condition_id": condition_id,
        "task_performance": _task_performance(progress_inputs),
        "transport_companion": _transport_companion(scan),
        "communication_cost": _communication_cost(scan),
        **_RELEASE,
    }


def _performance_mapping(values: Any, field: str) -> list[tuple[str, float | None]]:
    if not isinstance(values, Mapping) or not values:
        raise ValueError(f"{field} must be a nonempty mapping")
    rows: list[tuple[str, float | None]] = []
    for key, value in values.items():
        name = _text(key, f"{field} key")
        rows.append((name, _optional_finite(value, f"{field}[{name!r}]")))
    return rows


def retention_curve(
    performance_by_condition: Mapping[str, float | None],
    *,
    reference_condition: str,
    min_reference: float | None = None,
    thresholds: Sequence[float] = (.9, .5),
) -> dict[str, Any]:
    """Performance retention R_x over increasing fixed-delay / IID-loss severity.

    ``performance_by_condition`` maps condition ids to one preregistered
    task performance ``M`` (``None`` = N/A) in increasing severity order;
    ``reference_condition`` (``x_ref = 0``) must be the first entry.
    ``x_q`` is the first condition whose retention drops below ``q``; the
    result only ever brackets between adjacent tested conditions — no value
    is extrapolated outside the tested range.  A reference below
    ``min_reference`` (``m_min^comm``) voids every retention and threshold.
    """
    _text(reference_condition, "reference_condition")
    rows = _performance_mapping(performance_by_condition,
                                 "performance_by_condition")
    order = [name for name, _value in rows]
    if reference_condition != order[0]:
        raise ValueError(
            "reference_condition must be the first (lowest-severity) entry")
    gate = (None if min_reference is None else
            _finite(min_reference, "min_reference", nonnegative=True))
    qs = _validate_thresholds(thresholds)
    ref_value = dict(rows)[reference_condition]
    if ref_value is None:
        return {"schema": COMMUNICATION_METRICS_SCHEMA,
                "version": COMMUNICATION_METRICS_VERSION,
                "status": "na_reference_missing",
                "reference": {"condition": reference_condition,
                              "performance": None, "valid": None},
                "rows": [{"condition": name, "performance": value,
                          "retention": None} for name, value in rows],
                "thresholds": {str(q): {"status": "na_reference_missing",
                                        "threshold": q} for q in qs},
                "no_extrapolation": True, **_RELEASE}
    reference_valid = gate is None or ref_value >= gate
    zero_reference = ref_value <= 0
    out_rows = []
    for name, value in rows:
        retention = None
        if reference_valid and not zero_reference and value is not None:
            retention = value / ref_value
        out_rows.append({"condition": name, "performance": value,
                         "retention": retention})
    if not reference_valid:
        return {"schema": COMMUNICATION_METRICS_SCHEMA,
                "version": COMMUNICATION_METRICS_VERSION,
                "status": "reference_below_minimum",
                "reference": {"condition": reference_condition,
                              "performance": ref_value,
                              "valid": False},
                "rows": out_rows,
                "thresholds": {str(q): {"status": "reference_below_minimum",
                                        "threshold": q} for q in qs},
                "no_extrapolation": True, **_RELEASE}
    thresholds_out: dict[str, Any] = {}
    for q in qs:
        known = [row for row in out_rows if row["retention"] is not None]
        violating = next((row for row in known[1:]
                          if _falls_below(row["retention"], q)), None)
        if violating is None:
            thresholds_out[str(q)] = {
                "status": "above_tested_maximum", "threshold": q,
                "x_upper_bound_condition": order[-1]}
            continue
        index = out_rows.index(violating)
        preceding = next((row for row in reversed(out_rows[:index])
                          if row["retention"] is not None), None)
        if preceding is None:
            thresholds_out[str(q)] = {
                "status": "at_or_below_tested_minimum", "threshold": q,
                "x_upper_bound_condition": violating["condition"]}
        else:
            thresholds_out[str(q)] = {
                "status": "bracketed", "threshold": q,
                "x_lower_condition": preceding["condition"],
                "x_upper_condition": violating["condition"]}
    result = {"schema": COMMUNICATION_METRICS_SCHEMA,
              "version": COMMUNICATION_METRICS_VERSION, "status": "ok",
              "reference": {"condition": reference_condition,
                            "performance": ref_value, "valid": True},
              "rows": out_rows, "thresholds": thresholds_out,
              "no_extrapolation": True, **_RELEASE}
    if zero_reference:
        result["zero_reference_note"] = (
            "reference performance is not positive: retention is N/A")
    return result


def capacity_thresholds(
    performance_by_capacity: Mapping[float | None, float | None],
    *,
    c0_bps: float,
    min_reference: float | None = None,
    thresholds: Sequence[float] = (.9, .5),
) -> dict[str, Any]:
    """C_90/C_50 brackets from task performance over the absolute capacity axis.

    Keys are absolute capacities in frozen bit/s; the ``None`` key is the
    required unlimited reference (``C_ref = inf``).  ``C_q`` is the lowest
    tested capacity whose retention ``R_C(C) = M(C) / M(inf)`` still meets
    ``q``, reported as a bracket between adjacent tested capacities —
    never a single extrapolated value.  This is the task-performance
    counterpart of ``communication_analysis.summarize_capacity_curve`` (the
    synthetic delivery-rate diagnostic) and reuses its bracket vocabulary.
    """
    c0 = _finite(c0_bps, "c0_bps", positive=True)
    gate = (None if min_reference is None else
            _finite(min_reference, "min_reference", nonnegative=True))
    qs = _validate_thresholds(thresholds)
    if not isinstance(performance_by_capacity, Mapping) or not performance_by_capacity:
        raise ValueError("performance_by_capacity must be a nonempty mapping")
    finite_points: list[tuple[float, float | None]] = []
    reference_value: float | None = None
    for raw_capacity, value in performance_by_capacity.items():
        parsed = _optional_finite(value, "performance value")
        if raw_capacity is None:
            if reference_value is not None:
                raise ValueError("performance_by_capacity has duplicate unlimited reference")
            reference_value = parsed
            continue
        capacity = _finite(raw_capacity, "capacity key", positive=True)
        finite_points.append((capacity, parsed))
    if reference_value is None:
        return {"schema": COMMUNICATION_METRICS_SCHEMA,
                "version": COMMUNICATION_METRICS_VERSION,
                "status": "missing_reference", "c0_bps": c0,
                "capacity_unit": "bits_per_second",
                "reference": None, "points": [],
                "thresholds": {str(q): {"status": "missing_reference",
                                        "threshold": q} for q in qs},
                "no_extrapolation": True, **_RELEASE}
    finite_points.sort()
    points = [{"capacity_bits_per_s": capacity, "performance": value,
               "retention": (None if reference_value <= 0 or value is None
                             else value / reference_value),
               "capacity_ratio_c0": capacity / c0}
              for capacity, value in finite_points]
    reference_valid = gate is None or reference_value >= gate
    reference_block = {"capacity_bits_per_s": None, "capacity_mode": "unlimited",
                       "performance": reference_value,
                       "valid": reference_valid and reference_value > 0}
    if not reference_valid or reference_value <= 0:
        status = ("reference_below_minimum" if not reference_valid
                  else "na_zero_reference_performance")
        return {"schema": COMMUNICATION_METRICS_SCHEMA,
                "version": COMMUNICATION_METRICS_VERSION, "status": status,
                "c0_bps": c0, "capacity_unit": "bits_per_second",
                "reference": reference_block, "points": points,
                "thresholds": {str(q): {"status": status, "threshold": q}
                               for q in qs},
                "no_extrapolation": True, **_RELEASE}
    thresholds_out: dict[str, Any] = {}
    known = [point for point in points if point["retention"] is not None]
    for q in qs:
        qualified = [point for point in known
                     if _meets(point["retention"], q)]
        if not qualified:
            thresholds_out[str(q)] = {
                "status": "above_tested_maximum", "threshold": q,
                "capacity_lower_bound_bits_per_s": (
                    finite_points[-1][0] if finite_points else None)}
            continue
        high = min(qualified, key=lambda point: point["capacity_bits_per_s"])
        lower = [point for point in known
                 if point["capacity_bits_per_s"] < high["capacity_bits_per_s"]]
        if lower and _falls_below(lower[-1]["retention"], q):
            thresholds_out[str(q)] = {
                "status": "bracketed", "threshold": q,
                "capacity_lower_bits_per_s": lower[-1]["capacity_bits_per_s"],
                "capacity_upper_bits_per_s": high["capacity_bits_per_s"]}
        else:
            thresholds_out[str(q)] = {
                "status": "at_or_below_tested_minimum", "threshold": q,
                "capacity_upper_bound_bits_per_s": high["capacity_bits_per_s"]}
    return {"schema": COMMUNICATION_METRICS_SCHEMA,
            "version": COMMUNICATION_METRICS_VERSION, "status": "ok",
            "c0_bps": c0, "capacity_unit": "bits_per_second",
            "reference": reference_block, "points": points,
            "thresholds": thresholds_out,
            "measurement_scope": "task_performance_communication_capacity",
            "no_extrapolation": True, **_RELEASE}


def communication_dependency(perfect: float | None, zero: float | None,
                             *, m_min: float) -> dict[str, Any]:
    """Communication dependency D_comm = (M_perfect - M_zero) / M_perfect.

    ``D_comm`` is a condition-and-diagnostic quantity, not a
    communication-adaptation score: larger values mean communication is more
    valuable for this task/system, not that adaptation is weaker.  It is
    computed only behind the ``m_min`` reference-validity gate; both M
    values, the absolute difference, and the reference validity are always
    reported together.
    """
    gate = _finite(m_min, "m_min", nonnegative=True)
    perfect_value = _optional_finite(perfect, "perfect")
    zero_value = _optional_finite(zero, "zero")
    base = {"schema": COMMUNICATION_METRICS_SCHEMA,
            "version": COMMUNICATION_METRICS_VERSION,
            "M_perfect": perfect_value, "M_zero": zero_value, "m_min": gate,
            "diagnostic_only": True, "not_communication_score": True,
            **_RELEASE}
    if perfect_value is None or zero_value is None:
        return {**base, "status": "na_missing_performance",
                "absolute_difference": None, "D_comm": None,
                "reference_valid": None}
    difference = perfect_value - zero_value
    if perfect_value < gate:
        return {**base, "status": "reference_below_minimum",
                "absolute_difference": difference, "D_comm": None,
                "reference_valid": False}
    if perfect_value == 0:
        return {**base, "status": "na_zero_reference_performance",
                "absolute_difference": difference, "D_comm": None,
                "reference_valid": True}
    return {**base, "status": "computed", "absolute_difference": difference,
            "D_comm": difference / perfect_value, "reference_valid": True}


def _resolve_contrast_conditions(conditions: set[str],
                                 left_condition: str | None,
                                 right_condition: str | None
                                 ) -> tuple[str, str, str | None]:
    if (left_condition is None) != (right_condition is None):
        raise ValueError("left_condition and right_condition must be given together")
    if left_condition is not None and right_condition is not None:
        left = _text(left_condition, "left_condition")
        right = _text(right_condition, "right_condition")
        if left == right:
            raise ValueError("left_condition and right_condition must differ")
        return left, right, None
    # Default orientations follow the capacity-experiment condition
    # vocabulary so JP/BP rows plug in directly: JP = M_jitter - M_fixed,
    # BP = M_burst - M_IID.
    if conditions == _JP_CONDITIONS:
        return "fixed_delay", "jitter", "JP"
    if conditions == _BP_CONDITIONS:
        return "iid_loss", "burst_loss", "BP"
    raise ValueError(
        "paired rows carry neither a known condition pair "
        "(fixed_delay/jitter or iid_loss/burst_loss) nor explicit "
        "left_condition/right_condition")


def distribution_contrast(
    paired_observations: Sequence[Mapping[str, Any]],
    *,
    metric: str,
    left_condition: str | None = None,
    right_condition: str | None = None,
    bootstrap_samples: int = 2000,
    seed: int = 0,
) -> dict[str, Any]:
    """Same-mean distribution contrast JP/BP on task performance M.

    Rows reuse the capacity-experiment paired structure
    (``base_instance_id``, ``channel_seed``, ``comparison_level``,
    ``condition``, ``metric_value``).  Differences are paired by base
    instance, channel seed and comparison level, averaged over channel
    seeds within each base instance before bootstrap resampling; unmatched
    pairs never enter the effect.  The reported difference is
    ``M_right - M_left``; with the default orientations that is exactly
    ``JP = M_jitter - M_fixed`` and ``BP = M_burst - M_IID``.
    """
    _text(metric, "metric")
    if isinstance(paired_observations, (str, bytes)) or not isinstance(
            paired_observations, Sequence):
        raise ValueError("paired_observations must be a sequence of objects")
    values: dict[tuple[str, str, str, str], float] = {}
    conditions: set[str] = set()
    for row in paired_observations:
        if not isinstance(row, Mapping):
            raise ValueError("each paired observation must be an object")
        base = _text(row.get("base_instance_id"), "base_instance_id")
        channel = _text(row.get("channel_seed"), "channel_seed")
        level = _text(row.get("comparison_level"), "comparison_level")
        condition = _text(row.get("condition"), "condition")
        if "metric_value" in row:
            value = row["metric_value"]
        else:
            metrics = row.get("metrics")
            value = metrics.get(metric) if isinstance(metrics, Mapping) else None
        value = _finite(value, "metric_value")
        row_metric = row.get("metric")
        if row_metric is not None and row_metric != metric:
            raise ValueError("paired observations contain a different metric")
        key = (base, channel, level, condition)
        if key in values:
            raise ValueError(f"duplicate observation for {key!r}")
        values[key] = value
        conditions.add(condition)
    if not values:
        raise ValueError("paired_observations must not be empty")
    left, right, effect = _resolve_contrast_conditions(
        conditions, left_condition, right_condition)
    foreign = conditions - {left, right}
    if foreign:
        raise ValueError(
            f"observation conditions are outside the requested pair: {sorted(foreign)}")
    levels = sorted({key[2] for key in values})
    effects = []
    for level in levels:
        by_base: dict[str, list[float]] = defaultdict(list)
        pair_keys = {(base, channel) for base, channel, current_level, _condition
                     in values if current_level == level}
        for base, channel in sorted(pair_keys):
            left_value = values.get((base, channel, level, left))
            right_value = values.get((base, channel, level, right))
            if left_value is not None and right_value is not None:
                by_base[base].append(right_value - left_value)
        per_base = {base: sum(deltas) / len(deltas)
                    for base, deltas in by_base.items()}
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
                        "bootstrap_ci95": _bootstrap(
                            sample, seed=seed + len(effects),
                            samples=bootstrap_samples)})
    return {"schema": COMMUNICATION_METRICS_SCHEMA,
            "version": COMMUNICATION_METRICS_VERSION, "status": "ok",
            "metric": metric, "left_condition": left,
            "right_condition": right, "protocol_effect": effect,
            "effects": effects, "no_unmatched_pair_inference": True,
            **_RELEASE}


__all__ = [
    "BYTE_TO_BIT_FACTOR", "COMMUNICATION_METRICS_SCHEMA",
    "COMMUNICATION_METRICS_VERSION", "capacity_thresholds",
    "communication_condition_metrics", "communication_dependency",
    "distribution_contrast", "retention_curve",
]
