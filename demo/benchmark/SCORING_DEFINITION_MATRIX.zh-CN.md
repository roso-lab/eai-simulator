# 六能力"指标—生产器—门禁—缺失原因"定义矩阵（定义冻结提案）

状态：**定义冻结提案，等待用户/viewer 拍板；未实现任何公式或生产器**
日期：2026-09-24
输入基线：`codex/benchmark-pose-telemetry`，HEAD `419d15f`
上游依据：viewer 路线裁定 `tmp/stage33_route_decision_viewer.md`（批次一验收 5：
"F 文档明确：固定子任务分母不能套到模型自分解，也不能用里程碑数直接替代；定义各轴的
N/A、平台失败、删失与分母政策。未冻结的参数必须列出，禁止实现者自行填权重。"）
与 `STAGE33_ROUTE_AND_RESTAURANT.zh-CN.md` 的子代理 C 分工。

## 0. 文档定位与硬边界

- 本文档是**定义层**：逐轴给出指标口径、现有生产器、进正式分母的门禁、N/A 与删失
  政策、自分解分母政策、未冻结参数清单。它不实现任何计算，不改变协议原文
  （`异构多机器人高层协作智能+Benchmark+能力分类与评测协议.md`，SHA256
  `539822d71b46b520c21df9170b182e0b7fe66016b8cfb26825a33294f52651c1`），
  不修改公开题包哈希，不抬升 `execution_validated` / `business_outcomes_validated`
  / `scoreable` 三个发布标志。
- 轴目对齐 `goal_scoring_adapter.py::DIMENSION_STATUS` 的七个条目（六能力，动态重构
  拆规模响应与失效恢复两个子条目）。`DIMENSION_STATUS` 中的 `"P/X"`、`"X/M"` 等
  字符串在源代码内没有附图例（unknown）；各轴现状以本文逐节文字为准。
- 每个未冻结参数带编号（G/TA/CA/TC/DR/XL/CE 前缀），汇总见第 9 节。**实现者不得
  自行填值**；未拍板前对应指标只能 raw-only 归档或缺生产器标注。
- 事实引用给文件+符号；本文写作时核对于 `419d15f`。

用户既有原则（本文按其设计口径，其中带 unknown 标注的尚无仓库书面记录）：

1. 同一任务重复 10 次（评测可信度三支柱记忆；仓库内未检索到书面冻结记录，
   **unknown**，正式重复数与聚合层级见 G1/G2）。
2. 出题人不限制团队规模：团队规模是被测算法的决策，协调成本是测量信号
   （书面出处 `TODO.zh-CN.md` 2026-09-22 用户原则，v2 池 `max_active_agents`
   已放开为池大小）。
3. 重规划时间三层计费（用户记忆原则；协议原文只有重规划计数与"无收益重规划"
   诊断，无分层计费，层定义待拍板，见 G5/CE5）。
4. 进度分参考 RoboChallenge 的阶段拆分与重试扣分（用户记忆原则；协议 §2.6 的
   Q(t) 权重里程碑不含重试扣分项，是否引入待拍板，见 G4）。

## 1. 任务分配（task_allocation，DIMENSION_STATUS `"P/X"`）

### 1.1 指标定义

通俗：模型把自拆出来的工作交给谁、和谁组队；以及在当前团队确实不够时能否正确
说"不行"。协议 §3.1 原定义（给定固定子任务集合）在 Goal 自分解主线上标记
`not_applicable_protocol_mismatch`（`goal_scoring_adapter.py` task_design 字段），
本轴拆成两层：

- **部署判断层（外部可判定）**：在每个要求评估的测量点（assessment epoch），
  模型对"当前可用池能否完成剩余任务"输出二元判断，与隐藏 oracle 的
  `deployable|not_deployable` 标签比对，得四类结果：
  correct_deployment（可行且部署）、correct_refusal（不可行且拒绝）、
  false_refusal（可行却拒绝，协议 FIR 语义）、false_deployment（不可行却部署，
  协议 IRR 的错误面）。
- **自分解诊断层（模型自拆计划的质量画像）**：计划准入尝试的接受/拒绝率、
  拒绝类别分布、资源冲突率、修订存活率。这层**不构成固定子任务分数**，是
  raw-only 诊断。

形式化：部署层比率以"要求评估的 epoch 数"为分母（当前实现见
`goal_scoring_adapter.py::deployment_assessment_observations()` 的
`accuracy_denominator_scope =
"all_required_epochs_of_oracle_verified_episodes_including_missing_answers"`）；
模型未作答的 epoch 保留在分母并计入 `missing_assessment_count`，不删除、不改判。

统计单位：assessment epoch（部署层）与 plan attempt（诊断层）；跨 episode 聚合
以 base_instance 为聚类单位。

### 1.2 生产器

