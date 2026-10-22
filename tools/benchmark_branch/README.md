# Benchmark branch collection tool

[中文文档](README.zh-CN.md) · [Plugin integration](PLUGIN_GUIDE.md) · [Example dependencies](examples/README.md)

The CLI now supports a versioned `runtime_factory` for run/resume. See
[runtime integration](RUNTIME_INTEGRATION.zh-CN.md) for `measured_diagnostic`,
main-thread EAI sessions, branch-bound evaluator artifacts and external
benchmark dependencies. No production task or scene is bundled by this seam.

`tools.benchmark_branch` collects paired branch experiments across scenes,
tasks, systems, conditions and repeats. It owns experiment identities,
checkpoint fan-out, worker execution, append-only ledgers, retries, validation,
summary and export. Upstream plugins own scene assets, controllers, physical
stepping and external evaluators. This distribution contains reusable tools,
tests, schemas and configuration examples; scene runners are separate dependencies.

## Tool boundaries

| Component | Responsibility |
| --- | --- |
| `benchmark_branch` | Plan `fact/reference/sham/intervention`, execute plugins, persist and audit data |
| `physics_replay` | Capture/restore/fidelity; `ReplayScheduler` handles resource admission, GPU preflight and leases |
| Upstream scene plugin | Isaac application, isolated runtime state, task semantics and evaluator evidence |

The CLI runtime factory or a Python caller supplies a `ReplayScheduler`. An
upstream runner can explicitly pass one scheduler and a `SessionBackend` to
`BranchExecutor`; that path admits all scene groups through one capacity
ledger and runs branch slots concurrently inside each scene session. Two
independent CLI runs still do not share a GPU capacity budget.

## Quick start

Use Python 3.10+ from the repository root. The JSON/Dummy path uses the standard
library. Tests require `pytest`; YAML requires `PyYAML` for full syntax, and
Parquet export requires `pyarrow`. Isaac is needed only by an external measured
plugin. Use a new output directory for each run.

```bash
python3 -m tools.benchmark_branch plan --config tools/benchmark_branch/examples/dummy_32.json
python3 -m tools.benchmark_branch run --config tools/benchmark_branch/examples/dummy_32.json \
  --max-workers 32 --output /tmp/branch-dummy-run
python3 -m tools.benchmark_branch validate --run-dir /tmp/branch-dummy-run
python3 -m tools.benchmark_branch summarize --run-dir /tmp/branch-dummy-run
python3 -m tools.benchmark_branch export --run-dir /tmp/branch-dummy-run --format jsonl
python3 -m tools.benchmark_branch resume --run-dir /tmp/branch-dummy-run --max-workers 32
```

Use `--execution-mode shared_session` with the same Dummy config to exercise
the vectorized callback contract without Isaac. `run` also accepts `--slots`,
`--arms`, `--execution-scope`, `--timeout`, `--queue-size` and `--resume`.
Formal plans require all four arms even when `--arms` is supplied.

## Plan and concurrency

`pair_slots = slots × repeats × scenes × tasks × systems × conditions`.
`logical_branches = pair_slots × 4`. With one combination and 32 slots, the
plan has **128 branches**, not 128 simultaneous workers. `dummy_matrix.json`
shows Cartesian expansion. It is a protocol example, not a physical scene test.

| Backend | Ownership and counting |
| --- | --- |
| `isolated_parallel` (default) | Fresh process per branch; bounded queue; parent writes canonical ledgers |
| `shared_session` (recommended vectorized path) | One managed vectorized scene session; each active branch owns a distinct environment slot and all branches advance through one batch call |

The shared path requires the managed `vectorized_session` contract, versioned
participant state, generation-fenced slot tokens, copied controls, inactive-slot
readback, and fresh-process differential qualification for native physics. Static
assets may be reused. A simulator process crash can affect the whole shared
scene; `process_failure_scope` is therefore `shared_session`. `job.slot`
identifies an experimental pair; it is not a physical environment index.

`requested_max_workers`, `admitted_max_workers`, `requested_shared_envs`,
`admitted_shared_envs` and `observed_peak_branch_workers` describe different
things. The process backend's peak counts live child processes; the shared
backend's peak comes from the adapter report (zero when absent). Neither alone
proves 32 simultaneous physics branches. Check overlapping tick/heartbeat
intervals, independent branch receipts, isolation and device evidence.
`validate`'s `overlap_detected` means some overlap, not a 32-way acceptance gate.
Legacy `resident` can report retained/peak capacity after a run; use lifecycle
receipts to establish current live occupancy. `completed` is runtime completion,
not a physical-success count.

## Multiple physical scenes

