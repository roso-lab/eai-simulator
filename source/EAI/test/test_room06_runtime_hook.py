from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from EAI.room06_runtime_hook import (
    CallbackRoom06RuntimeHookSource,
    HOOK_CAPTURE_SCHEMA,
    HOOK_SOURCE_SCHEMA,
    Room06RuntimeHookError,
    collect_room06_runtime_hook,
)


def test_callback_source_wrapper_requires_explicit_callbacks_and_preserves_identity():
    bindings = lambda **kwargs: dict(PATHS)
    collector = lambda **kwargs: {"assignment": _assignment(), "terminal_inputs": _terminal_inputs()}
    source = CallbackRoom06RuntimeHookSource(
        object_path_bindings=bindings, collect_room06=collector
    )
    assert source.schema == HOOK_SOURCE_SCHEMA
    assert source.object_path_bindings(package=_package()) == PATHS
    assert source.collect_room06(package=_package()) == {
        "assignment": _assignment(), "terminal_inputs": _terminal_inputs()
    }


@pytest.mark.parametrize("name", ["object_path_bindings", "collect_room06"])
def test_callback_source_wrapper_rejects_missing_callback(name):
    kwargs = {
        "object_path_bindings": lambda **_: dict(PATHS),
        "collect_room06": lambda **_: {"assignment": _assignment(), "terminal_inputs": _terminal_inputs()},
    }
    kwargs[name] = None
    with pytest.raises(TypeError, match="callback must be callable"):
        CallbackRoom06RuntimeHookSource(**kwargs)
from tools.validation.room06_livt04_gpu_probe import normalize_runtime_hook_capture


ROBOTS = ("hc-rt-006", "hc-rt-020", "hc-rt-021")
AGENTS = {robot: f"runtime_{index}" for index, robot in enumerate(ROBOTS)}
PATHS = {
    "book_batch_01": "/World/Task/book_batch_01",
    "heavy_object": "/World/Task/heavy_object",
}


def _package():
    return {
        "plan": {"selected_robot_ids": list(ROBOTS)},
        "task_object_bindings": {
            "objects": [
                {"entity_id": "book_batch_01"},
                {"entity_id": "heavy_object"},
            ]
        },
    }


def _event(*, status="completed", tick=1, robot_id=ROBOTS[0]):
    return {
        "schema": "heterocoord.observed-skill-event.v1",
        "session_id": "observer-session-1",
        "event_type": "relay_pickup_completed",
        "status": status,
        "tick": tick,
        "robot_id": robot_id,
        "object_id": "book_batch_01",
        "target_ref": "floor1-bookshelf",
        "evidence_digest": hashlib.sha256(b"real-observer-record").hexdigest(),
    }


def _assignment():
    return {
        "tick": 0,
        "assigned_robot_ids": list(ROBOTS),
        "decision_digest": hashlib.sha256(b"decision").hexdigest(),
    }


def _terminal_inputs():
    return {
        "episode": {},
        "d1": {},
        "d4_first_event_observation": {},
        "d6_measurement": {},
    }


class _Runtime:
    def __init__(self):
        self.holds = {agent: None for agent in AGENTS.values()}

    def held_object(self, robot_name, *, env_id=0):
        assert env_id == 0
        return self.holds[robot_name]


class _Source:
    schema = HOOK_SOURCE_SCHEMA

    def __init__(self, action=None, *, close=True):
        self.action = action
        self.should_close = close

    def object_path_bindings(self, **_kwargs):
        return dict(PATHS)

    def collect_room06(self, *, collector, **_kwargs):
        if self.action is None:
            collector.observe_completed_skill_session(_event())
            collector._base_env.common_step_counter = 2
            collector.sample_holds()
        else:
            self.action(collector)
        if self.should_close:
            collector.close()
        return {"assignment": _assignment(), "terminal_inputs": _terminal_inputs()}


def _session(source=None):
    runtime = _Runtime()
    env = SimpleNamespace(
        num_envs=1,
        common_step_counter=0,
        _idealized_manipulation_runtime=runtime,
    )
    if source is not None:
        env.room06_runtime_hook_source = source
    return SimpleNamespace(base_env=env), runtime


def test_factory_emits_probe_capture_from_observer_events_and_live_holds():
    def collect(collector):
        collector.observe_completed_skill_session(_event())
        collector._base_env.common_step_counter = 2
        collector._base_env._idealized_manipulation_runtime.holds.update(
            {
                AGENTS[ROBOTS[0]]: SimpleNamespace(object_body_path=PATHS["heavy_object"]),
                AGENTS[ROBOTS[1]]: SimpleNamespace(object_body_path=PATHS["heavy_object"]),
            }
        )
        collector.sample_holds()

    session, _runtime = _session(_Source(collect))
    capture = collect_room06_runtime_hook(
        session=session, package=_package(), instance_agent_map=AGENTS
    )
    assert capture["schema"] == HOOK_CAPTURE_SCHEMA
    assert capture["collector_closed"] is True
    assert capture["observations"] == [
        {"kind": "skill_event", "event": _event()},
        {
            "kind": "held_object_snapshot",
            "tick": 2,
            "held_objects": {
                ROBOTS[0]: "heavy_object",
                ROBOTS[1]: "heavy_object",
                ROBOTS[2]: None,
            },
        },
    ]
    assert "success" not in capture and "terminal_payload" not in capture
    assert normalize_runtime_hook_capture(
        capture, selected_robot_ids=ROBOTS
    ) == capture


