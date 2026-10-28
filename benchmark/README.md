# Benchmark orchestration

The step-by-step paper experiment implementation plan is documented in
[`EXPERIMENT_IMPLEMENTATION_PLAN.zh-CN.md`](EXPERIMENT_IMPLEMENTATION_PLAN.zh-CN.md).

`benchmark/` is the paper-specific experiment layer. It is intentionally
separate from `task/`, which remains the reusable task graph and task-package
layer.

The long-horizon implementation checklist is kept in
[`TODO.zh-CN.md`](TODO.zh-CN.md). The target path is
`task_catalog -> ExperimentCell -> branch artifact -> D1-D6 release index`.

Current modules are deliberately flat:

- `protocol.py`: versioned task identity, run keys, checkpoint references and
  immutable branch roles;
- `runtime.py`: Isaac-independent session binding and a monotonic tick shell;
- `fleet.py`: one branch-scoped Fleet Service Runtime for identities, typed
  leases, message routing, P2 handoff events, dynamic member exit and
  safe-stop audit. It is bound by `session.py` and writes into the same
  `BranchLedger` as the episode.
- `adapters.py`: protocols for skills, execution, evaluation and replay;
- `evidence.py`: branch-local evidence records and hash-chained ledger.
- `emos.py`: EMOS assignment-only adapter; deterministic EMOS fallback by
  default, native policy callback by explicit injection.
- `native_algorithms.py`: source-verified providers for the supplied paper's six
  result columns (FMC-ATA, BT capabilities, SADCHER, HARL:HAPPO, TeamWeaver,
  EMOS). This is the original-algorithm entry point. Per-method scope, model and
  environment requirements are explicit; there is no substitute planner.
- `external_algorithms.py`: historical diagnostic wrappers. Fixed allocations
  and rewritten policies in this path are not eligible as original paper methods.
- `eai_adapter.py`: dependency-light bridge from `SimulatorSession` to the
  common topology-bound `NavigationExecutor`, registered controller adapters,
  neutral actions and bounded host stepping. It reports execution feedback only.
- `p2.py`: external-observation P2 evaluator for precedence, release, handoff
  and accept milestones, including receipt ordering and stable windows.
- `replay.py`: checkpoint/sham/paired-branch protocol gates. It consumes the
  existing `tools/physics_replay` restore report and never treats state
  write-back as causal replay or task success.
- `replay_backend.py`: the narrow adapter from a replay coordinator to the
  benchmark `ReplayBackend` contract. It defaults to observable-state replay;
  `causal_replay` requires an explicit fidelity provider.
- `branch_execution.py`: four-branch execution gate. Sequential mode reuses
  one live coordinator; parallel mode requires one coordinator factory per
  branch so mutable Isaac sessions are never shared between workers.
  It accepts an optional tool-owned `BranchResourceGate` for scene/branch
  resource leases; GPU capacity decisions remain in `tools/physics_replay`.
- `task_catalog.py` and `task_catalog.json`: the 30 paper anchors (10 per
  domain), complete relation signatures, P3-J/P3-S subtype, Table S2 common /
  typical-only cells, eight difficulty bins and common-anchor semantic checks.
- `metrics.py`: the frozen D1--D6 endpoint registry. It records numerator,
  denominator, risk set, direction, required evidence and explicit N/A reasons;
  it does not estimate a result when support is absent.
- `experiment_plan.py`: the EMOS phase-0--7 matrix, K=3 repeats, 0.02 s tick,
  communication and reconfiguration conditions, strict D5 pair gate and D6
  typical nominal cost panel. It expands to repeat-level work items without
  launching a simulator.
- `release.py`: release-index assembly for immutable artifacts and D1--D6
  observations. It preserves repeat/branch/risk-set identity and reports
  pending or explicit N/A support without filling missing cells with zero.
- `reducer.py`: downstream paper-table reduction from released metric
  observations. It applies repeat → base-instance weighting, cluster
  bootstrap and explicit missingness; it does not infer physical outcomes.
- `batch.py`: deterministic `ExperimentCell` to `RunKey`/`CheckpointRef`/
  `BranchSpec` manifests, with digest-checked resume and in-process idempotency.
