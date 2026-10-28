# SADCHER native adapter evidence — 2026-10-28

The adapter executes the unchanged upstream `SadcherScheduler.calculate_robot_assignment`, including neural rewards, `CachedBipartiteMatcher.solve`, `filter_redundant_assignments`, `filter_overassignments`, and start/idle/end handling. It constructs actual upstream `Simulation`, `Task`, and `Robot` objects. No policy reimplementation, SciPy fallback, import stub, upstream modification, commit, or push was used.

## Integration

```python
from algorithm.system.coordination.sadcher.native_adapter import NativeSadcherAdapter

provider = NativeSadcherAdapter(
    source_root="/tmp/heterocoord-algorithms-20261028/sadcher",
    checkpoint_path="imitation_learning/checkpoints/hyperparam_2_8t3r3s/best_checkpoint.pt",
    skill_names=("terrain", "button", "sensor"),
)
decision = provider(task, robots)
```

Default source: `/home/airs/.cache/heterocoord/upstream/sadcher`; overrides: explicit `source_root`, `HETEROCOORD_SADCHER_ROOT`. Checkpoint override: explicit `checkpoint_path`, `HETEROCOORD_SADCHER_CHECKPOINT`; relative checkpoint paths are relative to the selected source. Parent independently wraps source verification; no shared registry was edited. A current `verify_source` receipt is saved in `checkpoint_audit.json`: upstream commit `a6b5363a4ab9bccb540f0ecf7f10709b8c7911ae`, 57 code/config/doc files, code digest `a1ab0f3a925758897639bed75b2da1e65bf26ca8052b3b8c13cbd13bedf7f60d`. The task interfaces were read from origin `769e31d8e4a0a72682d8eeaf6acf473a45419e7d`.

Required task metadata: `duration_s` (>0), `position` (2D or 3D on a common plane); optional `status` is `PENDING` (default), `IN_PROGRESS`, or `DONE`. `IN_PROGRESS` requires `remaining_duration_s` and a robot's explicit `current_task_id`. No duration is invented. Robot position is required; native speed is one coordinate unit/s. Optional boolean `available` may restrict availability; inactive robots cannot receive assignments. `current_task_id` also accepts `__start__`, `__idle__`, `__end__`.

Package metadata requires `sadcher.start_position` and `sadcher.end_position`. The upstream Simulation computes duration/location normalization, sentinel records and DAG adjacency. Unsupported skills, nonplanar geometry, invalid normalization, unsupported statuses and inconsistent ownership fail explicitly. Exactly three named binary skills are supported; names map to explicit ordered checkpoint channels, without claiming new training or domain validation.

`replan(task, robots, state)` translates executor-owned completed IDs into native `DONE` status and calls the original scheduler again. Live coordinates/current assignments must be supplied by the caller. Terminal failure/blocked states are unsupported and rejected. There is no fabricated `initialize(H_r,o_r)` or `update_observation`.

**Coalitions:** `NativeCoalitionScopeError` retains the full `AlgorithmDecision` in `.decision`, complete raw mapping in `.native_assignments`, and all robot members in `.decision.provenance['coalitions']`. Current TaskRunner rejects duplicate task assignments, so no coalition member is selected as a substitute. Idle/end remain explicit provenance, not fabricated work assignments. A zero-assignment decision is valid native behavior.

## Checkpoints

| Official checkpoint | Strict audit | Evidence |
|---|---|---|
| `8t3r3s_models/model_0/best_checkpoint.pt` (requested/default) | **Rejected** | Expected 116 keys; 184 provided; 16 missing, 84 unexpected, 86 shape mismatches; only 14 keys would match permissively. Checkpoint embedding 128 vs scheduler 256. |
| `hyperparam_0_8t3r3s/best_checkpoint.pt` | **Rejected** | 36 unexpected keys from an additional GAT layer. |
| `hyperparam_2_8t3r3s/best_checkpoint.pt` (explicit validation override) | **116/116 passed** | No missing/unexpected/mismatched/nonfinite keys. SHA256 `8c391003af62d9b99399563e85790e4af6b65162120c79917077dc02882bb10e`. |

