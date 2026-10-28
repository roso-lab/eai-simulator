"""Protocol tests, NOT algorithm acceptance: scripted transport, original EMOS.

Run with an explicit trusted source root and native model dependencies installed:
EMOS_SOURCE_ROOT=/tmp/emos-1793068059 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /home/airs/miniconda3/envs/env_isaaclab/bin/python -m pytest -q algorithm/system/coordination/emos/tests/test_native_adapter.py

Only the completion transport is a deterministic double. The original prompt
builders, parsers, discussion loop, OpenAIModel.chat and interpreter are executed.
"""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from algorithm.common_coordination.contract import CoordinationTask, RobotCapability, TaskGraph
from algorithm.system.coordination.emos.native_adapter import (
    DISCUSSION_PATH, MODEL_PATH, EmosNativeAdapter, MeteredNativeEndpoint,
    NativeDiscussion, NativeEmosError, NativeEmosUnavailable, NativeOutputScopeError,
    OUTPUT_CONTRACT_VERSION, OUTPUT_CONTRACT_SCHEMA, OUTPUT_CONTRACT_SCHEMA_SHA256,
)
from benchmark.emos import CaseAssignmentRequest
from task.contracts import AlgorithmDecision, EaiTaskPackage, RobotState


class ScriptedTransport:
    """Explicit protocol-only completion responses; no assignment algorithm."""

    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        from openai.types.chat import ChatCompletion
        self.requests.append(deepcopy(kwargs))
        answer = next(self.responses)
        if isinstance(answer, Exception):
            raise answer
        return ChatCompletion.model_validate({
            "id": "protocol-double", "object": "chat.completion", "created": 0,
            "model": kwargs["model"],
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": answer, "tool_calls": None,
            }}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        })


@pytest.fixture
def case():
    robots = tuple(RobotState(RobotCapability(name, frozenset({"inspect"})), (i, 0, 0))
                   for i, name in enumerate(("r1", "r2")))
    tasks = (CoordinationTask("t1", "Inspect object one"),
             CoordinationTask("t2", "Inspect object two"))
    package = EaiTaskPackage("case-native", "fixture-only", TaskGraph(tasks, source="protocol-fixture"), robots)
    request = CaseAssignmentRequest("Inspect the two objects.", "case-native",
                                    {r.capability.robot_id: r.position for r in robots},
                                    ("t1", "t2"))
    resumes = {name: {"robot_type": "FixtureRobot", "mobility": {
        "summary": "Fixture can move on a flat plane"}} for name in ("r1", "r2")}
    observation = {"robot_positions": dict(request.robot_positions),
                   "scene_description": "Synthetic protocol fixture, native [x,z,y] coordinates. "
                   "Object one at [1,0,0]; object two at [2,0,0]."}
    return request, package, resumes, observation


@pytest.fixture
def native_root():
    root = Path(os.environ.get("EMOS_SOURCE_ROOT", "/home/airs/.cache/heterocoord/upstream/emos"))
    if not (root / DISCUSSION_PATH).is_file():
        pytest.skip("native EMOS source unavailable; protocol execution not verified")
    for dependency in ("openai", "docstring_parser", "colorama"):
        pytest.importorskip(dependency)
    paths = [root / DISCUSSION_PATH, root / MODEL_PATH]
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
    yield root
    assert before == [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]


@pytest.fixture
def make_adapter(case, tmp_path):
    adapters = []

    def make(*, native_root=None, responses=None, initialize=True, **kwargs):
        request, package, resumes, observation = case
        endpoint = None
        transport = None
        if responses is not None:
            transport = ScriptedTransport(responses)
            endpoint = MeteredNativeEndpoint(transport, model="protocol-double")
        adapter = EmosNativeAdapter(request, robot_resume=resumes, endpoint=endpoint,
                                    source_root=native_root, log_dir=tmp_path, **kwargs)
        adapters.append(adapter)
        if initialize:
            adapter.initialize({}, observation)
        return adapter, transport

    yield make
    for adapter in adapters:
        adapter.close()


def test_uninitialized_and_unconfigured_fail_closed(case, make_adapter):
    _, package, _, observation = case
    adapter, _ = make_adapter(initialize=False)
    with pytest.raises(NativeEmosError, match="must be initialized"):
        adapter(package, package.robots)
    adapter.initialize({}, observation)
    with pytest.raises(NativeEmosUnavailable, match="not configured; no fallback"):
        adapter(package, package.robots)
    assert adapter.native_results == {}


