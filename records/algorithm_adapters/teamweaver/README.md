# TeamWeaver original native solver adapter — 2026-10-28

Implemented and exercised: `NativeTeamWeaverAdapter.__call__(task, robots)` invokes the official **unmodified** `class_def.RTA_task.RTA.solve_miqp_phase_aware`, through CVXPY and Gurobi. The native decision and TaskRunner roundtrip run successfully. This is **supplied-decomposition allocation**, not full TeamWeaver semantic planning or a full paper reproduction. The existing in-tree `eai_adapter` is a separate rewritten implementation and is neither imported nor modified by this adapter.

## Integration

```python
from algorithm.system.coordination.TeamWeaver.native_adapter import NativeTeamWeaverAdapter

provider = NativeTeamWeaverAdapter(
    source_root="/home/airs/.cache/heterocoord/upstream/teamweaver",
    python_executable="/home/airs/.cache/heterocoord/envs/teamweaver-native/bin/python",
    scope="supplied_decomposition",
    timeout_s=60,
)
decision = provider(task, robots)  # EaiTaskPackage, Sequence[RobotState] -> AlgorithmDecision
```

The source argument is optional: `HETEROCOORD_TEAMWEAVER_SOURCE_ROOT`, then `/home/airs/.cache/heterocoord/upstream/teamweaver`. The interpreter argument is optional: `HETEROCOORD_TEAMWEAVER_PYTHON`, then the above cached isolated interpreter if present, then `sys.executable`. Full semantic preparation uses `semantic_python_executable`, then `HETEROCOORD_TEAMWEAVER_SEMANTIC_PYTHON`, then `/home/airs/.cache/heterocoord/envs/teamweaver-semantic/bin/python`. Set interpreters explicitly in portable integrations. No `initialize(H_r, o_r)`, `update_observation`, or `replan` is exposed: no native state reconstruction or semantic feedback mapping is claimed. Each call uses the supplied runtime roster and positions in a fresh process.

`NativeScopeError.native_result` and `provider.last_native_result` preserve complete native output for a coalition that cannot be dispatched. `NativeSolverError.native_result` preserves non-optimal solver output. There is no Hungarian, greedy, LP, or rewritten-allocator fallback.

## Source and integrity

- Actual supplied official repository: `/tmp/tw-1793068059`.
- Isolated clone: `/home/airs/.cache/heterocoord/upstream/teamweaver`; created with `git clone --no-hardlinks` from that repository.
- Frozen commit: `edf4619e4730860b96eebaac39620df4bbdc486a`.
- Git tree: `1f8ab1c1c9e41cec7c6bb7296cf90b1ffeba1d29`.
- Shared `verify_source` receipt: 390 code/config/document files, aggregate SHA-256 `6e43e80bcad2142bfeaf51eac00e81d7962f799386af123005709792ad875953`.
- The actual planner path is `TeamWeaver/planner/llm_planner_comparison/llm_planner_miqp.py`. The root-level `planner/llm_planner_miqp.py` mentioned in the request does not exist at this commit.
- `verify_source(root, EXPECTED_COMMIT)` checks before and after native calls. `native_receipt.json` includes both receipts and individual hashes of the solver, configuration, wrapper, phase manager, planner and prompt files.
- Both upstream checkouts remain clean. No source statements, package initializers, prompts, solver options, utility functions, objective terms or constraints were edited or monkeypatched.
- Allocation uses the original `class_def`, `params_module`, `task_module`, and `sys_module` namespaces by adding the official HRCS directory to the isolated child's import path, as the standalone original HRCS code does. It does not need fake `habitat_llm` modules.
- The semantic worker builds a temporary `habitat_llm` namespace using symlinks to actual TeamWeaver files, supplemented only for absent files by official PARTNR. All original package initializers execute; there are no synthetic packages, skipped statements or source patches. PARTNR provides the omitted `agent/env` package. Both source dependencies are checked with `verify_source` before/after.

## Input/output mapping and scope

