"""Tests for demo.benchmark.goal_temporal_metrics (protocol batch 3).

Covers the P0-3 frozen time anchors (real termination, right-censored
non-inference, last-observation-is-not-termination counterexample, native
receipt source), the precedence/synchronization E2E triples with
denominator discipline, signed handoff gaps, mutex overlap, unnecessary
waits with unstarted opportunities preserved, and the offline
coordination-stall state machine including confirmation, delta_stall
threshold, Q-tolerance interruption, recovery predicate, right-censoring
and N/A conditions.  Release flags must be false everywhere.
"""

from demo.benchmark.goal_temporal_metrics import (
    ANCHOR_SOURCE_HOST_EVENT,
    ANCHOR_SOURCE_NATIVE_RECEIPT,
    ANCHOR_SOURCE_UNVERIFIED,
    TERMINATION_UNVERIFIED,
    coordination_stall_metrics,
    frozen_time_anchors,
    handoff_gap_metrics,
    mutex_metrics,
    precedence_metrics,
    synchronization_metrics,
    unnecessary_wait_metrics,
)


def _event(etype, time_s, sequence, **payload):
    return {"type": etype, "time_s": time_s, "sequence": sequence,
            "payload": payload}


def _accepted(action_id, time_s, sequence):
    return _event("action_result", time_s, sequence, action_id=action_id,
                  status="accepted", reason="ledger admission; no execution claim")


def _terminal(action_id, time_s, sequence, status="succeeded"):
    return _event("action_result", time_s, sequence, action_id=action_id,
                  status=status,
                  reason="trusted backend terminal receipt recorded")


def _report(events, actions=None, tasks=None):
    return {"events": list(events), "actions": list(actions or []),
            "tasks": dict(tasks or {})}


def _terminated_report(extra_events=(), end_s=100.0):
    events = [_event("episode_started", 0.0, 0, public_task_digest="digest")]
    events.extend(extra_events)
    events.append(_event("human_assistance_requested", end_s,
                         len(events), assessment_id="a0", assessment_epoch=0))
    return _report(events)


# ---------------------------------------------------------------------------
# Frozen time anchors (viewer P0-3)
# ---------------------------------------------------------------------------

def test_frozen_anchors_real_termination_from_host_event():
    report = _terminated_report(
        extra_events=[_accepted("a1", 10.0, 1), _terminal("a1", 40.0, 2)],
        end_s=50.0)
    anchors = frozen_time_anchors(report)
    assert anchors["episode_start"] == {"time_s": 0.0,
                                        "source": ANCHOR_SOURCE_HOST_EVENT,
                                        "event_sequence": 0}
    termination = anchors["termination"]
    assert termination["time_s"] == 50.0
    assert termination["source"] == ANCHOR_SOURCE_HOST_EVENT
    assert termination["record"] == "human_assistance_requested_event"
    assert anchors["censoring"]["termination_right_censored"] is False
    assert anchors["status"] == "frozen"


def test_frozen_anchors_unverified_termination_never_inferred():
    # Archive-wrapper form ({"episode_id": ..., "report": {...}}) with no
    # host termination record anywhere.
    inner = _report([
        _event("episode_started", 0.0, 0, public_task_digest="digest"),
        _accepted("a1", 10.0, 1),
        _terminal("a1", 45.0, 2),
    ])
    anchors = frozen_time_anchors({"episode_id": "ep", "report": inner})
    assert anchors["episode_start"]["time_s"] == 0.0
    assert anchors["termination"]["time_s"] is None
    assert anchors["termination"]["source"] == ANCHOR_SOURCE_UNVERIFIED
    assert anchors["termination"]["label"] == TERMINATION_UNVERIFIED
    assert anchors["termination"]["label"] == "termination_unverified_do_not_infer"
    assert anchors["censoring"]["termination_right_censored"] is True


