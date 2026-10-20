# Topology authoring and node-route validation

This record covers the fixes following `b5e819efd9c4515c06e72433628fb3a130abf544`.
The version 1 implementation keeps the existing directed XY shortest path and node PID.
Scene selection, task orchestration, physical evaluation and experiment identity
remain host responsibilities. No formal scene/task contract is introduced.

The later version 2 multilevel implementation and Go2 stair validation are
recorded separately in [MULTILEVEL_VALIDATION.md](MULTILEVEL_VALIDATION.md).

## Linked dual-graph editor — 2026-10-20

The linked entry now has ground/aerial documents, shared regions and per-node
2.5D/3D editing modes. The tests below use the current `node_route` interface.
Several older tests still import the removed `ground_navigation` or `provider`
modules; those historical suites do not collect against the current API.

From the repository root, use Python with numpy, Pillow, shapely and pytest:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q \
  tools/validation/test_topology_editing_project.py \
  tools/validation/test_topology_domain_navigation.py \
  tools/validation/test_topology_editing_clients.py \
  tools/validation/test_isaac_bridge.py \
  tools/validation/test_web_isaac_overlay.py \
  tools/validation/test_web_isaac_overlay_link.py \
  tools/validation/test_topology_authoring.py \
  tools/validation/test_topology_authoring_ui.py \
  tools/validation/test_semantic_topology_authoring.py \
  tools/validation/test_topology_multilevel_authoring.py \
  tools/validation/test_topology_pose_ui.py \
  tools/validation/test_topology_surfaces.py \
  tools/validation/test_topology_route_contract.py
node --check tools/topology_authoring/web/app.js
git diff --check
```

The real USD overlay tests require `pxr`; they skip when it is unavailable.
Use an Isaac/OpenUSD Python with the same dependencies to run them, and run
`tools/validation/test_topology_surfaces.py` in that environment as well.

The final focused groups ran in a temporary Python 3.12 environment inheriting
the installed Isaac/OpenUSD runtime: **204 passed, 2 skipped** (138 backend,
authoring and navigation; 36 client/bridge; 30 overlay/session). Both skips are
historical navigation checks marked for API migration. JavaScript syntax,
`git diff --check` and read-only launcher bindings for all 30 scenes passed.

The project tests cover isolated graph edits, mixed node modes, unchanged route
digests for mode-only edits, shared region geometry and per-graph entrances,
closed-edge insertion, invalid polygons, complete undo/redo, migration and
failed-save preservation. Region property patches preserve aliases, custom
properties and references. Native lifecycle tests cover graph/floor changes at
the same revision and invalidation of input on disconnect/reconnect. Real USD
checks compare world transforms for metre/centimetre stages and verify that
overlay rebuilds and previews leave the authored layer content unchanged.

Actual Chrome/CDP canvas interactions covered node creation/properties/drag,
Esc, region closure/insertion/drag/invalid rollback, edge preview and direction,
shared regions with isolated graphs, conflict/manual retry, mode and selection
sync, scene switching and reconnection. A response-loss check confirmed that
an already-committed create cannot be repeated through the uncertain-draft UI.
Static screenshots were sent as real image attachments to GLM-5.3-Flash/max;
the final layout shows all three tools, both node-mode controls and the legend.
These browser checks do not establish native Isaac mouse delivery.

A strict Chrome↔Kit run used a temporary metre/Z-up stage and local HTTP
service. Chrome changed the same aerial node to `2.5d`, Z=2.5 and a new label;
actual Kit mouse drag then changed its XY, retained Z=2.5 and left the node
count unchanged. The webpage inspector displayed the returned XY 203 ms after
the test detected the authoritative revision. This is one observed local run,
not a bound on every update. A preceding missed-node attempt is retained as
failed evidence and is not counted as successful synchronization.

Native acceptance ran on Isaac Sim 6.1 / RTX 5080, using a temporary metre/Z-up
USD, temporary project, actual Kit viewport gesture registration and local HTTP
service. `omni.kit.ui_test` delivered real mouse and keyboard input. The run
observed:

| Operation | Observed result |
| --- | --- |
| Aerial 3D height handle | Real Z-axis hit and drag changed Z from 2 to 2.3571495; XY stayed fixed |
| Ground edge | Real endpoint clicks retained an uncommitted stairs preview; confirmation submitted one edge, direction changed, deletion removed it |
| Region creation and insertion | Real clicks closed at the first point; the native `Add point here` menu hit the last-to-first edge (index 3) and inserted a fifth vertex |
| Region vertex drag | Real handle drag committed once; an intersecting drag returned 400 and preserved revision and valid polygon |
| Esc and camera | Keyboard Esc cleared drafts without an edit; Alt-left, right and middle input changed the camera and caused no topology input or revision |
| Layer isolation | Preview frames and final overlays lived in the session layer; authored layer text and USD file SHA-256 stayed identical |

Edge confirmation, direction and deletion in this matrix invoked the native
panel's controller callbacks; viewport picking, Z/vertex dragging, closure,
boundary-menu selection, Esc and camera navigation used actual input delivery.
The separate Chrome↔Kit run above covers real 2.5D dragging and return to the
webpage. These are editor and synchronization checks on temporary fixtures;
they do not add a physical navigation or obstacle-avoidance algorithm.

Native testing exposed Kit ending a non-dragging `DragGesture` click as
`CANCELED`. The wrapper now cancels only active transform drags on that path,
preserving edge endpoints, boundary candidates and submitted-edit conflict
evidence. Explicit Esc still cancels the draft. The final client regression
covers this distinction.

GLM-5.3-Flash/max also inspected actual full-window screenshots for selected
3D, 2.5D and region-menu states. The complete native panel, visible scene
geometry, corresponding axis handles and insertion menu passed static layout
review. Kit still displayed transient attribute/primvar status warnings; the
malformed single-point curve warning was fixed and absent in the final matrix.
The renderer tests and native layer/file comparisons remained green.

Local evidence for this run is retained under
`/home/ljy/Documents/Codex/agent-team/workspaces/topology-editing-20261020/artifacts/`:
`root/` contains the final pytest logs, Chrome scripts/results and closing-edge
native matrix; `team_engineer/` contains the native harnesses, synchronization
result and full-window screenshots. These diagnostics use temporary projects
and do not modify scene-local map products.

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
  tools/validation/test_topology_pose_ui.py \
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
   adapter, and inject it into `NavigationExecutor`. Open one EAI plane
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
