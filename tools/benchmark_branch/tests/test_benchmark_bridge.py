from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from tools.benchmark_branch import (
    BranchExecutor,
    BranchPlan,
    BranchRunStore,
    DummyAdapter,
    branch_plan_from_batch,
)
from tools.benchmark_branch.validation import summarize_run, validate_run


@dataclass(frozen=True)
class _Checkpoint:
    checkpoint_id: str = "cp-1"
    digest: str = "checkpoint-digest"
    schema: str = "benchmark.checkpoint.v1"
    artifact_uri: str = "memory://checkpoint"
    step: int = 10


def _batch(*, strict: bool):
    repeat_id = "r2"
    checkpoint = _Checkpoint()
    cell = SimpleNamespace(
        repeat_id=repeat_id, checkpoint_round="round-1",
        strict_pair_required=strict,
    )
    anchor = SimpleNamespace(anchor_id="anchor-1", scene_key="scene-a")
    roles = ("fact", "reference", "sham", "intervention") if strict else ("fact",)
    branches = []
    for role in roles:
        key = SimpleNamespace(
            base_instance_id="base-1", system_id="system-a",
            condition_id="nominal", repeat_id=repeat_id,
            checkpoint_round="round-1", untreated_history_id="history-1",
            branch_role=role,
        )
        branches.append(SimpleNamespace(
            run_key=key, treatment_id=f"treatment-{role}",
            authority="benchmark.protocol", non_target_budget={"communication": 1},
            checkpoint=checkpoint,
        ))
    return SimpleNamespace(
        manifest_digest="manifest-digest", plan_digest="plan-digest",
        anchor_digest="anchor-digest", anchor_id="anchor-1", anchor=anchor,
        cell=cell, checkpoint=checkpoint, branches=tuple(branches),
        strict_pair_required=strict, base_instance_id="base-1",
        metadata={},
        manifest=lambda: {"anchor_digest": "anchor-digest"},
    )


def test_bridge_preserves_benchmark_run_keys_and_manifest_digest():
    batch = _batch(strict=True)
    plan = branch_plan_from_batch(batch, execution_scope="protocol_synthetic")
    assert len(plan.jobs()) == len(batch.branches) == 4
    assert {job.branch_type for job in plan.jobs()} == {"fact", "reference", "sham", "intervention"}
    assert {job.base_instance_id for job in plan.jobs()} == {batch.base_instance_id}
    assert {job.repeat_id for job in plan.jobs()} == {batch.cell.repeat_id}
    assert {job.metadata["benchmark_manifest_digest"] for job in plan.jobs()} == {batch.manifest_digest}
    assert all(job.metadata["benchmark_authority"] == "benchmark" for job in plan.jobs())
    assert {job.metadata["treatment_id"] for job in plan.jobs()} == {
        branch.treatment_id for branch in batch.branches
    }
    assert {job.slot for job in plan.jobs()} == {0}
    assert {job.checkpoint_digest for job in plan.jobs()} == {batch.checkpoint.digest}
    assert {job.metadata["checkpoint_round"] for job in plan.jobs()} == {batch.cell.checkpoint_round}


def test_bridge_does_not_force_fact_only_batch_into_four_arms():
    batch = _batch(strict=False)
    plan = branch_plan_from_batch(batch, execution_scope="protocol_synthetic")
    assert len(plan.jobs()) == 1
    assert plan.arms == ("fact",)
    assert plan.logical_branch_count == 1
    assert {job.slot for job in plan.jobs()} == {0}


def test_retry_preserves_base_instance_and_repeat_identity():
    job = BranchPlan(slots=1).jobs()[0]
    retry = job.with_attempt(3)
    assert retry.base_instance_id == job.base_instance_id
    assert retry.repeat_id == job.repeat_id
    assert retry.logical_branch_id == job.logical_branch_id


def test_legacy_numeric_repeat_is_normalized_on_resume():
    job = BranchPlan(slots=1).jobs()[0]
    restored = type(job).from_mapping({**job.identity(), "repeat_id": 0})
    assert restored.repeat_id == "0"


def test_dummy_adapter_cannot_be_promoted_to_measured(tmp_path):
    plan = BranchPlan(slots=1, execution_scope="measured")
    store = BranchRunStore.create(
        tmp_path, plan, requested_max_workers=4,
        execution_scope="measured", adapter="dummy",
    )
    with pytest.raises(ValueError, match="does not support execution_scope='measured'"):
        BranchExecutor(plan, DummyAdapter(steps=1), store, max_workers=4).run()
    manifest = store.manifest
    assert manifest["collector_status"] == "rejected"
    assert manifest["inference_status"] == "unsupported"


def test_collector_summary_never_claims_formal_pairing(tmp_path):
    plan = BranchPlan(slots=1)
    store = BranchRunStore.create(
        tmp_path, plan, requested_max_workers=4,
        execution_scope="protocol_synthetic", adapter="dummy",
    )
    BranchExecutor(plan, DummyAdapter(steps=1), store, max_workers=4).run()
    validation = validate_run(tmp_path)
    summary = summarize_run(tmp_path)
    assert validation["metrics"]["strict_pair_claim_status"] == "unsupported"
    assert validation["metrics"]["formal_pairing_eligible"] is False
    assert summary["inference_status"] == "unsupported"


def test_explicit_benchmark_plan_survives_store_resume(tmp_path):
    batch = _batch(strict=True)
    plan = branch_plan_from_batch(batch, execution_scope="protocol_synthetic")
    store = BranchRunStore.create(
        tmp_path, plan, requested_max_workers=4,
        execution_scope="protocol_synthetic", adapter="dummy",
    )
    result = BranchExecutor(plan, DummyAdapter(steps=1), store, max_workers=4).run()
    assert result["completed"] == 4
    resumed = BranchRunStore.resume(tmp_path)
    assert resumed.plan.logical_branch_count == 4
    assert {job.branch_id for job in resumed.plan.jobs()} == {job.branch_id for job in plan.jobs()}
