"""Paper-specific inference; all inputs are frozen scheduled observations.

Instance rows contain repeat-averaged vectors (averaging must preserve all
scheduled repeats). Shared generator streams are resampled as whole clusters.
Quantiles use the empirical inverse CDF (nearest rank), including infinities.
No scipy dependency and no implicit deletion of unknown observations.
"""
from dataclasses import asdict, dataclass
import math
import random
from typing import Callable, Mapping, Sequence


@dataclass(frozen=True)
class InstanceV3:
    instance_id: str
    stratum: str
    values: tuple[float | None, ...]
    stream_id: str = ""


@dataclass(frozen=True)
class IntervalV3:
    point: float
    lower: float
    upper: float
    method: str
    alpha: float
    seed: int | None = None
    resamples: int = 0

    def __post_init__(self):
        if any(not math.isfinite(v) for v in (self.point, self.lower, self.upper, self.alpha)) or self.lower > self.upper or not 0 < self.alpha < 1 or not self.method:
            raise ValueError("interval needs finite ordered bounds and a registered method")


def _alpha(alpha):
    if not math.isfinite(alpha) or not 0 < alpha < 1:
        raise ValueError("alpha must be in (0,1)")


def _weights(weights):
    if not weights or any(not math.isfinite(w) or w < 0 for w in weights) or not math.isclose(sum(weights), 1, abs_tol=1e-9):
        raise ValueError("fixed weights must be finite, nonnegative and sum to one")


def quantile(values, probability):
    if not values or not 0 <= probability <= 1:
        raise ValueError("nonempty samples and valid quantile required")
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered)-1, math.ceil(probability * len(ordered))-1))]


class ClusterDesignV3:
    def __init__(self, instances: Sequence[InstanceV3], stratum_weights: Mapping[str, float]):
        instances = tuple(sorted(instances, key=lambda r: (r.stratum, r.stream_id or r.instance_id, r.instance_id)))
        self.instances = instances
        self.weights = dict(sorted(stratum_weights.items()))
        _weights(tuple(self.weights.values()))
        if not instances or len({r.instance_id for r in instances}) != len(instances):
            raise ValueError("unique nonempty base-instance directory required")
        width = len(instances[0].values)
        if not width or any(not r.instance_id or not r.stratum or len(r.values) != width or any(v is not None and (not math.isfinite(v) or not 0 <= v <= 1) for v in r.values) for r in instances):
            raise ValueError("instance vectors must align and lie in [0,1] or unknown")
        if set(self.weights) != {r.stratum for r in instances}:
            raise ValueError("every registered stratum must have support")
        self.width = width
        self.groups = {}
        for row in instances:
            key = row.stream_id or row.instance_id
            self.groups.setdefault(key, []).append(row)
        self.strata = {s: tuple(r for r in instances if r.stratum == s) for s in self.weights}
        # Cross-stratum streams require a joint block design, not independent
        # per-stratum draws. Reject instead of pretending they are independent.
        if any(len({r.stratum for r in group}) != 1 for group in self.groups.values()):
            raise ValueError("shared streams crossing strata require a registered joint-block design")
        self.group_strata = {s: tuple(g for g in self.groups.values() if g[0].stratum == s) for s in self.weights}

    def means(self, rows=None):
        rows = self.instances if rows is None else rows
        result = [0.0] * self.width
        for s, weight in self.weights.items():
            members = [r for r in rows if r.stratum == s]
            if not members or any(v is None for r in members for v in r.values):
                raise ValueError("unknowns need explicit endpoint completion")
            for j in range(self.width):
                result[j] += weight * sum(r.values[j] for r in members) / len(members)
        return tuple(result)

    def draws(self, *, seed: int, resamples: int):
        if type(resamples) is not int or resamples < 100:
            raise ValueError("at least 100 registered bootstrap resamples required")
        rng = random.Random(seed)
        for _ in range(resamples):
            rows = []
            for groups in self.group_strata.values():
                for _ in groups:
                    rows.extend(rng.choice(groups))
            yield self.means(rows)

    def independent_weights(self):
        return tuple(self.weights[g[0].stratum] * len(g) / len(self.strata[g[0].stratum]) for g in self.groups.values())

    def complete(self, endpoints: Sequence[float]):
        if len(endpoints) != self.width or any(v not in (0, 1) for v in endpoints):
            raise ValueError("one binary endpoint per component required")
        return ClusterDesignV3([InstanceV3(r.instance_id, r.stratum, tuple(endpoints[j] if v is None else v for j, v in enumerate(r.values)), r.stream_id) for r in self.instances], self.weights)


