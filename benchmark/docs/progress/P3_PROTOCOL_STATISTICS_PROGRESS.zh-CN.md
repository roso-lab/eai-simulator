# P3 协议—统计贯通阶段报告

日期：2026-10-21。论文基准：Overleaf `75ceff6f55202b6b1a31b2e2e0f6de56023940fd`。
本阶段只修改 `/home/pupil/HeteroCoord` 的协议记录、统计入口和测试，没有修改论文，
没有把软件结果升级为物理资格。

## 2026-10-21 第二轮补强与反思

本轮把两个此前可伪造的入口收紧到了可验证链路。paired attempt receipt 现在必须携带并校验完整 registration digest、checkpoint、random stream、controller substream、原始记录映射、每个 branch 的 result digest 及自身不可变摘要；scheduled run 转换必须使用发行该 receipt 的 append-only ledger，并且 `valid-pair` 也保留 paired block/attempt provenance。D3 `RelationRunV3` 不再接受任意非空 ledger digest，而要求 canonical obligation records、合法 SHA-256、共享 run identity 和由记录推导出的 dispositions 全部一致。

D2 的公开 `identification_interval(d2=True)` 已重定向到论文 `75ceff6` 的有限样本 retention-set 路径；unknown endpoint 现在保留自己的 `alpha`、`family_size` 和 retention bands，便于审计，不再使用旧 bootstrap/`point=100` 兜底语义。

本轮验收为聚焦测试 `79 passed`、benchmark 全量 `475 passed`。这些结果证明软件协议链的伪造入口已被拒绝，不证明 Tool production paired rerun、真实 contention/stop、native full-state replay 或正式物理资格。`rankable`、`formal_measured_qualified`、`native_full_state_qualified`、`process_isolation_qualified`、`physical_qualified` 继续保持 false。

阶段反思：摘要字段非空不能替代来源绑定；旧测试 fixture 也会把这一缺口掩盖成绿灯，因此 D3 release fixture 已改为从真实 obligation records 派生。下一阶段应把相同的来源绑定原则扩展到 P4 certified catalog、calibration 和 baseline manifest，不能把当前统计绿灯升级成实验资格。

## 本阶段修复的三条断链

### 1. Paired rerun 从 receipt 进入统计目录

此前 `unknown-record` 和 `pair-invalid` 只存在于进程内 retry receipt，统计入口只认识
`infrastructure_unknown`，因此论文中新增加的 paired-block 结论无法持久化到正式输入。

现在 attempt receipt 携带 block、attempt、完整 branch 集合和 disposition，可严格 JSON
往返。统计运行记录可以从该 receipt 按 branch 构造，保留 paired block/attempt 身份，
并分别统计 `unknown-record` 与 `pair-invalid`。两者进入固定权重端点范围，不会被改写成
method failure；method failure 仍是已观测方法结果。

该桥只解决 schema 和统计消费。真实 collector/executor 是否完整运行 paired block、是否
保留原记录和是否遵守 retry cap，仍须由 Tool 和正式 task driver 的执行收据证明。

### 2. D3 obligation disposition 不再丢失

此前 UNKNOWN obligation 会被默认标成 `unreached`；关系统计只保存一个数值映射，
`violated` 与 `unreached` 虽然都计零，却无法再审计失败性质。

现在 obligation ingress 强制检查：obligation ID、P2/P3/P4 relation、非空且唯一的
participants、冻结 opportunity、tick 顺序、P3 subtype，以及 violation 的外部证据。
`completed`、`violated`、`unreached`、`unknown`、`incomplete` 互斥；unknown/incomplete
不得携带完成或违反声明。Identity 和 obligation 都有严格 JSON reload，直接把未验证的
identity dictionary 塞入记录会被拒绝。

obligation ledger 聚合为 relation run 时，同时保存每个 relation 的 disposition 列表和
整个 ledger digest；有 unknown/incomplete 的 relation 数值保持 `None`，由固定权重两端
处理。正式 D3 statistical evidence 必须声明所有运行都绑定 obligation ledger。

### 3. D2 unknown 与有限样本区间

此前 D2 在 instance 已经被压成 `None` 后才补 0/1，会抹掉同一实例另外两个已观测
repeat；高端 reference 消失时又只把最终 point 改成 100，没有改变 reference rate 或
区间。主区间仍使用旧的 cluster percentile，与 `75ceff6` 的有限样本规则冲突。

现在 unknown 先在每条 scheduled repeat 上按端点完成，再计算三次 repeat 均值。低端把
unknown reference 置 1、restricted 置 0；高端反向完成，若 reference rate 消失，则只把
固定权重最小的 unknown reference 置 1，并用稳定 run identity 处理同权情况。报告单独
保留“某个完成会使 reference 为零”的状态。

无 unknown 的 D2 使用论文定义的有限样本 retention set：每个容量层计算 Hoeffding
集合和指数型 contrast 集合的交集，再通过单调截断映射到 0–100 分数区间。含 unknown
的 retention 使用极端完成率上的 Hoeffding 端点；不含 unknown 的 retention 保留完整
有限样本集合。candidate–reference 差值按六个 retention 分配误差，并由两个系统的
分数区间形成保守差值区间。

## 验收

聚焦测试：

```text
test_protocol_v3.py
test_analysis_v3.py
test_statistics_v3.py
test_relations_v3.py
test_release_v3.py
78 passed
```

完整 benchmark 回归：

```text
benchmark/tests
474 passed
```

`py_compile` 和 `git diff --check` 均通过。新增反例覆盖：unknown obligation JSON 往返、
violated/unreached 审计差异、paired receipt 到 scheduled run、unknown repeat 不覆盖已知
repeat、不等权 strata 下最小权重 reference 例外、unknown D2 报告通过 statistical
evidence gate，以及 D2 有限样本结果不依赖 bootstrap seed/resample count。

## 阶段反思与资格边界

这一阶段暴露出“局部对象正确”并不能证明协议已经贯通：receipt 若不能被下游消费，
其 disposition 实际会丢失；D3 若只保留零分，违反和未到达会变成同一事实；D2 若在
instance 聚合后才补 unknown，会悄悄改变已观测 repeat 的权重。

当前修复证明 schema、固定权重和统计构造在软件层闭环。它不证明真实 paired branches
已经运行，不证明 obligation 事件来自可信物理 evaluator，也不提供正式 D2 样本、目录、
校准或 baseline。所有报告继续固定 `physical_qualified=false`，P4/P5 仍未完成。