def test_missing_environment_does_not_construct_client(monkeypatch):
    monkeypatch.delenv("EMOS_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(NativeEmosUnavailable, match="no configured"):
        MeteredNativeEndpoint.from_environment()


def test_missing_source_fails_closed(tmp_path):
    with pytest.raises(NativeEmosUnavailable, match="missing required"):
        NativeDiscussion(tmp_path, MeteredNativeEndpoint(None))


def test_initialize_copies_history_observation_and_updates_request(case, make_adapter):
    _, _, _, observation = case
    adapter, _ = make_adapter(initialize=False)
    history = {"task_description": "Caller-owned task text", "excluded_task_ids": ["t2"],
               "evaluator_note": {"events": ["before"]}}
    result = adapter.initialize(history, observation)
    history["evaluator_note"]["events"].append("mutated")
    observation["robot_positions"]["r1"] = (99, 99, 99)
    copied = adapter.history
    copied["evaluator_note"]["events"].append("also mutated")
    assert adapter.history["evaluator_note"]["events"] == ["before"]
    assert result.excluded_task_ids == ("t2",)
    assert result.task_description == "Caller-owned task text"
    assert result.robot_positions["r1"] == (0, 0, 0)
    result.robot_positions["r1"] = (40, 40, 40)
    assert adapter.request.robot_positions["r1"] == (0, 0, 0)
    with pytest.raises(NativeEmosError, match="already initialized"):
        adapter.initialize({}, observation)


@pytest.mark.parametrize("bad_positions", [{"wrong": (0, 0, 0)}, {"r1": (1, 2), "r2": (0, 0, 0)},
                                          {"r1": (float("nan"), 0, 0), "r2": (0, 0, 0)}])
def test_observation_validation(case, make_adapter, bad_positions):
    adapter, _ = make_adapter(initialize=False)
    observation = {**case[3], "robot_positions": bad_positions}
    with pytest.raises(ValueError):
        adapter.initialize({}, observation)
    assert not adapter._initialized


def test_native_scene_text_required(case, make_adapter):
    adapter, _ = make_adapter(initialize=False)
    with pytest.raises(ValueError, match="explicit native scene"):
        adapter.initialize({}, {"robot_positions": case[0].robot_positions})


def test_update_requires_initialized_and_explicit_scene(case, make_adapter):
    adapter, _ = make_adapter(initialize=False)
    with pytest.raises(NativeEmosError, match="must be initialized"):
        adapter.update_observation(case[3])
    adapter.initialize({}, case[3])
    with pytest.raises(ValueError, match="explicit native scene"):
        adapter.update_observation({"robot_positions": case[0].robot_positions})
    updated = adapter.update_observation({**case[3], "robot_positions": {"r1": (8, 0, 0), "r2": (9, 0, 0)}})
    assert updated.robot_positions["r1"] == (8, 0, 0)


def test_external_history_feedback_and_replan_are_not_fabricated(case, make_adapter):
    adapter, _ = make_adapter(initialize=False)
    with pytest.raises(NotImplementedError, match="cannot restore"):
        adapter.initialize({"native_chat_history": ["past"]}, case[3])
    with pytest.raises(NotImplementedError, match="execution-feedback"):
        adapter.feedback({"outcome": "failed"})
    assert not hasattr(adapter, "replan")


def test_zero_initialization_budget(case, make_adapter):
    adapter, _ = make_adapter(initialize=False, initialization_budget_calls=0)
    with pytest.raises(NativeEmosError, match="initialization budget"):
        adapter.initialize({}, case[3])


@pytest.mark.parametrize("bad", [-1, True, 1.5])
def test_invalid_initialization_budget(make_adapter, bad):
    with pytest.raises(ValueError, match="non-negative integer"):
        make_adapter(initialize=False, initialization_budget_calls=bad)


@pytest.mark.parametrize("bad", [0, -1, True, 1.5])
def test_invalid_completion_budget(bad):
    with pytest.raises(ValueError, match="positive integer"):
        MeteredNativeEndpoint(None, max_calls=bad)


def test_runtime_roster_and_scenario_rejected_before_model(case, make_adapter):
    _, package, _, _ = case
    adapter, _ = make_adapter()
    with pytest.raises(ValueError, match="roster mismatch"):
        adapter(package, package.robots[:-1])
    with pytest.raises(ValueError, match="scenario mismatch"):
        adapter(replace(package, task_id="another"), package.robots)


def test_no_invented_robot_capabilities(case, tmp_path):
    request, _, resumes, _ = case
    resumes["r1"] = {"robot_type": "unknown"}
    with pytest.raises(ValueError, match="explicit capabilities"):
        EmosNativeAdapter(request, robot_resume=resumes, log_dir=tmp_path)


def test_original_native_discussion_and_models_execute(case, make_adapter, native_root):
    request, package, _, observation = case
    adapter, transport = make_adapter(native_root=native_root, responses=[
        "{r1||Inspect object one}{r2||Inspect object two}", "{yes}", "{yes}"])
    decision = adapter(package, package.robots)
    assert isinstance(decision, AlgorithmDecision)
    assert [(a.robot_id, a.task_id) for a in decision.assignments] == [("r1", "t1"), ("r2", "t2")]
    assert decision.unassigned_tasks == ()
    assert len(transport.requests) == 3
    assert adapter.endpoint.receipt()["total_tokens"] == 9
    assert adapter._native.module.group_discussion.__code__.co_filename == str(native_root / DISCUSSION_PATH)
    assert adapter._native.model_class.chat.__code__.co_filename == str(native_root / MODEL_PATH)
    assert request.task_description in transport.requests[0]["messages"][-1]["content"]
    assert observation["scene_description"] in transport.requests[0]["messages"][-1]["content"]
    assert transport.requests[1]["messages"][0]["content"].startswith('You are a "FixtureRobot"')
    assert all(r["model"] == "protocol-double" for r in transport.requests)
    assert decision.provenance["evidence_kind"] == "protocol_only"
    assert decision.provenance["algorithm_acceptance"] == "not_evaluated"
    assert decision.provenance["fallback_used"] is False
    assert decision.provenance["physical_success"] is False
    assert decision.provenance["history_restored_to_native"] is False
    assert adapter.native_history["r1"][0][1].content == "{yes}"
    assert (Path(decision.provenance["native_log_dir"]) / "leader_group_chat_history.json").is_file()
    saved = adapter.native_results
    saved["r1"].subtask_description = "mutated"
    assert adapter.native_results["r1"].subtask_description == "Inspect object one"


def test_original_reflection_reassigns_without_adapter_decisions(case, make_adapter, native_root):
    package = case[1]
    adapter, transport = make_adapter(native_root=native_root, responses=[
        "{r1||t1}{r2||t2}", "{no||Target beyond my workspace}", "{yes}",
        "{r1||t2}{r2||t1}", "{yes}", "{yes}"])
    decision = adapter(package, package.robots)
    assert [(a.robot_id, a.task_id) for a in decision.assignments] == [("r1", "t2"), ("r2", "t1")]
    assert len(transport.requests) == 6
    # This is the upstream feedback prompt, not an adapter-authored repair.
    assert "Robot r1 response: no, reason: Target beyond my workspace" in transport.requests[3]["messages"][-1]["content"]
    assert "Based on the feedback, please modify the task" in transport.requests[3]["messages"][-1]["content"]


def test_original_numerical_interpreter_executes(case, make_adapter, native_root, monkeypatch):
    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""))
    adapter, transport = make_adapter(native_root=native_root, responses=[
        "{r1||t1}{r2||t2}", '```python\nprint("sum: ", 1 + 1)\n```', "{yes}", "{yes}"])
    decision = adapter(case[1], case[1].robots)
    assert len(decision.assignments) == 2
    assert "sum:  2" in transport.requests[2]["messages"][-1]["content"]
    assert adapter.endpoint.calls == 4


def test_native_all_idle_remains_idle(case, make_adapter, native_root):
    adapter, _ = make_adapter(native_root=native_root, responses=[
        "{r1||Nothing to do}{r2||Nothing to do}", "{yes}", "{yes}"])
    decision = adapter(case[1], case[1].robots)
    assert decision.assignments == ()
    assert decision.unassigned_tasks == ("t1", "t2")


