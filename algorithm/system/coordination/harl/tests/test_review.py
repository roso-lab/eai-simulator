"""Review regressions, not additional algorithm/environment acceptance."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import sys

import pytest

from algorithm.common_coordination.contract import ExecutionFeedback, FailureCode
from algorithm.system.coordination.harl.native_adapter import NativeHAPPOAdapter, NativeHAPPOError, NativeHAPPOScopeError
from algorithm.system.coordination.harl.generic_manifest import contract_digest
from algorithm.system.coordination.harl.tests.test_native_adapter import (
    SOURCE, adapter, bind_snapshot, bound_bundle, bundle, package, rewrite,
)


def update_provenance(path, manifest, mutate=lambda p: None):
    provenance_path = path.parent / "provenance.json"
    provenance = json.loads(provenance_path.read_text())
    provenance["contract_sha256"] = contract_digest(manifest["contract"])
    mutate(provenance)
    rewrite(provenance_path, provenance)
    manifest["checkpoint_provenance"]["sha256"] = hashlib.sha256(provenance_path.read_bytes()).hexdigest()
    rewrite(path, manifest)


@pytest.mark.parametrize("terminal", ["false", "true", 0, 1, None])
def test_terminal_requires_boolean_and_preserves_pending(adapter, package, terminal):
    first = adapter(package, package.robots)
    pending = dict(adapter.pending)
    feedback = [ExecutionFeedback(a.task_id, a.robot_id, "macro_completed") for a in first.assignments]
    with pytest.raises(NativeHAPPOError, match="boolean"):
        adapter.update_feedback(feedback, terminal=terminal)
    assert adapter.pending == pending and adapter.awaiting_feedback
    assert not adapter.terminal and not adapter.completed


def test_failed_feedback_requires_failure_code_and_is_atomic(adapter, package):
    first = adapter(package, package.robots)
    assert first.assignments
    feedback = [ExecutionFeedback(a.task_id, a.robot_id, "macro_failed") for a in first.assignments]
    with pytest.raises(NativeHAPPOError, match="failure_code"):
        adapter.update_feedback(feedback)
    assert adapter.awaiting_feedback and adapter.pending and not adapter.completed
    adapter.update_feedback([replace(f, failure_code=FailureCode.EXECUTION_FAILURE) for f in feedback])
    assert not adapter.awaiting_feedback and not adapter.pending


@pytest.mark.parametrize("field,value", [
    ("available_actions", [1, 1-1e-9]),
    ("available_actions", [1, 1e-50]),
    ("values", [1+1e-9, 0, 0, .2, .2, .2, .2, 0, 0]),
])
def test_float32_rounding_cannot_bypass_validation(bound_bundle, package, field, value):
    a = NativeHAPPOAdapter(bound_bundle, source_root=SOURCE)
    task = bind_snapshot(package, a)
    snapshot = deepcopy(task.robots[0].metadata["happo_observation"])
    snapshot[field] = value
    robots = (replace(task.robots[0], metadata={"happo_observation": snapshot}), task.robots[1])
    with pytest.raises(NativeHAPPOError):
        a(replace(task, robots=robots), robots)
    assert a.step == 0 and not a.awaiting_feedback


def test_cycle_rejected_even_with_matching_provenance(bound_bundle):
    m = json.loads(bound_bundle.read_text())
    tasks = m["contract"]["tasks"]
    tasks[0]["prerequisites"] = [tasks[1]["task_id"]]
    tasks[1]["prerequisites"] = [tasks[0]["task_id"]]
    update_provenance(bound_bundle, m)
    with pytest.raises(NativeHAPPOError, match="cycle"):
        NativeHAPPOAdapter(bound_bundle, source_root=SOURCE)


@pytest.mark.parametrize("change", ["empty", "zero_updates", "different_weight", "zero_optimizer_steps"])
def test_local_training_receipt_checked_not_just_hashed(bound_bundle, change):
    path = bound_bundle.parent/"training_receipt.json"
    receipt = json.loads(path.read_text())
    if change == "empty":
        receipt = {}
    elif change == "zero_updates":
        receipt["updates"] = 0
    elif change == "different_weight":
        receipt["actors"][0]["checkpoint_sha256"] = "0" * 64
    else:
        receipt["actors"][0]["optimizer_steps"] = 0
    rewrite(path, receipt)
    m = json.loads(bound_bundle.read_text())
    update_provenance(bound_bundle, m, lambda p: p["training_evidence"].update(
        sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    with pytest.raises(NativeHAPPOError, match="training receipt"):
        NativeHAPPOAdapter(bound_bundle, source_root=SOURCE)


def test_episode_identity_includes_scene(bound_bundle, package):
    m = json.loads(bound_bundle.read_text())
    m["contract"]["scene_ids"].append("second-declared-scene")
    update_provenance(bound_bundle, m)
    a = NativeHAPPOAdapter(bound_bundle, source_root=SOURCE)
    first = bind_snapshot(package, a)
    d = a(first, first.robots)
    a.update_feedback([ExecutionFeedback(x.task_id, x.robot_id, "macro_failed", FailureCode.EXECUTION_FAILURE)
                       for x in d.assignments], decision_token=d.provenance["feedback_token"])
    second = replace(bind_snapshot(package, a), scene_id="second-declared-scene")
    with pytest.raises(NativeHAPPOError, match="identity changed"):
        a(second, second.robots)
    assert a.step == 1


def test_package_live_activity_mismatch_rejected_before_state_commit(bound_bundle, package):
    a = NativeHAPPOAdapter(bound_bundle, source_root=SOURCE)
    task = bind_snapshot(package, a)
    disabled = replace(task.robots[0], capability=replace(task.robots[0].capability, active=False))
    with pytest.raises(NativeHAPPOError, match="disagree"):
        a(replace(task, robots=(disabled, task.robots[1])), task.robots)
    assert a.step == 0


def test_mutated_loaded_binding_cannot_reuse_old_hash(bound_bundle, package):
    a = NativeHAPPOAdapter(bound_bundle, source_root=SOURCE)
    task = bind_snapshot(package, a)
    a.boundary.specs[0]["actions"].reverse()
    with pytest.raises(NativeHAPPOError, match="manifest mutated"):
        a(task, task.robots)
    assert a.step == 0


def test_result_provenance_does_not_alias_live_origin_or_hashes(bound_bundle, package):
    a = NativeHAPPOAdapter(bound_bundle, source_root=SOURCE)
    task = bind_snapshot(package, a)
    d = a(task, task.robots)
    hashes = list(a.checkpoint_hashes)
    origin = deepcopy(a.boundary.origin)
    d.provenance["checkpoint_sha256"].clear()
    d.provenance["checkpoint_origin"]["kind"] = "changed"
    assert a.checkpoint_hashes == hashes and a.boundary.origin == origin


def test_v2_rejects_delayed_feedback_across_reset_and_retry(bound_bundle, package):
    a = NativeHAPPOAdapter(bound_bundle, source_root=SOURCE)
    old_task = bind_snapshot(package, a)
    old = a(old_task, old_task.robots)
    old_feedback = [ExecutionFeedback(x.task_id, x.robot_id, "macro_completed") for x in old.assignments]
    assert old_feedback
    a.reset()
    new_task = bind_snapshot(package, a)
    new = a(new_task, new_task.robots)
    assert [(x.task_id, x.robot_id) for x in old.assignments] == [(x.task_id, x.robot_id) for x in new.assignments]
    for token in (None, old.provenance["feedback_token"]):
        with pytest.raises(NativeHAPPOError, match="decision_token"):
            a.update_feedback(old_feedback, decision_token=token)
        assert a.awaiting_feedback and not a.completed
    a.update_feedback([replace(f, outcome="macro_failed", failure_code=FailureCode.EXECUTION_FAILURE)
                       for f in old_feedback], decision_token=new.provenance["feedback_token"])
    retry_task = bind_snapshot(package, a)
    retry = a(retry_task, retry_task.robots)
    with pytest.raises(NativeHAPPOError, match="decision_token"):
        a.update_feedback(old_feedback, decision_token=new.provenance["feedback_token"])
    a.update_feedback([ExecutionFeedback(x.task_id, x.robot_id, "macro_completed") for x in retry.assignments],
                      decision_token=retry.provenance["feedback_token"])
    assert a.completed == {x.task_id for x in retry.assignments}


def test_cli_keeps_scope_error_raw_outputs_without_editing_shared_cli(monkeypatch, tmp_path, package):
    # Pure protocol negative fixture, never native acceptance. Invoke actual
    # CLI failure serialization while replacing only its external input seam.
    from tools.algorithm_adapters import run
    output = [{"actor_index": 0, "action": 1}, {"actor_index": 1, "action": 1}]
    class RejectingProvider:
        def __call__(self, *_):
            raise NativeHAPPOScopeError("coalition cannot map", output)
    monkeypatch.setattr(run, "build_native_adapter", lambda *a, **k: RejectingProvider())
    monkeypatch.setattr(run.EaiTaskPackage, "from_json", lambda _: package)
    (tmp_path / "task.json").write_text("{}")
    report = tmp_path/"report.json"
    monkeypatch.setattr(sys, "argv", ["run", "--algorithm", "harl_happo", "--task", str(tmp_path/"task.json"),
                                    "--report", str(report)])
    with pytest.raises(SystemExit) as exc:
        run.main()
    assert exc.value.code == 1
    data = json.loads(report.read_text())
    assert data["status"] == "not_passed"
    assert data["rejected_native_result"]["actor_outputs"] == output
    assert not data["simulation_acceptance_passed"] and data["physical_success"] is None
