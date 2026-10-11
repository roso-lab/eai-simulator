# 框架与论文计算链路审查（2026-10-11）

本轮目标是使未来正式任务能够接入可审计的实验工程。正式任务、合同、隐藏集和
业务 evaluator 继续由任务编排线程负责；本轮没有创建新的正式工厂任务或改写论文。

## 核验的版本与材料

- EAI 基线：`aba946e07fbe8e5aabf1bb92545274096def4708`（HeteroCoord）。
- 在线 Overleaf：项目 `6ab4c7c4e984abb8da9c8113`，读取前后 history 均为 v773。
  标题为 *HeteroCoord: Benchmarking Whether, When and Why Heterogeneous
  Multi-Robot Coordination Fails*。逐文件读取，未向线上写入或修改论文。
- 主要计算依据：`sec/5_evaluation_protocol.tex`、`sec/X_suppl.tex`、
  `sec/supp_coverage.tex`，并核对结果表和 `sec/6_validation.tex`。
  临时源快照位于运行目录 `tmp/experiment_analysis/paper/`。
- 外部 `benchmark/` 是另一工程的未提交开发代码（其仓库 HEAD `6c980d3d`），
  不能把该 HEAD 当作这些未提交文件的完整版本。只读审查，未修改或复制其任务注册。
- 旧 EAI `test_benchmark_goal_*` 测试引用已不存在的 `demo.benchmark`，不是新版
  六维度计算实现。本轮新增独立的现行公式反例，不以旧测试数量证明有效性。
- 论文结果段仍有 `xxx` 占位；本轮模拟计算不填入论文或冒充实测结论。

材料护照：这是软件/公式验证，输入是在线论文源、当前代码与明确标注的合成记录；
可支持数学实现和接口行为结论，不能支持机器人成功率、方法优劣或正式因果结论。

## 当前分层与本轮补齐

```text
外部任务/方法/统计注册（唯一语义权威）
        ↓ 任务包、冻结实例/条件/重复/支持集
task runner → skills/runtime → caller-owned EAI session
        ↓ 运行记录与独立物理证据
benchmark_branch collector（所有 attempt、封存日志）
        ↓ 只读 handoff，核对 seal 与分支身份
外部 evaluator（成功谓词、数值/事件、排除原因、选择策略）
        ↓ evaluated_outcomes + 冻结设计
experiment_analysis freeze + assemble → 登记重试/支持/完整性检查 → 数学归约
        ↓ 带来源与资格限制的 JSON/CSV/Markdown
结果包 verify → 保存证据重放 + 可选精确环境完整复算
        ↓ 可选 archive：原始封存日志与结果包精确绑定
verify-archive → 私有快照内重验 seal/日志/交接，支持迁移后复算
        ↓ 内容一致性结果（不认证注册时间/原始物理真值）
外部协议 release 审计（本工具不授予发布资格）
```

此前运行框架已经包含 observation/replan、资源占用与隔离、协作技能、checkpoint、
独立进程 watchdog 和 GPU 准入。本轮补齐其下游数据准备、运行量预检查、证据交接、
计划外/重复身份拒绝、缺失实例显式化、归约和复算导出。

后续 D 阶段（基线 `125bb9d5`）进一步补齐多批次收集。日程精确枚举设计需要的
观测，冻结任务、代码、检查点、资产和所有分支 metadata；只有原始 episode 与
evaluator 一致且允许的基础设施原因才能推进重试。方法失败不能被后续成功替换。
两批 CPU 脚本控制使用真实 collector 封存，共 14 attempts / 12 observations，
结果为手算的 5/12；不是 Isaac 物理运行。

结果包保存来源快照、选择审计、数据和派生视图。离线验证可重放选择并在同版本
Python/源码下完整复算；D 阶段单独的结果包不含原始 ledger，因此普通 `verify`
仍不宣称重验原始 seal。
单批次 `bind-outcomes` 保留兼容，其任意 policy digest 仍由调用方负责；登记选择规则
需使用 `assemble`。正式 benchmark 的语义权威与外部未提交代码边界保持不变。

E 阶段（基线 `7008cd0a`）增加可选原始证据归档：按封存哈希一一绑定 collector，
保留日志和分析结果字节，在私有快照中重验 seal、结构及重新生成的 handoff。读取
拒绝符号链接与特殊文件，限制文件/总字节，发布采用原子且不覆盖目标的操作。
原始目录移走后，两批次 42 个原始文件仍通过重验与完整复算；全套回归为
447 passed / 1 个已知 skip，独立终审无未关闭 P0/P1/P2，详见 [验收记录](VALIDATION.md)。
归档不自动包含模型、资产、视频或未封存状态，不认证 collector/evaluator 真实性、
登记时间或物理成功；它补齐记录迁移与内容复核，不构成完整物理重跑环境。

## 论文公式到代码的对应

