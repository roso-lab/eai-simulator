"""Positive and negative tests for the bound oracle predicates.

Every boundary value asserted here comes from the case-author binding record
``benchmark/docs/progress/PREDICATE_BINDING_20261023.zh-CN.md``:
overlap >= 1 tick, reconcile deadline 500 ticks, sync window 250 ticks,
coverage gap <= 50 ticks, carrier contacts >= 2, strict precedence with
touching intervals never overlapping (half-open ticks).
"""

import pytest

from tools.benchmark_branch import oracle_predicates as op
from tools.benchmark_branch.oracle_predicates import (
    CauseCode,
    HoldInterval,
    MilestoneEvent,
    PredicateVerdict,
    PredicateStreams,
    TaskEvent,
    VerdictStatus,
    ZoneInterval,
)


def verdicts_by_predicate(anchor_id, streams):
    return {v.predicate_id: v for v in op.judge_anchor(anchor_id, streams)}


# ---------------------------------------------------------------- prod-p1-01


def test_parallel_overlap_is_diagnostic_only():
    streams = PredicateStreams(
        task_events=[
            TaskEvent("lot_first_pickup", "lot_a", 100),
            TaskEvent("lot_last_scan", "lot_a", 301),
            TaskEvent("lot_first_pickup", "lot_b", 300),
            TaskEvent("lot_last_scan", "lot_b", 500),
        ]
    )
    v = verdicts_by_predicate("prod-p1-01", streams)[
        "lot_a_and_lot_b_may_run_in_parallel"
    ]
    assert v.status is VerdictStatus.SATISFIED
    assert v.evidence["scoring_role"] == "diagnostic"  # V2: not scored
    assert v.evidence["overlap_ratio"] >= 0.0


def test_parallel_disjoint_intervals_violated():
    streams = PredicateStreams(
        task_events=[
            TaskEvent("lot_first_pickup", "lot_a", 100),
            TaskEvent("lot_last_scan", "lot_a", 300),
            TaskEvent("lot_first_pickup", "lot_b", 300),
            TaskEvent("lot_last_scan", "lot_b", 500),
        ]
    )
    v = verdicts_by_predicate("prod-p1-01", streams)[
        "lot_a_and_lot_b_may_run_in_parallel"
    ]
    assert v.status is VerdictStatus.VIOLATED
    assert v.evidence["overlap_ticks"] == 0


def test_parallel_missing_event_is_data_insufficient():
    streams = PredicateStreams(
        task_events=[TaskEvent("lot_first_pickup", "lot_a", 100)]
    )
    v = verdicts_by_predicate("prod-p1-01", streams)[
        "lot_a_and_lot_b_may_run_in_parallel"
    ]
    assert v.status is VerdictStatus.DATA_INSUFFICIENT


def test_reconcile_deadline_boundary_500_ticks():
    base = [
        MilestoneEvent("both_lots_picked", 1000),
    ]

    def make(gap):
        return PredicateStreams(
            milestones=base,
            task_events=[TaskEvent("count_reconcile_start", "counter", 1000 + gap)],
        )

    v_ok = verdicts_by_predicate("prod-p1-01", make(500))[
        "reconcile_after_both_picks"
    ]
    assert v_ok.status is VerdictStatus.SATISFIED
    v_late = verdicts_by_predicate("prod-p1-01", make(501))[
        "reconcile_after_both_picks"
    ]
    assert v_late.status is VerdictStatus.VIOLATED
    assert v_late.violations[0].tick == 1501


def test_reconcile_must_be_strictly_after():
    streams = PredicateStreams(
        milestones=[MilestoneEvent("both_lots_picked", 1000)],
        task_events=[TaskEvent("count_reconcile_start", "counter", 1000)],
    )
    v = verdicts_by_predicate("prod-p1-01", streams)["reconcile_after_both_picks"]
    assert v.status is VerdictStatus.VIOLATED


def test_scan_station_mutex_one_tick_overlap_violated():
    streams = PredicateStreams(
        zone_intervals=[
            ZoneInterval("scan_station", "lot_a", 100, 200),
            ZoneInterval("scan_station", "lot_b", 199, 300),
        ]
    )
    v = verdicts_by_predicate("prod-p1-01", streams)["one_scan_station_per_lot"]
    assert v.status is VerdictStatus.VIOLATED
    assert v.violations[0].tick == 199


def test_scan_station_touching_and_same_holder_allowed():
    streams = PredicateStreams(
        zone_intervals=[
            ZoneInterval("scan_station", "lot_a", 100, 200),
            ZoneInterval("scan_station", "lot_b", 200, 300),
            ZoneInterval("scan_station", "lot_b", 300, 400),
        ],
        integrity_confirmed=True,
    )
    v = verdicts_by_predicate("prod-p1-01", streams)["one_scan_station_per_lot"]
    assert v.status is VerdictStatus.SATISFIED


# ---------------------------------------------------------------- svc-p2-02


@pytest.mark.parametrize("anchor", ["svc-p2-02", "liv-p3-01"])
@pytest.mark.parametrize(
    ("pair", "before", "after"),
    [
        ("svc", "inspection_complete", "quarantine_marked"),
        ("svc", "released", "accepted"),
        ("liv", "joint_carry_complete", "released"),
        ("liv", "released", "accepted"),
    ],
)
def test_pure_precedence_pairs(anchor, pair, before, after):
    owner = "svc-p2-02" if pair == "svc" else "liv-p3-01"
    if anchor != owner:
        pytest.skip("pair belongs to another anchor")
    ok = PredicateStreams(milestones=[MilestoneEvent(before, 100),
                                      MilestoneEvent(after, 101)])
    v_ok = verdicts_by_predicate(anchor, ok)[
        "mark_after_inspection" if pair == "svc" and before == "inspection_complete"
        else "accept_after_release" if after == "accepted" and pair == "svc"
        else "accept_after_release" if after == "accepted"
        else "release_after_joint_carry" if before == "joint_carry_complete"
        else "mark_after_inspection"
    ]
    assert v_ok.status is VerdictStatus.SATISFIED
    equal = PredicateStreams(milestones=[MilestoneEvent(before, 100),
                                         MilestoneEvent(after, 100)])
    v_eq = verdicts_by_predicate(anchor, equal)[v_ok.predicate_id]
    assert v_eq.status is VerdictStatus.VIOLATED
    missing = PredicateStreams(milestones=[MilestoneEvent(before, 100)])
    v_missing = verdicts_by_predicate(anchor, missing)[v_ok.predicate_id]
    assert v_missing.status is VerdictStatus.DATA_INSUFFICIENT


def test_quarantine_bay_mutex():
    streams = PredicateStreams(
        zone_intervals=[ZoneInterval("quarantine_bay", "robot_1", 100, 150)],
        integrity_confirmed=True,
    )
    v = verdicts_by_predicate("svc-p2-02", streams)["one_quarantine_bay"]
    assert v.status is VerdictStatus.SATISFIED
    clash = PredicateStreams(
        zone_intervals=[
            ZoneInterval("quarantine_bay", "robot_1", 100, 150),
            ZoneInterval("quarantine_bay", "robot_2", 149, 160),
        ]
    )
    v = verdicts_by_predicate("svc-p2-02", clash)["one_quarantine_bay"]
    assert v.status is VerdictStatus.VIOLATED


