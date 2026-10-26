from tools.benchmark_branch.oracle_predicates import (
    PredicateStreams, MilestoneEvent, TaskEvent, HoldInterval, judge_anchor,
)


def streams(end=15000, handoff=2, carriers=2):
    return PredicateStreams(
        milestones=[MilestoneEvent("relay_started", 1), MilestoneEvent("relay_complete", 3),
                    MilestoneEvent("heavy_carry_complete", 20), MilestoneEvent("accepted", end)],
        task_events=[TaskEvent("transition_handoff_observed", "transition_node", handoff),
                     TaskEvent("bilateral_support_established", "heavy_object", 10)],
        hold_intervals=[HoldInterval("heavy_object", str(i), 10, 20) for i in range(carriers)],
        integrity_confirmed=True,
    )


def test_room06_three_predicates_and_inclusive_deadline():
    verdicts = judge_anchor("liv-t-04", streams())
    assert len(verdicts) == 3
    assert all(v.status.value == "satisfied" for v in verdicts)
    assert judge_anchor("liv-t-04", streams(end=15001))[-1].status.value == "violated"


def test_room06_handoff_strict_and_support_safety():
    assert judge_anchor("liv-t-04", streams(handoff=1))[0].status.value == "violated"
    support = judge_anchor("liv-t-04", streams(carriers=1))[1]
    assert support.status.value == "violated"
    assert support.evidence["safety_class"] is True
