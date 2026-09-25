# benchmark-branch 融合分析报告（阶段61）

日期：2026-09-25  
融合基线：benchmark-branch `5d2181e`（4 个独有提交）→ 融合进 benchmark-task
`4d01e94`/`9c5667d`（未改动 benchmark-branch 本身）  
分析者：ZCode 主任务（用户授权直接融合 + 修改需先审）

## 融合执行摘要

- **变更集不相交**：benchmark-branch 自 merge-base（`9bb6bf1`，Stage35 时代）以来的
  全部 A/M 文件，我方均未触碰（唯一例外是 6 份双侧都改的文档/无实义文件，已恢复
  我方版本）。因此整侧取入零冲突：63 文件 ~15.7k 行。
- **取入内容**：`branch_data.py`（988 行，模拟器无关账本+32 分支生命周期：
  open/resume/validate + admission/completion/verification/active-step/ledger
  五类门禁，append-only JSONL）；`protocol_runner.py`（557 行，六维度协议
  runner，场景无关）；五场景 `checkpoint_branches.py`（餐厅 559/工厂 1055/
  城市 1143/仓库 1786/房屋 1845）+ `branch_data_adapter.py` + `shared_static.py`；
  `tools/physics_replay` 增强（budget/memory_profile/contact_reports/wrench_state/
  runtime_state/physics_trace —— 回溯的物理测量层）；五个 `benchmark_*.json`
  场景注册 + Env DIY 集成 + 缩略图；测试 4 份（test_branch_data 等）。
- **验证**：工具测试 20 passed；融合影响族聚焦 263 passed；Env DIY HTML 契约
  PASS；本机全量 3997 passed / 2 failed（Factory 已知哈希 + 餐厅场景需求锁，
  后者已按三文件闭包修正 9c5667d）；ljy 聚焦验证在跑。
- **修正记录**：融合提交曾误吸 `my_env.json` 与会议纪要（保护文件），已在
  推送前 amend 摘除（`4d01e94`），用户工作区状态保持未提交原样。

## 与 R1–R5 的对接点（为什么这个工具正合适）

| 路线图出口 | 对接方式 |
|---|---|
| **R4 恢复+sham** | `BranchSceneAdapter.capture_checkpoint/restore_checkpoint` + physics_replay 的 runtime_state/wrench/contact 报告 = R4 需要的"变化前快照→短窗口变化→真实恢复→即时读回→两次无处理 sham"基础设施。分支账本的 append-only + resume_batch 直接支撑严格分支对拍。 |
| **R3 405 条件实验效度** | 32 分支批处理 = 条件×重复的并行执行层：每分支一个条件档，账本强制 admission/completion/verification 门禁，`validate_run` 拒绝缺门禁的 formal 结果——正是"不依据结果好坏选择 oracle_status"的机器化。 |
| **R1 并发证据** | checkpoint 引擎的场景 runner 拥有 stepping——可与本批 `goal_concurrency_evidence.py`（fake backend）衔接：fake 层先验协议，checkpoint 引擎做 ljy 原生验证。 |
| **R2 成本** | `record_cost`/`record_network` 账本行与声明边界成本快照互补（前者逐步计量，后者按声明边界切）。 |

## 实验逻辑的出入（融合分析重点）

1. **工具是解耦的，不知道 Goal 协议**：branch_data 的"participant actions"是
   场景自由格式；它不理解 GoalSession 的 6 动作/8 事件、部署评估 epoch、
   通信计费口径。→ 对接策略：让 Goal episode 侧实现 BranchSceneAdapter，
   把 session 事件投影进 `record_event`，而不是让工具反向驱动 Goal。
2. **32 分支 ≠ 32 独立样本**：分支共享初态（这正是回溯实验要的），统计上按
   base_instance 聚类的纪律不变；路线图"条件展开/重复 seed 不增加独立样本数"
   仍然适用——工具提供执行并行，不提供统计独立。
3. **餐厅 checkpoint 引擎滞后**：其 restaurant checkpoint_branches 写明
   "business task remains authoring-only"，基于 Stage35 前的餐厅栈；需对齐
   Stage51-60 的公开运行入口/初始 oracle/业务事实分层后才能跑餐厅分支。
4. **protocol_runner 的"六维度"是旧协议词汇**：与我们 SCORING_DEFINITION_MATRIX
   的六轴（含 44 未冻结参数）不是同一套——不能直接当评分器用，只当执行骨架。

## 需要 benchmark-branch 侧修改的清单（**待你审核**，未动其分支）

1. **LFS 对象不在 bundle**：五个场景缩略图是 LFS；离线机器（ljy 无法达 GitLab）
   只能 skip-smudge 检出。建议：benchmark-branch 侧把 UI 缩略图改非 LFS 小文件，
   或在发布清单附 LFS 对象包。
2. **餐厅 adapter 停留在 authoring 语义**：等我们对齐其调用面后，可能需要
   benchmark-branch 侧更新 restaurant adapter 的 task_status/scene_payload 以
   匹配公开运行入口；届时提供精确 diff 给你审。
3. （可选）`branch_data.open_batch` 的 `execution_scope` 词表建议与我们
   development/release 两轨对齐（diagnostic/formal），避免双词汇。

## 后续动作（已排队）

- 子代理C（并发 fake-backend 切片）完成后审阅提交；
- ljy 融合聚焦结果回收；
- 下一批：用 checkpoint 引擎在 ljy 跑**单场景 R4 最小切片**（快照→变化→恢复→
  读回→双 sham），同时 R3 侧把 405 条件映射到 32 分支批的执行计划。
