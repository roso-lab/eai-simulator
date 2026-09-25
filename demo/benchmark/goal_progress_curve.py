"""Protocol progress curves: Q(t), MCR, AUP, and execution-window PGI/nPGI.

The evaluation protocol makes the weighted contract-milestone progress
:math:`Q_i(t)` the universal result axis for temporal coordination, dynamic
reconfiguration, cross-episode learning and cost efficiency.  This module
computes those curves offline from milestone observation streams that
episode archives already carry (first-satisfaction times), under the
protocol's rules:

- weights are frozen with the observation set (default policy
  ``protocol-v1-equal``: every active milestone weighs one — the same
  equal-weight rule the protocol prescribes for the required-milestone
  component);
- :math:`y_m(t)` is a monotone step: once a milestone is satisfied (and
  held for its stability duration) it stays satisfied; duplicate later
  observations never double-count (``anti_double_count``);
- :math:`Q_i(H)` holds the last valid external observation up to ``H``;
  AUP integrates the step function exactly (legal discretisation), PGI and
  nPGI run over the actual execution window ``[0, T_exec]`` with zero-length
  windows explicitly N/A — never a silent zero or divide-by-zero constant.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

PROGRESS_CURVE_SCHEMA = "eai.benchmark.goal_progress_curve"
PROGRESS_CURVE_VERSION = 1
EQUAL_WEIGHT_POLICY = "protocol-v1-equal"
_RELEASE = {"execution_validated": False, "business_outcomes_validated": False,
            "scoreable": False}


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    value = float(value)
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"{label} must be a finite number")
    return value


def _normalize_observations(observations: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    for row in observations or []:
        if not isinstance(row, Mapping):
            raise ValueError("milestone observations must be mappings")
        milestone_id = row.get("milestone_id")
        if not isinstance(milestone_id, str) or not milestone_id:
            raise ValueError("milestone_id must be a nonempty string")
        satisfied_at = _finite(row.get("satisfied_at_s"),
                               f"satisfied_at_s of {milestone_id}")
        if satisfied_at < 0:
            raise ValueError("satisfied_at_s must be nonnegative")
        stability = row.get("stability_duration_s", 0.0)
        stability = _finite(stability, f"stability_duration_s of {milestone_id}")
        if stability < 0:
            raise ValueError("stability_duration_s must be nonnegative")
        candidate = {"milestone_id": milestone_id,
                     "satisfied_at_s": satisfied_at,
                     "stability_duration_s": stability}
        # anti_double_count: the FIRST satisfaction is the earliest reported
        # one; later duplicate observations never double-count.
        previous = seen.get(milestone_id)
        if previous is None or satisfied_at < previous["satisfied_at_s"]:
            seen[milestone_id] = candidate
        satisfied_at = _finite(row.get("satisfied_at_s"),
                               f"satisfied_at_s of {milestone_id}")
        if satisfied_at < 0:
            raise ValueError("satisfied_at_s must be nonnegative")
        stability = row.get("stability_duration_s", 0.0)
        stability = _finite(stability, f"stability_duration_s of {milestone_id}")
        if stability < 0:
            raise ValueError("stability_duration_s must be nonnegative")
    rows = list(seen.values())
    rows.sort(key=lambda item: item["satisfied_at_s"])
    return rows


def _frozen_weights(active_ids: Sequence[str],
                    weights: Mapping[str, float] | None) -> dict[str, float]:
    if weights is None:
        return {milestone_id: 1.0 for milestone_id in active_ids}
    frozen: dict[str, float] = {}
    for milestone_id in active_ids:
        value = weights.get(milestone_id)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"weight of {milestone_id} must be a nonnegative number")
        frozen[milestone_id] = float(value)
    return frozen


def _q_at(rows: Sequence[Mapping[str, Any]], weights: Mapping[str, float],
          total_weight: float, t: float) -> float:
    satisfied = sum(weights[row["milestone_id"]] for row in rows
                    if row["satisfied_at_s"] + row["stability_duration_s"] <= t)
    return satisfied / total_weight


def build_progress_curve(
    active_milestones: Iterable[str],
    observations: Iterable[Mapping[str, Any]],
    *,
    horizon_s: float,
    execution_end_s: float | None = None,
    weights: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Compute Q(t)/MCR/AUP/PGI/nPGI for one episode.

    ``active_milestones`` freezes the working set; observations carry the
    first external satisfaction time per milestone (absent milestones stay
    unsatisfied — a missing measurement is never counted as done).
    ``execution_end_s`` (``t_term``) bounds the cost observation window;
    ``None`` means the full horizon was used.
    """
    active = sorted(set(active_milestones or []))
    if not active:
        return {"schema": PROGRESS_CURVE_SCHEMA, "version": PROGRESS_CURVE_VERSION,
                "status": "na_empty_active_set",
                "na_reason": "no active milestones: Q/MCR/AUP are N/A",
                **_RELEASE}
    horizon = _finite(horizon_s, "horizon_s")
    if horizon <= 0:
        raise ValueError("horizon_s must be positive")
    frozen = _frozen_weights(active, weights)
    total_weight = sum(frozen.values())
    policy = EQUAL_WEIGHT_POLICY if weights is None else "explicit_frozen"
    if total_weight <= 0:
        return {"schema": PROGRESS_CURVE_SCHEMA, "version": PROGRESS_CURVE_VERSION,
                "status": "na_zero_total_weight",
                "na_reason": "total milestone weight is zero",
                "weight_policy": policy, **_RELEASE}
    rows = _normalize_observations(observations)
    unknown = {row["milestone_id"] for row in rows} - set(active)
    if unknown:
        raise ValueError(f"observations reference inactive milestones: {sorted(unknown)}")

    # Exact step-function integral: Q is piecewise constant, changing only
    # at each completion instant (satisfied_at + stability).
    change_points = sorted({min(row["satisfied_at_s"] + row["stability_duration_s"],
                                horizon) for row in rows}
                           | {0.0, horizon})
    area = 0.0
    q_samples: list[dict[str, float]] = []
    for start, end in zip(change_points, change_points[1:]):
        value = _q_at(rows, frozen, total_weight, start)
        area += value * (end - start)
        q_samples.append({"t_s": start, "Q": value})
    q_h = _q_at(rows, frozen, total_weight, horizon)

    t_exec = horizon if execution_end_s is None else _finite(
        execution_end_s, "execution_end_s")
    if t_exec < 0 or t_exec > horizon:
        raise ValueError("execution_end_s must lie in [0, horizon_s]")
    pgi = 0.0
    for start, end in zip(change_points, change_points[1:]):
        window_end = min(end, t_exec)
        if window_end <= start:
            break
        pgi += (1.0 - _q_at(rows, frozen, total_weight, start)) * (window_end - start)

    result: dict[str, Any] = {
        "schema": PROGRESS_CURVE_SCHEMA, "version": PROGRESS_CURVE_VERSION,
        "status": "computed",
        "active_milestone_count": len(active),
        "weight_policy": policy,
        "horizon_s": horizon,
        "Q_H": q_h,
        "MCR": q_h,  # same weighted completion at H by definition
        "AUP": area / horizon,
        "Q_samples": q_samples,
        "completed_milestones": [row["milestone_id"] for row in rows
                                 if row["satisfied_at_s"]
                                 + row["stability_duration_s"] <= horizon],
        "execution_window": {"t_exec_s": t_exec,
                             "source": "horizon" if execution_end_s is None
                                       else "declared_end"},
        "PGI_exec": pgi,
        **_RELEASE,
    }
    if t_exec > 0:
        result["nPGI_exec"] = pgi / t_exec
        result["S_progress"] = 1.0 - pgi / t_exec
        result["nPGI_note"] = None
    else:
        result["nPGI_exec"] = None
        result["S_progress"] = None
        result["nPGI_note"] = "zero_length_execution_window_na"
    if q_h == 0:
        result["per_progress_note"] = "undefined_zero_progress"
    return result