def test_frozen_anchors_last_observation_is_not_termination():
    # Counterexample for the stage65 drift where the last milestone
    # observation time masquerades as the episode end: the latest ledger
    # event sits at t=190 with milestone-looking payloads, yet the
    # termination anchor must stay unverified rather than borrow 190.
    events = [
        _event("episode_started", 0.0, 0, public_task_digest="digest"),
        _event("observation", 120.0, 1, message_id="m1"),
        _event("observation", 190.0, 2, message_id="m2"),
        _terminal("a_last", 190.0, 3),
    ]
    native_log = {"records": [
        {"kind": "arrival", "arrival": {"time_s": 30.0}},
        {"kind": "observation", "arrival": {"time_s": 190.0}},
        {"kind": "relay_and_receipt", "receipt_stamp": {"time_s": 190.0}},
    ]}
    anchors = frozen_time_anchors(_report(events), native_log)
    assert anchors["termination"]["time_s"] is None
    assert anchors["termination"]["label"] == TERMINATION_UNVERIFIED
    assert anchors["last_event_context"]["time_s"] == 190.0
    assert anchors["last_event_context"]["usage"] == "context_only_not_a_time_anchor"
    # Ordinary evidence kinds never act as a native termination receipt.
    assert "last_milestone_observation" in anchors["forbidden_termination_sources"]


def test_frozen_anchors_native_receipt_source():
    events = [_event("episode_started", 0.0, 0, public_task_digest="digest")]
    native_log = {"records": [
        {"kind": "arrival", "arrival": {"time_s": 10.0}},
        {"kind": "episode_terminated", "arrival": {"time_s": 88.0}},
    ]}
    anchors = frozen_time_anchors(_report(events), native_log)
    assert anchors["termination"]["time_s"] == 88.0
    assert anchors["termination"]["source"] == ANCHOR_SOURCE_NATIVE_RECEIPT
    assert anchors["termination"]["record_index"] == 1
    assert anchors["status"] == "frozen"


# ---------------------------------------------------------------------------
# Precedence relations
# ---------------------------------------------------------------------------

def test_precedence_satisfied_triple():
    report = _terminated_report(extra_events=[
        _accepted("pred", 10.0, 1), _accepted("succ", 15.0, 2)])
    relations = [{
        "relation_id": "p1",
        "predecessor_event": {"type": "action_result",
                              "payload": {"action_id": "pred"}},
        "successor_event": {"type": "action_result",
                            "payload": {"action_id": "succ"}},
        "min_gap_s": 0.0, "max_gap_s": 10.0,
    }]
    result = precedence_metrics(report, relations)
    assert result["status"] == "computed"
    assert result["relations"][0]["status"] == "satisfied"
    assert result["relations"][0]["gap_s"] == 5.0
    assert result["PrecE2E"] == 1.0
    assert result["PrecOpportunity"] == 1.0
    assert result["PrecGivenOpportunity"] == 1.0
    assert result["n_evaluable"] == 1


def test_precedence_successor_before_predecessor_violated():
    events = [_event("episode_started", 0.0, 0, public_task_digest="d"),
              _accepted("pred", 10.0, 1), _accepted("succ", 8.0, 2)]
    swapped = precedence_metrics(_report(events), [{
        "relation_id": "p1",
        "predecessor_event": {"type": "action_result",
                              "payload": {"action_id": "pred"}},
        "successor_event": {"type": "action_result",
                            "payload": {"action_id": "succ"}},
        "min_gap_s": 0.0, "max_gap_s": 10.0}])
    assert swapped["relations"][0]["status"] == "violated_successor_before_predecessor"
    assert swapped["relations"][0]["precedence_ok"] == 0
    assert swapped["PrecE2E"] == 0.0
    assert swapped["PrecOpportunity"] == 1.0
    assert swapped["PrecGivenOpportunity"] == 0.0
    # Successor observed while the predecessor never appeared: the same
    # immediate violation (append-only ledger prefix).
    successor_only = precedence_metrics(_report(events), [{
        "relation_id": "p2",
        "predecessor_event": {"type": "action_result",
                              "payload": {"action_id": "ghost"}},
        "successor_event": {"type": "action_result",
                            "payload": {"action_id": "succ"}},
        "min_gap_s": 0.0, "max_gap_s": 10.0}])
    assert successor_only["relations"][0]["status"] == "violated_successor_before_predecessor"
    assert successor_only["PrecE2E"] == 0.0
    assert successor_only["PrecOpportunity"] == 1.0


