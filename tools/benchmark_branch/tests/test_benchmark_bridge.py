import pytest

from benchmark import build_batch_identity, default_experiment_plan
from tools.benchmark_branch import (
    BranchExecutor,
    BranchPlan,
    BranchRunStore,
    DummyAdapter,
    branch_plan_from_batch,
)
from tools.benchmark_branch.validation import summarize_run, validate_run


def _batch(*, strict: bool):
    plan = default_experiment_plan()
    cell = next(item for item in plan.iter_cells() if item.strict_pair_required is strict)
    return build_batch_identity(cell, plan=plan)


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
    assert {job.checkpoint_digest for job in plan.jobs()} == {batch.checkpoint.digest}
    assert {job.metadata["checkpoint_round"] for job in plan.jobs()} == {batch.cell.checkpoint_round}


def test_bridge_does_not_force_fact_only_batch_into_four_arms():
    batch = _batch(strict=False)
    plan = branch_plan_from_batch(batch, execution_scope="protocol_synthetic")
    assert len(plan.jobs()) == 1
    assert plan.arms == ("fact",)
    assert plan.logical_branch_count == 1


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
