# Parent wrapper and generic manifest follow-up — 2026-10-28

Both saved holdout packages ran through the unmodified parent CLI:

| Manifest | CLI result | Native assignments | Source root | Physical verdict |
| --- | --- | --- | --- | --- |
| Existing v1 smoke | native_decision_returned | 2 | permanent HARL cache | N/A |
| V2, same trained semantics | native_decision_returned | 2 | permanent HARL cache | N/A |

Source root: `/home/airs/.cache/heterocoord/upstream/harl`, revision
`b1af98b0dbab72a2eee9d160751cd09aedbb8ce2`, verified by the parent wrapper.
V2 also ran through `TaskRunner(executor=SymbolicMacroExecutor())`, preserving
both native assignments as actual symbolic dispatches. The CLI itself remains
decision-only. No Isaac work was started.

The kwargs are a **flat object**:

```json
{
  "manifest_path": "/home/airs/hetrocoord/records/algorithm_adapters/harl/local_macro_smoke/manifest.json",
  "device": "cpu"
}
```

Do not nest this under `kwargs`, `configuration`, or `adapter_kwargs`. Supply
an optional source root via the CLI flag; its default is already permanent.

```bash
cd /home/airs/hetrocoord
/home/airs/.cache/heterocoord/envs/harl/bin/python -m tools.algorithm_adapters.run \
  --algorithm harl_happo \
  --task records/algorithm_adapters/harl/wrapper_holdout_task.json \
  --config records/algorithm_adapters/harl/wrapper_kwargs.json \
  --report records/algorithm_adapters/harl/wrapper_cli_report.json
```

The audit confirmed that v1 previously rejected every non-smoke manifest
unconditionally. That behavior remains appropriate for v1's fixed contract;
`NativeHAPPOAdapter` now additionally accepts
`heterocoord.happo.checkpoint.v2` with caller-defined actor, vector-observation,
discrete-action, task, feedback and provenance bindings. It has no hardcoded
smoke condition name or two-actor limit. Original HAPPO inference, architectures,
complete weight loading, and recurrent feedback are unchanged.

V2 is deliberately limited to exact float32 vectors and discrete wait/single
task assignments. It does not claim to convert unrelated environment policies.
Every actor receives a fresh host snapshot bound by contract hash, episode,
step, completion state and exact mask. No feature padding or mask rewriting
occurs. Same-task multi-robot outputs raise `NativeHAPPOScopeError` with complete
`actor_outputs`; no arbitrary winner is selected and recurrent state is not
committed. Per-actor architectures and counts are configurable, but runtime
acceptance here covers only the known trained two-actor model.

An external/official checkpoint can be described by a hash-bound provenance
document without using the smoke condition. **No compatible official non-smoke
checkpoint was supplied or executed.** Provenance is explicitly marked
`caller_attested_hash_bound_not_independently_verified`; checksums are not
certification of training origin or semantic compatibility. The external-origin
test uses known local-trained weights and says so in its fixture reference.
V2 execution evidence uses unchanged local weights and unchanged v1 semantics.

Validation: **57 tests passed** (42 adapter/wrapper/v2 tests plus 15 TaskRunner
tests). Tests include original full-network training, direct actor/recurrent
parity across v1/v2, parent wrapper and subprocess CLI, strict weight/provenance
rejection, stale snapshot rejection, no mask rewriting after completion,
unsupported-action rejection, and complete raw-output scope errors.

Changed source/test/docs in this follow-up:

- `/home/airs/hetrocoord/algorithm/system/coordination/harl/native_adapter.py`
- `/home/airs/hetrocoord/algorithm/system/coordination/harl/generic_manifest.py` (new)
- `/home/airs/hetrocoord/algorithm/system/coordination/harl/tests/test_native_adapter.py`
- `/home/airs/hetrocoord/algorithm/system/coordination/harl/NATIVE_ADAPTER.md`

New evidence under `/home/airs/hetrocoord/records/algorithm_adapters/harl/`:

- `wrapper_holdout_task.json`, `wrapper_kwargs.json`, `wrapper_cli_report.json`
- `v2_same_contract/manifest.json`, `provenance.json`, trained actor copies and training receipt
- `v2_same_contract/task.json`, `kwargs.json`, `wrapper_cli_report.json`, `taskrunner.json`
- `v2_tests_with_runner.log`, `v2_final_integrity.json`

No upstream source, shared registry/wrapper/CLI, or unrelated dirty files were
edited. No commit or push was performed.
