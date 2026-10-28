# SADCHER UR5 physical preflight — blocked before Isaac

Date: 2026-10-28. Scope: CPU native decision and the shared host's pure
contract function only. Isaac, Isaac Lab, CUDA, `AppLauncher`, and a physical
executor were not started.

The exact earlier task `(0.45, 0.05, 0)` is not accepted by the shared host:
the original task document lacks the required `/World/Object` target reference.
After adding the host's explicit physical metadata, the same Y coordinate is
rejected because the declared task XY disagrees with the host object at
`(0.45, 0.0, 0.475)`. `task_host_aligned.json` changes only the declared task
Y to `0.0` and passes `physical_options()`.

With the aligned input, the unchanged native SADCHER scheduler, complete
official checkpoint, original neural rewards, matcher, filters, and sentinels
return raw mapping `robot index 0 -> native task index 2`, where index 2 is
`__idle__`. The EAI decision therefore has no assignment and leaves
`pick_place` unassigned. TaskRunner dispatch count is zero and an executor
tripwire was not called. The shared physical host would reject before its
executor boundary with `native policy did not dispatch the supported task; no
skill substituted`.

This is an algorithm output and scope block. No parameter search, skill
substitution, single-robot selection, scheduler change, model change, or
physical pass is claimed. Physical success remains `N/A`.

Evidence:

- `host_contract_check.json`: original rejection and aligned contract acceptance.
- `host_aligned.preflight.json`: factory, TaskRunner, independent native
  Simulation parity, and zero-dispatch gate.
- `original_with_host_metadata.preflight.json`: same native idle result before
  the host's Y-coordinate rejection.
- `task_original_exact.json`, `task_original_with_host_metadata.json`, and
  `task_host_aligned.json`: immutable input variants.
- `run_command.sh`: prepared command only; it has not been executed.

The next required evidence is a native decision that contains exactly one
`ur5_1 -> pick_place` assignment under the unchanged policy, followed by the
parent-owned Isaac host/evaluator run. This preflight cannot establish that
condition and does not bypass it.
