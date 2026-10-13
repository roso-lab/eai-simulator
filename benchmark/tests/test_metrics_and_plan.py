from __future__ import annotations

import json

import pytest

from benchmark.experiment_plan import default_experiment_plan
from benchmark.metrics import MetricError, MetricRegistry, MetricStatus
from benchmark.task_catalog import load_default_catalog


def test_metric_registry_keeps_d5_na_until_common_anchors_are_certified() -> None:
    catalog = load_default_catalog()
    registry = MetricRegistry()
    status, reason = registry.support_status("D5.p2_transfer_gap", catalog)
    assert status is MetricStatus.NA
    assert reason == "common_anchor_uncertified"


def test_metric_observation_rejects_na_as_zero() -> None:
    registry = MetricRegistry()
    with pytest.raises(MetricError):
        registry.make_observation(
            "D3.p2_handoff_e2e",
            status=MetricStatus.NA,
            value=0.0,
            na_reason="empty_support",
        )


def test_plan_freezes_tick_repeats_and_strict_d5_pair() -> None:
    plan = default_experiment_plan()
    assert plan.tick_dt_s == 0.02
    assert plan.repeats == 3
    d5 = next(item for item in plan.phases if item.ordinal == 5)
    assert d5.conditions[0].strict_pair_required
    assert set(d5.conditions[0].branch_roles) == {"fact", "reference", "sham", "intervention"}
    assert len(plan.anchors_for(d5)) == 3
    json.dumps(plan.manifest())


def test_plan_expands_d6_only_over_typical_anchors() -> None:
    plan = default_experiment_plan()
    d6 = next(item for item in plan.phases if item.ordinal == 6)
    assert len(plan.anchors_for(d6)) == 9
    assert all(item.anchor_id.startswith(("prod-t-", "svc-t-", "liv-t-")) for item in plan.catalog if item.typical_or_common == "typical")
