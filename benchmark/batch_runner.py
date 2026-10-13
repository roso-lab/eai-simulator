"""Composition root for one catalog batch and its branch artifacts.

This module is intentionally a thin caller-owned seam.  It does not create an
Isaac application, restore physics, or decide a physical outcome.  The caller
supplies task/session/decision/evaluator factories; the runner only connects
them to the durable queue, ``run_session_episode``, artifact validation and an
optional release index.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, Callable, ContextManager, Iterable, Mapping

from task.contracts import EaiTaskPackage

from .artifacts import BranchArtifactStore, StoredBranchArtifact
from .batch import ExperimentBatch
from .evidence import EvaluationStatus
from .protocol import BranchSpec, ProtocolContract
from .queue import BatchExecutionQueue, QueueBatch, QueueBranch
from .release import IndexedMetric, ReleaseError, ReleaseIndex, ReleaseReport
from .branch_execution import BranchResourceGate
from .runtime import EpisodeResult
from .session import run_session_episode
from .task_catalog import TaskAnchor


TaskFactory = Callable[[ExperimentBatch, BranchSpec], EaiTaskPackage]
SessionFactory = Callable[[ExperimentBatch, BranchSpec], Any]
ContractFactory = Callable[[TaskAnchor, BranchSpec], ProtocolContract]
DecisionProviderFactory = Callable[[EaiTaskPackage, BranchSpec], Any]
EvaluatorFactory = Callable[[ProtocolContract, BranchSpec], Any | None]
MetricRecordsFactory = Callable[[ExperimentBatch, BranchSpec, StoredBranchArtifact], Iterable[IndexedMetric]]
BranchPreparer = Callable[[ExperimentBatch, BranchSpec], None]


@dataclass(frozen=True)
class BranchRun:
    """Queue state plus the validated artifact for one completed branch."""

    branch: QueueBranch
    artifact: StoredBranchArtifact | None
    evaluation_status: EvaluationStatus | None


@dataclass(frozen=True)
class BatchRunReport:
    """Serializable execution view; release remains an audit product."""

    batch: ExperimentBatch
    branches: tuple[BranchRun, ...]
    release_report: ReleaseReport | None = None

    @property
    def paired_claim_status(self) -> str:
        """Strict paired claims remain external to this execution seam."""

        return "unsupported" if self.batch.strict_pair_required else "not-required"

    @property
    def paired_claim_reason(self) -> str:
        if not self.batch.strict_pair_required:
            return "cell_is_not_D5_strict_pair"
        return "causal_replay_fidelity_and_sham_calibration_required"

    @property
    def artifact_digests(self) -> tuple[str, ...]:
        return tuple(
            item.artifact.payload["artifact_digest"]
            for item in self.branches
            if item.artifact is not None
        )

    @property
    def failed(self) -> tuple[BranchRun, ...]:
        return tuple(item for item in self.branches if item.branch.error)


class BatchEpisodeRunner:
    """Run queued branches through injected live-session dependencies."""

    def __init__(
        self,
        queue: BatchExecutionQueue,
        artifact_store: BranchArtifactStore,
        *,
        release_index: ReleaseIndex | None = None,
    ) -> None:
        self.queue = queue
        self.artifact_store = artifact_store
        self.release_index = release_index

    def run(
        self,
        batch: ExperimentBatch,
        *,
        task_factory: TaskFactory,
        session_factory: SessionFactory,
        contract_factory: ContractFactory,
        decision_provider_factory: DecisionProviderFactory,
        evaluator_factory: EvaluatorFactory | None = None,
        metric_records_factory: MetricRecordsFactory | None = None,
        branch_preparer: BranchPreparer | None = None,
        max_steps: int = 1,
        initial_observations: Callable[[ExperimentBatch, BranchSpec], Iterable[Mapping[str, Any]]] | None = None,
        observation_provider: Callable[[Any], Mapping[str, Any]] | None = None,
        artifact_metadata_factory: Callable[[ExperimentBatch, BranchSpec, Any], Mapping[str, Any]] | None = None,
        stop_on_error: bool = True,
        resource_gate: BranchResourceGate | None = None,
    ) -> BatchRunReport:
        """Execute pending branches and return validated artifacts.

        ``session_factory`` may return either an already-open session or a
        context manager such as ``open_simulator_session(...)``.  The runner
        uses ``run_session_episode`` for every branch and never shares a
        mutable session implicitly.  Parallel isolation, checkpoint restore,
        and causal replay remain explicit responsibilities of the supplied
        factory/preparer.
        """

        if not callable(task_factory) or not callable(session_factory) or not callable(contract_factory):
            raise TypeError("task_factory, session_factory and contract_factory must be callable")
        if not callable(decision_provider_factory):
            raise TypeError("decision_provider_factory must be callable")
        if max_steps <= 0:
            raise ValueError("max_steps must be positive")
        queued = self.queue.enqueue(batch)

        def worker(queue_branch: QueueBranch) -> str:
            branch = queue_branch.branch
            if branch_preparer is not None:
                branch_preparer(batch, branch)
            artifact_path = self.artifact_store.path_for(branch.run_key)
            if artifact_path.exists():
                stored = self.artifact_store.read(branch.run_key)
                self._admit_artifact(batch, branch, stored, metric_records_factory)
                return str(stored.payload["artifact_digest"])

            lease = None
            resource_acquired = False
            resource_status = "worker_error"
            try:
                if resource_gate is not None:
                    lease = resource_gate.acquire(branch)
                    resource_acquired = True
                task = task_factory(batch, branch)
                contract = contract_factory(batch.anchor, branch)
                decision_provider = decision_provider_factory(task, branch)
                evaluator = evaluator_factory(contract, branch) if evaluator_factory is not None else None
                observations = tuple(initial_observations(batch, branch)) if initial_observations is not None else ()
                context = session_factory(batch, branch)
                with context if hasattr(context, "__enter__") and hasattr(context, "__exit__") else nullcontext(context) as session:
                    result = run_session_episode(
                        session,
                        task,
                        contract=contract,
                        run_key=branch.run_key,
                        decision_provider=decision_provider,
                        evaluator=evaluator,
                        branch=branch,
                        initial_observations=observations,
                        observation_provider=observation_provider,
                        max_steps=max_steps,
                        artifact_store=self.artifact_store,
                        artifact_metadata=(
                            artifact_metadata_factory(batch, branch, session)
                            if artifact_metadata_factory is not None
                            else {"batch_manifest_digest": batch.manifest_digest}
                        ),
                    )
                resource_status = "completed"
            finally:
                if resource_gate is not None and resource_acquired:
                    try:
                        resource_gate.release(branch, lease, status=resource_status)
                    except Exception:
                        # Tool audit owns release-failure evidence; do not
                        # replace the branch's primary execution exception.
                        pass
            del result
            stored = self.artifact_store.read(branch.run_key)
            self._admit_artifact(batch, branch, stored, metric_records_factory)
            return str(stored.payload["artifact_digest"])

        self.queue.run_pending(worker, manifest_digest=queued.manifest_digest, stop_on_error=stop_on_error)
        current = self.queue.get(queued.manifest_digest)
        if current is None:  # pragma: no cover - enqueue guarantees presence
            raise RuntimeError("queued batch disappeared during execution")
        runs = tuple(self._branch_run(batch, branch, metric_records_factory) for branch in current.branches)
        release_report = None
        if self.release_index is not None:
            release_report = self.release_index.finalize(expected_plan_digest=batch.plan_digest)
        return BatchRunReport(batch, runs, release_report)

    def _branch_run(
        self,
        batch: ExperimentBatch,
        branch: QueueBranch,
        metric_records_factory: MetricRecordsFactory | None,
    ) -> BranchRun:
        del metric_records_factory
        artifact = None
        if branch.status.value == "completed":
            artifact = self.artifact_store.read(branch.branch.run_key)
        evaluation_status = None
        if artifact is not None:
            raw = artifact.payload.get("evaluation", {})
            if isinstance(raw, Mapping) and raw.get("status") is not None:
                evaluation_status = EvaluationStatus(raw["status"])
        return BranchRun(branch, artifact, evaluation_status)

    def _admit_artifact(
        self,
        batch: ExperimentBatch,
        branch: BranchSpec,
        artifact: StoredBranchArtifact,
        metric_records_factory: MetricRecordsFactory | None,
    ) -> None:
        if self.release_index is None:
            return
        digest = str(artifact.payload.get("artifact_digest", ""))
        newly_admitted = digest not in self.release_index.artifact_digests
        if newly_admitted:
            self.release_index.add_artifact(artifact)
        if metric_records_factory is None or not newly_admitted:
            return
        try:
            records = tuple(metric_records_factory(batch, branch, artifact))
            for record in records:
                self.release_index.add_metric(record)
        except ReleaseError:
            raise


__all__ = [
    "BatchEpisodeRunner",
    "BatchRunReport",
    "BranchPreparer",
    "BranchRun",
    "ContractFactory",
    "DecisionProviderFactory",
    "EvaluatorFactory",
    "MetricRecordsFactory",
    "SessionFactory",
    "TaskFactory",
]