| 量 | 生产器（文件+符号） | 状态 |
| --- | --- | --- |
| 四类分类计数、漏答、host 声明延迟 | `goal_scoring_adapter.py::deployment_assessment_observations()` | 已有，raw-only |
| 部署层与诊断层合并画像 | `goal_scoring_adapter.py::task_allocation_observations()` | 已有，raw-only |
| 计划准入诊断（拒绝率/类别/修订存活） | `goal_scoring_adapter.py::plan_admission_observations()` | 已有，raw-only；拒绝分类目前按英文错误文本启发式匹配（`category()`），需上游稳定 reason_code |
| 二元正式标签 oracle | `goal_remaining_feasibility_oracle.py::RegisteredRemainingTaskOracle`、`query_remaining_feasibility()`、发布审核 `formal_oracle_release_review()` | 骨架已有；五场景 native 完备性认证未完成（B3），当前候选均 failed/null |
| 必要条件内部诊断 | `goal_deployment_feasibility.py::evaluate_deployment_feasibility()`（投影 `gate_evaluation()` 恒 `oracle_status="failed"`、`evaluator_ref="unregistered_necessary_condition_diagnostic"`） | 已有，明确不供正式标签 |
| 作者候选实例 | `goal_deployment_cases.py::build_deployment_cases()` | 已有，`authoring_only_requires_native_and_positive_certificate` |
| 门禁事件与 epoch 账本 | `goal_session.py::GoalSession`（`deployment_assessment` 动作、`_invalidate_deployment_assessment()`、`record_deployment_assessment_evaluation()`） | 已有；oracle 在参与者 act 前求值并逐 epoch 缓存 |
| 时间与声明前成本字段 | `deployment_assessment_observations()` 中 `physical_change_latency_s`、`notification_delivery_latency_s`、`pre_declaration_communication_bytes`、`pre_declaration_activation_cost`、`pre_declaration_planning_compute_cost` | **缺生产器**（字段保留为 null）。建议实现位置：`goal_episode_runner.py` 接批次二真实下行调度器与 native 通知源后注入；字节/激活部分可直接复用 `goal_cost_evidence.py` 的 replay 校验路径 |
| 自分解"分配可执行性"外部校验器（协议 V_i 的自分解等价物） | 无 | **缺生产器**。建议实现位置：新模块（如 `goal_allocation_verifier.py`），输入为模型自拆计划摘要+当前池声明，只做可执行性/冲突检查，不与作者 witness 匹配 |

### 1.3 门禁（进正式分母的条件）

1. 该 episode 的全部要求评估 epoch 均有 `oracle_status=verified` 且无质量失败
   （`episode_oracle_eligible`，`deployment_assessment_observations()` 已实现
   fail-closed：任一点 failed 即分母置 0 并单列基础设施缺口）。
2. 五场景 native 剩余任务 oracle 注册与测量点认证（REMAINING_FEASIBILITY_ORACLE
   阶段出口 2/3；`goal_native_oracle_registry.py` 审计 verified）。
3. 可信状态合同绑定（`TRUSTED_STATE_CONTRACT.zh-CN.md`，B3a；scoring adapter 的
   `trusted_state_contract.native_verified` 目前为 False）。
4. 部署判断时间字段接入真实时钟前，任何延迟类指标不入分母（当前
   `clock_source="goal_host_receipt_clock"`、`physical_clock_bound=False`）。

### 1.4 N/A 与删失政策

- oracle failed / 平台失败：该 episode 整体退出部署判断指标，计入
  `oracle_infrastructure_failure_count`，单列平台缺口；**不填零、不算模型错**
  （`REMAINING_FEASIBILITY_ORACLE.zh-CN.md` 输出与统计边界）。
- 模型未作答：oracle 已 verified 时保留测量机会与缺答事实（分母含缺答），
  不把 oracle 改 failed。
- 初始/运行中测量机会分别保留；连续两次外生变化之间未作答的 epoch 不丢失。
- 平台错误或未知标签（`platform_valid` 非 True、标签不合法）不入准确率分母。
- 诊断层缺失归档时写 `quality_failure` 原因（如 `analysis_archive_missing`），
  不作零值。

### 1.5 分母政策（自分解落地）

- **得分基数锚定作者侧冻结物**：部署层分母 = oracle-verified episode 的全部
  要求评估 epoch（含缺答）。该基数由题面预注册测量点决定，与模型拆出多少任务、
  提交多少计划无关。
- 模型多拆/少拆只进入诊断层计数（`plan_attempt_count` 等），**不改变任何分母**；
  里程碑数量同样不得直接顶替固定子任务数充当分母（viewer 批次一 F 裁定）。
- 协议 §3.1 的 VAR/FSR/ConflictAny/ConflictRel 以固定子任务为分母，在 Goal 主线
  保持 `not_applicable_protocol_mismatch`；若将来恢复，须先定义自分解可执行性
  校验器（1.2 表末行）并单独注册生产器，不与部署层混算。

### 1.6 未冻结参数

- **TA1** 部署四类率的正式命名与公式（是否沿用 IRR/FIR 名称承载新语义，或另起
  机器名），属协议修订，需用户拍板。
- **TA2** 自分解分配可执行性校验的判定范围（能力/载荷/接口/资源冲突哪些进
  V_i 等价物）与严格度。
- **TA3** 计划拒绝稳定分类码词表（替换 `plan_admission_observations.category()`
  的文本匹配）。
- **TA4** 部署判断时间字段的采信源与单位（native clock 接入后统一）。

## 2. 通信适应（communication_adaptation，DIMENSION_STATUS `"X/M"`）

### 2.1 指标定义

通俗：通信变差（延迟、丢包、带宽掉、抖动、突发）之后，协作和任务表现还能维持
多少。形式化沿用协议 §3.2：性能保持率 R_x(x)=M(x)/M(x_ref)；临界点 x_90/x_50
（延迟/丢包）与 C_90/C_50（共享系统容量，只报括号区间不外推）；同均值分布对照
附加损失 JP（固定延迟 vs 抖动）、BP（IID vs 突发）；诊断量 D_comm、实际吞吐、
排队、AoI、实际丢包率不进核心结果。M 每次只代表一个预注册的越高越好任务指标，
不混合。

统计单位：消息/字节（传输层）、AoI 秒、容量 bit/s；跨条件统计先在 base_instance
× channel_seed 内求均值，再以 base_instance 为 bootstrap 单位（
`communication_analysis.py` 模块 docstring 冻结的层级聚合）。