def test_missing_production_observer_source_fails_closed():
    session, _runtime = _session()
    with pytest.raises(Room06RuntimeHookError, match="observer source is unavailable"):
        collect_room06_runtime_hook(
            session=session, package=_package(), instance_agent_map=AGENTS
        )


def test_instance_agent_map_must_cover_exact_frozen_roster():
    session, _runtime = _session(_Source())
    partial = dict(AGENTS)
    partial.pop(ROBOTS[-1])
    with pytest.raises(Room06RuntimeHookError, match="exactly cover"):
        collect_room06_runtime_hook(
            session=session, package=_package(), instance_agent_map=partial
        )


def test_noncompleted_skill_session_is_not_observer_evidence():
    def collect(collector):
        collector.observe_completed_skill_session(_event(status="running"))

    session, _runtime = _session(_Source(collect))
    with pytest.raises(Room06RuntimeHookError, match="only completed"):
        collect_room06_runtime_hook(
            session=session, package=_package(), instance_agent_map=AGENTS
        )


def test_completed_event_robot_must_be_in_observed_assignment():
    class PartialAssignment(_Source):
        def collect_room06(self, *, collector, **_kwargs):
            collector.observe_completed_skill_session(_event(robot_id=ROBOTS[-1]))
            collector._base_env.common_step_counter = 2
            collector.sample_holds()
            collector.close()
            assignment = _assignment()
            assignment["assigned_robot_ids"] = list(ROBOTS[:-1])
            return {"assignment": assignment, "terminal_inputs": _terminal_inputs()}

    session, _runtime = _session(PartialAssignment())
    with pytest.raises(Room06RuntimeHookError, match="outside the observed assignment"):
        collect_room06_runtime_hook(
            session=session, package=_package(), instance_agent_map=AGENTS
        )


def test_unclosed_collector_fails_even_if_source_returns_material():
    session, _runtime = _session(_Source(close=False))
    with pytest.raises(Room06RuntimeHookError, match="did not close"):
        collect_room06_runtime_hook(
            session=session, package=_package(), instance_agent_map=AGENTS
        )


def test_missing_idealized_hold_reader_fails_closed():
    session, _runtime = _session(_Source())
    del session.base_env._idealized_manipulation_runtime
    with pytest.raises(Room06RuntimeHookError, match="held_object readback is unavailable"):
        collect_room06_runtime_hook(
            session=session, package=_package(), instance_agent_map=AGENTS
        )


def test_unbound_held_body_path_is_not_guessed_from_its_name():
    def collect(collector):
        collector.observe_completed_skill_session(_event())
        collector._base_env.common_step_counter = 2
        collector._base_env._idealized_manipulation_runtime.holds[
            AGENTS[ROBOTS[0]]
        ] = SimpleNamespace(object_body_path="/World/LooksLike/heavy_object")
        collector.sample_holds()

    session, _runtime = _session(_Source(collect))
    with pytest.raises(Room06RuntimeHookError, match="no exact logical object binding"):
        collect_room06_runtime_hook(
            session=session, package=_package(), instance_agent_map=AGENTS
        )


def test_source_exception_is_retained_as_collection_failure():
    class Broken(_Source):
        def collect_room06(self, **_kwargs):
            raise ValueError("native runner stopped")

    session, _runtime = _session(Broken())
    with pytest.raises(
        Room06RuntimeHookError,
        match="production room06 observer collection failed: native runner stopped",
    ):
        collect_room06_runtime_hook(
            session=session, package=_package(), instance_agent_map=AGENTS
        )


@pytest.mark.parametrize("missing", ["event", "hold"])
def test_event_and_hold_observations_are_both_required(missing):
    def collect(collector):
        if missing != "event":
            collector.observe_completed_skill_session(_event())
        if missing != "hold":
            collector._base_env.common_step_counter = 2
            collector.sample_holds()

    session, _runtime = _session(_Source(collect))
    with pytest.raises(Room06RuntimeHookError, match="emitted no"):
        collect_room06_runtime_hook(
            session=session, package=_package(), instance_agent_map=AGENTS
        )


def test_horizon_is_accepted_only_from_live_tick_15000():
    def too_early(collector):
        collector.observe_completed_skill_session(_event())
        collector._base_env.common_step_counter = 2
        collector.sample_holds()
        collector.observe_horizon()

    session, _runtime = _session(_Source(too_early))
    with pytest.raises(Room06RuntimeHookError, match="requires live tick 15000"):
        collect_room06_runtime_hook(
            session=session, package=_package(), instance_agent_map=AGENTS
        )
