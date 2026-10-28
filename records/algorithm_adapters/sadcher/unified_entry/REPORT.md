# SADCHER unified entry and feedback evidence — 2026-10-28

Executed the real shared `benchmark.native_algorithms.build_native_adapter` and `python -m tools.algorithm_adapters.run` on CPU. No shared entrypoint, shared report, registry, original policy, adapter implementation, upstream source or GPU execution was changed in this follow-up. Added only the SADCHER integration test and method-specific evidence.

## Factory + CLI results

All cases use frozen upstream `a6b5363a4ab9bccb540f0ecf7f10709b8c7911ae`; successful decisions contain the factory's independent source-integrity receipt. Positive cases explicitly select the fully compatible official `hyperparam_2_8t3r3s/best_checkpoint.pt` (116/116 states); no checkpoint substitution occurs.

| Case | CLI exit/status | Native result | Actual executor dispatches |
|---|---|---|---:|
| `normal` | 0 / `native_decision_returned` | `t0->r0`, `t1->r1`, `r2->idle`; `t2` waits on `t0` | 0: CLI is decision-only |
| `ur5_idle` | 0 / `native_decision_returned` | `ur5_1->idle`, `pick_place` unassigned | 0 |
| `coalition_rejected` | 1 / `not_passed` | `NativeCoalitionScopeError`, complete `r0+r1->t0` retained by factory test | 0 |
| `default_checkpoint_rejected` | 1 / `not_passed` | `CheckpointCompatibilityError`; original requested model_0 is incompatible | 0 |

Each case has `.task.json`, `.config.json`, `.factory.json`, `.cli.json`, `.command.json`, `.cli.stdout.txt` and `.cli.stderr.txt`. Factory and CLI assignments/raw mappings are compared directly. CLI status `native_decision_returned` is not interpreted as work completion or physical acceptance. Reports retain `physical_success: null` and `simulation_acceptance_passed: false`.

The UR5 inputs are unchanged: one `ur5_1` with capability `manipulate`, robot `(0,0,0)`, task `(0.45,0.05,0)`, duration 30, start/end `(0,0,0)`, skill order `manipulate,inspect,transport`. No parameters were searched or changed to induce an assignment.

## run_orchestrated feedback loop

`run_orchestrated_native_feedback.json` records a real call to `TaskRunner.run_orchestrated`, using the shared verified factory provider and its wrapped `replan`, an uninterrupted original 2D `Simulation`, and `WorldSnapshot` observations of live native robot positions/current tasks. Completion feedback is produced only after original `Simulation.step()` reports task DONE. No mocked completion or time reset is used.

- Initial native output at tick 0: `{0: 1, 1: 2, 2: 4}` (two tasks plus idle).
- First batch runs tick 0–24: `t0` completes at 19, `t1` at 24.
- Feedback + fresh observation trigger native replanning at tick 24: `{2: 3}`.
- Second batch runs tick 24–31: `t2` completes at 31.
- Runner terminates `all_dependencies_completed`: **3 dispatches, 2 batches, 3 completion feedback records**, all task states completed. Evaluator/physical verdict remains **N/A**.
- After runner completion, a separately labelled native environment tail sends all three robots to the original end sentinel and reaches exit with native makespan **59**. This tail is not a fourth EAI dispatch.

At each of the two dispatch decisions and the separate end decision, independent original scheduler execution on an identical native state produces **exactly equal reward tensors, feature tensors and assignments**. Source verification wraps all three provider calls. Idle and sentinel actions are applied through original `assign_tasks_to_robots`; no selected work assignment is omitted or replaced.

**Cadence limitation:** the current synchronous TaskRunner batch executor waits for all tasks in the batch before replanning. Upstream's original event-driven rollout can replan when the first robot becomes available. Therefore this run's makespan 59 is not asserted equivalent to the earlier unmodified event-driven rollout's makespan 51. Per-observed-state native algorithm parity and complete feedback flow are verified; event-by-event scheduling equivalence remains unsupported by this batch interface. Shared code was not altered to conceal that difference.

`ur5_idle.orchestrated.json` additionally proves four native decisions (initial plus three bounded replans) remain idle: **0 dispatches, 0 feedback, task pending, termination `max_rounds_reached`**. No native or physical task success is claimed. A separate run_orchestrated coalition test raises the explicit scope error before executor use, retaining both members.

## Validation and reproduction

Combined tests: **28 passed** (21 existing native-adapter tests + 7 new unified-entry/feedback tests). `pytest.txt` and `pytest.xml` retain actual results; `receipt.json` contains case outcomes and hashes of the shared entrypoints consumed. PuLP warnings are upstream API deprecation warnings, not solver fallback.

```bash
cd /home/airs/hetrocoord
CUDA_VISIBLE_DEVICES='' OMP_NUM_THREADS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
HETEROCOORD_SADCHER_TEST_ROOT=/tmp/heterocoord-algorithms-20261028/sadcher \
HETEROCOORD_SADCHER_EVIDENCE_DIR=/home/airs/hetrocoord/records/algorithm_adapters/sadcher \
/home/airs/.cache/heterocoord/envs/sadcher/bin/python -m pytest \
algorithm/system/coordination/sadcher/tests/test_native_adapter.py \
algorithm/system/coordination/sadcher/tests/test_unified_entry.py -q
```

Actual CLI invocation for the normal case (replace `normal` with `ur5_idle` for the exact unchanged idle case):

```bash
cd /home/airs/hetrocoord
CUDA_VISIBLE_DEVICES='' OMP_NUM_THREADS=1 \
/home/airs/.cache/heterocoord/envs/sadcher/bin/python -m tools.algorithm_adapters.run \
--algorithm sadcher --source-root /tmp/heterocoord-algorithms-20261028/sadcher \
--task /home/airs/hetrocoord/records/algorithm_adapters/sadcher/unified_entry/normal.task.json \
--config /home/airs/hetrocoord/records/algorithm_adapters/sadcher/unified_entry/normal.config.json \
--report /home/airs/hetrocoord/records/algorithm_adapters/sadcher/unified_entry/normal.cli.json
```

Changed code: `/home/airs/hetrocoord/algorithm/system/coordination/sadcher/tests/test_unified_entry.py`. Evidence: `/home/airs/hetrocoord/records/algorithm_adapters/sadcher/unified_entry/`. Existing method evidence JSON was refreshed by the combined run; the shared summary report remains owner-managed. No commits or pushes.
