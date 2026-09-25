from demo.benchmark.goal_declaration_cost_snapshot import (
    DECLARATION_COST_SCHEMA,
    build_declaration_cost_snapshots,
)


def _session(*, actions, events, history):
    return {"episode_id": "ep-1", "report": {
        "episode_id": "ep-1", "actions": actions, "events": events,
        "deployment_assessment": {"history": history}}}


def _action(action_id, action_type, at, payload=None):
    return {"schema": "eai.benchmark.goal_action", "version": 1,
            "action_id": action_id, "submitted_at_s": at,
            "type": action_type, "payload": payload or {}}


def _event(sequence, event_type, at, payload=None):
    return {"schema": "eai.benchmark.goal_event", "version": 1,
            "sequence": sequence, "type": event_type, "time_s": at,
            "payload": payload or {}}


def _communication(ledger):
    return {"ledger": ledger,
            "byte_accounting": {"semantics": "v4_mixed"}}


def _served(time_s, size, pool="p2p"):
    return {"event": "bytes_served", "time_s": time_s, "bytes": size,
            "pool": pool}


def _history(assessment_id, epoch, action_id, at, decision="deployable"):
    return {"assessment_id": assessment_id, "assessment_epoch": epoch,
            "decision_action_id": action_id, "declared_at_s": at,
            "decision": decision}


def test_snapshot_cuts_costs_at_the_declaration_boundary():
    actions = [_action("a1", "select_team", 1.0),
               _action("a2", "dispatch_skill", 4.0),
               _action("d1", "deployment_assessment", 6.0),
               _action("a3", "dispatch_skill", 8.0)]
    events = [_event(0, "episode_started", 0.0),
              _event(1, "action_result", 1.0, {"action_id": "a1"}),
              _event(2, "action_result", 4.0, {"action_id": "a2"}),
              _event(3, "action_result", 6.0, {"action_id": "d1"}),
              _event(4, "tick", 8.0),
              _event(5, "action_result", 8.0, {"action_id": "a3"})]
    report = build_declaration_cost_snapshots(
        _session(actions=actions, events=events,
                 history=[_history("as-1", 0, "d1", 6.0)]),
        communication_report=_communication([
            _served(2.0, 100), _served(5.0, 50), _served(7.0, 999)]))
    assert report["status"] == "snapshotted"
    assert report["snapshot_count"] == 1
    snapshot = report["snapshots"][0]
    pre = snapshot["pre_declaration"]
    assert pre["action_count"] == 2
    assert pre["event_count"] == 3  # episode_started + two pre-declaration results
    assert pre["tick_count"] == 0
    assert pre["communication"]["pre_boundary_bytes"] == 150
    assert pre["communication"]["bytes_by_pool"] == {"p2p": 150}
    assert snapshot["boundary"]["ordering_basis"] == "actual_call_order"
    assert report["scoreable"] is False


def test_same_timestamp_events_split_by_actual_call_order():
    # At t=6.0: an event before the declaration's own result (sequence 3)
    # is pre-declaration; the declaration result itself is not.
    actions = [_action("a1", "dispatch_skill", 6.0),
               _action("d1", "deployment_assessment", 6.0)]
    events = [_event(0, "observation", 6.0),
              _event(1, "action_result", 6.0, {"action_id": "a1"}),
              _event(2, "action_result", 6.0, {"action_id": "d1"})]
    report = build_declaration_cost_snapshots(
        _session(actions=actions, events=events,
                 history=[_history("as-1", 0, "d1", 6.0)]))
    pre = report["snapshots"][0]["pre_declaration"]
    assert pre["event_count"] == 2
    assert report["snapshots"][0]["boundary"]["boundary_event_sequence"] == 2


def test_boundary_timestamp_communication_rows_are_segregated():
    report = build_declaration_cost_snapshots(
        _session(actions=[_action("d1", "deployment_assessment", 5.0)],
                 events=[_event(0, "episode_started", 0.0)],
                 history=[_history("as-1", 0, "d1", 5.0)]),
        communication_report=_communication([
            _served(1.0, 10), _served(5.0, 70), _served(5.0, 30),
            _served(6.0, 400)]))
    comm = report["snapshots"][0]["pre_declaration"]["communication"]
    assert comm["pre_boundary_bytes"] == 10
    assert comm["at_boundary_not_attributed_bytes"] == 100
    assert comm["total_physical_progress"] is False
    assert comm["byte_accounting"] == {"semantics": "v4_mixed"}


def test_multiple_declarations_freeze_independent_snapshots():
    actions = [_action("d1", "deployment_assessment", 3.0),
               _action("a1", "dispatch_skill", 4.0),
               _action("d2", "deployment_assessment", 7.0)]
    events = [_event(0, "episode_started", 0.0),
              _event(1, "action_result", 3.0, {"action_id": "d1"}),
              _event(2, "action_result", 4.0, {"action_id": "a1"}),
              _event(3, "action_result", 7.0, {"action_id": "d2"})]
    report = build_declaration_cost_snapshots(
        _session(actions=actions, events=events,
                 history=[_history("as-1", 0, "d1", 3.0),
                          _history("as-2", 1, "d2", 7.0, "not_deployable")]),
        communication_report=_communication([_served(5.0, 60)]))
    assert report["snapshot_count"] == 2
    first, second = report["snapshots"]
    assert first["pre_declaration"]["action_count"] == 0
    assert first["pre_declaration"]["communication"]["pre_boundary_bytes"] == 0
    assert second["pre_declaration"]["action_count"] == 2  # d1 + a1 precede d2
    assert second["pre_declaration"]["communication"]["pre_boundary_bytes"] == 60
    assert second["decision"] == "not_deployable"


