"""Kaplan-Meier survival curves and RMST for protocol sections 2.6/3.3/3.5.

This module closes the legacy aggregation gap #1 left open by
:mod:`demo.benchmark.goal_metric_aggregation`: that aggregator collects the
right-censored ``(tilde_tau, Delta)`` pairs of the first coordination stall
(protocol 3.3) and the recovery-duration pairs
:math:`(\\widetilde L_{im},\\Delta_{im}^{rec})`, but it only counts events —
it never estimates the censored time distribution.  The estimators here are
the protocol-mandated ones:

* **Kaplan-Meier** (protocol 3.5)
  :math:`S_n^{c}(t)=P(T_n^{c}>t)` — the completion-time (or first-stall /
  recovery-duration) survival curve as a monotone non-increasing step
  function.  The curve drops only at event times; a censoring time removes
  its subject from the risk set without moving :math:`S`.  Ties follow the
  standard convention: events at time :math:`t` happen before censorings at
  :math:`t` are removed, so subjects censored at :math:`t` stay in the risk
  set at :math:`t`.
* **RMST** (protocol 3.5)
  :math:`\\mathrm{RMST}_n^{c}(H)=\\int_0^H S_n^{c}(t)\\,dt` — the area under
  the survival curve on the preregistered window :math:`[0,H]`.  Right
  censoring is never extrapolated: after the last observation the curve is
  carried flat to :math:`H`; :math:`S` is **not** assumed to drop to 0 behind
  a censored observation.
* **Learning gain direction** (protocol 3.5)
  :math:`LG_n^{\\mathrm{RMST}}=\\mathrm{RMST}^{C}(H)-\\mathrm{RMST}^{R}(H)`
  where :math:`C` is the no-experience baseline condition and :math:`R` the
  learning condition; a positive difference means the learning condition
  completes earlier within the fixed horizon.
* **Recovery probability** (protocol 3.3)
  :math:`P(T_{\\mathrm{rec}}\\le\\ell^{\\star})=1-S(\\ell^{\\star})` for the
  preregistered duration :math:`\\ell^{\\star}`, plus the general two-point
  window mass :math:`S(\\mathrm{lo})-S(\\mathrm{hi})=P(\\mathrm{lo}<T\\le\\mathrm{hi})`.

Protocol 2.6 statistical discipline is preserved end to end: response /
confirmation / recovery /达标 times are reported as "probability of happening
within :math:`H`" together with the right-censored time distribution; empty
sets, all-empty episode collections and all-censored pair sets return
explicit N/A labels, never silent constants; uncertainty is the
instance-level (episode/cluster) stratified bootstrap — resample pair-set
clusters first, then the pairs inside every drawn cluster, rebuild the KM
curve and take the 2.5/97.5 percentiles of the RMST distribution — the same
discipline as ``goal_metric_aggregation``.  Pooling the same-kind pair sets
of many episodes into one KM curve is the protocol-allowed direct merge; the
bootstrap keeps the cluster structure explicit so pooled events of one
episode are never treated as independent samples.

This module is pure Python and offline; it never starts Isaac, ROS or a
network transfer, computes no protocol score, and every output carries
``release=False`` (formal promotion is a separate release action, mirroring
the aggregator's no-approved-state rule).
"""

from __future__ import annotations

import bisect
import math
import random
from collections.abc import Mapping, Sequence
from typing import Any

SURVIVAL_SCHEMA = "eai.benchmark.goal_survival_analysis"
SURVIVAL_VERSION = 1
PROTOCOL_SECTION = "2.6"
PROTOCOL_SECTION_STALL = "3.3"
PROTOCOL_SECTION_LEARNING = "3.5"

#: Explicit N/A labels (protocol 2.6: predefined labels, never constants).
#: The first three intentionally reuse the ``goal_metric_aggregation``
#: vocabulary so survival N/A states and aggregation N/A states read as one
#: protocol taxonomy.
NA_EMPTY_SET = "na_empty_set"
NA_ALL_MISSING = "na_all_instances_missing"
NA_NO_EVENTS = "na_no_events_all_right_censored"
NA_ELL_STAR_BEYOND_HORIZON = "na_ell_star_beyond_horizon"
NA_WINDOW_BEYOND_HORIZON = "na_window_beyond_horizon"

DEFAULT_BOOTSTRAP_ITERATIONS = 2000
DEFAULT_BOOTSTRAP_SEED = 0