def test_coalition_is_preserved_and_explicitly_out_of_scope(case, make_adapter, native_root):
    adapter, _ = make_adapter(native_root=native_root, responses=[
        "{r1||t1}{r2||t1}", "{yes}", "{yes}"])
    with pytest.raises(NativeOutputScopeError, match="native coalition") as exc:
        adapter(case[1], case[1].robots)
    assert exc.value.native_subtasks == {"r1": "t1", "r2": "t1"}
    assert {r: v.subtask_description for r, v in adapter.native_results.items()} == exc.value.native_subtasks


def test_exact_predeclared_multi_task_binding(case, make_adapter, native_root):
    adapter, _ = make_adapter(native_root=native_root,
                              output_bindings={"Inspect both objects": ("t1", "t2")},
                              responses=["{r1||Inspect both objects}{r2||Nothing to do}", "{yes}", "{yes}"])
    decision = adapter(case[1], case[1].robots)
    assert [(a.robot_id, a.task_id) for a in decision.assignments] == [("r1", "t1"), ("r1", "t2")]


@pytest.mark.parametrize("output", ["inspect object one", "t1 and t2", "invented", " t1 "])
def test_no_semantic_matching_or_whitespace_repair(case, make_adapter, native_root, output):
    adapter, _ = make_adapter(native_root=native_root, responses=[
        "{r1||" + output + "}{r2||Nothing to do}", "{yes}", "{yes}"])
    with pytest.raises(NativeOutputScopeError, match="exact task binding"):
        adapter(case[1], case[1].robots)
    assert adapter.native_results["r1"].subtask_description == output


def test_ambiguous_description_not_guessed(case, make_adapter, native_root):
    package = replace(case[1], graph=TaskGraph((CoordinationTask("t1", "Same"), CoordinationTask("t2", "Same")),
                                             source="protocol-fixture"))
    adapter, _ = make_adapter(native_root=native_root, responses=[
        "{r1||Same}{r2||Nothing to do}", "{yes}", "{yes}"])
    with pytest.raises(NativeOutputScopeError, match="exact task binding"):
        adapter(package, package.robots)


def test_binding_cannot_reassign_canonical_id(case, make_adapter):
    adapter, _ = make_adapter(output_bindings={"t1": "t2"})
    with pytest.raises(ValueError, match="conflicts"):
        adapter(case[1], case[1].robots)


@pytest.mark.parametrize("response", ["malformed response", "{r1||t1}", "{unknown||t1}{r2||t2}"])
def test_malformed_or_missing_native_robot_is_not_filled(case, make_adapter, native_root, response):
    adapter, _ = make_adapter(native_root=native_root, responses=[response, "{yes}", "{yes}"])
    with pytest.raises(NativeEmosError, match="discussion failed"):
        adapter(case[1], case[1].robots)


def test_excluded_task_not_reassigned_or_repaired(case, make_adapter, native_root):
    adapter, _ = make_adapter(native_root=native_root, initialize=False, responses=[
        "{r1||t1}{r2||t2}", "{yes}", "{yes}"])
    adapter.initialize({"excluded_task_ids": ("t1",)}, case[3])
    with pytest.raises(NativeEmosError, match="excluded task"):
        adapter(case[1], case[1].robots)
    assert adapter.native_results["r1"].subtask_description == "t1"


def test_inactive_robot_not_replaced(case, make_adapter, native_root):
    package = case[1]
    robots = (replace(package.robots[0], capability=replace(package.robots[0].capability, active=False)),
              package.robots[1])
    adapter, _ = make_adapter(native_root=native_root, responses=[
        "{r1||t1}{r2||t2}", "{yes}", "{yes}"])
    with pytest.raises(NativeEmosError, match="inactive robot"):
        adapter(package, robots)


def test_endpoint_failure_is_redacted_and_never_falls_back(case, make_adapter, native_root):
    adapter, _ = make_adapter(native_root=native_root, responses=[RuntimeError("secret-provider-value")])
    with pytest.raises(NativeEmosError, match="endpoint failed; no fallback") as exc:
        adapter(case[1], case[1].robots)
    assert "secret-provider-value" not in str(exc.value)
    assert exc.value.__suppress_context__
    assert adapter.endpoint.calls == adapter.endpoint.failed_calls == 1
    assert adapter.native_results == {}


def test_completion_budget_counts_inside_native_discussion(case, make_adapter, native_root):
    adapter, transport = make_adapter(native_root=native_root, responses=[
        "{r1||t1}{r2||t2}", "{yes}", "{yes}"])
    adapter.endpoint.max_calls = 2
    with pytest.raises(NativeEmosError, match="completion budget exhausted"):
        adapter(case[1], case[1].robots)
    assert adapter.endpoint.calls == len(transport.requests) == 2
    assert adapter.native_results == {}


def test_later_call_uses_new_native_scene_not_cached_assignment(case, make_adapter, native_root):
    adapter, transport = make_adapter(native_root=native_root, responses=[
        "{r1||t1}{r2||t2}", "{yes}", "{yes}",
        "{r1||t2}{r2||t1}", "{yes}", "{yes}"])
    first = adapter(case[1], case[1].robots)
    adapter.update_observation({**case[3], "scene_description": "Changed native scene from evaluator"})
    assert adapter.native_history == {}
    second = adapter(case[1], case[1].robots)
    assert first.assignments != second.assignments
    assert "Changed native scene from evaluator" in transport.requests[3]["messages"][-1]["content"]
    assert second.provenance["discussion_calls"] == 2
    assert second.provenance["model_endpoint"]["completion_attempts"] == 6


def test_private_source_loading_does_not_monkeypatch_global_sdk(native_root):
    import openai
    original = openai.OpenAI
    first = NativeDiscussion(native_root, MeteredNativeEndpoint(ScriptedTransport([])))
    second = NativeDiscussion(native_root, MeteredNativeEndpoint(ScriptedTransport([])))
    try:
        assert openai.OpenAI is original
        assert first.module is not second.module
        assert first.model_class is not second.model_class
        assert first.module.create_leader_prompt("resume") == second.module.create_leader_prompt("resume")
    finally:
        first.close()
        second.close()


