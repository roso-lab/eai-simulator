"""Dependency-free, endpoint-neutral statistical primitives.

These helpers calculate registered mathematical summaries only.  They do not
choose scientific endpoints, generate p-values, or grant release eligibility.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from typing import Any

from .schema import AnalysisError, number, text


def percentile(values: Sequence[float], q: float) -> float:
    """Return the linearly interpolated empirical quantile on ``[0, 1]``."""

    quantile = number(q, 0, 1)
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence) or not values:
        raise AnalysisError("percentile requires a nonempty sequence")
    ordered = sorted(number(value) for value in values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _event_inputs(
    samples: list[dict[str, Any]], causes: tuple[str, ...], horizon: float
) -> tuple[list[tuple[float, str]], tuple[str, ...], float]:
    if not isinstance(samples, list):
        raise AnalysisError("competing-risk samples must be a list")
    if not isinstance(causes, tuple) or not causes:
        raise AnalysisError("causes must be a nonempty tuple")
    parsed_causes = tuple(text(cause) for cause in causes)
    if len(set(parsed_causes)) != len(parsed_causes) or "censor" in parsed_causes:
        raise AnalysisError("event causes must be unique and cannot include censor")
    stop = number(horizon, 0)
    parsed = []
    allowed = set(parsed_causes) | {"censor"}
    for sample in samples:
        if not isinstance(sample, dict) or set(sample) != {"time", "cause"}:
            raise AnalysisError("each event sample requires exactly time and cause")
        time = number(sample["time"], 0)
        cause = sample["cause"]
        if not isinstance(cause, str) or cause not in allowed:
            raise AnalysisError(f"unregistered event cause: {cause!r}")
        parsed.append((time, cause))
    return parsed, parsed_causes, stop


def competing_risks(
    samples: list[dict[str, Any]], horizon: float, causes: tuple[str, ...]
) -> dict[str, Any]:
    """Compute Aalen--Johansen CIFs and all-cause restricted mean survival.

    Event and censor observations tied at a time use the same pre-time risk set:
    all event increments are applied first, then censored subjects leave.  A
    subject observed beyond ``horizon`` is administratively censored at the
    horizon and never converted into an event.
    """

    parsed, cause_names, stop = _event_inputs(samples, causes, horizon)
    empty_cif = {cause: None for cause in cause_names}
    if not parsed:
        return {
            "status": "N/A",
            "reason": "empty_sample",
            "cif": empty_cif,
            "rmst": None,
            "n_events": 0,
            "n_censored": 0,
            "risk": 0,
            "at_risk_at_horizon": 0,
            "observed_censored": 0,
            "administrative_censored": 0,
            "curve": [],
        }

    # A horizon beyond the last observation is unsupported while positive
    # survival remains.  We still build the observed curve for audit, but never
    # expose a truncated numerical estimand as if follow-up were complete.
    max_observed = max(time for time, _ in parsed)
    groups: dict[float, dict[str, Any]] = {}
    for time, cause in parsed:
        if time > stop:
            continue
        group = groups.setdefault(
            time,
            {"events": {name: 0 for name in cause_names}, "censored": 0},
        )
        if cause == "censor":
            group["censored"] += 1
        else:
            group["events"][cause] += 1

    administrative = sum(time > stop for time, _ in parsed)
    if administrative:
        group = groups.setdefault(
            stop,
            {"events": {name: 0 for name in cause_names}, "censored": 0},
        )
        group["censored"] += administrative

    at_risk = len(parsed)
    survival = 1.0
    cif = {cause: 0.0 for cause in cause_names}
    rmst = 0.0
    last_time = 0.0
    total_events = 0
    observed_censored = 0
    risk_at_horizon = at_risk if stop == 0 else 0
    curve: list[dict[str, Any]] = []

    for time in sorted(groups):
        if time > stop:  # defensive; construction already clips at horizon
            break
        if time < last_time:
            raise AnalysisError("event times are not monotone")
        rmst += survival * (time - last_time)
        group = groups[time]
        event_counts = dict(group["events"])
        events = sum(event_counts.values())
        censored = int(group["censored"])
        if events + censored > at_risk:
            raise AnalysisError("event/censor count exceeds the risk set")
        before = survival
        if events:
            for cause in cause_names:
                cif[cause] += before * event_counts[cause] / at_risk
            survival = before * (1.0 - events / at_risk)
        if time == stop:
            risk_at_horizon = at_risk
        total_events += events
        # Administrative censoring is included in group.censored at the horizon
        # but kept separate in the aggregate audit fields.
        observed_here = censored - (administrative if time == stop else 0)
        observed_censored += observed_here
        curve.append(
            {
                "time": time,
                "risk": at_risk,
                "events": event_counts,
                "censored": censored,
                "administrative_censored": administrative if time == stop else 0,
                "survival": survival,
                "cif": dict(cif),
            }
        )
        at_risk -= events + censored
        last_time = time

    if max_observed < stop and survival > 0.0:
        return {
            "status": "N/A",
            "reason": "horizon_beyond_observed_support",
            "cif": empty_cif,
            "rmst": None,
            "n_events": total_events,
            "n_censored": observed_censored,
            "risk": len(parsed),
            "at_risk_at_horizon": 0,
            "observed_censored": observed_censored,
            "administrative_censored": 0,
            "curve": curve,
        }

    # This tail is either within observed support, or survival is already zero.
    rmst += survival * (stop - last_time)
    if stop > last_time and survival == 0.0:
        curve.append(
            {
                "time": stop,
                "risk": 0,
                "events": {cause: 0 for cause in cause_names},
                "censored": 0,
                "administrative_censored": 0,
                "survival": 0.0,
                "cif": dict(cif),
            }
        )
    return {
        "status": "computed",
        "reason": None,
        "cif": cif,
        "rmst": rmst,
        "n_events": total_events,
        "n_censored": observed_censored + administrative,
        "risk": len(parsed),
        "at_risk_at_horizon": risk_at_horizon,
        "observed_censored": observed_censored,
        "administrative_censored": administrative,
        "curve": curve,
    }


def holm_adjust(
    p_values: Mapping[str, float | None],
    declared_family: Sequence[str],
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Apply Holm adjustment without shrinking the preregistered family."""

    if not isinstance(p_values, Mapping):
        raise AnalysisError("p_values must be a mapping")
    if isinstance(declared_family, (str, bytes)) or not isinstance(declared_family, Sequence):
        raise AnalysisError("declared_family must be a sequence")
    family = tuple(text(name) for name in declared_family)
    if not family or len(set(family)) != len(family):
        raise AnalysisError("declared_family must be nonempty and unique")
    if set(p_values) - set(family):
        raise AnalysisError("p_values contains an undeclared hypothesis")
    cutoff = number(alpha, 0, 1)
    if cutoff in {0.0, 1.0}:
        raise AnalysisError("alpha must be strictly between zero and one")
    observed = []
    for name in family:
        value = p_values.get(name)
        if value is not None:
            observed.append((number(value, 0, 1), name))
    observed.sort(key=lambda item: (item[0], item[1]))
    family_size = len(family)
    adjusted: dict[str, float] = {}
    rejected: dict[str, bool] = {}
    running_adjusted = 0.0
    still_rejecting = True
    for index, (p_value, name) in enumerate(observed):
        multiplier = family_size - index
        running_adjusted = max(running_adjusted, min(1.0, multiplier * p_value))
        adjusted[name] = running_adjusted
        reject = still_rejecting and p_value <= cutoff / multiplier
        rejected[name] = reject
        if not reject:
            still_rejecting = False
    tests = {}
    for name in family:
        value = p_values.get(name)
        tests[name] = (
            {
                "status": "N/A",
                "reason": "missing_p_value",
                "p_value": None,
                "adjusted_p": None,
                "reject": None,
            }
            if value is None
            else {
                "status": "computed",
                "reason": None,
                "p_value": float(value),
                "adjusted_p": adjusted[name],
                "reject": rejected[name],
            }
        )
    return {
        "status": "computed" if len(observed) == family_size else "N/A",
        "reason": None if len(observed) == family_size else "incomplete_declared_family",
        "alpha": cutoff,
        "family_size": family_size,
        "observed_tests": len(observed),
        "tests": tests,
    }


