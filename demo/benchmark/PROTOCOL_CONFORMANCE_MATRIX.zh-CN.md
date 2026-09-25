# 协议逐节交叉映射矩阵（PROTOCOL_CONFORMANCE_MATRIX）

状态：机器可用的差距台账（raw gap ledger），不实现任何公式，不改代码语义。
日期：2026-09-25。
基线：分支 `codex/benchmark-stage57-oracle-integration`，HEAD `0436cd0`（任务描述中的
`1387c0c` 之后另有 1 个提交 `0436cd0 feat(benchmark): independently recompute episode
facts with explicit gap semantics`）。
对照协议：`/home/pupil/下载/异构多机器人高层协作智能+Benchmark+能力分类与评测协议(1).md`
（2476 行；仓库内同名无后缀副本的既登记哈希为
`539822d71b46b520c21df9170b182e0b7fe66016b8cfb26825a33294f52651c1`，见
`SCORING_DEFINITION_MATRIX.zh-CN.md` §0 与 `protocol_runner.py::DEFAULT_PROTOCOL_SHA256`；
本矩阵按"(1)"版全文逐节核对，未重新计算哈希，两版差异 unknown）。

阅读约定：

- "对应物"列给出真实存在的文件+符号（本次以 Read/rg 核对）；"无"表示未检索到任何
  对应实现，并给建议落点。
- 状态取值：**已满足**（语义与词汇均对齐）/ **部分满足**（结构或子集对齐）/
  **缺失**（无对应物）/ **词汇冲突**（语义相近但机器词不同，单列）。
- 工作量：S ≤1 天 / M 数天 / L >1 周。
- 测试基线：`source/EAI/test/test_benchmark_goal_*.py` 共 **1302** 个测试函数
  （`rg -c "def test_"` 实测），`source/EAI/test/` 全目录 2601 个；任何合同 schema
  扩展按"新增可选字段+新版本号+fail-closed 校验"的既有先例（v2→v3 增
  `deployment_assessment`，`goal_driven_contract.py::_PUBLIC_TASK_FIELDS_V3`）评估影响面。

---

## 1. 逐节映射表

### 1.1 协议 §2.6（统一任务合同、进展定义与统计协议）

| # | 协议条目 | 我们的对应物（文件+符号） | 状态 | 差距一句话 | 量 | 依赖 |
|---|---|---|---|---|---|---|
| 2.6-01 | `task_id` 稳定唯一+生成器版本 | `goal_driven_contract.py::_PUBLIC_TASK_FIELDS` 的 `task_id`、`skill_catalog_version`；`provenance.canonical_sha256` 绑定 | 部分满足 | 无独立"生成器版本"字段（由 skill catalog 版本间接承载） | S | 无 |
| 2.6-02 | `initial_state`/`goal_state`/终止条件 | `mission{scene_key,goal,context}` + `required_outcomes`（`_OUTCOME_FIELDS`）+ `budgets.simulation_s` | 部分满足 | goal 侧是文本 `acceptance`，无机器可读目标状态谓词；H 由 budget 充当 | M | 无 |
| 2.6-03 | `contract_milestones`（跨系统固定 ID/语义/权重/谓词） | `required_outcomes`（id/description/acceptance/evidence_expectations）+ 隐藏 `outcome_predicates`（`validate_goal_hidden_evaluator`） | 部分满足 | 里程碑=outcome 二级结构已有；谓词在 hidden evaluator 侧非公开合同，且无 `weight` | M | 无 |
| 2.6-04 | 里程碑 `weight`（Q(t) 权重） | **无**（`goal_progress_evidence.py` 显式 `"weighted_progress": None`、`missing_inputs:["frozen_milestone_weights"]`；`protocol_runner.py` 试点有 1.0/2.0 权重的合成 `progress_milestones`） | 缺失 | 合同无冻结权重字段；`goal_restaurant_business_facts.py:152` 同样 None | S（加字段）/M（全链） | 拍板 G4/权重表 |
| 2.6-05 | 里程碑 `activation_rule`+工作集版本 v（动态事件冻结新分母） | 相邻物：`goal_restaurant_reassessment.py::REASSESSMENT_TRIGGERS`（`milestone_state_change` 触发重评估 epoch） | 缺失 | 无"激活规则决定 M_active 并按版本冻结分母"的机制；重评估只管部署标签 | M | 2.6-04 |
| 2.6-06 | 里程碑 `stability_duration`（持续达标才算 y=1） | **无**（`GoalProgressRecorder.append` 边界采样，`satisfied` 可 true/false/unknown 且可撤销；`record_business_outcome` 一次写终） | 缺失 | 采样三态+可撤销与协议二值+稳定时长不同构 | M | 2.6-04 |
| 2.6-07 | `monotonicity`/`visibility`/`anti_double_count_rule` | **无** | 缺失 | 合同无这三个字段；`changed_predicate_ids` 只做变更标记 | S（字段）/M（判定） | 2.6-06 |
| 2.6-08 | `capability_constraints`（ID/门槛/容差/`aggregation_rule`/激活/证据字段） | `goal_constraints.py::validate_constraint_model`（route_envelope_resource_model：载荷/接口/包络/资源容量）+ `goal_deployment_feasibility.py::scene_requirements`（`capability_alternatives` 析取、`transport_object_id`、`reason_code`） | 部分满足 | 有物理能力门槛与析取聚合的实质检查，但无逐义务 `constraint_id/threshold/tolerance/aggregation_rule` 四值枚举（`single_actor/coalition_sum/exists/all`） | M | 无 |
| 2.6-09 | `role_constraints`（独立类：required_role/cardinality） | **无**（仅 `mobility∈{ground,aerial}` 物理现实与 `DEPLOYMENT_REASON_CODES` 的 `unavailable_robot_type`） | 缺失 | 无角色本体与数量约束；平台类别未作为合同义务类 | M | 2.6-08 |
| 2.6-10 | `milestone_validity_rules`（只描述外部状态合法性） | `goal_constraints.py` docstring 声明的 envelope/resource 检查 + 领域判卷 `goal_session.record_business_outcome(evaluator_ref)` | 部分满足 | 判卷器按场景硬编码，无"合法性规则集"合同字段 | M | 无 |
| 2.6-11 | `system_plan_schema`（计划版本/committed_plan/子任务/里程碑引用/声明/确认/重规划事件） | `build_goal_participant_api` 的 `plan_contract`（fields/task_fields/execution_fields/timing）+ `semantic_goal_plan_signature` | 部分满足 | 计划结构有；缺里程碑引用字段（task_fields 无 `milestone_id`）、committed_plan 事件与确认词汇 | M | §2.7 批次 |
| 2.6-12 | `success_predicate`+稳定时长（Y_i 判定） | `GoalSession._business_task_success`（全部 outcome validated 且无 failed）+ 隐藏谓词 | 部分满足 | 无稳定时长、无"Y=1 需在 H 前成立"的显式事件 | S | 2.6-06 |
| 2.6-13 | `minimum_team_certificate`（删必要能力后所有合法分解不可行证书） | `mechanism_templates.validate_team_necessity_certificate` + `goal_alternative_path_certificate.validate_alternative_path_certificate`（负标签仅在带 alternative-path 完备性证书时给出，`goal_restaurant_reassessment.py:526-535`） | 部分满足 | 有"反例路径完备性"证书等价物；无按"删除必要能力/角色"枚举的证书 schema | M | 2.6-08/09 |
| 2.6-14 | `deployment_oracle` 输出 `deployable`/`team_state_infeasible` | `goal_deployment_feasibility.py::evaluate_deployment_feasibility`（label ∈ {deployable, **not_deployable**, unverified}）；`goal_restaurant_initial_oracle.RestaurantInitialPositiveOracle`；`goal_remaining_feasibility_oracle.RegisteredRemainingTaskOracle` | **词汇冲突** | oracle 真值词用了系统的 `not_deployable`（协议保留给系统声明 D_i），协议要求 `team_state_infeasible` | S（别名/映射层） | TA1 拍板 |
| 2.6-15 | `assistance_policy`（合法触发/终止/禁止补充资源/重试） | `goal_deployment_assessment.deployment_assessment_contract`（termination_event=`human_assistance_requested`）+ `GoalSession` `_deployment["termination"]{category,resource_injected:False}` | 部分满足 | 求助即终止且不注资已实现；禁止重试规则未 schema 化（行为上 episode 已 fail-closed） | S | 无 |
| 2.6-16 | `target`/`nuisance` 冻结因素声明 | `experiment_factor{id,level,public_rule,frozen_inputs}` + hidden `factor_effect{changed_dimension,frozen_inputs}` | 部分满足 | 有 changed/frozen 二分；无 `nuisance` 命名字段 | S | 无 |
| 2.6-17 | `failure_events`（能力/角色/必需里程碑/技能/信息/时序/服务失败） | 隐藏 `outcome_predicates[].failure_codes` + `DEPLOYMENT_REASON_CODES`（6 条）+ `classify_plan_rejection` | 部分满足 | 缺协议 §3.1 的四个约束失败标签（`capability_constraint_violation` 等）与角色/时序/服务标签 | M | 2.6-08/09 |
| 2.6-18 | `oracle` 权限（可行性/参考性能/探针分别声明） | `goal_restaurant_initial_oracle._validate_authorities`（单 oracle 权限组） | 部分满足 | 仅初始正证书场景有权限声明；无三类权限的通用 schema | M | §7 探针 |
| 2.6-19 | `intervention_points`（预注册介入时刻） | `goal_checkpoint_branch.build_branch_plan`（强制 pre-change checkpoint） | 部分满足 | 有"变更前快照"纪律；无预注册干预时刻表（t_k 选择规则） | M | checkpoint 捕获 |
| 2.6-20 | `snapshot_schema`（环境/系统可恢复/可见历史/随机流计数器） | `goal_checkpoint.py::STATE_DOMAINS`（7 域：environment_physics/participant_recoverable/visible_history/rng_and_clocks/host_scheduler/deployment_gate_epoch/preregistered_oracle）+ `build_checkpoint/validate_checkpoint` | 部分满足 | schema 覆盖并超出协议四要素；`restore_status` 恒未验证（`SHAM_ISOLATION_BLOCKER="native_restore_verification_pending"`） | L（捕获/恢复） | Isaac 侧 |
| 2.6-21 | `reference_validity`（保持率门槛 m_min 与 N/A 规则） | `communication_analysis.summarize_capacity_curve(min_reference_mean)` → `reference_below_minimum` | 部分满足 | 仅通信容量轴实现；其余轴无门槛字段 | S（推广） | 无 |
| 2.6-22 | Q(t) 定义与"提前停止保持最后观测到 H" | **无正式实现**（试点：`protocol_runner.py:466` `q_t` 合成计算；Goal 轴：`goal_progress_evidence` 只有谓词样本） | 缺失 | 见第 2 节专述 | M | 2.6-04/06 |
| 2.6-23 | K 重复先实例内均值、配对差 d_i、base instance 为统计单位 | `communication_analysis._point_values`（base 内 channel_seed 均值）+ `summarize_paired_distribution_effect`（按 base×seed×level 配对）+ `protocol_runner.paired_difference` | 部分满足 | 通信轴+试点已按协议层级；Goal 主线归档无重复层（G1/G2 未拍板） | M | G1/G2 拍板 |
| 2.6-24 | 四类分支命名 fact/reference/sham/intervention | `branch_data.py::BRANCH_TYPES = frozenset({"fact","reference","sham","intervention"})`；`goal_checkpoint.py::BRANCH_IDS` 同 | **已满足** | 词汇完全一致（仅命名，执行见 §7 行） | — | 无 |
| 2.6-25 | 实例状态标签 T/V/I（`feasibility_unverified` 不入主分母、`invalid_instance` 剔除） | oracle 侧 `label="unverified"`、`oracle_status="failed"`（`gate_evaluation` 恒 failed+null 标签）；`deployment_assessment_observations` 的 `episode_oracle_eligible`/`excluded_from_deployment_metrics` | 部分满足 | unverified 语义已 fail-closed；`invalid_instance`（平台内部错误）作为独立剔除类**无** schema | S | 无 |
| 2.6-26 | 外生随机量按（版本/实例/重复/过程/实体/事件键）生成 | `goal_checkpoint_branch.branch_rng_digest`（`EAI.keyed_random.KeyedRandom`，`_RNG_KEYS={scheme,seed,process,entity,event_keys,branch_draws}`）；`CommunicationRuntime` 确定性 | 部分满足 | 键控 RNG 合同有；native 仿真随机流未接入该键控体系 | L | Isaac 侧 |

