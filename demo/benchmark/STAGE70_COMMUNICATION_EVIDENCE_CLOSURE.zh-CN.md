# Stage70 通信矩阵磁盘来源闭包

日期：2026-09-26
基线：`0db4339`（Stage69 `cc6ec50` 的文档后继）
状态：raw-only 磁盘 producer/verifier；未接入正式通信评分

## 目标

Stage69 已能严格验证调用方提供的 expected manifest 与 observed rows，但格式正确的摘要本身不等于真实 episode 来源。Stage70 新增 `goal_communication_evidence.py`，补上 evaluator-owned 的两阶段磁盘闭包：

1. episode 运行前以 create-once 语义冻结 `communication_expected_manifest.json`；
2. 现有 `FormalGoalEpisodeRunner` 正常写完一个 episode；
3. evaluator 在 `runner.run()` 返回后，从磁盘文件生成一个 source row；
4. 纯只读 verifier 重新读取并重算每个 row，再调用 Stage69 validator。

Stage70 不创建第二个通信 runtime、不修改 runner 生命周期、不生成五实验族的产品网格，也不调用正式六维聚合器。

## 事前 manifest

manifest 延续 Stage69 主键：

```text
task_id × base_instance_id × family × condition_id × channel_seed
```

Stage70 为每个 cell 额外冻结：

- 唯一 `episode_id`；
- 位于 matrix root 的 `episodes/` 下、禁止绝对路径和 `..` 的 `episode_relpath`；
- 版本化 `condition_spec` 及其 canonical SHA-256；
- shared transport 的 `initial_contract` 与预期 `final_contract`；
- 去除 ledger `sequence` 后的、有序 `condition_events`，只包含
  `channel_condition_changed` / `channel_condition_change_rejected` 事件。

首次冻结只允许空 matrix root。已有 manifest 仅允许字节完全相同的幂等确认；不同内容、已有运行产物后补 manifest、重复 episode id/path、条件摘要不一致全部拒绝。observed row 永远不能扩展 expected cells。

## episode 后 producer

`build_episode_source_row()` 只读取 runner 已完成的文件：

- `episode_manifest.json`
- `public_task.json`
- `goal_session_report.json`
- `communication_transport_report.json`
- `communication_transport_replay.json`
- `communication_tool_report.json`
- `goal_progress_evidence.json`
- `goal_progress_replay.json`
- `result.json`
- `scoring_adapter_metadata.json`

producer 会重新运行 shared transport replay 与 progress replay，核对 task/episode/public-task identity，并使用仓库 `build_archive_index()` 重建完整已知 archive index；stored index 的 files、counts、字节数和 SHA-256 必须与重建结果完全一致。`communication_tool_report.json` 的 profile 必须具有正确 schema/version，其 `communication` 子报告必须与 shared transport report canonical 相同；session 的 sequence-0 `episode_started.public_task_digest` 也必须绑定同一 public task。endpoint/pool binding 由 tool profile 的 endpoints/directions 与 shared report 的 initial pool contract 构造。row 内的 communication/progress/archive/endpoint 摘要均由磁盘内容重算，不接受调用方传入的摘要或 `native=true`。

实际 report 的 initial/final contract 及 sequence-free condition event 列表必须与 frozen condition spec exact-match。这样 `family/condition_id` 不只是标签：未施加、额外施加或施加顺序不同的通信条件都会失败关闭。

`write_episode_source_row()` 在写入前再次从磁盘重建 row；文件名为完整 expected key 的 canonical digest，并使用 exclusive-create。相同 key 的第二次写入即 duplicate，不能以“内容相同”为由覆盖或去重。

## 独立只读 verifier

`verify_communication_evidence_archive()`：

- 不创建、补写或修复任何文件；
- 拒绝 symlink、越界 episode path、未知 row entry 和非 canonical row 文件名；
- 从 frozen cell 指定的 episode path 重建 row；
- 对 row 与重建结果做 canonical 对比；
- 将缺失、篡改、unexpected、duplicate 或 forged row 强制留在 rejected/missing 账本；
- 最后调用 `build_communication_source_matrix()`，保留 Stage69 的 raw-only 状态和稳定 reason codes。

shared transport report/replay 缺失或失败时不回退到 pose/camera/tool report 求和。完整可重放的零消息运行保留真实 `B_offer=0/B_link=0`；报告缺失则来源拒绝，不能解释成零成本。

## task performance 边界

Stage70 会从真实 shared transport report 重算 transport companion 与通信成本，但不会把可撤销、边界采样的 progress evidence 猜成单调 milestone curve。由于全局 milestone→metric 转换、权重和撤销语义尚未事前冻结，`communication_condition_metrics()` 的 `progress_inputs` 明确为 `None`：

```text
task_performance_mapping_status = na_not_frozen_stage70_no_inference
```

因此来源完整的 Stage70 row 仍为 `diagnostic_only`，不是 `accepted_raw_only` 任务表现样本。synthetic delivery 不会回填 M；`SR_comm=Q(H)` 仍为 proxy-only，不能成为正式 Y_i。

## API

```python
from demo.benchmark.goal_communication_evidence import (
    freeze_expected_manifest,
    build_episode_source_row,
    write_episode_source_row,
    verify_communication_evidence_archive,
)

freeze_expected_manifest(matrix_root, manifest)
result = runner.run(initial_state=initial_state)
row = build_episode_source_row(matrix_root, episode_root, expected_cell)
write_episode_source_row(matrix_root, row)
verification = verify_communication_evidence_archive(matrix_root)
```

## 验收边界

聚焦测试使用真实 `CommunicationRuntime`，并包含一次实际 `FormalGoalEpisodeRunner` 的磁盘归档。覆盖 create-once、实际 runner happy path、零消息、缺报告、transport/progress replay 伪造、endpoint/profile 缺失、archive index 篡改、封存后源文件篡改、duplicate/unexpected row、伪造 release 标志、路径绑定以及 verifier 重复读取不改磁盘。

Stage70 完成后仍保持：

- `communication_adaptation = na_dimension_source_not_wired`
- `dynamic_reconfiguration = na_dimension_source_not_wired`
- `cross_episode_learning = na_dimension_source_not_wired`
- `release=false`
- `scoreable=false`
- `execution_validated=false`
- `business_outcomes_validated=false`

未运行新的 Isaac/GPU/native 五实验族矩阵；单测通过数不能解释为真实实验完成、业务验收或发布资格。
