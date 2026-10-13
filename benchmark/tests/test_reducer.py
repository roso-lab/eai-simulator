from __future__ import annotations

import json

from benchmark.metrics import MetricRegistry, MetricStatus
from benchmark.reducer import MetricReducer, ReductionRecord


def _record(base: str, repeat: str, value: float, *, metric_id: str = "D1.initial_plan_validity", condition: str = "nominal", status: MetricStatus = MetricStatus.VALID, relation: str | None = None, digest: str = "a") -> ReductionRecord:
    registry = MetricRegistry()
    observation = registry.make_observation(metric_id, status=status, value=value if status is MetricStatus.VALID else None, denominator=1 if status is MetricStatus.VALID else None, evidence_hash="e", na_reason="evidence_missing" if status is MetricStatus.NA else None)
    return ReductionRecord(observation, metric_id, base, repeat, "fact", condition, relation, digest, "ready", True)


def test_repeats_are_nested_and_base_instances_are_equally_weighted() -> None:
    reducer = MetricReducer(n_bootstrap=20, bootstrap_seed=4)
    # base-1 has three repeats (mean .8), base-2 has one repeat (0.0).
    report = reducer.reduce([_record("base-1", "r1", 1.0), _record("base-1", "r2", 1.0), _record("base-1", "r3", 0.4), _record("base-2", "r1", 0.0)])
    metric = report.by_metric("D1.initial_plan_validity")
    assert metric.status is MetricStatus.VALID
    assert metric.estimate == 0.4  # (.8 + 0) / 2, not (2.4 + 0) / 4
    assert metric.denominator == 2
    assert metric.support_count == 4
    assert metric.coverage == 1.0


def test_pending_and_na_are_not_converted_to_zero() -> None:
    reducer = MetricReducer(n_bootstrap=0)
    pending = _record("base-1", "r1", 0.0, status=MetricStatus.PENDING)
    na = _record("base-2", "r1", 0.0, status=MetricStatus.NA)
    report = reducer.reduce([pending, na])
    metric = report.by_metric("D1.initial_plan_validity")
    assert metric.estimate is None
    assert metric.status is MetricStatus.PENDING
    assert metric.exclusions["status_pending"] == 1
    assert metric.exclusions["status_N/A"] == 1


def test_relation_mismatch_fails_closed() -> None:
    reducer = MetricReducer(n_bootstrap=0)
    row = _record("base-1", "r1", 1.0, metric_id="D3.p2_handoff_e2e", relation="P4")
    report = reducer.reduce([row])
    metric = report.by_metric("D3.p2_handoff_e2e")
    assert metric.status is MetricStatus.NA
    assert metric.exclusions["relation_mismatch"] == 1


def test_bootstrap_is_deterministic_and_exports(tmp_path) -> None:
    records = [_record("base-1", "r1", 1.0), _record("base-2", "r1", 0.0)]
    reducer = MetricReducer(n_bootstrap=30, bootstrap_seed=11)
    first = reducer.reduce(records)
    second = reducer.reduce(records)
    assert first.by_metric("D1.initial_plan_validity").ci_low == second.by_metric("D1.initial_plan_validity").ci_low
    paths = reducer.export(first, tmp_path)
    assert all(path.exists() for path in paths)
    payload = json.loads((tmp_path / "reduced_metrics.json").read_text(encoding="utf-8"))
    assert payload["metrics"]


def test_release_gate_failure_does_not_produce_point_estimate() -> None:
    reducer = MetricReducer(n_bootstrap=0)
    row = _record("base-1", "r1", 1.0)
    report = reducer.reduce([row], release=type("Release", (), {"inferentially_ready": False, "catalog_digest": "", "plan_digest": "", "artifact_digests": ()})())
    metric = report.by_metric("D1.initial_plan_validity")
    assert metric.status is MetricStatus.PENDING
    assert metric.estimate is None