def test_precedence_not_reached_stays_in_denominator():
    report = _terminated_report(extra_events=[
        _accepted("pred", 10.0, 1), _accepted("succ", 15.0, 2)])
    result = precedence_metrics(report, [
        {"relation_id": "p_ok",
         "predecessor_event": {"type": "action_result",
                               "payload": {"action_id": "pred"}},
         "successor_event": {"type": "action_result",
                             "payload": {"action_id": "succ"}},
         "min_gap_s": 0.0, "max_gap_s": 10.0},
        {"relation_id": "p_never",
         "predecessor_event": {"type": "action_result",
                               "payload": {"action_id": "ghost1"}},
         "successor_event": {"type": "action_result",
                             "payload": {"action_id": "ghost2"}},
         "min_gap_s": 0.0, "max_gap_s": 10.0},
    ])
    assert result["not_reached_system_count"] == 1
    assert result["relations"][1]["status"] == "not_reached_system"
    # The never-reached relation stays in the E2E denominator (1/2), while
    # the conditional rate over formed opportunities stays 1/1.
    assert result["PrecE2E"] == 0.5
    assert result["PrecOpportunity"] == 0.5
    assert result["PrecGivenOpportunity"] == 1.0


def test_precedence_zero_opportunities_given_na():
    result = precedence_metrics(None, [{
        "relation_id": "p1",
        "predecessor_event": {"type": "action_result",
                              "payload": {"action_id": "x"}},
        "successor_event": {"type": "action_result",
                            "payload": {"action_id": "y"}},
        "min_gap_s": 0.0, "max_gap_s": 5.0}])
    assert result["PrecGivenOpportunity"] is None
    assert result["PrecGivenOpportunity_note"] == "na_zero_opportunities"
    # Zero opportunities is not zero E2E: the relation is still unmet mass.
    assert result["PrecE2E"] == 0.0


def test_precedence_not_evaluable_external_excluded_with_disclosure():
    report = _terminated_report(extra_events=[
        _accepted("pred", 10.0, 1), _accepted("succ", 15.0, 2)])
    result = precedence_metrics(report, [
        {"relation_id": "p_ok",
         "predecessor_event": {"type": "action_result",
                               "payload": {"action_id": "pred"}},
         "successor_event": {"type": "action_result",
                             "payload": {"action_id": "succ"}},
         "min_gap_s": 0.0, "max_gap_s": 10.0},
        {"relation_id": "p_fault",
         "predecessor_event": {"type": "action_result",
                               "payload": {"action_id": "ghost1"}},
         "successor_event": {"type": "action_result",
                             "payload": {"action_id": "ghost2"}},
         "min_gap_s": 0.0, "max_gap_s": 10.0,
         "not_evaluable_external_reason":
             "evaluator_injected_infrastructure_fault"},
    ])
    assert result["n_relations_total"] == 2
    assert result["n_evaluable"] == 1
    assert result["n_not_evaluable_external"] == 1
    assert result["not_evaluable_external"][0]["relation_id"] == "p_fault"
    assert (result["not_evaluable_external"][0]["reason"]
            == "evaluator_injected_infrastructure_fault")
    assert result["PrecE2E"] == 1.0  # excluded from the denominator entirely


