# HeteroCoord 工程进度文档索引

这些文档记录工程阶段、验收证据和未闭合条件，不是论文正文，也不替代运行收据。当前
冻结的权威论文 ref 为
`overleaf-old/main@dac7b94207a2683d494bdb388fc48533c9219bdf`。当前发布身份与
contract-only difficulty 语义已经工程实现；生产 certification registries、真实运行与
物理/GPU 证据仍为 pending，正式资格门保持关闭。历史文档内的旧 SHA 只描述当时阶段，
不得覆盖本页的当前权威 ref。

工程目录职责和提交边界见 [工程结构说明](../PROJECT_STRUCTURE.zh-CN.md)。
本轮整理和待提交批次见 [工程整理计划](ENGINEERING_CLEANUP_PLAN_20261021.zh-CN.md)。

## 读取顺序

1. [2026-10-23 阶段收尾](STAGE_CLOSEOUT_20261023.zh-CN.md)：查看受监督原生 source seam、失败证据、修复验收和当前七个 gate。
2. [后续任务规划与阶段性收尾](FOLLOWUP_TASK_PLAN_20261022.zh-CN.md)：这是以 `72b6a3b` 编写的历史规划；执行时以 `dac7b942…` 当前发布身份和最新语义迁移审计为准。
3. [当前论文对齐](CURRENT_PAPER_ALIGNMENT.zh-CN.md)：确认论文版本、协议差异和正式门状态；其中旧 SHA 说明仅作历史参考。
4. [论文 72b6a3b 实施阶段](PAPER_72B6A3_IMPLEMENTATION.zh-CN.md)：查看 P0–P2 新状态所有权和初始化合同。
5. [P0–P5 总进度](P0_P5_IMPLEMENTATION.zh-CN.md)：查看各阶段已完成和未完成的工程边界。
6. [GPU / shared-session 重排](GPU_SHARED_SESSION_REPLAN_20261021.zh-CN.md)：查看 GPU qualification 和 shared-session 的执行依赖。
7. [G1/R7 崩溃归因反思](G1_SHARED_SESSION_CRASH_REFLECTION_20261021.zh-CN.md)：查看 active/in-flight 与 queued/not-run 的故障域规则。

## 按用途分组

### 论文与框架对齐

- [HeteroCoord v3 框架审计](HETEROCOORD_V3_FRAMEWORK_AUDIT.zh-CN.md)
- [P0–P5 总进度](P0_P5_IMPLEMENTATION.zh-CN.md)
- [当前论文对齐](CURRENT_PAPER_ALIGNMENT.zh-CN.md)

### 评价器与统计

- [D1/D4 evaluator](D1_D4_EVALUATOR_PROGRESS.zh-CN.md)
- [P3 protocol/statistics](P3_PROTOCOL_STATISTICS_PROGRESS.zh-CN.md)
- [P4/P5 evidence closure](P4_P5_EVIDENCE_CLOSURE_20261021.zh-CN.md)
- [D1–D6 结果计算与输出闭环](RESULTS_PIPELINE_V3_20261025.zh-CN.md)

### 本地运行、恢复与资源

- [持久恢复](DURABLE_RECOVERY_PROGRESS.zh-CN.md)
- [资源测量](RESOURCE_MEASUREMENT_PROGRESS.zh-CN.md)
- [工程状态](ENGINEERING_STATUS.zh-CN.md)
- [GPU 三路原生共享槽位进展（2026-10-24）](GPU_THREE_WAY_NATIVE_SLOT_PROGRESS_20261024.zh-CN.md)
- [生产闭环阶段收尾（2026-10-26）](PRODUCTION_PHASE_CLOSEOUT_20261026.zh-CN.md)

### Tool / R7

- [Tool convergence rules](TOOL_CONVERGENCE_RULES.zh-CN.md)
- [Tool recovery integration](TOOL_RECOVERY_INTEGRATION.zh-CN.md)
- [Tool vectorized integration](TOOL_VECTORIZED_INTEGRATION.zh-CN.md)
- [Tool diagnostic evidence adapter](TOOL_DIAGNOSTIC_EVIDENCE_ADAPTER.zh-CN.md)
- [shared-session crash reflection](G1_SHARED_SESSION_CRASH_REFLECTION_20261021.zh-CN.md)

## 证据边界

- 过程文档只能说明设计、命令和验收状态；正式论文证据必须绑定可重算的 manifest、GPU runner receipt、独立审计和发布门。
- CPU、合成 vectorized backend、静态测试和仅填写 `requested_device=cuda` 的结果不能充当 GPU/物理资格证据。
- 当前 `rankable`、`physical_qualified`、`paper_score_eligible`、`process_isolation_qualified` 和 `native_full_state_qualified` 均保持 `false`。
- 任何 Isaac Sim/Kit 运行结束后，必须清理 `omni.telemetry.transmitter`，并把清理结果写入对应收据。