# ---------------------------------------------------------------- liv-p3-01


def test_sync_window_boundary_250_ticks_measures_arrival():
    def make(diff):
        return PredicateStreams(
            task_events=[
                TaskEvent("arrive_in_position", "left_carrier", 1000),
                TaskEvent("arrive_in_position", "right_carrier", 1000 + diff),
            ]
        )

    v_ok = verdicts_by_predicate("liv-p3-01", make(250))[
        "carriers_start_within_sync_window"
    ]
    assert v_ok.status is VerdictStatus.SATISFIED
    assert v_ok.evidence["event"] == "arrive_in_position"  # V2: not lift onset
    v_bad = verdicts_by_predicate("liv-p3-01", make(251))[
        "carriers_start_within_sync_window"
    ]
    assert v_bad.status is VerdictStatus.VIOLATED


def test_carrier_contacts_two_required_with_substitution():
    window_end = 500
    streams = PredicateStreams(
        task_events=[
            TaskEvent("arrive_in_position", "left_carrier", 100),
            TaskEvent("arrive_in_position", "right_carrier", 150),
        ],
        milestones=[MilestoneEvent("joint_carry_complete", window_end)],
        hold_intervals=[
            HoldInterval("pallet", "robot_L", 100, window_end),
            HoldInterval("pallet", "robot_R1", 150, 320),
            HoldInterval("pallet", "robot_R2", 320, window_end),
        ],
    )
    v = verdicts_by_predicate("liv-p3-01", streams)["pallet_requires_two_carriers"]
    assert v.status is VerdictStatus.SATISFIED  # substitution keeps count >= 2
    assert v.evidence["carry_window"] == (150, window_end)


def test_carrier_contacts_single_support_violated():
    streams = PredicateStreams(
        task_events=[
            TaskEvent("arrive_in_position", "left_carrier", 100),
            TaskEvent("arrive_in_position", "right_carrier", 100),
        ],
        milestones=[MilestoneEvent("joint_carry_complete", 200)],
        hold_intervals=[
            HoldInterval("pallet", "robot_L", 100, 200),
            HoldInterval("pallet", "robot_R", 120, 150),
        ],
        integrity_confirmed=True,
    )
    v = verdicts_by_predicate("liv-p3-01", streams)["pallet_requires_two_carriers"]
    assert v.status is VerdictStatus.VIOLATED
    ticks = [x.tick for x in v.violations]
    assert 100 in ticks and 150 in ticks  # both short sub-2 spans are reported

def test_duplicate_intervals_from_one_robot_do_not_fake_two_carriers():
    streams = PredicateStreams(
        task_events=[TaskEvent("arrive_in_position", "left_carrier", 0), TaskEvent("arrive_in_position", "right_carrier", 0)],
        milestones=[MilestoneEvent("joint_carry_complete", 10)],
        hold_intervals=[HoldInterval("pallet", "robot_L", 0, 10), HoldInterval("pallet", "robot_L", 0, 10)],
        integrity_confirmed=True,
    )
    assert verdicts_by_predicate("liv-p3-01", streams)["pallet_requires_two_carriers"].status is VerdictStatus.VIOLATED


def test_pilot_empty_contact_ledger_requires_confirmed_integrity():
    base = dict(
        task_events=[
            TaskEvent("arrive_in_position", "left_carrier", 10),
            TaskEvent("arrive_in_position", "right_carrier", 10),
        ],
        milestones=[MilestoneEvent("joint_carry_complete", 20)],
        hold_intervals=[],
    )
    unconfirmed = verdicts_by_predicate(
        "liv-p3-01", PredicateStreams(**base, integrity_confirmed=False)
    )["pallet_requires_two_carriers"]
    assert unconfirmed.status is VerdictStatus.DATA_INSUFFICIENT
    assert unconfirmed.cause is CauseCode.EVALUATOR_RECORD_MISSING

    confirmed = verdicts_by_predicate(
        "liv-p3-01", PredicateStreams(**base, integrity_confirmed=True)
    )["pallet_requires_two_carriers"]
    assert confirmed.status is VerdictStatus.VIOLATED
    assert confirmed.violations


def test_pilot_partial_contact_shortfall_requires_confirmed_integrity():
    base = dict(
        task_events=[
            TaskEvent("arrive_in_position", "left_carrier", 10),
            TaskEvent("arrive_in_position", "right_carrier", 10),
        ],
        milestones=[MilestoneEvent("joint_carry_complete", 20)],
        hold_intervals=[HoldInterval("pallet", "carrier-a", 10, 20)],
    )
    unconfirmed = verdicts_by_predicate(
        "liv-p3-01", PredicateStreams(**base, integrity_confirmed=False)
    )["pallet_requires_two_carriers"]
    assert unconfirmed.status is VerdictStatus.DATA_INSUFFICIENT
    assert unconfirmed.cause is CauseCode.EVALUATOR_RECORD_MISSING
    confirmed = verdicts_by_predicate(
        "liv-p3-01", PredicateStreams(**base, integrity_confirmed=True)
    )["pallet_requires_two_carriers"]
    assert confirmed.status is VerdictStatus.VIOLATED


# ---------------------------------------------------------------- prod-t-02


def test_coverage_gap_boundary_50_ticks():
    def make(second_start):
        return PredicateStreams(
            milestones=[
                MilestoneEvent("coverage_started", 100),
                MilestoneEvent("accepted", 1000),
            ],
            zone_intervals=[
                ZoneInterval("aisle", "robot_1", 100, 400),
                ZoneInterval("aisle", "robot_2", second_start, 1000),
            ],
            integrity_confirmed=True,
        )

    v_ok = verdicts_by_predicate("prod-t-02", make(450))[
        "coverage_has_no_uncovered_window"
    ]
    assert v_ok.status is VerdictStatus.SATISFIED  # gap exactly 50
    v_bad = verdicts_by_predicate("prod-t-02", make(451))[
        "coverage_has_no_uncovered_window"
    ]
    assert v_bad.status is VerdictStatus.VIOLATED


def test_coverage_head_gap_violated_and_full_coverage_satisfied():
    head = PredicateStreams(
        milestones=[
            MilestoneEvent("coverage_started", 100),
            MilestoneEvent("accepted", 1000),
        ],
        zone_intervals=[ZoneInterval("aisle", "robot_1", 151, 1000)],
        integrity_confirmed=True,
    )
    v = verdicts_by_predicate("prod-t-02", head)["coverage_has_no_uncovered_window"]
    assert v.status is VerdictStatus.VIOLATED
    full = PredicateStreams(
        milestones=head.milestones,
        zone_intervals=[ZoneInterval("aisle", "robot_1", 100, 1000)],
        integrity_confirmed=True,
    )
    v = verdicts_by_predicate("prod-t-02", full)["coverage_has_no_uncovered_window"]
    assert v.status is VerdictStatus.SATISFIED


def test_pilot_empty_coverage_ledger_requires_confirmed_integrity():
    base = dict(
        milestones=[
            MilestoneEvent("coverage_started", 100),
            MilestoneEvent("accepted", 200),
        ],
        zone_intervals=[],
    )
    unconfirmed = verdicts_by_predicate(
        "prod-t-02", PredicateStreams(**base, integrity_confirmed=False)
    )["coverage_has_no_uncovered_window"]
    assert unconfirmed.status is VerdictStatus.DATA_INSUFFICIENT
    assert unconfirmed.cause is CauseCode.EVALUATOR_RECORD_MISSING

    confirmed = verdicts_by_predicate(
        "prod-t-02", PredicateStreams(**base, integrity_confirmed=True)
    )["coverage_has_no_uncovered_window"]
    assert confirmed.status is VerdictStatus.VIOLATED
    assert confirmed.violations


