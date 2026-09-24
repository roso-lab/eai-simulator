# Benchmark 任务生成与审核 · 交付状态总览

## 当前入口：阶段53餐厅增量原生执行与发布路线（2026-09-25）

当前计划以 [发布主线路线图](RELEASE_ROADMAP.zh-CN.md) 为准，阶段53输入基线 `629a329`。
正式目标为五场景 **405个条件题包、315个单因子对照对**；生成/读回已验证，
不代表405次原生通过或405个独立业务任务。阶段46补齐City终态几何诊断，
本机37文件754通过，一次原生正常归档、6成功回执、业务仍3/4；
人员02当前可见网格在固定圆盘内，原world AABB角点超界，测量分歧已确认。
到达/安全/业务判定未变，另两人的新诊断unknown；ljy无路由，本批远端复验待补。
[有限验收](STAGE46_ACCEPTANCE.zh-CN.md)不等于自主原生闭环或正式评分资格完成。

阶段47已停止不适用于四个实际资产的定向包络改造，v1/v2不变、没有新的GPU结果。
[本批评分声明校正](STAGE47_SCORING_SCOPE.zh-CN.md)将raw evidence、适用声明要求和
发布认证分开；修正静态“无通信/串行/固定子任务缺口”描述，快照未观测值保持null。
普通基线不再被声明需要strict restore/sham；R1–R5及所有发布资格仍未完成。

[阶段48](STAGE48_DEPLOYMENT_DENOMINATORS.zh-CN.md)修正可行/不可行条件分母，保留
漏答和零分母，新增初始/运行中分层以及oracle/task来源一致性检查；本机423项通过。
旧档案只读重算未改业务证据，没有新增GPU或远端通过。计划/成本来源绑定及原生
oracle资格仍待补，不能把raw诊断指标当作正式六轴分数。

[阶段49](STAGE49_SCORE_SOURCE_BINDING.zh-CN.md)建立计划/成本来源绑定，本机29文件
476项通过。消费者不会补造旧档案证明，缺证null、真实零保留；餐厅读取器兼容旧版且
拒绝正式档缺证降级。全局审查建议下一P0回到有界初始oracle和完整自主基线；原生
oracle、五场景自主接线与正式六轴统计仍未完成，声明前成本接入后续真实稳定档案。

[阶段50](STAGE50_INITIAL_ORACLE_BOUNDARY.zh-CN.md)完成City初始资格的有界STOP审查，
保持平台failed/null、不重复几何诊断。比较后选择餐厅原始8候选池作为下一完整R1目标，
已并行补齐全队控制基座与readiness v2实际通信投递，Factory时钟后置。
独审组合349项、评分/因子等6文件134项通过；本机8机共场景idle pump完成240tick/4.8s。
该检查不验证导航/业务，静态准入仍platform_blocked；原生观察/自主入口/事件泵继续待接。

[阶段51](STAGE51_NATIVE_OBSERVATIONS.zh-CN.md)补齐八机预热后可信epoch/根状态及同tick
原生观察；311项Restaurant回归与47项评分/因子/容量检查通过。本机22条实际查询，最终
代码只读回放一致；未新增导航/业务资格。七处真实出生区域绑定仍缺失，先做显式版本化
区域/路线修正，不将分散出生点假标为共同staging。全局审查持续把关投入方向与证据范围。

[阶段52](STAGE52_DEPLOYMENT_ENTRY.zh-CN.md)已完成显式八机部署与真实自主会话准入，
两种自拟计划通过；独审352项通过，历史根状态8/8几何解释成立。未新增原生执行，
`execution_loop_connected=false`，不等于完整业务或oracle资格。

[阶段53](STAGE53_NATIVE_EXECUTION_PUMP.zh-CN.md)已实现增量原生执行/取消与共享通信，
工程222、独审409通过；本机诊断5/5技能成功，双导航实际重叠12.74s，真实观察、服务回执
及双时间审计/通信重放通过。旧business-v2仍不消费full8新终态；诊断链路不是冻结
因子资格证明，初始oracle仍failed/null，所有发布标志保持false。

下一主线为：版本化full8业务验证/双时间事实适配 → 准确条件完整baseline及initial oracle → 可信
里程碑、时间和成本事实 → 六能力/405条件的实验效度 → 单场景发布认证。真实并发、
执行中事件响应及重评估由具有相应机会的条件另行验收。
native restore/sham只门控严格同快照结论。具体依赖、原有A–E任务
映射及验收见路线图；当前执行清单见 [TODO](TODO.zh-CN.md)。