def test_precedence_missing_successor_deadline_after_end_right_censored():
    # Opportunity formed at t=80 with a 30 s allowance, but the trusted
    # termination sits at t=100: the deadline never arrived inside the
    # observation window, so the fate is right-censored and the relation
    # stays unmet mass in PrecE2E rather than being scored satisfied.
    report = _terminated_report(extra_events=[_accepted("pred", 80.0, 1)])
    result = precedence_metrics(report, [{
        "relation_id": "p1",
        "predecessor_event": {"type": "action_result",
                              "payload": {"action_id": "pred"}},
        "successor_event": {"type": "action_result",
                            "payload": {"action_id": "never"}},
        "min_gap_s": 0.0, "max_gap_s": 30.0}])
    row = result["relations"][0]
    assert row["status"] == "right_censored_deadline_after_observation_end"
    assert row["right_censored"] is True
    assert row["deadline_s"] == 110.0
    assert result["PrecE2E"] == 0.0
    assert result["PrecOpportunity"] == 1.0
    assert result["PrecGivenOpportunity"] == 0.0


# ---------------------------------------------------------------------------
# Synchronization groups
# ---------------------------------------------------------------------------

def _milestone_event(milestone_id, time_s, sequence):
    return _event("milestone_reached", time_s, sequence,
                  milestone_id=milestone_id)


def test_sync_within_window_satisfied():
    report = _terminated_report(extra_events=[
        _milestone_event("m1", 10.0, 1),
        _milestone_event("m2", 12.0, 2),
        _milestone_event("m3", 14.0, 3)])
    result = synchronization_metrics(report, [{
        "group_id": "g1", "window_s": 5.0,
        "events": [
            {"type": "milestone_reached", "payload": {"milestone_id": "m1"}},
            {"type": "milestone_reached", "payload": {"milestone_id": "m2"}},
            {"type": "milestone_reached", "payload": {"milestone_id": "m3"}},
        ]}])
    row = result["groups"][0]
    assert row["status"] == "satisfied"
    assert row["delta_g_s"] == 4.0
    assert result["SyncE2E"] == 1.0
    assert result["SyncOpportunity"] == 1.0
    assert result["SyncGivenOpportunity"] == 1.0


def test_sync_violated_window_exceeded():
    report = _terminated_report(extra_events=[
        _milestone_event("m1", 10.0, 1), _milestone_event("m2", 20.0, 2)])
    result = synchronization_metrics(report, [{
        "group_id": "g1", "window_s": 5.0,
        "events": [
            {"type": "milestone_reached", "payload": {"milestone_id": "m1"}},
            {"type": "milestone_reached", "payload": {"milestone_id": "m2"}},
        ]}])
    row = result["groups"][0]
    assert row["status"] == "violated_window_exceeded"
    assert row["delta_g_s"] == 10.0
    assert result["SyncE2E"] == 0.0
    assert result["SyncGivenOpportunity"] == 0.0


def test_sync_violated_required_event_missing_and_not_reached():
    # Group A: opportunity formed (m1 observed) but the required m4 never
    # appeared while the deadline (10 + 5) passed inside the trusted
    # observation window -> violated.  Group B never reached at all ->
    # not_reached_system, still in the denominator.
    report = _terminated_report(extra_events=[_milestone_event("m1", 10.0, 1)])
    result = synchronization_metrics(report, [
        {"group_id": "gA", "window_s": 5.0,
         "events": [
             {"type": "milestone_reached", "payload": {"milestone_id": "m1"}},
             {"type": "milestone_reached", "payload": {"milestone_id": "m4"}},
         ]},
        {"group_id": "gB", "window_s": 5.0,
         "events": [
             {"type": "milestone_reached", "payload": {"milestone_id": "m5"}},
             {"type": "milestone_reached", "payload": {"milestone_id": "m6"}},
         ]},
    ])
    assert result["groups"][0]["status"] == "violated_required_event_missing"
    assert result["groups"][1]["status"] == "not_reached_system"
    assert result["not_reached_system_count"] == 1
    assert result["SyncE2E"] == 0.0
    assert result["SyncOpportunity"] == 0.5
    assert result["SyncGivenOpportunity"] == 0.0