There is **no automatic checkpoint replacement**. The default remains rejected. The unchanged upstream constructor contains a permissive loader; the external audit checks every key/shape/finite value, performs `strict=True` loading, and verifies exact tensor equality before allowing any native inference. Invalid construction never reaches scheduling.

## Executed evidence

- **Contract/native:** 21 tests passed. Independent original Simulation versus adapter has identical native assignments, reward tensors, feature tensors and DAG adjacency. Call profiling proves the original scheduler, matching solver and both filters ran.
- **TaskRunner:** a three-task DAG yielded `t0->r0`, `t1->r1`, and `r2->idle`; the upstream 2D simulator supplied real task status feedback through `TaskRunner`. After completion feedback and refreshed positions, a second native decision assigned `t2->r2`, exactly matching the independently stepped original simulator. Both TaskRunner verdicts remain `N/A` for physical evidence. Times in each reconstructed Simulation are local to that call, not global EAI timestamps.
- **Original simulation:** the original complete simulation loop finished all three tasks and exit, with makespan **51**, below worst-case limit 178.4978. All task statuses including sentinels were DONE and all robots reached exit. This is bounded upstream 2D simulation only.
- **UR5 requested physical input:** tested one `ur5_1` robot with `manipulate`, position `(0,0,0)`; one manipulate task at `(0.45,0.05,0)`, duration 30; start/end `(0,0,0)`; ordered skills `('manipulate','inspect','transport')`. Independent native execution and adapter both return `{0: 2}` = **idle**. Real-task reward 1.5554153, idle reward 1.8599234. No assignment or physical dispatch is claimed, and the policy was not modified. Physical host remains parent-owned.
- **Physical/EAI/Isaac:** **not executed here**, physical success **N/A**. Native passing checks do not turn the default incompatible checkpoint or the UR5 idle result into successful task execution.

Artifacts: `checkpoint_audit.json`, `native_parity_and_feedback.json`, `original_simulation_rollout.json`, `ur5_pickplace_native_decision.json`, `environment.json`, `pytest.txt`, `pytest.xml` in this directory. Python source hashes were unchanged before/after execution; upstream git remained clean.

## Reproduce

Isolated environment: `/home/airs/.cache/heterocoord/envs/sadcher` with inherited system packages; installed PuLP 3.3.2 and torch-geometric 2.8.1 (the latter is not required by the imported scheduler path). Tests ran on CPU with inherited Torch 2.9.1+cu128, Python 3.10, NumPy 1.23.5. Upstream declares Python >=3.11,<3.13 and Torch 2.4.0; this environment deviation is explicitly recorded, not represented as exact upstream dependency reproduction. PuLP emitted deprecation warnings; no solver fallback was used.

```bash
cd /home/airs/hetrocoord
CUDA_VISIBLE_DEVICES='' OMP_NUM_THREADS=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
HETEROCOORD_SADCHER_TEST_ROOT=/tmp/heterocoord-algorithms-20261028/sadcher \
HETEROCOORD_SADCHER_EVIDENCE_DIR=/home/airs/hetrocoord/records/algorithm_adapters/sadcher \
/home/airs/.cache/heterocoord/envs/sadcher/bin/python -m pytest \
algorithm/system/coordination/sadcher/tests/test_native_adapter.py -q
```

## Changed source files

- `/home/airs/hetrocoord/algorithm/system/coordination/sadcher/native_adapter.py` — new native I/O adapter and strict checkpoint/scope gates.
- `/home/airs/hetrocoord/algorithm/system/coordination/sadcher/tests/test_native_adapter.py` — executed native parity, feedback, simulation, failure and UR5 tests.
- `/home/airs/hetrocoord/algorithm/system/coordination/sadcher/fire_rescue_policy.py` — legacy incomplete request now fails closed; removed unfaithful inference/matching fallback.
- `/home/airs/hetrocoord/algorithm/system/coordination/sadcher/benchmark_condition.py` — legacy condition explicitly marked disabled; durable default source path.
- `/home/airs/hetrocoord/algorithm/system/coordination/sadcher/__init__.py` — exports native adapter and coalition scope exception.

Shared registry, native_runtime, frozen manifest, `benchmark/external_algorithms.py`, upstream source and unrelated dirty files were not edited.