### 2.2 生产器

| 量 | 生产器 | 状态 |
| --- | --- | --- |
| 三池传输运行时（backhaul 上/下行、p2p、access 上限、主动/缓冲/TTL 丢包、回放校验、连续 AoI） | `communication.py::CommunicationRuntime`（L253）、`ParticipantCommunicationBoundary`（L1006）、报告校验 `validate_communication_report()` | 已有（离线/工具侧） |
| 容量/延迟/丢包/JP/BP 实验单元 | `communication_capacity_experiments.py::run_delay_cell()` / `run_loss_cell()` / `run_capacity_cell()` / `jitter_paired_observations()` / `loss_paired_observations()` / `capacity_observations()` | 已有（离线） |
| C90/C50 括号化与配对分布效应统计 | `communication_analysis.py::summarize_capacity_curve()`（无外推、reference 必需）、`summarize_paired_distribution_effect()`（不允许未配对推断） | 已有 |
| 端点表与工具调用 | `goal_communication_tool.py::CommunicationEndpointTable`、`submit_communication_tool_call()`、`derive_pool_direction()`；已接入 host/runner（`goal_algorithm_adapter.py` 的 `communication_tools` 注入与 `pump_communication_tools()`，git `904a404`） | 已有（工具面） |
| 通信分析归档接入 sidecar | `goal_scoring_adapter.py::communication_analysis_observations()` / `communication_capacity_observations()`；`goal_communication_archive.py::build_communication_analysis()` | 已有，raw-only |
| episode/run 启动的端点拓扑、channel 词表、初始池绑定 | 无（批次一拍板 2 的"写入 evaluator 的 episode/run 启动绑定"） | **缺生产器**。建议实现位置：`goal_communication_tool.py` 增 evaluator 侧启动绑定 API，由 `goal_episode_runner.py` 在 episode 开始时调用并归档 |
| 正式 episode 的 native 通信 provenance（真实下行、成员/能力生命周期通知字节） | 无（批次二） | **缺生产器**。建议实现位置：runner 执行调度层生成规范派遣/取消消息并入共享传输账本（批次一拍板 1 的唯一受限 CommunicationRuntime） |
| trusted 物理时钟/通知送达时刻 | 无 | **缺生产器**（`COMMUNICATION_STAGE22_DESIGN.zh-CN.md` 接口问题 5；确认前保持 null） |

### 2.3 门禁

1. SCOREABILITY_REASONS 第 2 条
   `no_native_communication_episode_provenance` 解除：正式 episode 绑定端点/池
   契约 + 真实消息流经共享传输账本封存且 replay 校验通过。
2. 方向分解 fail-closed：任一 pooled 传输缺 publication direction 时方向分解为
   null（`goal_cost_evidence.py::_communication_bytes()` 的 `direction_complete`
   逻辑；`COMMUNICATION_STAGE22_DESIGN.zh-CN.md` 评分侧车一节）。
3. 通信证据缺失或 replay 失败按基础设施状态处理，不当零成本
   （`communication_analysis_observations()` 的 `quality_failure`）。
4. C90/C50 只在预注册扫描点内括号报告；未包围如实报
   `above_tested_maximum` / `at_or_below_tested_minimum` /
   `reference_below_minimum`（`summarize_capacity_curve()` 已实现）。
5. 小样本 smoke 不得宣称可靠 C90/C50（viewer 批次二 D 验收）。

### 2.4 N/A 与删失政策

- 参考条件（`capacity_bps=None` 完美容量点）缺失：整个汇总
  `status="missing_reference"`，不算任何阈值（已实现）。
- M(x_ref) 低于参考有效性门槛 m_min：保持率与派生阈值 N/A，只报原始值与绝对差。
- 通道/池账本缺失或校验失败：`analysis_archive_missing` /
  `analysis_archive_invalid:*`，单列平台缺口。
- 进程内部讨论不计量；未经过 benchmark 链路的消息不记账
  （`COMMUNICATION_STAGE22_DESIGN.zh-CN.md` 冻结约束）。

### 2.5 分母政策（自分解落地）

- 通信轴分母是**实验条件网格**（预注册容量/延迟/丢包档位 × base_instance ×
  channel seed），与模型分解无关；模型自选是否使用通信工具，不构成 N/A 或扣分。
- 保持率分母锚定各方法相同的绝对容量档位（协议 §3.2"主排名只使用对所有方法
  相同的绝对容量档位"）；方法特定加点只用于临界点区间，不用于档位间排名。
- 配对以 base_instance 为单位；不同方法的不同消息轨迹不按消息序号强行一一配对。

### 2.6 未冻结参数

- **CA1** 每任务族容量锚点 C_0（bit/s）。
- **CA2** 参考有效性门槛 m_min^comm / m_min^step。
- **CA3** 保持率阈值集合（代码默认 `(.9, .5)`，正式值待拍板）。
- **CA4** 端点身份命名（`cloud` / `edge:<id>` / `robot:<id>`）——STAGE22 接口问题 1。
- **CA5** companion contract v1 vs Goal contract v4——问题 2。
- **CA6** 是否允许跨 edge p2p——问题 3。
- **CA7** `consume` 显式确认 vs 送达即计 AoI delivery——问题 4。
- **CA8** trusted 物理时钟/送达时刻/规划计时的宿主组件——问题 5。
- **CA9** 临界点自适应加点的触发条件、最大次数与停止精度（协议 §3.2 要求预注册）。

## 3. 时序协同（temporal_coordination，DIMENSION_STATUS `"M/P"`）