### 1.2 协议 §2.7（系统计划与统一日志接口）

现有动作词汇（`goal_driven_contract.py::_ACTION_FIELDS`/`_DEPLOYMENT_ACTION_FIELDS`）：
`select_team/submit_plan/revise_plan/dispatch_skill/cancel_skill/consume_message/deployment_assessment`。
现有事件词汇（`_EVENT_FIELDS`/`_DEPLOYMENT_EVENT_FIELDS`）：`episode_started/observation/member_join/member_exit/capability_change/communication_status/action_result/tick/deployment_assessment_required/human_assistance_requested/resource_status/deployment_change`。

| # | 协议事件 | 我们的对应物 | 状态 | 差距一句话 | 量 | 依赖 |
|---|---|---|---|---|---|---|
| 2.7-01 | `plan_version` | 无事件；`GoalSession._plan_version` 内部计数随 `action_result` 间接可见 | 缺失 | 版本号存在但不作为带子任务列表+哈希的外显事件 | S | 2.6-11 |
| 2.7-02 | `committed_plan`（里程碑映射/承担者/合同字段） | `submit_plan/revise_plan` 载荷 + `GoalSession._plans[plan_id]{version,submitted_at_s,roster_version,visible_event_sequence}`；派发强制当前计划版本（`_admit_dispatch`） | 部分满足 | 有"派发前最后承诺版本"的锁定语义；缺 `milestone_id` 映射与能力/角色声明字段 | M | 2.6-08/11 |
| 2.7-03 | `subtask_created` | 无（子任务随整计划内嵌提交，无独立创建事件） | 缺失 | 自分解粒度可不强制事件化；仅在需要覆盖率时补 | M | 2.7-02 |
| 2.7-04 | `assignment_proposed`/`assignment_confirmed` | 无（`assigned_agents` 为计划字段；无提议/确认两段式） | 缺失 | 确认语义可先记 N/A（协议允许架构不要求显式确认） | M | 2.7-02 |
| 2.7-05 | `action_started`/`action_ended` | `dispatch_skill` 受理 + `complete_skill/_complete_skill_result → action_result{status∈succeeded/failed/cancelled}` | 部分满足 | started=账本受理而非执行开始；ended 缺"外部接口状态"字段 | S | native 时钟 |
| 2.7-06 | `handoff`（原/新提供者/服务窗口/状态版本） | 无（`handoff` 是技能名不是事件） | 缺失 | §3.3 交接指标的上游 | M | 2.7-05 |
| 2.7-07 | `replan`（原因/受影响里程碑/新旧哈希） | `revise_plan{trigger_event_sequence,replaced_plan_id,reason,plan}` + `semantic_goal_plan_signature`（可派生新旧语义哈希） | 部分满足 | 原因与新旧哈希可派生；缺"受影响里程碑"字段 | S | 2.6-11 |
| 2.7-08 | `milestone_verified`（首次成立/稳定时间/失效事件） | `GoalSession.record_business_outcome`（trusted-host-only，不发参与者事件）+ `GoalProgressRecorder` 谓词样本（`changed_predicate_ids`） | 部分满足 | 判定存在但非标准事件，且无首次/稳定/失效三时刻 | M | 2.6-06 |
| 2.7-09 | `episode_terminated`（类型/时间/Y/Q(H)） | 无（termination 仅部署求助一类：`_deployment["termination"]`；预算耗尽为隐式拒绝） | 缺失 | 成本轴 t_term 与三类终止的上游 | S–M | 2.6-12/22 |
| 2.7-10 | 事件保留系统/base/条件/分支/重复/轮次索引 | `branch_data.BranchIdentity`（checkpoint_id/base_instance_id/condition_id/pair_repeat/branch_id/trigger_event_key）+ 归档 `episode_id/public_task_sha256` | 部分满足 | 分支工具侧索引齐全；Goal episode 归档缺 condition/branch/round 列（当前 `branch_id=null/single_episode_no_protocol_branch`，`goal_scoring_adapter`） | M | 分支执行 |
| 2.7-11 | 缺失里程碑映射时不计算严格覆盖率/门控时序 | 无对应判定逻辑（覆盖率指标本身缺失） | 缺失 | 属新指标的护栏条款，随覆盖率一起落 | M | 2.7-02 |
| 2.7-12 | 评测器外部状态优先于系统自报 | 已有等价原则：`GoalSession` 完成只认 trusted receipt（`complete_skill` 文档串）、`record_business_outcome` 由域判卷器给 | 已满足（原则层） | 无差距（原则已在账本边界落实） | — | 无 |