`scoreable=false`、`execution_validated=false`、`business_outcomes_validated=false`。
已有原生诊断与组件回归不自动抬升发布资格。

## 历史交付快照（2026-09-20，以下保留原文）

以下“四场景/36题包”、旧阶段“现行/待办”和门禁描述属于当时快照，**不作为当前
数量、优先级或完成状态**。保留用于追溯旧证据；新工作从上方路线图进入。

整理日期：2026-09-20（诊断粒度、命名与维护审计规范更新）
工作来源：codex 会话 01a0aa7a（阶段 1–18）及其前置/并行会话，分支 `codex/factory-vertical-challenge`。
本文是整个 benchmark 工程的交付入口：先讲清工程结构和文档地图，再按「已完成 / 现在可用 / 待开发被阻断」三档给出交付状态。阶段级历史证据见 [TODO.zh-CN.md](TODO.zh-CN.md) 与各 `STAGE*_REVIEW` 记录，本文不重复其细节。

四场景（Factory / Warehouse NVD / Modern House NVD / City）的任务生成与审核本体已经可复跑。House 业务干预已有历史完成证据；City 本轮完成四人安全疏导和交通 hold/release 读回，严格原生探针仍停在背景车辆改道碰撞检查。按用户最新范围，背景车辆碰撞不再作为本轮任务流程整改的阻断项，也不据此把旧探针报告改写为通过。后续正式发布仍按 §6 的独立门禁验收。

---

## 1. 工程结构

