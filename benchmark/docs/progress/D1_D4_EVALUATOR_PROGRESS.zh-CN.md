# D1/D4 论文评价层推进报告

日期：2026-10-21。论文基线：Overleaf 提交 `75ceff6f55202b6b1a31b2e2e0f6de56023940fd`。

## 本阶段完成的工程能力

### D1：首次提交计划与双可行性支持

`benchmark/organization_v3.py` 建立了一条独立评价路径。它先冻结每个实例所属的统计单元、可部署或团队状态不可行类别、证书引用、公开物理模型摘要、提交截止时间、评价截止时间、适用的语义检查和三个重复键，然后读取外部评价器的事件日志。

对可部署实例，评价器只接受截止时间前的第一个 `commit` 事件。内部候选和之后的重规划不会改写这个记录；首个提交不可解析时，组织分数和完整性分数都为 0。每个语义检查只计一次，合法替代方案采用“任意一个满足即可”，因此新增子任务或重复分配不能人为增加分母。检查缺少独立观测时保留为 unknown；明确的遗漏必须由评价器写出 false，不能用“没有日志”代替。

对团队状态不可行实例，只有评价截止时仍生效的 `not_deployable` 声明、未发生 dispatch、且有验证过的 safe-stop 才记为正确拒绝。缺少声明、声明格式错误或已经 dispatch 后才停止都不记正例。`feasibility_unverified` 不进入两类支持。

报告按“重复 → 实例 → 固定 cell”聚合，分别保留可部署和不可行支持。每个类别、每个 cell 都要求至少 30 个实例和至少 80% 的基础设施及可评价覆盖率；低于门槛时返回 `NA`，不重新分配 cell 权重，但仍报告已测组件和排除漏斗。类别贡献严格加和到 `30 Qorg`，且 `V <= Qorg`。D1 的 bootstrap 同时保留 cell、可行性类别和共享生成流的聚类结构。

### D4：注册时间价值与连续服务

`benchmark/dynamic_value_v3.py` 将注册规则和外部时间线分开。注册项冻结 deadline、时间价值密度、连续服务需求区间、稳定窗口、结果集合与权重，并固定论文中的 `beta=1/2`。非均匀密度必须带任务目的，密度在整个注册窗口上积分为 1。

时间线逐个记录结果合法性、服务状态、终止事件及 operation/feasibility/progress 证据摘要。重复结果不重复计分；结果失效后不会继续贡献时间价值；观察窗口不足或状态未知时返回 `incomplete`。连续服务始终使用完整注册需求区间作分母，提前停止不会缩短需求；最终稳定窗口必须在需求末端成立，确认时刻取 onset 之后第一个完整稳定窗口的终点。离散工作只记录首个有效受影响里程碑。

同一 tick 的终止顺序固定为 `fail > certified exogenous infeas > safe > resume`。策略自身耗尽时间会被改码为 fail；未认证的不可行或 safe-stop 使结果 incomplete，但原始事件仍保留在收据中。`dynamic_v3.py` 把这些评价结果接入现有受影响工作闭包；注册项必须覆盖闭包，所有 item 使用共同 onset/window，缺失 timeline 不填成零。

## 验收证据

聚焦回归：

```text
benchmark/tests/test_organization_v3.py
benchmark/tests/test_dynamic_value_v3.py
benchmark/tests/test_dynamic_v3.py
benchmark/tests/test_analysis_v3.py
benchmark/tests/test_release_v3.py
benchmark/tests/test_current_paper_binding_v3.py
87 passed in 0.89s
```

ZCode GLM-5.3/max 按既定只读任务重新运行 D1 测试：27 passed，`git diff --check` 通过。主工程联合回归为 642 passed；`py_compile` 与 `git diff --check` 通过。命令行从保存的 D1 输入重新计算报告，与直接生成的 `D1_report.json` 字节一致。最终源码清单与 D1/D4 synthetic fixture 位于 `artifacts/p0-p5/acceptance-d1-d4-final/`，明确标记为工程验收夹具，不能当作论文数据。该目录的 D4 主统计报告仍使用归一化夹具，额外的 `D4_timeline_example.json` 才展示本轮原始时间线计算；它不是物理配对实验。

## 阶段反思

本阶段最初按旧稿把 D2–D4 的残余 unknown 记为 incomplete；该结论已被最新
`75ceff6` 取代。当前规则是六个维度都保留固定权重 identification range，D2 另用
保持 reference rate 约束的极端完成与有限样本 retention set。D1 的可行性类别和 cell
覆盖门仍是独立的发布条件。最新实现和反例见 `P3_PROTOCOL_STATISTICS_PROGRESS.zh-CN.md`。

第二个修正是把“评价器给出的数值”与“物理事实已被可信验证”分开。当前入口验证结构、身份和证据摘要的一致性，不能代替 Isaac 物理 oracle、独立校准、共同起点或真实场景执行，因此 `physical_qualified` 和 `publication_ready` 固定为 false。

仍未完成的工作包括：认证实例目录、完整共同 checkpoint 与可信恢复、真实 D1 七类检查器、D4 物理服务证据、独立容量/资源校准、认证 baseline、预埋故障和留出集归因。这些缺口由正式 release 的 blockers 继续暴露。

Sol 队友的只读交叉审查见 `workspaces/heterocoord-p0-p5-20261020/handoffs/d1-d4-review.md`，结论是没有发现本轮新增的阻断性缺陷。主代理核对并纠正交接中的两处措辞：D1 选择的是第一个提交，然后检查可解析性；不会跳过不可解析的第一次提交。未认证终止事件会以 unknown 优先显示，结果整体为 incomplete，所有原始事件保留；该显示规则不参与正式排名。
