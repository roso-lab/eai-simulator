from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil

import numpy as np
import pytest
import torch

from algorithm.system.coordination.harl.native_adapter import (
    CONTRACT, NativeHAPPOAdapter, NativeHAPPOError, digest, encode,
)
from algorithm.system.coordination.harl.macro_environment import EaiMacroEnvironment, SymbolicMacroExecutor
from algorithm.system.coordination.harl.train_native_smoke import train
from algorithm.common_coordination.contract import ExecutionFeedback, FailureCode
from task.contracts import TaskRunRequest
from task.runner import TaskRunner

SOURCE = Path(os.environ.get("HETEROCOORD_HARL_SOURCE_ROOT", "/home/airs/.cache/heterocoord/upstream/harl"))


@pytest.fixture(scope="session")
def bundle(tmp_path_factory):
    # Real original training, never synthetic/random actor weights as success.
    return train(tmp_path_factory.mktemp("happo-native"), source_root=SOURCE, updates=16)


@pytest.fixture
def adapter(bundle):
    return NativeHAPPOAdapter(bundle, source_root=SOURCE)


@pytest.fixture
def package():
    return EaiMacroEnvironment(20021, split="test-holdout").package


def cloned(bundle, tmp_path):
    dest = tmp_path/"bundle"
    shutil.copytree(bundle.parent, dest)
    return dest/"manifest.json"


def rewrite(path, value):
    path.write_text(json.dumps(value))


def test_original_training_and_native_taskrunner_dispatch(bundle, adapter, package):
    receipt = json.loads((bundle.parent/"training_receipt.json").read_text())
    assert receipt["updates"] == 16
    assert all(receipt["original_runner_methods"].values())
    assert all(a["optimizer_steps"] > 0 and a["initial_hash"] != a["final_hash"] for a in receipt["actors"])
    executor = SymbolicMacroExecutor()
    result = TaskRunner(executor=executor).run(TaskRunRequest(package), adapter)
    actions = result.decision.provenance["actor_outputs"]
    assert executor.dispatched == [(CONTRACT["actors"][i]["robot_id"], CONTRACT["actors"][i]["task_id"])
                                   for i, out in enumerate(actions) if out["action"] == 1]
    assert executor.dispatched  # at least one actual dispatch from learned actor
    assert result.verdict["status"] == "N/A"
    assert result.decision.provenance["evidence"]["benchmark"] == "not_evaluable"


def test_exact_original_actor_outputs_and_recurrence(adapter, package):
    # Roster ordering must not change actor identity.
    robots = tuple(reversed(package.robots))
    obs, available = encode(package, robots)
    expected = []
    with torch.no_grad():
        for i, actor in enumerate(adapter.actors):
            action, state = actor.act(obs[i:i+1], adapter.rnn_states[i].copy(),
                                     np.zeros((1, 1), np.float32), available[i:i+1], deterministic=True)
            expected.append((int(action.item()), state.detach().numpy()))
    first = adapter(package, robots)
    for i, trace in enumerate(first.provenance["actor_outputs"]):
        assert trace["action"] == expected[i][0]
        np.testing.assert_array_equal(adapter.rnn_states[i], expected[i][1])
    # Failed execution lets the next call retry solely through the native actor.
    feedback = [ExecutionFeedback(a.task_id, a.robot_id, "macro_failed", FailureCode.EXECUTION_FAILURE)
                for a in first.assignments]
    adapter.update_feedback(feedback)
    obs, available = encode(package, robots, step=1)
    states = [s.copy() for s in adapter.rnn_states]
    with torch.no_grad():
        expected_next = [actor.act(obs[i:i+1], states[i], np.ones((1, 1), np.float32),
                                   available[i:i+1], deterministic=True)
                         for i, actor in enumerate(adapter.actors)]
    second = adapter(package, robots)
    for i, trace in enumerate(second.provenance["actor_outputs"]):
        assert trace["rnn_input_sha256"] == digest(states[i].tobytes())
        assert trace["reset_mask"] == 1
        assert trace["action"] == int(expected_next[i][0].item())
        np.testing.assert_array_equal(adapter.rnn_states[i], expected_next[i][1].numpy())
    adapter.reset()
    again = adapter(package, robots)
    assert again.provenance["actor_outputs"] == first.provenance["actor_outputs"]


