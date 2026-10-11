# 实验分析基础设施开发清单

范围：通用任务运行、collector、外部 evaluator 与统计计算之间的接口；不创建正式
任务或第二套 D1–D6 注册表。实验场景、在线论文快照、模拟数据与报告只写 `tmp/`。

审查基线：EAI `aba946e0`；2026-10-11 读取 Overleaf 项目
`6ab4c7c4e984abb8da9c8113`，history 最新版本 773。主要依据
`sec/5_evaluation_protocol.tex`、`sec/X_suppl.tex`、`sec/supp_coverage.tex`。
论文 Validation and Results 仍为 `xxx` 占位，不能当作实测证据。

## A：版本与接口审查

- [x] 隔离开发工作区，保留原工程和正式 benchmark 工程的未提交修改。
- [x] 核实在线论文，与本地旧分析测试分开；旧测试导入的 `demo.benchmark` 已不存在。
- [x] 完成 runtime → collector → evaluator → analysis 所有权和缺口审计。
- [x] 明确 base instance / repeat / attempt、cell、family、condition 与 branch 身份。

## B：可独立运行的结果计算层

- [x] 严格、版本化的分析计划和外部结果数据接口，哈希绑定与来源隔离。
- [x] 冻结 planned/infra/support 集，按实例内 repeat → cell → 固定 panel 聚合。
- [x] 精确配对与共同 repeat 支持；panel means 的比值、cell ratios 的面板、线性对比。
- [x] Aalen–Johansen 竞争风险与 RMST；不将 competing event 当 censor。
- [x] 配对迁移 family 均值、固定归一化、family cluster bootstrap。
- [x] cluster bootstrap 保留全部 repeats/branches；缺失 cell 不重归一化。
- [x] Holm 家族校正、NI 和同时带/网格 crossing 数学工具与限制说明。

## C：接线、模拟数据与反例验收

- [x] collector 只读证据交接与明确的外部 evaluator 输入边界。
- [x] CLI：校验 → 计算 → 可审计 JSON/CSV/Markdown；不接受发布资格自声明。
- [x] 固定 seed 的模拟数据生成器；运行数据不纳入 Git。
- [x] 手算对照、错配/缺失/伪重复/零分母/删失/篡改回归。
- [x] 多 cell、多系统、多条件、多 family 端到端验收与确定性检查。
- [x] 独立审查、相关框架回归、文档和接入交接。

A–C 验收：341 passed、1 skipped（未安装的历史 demo 场景插件）；独立审查未遗留
P0/P1。模拟矩阵 3,984 条记录、19 项结果与解析答案一致；运行数据与复算报告在
`tmp/experiment_analysis/verified-controls` 和 `recomputed-controls`。详见
[验收记录](VALIDATION.md)。这些数字只描述软件/公式验收，不是机器人实测结果。

## 正式任务线程提供后才能完成的实验认证

- [ ] 正式任务合同、物理 evaluator、生成前冻结的 oracle 与 relation 支持集。
- [ ] benchmark 侧 base-instance 身份与 release/reducer 接口修正及接入。
- [ ] 严格 replay/sham 证书、transfer family 证书、注册时间与隐藏集审计。
- [ ] 真实运行矩阵、六主指标注册与候选/参考检验方案、正式推断发布。

最后一组不阻碍前面数学和架构开发；计算通过不等于物理成功、科学有效性或论文结果通过。
