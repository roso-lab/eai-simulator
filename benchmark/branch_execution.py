"""Execution and scheduling seams for the four paper branches.

The scheduler owns ordering and isolation policy only.  A live Isaac session
is mutable, so sequential execution reuses one coordinator while parallel
execution requires a caller supplied coordinator factory that creates one
independent session/coordinator per branch.  The scheduler never shares a
coordinator between parallel workers and never turns a worker return value
into a physical verdict.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .replay import (
    BranchExecutionStatus,
    PairStatus,
    PairedBranchCoordinator,
    PairedBranchPlan,
    RestoreDecision,
)
from .protocol import BranchSpec


BranchWorker = Callable[[BranchSpec, BranchExecutionStatus], Any]
CoordinatorFactory = Callable[[BranchSpec], PairedBranchCoordinator]


class BranchResourceGate(Protocol):
    """Tool-owned resource lease seam for one benchmark branch.

    The gate may reserve a scene/branch slot, validate the actual device and
    return an opaque lease.  It must not inspect or alter paper branch
    semantics.  Implementations are expected to be thread-safe when the
    scheduler is run in parallel mode.
    """

    def acquire(self, branch: BranchSpec) -> Any:
        ...

    def release(self, branch: BranchSpec, lease: Any, *, status: str) -> None:
        ...


@dataclass(frozen=True)
class BranchExecutionResult:
    """One branch's restore gate and optional worker result."""

    branch: BranchSpec
    status: BranchExecutionStatus
    value: Any = None
    error: str = ""

    @property
    def completed(self) -> bool:
        return not self.error and self.value is not None