def hoeffding(values, weights, *, alpha=.05, bounds=(0.0, 1.0)):
    _alpha(alpha)
    _weights(weights)
    low, high = bounds
    if not math.isfinite(low) or not math.isfinite(high) or low >= high or len(values) != len(weights) or any(not math.isfinite(v) or not low <= v <= high for v in values):
        raise ValueError("observations must match weights and bounds")
    point = sum(w*v for w, v in zip(weights, values))
    half = (high-low)*math.sqrt(math.log(2/alpha)*sum(w*w for w in weights)/2)
    return IntervalV3(point, max(low, point-half), min(high, point+half), "weighted_hoeffding", alpha)


def cluster_interval(design: ClusterDesignV3, estimator: Callable, *, alpha=.05, seed=0, resamples=2000, bounds=(0., 100.)):
    _alpha(alpha)
    point = estimator(design.means())
    samples = [estimator(m) for m in design.draws(seed=seed, resamples=resamples)]
    lo, hi = quantile(samples, alpha/2), quantile(samples, 1-alpha/2)
    method = "stratified_cluster_percentile"
    if min(samples) == max(samples):
        # Conservative bounded-score fallback on independent clusters. Valid
        # for registered linear bounded per-instance scores, not D2 ratios.
        half = (bounds[1]-bounds[0])*math.sqrt(math.log(2/alpha)*sum(w*w for w in design.independent_weights())/2)
        lo, hi = max(bounds[0], point-half), min(bounds[1], point+half)
        method = "constant_cluster_hoeffding"
    return IntervalV3(point, lo, hi, method, alpha, seed, resamples)


def d2_projection(design: ClusterDesignV3, *, alpha=.05, seed=0, resamples=2000, family_size=3, _joint_samples=None):
    """Simultaneous retention bands then capped monotone projection (not
    a percentile bootstrap of the capped score). family_size=6 for contrasts.
    """
    _alpha(alpha)
    if design.width != 4 or family_size not in (3, 6):
        raise ValueError("D2 needs C0..C3; simultaneous family has 3 or 6 bands")
    rates = design.means()
    samples = tuple(design.draws(seed=seed, resamples=resamples)) if _joint_samples is None else tuple(_joint_samples)
    zero_count = sum(m[0] == 0 for m in samples)
    bands = []
    band_alpha = alpha / family_size
    weights = design.independent_weights()
    rate_half = math.sqrt(math.log(4/band_alpha)*sum(w*w for w in weights)/2)
    for j in range(1, 4):
        constant = len({r.values[0] for r in design.instances}) == 1 or len({r.values[j] for r in design.instances}) == 1
        if constant:
            ref_lo, ref_hi = max(0., rates[0]-rate_half), min(1., rates[0]+rate_half)
            num_lo, num_hi = max(0., rates[j]-rate_half), min(1., rates[j]+rate_half)
            lo = num_lo/ref_hi if ref_hi else 0.
            hi = num_hi/ref_lo if ref_lo else math.inf
        else:
            lo = quantile([m[j]/m[0] if m[0] else 0. for m in samples], band_alpha/2)
            hi = quantile([m[j]/m[0] if m[0] else math.inf for m in samples], 1-band_alpha/2)
        bands.append((lo, hi))
    score = lambda rs: 100*sum(min(1., r) for r in rs)/3
    zero_fraction = zero_count/resamples
    status = "na" if not rates[0] else "unstable" if zero_fraction > alpha/2 else "valid"
    return {"point": score([p/rates[0] for p in rates[1:]]) if rates[0] else None,
            "lower": score([b[0] for b in bands]), "upper": score([b[1] for b in bands]),
            "retention_bands": bands, "nominal": rates[0], "zero_reference_resample_fraction": zero_fraction,
            "method": "simultaneous_retention_projection", "alpha": alpha, "seed": seed, "resamples": resamples,
            "quantile_rule": "empirical_inverse_cdf", "status": status}


