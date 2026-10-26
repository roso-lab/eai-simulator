# 论文 `72b6a3b` 实施阶段记录

更新日期：2026-10-22。当前 Overleaf 基准为
`72b6a3b975ae83743dc1d447b99edd728cb1dcdc`。

这份文档记录工程实施，不修改论文正文，也不替代真实 GPU 收据。所有实验仍必须从
`/home/pupil/HeteroCoord` 和其本地 `source/` 运行；不得从 `/home/pupil/eai-simulator/source`
导入 EAI。

## 当前并行批次

### P0：论文身份迁移（已完成合同层）

- `benchmark/scorebook_v3.py`、`protocol_v3.py`、`statistics_v3.py` 已绑定 `72b6a3b`。
- 旧的 `fc3d686`、`1f32949`、`75ceff6` 等只作为历史基线，不再接受为当前正式注册身份。
- 身份拒绝和旧版本兼容测试：9 项通过。
- 这一步只证明注册身份迁移，不证明任何旧 artifact 已经重新资格化。

### P1–P2：物理检查点、许可历史和 fresh initialization（合同层已验收，执行接线未完成）

新增 [paper_state_v3.py](/home/pupil/HeteroCoord/tools/benchmark_branch/paper_state_v3.py)，建立以下边界：

- `PhysicalCheckpoint` 只封存评价器拥有的 world、robots、controllers、skills、evaluator、transport；拒绝 submitted-system 的 brain/model/algorithm/private state。
- `PermittedHistory` 绑定 source run 和物理检查点，按 node 限定 observations、committed actions、delivered messages；拒绝未来记录、其他节点私有记录、未投递消息和 condition 泄漏。
- `FreshInitializer` 固定 history encoder、上下文、host time、tool-call、writable-state 预算和 episode reset 方法。
- `build_initialization_request` 在 condition 分配前生成 condition-blind 请求，并为每个新 branch 建立私有 writable namespace。
- 初始化收据要求 physical tick 不变、physical action count 为零；超时、格式错误、预算超限和方法错误保留为 method outcome。
- `DiagnosticResetCollector` 对调用方提供的四类 namespace 记录签发进程内诊断收据，并拒绝上下文复用；它尚未实际观察外部服务或清空资源，不能作为生产 reset 证据。
- `BranchPaperIdentity` 绑定 paper/source/checkpoint/history/initializer/condition/repeat/execution model 和随机流。

2026-10-22 恢复会话后的 R7 修订：请求输出必须使用评价器独立保存的 checkpoint、history、initializer、当前观测、fresh context 和 private initialization stream 重新验证。即使替换合法观测值、上下文或随机流后重算公开摘要，也会在输出前拒绝。调用者不能从待验请求本身提取这些可信输入。

主代理联合回归：189 项通过；编译、范围内差异检查通过。Sol 独立复审 Accept，其另一套含恢复桥接/共享看门狗的回归为 206 项通过；ZCode 同一固定回归集独立复跑 189 项通过。此结论只适用于合同与离线回归；counter 仍为 caller-reported diagnostic，所有 formal gates 为 false。

项目本地导入已实际复核：EAI、EAI_assets、EAI_hmrs 三个包均位于 `/home/pupil/HeteroCoord/source/`；未启动 Isaac/Kit。

## 尚未完成的接线

P1–P2 当前是独立合同模块，还没有替换现有 `ReplayStateRegistryV3` 或 `BranchExecutor` 的实际恢复路径。原因是旧恢复注册表仍把 `algorithm` 作为必需 participant；新论文要求 evaluator-owned physical state 与 submitted-system fresh initialization 分离。下一步必须由主代理设计兼容迁移：

1. source-run 生成器在隔离进程产生 `PhysicalCheckpoint + PermittedHistory`；
2. branch executor 在 treatment 前调用 fresh initializer；
3. collector 将两条证据链和 `BranchPaperIdentity` 写入同一结果身份；
4. 只有 continuation blocks 才允许进入 shared-session；source runs 保持 isolated。

## 后续批次

| 批次 | 目标 | 当前状态 |
|---|---|---|
| P3 | shared-session slot-private runtime 与 R7 active/queued 归因接线 | 尚未开始 |
| P4 | fixed roster、sham/intervention repair gain、unknown range | 尚未开始 |
| P5 | 真实 GPU 三路资格矩阵 | 尚未开始；不接受 CPU/mock 替代 |
| P6 | isolated closed-loop audit、超几何 bound、whole-block widening | 尚未开始 |
| P7 | GPU pilot、认证简历目录闭合、release gate | 尚未开始 |

## Formal gate 状态

以下门在完成真实 GPU 资格、独立 closed-loop audit、统计 widening 和完整发布证据前均保持
`false`：

`physical_checkpoint_qualified`、`history_initialization_qualified`、`branch_isolation_qualified`、
`closed_loop_audit_qualified`、`statistical_widening_qualified`、`paper_score_eligible`、`rankable`。

本阶段没有运行 Isaac/Kit/GPU，没有提交或推送代码，也没有修改论文正文。