### 1.3 协议 §3.1（任务组织）

| # | 协议条目 | 我们的对应物 | 状态 | 差距一句话 | 量 | 依赖 |
|---|---|---|---|---|---|---|
| 3.1-01 | 状态机 episode→assessment→deployable→commit→dispatch / not_deployable→assist→end | `GoalSession._invalidate_deployment_assessment`（episode_started 即 epoch 0）、`_require_deployable`（select_team/submit_plan/revise_plan/dispatch_skill 前必须 deployable）、not_deployable → `human_assistance_requested` 终止 | **已满足** | 结构与顺序一致（首个任务级派发前完成判断） | — | 无 |
| 3.1-02 | `team_state_infeasibility_detection_rate` | `goal_scoring_adapter._conditional_risk_rates` 的 `correct_refusal_rate`（label=not_deployable∧decision=not_deployable / not_deployable_required_epoch_count） | 词汇冲突+部分满足 | 公式同构；oracle 标签词 `not_deployable` ≠ `team_state_infeasible`，指标名不同 | S（改名/映射） | 2.6-14 |
| 3.1-03 | `false_non_deployable_rate` | 同上 `false_refusal_rate`（分母 deployable_required） | 词汇冲突+部分满足 | 同上 | S | 2.6-14 |
| 3.1-04 | `false_deployable_rate` | `false_deployment_rate`（分母 not_deployable_required） | 词汇冲突+部分满足 | 名称近义；标签词同 2.6-14 | S | 2.6-14 |
| 3.1-05 | `pre_dispatch_safe_stop_rate`（H=1,U=1,X=0 联合） | `deployment_assessment_observations` records 的 `human_assistance_requested` + `stopped_before_epoch_dispatch`（declaration 前 dispatch_skill 计数=0）+ `pre_declaration_accepted_action_counts` | 部分满足 | 三条件素材齐（U=1 由 termination 事件、X=0 由计数），未合成单一比率（分母=not_deployable epochs 需跨 episode 聚合） | S | 2.6-14 |
| 3.1-06 | `assessment_latency`（右删失） | `host_declaration_latency_s`（declared_at_s − trigger time_s），`clock_source="goal_host_receipt_clock"`、`physical_clock_bound=False` | 部分满足 | 只差物理时钟绑定；右删失（无声明 epoch 留 unanswered）已由 `unanswered_*` 计数承载 | S–M | native 时钟（TA4） |
| 3.1-07 | 约束满足向量 CS=(cap,role,mile) | **无**（`SCORING_DEFINITION_MATRIX` §1.2 末行已登记"缺生产器"；`validate_plan_constraints` 是 fail-closed 准入门禁，不输出逐义务 verdict） | 缺失 | 需新 `goal_constraint_verifier`：输出每义务 active/satisfied/violation_reason/evidence | M–L | 2.6-08/09、2.7-02 |
| 3.1-08 | 入口门控 G_is（deployable∧D=deployable∧O^commit=1，否则分量记 0） | 无（门控量未定义；拒绝难例目前只进 deployment 层计数） | 缺失 | 随 CS 向量一并定义，防"不输出计划躲避评价" | M | 3.1-07 |
| 3.1-09 | `assessment_latency` 只表及时性不作质量 | 文档层已遵守（`SCORING_DEFINITION_MATRIX` §1.2 行 1 注明 raw-only） | 已满足（口径层） | — | — | 无 |
| 3.1-10 | 三类义务激活规则预注册、事后不增删 | `DEPLOYMENT_REASSESSMENT_EVENTS` 触发 epoch 由 trusted 运行时注入；`scene_requirements` 按任务哈希绑定（`task_sha256` 校验） | 部分满足 | 必要条件按场景冻结有；"激活规则"字段化与按义务粒度缺失 | M | 2.6-08 |
| 3.1-11 | `aggregation_rule` 逐条指定（coalition_sum 等） | `_transport_has_option`（隐式按 handling mode slots 组合排列）+ `scene_requirements.capability_alternatives`（析取） | 部分满足 | 组合搬运按槽位隐式聚合；无四值枚举与容差字段 | M | 2.6-08 |
| 3.1-12 | 3.1 主实验冻结通信/时序/底层技能为理想化条件 | `hidden_event_schedule`：team-continuity 试验强制 immediate 交付（`goal_driven_contract.py:1237-1239`）；`factor_matrix.py` stable 层 | 部分满足 | 通信冻结有；"统一理想化时序执行器+预验证确定性底层技能"未作为主实验条件声明 | M | 3.3 并发 |
| 3.1-13 | 失败标签 `capability_constraint_violation/role_constraint_violation/required_milestone_unmet/no_committed_plan` | **无**（现有 `reason_code` 是部署必要条件诊断词表） | 缺失 | 四标签可直接采纳为 adapter 输出词表 | S | 3.1-07 |
| 3.1-14 | 评测器判定边界回归测试（角色过能力欠等） | `source/EAI/test/test_benchmark_goal_deployment_risk_integration.py`（部署侧）+ `test_benchmark_goal_deployment_assessment.py` | 部分满足 | 有部署风险集成测试；约束向量边界用例随 3.1-07 新增 | M | 3.1-07 |

### 1.4 协议 §3.2（通信适应）