def _intersect_interval_sets(left, right):
    result = []
    for a, b in left:
        for c, d in right:
            lo, hi = max(a, c), min(b, d)
            if lo <= hi:
                result.append((lo, hi))
    result.sort()
    merged = []
    for lo, hi in result:
        if merged and lo <= merged[-1][1] + 1e-12:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def _quadratic_leq_intervals(a, b, c):
    """Exact intervals in [0,1] where a*x^2+b*x+c <= 0."""
    eps = 1e-14
    if abs(a) < eps:
        if abs(b) < eps:
            return [(0., 1.)] if c <= 0 else []
        root = -c / b
        return _intersect_interval_sets(
            [(0., root)] if b > 0 else [(root, 1.)], [(0., 1.)])
    discriminant = b*b - 4*a*c
    if discriminant < -eps:
        return [(0., 1.)] if c <= 0 else []
    root_delta = math.sqrt(max(0., discriminant))
    roots = sorted(((-b-root_delta)/(2*a), (-b+root_delta)/(2*a)))
    points = [0.] + [value for value in roots if 0. < value < 1.] + [1.]
    intervals = []
    for lo, hi in zip(points, points[1:]):
        mid = (lo+hi)/2
        if a*mid*mid+b*mid+c <= eps:
            intervals.append((lo, hi))
    for value in points:
        if a*value*value+b*value+c <= eps:
            intervals.append((value, value))
    return _intersect_interval_sets(intervals, [(0., 1.)])


def _d2_group_rows(design: ClusterDesignV3, restricted_index: int):
    groups = []
    for members in design.groups.values():
        stratum = members[0].stratum
        weight = design.weights[stratum] * len(members) / len(design.strata[stratum])
        reference = sum(row.values[0] for row in members) / len(members)
        restricted = sum(row.values[restricted_index] for row in members) / len(members)
        groups.append((weight, reference, restricted))
    return groups


def _d2_bennett_acceptance(groups, *, delta):
    count = len(groups)
    depth = math.ceil(math.log2(math.sqrt(count))) + 2
    lambdas = tuple(sorted({.9, *(2. ** -index for index in range(1, depth+1))}))
    threshold = math.log(4*len(lambdas)/delta)
    max_weight = max(weight for weight, _, _ in groups)

    def accepted(*, reciprocal):
        intervals = [(0., 1.)]
        for sign in (-1., 1.):
            for lam in lambdas:
                psi = -math.log(1-lam)-lam
                constants, slopes = [], []
                for weight, reference, restricted in groups:
                    scaled_reference = weight/max_weight*reference
                    scaled_restricted = weight/max_weight*restricted
                    if reciprocal:
                        constants.append(-scaled_reference)
                        slopes.append(scaled_restricted)
                    else:
                        constants.append(scaled_restricted)
                        slopes.append(-scaled_reference)
                theta0, theta1 = sum(constants), sum(slopes)
                square0 = sum(value*value for value in constants)
                square1 = 2*sum(a*b for a, b in zip(constants, slopes))
                square2 = sum(value*value for value in slopes)
                condition = _quadratic_leq_intervals(
                    -psi*square2,
                    sign*lam*theta1-psi*square1,
                    sign*lam*theta0-psi*square0-threshold,
                )
                intervals = _intersect_interval_sets(intervals, condition)
                if not intervals:
                    return []
        return intervals

    low = accepted(reciprocal=False)
    reciprocal = accepted(reciprocal=True)
    high = []
    for lo, hi in reciprocal:
        if hi <= 0:
            continue
        high.append((1/hi, math.inf if lo <= 0 else 1/lo))
    return _intersect_interval_sets(low + high, [(0., math.inf)])