_RELEASE = {"release": False, "scoreable": False,
            "execution_validated": False,
            "business_outcomes_validated": False}

_NO_EXTRAPOLATION_RULE = (
    "right censoring is never extrapolated: after the last observation the "
    "survival curve is carried flat to H and S is not assumed to be 0 behind "
    "a censored observation; H is never treated as a true event time")

#: Mapping-key vocabularies accepted for one pair.  They cover the pair
#: shapes emitted by ``goal_metric_aggregation`` (``tilde_tau_det_s`` /
#: ``delta_i`` first-stall pairs and the ``L_obs_s`` / ``recovery_observed``
#: recovery-duration rows of stall episodes) plus the abstract protocol
#: notation (``tilde_tau`` / ``Delta``, ``tilde_L``).
_TIME_KEYS = ("time_s", "tilde_tau_det_s", "tilde_tau", "tilde_L", "L_obs_s")
_EVENT_KEYS = ("delta_i", "Delta_i", "delta", "Delta", "event_indicator",
               "recovery_observed")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def _nonnegative(value: Any, label: str) -> float:
    number = _finite(value, label)
    if number < 0:
        raise ValueError(f"{label} must be nonnegative")
    return number


def _event_indicator(value: Any, label: str) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)) and float(value) in (0.0, 1.0):
        return int(float(value))
    raise ValueError(f"{label} must be a 0/1 event indicator")


def _validate_horizon(value: Any, label: str = "horizon_s") -> float:
    number = _finite(value, label)
    if number <= 0:
        raise ValueError(f"{label} must be positive")
    return number


