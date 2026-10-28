# EMOS native discussion adapter — 2026-10-28

Implemented `algorithm/system/coordination/emos/native_adapter.py` and
`algorithm/system/coordination/emos/tests/test_native_adapter.py`, plus the live-only
JSON/CLI helper `tools/algorithm_adapters/emos_config.py`. The new test was moved
out of root `tests/`; other existing tests were not edited. This directory contains the audit, probe and test
receipts. No upstream core, in-tree brain, shared source verifier, registry, or
benchmark adapter was edited by this work. The parent independently validates
the frozen source and owns integration.

**Result: 67 native adapter/configuration protocol tests + 8 existing EMOS benchmark tests =
75 passed, 0 skipped. Live model / algorithm acceptance: not passed.** The live
probe found no EMOS/OpenAI credential in the existing process environment and
closed with exit 2 before any network request. No keys were requested or printed.
Physical acceptance and decentralized action execution were not evaluated.

Integration note: the relocated package test is visible to Git through the
parent-owned ignore exceptions. JSON examples/receipts may still need explicit
force-add when publishing. No ignore rules or staging state were changed here.

## Actual source audit

Tested checkout: `/tmp/emos-1793068059`, branch `embodied_mas`, commit
`e9501db45d634b087bf5d1a14228266685e8feeb`; no tracked modifications. The current
`benchmark/emos.py` exactly matches `/tmp/hetrocoord-origin-20261028/benchmark/emos.py`.
See `source_audit.json` for hashes, line references and dependency versions.

| Boundary | Actual upstream | Current in-tree implementation |
| --- | --- | --- |
| Discussion | `habitat-baselines/habitat_baselines/rl/multi_agent/multi_llm_policy.py:227` | `brain/MultiLLM_discussion.py:691`; all 9 shared functions differ by AST |
| Prompts | Original English leader and robot prompts; numerical reasoning enabled by default | Chinese prompts, Radxa-specific instructions, format retries, optional leader self-reflection |
| Model loop | `habitat-mas/habitat_mas/utils/models.py:OpenAIModel`; default `gpt-4o` | `brain/API/Model_API.py:DeepSeekModel`, provider/stream/gate changes |
| Output | Original `AgentArguments`, including free-text subtasks and per-robot history | Scenario-specific parse/sanitize/fix path and heuristic fallback in `assignment.py` / `engine.py` |
| External feedback | Stage-one discussion has no execution feedback/history-restoration parameter; stage-two `CrabAgent.chat(observation)` is separate | Manager has additional application-specific rescue replanning/fallback behavior |

The adapter loads the upstream nine function AST nodes and six literal constants
without rewriting their bodies, prompts or parsing logic. This isolates planner
components from Habitat's Gym/Torch/simulator policy imports. It imports the real
upstream `AgentArguments`, `OpenAIModel`, `crab_core` and `SubprocessInterpreter`
under a private package name. Only the model's module-local OpenAI transport
constructor is connected to `MeteredNativeEndpoint`; the global SDK is unchanged.
Model selection and request budgets are explicit IO configuration.

Original flags/defaults are retained: discussion, robot reflection, resume,
numerical reasoning, and three refinement rounds. Upstream's logging-disabled
branch references an unbound `episode_save_dir`, so the adapter uses its supported
logging-enabled path. Upstream can return its final allocation after three rounds
even when a robot still responds `no`; that behavior and its history are preserved,
and are not turned into feasibility or success evidence. Native numerical code
execution uses the original interpreter and requires `python` on PATH.

## Integration contract

Public entry point: `EmosNativeAdapter(request, *, robot_resume, endpoint=None,
source_root=None, log_dir, output_bindings=None, initialization_budget_calls=1)`.
Requests are the latest `benchmark.emos.CaseAssignmentRequest` or the existing
Fire Rescue `AssignmentRequest`.

```python
from algorithm.system.coordination.emos.native_adapter import (
    EmosNativeAdapter, MeteredNativeEndpoint, NativeOutputScopeError,
)

endpoint = MeteredNativeEndpoint.from_environment(max_calls=32, timeout=30)
adapter = EmosNativeAdapter(
    request,
    robot_resume=native_robot_resumes,       # explicit upstream-format capabilities
    endpoint=endpoint,
    source_root="/tmp/emos-1793068059",      # parent verifies frozen source
    log_dir="/home/airs/hetrocoord/records/algorithm_adapters/emos/run_logs",
)
adapter.initialize(H_r, {
    "robot_positions": public_robot_positions,
    "scene_description": native_scene_text,
})
try:
    decision = adapter(task_package, task_package.robots)  # AlgorithmDecision
finally:
    adapter.close()
```