### 3.1 指标定义

通俗：分工定了之后，相互依赖的事能不能对上时间——该等的等、该同步的同步、
该排他的排他、交接不断档、不无故停摆。形式化沿用协议 §3.3：三类联合报告的
前置/同步指标（PrecE2E/Opportunity/GivenOpportunity 与 Sync 同构）、交接间隙
G_h 与服务未覆盖 U_s、互斥 MutexAny/MutexOverlap、协调停滞 StallRate 及其右删失
首次/恢复分布、严格逻辑死锁率（仅 wait-for graph 证书覆盖时）。`not_reached_system`
保留在端到端分母，`not_evaluable_external` 披露后剔除。

统计单位：关系/组/交接/资源占用区间/停滞区间；时间量（秒或决策步）以冻结时间
基准报告，事件未发生按右删失。

### 3.2 生产器

| 量 | 生产器 | 状态 |
| --- | --- | --- |
| 事件/动作/计划账本（原始素材） | `goal_session.py::GoalSession`（events/actions/plans）、`goal_episode_runner.py::FormalGoalEpisodeRunner`、技能收据 `skill_receipts.json`、业务证据 `business_evidence.json`（runner 写出） | 已有，raw-only |
| 并发执行基础设施 | 无（SCOREABILITY_REASONS 第 4 条：正式 runner 串行） | **缺生产器**（viewer 批次二 H：同一 native clock 重叠执行区间+实际完成事件）。建议实现位置：`goal_episode_runner.py` 后端调度层 |
| Q(t) 里程碑进展曲线 | 无 | **缺生产器**。建议实现位置：新模块（如 `goal_progress_curve.py`），从 business_evidence/native 读回事件驱动更新，按任务合同冻结权重 |
| 就绪时刻 t_ready 与就绪集 R(t) | 无 | **缺生产器**，与 Q(t) 同一模块 |
| 协调停滞状态机（候选区间、确认、恢复谓词、删失） | 无 | **缺生产器**。建议实现位置：新模块（如 `coordination_stall.py`），消费 Q(t) 与动作映射 |
| 同步窗口/互斥占用/交接事件检测 | 无 | **缺生产器**，依赖并发与 native 时钟 |
| native 仿真时钟 | 无（当前 `clock_source="goal_host_receipt_clock"`、`physical_clock_bound=False`） | **缺生产器**（native probe 侧产出，需 trusted 宿主确认） |

### 3.3 门禁

1. 并发证据：同一 native clock 上的重叠执行区间与实际完成事件（viewer 批次二
   验收；多 episode 并行不算）。
2. 动作-任务映射可验证：无法提供映射的任务族不计算动作门控 StallRate 主指标，
  只报 `progress_stall_unverified` 诊断（协议 §3.3 规则，不得人工补标）。
3. native 时钟通过时钟真实性门禁（SCORING_CHECKPOINT_DESIGN §6 门禁 4）前，
  所有时序指标不入分母。
4. C 覆盖完整状态域的 checkpoint + sham 容差复现通过后，耦合/解耦同快照配对才
   允许（viewer 批次二 C）。

### 3.4 N/A 与删失政策

- 机会数为零：GivenOpportunity 类指标 N/A（协议明确）。
- 无互斥资源占用：MutexOverlap N/A。
- 无已确认停滞（ΣM_i=0）：恢复类指标 N/A。
- wait-for graph 证书覆盖为零（ΣJ_i=0）：严格死锁率 N/A；协调停滞不得改称死锁。
- 未在 H 前发生的停滞/恢复事件按右删失，H 不填成真实事件时刻。
- 评测器注入故障导致关系不可评价：`not_evaluable_external` 剔除并披露数量原因。

### 3.5 分母政策（自分解落地）

- 前置/同步/交接关系来自**任务合同声明的公开里程碑与使能关系**（作者侧冻结），
  不来自模型自拆的内部计划结构；模型自行增加的内部依赖不进分母也不进分子。
- 端到端分母含系统未推进到的关系（`not_reached_system`），防止模型少拆任务
  缩小分母——少拆导致未推进在端到端口径自动计为未满足。
- 里程碑数不替代固定子任务数：分母单位是"关系/组/事件机会"，不是里程碑计数。

### 3.6 未冻结参数

- **TC1** Δt_obs（采样周期/时间戳分辨率）。
- **TC2** δ_stall（停滞确认最短连续时长）。
- **TC3** δ_clear（解除确认最短连续时长）。
- **TC4** ε_Q（Q 视为未变化的测量容差）。
- **TC5** T_a^max（每类标准高层动作超时表）。
- **TC6** 同步窗口 W_g 与交接容差 G_h^max 的逐任务族取值。
- **TC7** 每任务族 H。
- **TC8** 是否构造 wait-for graph 证书（哪些任务族支持严格死锁口径）。

## 4. 动态重构（dynamic_restructuring_scale `"M"` / _failure `"P/M"`，两子轴并列）

### 4.1 指标定义

通俗：执行中任务变多/团队变大（规模响应）或成员/能力损失（失效恢复）后，能否
重新组织并恢复推进；变后确实不可行能否正确说不行。形式化沿用协议 §3.4：
外生事件链 t_trigger→t_valid_response→[t_confirm]→t_resume；可行变化的恢复事件
概率 P_e(H) 与右删失恢复时间、受影响里程碑进展 Q_aff(H)、剩余/增量完成率、
积压清零、同快照配对分支差（Δ_join/恢复保持 vs healthy twin）；不可行变化的
IRR_post/FIR_post 与正确声明右删失延迟；负对照的虚假重构率
SpuriousReconfigRate。规模响应与失效恢复分别报告。

