# 评分协议接入边界（阶段记录）

本记录把 Goal 主轨的原始运行证据与《异构多机器人高层协作智能+Benchmark+能力分类与评测协议》分开。协议原文由用户提供，当前版本 SHA256 为 `539822d71b46b520c21df9170b182e0b7fe66016b8cfb26825a33294f52651c1`。

## 当前两条轨道

**Track-G / Goal 工程轨**保留公开业务目标、参与者自行分解、团队选择和重规划。作者见证计划只用于隐藏可达性审计，不能作为模型的标准答案。正式 runner 继续串行执行技能，使用可信 host 收据时钟；原生位姿工具是可选的局部物理读数。每回合现在额外归档 `scoring_adapter_metadata.json`，其中明确列出原始归档文件哈希、事件计数、时钟和快照证据范围，以及各协议维度的 `A/P/M/X` 状态。

**Track-A / Protocol-Scoring** 仍是后续套件。它需要冻结子任务全集、独立可行性校验器、共享 bit/s 链路、实际物理并发事件、同快照 fact/reference/sham/intervention 分支和可恢复随机状态。它不能把 Track-G 的自分解计划、窗口通信账本、符号快照或串行计划窗口直接改名为协议指标。

## 旁路元数据的含义

`scoring_adapter_metadata.json` 是评测方内部的 raw-only sidecar，不进入 `public_task.json`，不改变题包哈希，也不向参与者公开。`branch_id` 保持空值并标记 `single_episode_no_protocol_branch`；当前归档没有协议分支语义，不能声称已经完成反事实配对。`execution_validated`、`business_outcomes_validated` 和 `scoreable` 始终为 `false`。

当前可以审计：GoalSession 事件、动作、计划、技能收据、业务证据、通信/位姿工具账本和局部 native readback。当前不能计算：协议 3.1 的 VAR/FSR/IRR、共享容量 C/C90/C50、完整同步/互斥/停滞主矩阵、严格 Q(t) 与删失、同快照因果归因、跨回合经验学习分数或 0–100 合成分。

## 阶段门禁

1. 先保持 raw sidecar、文件哈希和负向测试，确保缺少物理证据时明确降级。
2. 在实现 Track-A 前先确定任务分配轨、通信模型、checkpoint/branch、原生并发、餐厅准入、评分权重、LLM 成本口径和探针权限八项政策。
3. 每个新增评分生产器都必须绑定真实 episode 时钟、实体键、条件/种子和删失状态，并与 Goal 工程轨分开归档。
4. 只有正式原生 episode、业务结果和评分证据均通过独立门禁后，才允许讨论 `execution_validated`；本阶段不改变 `scoreable`。

## 与本阶段代码的关系

- `goal_scoring_adapter.py` 只生成 sidecar 元数据和原始归档索引，不实现评分公式。
- `goal_episode_runner.py` 在每回合结束后写入该 sidecar；失败回合也保留 `scoreable=false`。
- `measurement_contract.py` 的旧干预标签不自动等同协议新版本权限；在政策冻结前不重命名、不扩展其评分语义。
