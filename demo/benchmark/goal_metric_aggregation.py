"""Cross-episode aggregation of protocol metrics (protocol section 2.6).

Protocol 2.6 fixes the statistical unit and the estimator hierarchy for
every capability number:

* the **base instance** is the statistical unit — repeated epochs / channel
  seeds / episodes of the same instance are never independent samples;
* binary and continuous quantities are first averaged **within** the
  instance (:math:`\\bar y_{ic}`) and then averaged with equal weight
  **across** instances (:math:`\\widehat M_c=\\frac{1}{N}\\sum_i\\bar y_{ic}`);
* raw binary rates disclose numerator, denominator and a Wilson 95%
  confidence interval; paired differences use instance-level paired
  bootstrap; the stratified bootstrap resamples base instances first and
  channel seeds / repeats within the instance second;
* response / recovery / detection times are reported as "probability of
  happening within :math:`H`" **and** a right-censored time distribution —
  never as a mean over successful samples only;
* empty sets, all-missing inputs and zero denominators return explicit
  N/A labels, never silent constants.

This module is the batch-3 caller-side reduction that
:mod:`demo.benchmark.goal_temporal_metrics` deliberately left open: it
aggregates per-episode metric results into cross-episode / cross-instance
protocol numbers.

**Evidence-source gate (viewer stage65 batch-3 top risk).**  Structurally
valid, hash-consistent development-state inputs must not be upgraded into
formally trusted evidence by an aggregator.  :func:`evidence_source_gate`
buckets every episode report by its provenance markers
(``contract_sha256`` / ``episode_id`` / ``evaluator_ref`` / termination
anchor source).  A report missing any required marker lands in the
``development_evidence`` bucket with an explicit count and never enters the
formal denominator; a fully bound report is merely ``formal_eligible``.
There is deliberately **no approved state** (mirroring
:mod:`demo.benchmark.goal_qualification_attestation`): formal promotion is
a separate release action, which is why every output here carries
``release=False`` and the release flags stay ``False``.

The module is pure Python and offline; it never starts Isaac, ROS or a
network transfer, and it computes no protocol score.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from typing import Any

from .protocol_runner import DIMENSIONS as _PROTOCOL_DIMENSIONS

AGGREGATION_SCHEMA = "eai.benchmark.goal_protocol_metrics_aggregation"
AGGREGATION_VERSION = 1
PROTOCOL_SECTION = "2.6"

#: Explicit N/A labels (protocol 2.6: predefined labels, never constants).
NA_EMPTY_SET = "na_empty_set"
NA_ALL_MISSING = "na_all_instances_missing"
NA_ZERO_DENOMINATOR = "na_zero_denominator"
NA_NO_EVENTS = "na_no_events_all_right_censored"
NA_ZERO_CONFIRMED_STALLS = "na_zero_confirmed_stalls"
NA_ZERO_OCCUPANCY = "na_no_resource_occupancy"
NA_NO_REPORTS = "na_no_reports"
NA_DIMENSION_SOURCE_NOT_WIRED = "na_dimension_source_not_wired"

#: Metric-name vocabularies.  Unknown names are rejected fail-closed.
BINARY_RATE_METRICS = frozenset({
    "team_state_infeasibility_detection_rate",
    "false_non_deployable_rate",
    "false_deployable_rate",
    "pre_dispatch_safe_stop_rate",
    "StallRate",
    "MutexAny",
    "P_T_det_le_H",
    "continuity_rate",
})
CONTINUOUS_METRICS = frozenset({
    "Q_H", "MCR", "AUP", "nPGI_exec", "PGI_exec",
    "assessment_latency",
    "G_h_mean_s", "mean_wait_s", "L_stall_obs_s",
    "PrecE2E", "PrecGivenOpportunity",
    "SyncE2E", "SyncGivenOpportunity",
    "MutexOverlap",
    "cs_cap", "cs_role", "cs_mile",
    "continuity_rate",
})
PAIRED_METRICS = frozenset({
    "Q_H", "MCR", "AUP", "nPGI_exec", "assessment_latency",
    "PrecE2E", "SyncE2E", "MutexOverlap",
    "cs_cap", "cs_role", "cs_mile",
})

#: Evidence-source gate vocabulary.  There is no ``approved`` state.
EVIDENCE_BUCKETS = ("development_evidence", "formal_eligible")
GATE_STATUSES = ("formal_eligible", "development_evidence")
SOURCE_BINDING_FIELDS = ("contract_sha256", "episode_id", "evaluator_ref",
                         "termination_anchor_source")
TRUSTED_ANCHOR_SOURCES = frozenset({"host_event", "native_receipt"})
UNVERIFIED_ANCHOR_SOURCE = "unverified"
FORMAL_PROMOTION_STATUS = "separate_release_action_not_issued_here"

#: The shared six-capability vocabulary (protocol DIMENSIONS).
DIMENSIONS = tuple(_PROTOCOL_DIMENSIONS)

DEFAULT_BOOTSTRAP_ITERATIONS = 2000
DEFAULT_BOOTSTRAP_SEED = 0

_RELEASE = {"release": False, "scoreable": False, "execution_validated": False,
            "business_outcomes_validated": False}

_WILSON_Z = 1.959963984540054

_HEXDIGITS = frozenset("0123456789abcdef")

_WITHIN_INSTANCE_RULE = ("within-instance mean over epochs/repeats first, "
                         "then equal-weight macro average across base "
                         "instances (protocol 2.6)")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


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


def _unit_value(value: Any, label: str) -> float:
    number = _finite(value, label)
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"{label} must lie in [0, 1]")
    return number


def _is_sha256(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and not (set(value) - _HEXDIGITS))


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    """Nearest-rank (ceiling) quantile on an ascending-sorted sample."""
    if not sorted_values:
        raise ValueError("percentile of an empty sample")
    rank = max(1, math.ceil(q * len(sorted_values)))
    return sorted_values[min(rank, len(sorted_values)) - 1]


def wilson_95_interval(numerator: float, denominator: float) -> dict[str, Any]:
    """Wilson score interval for a rate with an optional fractional numerator.

    Same formula as ``goal_protocol_deployment.wilson_95_interval`` and
    ``protocol_runner.wilson_ci``; integer inputs reproduce those exactly.
    The fractional-numerator form exists because the protocol macro average
    is the mean of instance-level rates, so the Wilson interval at the
    base-instance level uses the effective numerator
    :math:`N\\cdot\\widehat M_c`.
    """
    if denominator <= 0:
        return {"lower": None, "upper": None, "status": NA_ZERO_DENOMINATOR}
    p = numerator / denominator
    z2 = _WILSON_Z * _WILSON_Z
    centre = (p + z2 / (2 * denominator)) / (1 + z2 / denominator)
    half = (_WILSON_Z * math.sqrt(
        p * (1 - p) / denominator + z2 / (4 * denominator * denominator))
        / (1 + z2 / denominator))
    return {"lower": max(0.0, centre - half), "upper": min(1.0, centre + half),
            "status": "defined"}


# ---------------------------------------------------------------------------
# Per-instance row parsing
# ---------------------------------------------------------------------------

class _Instance:
    __slots__ = ("instance_id", "repeats", "mean")

    def __init__(self, instance_id: str, repeats: list[float] | None,
                 mean: float | None) -> None:
        self.instance_id = instance_id
        self.repeats = repeats
        self.mean = mean  # None when the instance carries no usable data


def _instance_list(rows: Any, *, metric_name: str, binary: bool,
                   ) -> list[_Instance]:
    """Parse and validate ``[{base_instance_id, epoch_rows|instance_mean}]``.

    Binary rows additionally accept integer ``numerator``/``denominator``
    pairs (identical to that many 0/1 epoch rows).  An instance whose
    repeat list is empty is *missing* (counted, never fabricated); a row
    with no value key at all is malformed and raises.  Duplicate
    ``base_instance_id`` values raise.
    """
    if isinstance(rows, Mapping) or not isinstance(rows, Sequence):
        raise ValueError("rows must be a sequence of instance mappings")
    instances: list[_Instance] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"instance row {index} must be a mapping")
        instance_id = _text(row.get("base_instance_id"),
                            f"instance row {index} base_instance_id")
        if instance_id in seen:
            raise ValueError(f"duplicate_base_instance_id:{instance_id}")
        seen.add(instance_id)
        label = f"{metric_name} instance {instance_id}"
        repeats: list[float] | None = None
        mean: float | None = None
        if "epoch_rows" in row:
            raw = row["epoch_rows"]
            if raw is None:
                raise ValueError(f"{label} epoch_rows must be a list, not null")
            if (not isinstance(raw, Sequence)
                    or isinstance(raw, (str, bytes, Mapping))):
                raise ValueError(f"{label} epoch_rows must be a list")
            values: list[float] = []
            for item in raw:
                if binary:
                    if isinstance(item, bool):
                        values.append(1.0 if item else 0.0)
                    elif isinstance(item, int) and item in (0, 1):
                        values.append(float(item))
                    elif isinstance(item, float) and item in (0.0, 1.0):
                        values.append(item)
                    else:
                        raise ValueError(
                            f"{label} epoch rows must be 0/1 binary values")
                else:
                    values.append(_finite(item, f"{label} epoch row"))
            repeats = values
            mean = (sum(values) / len(values)) if values else None
        elif "instance_mean" in row:
            raw_mean = row["instance_mean"]
            if raw_mean is None:
                mean = None
            else:
                mean = (_unit_value(raw_mean, f"{label} instance_mean")
                        if binary else _finite(raw_mean, f"{label} instance_mean"))
        elif binary and "numerator" in row and "denominator" in row:
            numerator = row["numerator"]
            denominator = row["denominator"]
            if (isinstance(numerator, bool) or isinstance(denominator, bool)
                    or not isinstance(numerator, int)
                    or not isinstance(denominator, int)):
                raise ValueError(
                    f"{label} numerator/denominator must be integers")
            if numerator < 0 or denominator < 0 or numerator > denominator:
                raise ValueError(
                    f"{label} needs 0 <= numerator <= denominator")
            repeats = ([1.0] * numerator + [0.0] * (denominator - numerator)
                       if denominator else [])
            mean = (numerator / denominator) if denominator else None
        else:
            raise ValueError(
                f"{label} must carry epoch_rows, instance_mean"
                + (", or numerator/denominator" if binary else ""))
        instances.append(_Instance(instance_id, repeats, mean))
    return instances


def _validate_bootstrap_args(iterations: int, seed: int) -> None:
    if (isinstance(iterations, bool) or not isinstance(iterations, int)
            or iterations <= 0):
        raise ValueError("bootstrap_iterations must be a positive integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")


def _bootstrap_block(instances: Sequence[_Instance], *, iterations: int,
                     seed: int) -> dict[str, Any]:
    """Stratified bootstrap CI of the macro mean (instances, then repeats).

    Protocol 2.6/communication section: resample base instances first, then
    resample the within-instance repeats (channel seeds / epochs) of every
    drawn instance.  Instances reduced to a single ``instance_mean``
    contribute that constant; the procedure is deterministic under a fixed
    seed.
    """
    usable = [inst for inst in instances if inst.mean is not None]
    n = len(usable)
    block = {"iterations": iterations, "seed": seed,
             "method": "percentile_2.5_97.5",
             "resampling": ("stratified: base instances first, then "
                            "within-instance repeats (channel seeds)")}
    if n == 0:
        block.update({"ci_lower": None, "ci_upper": None,
                      "status": (NA_EMPTY_SET if not instances
                                 else NA_ALL_MISSING),
                      "replicates_skipped_zero_denominator": 0})
        return block
    rng = random.Random(seed)
    replicate_means: list[float] = []
    for _ in range(iterations):
        total = 0.0
        for _ in range(n):
            inst = usable[rng.randrange(n)]
            if inst.repeats:
                total += inst.repeats[rng.randrange(len(inst.repeats))]
            else:
                total += inst.mean
        replicate_means.append(total / n)
    replicate_means.sort()
    block.update({"ci_lower": _percentile(replicate_means, 0.025),
                  "ci_upper": _percentile(replicate_means, 0.975),
                  "status": "defined",
                  "replicates_skipped_zero_denominator": 0})
    return block


def _missing_block(instances: Sequence[_Instance]) -> dict[str, Any]:
    missing = [inst.instance_id for inst in instances if inst.mean is None]
    return {"missing_instance_count": len(missing),
            "missing_instance_ids": missing,
            "missing_rule": ("an instance with an empty repeat list is "
                             "missing: counted and disclosed, never replaced "
                             "by a constant")}


# ---------------------------------------------------------------------------
# Binary-rate aggregation (macro average + Wilson)
# ---------------------------------------------------------------------------

def aggregate_binary_rate(rows, *, metric_name: str) -> dict[str, Any]:
    """Aggregate a binary rate across base instances (protocol 2.6).

    Input rows are ``[{base_instance_id, epoch_rows: [0|1, ...]}, ...]``
    (or the already-averaged ``instance_mean`` / integer
    ``numerator``/``denominator`` forms).  The primary estimate is the
    equal-weight macro average of within-instance means — epoch pooling is
    disclosed as raw material only.  The Wilson 95% interval is evaluated
    at the base-instance level with the fractional effective numerator
    :math:`N\\cdot\\widehat M_c`.  Empty input and all-missing input are
    explicit N/A labels.
    """
    if metric_name not in BINARY_RATE_METRICS:
        raise ValueError(f"unknown binary-rate metric name: {metric_name}")
    instances = _instance_list(rows, metric_name=metric_name, binary=True)
    usable = [inst for inst in instances if inst.mean is not None]
    result: dict[str, Any] = {
        "schema": AGGREGATION_SCHEMA,
        "version": AGGREGATION_VERSION,
        "protocol_section": PROTOCOL_SECTION,
        "metric_name": metric_name,
        "aggregation": "binary_rate_macro",
        "unit": "base_instance",
        "within_instance_rule": _WITHIN_INSTANCE_RULE,
        **_missing_block(instances),
        **_RELEASE,
    }
    if not instances or not usable:
        status = NA_EMPTY_SET if not instances else NA_ALL_MISSING
        numerator_epochs = 0 if instances else None
        denominator_epochs = 0 if instances else None
        result.update({"status": status, "instance_count": len(instances),
                       "instances_used": len(usable), "macro_rate": None,
                       "wilson_95_ci": wilson_95_interval(0, 0),
                       "numerator_epochs": numerator_epochs,
                       "denominator_epochs": denominator_epochs,
                       "pooled_epoch_rate": None})
        return result
    macro = sum(inst.mean for inst in usable) / len(usable)
    numerator_epochs = sum(v for inst in usable for v in (inst.repeats or []))
    denominator_epochs = sum(len(inst.repeats or []) for inst in usable)
    result.update({
        "status": "computed",
        "instance_count": len(instances),
        "instances_used": len(usable),
        "macro_rate": macro,
        "wilson_95_ci": wilson_95_interval(macro * len(usable), len(usable)),
        "wilson_scope": ("base-instance level; the effective numerator "
                         "N*macro is fractional when instance means are "
                         "fractional"),
        "numerator_epochs": int(numerator_epochs),
        "denominator_epochs": int(denominator_epochs),
        "pooled_epoch_rate": (numerator_epochs / denominator_epochs
                              if denominator_epochs else None),
        "pooled_epoch_note": ("pooled epochs are raw-count disclosure only; "
                              "the primary estimate is the base-instance "
                              "macro average and epochs of one instance are "
                              "not independent samples"),
    })
    return result


# ---------------------------------------------------------------------------
# Continuous aggregation (macro mean + stratified bootstrap CI)
# ---------------------------------------------------------------------------

def aggregate_continuous(rows, *, metric_name: str,
                         bootstrap_iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
                         seed: int = DEFAULT_BOOTSTRAP_SEED) -> dict[str, Any]:
    """Aggregate a continuous quantity (Q_H / AUP / latency / ...) macro.

    Same estimator hierarchy as the binary rate: within-instance mean over
    repeats first, equal-weight mean across instances second.  Uncertainty
    is the instance-level stratified bootstrap CI (default 2000 resamples,
    deterministic under the fixed default seed).
    """
    if metric_name not in CONTINUOUS_METRICS:
        raise ValueError(f"unknown continuous metric name: {metric_name}")
    _validate_bootstrap_args(bootstrap_iterations, seed)
    instances = _instance_list(rows, metric_name=metric_name, binary=False)
    usable = [inst for inst in instances if inst.mean is not None]
    result: dict[str, Any] = {
        "schema": AGGREGATION_SCHEMA,
        "version": AGGREGATION_VERSION,
        "protocol_section": PROTOCOL_SECTION,
        "metric_name": metric_name,
        "aggregation": "continuous_macro_bootstrap",
        "unit": "base_instance",
        "within_instance_rule": _WITHIN_INSTANCE_RULE,
        **_missing_block(instances),
        "bootstrap": _bootstrap_block(instances, iterations=bootstrap_iterations,
                                      seed=seed),
        **_RELEASE,
    }
    if not instances or not usable:
        result.update({"status": NA_EMPTY_SET if not instances else NA_ALL_MISSING,
                       "instance_count": len(instances),
                       "instances_used": len(usable), "macro_mean": None,
                       "instance_mean_min": None, "instance_mean_max": None})
        return result
    means = [inst.mean for inst in usable]
    result.update({
        "status": "computed",
        "instance_count": len(instances),
        "instances_used": len(usable),
        "macro_mean": sum(means) / len(means),
        "instance_mean_min": min(means),
        "instance_mean_max": max(means),
        "instances": [{"base_instance_id": inst.instance_id,
                       "instance_mean": inst.mean,
                       "n_repeats": len(inst.repeats or [])}
                      for inst in usable],
    })
    return result


# ---------------------------------------------------------------------------
# Paired difference (instance-level paired bootstrap)
# ---------------------------------------------------------------------------

def _arm_values(row: Mapping[str, Any], key: str, label: str) -> list[float]:
    value = row.get(key)
    if (isinstance(value, Sequence)
            and not isinstance(value, (str, bytes, Mapping))):
        values = [_finite(item, f"{label} {key} repeat") for item in value]
        if not values:
            raise ValueError(f"{label} {key} arm is empty")
        return values
    return [_finite(value, f"{label} {key} arm")]


def aggregate_paired_difference(pairs, *, metric_name: str,
                                bootstrap_iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
                                seed: int = DEFAULT_BOOTSTRAP_SEED) -> dict[str, Any]:
    """Aggregate paired differences :math:`d_i` of two conditions.

    ``pairs`` rows carry ``base_instance_id`` plus ``treated`` and
    ``reference`` arms, each either the within-instance mean or the list of
    per-repeat values.  The point estimate is the mean of
    :math:`d_i=\\bar y_{i,c}-\\bar y_{i,c_{ref}}` over instances; the CI is
    the instance-level paired bootstrap (instances first, then repeats
    within each arm).  Only absolute differences are reported here —
    relative ratios need the preregistered reference-validity gate and are
    never manufactured by this module.
    """
    if metric_name not in PAIRED_METRICS:
        raise ValueError(f"unknown paired metric name: {metric_name}")
    _validate_bootstrap_args(bootstrap_iterations, seed)
    if isinstance(pairs, Mapping) or not isinstance(pairs, Sequence):
        raise ValueError("pairs must be a sequence of pair mappings")
    parsed: list[tuple[str, list[float], list[float]]] = []
    seen: set[str] = set()
    for index, row in enumerate(pairs):
        if not isinstance(row, Mapping):
            raise ValueError(f"pair row {index} must be a mapping")
        instance_id = _text(row.get("base_instance_id"),
                            f"pair row {index} base_instance_id")
        if instance_id in seen:
            raise ValueError(f"duplicate_base_instance_id:{instance_id}")
        seen.add(instance_id)
        label = f"pair {instance_id}"
        treated = _arm_values(row, "treated", label)
        reference = _arm_values(row, "reference", label)
        parsed.append((instance_id, treated, reference))

    result: dict[str, Any] = {
        "schema": AGGREGATION_SCHEMA,
        "version": AGGREGATION_VERSION,
        "protocol_section": PROTOCOL_SECTION,
        "metric_name": metric_name,
        "aggregation": "paired_difference_instance_bootstrap",
        "unit": "base_instance",
        **_RELEASE,
    }
    if not parsed:
        result.update({"status": NA_EMPTY_SET, "instance_count": 0,
                       "mean_difference": None, "paired_differences": [],
                       "bootstrap": {"iterations": bootstrap_iterations,
                                     "seed": seed, "ci_lower": None,
                                     "ci_upper": None,
                                     "status": NA_EMPTY_SET}})
        return result
    differences = [(iid, sum(t) / len(t) - sum(r) / len(r))
                   for iid, t, r in parsed]
    rng = random.Random(seed)
    replicates: list[float] = []
    n = len(parsed)
    for _ in range(bootstrap_iterations):
        total = 0.0
        for _ in range(n):
            _, treated, reference = parsed[rng.randrange(n)]
            t_star = treated[rng.randrange(len(treated))]
            r_star = reference[rng.randrange(len(reference))]
            total += t_star - r_star
        replicates.append(total / n)
    replicates.sort()
    result.update({
        "status": "computed",
        "instance_count": n,
        "mean_difference": sum(d for _, d in differences) / n,
        "paired_differences": [{"base_instance_id": iid, "d": d}
                               for iid, d in differences],
        "bootstrap": {"iterations": bootstrap_iterations, "seed": seed,
                      "method": "percentile_2.5_97.5",
                      "resampling": ("paired stratified: base instances "
                                     "first, then repeats within each arm"),
                      "ci_lower": _percentile(replicates, 0.025),
                      "ci_upper": _percentile(replicates, 0.975),
                      "status": "defined"},
        "reference_validity_rule": (
            "relative ratios only under the preregistered reference-validity "
            "gate (protocol 2.6); this entry reports absolute paired "
            "differences and uncertainty only"),
    })
    return result


# ---------------------------------------------------------------------------
# Stall aggregation (episode unit)
# ---------------------------------------------------------------------------

def _episode_id_of(row: Mapping[str, Any], index: int) -> str:
    for key in ("episode_id", "base_instance_id"):
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    return f"episode_{index}"


def _parse_delta(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)) and float(value) in (0.0, 1.0):
        return int(float(value))
    return None


def _parse_stall_episode(row: Mapping[str, Any], index: int) -> dict[str, Any]:
    episode_id = _episode_id_of(row, index)
    label = f"stall episode {episode_id}"
    indicator = row.get("S_i_stall_indicator", row.get("stall_indicator"))
    if isinstance(indicator, bool):
        indicator_value = 1.0 if indicator else 0.0
    elif isinstance(indicator, (int, float)) and float(indicator) in (0.0, 1.0):
        indicator_value = float(indicator)
    else:
        raise ValueError(f"{label} must carry a 0/1 S_i_stall_indicator")
    pair = row.get("first_stall_right_censored_pair")
    pair_source = pair if isinstance(pair, Mapping) else row
    tilde_tau = pair_source.get("tilde_tau_det_s")
    delta = _parse_delta(pair_source.get("delta_i", pair_source.get("Delta_i")))
    pair_complete = (isinstance(pair_source, Mapping)
                     and tilde_tau is not None and delta is not None)
    if pair_complete:
        tilde_tau = _nonnegative(tilde_tau, f"{label} tilde_tau_det_s")
    stalls = row.get("stalls")
    if not isinstance(stalls, list):
        stalls = []
    recovery_rows: list[dict[str, Any]] = []
    incomplete_recovery_rows = 0
    for stall in stalls:
        if not isinstance(stall, Mapping):
            raise ValueError(f"{label} stall rows must be mappings")
        l_obs = stall.get("L_obs_s")
        observed = stall.get("recovery_observed")
        if l_obs is None or not isinstance(observed, bool):
            incomplete_recovery_rows += 1
            continue
        recovery_rows.append({"L_obs_s": _nonnegative(l_obs, f"{label} L_obs_s"),
                              "recovery_observed": observed})
    declared_m = row.get("M_stall")
    if declared_m is not None:
        if isinstance(declared_m, bool) or not isinstance(declared_m, int):
            raise ValueError(f"{label} M_stall must be an integer")
        if declared_m != len(stalls):
            raise ValueError(
                f"{label} M_stall disagrees with the stall row count")
    return {"episode_id": episode_id, "s_indicator": indicator_value,
            "pair": ({"tilde_tau_det_s": tilde_tau, "delta_i": delta}
                     if pair_complete else None),
            "recovery_rows": recovery_rows,
            "incomplete_recovery_rows": incomplete_recovery_rows,
            "m_stall": len(stalls),
            "l_stall_obs_s": (None if row.get("L_stall_obs_s") is None else
                              _nonnegative(row["L_stall_obs_s"],
                                           f"{label} L_stall_obs_s"))}


def aggregate_stall_metrics(episode_stalls,
                            *, bootstrap_iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
                            seed: int = DEFAULT_BOOTSTRAP_SEED) -> dict[str, Any]:
    """Aggregate per-episode stall results across episodes (protocol 3.3).

    ``episode_stalls`` rows are per-episode results in the shape emitted by
    ``goal_temporal_metrics.coordination_stall_metrics`` (episode as the
    statistical unit):

    * ``StallRate`` — the N-episode mean of the indicator :math:`S_i` with
      numerator/denominator disclosure and a Wilson interval at the episode
      level; episodes without a stall stay in the denominator;
    * ``P_T_det_le_H`` — the same probability view of the first stall;
    * the first-stall right-censored pairs
      :math:`(\\widetilde\\tau_i^{det},\\Delta_i^{det})` are collected as
      the censored time-distribution set; incomplete pairs are excluded and
      counted explicitly, never fabricated.  When every pair is censored
      the time distribution is N/A while ``P_T_det_le_H`` stays defined;
    * ``StallRecoveryObserved`` — :math:`\\sum\\Delta/\\sum M_i` with an
      episode-clustered bootstrap CI (episodes are the clusters; event
      pooling would overstate precision).  Zero confirmed stalls is an
      explicit N/A.
    """
    _validate_bootstrap_args(bootstrap_iterations, seed)
    if isinstance(episode_stalls, Mapping) or not isinstance(
            episode_stalls, Sequence):
        raise ValueError("episode_stalls must be a sequence of episode rows")
    episodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(episode_stalls):
        if not isinstance(row, Mapping):
            raise ValueError(f"stall episode row {index} must be a mapping")
        episode = _parse_stall_episode(row, index)
        if episode["episode_id"] in seen:
            raise ValueError(f"duplicate_episode_id:{episode['episode_id']}")
        seen.add(episode["episode_id"])
        episodes.append(episode)

    result: dict[str, Any] = {
        "schema": AGGREGATION_SCHEMA,
        "version": AGGREGATION_VERSION,
        "protocol_section": PROTOCOL_SECTION,
        "metric": "stall_aggregation",
        "unit": "episode",
        **_RELEASE,
    }
    if not episodes:
        result.update({
            "status": NA_EMPTY_SET,
            "episode_count": 0,
            "StallRate": {"rate": None, "numerator": None,
                          "denominator": None,
                          "wilson_95_ci": wilson_95_interval(0, 0)},
            "P_T_det_le_H": None,
            "first_stall_right_censored_pairs": [],
            "first_stall_event_count": None,
            "first_stall_censored_count": None,
            "first_stall_time_distribution": {"status": NA_EMPTY_SET},
            "incomplete_right_censored_pair_count": None,
            "incomplete_recovery_row_count": None,
            "StallRecoveryObserved": {"rate": None, "numerator": None,
                                      "denominator": None,
                                      "status": NA_EMPTY_SET},
        })
        return result

    n_episodes = len(episodes)
    events = sum(int(round(ep["s_indicator"])) for ep in episodes)
    pairs = [dict(ep["pair"], episode_id=ep["episode_id"])
             for ep in episodes if ep["pair"] is not None]
    incomplete_pairs = sum(1 for ep in episodes if ep["pair"] is None)
    incomplete_recovery = sum(ep["incomplete_recovery_rows"]
                              for ep in episodes)
    pair_events = sum(pair["delta_i"] for pair in pairs)
    pair_censored = len(pairs) - pair_events

    total_m = sum(ep["m_stall"] for ep in episodes)
    total_delta = sum(1 for ep in episodes for row in ep["recovery_rows"]
                      if row["recovery_observed"])
    recovery: dict[str, Any] = {
        "numerator": total_delta, "denominator": total_m,
        "ci_scope": "episode_cluster_bootstrap",
    }
    if total_m == 0:
        recovery.update({"rate": None, "status": NA_ZERO_CONFIRMED_STALLS,
                         "bootstrap_ci": None})
    else:
        rng = random.Random(seed)
        replicates: list[float] = []
        skipped = 0
        for _ in range(bootstrap_iterations):
            m_star = 0
            delta_star = 0
            for _ in range(n_episodes):
                ep = episodes[rng.randrange(n_episodes)]
                m_star += ep["m_stall"]
                delta_star += sum(1 for row in ep["recovery_rows"]
                                  if row["recovery_observed"])
            if m_star == 0:
                skipped += 1
                continue
            replicates.append(delta_star / m_star)
        replicates.sort()
        recovery.update({
            "rate": total_delta / total_m, "status": "computed",
            "bootstrap_ci": ({"ci_lower": _percentile(replicates, 0.025),
                              "ci_upper": _percentile(replicates, 0.975),
                              "iterations": bootstrap_iterations,
                              "seed": seed,
                              "replicates_skipped_zero_denominator": skipped}
                             if replicates else
                             {"status": NA_ZERO_CONFIRMED_STALLS}),
            "clustering_note": ("episodes are the resampling clusters; "
                                "event pooling across episodes would treat "
                                "stalls of one episode as independent "
                                "samples"),
        })

    l_stall_values = [ep["l_stall_obs_s"] for ep in episodes
                      if ep["l_stall_obs_s"] is not None]
    result.update({
        "status": "computed",
        "episode_count": n_episodes,
        "StallRate": {
            "rate": events / n_episodes, "numerator": events,
            "denominator": n_episodes,
            "wilson_95_ci": wilson_95_interval(events, n_episodes),
            "scope": ("N-episode average of the per-episode indicator; "
                      "episodes without a stall stay in the denominator"),
        },
        "P_T_det_le_H": events / n_episodes,
        "first_stall_right_censored_pairs": pairs,
        "first_stall_event_count": pair_events,
        "first_stall_censored_count": pair_censored,
        "first_stall_time_distribution": {
            "status": "computed" if pair_events else NA_NO_EVENTS,
            "note": ("censored (observed time, event indicator) pairs; "
                     "episodes without a stall stay in the set as censored "
                     "pairs and H is never treated as a true stall time; "
                     "with zero events the time distribution is not "
                     "estimable while P_T_det_le_H stays defined"),
        },
        "incomplete_right_censored_pair_count": incomplete_pairs,
        "incomplete_pair_rule": ("an episode whose (tilde_tau_det_s, delta_i) "
                                 "pair is absent or partial is excluded from "
                                 "the pair set and counted here, never "
                                 "fabricated"),
        "incomplete_recovery_row_count": incomplete_recovery,
        "StallRecoveryObserved": recovery,
        "L_stall_obs": ({
            "status": "computed", "unit": "seconds",
            "macro_mean": sum(l_stall_values) / len(l_stall_values),
            "episode_count": len(l_stall_values),
            "note": ("restricted observed stall time; a final stall that "
                     "persists to H is a restricted observation, never a "
                     "complete recovery duration"),
        } if l_stall_values else
            {"status": NA_ALL_MISSING, "macro_mean": None,
             "episode_count": 0}),
    })
    return result


# ---------------------------------------------------------------------------
# Mutex aggregation (episode rate + occupied-time weighting)
# ---------------------------------------------------------------------------

def aggregate_mutex(episode_mutex,
                    *, bootstrap_iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
                    seed: int = DEFAULT_BOOTSTRAP_SEED) -> dict[str, Any]:
    """Aggregate per-episode mutex results across episodes (protocol 3.3).

    ``MutexAny`` is the episode-level any-violation rate
    :math:`N_{viol}/N` with a Wilson interval — the episode is the unit,
    never the pooled violation event.  ``MutexOverlap`` is the
    occupied-time-weighted ratio
    :math:`\\sum_r\\int 1[n_r(t)>1]dt / \\sum_r\\int 1[n_r(t)\\ge1]dt`
    across episodes, disclosed with its time numerator/denominator and an
    episode-cluster bootstrap CI.  Episodes without any occupancy stay out
    of the overlap ratio (counted explicitly) and total-zero occupancy is
    the protocol N/A.
    """
    _validate_bootstrap_args(bootstrap_iterations, seed)
    if isinstance(episode_mutex, Mapping) or not isinstance(
            episode_mutex, Sequence):
        raise ValueError("episode_mutex must be a sequence of episode rows")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(episode_mutex):
        if not isinstance(row, Mapping):
            raise ValueError(f"mutex episode row {index} must be a mapping")
        episode_id = _episode_id_of(row, index)
        if episode_id in seen:
            raise ValueError(f"duplicate_episode_id:{episode_id}")
        seen.add(episode_id)
        label = f"mutex episode {episode_id}"
        any_flag = row.get("MutexAny_episode")
        if isinstance(any_flag, bool):
            any_value = 1 if any_flag else 0
        elif isinstance(any_flag, int) and any_flag in (0, 1):
            any_value = any_flag
        else:
            raise ValueError(f"{label} must carry MutexAny_episode")
        overlap = _nonnegative(row.get("overlap_time_s"),
                               f"{label} overlap_time_s")
        occupied = _nonnegative(row.get("occupied_time_s"),
                                f"{label} occupied_time_s")
        rows.append({"episode_id": episode_id, "any_value": any_value,
                     "overlap_time_s": overlap, "occupied_time_s": occupied})

    result: dict[str, Any] = {
        "schema": AGGREGATION_SCHEMA,
        "version": AGGREGATION_VERSION,
        "protocol_section": PROTOCOL_SECTION,
        "metric": "mutex_aggregation",
        "unit": "episode",
        **_RELEASE,
    }
    if not rows:
        result.update({
            "status": NA_EMPTY_SET,
            "episode_count": 0,
            "MutexAny": {"rate": None, "numerator": None,
                         "denominator": None,
                         "wilson_95_ci": wilson_95_interval(0, 0)},
            "MutexOverlap": {"rate": None, "numerator_time_s": None,
                             "denominator_time_s": None,
                             "status": NA_EMPTY_SET},
        })
        return result

    violations = sum(row["any_value"] for row in rows)
    total_overlap = sum(row["overlap_time_s"] for row in rows)
    total_occupied = sum(row["occupied_time_s"] for row in rows)
    episodes_without_occupancy = sum(1 for row in rows
                                     if row["occupied_time_s"] <= 0)

    overlap_block: dict[str, Any] = {
        "numerator_time_s": total_overlap,
        "denominator_time_s": total_occupied,
        "weighting": ("occupied-time weighted across episodes; the mean of "
                      "per-episode ratios is not the protocol estimator"),
        "episodes_without_occupancy_count": episodes_without_occupancy,
        "episodes_without_occupancy_rule": (
            "episodes with no resource occupancy stay out of the overlap "
            "ratio and are counted, never treated as zero-overlap "
            "denominators"),
    }
    if total_occupied <= 0:
        overlap_block.update({"rate": None, "status": NA_ZERO_OCCUPANCY,
                              "bootstrap_ci": None})
    else:
        rng = random.Random(seed)
        replicates: list[float] = []
        skipped = 0
        for _ in range(bootstrap_iterations):
            o_star = 0.0
            c_star = 0.0
            for _ in range(len(rows)):
                row = rows[rng.randrange(len(rows))]
                o_star += row["overlap_time_s"]
                c_star += row["occupied_time_s"]
            if c_star <= 0:
                skipped += 1
                continue
            replicates.append(o_star / c_star)
        replicates.sort()
        overlap_block.update({
            "rate": total_overlap / total_occupied, "status": "computed",
            "bootstrap_ci": ({"ci_lower": _percentile(replicates, 0.025),
                              "ci_upper": _percentile(replicates, 0.975),
                              "iterations": bootstrap_iterations,
                              "seed": seed,
                              "replicates_skipped_zero_occupancy": skipped}
                             if replicates else
                             {"status": NA_ZERO_OCCUPANCY}),
        })

    result.update({
        "status": "computed",
        "episode_count": len(rows),
        "MutexAny": {
            "rate": violations / len(rows), "numerator": violations,
            "denominator": len(rows),
            "wilson_95_ci": wilson_95_interval(violations, len(rows)),
            "scope": ("episode-level any-violation rate; pooled violation "
                      "events are not independent samples"),
        },
        "MutexOverlap": overlap_block,
    })
    return result


# ---------------------------------------------------------------------------
# Evidence-source gate (development vs formal-eligible; no approved state)
# ---------------------------------------------------------------------------

def _binding_value(report: Mapping[str, Any], field: str) -> Any:
    bindings = report.get("source_bindings")
    if isinstance(bindings, Mapping) and field in bindings:
        # Viewer stage66 P0-3: a binding that disagrees with the report's
        # top-level value (when the top level also carries the field) is a
        # source conflict — resolve to None so the report drops to
        # development_evidence instead of silently preferring one side.
        top_level = report.get(field)
        if (top_level is not None
                and bindings[field] != top_level):
            return None
        return bindings[field]
    return report.get(field)


def _check_binding(field: str, value: Any) -> str | None:
    """Return a missing/invalid reason, or None when the binding holds."""
    if field == "contract_sha256":
        if not _is_sha256(value):
            return "contract_sha256_missing_or_invalid"
        return None
    if field == "termination_anchor_source":
        if not isinstance(value, str) or not value.strip():
            return "termination_anchor_source_missing"
        if value == UNVERIFIED_ANCHOR_SOURCE:
            # A present but unverified anchor is an honest marker of
            # non-trust: right-censored evidence stays development-state.
            return "termination_anchor_source_unverified"
        # Viewer stage66 P0-4: only trusted host/native receipt sources are
        # formal-eligible anchor provenance; any other label stays
        # development-state with an explicit reason.
        if value not in TRUSTED_ANCHOR_SOURCES:
            return "termination_anchor_source_not_trusted"
        return None
    if not isinstance(value, str) or not value.strip():
        return f"{field}_missing"
    return None


MANDATORY_SOURCE_BINDINGS = frozenset(SOURCE_BINDING_FIELDS)


def evidence_source_gate(reports, *, required_bindings: Sequence[str]) -> dict[str, Any]:
    """Bucket episode reports into development vs formal-eligible evidence.

    Viewer stage66 P0-1: ``required_bindings`` must cover every mandatory
    source-binding field — a caller cannot shrink the gate.  Passing fewer
    fields raises instead of quietly widening formal eligibility.

    Every report is checked for the required provenance markers
    (``contract_sha256`` as a 64-hex digest, ``episode_id``,
    ``evaluator_ref`` and the termination-anchor ``source``; values are
    read from a ``source_bindings`` mapping or the report top level).  A
    report missing any marker — or whose termination anchor is marked
    ``unverified`` — lands in the ``development_evidence`` bucket with an
    explicit reason list and is excluded from any formal denominator.
    Fully bound reports are ``formal_eligible`` only: there is no
    ``approved`` state, and formal promotion stays a separate release
    action outside this module.
    """
    if (isinstance(required_bindings, str)
            or not isinstance(required_bindings, Sequence)
            or not required_bindings):
        raise ValueError("required_bindings must be a nonempty sequence")
    unknown = sorted(set(required_bindings) - set(SOURCE_BINDING_FIELDS))
    if unknown:
        raise ValueError(f"unknown source binding fields: {unknown}")
    if not MANDATORY_SOURCE_BINDINGS <= set(required_bindings):
        missing = sorted(MANDATORY_SOURCE_BINDINGS - set(required_bindings))
        raise ValueError(
            "required_bindings cannot shrink the mandatory source gate; "
            f"missing mandatory fields: {missing}")
    if isinstance(reports, Mapping) or not isinstance(reports, Sequence):
        raise ValueError("reports must be a sequence of report mappings")
    required = list(required_bindings)

    formal: list[dict[str, Any]] = []
    development: list[dict[str, Any]] = []
    for index, report in enumerate(reports):
        if not isinstance(report, Mapping):
            raise ValueError(f"report {index} must be a mapping")
        # Viewer stage66 P0-2: a derived/synthetic episode id is not source
        # evidence — the row records None and the binding check below puts
        # the report into development_evidence.
        episode_id = report.get("episode_id")
        if not (isinstance(episode_id, str) and episode_id):
            episode_id = _binding_value(report, "episode_id")
        row = {"report_index": index,
               "episode_id": episode_id if isinstance(episode_id, str)
               and episode_id else None}
        reasons = [reason for reason in (
            _check_binding(field, _binding_value(report, field))
            for field in required) if reason]
        if reasons:
            row["missing_bindings"] = sorted(set(reasons))
            development.append(row)
        else:
            formal.append(row)
    gate_status = ("no_reports" if not reports else
                   "formal_eligible" if not development else
                   "development_input_present")
    return {
        "schema": AGGREGATION_SCHEMA,
        "version": AGGREGATION_VERSION,
        "gate": "evidence_source_gate",
        "required_bindings": required,
        "report_count": len(reports),
        "formal_eligible_count": len(formal),
        "development_evidence_count": len(development),
        "formal_eligible": formal,
        "development_evidence": development,
        "buckets": list(EVIDENCE_BUCKETS),
        "statuses": list(GATE_STATUSES),
        "no_approved_state": True,
        "gate_status": gate_status,
        "formal_promotion": FORMAL_PROMOTION_STATUS,
        "development_exclusion_rule": (
            "a report missing any required binding never enters the formal "
            "denominator; its exclusion is counted and disclosed, and no "
            "structural validity or content-hash consistency upgrades it"),
        **_RELEASE,
    }


# ---------------------------------------------------------------------------
# Top-level six-dimension report
# ---------------------------------------------------------------------------

_DEPLOYMENT_BINARY_RATES = (
    "team_state_infeasibility_detection_rate",
    "false_non_deployable_rate",
    "false_deployable_rate",
    "pre_dispatch_safe_stop_rate",
)
_CS_COMPONENT_METRICS = (("cs_cap", "cap"), ("cs_role", "role"),
                         ("cs_mile", "mile"))
_PROGRESS_METRICS = ("Q_H", "MCR", "AUP", "nPGI_exec", "PGI_exec")
_PREC_METRICS = ("PrecE2E", "PrecGivenOpportunity")
_SYNC_METRICS = ("SyncE2E", "SyncGivenOpportunity")


def _episode_key(report: Mapping[str, Any], index: int) -> str:
    for key in ("episode_id", "base_instance_id"):
        value = report.get(key)
        if isinstance(value, str) and value:
            return value
    return f"episode_{index}"


def _instance_id(report: Mapping[str, Any], index: int) -> str:
    value = report.get("base_instance_id")
    if isinstance(value, str) and value:
        return value
    return _episode_key(report, index)


class _ReportSplitter:
    """Groups per-episode values for one metric into instance repeat rows."""

    def __init__(self, parsed_rows: list[dict[str, Any]],
                 formal_ids: set[str], development_ids: set[str]) -> None:
        self.parsed_rows = parsed_rows
        self.formal_ids = formal_ids
        self.development_ids = development_ids

    def split(self, values_by_episode: list[tuple[str, str, Any]],
              ) -> tuple[list[dict[str, Any]], int, int]:
        """(instance rows, formal providing count, development providing)."""
        grouped: dict[str, list[Any]] = {}
        formal_providing = 0
        development_providing = 0
        for episode_id, instance_id, value in values_by_episode:
            if value is None:
                continue
            if episode_id in self.formal_ids:
                grouped.setdefault(instance_id, []).append(value)
                formal_providing += 1
            elif episode_id in self.development_ids:
                development_providing += 1
        rows = [{"base_instance_id": instance_id, "epoch_rows": repeats}
                for instance_id, repeats in sorted(grouped.items())]
        return rows, formal_providing, development_providing

    def metric_values_of(self, key_path: tuple[str, ...]
                         ) -> list[tuple[str, str, Any]]:
        collected: list[tuple[str, str, Any]] = []
        for row in self.parsed_rows:
            node: Any = row["report"]
            for key in key_path:
                if not isinstance(node, Mapping):
                    node = None
                    break
                node = node.get(key)
            collected.append((row["episode_id"], row["base_instance_id"],
                              node))
        return collected

    def wrap(self, aggregation: Mapping[str, Any], *, formal_providing: int,
             development_providing: int, formal_episode_count: int,
             ) -> dict[str, Any]:
        return {
            "aggregation": aggregation,
            "coverage": {
                "episodes_providing_metric": formal_providing,
                "episodes_providing_metric_development": development_providing,
                "episodes_formal_eligible": formal_episode_count,
                "coverage_ratio": (formal_providing / formal_episode_count
                                   if formal_episode_count else None),
                "development_excluded_episodes": development_providing,
            },
        }

    def na_component(self, reason: str, *, formal_providing: int,
                     development_providing: int,
                     formal_episode_count: int) -> dict[str, Any]:
        return self.wrap(
            {"schema": AGGREGATION_SCHEMA, "version": AGGREGATION_VERSION,
             "status": reason,
             "note": ("no formal-eligible episode provides this metric; "
                      "development-bucket providers are counted, never "
                      "aggregated into the formal denominator"),
             **_RELEASE},
            formal_providing=0, development_providing=development_providing,
            formal_episode_count=formal_episode_count)


def _na_dimension(reason: str, note: str) -> dict[str, Any]:
    return {"status": reason, "note": note, "metrics": {},
            "coverage": {"episodes_providing_metric": 0,
                         "coverage_ratio": None,
                         "development_excluded_episodes": 0}}


def build_protocol_metrics_report(
    episode_reports,
    *,
    required_bindings: Sequence[str] = SOURCE_BINDING_FIELDS,
    bootstrap_iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """Aggregate per-episode protocol metric results into the report.

    ``episode_reports`` rows are per-episode mappings carrying optional
    ``base_instance_id`` / ``episode_id``, optional ``source_bindings``,
    and any of the per-episode sub-results ``deployment_rates``
    (:func:`goal_protocol_deployment.protocol_deployment_rates`),
    ``cs_vector`` (:func:`goal_cs_vector.constraint_satisfaction_vector`),
    ``temporal`` (a mapping with optional ``mutex`` / ``stall`` /
    ``precedence`` / ``synchronization`` / ``handoff`` sub-results from
    :mod:`goal_temporal_metrics`) and ``progress_curve``
    (:func:`goal_progress_curve.build_progress_curve`).

    Episodes of one base instance are paired repeats: every metric is first
    averaged within the instance and then across instances (protocol 2.6).
    Only evidence-gate ``formal_eligible`` reports enter the formal
    denominators; development-bucket reports providing a metric are counted
    per component and disclosed.  Dimensions without an offline per-episode
    source in this batch (communication adaptation, dynamic
    reconfiguration, cross-episode learning) stay explicit N/A components —
    structurally valid development input is never upgraded into formal
    evidence.  ``release`` is always ``False``.
    """
    _validate_bootstrap_args(bootstrap_iterations, seed)
    if isinstance(episode_reports, Mapping) or not isinstance(
            episode_reports, Sequence):
        raise ValueError("episode_reports must be a sequence of mappings")
    parsed: list[dict[str, Any]] = []
    seen_episode_ids: set[str] = set()
    for index, report in enumerate(episode_reports):
        if not isinstance(report, Mapping):
            raise ValueError(f"episode report {index} must be a mapping")
        episode_id = _episode_key(report, index)
        if episode_id in seen_episode_ids:
            raise ValueError(f"duplicate_episode_id:{episode_id}")
        seen_episode_ids.add(episode_id)
        parsed.append({
            "episode_id": episode_id,
            "base_instance_id": _instance_id(report, index),
            "report": report,
        })
    gate_reports: list[dict[str, Any]] = []
    for row in parsed:
        gate_report: dict[str, Any] = {"episode_id": row["episode_id"]}
        report = row["report"]
        bindings = report.get("source_bindings")
        if isinstance(bindings, Mapping):
            gate_report["source_bindings"] = bindings
        for field in SOURCE_BINDING_FIELDS:
            if field in report:
                gate_report[field] = report[field]
        gate_reports.append(gate_report)
    gate = evidence_source_gate(gate_reports,
                                required_bindings=required_bindings)
    formal_ids = {row["episode_id"] for row in gate["formal_eligible"]}
    development_ids = {row["episode_id"]
                       for row in gate["development_evidence"]}
    formal_rows = [row for row in parsed if row["episode_id"] in formal_ids]
    formal_episode_count = len(formal_rows)
    splitter = _ReportSplitter(parsed, formal_ids, development_ids)

    def continuous_component(name: str,
                             values: list[tuple[str, str, Any]]):
        rows, formal_providing, dev = splitter.split(values)
        if not rows:
            return splitter.na_component(
                NA_ALL_MISSING if dev or formal_providing else NA_EMPTY_SET,
                formal_providing=formal_providing,
                development_providing=dev,
                formal_episode_count=formal_episode_count)
        return splitter.wrap(
            aggregate_continuous(rows, metric_name=name,
                                 bootstrap_iterations=bootstrap_iterations,
                                 seed=seed),
            formal_providing=formal_providing, development_providing=dev,
            formal_episode_count=formal_episode_count)

    # --- task_allocation: deployment rates + constraint satisfaction -----
    task_allocation_metrics: dict[str, Any] = {}
    for name in _DEPLOYMENT_BINARY_RATES:
        values: list[tuple[str, str, Any]] = []
        for row in parsed:
            rates = row["report"].get("deployment_rates")
            entry = (rates.get("rates", {}).get(name)
                     if isinstance(rates, Mapping)
                     and isinstance(rates.get("rates"), Mapping) else None)
            value = None
            if isinstance(entry, Mapping):
                numerator = entry.get("numerator")
                denominator = entry.get("denominator")
                if (isinstance(numerator, int) and isinstance(denominator, int)
                        and not isinstance(numerator, bool)
                        and not isinstance(denominator, bool)):
                    value = (numerator, denominator)
            values.append((row["episode_id"], row["base_instance_id"], value))
        # Bernoulli epochs of all episodes of an instance flatten into the
        # instance repeat list (within-instance mean over epochs first).
        grouped: dict[str, list[float]] = {}
        formal_providing = 0
        dev_providing = 0
        for episode_id, instance_id, value in values:
            if value is None:
                continue
            if episode_id in formal_ids:
                numerator, denominator = value
                grouped.setdefault(instance_id, []).extend(
                    [1.0] * numerator + [0.0] * (denominator - numerator))
                formal_providing += 1
            elif episode_id in development_ids:
                dev_providing += 1
        instance_rows = [{"base_instance_id": instance_id,
                          "epoch_rows": repeats}
                         for instance_id, repeats in sorted(grouped.items())]
        if not instance_rows:
            task_allocation_metrics[name] = splitter.na_component(
                NA_ALL_MISSING if (formal_providing or dev_providing)
                else NA_EMPTY_SET,
                formal_providing=0, development_providing=dev_providing,
                formal_episode_count=formal_episode_count)
        else:
            task_allocation_metrics[name] = splitter.wrap(
                aggregate_binary_rate(instance_rows, metric_name=name),
                formal_providing=formal_providing,
                development_providing=dev_providing,
                formal_episode_count=formal_episode_count)

    latency_values: list[tuple[str, str, Any]] = []
    for row in parsed:
        rates = row["report"].get("deployment_rates")
        entry = (rates.get("rates", {}).get("assessment_latency")
                 if isinstance(rates, Mapping)
                 and isinstance(rates.get("rates"), Mapping) else None)
        value = None
        if isinstance(entry, Mapping):
            observed = entry.get("observed_latencies_s")
            if isinstance(observed, list) and observed:
                value = (sum(_finite(item, "assessment latency item")
                             for item in observed) / len(observed))
        latency_values.append((row["episode_id"], row["base_instance_id"],
                               value))
    task_allocation_metrics["assessment_latency"] = continuous_component(
        "assessment_latency", latency_values)
    for metric_name, component in _CS_COMPONENT_METRICS:
        cs_values: list[tuple[str, str, Any]] = []
        for row in parsed:
            vector = row["report"].get("cs_vector")
            value = (vector.get("vector", {}).get(component)
                     if isinstance(vector, Mapping)
                     and isinstance(vector.get("vector"), Mapping) else None)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                value = None
            else:
                value = float(value)
            cs_values.append((row["episode_id"], row["base_instance_id"],
                              value))
        task_allocation_metrics[metric_name] = continuous_component(
            metric_name, cs_values)

    # --- temporal_coordination --------------------------------------------
    temporal_metrics: dict[str, Any] = {}
    mutex_rows_all: list[tuple[str, dict[str, Any]]] = []
    stall_rows_all: list[tuple[str, dict[str, Any]]] = []
    for row in parsed:
        temporal = row["report"].get("temporal")
        if not isinstance(temporal, Mapping):
            continue
        mutex = temporal.get("mutex")
        if isinstance(mutex, Mapping) and "MutexAny_episode" in mutex:
            enriched = dict(mutex)
            enriched.setdefault("episode_id", row["episode_id"])
            mutex_rows_all.append((row["episode_id"], enriched))
        stall = temporal.get("stall")
        if isinstance(stall, Mapping) and (
                "S_i_stall_indicator" in stall
                or "stall_indicator" in stall):
            enriched = dict(stall)
            enriched.setdefault("episode_id", row["episode_id"])
            stall_rows_all.append((row["episode_id"], enriched))

    def episode_aggregate_component(rows_all, aggregator):
        formal_rows = [row for episode_id, row in rows_all
                       if episode_id in formal_ids]
        dev = sum(1 for episode_id, _ in rows_all
                  if episode_id in development_ids)
        providing = sum(1 for episode_id, _ in rows_all
                        if episode_id in formal_ids)
        if not formal_rows:
            return splitter.na_component(
                NA_ALL_MISSING if rows_all else NA_EMPTY_SET,
                formal_providing=0, development_providing=dev,
                formal_episode_count=formal_episode_count)
        return splitter.wrap(aggregator(formal_rows),
                             formal_providing=providing,
                             development_providing=dev,
                             formal_episode_count=formal_episode_count)

    temporal_metrics["mutex"] = episode_aggregate_component(
        mutex_rows_all,
        lambda rows: aggregate_mutex(
            rows, bootstrap_iterations=bootstrap_iterations, seed=seed))
    temporal_metrics["stall"] = episode_aggregate_component(
        stall_rows_all,
        lambda rows: aggregate_stall_metrics(
            rows, bootstrap_iterations=bootstrap_iterations, seed=seed))
    for name in _PREC_METRICS:
        temporal_metrics[name] = continuous_component(
            name, splitter.metric_values_of(("temporal", "precedence", name)))
    for name in _SYNC_METRICS:
        temporal_metrics[name] = continuous_component(
            name, splitter.metric_values_of(
                ("temporal", "synchronization", name)))
    # Per-episode handoff continuity/G_h are already within-episode rates
    # and means, so the instance-level reduction is the continuous family.
    temporal_metrics["continuity_rate"] = continuous_component(
        "continuity_rate",
        splitter.metric_values_of(("temporal", "handoff", "continuity_rate")))
    temporal_metrics["G_h_mean_s"] = continuous_component(
        "G_h_mean_s",
        splitter.metric_values_of(("temporal", "handoff", "G_h_mean_s")))

    # --- coordination_cost_efficiency -------------------------------------
    cost_metrics = {name: continuous_component(
        name, splitter.metric_values_of(("progress_curve", name)))
        for name in _PROGRESS_METRICS}

    dimensions: dict[str, Any] = {
        "task_allocation": {
            "status": ("computed" if any(
                entry["aggregation"]["status"] == "computed"
                for entry in task_allocation_metrics.values())
                else NA_ALL_MISSING),
            "metrics": task_allocation_metrics,
        },
        "communication_adaptation": _na_dimension(
            NA_DIMENSION_SOURCE_NOT_WIRED,
            "requires channel-seed repeat results from the communication "
            "capacity track; no offline per-episode source is wired into "
            "this batch"),
        "temporal_coordination": {
            "status": ("computed" if any(
                entry["aggregation"]["status"] == "computed"
                for entry in temporal_metrics.values()) else NA_ALL_MISSING),
            "metrics": temporal_metrics,
        },
        "dynamic_reconfiguration": _na_dimension(
            NA_DIMENSION_SOURCE_NOT_WIRED,
            "requires paired fact/reference branch results from snapshot "
            "branching; no offline per-episode source is wired into this "
            "batch"),
        "cross_episode_learning": _na_dimension(
            NA_DIMENSION_SOURCE_NOT_WIRED,
            "requires paired multi-round learning-vs-baseline conditions; "
            "single-episode reports cannot evidence it"),
        "coordination_cost_efficiency": {
            "status": ("computed" if any(
                entry["aggregation"]["status"] == "computed"
                for entry in cost_metrics.values()) else NA_ALL_MISSING),
            "metrics": cost_metrics,
        },
    }
    return {
        "schema": AGGREGATION_SCHEMA,
        "version": AGGREGATION_VERSION,
        "protocol_section": PROTOCOL_SECTION,
        "status": "computed" if parsed else NA_NO_REPORTS,
        "episode_report_count": len(parsed),
        "base_instance_count": len({row["base_instance_id"]
                                    for row in formal_rows}),
        "dimension_names": list(DIMENSIONS),
        "dimensions": dimensions,
        "evidence_gate": gate,
        "aggregation_unit": _WITHIN_INSTANCE_RULE,
        "censoring_rule": ("response/recovery/detection times are reported "
                           "as within-H probability plus right-censored "
                           "(time, event) pairs; H is never a true event "
                           "time"),
        "na_labels": [NA_EMPTY_SET, NA_ALL_MISSING, NA_ZERO_DENOMINATOR,
                      NA_NO_EVENTS, NA_ZERO_CONFIRMED_STALLS,
                      NA_ZERO_OCCUPANCY, NA_NO_REPORTS,
                      NA_DIMENSION_SOURCE_NOT_WIRED],
        **_RELEASE,
    }


__all__ = [
    "AGGREGATION_SCHEMA", "AGGREGATION_VERSION", "BINARY_RATE_METRICS",
    "CONTINUOUS_METRICS", "DEFAULT_BOOTSTRAP_ITERATIONS",
    "DEFAULT_BOOTSTRAP_SEED", "DIMENSIONS", "EVIDENCE_BUCKETS",
    "FORMAL_PROMOTION_STATUS", "GATE_STATUSES", "NA_ALL_MISSING",
    "NA_DIMENSION_SOURCE_NOT_WIRED", "NA_EMPTY_SET", "NA_NO_EVENTS",
    "NA_NO_REPORTS", "NA_ZERO_CONFIRMED_STALLS", "NA_ZERO_DENOMINATOR",
    "NA_ZERO_OCCUPANCY", "PAIRED_METRICS", "PROTOCOL_SECTION",
    "SOURCE_BINDING_FIELDS", "UNVERIFIED_ANCHOR_SOURCE",
    "aggregate_binary_rate", "aggregate_continuous",
    "aggregate_mutex", "aggregate_paired_difference",
    "aggregate_stall_metrics", "build_protocol_metrics_report",
    "evidence_source_gate", "wilson_95_interval",
]
