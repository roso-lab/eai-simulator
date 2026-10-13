"""Durable, idempotent queue state for planned experiment batches.

The queue is deliberately smaller than a workflow engine.  It persists the
validated batch manifest and per-branch execution state so a process can be
restarted without changing run identity.  It does not open Isaac, capture a
checkpoint, or infer physical success; a runner supplies those operations and
records only the resulting artifact digest here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Any, Callable, Mapping

from .batch import BatchContractError, ExperimentBatch, resume_batch
from .experiment_plan import ExperimentPlan, default_experiment_plan
from .protocol import BranchRole, BranchSpec


QUEUE_SCHEMA_VERSION = "benchmark.queue.v1"
QueueWorker = Callable[["QueueBranch"], str]


class QueueError(ValueError):
    """Raised when durable queue state is malformed or conflicts."""


class QueueBranchStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class QueueBranch:
    """Durable state for one immutable branch specification."""

    branch: BranchSpec
    status: QueueBranchStatus = QueueBranchStatus.PENDING
    artifact_digest: str = ""
    error: str = ""

    @property
    def run_key(self) -> str:
        return self.branch.run_key.as_string()

    def to_dict(self) -> dict[str, Any]:
        return {
            "branch": self.branch.to_dict(),
            "status": self.status.value,
            "artifact_digest": self.artifact_digest,
            "error": self.error,
        }


@dataclass(frozen=True)
class QueueBatch:
    """A validated batch manifest and its branch progress snapshot."""

    manifest: Mapping[str, Any]
    branches: tuple[QueueBranch, ...]

    @property
    def manifest_digest(self) -> str:
        return str(self.manifest["manifest_digest"])

    @property
    def completed(self) -> bool:
        return bool(self.branches) and all(item.status is QueueBranchStatus.COMPLETED for item in self.branches)

    @property
    def pending(self) -> tuple[QueueBranch, ...]:
        return tuple(item for item in self.branches if item.status is QueueBranchStatus.PENDING)

    def to_dict(self) -> dict[str, Any]:
        return {"manifest": dict(self.manifest), "branches": [item.to_dict() for item in self.branches]}


class BatchExecutionQueue:
    """Persist queue state in one atomically replaced JSON file.

    ``enqueue`` is idempotent by manifest digest.  ``resume`` revalidates the
    stored manifest against the current experiment plan and resets abandoned
    ``running`` branches to ``pending``; a crash therefore cannot strand work.
    """

    def __init__(self, path: str | Path, *, plan: ExperimentPlan | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.plan = plan or default_experiment_plan()
        self._items: dict[str, QueueBatch] = {}
        if self.path.exists():
            self._load()
            self.recover()

    def enqueue(self, batch: ExperimentBatch | Mapping[str, Any]) -> QueueBatch:
        manifest = batch.manifest() if isinstance(batch, ExperimentBatch) else dict(batch)
        try:
            validated = resume_batch(manifest, plan=self.plan)
        except (BatchContractError, TypeError, ValueError) as exc:
            raise QueueError(f"invalid batch manifest: {exc}") from exc
        digest = validated.manifest_digest
        existing = self._items.get(digest)
        if existing is not None:
            if dict(existing.manifest) != dict(validated.manifest()):
                raise QueueError("manifest digest collision with different queue item")
            return existing
        item = QueueBatch(
            manifest=validated.manifest(),
            branches=tuple(QueueBranch(branch) for branch in validated.branches),
        )
        self._items[digest] = item
        self._save()
        return item

    def get(self, manifest_digest: str) -> QueueBatch | None:
        return self._items.get(str(manifest_digest))

    def claim_next(self, manifest_digest: str | None = None) -> QueueBranch | None:
        """Claim one pending branch and persist ``running`` before returning."""

        candidates = [self._items[key] for key in sorted(self._items)]
        if manifest_digest is not None:
            item = self.get(manifest_digest)
            candidates = [] if item is None else [item]
        for item in candidates:
            for index, branch in enumerate(item.branches):
                if branch.status is not QueueBranchStatus.PENDING:
                    continue
                updated = QueueBranch(branch.branch, QueueBranchStatus.RUNNING, branch.artifact_digest, branch.error)
                branches = item.branches[:index] + (updated,) + item.branches[index + 1 :]
                self._items[item.manifest_digest] = QueueBatch(item.manifest, branches)
                self._save()
                return updated
        return None

    def complete(self, manifest_digest: str, branch_role: BranchRole | str, *, artifact_digest: str) -> QueueBatch:
        return self._update_branch(manifest_digest, branch_role, QueueBranchStatus.COMPLETED, artifact_digest=artifact_digest)

    def fail(self, manifest_digest: str, branch_role: BranchRole | str, *, error: str) -> QueueBatch:
        return self._update_branch(manifest_digest, branch_role, QueueBranchStatus.FAILED, error=error)

    def run_pending(
        self,
        worker: QueueWorker,
        *,
        manifest_digest: str | None = None,
        stop_on_error: bool = True,
    ) -> tuple[QueueBranch, ...]:
        """Execute pending branches through a caller-owned worker.

        The worker receives a claimed branch and must return the verified
        artifact digest.  It may call ``run_session_episode`` or a paired
        scheduler, but the queue itself remains Isaac-independent.  Worker
        failures become auditable ``failed`` states; no failed branch is
        silently converted to a completed result.
        """

        if not callable(worker):
            raise TypeError("worker must be callable")
        results: list[QueueBranch] = []
        while True:
            claimed = self.claim_next(manifest_digest)
            if claimed is None:
                return tuple(results)
            try:
                artifact_digest = str(worker(claimed)).strip()
                if not artifact_digest:
                    raise QueueError("worker must return a non-empty artifact_digest")
                item = self.complete(
                    self._manifest_for_run_key(claimed.run_key),
                    claimed.branch.run_key.branch_role,
                    artifact_digest=artifact_digest,
                )
                results.append(next(branch for branch in item.branches if branch.run_key == claimed.run_key))
            except Exception as exc:
                digest = self._manifest_for_run_key(claimed.run_key)
                item = self.fail(digest, claimed.branch.run_key.branch_role, error=str(exc))
                results.append(next(branch for branch in item.branches if branch.run_key == claimed.run_key))
                if stop_on_error:
                    return tuple(results)

    def _manifest_for_run_key(self, run_key: str) -> str:
        for digest, item in self._items.items():
            if any(branch.run_key == run_key for branch in item.branches):
                return digest
        raise QueueError(f"run key is not present in queue: {run_key}")

    def recover(self, manifest_digest: str | None = None) -> int:
        """Reset running branches after a process restart; completed branches stay immutable."""

        selected = [self.get(manifest_digest)] if manifest_digest is not None else list(self._items.values())
        changed = 0
        for item in selected:
            if item is None:
                continue
            branches = []
            item_changed = False
            for branch in item.branches:
                if branch.status is QueueBranchStatus.RUNNING:
                    branches.append(QueueBranch(branch.branch, QueueBranchStatus.PENDING, branch.artifact_digest, ""))
                    item_changed = True
                    changed += 1
                else:
                    branches.append(branch)
            if item_changed:
                self._items[item.manifest_digest] = QueueBatch(item.manifest, tuple(branches))
        if changed:
            self._save()
        return changed

    def _update_branch(
        self,
        manifest_digest: str,
        branch_role: BranchRole | str,
        status: QueueBranchStatus,
        *,
        artifact_digest: str = "",
        error: str = "",
    ) -> QueueBatch:
        item = self.get(manifest_digest)
        if item is None:
            raise QueueError(f"unknown manifest_digest: {manifest_digest}")
        role = BranchRole(branch_role)
        for index, branch in enumerate(item.branches):
            if branch.branch.run_key.branch_role is not role:
                continue
            if status is QueueBranchStatus.COMPLETED and not artifact_digest.strip():
                raise QueueError("completed branches require an artifact_digest")
            if branch.status is QueueBranchStatus.COMPLETED and status is not QueueBranchStatus.COMPLETED:
                raise QueueError("completed branch state is immutable")
            updated = QueueBranch(branch.branch, status, artifact_digest.strip(), error.strip())
            branches = item.branches[:index] + (updated,) + item.branches[index + 1 :]
            result = QueueBatch(item.manifest, branches)
            self._items[item.manifest_digest] = result
            self._save()
            return result
        raise QueueError(f"branch role is not present: {role.value}")

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise QueueError(f"cannot load queue state: {self.path}") from exc
        if payload.get("schema_version") != QUEUE_SCHEMA_VERSION:
            raise QueueError("unsupported queue schema version")
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            raise QueueError("queue items must be a list")
        for raw in raw_items:
            if not isinstance(raw, Mapping):
                raise QueueError("queue item must be a mapping")
            manifest = raw.get("manifest")
            if not isinstance(manifest, Mapping):
                raise QueueError("queue item manifest is missing")
            try:
                validated = resume_batch(manifest, plan=self.plan)
            except (BatchContractError, TypeError, ValueError) as exc:
                raise QueueError(f"stored manifest failed validation: {exc}") from exc
            branches = tuple(self._branch_from_record(record, validated) for record in raw.get("branches", ()))
            if tuple(item.branch.run_key for item in branches) != tuple(item.run_key for item in validated.branches):
                raise QueueError("stored queue branches do not match manifest")
            self._items[validated.manifest_digest] = QueueBatch(validated.manifest(), branches)

    @staticmethod
    def _branch_from_record(record: Any, batch: ExperimentBatch) -> QueueBranch:
        if not isinstance(record, Mapping):
            raise QueueError("queue branch record must be a mapping")
        raw_branch = record.get("branch")
        if not isinstance(raw_branch, Mapping):
            raise QueueError("queue branch specification is missing")
        role = BranchRole(raw_branch.get("run_key", {}).get("branch_role"))
        branch = next((item for item in batch.branches if item.run_key.branch_role is role), None)
        if branch is None or branch.to_dict() != dict(raw_branch):
            raise QueueError("queue branch specification differs from manifest")
        try:
            status = QueueBranchStatus(record.get("status", QueueBranchStatus.PENDING.value))
        except ValueError as exc:
            raise QueueError("invalid queue branch status") from exc
        return QueueBranch(branch, status, str(record.get("artifact_digest", "")), str(record.get("error", "")))

    def _save(self) -> None:
        payload = {
            "schema_version": QUEUE_SCHEMA_VERSION,
            "items": [self._items[key].to_dict() for key in sorted(self._items)],
        }
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.path)


__all__ = ["BatchExecutionQueue", "QUEUE_SCHEMA_VERSION", "QueueBatch", "QueueBranch", "QueueBranchStatus", "QueueError", "QueueWorker"]