# ---------------------------------------------------------------------------
# Handoff gaps
# ---------------------------------------------------------------------------

def test_handoff_signed_gaps_and_continuity():
    report = _terminated_report(extra_events=[
        _terminal("carrier_a", 20.0, 1),   # old provider a ends
        _accepted("carrier_b", 25.0, 2),   # new provider b starts late
        _terminal("carrier_c", 40.0, 3),
        _accepted("carrier_d", 38.0, 4),   # overlap: starts before c ends
    ])
    result = handoff_gap_metrics(report, [
        {"handoff_id": "h1", "max_gap_s": 3.0,
         "start_event": {"type": "action_result",
                         "payload": {"action_id": "carrier_b"}},
         "end_event": {"type": "action_result",
                       "payload": {"action_id": "carrier_a"}}},
        {"handoff_id": "h2", "max_gap_s": 3.0,
         "start_event": {"type": "action_result",
                         "payload": {"action_id": "carrier_d"}},
         "end_event": {"type": "action_result",
                       "payload": {"action_id": "carrier_c"}}},
        {"handoff_id": "h3", "max_gap_s": 3.0,
         "start_event": {"type": "action_result",
                         "payload": {"action_id": "never_started"}},
         "end_event": {"type": "action_result",
                       "payload": {"action_id": "carrier_c"}}},
    ])
    rows = {row["handoff_id"]: row for row in result["handoffs"]}
    assert rows["h1"]["G_h_s"] == 5.0          # positive: service gap
    assert rows["h1"]["semantics"] == "gap"
    assert rows["h1"]["continuous"] is False
    assert rows["h2"]["G_h_s"] == -2.0         # negative: overlap
    assert rows["h2"]["semantics"] == "overlap"
    assert rows["h2"]["continuous"] is True
    assert rows["h3"]["status"] == "not_evaluable_missing_start_event"
    assert result["n_evaluable"] == 2
    assert result["n_missing_endpoint"] == 1
    assert result["continuity_rate"] == 0.5
    assert result["G_h_mean_s"] == 1.5


# ---------------------------------------------------------------------------
# Mutex violations
# ---------------------------------------------------------------------------

def test_mutex_violation_and_overlap_ratio():
    evidence = {"resource_intervals": {
        "route:r1": [
            {"task_id": "t1", "start_s": 0.0, "end_s": 10.0},
            {"task_id": "t2", "start_s": 8.0, "end_s": 20.0},
        ],
        "region:kitchen": [
            {"task_id": "t3", "start_s": 0.0, "end_s": 5.0},
        ],
    }}
    result = mutex_metrics(evidence)
    assert result["status"] == "computed"
    assert result["MutexAny_episode"] is True
    assert result["overlap_time_s"] == 2.0
    assert result["occupied_time_s"] == 25.0
    assert abs(result["MutexOverlap"] - 2.0 / 25.0) < 1e-12


def test_mutex_no_occupancy_na():
    result = mutex_metrics({"resource_intervals": {}})
    assert result["status"] == "na_no_resource_occupancy"
    assert result["MutexOverlap"] is None
    # Disjoint occupancies never double-hold: no violation, zero overlap.
    clean = mutex_metrics({"resource_intervals": {
        "route:r1": [
            {"task_id": "t1", "start_s": 0.0, "end_s": 10.0},
            {"task_id": "t2", "start_s": 10.0, "end_s": 20.0},  # touching
        ]}})
    assert clean["MutexAny_episode"] is False
    assert clean["MutexOverlap"] == 0.0


# ---------------------------------------------------------------------------
# Unnecessary waits
# ---------------------------------------------------------------------------

