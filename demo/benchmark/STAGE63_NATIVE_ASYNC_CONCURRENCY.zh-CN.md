# 阶段63：R1 收口——原生异步并发验证（餐厅异步 backend）

日期：2026-09-25；分支 `codex/benchmark-stage57-oracle-integration`，基线 `51baf60`
（ljy worktree 实际检出 `298401be`＝基线＋并行任务的 405 条件映射提交，本阶段未改任何现有模块）。
目标：把 23e34bc 落地的 runner 异步执行路径接到真实 Isaac 后端，在 ljy `cuda:0`
上产出原生并发证据（真实重叠区间＋双方 skill 回执），并至少再覆盖一类争议证据（本阶段覆盖互斥）。
本阶段全部结论均为诊断证据；`release/scoreable/execution_validated` 恒为 `false`。

## 新增文件

- `demo/benchmark/goal_restaurant_async_backend.py`：异步能力 backend＋最小原生驱动 CLI。
- `source/EAI/test/test_benchmark_goal_restaurant_async_backend.py`：本机 Isaac-free 测试（fake 机队）。
- 本文档。

未修改 runner、执行泵、fleet 控制器或任何既有模块。

## backend 接口摘要

`RestaurantAsyncExecutionBackend` 包装 Stage53 的原生八机基座
（`RestaurantFleetController`，即执行泵驱动的同一 tick 所有者）：

- `supports_async_execution=True`；runner 构造期能力协商（`advance_idle` 可调用、
  `completed_deliveries` list 型）全部满足，串行协议形状不变。
- `execute()`：导航任务先 `plan_route`＋`begin_navigation`（deadline=计划窗口末），
  随即返回 `in_flight`（控制指令已真实下发，不等待到达）；`observe` 仅在挂接原生
  观察提供者时执行，否则记录平台 gap 后按 `failed` 收据终止；`relay` 未接入
  readiness transport，同样以平台 gap 显式失败，绝不伪造成功。
- `advance_idle(target_s)`：真实全八机 control tick 步进；到达最早一个原生终态即停，
  交回 runner 提交收据。终态分类沿用泵语义：路线链续段、到达后按实测区域成员判定
  （`arrival_outside_region_membership`→failed）、deadline/stall 失败、取消。
- 收据按泵的双时间语义：receipt 的 `completed_at_s` 是不可变物理完成时刻（原生到达
  时刻），backend 归档额外记录 `staged_at_s` 与 runner 侧 drain 边界
  （`delivery_journal`）。
- 资源互斥：机队自身的"同机仅一个活动导航"约束从 `begin_navigation` 的原生拒绝中
  捕获，转成 `ResourceConflictExecutionRefused` 并以 `layer="native_fleet_mutex"` 记入
  `ConcurrencyProbeRecorder`；任务资源键沿用 `task_resource_keys` 镜像。
- 终态记录密封（`sha256:` 前缀 canonical hash）进入回执 `evidence_refs`。

驱动 CLI（同文件 `main()`）：预检（复用 `inspect_candidate`，要求 ready）→
`open_simulator_session` 打开 full8 选择 → `begin_episode` 预热 → 构造 recorder/backend →
`FormalGoalEpisodeRunner(execution_mode="async", concurrency_recorder=...)` 运行双 Carter
分区巡检计划（spawn_carter_1→kitchen_service、spawn_carter_2→dining_service，窗口
[0,45)s）→ runner 结束后在同一活机队上做组件级原生互斥 probe → 以
`build_concurrency_evidence_report` 写最终并发证据。

## 本机验证记录

- 新增测试 `source/EAI/test/test_benchmark_goal_restaurant_async_backend.py` 8 项通过
  （`env_isaaclab` python，pytest，无 Isaac）：能力声明与构造校验、指定出生区初始
  成员映射、fake 机队并行导航 in-flight→真实终态回执＋证据重叠、原生机队互斥拒绝
  记录、终止后 execute 拒绝、observe/relay 平台 gap、in-flight 取消关区间、以及真实
  `FormalGoalEpisodeRunner` 异步循环＋真实餐厅部署契约的 runner 级集成（fake 机队）。
- 既有 `test_benchmark_goal_concurrency_evidence.py` 20 项通过（未回归）。
- ljy worktree 内同一测试文件再次 8 项通过。

## ljy 原生运行

