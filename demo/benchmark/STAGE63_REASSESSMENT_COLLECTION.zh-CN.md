# 阶段63：运行中重评估的原生采集与扩展签发（机制就绪，原生采集被 GPU 物理阻塞）

日期：2026-09-25。执行：ZCode 工程代理（benchmark-task 侧）。
基线：`codex/benchmark-stage57-oracle-integration`，本地起点 `51baf60`，
运行期间并行任务推进到 `298401b`（ljy worktree 与之同步）。本机（pupil）
未启动 Isaac；两次原生尝试均在 ljy 隔离 worktree、`--device cuda:0` 完成
（见第 5 节）。全部 release 标志恒为 false。

## 0. 结论摘要

- **注入机制已交付并双机验证**：新文件
  `demo/benchmark/goal_restaurant_reassessment_injection.py`（sha256
  `a239afe34e2f49bfcd40293746e374d50c7542b4837fe88f0e78d0323767a25f`）以
  组合方式包装公开运行入口与 `RestaurantExecutionPump`，不改任何现有模块；
  配套测试 `source/EAI/test/test_benchmark_goal_restaurant_reassessment_injection.py`
  （sha256 `ad15b092970b151ddac69691d4197d8733bd6803d060d034237f0a6c723d63ee`）
  本机与 ljy 各 **15 passed**（连同 reassessment 既有套件 52 passed）。
- **本地合成链端到端走通**：真实 `AuditedGoalSession` → 注入 2 个 epoch →
  collection → review（digest 稳定）→ `issue_reassessment_extension` →
  `RestaurantReassessmentOracle(extensions=[...])` 对两个边界报告均返回
  **verified/deployable**（`test_issue_and_verify_end_to_end_against_synthetic_certificate`）。
- **原生采集被阻塞（交接问题 1）**：设备策略澄清后按 `--device cuda:0`
  执行的两次采集（seed 103、seed 97）**确定性失败于 t=61.0**——GPU 物理下
  carter_2 的到达保持停在 dining_service 容差圆边缘并即刻漂出，v3 合约对
  observe-1 的 dispatch 正确 fail-closed。按协调决策停止餐厅 GPU 并行测试，
  问题交接 tool 工程师修复（详见第 5 节，含复现命令与全部数值）。
- **附带发现交接问题 2**：fusion 提交 `4d01e94` 起 `simulator.py` 向
  `AerialSensorSuiteManager` 传入其 `__init__` 不接受的 `publish_ros` 参数，
  阻塞一切含空中机器人的 `open_simulator_session` 调用（第 6 节）。
- **扩展签发状态**：因原生采集未产出 collection，未发生实际签发。模块内
  `issue` 命令已实现完整链路与受阻路径：若采集 runtime profile 落在证书
  允许域外（cuda:0 必然如此，见第 7 节），写出"签发前一步"文档
  （extension input + 明确缺失清单）并以退出码 1 fail-closed。

## 1. 设备策略澄清与执行记录

- 任务书原定 `--device cpu`（GPU 槽被并行任务占用）；运行中收到澄清：
  **场景任务并行走 GPU 槽（cuda:0），后续原生运行一律 cuda:0，不回退 cpu**。
- 本批两次原生尝试（seed 103 / seed 97）均在澄清后按 `--device cuda:0`
  执行，`runtime-configuration.json` 实测 `effective_device: cuda:0`；未做
  任何 cpu 运行。cpu 证据仅引用 Stage60 存档（见第 5 节对照）。
- 后续对餐厅场景的 GPU 并行测试按协调决策暂停，待 tool 工程师修复后推进；
  本文档第 5 节即为交接记录。

## 2. 注入机制设计（新文件，零现有模块改动）

`goal_restaurant_reassessment_injection.py` 提供三个子命令：

