from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from TeamWeaver.tests.eai_test_support import (
    make_factory_world,
    valid_button_payload,
    valid_inspect_payload,
    valid_remove_obstacle_payload,
)


class FakeCompletions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=response))]
        )


class FakeClient:
    def __init__(self, responses):
        self.completions = FakeCompletions(responses)
        self.chat = SimpleNamespace(completions=self.completions)


def test_decomposer_accepts_dynamic_task_count_requirements_and_dag(factory_world):
    from TeamWeaver.eai_adapter.semantic_decomposer import DeepSeekSemanticDecomposer

    response = {
        "tasks": [
            valid_inspect_payload(task_id="inspect_fire"),
            valid_button_payload(
                task_id="open_channel",
                prerequisites=["inspect_fire"],
            ),
        ]
    }
    client = FakeClient([json.dumps(response)])
    result = DeepSeekSemanticDecomposer(
        env={"DEEPSEEK_API_KEY": "test"},
        client_factory=lambda **_kwargs: client,
    ).decompose("Inspect the fire, then open the rescue channel", factory_world)

    assert [task.task_id for task in result.tasks] == ["inspect_fire", "open_channel"]
    assert result.source == "deepseek"
    assert result.attempts == 1
    messages = client.completions.requests[0]["messages"]
    prompt_text = json.dumps(messages)
    assert "red,yellow,blue,green" not in prompt_text
    assert "Do not assign robots" in messages[0]["content"]
    user_payload = json.loads(messages[1]["content"])
    assert "hazard_1_red_scout" in user_payload["allowed_target_refs"]
    assert user_payload["world_state"]["robots"][0]["effective_capabilities"]
    assert user_payload["available_targets"][0]["compatible_task_types"]


def test_repair_attempts_include_validation_errors(factory_world):
    from TeamWeaver.eai_adapter.semantic_decomposer import DeepSeekSemanticDecomposer

    client = FakeClient(
        [
            "not json",
            '{"tasks": []}',
            json.dumps({"tasks": [valid_inspect_payload()]}),
        ]
    )
    result = DeepSeekSemanticDecomposer(
        env={"DEEPSEEK_API_KEY": "test"},
        client_factory=lambda **_kwargs: client,
    ).decompose("Inspect the fire", factory_world, max_attempts=3)

    assert result.attempts == 3
    assert len(client.completions.requests) == 3
    repair = json.loads(client.completions.requests[1]["messages"][-1]["content"])
    assert repair["validation_errors"]
    assert "corrected complete JSON" in repair["instruction"]


def test_runtime_retry_delays_are_one_three_five_seconds(factory_world):
    from TeamWeaver.eai_adapter.semantic_decomposer import (
        DecompositionError,
        DeepSeekSemanticDecomposer,
    )

    sleeps = []
    client = FakeClient([RuntimeError("down")] * 4)
    decomposer = DeepSeekSemanticDecomposer(
        env={"DEEPSEEK_API_KEY": "test"},
        client_factory=lambda **_kwargs: client,
        sleep_fn=sleeps.append,
    )

    with pytest.raises(DecompositionError) as error:
        decomposer.decompose(
            "Continue the mission",
            factory_world,
            max_attempts=4,
            retry_delays=(1.0, 3.0, 5.0),
        )

    assert sleeps == [1.0, 3.0, 5.0]
    assert len(error.value.errors) == 4
    assert all("down" in item.message for item in error.value.errors)


def test_default_transport_ignores_ambient_proxy_and_closes(
    monkeypatch, factory_world
):
    import httpx
    import openai
    from TeamWeaver.eai_adapter.semantic_decomposer import DeepSeekSemanticDecomposer

    captured_http = {}
    captured_openai = {}

    class FakeHttpClient:
        closed = False

        def close(self):
            self.closed = True

    fake_http = FakeHttpClient()
    client = FakeClient([json.dumps({"tasks": [valid_inspect_payload()]})])
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: captured_http.update(kwargs) or fake_http,
    )
    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda **kwargs: captured_openai.update(kwargs) or client,
    )

    result = DeepSeekSemanticDecomposer(
        env={"DEEPSEEK_API_KEY": "test"}
    ).decompose("Inspect", factory_world)

    assert result.source == "deepseek"
    assert captured_http == {"timeout": 60.0, "trust_env": False}
    assert captured_openai["http_client"] is fake_http
    assert fake_http.closed is True


def test_missing_key_and_exhausted_invalid_output_never_return_local_tasks(factory_world):
    from TeamWeaver.eai_adapter.semantic_decomposer import (
        DecompositionError,
        DeepSeekSemanticDecomposer,
    )

    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY is required"):
        DeepSeekSemanticDecomposer(env={}).decompose("Inspect", factory_world)

    client = FakeClient(['{"tasks": []}'])
    with pytest.raises(DecompositionError):
        DeepSeekSemanticDecomposer(
            env={"DEEPSEEK_API_KEY": "test"},
            client_factory=lambda **_kwargs: client,
        ).decompose("Inspect", factory_world)


def test_attempt_count_must_be_a_positive_integer(factory_world):
    from TeamWeaver.eai_adapter.semantic_decomposer import DeepSeekSemanticDecomposer

    decomposer = DeepSeekSemanticDecomposer(
        env={"DEEPSEEK_API_KEY": "test"},
        client_factory=lambda **_kwargs: FakeClient([]),
    )
    for invalid in (0, -1, 1.5, True):
        with pytest.raises(ValueError, match="positive integer"):
            decomposer.decompose("Inspect", factory_world, max_attempts=invalid)


