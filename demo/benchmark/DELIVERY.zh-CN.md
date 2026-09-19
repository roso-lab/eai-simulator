# Benchmark 任务生成与审核 · 交付状态总览

整理日期：2026-09-19
工作来源：codex 会话 01a0aa7a（阶段 1–18）及其前置/并行会话，分支 `codex/factory-vertical-challenge`。
本文是整个 benchmark 工程的交付入口：先讲清工程结构和文档地图，再按「已完成 / 现在可用 / 待开发被阻断」三档给出交付状态。阶段级历史证据见 [TODO.zh-CN.md](TODO.zh-CN.md) 与各 `STAGE*_REVIEW` 记录，本文不重复其细节。

**一句话结论**：四场景（Factory / Warehouse NVD / Modern House NVD / City）的任务生成与审核本体——任务族、骨架、公开题包、隐藏评测边界、因子冻结、统一审核 CLI——已经完成并可复跑；剩余未收口项均不在任务生成侧手中，而是三个外部 blocker（Carter 碰撞源资产、City 正式 episode 绑定、House 业务干预见证）加楼梯 RL 运动模型（已决定替换后再验）。

---

## 1. 工程结构

```text
demo/
  benchmark/                     场景公共层 + 任务生成与审核核心（70 个模块）
  factory_benchmark/             Factory 场景：F-INS-01 证据派遣 + F-VERT-01 楼梯诊断
  warehouse_benchmark/           仓储场景：10 节点订单链 + 原生物理 probe
  modern_house_benchmark/        家居场景：11 节点恢复链 + 原生 detector/path 后端
  city_benchmark/                城市第四场景：接入/episode 适配/服务见证（候选）
    scene/                       城市场景运行时子包（原 demo/city，2026-09-19 合并：
                                 事故序列/车辆/路人/Flow/环境配置钩子，可独立运行）
tools/
  scenario_generation/           任务生成器与审核 CLI（benchmark_generator、vertical_task 等）
  assets/                        NVD 资产准备、迁移、闭包审计、地图/体素重建
source/EAI/EAI/scenario_spec/    场景规范（ScenarioSpec、互斥 initial/reserve 清单等）
source/EAI/test/
  test_benchmark_*.py            benchmark 公共层专项测试（60+ 文件）
  test_factory_*.py              Factory 场景专项测试
  test_scenario_*.py             场景执行/协议/批量测试
usd/
  scene/{factory,warehouse_nvd,modern_house_nvd}/   场景 USD 与占据图/体素（运行时资产，不入库）
  effects/                       体积效果预设元数据（Omniverse 许可的 .usda 不入库）
```

城市包内部分层（依赖单向：`demo.benchmark 核心 → demo.city_benchmark → demo.city_benchmark.scene`）：
`city_benchmark/` 根层是任务生成与审核（prepare 产物、intake 审核、episode 适配、服务见证），
`scene/` 子包是场景运行时库——相当于其他三场景中「EAI_assets 场景配置」的角色。

`demo/benchmark/` 内部按职责分组：

| 分组 | 代表模块 | 职责 |
| --- | --- | --- |
| 题包契约 | `challenge_contract.py`、`scene_challenge.py`、`authoring.py`、`semantic_contract.py` | 四层题包（public / participant / baseline / hidden evaluator）的编译、作者语义审核、任务骨架 |
| goal-driven 主轨道 | `goal_driven_*.py`、`goal_suite.py`、`goal_freeze.py`、`goal_constraints.py` | 业务目标题面 + v2 participant API、语义 witness、数值约束审核、单因子冻结、统一生成/读回 CLI |
| 部署与诊断 | `goal_deployment*.py`、`goal_physical_*.py`、`goal_navigation_probe.py`、`goal_attachment_*.py`、`goal_native_*.py` | 四场景部署配置、Isaac 原生物理/对象/占用/形状 probe 与独立审核器 |
| 运行时 | `runtime.py`、`participant_runtime.py`、`participant_episode.py`、`control.py`、`scheduling.py`、`allocation.py` | 参与者回合执行、调度与分配、原子资源预留 |
| 证据链 | `provenance.py`、`control_step_evidence.py`、`domain_adapters.py`、`live_domain_collectors.py`、`reference_domain_providers.py`、`transport_evidence.py` | 源码/资产指纹、逐步控制证据、实时领域证据适配、参考 provider |
| 因子与生命周期 | `factor_matrix.py`、`factor_experiment.py`、`communication.py`、`team_lifecycle.py`、`interventions.py`、`lifecycle_plan.py`、`isaac_lifecycle_callbacks.py` | 通信/成员加入退出/能力变化因子、成对重放、预加载 reserve 回调 |
| 发布门禁 | `release_manifest.py`、`stage17_evidence_gate.py`、`stage18_evidence_gate.py`、`skill_ontology.py`、`reliable_skill_backend.py` | 统一发布审计、阶段证据汇总门禁、可靠技能契约与参考后端 |

