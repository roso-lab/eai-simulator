"""Paper-facing reduction of release-indexed metric observations.

The reducer is deliberately independent from the simulator and evaluator.  It
only consumes :class:`MetricObservation` records that have already been
associated with a release artifact.  Repeats are averaged inside a
``base_instance_id`` first; base instances then receive equal weight.  This
keeps K=3 repeats from becoming three independent statistical units.
"""

from __future__ import annotations

from collections import Counter, defaultdict
import csv
from dataclasses import dataclass, field
import json
from pathlib import Path
import random
from statistics import mean
from typing import Any, Iterable, Mapping, Sequence

from .metrics import MetricObservation, MetricRegistry, MetricStatus
from .release import ReleaseReport


REDUCER_VERSION = "benchmark.reducer.v1"


@dataclass(frozen=True)
class ReductionRecord:
    """One branch/episode observation admitted to the reducer.

    ``metric_spec_id`` is kept explicit even though it normally equals
    ``metric.metric_id``.  Keeping it in the serialized input catches stale
    or cross-version records instead of silently pooling them.
    """

    metric: MetricObservation
    metric_spec_id: str
    base_instance_id: str
    repeat_id: str
    branch_role: str
    condition_id: str
    relation: str | None
    artifact_digest: str
    release_status: str
    valid_for_inference: bool
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "metric_spec_id",
            "base_instance_id",
            "repeat_id",
            "branch_role",
            "condition_id",
            "artifact_digest",
            "release_status",
        ):
            if not str(getattr(self, name)).strip():
                raise ValueError(f"{name} is required")
        if not isinstance(self.metric, MetricObservation):
            raise TypeError("metric must be a MetricObservation")
        if self.metric_spec_id != self.metric.metric_id:
            raise ValueError("metric_spec_id must equal metric.metric_id")
        object.__setattr__(self, "relation", None if self.relation is None else str(self.relation))
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric.to_dict(),
            "metric_spec_id": self.metric_spec_id,
            "base_instance_id": self.base_instance_id,
            "repeat_id": self.repeat_id,
            "branch_role": self.branch_role,
            "condition_id": self.condition_id,
            "relation": self.relation,
            "artifact_digest": self.artifact_digest,
            "release_status": self.release_status,
            "valid_for_inference": self.valid_for_inference,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ReducedMetric:
    metric_id: str
    status: MetricStatus
    estimate: float | None
    ci_low: float | None
    ci_high: float | None
    support_count: int
    denominator: int
    risk_set_count: int
    coverage: float | None
    na_reason: str | None
    exclusions: Mapping[str, int]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "status": self.status.value,
            "estimate": self.estimate,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "support_count": self.support_count,
            "denominator": self.denominator,
            "risk_set_count": self.risk_set_count,
            "coverage": self.coverage,
            "na_reason": self.na_reason,
            "exclusions": dict(self.exclusions),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class ReductionReport:
    metrics: tuple[ReducedMetric, ...]
    reducer_version: str = REDUCER_VERSION
    catalog_digest: str = ""
    plan_digest: str = ""
    artifact_digests: tuple[str, ...] = ()
    bootstrap_seed: int = 0
    coverage: Mapping[str, Any] = field(default_factory=dict)
    exclusion_funnel: Mapping[str, int] = field(default_factory=dict)

    @property
    def reduced_metrics(self) -> tuple[ReducedMetric, ...]:
        """Compatibility alias for callers that use the contract wording."""
        return self.metrics

    def by_metric(self, metric_id: str) -> ReducedMetric:
        for metric in self.metrics:
            if metric.metric_id == metric_id:
                return metric
        raise KeyError(metric_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.reducer_version,
            "catalog_digest": self.catalog_digest,
            "plan_digest": self.plan_digest,
            "artifact_digests": list(self.artifact_digests),
            "bootstrap_seed": self.bootstrap_seed,
            "metrics": [metric.to_dict() for metric in self.metrics],
            "coverage": dict(self.coverage),
            "exclusion_funnel": dict(self.exclusion_funnel),
        }


def _quantile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return float(ordered[lower] * (1.0 - weight) + ordered[upper] * weight)