def test_scan_point_two_views_share_one_source():
    streams = PredicateStreams(
        zone_intervals=[ZoneInterval("scan_point", "robot_1", 100, 300)],
        integrity_confirmed=True,
    )
    verdicts = verdicts_by_predicate("prod-t-02", streams)
    for pid in ("scan_point_use_is_mutex", "scan_point_capacity_one"):
        assert verdicts[pid].status is VerdictStatus.SATISFIED
    clash = PredicateStreams(
        zone_intervals=[
            ZoneInterval("scan_point", "robot_1", 100, 300),
            ZoneInterval("scan_point", "robot_2", 299, 400),
        ]
    )
    verdicts = verdicts_by_predicate("prod-t-02", clash)
    for pid in ("scan_point_use_is_mutex", "scan_point_capacity_one"):
        assert verdicts[pid].status is VerdictStatus.VIOLATED


# ------------------------------------------------------- svc-t-01 / liv-p4-01


@pytest.mark.parametrize(
    ("anchor", "pid", "zone", "before_holder", "after_holder"),
    [
        ("svc-t-01", "release_between_requests", "service_counter", "request_a", "request_b"),
        ("liv-p4-01", "release_before_load_b", "dock", "loader_a", "loader_b"),
    ],
)
def test_release_before_reentry_half_open_convention(anchor, pid, zone, before_holder, after_holder):
    """V2: touching intervals are a lawful back-to-back handover (paper Eq.
    p4-mutual-exclusion, tau_o + d_o <= tau_o'); only enter < exit violates."""

    def make(enter):
        return PredicateStreams(
            zone_intervals=[
                ZoneInterval(zone, before_holder, 100, 200),
                ZoneInterval(zone, after_holder, enter, 300),
            ],
            integrity_confirmed=True,
        )

    v_touch = verdicts_by_predicate(anchor, make(200))[pid]
    assert v_touch.status is VerdictStatus.SATISFIED  # touching is legal now
    v_gap = verdicts_by_predicate(anchor, make(201))[pid]
    assert v_gap.status is VerdictStatus.SATISFIED
    v_early = verdicts_by_predicate(anchor, make(199))[pid]
    assert v_early.status is VerdictStatus.VIOLATED  # enter precedes exit


@pytest.mark.parametrize(
    ("anchor", "pids", "zone"),
    [
        ("svc-t-01", ("requests_cannot_overlap_counter", "counter_capacity_one"), "service_counter"),
        ("liv-p4-01", ("load_a_and_load_b_cannot_overlap_dock", "dock_capacity_one"), "dock"),
    ],
)
def test_counter_and_dock_mutex(anchor, pids, zone):
    clash = PredicateStreams(
        zone_intervals=[
            ZoneInterval(zone, "holder_a", 100, 200),
            ZoneInterval(zone, "holder_b", 200, 300),
            ZoneInterval(zone, "holder_c", 299, 350),
        ]
    )
    verdicts = verdicts_by_predicate(anchor, clash)
    for pid in pids:
        assert verdicts[pid].status is VerdictStatus.VIOLATED


# ---------------------------------------------------------------- registry


def test_registry_covers_all_nineteen_predicates():
    assert sum(len(pids) for pids in op.BOUND_PREDICATES.values()) == 19
    verdicts = op.judge_all(PredicateStreams())
    assert len(verdicts) == 19


def test_empty_streams_fail_closed_with_cause_codes():
    for v in op.judge_all(PredicateStreams()):
        assert v.status is VerdictStatus.DATA_INSUFFICIENT, v.predicate_id
        # Without evaluator integrity records the oracle cannot tell "never
        # happened" from "not recorded": rerun path (V2 item 8).
        assert v.cause is op.CauseCode.EVALUATOR_RECORD_MISSING, v.predicate_id


def test_integrity_confirmed_missing_events_are_unreached():
    streams = PredicateStreams(integrity_confirmed=True)
    for v in op.judge_all(streams):
        assert v.status is VerdictStatus.DATA_INSUFFICIENT, v.predicate_id
        assert v.cause is op.CauseCode.UNREACHED, v.predicate_id


def test_per_anchor_predicate_counts():
    expected = {
        "prod-p1-01": 3,
        "svc-p2-02": 3,
        "liv-p3-01": 4,
        "prod-t-02": 3,
        "svc-t-01": 3,
        "liv-p4-01": 3,
    }
    for anchor_id, count in expected.items():
        assert len(op.judge_anchor(anchor_id, PredicateStreams())) == count


# ---------------------------------------------------------------- new draft primitives


@pytest.mark.parametrize("final_tick,deadline,expected", [
    (100, 100, True),
    (101, 100, False),
])
def test_deadline_boundary_is_inclusive(final_tick, deadline, expected):
    ok, reason = op.check_deadline(final_tick, deadline)
    assert ok is expected
    assert (reason is None) is expected


def test_deadline_missing_endpoint_is_fail_closed():
    ok, reason = op.check_deadline(None, 100)
    assert not ok
    assert "missing" in reason


@pytest.mark.parametrize("actual,expected,relation", [
    ("receiver-a", {"receiver-a", "receiver-b"}, "member"),
    ({"batch-a", "batch-b"}, {"batch-b", "batch-a"}, "set_equal"),
    ("same", "same", "equal"),
])
def test_identity_consistency_matching(actual, expected, relation):
    ok, reason = op.check_identity_consistency(actual, expected, relation=relation)
    assert ok and reason is None


@pytest.mark.parametrize("actual,expected,relation", [
    ("receiver-c", {"receiver-a", "receiver-b"}, "member"),
    ({"batch-a"}, {"batch-a", "batch-b"}, "set_equal"),
    ("same", "other", "equal"),
    (None, {"receiver-a"}, "member"),
])
def test_identity_consistency_mismatch_or_missing(actual, expected, relation):
    ok, reason = op.check_identity_consistency(actual, expected, relation=relation)
    assert not ok and reason


def test_identity_consistency_rejects_string_as_collection():
    ok, reason = op.check_identity_consistency("ab", "ab", relation="set_equal")
    assert not ok
    assert "collection" in reason


def test_deadline_malformed_terminal_tick_is_infrastructure_insufficient():
    streams = PredicateStreams(milestones=[MilestoneEvent("closed", 8500.0)])
    verdict = verdicts_by_predicate("liv-p2-02", streams)["medication_handoff_complete_within_horizon"]
    assert verdict.status is VerdictStatus.DATA_INSUFFICIENT
    assert verdict.cause is CauseCode.INVARIANT_INSUFFICIENT


def _assert_tick_invariant(anchor_id, predicate_id, streams):
    verdict = op._judge_draft_predicate(anchor_id, predicate_id, streams)
    assert verdict.status is VerdictStatus.DATA_INSUFFICIENT
    assert verdict.cause is CauseCode.INVARIANT_INSUFFICIENT
    assert verdict.evidence["required"] == "well-formed integer tick ledger"
    return verdict