def noninferiority(lower_bound: float | None, margin: float) -> dict[str, Any]:
    """Compare a lower confidence bound with the preregistered ``-margin``."""

    delta = number(margin, 0)
    if lower_bound is None:
        return {
            "status": "N/A",
            "reason": "missing_lower_bound",
            "lower_bound": None,
            "margin": delta,
            "threshold": -delta,
        }
    lower = number(lower_bound)
    return {
        "status": "PASS" if lower >= -delta else "FAIL",
        "reason": None,
        "lower_bound": lower,
        "margin": delta,
        "threshold": -delta,
    }


def simultaneous_band(
    point_estimates: Mapping[str, float],
    joint_bootstrap_rows: Sequence[Mapping[str, float]],
    confidence: float = 0.95,
) -> dict[str, Any]:
    """Build a conservative sup-absolute-deviation band on a frozen grid."""

    if not isinstance(point_estimates, Mapping) or not point_estimates:
        raise AnalysisError("point_estimates must be a nonempty mapping")
    grid = tuple(text(name) for name in point_estimates)
    if len(set(grid)) != len(grid):  # mappings cannot normally reach this
        raise AnalysisError("duplicate grid identifier")
    points = {name: number(point_estimates[name]) for name in grid}
    level = number(confidence, 0, 1)
    if level in {0.0, 1.0}:
        raise AnalysisError("confidence must be strictly between zero and one")
    if isinstance(joint_bootstrap_rows, (str, bytes)) or not isinstance(joint_bootstrap_rows, Sequence):
        raise AnalysisError("joint_bootstrap_rows must be a sequence")
    deviations = []
    for row in joint_bootstrap_rows:
        if not isinstance(row, Mapping) or set(row) != set(grid):
            raise AnalysisError("every bootstrap row must contain the exact frozen grid")
        deviations.append(max(abs(number(row[name]) - points[name]) for name in grid))
    if not deviations:
        return {
            "status": "N/A",
            "reason": "empty_joint_bootstrap",
            "confidence": level,
            "critical": None,
            "grid": list(grid),
            "lower": None,
            "upper": None,
            "resamples": 0,
            "method": "sup_absolute_deviation",
        }
    critical = percentile(deviations, level)
    return {
        "status": "computed",
        "reason": None,
        "confidence": level,
        "critical": critical,
        "grid": list(grid),
        "lower": {name: points[name] - critical for name in grid},
        "upper": {name: points[name] + critical for name in grid},
        "resamples": len(deviations),
        "method": "sup_absolute_deviation",
    }


