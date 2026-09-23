# P1-L 经验学习难度与机会准入审计

日期：2026-09-23
输入版本：codex/benchmark-pose-telemetry，HEAD 23904c7。评分协议根目录的
异构多机器人高层协作智能+Benchmark+能力分类与评测协议.md 是本审计的分析输入，
未跟踪、未提交。本文只审计当前题族是否具备产生经验学习证据的条件，不修改题包、
见证计划、生成器或 scoreable 标志。

## 阶段结论

经验学习正式出分必须等到 P1-L 难度准入通过。当前阶段只完成 L0-L3：登记任务族、
定义证据边界、冻结 eligible / ceiling / unknown 判据草案。还没有正式 learning
opportunity 集合、R/C 分支、可恢复 checkpoint、sham 或真实业务证据，因此不能报告
学习增益，也不能把一次见证成功或一次执行失败当作学习机会。

结构上，Restaurant 是高难度候选；Factory、Warehouse、House、City 暂列
unknown。这里的 high 只表示任务结构具备较强的重复决策和协作信息价值，不表示
当前资产或原生执行已经合格。Restaurant 当前仍受 A2 资产源闭包阻断，所以正式状态仍是
unknown，不能进入学习分母。

三个发布标志继续保持：

    execution_validated=false
    business_outcomes_validated=false
    scoreable=false

## L0 五场景任务族登记

每个场景当前生成两份隐藏见证计划。它们是作者合法性证据，不能作为模型必须复现的
分解、人员或时序。学习统计的结构单位是冻结的 base_instance_id；同一任务族中仅
更换见证名称、机器人别名或等价标签的 M0/M1 结构签名只能登记一次。

| 场景 | task id | public hash | hidden hash | 候选池 | 见证任务数 | 关键终局 | 结构候选 | 当前正式状态 |
|---|---|---|---|---:|---:|---|---|---|
| Factory | FACTORY-GOAL-01 | 49c97523de62278b99e2e544e4ca3b911a96a8f4572c6892dcf6bebf81c30350 | dc232ef56604a5c0ed7c132e9b2c5325812351d69ace3c2a4a570c115fca33c9 | 4 | 9 / 10 | 异常确认、响应区域服务、维护交接 | unknown | unknown |
| Warehouse | WAREHOUSE-GOAL-01 | 25052247bf3176f4903b2fb4838226b9d67f5d069ad6ff93e29c97864ecdabf5 | 0817399a230f8f4995424be24b61ab68e3e3c7be73038b2c10d1e6f5d1b7b927 | 4 | 17 / 17 | 盘点、集货、dock 交接、关闭复核 | unknown | unknown |
| House | MODERN_HOUSE-GOAL-01 | 2c808a29dee58f61eba575987b4eefda4f839baec5e47585e004d3c971f28857 | 3d9c3c570cb727aa69fe7040e7c14ff6a00a7c8cda475340d9303fe78b8e96aa | 4 | 11 / 11 | 家具、物品暂存、住户通路、团队退出 | unknown | unknown |
| City | CITY-GOAL-01 | 10b1b9b40f5b3f1b7ca74be879cacff14c65085561eac5c7866de352237aecfb | 34f86e46d25383294eb351d9cf7b7dc871c5a104862a8b868c2227d6be680f30 | 3 | 12 / 13 | 事故评估、人员清点、交通保护、救援通路 | unknown | unknown |
| Restaurant | RESTAURANT-GOAL-01 | e34fe98668acece2234fda06140f867840f58c272e11216430151ce86eac9084 | aa61416a7a5476d81062d4d7a180cac602fbe13400119bf635917d51d864b69b | 8 | 7 / 6 | 厨房检查、用餐区检查、readiness 回执 | high 候选 | unknown（A2 阻断） |

当前每个场景的 witness_count=2，不是正式学习样本数。正式机会至少需要预注册的
独立 base_instance_id、共同首轮、后续轮次和 R/C 条件；同一见证的 A/B 变体不能
自动充当独立重复。

证据登记还区分 authoring_evidence、native_diagnostic、native_business_evidence、
participant_algorithm_evidence、documentation_only、unknown 和 infra_failure。当前
City 有可追溯的 TeamWeaver/TeamWeaver_LLM/EMOS participant trace，但业务 release
标志仍为 false；Factory、Warehouse、House 主要是 witness/诊断运行，室内失败混有
接近门槛、几何、控制器和基础设施因素；Restaurant 的算法 batch 只有文档声明，当前
checkout 缺少完整可重放 archive。因此这些记录可以帮助审计测量边界，不能直接形成
R/C 样本或 learning label。