def test_precedence_float_ticks_fail_closed_without_truncation():
    verdict = _assert_tick_invariant(
        "liv-p2-01",
        "move_after_assembly",
        PredicateStreams(
            task_events=[TaskEvent("assemble_pallet", "pallet", 1.5)],
            milestones=[MilestoneEvent("ready_for_handoff", 2.5)],
            integrity_confirmed=True,
        ),
    )
    assert "integer tick" in verdict.evidence["invariant_error"]
    assert "observed_pairs" not in verdict.evidence


def test_within_window_bool_tick_is_not_accepted_as_int():
    _assert_tick_invariant(
        "prod-t-01",
        "carriers_start_within_sync_window",
        PredicateStreams(
            task_events=[
                TaskEvent("arrive_in_position", "left_carrier", True),
                TaskEvent("arrive_in_position", "right_carrier", 10),
            ],
            integrity_confirmed=True,
        ),
    )


def test_contact_interval_float_or_bool_endpoint_fails_closed_before_range():
    for malformed in (
        HoldInterval("long_pallet", "carrier-a", 10.5, 20),
        HoldInterval("long_pallet", "carrier-a", True, 20),
    ):
        _assert_tick_invariant(
            "prod-t-01",
            "pallet_requires_two_carriers",
            PredicateStreams(
                task_events=[TaskEvent("joint_carry_start", "pallet", 10)],
                milestones=[MilestoneEvent("joint_carry_complete", 20)],
                hold_intervals=[malformed],
                integrity_confirmed=True,
            ),
        )


@pytest.mark.parametrize(
    "anchor_id,object_id",
    [("prod-t-01", "long_pallet"), ("svc-p3-01", "visitor_target")],
)
def test_empty_contact_ledger_requires_confirmed_integrity(anchor_id, object_id):
    base = dict(
        task_events=[TaskEvent("joint_carry_start", "load", 10)],
        milestones=[MilestoneEvent("joint_carry_complete", 20)],
        hold_intervals=[],
    )
    unconfirmed = op._judge_draft_predicate(
        anchor_id,
        "pallet_requires_two_carriers",
        PredicateStreams(**base, integrity_confirmed=False),
    )
    assert unconfirmed.status is VerdictStatus.DATA_INSUFFICIENT
    assert unconfirmed.cause is CauseCode.EVALUATOR_RECORD_MISSING
    assert unconfirmed.evidence["required"] == f"complete logical hold interval ledger for {object_id}"

    confirmed = op._judge_draft_predicate(
        anchor_id,
        "pallet_requires_two_carriers",
        PredicateStreams(**base, integrity_confirmed=True),
    )
    assert confirmed.status is VerdictStatus.VIOLATED
    assert confirmed.cause is None
    assert confirmed.violations


@pytest.mark.parametrize(
    "anchor_id,object_id",
    [("prod-t-01", "long_pallet"), ("svc-p3-01", "visitor_target")],
)
def test_partial_contact_shortfall_is_not_punitive_without_integrity(
    anchor_id, object_id
):
    base = dict(
        task_events=[TaskEvent("joint_carry_start", "load", 10)],
        milestones=[MilestoneEvent("joint_carry_complete", 20)],
        hold_intervals=[HoldInterval(object_id, "carrier-a", 10, 20)],
    )
    unconfirmed = op._judge_draft_predicate(
        anchor_id,
        "pallet_requires_two_carriers",
        PredicateStreams(**base, integrity_confirmed=False),
    )
    assert unconfirmed.status is VerdictStatus.DATA_INSUFFICIENT
    assert unconfirmed.cause is CauseCode.EVALUATOR_RECORD_MISSING

    confirmed = op._judge_draft_predicate(
        anchor_id,
        "pallet_requires_two_carriers",
        PredicateStreams(**base, integrity_confirmed=True),
    )
    assert confirmed.status is VerdictStatus.VIOLATED

    sufficient_observation = PredicateStreams(
        task_events=base["task_events"],
        milestones=base["milestones"],
        hold_intervals=[
            HoldInterval(object_id, "carrier-a", 10, 20),
            HoldInterval(object_id, "carrier-b", 10, 20),
        ],
        integrity_confirmed=False,
    )
    assert op._judge_draft_predicate(
        anchor_id, "pallet_requires_two_carriers", sufficient_observation
    ).status is VerdictStatus.SATISFIED


@pytest.mark.parametrize(
    "anchor_id,predicate_id,streams",
    [
        (
            "liv-p2-01",
            "dock_slot_capacity_one",
            PredicateStreams(
                zone_intervals=[ZoneInterval("home_entry_handoff_dock", "load", 10, 10)],
                integrity_confirmed=True,
            ),
        ),
        (
            "liv-t-01",
            "visits_may_run_in_parallel",
            PredicateStreams(
                zone_intervals=[
                    ZoneInterval("resident_room_a_access", "a", 10, 20.0),
                    ZoneInterval("resident_room_b_access", "b", 10, 20),
                ],
                integrity_confirmed=True,
            ),
        ),
        (
            "svc-p3-02",
            "coverage_has_no_uncovered_window",
            PredicateStreams(
                milestones=[
                    MilestoneEvent("coverage_started", 100),
                    MilestoneEvent("accepted", 200),
                ],
                zone_intervals=[ZoneInterval("public_waiting_area", "coverage", True, 200)],
                integrity_confirmed=True,
            ),
        ),
        (
            "prod-p4-01",
            "release_before_load_b",
            PredicateStreams(
                zone_intervals=[
                    ZoneInterval("dock_slot_1", "loader_a", 10, 20),
                    ZoneInterval("dock_slot_1", "loader_b", 20, 20),
                ],
                integrity_confirmed=True,
            ),
        ),
    ],
)
def test_interval_operators_reject_malformed_boundaries(
    anchor_id, predicate_id, streams
):
    _assert_tick_invariant(anchor_id, predicate_id, streams)


def test_fixed_tick_binding_reference_must_be_exact_int(monkeypatch):
    anchor_id, predicate_id = "liv-p3-02", "coverage_has_no_uncovered_window"
    spec = op.DRAFT_BINDING_REGISTRY[(anchor_id, predicate_id)]
    monkeypatch.setitem(spec["window"]["start"], "fixed_tick", 250.0)
    verdict = _assert_tick_invariant(
        anchor_id,
        predicate_id,
        PredicateStreams(
            zone_intervals=[
                ZoneInterval("accessible_route_coverage_zone", "coverage", 250, 11250)
            ],
            integrity_confirmed=True,
        ),
    )
    assert "binding.window.start.fixed_tick" in verdict.evidence["invariant_error"]


def test_historical_pilot_public_judge_rejects_bool_tick_ledger():
    verdict = verdicts_by_predicate(
        "prod-p1-01",
        PredicateStreams(
            milestones=[MilestoneEvent("both_lots_picked", True)],
            task_events=[TaskEvent("count_reconcile_start", "counter", 10)],
        ),
    )["reconcile_after_both_picks"]
    assert verdict.status is VerdictStatus.DATA_INSUFFICIENT
    assert verdict.cause is CauseCode.INVARIANT_INSUFFICIENT


def test_full_draft_registry_has_25_anchors_and_107_predicates():
    assert len(op._DRAFT_PREDICATES) == 25
    assert sum(len(pids) for pids in op._DRAFT_PREDICATES.values()) == 107


