import json
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from tools.benchmark_branch import BranchExecutor, BranchPlan, BranchRunStore, DummyAdapter
from tools.benchmark_branch.validation import summarize_run, validate_run


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