@pytest.mark.parametrize("green_robot", ["scout_1", "m20_1"])
def test_fire_rescue_boundary_preserves_native_and_rejects_invalid_binding(native_root, tmp_path, green_robot):
    from benchmark.emos import package_from_fire_rescue_request
    from task.fixtures.fire_rescue import build_fire_rescue_assignment_request
    request = build_fire_rescue_assignment_request()
    package = package_from_fire_rescue_request(request)
    mapping = {name: "green" if name == green_robot else "Nothing to do"
               for name in request.robot_positions}
    leader_text = "".join("{" + name + "||" + target + "}" for name, target in mapping.items())
    transport = ScriptedTransport([leader_text] + ["{yes}"] * len(mapping))
    endpoint = MeteredNativeEndpoint(transport)
    resumes = {name: {"robot_type": "FixtureOnly", "mobility": {"summary": "fixture"}}
               for name in mapping}
    adapter = EmosNativeAdapter(request, robot_resume=resumes, endpoint=endpoint,
                                source_root=native_root, log_dir=tmp_path)
    try:
        adapter.initialize({}, {"robot_positions": request.robot_positions,
                                "scene_description": "Fire Rescue protocol fixture; no physical evidence."})
        if green_robot == "scout_1":
            decision = adapter(package, package.robots)
            assert [(a.robot_id, a.task_id) for a in decision.assignments] == [("scout_1", "green")]
        else:
            with pytest.raises(ValueError, match="requires scout_1"):
                adapter(package, package.robots)
        assert {name: arg.subtask_description for name, arg in adapter.native_results.items()} == mapping
        assert endpoint.calls == len(mapping) + 1
    finally:
        adapter.close()


def test_original_discussion_round_limit_not_altered(case, make_adapter, native_root):
    # Native returns its final allocation even if an agent still says no after
    # three refinement rounds. Preserve that behavior without claiming acceptance.
    adapter, transport = make_adapter(native_root=native_root, responses=[
        "{r1||t1}{r2||t2}", "{no||still infeasible}", "{yes}"] * 4)
    decision = adapter(case[1], case[1].robots)
    assert len(transport.requests) == 12
    assert decision.provenance["algorithm_acceptance"] == "not_evaluated"
    assert adapter.native_history["r1"][-1][-1].content == "{no||still infeasible}"


@pytest.fixture
def json_config():
    import json
    repo = Path(__file__).resolve().parents[5]
    return json.loads((repo / "records/algorithm_adapters/emos/config.example.json").read_text())


def test_json_case_and_fire_rescue_requests(json_config):
    from dataclasses import asdict
    from tools.algorithm_adapters.emos_config import request_from_dict
    from task.fixtures.fire_rescue import build_fire_rescue_assignment_request
    case_request = request_from_dict(json_config["request"])
    assert isinstance(case_request, CaseAssignmentRequest)
    assert case_request.task_ids == ("t1", "t2")
    fire = build_fire_rescue_assignment_request()
    restored = request_from_dict({"kind": "fire_rescue", **asdict(fire)})
    assert asdict(restored) == asdict(fire)


def test_json_build_initializes_with_live_endpoint_only(json_config, native_root, monkeypatch, tmp_path):
    import openai
    from tools.algorithm_adapters.emos_config import build_emos_from_config, task_package_from_dict
    calls = []
    transport = ScriptedTransport(["{r1||Inspect object one}{r2||Inspect object two}", "{yes}", "{yes}"])
    def create_client(**kwargs):
        calls.append(kwargs)
        return transport
    # Explicit protocol test double at SDK boundary; production has no selection.
    monkeypatch.setattr(openai, "OpenAI", create_client)
    monkeypatch.setenv("EMOS_API_KEY", "test-secret-never-report")
    monkeypatch.setenv("EMOS_BASE_URL", "https://protocol-test.invalid/v1")
    monkeypatch.setenv("EMOS_MODEL", "protocol-test-model")
    adapter = build_emos_from_config(json_config, source_root=native_root, base_dir=tmp_path)
    try:
        assert adapter._initialized
        assert adapter.endpoint.calls == 0
        assert calls[0]["max_retries"] == 0
        assert calls[0]["timeout"] == 30
        assert adapter.request.robot_positions == {"r1": (0, 0, 0), "r2": (1, 0, 0)}
        package = task_package_from_dict(json_config["task_package"])
        decision = adapter(package, package.robots)
        assert len(decision.assignments) == 2
        assert adapter.endpoint.calls == 3
    finally:
        adapter.close()


@pytest.mark.parametrize("field", ["api_key_env", "base_url_env", "model_env"])
def test_explicit_endpoint_reference_never_uses_another_environment(json_config, monkeypatch, field):
    from tools.algorithm_adapters.emos_config import build_emos_from_config
    for name in ["EMOS_API_KEY", "EMOS_BASE_URL", "EMOS_MODEL"]:
        monkeypatch.setenv(name, "populated-for-test")
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://unrelated.invalid")
    monkeypatch.setenv("OPENAI_MODEL", "unrelated-model")
    monkeypatch.delenv(json_config["endpoint"][field])
    with pytest.raises(NativeEmosUnavailable):
        build_emos_from_config(json_config)


@pytest.mark.parametrize("key,value", [("api_key", "literal-secret"), ("base_url", "https://example.invalid"),
                                      ("client", "fixture"), ("protocol_only", True)])
def test_json_rejects_inline_endpoint_values_and_test_transports(json_config, key, value):
    from tools.algorithm_adapters.emos_config import build_emos_from_config, EmosConfigError
    json_config["endpoint"][key] = value
    with pytest.raises(EmosConfigError, match="references and limits only") as exc:
        build_emos_from_config(json_config)
    assert "literal-secret" not in str(exc.value)


@pytest.mark.parametrize("field,value", [("model_env", "gpt-4o"), ("api_key_env", "sk-literal-secret")])
def test_endpoint_fields_require_variable_names(json_config, field, value):
    from tools.algorithm_adapters.emos_config import endpoint_settings, EmosConfigError
    json_config["endpoint"][field] = value
    with pytest.raises(EmosConfigError):
        endpoint_settings(json_config["endpoint"])


