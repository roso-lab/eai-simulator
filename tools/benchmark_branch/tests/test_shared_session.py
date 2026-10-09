import json

from tools.benchmark_branch import BranchExecutor, BranchPlan, BranchRunStore
from tools.benchmark_branch.adapters import DummyAdapter


def test_shared_session_uses_vector_slots_and_keeps_branch_ledgers(tmp_path):
    plan = BranchPlan.from_mapping({
        "scene_key": "shared-test",
        "task_id": "shared-task",
        "slots": 8,
        "arms": ["fact", "reference", "sham", "intervention"],
        "execution_scope": "protocol_synthetic",
    })
    store = BranchRunStore.create(
        tmp_path / "run", plan, requested_max_workers=8,
        execution_mode="shared_session",
    )
    store.update_manifest(shared_session_envs=8)
    result = BranchExecutor(
        plan, DummyAdapter(steps=2, step_delay_s=0.0), store, max_workers=8,
    ).run()

    assert result["execution_backend"] == "shared_session"
    assert result["requested_shared_envs"] == 8
    assert result["admitted_shared_envs"] == 8
    assert result["observed_peak_branch_workers"] == 8
    assert result["completed"] == 32
    assert result["failed"] == 0
    assert result["slot_isolation_validated"] is True
    assert result["independent_branch_sessions"] == 1

    manifests = store.read_ledger("branch_manifest")
    episodes = store.read_ledger("episodes")
    assert len(manifests) == len(episodes) == 32
    assert {row["worker_instance_id"] for row in episodes} == {
        f"env-slot-{slot}" for slot in range(8)
    }
    assert all(row["execution_scope"] == "protocol_synthetic" for row in episodes)
    admissions = [row for row in store.read_ledger("metrics")
                  if row.get("kind") == "branch_slot_admitted"]
    for offset in range(0, len(admissions), 8):
        wave = admissions[offset:offset + 8]
        assert len({row["worker_session_id"] for row in wave}) == 8
        assert {row["environment_slot"] for row in wave} == set(range(8))
    branch_ledgers = sorted((tmp_path / "run" / "branches").glob("*/ledger.jsonl"))
    assert len(branch_ledgers) == 32
    assert all(path.read_text(encoding="utf-8").strip() for path in branch_ledgers)
    assert "branches/" in (tmp_path / "run" / "checksums.sha256").read_text(encoding="ascii")
    assert json.loads((tmp_path / "run" / "validation.json").read_text())["status"] == "passed"

    branch_ledgers[0].unlink()
    from tools.benchmark_branch.validation import validate_run
    invalid = validate_run(tmp_path / "run", write=False)
    assert invalid["status"] == "failed"
    assert any("missing shared-session branch ledger" in error for error in invalid["errors"])


def test_shared_session_respects_executor_ceiling(tmp_path):
    plan = BranchPlan(slots=8)
    store = BranchRunStore.create(tmp_path / "run", plan, requested_max_workers=32,
                                  execution_mode="shared_session")
    store.update_manifest(shared_session_envs=32)
    result = BranchExecutor(plan, DummyAdapter(steps=1, step_delay_s=0), store,
                            max_workers=4).run()
    assert result["admitted_shared_envs"] == 4
    assert result["observed_peak_branch_workers"] == 4
    assert result["completed"] == 32


def test_shared_restore_failures_do_not_count_as_active_slots(tmp_path):
    plan = BranchPlan(slots=2)
    store = BranchRunStore.create(tmp_path / "run", plan, requested_max_workers=8,
                                  execution_mode="shared_session")
    result = BranchExecutor(
        plan, DummyAdapter(steps=1, restore_mismatch_slots=(0, 1)), store,
        max_workers=8,
    ).run()
    assert result["admitted_shared_envs"] == 8
    assert result["observed_peak_branch_workers"] == 0
    assert result["completed"] == 0
    assert result["failed"] == 8


def test_shared_missing_observed_peak_is_not_replaced_with_capacity(tmp_path):
    class NoPeakAdapter(DummyAdapter):
        def run_shared_session(self, *args, **kwargs):
            result = dict(super().run_shared_session(*args, **kwargs))
            result.pop("observed_peak_branch_workers")
            return result

    plan = BranchPlan(slots=1)
    store = BranchRunStore.create(tmp_path / "run", plan, requested_max_workers=4,
                                  execution_mode="shared_session")
    result = BranchExecutor(plan, NoPeakAdapter(steps=1, step_delay_s=0), store,
                            max_workers=4).run()
    assert result["completed"] == 4
    assert result["observed_peak_branch_workers"] == 0
