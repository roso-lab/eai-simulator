"""Public event state changes are tested independently of physical spawning."""

from copy import deepcopy

import pytest

from demo.benchmark.participant_runtime import ParticipantSession
from demo.benchmark.scene_challenge import build_scene_runtime_contract, generate_scene_scenario


class SubmittedActions:
    def __init__(self):
        self.pending = []

    def manifest(self):
        return {"kind": "participant", "id": "test-input", "version": 1}

    def act(self, events):
        return deepcopy(self.pending)


def session_fixture():
    data = generate_scene_scenario(scene_key="warehouse_nvd", task_name="event_test", seed=1, origin=(0., 0.))
    public, api = build_scene_runtime_contract(data, protocol_components=True)
    adapter = SubmittedActions()
    return ParticipantSession(adapter=adapter, api=api, task_spec=public), adapter


def allocate(adapter, time_s, *, agent="reserve", identifier="allocation"):
    adapter.pending = [{"schema": "eai.benchmark.participant_action", "version": 1,
                        "action_id": identifier, "submitted_at_s": time_s, "type": "allocate",
                        "payload": {"node_id": "priority_carton_stage", "agent_ids": [agent]}}]


def declaration():
    return {"domain": "ground", "navigation_interface": True, "attachment_types": ["z1"]}


def test_join_is_visible_before_allocation_without_mutating_frozen_task():
    session, adapter = session_fixture()
    initial = session.report()["public_task_sha256"]
    session.emit("tick", time_s=1., payload={"robot_states": {}, "phase_states": {}, "resource_states": {}})
    allocate(adapter, 1.)
    with pytest.raises(ValueError, match="unknown agent"):
        session.request_actions()
    session.emit("member_join", time_s=2., payload={"agent_id": "reserve", "capability_declaration": declaration(), "capability_version": 1})
    allocate(adapter, 2.)
    assert session.request_actions()[0]["payload"]["agent_ids"] == ["reserve"]
    assert session.report()["public_task_sha256"] == initial
    assert "reserve" not in {r["id"] for r in session.task_spec["initial_fleet"]}
    assert "reserve" in {r["id"] for r in session.current_task_spec()["initial_fleet"]}


def test_capability_downgrade_invalidates_subsequent_assignment():
    session, adapter = session_fixture()
    session.emit("capability_change", time_s=1., payload={
        "agent_id": "m20_1", "capability_declaration": {**declaration(), "attachment_types": []},
        "capability_version": 2, "affected_node_ids": ["priority_carton_stage"]})
    allocate(adapter, 1., agent="m20_1")
    with pytest.raises(ValueError, match="manipulation interface"):
        session.request_actions()
    assert session.report()["actions"] == []


def test_exited_agent_cannot_act_and_version_rejection_is_atomic():
    session, adapter = session_fixture()
    session.emit("member_exit", time_s=1., payload={"agent_id": "m20_1", "affected_node_ids": []})
    allocate(adapter, 1., agent="m20_1")
    with pytest.raises(ValueError, match="unknown agent"):
        session.request_actions()
    before = session.report()
    with pytest.raises(ValueError, match="inactive"):
        session.emit("capability_change", time_s=2., payload={"agent_id": "m20_1",
            "capability_declaration": declaration(), "capability_version": 2, "affected_node_ids": []})
    assert session.report() == before
    with pytest.raises(ValueError, match="increase"):
        session.emit("member_join", time_s=2., payload={"agent_id": "m20_1",
            "capability_declaration": declaration(), "capability_version": 1})
    assert session.report() == before


def test_invalid_action_batch_does_not_retain_first_assignment():
    session, adapter = session_fixture()
    session.emit("tick", time_s=1., payload={"robot_states": {}, "phase_states": {}, "resource_states": {}})
    allocate(adapter, 1., agent="m20_1")
    adapter.pending.append(deepcopy(adapter.pending[0]))
    with pytest.raises(ValueError, match="unique"):
        session.request_actions()
    assert session.report()["actions"] == []
