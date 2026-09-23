# 评分协议接入边界（阶段记录）

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

- `goal_scoring_adapter.py` 生成 sidecar 元数据和原始归档索引，声明 checkpoint 需求为 `planned_not_captured`，不实现恢复或评分公式。
- sidecar 的 `task_allocation` 字段把已验证部署门禁的显式分类计数/错误率与模型自身
  plan admission 诊断合并为一份任务分配画像；oracle 不合格时部署分母为零，画像仍保持
  raw-only，不构成正式六轴分数。
- `goal_episode_runner.py` 在结果归档后写入该 sidecar；执行/工具收尾异常也保留错误及 `scoreable=false`。存储不可写和启动前校验失败不保证产生完整归档；场景 wrapper 后写的证据仍需最终封存索引。
- `measurement_contract.py` 的旧干预标签不自动等同协议新版本权限；在政策冻结前不重命名、不扩展其评分语义。
