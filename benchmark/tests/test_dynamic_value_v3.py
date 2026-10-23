import pytest

from benchmark.dynamic_value_v3 import (
    DensitySegmentV3,
    DynamicValueRegistrationV3,
    DynamicValueTimelineV3,
    OutcomeStateV3,
    OutcomeValueV3,
    ServiceStateV3,
    TerminalEventV3,
    evaluate_dynamic_value_v3,
)
from benchmark.records_v3 import RecordStatus


def timeline(item_id, *, through, outcomes=(), service=(), terminals=()):
    return DynamicValueTimelineV3(
        item_id, through, tuple(outcomes), tuple(service), tuple(terminals),
        "operation", "feasibility", "progress",
    )


def test_deadline_uses_absolute_clock_and_duplicate_updates_do_not_add_weight():
    registration = DynamicValueRegistrationV3(
        "delivery", "deadline", 5, 12,
        (OutcomeValueV3("quality", .7, True), OutcomeValueV3("receipt", .3, True)),
        deadline_tick=10,
    )
    result = evaluate_dynamic_value_v3(registration, timeline(
        "delivery", through=12,
        outcomes=(
            OutcomeStateV3("quality", 5, False, "q0"),
            OutcomeStateV3("quality", 6, True, "q1"),
            OutcomeStateV3("quality", 7, True, "q2"),
            OutcomeStateV3("receipt", 5, False, "r0"),
            OutcomeStateV3("receipt", 11, True, "r1"),
        ),
    ))
    assert result.status is RecordStatus.VALID
    assert result.legal_completion_fraction == pytest.approx(.7)
    assert result.service_fraction is None
    assert result.confirmation_tick == 6
    assert result.first_event_cause == "resume"


def test_time_value_integrates_registered_density_and_stops_after_invalidation():
    with pytest.raises(ValueError, match="documented task purpose"):
        DynamicValueRegistrationV3(
            "result", "time_valued", 0, 10, (OutcomeValueV3("usable", 1.),),
            deadline_tick=10,
            density=(DensitySegmentV3(0, 5, .05), DensitySegmentV3(5, 10, .15)),
        )
    registration = DynamicValueRegistrationV3(
        "result", "time_valued", 0, 10, (OutcomeValueV3("usable", 1.),),
        deadline_tick=10,
        density=(DensitySegmentV3(0, 5, .05), DensitySegmentV3(5, 10, .15)),
        density_purpose="later observations carry higher operational value",
    )
    result = evaluate_dynamic_value_v3(registration, timeline(
        "result", through=10,
        outcomes=(
            OutcomeStateV3("usable", 0, False, "s0"),
            OutcomeStateV3("usable", 4, True, "s1"),
            OutcomeStateV3("usable", 8, False, "s2"),
        ),
    ))
    assert result.legal_completion_fraction == 0
    assert result.service_fraction == pytest.approx(.5)
    assert result.confirmation_tick == 4


def test_continuous_early_terminal_keeps_full_denominator_and_tie_priority():
    registration = DynamicValueRegistrationV3(
        "relay", "continuous_supply", 5, 15, (OutcomeValueV3("coverage", 1.),),
        demand_start_tick=5, demand_end_tick=15, stability_ticks=3,
    )
    result = evaluate_dynamic_value_v3(registration, timeline(
        "relay", through=8,
        service=(ServiceStateV3(5, True, "service-on"),),
        terminals=(TerminalEventV3(8, "infeas", "exhausted", policy_exhaustion=True),),
    ))
    assert result.status is RecordStatus.VALID
    assert result.service_fraction == pytest.approx(.3)
    assert result.legal_completion_fraction == 0
    assert result.confirmation_tick == 8
    assert result.first_event_cause == "fail"
    assert result.tied_first_causes == ("fail", "resume")
    assert result.event_records[0]["declared_cause"] == "infeas"
    assert result.event_records[0]["note"] == "policy_exhaustion_cannot_be_exogenous"


def test_exact_tick_terminal_priority_preserves_all_tied_events():
    registration = DynamicValueRegistrationV3(
        "work", "deadline", 5, 10, (OutcomeValueV3("done", 1., True),),
        deadline_tick=10,
    )
    result = evaluate_dynamic_value_v3(registration, timeline(
        "work", through=8,
        outcomes=(OutcomeStateV3("done", 5, False, "d0"), OutcomeStateV3("done", 8, True, "d1")),
        terminals=(
            TerminalEventV3(8, "safe", "safe", certified=True),
            TerminalEventV3(8, "infeas", "infeas", certified=True),
            TerminalEventV3(8, "fail", "fail"),
        ),
    ))
    assert result.first_event_cause == "fail"
    assert result.tied_first_causes == ("fail", "infeas", "safe", "resume")
    assert len(result.event_records) == 4
    # Exact-tick terminal priority prevents that milestone from erasing the
    # failure or becoming post-terminal achievement.
    assert result.legal_completion_fraction == 0