The planner accepts `scenes`, `tasks`, `systems`, `conditions` and `repeats`.
New convenience plans use `base_identity_version=2`: one base ID is shared
across systems, conditions and repeats for the same run/scene/task/version/slot
and fixed base seed. Branch and pair-group IDs still distinguish those axes.
Explicit benchmark jobs retain the upstream identity unchanged. Resuming a
historical sealed plan without this version reconstructs v1 IDs exactly;
existing artifacts are never silently migrated or relabelled for inference.

For downstream mathematical checks and a read-only sealed collector/evaluator
handoff, see [experiment analysis](../experiment_analysis/README.md). That layer
keeps synthetic and unverified measured results descriptive and does not replace
the external benchmark's task semantics, evaluator or release gates.
The current executor loads one adapter and one base checkpoint per run; it does
not dispatch a different plugin/checkpoint for each physical scene. For Modern
House and Warehouse, launch them through an upstream supervisor with separate
scene adapters and shared GPU admission. An injected `SessionBackend` may open
two scene sessions at once and run branch slots concurrently; the tool itself
does not construct Isaac applications or certify their GPU capacity.

The `modern_house*.json` and `warehouse*.json` examples require
`demo.benchmark.branch_plugins` and scene assets from the upstream task package.
They cannot execute from this tool-only publication. The shared examples use
`runtime_steps: 0` for the upstream authored horizon, `device: cuda`,
`transport_mode: kinematic` and `participant_mode: public_greedy`. Those settings
do not prove dynamic-contact transport, EMOS integration or evaluator success.
See [example dependencies](examples/README.md) before using them on a GPU host.

## Persistence and audit

Run outputs include `manifest.json`, `plan.json`, `run_state.json`,
`collector_state.json`, `checksums.sha256`, `validation.json`, `summary.json`
and `branches/<branch-id>/ledger.jsonl`. The canonical JSONL ledgers are:

- `branch_manifest`, `checkpoints`, `steps`, `episodes`, `events`;
- `progress`, `network`, `costs`, `resources`, `metrics`, `observations`.

Lifecycle boundaries are fsynced. High-frequency rows are flushed and fsynced
at close. Resume appends a new attempt for unfinished logical branches, retaining
old rows. Plugins must reproduce the same immutable checkpoint when resumed;
this collector does not serialize arbitrary simulator state for them.

Validation checks identities, checkpoint consistency, restore receipts, pair
roles, sham status, event sequence, missing ledgers and checksums. Failed restores
exclude pairs without deleting raw fact data. Summary is rebuilt from raw ledgers;
export writes a separate file (never choose a raw ledger as its output path).
JSON schemas are field contracts; CLI validation implements its own checks and
does not invoke a general JSON Schema engine.

Optional `resource_limit` keys are `max_workers`, `per_worker_ram_bytes`,
`per_worker_vram_bytes`, `min_ram_available_bytes`, `min_vram_free_bytes`.
This probe is best-effort worker sizing (currently GPU 0 telemetry), not strict
multi-process GPU admission. Use `ReplayScheduler` and a measured device probe
for the formal GPU gate. Resume reloads the frozen runtime factory and timeout/
resource limits; the factory owns its scheduler capacity and reservation policy.

## Evidence and tests

`execution_scope` distinguishes `measured`, `measured_diagnostic`, `protocol_synthetic`, `pending` and
`failed`. Dummy runs must stay `protocol_synthetic`. Measured runtime receipts
can still have `physical_success: null` and `not_evaluable` outcomes. Only an
external evaluator can establish physical success; assignment, authored phase
completion, process exit, sham and intervention success cannot replace fact.
The local pairing checks are infrastructure checks, not the benchmark's strict
causal-pair gate or D1–D6 metric implementation.

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tools/physics_replay/tests/test_scheduler.py tools/benchmark_branch/tests
```

These checks cover planning, persistence, process overlap, failure/resume,
restore mismatch, corruption detection, shared slot bookkeeping and scheduler
lifecycle. Optional scene-plugin tests are skipped when the upstream package is
absent. This suite does not start Isaac or certify full GPU mission acceptance.

## Registered recovery and bounded execution

Measured resume retains method failures and only retries infrastructure losses under a frozen
`job.metadata.registered_recovery` rule. Checkpoint, random stream and registered participant
readback must match before steps. Generic fresh-process retries require new upstream instance
registration; synthetic protocol retries keep their existing behavior. Runtime cleanup precedes
lease release, uncertain cleanup halts admission, and `max_workers` applies across all scenes.
See [the integration contract](OPTIMIZATION_20261020.zh-CN.md) for fields and limitations.