def d2_finite_sample_interval(design: ClusterDesignV3, *, alpha=.05, family_size=3):
    """Finite-sample H∩B retention-set interval from paper commit 1f32949."""
    _alpha(alpha)
    if design.width != 4 or family_size not in (3, 6):
        raise ValueError("D2 needs C0..C3 and a three- or six-retention family")
    rates = design.means()
    delta = alpha/family_size
    bands = []
    for index in range(1, 4):
        groups = _d2_group_rows(design, index)
        epsilon = math.sqrt(.5*math.log(4/delta)*sum(weight*weight for weight, _, _ in groups))
        h_low = max(0., rates[index]-epsilon)/(rates[0]+epsilon)
        denominator = max(0., rates[0]-epsilon)
        h_high = ((rates[index]+epsilon)/denominator if denominator else
                  (math.inf if rates[index]+epsilon > 0 else 0.))
        accepted = _intersect_interval_sets(
            _d2_bennett_acceptance(groups, delta=delta), [(h_low, h_high)])
        if not accepted:
            raise ValueError("D2 finite-sample retention set is empty")
        bands.append((accepted[0][0], accepted[-1][1]))
    score = lambda endpoints: 100*sum(min(1., value) for value in endpoints)/3
    point = (score([rates[index]/rates[0] for index in range(1, 4)])
             if rates[0] > 0 else None)
    return {
        "point": point,
        "lower": score([band[0] for band in bands]),
        "upper": score([band[1] for band in bands]),
        "retention_bands": bands,
        "nominal": rates[0],
        "method": "d2_finite_sample_retention_set",
        "alpha": alpha,
        "family_size": family_size,
        "status": "valid" if point is not None else "na",
    }


def d2_unknown_identification_interval(low_design: ClusterDesignV3,
                                       high_design: ClusterDesignV3,
                                       all_failure_design: ClusterDesignV3,
                                       all_success_design: ClusterDesignV3,
                                       *, unknown_components: Sequence[bool],
                                       undefined_completion_possible: bool,
                                       alpha=.05, family_size=3):
    """Fixed-weight D2 range with run-level unknown completions.

    Affected retentions use the paper's extreme-rate Hoeffding set. Unaffected
    retentions retain the full finite-sample H∩B set.
    """
    if any(design.width != 4 for design in (low_design, high_design, all_failure_design, all_success_design)):
        raise ValueError("D2 unknown completion requires four aligned outcomes")
    if len(unknown_components) != 4:
        raise ValueError("D2 unknown mask must cover C0..C3")
    low_rates, high_rates = low_design.means(), high_design.means()
    minus_rates, plus_rates = all_failure_design.means(), all_success_design.means()
    score = lambda values: 100*sum(min(1., value/values[0]) for value in values[1:])/3
    if low_rates[0] <= 0 or high_rates[0] <= 0:
        return {"status": "na", "reason": "no_positive_reference_completion",
                "undefined_completion_possible": undefined_completion_possible}
    low_point, high_point = score(low_rates), score(high_rates)
    delta = alpha/family_size
    weights = low_design.independent_weights()
    epsilon = math.sqrt(.5*math.log(4/delta)*sum(weight*weight for weight in weights))
    unaffected = d2_finite_sample_interval(low_design, alpha=alpha, family_size=family_size)["retention_bands"]
    bands = []
    for index in range(1, 4):
        if unknown_components[0] or unknown_components[index]:
            lower = max(0., minus_rates[index]-epsilon)/(plus_rates[0]+epsilon)
            denominator = max(0., minus_rates[0]-epsilon)
            upper = ((plus_rates[index]+epsilon)/denominator if denominator else
                     (math.inf if plus_rates[index]+epsilon > 0 else 0.))
            bands.append((lower, upper))
        else:
            bands.append(unaffected[index-1])
    interval = (
        100*sum(min(1., band[0]) for band in bands)/3,
        100*sum(min(1., band[1]) for band in bands)/3,
    )
    low_endpoint = d2_finite_sample_interval(low_design, alpha=alpha, family_size=family_size)
    high_endpoint = d2_finite_sample_interval(high_design, alpha=alpha, family_size=family_size)
    endpoints = (
        {**low_endpoint, "point": low_point, "lower": interval[0],
         "endpoint_role": "low_completion", "interval_status": "valid"},
        {**high_endpoint, "point": high_point, "upper": interval[1],
         "endpoint_role": "high_completion", "interval_status": "valid"},
    )
    return {
        "status": "range" if not math.isclose(low_point, high_point) else "valid",
        "identification": (low_point, high_point),
        "interval": interval,
        "endpoints": endpoints,
        "retention_bands": bands,
        "method": "d2_finite_sample_unknown_extreme_rates",
        "undefined_completion_possible": undefined_completion_possible,
        "nominal_extremes": (low_rates[0], high_rates[0]),
    }