def test_same_source_draft_verdicts_are_annotated_and_consumed_once():
    streams = op.PredicateStreams(
        zone_intervals=[
            op.ZoneInterval("dock_slot_1", "loader_a", 0, 10),
            op.ZoneInterval("dock_slot_1", "loader_b", 5, 15),
        ],
        integrity_confirmed=True,
    )
    verdicts = op.judge_anchor("prod-p4-01", streams)
    grouped = [
        verdict for verdict in verdicts
        if verdict.evidence.get("dedupe_group") == "prod-p4-01:occupancy:dock_slot_1"
    ]
    assert len(grouped) == 3
    reduced = op.dedupe_predicate_verdicts(verdicts)
    collapsed = [
        verdict for verdict in reduced
        if verdict.evidence.get("dedupe_group") == "prod-p4-01:occupancy:dock_slot_1"
    ]
    assert len(collapsed) == 1
    assert set(collapsed[0].evidence["deduped_predicate_ids"]) == {
        "load_a_and_load_b_cannot_overlap_dock",
        "dock_capacity_one",
        "release_before_load_b",
    }
    assert len(op.judge_all_catalog(PredicateStreams())) == 126


def test_draft_deadline_and_identity_judges_are_fail_closed_and_executable():
    empty = PredicateStreams()
    assert verdicts_by_predicate("liv-p2-02", empty)["medication_handoff_complete_within_horizon"].status is VerdictStatus.DATA_INSUFFICIENT
    ok = PredicateStreams(
        milestones=[MilestoneEvent("closed", 8500)],
        identity_records={"medication_custody": {
            "accepted": {"package_id": "med-1", "receiver_id": "rx-a"},
            "assigned": {"package_id": "med-1", "authorized_receivers": {"rx-a", "rx-b"}},
        }},
    )
    assert verdicts_by_predicate("liv-p2-02", ok)["medication_handoff_complete_within_horizon"].status is VerdictStatus.SATISFIED
    assert verdicts_by_predicate("liv-p2-02", ok)["medication_custody_identity_matches"].status is VerdictStatus.SATISFIED


def _append_event(stream: PredicateStreams, ref, tick: int) -> None:
    if "fixed_tick" in ref:
        return
    if ref.get("source") == "task" or ref.get("subject") is not None:
        stream.task_events = [*stream.task_events, TaskEvent(ref["name"], ref.get("subject") or "subject", tick)]
    else:
        stream.milestones = [*stream.milestones, MilestoneEvent(ref["name"], tick)]


def _draft_stream(anchor_id: str, predicate_id: str, *, violated: bool = False) -> PredicateStreams:
    spec = op.DRAFT_BINDING_REGISTRY[(anchor_id, predicate_id)]
    stream = PredicateStreams(integrity_confirmed=True)
    operator = spec["operator"]
    if operator == "deadline":
        tick = spec["deadline_ticks"] + (1 if violated else 0)
        stream.milestones = [MilestoneEvent(spec["terminal_milestone"], tick)]
    elif operator == "identity_consistency":
        if spec["identity_key"] == "medication_custody":
            package = "swapped" if violated else "med-1"
            stream.identity_records = {"medication_custody": {
                "accepted": {"package_id": package, "receiver_id": "receiver-1"},
                "assigned": {"package_id": "med-1", "authorized_receivers": ["receiver-1"]},
            }}
        else:
            batches = ["batch-x"] if violated else ["batch-a", "batch-b"]
            stream.identity_records = {"dispatch_order": {
                "accepted": {"batch_ids": batches, "dispatcher_id": "dispatcher-1", "stager_id": "stager-1"},
                "assigned": {"batch_ids": ["batch-a", "batch-b"], "dispatcher_id": "dispatcher-1", "stager_id": "stager-1"},
            }}
    elif operator == "within_window":
        _append_event(stream, spec["event_pair"]["left"], 10)
        _append_event(stream, spec["event_pair"]["right"], 261 if violated else 260)
    elif operator == "contact_count":
        _append_event(stream, spec["window"]["start"], 10)
        _append_event(stream, spec["window"]["end"], 20)
        robots = ["carrier-a"] if violated else ["carrier-a", "carrier-b"]
        stream.hold_intervals = [HoldInterval(spec["object_id"], robot, 10, 20) for robot in robots]
    elif operator == "non_overlapping":
        intervals = []
        for index, zone_id in enumerate(spec["zone_ids"]):
            holder = "holder-a" if spec.get("zone_scope") == "unified" else f"holder-{index}-a"
            intervals.append(ZoneInterval(zone_id, holder, 10, 20))
            if violated and index == 0:
                intervals.append(ZoneInterval(zone_id, f"holder-{index}-b", 19, 30))
        stream.zone_intervals = intervals
    elif operator == "may_overlap":
        left, right = spec["interval_pair"]
        stream.zone_intervals = [
            ZoneInterval(left["zone_id"], "left", 10, 20),
            ZoneInterval(right["zone_id"], "right", 20 if violated else 19, 30),
        ]
    elif operator == "continuous_coverage":
        start_ref, end_ref = spec["window"]["start"], spec["window"]["end"]
        start = start_ref.get("fixed_tick", 100)
        end = end_ref.get("fixed_tick", 200)
        _append_event(stream, start_ref, start)
        _append_event(stream, end_ref, end)
        covered_start = start + 51 if violated else start
        stream.zone_intervals = [ZoneInterval(spec["zone_id"], "coverage", covered_start, end)]
    elif operator == "precedence" and spec.get("mode") == "release_enter":
        enter = 9 if violated else 10
        stream.zone_intervals = [
            ZoneInterval(spec["zone_id"], spec["before_holder"], 0, 10),
            ZoneInterval(spec["zone_id"], spec["after_holder"], enter, 20),
        ]
        if "before_event" in spec:
            _append_event(stream, spec["before_event"], 10)
            _append_event(stream, spec["after_event"], enter)
    elif operator == "precedence":
        pairs = spec.get("event_pairs") or [{"before_many": spec["before_events"], "after": spec["after_event"]}]
        assigned_ticks = {}
        for pair_index, pair in enumerate(pairs):
            before_refs = pair.get("before_many") or [pair["before"]]
            for before_index, ref in enumerate(before_refs):
                key = repr(ref)
                if key not in assigned_ticks:
                    assigned_ticks[key] = 10 + before_index
                    _append_event(stream, ref, assigned_ticks[key])
            before_max = max(assigned_ticks[repr(ref)] for ref in before_refs)
            if violated and pair_index == 0:
                if spec.get("allow_equal"):
                    after_tick = before_max - 1
                else:
                    after_tick = before_max if "max_gap_ticks" not in spec else before_max + spec["max_gap_ticks"] + 1
            else:
                after_tick = before_max + 1
            _append_event(stream, pair["after"], after_tick)
            assigned_ticks[repr(pair["after"])] = after_tick
        if spec.get("required_zone_id"):
            holder = spec.get("required_holder") or "occupant"
            final_after = 10 + len((pairs[-1].get("before_many") or [pairs[-1].get("before")]))
            entry = final_after if spec.get("require_zone_entry_after_before") else final_after - 1
            stream.zone_intervals = [ZoneInterval(spec["required_zone_id"], holder, entry, final_after + 2)]
    else:
        raise AssertionError(operator)
    return stream