```text
demo/
  benchmark/                     场景公共层 + 任务生成与审核核心
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

### 1.1 诊断粒度与轨道 ID（维护规范）

**City 决策：服务诊断即其分项测量，不新增固定 DAG 诊断层。**
`guide_people`、`control_traffic`（hold/divert/release）、`report_incident` 的请求、
实际状态、回执和时间窗口由服务探针分项记录。探针的固定调用顺序只是测试程序，
不成为参与者必须照抄的参考 DAG。背景车辆碰撞属于场景表现诊断，按本轮范围不作为
任务流程整改门槛；服务响应、人员结果、救援通路等业务证据仍按各自契约核验。

| 层级 / 场景 | 规范 ID | 被测决策及证据粒度 |
| --- | --- | --- |
| goal-driven / Factory | `FACTORY-GOAL-01` | 参与者生成业务计划、分解与分配 |
| goal-driven / Warehouse | `WAREHOUSE-GOAL-01` | 同上 |
| goal-driven / Modern House | `MODERN_HOUSE-GOAL-01` | 同上；原 `HOUSE-GOAL-01` 是历史产物 ID |
| goal-driven / City | `CITY-GOAL-01` | 业务计划与服务调度；发布状态单独审核 |
| 受限控制 / Factory | `F-INS-01` | 前三阶段由 benchmark 管理；仅 `route_confirmation` 允许等待、消费消息、选择目标和导航 |
| 固定 DAG / Factory | `F-TRAFFIC-01`、`F-COV-01` | 固定图中的分配、改派与恢复诊断 |
| 固定 DAG / Warehouse | `W-DAG-01` | 10 节点订单链；节点内分配、改派、导航与可见观测消费 |
| 固定 DAG / Modern House | `MH-DAG-01` | 11 节点恢复链；同上，等待仍由 benchmark 管理 |
| 服务分项 / City | `C-SERVICE-01` | 三项服务的调用与运行时结果测量，没有固定业务 DAG |
| 平台能力 / Factory | `F-VERT-01` | 不计分的运动能力探针，不等同于业务协作或规划轨道 |

F-INS 的受限控制与 Warehouse/House 的节点内分配是两种诊断粒度，不能以
“都有阶段图”混为相同规划自由度。主轨道继续使用完整场景名前缀，诊断沿用
`F` / `W` / `MH` / `C` 场景缩写与 `类型-序号`；文件名与内部实现版本名不充当轨道 ID。
Warehouse/House 默认 JSON 和新生成题包写入上述 DAG ID；原 `task_name` 保留为描述名称。
旧题包、执行报告与冻结摘要保留原样，跨版本比较须重新生成整套题包和 freeze，不能只改
旧报告中的 ID 或复用原摘要。

### 1.2 原生入口与入库 sidecar（维护规范）

新增或公开推荐的原生诊断入口统一为 `native_probe.py` 或 `native_probe_<用途>.py`。
实现模块可以保留历史名称，兼容入口只转发同一 `main`，不得复制执行器或更换证据协议。

| 场景 / 用途 | 规范模块入口 | 保留的历史入口 |
| --- | --- | --- |
| Warehouse 单机 | `demo.warehouse_benchmark.native_probe` | `native_witness_probe` |
| Warehouse 双机 | `demo.warehouse_benchmark.native_probe_paired` | `paired_native_probe` |
| Modern House | `demo.modern_house_benchmark.native_probe` | 同名 |
| City 场景 / 服务 | `demo.city_benchmark.native_probe` / `native_probe_services` | `service_probe` |
| Factory 楼梯能力 | `demo.factory_benchmark.native_probe_vertical` | `tools.scenario_generation.vertical_task` |

受维护的诊断输入与同名 `.audit.json` **一起纳入版本控制**。权威清单为
`demo.benchmark.diagnostic_audit.MAINTAINED_SCENARIOS`：Factory 的 dispatch、traffic、
coverage（含 dropout）、vertical，以及 Warehouse/House 的默认业务 DAG，共七项。
历史 `inspection_01.json` 与 `navigation_smoke.json` 属于兼容/冒烟输入，不伪装成已审核诊断题。
City 无固定 DAG，因此不制造场景 JSON sidecar；其 prepare manifest 与服务报告留在 `tmp/`。

sidecar 为作者 QA，包含原始场景文件 SHA-256、轨道 ID、诊断粒度与实际执行的审核项。
Factory sidecar 仅证明 schema/任务契约；Warehouse/House 另含本地地图、连通性、飞行净空和
执行计划审核。所有 sidecar 均明确 `execution_validated=false`、`scoreable=false`，
不得将作者审核当作物理验证。运行报告、日志、截图及下载资产仍不入库。

在 `env_isaaclab` 中重建或独立验证（室内几何审核需要本地地图产品，不启动 Isaac）：

```bash
python -m demo.benchmark.diagnostic_audit --write
python -m demo.benchmark.diagnostic_audit
# 也可指定单个 scenarios/<name>.json；默认只读核验，内容或输入变化即失败。
```

修改维护场景后必须重建 sidecar，并检查输入摘要及审核范围。Warehouse/House 的
`challenge --generate-scenario` 已自动写出同样格式的 sidecar；生成到 `tmp/` 的候选
只有经过明确评审、选为维护 fixture 后才进入 `scenarios/`。

`demo/benchmark/` 内部按职责分组：

| 分组 | 代表模块 | 职责 |
| --- | --- | --- |
| 题包契约 | `challenge_contract.py`、`scene_challenge.py`、`authoring.py`、`semantic_contract.py` | 四层题包（public / participant / baseline / hidden evaluator）的编译、作者语义审核、任务骨架 |
| goal-driven 主轨道 | `goal_driven_*.py`、`goal_suite.py`、`goal_freeze.py`、`goal_constraints.py` | 业务目标题面 + v2 participant API、语义 witness、数值约束审核、单因子冻结、统一生成/读回 CLI |
| 部署与诊断 | `goal_deployment*.py`、`goal_physical_*.py`、`goal_navigation_probe.py`、`goal_attachment_*.py`、`goal_native_*.py` | 四场景部署配置、Isaac 原生物理/对象/占用/形状 probe 与独立审核器 |
| 运行时 | `runtime.py`、`participant_runtime.py`、`participant_episode.py`、`control.py`、`scheduling.py`、`allocation.py` | 参与者回合执行、调度与分配、原子资源预留 |
| 算法接入与统计 | `goal_algorithm_adapter.py`、`algorithm_adapter.py`、`handoff_manifest.py` | 四场景 goal 主轨统一高层接入；固定 DAG 单独诊断；固定默认控制策略、运行身份、配对描述统计与交接读回 |
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
|  | [ALGORITHM_HANDOFF.zh-CN.md](ALGORITHM_HANDOFF.zh-CN.md) | 现行 | 四场景主轨算法接口、默认控制变量、接手步骤与统计边界 |
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

四场景主轨均为 goal-driven，规范 ID 见 §1.1，模型自主选择团队、分解业务目标、分配与
重规划。默认生成 36 个题包、审核 28 对因子对照。下表保留的是各场景诊断层的任务骨架，
不应把这些固定节点当作主轨强制参考解。

2026-09-21 补充：已建立单独的 M0 canonical mechanism registry，四场景各有
`decomposition`、`role_allocation`、`communication_adaptation`、`role_churn_recovery`
四类，共 16 个 authoring 模板。生成器可显式生成 `classified` 机制题包并独立读回；
旧 36 包兼容矩阵保持不变。每个 M0 模板带三态团队必要性证书，当前均为 `unknown`，
`execution_status=external_pending`、`scoreable=false`，尚未声称物理必要性或运行因果效应。

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
- 统一产物（当前口径）：四场景共 36 个完整作者题包、28 组单因子对照；City preparation
  candidate 作为独立 authoring 输入，不计入 goal package。`goal_suite audit --verify-sources`
  生成后 `verify` 可独立读回，默认场景集合由 `goal_scene_registry.py` 提供。

### 3.4 真实部署与物理证据（诊断级）

- Warehouse 真实导航：254 控制步、目标误差 0.4977m ≤ 0.5m，逐步命令/根状态证据 + 独立审核器复核（`navigation_deployment_review.py`）。
- 四场景串行 Isaac 诊断（240 控制步/场景）：独立机器人实例、控制状态键、PhysX 质量/位姿读回（阶段 12，`tmp/goal_physical_stage12/`）。
- Warehouse 单机固定点承载 v5：纸箱位移 1.973m、升降锚点回程误差 4.76μm、释放后 dock ready（阶段 18，`tmp/warehouse_stage18_native_witness_20260919_v5/`）。
- City v8 服务诊断：三项服务真实人员轨迹（~301 样本）、交通控制（~256 样本/类）、事故回执与连续通路读回（`tmp/city_benchmark/service_probe_stage18_20260919_v8/`）。
- House 原生后端 v3：真实 320×240 帧身份读回 + blocked 通路测量（`tmp/house_native_backend_stage18_20260919_v3/`）。

### 3.5 测试基线

- 交接相关回归：基线 **398 passed**；加入一键预检和 goal/fixed-DAG manifest fail-fast 后增量 3 项，当前合计 **401 passed**。默认 36 题包/28 对照导出与源码冻结读回、四景公开 adapter 接入、七份 sidecar 均已验证。命令及边界见 [交接验收记录](HANDOFF_ACCEPTANCE.zh-CN.md)。该数字与下述历史批次不累加。
- 交接前工程化预检已增加：`python -m demo.benchmark.handoff_preflight --output-root <新目录>` 一条命令生成、读回和公开 release 冒烟；部分失败会清理，不会留下可误交付目录。

- 2026-09-20 本轮整改：**125 + 274 = 399 passed**，覆盖命名/审计、防篡改、场景编译、goal题包、Factory准入及City服务回归；七份sidecar重建后独立核验通过，四个规范入口`--help`通过。完整命令和原生实验限制见 [恢复记录](STAGE19_RECOVERY.zh-CN.md)。
- 2026-09-21 authoring 增量：覆盖入口收敛、测量契约来源绑定和四场景 headless 原生初始化通过；离线定向回归 **100 passed**。四个 8 步 CPU headless 探针均 `returncode=0` 且 `diagnostic_completed=true`，证据目录见 `tmp/headless_measurement_20260921_*`。这些是配置/读回诊断，不能提升业务 `scoreable` 状态。
- 阶段18历史聚焦回归：**222 passed**，覆盖 v8 服务运行时、paired shape/probe、Stage18 gate。
- 历史峰值：`source/EAI/test` 全量 **1033 passed**；历史结果不作为当前工作树的新验收。
- 运行环境：Isaac Sim 5.1 / Isaac Lab 2.x（`env_isaaclab`）；仓库根目录执行；`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 防止系统 ROS pytest 插件干扰。

