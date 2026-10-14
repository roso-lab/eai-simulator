from __future__ import annotations

import json

from benchmark.metrics import MetricRegistry, MetricStatus
from benchmark.reducer import MetricReducer, ReductionRecord


def _record(base: str, repeat: str, value: float, *, metric_id: str = "D1.initial_plan_validity", condition: str = "nominal", status: MetricStatus = MetricStatus.VALID, relation: str | None = None, digest: str = "a") -> ReductionRecord:
    registry = MetricRegistry()
    observation = registry.make_observation(metric_id, status=status, value=value if status is MetricStatus.VALID else None, denominator=1 if status is MetricStatus.VALID else None, evidence_hash="e", na_reason="evidence_missing" if status is MetricStatus.NA else None)
    return ReductionRecord(observation, metric_id, base, repeat, "fact", condition, relation, digest, "ready", True)


def test_repeats_are_nested_and_base_instances_are_equally_weighted() -> None:
    reducer = MetricReducer(diagnostic=True, expected_repeat_ids=("r1", "r2", "r3"), n_bootstrap=20, bootstrap_seed=4)
    # Both bases have exactly the frozen three repeats.
    report = reducer.reduce([_record("base-1", "r1", 1.0), _record("base-1", "r2", 1.0), _record("base-1", "r3", 0.4), _record("base-2", "r1", 0.0), _record("base-2", "r2", 0.0), _record("base-2", "r3", 0.0)])
    metric = report.by_metric("D1.initial_plan_validity")
    assert metric.status is MetricStatus.VALID
    assert metric.estimate == 0.4  # (.8 + 0) / 2, not treating repeats as independent units
    assert metric.denominator == 2
    assert metric.support_count == 6
    assert metric.coverage == 1.0


def test_pending_and_na_are_not_converted_to_zero() -> None:
    reducer = MetricReducer(diagnostic=True, expected_repeat_ids=("r1", "r2", "r3"), n_bootstrap=0)
    pending = _record("base-1", "r1", 0.0, status=MetricStatus.PENDING)
    na = _record("base-2", "r1", 0.0, status=MetricStatus.NA)
    report = reducer.reduce([pending, na])
    metric = report.by_metric("D1.initial_plan_validity")
    assert metric.estimate is None
    assert metric.status is MetricStatus.PENDING
    assert metric.exclusions["status_pending"] == 1
    assert metric.exclusions["status_N/A"] == 1


def test_relation_mismatch_fails_closed() -> None:
    reducer = MetricReducer(diagnostic=True, expected_repeat_ids=("r1", "r2", "r3"), n_bootstrap=0)
    row = _record("base-1", "r1", 1.0, metric_id="D3.p2_handoff_e2e", relation="P4")
    report = reducer.reduce([row])
    metric = report.by_metric("D3.p2_handoff_e2e")
    assert metric.status is MetricStatus.NA
    assert metric.exclusions["relation_mismatch"] == 1


def test_bootstrap_is_deterministic_and_exports(tmp_path) -> None:
    records = [_record(base, repeat, value) for base, value in (("base-1", 1.), ("base-2", 0.)) for repeat in ("r1", "r2", "r3")]
    reducer = MetricReducer(diagnostic=True, expected_repeat_ids=("r1", "r2", "r3"), n_bootstrap=30, bootstrap_seed=11)
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


def test_incomplete_repeats_and_bootstrap_cannot_bypass_support_gate():
    reducer = MetricReducer(diagnostic=True, expected_repeat_ids=("r1", "r2", "r3"))
    rows = [_record("complete", r, 1.) for r in ("r1", "r2", "r3")]
    rows.append(_record("incomplete", "r1", 0.))
    metric = reducer.reduce(rows).by_metric("D1.initial_plan_validity")
    assert metric.estimate == 1.
    assert metric.denominator == 1 and metric.coverage == .5
    assert metric.ci_low is None and metric.ci_high is None
    assert reducer.bootstrap(rows)[metric.metric_id] == (None, None)
    assert MetricReducer().bootstrap(rows) == {}


def test_mixed_strata_and_substring_conditions_are_rejected():
    reducer = MetricReducer(diagnostic=True, expected_repeat_ids=("r1", "r2", "r3"))
    rows = [_record("base", r, 1.) for r in ("r1", "r2", "r3")]
    from dataclasses import replace
    rows[1] = replace(rows[1], metadata={"system_id": "other"})
    assert reducer.reduce(rows).by_metric("D1.initial_plan_validity").estimate is None
    assert reducer.bootstrap(rows) == {}
    row = _record("base", "r1", 1., condition="nominal-hidden")
    assert reducer.reduce([row]).by_metric(row.metric.metric_id).exclusions["condition_mismatch"] == 1