## 2. 文档地图

「状态」列标注文档时效：**现行** = 反映阶段 18 现状；**历史** = 保留为证据/设计依据，条目不再更新。

| 类别 | 文档 | 状态 | 用途 |
| --- | --- | --- | --- |
| **交付状态** | 本文（DELIVERY.zh-CN.md） | 现行 | 总览、三档交付状态、剩余 blocker |
| 阶段清单 | [TODO.zh-CN.md](TODO.zh-CN.md) | 现行 | 阶段 1–18 权威勾选记录（fail-closed：证据齐全才勾） |
| 设计文档 | [BENCHMARK_DESIGN.zh-CN.md](BENCHMARK_DESIGN.zh-CN.md) | 现行 | 三场景任务意义、四类模型输入、决策权限与隐藏评测边界 |
|  | [TASK_GENERATION_PROGRESS_REPORT.zh-CN.md](TASK_GENERATION_PROGRESS_REPORT.zh-CN.md) | 现行 | 任务生成在整体链路中的位置、四步审核法、协作边界 |
|  | [TASK_DESIGN_REVIEW.zh-CN.md](TASK_DESIGN_REVIEW.zh-CN.md) | 现行 | 出题侧任务契约与审核规则 |
| 轨道文档 | [GOAL_DRIVEN_TRACK.zh-CN.md](GOAL_DRIVEN_TRACK.zh-CN.md) | 现行 | goal-driven 主轨道（业务目标题面 + v2 API） |
|  | [GOAL_SUITE.zh-CN.md](GOAL_SUITE.zh-CN.md) | 现行 | 统一生成/审计/读回 CLI 与冻结机制 |
|  | [GOAL_DEPLOYMENT.zh-CN.md](GOAL_DEPLOYMENT.zh-CN.md)、[GOAL_DEPLOYMENT_CONFIGURATION.zh-CN.md](GOAL_DEPLOYMENT_CONFIGURATION.zh-CN.md) | 现行 | 部署诊断入口、四场景配置与参考执行 |
|  | [GOAL_PHYSICAL.zh-CN.md](GOAL_PHYSICAL.zh-CN.md)、[GOAL_SCENE_BINDING.zh-CN.md](GOAL_SCENE_BINDING.zh-CN.md) | 现行 | 物理诊断边界、场景绑定清单 |
|  | [VERTICAL_TRAVERSAL.md](VERTICAL_TRAVERSAL.md) | 现行 | 楼梯垂直探针协议 |
| 阶段审核 | [STAGE12_REVIEW.zh-CN.md](STAGE12_REVIEW.zh-CN.md) ~ [STAGE18_REVIEW.zh-CN.md](STAGE18_REVIEW.zh-CN.md) | 历史（追加式） | 各阶段真实证据路径、SHA-256 与反思（阶段 12–18） |
| 场景文档 | [factory_benchmark/README.zh-CN.md](../factory_benchmark/README.zh-CN.md) | 现行 | Factory 入口：状态块、F-INS/F-VERT/F-TRAFFIC/F-COV、标签与派车基线 |
|  | [warehouse_benchmark/README.zh-CN.md](../warehouse_benchmark/README.zh-CN.md) | 现行 | Warehouse 入口：状态块、10 节点订单链、原生承载诊断 |
|  | [modern_house_benchmark/README.zh-CN.md](../modern_house_benchmark/README.zh-CN.md)、[NATIVE_BACKENDS.md](../modern_house_benchmark/NATIVE_BACKENDS.md) | 现行 | House 入口：状态块、11 节点恢复链、原生 detector/path 后端 |
|  | [city_benchmark/README.md](../city_benchmark/README.md)、[scene/README.md](../city_benchmark/scene/README.md)、[TODO.zh-CN.md](../city_benchmark/TODO.zh-CN.md)、[VALIDATION.zh-CN.md](../city_benchmark/VALIDATION.zh-CN.md) | 现行（VALIDATION 为日期记录） | 城市第四场景：prepare/intake、服务边界、场景运行时子包、验证记录 |
|  | `demo/<scene>_benchmark/README.md`（英文镜像） | 现行 | 顶部含英文状态块；中文版为权威 |
| 历史归档 | [HCS-IRD方案调研报告.zh-CN.md](HCS-IRD方案调研报告.zh-CN.md) | 历史 | 2026-09-15 调研讨论稿 |
|  | [factory_benchmark/ROADMAP.zh-CN.md](../factory_benchmark/ROADMAP.zh-CN.md) | 历史 | 启动期设计规划（结论已被后续实现吸收） |
|  | [factory_benchmark/TODO.zh-CN.md](../factory_benchmark/TODO.zh-CN.md) | 历史 | Factory 优先时期 P0–P6 阶段记录（已被主线 TODO 取代） |