def d2_headline_interval(design: ClusterDesignV3, *, alpha=.05, seed=0, resamples=2000):
    # seed/resamples remain accepted for schema compatibility; the registered
    # D2 interval is finite-sample and uses neither bootstrap randomness nor a
    # resample count.
    return d2_finite_sample_interval(design, alpha=alpha)


def identification_interval(design, estimator, *, d2=False, alpha=.05, seed=0, resamples=2000):
    """Return the current paper's fixed-weight identification set.

    Infrastructure unknowns are first rerun under the registered recovery
    rule.  Any record still unknown is retained at both outcome endpoints;
    D2 uses the special endpoint completions that keep the nominal reference
    rate positive whenever the paper's rule permits it.  This range is an
    identification result, not sampling uncertainty, and is never silently
    converted into a point estimate.
    """
    if d2:
        if design.width != 4:
            raise ValueError("D2 identification requires C0..C3")
        unknown = tuple(any(row.values[index] is None for row in design.instances) for index in range(4))
        if not any(unknown):
            headline = d2_finite_sample_interval(design, alpha=alpha)
            return {"status": "valid", "identification": (headline["point"], headline["point"]),
                    "interval": (headline["lower"], headline["upper"]),
                    "endpoints": (headline, headline), "method": headline["method"],
                    "alpha": alpha, "family_size": headline["family_size"]}

        def completed(endpoints):
            return ClusterDesignV3([
                InstanceV3(row.instance_id, row.stratum,
                           tuple(endpoints[j] if value is None else value for j, value in enumerate(row.values)),
                           row.stream_id)
                for row in design.instances], design.weights)

        low = completed((1, 0, 0, 0))
        high = completed((0, 1, 1, 1))
        undefined = high.means()[0] == 0 and unknown[0]
        # The paper's high endpoint keeps the smallest-weight unknown C0
        # record at one if otherwise the reference denominator vanishes.
        if high.means()[0] == 0 and unknown[0]:
            candidates = []
            for row in design.instances:
                if row.values[0] is None:
                    candidates.append((design.weights[row.stratum] / len(design.strata[row.stratum]), row))
            if candidates:
                _, selected = min(candidates, key=lambda pair: (pair[0], pair[1].instance_id))
                high = ClusterDesignV3([
                    InstanceV3(row.instance_id, row.stratum,
                               ((1,) + tuple(value if value is not None else 1 for value in row.values[1:]))
                               if row.instance_id == selected.instance_id else
                               tuple(0 if value is None and index == 0 else (1 if value is None else value)
                                     for index, value in enumerate(row.values)), row.stream_id)
                    for row in design.instances], design.weights)
        all_failure = completed((0, 0, 0, 0))
        all_success = completed((1, 1, 1, 1))
        result = d2_unknown_identification_interval(low, high, all_failure, all_success,
                                                     unknown_components=unknown,
                                                     undefined_completion_possible=undefined,
                                                     alpha=alpha)
        return {**result, "alpha": alpha, "family_size": 3}

    low = design.complete((0,)*design.width)
    high = design.complete((1,)*design.width)
    a, b = [asdict(cluster_interval(d, estimator, alpha=alpha, seed=seed, resamples=resamples)) for d in (low, high)]
    return {"status": "range" if a["point"] != b["point"] else "valid", "identification": (a["point"], b["point"]), "interval": (a["lower"], b["upper"]), "endpoints": (a, b)}


def paired_design(candidate, reference):
    a = {(r.stratum, r.instance_id, r.stream_id): r for r in candidate.instances}
    b = {(r.stratum, r.instance_id, r.stream_id): r for r in reference.instances}
    if a.keys() != b.keys() or candidate.weights != reference.weights or candidate.width != reference.width:
        raise ValueError("paired systems must share exact scheduled support and cluster identities")
    return ClusterDesignV3([InstanceV3(r.instance_id, r.stratum, r.values+b[k].values, r.stream_id) for k, r in a.items()], candidate.weights)


