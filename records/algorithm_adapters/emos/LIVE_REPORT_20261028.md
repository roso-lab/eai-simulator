# EMOS live endpoint and UR5 host evidence — 2026-10-28

Real native discussion ran with requested and returned model `gpt-5.6-luna`,
`reasoning_effort=low`. Both cases failed exact output binding. Neither produced
an accepted AlgorithmDecision or dispatched a physical skill. No algorithm or
physical-task acceptance is claimed. Earlier missing-credential receipts remain
historical evidence; authorization to read the four Dongli assignments allowed
these later live runs.

| Independent case | Requests / failed | Prompt + completion = total tokens | Outcome |
| --- | --- | --- | --- |
| Four-robot Fire Rescue symbolic fixture | 20 / 0 | 23,549 + 1,848 = 25,397 | Native reflection completed; conditional idle prose could not bind |
| One-robot UR5 host attempt | 2 / 0 | 1,308 + 212 = 1,520 | Robot answered `{{yes}}`; expanded leader prose could not bind; no skill dispatched |

All 22 responses ended with `finish_reason=stop`. These are separate from the
parent's live examples. The four-robot native `token_usage.json` keys by robot
type and overwrites one of the two M20 totals; the metered endpoint trace above
counts every real request. No retries, output-length override, substituted
response, prompt change, parser repair, or post-inference binding was applied.

## Exact evidence

- `live_dongli_fire_rescue_20261028T075825Z/unified_live_report.json`: unified CLI/factory/TaskRunner failure, source provenance, input hashes, 296.77 s.
- `live_dongli_fire_rescue_20261028T075825Z/evidence_summary.json`: reconciled provider usage and artifact checks.
- `live_dongli_fire_rescue_20261028T075825Z/native_logs/`: native histories and all 20 actual API responses.
- `live_dongli_ur5_20261028T080837Z/config.json` and `task.json`: predeclared physical host fixture, six-joint stationary UR5 resume and correct native coordinates. Object `[x,z,y]=[0.45,0.475,0]`, destination `[0.425,0.475,0.01]`; task physical coordinates retain ordinary `[x,y,z]`.
- `live_dongli_ur5_20261028T080837Z/preregistered_inputs.json`: exact task ID `move_carton` and description `Pick and place the workbench carton` registered before inference. Hashes match the final inputs. No extra output bindings.
- `live_dongli_ur5_20261028T080837Z/native_decision.json`: real two-request native run, rejected complete native description, source provenance, 19.70 s.
- `live_dongli_ur5_20261028T080837Z/integration.json`: existing Isaac host invoked TaskRunner and refused skill dispatch after native failure. Isaac did not start and `physical_execution.json` does not exist.
- `live_dongli_ur5_20261028T080837Z/native_logs/`: actual leader response and robot `{{yes}}` response, original histories and endpoint trace.
- `live_dongli_ur5_20261028T080837Z/evidence_summary.json`: usage, hashes, input registration checks, secret-scan result.

The UR5 input declares the existing host's Cartesian pick/place capability and
idealized follow-proxy attachment; it does not claim measured reachability or a
successful contact grasp. A robot's textual `yes` is not execution evidence.
The host's existing 120 s subprocess timeout was sufficient. No host or skill
code was changed. This agent ran exactly one UR5 attempt.

## Implementation and validation

Owned implementation files:

- `algorithm/system/coordination/emos/native_adapter.py`: `EmosNativeAdapter`, `MeteredNativeEndpoint`, unchanged original planner loading, strict IO mapping, optional endpoint reasoning effort and raw-response traces even on errors.
- `tools/algorithm_adapters/emos_config.py`: `build_emos_from_config`, explicit JSON request/resume/observation parsing, environment-variable references and metered live-only entry.
- `algorithm/system/coordination/emos/tests/test_native_adapter.py`: relocated package-local protocol tests, including shared `build_native_adapter('emos', json_config=..., config_base_dir=...)`, CLI failure provenance and endpoint metering.

Source checkout `/tmp/emos-1793068059`, branch `embodied_mas`, commit
`e9501db45d634b087bf5d1a14228266685e8feeb`, has no tracked modifications. Shared
factory/run provenance records the original 778 source files and adapter/register
hashes. The original prompts, discussion, three reflection rounds, numerical
interpreter and free-text parsing are unchanged. Stage-one external feedback and
replanning remain unsupported rather than fabricated.

Focused protocol validation: 9 passed, 67 deselected (Fire Rescue directory's
`protocol_tests.xml`). Full package-local protocol validation: **76 passed in
1.44 s**, recorded in the UR5 directory's `protocol_tests.xml`. Deterministic doubles in these tests are
protocol evidence only; the live results above come from the actual endpoint.
The new root `tests/test_native_adapter.py` is absent. No unrelated tests were run
in this live follow-up. Earlier audit/test/blocked receipts were not overwritten.

Credentials were loaded only from the four authorized literal Dongli assignments
in the first 23 lines of `~/.bashrc`, then passed in child process environment.
No credential or URL was printed or saved. Both evidence directories were checked
for the actual key and base URL in memory; neither was present in artifacts.

## Runnable configuration and command

The UR5 `config.json` is a complete example using only environment names:

```json
{
  "api_key_env": "DONGLI_API_KEY",
  "base_url_env": "DONGLI_BASE_URL",
  "model_env": "DONGLI_MODEL",
  "reasoning_effort_env": "DONGLI_REASONING_EFFORT",
  "max_calls": 12,
  "timeout": 90
}
```

Required model and effort are `gpt-5.6-luna` and `low`. With those existing
variables exported, the exact host entry used was:

```bash
cd /home/airs/hetrocoord
export PATH=/home/airs/miniconda3/envs/env_isaaclab/bin:"$PATH"
/home/airs/miniconda3/envs/env_isaaclab/bin/python -m tools.algorithm_adapters.isaac_pick_place \
  --algorithm emos \
  --algorithm-python /home/airs/miniconda3/envs/env_isaaclab/bin/python \
  --task records/algorithm_adapters/emos/live_dongli_ur5_20261028T080837Z/task.json \
  --config records/algorithm_adapters/emos/live_dongli_ur5_20261028T080837Z/config.json \
  --source-root /tmp/emos-1793068059 \
  --output records/algorithm_adapters/emos/live_dongli_ur5_20261028T080837Z
```

This documents the executed command; use a new output directory and new config
`log_dir` for another run so these historical reports remain intact. The uniform
decision-only entry is `python -m tools.algorithm_adapters.run --algorithm emos
--task TASK --config CONFIG --source-root /tmp/emos-1793068059 --report REPORT`.
The source root is explicit; reproducible source provisioning belongs to the
parent's shared infrastructure.