class MetricReducer:
    """Aggregate reduction records and produce paper-ready exports."""

    def __init__(self, *, metric_registry: MetricRegistry | None = None, bootstrap_seed: int = 0, n_bootstrap: int = 2000):
        self.registry = metric_registry or MetricRegistry()
        self.bootstrap_seed = int(bootstrap_seed)
        self.n_bootstrap = max(0, int(n_bootstrap))

    @staticmethod
    def _release_ready(release: ReleaseReport | None) -> bool:
        return release is None or release.inferentially_ready

    def _eligible(self, record: ReductionRecord, release: ReleaseReport | None) -> tuple[bool, str | None]:
        try:
            spec = self.registry.get(record.metric.metric_id)
        except Exception:
            return False, "unknown_metric"
        if record.metric_spec_id != spec.metric_id:
            return False, "metric_spec_mismatch"
        if not record.valid_for_inference:
            return False, "record_not_valid_for_inference"
        if not self._release_ready(release):
            return False, "release_gate_failed"
        if not record.artifact_digest:
            return False, "artifact_missing"
        if spec.relation is not None and record.relation != spec.relation:
            return False, "relation_mismatch"
        expected = spec.condition
        # condition_id may carry an experiment-qualified value, but should
        # still contain the registered condition token.
        if expected and expected not in record.condition_id:
            return False, "condition_mismatch"
        if record.metric.status is not MetricStatus.VALID:
            return False, f"status_{record.metric.status.value}"
        denominator = record.metric.denominator
        if denominator is None or float(denominator) < spec.denominator_floor:
            return False, "denominator_below_floor"
        return True, None

    def _bootstrap_intervals(self, grouped: Mapping[str, Mapping[str, Sequence[float]]], *, n_resamples: int, seed: int) -> dict[str, tuple[float | None, float | None]]:
        rng = random.Random(seed)
        result: dict[str, tuple[float | None, float | None]] = {}
        for metric_id, bases in grouped.items():
            base_values = [mean(values) for values in bases.values() if values]
            if not base_values or n_resamples <= 0:
                result[metric_id] = (None, None)
                continue
            if len(base_values) == 1:
                result[metric_id] = (base_values[0], base_values[0])
                continue
            samples = [mean(rng.choices(base_values, k=len(base_values))) for _ in range(n_resamples)]
            result[metric_id] = (_quantile(samples, 0.025), _quantile(samples, 0.975))
        return result

    def bootstrap(self, records: Sequence[ReductionRecord], *, cluster_key: str = "base_instance_id", n_resamples: int | None = None, seed: int | None = None) -> dict[str, tuple[float | None, float | None]]:
        if cluster_key != "base_instance_id":
            raise ValueError("only base_instance_id cluster bootstrap is supported")
        grouped: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        for record in records:
            eligible, _ = self._eligible(record, None)
            if eligible and record.metric.value is not None:
                grouped[record.metric.metric_id][record.base_instance_id].append(float(record.metric.value))
        return self._bootstrap_intervals(grouped, n_resamples=self.n_bootstrap if n_resamples is None else int(n_resamples), seed=self.bootstrap_seed if seed is None else int(seed))

    def reduce(self, records: Sequence[ReductionRecord], *, release: ReleaseReport | None = None) -> ReductionReport:
        records = tuple(records)
        grouped: dict[str, dict[str, dict[str, list[float]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
        all_by_metric: dict[str, list[ReductionRecord]] = defaultdict(list)
        exclusions: dict[str, Counter[str]] = defaultdict(Counter)
        identities: set[tuple[str, str, str, str, str, str]] = set()
        for record in records:
            all_by_metric[record.metric.metric_id].append(record)
            identity = (record.metric.metric_id, record.base_instance_id, record.repeat_id, record.branch_role, record.condition_id, record.relation or "")
            if identity in identities:
                exclusions[record.metric.metric_id]["duplicate_record_identity"] += 1
                continue
            identities.add(identity)
            eligible, reason = self._eligible(record, release)
            if eligible:
                grouped[record.metric.metric_id][record.base_instance_id][record.repeat_id].append(float(record.metric.value))
            elif reason:
                exclusions[record.metric.metric_id][reason] += 1

        ci = self._bootstrap_intervals(
            {metric_id: {base: [mean(values) for values in repeats.values() if values] for base, repeats in bases.items()} for metric_id, bases in grouped.items()},
            n_resamples=self.n_bootstrap,
            seed=self.bootstrap_seed,
        )
        reduced: list[ReducedMetric] = []
        funnel: Counter[str] = Counter()
        metric_ids = sorted(set(all_by_metric) | {spec.metric_id for spec in self.registry.specs})
        for metric_id in metric_ids:
            rows = all_by_metric.get(metric_id, [])
            spec = self.registry.get(metric_id)
            bases = grouped.get(metric_id, {})
            base_means = [mean([mean(values) for values in repeats.values() if values]) for repeats in bases.values() if any(repeats.values())]
            denominator = len(base_means)
            valid_repeats = sum(sum(bool(values) for values in repeats.values()) for repeats in bases.values())
            risk_set_count = len({(row.base_instance_id, row.repeat_id) for row in rows})
            coverage = (denominator / len({row.base_instance_id for row in rows})) if rows else 0.0
            counter = exclusions.get(metric_id, Counter())
            for key, value in counter.items():
                funnel[key] += value
            if base_means and denominator >= spec.denominator_floor:
                status = MetricStatus.VALID
                estimate = float(mean(base_means))
                low, high = ci.get(metric_id, (None, None))
                reason = None
            else:
                estimate = low = high = None
                statuses = Counter(row.metric.status for row in rows)
                if counter.get("release_gate_failed"):
                    status, reason = MetricStatus.PENDING, "release_gate_failed"
                elif statuses.get(MetricStatus.INVALID):
                    status, reason = MetricStatus.INVALID, "invalid_observation"
                elif statuses.get(MetricStatus.PENDING):
                    status, reason = MetricStatus.PENDING, "pending_evidence"
                elif statuses.get(MetricStatus.NA):
                    status = MetricStatus.NA
                    reason = next((row.metric.na_reason for row in rows if row.metric.na_reason), "empty_support")
                elif counter.get("denominator_below_floor"):
                    status, reason = MetricStatus.NA, "denominator_below_floor"
                else:
                    status, reason = MetricStatus.NA, "empty_support"
            metadata = {
                "dimension": spec.dimension,
                "condition": spec.condition,
                "relation": spec.relation,
                "direction": spec.direction,
                "unit": spec.unit,
                "base_instance_ids": sorted(bases),
                "repeat_count": valid_repeats,
            }
            reduced.append(ReducedMetric(metric_id, status, estimate, low, high, valid_repeats, denominator, risk_set_count, coverage, reason, dict(counter), metadata))
        if release is None:
            catalog_digest = plan_digest = ""
            artifacts: tuple[str, ...] = tuple(sorted({record.artifact_digest for record in records if record.artifact_digest}))
        else:
            catalog_digest, plan_digest, artifacts = release.catalog_digest, release.plan_digest, release.artifact_digests
        return ReductionReport(tuple(reduced), catalog_digest=catalog_digest, plan_digest=plan_digest, artifact_digests=tuple(artifacts), bootstrap_seed=self.bootstrap_seed, coverage={metric.metric_id: {"support": metric.support_count, "denominator": metric.denominator, "risk_set": metric.risk_set_count, "coverage": metric.coverage} for metric in reduced}, exclusion_funnel=dict(funnel))

    def export(self, report: ReductionReport, destination: str | Path) -> tuple[Path, ...]:
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        json_path = destination / "reduced_metrics.json"
        csv_path = destination / "reduced_metrics.csv"
        md_path = destination / "paper_tables.md"
        (destination / "coverage_report.json").write_text(json.dumps({"coverage": report.coverage, "exclusion_funnel": report.exclusion_funnel}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        json_path.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        fields = ["metric_id", "status", "estimate", "ci_low", "ci_high", "support_count", "denominator", "risk_set_count", "coverage", "na_reason"]
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for metric in report.metrics:
                writer.writerow({field: metric.to_dict()[field] for field in fields})
        lines = ["# D1–D6 reduced metrics", "", "| metric | status | estimate | 95% CI | support | denominator | risk set |", "|---|---:|---:|---:|---:|---:|---:|"]
        for metric in report.metrics:
            ci_text = "N/A" if metric.ci_low is None else f"[{metric.ci_low:.6g}, {metric.ci_high:.6g}]"
            estimate = "N/A" if metric.estimate is None else f"{metric.estimate:.6g}"
            lines.append(f"| {metric.metric_id} | {metric.status.value} | {estimate} | {ci_text} | {metric.support_count} | {metric.denominator} | {metric.risk_set_count} |")
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return (json_path, csv_path, md_path, destination / "coverage_report.json")


__all__ = ["REDUCER_VERSION", "MetricReducer", "ReducedMetric", "ReductionRecord", "ReductionReport"]
