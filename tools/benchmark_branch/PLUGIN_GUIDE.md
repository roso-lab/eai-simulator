# Third-party plugin integration

## Runtime factories (current entry point)

Configure `runtime_factory: "your_package.runtime:create_runtime"` and optional
`runtime_kwargs`. The callable receives `plan`, `run_dir`, and those kwargs,
returning `RuntimeComponents(adapter, session_backend, scheduler, plugin_id,
plugin_version, executor_options)`. Both CLI run and resume load this factory;
resume rejects changed source/config/version. Factories must not launch Isaac.
Task/asset dependency hashes remain the external package's responsibility.

`EaiSessionBackend` uses the existing EAI session context on the main thread,
with injected driver and evaluator callbacks. It serializes branches; another
backend must explicitly own process isolation for real physical concurrency.
The session path's timeout is cooperative at emit/return boundaries, not a
watchdog for blocked native calls. `close_branch` must be idempotent.

Use `measured_diagnostic` for temporary tool probes without benchmark-owned
identities; it cannot establish formal pairing. `measured` rejects missing
upstream identity before opening a session. Both require actual device and
restore/tick evidence. Evaluators call `write_evaluator_evidence` to persist
raw observations and branch-bound verdicts; execution and offline validation
re-read files, hashes, identity, predicates and verdict consistency.

See [the full integration contract](RUNTIME_INTEGRATION.zh-CN.md).
The adapter-only paths below remain useful for synthetic protocol checks.

`benchmark_branch` is intentionally scene-neutral. A plugin is an importable
Python object configured with `adapter: "package.module:Class"`; it must not
modify the collector's ledgers directly.

## Required adapter contract

Implement `validate_contract`, `create_scene`, `prepare_base_instance`,
`capture_checkpoint`, `restore_checkpoint`, `validate_restore`,
`apply_treatment`, `step`, `observe_external_state`, `collect_events`,
`collect_progress`, `collect_network`, `collect_cost`, `finish_episode` and
`close`. `examples/plugin_template.py` is a minimal skeleton.

Freeze the task contract before workers start: DAG, milestones and weights,
capabilities, roles, observation permissions, locks, fixed timestep, horizon,
termination predicates, treatment arms, nuisance factors, intervention points,
validity thresholds, feasibility status and proof hash. Use immutable digests for
scene build, asset resolution, build order, task and skill versions.

`capture_checkpoint` returns a public state plus metadata. Every branch restores
that artifact and returns a readback digest and tolerance. A restore mismatch
invalidates the pair statistic but must leave the fact ledger intact.

`finish_episode` may return evaluator evidence and a terminal status. It must
never infer physical success from assignment, phase completion, a zero process
exit, or replay completion. Keep detector output and sensor frame references
separate from evaluator truth. If evidence is missing, return `not_evaluable`,
`pending` or `physical_success: null`.

## Isolation

The default executor starts one fresh process per branch. Never reuse mutable
simulator, controller, task, queue, RNG, checkpoint restore or output objects
across branches. Each worker must identify its PID/session and branch directory.

A plugin may implement `run_shared_session(jobs, checkpoint, emit, *, num_envs,
timeout_s)` for one scene-owned simulator process with vectorized environments.
Only static scene assets may be shared. Assign every active branch a distinct
environment slot, mutable state, controller state, queues, RNG namespace and
ledger identity. Report `slot_isolation_validated` and
`cross_slot_contact_count`; report `observed_peak_branch_workers` from actual
active slots. The collector treats adapter capacity as a limit, not evidence.

For two physical scenes, create two plugin instances/runs and use a supervisor
that admits both through one `ReplayScheduler`. Keep scene processes and output
roots separate. A process-per-scene design is compatible with the tool; the
collector does not silently combine two scene sessions.

## Evidence required for measured scope

A measured run should record requested and actual physics device, controller
device, GPU UUID, free/used VRAM, host memory, scene/env count, CPU fallback,
worker/session PID, first/last simulation tick, heartbeat, startup/warmup and
episode time boundaries. Device `cuda` in JSON is only a request. Unknown
actual device is `unavailable`; CPU fallback makes the GPU run invalid/pending.

A plugin may be tested with `DummyAdapter`, but protocol-synthetic overlap does
not prove Isaac concurrency, task physics, sensor correctness, evaluator truth,
or GPU capacity. Keep source revision and artifact paths in the manifest.

## Scheduler-aware session backend

For a measured run, pass an implementation of `SessionBackend` and one
`ReplayScheduler` to `BranchExecutor`. The collector then executes this order
for every scene group:

```text
acquire_scene
  -> open_scene
  -> warmup
  -> scheduler.preflight
  -> acquire_branch
  -> open_branch / run_branch / close_branch
  -> release_branch
  -> close_scene / release_scene
```

The backend methods are declared in `tools/benchmark_branch/session.py`. For
`execution_affinity="thread_pool"`, scene groups run in parallel session threads,
and each scene runs its admitted branch
slots concurrently up to `ReplayScheduler.capacity.max_branches_per_scene`;
the backend must give every slot an isolated mutable runtime even when static
assets are shared. `caller_thread` runs serially on the caller, as required by
`EaiSessionBackend` and Isaac/Kit.
`run_branch` must return explicit `completed` and `status` fields; missing
responses become `missing_response`. A failed preflight creates raw
`infrastructure_invalid` episode rows with `physical_success: null` and never
opens branch leases. Scheduler audit rows are stored in `resources.jsonl`.
Collector-owned infrastructure failures retain explicit machine-readable
`infrastructure_reason` codes: `preflight_rejected` and
`missing_runtime_receipt`. Missing-receipt diagnostics are sealed at episode
top level even when the backend supplies a nested `outcome`. Adapters declaring
their own infrastructure failure must provide an explicit reason code; an
external evaluator cannot reclassify a method failure as a retryable incident.
The downstream [analysis schedule](../experiment_analysis/README.md) requires
the sealed code to match its frozen allowlist and evaluated exclusion reason.

Use `requested_device="cuda:0"` (or another explicit CUDA device) and a real
device probe that reports actual physics/controller devices and GPU UUID. The
collector rejects `measured` execution unless both the session backend and
scheduler are supplied. The collector still does not run the benchmark paired
gate or infer physical success.