统计单位：外生变化事件（预注册测量点）；时间量为右删失时长；配对单位为
base_instance 的同快照分支。

### 4.2 生产器

| 量 | 生产器 | 状态 |
| --- | --- | --- |
| 因子与外生事件设计（team_continuity：stable/member_exit/replacement_join/capability_change；communication_quality 5 档） | `factor_matrix.py`（两因子轴定义；默认 goal 套件据此展开 36 包/28 factor pair，见 `COLLABORATION_MEASUREMENT_AUDIT.zh-CN.md`）、`factor_experiment.py::compile_factor_pair()` | 已有（条件生成） |
| 符号级团队生命周期 | `team_lifecycle.py::TeamLifecycle` | 已有（符号层） |
| 作者候选（初始不可行/唯一提供者退出/降级） | `goal_deployment_cases.py::build_deployment_cases()` | 已有，authoring_only |
| 变化触发与重评估 epoch | `goal_session.py::GoalSession._invalidate_deployment_assessment()` + deployment_assessment 历史（initial/runtime 相位在 `deployment_assessment_observations()` records 中区分） | 已有，raw-only |
| 同快照 fact/reference 分支 | `goal_checkpoint.py::build_checkpoint()` / `validate_checkpoint()`（骨架）；scoring adapter `checkpoint_requirements.status="planned_not_captured"`、七状态域 | **缺生产器**（保存/恢复未实现）。建议实现位置：`goal_checkpoint.py` + 新 restore 服务；验收按 SCORING_CHECKPOINT_DESIGN §6 |
| 事件链 t_valid_response/t_confirm/t_resume 检测 | 无 | **缺生产器**。建议实现位置：新模块（如 `restructuring_event_chain.py`），挂在 GoalSession 事件流与 Q(t) 之上 |
| Q_aff(t) 受影响里程碑进展 | 无 | **缺生产器**，依赖第 3 节 Q(t) 生产器 |
| healthy twin / no-join 参考分支 | 无 | **缺生产器**，依赖 checkpoint 并行分支（批次二 H） |
| native 成员退出/能力失效物理执行 | 无（team_lifecycle 为符号级） | **缺生产器**。建议实现位置：native 侧按 `factor_matrix.py` 注明的实现路径（ground-only 替换路径/导航接口命令准入）扩展 |

### 4.3 门禁

1. 事件纳入由评测器注入日志与状态校验器判定，不由被测系统是否检测到决定
   （协议 §3.4 外部可验证事件链）。
2. 同快照分支比较必须通过保存/恢复一致性 + sham 回放 + 分支隔离门禁
   （SCORING_CHECKPOINT_DESIGN §6 门禁 1–3）。
3. 突变后可行/不可行由独立 oracle 标注（复用第 1 节门禁）；不可行实例不进恢复
   失败分母，其正确声明是独立成功结果。
4. 动态测量点要求 pending 需求由独立 provider 判定
   （`goal_deployment_feasibility.py::DeploymentFeasibilityEvaluator` 的
   `pending_requirements_provider` 约束：epoch>0 且无 provider 时跳过，不凭
   里程碑未终结推断不可行）。

### 4.4 N/A 与删失政策

- 受影响里程碑集为空且无受影响服务：该事件无恢复评价机会，N/A。
- 评测时限内 t_need 未发生（备用/全程冗余成员）：响应与恢复时间 N/A，转入
  无需求负对照统计；t_need 前征用标记为需求前征用，不记负延迟。
- 未在 H 前恢复的可行事件保留为右删失；恢复观察被新外生故障打断时在该故障
  起点右删失。
- oracle failed 的测量点：退出 IRR_post/FIR_post 分母，单列平台缺口（同 1.4）。
- 无需求窗口因其他合法事件提前结束：窗口截止，不把之后必要重构误记虚假重构。

### 4.5 分母政策（自分解落地）

- 外生变化及其预注册测量点是分母的唯一来源（作者侧冻结 hidden_event_schedule）；
  模型自行发起的重构不产生测量机会，不扩大分母。
- Q_aff 的里程碑集合 M_aff 在触发时刻按环境真值冻结（新增工作、原方案受影响的
  未完成工作、依赖失效服务的工作），与模型拆分粒度无关；权重随任务合同冻结。
- 需求扩张分支与 no-change 分支工作集不同，只比较合同定义的新增工作/受影响
  里程碑/积压，不比较未归一化总完成量（协议 §3.4 同快照原则）。
- IRR_post/FIR_post 分母 = 突变后 oracle 标注的不可行/可行测量点数，不含
  oracle failed 点。

### 4.6 未冻结参数

- **DR1** 分支后续观察窗 τ。
- **DR2** 恢复判定进展量 δ_Q^aff 与服务门槛。
- **DR3** 恢复持续时长 h_R。
- **DR4** M_aff 冻结规则字段（哪些里程碑算"受影响"的机器判定）。
- **DR5** 无需求窗口 W_no-need 与虚假重构判定字段（哪些计划变化算激活）。
- **DR6** 积压清零持续时长 h_B。

## 5. 跨回合学习（cross_episode_learning，DIMENSION_STATUS `"M"`）

### 5.1 指标定义

通俗：同一场景/任务族多轮跑，留着经验的条件 R 比每轮清空经验的条件 C 是否
可重复地更好、更快、更少重犯错。形式化沿用协议 §3.5：共同首轮主干 + 逐轮
R/C 配对；不混合单位的逐轮学习增益 LG_n^{Y/Q(H)/AUP}、完成时间 RMST 差、
总体增益 AULG、达标轮数 N_τ（右删失）、错误/陷阱复发下降 ΔRec_e（G/O/Z 三元
机会判定）。学习在外显行为层定义，不要求参数更新。