- `run`（原生）：复用 `goal_restaurant_public_run.prepare_request` /
  资产预检 / oracle 输入采集，镜像其 native 路径，但由
  `ScriptedReassessmentInjector.run(max_ticks)` 自行驱动 `pump.step_once()`，
  在到达剧本 `at_s` 的首个 tick 边界注入事件：
  - `member_exit`：先以"去除退出成员"的实测区域刷新可信 runtime state，
    再 emit 公开生命周期事件——冻结的 deployment gate 因此携带**变更后
    花名册与同时刻 runtime 快照**（与 `GoalRestaurantInterventions` 的
    回调纪律一致）；emit 后再次回写快照清除 v3 stale 标记，参与者在新
    决策后可继续 dispatch（该顺序缺失曾真实导致 r2 失败，已修复并有测试）。
  - `capability_change`：按当前声明移除指定能力、严格递增 capability
    version 后重声明（例如移除闲置成员的 `relay`）。
  - `milestone_state_change`：**fail-closed 拒绝**——Goal v3 会话事件词汇
    无此类型，里程碑 epoch 在会话合约扩展前无法原生产生（任务书预设剧本
    的 t=120 milestone 项因此不可执行，属已知缺口而非静默改标）。
  - 每个 epoch 落盘 `reassessment-epochs/epoch-N.json`：变更后快照
    （roster/capability/resource/deployment_change 版本、runtime_state 及
    其 sha256）、established facts（按 readiness service 派生：receipt→
    三事实齐全；仅观测→两区域事实）、剩余预算（240000−t·1000）、以及
    **决策前 oracle 边界会话报告**（离线 oracle 复算的唯一输入）。运行期
    评测按 `restaurant_runtime_reassessment_extension_pending` 记录为
    failed/null（扩展在采集时尚不存在）。
  - 结束时用 `collect_reassessment_epoch_inputs` 归档 collection 兼容格式。
- `issue`（离线）：collection → `review_reassessment_extension_collection`
  复核（digest fail-closed）→ `build_extension_input` → 填充事实分区
  （required_true=该 epoch established facts、varying=其余）与 qualification
  （scope 继承证书、`native:reassessment:<raw_archive_sha256>` 证据、
  runtime profile 允许列表、试验槽）→ `_freeze_extension_registration` +
  `validate_restaurant_reassessment_extension` 自校验。**profile 不在证书
  允许域时写出受阻文档**（含 extension input、established facts、raw
  archive 摘要、缺失清单），退出码 1。
- `verify`（离线）：加载证书 + 可信 authorities + 签发文档，对每个边界
  报告重放 `RestaurantReassessmentOracle`；省略 `--extension` 时按无扩展
  fail-closed 复放（预期 `runtime_epoch_outside_qualified_domain`）。

本机纯 Python 测试覆盖：剧本校验（顺序/重复/未知类型/里程碑拒绝/能力
移除约束）、事实派生三分支、member_exit/capability_change 冻结语义
（gate 状态、roster/版本、runtime_state 覆盖、边界捕获时序、pending 评测）、
泵循环驱动、边界前/无区域 fail-closed、签发域校验（外来 profile、未知
事实、缺事实）、以及第 0 节的合成端到端链。

## 3. ljy 环境与工件

- 隔离 worktree：`/home/ljy/eai-simulator-worktrees/benchmark-stage63-reassessment`
  （`git bundle` 流创建于 `298401b`；未触碰 ljy 主仓检出）。资产配方沿用
  Stage62：`EAI_USD_ROOT=/home/ljy/eai-simulator/usd`、
  `EAI_NVD_ASSET_ROOT=/home/ljy/NVD_assets_sanitized`、
  `EAI_CONTROLLER_ROOT=/home/ljy/eai-simulator/source/EAI_assets/EAI_assets/controller`、
  `EAI_ASSETS_AUTO_DOWNLOAD=0`；products root
  `/home/ljy/a/b/c/d/products-candidate-v7`。
- Stage60 v2 证书（只读引用）：证书摘要
  `9f99a849a08c94a9d38614c0e7ae0cb043105b3b35727d79f1efeae07a995e86`
  （`tmp/stage60-initial-oracle-calibration/v2/issued.json`，本机）；
 ljy 可信 authorities 取
  `.../ljy-certificate-use-seed101/initial-oracle-inputs.json`。
