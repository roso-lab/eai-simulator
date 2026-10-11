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

在后续正式接入前，通用采集与结果包基础设施已继续推进 D 阶段，见下方。

- [ ] 正式任务合同、物理 evaluator、生成前冻结的 oracle 与 relation 支持集。
- [ ] benchmark 侧 base-instance 身份与 release/reducer 接口修正及接入。
- [ ] 严格 replay/sham 证书、transfer family 证书、注册时间与隐藏集审计。
- [ ] 真实运行矩阵、六主指标注册与候选/参考检验方案、正式推断发布。

最后一组不阻碍前面数学和架构开发；计算通过不等于物理成功、科学有效性或论文结果通过。

## D：多批次采集、登记重试与离线复核

- [x] 从 Design 精确枚举观测，冻结全部批次计划、评估器和有序尝试链。
- [x] 核对任务/代码/检查点/场景/资产/skill/RNG/slot/pair group/metadata，禁止跨版本补成功。
- [x] 重试同时核对 sealed episode 原始基础设施状态、明确原因和登记允许列表。
- [x] 方法失败保留；缺前序证据阻断后续成功；所有尝试和缺失原因可审计。
- [x] 多批次组合与兼容的单批次入口；不混合 synthetic、measured 和 diagnostic。
- [x] 结果包严格文件集、哈希、派生视图和保存快照重放；可选精确环境完整复算。
- [x] 先冻结后运行的双批次 CPU 合成链路，14 attempts → 12 observations，解析结果 5/12。
- [x] 新增对抗测试、独立终审及扩大回归验收：407 passed、1 个已知历史插件 skip。
- [x] 整理运行证据和文档，按先 pull 再推送流程交付 HeteroCoord（提交号见 Git 历史）。

D 阶段的 registry 是通用采集日程，不替代正式任务 benchmark；结果包校验只证明
保存内容一致，不认证注册时间、物理成功或论文发布资格。

## E：可迁移的原始证据归档

- [x] 将 bound/batch 结果包与全部对应封存 collector 精确绑定，保留原始字节。
- [x] 归档固定类型的封存 ledger、branch mirror 与 evaluator artifact；拒绝隐式外部路径。
- [x] 无符号链接的目录描述符读取、流式字节/文件限额、复制过程中变更检测。
- [x] 私有快照内重验 seal 和原始日志，重建 handoff 并与分析包逐字段比对。
- [x] 支持搬走原目录后的复核；保留原结果字节，完整复算按精确版本执行。
- [x] 原子且不覆盖已有目录的发布；失败自动清理私有 staging。
- [x] 专项 40 项与完整回归 447 passed / 1 个已知 skip，独立终审无未关闭 P0/P1/P2。
- [x] 原始目录不可用后的 CLI 迁移验收：两批次 42 个原始文件重验、结果字节不变、完整复算。
- [x] 整理文档与证据，按先 pull 再推送流程交付 HeteroCoord（提交号见 Git 历史）。

归档验证不签发真实性或物理成功证书，也不自动打包资产/视频/模型/未封存状态。
这一步补齐实验记录的迁移与复核，不替代正式任务方的物理与科学验收。

## F：一致性采集与事务式结果发布

- [x] 普通 handoff/bind/assemble 与归档复用同一封存文件语法和受限快照读取器。
- [x] 全部封存 JSON/JSONL 严格解析，私有快照内验证，保留原始 collector_path。
- [x] 单批绑定只获取并保存同一份 handoff，多批次共享总文件/字节预算。
- [x] 结果在私有目录中完成写入和完整性验证，再原子且不覆盖目标地发布。
- [x] Python 中断清理暂存；不可恢复进程退出不暴露半成品，原路径可重试。
- [x] 禁止采集入口把输出写入原始 collector，保留合成运行工作区的失败证据。
- [x] 最终合成端到端与完整回归：482 passed / 1 个已知 skip；35 项新故障验收。
- [x] 独立终审无未关闭 P0/P1/P2；旧结果包普通复核兼容，精确复算版本约束保留。
- [x] 文档与证据更新，先 pull 后推送 HeteroCoord（提交号见 Git 历史）。

本阶段保证内容一致性及结果目录的原子可见性，不提供断电持久性或物理成功认证。

## G：真实 session、独立评估与下游分析接通

- [x] fact-only measured_diagnostic 明确区分新建场景初始化与 checkpoint restore。
- [x] 冻结初始化规范、规范 digest、模式和容差；原始标量读数可离线复算。
- [x] 初始化失败作为基础设施失败封存，不能变成可计分的方法失败。
- [x] driver 方法状态与独立 evaluator 的物理终态分开；安全清理不覆盖执行失败。
- [x] 单批绑定与多批分析共同拒绝方法/基础设施状态重标。
- [x] 扩大回归：543 passed / 1 个已知历史插件 skip。
- [x] 临时 Pepper 三种箱体与持箱故障完成 GPU Isaac、视觉与干净退出验收。
- [x] 真实封存运行 → 冻结日程分析 → 迁移后原始证据复验与完整复算（4 runs / 64 raw files）。
- [x] 更新验收记录，独立终审无阻断 P1；先 pull 后推送 HeteroCoord（提交号见 Git 历史）。

该阶段仍是 tmp 诊断，不创建正式任务或统计合同。每个诊断条件仅一次运行，
不用于估计总体成功率或置信区间；正式实验矩阵与 release 认证继续由外部线程负责。