class BranchExecutionScheduler:
    """Run branch workers with explicit sequential or isolated parallel policy."""

    def __init__(self, *, max_workers: int = 1):
        if int(max_workers) <= 0:
            raise ValueError("max_workers must be positive")
        self.max_workers = int(max_workers)

    def run(
        self,
        plan: PairedBranchPlan,
        *,
        worker: BranchWorker,
        coordinator: PairedBranchCoordinator | None = None,
        coordinator_factory: CoordinatorFactory | None = None,
        parallel: bool = False,
        resource_gate: BranchResourceGate | None = None,
    ) -> tuple[BranchExecutionResult, ...]:
        """Restore each branch and invoke ``worker`` after the restore gate.

        Sequential mode accepts one coordinator, which is the normal live
        session path.  Parallel mode rejects a shared coordinator and uses a
        factory so each branch owns its own mutable runtime.  Results are
        returned in protocol order even when workers finish out of order.
        """

        if not callable(worker):
            raise TypeError("worker must be callable")
        if parallel:
            if coordinator is not None:
                raise ValueError("parallel branch execution cannot share a coordinator")
            if coordinator_factory is None:
                raise ValueError("parallel branch execution requires coordinator_factory")
            if self.max_workers < 2:
                raise ValueError("parallel branch execution requires at least two workers")
            return self._run_parallel(plan, worker, coordinator_factory, resource_gate)
        if coordinator is None:
            if coordinator_factory is None:
                raise ValueError("sequential branch execution requires coordinator")
            coordinator = coordinator_factory(plan.branches[0])
        return tuple(self._run_one(coordinator, branch, worker, resource_gate=resource_gate) for branch in plan.branches)

    @staticmethod
    def _run_one(
        coordinator: PairedBranchCoordinator,
        branch: BranchSpec,
        worker: BranchWorker,
        *,
        resource_gate: BranchResourceGate | None = None,
    ) -> BranchExecutionResult:
        lease = None
        if resource_gate is not None:
            try:
                lease = resource_gate.acquire(branch)
            except Exception as exc:
                return BranchExecutionResult(
                    branch,
                    BranchExecutionScheduler._failed_status(branch, exc, reason="resource_admission_failed"),
                    error=f"resource_admission_failed:{exc}",
                )
        try:
            status = coordinator.restore_branch(branch)
        except Exception as exc:  # a malformed branch must remain auditable
            status = BranchExecutionStatus(
                branch=branch,
                restore=RestoreDecision(
                    status="unsupported",
                    fidelity="",
                    step=branch.checkpoint.step,
                    diagnostics=(f"restore_exception:{exc}",),
                ),
                pair_status=PairStatus.UNSUPPORTED,
                reason="restore_exception",
            )
            result = BranchExecutionResult(branch, status, error=str(exc))
            if resource_gate is not None:
                BranchExecutionScheduler._release_resource(resource_gate, branch, lease, "restore_error")
            return result
        if status.restore.status not in {"restored", "physical_state_only"}:
            result = BranchExecutionResult(branch, status, error=status.reason or "restore_gate_not_admitted")
            if resource_gate is not None:
                BranchExecutionScheduler._release_resource(resource_gate, branch, lease, "restore_rejected")
            return result
        try:
            result = BranchExecutionResult(branch, status, value=worker(branch, status))
            if resource_gate is not None:
                BranchExecutionScheduler._release_resource(resource_gate, branch, lease, "completed")
            return result
        except Exception as exc:
            result = BranchExecutionResult(branch, status, error=f"worker_exception:{exc}")
            if resource_gate is not None:
                BranchExecutionScheduler._release_resource(resource_gate, branch, lease, "worker_error")
            return result

    def _run_parallel(
        self,
        plan: PairedBranchPlan,
        worker: BranchWorker,
        coordinator_factory: CoordinatorFactory,
        resource_gate: BranchResourceGate | None,
    ) -> tuple[BranchExecutionResult, ...]:
        futures: dict[Future[BranchExecutionResult], int] = {}
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(plan.branches))) as pool:
            for index, branch in enumerate(plan.branches):
                futures[pool.submit(self._run_factory_one, coordinator_factory, branch, worker, resource_gate)] = index
            completed: dict[int, BranchExecutionResult] = {}
            for future in as_completed(futures):
                index = futures[future]
                branch = plan.branches[index]
                try:
                    completed[index] = future.result()
                except Exception as exc:  # defensive: _run_one already captures worker errors
                    completed[index] = BranchExecutionResult(branch, self._failed_status(branch, exc), error=str(exc))
        return tuple(completed[index] for index in range(len(plan.branches)))

    @classmethod
    def _run_factory_one(
        cls,
        coordinator_factory: CoordinatorFactory,
        branch: BranchSpec,
        worker: BranchWorker,
        resource_gate: BranchResourceGate | None,
    ) -> BranchExecutionResult:
        """Construct the isolated coordinator inside its worker thread."""

        try:
            coordinator = coordinator_factory(branch)
        except Exception as exc:
            return BranchExecutionResult(branch, cls._failed_status(branch, exc), error=f"factory_exception:{exc}")
        return cls._run_one(coordinator, branch, worker, resource_gate=resource_gate)

    @staticmethod
    def _failed_status(branch: BranchSpec, exc: Exception, *, reason: str = "scheduler_exception") -> BranchExecutionStatus:
        return BranchExecutionStatus(
            branch=branch,
            restore=RestoreDecision(
                status="unsupported",
                fidelity="",
                step=branch.checkpoint.step,
                diagnostics=(f"{reason}:{exc}",),
            ),
            pair_status=PairStatus.UNSUPPORTED,
            reason=reason,
        )

    @staticmethod
    def _release_resource(gate: BranchResourceGate, branch: BranchSpec, lease: Any, status: str) -> None:
        try:
            gate.release(branch, lease, status=status)
        except Exception:
            # The branch result remains the primary evidence.  A gate must
            # audit release failures itself; swallowing here prevents a tool
            # cleanup exception from corrupting the four-branch result order.
            return


__all__ = ["BranchExecutionResult", "BranchExecutionScheduler", "BranchResourceGate", "BranchWorker", "CoordinatorFactory"]
