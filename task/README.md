# Generic task orchestration layer

`task/` is a dependency-light boundary between external task packages,
coordination algorithms, caller-owned EAI sessions, and independent evidence
evaluators. It does not own scene construction, Isaac/Kit lifecycle, robot
controllers, task-specific success predicates, USD assets, or benchmark identity.

The dependency direction is:

```text
external EaiTaskPackage + live robot roster
                 -> planner / optional replan callback
                 -> TaskRunner DAG scheduler
                 -> injected executor (owns EAI/Kit stepping)
                 -> independent evaluator (owns physical verdict)
```

No production task manifest is bundled here. Unit tests build generic inline
packages. Scenario-specific files used for a bounded diagnostic belong under
the project ignored `tmp/` tree and do not become published tasks.

`SkillTaskExecutor` now routes caller-resolved `BoundSkill` instances through
the existing skill contracts. `WorldSnapshot` supplies detached, versioned live
observations to bounded orchestration; `ResourceManager` provides atomic
session-local ownership and quarantine when stopping cannot be confirmed.
Each executor binds to one run before scene/replay effects. Batches remain
serial, and a runtime completion receipt never replaces an independent physical
evaluator. Business request mapping, live validation and controller callbacks
remain caller-owned. See the [runtime guide](RUNTIME_GUIDE.zh-CN.md) and
[development checklist](DEVELOPMENT_TODO.zh-CN.md) for wiring and limitations.

## One-shot compatibility

`TaskRunner.run(...)` preserves the existing one-shot API: it requests one
decision, validates task and active-robot identities plus declared
capabilities, dispatches assignments sequentially through `SkillExecutor`,
and passes feedback to the evaluator. If no executor is injected, the result is
always `N/A / physical_evidence_required`, even when an evaluator object was
provided.

## Optional bounded feedback orchestration

`TaskRunner.run_orchestrated(...)` adds an optional DAG scheduler:

- `OrchestrationLimits(max_rounds, max_dispatches)` provides hard termination
  bounds.
- The initial planner may assign a whole DAG or a ready subset.
- A `ReplanProvider(task, robots, state)` may replace the pending assignment
  plan after each dispatch round. Returning `None` stops planning. The runner
  does not substitute another algorithm or silently reuse a failed assignment.
- The executor returns an `ExecutionUpdate` with an explicit scheduler-only
  `DependencyOutcome`: `completed`, `retryable_failure`, or
  `terminal_failure`. A retryable task runs again only if the replanner
  explicitly assigns it.
- A terminal failure blocks descendants but does not prevent independent ready
  tasks from completing.
- At most one assignment per robot is selected in a round. An executor may
  implement `execute_batch(assignments, tasks, scene=...)` to receive the
  parallel-ready set in one callback. The runner starts no threads and performs
  no Isaac steps; the callback must keep Kit/session access on its owner thread.
- Every feedback identity must exactly match one dispatched task/robot pair.
  Unknown tasks, inactive robots, duplicate task assignments, task/unassigned
  overlap, capability mismatches, missing feedback, and extra feedback fail
  closed.

Scheduler completion only means that the executor allowed DAG dependencies to
advance. It is not a task-success or physical-success verdict. Only the
injected evaluator may return such a verdict. With no evaluator the verdict
remains `N/A / physical_evidence_required`.

## External dependency protocol

The caller must supply and version the environment-specific pieces:

1. Build or load an `EaiTaskPackage`, then use `bind_runtime(...)` with the
   active robot roster sampled from the live session.
2. A scene provider returns an opaque caller-owned scene/session context.
3. A planner returns `AlgorithmDecision` records; it never executes controls
   or declares physical success.
4. The executor resolves task metadata such as object references, controller
   resources, assets, and skill implementations. The task runner can validate
   declared robot capabilities, but it cannot infer whether opaque scene
   resources exist. Missing or stale resources must produce an explicit
   executor failure/update, never a fabricated completion.
5. The executor owns all controller calls and simulator stepping. A batch
   callback may schedule a ready set serially inside one live EAI session.
6. The evaluator consumes runtime evidence and owns milestone predicates and
   the final verdict. Replay, when requested, is likewise an injected backend.

The append-only ledger records decisions, dispatches, scheduler updates,
termination, and the evaluator verdict so orchestration provenance remains
auditable without importing Isaac Sim.
