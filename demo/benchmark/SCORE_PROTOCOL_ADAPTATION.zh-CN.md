# 评分协议接入边界（阶段记录）

阶段49更新：当前mapping为 `protocol-20260925-goal-score-source-bindings-v5`；raw cost
version=2。正式计划/成本按expected episode、任务digest和runner封存的固定源文件hash
读取；缺绑定/替换文件的相关事实为null，消费者不得补造历史绑定。逐源失败不遮蔽
其他有效事实；共享transport保持唯一权威，不回退到工具镜像。只给episode或不传身份
的旧成本调用为明确legacy诊断，不授予task-bound资格。餐厅验真器区分正式与旧成本
版本复算，旧v1字段比较不认证来源。见[阶段49](STAGE49_SCORE_SOURCE_BINDING.zh-CN.md)。

阶段48更新：当时mapping为 `protocol-20260925-goal-claim-scopes-v4`，沿用下述v3的
声明适用范围；task_allocation子块version=2，误拒/误部署率使用可行/不可行各自风险
分母，旧全体份额移至`all_epoch_*_share`。未答保留、零分母null，按initial/runtime
分层；缺少新风险字段的旧调用不猜测条件率。见[阶段48](STAGE48_DEPLOYMENT_DENOMINATORS.zh-CN.md)。

2026-09-25更新：以下原阶段描述中的串行runner、共享容量未实现、
`planned_not_captured`及固定子任务协议缺口是历史快照，不代表当前运行。
阶段47映射为 `protocol-20260925-goal-claim-scopes-v3`，sidecar schema仍为v1；
已有字段保留，但状态字符串按新映射修正，消费者应记录mapping revision。
任务分配使用参与者自主计划事后审计与部署门禁，覆盖由外部里程碑检查，
不再将自主分解列为等待修复的缺陷。见[当前路线](RELEASE_ROADMAP.zh-CN.md)。

`scoreability_reasons`只描述raw-only与独立发布验收边界，不无条件声称通信证据缺失、
runner串行或需要固定子任务。`claim_requirements`列出适用声明所需证据，**不选择本次
实验声明、不证明要求已满足、不自动授予资格**。restore/sham仅用于严格同快照比较，
并发证据仅用于并发协调声明；普通基线仍须具备原生执行、业务和部署oracle证据。
checkpoint/trusted-state/clock的 `not_attested_by_adapter` 表示本适配器不出具认证，
不表示没有采集器；具体归档状态见引用的raw observation子块，`observed`也不代表
完整验真。`physical_clock_bound=false`是本侧车的未认证标记，不否定其他原始物理读数。
三项发布标志继续false，不改旧档案或公开任务。

`snapshot_evidence`原四个布尔字段改为可空的未观测值null，字段名保留；不可将null
当作已恢复或已捕获。该变化由mapping v3明确区分，旧消费者若只支持布尔值应拒绝
新mapping或处理null，不能填true/false冒充测量。未提交可选branch档案记
`status=missing`与`absence_reason`，不产生全局质量失败；提交后格式不合法仍记invalid。
严格分支声明的外部资格验收必须要求有效restore/sham证据，不能依据“没有quality_failure”
授予资格。本adapter没有新增声明选择或自动资格验收接口。

本记录把 Goal 主轨的原始运行证据与《异构多机器人高层协作智能+Benchmark+能力分类与评测协议》分开。协议原文由用户提供，当前版本 SHA256 为 `539822d71b46b520c21df9170b182e0b7fe66016b8cfb26825a33294f52651c1`。

## 主线：目标与里程碑，由模型拆题

2026-09-23 用户明确：公开总体目标和可验证的里程碑，例如取得灭火器、操作报警按钮；任务分解、成员指派、联盟和时序均由被测模型决定。里程碑不得携带推荐机器人、参考分解或执行顺序。物理前置条件、负载、安全和资源约束仍如实描述为环境规律，不能暗中变成作者预设调度。作者见证只用于隐藏可达性审计，不是唯一答案。

此前建议的固定子任务 Track-A 不再作为主线要求。协议 §3.1 中以固定子任务全集为分母的 VAR/FSR/IRR 暂标 `not_applicable_protocol_mismatch`，应修订评分定义以接纳自分解能力，不能反过来限制题面。里程碑数量不能直接代替固定子任务数量；模型多拆/少拆任务也不能直接改变评分分母。分解覆盖、可执行分配和时序质量应分别保留证据，技能执行失败不能自动改写为决策错误。

正式 runner 当前仍串行执行技能，使用 host 收据时钟；原生位姿是可选的局部物理读数。每回合额外归档 `scoring_adapter_metadata.json`，记录原始文件哈希、事件计数、任务与条件身份、失败结果和证据限制。完整物理并发、共享容量链路和同快照分支仍待实现。

## 旁路元数据的含义

`scoring_adapter_metadata.json` 是评测方内部的 raw-only sidecar，不进入 `public_task.json`，不改变题包哈希，也不向参与者公开。`branch_id` 保持空值并标记 `single_episode_no_protocol_branch`；当前归档没有协议分支语义，不能声称已经完成反事实配对。`execution_validated`、`business_outcomes_validated` 和 `scoreable` 始终为 `false`。

当前可以审计：GoalSession 事件、动作、计划、技能收据、业务证据、通信/位姿工具账本和局部 native readback。当前不能计算：原固定子任务 VAR/FSR/IRR、共享容量 C/C90/C50、完整同步/互斥/停滞主矩阵、严格 Q(t) 与删失、同快照因果归因、跨回合经验学习分数或 0–100 合成分。`code_baseline` 未绑定时保留空值，不拿题包版本冒充代码提交。

## 阶段门禁

1. 先保持 raw sidecar、文件哈希和负向测试，确保缺少物理证据时明确降级。
2. 自分解主线已确定。先按[Checkpoint 设计](SCORING_CHECKPOINT_DESIGN.zh-CN.md)冻结指标→处理→保存点→状态→配对分支的映射，再建设保存/恢复与并行分支执行；通信、评分权重等未实现部分明确待办，不用另建固定任务轨替代。
3. 每个新增评分生产器都必须绑定真实 episode 时钟、实体键、条件/种子和删失状态，并与 Goal 工程轨分开归档。
4. 只有正式原生 episode、业务结果和评分证据均通过独立门禁后，才允许讨论 `execution_validated`；本阶段不改变 `scoreable`。

## 与本阶段代码的关系

- `goal_scoring_adapter.py` 生成 sidecar 元数据和原始归档索引。checkpoint旧状态为
  `planned_not_captured`；mapping v3已改为 `not_attested_by_adapter`，只将恢复/sham
  列作严格同快照声明要求，不实现恢复或评分公式。
- sidecar 的 `task_allocation` 字段把已验证部署门禁的显式分类计数/错误率与模型自身
  plan admission 诊断合并为一份任务分配画像；oracle 不合格时部署分母为零，画像仍保持
  raw-only，不构成正式六轴分数。
- `goal_episode_runner.py` 在结果归档后写入该 sidecar；执行/工具收尾异常也保留错误及 `scoreable=false`。存储不可写和启动前校验失败不保证产生完整归档；场景 wrapper 后写的证据仍需最终封存索引。
- `measurement_contract.py` 的旧干预标签不自动等同协议新版本权限；在政策冻结前不重命名、不扩展其评分语义。