def paired_interval(candidate, reference, estimator, *, d2=False, alpha=.05, seed=0, resamples=2000):
    if d2:
        # The current paper allocates alpha across the six retentions and forms
        # the score-difference interval from the two finite-sample score sets.
        paired_design(candidate, reference)  # exact scheduled support remains mandatory
        left = d2_finite_sample_interval(candidate, alpha=alpha, family_size=6)
        right = d2_finite_sample_interval(reference, alpha=alpha, family_size=6)
        if left["point"] is None or right["point"] is None:
            raise ValueError("undefined D2 paired reference")
        return IntervalV3(
            left["point"]-right["point"],
            left["lower"]-right["upper"],
            left["upper"]-right["lower"],
            "d2_finite_sample_difference", alpha)
    joined = paired_design(candidate, reference)
    k = candidate.width
    return cluster_interval(joined, lambda m: estimator(m[:k])-estimator(m[k:]), alpha=alpha, seed=seed, resamples=resamples, bounds=(-100., 100.))


def invert_interval(builder, *, iterations=35):
    """Smallest alpha whose nested registered interval excludes zero."""
    lo, hi = 0., 1.
    for _ in range(iterations):
        mid = (lo+hi)/2
        interval = builder(mid)
        if interval.lower > 0 or interval.upper < 0:
            hi = mid
        else:
            lo = mid
    return hi


def holm_six(pvalues: Mapping[str, float], *, alpha=.05):
    _alpha(alpha)
    if set(pvalues) != {f"D{i}" for i in range(1, 7)} or any(not math.isfinite(p) or not 0 <= p <= 1 for p in pvalues.values()):
        raise ValueError("primary Holm family must contain exactly D1..D6")
    result, previous = {}, 0.
    for index, (key, p) in enumerate(sorted(pvalues.items(), key=lambda item: item[1])):
        previous = max(previous, min(1., (6-index)*p))
        result[key] = {"p": p, "adjusted_p": previous, "reject": previous <= alpha}
    return result


def operating_boundary(capacities, bands, *, threshold=.9):
    if len(capacities) != len(bands)+1 or any(not math.isfinite(c) or c <= 0 for c in capacities) or any(a <= b for a,b in zip(capacities, capacities[1:])) or not 0 < threshold <= 1:
        raise ValueError("registered strictly descending capacities required")
    if any(len(b) != 2 or math.isnan(b[0]) or math.isnan(b[1]) or b[0] < 0 or b[0] > b[1] for b in bands):
        raise ValueError("invalid retention bands")
    states = ["pass" if lo >= threshold else "fail" if hi < threshold else "unresolved" for lo, hi in bands]
    leading = 0
    for state in states:
        if state != "pass": break
        leading += 1
    failing = next((i+1 for i,s in enumerate(states) if i+1 > leading and s == "fail"), None)
    return {"states": states, "passing_capacity": capacities[leading], "failing_capacity": capacities[failing] if failing is not None else None, "grid_censored": failing is None, "nonmonotone_recovery_levels": [i+1 for i,s in enumerate(states) if s == "pass" and i+1 > leading]}


def competing_risks(trajectories, *, horizon):
    """Aalen–Johansen first-event CIF and event-free RMST, tied events before
    censor removals. Cluster uncertainty is computed by the caller's design.
    Each row is (observed time, cause or None for right censoring).
    """
    if not trajectories or not math.isfinite(horizon) or horizon <= 0 or any(not math.isfinite(t) or t < 0 for t,c in trajectories):
        raise ValueError("valid trajectories and horizon required")
    causes = sorted({c for t,c in trajectories if c is not None})
    cif = dict.fromkeys(causes, 0.)
    risk, survival, rmst, previous = len(trajectories), 1., 0., 0.
    curve = []
    for t in sorted({t for t,c in trajectories if t <= horizon}):
        rmst += survival*(t-previous)
        events = {c: sum(time == t and cause == c for time,cause in trajectories) for c in causes}
        censored = sum(time == t and cause is None for time,cause in trajectories)
        for cause, count in events.items():
            cif[cause] += survival*count/risk
        survival *= 1-sum(events.values())/risk
        risk -= sum(events.values())+censored
        previous = t
        curve.append({"time": t, "survival": survival, "cif": dict(cif), "risk_after": risk})
    rmst += survival*(horizon-previous)
    estimable = not (risk == 0 and survival > 0 and previous < horizon)
    return {"cif": cif, "event_free_survival": survival, "rmst": rmst if estimable else None, "status": "valid" if estimable else "censoring_support_ends_before_horizon", "censored": sum(c is None or t > horizon for t,c in trajectories), "curve": curve}


