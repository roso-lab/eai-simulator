# 地图到位后的首个真实 case 执行手册（2026-10-27）

## 目的与边界

本手册把“完整语义地图交付后，可以直接跑出论文所需数据”的前提压缩为一条可复核的执行链。它只描述工程接口和证据顺序，不修改论文定义，也不把 synthetic/CPU dry-run/GPU 诊断升级为正式论文证据。正式资格门在完整矩阵、真实 evaluator receipt、固定 panel 支持和物理证据齐全前保持关闭：

```json
{
  "rankable": false,
  "paper_score_eligible": false,
  "physical_qualified": false,
  "process_isolation_qualified": false,
  "native_full_state_qualified": false
}
```

## 当前已具备的可部署接缝

- 论文固定 D1–D6 估计、面板投影、导出和 replay：`benchmark/results_pipeline_v3.py`、`benchmark/paper_panel_v1.py`、`benchmark/paper_projection_v1.py`、`benchmark/production_results_v1.py`。
- 原始 receipt 生产和证据桥：`benchmark/receipt_producer_v1.py`、`benchmark/receipt_bridge_v1.py`。
- room_06 × `liv-t-04` 任务特定 evaluator：`benchmark/evaluator/room06_livt04.py`；它只接受显式 `episode/streams/base_facts/oracle_obligation_binding`，不会从进程退出、完成标志或 caller score 推导科学事实。
- 合同/目录 canonical artifact：`artifacts/catalog/CANONICAL_CONTRACT_ARTIFACT.v1.json`，当前绑定 30 个 baseline + `liv-t-04` extension，共 31 个 catalog 条目；状态为 `bound_pending_replay`，不表示资格通过。
- 诊断 synthetic E2E：`benchmark/tests/test_production_results_v1.py` 与 `artifacts/results-pipeline-20261027-synthetic-v2/` 已覆盖 receipt → fixed panel → D1–D6 → export/replay，但 `scope=synthetic_diagnostic_fixture`，不得用于论文结果。
- EMOS 已具备 `initialize(H_r, o_r)`、预算计数和可计量 model endpoint 接口；决策只进入执行边界，不产生物理成功事实。

## 地图交付后的唯一顺序

### 1. 冻结并审计地图输入

只读确认协作者写入已结束，记录最终 `save_id` 链、`project.current_revision`、manifest/footprints/topology/semantic digest 和文件 SHA-256。不要把 `room_06/current` 的活动目录直接当正式证据；必须引用最终保存快照。

使用 `case_input_audit_v2.1` 重新检查：命名点非空且唯一、导航绑定非空、12 个交互点齐全、拓扑摘要一致、四层空按 `liv-t-04` 豁免规则处理；不检查已取消的 `robot_validated`/`isaac_binding`。

### 2. 重建 room_06 case manifest

以最终地图 digest、已经裁决的 27/27 roster digest、`liv-t-04` 合同 digest 和 `HORIZON_TICKS=15000` 重新生成 manifest。物体身份使用任务级 `book_batch_01` 与 `heavy_object`；本场景重物由任务脚本生成时，不执行 USD prim 提取。manifest digest 变化是预期的，旧 diagnostic manifest 不可复用。

### 3. 执行 EMOS contract dry-run

先调用 `initialize(H_r, o_r)`，确认历史重建只更新登记字段、机器人 ID 与 manifest roster 完全一致、model endpoint 调用次数不超过预算；再生成 decision。该步骤只验证输入/接口/预算，不写入 evaluator success、D1–D6 或物理证据。

### 4. 运行真实 evaluator 并封存 terminal material

真实 Isaac/EAI runtime 必须向 `Room06LivT04Evaluator.collect_receipt_material()` 提供：

```text
episode
streams.milestones
streams.task_events
streams.hold_intervals
streams.identity_records
streams.zone_intervals = []
streams.integrity_confirmed
base_facts.d1/base_task_success/d4_items/d6
plan.oracle_obligation_binding
```

联合持有来自 `held_object/技能会话` 记录，不是物理接触区间；终局 deadline、交接拓扑节点和成功条件由 oracle/任务 evaluator 共同 fail-closed 判定。缺事件、篡改证据、非终局状态或缺 D6 calibration 时不得合成 receipt。

### 5. 通过 receipt bridge 与 case readiness

按 `receipt_producer_v1 → receipt_bridge_v1 → case_readiness_v1` 顺序执行。每个 receipt 必须绑定 case/catalog/plan/parameters digest 和内容寻址 evidence artifact。只接受 evaluator-owned streams/facts；不得使用 simulator exit、`completed`、mock score 或锁服务 grant/deny 替代事实。

### 6. 进入论文固定目录并导出

首个真实 case 先做纵向闭环，状态只能是 `measured_unqualified`。论文固定面板需要单独准备：

- D2：C0–C3 条件目录和 repeats；
- D3：P2、P3-J、P3-S、P4 关系目录；
- D4：healthy/loss/recovery closure；
- D5：六方向、12 common cells、family floor；
- D6：每个 instance 的独立 calibration 与运行行。

完成目录后调用 `production_results_v1.run_production_case_from_artifacts()`，然后 `verify_export()`；输出须包含 input/artifact/panel/projection digest、CSV/Markdown/JSON 和 replay 可重建证据。

## 明天仍然不应误报为“已完成”的事项

1. 最终语义地图尚未冻结；当前 `7f44a187` 仅是诊断快照。
2. 尚无真实 Isaac/EAI terminal evaluator artifact/raw receipt。
3. D2–D6 论文固定目录、真实 repeats 和独立 D6 calibration 尚未齐全。
4. GPU A/B/C native 三路证据尚未形成；GPU 直接使用，不因 ToDesk 阻断，但 CPU fallback 或 device readback 不一致只能保留为诊断证据。
5. 任何 synthetic 输出（包括 `artifacts/results-pipeline-20261027-synthetic-v2/`）均不得进入论文表格。

## 首个 case 的停止条件

- 地图 save-id/digest 链不一致：停止 manifest 重建并保留审计失败证据。
- evaluator material 不完整：停止 receipt 生成并报告缺失字段。
- artifact hash/ref 不可验证：停止结果计算，不静默重跑。
- 固定 panel 支持不足：可以导出 diagnostic case report，但不得生成 paper-score eligible 结论。

## 交付判据

只有当“最终地图 → 重建 manifest → 真实 evaluator terminal artifact → raw receipt → oracle/obligation → fixed panel → D1–D6 → export/replay”整链均有可复核文件和 digest，且完整矩阵满足论文分母/重复/校准要求，才允许重新评估正式资格门。本手册本身不改变任何 gate。
