# Independent code review — 2026-10-28

Scope was limited to read-only inspection of:

- `benchmark/native_algorithms.py`
- `tools/algorithm_adapters/run.py`
- HARL v2 `generic_manifest.py` and its lifecycle in `native_adapter.py`

The two parent files were not modified. Their pre-fix SHA-256 values were
recorded as `review_before.json`:

- `benchmark/native_algorithms.py`:
  `bb6d66ad1c39e2e3cc84202088556eacb6a115b7070d0e582a2cfd6e94e0473b`
- `tools/algorithm_adapters/run.py`:
  `73150923ef90c4e956c0b140a86776339c28b5db6e03f401ebde889747d06df7`

Findings:

1. **[P1] Report overwrite of input artifact — parent CLI, lines 27 and 83.** The guard
   compared only `report` to `task` and `config`. A report path could equal the
   manifest referenced inside JSON config, and the CLI wrote the success report
   over that manifest. The negative reproduction returned exit code 0 and
   `overwritten: true`. This is a destructive lifecycle bug in shared code;
   HARL does not change it.
2. **[P2] Replan wrapper contract mismatch — parent factory, lines 47–64.** The
   wrapper creates a positional-only lambda for `replan`; keyword invocation
   fails. It also rejects `None`, although `TaskRunner._call_replan` treats
   `None` as the native planner's valid stop signal. This is a lifecycle/API
   mismatch. HARL does not expose `replan`, so its direct `__call__` path is
   unaffected.
3. **Scope-error serialization boundary — parent CLI lines 53–67.** The CLI
   can serialize `exc.native_result`, but providers must attach it. HARL v2 now
   attaches all actor outputs to `NativeHAPPOScopeError.native_result`. This
   preserves evidence without silently selecting a robot.
4. **Float32 mask coercion — HARL v2.** Before the fix, `0.999999999` became
   `1.0` and passed the mask check. Validation now occurs before conversion.
5. **Lifecycle type coercion — HARL v2.** Before the fix, `terminal="false"`
   became `True`. It now requires a real boolean and failed feedback requires a
   non-`NONE` `FailureCode`; state remains pending when validation fails.
6. **Manifest graph validation — HARL v2.** Before the fix, prerequisite cycles
   were accepted. The loader now rejects cycles before any actor load.
7. **Receipt and binding integrity — HARL v2.** Local training evidence now
   checks source revision, positive updates, actor configuration, changed tensor
   fingerprints, and checkpoint hashes. Live/package capabilities and active
   state must agree; loaded manifest mutation is rejected; scene identity is
   part of the episode key.
8. **Delayed-feedback replay — HARL v2.** Task/robot identity alone did not
   distinguish retries or resets with reused task IDs. V2 now emits
   `provenance.feedback_token` and requires it as `decision_token`; stale tokens
   fail before state changes. V1 synchronous smoke calls remain tokenless.

The external-origin compatibility statement remains caller-attested. The
adapter validates the binding and bytes but cannot prove that an external
checkpoint was trained for the declared environment. It therefore keeps
`checkpoint_origin_verification` as
`caller_attested_hash_bound_not_independently_verified` and makes no benchmark
or physical-success claim.

Post-fix validation:

```text
81 passed in 6.09s
```

This includes HARL adapter, v2 schema/lifecycle, parent wrapper/CLI seam tests,
TaskRunner tests, and parent native-envelope tests. No parent/shared file,
upstream source, or unrelated dirty file was modified.

Own files changed in this review: `native_adapter.py`, `generic_manifest.py`,
`tests/test_native_adapter.py`, new `tests/test_review.py`, and
`NATIVE_ADAPTER.md`. No new action/environment scope, official-checkpoint
acceptance, or physical performance claim was added.