| 论文要求 | 通用实现 | 验证重点 |
|---|---|---|
| Eq. macro/main-panel：repeat → instance → fixed cell panel | `engine` 的 `mean` | 不按 repeat 或 cell 样本量隐式加权；缺 required cell 全 panel N/A |
| D2 retention：共同支持上的 panel means 比值 | `ratio_of_means` | 不平均逐实例/逐 cell 的 retention；保留分母阈值 |
| D3 conditional：机会/满足分子分母分别归约 | `ratio_of_means`；P4 可用 `mean_of_cell_ratios` | 零机会不静默删 cell；不同 estimand 的聚合顺序分开 |
| factor main/interaction | `linear` + exact terms | 先交集 repeat 和 instance，再用四角系数；系统身份不混合 |
| D4 recovery/response CIF | `primitives.competing_risks` + cell panel | fail/safe/infeasible 不当 censor；event/censor ties；随访不足 N/A |
| D5 family gap | `family_gap` | 重复、matched pair、family 三层；target−source；不按 family 样本量加权 |
| D6 cost + NI | raw natural-unit `mean`；独立 NI 数学函数 | 方法失败仍记录成本；NI 不改变成本数据，不自行授予优势声明 |
| cluster uncertainty | 固定 seed 的 cluster bootstrap | 样本是 instance 或 family；重复/配对分支整体搬运；无效 draw 不偷偷丢弃 |
| six-primary multiplicity | 固定家族 `holm_adjust` | 接收外部有效 p 值；缺失假设不缩小家族，不从 CI 反推 p 值 |
| stress crossing | joint sup-deviation band / grid bracket primitives | 相邻测试网格、删失、严格阈值；不把 pointwise CI 当 simultaneous band |

这些映射提供通用算子，不意味着 D1–D6 正式注册表已接线。原始 milestone 稳定窗、
oracle、关系签名、hand-off ownership、affected work 和 action deadline 均需要外部
evaluator 产生。当前还不支持任意非线性归一化、非等权 panel、跨 checkpoint round
汇总或 ratio-scale 的非线性 factor contrast；必须显式扩展协议后实现。

## 审查确认的风险与处理

1. **重复实验被当作独立实例。** 外部 `benchmark.batch` 的 base payload 包含 repeat；
   EAI `BranchPlan.jobs()` 原先同样从含 repeat/system/condition 的 pair group 生成 base。
   EAI 已增加 v2：固定 run、scene、task/version、slot、base seed 决定 base；branch 和
   pair key 仍保留 condition/system/repeat。显式外部 jobs 原样保留；旧封存 plan 没有
   version 时 resume 使用 v1，不重写历史。外部 batch 的修复仍由其拥有者落地。
2. **观察到一条数据就宣称完整。** 外部 ReleaseIndex 复现过单 metric+单 artifact 即
   `inferentially_ready=True`，并未对 planned cells/repeats 做完整连接；其 reducer 也可
   将 valid+pending 简化为有效点估计。新层从冻结设计枚举缺失，required cell 不齐则
   整 panel N/A。外部发布层仍需独立修复，不能直接接为本轮的资格权威。
3. **证据哈希存在不等于身份正确。** 外部 release 可将 base-B/r3/reference 指向
   base-A/r1/fact 的 artifact。新 bind-outcomes 逐字段对齐 collector 分支，seal、
   episode 和 evaluator 输入进入摘要；保留原始 sidecar 供审计。它仍不认证 evaluator。
4. **真实 batch 接线字段缺失。** 外部 batch 未提供 bridge 必需的 untreated_history_id；
   本轮不在 collector 猜造它。正式接线前任务方需明确并冻结历史与 checkpoint 语义。
5. **重试/来源/资格容易混淆。** collector 导出所有 attempt，无“挑成功重跑”；归约
   数据拒绝同一科学观测的多个记录。synthetic 来源沿绑定与导出保留；measured 也先是
   unverified。所有报告 `publishable=false`，没有自声明即可通过的发布开关。

## 后续工程节点及依赖

可独立开发的数学、输入验证、证据交接、缺失/资格报告和复算功能已经提供，无需等待
真实任务。正式实验闭环仍需任务方冻结后接入以下内容：

1. 正式生成器的 repeat-independent base IDs、每条件基础设施状态及完整计划矩阵。
2. 能真正读物理 ledger 的 endpoint evaluator，包括 method failure 与 censor 规则。
3. 可核验的 replay/sham、transfer family、oracle 和支持集证书；独立 registration 时间线。
4. 六主终点的具体候选/参考假设检验、共同支持、NI 单/双侧区间约定、同时带家族及
   precision policy。当前提供算子和描述区间，不猜测尚未注册的检验。
5. 外部 release 层按完整设计与证据身份重新验收，再接真实任务运行矩阵。

此处的未接入部分不是“所有框架已完成”的声明。也无需为了验证纯计算层重新启动
Pepper/Isaac；本轮没有修改物理 controller。真实 endpoint 产生后须再做物理与统计端到端验收。