统计单位：base_instance × 轮次 n × 条件（R/C）× 指标；配对聚类单位为
base_instance。

### 5.2 生产器

| 量 | 生产器 | 状态 |
| --- | --- | --- |
| 任务族登记、eligible/ceiling/unknown 判据、分母边界（L0–L3 审计） | `LEARNING_DIFFICULTY_AUDIT.zh-CN.md`（文档层；判据已冻结草案） | 已有（审计） |
| episode_round 触发点设计 | `goal_scoring_adapter.py` checkpoint_requirements.preregistered_trigger_design 含 `episode_round`；`initial` | 设计已有，`planned_not_captured` |
| learning opportunity registry | 无 | **缺生产器**。建议实现位置：新模块（如 `learning_opportunity_registry.py`），出题阶段冻结 base_instance/轮次/指标/H/阈值/陷阱键/反馈来源/oracle 证据 |
| R/C 条件 runner（经验保留/清空） | 无 | **缺生产器**。依赖 participant_recoverable 状态域（checkpoint 七域之一）+ 记忆清空等价证明；建议位置：runner 之上的多轮编排层 |
| G/O/Z 复发事件记录 | 无 | **缺生产器**。建议位置：与 Q(t)/业务证据同一事件流标注 |
| AUP/RMST/生存曲线计算器 | 无 | **缺生产器**。建议实现位置：新统计模块（如 `learning_statistics.py`），以 base_instance 为聚类单位 |

### 5.3 门禁

1. L-G1 顺序（LEARNING_DIFFICULTY_AUDIT P1-L 阶段门）：先 P0 五场景可信终局
   谓词 + 原生读回 + 剩余任务二元 oracle；再冻结机会注册表与 pilot；再真实
   checkpoint/restore、sham、编键 RNG、R/C 清空；最后接真实时钟、通信账本和
   Q(t)/完成事件。
2. eligible 判据五条全部满足（任务族/机会/同快照 R配对/恢复与 sham verified/
   外部终局与 Q(t) 证据完整且无平台污染）。
3. `retention_control_unverified` 时可报多轮现象，不进严格经验保留因果汇总
   （协议 §3.5）。
4. viewer 批次三：学习 R/C 共享首轮历史、记忆保留/清除分离。

### 5.4 N/A 与删失政策

- unknown 层：任一必要事实未验证（oracle 失败、无法配对、checkpoint/sham 未过、
  时钟或 Q(t) 缺失、基础设施污染）→ 只进平台缺口报告，不进模型对错或学习效果
  分母。
- ceiling 层：机会有效但无预注册上升空间（C 条件已达上限/陷阱无再发机会）→
  保留在覆盖率报告，不改写、不删除。
- 未在 E 轮内达标：右删失；只统计达标系统或事后选择达标条件均禁止。
- 共同首轮未合法接触陷阱（G=0）：可报事件率，不得宣称"记住并避开"。
- 分母为零写 N/A，不填 ε。

### 5.5 分母政策（自分解落地）

- 学习分母 = 预注册 learning opportunity registry 条目数（N_registered_
  applicable = N_eligible + N_ceiling + N_unknown，LEARNING_DIFFICULTY_AUDIT），
  与模型表现无关，禁止按结果剔除。
- 同一任务族仅换见证/别名/等价 M0/M1 结构签名只登记一次；通信因子等级是条件
  字段不是新任务族；factor pair 数不是学习样本数。
- Y/Q(H)/AUP/RMST/复发各自独立分母，不事后选择有利指标。
- 用户"同任务×10 重复"原则的落点候选：完全重复实例的重复轮次供给
  （见 G1/G2，归属待拍板）。

### 5.6 未冻结参数

- **XL1** 正式轮数 E（协议要求 E≥3，pilot 定）。
- **XL2** 达标阈值 τ_k 与持续轮数 h_k（逐指标）。
- **XL3** 陷阱集合与 G/O/Z 判定的事件键词表。
- **XL4** pilot 样本量与目标 CI 宽度。
- **XL5** R/C 记忆清空等价性的证明方式（哪些可写状态必须覆盖）。

## 6. 成本效率（cost_efficiency，DIMENSION_STATUS `"M/P"`）

### 6.1 指标定义

通俗：拿到同样的任务结果，高层协调花的启动、通信、指令、重规划成本是否节制、
进展是否及时。形式化沿用协议 §3.6：三类终止结果（effective_execution /
pre_execution_failure / ineffective_execution）与分层有效结果率；全样本成本向量
（C_fixed、N_msg、B_offer、B_link、交互/指令/重分配/重规划计数，均累计到
t_term）；Q_final 与 Q(H) 并存；PGI/nPGI 仅在真实执行窗口定义域；非劣门槛
（δ_p/δ_Q）通过后才允许成本效率优劣判断，Pareto 支配而非标量总分。本体能耗、
运动步数、硬件功耗不入核心成本（协议 §3.6 明确排除）。

统计单位：episode（事实分支）；成本为计数/字节/代理启动值；nPGI 为无量纲；
时间量 T_exec 秒。

### 6.2 生产器