| # | 协议条目 | 我们的对应物 | 状态 | 差距一句话 | 量 | 依赖 |
|---|---|---|---|---|---|---|
| 3.2-01 | perfect/zero 通信依赖两端+D_comm | 无 zero 单元；perfect 由 `capacity_bps=None`（unlimited，`_validate_pool_policy` 尾注）代理；D_comm 无实现 | 缺失 | 新增 zero cell（全 drop）+ `communication_analysis` 新 D_comm 函数即可 | S–M | 3.2-08（任务指标 M） |
| 3.2-02 | 固定延迟阶梯 d∈{0,1,2,4,8} | `communication_capacity_experiments.run_delay_cell(condition="fixed_delay")`（legacy v1，JP 对里仅 mean 1.0/1.6 两档）；池参数 `fixed_delay_s`（`communication.py::_POOL_POLICY_FIELDS`） | 部分满足 | 档位不全、指标是合成投递率非任务 M、无 x90/x50 阈值 | M | 3.2-08 |
| 3.2-03 | IID 丢包阶梯 p∈{0…1} | `run_loss_cell(condition="iid_loss")`；`_iid_drop_indices` 文档自认"seeded scattered fixed-count，**not Bernoulli IID**" | 部分满足+词汇警示 | 条件名 `iid_loss` 与真实分布不符（代码已声明）；真 Bernoulli 需接 channel RNG | S（真 IID） | 键控 RNG |
| 3.2-04 | 系统级链路容量 C/C0 六档 | `run_capacity_cell/capacity_observations`（v1）+ `communication_capacity_v2.run_capacity_cell_v2/capacity_observations_v2`（bit/s、`c0_bits_per_s` 全实验冻结校验、真 unlimited 参考）+ 池字段 `c0_bps/baseline_capacity_bps` | 部分满足 | 见第 3 节专述：三池 vs 系统级单 C 的映射 + 指标 M 尚为合成诊断 | M | formal hookup |
| 3.2-05 | C90/C50 括号（不外推） | `communication_analysis.summarize_capacity_curve(.summarize_capacity_curve_v2)`：thresholds ∈ {bracketed, above_tested_maximum, at_or_below_tested_minimum, reference_below_minimum}，`no_extrapolation:True` | **已满足**（离线诊断层） | 指标 M 是 delivery_rate，正式接入任务表现后即对齐 | M | 3.2-08 |
| 3.2-06 | 抖动对照 JP（CV_D=0.5） | `jitter_paired_observations`（`_jitter_delays` 确定性交替 mean±amp，代码自认"非随机抖动分布"）+ `summarize_paired_distribution_effect` | 部分满足 | 配对结构与 JP 公式同构；分布为确定性交替非 CV 参数化 | S（参数化） | 3.2-08 |
| 3.2-07 | 突发对照 BP | `loss_paired_observations`（`_burst_drop_indices` 连续块）+ 同上 summary | 部分满足 | 同均值同率配对成立；L_b∈{4,8} 档位未按协议设 | S | 3.2-08 |
| 3.2-08 | 主指标 M∈{SR_comm,MCR_comm,AUP_comm,单步效率} | **无**（全部实验 metric=delivery_rate/max_inter_delivery_gap_s；`COMMUNICATION_FORMAL_BLOCKER="communication_formal_episode_pending"` 已列入报告 blockers） | 缺失 | 五实验族的"因变量"需换成任务结果，依赖 Q(t)/成功判定接入正式 episode | M–L | §2.6-22 |
| 3.2-09 | x90/x50（延迟/丢包临界点） | 无（阈值括号仅容量维度实现） | 缺失 | `summarize_capacity_curve` 的 x 轴泛化即可复用 | S | 3.2-02/03 档位补齐 |
| 3.2-10 | D_total=D0+L/C+D_queue | `CommunicationRuntime`：`fixed_delay_s` + 容量序列化（Fraction 精确）+ `buffer_bytes`/`ttl_s` 溢出/TTL 丢包 | **已满足**（结构层） | 队列/溢出诊断量（P95 等）汇总在 ledger，未做报告聚合 | S | 无 |
| 3.2-11 | AoI（时间均值/P95） | `CommunicationRuntime.aoi()`、`_aoi_tracks`（按 (recipient,information_id) 分段）、`aoi_sample_policy∈{delivery,consume}` | **已满足** | 语义与协议 AoI_m(t)=t−u_m(t) 一致 | — | 无 |
| 3.2-12 | L_offer P95（自负载解释量） | 无（`bytes_served` 为链路侧；offered 与 receiver-weighted 拆分未分账） | 缺失 | 需在 publish 处记 offered/published 字节与 `d_m^admit` 接收数 | M | 2.7-05 |
| 3.2-13 | 通信随机数键 (task_version,i,s,src,dst,event_key) | 实验行 `channel_seed` 字符串 + `CommunicationRuntime` 全确定性；`KeyedRandom` 已在分支侧使用 | 部分满足 | channel_seed 未升格为结构化六元组键 | S | 2.6-26 |
| 3.2-14 | I-comm 探针（注入已存在信息+sham 对照） | 无（`goal_native_probe_runner` 是物理/附件探针，非机制探针） | 缺失 | 随 §7 探针家族 | L | §7 |
| 3.2-15 | 拓扑分裂/恢复为扩展协议 | 未实现亦未声明（协议同样列为扩展） | 已满足（范围一致） | 双方均不进首版主矩阵 | — | 无 |

### 1.5 协议 §3.3（时序协同）

| # | 协议条目 | 我们的对应物 | 状态 | 差距一句话 | 量 | 依赖 |
|---|---|---|---|---|---|---|
| 3.3-01 | PrecE2E/PrecOpportunity/PrecGivenOpportunity | **无**（`SCORING_DEFINITION_MATRIX` §3.2 建议新模块） | 缺失 | 前驱事件对+窗口判定器不存在；计划 prerequisites 是系统自声明（协议恰好要求作为事件验证而非输入约束） | M–L | native 时钟 |
| 3.3-02 | SyncE2E/Opportunity/GivenOpportunity、δ_g、W_g | 无（技能 `synchronize` 存在：`skill_ontology`，`agent_ids` 必须匹配 assigned） | 缺失 | 同步技能有、同步组指标无 | M–L | 3.3-01 |
| 3.3-03 | 交接间隙 G_h、连续率、U_s 未覆盖比例 | 无 | 缺失 | 依赖 2.7-06 handoff 事件 | M | 2.7-06 |
| 3.3-04 | t_ready 五分量与 W_unnecessary | 无 | 缺失 | 机会构造需合同外部状态+计划事件联判 | M–L | 2.6-22 |
| 3.3-05 | MutexAny/MutexOverlap | 准入拒绝：`GoalSession._admit_dispatch` 的 pending runtime resource conflict（Counter 超容量即拒）；组件级：`goal_concurrency_evidence.EVIDENCE_CATEGORIES` 含 `resource_conflicts`（fake clock） | 部分满足 | 是"拒绝"不是"违规率"；占用区间重叠比例（MutexOverlap 分母）无 | M | 3.3-07 并发 |
| 3.3-06 | 协调停滞协议（Δt_obs/δ_stall/δ_clear/ε_Q/T_a^max/K_stall、候选区间、恢复谓词、删失） | **无**（`SCORING_DEFINITION_MATRIX` §3.2 建议 `coordination_stall.py`） | 缺失 | 全套状态机缺失；是本轴最大单项 | L | 2.6-22、TC1–TC5 拍板 |
| 3.3-07 | `action_timeout` 与 A_eligible 集合 | 相邻：`complete_skill` 校验成功必须落在 admitted 窗口内（`dispatched_at_s ≤ physical ≤ end`），超窗拒绝；无超时事件 | 部分满足 | 超窗是账本错误不是 `action_timeout` 事件 | S–M | 3.3-06 |
| 3.3-08 | wait-for graph 死锁证书、LogicalDeadlockRate | 无 | 缺失 | 仅在可构造证书任务族报告（TC8 待拍板） | L | 3.3-06 |
| 3.3-09 | CR_couple（耦合/解耦同快照配对） | 无（checkpoint `planned_not_captured`） | 缺失 | 依赖分支执行 | L | 2.6-20 |
| 3.3-10 | I-timing 探针 | 无 | 缺失 | 随 §7 | L | §7 |
| 3.3-11 | 并发执行证据（同钟重叠实际执行+完成事件） | `goal_concurrency_evidence.ConcurrentFakeBackend`（in_flight 模式）+ `goal_episode_runner.py::execution_mode="async"`（要求 `supports_async_execution=True`+`advance_idle`，归档 `concurrency_evidence.json`） | 部分满足 | fake 后端已证明执行层；native/物理并发未接（`_INTEGRATION_GAP` 三条自述） | M–L | Isaac 侧 |
| 3.3-12 | not_reached_system 留在端到端分母 / not_evaluable_external 剔除披露 | 无对应词汇 | 缺失 | 随 3.3-01/02 指标一并定义 | S | 3.3-01 |

### 1.6 协议 §3.4（动态重构）

