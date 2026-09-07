import threading
from types import SimpleNamespace

import pytest

from algorithm.emos.example_policy import assign
from algorithm.emos.policy import assignment_tasks, load_assignment_policy, make_assignment_request
from algorithm.emos.types import compute_subtask_positions
from demo.fire_rescue.scenario import build_factory_emos_scenario


def _manager():
    from algorithm.emos.engine import EMOSDiscussionManager

    return EMOSDiscussionManager(
        SimpleNamespace(), ["m20_1", "m20_2", "carter_1", "scout_1"],
        lambda name: (1.0, 2.0, 0.0), build_factory_emos_scenario(), use_env_resume=False,
    )


def _raise_policy(request):
    raise ValueError("algorithm exploded")


def _mutate_request(request):
    request.scenario.subtasks["red"].name = "corrupted"
    request.subtask_positions["red"] = (99.0, 99.0)
    return {"m20_1": "red"}


def test_example_policy_uses_task_request_and_task_owned_target_positions():
    manager = _manager()
    positions = compute_subtask_positions(manager._scenario, (0.0, 0.0, 0.0))
    request = make_assignment_request(manager, "task", positions)
    tasks = assignment_tasks(request, assign(request))
    assert set(tasks) == set(manager._agents)
    assert tasks["scout_1"].subtask_colour == "green"
    assert {task.subtask_colour for task in tasks.values()} == {"red", "green", "blue", "yellow"}
    for task in tasks.values():
        assert task.target_xy == positions[task.subtask_colour]
    request.scenario.subtasks["red"].name = "changed"
    assert manager._scenario.subtasks["red"].name != "changed"


@pytest.mark.parametrize("result", [
    {}, {"unknown": "red"}, {"m20_1": "unknown"}, {"m20_1": "green"},
    {"carter_1": "yellow"}, {"m20_1": ["red"]},
])
def test_invalid_policy_results_are_rejected_not_reassigned(result):
    manager = _manager()
    positions = compute_subtask_positions(manager._scenario, (0.0, 0.0, 0.0))
    request = make_assignment_request(manager, "task", positions)
    with pytest.raises(ValueError):
        assignment_tasks(request, result)


def test_loader_defaults_to_emos_and_accepts_stateless_algorithm_module():
    assert load_assignment_policy("emos") is None
    assert load_assignment_policy("algorithm.emos.example_policy:assign") is assign
    with pytest.raises(ValueError, match="module:assign"):
        load_assignment_policy("invalid")
    with pytest.raises(ValueError, match="cannot load"):
        load_assignment_policy("algorithm.emos.example_policy:missing")


def test_programmatic_policy_registration_rejects_hidden_closure_state():
    state = {"calls": 0}

    def stateful(request):
        state["calls"] += 1
        return {"m20_1": "red"}

    with pytest.raises(ValueError, match="stateless"):
        _manager().set_assignment_policy(stateful, name="stateful")


def test_loader_rejects_mutable_function_attributes(monkeypatch):
    monkeypatch.setattr(assign, "calls", 1, raising=False)
    with pytest.raises(ValueError, match="stateless"):
        load_assignment_policy("algorithm.emos.example_policy:assign")


def test_custom_algorithm_runs_without_fallback_and_positions_are_read_on_main_thread():
    manager = _manager()
    reads = []
    manager._get_pos = lambda name: reads.append(threading.current_thread().name) or (1.0, 2.0, 0.0)
    manager.set_assignment_policy(assign, name="test")
    manager.trigger(1, (0.0, 0.0, 0.0))
    manager._thread.join(timeout=5)
    assert not manager._thread.is_alive()
    assert manager.poll()
    assert reads == [threading.current_thread().name] * 4
    assert manager.fallback_dispatch_count == 0
    assert manager.discussion_count == 1
    assert manager.should_preserve_raw_llm_assignments()


def test_custom_algorithm_error_is_not_hidden_by_heuristic_fallback():
    manager = _manager()
    manager.set_assignment_policy(_raise_policy, name="failing")
    manager.trigger(1, (0.0, 0.0, 0.0))
    manager._thread.join(timeout=5)
    with pytest.raises(RuntimeError, match="algorithm exploded"):
        manager.poll()
    assert manager.fallback_dispatch_count == 0


def test_callback_mutation_cannot_change_task_targets_or_scenario():
    manager = _manager()
    manager.set_assignment_policy(_mutate_request, name="mutating")
    manager.trigger(1, (0.0, 0.0, 0.0))
    manager._thread.join(timeout=5)
    result = manager.poll()
    assert result["m20_1"].subtask_name != "corrupted"
    assert result["m20_1"].target_xy != (99.0, 99.0)


def test_dynamic_replanning_uses_the_selected_algorithm_not_local_fallback():
    manager = _manager()
    manager.set_assignment_policy(assign, name="test")
    assert manager.trigger_local_fallback("rescue replan")
    manager._thread.join(timeout=5)
    assert manager.poll()
    assert manager.fallback_dispatch_count == 0


def test_native_replay_restores_algorithm_request_and_state_without_serializing_callback():
    from demo.fire_rescue.runtime.native_replay import build_emos_replay_component

    manager = _manager()
    manager.set_assignment_policy(assign, name="test")
    manager.trigger(1, (0.0, 0.0, 0.0))
    manager._thread.join(timeout=5)
    assert manager.poll()
    component = build_emos_replay_component(manager)
    snapshot = component.capture_state()
    manager._assignment_request = None
    manager._assignment_error = "future failure"
    manager.discussion_count = 99
    component.validate_state(snapshot)
    component.restore_state(snapshot)
    assert manager._assignment_policy is assign
    assert manager._assignment_request.hazard_id == 1
    assert manager._assignment_error is None
    assert manager.discussion_count == 1