def test_complete_json_fence_is_accepted_but_incomplete_fence_is_rejected(factory_world):
    from TeamWeaver.eai_adapter.semantic_decomposer import (
        DecompositionError,
        DeepSeekSemanticDecomposer,
    )

    content = json.dumps({"tasks": [valid_inspect_payload()]})
    fenced = FakeClient([f"```json\n{content}\n```"])
    result = DeepSeekSemanticDecomposer(
        env={"DEEPSEEK_API_KEY": "test"},
        client_factory=lambda **_kwargs: fenced,
    ).decompose("Inspect", factory_world)
    assert len(result.tasks) == 1

    incomplete = FakeClient([f"```json\n{content}"])
    with pytest.raises(DecompositionError):
        DeepSeekSemanticDecomposer(
            env={"DEEPSEEK_API_KEY": "test"},
            client_factory=lambda **_kwargs: incomplete,
        ).decompose("Inspect", factory_world)


def test_prompt_serializes_nested_execution_feedback(factory_world):
    from TeamWeaver.eai_adapter.semantic_decomposer import DeepSeekSemanticDecomposer

    world = replace(
        factory_world,
        recent_feedback=(
            {
                "task_id": "prior",
                "outcome": "failed",
                "world_changes": {"entity": {"available": False}},
            },
        ),
    )

    messages = DeepSeekSemanticDecomposer(
        env={"DEEPSEEK_API_KEY": "test"},
    ).build_messages("Continue", world)
    payload = json.loads(messages[1]["content"])
    assert payload["execution_feedback"][0]["world_changes"] == {
        "entity": {"available": False}
    }


def _blocking_world():
    from TeamWeaver.eai_adapter.task_models import ObstacleSnapshot

    obstacle = ObstacleSnapshot(
        "runtime_obstacle_1",
        (-5.0, -2.0, 0.25),
        (2.3, 0.3, 0.5),
        True,
        False,
        "inspect_fire",
        "carter_1",
        (-5.0, -0.8, 0.0),
        (-5.0, 1.7, 0.25),
    )
    return make_factory_world(obstacles=(obstacle,))


def _emergency_response():
    return {
        "tasks": [
            valid_remove_obstacle_payload(),
            valid_inspect_payload(prerequisites=["clear_runtime_obstacle"]),
        ]
    }


def test_prompt_serializes_active_obstacle_and_emergency_rules():
    from TeamWeaver.eai_adapter.semantic_decomposer import DeepSeekSemanticDecomposer

    messages = DeepSeekSemanticDecomposer(
        env={"DEEPSEEK_API_KEY": "test"},
    ).build_messages("Continue", _blocking_world())
    payload = json.loads(messages[1]["content"])

    assert payload["world_state"]["obstacles"][0]["obstacle_id"] == (
        "runtime_obstacle_1"
    )
    assert "remove_obstacle" in payload["allowed_task_types"]
    assert "obstacle_handling" in payload["capability_ontology"]
    assert payload["active_obstacle_contract"] == {
        "obstacle_id": "runtime_obstacle_1",
        "blocked_task_id": "inspect_fire",
        "required_remove_task_type": "remove_obstacle",
        "required_can_parallel": True,
        "replacement_task_id": "inspect_fire",
        "replacement_prerequisite": "the task_id of the remove_obstacle task",
    }
    assert "remove_obstacle" in messages[0]["content"]
    assert "can_parallel=true" in messages[0]["content"]
    assert "unrelated tasks may continue" in messages[0]["content"]
    assert "must not assign or prefer a robot" in messages[0]["content"]


def test_invalid_emergency_response_repairs_without_local_fallback():
    from TeamWeaver.eai_adapter.semantic_decomposer import DeepSeekSemanticDecomposer

    client = FakeClient(
        [
            json.dumps({"tasks": [valid_inspect_payload()]}),
            json.dumps(_emergency_response()),
        ]
    )
    result = DeepSeekSemanticDecomposer(
        env={"DEEPSEEK_API_KEY": "test"},
        client_factory=lambda **_kwargs: client,
    ).decompose("Continue after the obstacle appeared", _blocking_world(), max_attempts=2)

    assert result.source == "deepseek"
    assert result.attempts == 2
    repair = json.loads(client.completions.requests[1]["messages"][-1]["content"])
    assert any("obstacle removal" in item for item in repair["validation_errors"])
    assert repair["active_obstacle_contract"]["replacement_task_id"] == (
        "inspect_fire"
    )


def test_cancel_closes_the_active_default_http_transport(
    monkeypatch,
    factory_world,
):
    import threading

    import httpx
    import openai
    from TeamWeaver.eai_adapter.semantic_decomposer import DeepSeekSemanticDecomposer

    started = threading.Event()
    closed = threading.Event()

    class BlockingCompletions:
        def create(self, **_kwargs):
            started.set()
            closed.wait(timeout=5.0)
            raise RuntimeError("transport closed")

    class FakeHttpClient:
        def close(self):
            closed.set()

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=BlockingCompletions())
    )
    monkeypatch.setattr(httpx, "Client", lambda **_kwargs: FakeHttpClient())
    monkeypatch.setattr(openai, "OpenAI", lambda **_kwargs: client)
    decomposer = DeepSeekSemanticDecomposer(env={"DEEPSEEK_API_KEY": "test"})
    errors = []

    worker = threading.Thread(
        target=lambda: _capture_error(
            errors,
            lambda: decomposer.decompose("Inspect", factory_world),
        )
    )
    worker.start()
    assert started.wait(timeout=1.0)

    decomposer.cancel()
    worker.join(timeout=1.0)

    assert closed.is_set()
    assert worker.is_alive() is False
    assert errors


def _capture_error(errors, function):
    try:
        function()
    except Exception as exc:
        errors.append(exc)
