"""End-to-end protocol composition; these tests make no physics claims."""

from copy import deepcopy

import pytest

from demo.benchmark.communication import validate_communication_report
from demo.benchmark.interventions import InterventionSession
from demo.benchmark.team_lifecycle import TeamLifecycle
from test_benchmark_dynamic_participant import session_fixture, declaration


def harness():
    participant, adapter = session_fixture()
    return InterventionSession(participant), adapter


def submit(adapter, kind, payload, time_s, identifier):
    adapter.pending = [{"schema": "eai.benchmark.participant_action", "version": 1,
                        "action_id": identifier, "submitted_at_s": time_s,
                        "type": kind, "payload": payload}]


def test_reserve_join_changes_real_eligibility_and_remains_private_until_event():
    session, adapter = harness()
    session.register_reserve({"id": "reserve", "capabilities": declaration()})
    session.tick(0.)
    assert "reserve" not in str(session.participant.report())
    session.lifecycle_event({"type": "member_join", "time_s": 1., "payload": {"member_id": "reserve"}})
    session.ready("priority_carton_stage", time_s=1.)
    submit(adapter, "allocate", {"node_id": "priority_carton_stage", "agent_ids": ["reserve"]}, 1., "join-allocate")
    assert session.request_actions()[0]["status"] == "accepted"
    report = session.report()
    TeamLifecycle.validate_lifecycle_report(report["team_lifecycle"])
    assert report["execution_validated"] is False
    assert report["team_lifecycle"]["snapshot"]["objectives"]["priority_carton_stage"]["status"] == "assigned"


def test_rejected_visible_join_does_not_advance_communication_or_team():
    session, _ = harness()
    session.register_reserve({"id": "reserve", "capabilities": declaration()})
    # Deliberately drift the external public session to exercise rollback of
    # a team-valid event that cannot be accepted by the participant boundary.
    session.participant.emit("member_join", time_s=0., payload={"agent_id": "reserve",
        "capability_declaration": declaration(), "capability_version": 1})
    session.publish(message_id="pending", producer_id="iris_1", estimate={"label": "rack"}, valid_until_s=10.)
    before = deepcopy(session.report())
    with pytest.raises(ValueError, match="already active"):
        session.lifecycle_event({"type": "member_join", "time_s": 3., "payload": {"member_id": "reserve"}})
    assert session.report() == before


def test_condition_change_delivery_consumption_and_private_replay():
    session, adapter = harness()
    session.communication_change({"delay_s": 2.}, time_s=0., channel="observation")
    session.publish(message_id="rack", producer_id="iris_1", estimate={"label": "carton"}, valid_until_s=5.)
    session.tick(1.)
    assert not any(e["type"] == "observation_message" for e in session.participant.events)
    session.tick(2.)
    submit(adapter, "consume_message", {"message_id": "rack", "consumer_id": "priority_carton_stage"}, 2., "consume")
    assert session.request_actions()[0]["status"] == "accepted"
    session.tick(6.)
    submit(adapter, "consume_message", {"message_id": "rack", "consumer_id": "priority_carton_stage"}, 6., "expired")
    assert session.request_actions()[0]["status"] == "rejected"
    private = session.report()["communication"]
    assert validate_communication_report(private)["validated"]
    visible = str(session.participant.events)
    for secret in ("delay_s", "drop", "bytes_per_window", "channel_condition_changed"):
        assert secret not in visible


def test_wait_prevents_navigation_but_never_completes_objective():
    session, adapter = harness()
    session.ready("aisle_left_access", time_s=0.)
    submit(adapter, "allocate", {"node_id": "aisle_left_access", "agent_ids": ["m20_1"]}, 0., "allocate")
    assert session.request_actions()[0]["status"] == "accepted"
    submit(adapter, "wait", {"node_id": "aisle_left_access", "agent_id": "m20_1", "duration_s": 3.}, 0., "wait")
    assert session.request_actions()[0]["status"] == "accepted"
    payload = {"node_id": "aisle_left_access", "agent_id": "m20_1", "target_ref": "aisle_entry_zone"}
    submit(adapter, "navigate_goal", payload, 0., "too-early")
    assert session.request_actions()[0]["status"] == "rejected"
    session.tick(3.)
    submit(adapter, "navigate_goal", payload, 3., "after-wait")
    assert session.request_actions()[0]["status"] == "accepted"
    assert session.team.snapshot()["objectives"]["aisle_left_access"]["status"] == "assigned"
