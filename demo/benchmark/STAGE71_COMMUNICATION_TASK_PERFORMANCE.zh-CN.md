# Stage71 通信任务表现 MCR 磁盘映射闭包

日期：2026-09-26
基线：`b40e0a3`（Stage70）
状态：显式 opt-in 的 raw-only `MCR_comm` 重放；未接入正式通信评分

## 目标

Stage70 已能从事前 manifest、episode 原始档案和只读重算器闭合通信条件、传输统计、episode identity 与 progress evidence，但没有冻结 milestone 到任务表现 M 的转换，因此任务表现保持 N/A。

Stage71 新增 `goal_communication_task_performance.py`。只有 evaluator 在执行前提供 manifest v2，并嵌入 sealed contract-v4 映射时，producer 才能从磁盘 `public_task.json` 与 `goal_progress_evidence.json` 重算 terminal `MCR_comm`。没有该映射的 manifest v1 完全保持 Stage70 行为，不自动升级、不补默认权重。

本阶段不支持 `SR_comm`、`AUP_comm` 或 `e_step`，也不定义正式全局成功谓词、连续 progress 曲线或 decision-step 来源。

## 事前 mapping 契约

manifest v2 必须携带：

- `task_performance_mapping`：closed schema、version 1；
- `task_performance_mapping_sha256`：mapping 的 canonical SHA-256；
- `method_id = frozen-v4-terminal-mcr-v1`；
- `metric_name = MCR_comm`；
- `sample_selector = terminal`；
- evaluator-owned `progress_source.evaluator_ref` 与 `clock_source`；
- 非空、去重的 `eligible_termination_reasons`；
- `freeze_contract_v4()` 产生且能通过 `validate_frozen_v4()` 的 sealed contract-v4。

sealed v4 去掉 addon 后的 base-task digest 必须等于 manifest 的 `contract_sha256`，task id 必须一致。每个 milestone 都必须满足：

```text
activation_rule = episode_start_active
monotonicity = monotonic
stability_duration_s = 0
anti_double_count_rule = first_external_satisfaction_only
```

contract-v4 已验证 milestone 与 `required_outcomes` 一对一，并要求总权重大于零。Stage71 只执行 evaluator 明示的权重；不会调用 `goal_progress_curve` 的等权默认，也不会替真实 benchmark 作者选择权重或 termination domain。

## terminal MCR 重放

`replay_communication_task_performance()` 先调用 `replay_progress_evidence()`，再核对 episode、public task、predicate ids、evaluator ref 与 clock source。只有最后一个 terminal boundary 参与数值计算：

```text
MCR_comm = terminal 为 true 的 milestone 权重和 / 全部 milestone 权重和
```

判定规则：

- eligible termination 且全部正权重 terminal predicate 已知：`computed`；
- 全部已知为 false：真实 `0.0`，不是缺失；
- 任一正权重 terminal predicate 为 unknown：`na_unknown_terminal_predicates`；
- 任一 predicate 出现 `true → false`：`na_monotonicity_violation`；
- `true → null`：同样 N/A，不沿用旧 true；
- termination reason 不在事前 allowlist：`na_ineligible_termination_reason`。

重放块同时固定 mapping digest、v4 seal、episode/public-task identity、progress payload digest、terminal sequence 和 N/A 诊断。`SR_comm`、`AUP_comm`、`e_step` 均保持 null。

## producer、source binding 与 verifier

manifest v2 生成 row version 2，并在 `condition_metrics.task_performance` 保存重放块；source bindings 新增：

- `task_performance_mapping_sha256`
- `frozen_contract_v4_seal_sha256`
- `task_performance_replay_sha256`

Stage69 source validator 校验重放块的 closed schema、identity、状态/数值一致性、release flags 与 canonical digest。Stage70/71 只读 verifier 仍从磁盘 public task、progress report 和 frozen manifest 独立重算整行；它不信任 row 自报的 M、status 或摘要。

完整且 `computed` 的 v2 行只可成为 `accepted_raw_only`。合法 N/A 行为 `diagnostic_only`。任何 manifest、mapping、seal、原始 progress、stored replay 或 row 篡改均失败关闭；来源失败会清空整块 `condition_metrics`，避免保留旧 M、`B_offer` 或 `B_link`。

## 向后兼容与保留边界

- manifest v1 继续要求 row version 1，且不得携带 mapping；
- manifest v2 要求 row version 2 与完整 mapping；
- v1 继续返回 `na_not_frozen_stage70_no_inference`；
- 不修改 `goal_metric_aggregation.py`，不生成五实验族产品网格或跨实例统计；
- offline tests、source closure 与 `accepted_raw_only` 都不是 native、business 或正式评分证据。

Stage71 完成后仍保持：

- `communication_adaptation = na_dimension_source_not_wired`
- `dynamic_reconfiguration = na_dimension_source_not_wired`
- `cross_episode_learning = na_dimension_source_not_wired`
- `release=false`
- `scoreable=false`
- `execution_validated=false`
- `business_outcomes_validated=false`

未运行 Isaac/GPU/native 五实验族矩阵，未验证真实业务结果，也未获得发布资格。