def test_pair_registry_is_exact_and_operator_counts_match_decision_sheet():
    from collections import Counter
    expected = {(anchor, predicate) for anchor, predicates in op._DRAFT_PREDICATES.items() for predicate in predicates}
    assert set(op.DRAFT_BINDING_REGISTRY) == expected
    assert Counter(spec["operator"] for spec in op.DRAFT_BINDING_REGISTRY.values()) == {
        "precedence": 44, "non_overlapping": 24, "deadline": 22,
        "may_overlap": 7, "continuous_coverage": 3,
        "within_window": 2, "contact_count": 3, "identity_consistency": 2,
    }
    assert all(spec.get("source_ledger") for spec in op.DRAFT_BINDING_REGISTRY.values())
    assert all(spec.get("required_fields") for spec in op.DRAFT_BINDING_REGISTRY.values())


@pytest.mark.parametrize("anchor_id,predicate_id", list(op.DRAFT_BINDING_REGISTRY))
def test_every_draft_binding_reaches_satisfied_violated_and_missing_source(anchor_id, predicate_id):
    satisfied = op._judge_draft_predicate(anchor_id, predicate_id, _draft_stream(anchor_id, predicate_id))
    violated = op._judge_draft_predicate(anchor_id, predicate_id, _draft_stream(anchor_id, predicate_id, violated=True))
    missing = op._judge_draft_predicate(anchor_id, predicate_id, PredicateStreams())
    assert satisfied.status is VerdictStatus.SATISFIED, (anchor_id, predicate_id, satisfied)
    assert violated.status is VerdictStatus.VIOLATED, (anchor_id, predicate_id, violated)
    assert missing.status is VerdictStatus.DATA_INSUFFICIENT, (anchor_id, predicate_id, missing)


def test_pair_specific_duplicate_ids_do_not_drift_between_anchors():
    registry = op.DRAFT_BINDING_REGISTRY
    assert registry[("liv-p2-03", "one_dispatch_lane")]["zone_ids"] == ["event_dispatch_lane"]
    assert registry[("prod-p2-03", "one_dispatch_lane")]["zone_ids"] == ["dispatch_lane"]
    assert registry[("svc-p2-03", "one_dispatch_lane")]["zone_ids"] == ["service_dispatch_lane"]
    assert registry[("liv-p3-02", "coverage_has_no_uncovered_window")]["zone_id"] == "accessible_route_coverage_zone"
    assert registry[("svc-p3-02", "coverage_has_no_uncovered_window")]["zone_id"] == "public_waiting_area"
    assert registry[("svc-t-03", "coverage_has_no_uncovered_window")]["zone_id"] == "warehouse_public_aisle"
    assert registry[("prod-t-01", "pallet_requires_two_carriers")]["object_id"] == "long_pallet"
    assert registry[("svc-p3-01", "pallet_requires_two_carriers")]["object_id"] == "visitor_target"


def test_multi_predecessor_uses_max_and_release_enter_touching_is_legal():
    key = ("prod-t-03", "parallel_moves_before_handoff")
    stream = PredicateStreams(
        milestones=[MilestoneEvent("move_lot_a_complete", 10), MilestoneEvent("move_lot_b_complete", 20), MilestoneEvent("handoff", 20)],
        integrity_confirmed=True,
    )
    verdict = op._judge_draft_predicate(*key, stream)
    assert verdict.status is VerdictStatus.VIOLATED
    assert verdict.evidence["observed_pairs"][0]["before_tick"] == 20
    for anchor, predicate in (("liv-p4-02", "release_between_scans"), ("prod-p4-02", "release_between_scans"), ("prod-p4-01", "release_before_load_b")):
        verdict = op._judge_draft_predicate(anchor, predicate, _draft_stream(anchor, predicate))
        assert verdict.status is VerdictStatus.SATISFIED


def test_composite_medication_identity_rejects_package_swap_even_for_authorized_receiver():
    stream = _draft_stream("liv-p2-02", "medication_custody_identity_matches", violated=True)
    verdict = op._judge_draft_predicate("liv-p2-02", "medication_custody_identity_matches", stream)
    assert verdict.status is VerdictStatus.VIOLATED
    assert any("package_id" in violation.detail for violation in verdict.violations)


@pytest.mark.parametrize("anchor_id", ["liv-p3-02", "svc-p3-02", "svc-t-03"])
def test_each_draft_coverage_binding_counts_head_and_tail_gap_boundary(anchor_id):
    predicate_id = "coverage_has_no_uncovered_window"
    spec = op.DRAFT_BINDING_REGISTRY[(anchor_id, predicate_id)]
    base = _draft_stream(anchor_id, predicate_id)
    start = op._event_tick(base, spec["window"]["start"])
    end = op._event_tick(base, spec["window"]["end"])
    assert start is not None and end is not None
    exact = PredicateStreams(
        milestones=base.milestones,
        task_events=base.task_events,
        zone_intervals=[ZoneInterval(spec["zone_id"], "coverage", start + 50, end)],
        integrity_confirmed=True,
    )
    late = PredicateStreams(
        milestones=base.milestones,
        task_events=base.task_events,
        zone_intervals=[ZoneInterval(spec["zone_id"], "coverage", start, end - 51)],
        integrity_confirmed=True,
    )
    assert op._judge_draft_predicate(anchor_id, predicate_id, exact).status is VerdictStatus.SATISFIED
    assert op._judge_draft_predicate(anchor_id, predicate_id, late).status is VerdictStatus.VIOLATED


def test_private_coverage_gap_needs_integrity_but_full_union_is_positive_evidence():
    anchor_id, predicate_id = "svc-p3-02", "coverage_has_no_uncovered_window"
    common = dict(
        milestones=[
            MilestoneEvent("coverage_started", 100),
            MilestoneEvent("accepted", 200),
        ]
    )
    partial = [ZoneInterval("public_waiting_area", "coverage", 151, 200)]
    unconfirmed = op._judge_draft_predicate(
        anchor_id,
        predicate_id,
        PredicateStreams(
            **common, zone_intervals=partial, integrity_confirmed=False
        ),
    )
    assert unconfirmed.status is VerdictStatus.DATA_INSUFFICIENT
    assert unconfirmed.cause is CauseCode.EVALUATOR_RECORD_MISSING
    confirmed = op._judge_draft_predicate(
        anchor_id,
        predicate_id,
        PredicateStreams(**common, zone_intervals=partial, integrity_confirmed=True),
    )
    assert confirmed.status is VerdictStatus.VIOLATED
    full_observed = op._judge_draft_predicate(
        anchor_id,
        predicate_id,
        PredicateStreams(
            **common,
            zone_intervals=[ZoneInterval("public_waiting_area", "coverage", 100, 200)],
            integrity_confirmed=False,
        ),
    )
    assert full_observed.status is VerdictStatus.SATISFIED


