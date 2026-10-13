"""Composition helpers for running one benchmark episode on a live session.

This is intentionally a thin boundary.  ``simulator.py`` still owns Isaac
application lifecycle; this module only wires an already-created session to
the benchmark contracts.  The import of ``simulator`` is lazy so contract
tests and algorithm-only runs stay Isaac-independent.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from task.contracts import EaiTaskPackage

from .adapters import DecisionProvider, EvidenceEvaluator, SkillAdapter, SkillRequestFactory, TaskExecutor
from .eai_adapter import (
    CommandNavigationSkillAdapter,
    EaiSessionRuntime,
    EaiTaskExecutor,
    navigation_request_factory,
)
from .fleet import FleetRuntime
from .protocol import BranchSpec, ProtocolContract, RunKey
from .runtime import EpisodeResult, EpisodeRunner, RuntimeTaskBinding


def run_session_episode(
    session: Any,
    task: EaiTaskPackage,
    *,
    contract: ProtocolContract,
    run_key: RunKey,
    decision_provider: DecisionProvider,
    evaluator: EvidenceEvaluator | None,
    request_factory: SkillRequestFactory = navigation_request_factory,
    skill: SkillAdapter | None = None,
    executor: TaskExecutor | None = None,
    branch: BranchSpec | None = None,
    initial_observations: Sequence[Mapping[str, Any]] = (),
    observation_provider: Callable[[Any], Mapping[str, Any]] | None = None,
    max_steps: int = 1,
    binding_metadata: Mapping[str, Any] | None = None,
    artifact_store: Any | None = None,
    artifact_metadata: Mapping[str, Any] | None = None,
) -> EpisodeResult:
    """Run one sequential episode against an existing ``SimulatorSession``.

    The default skill/executor pair supports command-driven navigation.  A
    caller can inject a different skill, executor, request factory or
    observation provider while retaining the same runtime and ledger seams.
    ``evaluator`` remains explicit because a missing evaluator must produce
    ``N/A`` rather than an inferred physical result.
    """

    binding = RuntimeTaskBinding.from_session(
        task,
        session,
        contract=contract,
        metadata=binding_metadata,
    )
    runtime = EaiSessionRuntime(session, observation_provider=observation_provider)
    # A session episode always gets one branch-scoped Fleet Runtime.  Passing
    # it into the runner makes the fleet events and algorithm/executor trace
    # share one tamper-evident branch ledger.
    runner = EpisodeRunner(runtime, run_key=run_key, fleet=FleetRuntime(run_key))
    effective_skill = skill or CommandNavigationSkillAdapter()
    effective_executor = executor or EaiTaskExecutor(session, max_steps=max_steps)
    result = runner.run(
        binding,
        decision_provider=decision_provider,
        request_factory=request_factory,
        skill=effective_skill,
        executor=effective_executor,
        evaluator=evaluator,
        scene=session,
        branch=branch,
        initial_observations=initial_observations,
    )
    if artifact_store is not None:
        artifact_store.write(result, binding=binding, branch=branch, metadata=artifact_metadata)
    return result


def open_and_run_session_episode(
    config: Any,
    task: EaiTaskPackage,
    **kwargs: Any,
) -> EpisodeResult:
    """Open one Isaac session through the public launcher and run an episode.

    ``config`` is expected to be a ``SimulatorLaunchConfig``.  The type is
    intentionally structural and imported lazily to keep this module safe in
    lightweight environments.  The context manager remains owned by
    ``simulator.py`` and is closed before this function returns.
    """

    from simulator import open_simulator_session

    with open_simulator_session(config) as session:
        return run_session_episode(session, task, **kwargs)


__all__ = ["open_and_run_session_episode", "run_session_episode"]