| # | 协议条目 | 我们的对应物 | 状态 | 差距一句话 | 量 | 依赖 |
|---|---|---|---|---|---|---|
| 3.4-01 | 突变后团队状态 T_{i,e}（独立 oracle 重标注） | `goal_restaurant_reassessment.RestaurantReassessmentOracle`（初始正证书+extension 门控；fail-closed null；负标签需 alternative-path 完备证书）；注入侧 `goal_restaurant_reassessment_injection.ScriptedReassessmentInjector`（`SUPPORTED_EVENT_TYPES=(member_exit,capability_change)`）；采集侧 `goal_restaurant_reassessment_collection`（回放 epoch 输入+独立复核 digest） | 部分满足 | 仅餐厅场景+两类触发（`milestone_state_change` 列为 unsupported）；标签词同 2.6-14 冲突 | M（扩场景） | 2.6-14 |
| 3.4-02 | postchange 三率+安全停止+声明延迟 | `deployment_assessment_observations` 的 `conditional_rates_by_phase["runtime"]`（initial/runtime 分相已实现且校验相加一致） | 部分满足 | 语义对应"突变后"；词汇与跨 episode 聚合同 3.1-02~05 | S | 3.1-02~05 |
| 3.4-03 | H_post=min(H,t_change+τ) 事件级观察窗 | 无（DR1 未冻结；归档只有全 episode 时钟） | 缺失 | 加 τ 字段+窗口化统计 | S–M | DR1 拍板 |
| 3.4-04 | 同快照 fact/reference 分支+Δ_branch | `goal_checkpoint_branch.build_branch_plan/validate_branch_plan`（pre-change checkpoint 强制）、`build_branch_replay_report`、`merge_sham_into_comparison`；数据面 `branch_data.BranchDataBatch`（register_branch/record_step/record_progress/record_network/record_cost/record_checkpoint/finish_branch、`validate_run`） | 部分满足 | 合同与数据管道完整；保存/恢复不执行（`SHAM_ISOLATION_BLOCKER`），Goal 主线归档 `branch_id=null` | L | 2.6-20 |
| 3.4-05 | 需求扩张（新增里程碑工作集/IncrementalMilestoneCompletion/增量吞吐/积压清零 h_B） | **无**（hidden_event_schedule 无新增里程碑 treatment；`deployment_change` 事件+`change_version` 仅版本账本） | 缺失 | 需合同支持运行时新增 outcome+权重 | L | 2.6-05 |
| 3.4-06 | 团队扩张（member_join 注入/Δ_join/L_need→use/三类激活模式） | 注入面有：`_validate_hidden_event_schedule` 的 member_join treatment（`fixed_hidden_identity`、池外身份、capability 声明）；`GoalSession.emit("member_join")` 更新 roster/capability version | 部分满足 | 注入与账本有；Δ_join、need 触发条件、需求前征用标记、三类模式预注册均无 | M–L | 3.4-04、2.6-22 |
| 3.4-07 | SpuriousReconfigRate（无需求窗口负对照） | 无 | 缺失 | 需窗口校验器+计划变化判定（协议 DR5 已给字段语义） | M | 3.4-06 |
| 3.4-08 | 失效恢复（RemainingMilestoneCompletion/Q_aff/healthy twin/R_aff） | 触发面有：member_exit/capability_change 注入+epoch 重评估+`safety_stop` 取消挂起任务（`_invalidate_deployment_assessment`）；healthy twin 无 | 缺失（触发有、指标无） | Q_aff 依赖 Q(t)；healthy twin 依赖分支恢复 | L | 2.6-22、3.4-04 |
| 3.4-09 | 外部事件链 t_trigger→t_valid_response→[t_confirm]→t_resume | 无（`SCORING_DEFINITION_MATRIX` §4.2 建议 `restructuring_event_chain.py`） | 缺失 | valid_response 的外部有效性判定器不存在 | M–L | 2.6-22 |
| 3.4-10 | P_e(H)+右删失分布、风险集不事后流失 | 部分相邻：`unanswered_*` 计数保留缺答于分母（部署层）；恢复事件无 | 部分满足 | 删失纪律在部署层已内建；恢复链无 | M | 3.4-09 |
| 3.4-11 | 动态重构探针 I-role/I-timing/I-role+I-timing/I-skill | 无 | 缺失 | 随 §7 | L | §7 |
| 3.4-12 | 事件有效性由注入日志判定（非系统感知） | 已满足原则：`ScriptedReassessmentInjector` epoch 记录基于 episode 自身归档回放（collection 模块"never from a participant declaration"） | 已满足（原则层） | — | — | 无 |
| 3.4-13 | 需求扩张与团队扩张不同时发生 | `hidden_event_schedule` 单一 team_event+通信冻结互斥校验（每 level 恰一类事件） | 部分满足 | 现有因子结构天然单因素；扩张类变体未实现 | M | 3.4-05/06 |
| 3.4-14 | L_detect/L_replan 内部阶段诊断（自愿） | 无专用字段（`revise_plan.reason` 可承载） | 缺失（可选） | 协议标注自愿，可后补 | S | 无 |

### 1.7 协议 §3.5（经验学习）

| # | 协议条目 | 我们的对应物 | 状态 | 差距一句话 | 量 | 依赖 |
|---|---|---|---|---|---|---|
| 3.5-01 | 共同首轮主干+逐轮 R/C 配对（环境逐轮回档） | 试点：`protocol_runner.run_pilot`（learning_rounds、`retention_control` 字段、`experience_support`）；Goal 轴无 | 缺失 | 需 participant_recoverable 状态域捕获/清空+多轮编排层 | L | 2.6-20 |
| 3.5-02 | 完全重复实例/受控微调序列分开报告 | 无（`LEARNING_DIFFICULTY_AUDIT.zh-CN.md` 为文档层审计） | 缺失 | 机会注册表未建 | M | 3.5-01 |
| 3.5-03 | LG_n^{Y/Q/AUP}、AULG 三轴不混单位 | 无（试点仅 `Q_by_round` 聚合） | 缺失 | 依赖 Q(t)/AUP | M | 2.6-22 |
| 3.5-04 | RMST 差（右删失生存曲线） | 无 | 缺失 | 新统计模块（`SCORING_DEFINITION_MATRIX` §5.2 建议 `learning_statistics.py`） | M | 3.5-01 |
| 3.5-05 | N_τ 达标轮数（右删失） | 无 | 缺失 | — | M | 3.5-03 |
| 3.5-06 | ΔRec_e（G/O/Z 三元机会判定） | 试点 stub：`protocol_runner` `DeltaRec{status:"no_gain_unsupported_experience"}` | 缺失 | O 须按任务状态判定的规则未落 | M | 3.5-01 |
| 3.5-07 | `retention_control_unverified` 标签 | 无（试点有近似字段名） | 缺失（词汇可直接采纳） | 协议词汇照搬即可 | S | 3.5-01 |
| 3.5-08 | Med_j 机制定位差分（探针×学习） | 无 | 缺失 | 依赖 §7 探针 | L | §7 |
| 3.5-09 | 打乱历史/访问受限诊断条件 | 无 | 缺失 | 扩展条件，可后置 | M | 3.5-01 |

### 1.8 协议 §3.6（成本效率）

| # | 协议条目 | 我们的对应物 | 状态 | 差距一句话 | 量 | 依赖 |
|---|---|---|---|---|---|---|
| 3.6-01 | 三类终止（effective_outcome/pre_execution_failure/ineffective_execution）+E_cost+X_exec | **无**（runner `result.json` 仅部署终止信息；`SCORING_DEFINITION_MATRIX` §6.2 已登记） | 缺失 | 判定顺序纯离线可加（E_cost 依赖 oracle 标签+Y/Q(H)） | M | 2.6-22、2.7-09 |
| 3.6-02 | t_term 两类终止+T_exec | 无（`budgets.simulation_s` 是 H；无"声明结束"动作） | 缺失 | 需 2.7-09 episode_terminated + CE4 口径 | S–M | 2.7-09 |
| 3.6-03 | Q_final 与 Q(H) 并存 | 无（依赖 Q(t)） | 缺失 | 同底座 | M | 2.6-22 |
| 3.6-04 | C_fixed（外部确认启动计费） | `goal_episode_runner` 写 `team_usage.json` → `goal_cost_evidence._activation_proxy`（`is_proxy=True`，源 `candidate_pool_design.activation_cost`） | 部分满足 | proxy 非正式单价表（CE1）；"仅入池不计费"语义需显式化 | S–M | CE1 拍板 |
| 3.6-05 | N_msg/B_offer/B_link（接收者加权、广播按接收数） | `goal_cost_evidence._communication_bytes`：只采信 replay 校验账本的 `bytes_served`（总/池/方向，`scheduler_charged_bytes` 语义、共享传输去重） | 部分满足 | ≈B_link 单侧；B_offer（提交 payload）与 `d_m^admit` 拆分无 | M | 3.2-12 |
| 3.6-06 | 交互/指令/重分配/重规划原始计数+单位时间速率 | `build_raw_cost_evidence.accepted_actions.counts_by_type`（含 revise_plan=重规划计数） | 部分满足 | 原始计数有；λ=T_exec 归一无（依赖 T_exec） | S | 3.6-02 |
| 3.6-07 | PGI/nPGI（执行窗积分） | 无 | 缺失 | Q(t) 就绪后是纯离线计算 | M | 2.6-22、3.6-02 |
| 3.6-08 | 无变化重规划（规范化哈希判定） | `semantic_goal_plan_signature`（忽略自选 ID 的图着色哈希） | **已满足**（检测器） | 哈希器已有；未接入 N_replan^nochange 统计输出 | S | 3.6-06 |
| 3.6-09 | 无收益重规划（hold-plan 同快照参考分支） | 无 | 缺失 | 依赖 replan_pre checkpoint+分支 | L | 3.4-04 |
| 3.6-10 | 非劣门槛 δ_p/δ_Q+Pareto 向量 | 无 | 缺失 | 纯统计层，素材齐后 S 量 | S–M | 3.6-01/03 |
| 3.6-11 | 无效/撤销指令比例（分母 0→N/A） | 部分：`action_rejections`（仅 plan 类）+ `action_result` 拒绝原因 | 部分满足 | 指令级（dispatch/cancel）拒绝与撤销分类无 | S | 无 |
| 3.6-12 | 探针/sham 成本单列不进事实分支 | 无（无探针成本发生） | 缺失（随 §7） | — | M | §7 |
| 3.6-13 | 不可行层不计算 Q/PGI，报声明延迟+声明前成本 | `goal_declaration_cost_snapshot.build_declaration_cost_snapshots`（声明前动作计数/通信切分/规划成本）+ `SCORING_DEFINITION_MATRIX` §6.4 口径已写 | 部分满足 | 快照生产器有、未与 not_deployable 层正式 join | S | 3.6-01 |

