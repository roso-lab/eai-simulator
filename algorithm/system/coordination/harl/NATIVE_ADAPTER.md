# Native HARL/HAPPO checkpoint boundaries

Entry point: `algorithm.system.coordination.harl.native_adapter.NativeHAPPOAdapter`
(`NativeHARLAdapter` is an alias).

```python
adapter = NativeHAPPOAdapter(manifest_path, source_root=upstream_checkout, device="cpu")
decision = adapter(task_package, robot_states)
adapter.update_feedback(executor_feedback, terminal=False)
# Explicitly reset before another episode or replay branch:
adapter.reset()
```

`manifest_path` defaults to `HETEROCOORD_HARL_HAPPO_MANIFEST`. `source_root`
defaults to `HETEROCOORD_HARL_SOURCE_ROOT`, then
`/home/airs/.cache/heterocoord/upstream/harl`. Current tests default to that
permanent cache. The original training run used the pinned `/tmp` checkout.
The parent source-integrity
gate must independently verify the source. This adapter checks revision and
that loaded HARL modules reside under the selected root.

The sole supported v1 contract is `heterocoord.happo.two_actor_macro.v1`, declared
in `native_adapter.CONTRACT`. It binds actor 0 to robot `ground` and task
`inspect_ground`, actor 1 to `air` and `inspect_air`, with their distinct required
capabilities. Input robot order is irrelevant. Each actor has two actions:
wait or dispatch its own inspection. Wait is preserved as an empty assignment;
there is no fallback assignment or competing-actor tie breaker. Arbitrary
graphs, coalitions, SMAC observations, Fire Rescue, and changed actor rosters
are rejected. No generic `initialize`, `update_observation`, or `replan` API is
invented. The callable itself encodes the current observation.

Actor input is exactly nine float32 features: active, own completed, peer
completed, robot x/y, target x/y, last feedback completed, and step/4. Coordinates
must already be in the declared unit-square domain; there is no padding,
truncation, or inferred normalization. The package explicitly contains this
contract and an episode ID. Action masks permit wait and suppress dispatch
only when inactive or already complete. The centralized training critic gets
the exact concatenated 18-feature state.

Each actor uses original `HAPPO.act(..., deterministic=True)`. Recurrent tensors
returned by that call become the next call's inputs. First-call masks are zero;
later masks are one. Every call, including all-wait decisions, must receive
`update_feedback` before another call. Feedback outcomes are `macro_completed`
or `macro_failed`; this contract does not infer completion from physical data.
Changing episodes, reaching the four-step horizon, or terminal feedback requires
an explicit reset. One adapter instance belongs to one episode stream.

The manifest binds exact contract, actor identities, inference configuration,
source revision, actor checkpoint SHA-256 hashes, and a training-receipt hash.
All keys, tensor shapes/dtypes, finite values, trained tensor fingerprints, and
strict state loading must match. The receipt records original method identity,
positive optimizer steps, and changed full-network hashes. It is an auditable
local receipt, not a cryptographically signed assertion of external training.

`train_native_smoke` changes only runner construction to inject an environment:
HARL hard-codes its stock environment factories. Original `run`, `collect`,
`insert`, `compute`, `train`, `after_update`, and `save` are inherited unchanged.
The original HAPPO sequential importance-factor update, PPO loss, Adam optimizer,
critic, return calculation, buffers, and value normalizer run unchanged. The
bounded configuration uses two independent recurrent 32x32 actors, four CPU
environments, rollout length 16, recurrent chunk length 4, and 64 updates.
There is no policy reimplementation, import stub, or upstream source edit.

Reproduce from `/home/airs/hetrocoord`:

```bash
python3 -m venv --system-site-packages /home/airs/.cache/heterocoord/envs/harl
/home/airs/.cache/heterocoord/envs/harl/bin/python -m pip install -r algorithm/system/coordination/harl/requirements-native.txt

# Choose a NEW output directory; existing trained bundles are not overwritten.
/home/airs/.cache/heterocoord/envs/harl/bin/python -m algorithm.system.coordination.harl.train_native_smoke \
  --source-root /home/airs/.cache/heterocoord/upstream/harl \
  --output records/algorithm_adapters/harl/local_macro_reproduction --updates 64

/home/airs/.cache/heterocoord/envs/harl/bin/python -m algorithm.system.coordination.harl.infer_native_smoke \
  --source-root /home/airs/.cache/heterocoord/upstream/harl \
  --manifest records/algorithm_adapters/harl/local_macro_smoke/manifest.json

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /home/airs/.cache/heterocoord/envs/harl/bin/python -m pytest -q \
  algorithm/system/coordination/harl/tests/test_native_adapter.py
```