def test_feedback_gate_and_completion_mask(adapter, package):
    first = adapter(package, package.robots)
    with pytest.raises(NativeHAPPOError, match="feedback"):
        adapter(package, package.robots)
    with pytest.raises(NativeHAPPOError, match="identity"):
        adapter.update_feedback([ExecutionFeedback("unknown", "air", "macro_completed")])
    adapter.update_feedback([ExecutionFeedback(a.task_id, a.robot_id, "macro_completed") for a in first.assignments])
    second = adapter(package, package.robots)
    for trace in second.provenance["actor_outputs"]:
        if CONTRACT["actors"][trace["actor_index"]]["task_id"] in adapter.completed:
            assert trace["action"] == 0 and trace["available_actions"] == [1, 0]
    adapter.update_feedback([ExecutionFeedback(a.task_id, a.robot_id, "macro_completed")
                             for a in second.assignments], terminal=True)
    with pytest.raises(NativeHAPPOError, match="terminal"):
        adapter(package, package.robots)


def test_missing_manifest_fails_closed(monkeypatch):
    monkeypatch.delenv("HETEROCOORD_HARL_HAPPO_MANIFEST", raising=False)
    with pytest.raises(NativeHAPPOError, match="manifest"):
        NativeHAPPOAdapter(source_root=SOURCE)


@pytest.mark.parametrize("change", ["schema", "roster", "capability", "target", "nan", "graph", "scene", "episode"])
def test_environment_mismatches_fail_before_actor(adapter, package, change):
    if change == "schema":
        package = replace(package, observation_schema="smac")
    elif change == "roster":
        package = replace(package, robots=package.robots[:1])
    elif change == "capability":
        r = package.robots[0]
        package = replace(package, robots=(replace(r, capability=replace(r.capability, capabilities=frozenset())), package.robots[1]))
    elif change in ("target", "nan"):
        t = package.graph.tasks[0]
        t = replace(t, metadata={} if change == "target" else {"target_xy": [float("nan"), 0]})
        package = replace(package, graph=replace(package.graph, tasks=(t, package.graph.tasks[1])))
    elif change == "graph":
        package = replace(package, graph=replace(package.graph, tasks=package.graph.tasks[:1]))
    elif change == "scene":
        package = replace(package, scene_id="Fire_Rescue")
    else:
        package = replace(package, metadata={"happo_contract": CONTRACT})
    with pytest.raises(NativeHAPPOError):
        adapter(package, package.robots)
    assert adapter.step == 0 and all(not s.any() for s in adapter.rnn_states)


@pytest.mark.parametrize("change", ["hash", "missing_actor", "actor_map", "partial", "nonfinite", "config", "receipt", "smac"])
def test_checkpoint_mismatches_fail_closed(bundle, tmp_path, change):
    path = cloned(bundle, tmp_path)
    m = json.loads(path.read_text())
    if change == "hash":
        (path.parent/m["actors"][0]["file"]).write_bytes(b"corrupt")
    elif change == "missing_actor":
        m["actors"].pop()
    elif change == "actor_map":
        m["actors"][0]["robot_id"] = "air"
    elif change in ("partial", "nonfinite"):
        actor_path = path.parent/m["actors"][0]["file"]
        state = torch.load(actor_path, weights_only=True)
        if change == "partial":
            state.pop(next(iter(state)))
        else:
            state[next(iter(state))].fill_(float("nan"))
        torch.save(state, actor_path)
        m["actors"][0]["sha256"] = digest(actor_path.read_bytes())
        receipt_path = path.parent/"training_receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["actors"][0]["checkpoint_sha256"] = m["actors"][0]["sha256"]
        receipt["actors"][0]["final_hash"] = digest(b"".join(k.encode()+v.numpy().tobytes() for k,v in sorted(state.items())))
        rewrite(receipt_path, receipt)
        m["training_receipt"]["sha256"] = digest(receipt_path.read_bytes())
    elif change == "config":
        m["actor_args"]["activation_func"] = "tanh"
    elif change == "receipt":
        (path.parent/"training_receipt.json").write_text("{}")
    else:
        m["contract"]["schema"] = "SMAC"
    rewrite(path, m)
    with pytest.raises(NativeHAPPOError):
        NativeHAPPOAdapter(path, source_root=SOURCE)


def test_episode_identity_requires_reset(adapter, package):
    first = adapter(package, package.robots)
    adapter.update_feedback([ExecutionFeedback(a.task_id, a.robot_id, "macro_failed", FailureCode.EXECUTION_FAILURE)
                             for a in first.assignments])
    with pytest.raises(NativeHAPPOError, match="identity"):
        adapter(replace(package, task_id="other"), package.robots)


def test_inactive_actor_wait_mask(adapter, package):
    robots = tuple(replace(r, capability=replace(r.capability, active=False)) for r in package.robots)
    package = replace(package, robots=robots)
    decision = adapter(package, robots)
    assert not decision.assignments
    assert all(t["action"] == 0 for t in decision.provenance["actor_outputs"])


