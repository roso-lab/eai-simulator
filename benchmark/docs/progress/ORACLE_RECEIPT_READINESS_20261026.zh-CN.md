# Oracle → receipt readiness 阶段验收（2026-10-26）

## 阶段结论

本阶段完成了真实语义 case 在 CPU 侧的收据语义闭环：evaluator 原始流先由冻结 oracle 判定，再按冻结 predicate→obligation 映射重算 D3，最后校验 sealed raw receipt。调用方不能靠重新封存伪造的 obligation、success 或 D6 success 绕过重算。

该闭环只证明 **case receipt ready**，不等于论文固定面板就绪。`case_readiness_v1` 将两层显式分开：

- `case_receipt_ready`：case 身份、artifact 字节、receipt envelope、对象/机器人绑定、oracle 成功条件和 obligation 重算都通过；
- `receipt_support_surface_present`：raw receipts 中至少出现登记的 C0–C3、P2/P3-J/P3-S/P4、D4、D5、D6 测量表面；这只是存在性报告，不替代各维度 schema、面板和统计验收；
- `paper_support_ready`：本阶段恒为 false，因为 fixed base-instance/repeat/cell panel package 及其 projection digest 尚未进入本审计入口；
- `rankable`、`paper_score_eligible`、`physical_qualified` 恒为 false。

因此，一份 room_06 × liv-t-04 收据可以证明 oracle/receipt 接缝可执行，但不能冒充论文全局 D1–D6 结果包。

## 冻结语义

1. `contact_count` 保留历史 operator 名称以兼容合同，但 V3 唯一输入为 `hold_intervals`：`held_object` 或联合技能会话的逻辑持有状态，不读取物理接触。
2. `liv-t-04` 的接力先后是普通 P2 obligation；`heavy_carry_joint_hold` 是 safety success；deadline 是 success condition。后两者改变任务成功状态，不进入普通 D3 扣分。
3. `facts.obligations` 必须由冻结 `plan.oracle_obligation_binding` 重算。production case 缺该 binding 立即拒绝。
4. 安全/截止证据缺失时 fail-closed；完整账本证明未到达时收敛为任务不成功，不伪造 obligation 分值。
5. 任务生成对象使用任务级 object id；room_06 的书批/重物无需绑定现有 USD prim。

## 反例与验收证据

ZCode GLM-5.3/max 只读执行，系统 Python 3.14.7：

```text
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
  tools/benchmark_branch/tests/test_receipt_material_oracle_reduction.py
7 passed in 0.27s

PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
  tools/benchmark_branch/tests/test_receipt_material_oracle_reduction.py \
  benchmark/tests/test_receipt_occupant_binding.py \
  benchmark/tests/test_oracle_success_boundary.py \
  tools/benchmark_branch/tests/test_cp2_replay.py
25 passed in 0.31s
```

加入 fixed-panel 缺口显式化与 V3 logical-hold 术语收紧后，第二条命令再次执行：`25 passed in 0.35s`，exit 0。

覆盖的关键反例：

- 修改 obligation 后由 producer 重新封存并生成合法 envelope hash，仍被 oracle 重算拒绝；
- production case 缺冻结 obligation binding 拒绝；
- 单机持有、持有中断、数据缺失、超截止拍均不能产生成功；
- receipt 引用 case 外对象、机器人、区域或 occupancy holder 拒绝；
- 单 case 即使 receipt 合法，也只得到 `case_receipt_ready=true`，`paper_support_ready=false`。

## 尚未完成

1. 真实 evaluator 尚未为 room_06 × liv-t-04 产生第一份 terminal artifact/receipt；当前通过的是生产代码路径的 CPU fixture 验收，不是正式实验数据。
2. 固定 panel package、重复层级、D4 closure、D5 sham/intervention、D6 独立 calibration 的真实支持尚未齐全。
3. 机器人历史证据目前严格定位到 15/27；其余 12 台保持 unknown，不得置 `runtime_qualified=true`。
4. physical/statistical/release 证据未闭合，所有正式门继续 false。

## 发布策略

当前工作树含大量并行协作者和用户脏改动，不能直接整树提交或 pull/rebase。阶段发布必须以最新 `origin/HeteroCoord` 为父，在独立干净 worktree 构造并复验最小运行时依赖闭包，再快进推送到 `HeteroCoord`；不修改当前工作树的既有索引和无关文件。