环境：ljy（robot），Ubuntu 22.04.5，RTX 5080（driver 580.159.04），
`/home/ljy/miniconda3/envs/env_isaaclab/bin/python`（3.11.16），`--device cuda:0`，
`--seed 17`，headless，ROS 桥关闭。资产配方：
`EAI_USD_ROOT=/home/ljy/eai-simulator/usd`，
`EAI_CONTROLLER_ROOT=/home/ljy/eai-simulator/source/EAI_assets/EAI_assets/controller`，
`EAI_NVD_ASSET_ROOT=/home/ljy/NVD_assets_sanitized`，`EAI_ASSETS_AUTO_DOWNLOAD=0`，
products `…/benchmark-scoring-20260923-19eee7e/tmp/restaurant_benchmark/products-candidate-v7`
（`inspect_candidate`＝ready，源闭包指向 `/home/ljy/eai-simulator/usd/scene/restaurant/Restaurant_Only.usda`）。

部署（本机未运行任何 git 变更命令；worktree 元数据操作仅在 ljy 侧，属规定 bundle 流）：

```bash
git bundle create /tmp/stage63-native-async.bundle HEAD        # HEAD=298401be（含基线 51baf60）
scp /tmp/stage63-native-async.bundle ljy:/tmp/
scp demo/benchmark/goal_restaurant_async_backend.py ljy:/tmp/goal_restaurant_async_backend.py
scp source/EAI/test/test_benchmark_goal_restaurant_async_backend.py ljy:/tmp/
# ljy：
cd /home/ljy/eai-simulator
git fetch /tmp/stage63-native-async.bundle HEAD:refs/heads/stage63-native-async
GIT_LFS_SKIP_SMUDGE=1 git worktree add \
  /home/ljy/eai-simulator-worktrees/stage63-native-async-20260925 stage63-native-async
cp /tmp/goal_restaurant_async_backend.py …/demo/benchmark/goal_restaurant_async_backend.py
cp /tmp/test_benchmark_goal_restaurant_async_backend.py …/source/EAI/test/
```

预检（Isaac-free）：`python -m demo.benchmark.goal_restaurant_async_backend … --preflight-only`
→ `{"status": "preflight_ready", …}`。原生运行（nohup，`tmp/stage63_launch.sh` 内核命令）：

```bash
/home/ljy/miniconda3/envs/env_isaaclab/bin/python -m demo.benchmark.goal_restaurant_async_backend \
  --products-root /home/ljy/eai-simulator-worktrees/benchmark-scoring-20260923-19eee7e/tmp/restaurant_benchmark/products-candidate-v7 \
  --output "$WT/tmp/stage63-native-async-1" \
  --episode-id stage63-native-async-1 \
  --device cuda:0
```

结果：进程 exit 0，墙钟 78.87s（18:42:41–18:44:02），runner 3 轮、`error=null`；
八机 240 tick 预热后（origin physics step 962）异步泵 1058 tick＋互斥 probe 1037 tick，
原生机队时间 41.9s（physics step 9342）。归档：
ljy `$WT/tmp/stage63-native-async-1/`（本地镜像
`tmp/stage63-validation/stage63-native-async-1/`），未动 ljy 主仓与其他 worktree。

## 原生证据行

- 原生重叠（`episode/concurrency_evidence.json`，runner 级）：`nav-dining` 实际执行
  `[0.0, 12.82]`、`nav-kitchen` `[0.0, 21.16]`，二者均 `succeeded`，
  重叠区间 `[0.0, 12.82]`＝**12.82s**；计划窗口重叠 `[0,45]` 仅作上下文
  （`planned_overlap_not_counted=[]`，无把计划重叠当并发的记分）。
- 双方 skill 回执（`episode/skill_receipts.json`，经真实 `GoalSession.complete_skill`
  提交）：`stage63-native-async-1/nav-dining/async/1`（12.82s）与
  `stage63-native-async-1/nav-kitchen/async/2`（21.16s），evidence_refs 指向密封原生
  终态记录（如 `sha256:ca76c32a…`、`sha256:1d8a2ca6…`）。
- 互斥（组件级原生 probe，`concurrency_evidence_native.json`）：`probe-mutex-holder`
  真实导航 `[21.16, 41.9]` succeeded（到达 staging，回执
  `stage63-native-async-1/probe-mutex-holder/async/3`）；同时刻对同机
  `restaurant_carter_1` 的第二次导航被机队原生拒绝 →
  `ResourceConflictExecutionRefused`，记录
  `{task_id: probe-mutex-refused, resource_key: agent:restaurant_carter_1,
  held_by: probe-mutex-holder, layer: native_fleet_mutex, at_s: 21.16}`；
  `resource_intervals["agent:restaurant_carter_1"]` 显示该机串行持有
  nav-kitchen [0,21.16] → probe-holder [21.16,41.9]，无双重持有。
