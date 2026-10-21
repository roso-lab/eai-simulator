# HeteroCoord 工程进度文档索引

这些文档记录工程阶段、验收证据和未闭合条件，不是论文正文，也不替代运行收据。当前论文基准为 `1f329494e755523692f5f96c8e0889c6cec15a82`。

工程目录职责和提交边界见 [工程结构说明](../PROJECT_STRUCTURE.zh-CN.md)。
本轮整理和待提交批次见 [工程整理计划](ENGINEERING_CLEANUP_PLAN_20261021.zh-CN.md)。

## 读取顺序

1. [当前论文对齐](CURRENT_PAPER_ALIGNMENT.zh-CN.md)：先确认论文版本、协议差异和正式门状态。
2. [P0–P5 总进度](P0_P5_IMPLEMENTATION.zh-CN.md)：查看各阶段已完成和未完成的工程边界。
3. [GPU / shared-session 重排](GPU_SHARED_SESSION_REPLAN_20261021.zh-CN.md)：查看最新论文下 G0–G6 的执行依赖。
4. [G1/R7 崩溃归因反思](G1_SHARED_SESSION_CRASH_REFLECTION_20261021.zh-CN.md)：查看 active/in-flight 与 queued/not-run 的故障域规则。

## 按用途分组

### 论文与框架对齐

- [HeteroCoord v3 框架审计](HETEROCOORD_V3_FRAMEWORK_AUDIT.zh-CN.md)
- [P0–P5 总进度](P0_P5_IMPLEMENTATION.zh-CN.md)
- [当前论文对齐](CURRENT_PAPER_ALIGNMENT.zh-CN.md)

### 评价器与统计

- [D1/D4 evaluator](D1_D4_EVALUATOR_PROGRESS.zh-CN.md)
- [P3 protocol/statistics](P3_PROTOCOL_STATISTICS_PROGRESS.zh-CN.md)
- [P4/P5 evidence closure](P4_P5_EVIDENCE_CLOSURE_20261021.zh-CN.md)

### 本地运行、恢复与资源

- [持久恢复](DURABLE_RECOVERY_PROGRESS.zh-CN.md)
- [资源测量](RESOURCE_MEASUREMENT_PROGRESS.zh-CN.md)
- [工程状态](ENGINEERING_STATUS.zh-CN.md)

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