## 3. 已完成（可复核）

以下条目均有代码 + 专项测试或磁盘产物支撑，可直接复跑验证。

### 3.1 任务族与题面（四场景）

| 场景 | 任务族 | 被测模型实际决定的事 |
| --- | --- | --- |
| Factory | F-INS-01 四阶段证据派遣（俯视观察→地面接近→近景取证→工位选择） | 等待、消费有效观测、选择 assembly/dock、提交导航 |
| Factory | F-VERT-01 楼梯路线意图探针（独立诊断入口，非业务协作题） | 为指定机器人提交楼梯路线意图 |
| Warehouse NVD | 10 节点订单链（货架核对→双侧作业→大件联合转移→集货→dock 交接→复核） | 成员分配、改派、导航目标提交、消费送达观测 |
| Modern House NVD | 11 节点恢复链（入口取证→家具整理→物品暂存→机器人撤离→动线复核） | 同上 |
| City | 三项业务服务（guide_people / control_traffic / report_incident）+ 受影响人员/车辆 | 服务调度与消费（候选诊断态，见 §5） |

### 3.2 题包与信息边界

- 四层题包每场景一套：`release/public_task.json` + `participant_api.json` + `evaluator/hidden_evaluator.json` + baseline；内容 hash 绑定，改文案或伪造 `success=true` 无法绕过审核。
- 三层信息边界：被测模型只见公开题面与实际送达事件；出题者持完整配置与候选团队；隐藏评测侧持对象身份真值、未来事件、通信计划与参考分配。
- `member_join`/`member_exit`/`capability_change`/通信延迟丢包过期为公开因子 + 隐藏 treatment；reserve 成员身份不进 `initial_fleet`。

### 3.3 审核链（生成即审核）

- LLM 候选生成器与正式题包编译器共用同一套作者语义审核（`validate_authored_business_scenario`）：错误场景道具、破坏因果骨架、reserve 泄漏、隐藏身份写进公开层等在候选池阶段即被拒绝。
- Factory 审核按 `factory_dispatch_v1` / 长流程 / 垂直探针分轨道，F-INS 不再被长流程模板误判。
- 数值约束审核（`goal_constraints.py`，65 测试）：逐成员载荷、固定点接口、携物净空、资源冲突、所有权与位置连续性。
- 业务必要条件审核：拒绝「只有导航观察却自称完成订单」类作者证明。
- 单因子冻结（`goal_freeze.py`，45 测试）：同布局/同任务变体不跨 development/test，改任务号/文字/因子标签不能拆分冻结组。
- 统一产物（阶段 8/9 输出）：27 个完整作者题包、21 组单因子对照、1 个城市准备候选，`goal_suite audit --verify-sources` 生成后 `verify` 可独立读回。

### 3.4 真实部署与物理证据（诊断级）

