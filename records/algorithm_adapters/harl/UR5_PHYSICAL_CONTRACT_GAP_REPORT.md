# HARL:HAPPO -> Isaac UR5 physical-contract gap report

Date: 2026-10-28  
Scope: CPU decision/preflight only. Isaac Lab, PhysX, and GPU were not started.

## Decision

The current v2 checkpoint cannot be safely mapped to the bounded Isaac UR5
single-task host. The adapter therefore fails closed before native actor
inference and before any TaskRunner executor call. No policy output was
reinterpreted, relabeled, padded, or replaced.

This is a contract incompatibility, not a missing host field:

| Dimension | Trained HARL v2 condition | UR5 host package |
| --- | --- | --- |
| Environment | `heterocoord.happo.two_actor_macro.v1` | `isolated-ur5-workbench` |
| Scene | `happo-symbolic-macro-smoke` | `isolated-ur5-workbench` |
| Roster | `ground` + `air`, two recurrent actors | `ur5_1`, one robot |
| Capabilities | `ground_inspect`, `air_inspect` | `manipulate` |
| Tasks | `inspect_ground`, `inspect_air` | `move_carton` |
| Target | no target reference; unit-square XY features | `/World/Object`, object and place world XYZ |
| Actions | discrete `wait` / actor-owned inspection assignment | pick/place physical macro with Cartesian pose |
| Completion | `macro_completed` / `macro_failed` | host execution feedback `success` / `failure` |
| Observation | nine dimensionless symbolic features | no trained UR5 observation encoder or bound snapshot |
| Training semantics | symbolic instant completion, horizon 4 | stepped manipulation and physical evidence |

The host has a valid bounded physical fixture and explicit object/place
parameters. That proves host readiness only; it does not establish policy
compatibility. The host's reported physical scope remains
`idealized_follow_proxy_pick_place; not contact grasp`.

## Executed evidence

The unchanged v2 manifest and its original two-actor weights were passed through
the parent native CLI with the UR5 package:

```bash
/home/airs/.cache/heterocoord/envs/harl/bin/python -m tools.algorithm_adapters.run \
  --algorithm harl_happo \
  --task records/algorithm_adapters/harl/ur5_task_with_episode.json \
  --config records/algorithm_adapters/harl/v2_same_contract/kwargs.json \
  --report records/algorithm_adapters/harl/ur5_contract_rejection.json
```

Result: process exit 1, `status=not_passed`,
`error_type=NativeHAPPOError`, error
`task does not match bound environment contract`. The report records
`physical_success=null` and
`simulation_acceptance_passed=false`. The adapter rejected the task before
`HAPPO.act`; no Isaac process was launched.

A separate CPU holdout control continues to execute the original HAPPO actors
and records both actor outputs. It remains symbolic decision evidence only:
`simulation=not_executed`, `physical=N/A`, and benchmark
`not_evaluable`.

## Required contract before a future UR5 smoke

A valid future run needs a checkpoint trained for the exact declared contract,
or an independently verified compatible official checkpoint, including:

1. One actor-to-`ur5_1` binding and one `move_carton` task binding.
2. A training observation schema that defines the exact ordered features,
   units, preprocessing, bounds, episode/step snapshot, and action mask.
3. An action binding whose native discrete outputs represent a declared
   pick/place macro boundary. The adapter must not expand a discrete action into
   Cartesian control unless that expansion is part of the trained contract.
4. Completion/failure strings and failure-code semantics that the live host can
   deliver after execution, plus a persistent decision token for delayed
   feedback.
5. Complete native actor weights and exact architecture/configuration hashes.
   Partial weights, unrelated SMAC weights, tensor-size matching, or actor
   relabeling are invalid.
6. A live provider lifecycle that retains the recurrent actor state through
   host execution and calls `update_feedback` with the saved decision token.
   The current UR5 decision subprocess boundary destroys that state after
   native inference, so even a future compatible recurrent manifest would need
   host lifecycle support before claiming recurrent physical integration.

Only after these checks pass should the unified UR5 host be run. Its output can
provide bounded physical evidence, with no benchmark score implied.

## Changed/added HARL evidence

- `algorithm/system/coordination/harl/tests/test_physical_contract.py`: seven
  CPU-only tests for host-field readiness, fail-closed rejection before actor
  or executor, preserved two-actor holdout outputs, and feedback vocabulary.
- `records/algorithm_adapters/harl/ur5_task.json`: generated UR5 package
  without episode binding, retained as the first missing-field negative case.
- `records/algorithm_adapters/harl/ur5_task_with_episode.json`: same package
  with explicit episode ID, retained as the contract mismatch negative case.
- `records/algorithm_adapters/harl/ur5_contract_rejection.json`: CLI rejection
  receipt.
- `records/algorithm_adapters/harl/ur5_contract_cli.stdout`: CLI stderr/stdout.
- `records/algorithm_adapters/harl/UR5_PHYSICAL_CONTRACT_GAP_REPORT.md`: this
  report.

No Isaac/GPU process was started. No HARL upstream actor, runner, or trainer
was edited. No shared factory, CLI, or Isaac host file was edited.