### 3.6 算法接入边界（已具备离线接入，物理闭环仍分级）

四场景主轨统一通过 `demo.benchmark.goal_algorithm_adapter.GoalAlgorithmHost` 接入，
adapter 实现 `initialize(public_task, participant_api)`、`act(new_public_events)` 和
`close()`；现有 goal_action 由 `GoalSession` 准入，可信宿主提交状态及技能回执。
固定 DAG 诊断则使用 `algorithm_adapter.CallableAlgorithmAdapter` / `ParticipantSession`，
两者的 initialize 签名和动作 schema 不同。室内三个 `main(participant_adapter=...)` 属于
诊断入口，不能替代 goal 主轨接入。详细例子及验收步骤见 [接入指南](ALGORITHM_HANDOFF.zh-CN.md)。

TeamWeaver/EMOS 是被测多机系统。导航、移动、抓取采用 benchmark 冻结的默认理想技能：在
冻结参数下物理前提可行时确定性成功，不可行时由执行层确定性失败并只回传可观察事实；
不能由被测 adapter 替换；额外干扰只能作为显式 factor。共享 execution contract 和符号
参考后端已存在，但 `benchmark_default_ideal` 是比较策略声明，不证明所有历史 Isaac
控制器均已满足该契约。各场景实际后端和业务回执仍须单独验收。