- 运行输出（均保留为证据）：`tmp/stage63/runs/` 下
  `reassessment-collection-ljy-seed103-failed1`（publish_ros 崩溃，见第 6 节）、
  `reassessment-collection-ljy-seed103-r2`（dispatch 竞态，修复前，无 session 归档）、
  `reassessment-collection-ljy-seed103-r3`（episode id `...seed103r4`，命名
  差异为重试改名的副产物）、`reassessment-collection-ljy-seed97-r1`。
  启动脚本 `tmp/stage63/launch.sh`，日志 `tmp/stage63/collection*.log`。

## 4. 交接问题 1：餐厅公开构造在 GPU 物理下确定性失败（阻塞原生采集）

**现象**（两次运行数值完全一致）：`--device cuda:0`（`effective_device`
实测 cuda:0），seed 103 与 seed 97：

| 项 | 数值（两运行相同） |
| --- | --- |
| nav-1（carter_2→dining_service）物理完成 | t=12.88 s |
| carter_2 保持 dining_service 域内映射 | t=0.02→14.9 s，此后 matches=[] |
| t=61.0 时 carter_2 位置 → dining 中心距离 | `[14.0496, 21.7468]` → `[14.225, 21.925]`，距离 **0.2500**，容差 **0.25**（闭盘判据 `<=` 失败） |
| observe-0（carter_1→kitchen，nav-0 t=21.2 完成） | t=61.0 正常 dispatch（carter_1 始终在 kitchen 域内） |
| observe-1 dispatch | `ValueError: a fresh trusted runtime snapshot with selected-robot positions is required`（`goal_session.py:293`，`_admit_dispatch`→`_require_snapshot`） |
| 泵步数 / 失败时刻 | 3049 ticks / session time 61.0 |

**判定**：GPU PhysX 下 diff 到达保持把 carter_2 停在容差圆边缘（0.2500 vs
0.25）并即刻漂出闭盘；`_measure_regions` 无匹配区域→runtime snapshot 缺该
成员→v3 合约对 observe-1 的 dispatch fail-closed。该行为与种子无关（两种子
轨迹一致），与设备相关：Stage60 的 cpu 运行（ljy seed97/seed101，同
products/合约）observe-1 在 [61,71) 窗口正常 dispatch 且 5/5 技能完成
（`_check_construction` 曾强制该窗口）。即：**Stage60 构造域仅覆盖 cpu 物理，
cuda:0 尚在其演示域之外**——这与证书 runtime profile 只收 cpu 配置一致。

**给 tool 工程师的修复建议入口**：
- 到达保持/微漂移：carter_diff 控制器到达后的位姿保持（`controller/traditional/carter_diff`）
  与 dining 路线终点的目标点设置；或到达判据与区域容差的耦合
  （`navigation_probe.FINAL_TOLERANCE_M`、`goal_restaurant_fleet` 的 arrived
  判定）。
- 复现（ljy worktree 根，环境见第 3 节）：
  `python -m demo.benchmark.goal_restaurant_public_run --products-root /home/ljy/a/b/c/d/products-candidate-v7 --output <新目录> --episode-id <新id> --communication-contract tmp/stage63/communication.json --publication-treatment --device cuda:0 --seed 97 --warmup-ticks 240 --max-ticks 4200`
  （无需注入模块，公开入口即在 t=61 复现；需要第 6 节的 simulator.py 修复或 pin。）
- 完整失败档案：第 3 节 `reassessment-collection-ljy-seed97-r1/`
  （session/pump/injection-error 齐全）。

**对 R1 链路的次生影响**：即便构造修复，cuda:0 采集的 runtime profile
摘要（ljy 实测 `804483a05b9f8c42131550d324576c6ab5bfce52c72f4eafbca02868667b9a08`）
不在 Stage60 v2 证书允许列表（仅 local/ljy 两个 **cpu** profile：
`07c6a841…`、`8cd5a0bc…`）内，`eai.restaurant.reassessment_extension`
按规则（extension profiles ⊆ certificate profiles，不可放宽）无法绑定该
证书。要在 GPU 槽闭环签发，需要（a）修复构造后（b）为 cuda:0 profile 补
一次初始证书校准或出接受 GPU profile 的证书修订——这两项都在签发方
（trusted authority）一侧，不是本模块能自证的。