| EAI input | Original native input | Boundary |
|---|---|---|
| Active robot IDs in runtime order | Native robot indices | The complete runtime/package ID roster must match. Inactive robots are omitted from the native roster. |
| `RobotState.position` plus required `metadata.yaw_rad` | State columns `(x, y, yaw)` | Planar projection; EAI z is never treated as heading. Native differential-drive functions remain unchanged. |
| Capability names and task requirements | Binary `A`, identity `Hs`/`ws`, instance `T` | External boolean input vocabulary replaces scenario-example feature data. Original mapping computation and hard capability inequalities run unchanged. No task capability may be empty. |
| `metadata.native_task_type` | Existing native utility name | Supported mappings: `Navigate`, `Pick`, `Place`, `Open`, `Close`. No invented utility for arbitrary EAI primitives. |
| `metadata.target_position` or `target_xy` | `p_goal`, `target_object_position`, `target_receptacle_position`, `target_furniture_position` | Distinct simultaneous targets sharing one native global key are rejected, because the upstream instance solver reads shared globals. |
| `package.metadata.teamweaver_global_vars` | Original utility state and optional scalar parameters | Pick/Place require explicit `is_holding` and robot-name `holding_robot_id` (or null); Open/Close require `furniture_open_state`. Owner names map to active native indices. Unknown fields are rejected. Defaults for utility thresholds and orientation parameters remain native unless supplied explicitly. |
| Supplied graph and `package.metadata.completed_task_ids` | Current ready frontier | This is input filtering from externally supplied decomposition/completion facts, **not** native phase construction or native replanning. Unknown or non-dependency-closed completion records fail. |
| `package.metadata.time_s` | Native `t` (default 0) | Finite scalar only. |
| Native flattened `alpha` | Full robot-by-task matrix, then EAI Assignment | Binary shape/invariants checked. Any task with multiple assigned robots raises a scope error preserving the whole matrix. No coalition member is selected or dropped. |
| Native `u`, `delta`, bounds, status | Decision provenance | Diagnostics only; controls are not sent to physical robots. |

The adapter obtains native robot-count bounds from `OptimizationConfigTask.get_instance_robot_bounds`; it does **not** change them to force one owner. Original equality constraints require each active robot to receive one task. Consequently, excess robots can produce a coalition; too many required tasks or absent capabilities can make the problem infeasible. Those are explicit failures, not occasions to inject idle tasks, invent another phase, or substitute an allocator.

The optional aptitude term is absent (`aptitude_matrix=None`): no native LLM/task-helper resumes were generated from the supplied package. Source objective code and weights are intact; the report does not claim that omitted semantic/resume components ran. Load, deadlines, duration, obstacle geometry and other application metadata have no native mapping here.

The upstream phase-aware CBF construction evaluates each robot/task pair and reads shared utility globals. The adapter does not repair this behavior. It rejects conflicting target inputs before invocation and rejects native diagnostic errors or non-optimal status after invocation. Solver input and all raw outputs are retained in successful decision provenance.

## Verification results

The final focused test run is recorded in `test_results.xml` (28 tests). Coverage includes:

- Genuine native optimal solve with heterogeneous capabilities: `move_mobile -> mobile`, `move_sensor -> sensor`, `alpha=[[1,0],[0,1]]`.
- Actual original utility/MIQP execution for each of the five declared primitives; these are mathematical decision probes, not physical primitive execution.
- `TaskRunner().run(...)` with the actual provider: two assignments, two `not_executed` feedback records, verdict `N/A`, reason `physical_evidence_required`.
- Native coalition probe: two robots/one task returns `Optimal`, `alpha=[[1],[1]]`; adapter raises `NativeScopeError` and preserves the full result.
- Constructed infeasible probe: one robot lacking sensing/two required tasks. Upstream returns status `Exception` with zero arrays and an error diagnostic. The phase-aware failure path calls the analyzer using `self.constraints_`, while its constraints are held locally; the analyzer reads `self.constraints_['A_ineq']`. This upstream failure path is unchanged. No success or alternate assignment is reported.
- Supplied dependency frontier followed by supplied completion facts, runtime pose/heading mapping, strict invalid-input rejection and source immutability.

`native_receipt.json` stores the concrete decision, TaskRunner ledger, coalition and infeasible probes, hashes, versions, and semantic import diagnosis. `scoreable=false`; physical execution was not run.

## Full semantic progress and exact blocker

The current Python 3.9 semantic environment contains real Habitat-Sim 0.3.3 (headless Bullet), Habitat-Lab/Baselines, Torch CPU 2.4.1, torchvision 0.19.1, NumPy 1.26.4, CVXPY 1.7.5, Gurobi 12.0.3, Hydra, OpenAI 1.10.0, and their import dependencies. `python -I -m pip check` passes. Official dependencies:

- PARTNR: `ddfff19f4b6c098a31edea4d19e7b75db72433c2`, cache `.../upstream/teamweaver-partnr`.
- Habitat-Lab: `094d6be2f9d057e4781a68ae792132895fd4d3d0` (PARTNR pinned submodule), cache `.../upstream/teamweaver-habitat-lab`.
- `semantic_conda_explicit.txt` captures conda artifacts; `semantic_requirements.lock.txt` captures installed pip metadata, including conda build paths/editable roots (an environment snapshot, not a portable standalone pip requirements file). `semantic_versions.json` is the normalized package version inventory.