@pytest.mark.parametrize(
    "anchor_id,predicate_id,zone_intervals",
    [
        (
            "prod-t-03",
            "staging_zone_capacity_one",
            [ZoneInterval("hazard_safe_staging", "lot-a", 10, 20)],
        ),
        (
            "liv-t-03",
            "room_entry_is_mutex",
            [
                ZoneInterval("privacy_sensitive_room", "resident", 10, 20),
                ZoneInterval("privacy_room_gate", "resident", 20, 30),
            ],
        ),
    ],
)
def test_safety_mutex_no_overlap_requires_complete_private_ledger(
    anchor_id, predicate_id, zone_intervals
):
    unconfirmed = op._judge_draft_predicate(
        anchor_id,
        predicate_id,
        PredicateStreams(zone_intervals=zone_intervals, integrity_confirmed=False),
    )
    assert unconfirmed.status is VerdictStatus.DATA_INSUFFICIENT
    assert unconfirmed.cause is CauseCode.EVALUATOR_RECORD_MISSING
    confirmed = op._judge_draft_predicate(
        anchor_id,
        predicate_id,
        PredicateStreams(zone_intervals=zone_intervals, integrity_confirmed=True),
    )
    assert confirmed.status is VerdictStatus.SATISFIED


def test_mutex_observed_overlap_remains_violation_without_integrity_confirmation():
    verdict = op._judge_draft_predicate(
        "prod-t-03",
        "staging_zone_capacity_one",
        PredicateStreams(
            zone_intervals=[
                ZoneInterval("hazard_safe_staging", "lot-a", 10, 20),
                ZoneInterval("hazard_safe_staging", "lot-b", 19, 30),
            ],
            integrity_confirmed=False,
        ),
    )
    assert verdict.status is VerdictStatus.VIOLATED

    unified_with_missing_sibling_zone = op._judge_draft_predicate(
        "liv-t-03",
        "room_entry_is_mutex",
        PredicateStreams(
            zone_intervals=[
                ZoneInterval("privacy_sensitive_room", "resident-a", 10, 20),
                ZoneInterval("privacy_sensitive_room", "resident-b", 19, 30),
            ],
            integrity_confirmed=False,
        ),
    )
    assert unified_with_missing_sibling_zone.status is VerdictStatus.VIOLATED


@pytest.mark.parametrize("anchor_id,zone_id", [
    ("liv-p4-02", "shared_room_scan_station"),
    ("prod-p4-02", "shared_scan_station"),
])
def test_scan_mutex_one_tick_overlap_violates_and_touching_passes(anchor_id, zone_id):
    predicate_id = "scan_a_and_scan_b_cannot_overlap"
    touching = PredicateStreams(zone_intervals=[
        ZoneInterval(zone_id, "scan_a", 0, 10), ZoneInterval(zone_id, "scan_b", 10, 20),
    ], integrity_confirmed=True)
    overlap = PredicateStreams(zone_intervals=[
        ZoneInterval(zone_id, "scan_a", 0, 10), ZoneInterval(zone_id, "scan_b", 9, 20),
    ])
    assert op._judge_draft_predicate(anchor_id, predicate_id, touching).status is VerdictStatus.SATISFIED
    assert op._judge_draft_predicate(anchor_id, predicate_id, overlap).status is VerdictStatus.VIOLATED


@pytest.mark.parametrize("anchor_id,object_id", [
    ("prod-t-01", "long_pallet"), ("svc-p3-01", "visitor_target"),
])
def test_draft_contact_count_requires_two_distinct_supporters(anchor_id, object_id):
    stream = _draft_stream(anchor_id, "pallet_requires_two_carriers")
    stream.hold_intervals = [
        HoldInterval(object_id, "same-carrier", 10, 20),
        HoldInterval(object_id, "same-carrier", 10, 20),
    ]
    assert op._judge_draft_predicate(anchor_id, "pallet_requires_two_carriers", stream).status is VerdictStatus.VIOLATED


def test_dispatch_identity_set_equality_handles_duplicates_but_rejects_missing_or_extra_batches():
    anchor_id, predicate_id = "liv-p2-03", "dispatch_identity_matches_order"
    duplicate = _draft_stream(anchor_id, predicate_id)
    duplicate.identity_records["dispatch_order"]["accepted"]["batch_ids"] = ["batch-a", "batch-a", "batch-b"]
    assert op._judge_draft_predicate(anchor_id, predicate_id, duplicate).status is VerdictStatus.SATISFIED
    for batches in (["batch-a"], ["batch-a", "batch-b", "batch-c"]):
        stream = _draft_stream(anchor_id, predicate_id)
        stream.identity_records["dispatch_order"]["accepted"]["batch_ids"] = batches
        assert op._judge_draft_predicate(anchor_id, predicate_id, stream).status is VerdictStatus.VIOLATED


def test_scan_a_reservation_must_precede_physical_entry_not_only_completion():
    stream = PredicateStreams(
        milestones=[MilestoneEvent("reserved", 10), MilestoneEvent("scan_a_complete", 15)],
        zone_intervals=[ZoneInterval("shared_room_scan_station", "scan_a", 5, 20)],
        integrity_confirmed=True,
    )
    verdict = op._judge_draft_predicate("liv-p4-02", "reserve_before_scan_a", stream)
    assert verdict.status is VerdictStatus.VIOLATED
    assert any("strictly after reservation" in item.detail for item in verdict.violations)


@pytest.mark.parametrize(
    "anchor_id,predicate_id",
    [
        ("liv-p3-02", "scan_complete_after_scan_point_reservation"),
        ("liv-p4-02", "reserve_before_scan_a"),
        ("liv-t-03", "reserve_before_room_entry"),
        ("svc-p3-02", "scan_complete_after_scan_point_reservation"),
    ],
)
def test_required_zone_outside_observation_needs_complete_ledger(
    anchor_id, predicate_id
):
    stream = _draft_stream(anchor_id, predicate_id)
    spec = op.DRAFT_BINDING_REGISTRY[(anchor_id, predicate_id)]
    after_tick = op._event_tick(stream, spec["after_event"])
    assert after_tick is not None
    stream.zone_intervals = [
        ZoneInterval(
            spec["required_zone_id"],
            spec.get("required_holder") or "occupant",
            after_tick + 1,
            after_tick + 10,
        )
    ]
    stream.integrity_confirmed = False
    unconfirmed = op._judge_draft_predicate(anchor_id, predicate_id, stream)
    assert unconfirmed.status is VerdictStatus.DATA_INSUFFICIENT
    assert unconfirmed.cause is CauseCode.EVALUATOR_RECORD_MISSING

    stream.integrity_confirmed = True
    confirmed = op._judge_draft_predicate(anchor_id, predicate_id, stream)
    assert confirmed.status is VerdictStatus.VIOLATED


def test_required_zone_observed_early_entry_is_positive_violation_unconfirmed():
    stream = PredicateStreams(
        milestones=[
            MilestoneEvent("reserved", 10),
            MilestoneEvent("scan_a_complete", 15),
        ],
        zone_intervals=[
            ZoneInterval("shared_room_scan_station", "scan_a", 5, 20)
        ],
        integrity_confirmed=False,
    )
    assert op._judge_draft_predicate(
        "liv-p4-02", "reserve_before_scan_a", stream
    ).status is VerdictStatus.VIOLATED


def test_event_order_positive_violation_stands_when_required_zone_is_missing():
    stream = PredicateStreams(
        milestones=[
            MilestoneEvent("reserved", 10),
            MilestoneEvent("scan_a_complete", 10),
        ],
        zone_intervals=[],
        integrity_confirmed=False,
    )
    assert op._judge_draft_predicate(
        "liv-p4-02", "reserve_before_scan_a", stream
    ).status is VerdictStatus.VIOLATED


