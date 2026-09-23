# 阶段 28：主线优先级与评分接入收口

日期：2026-09-24  
基线：`codex/benchmark-pose-telemetry`，目标远端：`benchmark-task`

本阶段把高层能力分类、部署门禁和评分侧车按依赖关系收敛。题面仍只公开总体目标与
可验证里程碑；模型自行拆题、选人和排时序。本文是工程交接，不是新的题包或公开提示。

## 依赖顺序

| 优先级 | 工作包 | 当前状态 | 通过条件 |
|---|---|---|---|
| P0 | 部署可行性门禁 | 已接入 GoalSession/runner；五场景 native reader 未齐 | 初始、成员退出、能力变化和里程碑测量点都有二元 oracle；`failed` 只作平台证据失败 |
| P0 | 资产与场景准入 | 餐厅 A2 双主机已通过；Factory 冻结 USD 版本缺口待处理 | 资产哈希、source closure、真实 episode 启动证据齐全；不放宽冻结 resolver |
| P1 | 评分侧车 | 已有 raw cost、通信、部署和计划准入归档；本阶段新增统一任务分配画像 | 部署分母仅取 oracle verified episode；计划拒绝按类别归档；所有发布标志仍为 false |
| P1 | 三层通信与位置工具 | 三池/AoI 与可选 query/subscribe 已有离线契约 | 正式 episode 接线、同均值 JP/IID/BP、C90/C50 包围区间和 bootstrap CI |
| P1 | checkpoint/sham/RNG | 设计和编键随机流已落盘 | 原生保存/恢复、处理前快照、sham 回放、分支隔离全绿 |
| P2 | E2 机间服务/多端点 | 仅设计入口 | 先冻结云—边—端接口，再实现 p2p publish/consume 和多端点适配器 |

## 本阶段已落盘的评分接口

`goal_scoring_adapter.py` 新增 `task_allocation_observations()`。它把两类事实放入同一
raw-only 画像：

- 部署门禁的 `correct_deployment`、`correct_refusal`、`false_refusal`、
  `false_deployment` 计数与显式错误率；
- 模型自己提交的 `submit_plan`/`revise_plan` 准入尝试、能力/负载/资源/预算拒绝率和
  修订存活率。

部署 oracle 未达到 `episode_oracle_eligible=true` 时，部署分母强制为 0，错误率写
`null`，而计划准入事实仍保留供诊断。此画像不计算六轴分数、不引入固定子任务分母，
也不把技能失败改写成分配错误。

## 禁止事项与阶段出口

- 不合并基于旧基线且会回删当前主线的整支工作树；只挑选经过差异审计的独立提交。
- 不修改冻结题包、见证计划、`reach_m`、overlay/hash 或用户文件。
- 不把 `unverified` 暴露给模型或作为第三种正式答案。
- `scoreable=false`、`execution_validated=false`、`business_outcomes_validated=false`
  在本阶段及所有回归中保持不变。
- 只有 P0 native oracle/资产门禁完成后，才允许把统一画像接入正式分母；在此之前只归档
  raw evidence 和基础设施失败原因。