| 量 | 生产器 | 状态 |
| --- | --- | --- |
| 接受动作计数（按类型，含 dispatch/revise_plan 等） | `goal_cost_evidence.py::build_raw_cost_evidence()`（accepted_actions.counts_by_type，source `goal_session.actions`） | 已有，raw-only |
| host 收据时钟 | 同上（clock.source=`goal_host_receipt_clock`；明确声明不是物理时间） | 已有（仅账本界） |
| 通信字节（总/池/方向） | 同上 `_communication_bytes()`：只采信 replay validated 的 pose/camera/communication 账本，跨文件去重，方向不完整 fail-closed null | 已有，raw-only |
| 激活成本代理 | `goal_episode_runner.py::team_usage_summary()`（写 `team_usage.json`，source `candidate_pool_design.activation_cost`）→ cost_evidence.activation_cost_proxy（`is_proxy=True`） | 已有，proxy |
| 规划/重规划计算耗时 | `goal_cost_evidence.py::_explicit_compute_cost()`（字段 `planning_compute_cost_s`/`replanning_compute_cost_s` 预留，无生产者恒 None） | **缺生产器**。建议实现位置：trusted host scheduler（batches 一拍板后由 runner 记录），严禁从 participant trace 推断 |
| Q(t) 与成本同钟的进展曲线（PGI/nPGI、Q_final/Q(H)） | 无 | **缺生产器**，依赖第 3 节 Q(t) 生产器 |
| 三类终止类别判定 | 无（`goal_episode_runner.py` 的 `result.json` 目前只带 deployment 终止信息，无 E_cost/X_exec 三分类） | **缺生产器**。建议实现位置：`goal_episode_runner.py` 归档层按 E_cost/X_exec 判定顺序扩展 |
| 每资源启动单价表 c_j^start | 无（activation_cost 仅 proxy） | **缺生产器/待冻结**。建议实现位置：`candidate_pool_design.py` 升级为正式冻结单价表 |
| 无收益重规划的 hold-plan 配对参考分支 | 无 | **缺生产器**，依赖 `replan_pre` checkpoint |

### 6.3 门禁

1. Q(t) 与成本同钟（SCORING_CHECKPOINT_DESIGN §3 成本效率行）：真实时钟接入前
   PGI/AoI 类积分不入分母。
2. 三类终止类别全样本保留：`pre_execution_failure` 与 `ineffective_execution`
   的成本不置零、不从无条件均值删除（协议 §3.6）。
3. 成本效率优劣判断前置非劣门槛（LCB95(p_A-p_B)≥-δ_p 且 LCB95(Q_A-Q_B)≥-δ_Q）；
   未过门槛只能表述"支出较低且任务结果较差"。
4. 探针/sham/oracle 分支成本单独记录，不回填事实分支。
5. 动态重构触发的必要重分配/重规划不计为成本浪费（协议 §5 归属规则）。

### 6.4 N/A 与删失政策

- T_exec=0：nPGI 及一切单位时间指标 N/A（`undefined_zero_execution_time`），
  保留零时长记录与原始累计量。
- Q_final=0：单位进展指标 `undefined_zero_progress`，条件量不给失败系统效率优势。
- 不可行实例：不计算 Q(H)/PGI/nPGI，改报正确识别率、声明延迟（右删失）与声明
  前成本；不与可行层合并 Pareto 判断。
- 时钟/账本/单价未接或未冻结：对应分量 null 并写原因，不填零
  （`goal_cost_evidence.py` 的 validity 字段组）。

### 6.5 分母政策（自分解落地）

- 成本分母 = 全部有效事实分支 episode（按可行性分层），与模型拆分粒度无关；
  模型多拆产生的额外计划/交互计入其自身成本，不改变分母。
- 模型自主选择的团队规模是其成本向量的组成部分：出题人不限制团队规模
  （用户原则，TODO.zh-CN.md 2026-09-22），启动成本作为测量信号而非约束。
- 效率比较的分母锚定相同 base_instance/条件/外生过程（协议 §3.6 横向原则）；
  单位进展与单位时间指标必须同时保留原始累计值。

### 6.6 未冻结参数

- **CE1** 每资源启动单价 c_j^start（或确认保留分类向量不求和）。
- **CE2** 最低有效固定时限进展门槛 q_min。
- **CE3** 非劣门槛 δ_p 与 δ_Q。
- **CE4** t_term 两类终止的声明接口口径（何种声明算"标准接口声明结束"）。
- **CE5** 用户"重规划三层计费"的层定义（候选解释：首次规划/外生必要重规划/
  无收益重规划三档费率，**本条为待拍板解释，非冻结定义**）与各层费率。

## 7. 跨轴公共政策

1. **同任务重复与聚合层级**：用户原则"同任务×10"（评测可信度三支柱记忆，
   仓库无书面记录，unknown）。协议侧的既有层级是"base_instance 内随机重复
   （如 channel seed）先实例内均值，再以 base_instance 为统计单位"（协议
   §2.6；`communication_analysis.py` 已按此实现）。×10 归属于哪一层（实例内
   重复 K，还是独立配对重复 s，还是学习轴的轮次 n 之外再加重复）由 G1/G2
   拍板；拍板前不得把 10 个同任务 episode 当独立 base_instance。
2. **Q(t) 与成本同钟**：一切进展类指标（Q(H)、AUP、PGI/nPGI、Q_aff、恢复判定）
   与成本累计必须来自同一可信时钟与事件流（SCORING_CHECKPOINT_DESIGN §3/§5）。
   host 收据时钟只能作诊断；native 时钟门禁通过前不产生任何比率分母。
3. **配对身份（分支/条件）**：分支只用协议四类 fact/reference/sham/
   intervention，绑定 checkpoint_id、base_instance_id、condition_id、pair_repeat
   与 trigger_event_key（协议 §2.6；SCORING_CHECKPOINT_DESIGN §4 机器可读记录）。
   当前归档 `branch_id=null` / `single_episode_no_protocol_branch`，不得推断
   事实/参考关系（`goal_scoring_adapter.py`）。