结构签名按 required_outcomes + public_world + hidden_event_schedule 计算。M0/M1
机制模板不改变这三项输入，不能把机制变体重复计作学习机会；City native v1/v2/v3
即使结构签名相同，也必须保留 skill catalog 和 roster 条件，避免把新增能力当作重复
样本。通信因子等级会改变 hidden_event_schedule，因此是实验条件字段，不能被误并为
新的任务族，也不能把 factor pair 数当作独立学习样本数。模板覆盖数和 factor pair 数
是设计覆盖，不是独立学习样本数。

## 证据等级与当前缺口

| 证据层 | 可以证明 | 不能证明 |
|---|---|---|
| 结构登记 | 目标、公开世界、终局谓词、候选池和隐藏事件键存在且版本稳定 | 模型学会了经验、任务在当前资产上可执行 |
| 作者见证 | 至少一条作者声明的合法候选路径 | 被测模型应复现该分解、最优性或学习增益 |
| 轻量 runner / sidecar | 原始动作、事件、计划、拒绝、业务字段的完整归档 | 真实物理时间、恢复分支、R/C 差异或正式终局真值 |
| 原生 episode | 可信物理状态、时钟、通信和业务回执 | 如果没有 R/C 配对和机会注册，仍不能证明学习 |
| checkpoint + sham + pilot | 同快照可恢复、无处理分支一致、机会可重复 | 不能替代正式统计和预注册分母 |

当前侧车只能提供 raw evidence。goal_scoring_adapter.py 的
plan_admission_observations 可以聚合计划拒绝和修订存活；GoalSession.report()
保存动作、事件和符号运行态；这些字段应作为过程证据，不能在
business_outcomes_validated=false 时充当学习结果。

## L1 指标与有效性边界

正式学习单位为同一 base_instance_id 在轮次 n 的 R（保留经验）与 C（清除评测期
经验）配对。以下量分别报告，不事后选择一个最有利指标：

- Y：隐藏业务/原生证据验证的终局成功。
- Q(H)：固定评测时限 H 的外部里程碑进展。
- AUP = H^-1 ∫ Q(t)dt：保留未终局样本的进展过程。
- RMST(H)：含右删失的完成时间生存量；未完成样本记 Δ=0，不能把 H 当完成时间。
- 错误/陷阱复发：首轮已得到合法反馈 G=1，后续客观仍有再发机会 O=1，实际
  发生为 Z=1。机会由题面状态和外部事件决定，不能由系统是否绕行来决定分母。

无效高层指令、撤销、重复和超时可以作为过程量；低层控制、几何阻挡、抓取距离、
资产缺失、通信基础设施故障、校验器失败不能制造 learning opportunity。不可行团队
先由部署门禁二元 oracle 分层；oracle 失败属于平台质量缺口，不能算模型失败或第三种
学习答案。

## eligible / ceiling / unknown 判据

判定粒度为 base_instance × round × condition × metric，在正式结果前冻结。

### eligible

必须同时满足：

1. 任务族、结构签名、题包版本和事件键与机会注册表一致；等价 M0/M1 不重复计数。
2. 该指标有客观机会：任务可行性已由 verified oracle 证明；Y/Q/AUP/RMST 的
   终局、进展、时限和完成事件定义齐全；错误指标同时有 G=1 和后续 O=1。
3. R/C 从同一处理前 checkpoint 恢复，公开输入、外生事件、随机流、预算和工具权限
   一致，唯一系统性差异是评测期经验的保留/清除。
4. 环境物理、participant 状态、可见历史、通信队列、调度器、时钟和 RNG 恢复及 sham
   读回均 verified；经验清除覆盖缓存、案例库和反思日志。
5. 外部终局、Q(t)、时间和错误事件证据完整，且没有资产、平台、低层执行或校验器
   故障污染该指标。

### ceiling

机会和证据有效，但在正式前注册的阈值下没有可测上升空间，例如 C 条件已达到终局
上限，或预注册陷阱没有再发机会。ceiling 保留在覆盖率报告中，不能改写成学习成功，
也不能事后从分母删除。

### unknown

