# 阶段 64：新测评协议的路线校准

日期：2026-09-25  
输入：`/home/pupil/下载/异构多机器人高层协作智能+Benchmark+能力分类与评测协议(1).md`（2476 行，全文已读）  
依据：[PROTOCOL_CONFORMANCE_MATRIX.zh-CN.md](PROTOCOL_CONFORMANCE_MATRIX.zh-CN.md)（130 行逐节交叉映射，子代理产出）+ 主任务全文阅读  
说明：viewer 方向审查因订阅额度耗尽（429）中断，本校准由主任务按同一审查纲领取管完成。

## 一、总判定：部分收敛，评分接口层需要按协议重建，事实层不需要推倒

- **事实层（原始观测）大体兼容**：分支四类命名（`branch_data.py::BRANCH_TYPES` 与协议 fact/reference/sham/intervention 完全一致）、部署状态机、oracle 二元真值+fail-closed、AoI/延迟分解、成本原始量（B_offer/B_link 素材）、通信三池——协议不否定这些，只要求在其上建统一评分接口。
- **评分接口层缺口明确**（矩阵：58 缺失，S14/M31/L13）：Q(t)/MCR/AUP 通用进展轴、合同 v4（里程碑权重/激活/稳定时长/反重复计数、能力约束聚合规则、角色约束独立类）、三维约束向量、五部署率机器名、统一日志事件（committed_plan 映射）、时序 E2E 指标与停滞协议、学习 R/C、探针家族。
- R1–R5 出口体系仍有效，但 **R2"评分事实"的定义应升级为"协议接口事实"**，R3 的统计契约以协议公式为准。

## 二、两个张力的裁决

### 张力 A：搁置并行/回溯 vs 协议强制配对分支
协议要求同快照配对分支（动态重构配对差、学习 R/C 逐轮环境回档、无收益重规划 hold-plan 对照、§7 探针 fact/sham/intervention）——这是因果比较的**硬性平台要求**。裁决（建议用户确认）：
- **继续搁置**：32 分支并行批执行与 R3 分支批执行计划（`goal_condition_branch_plan` 的执行侧）——这是并行优化，协议不要求。
- **解除搁置（零新开发）**：单快照恢复+sham 回放核心——Stage62 R4 最小切片已在 ljy 原生验证（恢复读回 1.79e-07 vs 双 sham 漂移 10.28，5.7e7 倍分离），它是协议配对分支的最小实现，已被证明可用；学习 R/C 与探针直接复用，无需"回溯工具再开发"。

### 张力 B：44 未冻结参数 vs 协议
矩阵消化表：**协议直接回答 14 项 / 口径给定数值待拍板 24 项 / 纯工程决策 6 项**。SCORING_DECISION_DIGEST 的第一档 10 项中 G3/G4 等已被协议部分回答；后续拍板以矩阵消化表为准精简（预计真正需要用户拍的只剩 ~10 项数值类）。

## 三、词表映射陷阱（落地时必须处理）

1. **oracle 真值词**（唯一词汇级阻断，S 级修复）：协议用 `team_state_infeasible` 表真值、`not_deployable` 表系统声明；我们两者都用 `not_deployable`。修法：oracle 输出侧引入 `team_state_infeasible`，读取层双读旧归档。
2. `iid_loss` 条件名 vs 实际确定性散布分布（代码 docstring 自认 not Bernoulli IID）——正式实验需真 IID 或改名。
3. C90/C50 当前基于合成投递率（离线诊断层），协议要求基于任务表现 M——属于批次 1 之后的事。

## 四、批次路线（吸收矩阵四批次，按真实开发量排序）

| 批次 | 内容 | 量 | 依赖/出口 |
|---|---|---|---|
| **0** | oracle 词汇对齐（`team_state_infeasible` 双读）+ 部署五率/约束失败标签 join 进 adapter（素材已在 `deployment_assessment_observations`） | S | 无；唯一词汇阻断 |
| **1** | 里程碑权重拍板（用户一项决策）→ **Q(t)/MCR/AUP/PGI 离线计算器**在现有归档落地（`goal_progress_evidence.py` 已有边界采样与 replay 校验，只缺冻结权重；协议"保持最后观测到 H"使阶梯积分合法） | S-M | 时序/动态重构/学习/成本四轴共同底座 |
| **2** | 合同 v3→v4 **加法扩展**：`required_outcomes`→`contract_milestones`（weight/stability/activation/anti_double_count）+ 能力约束聚合规则四值 + 角色约束独立类 + `committed_plan`/`milestone_verified`/`episode_terminated` 事件 + CS 三维判定器 | M | 沿用 fail-closed 加字段先例；影响面按 1302 个 goal 测试的字段子集控制 |
| **3+** | 时序停滞协议/wait-for 死锁证书（L）、healthy twin+探针家族（L，复用 R4 回溯核心）、学习 R/C 编排（M-L，逐轮环境回档）、需求扩张（L） | L | 依赖批次 1/2 底座 |

## 五、对当前进行中工作的影响

- 独立复算（`goal_independent_recomputation.py`）与 R5 认证脚手架（`goal_qualification_attestation.py`）与协议方向一致，保留。
- R3 执行计划映射（405→批）搁置其执行侧，其**条件枚举/配对语义**仍是批次 2 合同 v4 的输入。
- 餐厅重评估/注入链、异步并发、原生 episode 均为事实层，不受影响。
