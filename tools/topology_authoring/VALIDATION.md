# Topology authoring and node-route validation

## Current unified editor: independent semantic points — 2026-10-20

The current linked editor provides Point, Edge and Semantic Point tools, with
independent ground, aerial and semantic-point layers. It replaces region editing
in the unified entry. The region acceptance record below describes the preceding
implementation and is retained only as history; it does not describe the current
UI or count as acceptance of semantic points.

The current focused checks use `env_isaaclab` Python and the current `node_route`
API:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
/home/ljy/miniconda3/envs/env_isaaclab/bin/python -m pytest -q \
  tools/validation/test_semantic_points_project.py \
  tools/validation/test_topology_editing_project.py \
  tools/validation/test_topology_editing_clients.py \
  tools/validation/test_web_isaac_overlay.py \
  tools/validation/test_web_isaac_overlay_link.py \
  tools/validation/test_topology_domain_navigation.py \
  tools/validation/test_isaac_bridge.py
```

The compatibility groups (`test_topology_authoring`, `test_topology_authoring_ui`,
`test_semantic_topology_authoring`, `test_topology_multilevel_authoring`,
`test_topology_pose_ui`, `test_topology_surfaces` and
`test_topology_route_contract`) passed **121 tests, with 2 historical skips**.
The combined focused and compatibility run passed **214 tests, with 2
historical skips**, in 7.15 seconds. The log is `final-acceptance/pytest-final.log`.
The same complete suite passed again after the final native panel/startup fixes
in 6.99 seconds.
JavaScript syntax, changed Python compilation, `git diff --check` and launcher
binding checks for all 30 scenes also passed.

The focused coverage includes independent semantic-point persistence and graph
digests; complete project undo/redo and failed-save rollback; graph/tool/visibility
isolation; mixed edit modes; whole-edge translation with shared endpoints; and
context, disconnect and conflict handling. Native regression checks also cover a
completed HTTP future remaining occupied until main-thread drain, edit-response
document aliases, empty-click cancellation, and hidden-layer picking. Semantic
points now keep navigation-node bindings only; historical Isaac object-binding
acceptance below describes the retired implementation.

Actual browser interaction used an isolated copy of room_06. It verified
all-height aerial display across reference-floor changes, explicit Z and mode
editing, 2.5D XY dragging, tool isolation, edge translation, semantic metadata,
hidden-layer non-selection, undo, and save/reload/reopen equality. Test data was
not saved into any of the 30 production map projects. A final mode-only change
on an aerial node with full-precision drag coordinates preserved every node
coordinate and edge exactly and preserved `route_graph_digest`, while changing
the product digest. The inspector now round-trips full coordinate precision
instead of rounding untouched fields to three decimals.

Actual native acceptance used **Isaac Sim 5.1.0 / RTX 5080** and the real room_06
USD, with an isolated project copy and live local HTTP server. The installed
`omni.kit.ui_test` API delivered mouse/keyboard events to the real viewport:

| Operation | Current observed result |
| --- | --- |
| Ground point | Viewport click created a node |
| Aerial 3D and 2.5D | Height handle changed Z from 1.5 to 2.6375201; subsequent planar dragging preserved Z exactly; explicit height input changed Z to 2.25 |
| Edge | Real endpoint clicks formed a preview, confirmation created the edge, and whole-edge mouse dragging translated its nodes/polyline |
| Semantic point | Viewport click created the point; real height-handle dragging changed Z from 1.5 to 2.4018725 |
| Isaac binding (historical, retired) | Object-selection mode accepted an actual single scene Mesh selection without creating a point; binding was accepted and reported valid |
| Camera | Alt-left, right and middle navigation changed the camera with no topology revision |
| Author layers | Session overlay existed; room_06 root-layer text and authored USD SHA-256 remained identical |

Edge confirmation, mode/property controls and binding confirmation used native
panel controller callbacks; picking and dragging used actual viewport input.
After the native matrix, the real browser connected to the same live Kit server.
It changed a semantic point's name, kind, description, aliases and mode to 2.5D.
The native snapshot observed that commit after **35.2 ms** in this local run,
with coordinates and binding exactly preserved. Real native planar drags then
changed XY, preserved Z and semantic fields, and appeared in the browser
inspector. The reverse-direction DOM samples were taken after driver delays and
do not measure transport latency. Browser polling is 500 ms and native polling
is 250 ms; the one-second visibility goal is not an unconditional latency bound.

The current native result is `final-acceptance/isaac-semantic-points-gui.json`
(`ok: true`). `web-to-native-semantic.json`, `native-to-web-semantic.json` and
`web-mode-precision-result.json` retain the synchronization and precision checks.
The final native panel run is `isaac-semantic-panel-gui.json` (`ok: true`). It
repeated the viewport actions with explicit event positions and settled
Alt-left/right/middle input streams; camera movement left the revision unchanged
after all input had drained. Mouse scrolling reached the bottom
(`scroll_y=120/120`) with no revision change, exposing binding, undo and save.
The panel is 560 × 820 with constrained fields and a vertical scrollbar. The
launcher registers a temporary CJK glyph file and installed Noto Sans CJK using
Kit's startup font settings. The final screenshots show Chinese names,
descriptions, aliases and reference-floor labels, with English controls intact.
Widget-specific font styles were removed because Kit 5.1 creates a separate
Latin-only atlas for them. `isaac-semantic-panel-top.png`,
`isaac-semantic-panel-bottom.png` and `web-semantic-final.png` retain the final
views. Following these layout/startup changes, 87 client, bridge, overlay and UI
checks passed in 4.49 seconds; Python compilation, JavaScript syntax and diff
checks also passed.
The earlier failed binding and camera-harness attempts are retained as diagnostics,
not counted as successful acceptance. The binding attempt exposed the confirmed
selection race fixed above; the camera harness was updated for Kit 5.1's `view`
matrix property.

### Isaac Sim 5.1 semantic-extension startup fix — 2026-10-21

On this machine `env_isaaclab` is a symlink to the same Isaac Sim 5.1 install as
`env_isaac`; both paths resolve to the same `isaacsim` directory. The startup
error was therefore not a map or topology-data failure. Kit's fast importer was
resolving the optional Python extensions `semantics.schema.editor` and
`semantics.schema.property` through their shared namespace package
`semantics.schema`, then received a namespace spec without search locations.

The unified launcher now installs an in-memory namespace-package shim before
creating `SimulationApp`. It points `semantics` and `semantics.schema` at the
installed extension directories and does not modify site-packages, USD files or
map data. Both extension startup messages complete without the `NoneType`
exception, and the application reaches `app ready` and `Simulation App Startup
Complete` from either of these commands:

```bash
OMNI_KIT_ACCEPT_EULA=YES \
/home/ljy/miniconda3/envs/env_isaaclab/bin/python \
tools/topology_authoring/launch.py --scene living_room_06 --no-browser --headless
```

```bash
OMNI_KIT_ACCEPT_EULA=YES \
/home/ljy/miniconda3/envs/env_isaac/bin/python \
tools/topology_authoring/launch.py --scene living_room_06 --no-browser --headless
```

The real startup runs reached readiness with no semantic-extension import error;
the validation harness then terminated the long-running editor process. The focused regression suite then
passed **106 tests**; changed Python compilation and `git diff --check` also pass.

All 30 current map projects were cleaned through atomic fixed-current saves. The audit
preserved **6203 nodes and 5534 edges**, while removing **376 region records**
(shared geometry counted once per graph), **516 entities**, **0 interaction
poses** and **668 named routes**. All current retired collections are empty;
region grids are zero and obsolete instance labels/region colors were removed.
Geometry basemaps remain; saves use one fixed `current/` package and remove historical revision directories. A repeated cleanup made no data changes. Independent semantic points were initialized empty rather than
converted from old entities.

Evidence is under
`/home/ljy/Documents/Codex/agent-team/workspaces/topology-semantic-points-20261020/artifacts/`.
`final-acceptance/cleanup-report.md` lists every scene's counts and original/final
revision; `cleanup-audit.json`, `cleanup-idempotent.json`,
`web-state-after-reopen.json` and `web-hidden-layer.json` retain detailed results.
The source audit matched 29 of 30 original USD hashes. `stadium.usda` changed
outside this work and matches the user's archived asset; it was not restored or
overwritten by cleanup.

A follow-up GLM-5.3-Flash acceptance was attempted after the provider became
visible again. The bridge successfully advertised the Flash model, but both live
smoke attempts disconnected in the ZCode app-server during `zcode_plugins`, before
a review session or image attachments could be created. Therefore no new Flash
visual verdict is claimed; the native/browser evidence above remains the source
of acceptance.

This record covers the fixes following `b5e819efd9c4515c06e72433628fb3a130abf544`.
The version 1 implementation keeps the existing directed XY shortest path and node PID.
Scene selection, task orchestration, physical evaluation and experiment identity
remain host responsibilities. No formal scene/task contract is introduced.

The later version 2 multilevel implementation and Go2 stair validation are
recorded separately in [MULTILEVEL_VALIDATION.md](MULTILEVEL_VALIDATION.md).

## Historical linked dual-graph editor with regions — 2026-10-20

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

## 覆盖式保存与直接 Isaac 操作 — 2026-10-21

本轮将保存目标固定为每张地图的 `current/`：连续保存覆盖同一目录，`project.json.current_revision` 始终为 `current`，逻辑修订号仍用于并发检测、撤销和同步。事务日志覆盖目录交换、索引更新和中断恢复；30 张地图审计通过，保留 6203 个节点、5534 条边，语义点为 0，区域/实体/交互点/命名路线集合为空，未残留 `revisions/` 或待处理事务目录。审计结果保存在 `artifacts/operation-logic-20261021/current-audit.json`。

固定存储回归共 10 项通过；统一编辑和导航回归共 228 项通过、2 项历史跳过。测试覆盖重复保存、并发冲突、外部修改拒绝、索引失败、提交前后进程中断、旧 revision 迁移、读写锁和清理告警。

在 Isaac Sim 5.1 实机中使用实际鼠标/键盘事件验证：未预选节点直接点击切换、2.5D 平面拖动保留 Z、3D 高度手柄、两次节点点击后的边预览、Enter 提交、Esc 取消、工具切换退出物体绑定模式、整边平移一次提交、相机 Alt/右键/中键导航不产生拓扑事务，以及保存两次仍使用固定 `current/`。作者 USD 根层和源 USD SHA-256 保持不变。核心记录为 `artifacts/operation-logic-20261021/isaac-operations.json`。

Flash 审查已按用户恢复的 provider 状态重新检查插件和 MCP，但 `zcode_create(GLM-5.3-Flash)` 仍返回 “Requested model unavailable or provider ambiguous; specify provider. Candidates:”。因此没有伪造 Flash 验收结果。

## Isaac 拖动提交链路修复 — 2026-10-21

针对“红色选中球移动但网页坐标不变”的竞态，Isaac 编辑 dispatcher 继续保持
单飞；选择或上下文请求占用通道时，拖动会冻结为一个完整的待提交草稿，前一
请求结束后只提交一次。移动响应会刷新服务端完整快照；移动失败会清掉 session
layer 预览并回退到权威坐标，409 则保留可显式重试的草稿。窗口状态栏现在区分
“预览中”“提交中”“已同步到网页”“提交失败，已回退”和“存在待重试草稿”。

Kit 某些版本会把已经越过拖动阈值的 DragGesture 报告为 `CANCELED`。viewport
适配器会用最后一个采样点完成一次拓扑提交；显式 Esc、换图、换图层、断连仍然
先清除 controller 手势，因此不会误提交。

新增回归覆盖选择请求忙时的拖动排队、选择失败不丢待提交拖动、成功响应权威快照
刷新、409/网络失败回退、状态栏状态和 canceled-release 提交。Isaac/编辑/覆盖式
保存重点集合共 **106 passed**（其中本轮客户端文件 33 项），`git diff --check`
和 Python 语法检查通过。

使用 `OMNI_KIT_ACCEPT_EULA=YES` 启动了 `living_room_06` 的 Isaac Sim 5.1
GUI，服务端状态为 ready，确认空中图两个节点可见，并通过同一 `/api/edit` 链路
执行一次临时 XY 移动后撤销；服务端坐标恢复，`current/topology-aerial.json`
和作者 USD 均未被写入。该次环境没有可用的 CUA 原生窗口句柄，因此未把这次
启动计入“真实鼠标拖动”验收；真实鼠标路径由上述 Kit 手势回归和此前临时场景
验收记录覆盖。

## Isaac 原生物体选择隔离 — 2026-10-21

确认场景加载使用作者 USD 且拓扑命中走投影几何；“拓扑点与场景物体同时被选中”
来自 Kit 的 `ObjectClick`/`Selection` manipulator 与拓扑手势同时接收同一鼠标事件，
不是场景加载模式改变了数据。普通点、边和语义点编辑期间，viewport 通过
`omni.kit.viewport.utility.disable_selection(..., disable_click=True)` 禁用原生
Prim 点击和框选，桥接层同时忽略并清空迟到的 USD selection，启动后也清除遗留选择。
当时仅在绑定 Isaac 物体时恢复原生选择；该流程现已退役，拓扑编辑始终使用拓扑拾取。

回归验证：Isaac 桥接、拓扑手势和 overlay 共 76 项通过；Isaac Sim 5.1 headless
启动 `living_room_06` 正常到达 app ready，未出现 semantics 扩展导入错误，作者 USD
未被写入。