机会可能存在，但任一必要事实未验证：oracle 失败、结构/事件键不一致、R/C 无法配对、
checkpoint/sham 未通过、时钟或 Q(t) 缺失、错误机会证据不完整，或基础设施/资产/
低层执行污染结果。unknown 只进入平台缺口报告，不进入模型正确、错误或学习效果分母。

## 分母、覆盖率和不确定性

出题阶段冻结不可变的 learning_opportunity_registry，至少包含：
base_instance_id、结构哈希、场景和任务版本、轮次、指标、H、阈值、陷阱键、
反馈来源、oracle 证据和输入签名。

每个指标单独报告：

    N_registered_applicable = N_eligible + N_ceiling + N_unknown
    coverage = (N_eligible + N_ceiling) / N_registered_applicable
    unknown_ratio = N_unknown / N_registered_applicable

分母为零写 N/A，不填 epsilon。正式效应只使用 N_eligible；ceiling 作为已验证
但无学习空间的独立层；unknown 全部列为基础设施缺口。R/C 差异以实例为配对聚类
单位，建议使用实例级 cluster bootstrap 95% CI；RMST 保留删失，不能只在成功样本上
算平均时间。样本不足或参考条件未达到预注册门槛时写明 N/A 原因，禁止外推。

## P1-L 阶段门与后续顺序

### 现在允许做的事（L0-L3）

- 维护任务族登记、输入哈希、证据等级和未知项。
- 维护判据、指标公式、删失和基础设施失败分类。
- 为后续机会注册表设计字段和独立 pilot 验收表。
- 将计划拒绝率、资源冲突率、修订存活率作为分配调度的 raw 诊断，与部署 IRR/FIR
  并列归档；不创建固定子任务评分轨。

### L-G1 之前禁止的事

- 修改 goal_driven_tasks.py、mechanism_templates.py、题包生成器或冻结 witness。
- 按正式被测系统结果调整机会集合、阈值、H、轮数或 ceiling。
- 把当前执行失败、几何限制或资产阻断包装成 learning opportunity。
- 在 checkpoint/restore/sham 未通过前启动严格 R/C 分支或发布六维学习分数。

### L-G1 之后的依赖顺序

1. 先完成 P0：五场景可信终局谓词、原生读回和剩余任务二元 oracle；部署门禁的
   oracle_status=failed 不进入学习分母。
2. 冻结机会注册表和 pilot 版本，至少预注册共同首轮、E≥3、H、事件键和阈值。
3. 实现真实 checkpoint/restore、无处理 sham、编键 RNG 配对、R/C 经验清空与保留。
4. 接入真实仿真时钟、通信账本和 Q(t)/完成事件，再验证五场景的机会覆盖率。
5. 只有正式机会全部达到预注册准入并披露 unknown 比例后，才可以计算学习效应。

## 当前审计判断

Restaurant 的两个检查区域和统一 readiness 回执提供了重复观察、通信交接和串行/并行
选择的结构，适合作为首个难度 pilot；但 A2 资产准备仍失败，故不能出正式结果。
Factory 的空中确认、地面服务和楼梯路径，Warehouse 的库存/集货/交接链，House 的
家具/物品/通路/退出链，City 的事故/人员/交通/通路链，都有潜在学习信号，但当前
执行器、几何、资产或业务证据不足以区分“模型没有学会”和“平台没有测到”，统一保持
unknown。这使难度审计遵守出题人视角：先证明题目能稳定提供可重复、可归因的学习
机会，再增加陷阱、通信降级或动态失效复杂度。

## 证据索引

- 五场景生成器：demo/benchmark/goal_driven_tasks.py。
- Goal 原始报告：demo/benchmark/goal_session.py、demo/benchmark/goal_episode_runner.py。
- 评分侧车：demo/benchmark/goal_scoring_adapter.py。
- checkpoint 合同：demo/benchmark/SCORING_CHECKPOINT_DESIGN.zh-CN.md。
- P0 部署与二元 oracle：demo/benchmark/DEPLOYMENT_STAGE_PLAN.zh-CN.md、
  demo/benchmark/REMAINING_FEASIBILITY_ORACLE.zh-CN.md。
- L1/L3 公式与边界交接：/home/pupil/Documents/Codex/agent-team/workspaces/benchmark-learning-audit-20260923/handoffs/L1-L3-criteria.md。