def test_missing_or_unknown_timeline_coverage_is_not_zero_filled():
    registration = DynamicValueRegistrationV3(
        "work", "time_valued", 0, 10, (OutcomeValueV3("done", 1.),),
        deadline_tick=10, density=(DensitySegmentV3(0, 10, .1),),
    )
    short = evaluate_dynamic_value_v3(registration, timeline(
        "work", through=7, outcomes=(OutcomeStateV3("done", 0, False, "d0"),),
    ))
    assert short.status is RecordStatus.INCOMPLETE
    assert short.legal_completion_fraction is None
    unknown = evaluate_dynamic_value_v3(registration, timeline(
        "work", through=10,
        outcomes=(OutcomeStateV3("done", 0, False, "d0"), OutcomeStateV3("done", 5, None, "missing")),
    ))
    assert unknown.status is RecordStatus.INCOMPLETE
    assert unknown.reason == "outcome_achievement_state_unknown"


def test_unverified_terminal_classification_stays_incomplete():
    registration = DynamicValueRegistrationV3(
        "work", "deadline", 0, 10, (OutcomeValueV3("done", 1.),), deadline_tick=10,
    )
    result = evaluate_dynamic_value_v3(registration, timeline(
        "work", through=5,
        outcomes=(OutcomeStateV3("done", 0, False, "d0"),),
        terminals=(TerminalEventV3(5, "infeas", "uncertified"),),
    ))
    assert result.status is RecordStatus.INCOMPLETE
    assert result.reason == "terminal_infeasibility_requires_exogenous_certificate"
    assert result.physical_qualified is False


def test_current_paper_freezes_beta_and_keeps_unclassified_terminal_receipts():
    with pytest.raises(ValueError, match="beta=1/2"):
        DynamicValueRegistrationV3(
            "work", "deadline", 0, 10, (OutcomeValueV3("done", 1.),),
            deadline_tick=10, beta=.7,
        )
    registration = DynamicValueRegistrationV3(
        "work", "deadline", 0, 10, (OutcomeValueV3("done", 1.),), deadline_tick=10,
    )
    result = evaluate_dynamic_value_v3(registration, timeline(
        "work", through=5, outcomes=(OutcomeStateV3("done", 0, False, "d0"),),
        terminals=(TerminalEventV3(3, "infeas", "u"), TerminalEventV3(4, "safe", "s")),
    ))
    assert result.status is RecordStatus.INCOMPLETE
    assert [row["evidence_digest"] for row in result.event_records] == ["u", "s"]


def test_persistent_outcome_unknown_before_terminal_stays_unknown():
    rule = DynamicValueRegistrationV3("work", "deadline", 0, 10,
        (OutcomeValueV3("done", 1., True),), deadline_tick=10)
    result = evaluate_dynamic_value_v3(rule, timeline("work", through=5,
        outcomes=(OutcomeStateV3("done", 0, None, "missing"),),
        terminals=(TerminalEventV3(5, "fail", "failed"),)))
    assert result.status is RecordStatus.INCOMPLETE
    assert result.legal_completion_fraction is None


def test_existing_onset_outcome_and_post_window_updates_do_not_confirm_resume():
    rule = DynamicValueRegistrationV3("work", "deadline", 5, 10,
        (OutcomeValueV3("done", 1.),), deadline_tick=10)
    for states in ((OutcomeStateV3("done", 5, True, "already-done"),),
                   (OutcomeStateV3("done", 5, False, "initial"),
                    OutcomeStateV3("done", 11, True, "outside-window"))):
        result = evaluate_dynamic_value_v3(rule, timeline("work", through=12, outcomes=states))
        assert result.confirmation_tick is None


def test_interruption_at_closed_stability_endpoint_delays_confirmation():
    rule = DynamicValueRegistrationV3("relay", "continuous_supply", 0, 10,
        (OutcomeValueV3("coverage", 1.),), demand_start_tick=0,
        demand_end_tick=10, stability_ticks=3)
    result = evaluate_dynamic_value_v3(rule, timeline("relay", through=10, service=(
        ServiceStateV3(0, True, "on"), ServiceStateV3(3, False, "gap"),
        ServiceStateV3(4, True, "back"))))
    assert result.confirmation_tick == 7
    assert result.service_fraction == .9
    assert result.legal_completion_fraction == 1


def test_uniform_value_before_use_and_exact_deadline_and_time_scaling():
    for scale in (1, 10):
        rule = DynamicValueRegistrationV3("result", "time_valued", 0, 10*scale,
            (OutcomeValueV3("done", 1.),), deadline_tick=10*scale,
            density=(DensitySegmentV3(0, 5*scale, 0.), DensitySegmentV3(5*scale, 10*scale, .2/scale)),
            density_purpose="result used only during final five time units")
        for completion, phi in ((3, 1), (5, 1), (7, .6), (10, 0)):
            result = evaluate_dynamic_value_v3(rule, timeline("result", through=10*scale,
                outcomes=(OutcomeStateV3("done", 0, False, "initial"),
                          OutcomeStateV3("done", completion*scale, True, "accepted"))))
            assert result.legal_completion_fraction == 1
            assert result.service_fraction == pytest.approx(phi)
