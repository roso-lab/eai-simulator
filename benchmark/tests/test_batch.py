import copy

import pytest

from benchmark.batch import (
    BatchContractError,
    BatchIdentityIndex,
    build_batch_identity,
    build_batch_manifest,
    resume_batch,
)
from benchmark.experiment_plan import ExperimentCell, default_experiment_plan


def _plan_and_cell(*, strict: bool = False):
    plan = default_experiment_plan()
    cell = next(cell for cell in plan.iter_cells() if cell.strict_pair_required is strict)
    return plan, cell


def test_batch_identity_and_manifest_digest_are_stable() -> None:
    plan, cell = _plan_and_cell()
    first = build_batch_identity(cell, plan=plan)
    second = build_batch_identity(cell, plan=plan)
    assert first.base_instance_id == second.base_instance_id
    assert first.manifest_digest == second.manifest_digest
    assert first.manifest() == second.manifest()
    assert first.base_run_key.base_instance_id == first.base_instance_id
    assert first.checkpoint_ref.digest == first.checkpoint.digest
    assert len(first.branch_manifest) == len(first.branches)
    assert build_batch_manifest(cell, plan=plan) == first.manifest()


def test_index_ensure_is_idempotent_without_writing_output() -> None:
    plan, cell = _plan_and_cell()
    index = BatchIdentityIndex(plan)
    first = index.ensure(cell)
    second = index.ensure(cell)
    assert first is second
    assert len(index) == 1
    assert index.get(first.manifest_digest) is first


def test_resume_recomputes_existing_manifest_and_rejects_mutation() -> None:
    plan, cell = _plan_and_cell(strict=True)
    batch = build_batch_identity(cell, plan=plan)
    resumed = resume_batch(batch.manifest(), plan=plan)
    assert resumed.manifest_digest == batch.manifest_digest
    assert resumed.strict_pair_gate["status"] == "pending"
    mutated = copy.deepcopy(batch.manifest())
    mutated["cell"]["condition_id"] = "tampered"
    with pytest.raises(BatchContractError, match="digest mismatch"):
        resume_batch(mutated, plan=plan)


def test_strict_pair_requires_complete_four_branch_roles() -> None:
    plan, cell = _plan_and_cell(strict=True)
    incomplete = ExperimentCell(
        phase_id=cell.phase_id,
        dimension=cell.dimension,
        anchor_id=cell.anchor_id,
        condition_id=cell.condition_id,
        repeat_id=cell.repeat_id,
        checkpoint_round=cell.checkpoint_round,
        branch_roles=("fact", "reference", "sham"),
        treatment_id=cell.treatment_id,
        strict_pair_required=True,
        evidence_required=cell.evidence_required,
    )
    with pytest.raises(BatchContractError, match="exactly one fact"):
        build_batch_identity(incomplete, plan=plan)


def test_strict_pair_manifest_retains_roles_and_gate() -> None:
    plan, cell = _plan_and_cell(strict=True)
    batch = build_batch_identity(cell, plan=plan)
    assert [branch.run_key.branch_role.value for branch in batch.branches] == [
        "fact",
        "reference",
        "sham",
        "intervention",
    ]
    assert batch.strict_pair_gate["required"] is True
    assert batch.strict_pair_gate["status"] == "pending"
    assert batch.strict_pair_gate["required_roles"] == ["fact", "reference", "sham", "intervention"]


def test_base_is_independent_of_treatment_system_repeat_and_checkpoint():
    from dataclasses import replace
    plan, cell = _plan_and_cell()
    first = build_batch_identity(cell, plan=plan)
    candidates = [c for c in plan.iter_cells() if c.anchor_id == cell.anchor_id]
    assert any(c.repeat_id != cell.repeat_id for c in candidates)
    assert any(c.condition_id != cell.condition_id for c in candidates)
    for candidate in candidates:
        assert build_batch_identity(candidate, plan=plan).base_instance_id == first.base_instance_id
    for changed_plan in (replace(plan, system_id="another-system"), replace(plan, checkpoint_round="later")):
        candidate = replace(cell, checkpoint_round=changed_plan.checkpoint_round)
        assert build_batch_identity(candidate, plan=changed_plan).base_instance_id == first.base_instance_id
    for changes in ({'base_seed': 19}, {'slot': 2}, {'initial_state_digest': 'actual-world'},
                    {'generator_version': 'new-generator'}):
        assert build_batch_identity(replace(cell, **changes), plan=plan).base_instance_id != first.base_instance_id
    with_history = build_batch_identity(replace(cell, untreated_history_id='measured-history'), plan=plan)
    assert with_history.base_instance_id == first.base_instance_id
    assert with_history.base_run_key.untreated_history_id == 'measured-history'
    assert resume_batch(with_history.manifest(), plan=plan).base_run_key.untreated_history_id == 'measured-history'
    assert first.base_run_key.untreated_history_id == ''
