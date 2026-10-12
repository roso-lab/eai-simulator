# Topology authoring and node-route validation

This record covers the fixes following `b5e819efd9c4515c06e72433628fb3a130abf544`.
The version 1 implementation keeps the existing directed XY shortest path and node PID.
Scene selection, task orchestration, physical evaluation and experiment identity
remain host responsibilities. No formal scene/task contract is introduced.

The later version 2 multilevel implementation and Go2 stair validation are
recorded separately in [MULTILEVEL_VALIDATION.md](MULTILEVEL_VALIDATION.md).

## Repeatable lightweight checks

From the repository root, using the prepared `env_isaaclab` Python:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m pytest --rootdir="$PWD" -q -p no:cacheprovider \
  tools/experiment_analysis/tests tools/benchmark_branch/tests \
  task/tests algorithm/skills/tests \
  tools/validation/test_topology_authoring.py \
  tools/validation/test_topology_route_contract.py \
  tools/validation/test_topology_authoring_ui.py \
  tools/validation/test_topology_multilevel_authoring.py \
  tools/validation/test_topology_surfaces.py \
  tools/validation/test_simulator_gui_shutdown.py
git diff --check
```

The focused tests cover malformed coordinates and topology, source and semantic
identity, real endpoint-segment validation, directed routes, product and graph
digests, failed-save preservation, editing history, adapter construction and
application cleanup. Mock GUI tests do not establish native stage behavior.

## Native acceptance procedure

Use a temporary plane adapter and diagnostic under ignored `tmp/`; keep all
generated products, screenshots, traces and process receipts there. The adapter
and temporary task are diagnostics, not reusable benchmark scenarios.

1. Start the production authoring GUI in Isaac Sim 5.1. Invoke its real Kit
   callbacks to create three nodes at `(0,0,0)`, `(3,0,0)`, `(3,3,0)`, remove
   the middle node, then recreate it and connect two directed edges. Bind the
   destination, validate, save and generate the product.
2. Inject a source-loading failure after replacing the USD stage. Check that
   the original document and stage are restored. Reload successfully, check
   that the empty document has a fresh history and a rebound overlay, then
   reload the saved draft. Inspect the actual rendered screenshots.
3. Freeze the generated product digest, load that same product with the
   adapter, and inject it into `GroundNavigationExecutor`. Open one EAI plane
   session through `open_simulator_session`, using the Carter differential
   controller and CUDA physics. Read actual root poses and apply commands
   through `env.step`; do not teleport or replace the controller.
4. Check the intermediate bend, final goal radius, terminal zero command and
   settled measured speed. Request the reverse route on the directed graph
   and check `route_unreachable` plus zero velocity.
5. Record source/controller/asset SHA-256 inputs, ensure they remain unchanged
   throughout both subprocesses, and require clean process-group termination.

This exercises real Kit callbacks and renders, not delivery of physical mouse
events. It verifies one supported ground controller on an unobstructed plane;
it does not certify all robots, collision-free scene routes or physical task
success. The caller still supplies and freezes the real scene topology, ensures
direct access to the nearest start node, and evaluates the task independently.

## Observed result — 2026-10-12

The original version 1 matrix (before adding the multilevel files above)
completed with **625 passed, 1 skipped** in 36.29 seconds.
The skip is a historical scene plugin unavailable in this checkout.
`git diff --check` passed.

Native evidence is retained locally at `tmp/native_topology/validation-05/`.
The local diagnostic command was:

```bash
PYTHONDONTWRITEBYTECODE=1 \
/home/pupil/miniconda3/envs/env_isaaclab/bin/python \
  tmp/native_topology/check.py run --root tmp/native_topology/validation-05
```

Each repeat requires a new output directory. The script is a local diagnostic,
not a shipped command or a formal benchmark task. It uses Isaac Sim 5.1 on a
Quadro RTX 6000 with both EAI and physics devices set to `cuda:0`.

| Check | Observed result |
| --- | --- |
| Editor generation, failed-stage rollback, successful reload, draft history reset | Passed; restored stage remained valid after 40 updates |
| Actual screenshot inspection | Editor/reloaded graph and robot start/bend/final frames inspected |
| Executed node route | `(0,0) → (3,0) → (3,3)`, 6 m, 605 control ticks |
| Bend visitation | Entered the 0.32 m check radius |
| Settled final XY distance to goal | 0.288082 m; configured arrival radius 0.30 m |
| Settled measured planar speed | About 0.0023 m/s; check limit 0.08 m/s |
| Reverse request on directed edges | `route_unreachable`, zero velocity command |
| Author / navigation processes | Both exit 0, process groups terminal; no timeout, TERM or KILL |
| Process durations | 34.34 s / 163.05 s |
| Frozen code, controller and asset inputs | Unchanged before/between/after subprocesses |

The full product digest in both authoring and execution was
`3d7c1fa02dc85559f1ef1723f896f522392f44c22dd8aa6ee373d775954e6e69`.
The selected executable graph digest was
`97410be9374a0f8ff496e54fe0a65f8ba20db61e778f91fbd3084fbc0f1d97a8`.
The input-manifest file SHA-256 was
`dfb8982c6ad9cf9a9d88767dfe19ba81a13428c572759c138d53d242a3233a25`.

Earlier native attempts are preserved separately: they exposed stage lifetime
and GUI shutdown defects that were fixed before the passing attempt. Those
attempts are not counted as successful validation. The native procedure invokes
real GUI callbacks; OS mouse delivery remains outside this validation.
