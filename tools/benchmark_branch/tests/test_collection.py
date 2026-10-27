import json
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from tools.benchmark_branch import BranchExecutor, BranchPlan, BranchRunStore, DummyAdapter
from tools.benchmark_branch.validation import summarize_run, validate_run


class MeasuredTerminalAdapter(DummyAdapter):
    """Spawn-safe explicit evaluator fixture for isolated worker tests."""

    supported_execution_scopes = {"measured", "measured_diagnostic"}
    evaluator_version = "isolated-terminal-fixture.v1"

    def finish_episode(self, runtime, job):
        return {
            "status": "valid_terminal",
            "completed": True,
            "physical_success": True,
            "terminal_predicate": True,
            "safety_predicate": True,
            "feasibility_predicate": True,
            # Evidence identity belongs to the writer, not this method row.
            "evaluator_version": "caller-must-not-own-version",
            "evidence_refs": [f"dummy://{job.branch_id}/forged"],
            "score": 1.0,
        }

    def collect_receipt_material(self, runtime, job):
        return {
            "episode": {
                "status": "valid_terminal",
                "condition_id": job.condition_id,
                # The executor does not derive this from branch_type; the
                # evaluator supplies a value accepted by receipt_producer_v1.
                "branch": "fact",
                "repeat_id": job.repeat_id,
                "checkpoint_id": "diagnostic-checkpoint",
                "evaluator_version": self.evaluator_version,
            },
            "streams": {
                "zone_intervals": [],
                "hold_intervals": [],
                "milestones": [{"label": "terminal", "tick": runtime["step"]}],
                "task_events": [],
                "identity_records": {"fixture": "isolated-worker"},
                "integrity_confirmed": True,
            },
            "facts": {
                "d1": {"fixture": True},
                "success": True,
                "obligations": [],
                "d4_items": [],
                "d6": {"success": True, "bits": 1, "time": 1.0},
            },
        }


class TamperedTerminalIdentityAdapter(MeasuredTerminalAdapter):
    """Returns valid-shaped material bound to the wrong branch identity."""

    def collect_receipt_material(self, runtime, job):
        material = super().collect_receipt_material(runtime, job)
        material["episode"]["condition_id"] = "foreign-condition"
        return material


class MissingTerminalHookAdapter(DummyAdapter):
    """Claims success at method level but has no evaluator material hook."""

    supported_execution_scopes = {"measured_diagnostic"}

    def finish_episode(self, runtime, job):
        return {
            "status": "valid_terminal",
            "completed": True,
            "physical_success": True,
            "terminal_predicate": True,
            "safety_predicate": True,
            "feasibility_predicate": True,
            "evaluator_version": "forged-method-version",
            "evidence_refs": [f"dummy://{job.branch_id}/forged"],
            "score": 999.0,
        }


class UnserializableMissingTerminalHookAdapter(MissingTerminalHookAdapter):
    """Adds an opaque value that must never be copied into an episode row."""

    def finish_episode(self, runtime, job):
        result = super().finish_episode(runtime, job)
        result["opaque"] = object()
        return result


def _run(tmp_path: Path, *, slots=2, workers=8, adapter=None):
    plan = BranchPlan(scene_key="test", task_id="task", system_id="system", slots=slots)
    store = BranchRunStore.create(tmp_path, plan, requested_max_workers=workers,
                                  execution_scope="protocol_synthetic", adapter="dummy")
    result = BranchExecutor(plan, adapter or DummyAdapter(steps=2, step_delay_s=0.005), store,
                            max_workers=workers).run()
    return result, tmp_path