### 1.9 协议 §4（统一量化框架）

| # | 协议条目 | 我们的对应物 | 状态 | 差距一句话 | 量 | 依赖 |
|---|---|---|---|---|---|---|
| 4-01 | 六维统一框架+每维风险集/N/A/不确定性要求 | `goal_scoring_adapter.DIMENSION_STATUS`（7 条目=六能力+动态重构拆二）+ 各 observations 的 `quality_failures`/`raw_only` | 部分满足 | 框架登记有；不确定性（CI）仅通信轴 bootstrap 有 | M | 各轴 |
| 4-02 | 时间三角色不混用 | `clock_source="goal_host_receipt_clock"`+`physical_clock_bound=False` 全局自declared | 部分满足 | 时钟真实性门禁未过，比率分母冻结规则未落 | M | native 时钟 |
| 4-03 | 可行/不可行/无效/feasibility_unverified 四分类 | 3.1/2.6 对应行（`episode_oracle_eligible`、`oracle_infrastructure_failure_count`） | 部分满足 | invalid_instance 类缺失 | S | 2.6-25 |
| 4-04 | 0–100 分接口原则（保留原始向量） | `SCOREABILITY_REASONS`+全 sidecar `scoreable:False` | 已满足（保守侧） | — | — | G7 拍板后解除 |
| 4-05 | Wilson CI、配对 bootstrap/置换 | `protocol_runner.wilson_ci`、`communication_analysis._bootstrap`（分层：先 base 后 seed） | 部分满足 | Wilson/配对 bootstrap 在试点与通信轴；Goal 主线无 | S–M | 推广 |

### 1.10 协议 §7（失败诊断：fact/sham/干预分支）

| # | 协议条目 | 我们的对应物 | 状态 | 差距一句话 | 量 | 依赖 |
|---|---|---|---|---|---|---|
| 7-01 | 仅 deployable 未成功 episode 运行归因 | 无（无归因编排） | 缺失 | 前置：Y 判定+失败筛选 | M | 2.6-12 |
| 7-02 | fact/sham/intervention 三类分支自同一 t_k 快照独立启动 | 词汇与骨架：`goal_checkpoint.BRANCH_IDS`、`goal_checkpoint_branch.BRANCH_KINDS=("treatment","control","sham_control")`、`branch_data` 全套 record API | 部分满足 | "恢复同快照再执行"未实现（native restore 待验证） | L | 2.6-20 |
| 7-03 | V_i(S) 技术有效性（状态哈希+噪声带+版本一致） | `goal_checkpoint` `state_payload_sha256`（逐域哈希）+ `branch_rng_digest`（流摘要相等性） | 部分满足 | 等价物在合同层；`_validate_certificate` 级的比对未执行 | M | 7-02 |
| 7-04 | R_replay（sham 复现失败：Y 相同且 ΔQ≤δ） | `build_branch_replay_report`/`merge_sham_into_comparison`（sham 严格资格=等 seed+等流摘要）；试点 `protocol_runner.RESTORE_TOLERANCE=7.63e-6` | 部分满足 | 现判结构等价不判结果容差（Y/Q 差容差 δ_replay 无字段）；`replay_mismatch` 词汇可直接采纳 | M | 7-02、2.6-22 |
| 7-05 | 探针权限集 P={role,timing,comm,skill} 与禁令表 | 无 | 缺失 | 四探针实现+权限声明均无 | L | 7-02 |
| 7-06 | Z_i(S)/ΔQ_i(S)/RR/RD/Coverage | 无 | 缺失 | 纯统计层，分支可跑后 S 量 | S–M | 7-05 |
| 7-07 | 最小挽救集合 branch-and-bound（含 minimality_unresolved） | 无 | 缺失 | 2^4 枚举编排 | M | 7-06 |
| 7-08 | 已知故障校准（Precision/Recall+混淆矩阵） | 无 | 缺失 | 需可开关故障注入 | L | 7-07 |
| 7-09 | I-comm placebo 分支 | 无 | 缺失 | — | M | 7-05 |
| 7-10 | 探针结果与正式结果隔离（附表分层） | 无探针故无需隔离；sidecar 均附 `not_protocol_score` | 已满足（原则层） | — | — | 无 |

---

## 2. Q(t)/MCR/AUP/PGI 计算现状专节

### 2.1 最接近的现有实现

1. **`goal_progress_evidence.py::GoalProgressRecorder`**（正式 Goal 轴）：按 6 类边界
   （`BOUNDARIES = {initial, skill_completed, idle, execution_stopped, business_evaluated, terminal}`）
   采样每个 `required_outcomes` 谓词的三态值（`satisfied∈{true,false,unknown}`+`evidence_refs`），
   记录 `changed_predicate_ids`，`horizon_s` 取自 `budgets.simulation_s`。
   报告显式自declared：`"weighted_progress": None`、
   `"missing_inputs": ["frozen_milestone_weights", "continuous_transition_coverage",
   "physical_source_authentication"]`。`replay_progress_evidence` 可离线重算并核对
   归档哈希——这是离线计算的可靠入口。
2. **`protocol_runner.py`（试点，合成数据）**：`build_contract` 生成
   `progress_milestones`（权重 1.0/2.0，按节点参与人数），`_outcome` 用确定性哈希
   造进度并计算 `q_t`（加权达成比）；带 `wilson_ci`、`paired_difference`、六维
   scorecard、学习轮 `Q_by_round`。**不接 Goal 轨道、不接 Isaac，是协议口径的
   pilot 骨架**。
3. **`goal_restaurant_business_facts.py:152`**：同样 `"weighted_progress": None`、
   `missing_inputs` 含 `frozen_milestone_weights`——native 事实重算侧同样自declared 缺权重。

### 2.2 差距清单（按协议口径）

| 协议量 | 差距 | 现有素材 |
| --- | --- | --- |
| Q(t) | 无冻结权重 w_m（三处源码均 None）；无激活工作集版本 v；unknown 三态未归约为二值 y_m；无稳定时长 | 边界样本+changed_predicate_ids+horizon_s |
| MCR | 同上（=Q(H) 的加权形式） | terminal 边界样本 |
| AUP | 无积分实现；边界采样密度 unknown（`sampling_scope="runner_boundaries_not_continuous_physical_transitions"`） | 样本时刻+协议允许"保持最后有效观测到 H"的阶梯延拓 |
| Q_final / t_term | 无声明结束接口、无终止类别 | `termination` 块仅部署类 |
| PGI/nPGI | 无；且依赖 Q(t) 与 T_exec 同钟 | `clock_source="goal_host_receipt_clock"` 仅诊断 |

### 2.3 最小落地路径（离线优先，不启动 Isaac）

