"""Participant event/action runtime boundary tests."""

from copy import deepcopy

import pytest

from demo.benchmark.participant_runtime import ParticipantSession, interpret_dispatch_actions, replay_adapter_actions
from demo.factory_benchmark.baseline_solver import BaselineDispatchAdapter
from demo.factory_benchmark.challenge import compile_challenge


def _session(policy="immediate", wait=10.0):
    bundle = compile_challenge()
    return ParticipantSession(
        adapter=BaselineDispatchAdapter(policy=policy, max_wait_s=wait),
        api=bundle["participant_api"], task_spec=bundle["public_task"])


def _ready(session, time_s=5.0):
    session.emit("phase_ready", time_s=time_s, payload={
        "node_id": "route_confirmation", "eligible_agent_ids": ["carter_1"],
    })


def _tick(session, time_s):
    session.emit("tick", time_s=time_s, payload={
        "robot_states": {}, "phase_states": {}, "resource_states": {},
    })


def _message(session, time_s=6.0, marker_id=23):
    session.emit("observation_message", time_s=time_s, payload={
        "message_id": f"ground_view:marker:{marker_id}",
        "producer_id": "simple_car_1", "published_at_s": 4.0,
        "valid_until_s": 120.0, "estimate": {"marker_id": marker_id},
    })


def test_immediate_baseline_consumes_visible_message_and_selects_public_station():
    session = _session()
    _ready(session)
    _message(session)
    _tick(session, 6.0)
    actions = session.request_actions()
    assert [action["type"] for action in actions] == ["consume_message", "navigate_goal"]
    assert actions[-1]["payload"]["target_ref"] == "dock_site"
    assert session.report()["adapter"]["input_scope"] == "participant_visible_events_only"


def test_wait_baseline_cannot_distinguish_withheld_from_not_yet_delivered():
    session = _session("wait_for_message", wait=10.0)
    _ready(session)
    _tick(session, 5.0)
    actions = session.request_actions()
    assert [action["type"] for action in actions] == ["wait"]
    for time_s in (6.0, 12.0):
        _tick(session, time_s)
        assert session.request_actions() == []
    _tick(session, 15.0)
    actions = session.request_actions()
    assert actions[-1]["type"] == "navigate_goal"
    assert actions[-1]["payload"]["target_ref"] == "assembly_site"
    assert all("mode" not in event["payload"] and "channel_ledger" not in event["payload"]
               for event in session.report()["events"])


def test_wait_baseline_commits_when_a_message_becomes_visible():
    session = _session("wait_for_message", wait=10.0)
    _ready(session)
    _tick(session, 5.0)
    session.request_actions()
    _message(session, 8.0)
    _tick(session, 8.0)
    actions = session.request_actions()
    assert actions[-1]["payload"]["target_ref"] == "dock_site"
    replayed = replay_adapter_actions(
        BaselineDispatchAdapter(policy="wait_for_message", max_wait_s=10.0),
        session.report()["events"])
    assert replayed == session.report()["actions"]


def test_session_rejects_actions_outside_the_current_task_scope():
    bundle = compile_challenge()

    class InvalidAdapter:
        def manifest(self):
            return {"kind": "participant", "id": "invalid", "version": 1}

        def act(self, events):
            return [{
                "schema": "eai.benchmark.participant_action", "version": 1,
                "action_id": "bad", "submitted_at_s": events[-1]["time_s"],
                "type": "reassign", "payload": {
                    "node_id": "route_confirmation", "agent_ids": ["carter_1"],
                    "reason": "unsupported in this task runtime",
                },
            }]

    session = ParticipantSession(adapter=InvalidAdapter(), api=bundle["participant_api"],
                                 task_spec=bundle["public_task"])
    _ready(session)
    with pytest.raises(ValueError, match="not allowed"):
        session.request_actions()


def test_session_rejects_duplicate_action_ids_across_turns():
    bundle = compile_challenge()

    class DuplicateAdapter:
        def manifest(self):
            return {"kind": "participant", "id": "duplicate", "version": 1}

        def act(self, events):
            return [{
                "schema": "eai.benchmark.participant_action", "version": 1,
                "action_id": "same", "submitted_at_s": events[-1]["time_s"],
                "type": "wait", "payload": {
                    "node_id": "route_confirmation", "agent_id": "carter_1", "duration_s": 1.0,
                },
            }]

    session = ParticipantSession(adapter=DuplicateAdapter(), api=bundle["participant_api"],
                                 task_spec=bundle["public_task"])
    _ready(session)
    session.request_actions()
    _tick(session, 6.0)
    with pytest.raises(ValueError, match="unique"):
        session.request_actions()


def test_session_rejects_action_time_outside_its_event_turn():
    bundle = compile_challenge()

    class WrongTimeAdapter:
        def manifest(self):
            return {"kind": "participant", "id": "wrong-time", "version": 1}

        def act(self, events):
            return [{
                "schema": "eai.benchmark.participant_action", "version": 1,
                "action_id": "future", "submitted_at_s": events[-1]["time_s"] + 1,
                "type": "wait", "payload": {
                    "node_id": "route_confirmation", "agent_id": "carter_1", "duration_s": 1.0,
                },
            }]

    session = ParticipantSession(adapter=WrongTimeAdapter(), api=bundle["participant_api"],
                                 task_spec=bundle["public_task"])
    _ready(session)
    with pytest.raises(ValueError, match="current turn time"):
        session.request_actions()


def test_dispatch_action_interpreter_rejects_hidden_messages_and_late_consumption():
    session = _session()
    _ready(session)
    _message(session)
    _tick(session, 6.0)
    actions = session.request_actions()
    result = interpret_dispatch_actions(
        actions, node_id="route_confirmation", agent_id="carter_1",
        visible_message_ids={"ground_view:marker:23"}, consumed_message_ids=set())
    assert result["consumed_message_ids"] == {"ground_view:marker:23"}
    assert result["navigate_action"]["payload"]["target_ref"] == "dock_site"
    with pytest.raises(ValueError, match="unavailable"):
        interpret_dispatch_actions(
            actions, node_id="route_confirmation", agent_id="carter_1",
            visible_message_ids=set(), consumed_message_ids=set())
    reversed_actions = [actions[1], actions[0]]
    with pytest.raises(ValueError, match="consume a message before"):
        interpret_dispatch_actions(
            reversed_actions, node_id="route_confirmation", agent_id="carter_1",
            visible_message_ids={"ground_view:marker:23"}, consumed_message_ids=set())