def test_invalid_json_is_rejected_without_echo(tmp_path):
    from tools.algorithm_adapters.emos_config import load_config, EmosConfigError
    path = tmp_path / "config.json"
    path.write_text('{"secret":"private-value", "schema":')
    with pytest.raises(EmosConfigError) as exc:
        load_config(path)
    assert "private-value" not in str(exc.value)
    path.write_text('{"schema":"a", "schema":"b"}')
    with pytest.raises(EmosConfigError, match="duplicate"):
        load_config(path)


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), "1"])
def test_json_positions_are_strict_numeric(json_config, bad):
    from tools.algorithm_adapters.emos_config import request_from_dict, EmosConfigError
    json_config["request"]["robot_positions"]["r1"][0] = bad
    with pytest.raises(EmosConfigError):
        request_from_dict(json_config["request"])


def test_json_task_observation_mismatch_fails_before_endpoint(json_config):
    from tools.algorithm_adapters.emos_config import build_emos_from_config, EmosConfigError
    json_config["task_package"]["robots"][0]["position"][0] = 40
    with pytest.raises(EmosConfigError, match="positions/roster"):
        build_emos_from_config(json_config)


def test_cli_live_only_blocked_receipt_and_no_secret_output(json_config, native_root, tmp_path, monkeypatch, capsys):
    import json
    from tools.algorithm_adapters.emos_config import main
    path, report = tmp_path / "config.json", tmp_path / "report.json"
    path.write_text(json.dumps(json_config))
    monkeypatch.delenv("EMOS_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-used-or-printed")
    result = main(["--config", str(path), "--report", str(report), "--source-root", str(native_root)])
    output = capsys.readouterr()
    receipt = json.loads(report.read_text())
    assert result == 2
    assert receipt["status"] == "blocked"
    assert receipt["model_requests"] == 0
    assert receipt["mode"] == "native_api_only"
    assert receipt["source_receipt"]["commit"]
    assert "must-not-be-used-or-printed" not in report.read_text() + output.out + output.err


def test_cli_does_not_overwrite_config(json_config, tmp_path, capsys):
    import json
    from tools.algorithm_adapters.emos_config import main
    path = tmp_path / "config.json"
    before = json.dumps(json_config)
    path.write_text(before)
    assert main(["--config", str(path), "--report", str(path)]) == 1
    assert path.read_text() == before


def test_cli_protocol_mode_not_available(tmp_path):
    from tools.algorithm_adapters.emos_config import main
    with pytest.raises(SystemExit) as exc:
        main(["--config", str(tmp_path / "config.json"), "--report", str(tmp_path / "report.json"),
              "--mode", "protocol"])
    assert exc.value.code == 2


@pytest.mark.parametrize("max_calls,leader_text,expected_status,expected_calls", [
    (3, "{r1||t1}{r2||t2}", "native_decision_returned", 3),
    (2, "{r1||t1}{r2||t2}", "failed_closed", 2),
    (3, "{r1||t1}{r2||t1}", "failed_closed", 3),
])
def test_cli_native_loop_metering_and_coalition_receipt_protocol_only(
    json_config, native_root, tmp_path, monkeypatch, max_calls, leader_text, expected_status, expected_calls
):
    import json
    import openai
    from tools.algorithm_adapters.emos_config import main
    # SDK transport double only; the CLI has no mock/protocol production branch.
    transport = ScriptedTransport([leader_text, "{yes}", "{yes}"])
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: transport)
    monkeypatch.setenv("EMOS_API_KEY", "private-test-value")
    monkeypatch.setenv("EMOS_BASE_URL", "https://test.invalid/v1")
    monkeypatch.setenv("EMOS_MODEL", "protocol-only-test")
    json_config["endpoint"]["max_calls"] = max_calls
    path, report = tmp_path / "config.json", tmp_path / "report.json"
    path.write_text(json.dumps(json_config))
    exit_code = main(["--config", str(path), "--report", str(report), "--source-root", str(native_root)])
    receipt = json.loads(report.read_text())
    assert exit_code == (0 if expected_status == "native_decision_returned" else 1)
    assert receipt["status"] == expected_status
    assert receipt["model_requests"] == expected_calls == len(transport.requests)
    assert receipt["endpoint"]["completion_attempts"] == expected_calls
    assert receipt["algorithm_acceptance"] == "not_passed"
    assert "private-test-value" not in report.read_text()
    if leader_text == "{r1||t1}{r2||t1}":
        assert receipt["raw_native_subtasks"] == {"r1": "t1", "r2": "t1"}
        assert receipt["error_type"] == "NativeOutputScopeError"


def test_unified_factory_json_constructor_and_native_decision_protocol_only(
    json_config, native_root, tmp_path, monkeypatch
):
    """Real unified factory/source gate; only completion IO is a protocol double."""
    import openai
    from benchmark.native_algorithms import build_native_adapter, VerifiedNativeAdapter
    from tools.algorithm_adapters.emos_config import task_package_from_dict
    transport = ScriptedTransport(["{r1||t1}{r2||t2}", "{yes}", "{yes}"])
    client_args = []

    def sdk_client(**kwargs):
        client_args.append(kwargs)
        return transport

    monkeypatch.setattr(openai, "OpenAI", sdk_client)
    monkeypatch.setenv("EMOS_API_KEY", "factory-protocol-test-secret")
    monkeypatch.setenv("EMOS_BASE_URL", "https://factory-protocol-test.invalid/v1")
    monkeypatch.setenv("EMOS_MODEL", "factory-protocol-test-model")
    json_config["history"] = {"evaluator_note": ["factory-protocol-only"]}
    adapter = build_native_adapter("emos", source_root=native_root,
                                   json_config=json_config, config_base_dir=tmp_path)
    try:
        assert isinstance(adapter, VerifiedNativeAdapter)
        assert isinstance(adapter.provider, EmosNativeAdapter)
        assert adapter.provider._initialized is True
        assert adapter.provider._initialization_calls == 1
        assert adapter.provider.log_dir == (tmp_path / "cli_native_logs").resolve()
        assert adapter.provider.history == json_config["history"]
        assert adapter.provider.source_root.resolve() == native_root.resolve()
        assert adapter.source_receipt.commit == "e9501db45d634b087bf5d1a14228266685e8feeb"
        assert adapter.endpoint.calls == 0
        assert client_args[0]["max_retries"] == 0
        assert not hasattr(adapter, "replan")
        task = task_package_from_dict(json_config["task_package"])
        decision = adapter(task, task.robots)
        assert [(a.robot_id, a.task_id) for a in decision.assignments] == [("r1", "t1"), ("r2", "t2")]
        assert decision.provenance["paper_algorithm"] == "emos"
        assert decision.provenance["native_adapter_call"] == 1
        assert decision.provenance["model_endpoint"]["completion_attempts"] == 3
        assert decision.provenance["source_integrity"]["commit"] == adapter.source_receipt.commit
        assert decision.provenance["algorithm_acceptance"] == "not_evaluated"
        assert decision.provenance["decision_is_physical_evidence"] is False
    finally:
        adapter.close()