1. **新增纯函数模块**（建议 `goal_progress_curve.py`，或扩展
   `goal_progress_evidence.py` 增加 `compute_progress_metrics(report, weights)`）：
   - 输入：已归档的 progress evidence（或 `goal_session_report.json` 的
     events/actions/business_outcomes + skill_receipts 时间轴）+ 冻结权重表；
   - 步进 Q(t)：样本间保持最后观测（协议 §2.6 自己规定的延拓规则，阶梯积分即
     AUP 的合法离散化）；unknown 一律按 y=0 且带 `na_coverage`；
   - 输出：Q(H)、MCR、AUP、（给定 t_term 后）PGI/nPGI，全部附
     `weights_source`、`sample_count`、`unknown_coverage`。
2. **权重来源分层**：正式口径必须读合同新字段（缺失→按协议 Σw=0 记 N/A，不填
   默认值）；另设显式 `weight_mode="equal"` 的 development 诊断开关（等权=1 是
   一种合法冻结选择，但必须由用户拍板后写入合同，实现者不得隐式默认——与
   `SCORING_DEFINITION_MATRIX` §9"拍板前默认行为"一致）。
3. **验证方式**：对既有 episode 归档离线重放（`replay_progress_evidence` 已保证
   归档一致性），用 `protocol_runner` 的合成 q_t 做交叉校验（同公式两实现）。
4. **同钟升级**：AUP/PGI 的正式分母等 `physical_clock_bound=True`（native 时钟
   门禁），诊断值可先行。

结论：**Q(H)/MCR/AUP 可以在现有归档上离线算出（权重拍板后 S–M 量）；PGI/nPGI
额外依赖 t_term 声明接口（2.7-09）**。

---

## 3. 五实验族通信映射专节（三池 ↔ 系统级 C）

### 3.1 现有池模型

`communication.py::_POOL_ALIASES`+`_POOL_POLICY_FIELDS`：`backhaul_uplink`、
`backhaul_downlink`（别名）、`p2p`，另有 per-robot `access` 上限；每池
`capacity_bps/fixed_delay_s/drop/active_drop/ttl_s/buffer_bytes/bytes_per_window/
window_s/c0_bps`。v2（`communication_capacity_v2.py`）已固定
`CAPACITY_UNIT="bits_per_second"`、`c0_bits_per_s` 全实验单一值校验、真
unlimited 参考（`capacity_mode∈{finite,unlimited}`）。

### 3.2 协议"系统级单链路容量 C"的精确映射建议（不重写运行时）

1. **指定承载池**：把 `backhaul_uplink` 定义为协议 C 轴的**唯一主承载池**——
   所有方法共发的共享流量（observation/指令下行）已经过该池；`p2p` 与
   `backhaul_downlink` 作为**扩展协议维度**单独报告（协议本身允许扩展 Suite，
   §3.2 末段拓扑分裂同例）。这样"方法无关的绝对容量档位"= v2 行的
   `capacity_bits_per_s`，无需合并多池。
2. **C0 锚点**：直接采用池字段 `c0_bps`（v2 `c0_bits_per_s`，已强制全实验冻结
   单值，违反即 fail-closed）作为任务族预注册锚点；成绩单双横轴（绝对 bit/s +
   C/C0）由 `summarize_capacity_curve_v2` 的 `capacity_ratio_c0` 已直接给出。
3. **C/C0 档位**：协议六档 {∞,1,0.75,0.5,0.25,0.1} → 在 v2 单元生成时按
   `capacity_bits_per_s = ratio × c0_bits_per_s` 展开即可（现在
   `capacity_observations` 的档位是绝对值列表，加一个 ratio 生成器 S 量）。
4. **指标 M 替换**：当前 `metric="delivery_rate"`（`measurement_scope=
   "synthetic_link_delivery_diagnostic"`，自declared 非任务表现）。协议要求
   M∈{SR_comm,MCR_comm,AUP_comm,η_step}——保持现有 delivery 曲线作伴随网络状态，
   正式曲线待 `COMMUNICATION_FORMAL_BLOCKER="communication_formal_episode_pending"`
   解除（正式 episode 的任务结果流经共享传输账本）。
5. **perfect/zero**：perfect=unlimited 档已有；zero 新增 cell（全 `drop` 或
   capacity=0），D_comm 建议落 `communication_analysis` 新函数
   （`(M_perfect−M_zero)/M_perfect`，仅 `M_perfect≥m_min^comm` 时计算——门槛
   机制 `min_reference_mean` 已存在）。
6. **延迟/丢包档位与真 IID**：`fixed_delay_s` 已是池参数，补协议阶梯
   {0,1,2,4,8} 决策步（换算秒）；`iid_loss` 改造为键控 Bernoulli
   （`EAI.keyed_random.KeyedRandom` 已在分支侧使用，channel_seed 升格为
   (task_version,i,s,src,dst,event_key) 结构键），或保留现确定性散布但按代码
   自declared 更名 `scattered_loss` 以消除词汇误导。
7. **x90/x50 泛化**：`summarize_capacity_curve` 的自变量从 capacity 泛化为
   严重度 x（阈值括号逻辑/无外推/reference 有效性全部复用）。

### 3.3 与协议的残余差距（映射后仍缺）

- 任务表现 M（3.2-08，依赖 Q(t)/成功判定接入正式 episode）；
- `L_offer P95` 与 B_offer/B_link 分账（3.2-12、3.6-05）；
- I-comm 探针与 placebo（§7）。

---

## 4. 44 未冻结参数消化表

来源：`SCORING_DEFINITION_MATRIX.zh-CN.md` §9（G1–G7/TA1–TA4/CA1–CA9/TC1–TC8/
DR1–DR6/XL1–XL5/CE1–CE5，共 44）。协议"(1)"版逐条核对结果分三类：

### 4.1 协议直接回答（给出机器名/取值/公式，可直接采纳，共 14 项）

| 编号 | 协议节 | 协议回答 |
| --- | --- | --- |
| TA1 | §3.1 | 五指标机器名与公式已给定：`team_state_infeasibility_detection_rate`/`false_non_deployable_rate`/`false_deployable_rate`/`pre_dispatch_safe_stop_rate`/`assessment_latency`（分子分母含 A_i/H_i/U_i/X_i 定义） |
| TA2 | §3.1 | 约束检查器只做三类有限判定；`committed_plan` 必须暴露的字段集固定（plan_version/subtask_id/milestone_id/robot_ids/capability_type/capability_value/aggregation_rule/role_id/计划哈希） |
| CA3 | §3.2 | 保持率阈值固定 q∈{0.9,0.5}（代码默认 `(.9,.5)` 与协议一致） |
| G4 | §2.6 | Q(t) 为权重里程碑加权和，**无重试扣分项**；引入扣分属协议修订 |
| G7 | §4.4 | 当前阶段不合成 0–100；若引入须遵守九条原则 |
| G1 | §2.6 | 重复属实例内 K（ȳ_ic=K 内均值），base instance 为统计单位；×10 不得当独立 base instance |
| G2 | §2.6 | 聚合公式已给定（先实例内、后跨实例；配对差 d_i） |
| XL1（下界） | §3.5 | E≥3（含共同主干一轮+两轮观测），正式值 pilot 定 |
| XL5 | §3.5 | 清空等价性要求已给定：删除**全部**评测期可写状态（外部记忆/缓存/案例库/反思日志），仅清单一文件不合格→`retention_control_unverified` |
| TC8 | §3.3 | 严格死锁率仅在可构造 wait-for 证书的任务族报告；ΣJ=0 时 N/A |
| DR5 | §3.4.1 | 虚假重构判定字段已给定：窗口内实际激活新资源，或输出改变承担者/顺序/同步/资源预约的重分配；仅内部置信度更新不计 |
| DR4（语义） | §3.4 | M_aff 冻结规则语义已给定（新增+原方案受影响未完成+依赖失效服务，按环境真值在触发时刻冻结） |
| CE5/G5（部分） | §3.6 | 协议**无**重规划分层计费，只有无变化/无收益重规划诊断+“当前阶段不合成总分”；三层计费属协议外扩展 |
| 3.3 停滞参数语义 | §3.3 | TC1–TC5 五参数的含义表已给定（Δt_obs/δ_stall/δ_clear/ε_Q/T_a^max），K_stall 换算公式给定 |