## 5. 交接问题 2：`publish_ros` 回归阻塞含空中机器人的会话启动

- 提交 `4d01e94`（阶段61 fusion）起，`simulator.py`
  `_setup_aerial_sensor_manager` 调用
  `AerialSensorSuiteManager(base_env, specs, seed=…, publish_ros=…)`，但
  `source/EAI/EAI/hmrs_ros/aerial_sensor_suite.py` 的 `__init__` 签名为
  `(env, specs, *, seed=0, runtime=None)`——`TypeError: unexpected keyword
  argument 'publish_ros'`。
- 影响面：一切经 `open_simulator_session` 且 selection 含空中机器人（规格
  非空）的运行。Stage62 的 warehouse 引擎无空中机器人（specs 为空提前
  return）故未暴露；餐厅 full8（iris×2）立即触发。
- 本批处置（如实记录，未改任何仓库文件）：在 **ljy worktree 内**以
  `git checkout 39bbf46 -- simulator.py` 将启动器钉回 Stage60 版本
  （uncommitted、仅该 worktree；`goal_restaurant_public_run._source_snapshot`
  不对 simulator.py 计摘要，请求源清单不受此 pin 影响）。仓库侧修复
  （补参数或改调用）留给 tool 工程师。

## 6. 签发与端到端状态（当前批次）

- **未签发**：无原生 collection → 无 review digest → 无 extension。此为
  采集阻塞的直接后果，非签发链缺陷；签发/受阻/复验路径已在本机与 ljy 的
  单测和合成端到端中验证（第 0、2 节）。
- 若后续在 cpu 域完成采集（或 GPU 修复 + 证书域扩展后），`issue` 的缺失
  清单机制会显式列出：(a) 采集 runtime profile 需进入证书允许列表；
  (b) 采集树执行依赖清单相对 Stage60 证书清单存在漂移（`demo/benchmark`
  新增 10 文件、runner 等已变更），该漂移使同 episode 的 epoch-0 初始
  oracle 必然 `execution_authority_mismatch` fail-closed——运行期证据已如实
  归档（两次运行的 `initial-oracle-report.json`）。
- 里程碑 epoch（`milestone_state_change`）在 Goal v3 会话词汇扩展前不可
  原生产生：注入端 fail-closed 拒绝，oracle/collection 侧词汇已就绪。

## 7. 工件位置

```text
本机（pupil，未提交、未跟踪）
  demo/benchmark/goal_restaurant_reassessment_injection.py        注入/签发/复验模块
  source/EAI/test/test_benchmark_goal_restaurant_reassessment_injection.py
  demo/benchmark/STAGE63_REASSESSMENT_COLLECTION.zh-CN.md         本文档
ljy（/home/ljy/eai-simulator-worktrees/benchmark-stage63-reassessment）
  tmp/stage63/launch.sh、collection*.log                          启动脚本与日志
  tmp/stage63/runs/reassessment-collection-ljy-seed103-failed1    publish_ros 崩溃档
  tmp/stage63/runs/reassessment-collection-ljy-seed103-r2         dispatch 竞态档（修复前）
  tmp/stage63/runs/reassessment-collection-ljy-seed103-r3         cuda:0 seed103 完整失败档
  tmp/stage63/runs/reassessment-collection-ljy-seed97-r1          cuda:0 seed97 完整失败档
  worktree 内 simulator.py 钉于 39bbf46（uncommitted，见第 5 节）
```

## 8. 范围与 release 声明

本批交付注入机制与其纯 Python 验证、签发/受阻/复验 CLI、合成链端到端
verified/deployable 演示，以及两项交接问题的完整证据。未产生原生
collection/review digest/扩展签发；未验证 GPU 物理域的任何餐厅可行性结论。
全部 `execution_validated / business_outcomes_validated /
feasibility_oracle_verified / release_ready / scoreable` 恒为 false。
