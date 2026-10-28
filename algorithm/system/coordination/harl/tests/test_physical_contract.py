"""CPU-only UR5 boundary audit; no simulator or physical-success fixtures.

The local inspection checkpoint is intentionally incompatible with UR5.
Adversarial header relabeling below is a rejection test, never a conversion.
"""
from copy import deepcopy
from dataclasses import replace
import json

import numpy as np
import pytest

from algorithm.common_coordination.contract import ExecutionFeedback, FailureCode
from algorithm.system.coordination.harl.native_adapter import NativeHAPPOError
from algorithm.system.coordination.harl.tests.test_native_adapter import (
    SOURCE, bind_snapshot, bound_bundle, bundle, package,
)
from benchmark.native_algorithms import build_native_adapter
from task.contracts import EaiTaskPackage, TaskRunRequest
from task.runner import TaskRunner
from tools.algorithm_adapters.isaac_pick_place import (
    case_document, native_decision_from_report, physical_options,
)


@pytest.fixture
def ur5_package(tmp_path):
    path = tmp_path / "ur5.json"
    path.write_text(json.dumps(case_document()))
    return EaiTaskPackage.from_json(path)


def test_physical_host_parameters_exist_but_are_not_policy_compatibility(ur5_package, tmp_path):
    options = physical_options(ur5_package, tmp_path / "unused.json")
    assert options.arm == "ur5" and options.trials == 1
    assert options.object_position == [0.45, 0.0, 0.475]
    assert options.place_position == [0.425, 0.01, 0.475]
    assert options.mode == "cartesian_pose"
    assert "happo_observation" not in ur5_package.robots[0].metadata
    assert not (tmp_path / "unused.json").exists()


@pytest.mark.parametrize("input_stage,error", [
    ("original", "explicit episode_id"),
    ("episode_only", "bound environment contract"),
    ("forged_headers", "actor-to-robot roster mismatch"),
])
def test_ur5_rejected_before_native_act_or_executor(
        bound_bundle, ur5_package, monkeypatch, input_stage, error):
    provider = build_native_adapter("harl_happo", source_root=SOURCE,
                                    manifest_path=str(bound_bundle), device="cpu")
    task = ur5_package
    if input_stage != "original":
        task = replace(task, metadata={**task.metadata, "episode_id": "ur5-negative-test"})
    if input_stage == "forged_headers":
        contract = provider.boundary.contract
        task = replace(task, scene_id=contract["scene_ids"][0],
                       observation_schema=contract["observation_schema"],
                       metadata={**task.metadata, "happo_contract_sha256": provider.boundary.contract_hash})
    calls = {"actor": 0, "executor": 0, "evaluator": 0}
    for actor in provider.actors:
        original = actor.act

        def observed_act(*args, _original=original, **kwargs):
            calls["actor"] += 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(actor, "act", observed_act)

    class MustNotExecute:
        def execute(self, *args, **kwargs):
            calls["executor"] += 1
            raise AssertionError("incompatible policy reached executor")

        def evaluate(self, **kwargs):
            calls["evaluator"] += 1
            raise AssertionError("incompatible policy reached evaluator")

    before = [state.copy() for state in provider.rnn_states]
    sentinel = MustNotExecute()
    with pytest.raises(NativeHAPPOError, match=error):
        TaskRunner(executor=sentinel, evaluator=sentinel).run(TaskRunRequest(task), provider)
    assert calls == {"actor": 0, "executor": 0, "evaluator": 0}
    assert provider.step == 0 and not provider.awaiting_feedback and not provider.pending
    for actual, expected in zip(provider.rnn_states, before):
        np.testing.assert_array_equal(actual, expected)


def test_real_holdout_outputs_are_not_collapsed_into_single_ur5_action(bound_bundle, package):
    provider = build_native_adapter("harl_happo", source_root=SOURCE,
                                    manifest_path=str(bound_bundle), device="cpu")
    task = bind_snapshot(package, provider)
    result = TaskRunner().run(TaskRunRequest(task), provider)
    traces = deepcopy(result.decision.provenance["actor_outputs"])
    assert len(traces) == 2 and result.decision.assignments
    assert result.verdict["status"] == "N/A"
    assert all(item.outcome == "not_executed" for item in result.feedback)
    assert provider.awaiting_feedback  # decision-only host has no execution acknowledgement
    native_report = {"status": "native_decision_returned", "result": result.to_dict(),
                     "provenance": dict(result.decision.provenance)}
    with pytest.raises(RuntimeError, match="no skill substituted"):
        native_decision_from_report(native_report)
    assert result.decision.provenance["actor_outputs"] == traces
    assert {item["robot_id"] for item in traces} == {"ground", "air"}


@pytest.mark.parametrize("outcome,code", [
    ("success", FailureCode.NONE), ("failure", FailureCode.EXECUTION_FAILURE),
])
def test_ur5_feedback_vocabulary_requires_declared_mapping(bound_bundle, package, outcome, code):
    provider = build_native_adapter("harl_happo", source_root=SOURCE,
                                    manifest_path=str(bound_bundle), device="cpu")
    task = bind_snapshot(package, provider)
    decision = provider(task, task.robots)
    assert decision.assignments
    pending = dict(provider.pending)
    states = [state.copy() for state in provider.rnn_states]
    # Correct identities/token deliberately isolate the outcome mismatch.
    # These records are negative protocol inputs, not physical execution evidence.
    feedback = [ExecutionFeedback(a.task_id, a.robot_id, outcome, code)
                for a in decision.assignments]
    with pytest.raises(NativeHAPPOError, match="outcome outside declared macro contract"):
        provider.update_feedback(feedback, decision_token=decision.provenance["feedback_token"])
    assert provider.pending == pending and provider.awaiting_feedback and not provider.completed
    for actual, expected in zip(provider.rnn_states, states):
        np.testing.assert_array_equal(actual, expected)
