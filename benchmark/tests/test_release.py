from __future__ import annotations

from types import SimpleNamespace

from benchmark.experiment_plan import default_experiment_plan
from benchmark.metrics import MetricRegistry, MetricStatus
from benchmark.protocol import BranchRole
from benchmark.release import ReleaseIndex, ReleaseStatus
from benchmark.task_catalog import load_default_catalog


def _artifact(digest: str = "artifact-1") -> SimpleNamespace:
    return SimpleNamespace(
        payload={
            "artifact_digest": digest,
            "run_key": {
                "base_instance_id": "base-1",
                "system_id": "EMOS-adapted-to-EAI",
                "condition_id": "nominal",
                "repeat_id": "r1",
                "checkpoint_round": "first-eligible",
                "branch_role": "fact",
            },
        }
    )


def test_release_index_preserves_pending_and_na_without_zero_fill() -> None:
    plan = default_experiment_plan()
    registry = MetricRegistry()
    index = ReleaseIndex(plan.catalog, metric_registry=registry, plan_digest="plan-1")
    index.add_artifact(_artifact())
    anchor = next(item for item in plan.catalog if item.anchor_id == "prod-p2-01")
    observation = registry.make_observation(
        "D3.p2_handoff_e2e",
        status=MetricStatus.PENDING,
        support_count=1,
        metadata={"reason": "physical_evidence_required"},
    )
    index.add_observation(
        observation,
        anchor_id=anchor.anchor_id,
        condition_id="nominal",
        branch_role=BranchRole.FACT,
        base_instance_id="base-1",
        repeat_id="r1",
        risk_set="P2 relation-eligible repeats",
        artifact_digest="artifact-1",
    )
    report = index.finalize(expected_plan_digest="plan-1")
    assert report.status is ReleaseStatus.PENDING
    assert report.records[0].observation.value is None
    assert report.coverage["production/P2/common"]["pending"] == 1


def test_release_index_admits_explicit_na_and_reports_missing_artifacts_as_gate() -> None:
    catalog = load_default_catalog()
    registry = MetricRegistry()
    index = ReleaseIndex(catalog, metric_registry=registry, plan_digest="plan-1")
    anchor = next(item for item in catalog if item.anchor_id == "prod-p2-01")
    observation = registry.make_observation(
        "D5.p2_transfer_gap",
        status=MetricStatus.NA,
        na_reason="common_anchor_uncertified",
    )
    index.add_observation(
        observation,
        anchor_id=anchor.anchor_id,
        condition_id="production_to_service_p2",
        branch_role=BranchRole.FACT,
        base_instance_id="base-1",
        repeat_id="r1",
    )
    report = index.finalize(expected_plan_digest="plan-1")
    assert report.status is ReleaseStatus.READY
    assert report.records[0].observation.na_reason == "common_anchor_uncertified"
    assert report.gates["artifacts_indexed"] is False


def test_release_index_rejects_metric_strata_mismatch() -> None:
    catalog = load_default_catalog()
    registry = MetricRegistry()
    index = ReleaseIndex(catalog, metric_registry=registry, plan_digest="plan-1")
    anchor = next(item for item in catalog if item.anchor_id == "prod-p2-01")
    observation = registry.make_observation(
        "D3.p2_handoff_e2e",
        status=MetricStatus.PENDING,
    )
    from benchmark.release import IndexedMetric, ReleaseError

    row = IndexedMetric(
        observation=observation,
        anchor_id=anchor.anchor_id,
        domain="service",
        primary_label=anchor.primary_label,
        relation_signature=anchor.relation_signature,
        p3_subtype=anchor.p3_subtype,
        condition_id="nominal",
        branch_role=BranchRole.FACT,
        base_instance_id="base-1",
        repeat_id="r1",
    )
    try:
        index.add_metric(row)
    except ReleaseError:
        pass
    else:
        raise AssertionError("strata mismatch must be rejected")