### 4.2 协议给出口径但数值/词表仍需拍板（共 24 项）

| 编号 | 协议给的口径 | 仍需拍板 |
| --- | --- | --- |
| G3 / TC7 / DR1（H、τ 部分） | §2.6/§2.8 H 由合同冻结、任务族可异（H_i）；τ_e 预注册 | 各任务族具体数值 |
| G6 | §2.6 bootstrap/置换的层级 | 样本数与 seed 策略（代码现默认 2000） |
| CA1 | §3.2 C0 预冻结、方法无关、报绝对值+C/C0 | 每任务族锚点数值 |
| CA2 | §3.2 m_min^comm/m_min^step 须预注册 | 数值（pilot） |
| CA9 | §3.2 加点须预注册且只用于临界点 | 触发/次数/精度 |
| TC1–TC5 | §3.3 含义表 | 各任务族数值/动作超时表 |
| TC6 | §3.3 W_g 预声明、G_h^max 合同 | 数值 |
| DR1（τ 数值）/DR2/DR3 | §3.4 H_post、δ_Q^aff、h_R 定义 | 数值 |
| DR6 | §3.4.1 积压清零持续 h_B | 数值 |
| XL2 | §3.5 N_τ 预注册单项 | τ_k/h_k |
| XL3（部分） | §3.5 G/O/Z 语义 | 事件键机器词表 |
| XL4 | §2.6 预实验定样本量 | 数值 |
| CE1 | §3.6 c_j^start 冻结或保留向量 | 单价/向量选择 |
| CE2 | §3.6 q_min 合同冻结 | 数值 |
| CE3 | §3.6 δ_p/δ_Q 冻结 | 数值 |
| CE4 | §3.6 “标准接口声明结束”+两类终止 | Goal 侧对应 action 口径 |
| TA3（部分） | §3.1 四个约束失败标签 | 计划拒绝分类码不在协议词汇，需自定稳定码 |
| TA4 | §3.1 右删失+外部时钟要求 | 时钟源接线选择 |
| 2.6-04 权重 w_m | §2.6 随合同冻结 | 每任务族权重表（含是否等权） |

### 4.3 协议未回答、纯工程/产品决策（共 6 项）

CA4（端点身份命名）、CA5（companion v1 vs Goal v4 合同选择）、CA6（跨 edge
p2p 允许性）、CA7（AoI delivery vs consume——运行时已支持二选一但缺正式选择）、
CA8（trusted 物理时钟宿主组件）、TA3 的文本→稳定码映射实现细节。

> 拍板前默认行为维持 `SCORING_DEFINITION_MATRIX` §9：raw-only 或缺生产器标注，
> 任何实现提交不得引用未拍板参数的临时取值。

---

## 5. 建议落地顺序（离线可验证优先 / 合同 v2 扩展不破坏现有测试 / Isaac 最小化）

**批次 0：词汇对齐（S，纯离线，零 schema 变更）**
1. oracle 真值词映射：`feasibility_label` 输出侧引入 `team_state_infeasible`
   （建议：`goal_deployment_assessment.py` 标签集扩展 + `goal_scoring_adapter`
   读取层做新旧双读，旧归档不重写），同时把 §3.1 五指标机器名、四个约束失败
   标签、`replay_mismatch`/`retention_control_unverified`/`not_reached_system`
   /`not_evaluable_external` 写入 adapter 映射表（对应用 4.1 的协议直接回答）。

**批次 1：离线计算器（S–M，现有归档即可验证，不启动 Isaac）**
2. Q(t)/MCR/AUP 离线计算器（第 2.3 节路径；`replay_progress_evidence`+
   `protocol_runner` 交叉验证）。
3. 部署五率合成：`pre_dispatch_safe_stop_rate` 三条件 join + 跨 episode 聚合
   （素材已在 `deployment_assessment_observations` records 中）。
4. `summarize_capacity_curve` 的 x 轴泛化（x90/x50）+ 容量档 ratio 生成器 +
   D_comm 函数（perfect/zero cell）。
5. 无变化重规划统计输出（复用 `semantic_goal_plan_signature`，接
   `plan_admission_observations`）。

**批次 2：合同 schema 扩展 v3→v4（M，加法字段+新版本号，沿用
`_PUBLIC_TASK_FIELDS_V3` 先例；影响面按 1302 个 goal 测试函数中直接断言
字段集的子集评估，新增字段一律 optional-in-v4、fail-closed 校验新函数）**
6. `required_outcomes` 升级 `contract_milestones`（weight/stability_duration/
   activation_rule/monotonicity/visibility/anti_double_count_rule）。
7. `capability_constraints`/`role_constraints` 独立类（含 aggregation_rule 四值
   枚举）+ `milestone_validity_rules`、`nuisance`、`invalid_instance` 标签。
8. 事件词汇扩展：`episode_terminated`（终止类型/t_term）、`milestone_verified`
   （外显化）、`plan_version`；计划 task_fields 增 `milestone_id`。
9. CS 三维向量判定器（新 `goal_constraint_verifier`：逐义务
   active/satisfied/violation_reason/evidence + G_is 门控；纯离线可测）。

**批次 3：Isaac 原生需求最小集（M–L）**
10. native 时钟绑定（TA4/CA8 拍板后；解除 `physical_clock_bound=False`）→
    assessment_latency 物理化、时序轴前提、PGI 正式化。
11. 三类终止 E_cost/X_exec 判定进 runner 归档层。
12. checkpoint 七域捕获/恢复 + fact/reference 分支执行（解除
    `SHAM_ISOLATION_BLOCKER`）→ 同快照配对差、healthy twin、无收益重规划。

**批次 4：长线（L）**
13. 协调停滞状态机+wait-for 证书（TC1–TC8 拍板为前置）。
14. §7 探针家族 I-role/I-timing/I-comm/I-skill+placebo+最小挽救集合+故障校准。
15. 学习 R/C 多轮编排+RMST/AULG/ΔRec（依赖 participant_recoverable 域）。
16. 3.4 需求扩张/团队扩张全套（合同运行时新增里程碑+L_need→use+虚假重构）。

依赖主线：**Q(t) 底座（批次 1-2）→ 成本积分/停滞 ε_Q/Q_aff/AUP；checkpoint 恢复
（批次 3-12）→ 一切同快照配对结论；native 时钟（批次 3-10）→ 一切比率分母正式化**。

---

## 6. 本文档核对过的源码清单（证据索引）

`goal_driven_contract.py`（1431 行全文）、`goal_session.py`（942 行全文）、
`goal_deployment_assessment.py`（1–125）、`goal_deployment_feasibility.py`（304 行全文）、
`goal_scoring_adapter.py`（24–160、200–870、872–1005）、`goal_progress_evidence.py`
（166 行全文）、`goal_cost_evidence.py`（23–60、330–609）、
`goal_declaration_cost_snapshot.py`（结构扫描）、`goal_constraints.py`（1–40、508–548）、
`communication.py`（40–360）、`communication_capacity_experiments.py`（364 行全文）、
`communication_capacity_v2.py`（结构扫描）、`communication_analysis.py`（129–430）、
`goal_concurrency_evidence.py`（1–98）、`goal_episode_runner.py`（async 相关行）、
`goal_checkpoint.py`（1–60）、`goal_checkpoint_branch.py`（1–45）、`branch_data.py`
（结构扫描）、`goal_restaurant_initial_oracle.py`（结构扫描）、
`goal_restaurant_reassessment.py`（1–53+关键行）、
`goal_restaurant_reassessment_injection.py`/`_collection.py`（结构扫描）、
`goal_remaining_feasibility_oracle.py`（结构扫描）、`protocol_runner.py`（结构+关键行）、
`SCORING_DEFINITION_MATRIX.zh-CN.md`（552 行全文）。
未读但被矩阵引用其存在的：`mechanism_templates.py`、`goal_alternative_path_certificate.py`、
`skill_ontology.py`、`factor_matrix.py`、`candidate_pool_design.py`（标注 unknown 细节）。
