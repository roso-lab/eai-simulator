# EMOS structured output contract — 2026-10-28

The EMOS adapter now supports the versioned JSON wire contract
`emos.native-output.v1`. It is injected at the model transport seam as an
additional instruction on the native system message. The upstream EMOS source
functions, prompts, reflection loop, reassignment loop, numerical interpreter,
and original brace parser are loaded and executed unchanged. A validated JSON
response is projected into the original brace form solely so those unchanged
functions can continue running; the original JSON response and the structured
document remain in the endpoint trace.

The contract preserves:

- exact `robot_id` identity and the complete robot roster;
- exact registered `task_ids`;
- `assigned`, `idle`, and `refused` assignment states;
- multiple task IDs assigned to one robot;
- repeated task IDs across robots, which remain a coalition and are rejected by
  the current TaskRunner boundary with the complete structured output retained;
- robot `yes`, `no`, and `refused` feedback, including its exact reason;
- original numerical-computation turns through a contract `computation` object,
  whose Python source is projected into the original fenced-code input for the
  unchanged interpreter.

Unknown IDs, missing robots, duplicate JSON keys, malformed schema, wrong
speaker identity, unsafe brace/parser content, and invalid computation objects
fail closed. No semantic task matching, winner selection, coalition repair,
refusal-to-idle conversion, or post-inference binding is performed. Idle uses
the native `Nothing to do` marker; its original reason remains in structured
records. Native `yes` carries no reason into the original parser, so its JSON
reason is retained in those records too. The original three-round limit can
return an allocation despite unresolved robot objections; this behavior remains
unchanged, with `final_robot_feedback` and `final_unanimous_feasibility` exposed
in provenance rather than interpreted as success.

This is a new output-format-conditioned experimental configuration. The
upstream functions are unchanged, but the effective model prompt has changed;
results must not be labeled as an unchanged-prompt reproduction.

The schema hash is:

```text
2825dc41ed4734fde0b733873e370ffb0bb093dd764db582bd76136ecd261af9
```

Each live request records both the schema hash and the injected prompt hash by
speaker. Failure paths attach `native_result` and `adaptation_condition` to the
exception, including those hashes, the raw structured turns, endpoint receipt,
and the unchanged native subtask text. The shared runner can therefore retain
contract provenance even when no `AlgorithmDecision` is returned.

## Live CLI evidence

No second live run was started by this agent. The active independent run is the
parent's directory:

`/home/airs/hetrocoord/records/algorithm_adapters/emos/live_dongli_ur5_structured_v1_20261028T165508`

Its completed decision-only CLI report records:

- requested/returned model: `gpt-5.6-luna`;
- reasoning effort: `low`;
- 2 completion requests, 0 endpoint failures, 2,827 total tokens;
- 2 structured responses accepted: leader assignment and UR5 feasibility
  feedback;
- assignment: `ur5_1 -> move_carton`;
- `finish_reason=stop` for both calls;
- contract version and schema hash above;
- leader and robot prompt hashes:
  `b695bd546cb85640757d8ef14ea888ce7fe594053ece1d673c9b4ed8b6d701f8` and
  `5cf85add86870639ecfaedcd92e30583d2e171ccc02bb99b22783d730c2d48f9`.

The report is decision-only. Its TaskRunner verdict remains `N/A` with
`physical_evidence_required`; no Isaac process was started by this agent. The
host2 run was executed sequentially by the parent in its own new output
directory. The configured relative `log_dir` produced a nested path; the
decision provenance records that actual path and it must remain unchanged.

The final condition-identity repair was applied after that runtime: legacy
conditions keep the upstream discussion source hash, while enabled-contract
conditions now hash canonical JSON containing the upstream prompt hash, schema
hash, and speaker-specific contract prompt hashes. Their assignments also carry
`plan_version=upstream-stage-one-v1+emos.native-output.v1` and condition notes
name the output contract. The earlier live records retain their original
adapter hash and were not rewritten.

The completed host2 receipts were read independently at:

`/home/airs/hetrocoord/records/algorithm_adapters/emos/live_dongli_ur5_structured_v1_20261028T165508_host2/`

`integration.json` and `physical_execution.json` both record `passed=true`.
Its separate native run used 2 real requests and 2,783 tokens, with no endpoint
failure or contract rejection. The measured lift is 0.135033 m, final object
origin error 0.000011 m, and attachment/release counts 1/1. This is one UR5
Cartesian pick/place using **idealized follow-proxy attachment, not contact
grasp**; it is not multi-robot or full EMOS stage-two acceptance. Physical receipt
SHA256: `fa2a667f3d3f47cb1cde4d2e6c1d2820fce70b825288c21779b92c254f53640e`.

## Test evidence

The EMOS package test receipt
`records/algorithm_adapters/emos/structured_output_tests_20261028.xml` records
99 passed tests at its earlier snapshot. A later independent check of the frozen
live adapter and current package tests produced **96 passed in 1.53 s**, recorded
in `records/algorithm_adapters/emos/structured_output_review_20261028.xml`.
The current tests include valid structured output, invalid schema/identity/
duplicate-key cases, coalition preservation, idle/refusal semantics, numerical
computation projection, raw-response retention, factory construction, and
failure provenance. These are protocol tests using deterministic completion
doubles; they are not algorithm acceptance.

The shared validation reported by the parent is separate: 164 passed and 1
skipped, with the host suite at 11 passed. No shared files were edited here.

## Files changed in this work

- `/home/airs/hetrocoord/algorithm/system/coordination/emos/native_adapter.py`
- `/home/airs/hetrocoord/algorithm/system/coordination/emos/tests/test_native_adapter.py`
- `/home/airs/hetrocoord/tools/algorithm_adapters/emos_config.py`
- `/home/airs/hetrocoord/records/algorithm_adapters/emos/STRUCTURED_OUTPUT_CONTRACT_20261028.md`
- `/home/airs/hetrocoord/records/algorithm_adapters/emos/structured_output_review_20261028.xml`
- `/home/airs/hetrocoord/records/algorithm_adapters/emos/condition_identity_tests_20261028.xml`

The implementation preserves the existing optional legacy wire mode when
`output_contract_version` is omitted or null. The new live configuration opts
in explicitly with:

```json
"output_contract_version": "emos.native-output.v1"
```

The endpoint still receives credentials only through environment-variable
names in JSON. No credential, URL value, or secret was written to this record.
