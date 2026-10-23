"""Prerequisites for formal experiments, explicit about unresolved inputs."""
from dataclasses import dataclass, asdict
import math
from typing import Mapping
from .scorebook_v3 import PAPER_COMMIT
from .records_v3 import canonical_digest
from .protocol_v3 import CalibrationBatchV3


@dataclass(frozen=True)
class ExperimentRegistrationV3:
    paper_commit: str
    catalog_digest: str | None
    asset_digest: str | None
    split_digest: str | None
    evaluator_digest: str | None
    checkpoint_provider_digest: str | None
    calibration_coordinator_id: str | None
    capacities_bps: tuple[float, ...] | None
    queue_limit_bytes: int | None
    baseline_manifest_digest: str | None
    common_start_directory_digest: str | None
    d5_reference_directory_digest: str | None
    seed: int = 0
    bootstrap_resamples: int = 2000
    repeats: int = 3
    minimum_instances: int = 30
    infrastructure_coverage: float = .8
    max_instances: int = 200
    precision_half_width: float = 15.
    alpha: float = .05

    def __post_init__(self):
        if self.paper_commit != PAPER_COMMIT:
            raise ValueError("registration must bind the frozen paper")
        if self.capacities_bps is not None and (len(self.capacities_bps) != 4 or any(not math.isfinite(c) or c <= 0 for c in self.capacities_bps) or any(a <= b for a,b in zip(self.capacities_bps, self.capacities_bps[1:]))):
            raise ValueError("D2 requires C0>C1>C2>C3>0")
        if self.queue_limit_bytes is not None and (type(self.queue_limit_bytes) is not int or self.queue_limit_bytes <= 0):
            raise ValueError("queue limit must be positive bytes")
        if type(self.seed) is not int or type(self.bootstrap_resamples) is not int or self.bootstrap_resamples < 100 or self.repeats != 3 or not 30 <= self.minimum_instances <= self.max_instances <= 200 or not 0 < self.infrastructure_coverage <= 1 or not 0 < self.precision_half_width < 100 or not 0 < self.alpha < 1:
            raise ValueError("invalid preregistered inference settings")

    def unresolved(self):
        return tuple(k for k,v in asdict(self).items() if v is None or v == "")

    def digest(self):
        return canonical_digest(asdict(self))


def coverage_status(*, planned: int, infrastructure_valid: int, minimum=30, fraction=.8):
    if type(planned) is not int or type(infrastructure_valid) is not int or planned <= 0 or not 0 <= infrastructure_valid <= planned:
        raise ValueError("invalid frozen infrastructure funnel")
    ratio = infrastructure_valid/planned
    return {"planned": planned, "infrastructure_valid": infrastructure_valid, "coverage": ratio, "reportable": infrastructure_valid >= minimum and ratio >= fraction}


def calibration_sensitivity(batches: tuple[CalibrationBatchV3, ...], *, candidate_ids, run_resources):
    """Independent-batch medians and observed cross-batch sensitivity envelope.
    Envelope is NOT a confidence interval; no invented calibration precision.
    """
    from .metrics_v3 import d6_score
    if len(batches) < 2 or len({b.batch_id for b in batches}) != len(batches):
        raise ValueError("at least two distinct independent calibration batches required")
    if len({(b.coordinator_id, b.source_digest) for b in batches}) != 1:
        raise ValueError("calibration batches must use one frozen coordinator implementation")
    ids = [rid for b in batches for rid in b.run_ids]
    if len(ids) != len(set(ids)) or len({b.independent_task_digest for b in batches}) != len(batches):
        raise ValueError("independent calibration batches cannot reuse runs/tasks")
    refs = {b.batch_id: b.references(candidate_ids=candidate_ids) for b in batches}
    scores = {key: d6_score(run_resources, reference_bits=b, reference_time=t).point for key,(b,t) in refs.items()}
    intervals = {}
    for batch in batches:
        bits = [b for b,s in zip(batch.transmitted_bits, batch.success) if s]
        times = [t for t,s in zip(batch.physical_seconds, batch.success) if s]
        # Two references per batch, simultaneous across supplied independent
        # batches. Distribution-free median intervals over successful runs.
        intervals[batch.batch_id] = {"bits": median_interval(bits, alpha=.05/(2*len(batches))), "time": median_interval(times, alpha=.05/(2*len(batches)))}
    return {"references": refs, "scores": scores, "sensitivity_envelope": (min(scores.values()), max(scores.values())), "sampling_interval": intervals, "status": "independent_batch_median_intervals", "interval_method": "binomial_order_statistic_bonferroni", "unbounded": any(v[1] == math.inf for pair in intervals.values() for v in pair.values())}


def median_interval(values, *, alpha=.05):
    """Distribution-free median interval for independent nonnegative samples.
    Insufficient observations give [0,infinity], never false exact precision.
    """
    if not values or not 0 < alpha < 1 or any(not math.isfinite(x) or x < 0 for x in values):
        raise ValueError("median interval needs independent nonnegative observations")
    ordered = sorted(values)
    n, k = len(ordered), 0
    for candidate in range(1, n//2+1):
        tail = sum(math.comb(n,j) for j in range(candidate))/2**n
        if tail <= alpha/2: k = candidate
        else: break
    return (ordered[k-1], ordered[n-k]) if k else (0., math.inf)


def formal_readiness(registration: ExperimentRegistrationV3, *, case_report: Mapping, baseline_report: Mapping, calibration_report: Mapping | None):
    blockers = list(registration.unresolved())
    if case_report.get("status") != "ready": blockers.append("certified_catalog")
    if baseline_report.get("status") != "ready": blockers.append("qualified_baselines")
    if calibration_report is None or calibration_report.get("sampling_interval") is None: blockers.append("independent_calibration_uncertainty")
    return {"ready": not blockers, "blockers": blockers, "registration_digest": registration.digest(), "paper_commit": PAPER_COMMIT}