def test_unified_factory_without_live_environment_fails_closed(json_config, native_root, tmp_path, monkeypatch):
    from benchmark.native_algorithms import build_native_adapter
    monkeypatch.delenv("EMOS_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-secret-must-not-be-used")
    with pytest.raises(NativeEmosUnavailable, match="no configured") as exc:
        build_native_adapter("emos", source_root=native_root,
                             json_config=json_config, config_base_dir=tmp_path)
    assert "unrelated-secret" not in str(exc.value)
    assert exc.value.model_requests == 0
    assert exc.value.endpoint_receipt["completion_attempts"] == 0
    assert exc.value.native_subtasks == {}


@pytest.mark.parametrize("max_calls,leader_text,error_type", [
    (2, "{r1||t1}{r2||t2}", NativeEmosError),
    (3, "{r1||t1}{r2||t1}", NativeOutputScopeError),
])
def test_unified_factory_preserves_native_failure_and_metering_protocol_only(
    json_config, native_root, tmp_path, monkeypatch, max_calls, leader_text, error_type
):
    import openai
    from benchmark.native_algorithms import build_native_adapter
    from tools.algorithm_adapters.emos_config import task_package_from_dict
    transport = ScriptedTransport([leader_text, "{yes}", "{yes}"])
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: transport)
    monkeypatch.setenv("EMOS_API_KEY", "factory-protocol-test-secret")
    monkeypatch.setenv("EMOS_BASE_URL", "https://factory-protocol-test.invalid/v1")
    monkeypatch.setenv("EMOS_MODEL", "factory-protocol-test-model")
    json_config["endpoint"]["max_calls"] = max_calls
    adapter = build_native_adapter("emos", source_root=native_root,
                                   json_config=json_config, config_base_dir=tmp_path)
    try:
        task = task_package_from_dict(json_config["task_package"])
        with pytest.raises(error_type) as exc:
            adapter(task, task.robots)
        assert adapter.calls == 0  # no successful AlgorithmDecision in envelope
        assert adapter.endpoint.calls == max_calls
        assert len(transport.requests) == max_calls
        assert exc.value.model_requests == max_calls
        assert exc.value.endpoint_receipt["completion_attempts"] == max_calls
        if error_type is NativeOutputScopeError:
            assert exc.value.native_subtasks == {"r1": "t1", "r2": "t1"}
            assert {key: result.subtask_description for key, result in adapter.native_results.items()} == exc.value.native_subtasks
    finally:
        adapter.close()


def test_unified_cli_preserves_native_subtasks_protocol_only(json_config, native_root, tmp_path, monkeypatch, capsys):
    """Shared CLI -> shared factory -> native planner; deterministic SDK IO only."""
    import json
    import openai
    from tools.algorithm_adapters.run import main
    transport = ScriptedTransport(["{r1||t1}{r2||t1}", "{yes}", "{yes}"])
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: transport)
    monkeypatch.setenv("EMOS_API_KEY", "unified-cli-protocol-test-secret")
    monkeypatch.setenv("EMOS_BASE_URL", "https://unified-cli-test.invalid/v1")
    monkeypatch.setenv("EMOS_MODEL", "unified-cli-protocol-only")
    config_path, task_path, report = (tmp_path / name for name in ("config.json", "task.json", "receipt.json"))
    config_path.write_text(json.dumps(json_config))
    task_path.write_text(json.dumps(json_config["task_package"]))
    monkeypatch.setattr(sys, "argv", ["run", "--algorithm", "emos", "--source-root", str(native_root),
                                    "--config", str(config_path), "--task", str(task_path), "--report", str(report)])
    with pytest.raises(SystemExit) as exc:
        main()
    output = capsys.readouterr()
    receipt = json.loads(report.read_text())
    assert exc.value.code == 1
    assert receipt["status"] == "not_passed"
    assert receipt["error_type"] == "NativeOutputScopeError"
    assert receipt["rejected_native_subtasks"] == {"r1": "t1", "r2": "t1"}
    assert len(transport.requests) == 3
    assert "unified-cli-protocol-test-secret" not in report.read_text() + output.out + output.err


def test_reasoning_effort_only_changes_endpoint_parameters():
    transport = ScriptedTransport(["unchanged native output"])
    endpoint = MeteredNativeEndpoint(transport, model="gpt-5.6-luna", reasoning_effort="low")
    messages = [{"role": "system", "content": "original system"},
                {"role": "user", "content": "original task"}]
    result = endpoint.create(model="gpt-4o", messages=messages)
    assert transport.requests == [{"model": "gpt-5.6-luna", "reasoning_effort": "low", "messages": messages}]
    assert result.choices[0].message.content == "unchanged native output"
    assert endpoint.receipt()["reasoning_effort"] == "low"
    assert endpoint.response_records[0]["response"]["choices"][0]["message"]["content"] == "unchanged native output"


