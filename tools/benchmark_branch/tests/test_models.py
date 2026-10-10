from tools.benchmark_branch import BRANCH_ROLES, BranchPlan


def test_formal_plan_expands_to_four_arms():
    plan = BranchPlan(slots=32)
    assert plan.pair_slots == 32
    assert plan.logical_branch_count == 128
    assert len(plan.jobs()) == 128
    assert {job.branch_type for job in plan.jobs()} == set(BRANCH_ROLES)
    assert len({job.branch_id for job in plan.jobs()}) == 128