The venv isolates newly installed method dependencies and intentionally inherits
the host Torch/NumPy installation; it is not a fully hermetic environment.
The source is imported from the selected checkout without installation/editing.

Training environment seeds 11–14 and evaluation seeds 10011/10012 are disjoint.
The environment implements symbolic instantaneous macro completion with reward
`new_completions - 0.05 + 0.5 * all_completed`. A disjoint seed/position holdout
is only a wiring check on this tiny environment; it is not evidence of new-task
generalization. `SymbolicMacroExecutor` records actual TaskRunner dispatches,
but is not an EAI/Isaac physical executor. Physical success and benchmark
performance remain `N/A`/`not_evaluable` even when every macro completes.

## Parent wrapper and CLI

The parent wrapper uses the same constructor without changing shared code:

```python
from benchmark.native_algorithms import build_native_adapter
provider = build_native_adapter(
    "harl_happo",
    manifest_path="/home/airs/hetrocoord/records/algorithm_adapters/harl/local_macro_smoke/manifest.json",
    device="cpu",
)
```

The CLI `--config` is a **flat JSON object of constructor kwargs**, not nested
under `kwargs`/`adapter_kwargs`/`configuration`. The saved
`records/algorithm_adapters/harl/wrapper_kwargs.json` contains `manifest_path`
and `device`. Set `source_root` using the CLI flag, not again in that JSON.

```bash
/home/airs/.cache/heterocoord/envs/harl/bin/python -m tools.algorithm_adapters.run \
  --algorithm harl_happo \
  --task records/algorithm_adapters/harl/wrapper_holdout_task.json \
  --config records/algorithm_adapters/harl/wrapper_kwargs.json \
  --report records/algorithm_adapters/harl/wrapper_cli_report.json
```

This executed with the permanent cache and returned `native_decision_returned`
and two assignments. The CLI is decision-only: it does not execute assignments
or produce physical success evidence.

## Generic v2 manifest

The same `NativeHAPPOAdapter` dispatches manifests with schema
`heterocoord.happo.checkpoint.v2` through `generic_manifest.BoundContract`.
V1 remains deliberately fixed to the tested smoke setup. **V2 does not reject
non-smoke condition names or external checkpoint provenance.** It supports a
caller-specified number of actors, separate native actor configurations,
float32 vector observation sizes, and discrete wait/task-assignment actions.
Recurrent, naive recurrent, or feedforward policy flags pass unchanged to the
original HAPPO constructor. No weights are partially loaded or replaced.

An exact v2 example is in
`records/algorithm_adapters/harl/v2_same_contract/manifest.json`. It reuses the
known locally trained smoke weights with **identical** observations/actions;
the new manifest format is not a claim of a new trained environment.

Required top-level fields:

- `schema`, `algorithm: "HAPPO"`, `source_commit`, and nonempty `condition`.
- `contract`: `heterocoord.happo.bound_contract.v1`, containing environment ID,
  observation schema, allowed scene IDs, horizon, completion/failure outcome
  strings, exact task IDs/capabilities/prerequisites/target refs, and actors.
- Each contract actor: sequential `actor_index`, unique `robot_id`, capabilities,
  `observation` with dtype, vector shape, ordered features (name, meaning, unit,
  finite-or-null lower/upper bounds), explicit preprocessing description, and
  `actions` in original discrete-index order.
- Each action is exactly `{"kind":"wait"}` or
  `{"kind":"assign","task_id":"..."}`. Unsupported control types are rejected.
- `actors`: one checkpoint per declared actor, with exact actor/robot identity,
  file, SHA-256, and complete native `actor_args` from its training configuration.
- `checkpoint_provenance`: relative JSON file and SHA-256. Its document binds
  the contract hash, source revision, condition, actor hashes/configurations,
  origin reference, training environment, and an explicit compatibility statement.