比较运行使用 `build_algorithm_run_manifest()` 固定 algorithm/version、benchmark commit、
题包与场景 hash、provider revision、seed、factor treatment、episode/comparison key、
branch、独立 `run_root` 以及 `benchmark_default_ideal` 控制栈；固定后端版本与参数记录在
runtime_config。`run_algorithm_batch()` 只是离线回调线程池，不在其中创建多个 Isaac 应用；
多进程/机器调度归外部开发者。`build_algorithm_result()` 只接受显式状态、指标、参与者
流和受控证据。`summarize_algorithm_metric()` 输出状态计数、均值/中位数与 bootstrap CI；
`compare_paired_metric()` 按相同 task/seed/factor 的 comparison key 做成对差值；多重比较
使用 `holm_bonferroni()`；成本指标需设置 `higher_is_better=False`。同一布局的分支不是
独立任务样本，这些描述统计不证明跨任务泛化，也不替代领域回执或业务成功评测。

交接前可运行 `python -m demo.benchmark.handoff_manifest --output-root <新目录>` 导出四场景
真实题包和交接矩阵，再用 `--verify <目录>` 从磁盘重新审核；默认无参数只打印当前矩阵。
它要求 Factory、Warehouse、Modern House、City 都具备相同的四层题包形状、公开 API、
隐藏边界和作者审核结果，并统一声明 `benchmark_default_ideal` 控制栈；四行的
`execution_status` 保持 `external_pending`、`scoreable=false`，因此“任务生成与审核可交接”
不会被误写成“物理执行已经完成”。四场景 runtime_boundary 均为 GoalSession，
另列各自 diagnostic_boundary，不混淆主轨与诊断。

GoalSession 是每个 episode 的唯一权威账本：动作准入、runtime snapshot、技能 receipt 和
领域业务证据必须绑定同一 `episode_id`。benchmark 提供题包、公开 API、证据语义和固定的
理想低层技能；外部算法进程隔离、超时/进程组回收、批量幂等与重试、通信/生命周期真实
事件源以及统计泛化分析由下游运行平台工程师实现，具体契约见
[ALGORITHM_HANDOFF.zh-CN.md](ALGORITHM_HANDOFF.zh-CN.md)。

## 4. 现在可用的入口与命令

统一生成与读回（作者侧核心闭环，纯 Python，不启动 Isaac）：