`from_environment` reads existing `EMOS_API_KEY` / `OPENAI_API_KEY`,
`EMOS_BASE_URL` / `OPENAI_BASE_URL`, and `EMOS_MODEL` / `OPENAI_MODEL`; it never
loads credential files. The original model default is `gpt-4o`. Missing endpoint,
source, dependencies, invalid outputs, exhausted budgets and provider errors all
fail closed. SDK retries are disabled for the environment-created client; custom
clients must likewise disable internal retries for exact network-call accounting.
Every native completion is metered, including numerical continuations and
refinement rounds. `initialize` itself is data-only and makes no model call.

`o_r.scene_description` is required on initialization and every observation
update. The caller supplies a fresh native scene description with the upstream
`[x,z,y]` coordinate convention (height second) and compatible resumes; the adapter
validates public robot positions but does not invent, transform or append scene
facts. `update_observation` updates this input; the next call starts a fresh native
discussion, not an invented stateful replan. `history` exposes copied evaluator
history; `native_history` and `native_results` expose original discussion outputs.
Explicit external chat-history restoration and `feedback(...)` are unsupported
and raise `NotImplementedError`. There is no `replan` method.

Native free text is mapped only by exact task ID, unique exact task description,
or a caller-declared `output_bindings` vocabulary fixed before inference. No fuzzy
matching, whitespace repair, task filling, capability-based reassignment or
fallback is used. `Nothing to do` stays idle. One native combined subtask may map
to multiple explicit task IDs. When two robots map to the same task, the current
TaskRunner cannot represent the coalition: `NativeOutputScopeError` preserves all
`native_subtasks`, and `adapter.native_results` retains every original output.
No robot is silently selected. Unknown/ambiguous text raises the same scope-error
type. Fire Rescue constraints are validated without fixing the native decision.

## Live-only JSON configuration entry

The parent can integrate:

```python
from tools.algorithm_adapters.emos_config import load_config, build_emos_from_config
config = load_config(config_path)
adapter = build_emos_from_config(config, source_root=verified_root,
                                 base_dir=config_path.parent)
# Already initialized. Parent supplies the EaiTaskPackage and source gate.
decision = adapter(task_package, task_package.robots)
```

Standalone command, runnable now (returns exit 2 / blocked until configured):

```bash
cd /home/airs/hetrocoord
/home/airs/miniconda3/envs/env_isaaclab/bin/python -m tools.algorithm_adapters.emos_config \
  --config records/algorithm_adapters/emos/config.example.json \
  --source-root /tmp/emos-1793068059 \
  --report records/algorithm_adapters/emos/cli_live_receipt.json
```

`config.example.json` contains a complete explicit case request, native robot
resumes, observation/scene text, history and task package. The separate
`config.fire_rescue.example.json` demonstrates `AssignmentRequest` with the native
scenario dataclass JSON shape (`PositionRule.kind`, `RobotProfileSpec.robot_type`).
Both are synthetic input examples, not robot capability or task-success evidence.
No source prompt or task description is rewritten to make output fit a task ID.

Endpoint configuration contains names only:

```json
"endpoint": {
  "api_key_env": "EMOS_API_KEY",
  "base_url_env": "EMOS_BASE_URL",
  "model_env": "EMOS_MODEL",
  "max_calls": 16,
  "timeout": 30
}
```

