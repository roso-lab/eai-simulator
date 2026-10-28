# SADCHER independent boundary review — 2026-10-28

Read upstream frozen source, shared factory/CLI/lifecycle code, adapter and TaskRunner; reproduced suspected defects on CPU before changing only owned adapter/tests. This review supersedes the earlier report's statement that the audit performs a second strict load. Shared entrypoints and upstream source were not changed.

## Concrete findings

1. **P1 — live task observation was ignored (fixed).** TaskRunner binds snapshots to `task.metadata.runtime_observation`; the adapter read only static node metadata. With an observed IN_PROGRESS task and remaining duration 2, it constructed PENDING and remaining workload 5. That changes native input features and potentially assignment, although previous synchronous batch-completion tests did not expose it. `build_simulation` now consumes the explicit `runtime_observation.objects.native_tasks` vocabulary, requires complete real-task coverage/status/remaining duration, and rejects disagreement with executor-owned completed feedback. Native feature/reward/assignment parity is now tested at actual in-progress tick 17. This does not add an external scheduler or asynchronous runner capability.

2. **P2 — checkpoint audit repaired state before checking it (fixed).** Native permissive load followed by strict reload does **not** change any value for the accepted official hyperparam_2 checkpoint: measured 116 unchanged state tensors, identical rewards and identical schedule. But perturbing one native-loaded tensor by +1 was silently repaired by the audit and accepted. A different compatible checkpoint appearing between reads could similarly replace native-loaded values. The external audit now only checks complete keys, shapes, finite values, dtype and exact tensor equality, never calls `load_state_dict`, and rejects differences without repair. Hash is calculated from the same bytes that were audited; construction also checks the pre-load hash against the audited hash. Existing incompatible model_0 and hyperparam_0 files remain rejected before scheduling.

3. **P2 — terminal coincident geometry was rejected unnecessarily (fixed).** A DONE single task with its robot at the same location was rejected by the zero relative-distance guard, despite the original scheduler taking its end-sentinel branch without neural inference. The guard now permits all-DONE terminal input; a regression verifies the original end output. Normal neural inference still rejects undefined all-coincident distance normalization.

4. **P2 — shared factory keyword replan fails (not changed).** `benchmark/native_algorithms.py:51-52` uses `lambda *args` and `_run` also accepts positional arguments only. `verified.replan(task=task, robots=robots, state=state)` raises TypeError although the provider accepts those arguments. TaskRunner calls positionally, so the tested runner path works. Suggested owner change: preserve `**kwargs` through wrapper and `_run`. No synthetic lifecycle callback is needed.

5. **P2 — shared factory masks adapter source-root environment configuration (not changed).** `benchmark/native_algorithms.py:97-105` always chooses/passes a nonempty cache root when source_root is omitted. Consequently `HETEROCOORD_SADCHER_ROOT` is never consulted by the provider in the unified factory/CLI path. Reproduced with a deliberately missing environment path: factory still constructed from the cache. Explicit `--source-root` works and all current evidence uses it. Owner should either resolve supported per-method env before verification or document explicit/cache-only semantics for the factory. This is a configuration inconsistency, not evidence that the tested explicit-root runs used wrong source.

6. **P1 boundary if advertised as full replay — synchronous batch timing is not original event-driven timing (unchanged, already disclosed).** Upstream can replan at tick 19 when t0 finishes; TaskRunner's test batch waits until t1 completes at 24. Thus native per-state decisions match, but batch-host exit makespan is 59 versus original event-driven 51. No full rollout equivalence or native event timing certification should be claimed.

## Native matcher/filter preservation

The executed call trace is in `after_fix.json`: original `SadcherScheduler.calculate_robot_assignment` -> original `CachedBipartiteMatcher.solve(n_threads=6, gap=0.0)` -> original `filter_redundant_assignments` -> original `filter_overassignments`. No copied solver, alternative objective, additional filter, scipy path or changed filter order exists. `filter_unqualified_assignments` is present upstream but not called by original scheduler; the adapter correctly does not add it.

Adapter creates a fresh scheduler/matcher per call because it rebuilds the simulation snapshot. This discards cache reuse, changes computational cost and consumes global Torch RNG during model construction, but the deterministic accepted model overwrites all state, uses eval mode and nonstochastic `sample_rewards`. Tested snapshots have exact reward and assignment equality. This does not prove every MILP tie is resolved identically under every solver/environment; no broad timing or stochastic-equivalence claim is made.

Original matcher does not explicitly assert solver optimal status before reading variables, and original end handling counts PENDING tasks (excluding IN_PROGRESS). These are inherited source semantics, not adapter fixes or additional correctness guarantees. We have not patched them.

## Factory/lifecycle conclusion and actual scope

No critical policy-substitution/lifecycle error was found in SADCHER's exercised explicit-root, positional-call path. Current factory exposes `replan` only if provided, checks source before/after successful/error callbacks, and keeps AlgorithmDecision metadata. SADCHER has no initialize/update_observation method; none is fabricated. EMOS's existing initialize/update_observation return request state rather than AlgorithmDecision, so they cannot simply be routed through the decision-only `_run` wrapper; this review did not execute EMOS or certify other five methods.

Checkpoint completeness proves loaded-state compatibility, not training provenance of arbitrary external weights. Explicit custom checkpoints are allowed and their hash is recorded; only the frozen official hyperparam_2 file is covered by positive native evidence here. Skill labels are explicit channel mappings, not a claim of retraining or validated semantic transfer.

Coalitions remain fully retained in NativeCoalitionScopeError and cannot be dispatched by the single-owner runner. Idle/end remain provenance and require a host to interpret native environmental behavior. UR5 exact requested input remains idle and has no dispatch. Default model_0 is rejected. Evidence is native decision parity and bounded upstream 2D execution; **no EAI/Isaac physical pass**.

## Verification and files

**32 passed, 0 failed/skipped**, CPU only: `pytest.txt`, `pytest.xml`. `before_fix.json` contains reproductions; `after_fix.json` contains call trace, checkpoint audit and source receipt. The combined suite also reran actual unified CLI, coalition/default rejection, UR5 idle and persistent native feedback tests under `review/unified_entry/` without overwriting prior evidence.

Changed only:

- `/home/airs/hetrocoord/algorithm/system/coordination/sadcher/native_adapter.py`
- `/home/airs/hetrocoord/algorithm/system/coordination/sadcher/tests/test_native_adapter.py`
- `/home/airs/hetrocoord/records/algorithm_adapters/sadcher/review/` artifacts

Earlier report/test counts remain historical snapshots. No upstream/shared edits, GPU jobs, commits or pushes. A network cross-check of the pinned raw GitHub file returned 503; source findings use the locally verified frozen Git checkout and executed code instead.