def test_missing_communication_is_unmeasured_not_zero():
    report = build_declaration_cost_snapshots(
        _session(actions=[_action("d1", "deployment_assessment", 2.0)],
                 events=[_event(0, "episode_started", 0.0)],
                 history=[_history("as-1", 0, "d1", 2.0)]))
    comm = report["snapshots"][0]["pre_declaration"]["communication"]
    assert comm["available"] is False
    assert comm["pre_boundary_bytes"] is None
    assert comm["reason"] == "communication_report_missing"


def test_planning_costs_only_when_host_recorded():
    actions = [_action("d1", "deployment_assessment", 2.0)]
    events = [_event(0, "episode_started", 0.0)]
    base = _session(actions=actions, events=events,
                    history=[_history("as-1", 0, "d1", 2.0)])
    unmeasured = build_declaration_cost_snapshots(base)
    planning = unmeasured["snapshots"][0]["pre_declaration"]["planning_compute"]
    assert planning == {"planning_s": None, "replanning_s": None,
                        "recorded": False}
    recorded = build_declaration_cost_snapshots(
        base, planning_costs={"planning_s": 0.8, "replanning_s": 0.2})
    planning = recorded["snapshots"][0]["pre_declaration"]["planning_compute"]
    assert planning == {"planning_s": 0.8, "replanning_s": 0.2,
                        "recorded": True}


def test_physical_progress_cost_stays_null_with_reason():
    report = build_declaration_cost_snapshots(
        _session(actions=[_action("d1", "deployment_assessment", 2.0)],
                 events=[_event(0, "episode_started", 0.0)],
                 history=[_history("as-1", 0, "d1", 2.0)]))
    pre = report["snapshots"][0]["pre_declaration"]
    assert pre["physical_progress_cost"] is None
    assert pre["physical_progress_reason"] == (
        "not_measured_at_declaration_boundary")


def test_legacy_archive_without_declarations_is_explicitly_unmeasured():
    report = build_declaration_cost_snapshots(
        {"episode_id": "legacy", "report": {
            "episode_id": "legacy",
            "actions": [_action("a1", "dispatch_skill", 1.0)],
            "events": [_event(0, "episode_started", 0.0)],
            "deployment_assessment": {}}})
    assert report["status"] == "no_declarations"
    assert report["snapshots"] == []
    assert report["unmeasured_note"] == (
        "declaration_granularity_unavailable_not_zero")
    assert report["scoreable"] is False


def test_unresolvable_declaration_action_fails_closed():
    report = build_declaration_cost_snapshots(
        _session(actions=[_action("a1", "dispatch_skill", 1.0)],
                 events=[_event(0, "episode_started", 0.0)],
                 history=[_history("as-1", 0, "ghost", 2.0)]))
    snapshot = report["snapshots"][0]
    assert snapshot["status"] == "boundary_unresolvable"
    assert snapshot["quality_failures"] == ["declaration_action_not_in_report"]
    assert "pre_declaration" not in snapshot


def test_schema_and_release_flags():
    report = build_declaration_cost_snapshots(
        _session(actions=[_action("d1", "deployment_assessment", 2.0)],
                 events=[_event(0, "episode_started", 0.0)],
                 history=[_history("as-1", 0, "d1", 2.0)]))
    assert report["schema"] == DECLARATION_COST_SCHEMA
    assert report["version"] == 1
    assert report["execution_validated"] is False
    assert report["business_outcomes_validated"] is False
    assert report["scoreable"] is False


def test_sidecar_observes_runner_written_snapshots(tmp_path):
    import json

    from demo.benchmark.goal_scoring_adapter import (
        declaration_cost_snapshot_observations,
    )

    observed = declaration_cost_snapshot_observations(tmp_path)
    assert observed["status"] == "missing"
    assert observed["scoreable"] is False
    report = build_declaration_cost_snapshots(
        _session(actions=[_action("d1", "deployment_assessment", 2.0)],
                 events=[_event(0, "episode_started", 0.0)],
                 history=[_history("as-1", 0, "d1", 2.0)]))
    (tmp_path / "declaration_cost_snapshots.json").write_text(
        json.dumps(report), encoding="utf-8")
    observed = declaration_cost_snapshot_observations(tmp_path)
    assert observed["status"] == "observed"
    assert observed["snapshot_count"] == 1
    tampered = dict(report)
    tampered["scoreable"] = True
    (tmp_path / "declaration_cost_snapshots.json").write_text(
        json.dumps(tampered), encoding="utf-8")
    observed = declaration_cost_snapshot_observations(tmp_path)
    assert observed["status"] == "invalid"
    assert "declaration_snapshots_invalid" in observed["quality_failure"]
