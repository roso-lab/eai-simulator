# HeteroCoord 任务层 TODO List

> 评审结论（2026-10-07）：`task/` 当前作为“任务合同与编排骨架”是合理的，
> 但还不是可以直接运行完整 Isaac Sim benchmark 的最终 runtime。后续实现必须保持
> “系统算法 → 任务编排 → EAI Skill/Controller → 物理证据评测”的单向边界。

## 当前已具备

- [x] `task/contracts.py` 提供任务包、机器人能力、算法决策、执行反馈和运行结果合同。
- [x] `task/runner.py` 支持注入 scene provider、replay backend、SkillExecutor 和 evaluator。
- [x] 系统算法只输出 assignment/decision，不直接声称物理成功。
- [x] 静态任务合同可以通过 `EaiTaskPackage.bind_runtime()` 绑定运行时机器人池。
- [x] `TaskRunRequest` 预留 `branch_id`、`checkpoint` 和 `repeat_id`。
- [x] 无 executor/evaluator 时返回 `N/A / physical_evidence_required`，避免把规划成功误判为物理成功。
- [x] `EventLedger` 为算法决策、分配和最终 verdict 提供可审计记录。
- [x] 无 Isaac 的合同测试已通过：`task/tests` 与 `algorithm/common_coordination/tests` 共 4 项。

## P0：接入真实运行前必须完成

### 1. 修正 checkpoint 与 branch 的生命周期顺序

- [ ] 在 `task/runner.py` 中先创建/切换 branch 并恢复 checkpoint，再绑定该 branch 的 scene 和 robot state。
- [ ] 保证 `scene_provider` 能够接收 branch/checkpoint 上下文，不读取事实分支资源。
- [ ] 为事实分支和回放分支分别创建独立的 ledger、scene binding 和运行元数据。
- [ ] 增加一个无 Isaac 的 branch-order 回归测试。

目标顺序：

```text
创建 branch → 恢复 checkpoint → 绑定场景/机器人 → 算法决策 → 执行 → 评测
```

### 2. 实现执行器、评测器和回放后端

- [ ] 在 `task/executors/` 实现 `Assignment → SkillRequest → SkillPlan → EAI controller/skill`。
- [ ] 在 `task/evaluators/` 读取 EAI 物理状态，输出 milestone evidence 和终止 verdict。
- [ ] 在 `task/replay/` 接入现有 checkpoint、并行回溯和分支工具。
- [ ] 系统算法不得反向调用 executor、controller 或 simulator。
- [ ] 真实执行只能使用调用者创建的 `open_simulator_session(...)`，`task/runner.py` 不创建 Isaac App。

## P1：多机协作与可审计性

### 3. 增加正式的 `DecisionContext`

- [ ] 新增明确的数据结构，至少包含：当前任务图、机器人状态、目标/语义地图、事件、
  已完成任务、执行反馈、通信状态、失联/故障状态、branch/checkpoint 信息和历史 ledger。
- [ ] 将算法回调从 `provider(task, robots)` 扩展为接收 `DecisionContext`。
- [ ] 保持旧的最小回调兼容层，但不得继续把运行时状态全部塞入 `metadata`。

### 4. 实现统一的分配合法性校验

- [ ] 检查未知任务、未知/ inactive 机器人和重复分配。
- [ ] 检查机器人 capability 是否满足任务要求。
- [ ] 检查 prerequisite 是否满足当前 phase。
- [ ] 检查 `unassigned_tasks` 是否属于任务图。
- [ ] 明确单机器人、重复占用和 coalition/multi-robot assignment 的规则。
- [ ] 明确必需任务是否必须完整覆盖，以及不可分配时的 `N/A`/失败语义。

### 5. 实现 DAG phase 和并行执行调度

- [ ] 根据任务图 prerequisite 生成可执行 phase。
- [ ] 同一 phase 的独立 assignment 支持并行执行和同步等待。
- [ ] 并行度由 executor/scheduler 管理，算法 adapter 不自行创建隐藏线程。
- [ ] 支持 timeout、safe-stop、communication loss 和 member exit 等失败状态。

### 6. 完善 ledger 和反馈链

- [ ] 将每条 `ExecutionFeedback` 写入 ledger。
- [ ] 记录 `milestone_evidence`、`replan_requested` 和 checkpoint restore 事件。
- [ ] 保存每次 branch 的 condition、输入摘要、反馈、verdict 和 digest。
- [ ] 保证 ledger 事件 JSON 可序列化且 append-only。

## P2：任务合同和实验矩阵

### 7. 扩展 Fire Rescue 任务合同

- [ ] 在不引入 USD/PhysX 的前提下补充任务阶段、目标位置、能力要求和并行关系。
- [ ] 增加风险/扰动、checkpoint 条件、失败处理策略和物理 milestone 声明。
- [ ] 明确典型任务、共性任务、干扰分支和恢复分支的 schema。
- [ ] 保持任务 JSON 与具体 EAI scene、robot controller、asset path 解耦。

### 8. 建立算法测评边界

- [ ] 系统算法只负责规划/分配/重规划；底层运动和控制统一由 `algorithm/skills/` 与 EAI executor 提供。
- [ ] 没有官方源码或官方 checkpoint 的算法不得伪造完整复现结果，只记录为 `N/A`。
- [ ] DCMRTA/HeteroMRTA 等跨输入分布 checkpoint 只做 decision-only 轨道；无法证明泛化时记为 `N/A`。
- [ ] FMC-ATA 等不支持当前任务分解形式的算法使用明确的默认分配适配，并把不适用指标记为 `N/A`。
- [ ] HAPPO 作为相关工作记录，不进入当前多机器人任务分配测评矩阵，除非后续获得可核验的官方团队任务 checkpoint。

## 验收顺序

1. [ ] 无 Isaac 合同测试、序列化测试和 branch-order 测试通过。
2. [ ] 完成分配校验、`DecisionContext` 和 DAG scheduler 的纯 Python 测试。
3. [ ] 用 `source/EAI_hmrs/EAI_hmrs/envs/EAI-Factory-Z1-v0.json` 做单环境 Isaac smoke test。
4. [ ] 在一个 Fire Rescue 场景中完成“算法分配 → Skill 执行 → evaluator 证据 → ledger”闭环。
5. [ ] 再接入 checkpoint 分支、并行回溯和多机并行执行。
6. [ ] 最后扩展多场景、重复实验和完整 benchmark 矩阵。


## 非目标

- [ ] 不在 `task/` 注册 Gym task。
- [ ] 不在 `task/` 创建 Isaac App 或直接推进仿真。
- [ ] 不把 USD、下载的模型权重、checkpoint 或实验输出提交到框架源码。
- [ ] 不让算法 adapter 自行判定物理成功。