- `queue.py`: a small durable JSON queue for validated batch manifests and
  branch progress. It resets abandoned `running` claims after restart and
  records artifact digests. `run_pending(worker)` is the narrow execution
  hook: the caller owns Isaac/session work and returns a verified artifact
  digest; the queue never upgrades execution into physical evidence.
- `batch_runner.py`: the composition seam that connects one queued batch to
  injected task/session/decision/evaluator factories, `run_session_episode`,
  artifact verification and an optional `ReleaseIndex`. It accepts an open
  session or an `open_simulator_session(...)` context manager, but never creates
  the Isaac application itself.
- `artifacts.py`: immutable, branch-local JSON artifacts containing the
  evaluator report, Fleet audit summary and verified hash-chained ledger. It refuses overwrites and
  fails closed on digest or ledger-chain mismatch.
- `session.py`: thin composition helpers for an existing
  `SimulatorSession`, plus an explicit lazy `open_and_run_session_episode`
  entrypoint for Isaac smoke runs.

The current vertical slice remains explicit about its execution boundary:
`RuntimeTaskBinding.from_session(...)` snapshots a live session roster and
controller metadata; `EaiSessionRuntime`/`NavigationTaskExecutor` drive a real
session through its bound common navigation executor and `env.step` surface; and
`P2MilestoneEvaluator` is the only component allowed to produce a physical
terminal verdict. A strict paired claim is admitted only after all four branch
roles share one checkpoint digest, the backend reports `causal_replay`, and
fact/sham observations pass calibration. The D1-D6 registry and plan are now
frozen; a metric remains `N/A` or `pending` until its evidence and support
gates pass.

`BatchEpisodeRunner` can execute a strict four-branch manifest through isolated
session factories, but its report marks the paired claim `unsupported` until an
external causal-replay and sham-calibration gate is supplied.

`BatchEpisodeRunner.run(..., resource_gate=...)` and
`BranchExecutionScheduler.run(..., resource_gate=...)` accept the tool-owned
branch lease seam described in
[`tools/physics_replay/REPLAY_SCHEDULER_DEVELOPER_HANDOFF.zh-CN.md`](../tools/physics_replay/REPLAY_SCHEDULER_DEVELOPER_HANDOFF.zh-CN.md).
The benchmark layer only brackets branch work with `acquire/release`; it does
not implement GPU capacity or infer device validity.

To inspect the paper matrix without starting Isaac:

```bash
PYTHONPATH=. python - <<'PY'
from benchmark.experiment_plan import default_experiment_plan

plan = default_experiment_plan()
print(plan.manifest())
print("planned cells:", sum(1 for _ in plan.iter_cells()))
PY
```

To construct one execution-free batch identity:

```python
from benchmark.batch import build_batch_identity
from benchmark.experiment_plan import default_experiment_plan

plan = default_experiment_plan()
cell = next(plan.iter_cells())
batch = build_batch_identity(cell, plan=plan)
print(batch.base_instance_id, batch.manifest_digest)
```

The default checkpoint in this manifest is `planned-base-instance`. A live
runner must replace it with a captured `CheckpointRef` before claiming replay
or paired evidence.

For a live session, callers can keep the lifecycle in `simulator.py` and wire
one episode as follows:

```python
from simulator import open_simulator_session
from benchmark.session import run_session_episode
from benchmark.artifacts import BranchArtifactStore

with open_simulator_session(config) as session:
    result = run_session_episode(
        session,
        task_package,
        contract=p2_contract,
        run_key=run_key,
        decision_provider=emos_adapter,
        evaluator=p2_evaluator,
        max_steps=20,
        artifact_store=BranchArtifactStore("tmp/benchmark-artifacts"),
    )
```

`run_session_episode` samples the runtime observation after each environment
step. The evaluator may add independently collected target and receipt data
through `observation_provider`; those fields are merged with the public robot
pose sample and remain external evidence.

The EMOS example is intentionally scoped to the system decision boundary. The
existing native EMOS discussion manager remains a separate runtime requiring
its configured model/API path; this adapter is imported explicitly and does
not silently replace it or pretend that the deterministic fallback is a paper
result. Keeping it out of `benchmark.__init__` also prevents generic runtime
imports from pulling in Fire Rescue/EMOS dependencies.

The pulled external algorithms use the same boundary through
`benchmark.external_algorithms.ExternalAlgorithmDecisionAdapter`:

