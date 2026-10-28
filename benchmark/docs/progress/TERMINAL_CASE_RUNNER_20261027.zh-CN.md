# Terminal case runner 接缝阶段记录（2026-10-27）

## 目的

在最终语义地图冻结后，真实 task runtime 只需提供显式 evaluator payload，即可
按固定顺序封存证据并进入论文结果计算链：

```text
Room06 evaluator
  → collect_receipt_material()
  → content-addressed evidence
  → artifact_manifest
  → raw receipt / case readiness
  → run_production_case_from_artifacts()
  → fixed panel / D1–D6 / export / replay
```

实现位于 `benchmark/terminal_case_runner_v1.py`，提供：

- `seal_terminal_artifacts(...)`：对一个或多个 terminal payload 调用任务特定
  evaluator，封存内容寻址 artifact，生成带路径/哈希/大小的 manifest，随后通过
  receipt bridge 生成 raw receipt 并执行 case-readiness 审计；
- `run_production_from_terminals(...)`：在同一份 sealed artifact 输入上调用固定
  panel 生产入口 `run_production_case_from_artifacts(...)`。

## 重要边界

1. `evaluator_result` 必须由运行时显式提供；runner 不从进程退出、`completed`、锁服务
   应答、EMOS decision 或 caller score 推导任何科学事实。
2. evaluator 缺字段、证据哈希/路径变化、case/robot/object 绑定漂移均 fail-closed。
3. 返回的 readiness 和 production report 都保留 `rankable=false`、
   `paper_score_eligible=false`、`physical_qualified=false`；单 case 不能冒充论文矩阵。
4. 当前测试使用 `build_diagnostic_runtime_fixture()`，仅证明接缝；不进入正式结果。

## 验收证据

```text
benchmark/tests/test_terminal_case_runner_v1.py + benchmark/evaluator/test_room06_livt04.py
→ 11 passed
```

最终地图到位后，调用方应使用新地图 digest 重建 case manifest，再把 Isaac/EAI
真实 terminal payload 和 evaluator verdict 传入同一 runner；不得复用诊断 manifest
或 synthetic artifact。
