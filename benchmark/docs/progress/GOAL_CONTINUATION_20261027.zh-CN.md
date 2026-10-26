# Goal 持续推进与论文数据就绪计划（2026-10-27）

## 当前结论

HeteroCoord 的 CPU 合同、oracle、收据桥、D1–D6 计算、固定 panel、导出与 replay
接缝已经具备可部署形态；输入审计和 room_06 单场景 manifest 也已闭合。现阶段
仍不能称为论文正式结果，原因是 evaluator-owned 的真实终局 receipt、论文要求的
固定支持目录和真实 GPU 物理证据尚未同时到位。`physical_qualified`、`rankable`、
`paper_score_eligible` 继续保持 `false`。

## 已验收证据

- 接受地图快照 `7f44a187`：`audit_semantic_map.case_ready=True`，场景键
  `living_room_06`，12 个语义地图条目、四个书架语义点、save-id 链和拓扑通过；
- promoted robot roster：27/27 身份绑定、27/27 runtime qualified，pool digest
  `33d92468c48bd1631fdc034d2d4fb01858294bce573308b1b7dd6542062836f4`；
- room_06 × liv-t-04 manifest 重建与既有 artifact 逐字节一致，digest
  `ded55b75f3d6e3d73924e913984e3300fc0dfb5b040aaffb6841514a7cc5fbbb`；
- ZCode 在 `env_isaaclab` 中完成 room_06 重点回归 `55 passed`、五模块 compileall
  exit 0；GPU/Isaac/Kit 未启动；
- GPU 三路 runner 已具备 requested/actual device、physics/session/controller device
  和 GPU UUID 的逐路 readback 与 fail-closed 对账；ToDesk 仅是无关显示服务，不再作为
  公开 benchmark 的普通阻断条件；
- measured executor 已要求 evaluator 显式提供 `collect_receipt_material()`，并将
  合法材料写入内容寻址 evidence artifact；缺 hook、缺材料、篡改或验证失败均不产生
  measured scientific success。

## 明日地图 handoff 后的唯一主线

```text
final semantic-map/current
  → case_input_audit_v2.1
  → 重算 semantic/topology digest
  → 重建 case-manifest.v1
  → EMOS initialize(H_r, o_r) / decision dry-run
  → evaluator terminal receipt + evidence bytes
  → receipt bridge / oracle / obligation reduction
  → fixed panel projection
  → D1–D6（含 per-instance D6 calibration）
  → JSON/CSV/Markdown export
  → verify_export replay
  → 直接 CUDA 诊断与三路 A/B/C 对账
```

其中最后一步只在 receipt 接缝和 manifest 已经绑定后执行；GPU 结果先按 diagnostic
处理，任何一路恢复/设备/物理状态不一致都原样留证，不开启资格门。

## 仍需外部输入或真实运行才能关闭的缺口

1. 协作者最终 `room_06/current` 以及其余场景的语义地图 handoff；当前工作树的旧
   `current` 不能直接覆盖接受快照。
2. evaluator-owned terminal receipt：完整 milestone/event/zone/held-object/
   identity streams、oracle verdict、义务 ledger、D4 closure、D5 attempt 和独立
   D6 calibration 引用，以及可验证 evidence bytes。
3. 论文支持目录：D2 C0–C3、D3 P2/P3-J/P3-S/P4 relation support、D4 paired
   healthy/loss closure、D5 六方向/每 family ≥30 cases、D6 独立 calibration。
4. 真实 CUDA 运行：A 不间断、B 隔离进程恢复、C 共享会话槽位恢复；要求 requested
   device 与实际 physics/session/controller device、GPU UUID 和 checkpoint identity
   一致。CPU/mock/synthetic 结果不替代这一步。

## 工程边界

- 主代理负责论文契约、架构决策、实现、科学验收和阶段收尾；ZCode 负责固定测试、
  编译、批量审计和证据提取，不裁决语义、不修改源码、不启动 GPU；
- 不覆盖协作者地图、不 reset/clean 用户脏工作树、不修改论文正文；
- 只有真实 terminal receipt 和完整统计支持目录闭合后，才讨论正式论文表格或资格。

## 收尾环境与结果链复核

在 `env_isaaclab` 中，`torch=2.7.0+cu128` 与 `lark` 均可导入。论文结果链现有四个
测试文件（`test_production_results_v1.py`、`test_paper_panel_v1.py`、
`test_results_pipeline_v3.py`、`test_emos.py`）共 `48 passed`；五个核心模块
`compileall` exit 0。仓库最新结果报告和历史运行时报告均显示
`physical_qualified=False`、`rankable=False`、`paper_score_eligible=False`。
不存在同名的 `test_statistical_evidence_v3.py`，该缺失已记录为命令入口差异而非代码
失败，未通过重命名或替换测试掩盖。