Use the names of existing provider variables if different (for example,
`DEEPSEEK_API_KEY` with the deployment's actual base-URL and model variable names).
All three referenced variables must be populated by the deployment environment;
the CLI does not auto-select another provider, load `.env`, solicit credentials,
accept inline secrets, or select a test client. Relative `log_dir`/`source_root`
paths resolve against the configuration directory; a CLI `--source-root` override
is recommended as an absolute path. Omitting source_root retains the adapter's
EMOS_SOURCE_ROOT/cache lookup. The CLI validates the frozen source using the
unchanged shared verifier before native invocation; the parent factory can do
its own check through `build_emos_from_config`.

Exit codes: 0 = native decision returned (still no algorithm/physical acceptance),
2 = unavailable configuration/dependencies/source directory, 1 = invalid config,
source integrity failure, native API/discussion failure or unrepresentable output.
Receipts include completion counts, failed attempts, tokens and original subtask
outputs even on coalition mapping failure. A native budget failure cannot yield
a successful decision. The CLI has no protocol/fallback mode; its deterministic
SDK doubles exist only in the tests and remain protocol evidence.

Current `cli_live_receipt.json`: frozen source verified, `blocked`, zero requests.
`endpoint_config_audit.json` records only variable names/presence and checked
configuration file presence. The checked environment has no EMOS/OpenAI/DeepSeek/
Zhipu key; no usable endpoint configuration was discovered in the scoped project
inspection. This does not claim the absence of credentials elsewhere on the host.

## Unified factory / CLI verification

The shared integration now accepts:

```python
build_native_adapter(
    "emos",
    source_root=verified_root,
    json_config=config,
    config_base_dir=config_path.parent,
)
```

and `tools/algorithm_adapters/run.py` selects this path when the JSON schema is
`heterocoord.emos_native_config.v1`. The adapter is initialized from the JSON
request, resume, history and observation before the first native call; endpoint
construction remains live-only and metered.

Actual unified invocation on October 28, 2026:

```bash
cd /home/airs/hetrocoord
/home/airs/miniconda3/envs/env_isaaclab/bin/python -m tools.algorithm_adapters.run \
  --algorithm emos \
  --task records/algorithm_adapters/emos/task.example.json \
  --config records/algorithm_adapters/emos/config.example.json \
  --source-root /tmp/emos-1793068059 \
  --report records/algorithm_adapters/emos/unified_run_blocked_receipt.json
```

The shared CLI reached the unified JSON factory and stopped before SDK client
construction with `NativeEmosUnavailable`: the configured `EMOS_API_KEY`,
`EMOS_BASE_URL` and `EMOS_MODEL` names were unset. Result: exit code 1,
`status=not_passed`, `model_requests=0`, `algorithm_acceptance=not_passed`.
The direct factory check independently recorded `status=blocked`, zero requests,
and an endpoint receipt with zero completion attempts in
`unified_integration_receipt.json`. No credential value was emitted.
The shared CLI's own report currently writes `not_passed` and the error type but
does not serialize request counts; the zero count above is recorded in the
companion integration receipt. The helper/adapter exception now exposes
`model_requests` and `endpoint_receipt` for the parent to copy into shared reports.

The focused unified integration test run was **5 passed, 67 deselected**. It used
an SDK transport double only to exercise the shared factory/CLI path. The tests
confirm that native API failures carry `model_requests`, `endpoint_receipt` and
`native_subtasks`; a coalition report is preserved by `run.py` under
`rejected_native_subtasks` and remains `not_passed`. The actual blocked run had no
native output to preserve because no request was made.

## Reproduction and receipts

```bash
cd /home/airs/hetrocoord
EMOS_SOURCE_ROOT=/tmp/emos-1793068059 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /home/airs/miniconda3/envs/env_isaaclab/bin/python -m pytest -q \
  algorithm/system/coordination/emos/tests/test_native_adapter.py benchmark/tests/test_emos.py \
  benchmark/tests/test_emos_case_integration.py \
  --junitxml=records/algorithm_adapters/emos/tests.xml

/home/airs/miniconda3/envs/env_isaaclab/bin/python \
  records/algorithm_adapters/emos/probe.py --mode protocol --source-root /tmp/emos-1793068059
/home/airs/miniconda3/envs/env_isaaclab/bin/python \
  records/algorithm_adapters/emos/probe.py --mode live --source-root /tmp/emos-1793068059
```

- `tests.xml`: 75 passed. The new tests execute original discussion, reflection,
  reassignment, native model chat and a real local numerical subprocess with a
  deterministic completion double. They also cover both request types, history,
  observation updates, idle output, coalition rejection, exact multi-task binding,
  unavailable inputs, metering, provider failure and no source/global-SDK edits.
  Configuration tests cover both request schemas, strict env references, rejected
  inline secrets/test clients, live-only CLI blocked/success/budget/coalition
  receipts with SDK doubles, and preservation of the input configuration.
- `protocol_probe.json`: one recorded synthetic probe, six scripted completions
  through the original loop, including a robot rejection and leader reassignment.
  `protocol_native_logs/` and `protocol_native_stdout.txt` retain native traces.
- `live_probe.json`: `blocked`, zero model requests, `algorithm_acceptance=not_passed`.
- `source_audit.json`: source identity, nine original/in-tree function comparisons,
  dependency versions, owned code hashes and unchanged benchmark snapshot check.

All deterministic results are protocol evidence only. The adapter is usable with
a configured native endpoint and caller-supplied native inputs, but this work does
not establish live EMOS task quality, complete Habitat-MAS reproduction, physical
success, causal replay, or scoreable benchmark acceptance.