def test_unnecessary_wait_stats_and_unstarted_marked():
    report = _terminated_report()  # trusted observation end at 100 s
    result = unnecessary_wait_metrics(report, [
        {"opportunity_id": "o1", "ready_at_s": 10.0, "start_at_s": 22.0},
        {"opportunity_id": "o2", "ready_at_s": 10.0, "start_at_s": 15.0},
        {"opportunity_id": "o3", "ready_at_s": 20.0, "start_at_s": None},
    ])
    assert result["status"] == "computed"
    assert result["n_opportunities"] == 3
    assert result["n_started"] == 2
    assert result["n_not_started"] == 1
    assert abs(result["not_started_fraction"] - 1 / 3) < 1e-12
    assert result["total_unnecessary_wait_s"] == 17.0
    assert result["mean_wait_s"] == 8.5
    assert result["median_wait_s"] == 8.5
    assert result["p90_wait_s"] == 12.0  # nearest-rank ceiling on [5, 12]
    rows = {row["opportunity_id"]: row for row in result["opportunities"]}
    assert rows["o3"]["status"] == "not_started"
    assert rows["o3"]["wait_s"] is None
    # A start before readiness clamps to zero wait (protocol formula) and
    # the raw negative gap is disclosed, not hidden.
    clamped = unnecessary_wait_metrics(report, [
        {"opportunity_id": "o4", "ready_at_s": 10.0, "start_at_s": 4.0}])
    assert clamped["opportunities"][0]["wait_s"] == 0.0
    assert clamped["n_negative_raw_gap"] == 1


# ---------------------------------------------------------------------------
# Coordination stall (offline state machine)
# ---------------------------------------------------------------------------

def _flat_q(horizon=100.0):
    return {"Q_samples": [{"t_s": 0.0, "Q": 0.0}, {"t_s": horizon, "Q": 0.0}]}


def test_stall_confirmed_with_recovery():
    report = _terminated_report()
    result = coordination_stall_metrics(
        report,
        delta_stall_s=20.0, delta_clear_s=10.0, epsilon_q=0.1,
        q_curve=_flat_q(), action_timeout_table={},
        opportunity_windows=[{"opportunity_id": "o1", "ready_at_s": 0.0}],
        recovery_windows=[[50.0, 100.0]])
    assert result["status"] == "computed"
    assert result["M_stall"] == 1
    assert result["S_i_stall_indicator"] == 1
    assert result["stalls"][0]["T_on_s"] == 0.0
    assert result["stalls"][0]["T_det_s"] == 20.0
    # Recovery predicate (external progress held >= delta_clear) fires at
    # max(T_det, 50 + 10) = 60 and is genuinely observed before censoring.
    assert result["stalls"][0]["T_rec_s"] == 60.0
    assert result["stalls"][0]["recovery_observed"] is True
    assert result["StallRecoveryObserved"] == 1.0
    assert result["L_stall_obs_s"] == 60.0
    assert result["first_stall_right_censored_pair"] == {
        "tilde_tau_det_s": 20.0, "delta_i": 1}


def test_stall_below_delta_stall_not_confirmed():
    # The only ready opportunity finishes at t=10 (< delta_stall=20), so the
    # candidate stall never reaches the confirmation threshold.
    report = _terminated_report()
    result = coordination_stall_metrics(
        report,
        delta_stall_s=20.0, delta_clear_s=10.0, epsilon_q=0.1,
        q_curve=_flat_q(), action_timeout_table={},
        opportunity_windows=[{"opportunity_id": "o1", "ready_at_s": 0.0,
                              "satisfied_at_s": 10.0}])
    assert result["candidate_intervals"] == [{"start_s": 0.0, "end_s": 10.0}]
    assert result["M_stall"] == 0
    assert result["S_i_stall_indicator"] == 0
    assert result["StallRate"] == 0.0
    # No stall => first-stall time is right-censored at the horizon, and
    # the episode indicator is 0 (the horizon is never a stall time).
    assert result["first_stall_right_censored_pair"] == {
        "tilde_tau_det_s": 100.0, "delta_i": 0}
    assert result["StallRecoveryObserved"] is None
    assert result["StallRecoveryObserved_note"] == "na_zero_confirmed_stalls"