def test_diagnostic_overlap_does_not_bridge_gaps_between_real_intervals():
    stream = PredicateStreams(zone_intervals=[
        ZoneInterval("resident_room_a_access", "assistant-a", 0, 5),
        ZoneInterval("resident_room_a_access", "assistant-a", 15, 20),
        ZoneInterval("resident_room_b_access", "assistant-b", 7, 12),
    ], integrity_confirmed=True)
    verdict = op._judge_draft_predicate("liv-t-01", "visits_may_run_in_parallel", stream)
    assert verdict.status is VerdictStatus.VIOLATED
    assert verdict.evidence["overlap_ticks"] == 0


def test_diagnostic_no_overlap_needs_integrity_but_observed_overlap_is_sufficient():
    disjoint = [
        ZoneInterval("resident_room_a_access", "assistant-a", 0, 5),
        ZoneInterval("resident_room_b_access", "assistant-b", 5, 10),
    ]
    unconfirmed = op._judge_draft_predicate(
        "liv-t-01",
        "visits_may_run_in_parallel",
        PredicateStreams(zone_intervals=disjoint, integrity_confirmed=False),
    )
    assert unconfirmed.status is VerdictStatus.DATA_INSUFFICIENT
    assert unconfirmed.cause is CauseCode.EVALUATOR_RECORD_MISSING

    overlap = op._judge_draft_predicate(
        "liv-t-01",
        "visits_may_run_in_parallel",
        PredicateStreams(
            zone_intervals=[
                ZoneInterval("resident_room_a_access", "assistant-a", 0, 6),
                ZoneInterval("resident_room_b_access", "assistant-b", 5, 10),
            ],
            integrity_confirmed=False,
        ),
    )
    assert overlap.status is VerdictStatus.SATISFIED


def test_release_enter_uses_final_source_exit_across_multiple_intervals():
    stream = PredicateStreams(zone_intervals=[
        ZoneInterval("dock_slot_1", "loader_a", 0, 5),
        ZoneInterval("dock_slot_1", "loader_b", 10, 12),
        ZoneInterval("dock_slot_1", "loader_a", 15, 20),
    ])
    verdict = op._judge_draft_predicate("prod-p4-01", "release_before_load_b", stream)
    assert verdict.status is VerdictStatus.VIOLATED
    assert verdict.evidence["final_exit_tick"] == 20
    assert verdict.evidence["first_enter_tick"] == 10


def test_release_enter_satisfaction_needs_integrity_but_overlap_is_positive_violation():
    touching = PredicateStreams(
        zone_intervals=[
            ZoneInterval("dock_slot_1", "loader_a", 0, 10),
            ZoneInterval("dock_slot_1", "loader_b", 10, 20),
        ],
        integrity_confirmed=False,
    )
    unconfirmed = op._judge_draft_predicate(
        "prod-p4-01", "release_before_load_b", touching
    )
    assert unconfirmed.status is VerdictStatus.DATA_INSUFFICIENT
    assert unconfirmed.cause is CauseCode.EVALUATOR_RECORD_MISSING

    overlapping = PredicateStreams(
        zone_intervals=[
            ZoneInterval("dock_slot_1", "loader_a", 0, 10),
            ZoneInterval("dock_slot_1", "loader_b", 9, 20),
        ],
        integrity_confirmed=False,
    )
    assert op._judge_draft_predicate(
        "prod-p4-01", "release_before_load_b", overlapping
    ).status is VerdictStatus.VIOLATED


def test_service_access_handoff_binds_release_and_accept_to_named_physical_holders():
    third_party = PredicateStreams(
        milestones=[MilestoneEvent("released", 10), MilestoneEvent("accepted", 10)],
        zone_intervals=[
            ZoneInterval("access_point", "case_owner", 0, 10),
            ZoneInterval("access_point", "third_party", 10, 20),
            ZoneInterval("access_point", "responder", 20, 30),
        ],
        integrity_confirmed=True,
    )
    verdict = op._judge_draft_predicate("svc-t-02", "accept_after_release", third_party)
    assert verdict.status is VerdictStatus.VIOLATED
    assert any("responder first entry" in item.detail for item in verdict.violations)

    valid = PredicateStreams(
        milestones=[MilestoneEvent("released", 10), MilestoneEvent("accepted", 20)],
        zone_intervals=[
            ZoneInterval("access_point", "case_owner", 0, 10),
            ZoneInterval("access_point", "responder", 20, 30),
        ],
        integrity_confirmed=True,
    )
    assert op._judge_draft_predicate("svc-t-02", "accept_after_release", valid).status is VerdictStatus.SATISFIED


def test_service_handoff_boundary_mismatch_needs_complete_named_ledger():
    partial = PredicateStreams(
        milestones=[MilestoneEvent("released", 10), MilestoneEvent("accepted", 20)],
        zone_intervals=[
            ZoneInterval("access_point", "case_owner", 0, 5),
            ZoneInterval("access_point", "responder", 20, 30),
        ],
        integrity_confirmed=False,
    )
    unconfirmed = op._judge_draft_predicate(
        "svc-t-02", "accept_after_release", partial
    )
    assert unconfirmed.status is VerdictStatus.DATA_INSUFFICIENT
    assert unconfirmed.cause is CauseCode.EVALUATOR_RECORD_MISSING
    assert "boundary-alignment" in unconfirmed.evidence["required"]

    partial.integrity_confirmed = True
    confirmed = op._judge_draft_predicate(
        "svc-t-02", "accept_after_release", partial
    )
    assert confirmed.status is VerdictStatus.VIOLATED


@pytest.mark.parametrize(
    "released,accepted,owner_exit,responder_enter,expected",
    [
        (5, 20, 10, 20, VerdictStatus.VIOLATED),
        (10, 20, 10, 10, VerdictStatus.VIOLATED),
        (10, 20, 5, 20, VerdictStatus.DATA_INSUFFICIENT),
        (10, 20, 10, 25, VerdictStatus.DATA_INSUFFICIENT),
    ],
)
def test_service_handoff_boundary_mismatch_monotonic_directions(
    released, accepted, owner_exit, responder_enter, expected
):
    stream = PredicateStreams(
        milestones=[
            MilestoneEvent("released", released),
            MilestoneEvent("accepted", accepted),
        ],
        zone_intervals=[
            ZoneInterval("access_point", "case_owner", 0, owner_exit),
            ZoneInterval("access_point", "responder", responder_enter, 30),
        ],
        integrity_confirmed=False,
    )
    verdict = op._judge_draft_predicate(
        "svc-t-02", "accept_after_release", stream
    )
    assert verdict.status is expected
    if expected is VerdictStatus.DATA_INSUFFICIENT:
        assert verdict.cause is CauseCode.EVALUATOR_RECORD_MISSING


def test_service_handoff_observed_overlap_remains_violation_unconfirmed():
    stream = PredicateStreams(
        milestones=[MilestoneEvent("released", 10), MilestoneEvent("accepted", 9)],
        zone_intervals=[
            ZoneInterval("access_point", "case_owner", 0, 10),
            ZoneInterval("access_point", "responder", 9, 20),
        ],
        integrity_confirmed=False,
    )
    assert op._judge_draft_predicate(
        "svc-t-02", "accept_after_release", stream
    ).status is VerdictStatus.VIOLATED
