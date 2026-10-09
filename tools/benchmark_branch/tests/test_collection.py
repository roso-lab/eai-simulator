import json
from pathlib import Path

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