def _validate_bootstrap_args(iterations: int, seed: int) -> None:
    if (isinstance(iterations, bool) or not isinstance(iterations, int)
            or iterations <= 0):
        raise ValueError("bootstrap_iterations must be a positive integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    """Nearest-rank (ceiling) quantile on an ascending-sorted sample.

    Same rule as ``goal_metric_aggregation._percentile`` so percentile CIs
    of both modules are quantile-compatible.
    """
    if not sorted_values:
        raise ValueError("percentile of an empty sample")
    rank = max(1, math.ceil(q * len(sorted_values)))
    return sorted_values[min(rank, len(sorted_values)) - 1]


# ---------------------------------------------------------------------------
# Pair parsing (aggregator-compatible)
# ---------------------------------------------------------------------------

def _parse_pair(value: Any, label: str) -> tuple[float, int] | None:
    """Parse one right-censored pair.

    Accepts a ``(time, event_indicator)`` sequence or a mapping using the
    aggregator's key vocabulary.  A mapping that carries only one side of
    the pair is *incomplete*: ``None`` is returned so the caller can exclude
    and count it, never fabricate it (the aggregator's
    ``incomplete_right_censored_pair`` discipline).
    """
    if isinstance(value, Mapping):
        time_value = next((value[key] for key in _TIME_KEYS if key in value),
                          None)
        event_value = next(
            (value[key] for key in _EVENT_KEYS if key in value), None)
        if time_value is None or event_value is None:
            return None
        return (_nonnegative(time_value, f"{label} time"),
                _event_indicator(event_value, f"{label} event indicator"))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if len(value) != 2:
            raise ValueError(f"{label} must be a (time, event) pair")
        return (_nonnegative(value[0], f"{label} time"),
                _event_indicator(value[1], f"{label} event indicator"))
    raise ValueError(f"{label} must be a pair sequence or a mapping")


def _parse_pair_list(pairs: Any, *, horizon: float,
                     label: str) -> tuple[list[tuple[float, int]], int, int]:
    """Parse one episode's pair collection.

    Returns ``(parsed, incomplete_count, beyond_horizon_count)``.  A pair
    observed beyond the analysis horizon cannot carry information inside
    ``[0, H]``: it is treated as right-censored exactly at ``H`` (an event
    after ``H`` means the subject was event-free through ``H``) and the
    substitution is disclosed through the counter, never done silently.
    """
    if isinstance(pairs, Mapping) or not isinstance(pairs, Sequence):
        raise ValueError(f"{label} must be a sequence of pairs")
    parsed: list[tuple[float, int]] = []
    incomplete = 0
    beyond = 0
    for index, value in enumerate(pairs):
        pair = _parse_pair(value, f"{label}[{index}]")
        if pair is None:
            incomplete += 1
            continue
        time_value, event_value = pair
        if time_value > horizon:
            beyond += 1
            parsed.append((horizon, 0))
        else:
            parsed.append((time_value, event_value))
    return parsed, incomplete, beyond


# ---------------------------------------------------------------------------
# Kaplan-Meier estimator
# ---------------------------------------------------------------------------

def _km_arrays(parsed: Sequence[tuple[float, int]], horizon: float,
               ) -> tuple[list[float], list[float], list[int], list[int],
                          list[int]]:
    """KM arrays over already-validated (time, event) pairs.

    ``times`` starts at 0, contains every distinct observation time, and
    ends at ``horizon``.  ``S_t[i]`` is the right-continuous survival
    :math:`S(t)=P(T>t)` evaluated at ``times[i]`` (events at exactly
    ``times[i]`` are processed before the entry is recorded), so
    :math:`S(t)=S_t[i]` on ``[times[i], times[i+1])``.  ``n_at_risk[i]``
    counts pairs with time ``>= times[i]`` — censorings tied at an event
    time stay in that event time's risk set.
    """
    times = sorted({0.0} | {float(time_value) for time_value, _ in parsed})
    if times[-1] < horizon:
        times.append(float(horizon))
    s_values: list[float] = []
    n_at_risk: list[int] = []
    n_events: list[int] = []
    n_censored: list[int] = []
    survival = 1.0
    for time_value in times:
        at_risk = sum(1 for obs_time, _ in parsed if obs_time >= time_value)
        events = sum(1 for obs_time, obs_event in parsed
                     if obs_time == time_value and obs_event == 1)
        censored = sum(1 for obs_time, obs_event in parsed
                       if obs_time == time_value and obs_event == 0)
        if events:
            survival *= 1.0 - events / at_risk
        s_values.append(survival)
        n_at_risk.append(at_risk)
        n_events.append(events)
        n_censored.append(censored)
    return times, s_values, n_at_risk, n_events, n_censored


def kaplan_meier_curve(pairs, *, horizon_s: float) -> dict[str, Any]:
    """Kaplan-Meier survival curve of right-censored pairs (protocol 3.5).

    ``pairs`` is a nonempty collection of ``(time, event_indicator)`` right
    censored pairs — the per-episode ``(tilde_tau, Delta)`` first-stall
    pairs of protocol 3.3, the recovery-duration
    ``(tilde_L, Delta_rec)`` pairs, or the completion-time
    ``(tilde T, Delta)`` pairs of protocol 3.5.  Mapping forms using the
    aggregator's key vocabulary are accepted too.

    The returned curve is the monotone non-increasing step function
    :math:`S(t)=P(T>t)` on ``[0, H]``: ``S_t`` drops only at event times,
    a censoring time only shrinks the risk set, and beyond the last
    observation the curve is carried flat to ``H`` (no extrapolation, no
    forced drop to 0).  ``times[0]`` is always ``0.0`` and ``times[-1]``
    is ``horizon_s``.
    """
    horizon = _validate_horizon(horizon_s)
    if isinstance(pairs, Mapping) or not isinstance(pairs, Sequence):
        raise ValueError("pairs must be a sequence of (time, event) pairs")
    parsed, incomplete, beyond = _parse_pair_list(
        pairs, horizon=horizon, label="pairs")
    if not parsed:
        raise ValueError("pairs must contain at least one complete pair")
    times, s_values, n_at_risk, n_events, n_censored = _km_arrays(
        parsed, horizon)
    return {
        "estimator": "kaplan_meier",
        "survival_definition": "S(t) = P(T > t), right-continuous step "
                               "function; drops only at event times",
        "horizon_s": horizon,
        "times": times,
        "S_t": s_values,
        "n_at_risk": n_at_risk,
        "n_events": n_events,
        "n_censored": n_censored,
        "n_pairs": len(parsed),
        "n_events_total": sum(n_events),
        "n_censored_total": sum(n_censored),
        "incomplete_pair_count": incomplete,
        "beyond_horizon_pair_count": beyond,
        "beyond_horizon_rule": ("a pair observed beyond horizon_s carries no "
                                "information inside [0, H]: it is treated as "
                                "right-censored at horizon_s and counted "
                                "here, never silently dropped"),
        "tie_rule": ("events at time t precede censoring removal at t; "
                     "subjects censored at t stay in the risk set at t"),
        "no_extrapolation_rule": _NO_EXTRAPOLATION_RULE,
        **_RELEASE,
    }


def _curve_arrays(curve: Any) -> tuple[Sequence[float], Sequence[float], float]:
    """Validate a curve mapping and return ``(times, S_t, horizon)``."""
    if not isinstance(curve, Mapping):
        raise ValueError("curve must be a survival-curve mapping produced by "
                         "kaplan_meier_curve")
    times = curve.get("times")
    s_values = curve.get("S_t")
    horizon = curve.get("horizon_s")
    if (not isinstance(times, Sequence)
            or isinstance(times, (str, bytes))
            or not isinstance(s_values, Sequence)
            or isinstance(s_values, (str, bytes))):
        raise ValueError("curve must carry times and S_t arrays")
    if len(times) != len(s_values) or not times:
        raise ValueError("curve times and S_t must be parallel nonempty arrays")
    horizon = _validate_horizon(horizon, "curve horizon_s")
    for index, (time_value, survival_value) in enumerate(zip(times, s_values)):
        time_value = _nonnegative(time_value, f"curve times[{index}]")
        survival_value = _finite(survival_value, f"curve S_t[{index}]")
        if not 0.0 <= survival_value <= 1.0:
            raise ValueError(f"curve S_t[{index}] must lie in [0, 1]")
        if index and time_value < times[index - 1]:
            raise ValueError("curve times must be nondecreasing")
        if index and survival_value > s_values[index - 1]:
            raise ValueError("curve S_t must be non-increasing (a censoring "
                             "time must never raise S)")
    if times[0] != 0.0:
        raise ValueError("curve times must start at 0")
    if times[-1] < horizon:
        raise ValueError("curve must be defined up to its horizon")
    return times, s_values, horizon


def _s_at_arrays(times: Sequence[float], s_values: Sequence[float],
                 t: float) -> float:
    """Right-continuous survival :math:`S(t)` from parallel arrays."""
    index = bisect.bisect_right(times, t) - 1
    if index < 0:
        index = 0
    return s_values[index]


def _rmst_arrays(times: Sequence[float], s_values: Sequence[float],
                 h: float) -> float:
    """:math:`\\int_0^h S(t)\\,dt` as the exact area of the step function."""
    area = 0.0
    for index in range(len(times) - 1):
        if times[index] >= h:
            break
        area += s_values[index] * (min(times[index + 1], h) - times[index])
    return area


# ---------------------------------------------------------------------------
# RMST, differences and window probabilities
# ---------------------------------------------------------------------------

def rmst(curve, *, horizon_s: float) -> float:
    """Restricted mean survival time :math:`\\int_0^H S(t)\\,dt` (3.5).

    The integral is the exact area of the Kaplan-Meier step function on
    ``[0, horizon_s]``.  ``horizon_s`` may not exceed the curve's own
    horizon — right censoring is never extrapolated, so an RMST behind the
    curve's window is a ``ValueError`` instead of an assumed tail.
    """
    times, s_values, curve_horizon = _curve_arrays(curve)
    horizon = _validate_horizon(horizon_s)
    if horizon > curve_horizon:
        raise ValueError("rmst horizon_s exceeds the curve horizon; right "
                         "censoring is never extrapolated beyond the "
                         "preregistered window")
    return _rmst_arrays(times, s_values, horizon)


def rmst_difference(curve_a, curve_b, *, horizon_s: float) -> dict[str, Any]:
    """RMST difference in the protocol learning-gain direction (3.5).

    Returns :math:`\\mathrm{RMST}^{C}(H)-\\mathrm{RMST}^{R}(H)` where
    ``curve_a`` is the no-experience baseline condition :math:`C` and
    ``curve_b`` the learning condition :math:`R`.  A positive difference
    means the learning condition completes earlier within the fixed
    horizon (:math:`LG_n^{\\mathrm{RMST}}>0`).
    """
    rmst_a = rmst(curve_a, horizon_s=horizon_s)
    rmst_b = rmst(curve_b, horizon_s=horizon_s)
    return {
        "definition": "LG_RMST = RMST^C(H) - RMST^R(H) (protocol 3.5)",
        "condition_a": "no-experience baseline condition C",
        "condition_b": "learning condition R",
        "horizon_s": _validate_horizon(horizon_s),
        "rmst_condition_a": rmst_a,
        "rmst_condition_b": rmst_b,
        "difference": rmst_a - rmst_b,
        "interpretation": ("difference > 0 means the learning condition "
                           "completes earlier within the fixed horizon"),
        "no_extrapolation_rule": _NO_EXTRAPOLATION_RULE,
        **_RELEASE,
    }


def recovery_probability(curve, *, ell_star: float) -> dict[str, Any]:
    """:math:`P(T\\le\\ell^{\\star})=1-S(\\ell^{\\star})` (protocol 3.3).

    The preregistered within-``\\ell^{\\star}`` probability of the event
    (stall recovery, response, ...).  ``\\ell^{\\star}`` beyond the curve's
    horizon is not estimable without extrapolating the censored tail, so it
    is an explicit N/A label rather than a fabricated number.
    """
    times, s_values, horizon = _curve_arrays(curve)
    ell = _nonnegative(ell_star, "ell_star")
    if ell > horizon:
        return {
            "definition": "P(T <= ell_star) = 1 - S(ell_star) (protocol 3.3)",
            "ell_star": ell,
            "horizon_s": horizon,
            "status": NA_ELL_STAR_BEYOND_HORIZON,
            "S_at_ell_star": None,
            "probability": None,
            "note": ("ell_star beyond the curve horizon needs the censored "
                     "tail extrapolated; the protocol forbids that, so this "
                     "is an explicit N/A"),
            **_RELEASE,
        }
    survival = _s_at_arrays(times, s_values, ell)
    return {
        "definition": "P(T <= ell_star) = 1 - S(ell_star) (protocol 3.3)",
        "ell_star": ell,
        "horizon_s": horizon,
        "status": "computed",
        "S_at_ell_star": survival,
        "probability": 1.0 - survival,
        "no_extrapolation_rule": _NO_EXTRAPOLATION_RULE,
        **_RELEASE,
    }


def restricted_mean_gap(curve, *, lo: float, hi: float) -> dict[str, Any]:
    """Two-point survival-curve gap :math:`S(\\mathrm{lo})-S(\\mathrm{hi})`.

    The gap is the event probability mass inside the preregistered window
    :math:`P(\\mathrm{lo}<T\\le\\mathrm{hi})`.  With ``lo=0`` it is exactly
    the within-``\\ell^{\\star}`` recovery probability of protocol 3.3
    (:math:`S(0)-S(\\ell^{\\star})=1-S(\\ell^{\\star})`).  ``hi`` beyond the
    curve horizon is an explicit N/A; a malformed window
    (``lo > hi`` or negative bounds) raises.
    """
    times, s_values, horizon = _curve_arrays(curve)
    lo_value = _nonnegative(lo, "lo")
    hi_value = _nonnegative(hi, "hi")
    if lo_value > hi_value:
        raise ValueError("lo must not exceed hi")
    if hi_value > horizon:
        return {
            "definition": "S(lo) - S(hi) = P(lo < T <= hi)",
            "lo": lo_value,
            "hi": hi_value,
            "horizon_s": horizon,
            "status": NA_WINDOW_BEYOND_HORIZON,
            "S_lo": None,
            "S_hi": None,
            "gap": None,
            "note": ("the window upper bound lies beyond the curve horizon; "
                     "the censored tail must not be extrapolated"),
            **_RELEASE,
        }
    s_lo = _s_at_arrays(times, s_values, lo_value)
    s_hi = _s_at_arrays(times, s_values, hi_value)
    return {
        "definition": "S(lo) - S(hi) = P(lo < T <= hi)",
        "lo": lo_value,
        "hi": hi_value,
        "horizon_s": horizon,
        "status": "computed",
        "S_lo": s_lo,
        "S_hi": s_hi,
        "gap": s_lo - s_hi,
        "recovery_equivalence": ("with lo=0 the gap equals the preregistered "
                                 "within-ell_star recovery probability "
                                 "1 - S(ell_star) of protocol 3.3"),
        "no_extrapolation_rule": _NO_EXTRAPOLATION_RULE,
        **_RELEASE,
    }


# ---------------------------------------------------------------------------
# Aggregator interface: pooled KM + cluster bootstrap
# ---------------------------------------------------------------------------

def aggregate_survival(pair_sets, *, horizon_s: float, ell_star=None,
                       bootstrap_iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
                       seed: int = DEFAULT_BOOTSTRAP_SEED) -> dict[str, Any]:
    """Aggregate per-episode right-censored pair sets (protocol 2.6).

    ``pair_sets`` is the aggregator's per-episode ``[(tilde_tau, Delta)]``
    collections — one sequence of pairs per episode; both tuple pairs and
    the mapping shapes emitted by ``goal_metric_aggregation`` are accepted.
    Same-kind pair sets are merged directly (the protocol-allowed pool) and
    one Kaplan-Meier curve is estimated over the union; per-episode
    clusters stay the resampling unit.

    The result discloses the pooled curve, :math:`\\mathrm{RMST}(H)`, the
    within-:math:`\\ell^{\\star}` probability when ``ell_star`` is given,
    event/censoring counts, and the cluster-bootstrap CI (episodes first,
    then pairs inside every drawn episode; rebuild KM; take the 2.5/97.5
    percentiles of the RMST distribution — the same discipline as
    ``goal_metric_aggregation``).  Bootstrap replicates whose resample
    contains no event keep :math:`S\\equiv1` (RMST = H); they are honest
    samples of estimator variability, not skipped.

    Empty input, all-empty episode collections and all-censored pair sets
    are explicit N/A labels, never zero values.
    """
    horizon = _validate_horizon(horizon_s)
    if ell_star is not None:
        ell_star = _nonnegative(ell_star, "ell_star")
    _validate_bootstrap_args(bootstrap_iterations, seed)
    if isinstance(pair_sets, Mapping) or not isinstance(pair_sets, Sequence):
        raise ValueError("pair_sets must be a sequence of per-episode pair "
                         "collections")

    clusters: list[list[tuple[float, int]]] = []
    incomplete_pairs = 0
    beyond_pairs = 0
    empty_episodes = 0
    for index, episode in enumerate(pair_sets):
        parsed, incomplete, beyond = _parse_pair_list(
            episode, horizon=horizon, label=f"pair_sets[{index}]")
        incomplete_pairs += incomplete
        beyond_pairs += beyond
        if not parsed:
            empty_episodes += 1
        clusters.append(parsed)

    pooled_pairs = [pair for cluster in clusters for pair in cluster]
    n_events = sum(event for _, event in pooled_pairs)
    n_censored = len(pooled_pairs) - n_events
    result: dict[str, Any] = {
        "schema": SURVIVAL_SCHEMA,
        "version": SURVIVAL_VERSION,
        "protocol_sections": [PROTOCOL_SECTION, PROTOCOL_SECTION_STALL,
                              PROTOCOL_SECTION_LEARNING],
        "metric": "survival_aggregation",
        "unit": "episode_pair_set",
        "horizon_s": horizon,
        "ell_star": ell_star,
        "episode_count": len(clusters),
        "episodes_with_pairs": len(clusters) - empty_episodes,
        "empty_episode_count": empty_episodes,
        "incomplete_pair_count": incomplete_pairs,
        "incomplete_pair_rule": ("a mapping pair carrying only one side of "
                                 "(time, event) is excluded and counted, "
                                 "never fabricated"),
        "beyond_horizon_pair_count": beyond_pairs,
        "beyond_horizon_rule": ("pairs observed beyond horizon_s are treated "
                                "as right-censored at horizon_s and counted; "
                                "H is never a true event time"),
        "n_events": n_events,
        "n_censored": n_censored,
        "pooled_curve": None,
        "rmst_H": None,
        "recovery_prob_at_ell_star": None,
        "bootstrap": {"iterations": bootstrap_iterations, "seed": seed,
                      "method": "percentile_2.5_97.5", "ci_lower": None,
                      "ci_upper": None, "status": NA_EMPTY_SET},
        "no_extrapolation_rule": _NO_EXTRAPOLATION_RULE,
        **_RELEASE,
    }

    if not clusters:
        result.update({
            "status": NA_EMPTY_SET,
            "n_events": None,
            "n_censored": None,
        })
        return result
    if not pooled_pairs:
        result.update({
            "status": NA_ALL_MISSING,
            "n_events": 0,
            "n_censored": 0,
        })
        return result
    if n_events == 0:
        # All-censored: the censored time distribution is not estimable
        # (goal_metric_aggregation's NA_NO_EVENTS discipline).  RMST is not
        # reported as the trivial horizon value — that would claim zero
        # event risk from zero event information.
        result["bootstrap"].update({"status": NA_NO_EVENTS})
        result.update({
            "status": NA_NO_EVENTS,
            "rmst_note": ("with zero events the KM curve is not estimable; "
                          "RMST is an explicit N/A, not the horizon and not "
                          "0"),
        })
        return result

    curve = kaplan_meier_curve(pooled_pairs, horizon_s=horizon)
    rmst_value = rmst(curve, horizon_s=horizon_s)
    result.update({
        "status": "computed",
        "pooled_curve": curve,
        "rmst_H": rmst_value,
        "rmst_definition": "RMST(H) = integral_0^H S(t) dt over the pooled "
                           "Kaplan-Meier curve (protocol 3.5)",
        "pooling_rule": ("same-kind per-episode pair sets are merged directly "
                         "into one KM curve (protocol-allowed merge); the "
                         "bootstrap keeps episodes as clusters so pooled "
                         "events of one episode are never independent "
                         "samples"),
    })
    recovery_active = ell_star is not None and ell_star <= horizon
    if ell_star is not None:
        recovery = recovery_probability(curve, ell_star=ell_star)
        result["recovery_prob_at_ell_star"] = recovery

    rng = random.Random(seed)
    n_clusters = len(clusters)
    rmst_replicates: list[float] = []
    recovery_replicates: list[float] = []
    skipped_empty = 0
    for _ in range(bootstrap_iterations):
        sample: list[tuple[float, int]] = []
        for _ in range(n_clusters):
            cluster = clusters[rng.randrange(n_clusters)]
            if not cluster:
                continue
            size = len(cluster)
            for _ in range(size):
                sample.append(cluster[rng.randrange(size)])
        if not sample:
            skipped_empty += 1
            continue
        rep_times, rep_s_values, _, _, _ = _km_arrays(sample, horizon)
        rmst_replicates.append(_rmst_arrays(rep_times, rep_s_values, horizon))
        if recovery_active:
            recovery_replicates.append(
                1.0 - _s_at_arrays(rep_times, rep_s_values, ell_star))
    rmst_replicates.sort()
    recovery_replicates.sort()
    bootstrap: dict[str, Any] = {
        "iterations": bootstrap_iterations,
        "seed": seed,
        "method": "percentile_2.5_97.5",
        "resampling": ("instance-level (episode cluster) stratified: pair-set "
                       "clusters first, then pairs within every drawn "
                       "cluster; KM rebuilt per replicate — same discipline "
                       "as goal_metric_aggregation"),
        "zero_event_replicate_rule": ("a replicate whose resample contains no "
                                      "event keeps S=1 (RMST = H) and is an "
                                      "honest sample of estimator "
                                      "variability, not skipped"),
        "replicates_skipped_empty_resample": skipped_empty,
    }
    if rmst_replicates:
        bootstrap.update({
            "ci_lower": _percentile(rmst_replicates, 0.025),
            "ci_upper": _percentile(rmst_replicates, 0.975),
            "target": "rmst_H",
            "status": "defined",
        })
    else:
        bootstrap.update({"ci_lower": None, "ci_upper": None,
                          "status": NA_EMPTY_SET})
    if recovery_active and recovery_replicates:
        bootstrap["recovery_ci"] = {
            "ci_lower": _percentile(recovery_replicates, 0.025),
            "ci_upper": _percentile(recovery_replicates, 0.975),
            "target": "recovery_prob_at_ell_star",
            "status": "defined",
        }
    result["bootstrap"] = bootstrap
    return result


__all__ = [
    "DEFAULT_BOOTSTRAP_ITERATIONS", "DEFAULT_BOOTSTRAP_SEED",
    "NA_ALL_MISSING", "NA_ELL_STAR_BEYOND_HORIZON", "NA_EMPTY_SET",
    "NA_NO_EVENTS", "NA_WINDOW_BEYOND_HORIZON",
    "PROTOCOL_SECTION", "PROTOCOL_SECTION_LEARNING", "PROTOCOL_SECTION_STALL",
    "SURVIVAL_SCHEMA", "SURVIVAL_VERSION",
    "aggregate_survival", "kaplan_meier_curve", "recovery_probability",
    "restricted_mean_gap", "rmst", "rmst_difference",
]