def crossing_brackets(
    grid: Sequence[float],
    values: Sequence[float],
    threshold: float,
    direction: str,
) -> dict[str, Any]:
    """Report adjacent measured crossing brackets without interpolation.

    The result is descriptive only.  A band-qualified crossing must be assessed
    separately from simultaneous lower/upper bands; point values cannot certify
    it.
    """

    if isinstance(grid, (str, bytes)) or not isinstance(grid, Sequence):
        raise AnalysisError("grid must be a sequence")
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise AnalysisError("values must be a sequence")
    if len(grid) != len(values) or len(grid) < 2:
        raise AnalysisError("crossing requires equally sized grid/value sequences of length >= 2")
    x = [number(item) for item in grid]
    y = [number(item) for item in values]
    if any(right <= left for left, right in zip(x, x[1:])):
        raise AnalysisError("grid must be strictly increasing")
    target = number(threshold)
    if direction not in {"increasing", "decreasing"}:
        raise AnalysisError("direction must be increasing or decreasing")
    # The target is the first measured threshold crossing. For an increasing
    # curve the crossed side includes equality; for a decreasing stress curve
    # the registered event is strictly below the threshold. If the first point
    # is already on the crossed side, the crossing is left-grid-censored. If no
    # adjacent pair reaches it, the crossing is right-grid-censored.
    already_crossed = y[0] >= target if direction == "increasing" else y[0] < target
    grid_censor = "left" if already_crossed else None
    brackets = []
    if not already_crossed:
        for index, (left, right) in enumerate(zip(y, y[1:])):
            crossed = (
                left < target <= right
                if direction == "increasing"
                else left >= target > right
            )
            if crossed:
                brackets.append(
                    {
                        "lower": x[index],
                        "upper": x[index + 1],
                        "lower_value": left,
                        "upper_value": right,
                        "qualification": "descriptive",
                    }
                )
                break
        if not brackets:
            grid_censor = "right"
    return {
        "status": "descriptive",
        "reason": None if brackets else f"{grid_censor}_grid_censored",
        "direction": direction,
        "threshold": target,
        "brackets": brackets,
        "grid_censor": grid_censor,
        "interpolated": False,
        "band_qualified": False,
        "qualification": "descriptive_only; simultaneous_band_required_for_band_qualification",
    }


__all__ = [
    "competing_risks",
    "crossing_brackets",
    "holm_adjust",
    "noninferiority",
    "percentile",
    "simultaneous_band",
]