def test_dummy_run_has_ledgers_and_valid_pairing(tmp_path):
    result, root = _run(tmp_path)
    assert result["failed"] == 0
    assert result["observed_peak_branch_workers"] == 8
    assert result["resident"] == 8
    validation = validate_run(root)
    assert validation["status"] == "passed"
    assert validation["metrics"]["logical_branches"] == 8
    assert len(validation["metrics"]["pairing_eligible_groups"]) == 2
    rows = [json.loads(line) for line in (root / "metrics.jsonl").read_text().splitlines() if line]
    started = [row for row in rows if row.get("kind") == "worker_started"]
    assert len({row["worker_session_id"] for row in started}) == 8
    assert all(row.get("heartbeat_at_ns") or row.get("started_at_ns") for row in rows)
    assert len(list((root / "branches").iterdir())) == 8
    summary = summarize_run(root)
    assert summary["validation_status"] == "passed"
    episodes = [json.loads(line) for line in (root / "episodes.jsonl").read_text().splitlines()]
    assert all("evidence_refs" not in row for row in episodes)
    assert all(row["outcome"]["evidence_refs"][0].startswith("dummy://") for row in episodes)


def test_isolated_measured_worker_seals_explicit_terminal_material(tmp_path):
    plan = BranchPlan(
        scene_key="test", task_id="task", system_id="system", slots=1,
        arms=("fact",), execution_scope="measured_diagnostic",
    )
    store = BranchRunStore.create(
        tmp_path, plan, requested_max_workers=1,
        execution_scope="measured_diagnostic", adapter="measured-terminal-fixture",
    )
    adapter = MeasuredTerminalAdapter(
        steps=1, evaluator_version="isolated-terminal-fixture.v1",
    )
    result = BranchExecutor(
        store.plan, adapter, store,
        max_workers=1, start_method="spawn",
    ).run()

    # Worker completion describes the method process; scientific evaluability
    # is carried independently by the terminal episode/evidence envelope.
    assert result["completed"] == 1
    assert result["failed"] == 0
    episode = json.loads((tmp_path / "episodes.jsonl").read_text().strip())
    assert episode["status"] == "valid_terminal"
    assert episode["completed"] is True
    assert episode["physical_success"] is True
    assert episode["evaluator_version"] == adapter.evaluator_version
    assert episode["evaluator_verdict"]["evidence_hash"] == episode["evidence_hash"]
    assert episode["method_diagnostic"] == {
        "result_type": "dict", "status": "valid_terminal", "completed": True,
    }
    assert "score" not in episode["outcome"]
    assert not episode["evidence_refs"][0].startswith("dummy://")
    evidence_path = tmp_path / episode["evidence_refs"][0]
    assert evidence_path.is_file()
    assert evidence_path.parent == tmp_path / "evidence"
    artifact = json.loads(evidence_path.read_text())
    material_episode = artifact["observations"]["receipt_material"]["episode"]
    assert material_episode["branch"] == "fact"
    assert material_episode["checkpoint_id"] == "diagnostic-checkpoint"
    assert material_episode["system_id"] == store.plan.system_id
    assert material_episode["base_instance_id"] == store.plan.jobs()[0].base_instance_id
    assert validate_run(tmp_path)["status"] == "passed"


def test_isolated_measured_worker_missing_hook_is_not_evaluable(tmp_path):
    plan = BranchPlan(
        scene_key="test", task_id="task", system_id="system", slots=1,
        arms=("fact",), execution_scope="measured_diagnostic",
    )
    store = BranchRunStore.create(
        tmp_path, plan, requested_max_workers=1,
        execution_scope="measured_diagnostic", adapter="missing-terminal-hook",
    )
    result = BranchExecutor(
        store.plan, MissingTerminalHookAdapter(steps=1), store,
        max_workers=1, start_method="spawn",
    ).run()

    assert result["completed"] == 1
    assert result["failed"] == 0
    episode = json.loads((tmp_path / "episodes.jsonl").read_text().strip())
    assert episode["status"] == "not_evaluable"
    assert episode["completed"] is False
    assert episode["physical_success"] is None
    assert "collect_receipt_material" in episode["evaluator_error"]
    assert "evidence_refs" not in episode
    assert not (tmp_path / "evidence").exists()
    report = validate_run(tmp_path)
    assert report["status"] == "passed"
    assert report["metrics"]["runtime_failed"] == 1