def resource_saving_gate(success: IntervalV3 | None, progress: IntervalV3 | None, *, success_margin: float, progress_margin: float):
    if any(not math.isfinite(m) or m < 0 for m in (success_margin, progress_margin)):
        raise ValueError("noninferiority margins must be frozen and nonnegative")
    if success is None or progress is None:
        return "na"
    return "pass" if success.lower >= -success_margin and progress.lower >= -progress_margin else "fail"


def fixed_identification(values, weights, *, alpha=.05):
    """D5/D6 run weights stay fixed at both unknown endpoints."""
    from dataclasses import asdict
    ends = [hoeffding([endpoint if v is None else v for v in values], weights, alpha=alpha) for endpoint in (0., 1.)]
    return {"identification": [100*e.point for e in ends], "interval": [100*ends[0].lower, 100*ends[1].upper], "endpoints": [asdict(e) for e in ends], "method": "fixed_catalog_weighted_hoeffding"}


def hoeffding_sample_size(*, target_half_width=15., alpha=.05, floor=30, cap=200, paired=False):
    _alpha(alpha)
    if not 0 < target_half_width < 100 or not 1 <= floor <= cap:
        raise ValueError("invalid preregistered precision plan")
    scale = 200. if paired else 100.
    needed = math.ceil(scale**2 * math.log(2/alpha) / (2*target_half_width**2))
    selected = min(cap, max(floor, needed))
    width = scale*math.sqrt(math.log(2/alpha)/(2*selected))
    return {"count": selected, "required": needed, "half_width": width, "precision_sufficient": width <= target_half_width, "unit": "independent_complete_run"}


def d2_precision_plan(pilots: Mapping[str, ClusterDesignV3], *, counts=(30, 60, 100, 150, 200), target_half_width=15., simulations=20, resamples=2000, seed=0):
    """Independent pilot only; jointly simulate all four capacities. Counts
    are numbers per stratum, fixed before candidate execution. Select smallest
    grid count meeting target for every registered pilot on mean half-width.
    Report the simulation rule; never adapt a catalog to candidate outcomes.
    """
    if not pilots or not counts or tuple(sorted(set(counts))) != tuple(counts) or min(counts) < 30 or max(counts) > 200 or simulations < 1 or not 0 < target_half_width < 100:
        raise ValueError("invalid D2 independent-pilot precision plan")
    if any(any(len(g) != 1 for g in d.groups.values()) for d in pilots.values()):
        raise ValueError("pilot precision planner requires independent base instances")
    rng, history = random.Random(seed), []
    for n in counts:
        widths = {}
        for name, pilot in pilots.items():
            simulated = []
            for trial in range(simulations):
                rows = [InstanceV3(f"{s}/{i}", s, rng.choice(members).values) for s,members in pilot.strata.items() for i in range(n)]
                interval = d2_headline_interval(ClusterDesignV3(rows, pilot.weights), seed=seed+trial, resamples=resamples)
                simulated.append((interval["upper"]-interval["lower"])/2
                                 if interval["status"] == "valid" else math.inf)
            widths[name] = sum(simulated)/simulations
        history.append({"count_per_stratum": n, "mean_half_width_by_pilot": widths})
        if all(w <= target_half_width for w in widths.values()):
            return {"count_per_stratum": n, "precision_sufficient": True, "history": history, "seed": seed, "simulations": simulations}
    return {"count_per_stratum": counts[-1], "precision_sufficient": False, "history": history, "seed": seed, "simulations": simulations}