```python
from benchmark.external_algorithms import (
    ExternalAlgorithmDecisionAdapter,
    package_for_external_algorithm,
)
from task.fixtures.fire_rescue import build_fire_rescue_assignment_request

request = build_fire_rescue_assignment_request()
package = package_for_external_algorithm(request)
adapter = ExternalAlgorithmDecisionAdapter(request, "robot_fleet")
decision = adapter(package, package.robots)
```

Pass `adapter.replan` to `TaskRunner.run_orchestrated(...)` when execution
feedback should trigger an explicit pending-task reallocation. The adapter
returns a decision and provenance only; `TaskRunner`, the EAI executor and the
external evaluator still own dispatch, execution and physical evidence.

For a checkpoint branch, construct `PhysicsReplayBackend` around the existing
`tools.physics_replay.ReplayCoordinator` and pass it to
`PairedBranchCoordinator`. The adapter restores `checkpoint.step` separately
for every role and applies treatments only after restore. Without an explicit
fidelity provider, the result remains `physical_state_only` and strict paired
claims stay unsupported.

Use `BranchExecutionScheduler` after constructing a `PairedBranchPlan` when
the branch needs to execute an actual episode. Its worker is called only after
the restore gate admits `restored` or `physical_state_only`; it can call
`run_session_episode(..., branch=branch, artifact_store=...)`. For `parallel=True`
the caller must provide `coordinator_factory(branch)` and each factory result
must own an isolated session and branch-local artifact root.

## Protocol repair and migration notes

The current protocol uses versioned identity and keeps execution attempts separate from scientific units. New manifests use `base.v2` identity (generator version, anchor membership/initial-state digest, base seed and slot); system, condition, repeat, checkpoint, branch role and the actual untreated-history identifier live in the continuation `RunKey`. Existing manifests and artifacts are not rewritten. An empty `untreated_history_id` remains diagnostic/planning metadata and cannot qualify an inferential release.

Fleet membership changes are scoped to the registered session. A local exit marks that identity inactive while survivors continue; a reserve can join only when its pre-registered capabilities and live articulation identity are read back unchanged. A partial membership operation or function loss requests a global safe stop. Stop acknowledgement requires a non-empty, identity-bound readback evidence hash, and closing an episode never fabricates an acknowledgement.

Transport receipts distinguish queued, delayed, delivered and dropped messages. Assignment execution is admitted only after the assignment message is consumed by its intended robot. Delay, TTL, bandwidth, seeded loss and link outages are represented in logical ticks; this accounting is not a wire protocol or physical communication-cost qualification.

The sequential runtime and the opt-in `run_orchestrated` entrypoint share one clock, ledger and assignment admission path. A predecessor releases a successor only after explicit dependency completion evidence. Replanning and live world observations are injected through adapter contracts; no second scheduler is introduced. Default navigation requires `session.navigation_executor` with a bound topology and registered controller adapter. A native adapter command sink also exposes its current external action rows through `session.navigation_actions` (a mapping or zero-argument callback); these rows override the neutral baseline for the shared host tick. The benchmark drives dispatch, prepare, host tick and after-step without interpreting controller command types, and releases the fleet lease only after the common executor reports a non-quarantined, measured stopped-and-settled state.

Dynamic availability events are monotonic and idempotent. Their operation callback must return matching event/robot/function/tick/dependency identities, an evidence hash, guard verification and world-preservation evidence. Partial operations request a global safe stop. The schedule snapshot is for audit only and is explicitly not strict replay qualification.

Release and reduction are fail-closed. The release index verifies stored artifact and ledger hashes, binds every metric to the complete artifact identity and requires evidence membership. The formal analysis adapter reuses the frozen design/registration/collector/engine/bundle path and checks the paper policy (`K=3`, `N>=30`, coverage `>=0.8`) without inventing missing values. `READY` means index assembly is complete; inferential readiness stays false until the registered design, support, evaluator evidence and physical qualification are present. D5 source/target family transfer remains a separate, deferred identity and is not inferred from the four replay roles.

These contracts improve the engineering boundary but do not establish GPU or physical qualification. Strict replay still requires complete state and sensor restoration, and physical stop/guard evidence must come from registered Isaac/robot adapters. Specific scene and task generator design remains deferred.