def test_explicit_reasoning_environment_is_required(json_config, monkeypatch):
    from tools.algorithm_adapters.emos_config import build_emos_from_config
    json_config["endpoint"]["reasoning_effort_env"] = "TEST_EXPLICIT_EMOS_REASONING"
    monkeypatch.setenv("EMOS_API_KEY", "test-key")
    monkeypatch.setenv("EMOS_BASE_URL", "https://test.invalid/v1")
    monkeypatch.setenv("EMOS_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "low")
    monkeypatch.delenv("TEST_EXPLICIT_EMOS_REASONING", raising=False)
    with pytest.raises(NativeEmosUnavailable, match="reasoning effort environment variable is unset"):
        build_emos_from_config(json_config)


def test_reasoning_env_constructor_preserves_native_request(json_config, monkeypatch):
    import openai
    from tools.algorithm_adapters.emos_config import build_emos_from_config
    transport = ScriptedTransport(["native"])
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: transport)
    monkeypatch.setenv("EMOS_API_KEY", "test-key")
    monkeypatch.setenv("EMOS_BASE_URL", "https://test.invalid/v1")
    monkeypatch.setenv("EMOS_MODEL", "gpt-5.6-luna")
    monkeypatch.setenv("TEST_EMOS_REASONING", "low")
    json_config["endpoint"]["reasoning_effort_env"] = "TEST_EMOS_REASONING"
    adapter = build_emos_from_config(json_config)
    try:
        adapter.endpoint.create(model="gpt-4o", messages=[{"role": "user", "content": "native"}])
        assert transport.requests[0]["reasoning_effort"] == "low"
    finally:
        adapter.close()


def test_raw_endpoint_outputs_survive_native_mapping_failure(case, make_adapter, native_root):
    import json
    adapter, _ = make_adapter(native_root=native_root, responses=[
        "{r1||Unmapped original subtask}{r2||Nothing to do}", "{yes}", "{yes}"])
    with pytest.raises(NativeOutputScopeError):
        adapter(case[1], case[1].robots)
    trace = json.loads(next(adapter.log_dir.glob("endpoint_trace_*.json")).read_text())
    assert trace["endpoint"]["completion_attempts"] == 3
    assert trace["responses"][0]["response"]["choices"][0]["message"]["content"] == "{r1||Unmapped original subtask}{r2||Nothing to do}"


def allocation(*entries):
    return json.dumps({"contract_version": OUTPUT_CONTRACT_VERSION, "kind": "assignment",
                       "assignments": [dict(robot_id=r, state=s, task_ids=t, subtask_description=d)
                                       for r, s, t, d in entries], "feedback": None})


def reflection(robot, state="yes", reason=None, code=None):
    return json.dumps({"contract_version": OUTPUT_CONTRACT_VERSION,
                       "kind": "computation" if code else "feedback", "assignments": [],
                       "feedback": {"robot_id": robot, "state": state, "reason": reason,
                                    "python_code": code}})


def structured_adapter(make_adapter, native_root, responses):
    adapter, transport = make_adapter(native_root=native_root, responses=responses)
    adapter.endpoint.output_contract_version = OUTPUT_CONTRACT_VERSION
    return adapter, transport


def test_structured_valid_multitask_idle_and_raw_roundtrip(case, make_adapter, native_root):
    raw = allocation(("r1", "assigned", ["t1", "t2"], "Inspect both original objects"),
                     ("r2", "idle", [], "Waiting for the other robot"))
    adapter, transport = structured_adapter(make_adapter, native_root,
                                            [raw, reflection("r1"), reflection("r2")])
    decision = adapter(case[1], case[1].robots)
    assert [(a.robot_id, a.task_id) for a in decision.assignments] == [("r1", "t1"), ("r1", "t2")]
    assert decision.provenance["native_structured_output"] == json.loads(raw)
    assert adapter.native_results["r2"].subtask_description == "Nothing to do"
    assert adapter.endpoint.response_records[0]["response"]["choices"][0]["message"]["content"] == raw
    assert OUTPUT_CONTRACT_VERSION in transport.requests[0]["messages"][0]["content"]
    assert "task_id" in transport.requests[0]["messages"][0]["content"]
    assert "ADAPTER OUTPUT CONTRACT" not in adapter._native.module.create_leader_prompt("resume")
    assert decision.provenance["adaptation_condition"]["output_contract"]["schema_sha256"] == OUTPUT_CONTRACT_SCHEMA_SHA256


def test_structured_coalition_is_not_dropped(case, make_adapter, native_root):
    raw = allocation(("r1", "assigned", ["t1"], "Jointly inspect object one"),
                     ("r2", "assigned", ["t1"], "Jointly inspect object one"))
    adapter, _ = structured_adapter(make_adapter, native_root, [raw, reflection("r1"), reflection("r2")])
    with pytest.raises(NativeOutputScopeError, match="coalition") as exc:
        adapter(case[1], case[1].robots)
    assert exc.value.native_result["structured_output"] == json.loads(raw)
    assert exc.value.native_result["adaptation_condition"]["output_contract_schema_sha256"] == OUTPUT_CONTRACT_SCHEMA_SHA256
    assert len(exc.value.native_result["adaptation_condition"]["contract_prompt_sha256"]) == 3


def test_structured_native_reflection_reassignment_and_computation(case, make_adapter, native_root, monkeypatch):
    import shutil
    python = shutil.which("python")
    if python is None:
        monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    first = allocation(("r1", "assigned", ["t1"], "Inspect object one"),
                       ("r2", "assigned", ["t2"], "Inspect object two"))
    second = allocation(("r1", "assigned", ["t2"], "Inspect object two"),
                        ("r2", "assigned", ["t1"], "Inspect object one"))
    adapter, transport = structured_adapter(make_adapter, native_root, [
        first, reflection("r1", "compute", code="print('distance: 2')"),
        reflection("r1", "no", "Object one is out of reach"), reflection("r2"),
        second, reflection("r1"), reflection("r2")])
    decision = adapter(case[1], case[1].robots)
    assert [(a.robot_id, a.task_id) for a in decision.assignments] == [("r1", "t2"), ("r2", "t1")]
    assert "distance: 2" in str(transport.requests[2]["messages"])
    assert "Object one is out of reach" in str(transport.requests[4]["messages"])
    assert adapter.endpoint.calls == 7