4. **按单场景审核**：逐场景出评分证据矩阵；一个场景（如 Factory 冻结 hash
   pending）不否决其他场景已满足的证据（viewer 批次三验收）。
5. **不填零总则**：分母为零、参考低于门槛、零执行时间、无机会、任务语义不
   适用时，写预声明 N/A 标签（`undefined_no_output`、
   `not_applicable_protocol_mismatch`、`undefined_zero_progress`、
   `reference_below_threshold`、`missing_reference` 等）；平台/基础设施失败
   单列（如 `oracle_infrastructure_failure_count`），不转化为模型得分或零成本。
6. **删失总则**：响应/确认/恢复/达标/完成等事件未在 H 内发生一律右删失并报
   "H 内发生概率"；H 不得填为真实事件时刻；仅事件已发生样本的均值只作诊断
   （协议 §2.6/§4.2）。
7. **进度分与重试扣分**：用户原则"进度分参考 RoboChallenge（阶段拆分/重试
   扣分）"。协议 Q(t) 是权重里程碑的加权和（无扣分项）；是否把重试扣分引入
   Q(t)/PC（以及扣分系数）是协议修订级决策（G4），未拍板前 Q(t) 按协议原式。
8. **发布标志**：所有轴在本文定义下仍 `raw_only`/`not_protocol_score`；三标志
   false 不因定义冻结而改变，只有第 8 节门禁逐条解除并经独立 release review
   后才逐轴讨论。

## 8. 与 SCOREABILITY_REASONS 的对应

`goal_scoring_adapter.py::SCOREABILITY_REASONS` 五条逐条解除条件：

| reason | 解除所需生产器 | 解除所需门禁 |
| --- | --- | --- |
| `fixed_subtask_metrics_require_revision_for_goal_decomposition` | 本文档（分母/N/A/删失定义）获用户拍板；自分解分配可执行性校验器（1.2 末行）与修订后部署层公式注册 | 用户批准修订后的分母政策（TA1/TA2）；实现层验收前仍 raw-only |
| `no_native_communication_episode_provenance` | episode/run 启动端点/池绑定（2.2 表第 6 行）；真实下行与生命周期通知入共享传输账本（批次二）；replay 校验通过 | 批次一拍板 1/2 落地 + 批次二真实下行全链验收（延迟/过期/退出/取消竞争）；方向分解 fail-closed 保持 |
| `no_physics_checkpoint_or_fact_reference_sham_branch` | `goal_checkpoint.py` 保存 + 新恢复服务；七状态域（environment_physics/participant_recoverable/visible_history/rng_and_clocks/host_scheduler_and_pending_communication/deployment_gate_epoch/preregistered_oracle）全覆盖 | SCORING_CHECKPOINT_DESIGN §6 门禁 1–3：恢复一致性、sham 容差复现、分支隔离；不得把 native_restore_verified 直接改 true |
| `formal_runner_is_serial_and_has_no_protocol_concurrency_evidence` | 并发执行基础设施（批次二 H）；同一 native clock 重叠执行区间 + 实际完成事件的生产器 | viewer 批次二验收口径；多 episode 并行不算；恢复困难不得用禁用记忆/缩团队/强制串行规避 |
| `execution_and_business_validation_are_release_gated` | 独立判卷证据链（native oracle registry verified、business evidence、判卷 reader/探针采集）与逐轴评分生产器 | 独立 release review（餐厅注册翻转式发布动作：证据 2/3 + review）；单 episode 不解除（STAGE33 餐厅先例） |

## 9. 未冻结参数汇总（共 44 项，均需用户/viewer 拍板）

| 组 | 编号 |
| --- | --- |
| 跨轴公共（7） | G1 ×10 重复的层级归属；G2 实例内重复数 K 与聚合公式；G3 逐任务族 H；G4 Q(t) 是否引入 RoboChallenge 式阶段拆分/重试扣分及系数；G5 重规划三层计费层定义（与 CE5 联动）；G6 bootstrap 样本数/seed 策略（代码现默认 2000）；G7 是否启用 0–100 合成分（协议 §4.4 仅留原则） |
| 任务分配（4） | TA1–TA4（§1.6） |
| 通信适应（9） | CA1–CA9（§2.6，含 STAGE22 五个接口问题） |
| 时序协同（8） | TC1–TC8（§3.6） |
| 动态重构（6） | DR1–DR6（§4.6） |
| 经验学习（5） | XL1–XL5（§5.6） |
| 成本效率（5） | CE1–CE5（§6.6） |

拍板前默认行为：对应指标 raw-only 或缺生产器标注；任何实现提交不得引用本文
未拍板参数的临时取值。

## 10. 最关键的定义决策点（建议优先拍板）

1. **自分解主线下任务分配轴的正式公式**（TA1/TA2）：部署四类率是否作为本轴
   唯一核心结果、自分解可执行性校验器是否存在——决定本轴能否脱离
   `not_applicable_protocol_mismatch`。
2. **Q(t) 进展底座**（G3/G4/TC1–TC7）：里程碑权重、H、停滞参数与是否引入
   重试扣分，是时序/动态重构/学习/成本四轴的共同分母与时钟前提。
3. **跨回合学习 R/C 的记忆清空等价性与轮数**（XL1/XL5 + G1/G2）：决定"同任务
   ×10"原则落在学习轴还是全部轴的重复层，也是 sham/checkpoint 门禁的第一
   消费者。