def test_isolated_measured_worker_rejects_tampered_material_identity(tmp_path):
    plan = BranchPlan(
        scene_key="test", task_id="task", system_id="system", slots=1,
        arms=("fact",), execution_scope="measured_diagnostic",
    )
    store = BranchRunStore.create(
        tmp_path, plan, requested_max_workers=1,
        execution_scope="measured_diagnostic", adapter="tampered-terminal-identity",
    )
    adapter = TamperedTerminalIdentityAdapter(
        steps=1, evaluator_version="isolated-terminal-fixture.v1",
    )
    result = BranchExecutor(
        store.plan, adapter, store, max_workers=1, start_method="spawn",
    ).run()

    assert result["completed"] == 1
    episode = json.loads((tmp_path / "episodes.jsonl").read_text().strip())
    assert episode["status"] == "not_evaluable"
    assert episode["completed"] is False
    assert episode["physical_success"] is None
    assert "condition_id disagrees" in episode["evaluator_error"]
    assert "evidence_refs" not in episode
    assert not (tmp_path / "evidence").exists()
    assert validate_run(tmp_path)["status"] == "passed"


def test_isolated_measured_worker_sanitizes_unserializable_failure(tmp_path):
    plan = BranchPlan(
        scene_key="test", task_id="task", system_id="system", slots=1,
        arms=("fact",), execution_scope="measured_diagnostic",
    )
    store = BranchRunStore.create(
        tmp_path, plan, requested_max_workers=1,
        execution_scope="measured_diagnostic", adapter="opaque-missing-hook",
    )
    result = BranchExecutor(
        store.plan, UnserializableMissingTerminalHookAdapter(steps=1), store,
        max_workers=1, start_method="spawn",
    ).run()

    assert result["completed"] == 1
    assert result["failed"] == 0
    episode = json.loads((tmp_path / "episodes.jsonl").read_text().strip())
    assert episode["status"] == "not_evaluable"
    assert episode["completed"] is False
    assert episode["physical_success"] is None
    assert "collect_receipt_material" in episode["evaluator_error"]
    assert episode["method_diagnostic"] == {
        "result_type": "dict", "status": "valid_terminal", "completed": True,
    }
    assert "opaque" not in episode["outcome"]
    assert not (tmp_path / "evidence").exists()
    assert validate_run(tmp_path)["status"] == "passed"


def test_formal_measured_still_requires_session_backend_and_scheduler(tmp_path):
    plan = BranchPlan(slots=1, execution_scope="measured")
    store = BranchRunStore.create(tmp_path, plan, execution_scope="measured")
    with pytest.raises(ValueError, match="formal measured execution requires"):
        BranchExecutor(plan, MeasuredTerminalAdapter(steps=1), store).run()


def test_measured_diagnostic_rejects_half_configured_session(tmp_path):
    plan = BranchPlan(
        slots=1, arms=("fact",), execution_scope="measured_diagnostic",
    )
    store = BranchRunStore.create(
        tmp_path, plan, execution_scope="measured_diagnostic",
    )
    with pytest.raises(ValueError, match="supplied together"):
        BranchExecutor(
            plan, MeasuredTerminalAdapter(steps=1), store,
            session_backend=object(), scheduler=None,
        ).run()


def test_thirty_two_branch_workers_overlap_and_keep_distinct_sessions(tmp_path):
    # Eight pair slots × four arms gives exactly 32 logical branches.  This
    # is the minimum concurrency gate independent of the 128-branch formal
    # batch used by the larger smoke runs.
    result, root = _run(tmp_path, slots=8, workers=32,
                        adapter=DummyAdapter(steps=3, step_delay_s=0.01))
    assert result["requested"] == 32
    assert result["resident"] == 32
    assert result["observed_peak_branch_workers"] == 32
    assert result["active"] == 0
    validation = validate_run(root)
    assert validation["status"] == "passed"
    assert validation["metrics"]["overlap_detected"] is True
    assert validation["metrics"]["independent_branch_outputs"] == 32
    started = [json.loads(line) for line in (root / "metrics.jsonl").read_text().splitlines()
               if line and '"kind":"worker_started"' in line]
    assert len(started) == 32
    assert len({row["worker_session_id"] for row in started}) == 32


