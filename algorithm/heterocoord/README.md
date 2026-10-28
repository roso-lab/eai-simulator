# HeteroCoord native algorithm integration

Current integration index: [2026-10-28 publication and acceptance boundaries](ADAPTATION_PUBLICATION_20261028.zh-CN.md). Historical test counts below must be interpreted with their run/version receipts.

The supplied `CVPR2026_Template.pdf` (SHA-256 in `paper_algorithms.json`)
has six result columns in Table 3 / Section J.1: **FMC-ATA, BT capabilities,
SADCHER, HARL:HAPPO, TeamWeaver, EMOS**. Gosrich, Qrainbow and COHERENT are
related-work records; RobotFleet and LaMMA-P are outside this result matrix.
HeteroMRTA is excluded by user request.

Use `benchmark.native_algorithms.build_native_adapter`. Each provider lives in
`algorithm/system/coordination/<method>/native_adapter.py`; official source lives
outside the project in `~/.cache/heterocoord/upstream/`. `paper_algorithms.json`
freezes repository, commit and paper identity. The integrity envelope checks
source before and after decisions, records hashes, and never repairs assignments.

```python
from benchmark.native_algorithms import build_native_adapter
from task.runner import TaskRunner
from task.contracts import TaskRunRequest

adapter = build_native_adapter("harl_happo", manifest_path="path/to/manifest.json")
result = TaskRunner(executor=eai_executor, evaluator=eai_evaluator).run(
    TaskRunRequest(task_package), adapter
)
```

The manifest must match the trained observation/action environment. A local
symbolic training checkpoint is a separate condition, not an official pretrained
policy or proof of Isaac task success. Each method has explicit input
requirements; unsupported domains are rejected rather than mapped heuristically.
For orchestration, pass `replan=adapter.replan` only if the provider actually
implements that lifecycle. Forward feedback through its documented mapping.

Reproduction and current acceptance are documented in `REPRODUCTION_PLAN.zh-CN.md`
and `NATIVE_ALGORITHM_ADAPTATION_REPORT_20261028.zh-CN.md`. Per-method receipts
are under `records/algorithm_adapters/`; BT runtime setup also lives with its
adapter. Registry membership does not establish runtime acceptance.

## Preserved boundaries

- Only task, observation, capability, history, action and feedback I/O may be
  adapted. Native prompts, solvers, networks and learned tensors remain intact.
- Strict checkpoint audits reject missing, extra, incompatible or nonfinite
  tensors. A missing dependency never enables a substitute solver.
- Native idle, failure, fractional allocation and coalitions are retained. The
  current single-owner TaskRunner rejects coalitions while retaining raw results.
- The EAI executor owns robot execution; the external evaluator owns physical
  success. Native decisions and symbolic tests do not establish Benchmark scores.

## Historical diagnostic code

`fire_rescue_bridge.py`, `benchmark.external_algorithms` and the in-tree
TeamWeaver `eai_adapter` remain for historical compatibility. Their rewritten or
fixed-default logic must not be reported as unchanged original algorithms.
The default fallback in `benchmark.emos` is also diagnostic; native EMOS requires
the explicitly configured original implementation and model endpoint.

## Paper implementation register

`PRIMARY_SYSTEM_COLUMNS` is the sole ordered-column API. Each registry row also
records source/code/model and adapter versions, cognition/training/runtime
configuration, supplied decomposition, task and disturbance scope, comparison
condition, information-rights group, adaptation components, actor/action
encoding, semantic/world models, solver, replanning, and budget accounting.
`validate_implementation_register()` rejects a missing field or primary-roster
drift. `primary_condition_record()` resolves the complete runtime system,
comparison-condition, and information-rights identity; arbitrary diagnostic
identities do not become paper-rankable.

The current register intentionally leaves unresolved information rights and
unqualified runtimes as `N/A`. A complete six-row structural roster can only
pass the formal baseline gate when each row supplies independent physical and
artifact qualification evidence; the registry itself never supplies it.


The formal condition register remains separate from native adapter acceptance; historical condition metadata is retained for interpreting earlier runs.