def progress_curve_from_archive_mappings(
    active_milestones: Iterable[str],
    archive_rows: Iterable[Mapping[str, Any]],
    *,
    horizon_s: float,
    kind_to_milestone: Mapping[str, str],
    time_key: str = "time_s",
    execution_end_s: float | None = None,
) -> dict[str, Any]:
    """Adapt archive evidence rows (kind + time) into milestone observations."""
    observations: list[dict[str, Any]] = []
    for row in archive_rows or []:
        milestone_id = kind_to_milestone.get(row.get("kind", ""))
        if milestone_id is None:
            continue
        time_value = row.get(time_key)
        if time_value is None and isinstance(row.get("stamp"), Mapping):
            time_value = row["stamp"].get(time_key)
        if time_value is None and isinstance(row.get("arrival"), Mapping):
            time_value = row["arrival"].get(time_key)
        observations.append({"milestone_id": milestone_id,
                             "satisfied_at_s": time_value})
    return build_progress_curve(active_milestones, observations,
                                horizon_s=horizon_s,
                                execution_end_s=execution_end_s)


__all__ = [
    "EQUAL_WEIGHT_POLICY", "PROGRESS_CURVE_SCHEMA", "PROGRESS_CURVE_VERSION",
    "build_progress_curve", "progress_curve_from_archive_mappings",
]