def test_failed_worker_does_not_drop_other_branches_and_resume_retries(tmp_path):
    result, root = _run(tmp_path, slots=3, workers=6, adapter=DummyAdapter(steps=2, fail_slots=(1,), step_delay_s=0.002))
    assert result["completed"] == 8
    assert result["failed"] == 4
    before = (root / "branch_manifest.jsonl").read_text().count("\n")
    store = BranchRunStore.resume(root)
    retry_result = BranchExecutor(store.plan, DummyAdapter(steps=1, step_delay_s=0.001), store, max_workers=6).run()
    assert retry_result["failed"] == 0
    after = (root / "branch_manifest.jsonl").read_text().count("\n")
    assert after > before
    assert any("#attempt-1" in line for line in (root / "branch_manifest.jsonl").read_text().splitlines())


def test_restore_mismatch_excludes_pair_but_retains_fact(tmp_path):
    result, root = _run(tmp_path, slots=1, workers=4, adapter=DummyAdapter(steps=1, restore_mismatch_slots=(0,)))
    assert result["failed"] == 4
    validation = validate_run(root)
    assert validation["status"] == "passed"
    assert validation["metrics"]["pairing_excluded_groups"]
    episodes = (root / "episodes.jsonl").read_text()
    assert '"branch_type":"fact"' in episodes


def test_validate_detects_checksum_corruption(tmp_path):
    _run(tmp_path)
    path = tmp_path / "steps.jsonl"
    path.write_text(path.read_text() + "{\"corrupt\":true}\n")
    result = validate_run(tmp_path, write=False)
    assert result["status"] == "failed"
    assert any("checksum mismatch" in error for error in result["errors"])


def test_validate_detects_duplicate_branch_and_event_gap(tmp_path):
    _run(tmp_path, slots=1, workers=4)
    branch_path = tmp_path / "branch_manifest.jsonl"
    first = branch_path.read_text().splitlines()[0]
    branch_path.write_text(branch_path.read_text() + first + "\n")
    event_path = tmp_path / "events.jsonl"
    row = json.loads(event_path.read_text().splitlines()[0])
    row["event_seq"] = 99
    event_path.write_text(event_path.read_text() + json.dumps(row) + "\n")
    result = validate_run(tmp_path, write=False)
    assert result["status"] == "failed"
    assert any("duplicate branch_id" in error for error in result["errors"])
    assert any("event_seq" in error for error in result["errors"])


def test_validate_detects_missing_ledger(tmp_path):
    _run(tmp_path, slots=1, workers=4)
    (tmp_path / "network.jsonl").unlink()
    result = validate_run(tmp_path, write=False)
    assert result["status"] == "failed"
    assert any("missing ledger: network.jsonl" in error for error in result["errors"])


def test_duplicate_manifest_is_rejected_before_append(tmp_path):
    plan = BranchPlan(scene_key="test", task_id="task", system_id="system", slots=1)
    store = BranchRunStore.create(tmp_path, plan, requested_max_workers=4,
                                  execution_scope="protocol_synthetic", adapter="dummy")
    job = plan.jobs()[0]
    store.append("branch_manifest", job.identity(), job=job)
    before = (tmp_path / "branch_manifest.jsonl").read_text()
    try:
        store.append("branch_manifest", job.identity(), job=job)
    except ValueError as exc:
        assert "duplicate branch_id" in str(exc)
    else:
        raise AssertionError("duplicate branch manifest was accepted")
    assert (tmp_path / "branch_manifest.jsonl").read_text() == before
    store.finish()