`prepare_full_semantic(task, robots)` now really constructs upstream `WorldGraph`, Room/Furniture/Object/agent entities, graph edges, two original `Agent` objects and original `MotorSkillTool` objects, and renders the unchanged native `few_shot_centralized_full_obs_multiagent.yaml` template using original tool descriptions. The original world-description function runs. The original PARTNR `EnvironmentInterface` class imports successfully. Preparation is a separate diagnostic operation, not an AlgorithmDecision.

Explicit input fields are `package.metadata.instruction`, `teamweaver_world_graph` (nodes/edges and `coordinate_frame="habitat_y_up"`), and each runtime robot's `metadata.teamweaver_position`. Runtime positions overwrite corresponding agent nodes in a copied graph; no implicit EAI/Habitat frame conversion is invented. Exactly two active robot roles are required. The optional `teamweaver_tool_names` selects original YAML stems. The current fixture is a supplied synthetic symbolic world, not a sensed physical environment or a claim that the fixture robots match all native role capabilities.

The first actual full-planner import failure is now **upstream source syntax**, not absent packages or credentials:

```text
TeamWeaver/planner/HRCS/plan_module/execution_manager.py:73
iflen(self.execution_history['actions']) > self.max_history_length:
SyntaxError: invalid syntax
```

The clean Git blob contains the same bytes. SHA-256: `f4a96b9eb35456865a9dfde3e487353d5d5fcfa79e2afa25d1fbb7e03d671f7a`. Both the planner package initializer and the requested enhanced planner import this ExecutionManager. No alternate import path bypasses it here. `upstream_syntax_audit.json` additionally records a second `iflen` syntax defect in `planner/miqp_planner/optimizer/adaptive_optimizer_v2.py:381`; that second file is not the current import blocker. Compilation diagnostics read original bytes without writing bytecode or correcting source.

`scope="full_semantic"` performs the real preparation/import and raises `NativeScopeError` with a detailed `native_result`; it does not return an allocator result under a semantic label. This fixed commit cannot execute its complete planner unchanged. A corrected, explicitly selected upstream revision would be needed before end-to-end native replan can be validated. The adapter has **not** implemented or validated a working full replan invocation behind this import failure.

## Real Dongli call and unified evidence

User-authorized environment configuration is `DONGLI_API_KEY`, `DONGLI_BASE_URL`, `DONGLI_MODEL=gpt-5.6-luna`, `DONGLI_REASONING_EFFORT=low`. Source `~/.bashrc` in the launching shell; the adapter does not read credential files. Keys are never serialized into reports or command arguments. `live_llm_probe=True` explicitly enables the real, billable boundary probe; it is false by default.

The original `OpenAIChat` constructor and **unchanged** `OpenAIChat.generate` execute. An API transport adapter replaces the initialized Azure client with a real OpenAI-compatible SDK client at the authorized Dongli endpoint and adds only the authorized `reasoning_effort`. Model selection is API configuration. System prompt, user prompt, original generate behavior (including its local stop splitting), and `actions_parser` are unchanged. The original wrapper itself sends only model/messages; we do not silently add its unused sampling settings or force generated actions. The API transport does not replace a planner/solver or fake a response.

`unified_entry/full_semantic_report.json` is from the shared `python3 -m tools.algorithm_adapters.run` and shared verified factory, with a real Dongli response and original parser output preserved under `rejected_native_result.semantic_mapping.llm_boundary_probe`. The requested and returned model are `gpt-5.6-luna`, reasoning is `low`. The CLI exits **1**, status **not_passed**, because the enhanced planner still cannot import. Source provenance is retained by the shared wrapper. `semantic_live_preparation.json` is an earlier independent boundary call, not a full algorithm success.

LLM response receipt is not semantic correctness: the independent prompt may generate target names absent from the supplied graph. The report preserves unknown single-target entities and unavailable tools without repairing or dropping actions, and creates no Assignment. This boundary does not run MIQP-guided prompt construction, decomposition, phase handling or native action validation/recovery. It cannot stand in for the complete algorithm.

Remaining mapping work after a corrected upstream becomes available:

- Construct and bind a real Habitat environment or a clearly identified EAI planning I/O interface; the actual `EnvironmentInterface` constructor requires real simulator wrappers. It was imported but **not instantiated**.
- Bind real motor skills and validate tool grammar; current native tools expose descriptions only and are never executed with dry-run success feedback.
- Execute native LLM decomposition, resume/phase/matrix construction, MIQP, and `replan`, preserving all original prompt/solver statements. No lifecycle methods (`initialize`, `update_observation`, `replan`) are fabricated on this provider.
- Strictly map generated task catalog/actions to EAI and reject unsupported coalitions, ambiguous task IDs and unknown generated actions with raw output intact. No successful semantic Assignment mapping is claimed yet.
- Physical TaskRunner/EAI execution and scoring remain unverified.

The available credentials remove the previous credential blocker. The successful live boundary call does not erase the prior source-syntax blocker or establish a physical/full-semantic result.

## Output seam and source-repair boundary

`output_seam=True` is an opt-in, reject-only I/O boundary after the original TeamWeaver `actions_parser`. It adds an explicit task/action catalog and validates exact agent directives, entity identifiers, tool names, readiness, capabilities, duplicate directives and coalition dispatchability. It preserves the raw response and parser tuples. It never repairs an action, selects one coalition owner, creates an `Assignment`, reports execution success or replaces HRCS/MIQP/planner logic. `output_seam_receipt.json` records this boundary result.

The upstream syntax repair is advisory only. `syntax_repair_advisory.json` records the two minimal hypothetical replacements of `iflen(` with `if len(`, original and hypothetical patched hashes, and unified diffs. `applied=false`, `executed=false`, and `source_lock_changed=false`; no patched source was imported or run. The locked official checkout remains the only source used for the native allocation path.

## Runnable commands

Shared factory/CLI evidence (one real Dongli call; full semantic exit 1 is expected):

```bash
bash /home/airs/hetrocoord/records/algorithm_adapters/teamweaver/unified_entry/run_commands.sh
```

From `/home/airs/hetrocoord`, with the verified source clone already present:

```bash
python3 records/algorithm_adapters/teamweaver/run_native.py \
  --source-root /home/airs/.cache/heterocoord/upstream/teamweaver \
  --python-executable /home/airs/.cache/heterocoord/envs/teamweaver-native/bin/python \
  --output records/algorithm_adapters/teamweaver/native_receipt.json

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /home/airs/.cache/heterocoord/envs/teamweaver-native/bin/python -m pytest -q \
  algorithm/system/coordination/TeamWeaver/tests/test_native_adapter.py \
  --junitxml=records/algorithm_adapters/teamweaver/test_results.xml
```

The current original temporary checkout can also be passed with `--source-root /tmp/tw-1793068059`. To reproduce only the isolated dependency environment, use a new venv and the captured lock file:

```bash
python3 -m venv /path/to/teamweaver-env
/path/to/teamweaver-env/bin/python -I -m pip install \
  -r /home/airs/hetrocoord/records/algorithm_adapters/teamweaver/requirements.lock.txt
```

The tested environment uses Python 3.10, NumPy 2.2.6, CVXPY 1.7.5 and Gurobi 13.0.3. Successful small-instance solving does not establish large-instance licensing or scalability. The parent task owns source bootstrap/registry integration; shared files were only read.

## Files owned and created by this task

1. `algorithm/system/coordination/TeamWeaver/native_adapter.py`
2. `algorithm/system/coordination/TeamWeaver/tests/test_native_adapter.py`
3. `records/algorithm_adapters/teamweaver/run_native.py`
4. `records/algorithm_adapters/teamweaver/README.md`
5. `records/algorithm_adapters/teamweaver/requirements.lock.txt`
6. `records/algorithm_adapters/teamweaver/native_receipt.json`
7. `records/algorithm_adapters/teamweaver/test_results.xml`
8. `records/algorithm_adapters/teamweaver/.gitignore` (allow local JSON receipts).
9. `records/algorithm_adapters/teamweaver/semantic_preparation.json` (offline preparation receipt).
10. `records/algorithm_adapters/teamweaver/semantic_requirements.lock.txt`, `semantic_conda_explicit.txt`, `semantic_versions.json` (environment inventories).
11. `records/algorithm_adapters/teamweaver/upstream_syntax_audit.json`.
12. `records/algorithm_adapters/teamweaver/unified_entry/`: three task/config inputs, allocator/full-semantic reports, earlier live preparation receipt, and `run_commands.sh`.

No existing `eai_adapter` implementation, benchmark adapter, registry, source lock or shared `native_runtime.py` was edited. No commit, push, service restart or physical simulator run was performed.