- Warehouse 真实导航：254 控制步、目标误差 0.4977m ≤ 0.5m，逐步命令/根状态证据 + 独立审核器复核（`navigation_deployment_review.py`）。
- 四场景串行 Isaac 诊断（240 控制步/场景）：独立机器人实例、控制状态键、PhysX 质量/位姿读回（阶段 12，`tmp/goal_physical_stage12/`）。
- Warehouse 单机固定点承载 v5：纸箱位移 1.973m、升降锚点回程误差 4.76μm、释放后 dock ready（阶段 18，`tmp/warehouse_stage18_native_witness_20260919_v5/`）。
- City v8 服务诊断：三项服务真实人员轨迹（~301 样本）、交通控制（~256 样本/类）、事故回执与连续通路读回（`tmp/city_benchmark/service_probe_stage18_20260919_v8/`）。
- House 原生后端 v3：真实 320×240 帧身份读回 + blocked 通路测量（`tmp/house_native_backend_stage18_20260919_v3/`）。

### 3.5 测试基线

- 最新聚焦回归：**222 passed**（`env_isaaclab`，`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`，覆盖 v8 服务运行时、paired shape/probe、Stage18 gate）。
- 历史峰值：`source/EAI/test` 全量 **1033 passed**；阶段 7 定向 212 项、阶段 8 统一入口 380+16 项、阶段 9 六文件 263 项、阶段 11 十二文件 468 项。
- 运行环境：Isaac Sim 5.1 / Isaac Lab 2.x（`env_isaaclab`）；仓库根目录执行；`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 防止系统 ROS pytest 插件干扰。

## 4. 现在可用的入口与命令

统一生成与读回（作者侧核心闭环，纯 Python，不启动 Isaac）：

```bash
conda run --no-capture-output -n env_isaaclab \
  env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.benchmark.goal_suite list

# 生成三场景 × 九条件题包并读回（换用尚不存在的新目录）
conda run --no-capture-output -n env_isaaclab \
  env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.benchmark.goal_suite audit \
  --city-package <城市prepare目录> --verify-sources --output-root tmp/goal_suite_<date>

conda run --no-capture-output -n env_isaaclab \
  env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.benchmark.goal_suite verify \
  --output-root tmp/goal_suite_<date> --verify-sources
```

四层题包发布审计（fail-closed，当前全部 pending/不可计分是**设计状态**，见 §6）：

```bash
python -m demo.benchmark.release_manifest --output tmp/benchmark_release/release-manifest.json
```

场景资产闭包检查（不启动 Isaac）：

```bash
conda run --no-capture-output -n env_isaaclab env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.warehouse_benchmark.main --check-assets
# modern_house_benchmark / factory_benchmark 同理
```

聚焦回归（快速验证本交付）：

```bash
conda run -n env_isaaclab env PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest -q \
  source/EAI/test/test_benchmark_goal_driven.py \
  source/EAI/test/test_benchmark_goal_constraints.py \
  source/EAI/test/test_benchmark_goal_capability_audit.py \
  source/EAI/test/test_benchmark_goal_business_review.py \
  source/EAI/test/test_benchmark_release_manifest.py \
  source/EAI/test/test_benchmark_challenge_contract.py \
  source/EAI/test/test_benchmark_authoring.py --tb=short