@pytest.mark.parametrize("raw", [
    "not json", "[]", '{"contract_version":"bad"}',
    allocation(("r1", "assigned", ["unknown"], "Native description"), ("r2", "idle", [], "")),
    allocation(("r1", "assigned", ["t1"], "Native description"), ("r1", "idle", [], "")),
    allocation(("r1", "assigned", [], "Native description"), ("r2", "idle", [], "")),
    allocation(("r1", "idle", ["t1"], "Idle"), ("r2", "idle", [], "")),
    allocation(("r1", "assigned", ["t1", "t1"], "Native description"), ("r2", "idle", [], "")),
    allocation(("r1", "assigned", ["t1"], "unsafe } envelope"), ("r2", "idle", [], "")),
    allocation(("r1", "assigned", ["t1"], "Nothing to do"), ("r2", "idle", [], "")),
    allocation(("r1", "assigned", ["t1"], ""), ("r2", "idle", [], "")),
    reflection("r1"),
    '{"contract_version":"emos.native-output.v1","contract_version":"bad"}',
])
def test_structured_invalid_rejected_with_raw_and_hashes(case, make_adapter, native_root, raw):
    adapter, _ = structured_adapter(make_adapter, native_root, [raw])
    with pytest.raises(NativeEmosError) as exc:
        adapter(case[1], case[1].robots)
    assert adapter.endpoint.calls == 1
    assert adapter.endpoint.total_tokens == 3
    assert adapter.endpoint.failed_calls == 0
    assert adapter.endpoint.contract_rejections == 1
    trace = json.loads(next(adapter.log_dir.glob("endpoint_trace_*.json")).read_text())
    assert trace["responses"][0]["response"]["choices"][0]["message"]["content"] == raw
    assert exc.value.native_result["adaptation_condition"]["output_contract"]["prompt_sha256_by_speaker"]


def test_structured_wrong_feedback_identity_retains_allocation(case, make_adapter, native_root):
    raw = allocation(("r1", "assigned", ["t1"], "Inspect object one"), ("r2", "idle", [], ""))
    adapter, _ = structured_adapter(make_adapter, native_root, [raw, reflection("r2")])
    with pytest.raises(NativeEmosError, match="identity") as exc:
        adapter(case[1], case[1].robots)
    assert exc.value.native_result["structured_output"] == json.loads(raw)
    assert exc.value.native_subtasks["r1"] == "Inspect object one"


def test_structured_refusal_remains_refusal(case, make_adapter, native_root):
    raw = allocation(("r1", "refused", ["t1"], "No capability"), ("r2", "idle", [], ""))
    adapter, _ = structured_adapter(make_adapter, native_root, [raw, reflection("r1"), reflection("r2")])
    with pytest.raises(NativeOutputScopeError, match="refused") as exc:
        adapter(case[1], case[1].robots)
    assert exc.value.native_result["structured_output"]["assignments"][0]["state"] == "refused"


def test_structured_reflection_feedback_is_retained_without_changing_upstream_return(case, make_adapter, native_root):
    raw = allocation(("r1", "assigned", ["t1"], "Inspect object one"), ("r2", "idle", [], ""))
    adapter, _ = structured_adapter(make_adapter, native_root,
        [item for _ in range(4) for item in (raw, reflection("r1", "refused", "Cannot inspect"), reflection("r2"))])
    decision = adapter(case[1], case[1].robots)
    assert [(a.robot_id, a.task_id) for a in decision.assignments] == [("r1", "t1")]
    assert adapter.endpoint.calls == 12
    assert any(isinstance(turn["document"].get("feedback"), dict)
               and turn["document"]["feedback"].get("state") == "refused"
               for turn in adapter.endpoint.structured_outputs)


def test_structured_factory_opt_in_and_failure_receipt(json_config, native_root, monkeypatch, tmp_path):
    import openai
    from benchmark.native_algorithms import build_native_adapter
    json_config["endpoint"]["output_contract_version"] = OUTPUT_CONTRACT_VERSION
    transport = ScriptedTransport(["invalid JSON"])
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: transport)
    monkeypatch.setenv("EMOS_API_KEY", "structured-protocol-secret")
    monkeypatch.setenv("EMOS_BASE_URL", "https://structured.invalid/v1")
    monkeypatch.setenv("EMOS_MODEL", "protocol-only")
    from tools.algorithm_adapters.emos_config import task_package_from_dict
    task = task_package_from_dict(json_config["task_package"])
    adapter = build_native_adapter("emos", json_config=json_config, source_root=native_root, config_base_dir=tmp_path)
    try:
        with pytest.raises(NativeEmosError) as exc:
            adapter(task, task.robots)
        assert exc.value.native_source_provenance["paper_algorithm"] == "emos"
        assert exc.value.native_result["adaptation_condition"]["output_contract_schema_sha256"] == OUTPUT_CONTRACT_SCHEMA_SHA256
    finally:
        adapter.close()


def test_output_contract_condition_identity_is_distinct_and_legacy_unchanged(case, make_adapter, native_root):
    task = case[1]
    legacy, _ = make_adapter(native_root=native_root, responses=[
        "{r1||t1}{r2||Nothing to do}", "{yes}", "{yes}"])
    legacy_decision = legacy(task, task.robots)
    upstream_hash = hashlib.sha256((native_root / DISCUSSION_PATH).read_bytes()).hexdigest()
    assert legacy_decision.condition.prompt_revision == upstream_hash
    assert legacy_decision.condition.notes == "No native decentralized action execution or physical acceptance is claimed."
    assert legacy_decision.assignments[0].plan_version == "upstream-stage-one-v1"

    raw = allocation(("r1", "assigned", ["t1"], "Inspect object one"),
                     ("r2", "idle", [], ""))
    adapter, _ = structured_adapter(make_adapter, native_root, [raw, reflection("r1"), reflection("r2")])
    decision = adapter(task, task.robots)
    metadata = adapter.endpoint.contract_metadata()
    revision_inputs = {
        "upstream_prompt_sha256": upstream_hash,
        "output_contract_schema_sha256": OUTPUT_CONTRACT_SCHEMA_SHA256,
        "speaker_suffix_sha256": metadata["prompt_sha256_by_speaker"],
    }
    expected = hashlib.sha256(json.dumps(revision_inputs, sort_keys=True,
        separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
    assert decision.condition.prompt_revision == expected != legacy_decision.condition.prompt_revision
    assert OUTPUT_CONTRACT_VERSION in decision.condition.notes
    assert decision.assignments[0].plan_version == "upstream-stage-one-v1+" + OUTPUT_CONTRACT_VERSION
    # The catalog changes the actual speaker suffixes and therefore condition identity.
    changed_task = replace(task, graph=TaskGraph((replace(task.graph.tasks[0], description="Inspect a changed object"),
                                                 task.graph.tasks[1]), source="changed-catalog"))
    changed, _ = structured_adapter(make_adapter, native_root, [raw, reflection("r1"), reflection("r2")])
    assert changed(changed_task, changed_task.robots).condition.prompt_revision != expected
