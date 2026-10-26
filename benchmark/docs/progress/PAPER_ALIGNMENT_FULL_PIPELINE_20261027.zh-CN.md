# 论文对齐工程阶段记录（2026-10-27）

## 目标

在完整语义地图接入后，能够从一个真实 case 的 manifest 与 evaluator-owned
terminal receipt 直接完成 oracle/义务归约、固定 panel、D1–D6 计算、统计导出和
回放校验。该记录只描述工程 readiness，不把诊断结果升级为论文资格；正式资格门
保持 `false`。

## 本阶段已完成

1. 论文契约审查已完成，确认 Overleaf 六维公式、固定 cell 聚合和导出/replay 的
   CPU 计算链已经存在；缺口集中在真实 evaluator receipt、D5 完整目录和 D6 独立
   calibration。
2. D6 已升级为论文要求的 per-instance calibration schema：
   `calibration[{instance_id, reference_bits, reference_time}]` +
   `runs[{instance_id, success, bits, time}]`。实例缺失、重复、错配、非法参考值均
   fail-closed；旧全局参考值只保留给 `synthetic_diagnostic_fixture`。
3. `receipt_adapter_v3` 已接通同一 per-instance calibration。旧 global fixture
   仍可走诊断路径；真实 measured 输入必须提供实例表。
4. EMOS 零号适配器增加 `initialize(H_r, o_r)`、预算计量、严格 robot ID 对齐、可选
   model endpoint 和 `update_observation`。其输出仍仅为 typed decision，不写物理成功。
5. 历史机器人证据登记已生成 27/27 promoted roster，formal gates 仍全 false：
   `artifacts/robot-runtime-historical-20261027-r3/`。
6. 已用提交 `7f44a187` 的 room_06 地图快照完成 v2.1 审计和 case manifest 组装，
   物体按任务级 ID 绑定（`book_batch_01`、`heavy_object`），不绑定 USD prim：
   `artifacts/case-room06-livt04-20261027/`。该快照审计为 12 个语义点、50 个
   ground 节点、63 条边、`liv-t-04` horizon=15000；当前工作树的 room_06 `current`
   目录仍是协作者写入中的不完整版本，不覆盖它。

## 仍未完成

- 真实 evaluator 终局 receipt/artifact bytes 尚未产生；当前 case manifest 是输入
  接缝验收，不是实验结果。
- room_06 完整地图交付后必须重新审计并更新 semantic/topology digest，再重建 manifest。
- `liv-t-04` 的真实终局事件、交接节点、联合持有区间、deadline、identity 和
  obligation ledger 尚未由运行时产生。
- D2 的 C0–C3、D3 的 P2/P3-J/P3-S/P4、D4 paired closure、D5 六方向/12 common
  cells 和 30-case family floor 仍未满足；单 case 只能标 `measured_unqualified`。
- GPU 诊断线仍有 native step-counter/readback 不一致及 physics CPU fallback，暂不
  进入论文物理证据或正式资格链。

## 明日地图到位后的执行顺序

```text
map + roster
  → case_input_audit_v2.1
  → case_manifest (重新绑定 digest)
  → EMOS initialize/decision dry-run
  → evaluator terminal receipt + content-addressed evidence
  → receipt_bridge / oracle / obligation reduction
  → fixed panel projection
  → D1–D6 (per-instance D6 calibration)
  → JSON/CSV/Markdown export
  → verify_export replay
```

首个真实 case 只做纵向闭环和证据审计；成功后再扩展三次 repeat、C0–C3 和论文目录。
任何缺事件、缺 artifact bytes、缺校准实例或地图 digest 变化都保持 fail-closed。

## 持续推进计划（2026-10-27，Goal active）

### A. 地图到论文数据的主线（明日地图交付即执行）

1. 只读确认协作者的 `current` 写入已结束，记录最终 `save_id` 链和地图文件
   SHA-256；不复用本记录中的 `7f44a187` 诊断快照。
2. 用 `case_input_audit_v2.1` 复审语义点命名/导航绑定、拓扑和四层豁免规则；
   失败项原样归档，不靠 manifest digest 掩盖。
3. 用同一份 binding 重建 `case-manifest.json`，再执行 EMOS
   `initialize(H_r, o_r) → decision` CPU dry-run。该步骤只验证接口和预算，不产
   生物理成功事实。
4. 从真实 evaluator 读取 terminal material，写入内容寻址 evidence artifact；
   receipt producer/bridge 只接受 evaluator-owned streams/facts，不能用进程退出、
   assignment 完成或 mock score 代替。
5. 先跑 case readiness + oracle/obligation reduction，再跑固定 panel projection、
   D1–D6、export/replay。所有输出必须带 input/artifact/projection digest。

生产编排接缝已补齐：`benchmark.production_results_v1` 支持
`panel_endpoint_receipts`，允许固定 panel 的每个 endpoint 绑定自己的 evaluator
receipt 序列。D2 的 C0–C3 条件目录与 D5 的单 cell / 单 base instance / 三
repeats 因而可以来自不同目录，不再被迫共用同一批 receipt。缺 endpoint、未知
endpoint 或非序列输入仍 fail-closed；CLI 对应
`--panel-endpoint-receipts`（JSON 对象 `endpoint -> receipt[]`）。这只解决生产
编排，不改变论文估计量、oracle 语义或 formal gates；输出仍为
`measured_unqualified` 且三项 gate 全为 `false`。

### B. GPU 线（与论文 CPU 数据链解耦）

GPU 运行按论文方法直接请求真实 CUDA 设备。ToDesk 或其他显示共驻进程不再作为
普通运行的阻断条件，也不执行针对 ToDesk 的 kill；遥测清理仍只针对
`omni.telemetry.transmitter`。在任何资格结论前只保留一个必要检查：evaluator receipt
中的 `requested_device` 与实际 physics/device readback 必须一致。若 Isaac 仍回退 CPU，
该运行保留为诊断证据，不能进入论文物理结果。

### C. 当前并行工作

- `production_results_e2e_contract`：Sol 正在补一条小型 evaluator-owned synthetic
  E2E 回归，覆盖 receipt → panel → D1–D6 → export/replay；它用于验证工程接缝，
  不升级正式数据。
- ZCode：只读执行论文数据链专项回归、编译和 artifact 存在性检查；不改代码、不启动
  GPU。
- 主线程：负责地图 handoff、EMOS case dry-run、真实 evaluator receipt 接口和最终
  科学边界审查。

### D. 停止/升级条件

- 地图未完成或 `save_id` 链不一致：停止 manifest 重建，保留失败证据。
- evaluator 没有显式 terminal/oracle/obligation/D5/D6 material：不生成 measured
  结果，改报缺口。
- 任何路径需要修改论文正文或覆盖协作者目录：停止并记录，不自行扩大范围。
- 正式 gates (`physical_qualified`, `rankable`, `paper_score_eligible`) 在完整矩阵、
  真实设备和统计支持齐备前始终为 `false`。
