from __future__ import annotations

import pytest

from benchmark.batch import build_batch_identity
from benchmark.experiment_plan import default_experiment_plan
from benchmark.protocol import BranchRole
from benchmark.queue import BatchExecutionQueue, QueueBranchStatus, QueueError


def _batch():
    plan = default_experiment_plan()
    cell = next(item for item in plan.iter_cells() if item.strict_pair_required)
    return plan, build_batch_identity(cell, plan=plan)


def test_queue_enqueue_is_idempotent_and_persists_branch_progress(tmp_path) -> None:
    plan, batch = _batch()
    path = tmp_path / "queue.json"
    queue = BatchExecutionQueue(path, plan=plan)
    first = queue.enqueue(batch)
    second = queue.enqueue(batch.manifest())
    assert first is second
    assert len(first.branches) == 4
    assert path.exists()

    claimed = queue.claim_next(batch.manifest_digest)
    assert claimed is not None
    assert claimed.status is QueueBranchStatus.RUNNING

    resumed = BatchExecutionQueue(path, plan=plan)
    restored = resumed.get(batch.manifest_digest)
    assert restored is not None
    assert restored.pending[0].run_key == claimed.run_key
    assert resumed.recover(batch.manifest_digest) == 0


def test_queue_recovery_and_completion_are_restart_safe(tmp_path) -> None:
    plan, batch = _batch()
    path = tmp_path / "queue.json"
    queue = BatchExecutionQueue(path, plan=plan)
    queue.enqueue(batch)
    claimed = queue.claim_next()
    assert claimed is not None
    queue.complete(batch.manifest_digest, BranchRole.FACT, artifact_digest="sha256:fact")

    restarted = BatchExecutionQueue(path, plan=plan)
    current = restarted.get(batch.manifest_digest)
    assert current is not None
    fact = next(item for item in current.branches if item.branch.run_key.branch_role is BranchRole.FACT)
    assert fact.status is QueueBranchStatus.COMPLETED
    assert fact.artifact_digest == "sha256:fact"
    assert restarted.claim_next(batch.manifest_digest) is not None


def test_queue_requires_artifact_digest_and_does_not_mutate_completed_branch(tmp_path) -> None:
    plan, batch = _batch()
    queue = BatchExecutionQueue(tmp_path / "queue.json", plan=plan)
    queue.enqueue(batch)
    with pytest.raises(QueueError, match="artifact_digest"):
        queue.complete(batch.manifest_digest, BranchRole.FACT, artifact_digest="")
    queue.complete(batch.manifest_digest, BranchRole.FACT, artifact_digest="sha256:fact")
    with pytest.raises(QueueError, match="immutable"):
        queue.fail(batch.manifest_digest, BranchRole.FACT, error="late failure")


def test_queue_run_pending_records_worker_artifacts_and_failures(tmp_path) -> None:
    plan, batch = _batch()
    queue = BatchExecutionQueue(tmp_path / "queue.json", plan=plan)
    queue.enqueue(batch)
    seen: list[str] = []

    def worker(branch):
        seen.append(branch.run_key)
        return "sha256:" + branch.run_key.rsplit("/", 1)[-1]

    results = queue.run_pending(worker, manifest_digest=batch.manifest_digest)
    assert len(results) == 4
    assert queue.get(batch.manifest_digest).completed
    assert len(seen) == 4

    failing = BatchExecutionQueue(tmp_path / "failing.json", plan=plan)
    failing.enqueue(batch)

    def fail_once(_branch):
        raise RuntimeError("session unavailable")

    failed = failing.run_pending(fail_once, manifest_digest=batch.manifest_digest)
    assert failed[0].status is QueueBranchStatus.FAILED
    assert "session unavailable" in failed[0].error