```

物理诊断类入口（需 Isaac，串行执行）：`demo.benchmark.goal_navigation_probe`、`goal_physical_probe`、`goal_attachment_probe`、`demo.warehouse_benchmark.paired_native_probe` 等，参数与证据口径见 [GOAL_PHYSICAL.zh-CN.md](GOAL_PHYSICAL.zh-CN.md)。

## 5. 待开发与被阻断项

按「谁能解除」分类。任务生成侧无法凭空替代以下任何一项。

### 5.1 外部资产 blocker（已于 2026-09-19 解除，待 provider 发布）

- **Carter 碰撞源资产**（已修复）：根因是 `caster_swivel_left/right` 与 `caster_frame_base` 三个**空 Xform** 被施加了 `PhysicsMeshCollisionAPI`+`convexHull`——无网格可烹饪，故 v9 probe 的 deferred cooking 返回 `RESULT_ERROR_INVALID_PARSING`。本地修复：移除这三个 prim 的碰撞 API 与 approximation 属性（刚体/关节链保留，轮子解析 Cylinder 碰撞不受影响，**未添加任何代理形状**）。v10 复跑验证：cooking 失败消失，collider inventory 73→70，形状闭合门禁通过（证据：`tmp/warehouse_stage18_paired_native_20260919_v10/`，细节见 [STAGE18](STAGE18_REVIEW.zh-CN.md)）。
- **后续交接**：① 修复后的 `carter.usd`（SHA-256 `86de9633…`）目前仅是本地资产修复，需维护者按 provider 流程发布到不可变 revision；② probe 推进到的新卡点是**联合队形几何**——M20/Carter 与木箱静置 AABB 相互接触（reach 公约 0.8/0.6 m 与碰撞包络挤压），属 STAGE18 已记录的队形实测问题，非资产缺陷。

### 5.2 正式 episode 绑定（2 项，依赖场景/城市团队）

- **City**：v8 仅为候选诊断（`formal_episode_binding=false`）。需要城市维护方把人员/车辆 roster、危险区、集合容量、角色区间、动态实体固定进正式 episode，之后任务生成侧补两份独立业务见证与具体因子 treatment，再执行完整题包和单因子冻结门槛。
- **Modern House**：当前快照无业务干预。需要家具干预后的 before/after 正式见证（`room_entry_scan`/`room_covery_scan` 绑定），加上门外/门槛/门内区域与连续住户路径的真实几何绑定（ZCode 像素审核已确认 M20_2 历史上位于门外露台侧，出生位需复核）。

### 5.3 运动模型（用户已决策）

- **楼梯 RL**：Go2/Lite3/B2 的现有 RL 模型不支撑连续爬楼（首级/第二级 probe 以 `support_unknown`/`support_contact_gap_timeout` fail-closed，详见阶段 15）。用户已拍板：默认可行，**后续更换运动模型再验证**；楼梯题面 `F-VERT-01` 保持开放，B2 二维路线的地图膨胀问题已单独定位（楼梯排除区 + tracking reserve 重复膨胀，垂直接近段改用 0.0m reserve 后校准成功）。

### 5.4 跨团队交接门禁（不阻塞任务生成本体）

按 [TODO.zh-CN.md](TODO.zh-CN.md) 各阶段「外部交接」小节：资产发布到不可变 provider revision、实时领域 detector/dock/path 后端的真实 episode、reserve 生命周期 Isaac 成对运行、实验回放与评价模型（其他开发者）、held-out 未曝光数据集。全部完成前 `scoreable` 保持 false。

## 6. 发布状态口径

所有题包当前 `release_status=pending`、`execution_validated=false`、`scoreable=false`，这是 **fail-closed 设计的预期状态**，不代表任务生成与审核未完成：

- 题包已拆分 `authoring_status`（作者审核，已 passed）与 `execution_status`（外部执行，external_pending），「任务设计完成」与「benchmark 整体发布」不再混为一个状态。
- 提升为 `ready` 的唯一路径：统一清单无 blocker 且 reference execution 通过（阶段 5 出口条件）。
- 本会话的验证纪律：静态声明、调度完成、进程退出码 0 均不能替代运行证据；每个阶段的勾选都要求磁盘产物 + 独立读回。

## 7. 仓库内其他并行工作线

本分支同时携带与 benchmark 并行推进、且相互依赖的其他工作线，一并交付：

| 工作线 | 位置 | 状态 |
| --- | --- | --- |
| 物理回放 / probe_tool | `tools/physics_replay/`、simulator.py 集成 | 已提交（本分支既有 commit），locomotion 重建 PASS |
| 机械臂真实物理准入 | `tools/validation/{kinova,ur5}*.py`、`source/EAI_assets/.../robots/kinova_*.py`、`ur5_robotiq.py` | 审计/冒烟工具就位；部署矩阵 conditional/rejected/unknown，等外部实测证据 |
| C01/Mid360 传感器与 rmuc2024 场景（nav 适配线） | `sensor/low_sensor/octagon_aio_c01.py`、`robots/simple_car.py`、`scene/rmuc2024.py`、`EAI_hmrs/envs/rmuc2024_*.json`、`algorithm/nav_product_adapter/` | 传感器/场景/环境 JSON 就位；nav 闭环另有会话推进 |
| 城市演示（Flow 效果） | `demo/city_benchmark/scene/`（2026-09-19 自 `demo/city/` 合并） | demo 可独立运行；Flow 预设本体按 Omniverse 许可不入库（`usd/effects/` 仅提交元数据） |