def test_stall_interrupted_by_q_progress_and_retimed():
    # Q jumps from 0 to 1 at t=30 (> epsilon_q): the first candidate [0, 30]
    # cannot confirm (Q(e)-Q(s) exceeds the tolerance) and timing restarts
    # at t=30 with the new baseline, confirming [30, 100].
    q_curve = {"Q_samples": [{"t_s": 0.0, "Q": 0.0},
                             {"t_s": 30.0, "Q": 1.0}]}
    report = _terminated_report()
    result = coordination_stall_metrics(
        report,
        delta_stall_s=20.0, delta_clear_s=10.0, epsilon_q=0.1,
        q_curve=q_curve, action_timeout_table={},
        opportunity_windows=[{"opportunity_id": "o1", "ready_at_s": 0.0}])
    assert result["candidate_intervals"] == [
        {"start_s": 0.0, "end_s": 30.0}, {"start_s": 30.0, "end_s": 100.0}]
    assert result["M_stall"] == 1
    assert result["stalls"][0]["T_on_s"] == 30.0
    assert result["stalls"][0]["T_det_s"] == 50.0


def test_stall_recovery_window_shorter_than_delta_clear_not_recovery():
    # A predicate-true window of 5 s cannot satisfy delta_clear=10, so the
    # recovery stays right-censored even though a window was supplied.
    report = _terminated_report()
    result = coordination_stall_metrics(
        report,
        delta_stall_s=20.0, delta_clear_s=10.0, epsilon_q=0.1,
        q_curve=_flat_q(), action_timeout_table={},
        opportunity_windows=[{"opportunity_id": "o1", "ready_at_s": 0.0}],
        recovery_windows=[[40.0, 45.0]])
    assert result["M_stall"] == 1
    row = result["stalls"][0]
    assert row["T_rec_s"] is None
    assert row["recovery_observed"] is False
    assert row["L_obs_s"] == 100.0
    assert result["StallRecoveryObserved"] == 0.0


def test_stall_right_censored_without_recovery_predicate():
    # No recovery predicate supplied: the observation is right-censored at
    # the horizon and the stall length is only a restricted observation.
    report = _terminated_report()
    result = coordination_stall_metrics(
        report,
        delta_stall_s=20.0, delta_clear_s=10.0, epsilon_q=0.1,
        q_curve=_flat_q(), action_timeout_table={},
        opportunity_windows=[{"opportunity_id": "o1", "ready_at_s": 0.0}])
    row = result["stalls"][0]
    assert row["recovery_observed"] is False
    assert row["censor_reason"] == "right_censored_no_recovery_predicate"
    assert row["L_obs_s"] == 100.0
    assert result["L_stall_obs_s"] == 100.0
    assert result["L_stall_obs_restricted_at_horizon"] is True
    # Same episode without any termination record: the horizon itself is
    # right-censored context (last event at 90) and the censor reason says
    # so; termination is still not invented.
    unverified = _report([
        _event("episode_started", 0.0, 0, public_task_digest="d"),
        _terminal("a1", 90.0, 1),
    ])
    result2 = coordination_stall_metrics(
        unverified,
        delta_stall_s=20.0, delta_clear_s=10.0, epsilon_q=0.1,
        q_curve=_flat_q(horizon=90.0), action_timeout_table={},
        opportunity_windows=[{"opportunity_id": "o1", "ready_at_s": 0.0}])
    assert result2["horizon"]["right_censored"] is True
    assert result2["horizon"]["time_s"] == 90.0
    assert TERMINATION_UNVERIFIED in result2["stalls"][0]["censor_reason"]