@pytest.mark.parametrize("field", ["run_id", "execution_scope"])
def test_explicit_jobs_cannot_disagree_with_durable_run(tmp_path, field):
    plan = BranchPlan(run_id="registered-run", slots=1)
    jobs = plan.jobs()
    if field == "run_id":
        jobs = (replace(jobs[0], run_id="planned"), *jobs[1:])
    else:
        jobs = (replace(jobs[0], execution_scope="measured_diagnostic"), *jobs[1:])
    plan = replace(plan, explicit_jobs=jobs)
    root = tmp_path / "invalid"
    with pytest.raises(ValueError, match="explicit jobs must match"):
        BranchRunStore.create(root, plan, execution_scope="protocol_synthetic")
    assert not root.exists()


def test_generated_run_id_and_selected_scope_are_frozen_in_plan(tmp_path):
    plan = BranchPlan(slots=1)
    store = BranchRunStore.create(tmp_path / "run", plan, execution_scope="measured_diagnostic")
    assert store.plan.run_id == store.manifest["run_id"]
    assert store.plan.execution_scope == store.manifest["execution_scope"] == "measured_diagnostic"
    assert all(j.run_id == store.run_id and j.execution_scope == "measured_diagnostic" for j in store.plan.jobs())
    store.finish()


def test_executor_timeout_preserves_failed_and_queued_for_resume(tmp_path):
    plan = BranchPlan(scene_key="test", task_id="task", system_id="system", slots=8)
    store = BranchRunStore.create(tmp_path, plan, requested_max_workers=8,
                                  execution_scope="protocol_synthetic", adapter="dummy")
    result = BranchExecutor(
        plan, DummyAdapter(steps=20, step_delay_s=0.02), store,
        max_workers=8, timeout_s=0.08,
    ).run()
    assert result["failed"] > 0
    assert result["queued"] > 0
    assert result["active"] == 0
    validation = validate_run(tmp_path)
    assert validation["status"] == "passed"
    assert validation["metrics"]["runtime_failed"] > 0
    assert validation["metrics"]["outcome_status_counts"].get("method_timeout", 0) > 0

    resumed = BranchRunStore.resume(tmp_path)
    retry = BranchExecutor(
        resumed.plan, DummyAdapter(steps=1, step_delay_s=0.001), resumed,
        max_workers=8,
    ).run()
    assert retry["failed"] == 0
    assert retry["queued"] == 0
    assert validate_run(tmp_path)["status"] == "passed"


def test_resume_unversioned_legacy_plan_preserves_base_ids(tmp_path):
    plan = BranchPlan(scene_key="test", task_id="task", system_id="system", slots=1,
                      repeats=3, base_identity_version=1)
    store = BranchRunStore.create(tmp_path, plan, requested_max_workers=2,
                                  execution_scope="protocol_synthetic", adapter="dummy")
    BranchExecutor(store.plan, DummyAdapter(steps=1), store, max_workers=2).run()
    expected = {job.branch_id: job.base_instance_id for job in store.plan.jobs()}
    # Reconstruct the exact pre-version serialized schema and seal, rather than
    # pretending all old saved plans already carried a version marker.
    plan_path = tmp_path / "plan.json"
    plan_data = json.loads(plan_path.read_text())
    del plan_data["base_identity_version"]
    plan_path.write_text(json.dumps(plan_data, sort_keys=True) + "\n")
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["plan_digest"] = hashlib.sha256(json.dumps(plan_data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n")
    seal = tmp_path / "checksums.sha256"
    entries = []
    for line in seal.read_text().splitlines():
        _, name = line.split()
        entries.append(hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() + "  " + name)
    seal.write_text("\n".join(entries) + "\n")
    frozen = plan_path.read_bytes()
    restored = BranchRunStore.resume(tmp_path)
    assert restored.plan.base_identity_version == 1
    assert {job.branch_id: job.base_instance_id for job in restored.plan.jobs()} == expected
    assert plan_path.read_bytes() == frozen
    assert len({job.base_instance_id for job in restored.plan.jobs()}) == 3
