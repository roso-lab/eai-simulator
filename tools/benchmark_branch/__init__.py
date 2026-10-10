"""Scene-neutral paired branch benchmark collection toolkit."""

from .adapters import (DummyAdapter, ModernHouseExampleAdapter, SceneAdapter,
                       SharedSessionAdapter, WarehouseExampleAdapter)
from .benchmark_bridge import (
    BenchmarkBridgeError, branch_job_from_spec, branch_jobs_from_batch,
    branch_plan_from_batch, validate_jobs_against_batch,
)
from .contracts import (CHECKPOINT_FIELDS, METHOD_FAILURES, OUTCOME_STATUSES,
                        STEP_FIELDS, TaskContract, evaluator_result)
from .executor import BranchExecutor, BranchWorker, WorkerResult
from .models import (BRANCH_ROLES, EXECUTION_SCOPES, BranchJob, BranchPlan,
                     CheckpointArtifact, ResourceSnapshot, canonical_digest)
from .store import LEDGERS, BranchDataCollector, BranchRunStore
from .validation import BranchAggregator, BranchValidator, summarize_run, validate_run

__all__ = [
    "BRANCH_ROLES", "EXECUTION_SCOPES", "BranchJob", "BranchPlan", "CheckpointArtifact",
    "ResourceSnapshot", "canonical_digest", "SceneAdapter", "DummyAdapter",
    "ModernHouseExampleAdapter", "WarehouseExampleAdapter", "SharedSessionAdapter",
    "BenchmarkBridgeError", "branch_job_from_spec", "branch_jobs_from_batch",
    "branch_plan_from_batch", "validate_jobs_against_batch",
    "BranchExecutor", "BranchWorker", "WorkerResult",
    "TaskContract", "evaluator_result", "OUTCOME_STATUSES", "METHOD_FAILURES",
    "CHECKPOINT_FIELDS", "STEP_FIELDS",
    "LEDGERS", "BranchDataCollector", "BranchRunStore", "BranchValidator", "BranchAggregator",
    "validate_run", "summarize_run",
]