The provenance schema is `heterocoord.happo.checkpoint_provenance.v1`; origin
kind is `locally_trained` or `external_checkpoint`. Local origin additionally
references a hashed JSON `training_evidence` receipt whose source, environment,
updates, actor configuration, tensor fingerprints, and checkpoint hashes agree.
External origin may identify
official weights, but hashes verify bytes, not the truth of that assertion.
Output records `checkpoint_origin_verification` as
`caller_attested_hash_bound_not_independently_verified`. **No compatible official
non-smoke checkpoint was supplied or executed in this work.** A caller cannot
turn SMAC observations into EAI observations merely by matching tensor sizes;
the contract and compatibility statement must retain the actual trained semantics.

V2 host input is explicit, not inferred from arbitrary package metadata:

```python
task.metadata["episode_id"] = "host-episode-42"
task.metadata["happo_contract_sha256"] = contract_digest(manifest["contract"])
robot.metadata["happo_observation"] = {
    "contract_sha256": contract_hash,
    "episode_id": "host-episode-42",
    "step": 0,
    "completed_task_ids": [],
    "last_completed_task_ids": [],
    "values": [...],              # exact trained feature order and preprocessing
    "available_actions": [...],   # exact binary mask, at least one action enabled
}
```

Use `dataclasses.replace` to create updated frozen task/robot records in a host.
`contract_digest` is exported by `generic_manifest.py`. The host must provide a
fresh snapshot after `update_feedback`, including the adapter's current step,
completed-task set, and last-completed set. Validation checks all lengths,
identities, step/episode/hash, finite values, bounds, and task readiness. It does
not pad observations or rewrite masks. It also cannot independently verify the
meaning/truth of caller-produced sensor features; that belongs to host validation.

V2 feedback carries a decision-specific token:

```python
decision = provider(task, robots)
token = decision.provenance["feedback_token"]
provider.update_feedback(feedback_for_this_dispatch,
                         decision_token=token, terminal=False)
```

The host must save the token with the dispatch and reuse that saved token for
delayed callbacks. Missing or stale tokens are rejected across retries/resets,
even when task and robot IDs are reused. V1's synchronous smoke API remains
tokenless. Failed outcomes require a non-`NONE` `FailureCode`.

The decoder emits the original actor's discrete action mapping exactly. If
multiple actors select the same task, `NativeHAPPOScopeError.actor_outputs`
preserves **all** original outputs and their mappings; no winner is chosen.
Recurrent state is not committed on this scope failure. Continuous controls,
image observation encoders, and coalition dispatch are outside this mapping.

## Independent review findings

Read-only review of the parent files on 2026-10-28 found these integration
issues; the parent files were left unchanged:

- `benchmark/native_algorithms.py:47-53` exposes `replan` only as a positional
  lambda. It rejects the runner's keyword form `replan(task=..., robots=...,
  state=...)`, and it converts a native `replan -> None` stop signal into a
  `TypeError` because `_run` requires `AlgorithmDecision`. HARL has no replan,
  so this does not affect its callable path; a parent fix should preserve
  keyword arguments and the runner's documented `None` stop contract.
- `tools/algorithm_adapters/run.py:27` protects only task/config paths. A
  report path can overwrite a checkpoint manifest or actor file named in the
  config. The CLI then exits success if the adapter already returned a
  decision, even though it destroyed an input artifact. The parent should
  reject report paths that resolve inside configured manifest/checkpoint bundles
  and should write reports atomically after successful execution.
- `tools/algorithm_adapters/run.py:49-72` serializes `native_result` but the
  adapter must attach rejected raw outputs there. HARL v2 now does this through
  `NativeHAPPOScopeError.native_result`; no TaskRunner assignment is fabricated.

The review also found and fixed HARL-owned issues: v2 now rejects prerequisite
cycles, validates binary masks and bounds before float32 conversion, verifies
live/package capability parity, requires boolean terminal values, requires a
non-`NONE` failure code for failed feedback, checks local training receipts
against source/config/checkpoint/update evidence, guards against in-memory
manifest mutation, and includes scene identity in the episode key. The report
`records/algorithm_adapters/harl/review_before.json` preserves the negative
reproductions from before these local fixes; `review_tests.log` records the
post-fix result.

V2 validation is tested using the actual locally trained checkpoints: exact
native action/recurrent parity with v1, parent wrapper/CLI execution, stale and
malformed observation rejection, provenance/config mismatch rejection, and raw
coalition-output preservation. External-origin acceptance is a deliberately
labelled loader-only test using the same local weights, **not** official-model
or benchmark evidence. Actor counts/architectures beyond the trained two-actor
configuration are configurable but have not received native runtime acceptance.
