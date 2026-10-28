# 论文对齐阶段收口（2026-10-27）

本阶段目标是让完整语义地图到位后可以直接进入 case manifest、终局 evaluator receipt、oracle/义务判定和论文数据导出。正式资格门仍全部关闭。

## 已完成

- catalog 口径已固定为 **30 个 baseline + `liv-t-04` extension = 31 条**；baseline 每域仍为 10 条。
- 旧测试中 4 条硬编码“30 条/首项 P1”断言已改为按 anchor id 与 extension 口径验证。
- 新增 `benchmark/contract_artifact_v1.py`，按内容哈希加载唯一 canonical contract manifest；当前唯一指向：
  `artifacts/catalog/contracts/room06-extension-20261026/pilot_contracts-v3.json`。
- canonical contract manifest：
  `artifacts/catalog/CANONICAL_CONTRACT_ARTIFACT.v1.json`，31 条合同、30 条 baseline、extension=`liv-t-04`，认证与资格均为 false。
- ZCode 复验：主 Python 3.14.7 focused suite **61 passed**；`env_isaaclab` Python 3.11.16 focused suite **7 passed**；compileall 与 `git diff --check` 均 exit 0。

## 当前尚未闭合

1. 最终 room_06 语义地图仍需只读审计并绑定到 manifest；不得把诊断快照当正式地图。
2. 真实 Isaac/EAI runtime 仍需产生 terminal evaluator material、content-addressed artifact、raw receipt 及 `run_production_case_from_artifacts()` 记录。
3. D2 C0–C3、D3 四类 relation、D4 healthy/loss、D5 六方向/12 common cells、D6 独立 per-instance calibration 的固定支持目录仍需随真实 case 逐步建立。
4. 真实 GPU A/B/C 三路 native receipt 尚未产生；本阶段 CPU 测试不升级为资格证据。
5. 当前工作线与 `origin/HeteroCoord` 双向分叉；本阶段窄提交 `11a79256` 已保留，但不直接强推覆盖远端，待按远端最新基线做选择性集成。

## 下一步最短路径

地图交付后：

1. 审计 save-id 链、semantic/topology 文件哈希、12 个语义点、50/63 拓扑和 liv-t-04 四个 bookshelf 点；失败则只留证不绑定。
2. 用接受地图 + 27/27 历史资格名册重建 room_06 × liv-t-04 manifest，冻结 catalog/plan/contract/evaluator digests。
3. 运行一个真实 case，确认 evaluator adapter 输出的 milestones、task_events、logical hold intervals、identity records 和 D1–D6 base facts，生成 terminal material artifact。
4. 通过 receipt bridge → raw receipt → fixed panel/projection → D1–D6 → export/replay；结果保持 `measured_unqualified`。
5. 以该 case 的真实 receipt 作为三路 GPU A/B/C 的唯一后续输入，资格矩阵与正式 gate 另行评估。

## 证据边界

本阶段没有启动 GPU、Isaac、Kit 或 telemetry；没有修改论文正文；没有把 ToDesk 或其他显示服务写入 benchmark 语义；没有把 synthetic/CPU fixture 升格为正式论文数据。
