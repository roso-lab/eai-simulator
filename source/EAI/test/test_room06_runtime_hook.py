"""The production observer consumes benchmark sessions, never task-object prims."""
from types import SimpleNamespace
from copy import deepcopy
import json
from pathlib import Path

import pytest

from EAI.room06_runtime_hook import (
    CallbackRoom06RuntimeHookSource, Room06RuntimeHookError, collect_room06_runtime_hook,
)
from benchmark.room06_task_instance_v2 import logical_object_definitions
from task.tests.test_logical_objects import make_runtime

ROOT = Path(__file__).resolve().parents[3]
INPUTS = ROOT / "artifacts/project_runtime/room06-deployment-v2-20261028/inputs"


def _fixture(action=None):
    package = json.loads((INPUTS / "room06-fixed-package.json").read_text())
    ids = package["plan"]["selected_robot_ids"]
    objects = logical_object_definitions(package["task_setup"])
    runtime, host = make_runtime(objects=objects, robots=ids)
    env = SimpleNamespace(num_envs=1, common_step_counter=0, logical_object_runtime=runtime)
    agents = dict(zip(ids, ["pepper_1", "go2_1", "scout_1", "pegasus_1", "microduck_1"]))

    def collect(*, collector, **kwargs):
        record = runtime.execute(request_id="book-pick", operation="pick", object_id="book_01",
                                 robot_ids=[ids[1]], semantic_point="floor1-bookshelf")
        event = {
            "schema": "heterocoord.observed-skill-event.v1",
            "session_id": record["session_id"], "event_type": "relay_pickup_completed",
            "status": "completed", "tick": 0, "robot_id": ids[1], "object_id": "book_01",
            "target_ref": "floor1-bookshelf", "evidence_digest": record["evidence_digest"],
        }
        if action:
            action(env, collector, event)
        else:
            collector.observe_completed_skill_session(event)
            collector.sample_holds()
            collector.close()
        return {
            "assignment": {"tick": 0, "assigned_robot_ids": [ids[1]], "decision_digest": "d" * 64},
            "terminal_inputs": {"episode": {}, "d1": {}, "d4_first_event_observation": {}, "d6_measurement": {}},
        }

    env.room06_runtime_hook_source = CallbackRoom06RuntimeHookSource(
        object_bindings=lambda **_: deepcopy(objects), collect_room06=collect,
    )
    return SimpleNamespace(base_env=env), package, agents


def test_session_and_hold_capture_requires_no_usd_stage_or_attachment():
    session, package, agents = _fixture()
    capture = collect_room06_runtime_hook(session=session, package=package, instance_agent_map=agents)
    assert capture["collector_closed"] is True
    assert capture["observations"][1]["held_objects"] == {
        "hc-rt-004": None, "hc-rt-006": "book_01", "hc-rt-015": None,
        "hc-rt-021": None, "hc-rt-027": None,
    }
    assert not hasattr(session.base_env, "stage")
    assert not hasattr(session.base_env, "_idealized_manipulation_runtime")


@pytest.mark.parametrize("assigned", [[], ["hc-rt-027"]])
def test_live_horizon_without_any_successful_skill_is_valid_failure_capture(assigned):
    from EAI.room06_runtime_hook import Room06RuntimeHookCollector
    session, package, agents = _fixture()
    objects = logical_object_definitions(package["task_setup"])
    runtime, host = make_runtime(objects=objects, robots=list(agents))
    session.base_env.logical_object_runtime = runtime
    collector = Room06RuntimeHookCollector(base_env=session.base_env,
        selected_robot_ids=list(agents), instance_agent_map=agents,
        object_bindings={r["entity_id"]: r for r in objects})
    collector.sample_holds()
    with pytest.raises(Room06RuntimeHookError, match="live tick"):
        collector.observe_horizon()
    host.tick = session.base_env.common_step_counter = 15000
    collector.sample_holds()
    collector.observe_horizon()
    collector.close()
    result = collector.capture(assignment={"tick": 0, "assigned_robot_ids": assigned, "decision_digest": "d"*64},
        terminal_inputs={"episode": {}, "d1": {}, "d4_first_event_observation": {}, "d6_measurement": {}})
    assert [r["kind"] for r in result["observations"]] == ["held_object_snapshot", "held_object_snapshot", "horizon_reached"]
    assert all(runtime.object_state(i)["state"] == "at_point" for i in runtime.definitions)
    assert result["assignment"]["assigned_robot_ids"] == assigned


@pytest.mark.parametrize("mutation", ["session", "object", "point", "digest", "tick"])
def test_algorithm_cannot_forge_a_completed_skill_event(mutation):
    def action(env, collector, event):
        if mutation == "session": event["session_id"] = "invented"
        elif mutation == "object": event["object_id"] = "book_02"
        elif mutation == "point": event["target_ref"] = "floor2-bookshelf-1"
        elif mutation == "digest": event["evidence_digest"] = "e" * 64
        else: event["tick"] = 1
        collector.observe_completed_skill_session(event)
    session, package, agents = _fixture(action)
    with pytest.raises(Room06RuntimeHookError):
        collect_room06_runtime_hook(session=session, package=package, instance_agent_map=agents)


def test_attachment_reader_does_not_replace_missing_logical_ledger():
    def action(env, collector, event):
        del env.logical_object_runtime
        env._idealized_manipulation_runtime = SimpleNamespace(held_object=lambda *_: None)
        collector.sample_holds()
    session, package, agents = _fixture(action)
    with pytest.raises(Room06RuntimeHookError, match="logical object ledger is unavailable"):
        collect_room06_runtime_hook(session=session, package=package, instance_agent_map=agents)


def test_missing_source_fails_closed():
    session, package, agents = _fixture()
    del session.base_env.room06_runtime_hook_source
    with pytest.raises(Room06RuntimeHookError, match="observer source is unavailable"):
        collect_room06_runtime_hook(session=session, package=package, instance_agent_map=agents)


def test_unclosed_collector_is_rejected():
    def action(env, collector, event):
        collector.observe_completed_skill_session(event)
        collector.sample_holds()
    session, package, agents = _fixture(action)
    with pytest.raises(Room06RuntimeHookError, match="did not close"):
        collect_room06_runtime_hook(session=session, package=package, instance_agent_map=agents)


def test_wrong_ledger_definitions_rejected():
    session, package, agents = _fixture()
    session.base_env.logical_object_runtime.definitions["book_01"]["metadata"]["mass_kg"] = 4
    with pytest.raises(Room06RuntimeHookError, match="ledger identity"):
        collect_room06_runtime_hook(session=session, package=package, instance_agent_map=agents)


@pytest.mark.parametrize("name", ["object_bindings", "collect_room06"])
def test_wrapper_requires_callable_callbacks(name):
    args = {"object_bindings": lambda **_: [], "collect_room06": lambda **_: {}}
    args[name] = None
    with pytest.raises(TypeError, match="callback"):
        CallbackRoom06RuntimeHookSource(**args)