def test_environment_action_validation():
    env = EaiMacroEnvironment()
    for bad in ([[2], [0]], [[0.5], [1]], [1, 0]):
        with pytest.raises(ValueError):
            env.step(bad)


def test_train_holdout_overlap_rejected(tmp_path):
    with pytest.raises(ValueError, match="disjoint"):
        train(tmp_path, source_root=SOURCE, updates=1, seed=10011)


def test_parent_wrapper_flat_kwargs(bundle, package):
    from benchmark.native_algorithms import build_native_adapter
    kwargs = {"manifest_path": str(bundle), "device": "cpu"}
    wrapped = build_native_adapter("harl_happo", source_root=SOURCE, **kwargs)
    direct = NativeHAPPOAdapter(**kwargs, source_root=SOURCE)
    expected = direct(package, package.robots)
    actual = wrapped(package, package.robots)
    assert actual.assignments == expected.assignments
    assert actual.provenance["actor_outputs"] == expected.provenance["actor_outputs"]
    assert actual.provenance["source_integrity"]["root"] == str(SOURCE.resolve())
    wrapped.update_feedback([ExecutionFeedback(a.task_id, a.robot_id, "macro_completed") for a in actual.assignments])
    assert wrapped.completed == {a.task_id for a in actual.assignments}


def test_parent_cli_flat_config(bundle, package, tmp_path):
    import subprocess
    import sys
    from dataclasses import asdict
    task_json = {"task_id": package.task_id, "scene_id": package.scene_id,
                 "observation_schema": package.observation_schema, "metadata": package.metadata,
                 "tasks": [{**asdict(t), "required_capabilities": sorted(t.required_capabilities)} for t in package.graph.tasks],
                 "robots": [{"robot_id": r.capability.robot_id, "capabilities": sorted(r.capability.capabilities),
                             "active": r.capability.active, "position": r.position, "metadata": r.metadata}
                            for r in package.robots]}
    rewrite(tmp_path/"task.json", task_json)
    rewrite(tmp_path/"kwargs.json", {"manifest_path": str(bundle), "device": "cpu"})
    completed = subprocess.run([sys.executable, "-m", "tools.algorithm_adapters.run", "--algorithm", "harl_happo",
                                "--source-root", str(SOURCE), "--task", str(tmp_path/"task.json"),
                                "--config", str(tmp_path/"kwargs.json"), "--report", str(tmp_path/"report.json")],
                               capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr
    report = json.loads((tmp_path/"report.json").read_text())
    assert report["status"] == "native_decision_returned"
    assert report["result"]["assignments"]
    assert report["provenance"]["paper_algorithm"] == "harl_happo"
    assert report["result"]["verdict"]["status"] == "N/A"


def make_bound_bundle(bundle, target):
    """Version the *same* trained semantics as v2; no weight/action relabeling."""
    from algorithm.system.coordination.harl.generic_manifest import contract_digest
    target.mkdir(parents=True, exist_ok=True)
    old = json.loads(bundle.read_text())
    c = {"schema": "heterocoord.happo.bound_contract.v1", "environment_id": CONTRACT["schema"],
         "observation_schema": CONTRACT["schema"], "scene_ids": [CONTRACT["scene_id"]],
         "horizon": CONTRACT["horizon"], "feedback": {"completed": "macro_completed", "failed": ["macro_failed"]},
         "tasks": [{"task_id": a["task_id"], "required_capabilities": a["capabilities"],
                    "prerequisites": [], "target_ref": None} for a in CONTRACT["actors"]],
         "actors": []}
    for a in CONTRACT["actors"]:
        c["actors"].append({"actor_index": a["actor_index"], "robot_id": a["robot_id"], "capabilities": a["capabilities"],
                            "observation": {"dtype": "float32", "shape": [9],
                            "preprocessing": "native_adapter.encode two_actor_macro.v1, unchanged ordering and unit-square coordinates",
                            "features": [{"name": f, "meaning": f"two_actor_macro.v1:{f}", "unit": "dimensionless",
                                          "low": 0.0, "high": 1.0} for f in CONTRACT["observation_features"]]},
                            "actions": [{"kind": "wait"}, {"kind": "assign", "task_id": a["task_id"]}]})
    actors = []
    for old_actor in old["actors"]:
        shutil.copy2(bundle.parent/old_actor["file"], target/old_actor["file"])
        actors.append({**old_actor, "actor_args": deepcopy(old["actor_args"])})
    shutil.copy2(bundle.parent/"training_receipt.json", target/"training_receipt.json")
    manifest = {"schema": "heterocoord.happo.checkpoint.v2", "source_commit": old["source_commit"],
                "algorithm": "HAPPO", "condition": "HARL-HAPPO-v2-local-compatibility-test",
                "contract": c, "actors": actors}
    provenance = {"schema": "heterocoord.happo.checkpoint_provenance.v1", "algorithm": "HAPPO",
                  "source_commit": old["source_commit"], "condition": manifest["condition"],
                  "contract_sha256": contract_digest(c),
                  "actors": [{k: a[k] for k in ("actor_index", "robot_id", "sha256", "actor_args")} for a in actors],
                  "origin": {"kind": "locally_trained", "reference": "original HARL local training receipt",
                             "training_environment": CONTRACT["schema"]},
                  "training_evidence": {"file": "training_receipt.json", "sha256": digest((target/"training_receipt.json").read_bytes())},
                  "compatibility_statement": "Exact same actor order, nine features, masks and wait/inspect actions as v1; no environment transfer."}
    rewrite(target/"provenance.json", provenance)
    manifest["checkpoint_provenance"] = {"file": "provenance.json", "sha256": digest((target/"provenance.json").read_bytes())}
    rewrite(target/"manifest.json", manifest)
    return target/"manifest.json"


def bind_snapshot(package, adapter):
    obs, masks = encode(package, package.robots, completed=adapter.completed,
                        last_completed=adapter.last_completed, step=adapter.step)
    robots = tuple(replace(r, metadata={"happo_observation": {
        "contract_sha256": adapter.boundary.contract_hash, "episode_id": package.metadata["episode_id"],
        "step": adapter.step, "completed_task_ids": sorted(adapter.completed),
        "last_completed_task_ids": sorted(adapter.last_completed), "values": obs[i].tolist(),
        "available_actions": masks[i].tolist(),
    }}) for i, r in enumerate(package.robots))
    return replace(package, robots=robots, metadata={**package.metadata, "happo_contract_sha256": adapter.boundary.contract_hash})


@pytest.fixture
def bound_bundle(bundle, tmp_path):
    return make_bound_bundle(bundle, tmp_path/"bound")


def test_v2_native_equivalence_and_recurrent_feedback(bound_bundle, bundle, package):
    from benchmark.native_algorithms import build_native_adapter
    generic = build_native_adapter("harl_happo", source_root=SOURCE, manifest_path=bound_bundle)
    smoke = NativeHAPPOAdapter(bundle, source_root=SOURCE)
    for _ in range(2):
        snapshot = bind_snapshot(package, generic)
        decision = generic(snapshot, snapshot.robots)
        expected = smoke(package, package.robots)
        assert [(a.task_id, a.robot_id) for a in decision.assignments] == [(a.task_id, a.robot_id) for a in expected.assignments]
        for actual_trace, expected_trace in zip(decision.provenance["actor_outputs"], expected.provenance["actor_outputs"]):
            assert {k: actual_trace[k] for k in expected_trace} == expected_trace
        feedback = [ExecutionFeedback(a.task_id, a.robot_id, "macro_failed", FailureCode.EXECUTION_FAILURE)
                    for a in decision.assignments]
        generic.update_feedback(feedback, decision_token=decision.provenance["feedback_token"])
        smoke.update_feedback(feedback)
    assert decision.condition.algorithm_id == "HARL-HAPPO-v2-local-compatibility-test"
    assert decision.provenance["checkpoint_origin"]["kind"] == "locally_trained"
    assert decision.provenance["evidence"]["benchmark"] == "not_evaluable"


def test_v2_external_origin_is_accepted_but_not_certified(bound_bundle, package):
    m = json.loads(bound_bundle.read_text())
    p = bound_bundle.parent/"provenance.json"
    provenance = json.loads(p.read_text())
    provenance["origin"] = {"kind": "external_checkpoint", "reference": "TEST ONLY: same known local-trained weights, not an official checkpoint",
                            "training_environment": CONTRACT["schema"]}
    rewrite(p, provenance)
    m["checkpoint_provenance"]["sha256"] = digest(p.read_bytes())
    rewrite(bound_bundle, m)
    a = NativeHAPPOAdapter(bound_bundle, source_root=SOURCE)
    package = bind_snapshot(package, a)
    d = a(package, package.robots)
    assert d.provenance["checkpoint_origin"]["kind"] == "external_checkpoint"
    assert "not_independently_verified" in d.provenance["checkpoint_origin_verification"]
    assert d.provenance["evidence"]["physical"] == "N/A"


@pytest.mark.parametrize("change", ["dimension", "stale", "empty_mask", "nonbinary", "contract", "bounds", "missing_feature"])
def test_v2_rejects_incompatible_host_snapshots(bound_bundle, package, change):
    a = NativeHAPPOAdapter(bound_bundle, source_root=SOURCE)
    package = bind_snapshot(package, a)
    snapshot = deepcopy(package.robots[0].metadata["happo_observation"])
    if change == "dimension":
        snapshot["values"].append(0)
    elif change == "stale":
        snapshot["step"] = 1
    elif change == "empty_mask":
        snapshot["available_actions"] = [0, 0]
    elif change == "nonbinary":
        snapshot["available_actions"] = [1, .5]
    elif change == "contract":
        snapshot["contract_sha256"] = "wrong"
    elif change == "bounds":
        snapshot["values"][0] = 2
    else:
        snapshot.pop("values")
    robots = (replace(package.robots[0], metadata={"happo_observation": snapshot}), package.robots[1])
    with pytest.raises(NativeHAPPOError):
        a(replace(package, robots=robots), robots)
    assert a.step == 0


def test_v2_manifest_must_bind_configuration_and_contract(bound_bundle):
    m = json.loads(bound_bundle.read_text())
    m["actors"][0]["actor_args"]["activation_func"] = "tanh"
    rewrite(bound_bundle, m)
    with pytest.raises(NativeHAPPOError, match="provenance"):
        NativeHAPPOAdapter(bound_bundle, source_root=SOURCE)


@pytest.mark.parametrize("change", ["bytes", "contract", "continuous", "missing_actor"])
def test_v2_rejects_checkpoint_and_action_contract_mismatches(bound_bundle, change):
    m = json.loads(bound_bundle.read_text())
    if change == "bytes":
        (bound_bundle.parent/m["actors"][0]["file"]).write_bytes(b"corrupt")
    elif change == "contract":
        m["contract"]["actors"][0]["observation"]["features"][0]["meaning"] = "different semantics"
    elif change == "continuous":
        m["contract"]["actors"][0]["actions"][1] = {"kind": "velocity", "axis": "x"}
    else:
        m["actors"].pop()
    rewrite(bound_bundle, m)
    with pytest.raises(NativeHAPPOError):
        NativeHAPPOAdapter(bound_bundle, source_root=SOURCE)


def test_v2_feedback_requires_fresh_host_mask_without_rewriting(bound_bundle, package):
    a = NativeHAPPOAdapter(bound_bundle, source_root=SOURCE)
    initial = bind_snapshot(package, a)
    first = a(initial, initial.robots)
    assert first.assignments
    a.update_feedback([ExecutionFeedback(x.task_id, x.robot_id, "macro_completed") for x in first.assignments],
                      decision_token=first.provenance["feedback_token"])
    with pytest.raises(NativeHAPPOError, match="snapshot"):
        a(initial, initial.robots)
    fresh = bind_snapshot(package, a)
    i = next(i for i,s in enumerate(a.boundary.specs) if s["actions"][1]["task_id"] in a.completed)
    corrupted = deepcopy(fresh.robots[i].metadata["happo_observation"])
    corrupted["available_actions"][1] = 1
    robots = list(fresh.robots)
    robots[i] = replace(robots[i], metadata={"happo_observation": corrupted})
    with pytest.raises(NativeHAPPOError, match="refusing to rewrite mask"):
        a(replace(fresh, robots=tuple(robots)), robots)
    decision = a(fresh, fresh.robots)
    assert not ({x.task_id for x in decision.assignments} & a.completed)


def test_v2_coalition_error_preserves_complete_raw_outputs(bound_bundle):
    from algorithm.system.coordination.harl.generic_manifest import BoundContract
    from algorithm.system.coordination.harl.native_adapter import NativeHAPPOScopeError
    m = json.loads(bound_bundle.read_text())
    boundary = BoundContract(m, lambda entry: (bound_bundle.parent/entry["file"]).read_bytes())
    # Decoder scope-only negative test; never presented as a trained coalition.
    boundary.specs[1]["actions"][1]["task_id"] = boundary.specs[0]["actions"][1]["task_id"]
    raw = [{"actor_index": i, "robot_id": s["robot_id"], "action": 1} for i, s in enumerate(boundary.specs)]
    with pytest.raises(NativeHAPPOScopeError) as exc:
        boundary.decode(raw, "scope-test")
    assert len(exc.value.actor_outputs) == 2
    assert all(t["action"] == 1 and t["action_binding"]["task_id"] == "inspect_ground" for t in exc.value.actor_outputs)
