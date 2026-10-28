"""Author V3: logical holders, never physical contacts, on all four anchors."""
import pytest

from tools.benchmark_branch.oracle_predicates import (
    HoldInterval, PredicateStreams, TaskEvent, MilestoneEvent, judge_anchor,
)

CASES = [('liv-p3-01', 'pallet', 'pallet_requires_two_carriers'),
         ('prod-t-01', 'long_pallet', 'pallet_requires_two_carriers'),
         ('svc-p3-01', 'visitor_target', 'pallet_requires_two_carriers'),
         ('liv-t-04', 'heavy_object', 'heavy_carry_joint_hold')]


def verdict(anchor, obj, predicate, intervals, integrity=True):
    s = PredicateStreams(
        hold_intervals=[HoldInterval(obj, *interval) for interval in intervals],
        task_events=[TaskEvent('arrive_in_position', 'left_carrier', 10),
                     TaskEvent('arrive_in_position', 'right_carrier', 10),
                     TaskEvent('joint_carry_start', obj, 10),
                     TaskEvent('bilateral_support_established', obj, 10)],
        milestones=[MilestoneEvent('joint_carry_complete', 20),
                    MilestoneEvent('heavy_carry_complete', 20)],
        integrity_confirmed=integrity)
    return next(v for v in judge_anchor(anchor, s) if v.predicate_id == predicate)


@pytest.mark.parametrize('anchor,obj,predicate', CASES)
@pytest.mark.parametrize('intervals,expected', [
    ([('a', 10, 20), ('b', 10, 20)], 'satisfied'),
    ([('a', 10, 20), ('b', 10, 15), ('c', 15, 20)], 'satisfied'),
    ([('a', 10, 20)], 'violated'),
    ([('a', 10, 20), ('b', 10, 15), ('c', 16, 20)], 'violated'),
    ([('a', 10, 20), ('a', 10, 20)], 'violated'),
])
def test_joint_holders_and_substitution(anchor, obj, predicate, intervals, expected):
    result = verdict(anchor, obj, predicate, intervals)
    assert result.status.value == expected
    assert result.evidence['safety_class'] is True


@pytest.mark.parametrize('anchor,obj,predicate', CASES)
def test_missing_hold_data_fails_closed(anchor, obj, predicate):
    result = verdict(anchor, obj, predicate, [], integrity=False)
    assert result.status.value == 'data_insufficient'
    assert result.cause.value == 'evaluator_record_missing'


def test_old_physical_contact_stream_is_not_silently_reinterpreted():
    with pytest.raises(TypeError):
        PredicateStreams(contact_intervals=[])