- 离线读回审计：3 份密封终态记录逐条重算 hash 一致；runner 回执 evidence_refs 全部
  被密封原生终态覆盖；runner 与最终原生证据的重叠对逐字段一致；会话账本
  （`goal_session_report.json`）两任务 `succeeded` 且完成时刻/回执引用与回执文件一致。
- 双时间归档（`async-backend-archive.json` delivery_journal）：
  nav-dining 物理 12.82/staged 12.82/drain 边界 21.16；nav-kitchen 21.16/21.16/21.16；
  probe-holder 41.9（无 runner drain，组件级）。平台 gap 计 0。

## 与 fake 层对照

| 维度 | fake 层（既有 R1 证据） | 本阶段原生层 |
| --- | --- | --- |
| 执行体 | `ConcurrentFakeBackend` 假时钟 | 真实 `RestaurantFleetController`（Isaac `cuda:0`，八机全量 tick） |
| 完成时刻 | 假钟到达 `planned_end_s` 即完成 | 真实到达时刻（12.82/21.16s，早于 45s 窗口末） |
| 重叠来源 | 计划窗口端点截断（runner 测试 8.0s） | 物理同时执行区间（12.82s，与 Stage53 的 12.74s 同量级） |
| runner 循环 | 同一 `FormalGoalEpisodeRunner` async 两阶段泵 | 同一循环，未改 runner |
| 证据归档 | 同一 `ConcurrencyProbeRecorder`/`build_concurrency_evidence_report` | 同一权威函数与 schema v1 |
| 互斥证据 | backend 镜像资源键拒绝 | 机队原生 active-navigation 拒绝（`native_fleet_mutex` 层） |

关键差别：fake backend 在 `planned_end` 人为收口；原生层由物理到达收口，且互斥拒绝
来自机队自身约束而非镜像键。两者共用同一 runner 与证据权威，故 fake 层结论
（结构能力）与原生层结论（物理事实）分层成立。

## 争议四类覆盖状态

- 真实并发重叠：**触发并归档**（runner 级原生，见上）。
- 资源互斥：**触发并归档**（组件级原生 probe，同活机队同 recorder；层级如实标注
  `component_native_probe_on_live_fleet`。不能在 runner 级触发的原因：GoalSession
  准入层会先拒绝同机二次 dispatch，无效参与者批次将使 episode 失败闭门）。
- 终止后不启动：`not_triggered`（本次最小运行无 human-assistance 终止路径；缺口，
  留待后续在原生运行中引入 not_deployable 声明或成员退出来覆盖）。
- 取消竞争：`not_triggered`（无取消动作）。
- 飞行中成员退出：`not_triggered`（无成员退出）。

## 限制与缺口（如实）

- v1 证据模板的静态文案仍为 fake 层措辞（`limitations` 含
  `fake_backend_no_physical_execution`、`integration_gap.native_validation` 称"全部来自
  fake backend"）。本原生运行的执行事实（mode=`native_in_flight`、密封原生终态、
  物理时刻）在 executions/overlap/resource_intervals 中如实存在；模板文案只会
  **低估**而非夸大证据等级。修正该文案需要改 `build_concurrency_evidence_report`
  （既有模块），本阶段按约束未动，留作后续 schema v2 工作。
- 基线 `simulator.py:1894` 向 `AerialSensorSuiteManager` 传 `publish_ros`，而本谱系
  （`4d01e94` 引入该调用）的套件构造器无此参数：任何含 aerial 的选择走默认
  `aerial_sensor_backend="native"` 都会 `TypeError`。本运行使用既有的
  `aerial_sensor_backend="disabled"`（full8 选择全部 `attachments: []`，满足其
  附加物空闲要求）：aerial 物理保留、两台 Iris 全程 hover-hold，本诊断不消费任何
  aerial 传感流。该基线回归应单独修复。
- 双时间：runner 异步路径经 `complete_skill` 单时刻提交，会话无
  `result_delivered_at_s`；泵的 `complete_skill_delivery` 双时刻通道未被 runner 异步
  模式使用，物理/暂存/drain 三时刻保存在 backend 归档中。
- observe/relay 未接入原生运行（平台 gap 语义显式失败）；readiness transport、
  业务评估、通信计量均不在本阶段范围。运行最小化：1 个 episode、seed 17、
  cuda:0、双技能计划；probe 为组件级。
- 成员退出/终止/取消三类原生证据缺口如上；未声明碰撞安全、净空或业务完成。

`release_ready=false`、`scoreable=false`、`execution_validated=false`、
`business_outcomes_validated=false`，全程保持。
