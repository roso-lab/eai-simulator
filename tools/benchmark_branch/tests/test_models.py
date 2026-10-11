from tools.benchmark_branch import BRANCH_ROLES, BranchPlan
import pytest


def test_formal_plan_expands_to_four_arms():
    plan = BranchPlan(slots=32)
    assert plan.pair_slots == 32
    assert plan.logical_branch_count == 128
    assert len(plan.jobs()) == 128
    assert {job.branch_type for job in plan.jobs()} == set(BRANCH_ROLES)
    assert len({job.branch_id for job in plan.jobs()}) == 128


def test_base_identity_excludes_repeats_systems_and_conditions():
    plan = BranchPlan(slots=2, repeats=3, systems=("A", "B"), conditions=("nominal", "stress"))
    jobs = plan.jobs()
    assert len({job.base_instance_id for job in jobs}) == 2
    assert len({job.branch_id for job in jobs}) == 2 * 3 * 2 * 2 * 4
    assert len({job.base_instance_id for job in jobs if job.slot == 0}) == 1
    assert BranchPlan.from_mapping(plan.row()).jobs() == jobs


def test_legacy_base_identity_remains_reconstructible():
    plan = BranchPlan(slots=1, repeats=3, base_identity_version=1)
    assert len({job.base_instance_id for job in plan.jobs()}) == 3
    assert all(job.base_instance_id == job.pair_group_id + "/base" for job in plan.jobs())


@pytest.mark.parametrize("version", [True, "2", 0, 3])
def test_unknown_identity_version_is_rejected(version):
    with pytest.raises(ValueError, match="base_identity_version"):
        BranchPlan(base_identity_version=version)