def test_stall_no_opportunities_na():
    report = _terminated_report()
    kwargs = dict(delta_stall_s=20.0, delta_clear_s=10.0, epsilon_q=0.1,
                  q_curve=_flat_q(), action_timeout_table={})
    no_source = coordination_stall_metrics(report, opportunity_windows=None,
                                           **kwargs)
    assert no_source["status"] == "na_no_opportunity_source"
    empty = coordination_stall_metrics(report, opportunity_windows=[], **kwargs)
    assert empty["status"] == "na_no_opportunities"


def test_stall_eligible_action_and_timeout_boundary():
    # A plan-bound dispatch suppresses candidacy while eligible; once its
    # T_a^max expires without a terminal receipt it times out, leaves the
    # eligible set, and the subsequent idle window is a genuine stall.
    report = _terminated_report(extra_events=[_accepted("a1", 5.0, 1)])
    report["actions"] = [{"action_id": "a1", "type": "dispatch_skill",
                          "payload": {"task_id": "t1"}}]
    report["tasks"] = {"t1": {"status": "pending"}}
    result = coordination_stall_metrics(
        report,
        delta_stall_s=20.0, delta_clear_s=10.0, epsilon_q=0.1,
        q_curve=_flat_q(), action_timeout_table={"dispatch_skill": 30.0},
        opportunity_windows=[{"opportunity_id": "o1", "ready_at_s": 0.0}])
    assert result["action_timeouts"] == [
        {"action_id": "a1", "type": "dispatch_skill",
         "started_at_s": 5.0, "timeout_at_s": 35.0}]
    assert result["candidate_intervals"] == [
        {"start_s": 0.0, "end_s": 5.0}, {"start_s": 35.0, "end_s": 100.0}]
    assert result["M_stall"] == 1
    assert result["stalls"][0]["T_on_s"] == 35.0
    assert result["stalls"][0]["T_det_s"] == 55.0
    assert result["action_diagnostics"]["n_dispatch_not_bound_to_plan"] == 0


def test_stall_unbound_dispatch_not_eligible():
    # A dispatch whose task_id belongs to no stored plan task is not
    # explicitly bound to a declared plan action: it never enters the
    # eligible set and the ready-opportunity idleness stays a stall.
    report = _terminated_report(extra_events=[_accepted("a1", 5.0, 1)])
    report["actions"] = [{"action_id": "a1", "type": "dispatch_skill",
                          "payload": {"task_id": "t_unknown"}}]
    report["tasks"] = {"t1": {"status": "pending"}}
    result = coordination_stall_metrics(
        report,
        delta_stall_s=20.0, delta_clear_s=10.0, epsilon_q=0.1,
        q_curve=_flat_q(), action_timeout_table={"dispatch_skill": 30.0},
        opportunity_windows=[{"opportunity_id": "o1", "ready_at_s": 0.0}])
    assert result["action_diagnostics"]["n_dispatch_not_bound_to_plan"] == 1
    assert result["candidate_intervals"] == [{"start_s": 0.0, "end_s": 100.0}]
    assert result["stalls"][0]["T_on_s"] == 0.0


# ---------------------------------------------------------------------------
# Release discipline
# ---------------------------------------------------------------------------

def test_release_flags_always_false():
    report = _terminated_report()
    outputs = [
        frozen_time_anchors(report),
        precedence_metrics(report, []),
        synchronization_metrics(report, []),
        handoff_gap_metrics(report, []),
        mutex_metrics({"resource_intervals": {}}),
        unnecessary_wait_metrics(report, []),
        coordination_stall_metrics(
            report, delta_stall_s=20.0, delta_clear_s=10.0, epsilon_q=0.1,
            q_curve=_flat_q(), action_timeout_table={},
            opportunity_windows=[]),
    ]
    for output in outputs:
        assert output["release"] is False
        assert output["scoreable"] is False
        assert output["execution_validated"] is False
        assert output["business_outcomes_validated"] is False