```bash
conda run --no-capture-output -n env_isaaclab \
  env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.benchmark.goal_suite list

# 生成四场景 × 九条件题包并读回（换用尚不存在的新目录）
conda run --no-capture-output -n env_isaaclab \
  env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.benchmark.goal_suite audit \
  --verify-sources --output-root tmp/goal_suite_<date>

conda run --no-capture-output -n env_isaaclab \
  env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.benchmark.goal_suite verify \
  --output-root tmp/goal_suite_<date> --verify-sources

# 生成四场景 × 四种 M0 canonical mechanism template（16 个）并读回
conda run --no-capture-output -n env_isaaclab \
  env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.benchmark.goal_suite mechanism-audit \
  --output-root tmp/goal_mechanism_suite_<date>

conda run --no-capture-output -n env_isaaclab \
  env PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.benchmark.goal_suite mechanism-verify \
  --output-root tmp/goal_mechanism_suite_<date>
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

物理诊断类入口（需 Isaac，串行执行）：场景入口见 §1.2；共享历史入口 `demo.benchmark.goal_navigation_probe`、`goal_physical_probe`、`goal_attachment_probe` 继续兼容，参数与证据口径见 [GOAL_PHYSICAL.zh-CN.md](GOAL_PHYSICAL.zh-CN.md)。

## 5. 待开发与被阻断项

按「谁能解除」分类。任务生成侧无法凭空替代以下任何一项。

### 5.1 外部资产 blocker（已于 2026-09-19 解除，待 provider 发布）

- **Carter 碰撞源资产**（已修复）：根因是 `caster_swivel_left/right` 与 `caster_frame_base` 三个**空 Xform** 被施加了 `PhysicsMeshCollisionAPI`+`convexHull`——无网格可烹饪，故 v9 probe 的 deferred cooking 返回 `RESULT_ERROR_INVALID_PARSING`。本地修复：移除这三个 prim 的碰撞 API 与 approximation 属性（刚体/关节链保留，轮子解析 Cylinder 碰撞不受影响，**未添加任何代理形状**）。v10 复跑验证：cooking 失败消失，collider inventory 73→70，形状闭合门禁通过（证据：`tmp/warehouse_stage18_paired_native_20260919_v10/`，细节见 [STAGE18](STAGE18_REVIEW.zh-CN.md)）。
- **后续交接**：① 修复后的 `carter.usd`（SHA-256 `86de9633…`）目前仅是本地资产修复，需维护者按 provider 流程发布到不可变 revision；② 队形几何问题已于 2026-09-20 收口——Carter 不可达实测写入计划准入注册表（`goal_constraints._MEASURED_ANCHOR_INFEASIBLE`，配对选择仍属被测模型方案空间），双 M20 诊断走通 settled/lift 门禁并实测 78 运输 + 40 抬升样本（阶段19 战役记录）。

### 5.2 正式 episode 绑定（2026-09-19 大幅推进，各剩一个实测级卡点）

2026-09-20 更新：City v6四名人员安全疏导、交通hold/release已取得完整分项证据；
改道原生诊断仍停在背景碰撞检查。用户已明确此项不再作为本轮任务流程整改阻断，
停止物理排障。以下首跑状态是历史记录，后续以[本轮恢复记录](STAGE19_RECOVERY.zh-CN.md)为准。

- **City**：v8 仅为候选诊断（`formal_episode_binding=false`）。**2026-09-19**：作者固化绑定（4 人 roster 逐人绑定集合候选/危险区模板/控制点/反应窗口/回执端点，intake 机检）、`_city()` builder 双语义见证、communication_quality 因子 treatment、完整 goal bundle 的 build/verify/freeze、goal_suite 城市审计与单因子对——全部落地（离线 246+211 项测试）。正式探针首跑在服务 admission fail-closed（STAGE18 City 首跑记录）。2026-09-22 原生诊断取得四人 ACK、随后安全到达、交通 hold、事故接收及连续通路读回；可选 `bound_native_v1` 题包的 4 人/4 集合区/6 车身份审核通过。旧 v2 每人 10 秒窗口与到达相冲突；新的 City-only `eai.skills.v3` 将现场技能终态定为逐人 ACK，窗口各 20 秒，人员到达仍是后续业务证据，默认 v2 hash 不变。v3 离线审核 `tmp/city_benchmark/goal_native_bound_bundle_20260922_v3/binding_review.json` 仅为 `not_disproved_by_native_ack`，不证明参与者执行。当前仍无 GoalSession 原生 City episode，`execution_validated=false`、`scoreable=false`。
- **Modern House**：**2026-09-20 完成**（STAGE18 阶段18/19 战役归档）：区域几何双绑定 + 干预时序全部实现；阻挡物指认为 SceneXR 沙发（占据图漏采 Mesh 资产）；干预几何改北向后 **v20 全绿**——双 M20 attach + 抬升 0.0372 m + **搬运 2.05 m 到达停车格** + 撤离 all_clear + after 快照 measured + closeout + occupancy 断言。剩余场景项：占据图重建纳入 Mesh 资产（地图哈希修订流程）；M20 仅前向的实测结论待运动模型更换（P1/M1）。

### 5.3 运动模型（用户已决策）

- **楼梯运动模型**：用户已确认 Go2、B2 的替换模型具备上下楼梯能力，目前仍在部署和独立
  验证；M20 保留为已有楼梯能力基线。Go2/B2 的模型或 checkpoint、机器人配置和运行参数
  必须绑定到 `runtime_config` 及 provider/model hash。每个模型完成连续上行、平台停留、
  下行和失败/超时读回，并与 `F-VERT-01` 题包完成场景绑定后，才可解除该运动证据门禁。
  在此之前题包保持 `execution_validated=false`、`scoreable=false`；背景车辆碰撞不属于本轮
  任务流程门禁。

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
