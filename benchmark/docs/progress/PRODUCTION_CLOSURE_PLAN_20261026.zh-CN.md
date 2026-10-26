# 从真实语义收据到论文结果包：生产闭环计划（2026-10-26）

## Goal 重新启动与职责边界（2026-10-26）

本目标保持 `active`，采用持续推进模式：主代理只负责论文—工程语义对齐、系统架构、优先级、决策和最终验收；ZCode 负责已冻结计划中的 `py_compile`、pytest、schema/digest 检测、回归、批处理和只读证据提取。ZCode 不修改源码、测试、论文或正式 artifacts；发现需要改设计或写代码时只回传证据，由主代理处理后再交回复测。

本轮不以等待权限或等待测试结果作为工作内容。没有真实上游材料时，主代理继续推进接口与契约边界；材料到位后立即进入 G1 单 case 纵向闭环。所有诊断输出仍保持 `measured_unqualified` / `diagnostic_only`，正式资格门保持 `false`。

## 决策

当前 Goal 的交付对象是一个可审计的生产闭环：给定一个已经绑定语义地图和机器人池的真实 case，evaluator 产生原始收据，系统能够自动完成 oracle/义务归约、D1–D6 计算、结果导出和重放校验。输出在物理、统计和发布证据完整前仍只能是 `measured_unqualified` / `diagnostic_only`，所有正式资格门保持 `false`。

## 2026-10-26 结果层纠偏：fixed-panel projection

`paper_panel_package.json` 不能只作为 normalized receipt 行均值旁边的附件；否则固定 cell estimand 与主报告可能分叉。新增 `benchmark/paper_projection_v1.py` 作为显式接缝：注册的 panel package 只有在所有 D1–D4 端点为非退化 `valid`、D5 保留 family→case→unit→3-repeat 结构、D6 提供独立 structured runs/reference 时，才能投影为 `results_pipeline_v3` 的 D1–D6 `dimensions`。unknown/range/incomplete、缺 D5/D6 结构或 digest 不一致均 fail-closed；不得回退到 receipt 行平均。绑定 case 的生产入口现在强制提供 `panel_bindings` 与 `panel_projection_binding`，未形成 registered fixed-cell projection 时不能生成生产报告；receipt 行均值只保留在独立诊断路径中。

主代理负责接口、论文语义、数据契约、优先级和最终验收；ZCode 负责 pytest、py_compile、schema/digest 检测、回归和只读 artifact 检查。ZCode 不修改源码、测试、文档或正式 artifact，也不启动 GPU/CUDA/Isaac/Kit。

## 本轮持续 Goal 执行规约（2026-10-26）

本轮不再把“等待测试结果”当作主任务。主代理只推进四类工作：

1. 对齐冻结论文提交 `0b006d4efcd25d5160d4b0a6e75cddd9e5265e44` 的 estimand、支持目录、证据要求和资格边界；
2. 设计并实现跨模块数据契约，尤其是 `raw evaluator receipt → oracle/obligation → normalized D1–D6` 的生产接缝；
3. 根据协作者交付决定 case 是否可进入 G1、是否只能停留在 diagnostic，以及 formal gate 是否继续关闭；
4. 安排 ZCode 执行固定测试、编译、负例检测和结果提取，并只依据其实际命令、退出码和证据验收。

当前执行顺序固定为：

- **A：接口硬化**：验证 artifact manifest 字节绑定、case manifest digest、结果包 replay 和 synthetic 向后兼容；失败时主代理修复接口，ZCode 复测。此阶段不产生论文数据。
- **B：真实 case 接入**：协作者提供 semantic-map、topology、robot-pool、contract/oracle、replay/evaluator identity 后，由主代理审查其字段闭包并生成唯一 `case-manifest.v1`；随后接收一份真实 terminal receipt，执行单 case G1 纵向闭环。没有 raw receipt 和 artifact bytes 时不声称“已可直接出论文数据”。
- **C：论文支持目录**：G1 通过后，按 D2–D6 的固定目录补齐 C0–C3、relation ledger、D4 loss/healthy closure、D5 每 family 至少 30 cases、D6 独立 calibration；最后才进入 GPU qualification 和 release gate 审查。

明确的停止条件：上游事实缺失、digest 链断裂、unknown/incomplete 被误当成失败或成功、论文支持目录不足、恢复路径未通过三路等价时，继续开发接口或记录诊断证据，但不打开任何正式门。ToDesk 等外部显示服务不属于 benchmark 语义或资格输入。

## 当前状态

已经具备：

- 最新论文身份冻结为 `overleaf-old/main@0b006d4efcd25d5160d4b0a6e75cddd9e5265e44`；
- 30 个任务锚点、9 个现有 common-anchor 组和合同/oracle 层；
- `benchmark/receipt_adapter_v3.py`，负责 raw evaluator receipt 到 normalized D1–D6 package 的严格转换；
- `benchmark/case_manifest_v1.py`，冻结单个真实 case 的 semantic-map、robot-pool、contract、replay 和 evaluator 身份，并以 manifest digest 绑定收据；
- `benchmark/case_input_audit_v1.py`，在生成 `case-manifest.v1` 前逐字节检查 semantic-map revision 与 robot-pool material；review-only roster、空语义产品、未验证机器人和 digest 漂移均 fail-closed；
- `benchmark/results_pipeline_v3.py`，负责论文公式、复合分数、sanity checks、导出和 digest replay；
- `tools/benchmark_branch/evidence.py` / `executor.py` 已能保存分支级 evaluator artifact，但还没有把真实执行账本自动装配成 `heterocoord.raw-evaluator-receipt.v1`；因此当前适配器是可接入的生产边界，不应被误称为 live evaluator 已完成；
- 新增 `benchmark/receipt_producer_v1.py`：从绑定 case manifest、terminal episode、evaluator 明确提供的 streams/facts 装配并严格校验一份 raw receipt；它区分 artifact hash 与 receipt-envelope hash，拒绝身份/版本/状态不一致，不从进程退出或 caller score 推断科学事实；
- 新增 `benchmark/receipt_bridge_v1.py`：先验证 `evidence.py` 的内容寻址 artifact，再只接收 `observations.receipt_material = {episode, streams, facts}`，把已验证的 artifact hash/ref 与终局 evaluator 元数据绑定到 `receipt_producer_v1`；`completed`、`physical_success`、分支谓词和调用方分数不会被转换成科学事实；
- 缺 C0–C3、D3 relation support、D6 独立 calibration 或存在 unknown/incomplete 时，适配器 fail-closed；
- 结果报告固定为 diagnostic-only，不能开启 rankable、paper-score-eligible 或 physical-qualified；
- 合同/oracle/结果管线专项基线为 232 passed、4 skipped；
- GPU 三路目录目前没有完整 `concordance.json`，不能作为论文资格证据。

2026-10-26 的 G0/G1-contract-input 已完成只读验收：ZCode 对三个输入/适配/管线模块完成编译检查；合法 `case_manifest` 绑定、真实 case 预留字段接线、结果导出与 replay 验证通过；篡改语义地图摘要、base-instance 和 evaluator version 的负例全部 fail-closed；focused regression 为 `232 passed, 4 skipped`。这些是接口证据，仍不构成真实论文数据。

## 语义地图和机器人池接入后必须补齐的字段

语义地图不是只提供坐标。每个 case 必须冻结并哈希：scene/topology identity、frame/units、实体 ID、区域和容量、可达性/禁行规则、里程碑绑定、oracle predicate 参数、horizon、稳定窗口和 evaluator version。`semantic_map_digest` / `topology_digest` 只是身份绑定；它们必须同时能在内容寻址 artifact manifest 中解析到上述字段，且收据的 evidence refs 必须覆盖这些字节。只有 digest 没有可验证内容时，case 保留为 `unknown`，适配器不能猜测或把它当作可解释语义。

机器人池必须冻结：robot_id、asset/config/version、初始位姿和坐标系、可用性、能力简历、支持的 skill、适用约束、证据引用和 roster digest；同样必须有可验证的 roster artifact，而不是只提交 digest。被测系统可以选择池内成员，但不能改变已注册池、能力或初始状态来补齐失败结果。

真实 evaluator 收据还必须带：contract/catalog/plan digest、base-instance/repeat/condition/branch/checkpoint identity、evaluator evidence hash/ref、完整 milestone/event/zone/contact/identity streams，以及 D1–D6 所需的 evaluator-owned facts。当前缺口不是“再写一个分数函数”，而是把 simulator/evaluator 的真实账本、D1 初始计划检查、D4 closure、D5 attempt、D6 bits/time 独立地交给 producer 装配进这份 receipt；`receipt_producer_v1` 不会从进程退出、调用方分数或 grant/deny 推断物理证据。

## 分阶段推进

### G0：收据适配器 CPU 纵向切片

用四个条件 C0–C3、一个完整 pilot oracle stream、P2/P3-J/P3-S/P4 obligation、D4 item 和独立 D6 calibration 构造临时 fixture。验证 artifact → receipt bridge → normalize → compute → export → verify_export 全链路，以及缺 `receipt_material`、缺条件、缺 calibration、unknown obligation、篡改 evidence hash/report 的 fail-closed 行为。该阶段只证明接口和论文算术，不产生论文正式结果。

### G1：单个真实语义 case

协作者交付地图和机器人池后，先生成并校验 `heterocoord.case-manifest.v1`：scene/topology/semantic-map、实体与区域、frame/units/horizon、robot roster/capability/initial state、contract/oracle、replay serializer/RNG 和 evaluator version 全部冻结并绑定 `manifest_digest`。随后登记一个 case 的 scene/roster/contract/evaluator digests，生成一份真实 terminal receipt。适配器必须能够从收据重建 oracle verdict、义务结果和 D1–D6 package；任何缺失均生成明确原因码。通过后才允许扩大到同一 case 的重复键。

当前 G1 的唯一实质阻塞是上游真实 evaluator receipt：需要一份共享 task identity、完整事件/里程碑/区域/接触/身份流、四条件与 D3 obligation facts、D4 closure、D6 calibration 引用和可验证 evidence hash 的 terminal receipt。收到后先做一例纵向切片，成功再扩展重复键；不在输入到位前启动 GPU 或声称论文结果。

库存盘点也确认了该边界：目录仍是 30 个 `planned/not_yet_certified` 锚点（21 个 common-anchor 成员、9 个组）；现有 artifacts 的 output manifest 都属于 synthetic 结果包，尚无真实 evaluator receipt、独立 D6 calibration 或 robot-pool manifest。迁移审计 JSON 只说明论文语义迁移状态，不能替代地图或 roster 证据。

### G2：固定目录和重复层级

把真实收据组织为论文要求的 base-instance → repeat → fixed cell 目录：D2 必须有 C0–C3 配对支持；D3 必须覆盖完整 relation signature 且排除 primary label 重复计数；D4 必须有共同干预前检查点、healthy/loss 对照和受影响工作闭包；D5 必须有 source/target、sham、causal replay 和每 family 至少 30 cases；D6 必须引用独立 calibration batches。此阶段完成前，报告可以计算但保持 incomplete/NA。

### G3：真实 GPU 诊断与三路等价

GPU 运行器只负责产生可审计执行/恢复收据，ToDesk 等无关显示服务不得成为公开 benchmark 的硬依赖。GPU admission 只检查 benchmark 自己的 lease、显存预算和 compute workload。三路等价是诊断探针，要求同一检查点、随机流和共驻条件下 A/B/C 对账；不一致则保留差异并拒绝资格。

### G4：独立审计和发布门

独立检查 catalog、contract、semantic-map、roster、evaluator、calibration、artifact 和 report 的 hash 链；运行 release/precision/physical/statistical gates。只有固定目录、证据成员关系、重放一致性和统计支持全部满足时，才有资格讨论论文表格或排名。当前及前述所有阶段均不自动打开正式门。

## 需要协作者提供的最小交付包

1. 一个真实 scene 的 semantic-map manifest、实体/区域 ID 表和 topology digest；
2. 同一 scene 的 robot-pool manifest、能力证据和 roster digest；
3. 一个已绑定 pilot anchor 的 contract/oracle manifest；
4. 一份 evaluator raw receipt 示例（含 evidence refs 和可验证 hash）；
5. 一个独立 D6 calibration record；
6. 对应的 checkpoint、ledger、physical evidence 和 artifact manifest。

拿到这六项后，工程可以直接进入 G1；不需要重新设计结果计算器。缺失的论文级支持（D2–D6 fixed directory、重复和独立 calibration）仍需按 G2 补齐，不能用单个 case 越级替代。

## 失败处理

模拟进程启动前的基础设施错误（路径、输出解析、环境读取）可以用新的临时输出目录修复并继续；每次写入 `run_failure` 记录。模拟进程启动后的失败必须保留原始收据，逐次复核授权。任何 unknown、篡改、hash 不匹配、缺事件或跨 case identity 混用都 fail-closed。

## 当前持续推进板（2026-10-26）

Goal 保持 active，停止条件是“真实语义 case 完成并可审计地导出论文结果包”，而不是“某轮测试结束”。主代理与 ZCode 的边界固定如下：

| 阶段 | 主代理负责 | ZCode 负责 | 结束条件 |
|---|---|---|---|
| G0 接缝 | receipt/case 契约、论文语义、fail-closed 决策 | 编译、pytest、schema/hash/负例检测 | 接口链可验证，正式门仍 false |
| G1 真实单 case | 审查地图/机器人池闭包，生成唯一 case manifest，判断 receipt 是否具备论文语义 | 对真实 receipt 执行 normalize→compute→export→replay 与只读检查 | 至少一份真实 terminal receipt 全链通过 |
| G2 论文目录 | 冻结 D2–D6 支持关系、重复层级和 estimand 对齐 | 批量目录检测、重复/缺失/哈希审计 | 固定目录与独立 calibration 完整 |
| G3 物理诊断 | 决定证据等级和是否保留为 diagnostic | 运行/采集/对账/检测，不改变资格门 | 三路等价与物理证据可复核 |
| G4 发布门 | 独立审计结论、最终科学判断 | release/statistical/precision/physical 固定命令 | 所有 formal gates 才可被讨论，当前保持 false |

### 地图和机器人池接入后的“直接出论文数据”标准

接入不以文件存在为完成，而以以下链条闭合为完成：

`semantic-map + robot-pool + contract/oracle + replay/evaluator identity`
→ `case-manifest.v1`
→ `evaluator terminal receipt + artifact manifest`
→ `receipt_bridge/producer`
→ `oracle/obligation reduction`
→ `D1–D6 normalize`
→ `results_pipeline`
→ `export + replay verify`。

其中仍不能由地图/机器人池自动补齐的内容是 evaluator-owned 的真实事件和证明：完整 milestone/event/zone/contact/identity streams、D1 可行性与初始计划事实、D4 healthy/loss closure、D5 source/target/sham/causal attempt、D6 独立 calibration bits/time，以及内容寻址 artifact manifest。任何一项缺失都只能输出 `measured_unqualified` 或 `diagnostic_only`，不能生成论文正式表格或打开资格门。

当前上游材料盘点（2026-10-26）显示：`home_02` revision 的字节清单存在，但 `semantic-footprints.json` 的 regions/entities/interaction_poses/routes 为空，且 `robot_validated=false`；`robot_pool_resume.v1.json` 仍为 `review_draft_not_runtime_input`，实例身份与 runtime qualification 未绑定。因此这两份材料目前只能作为审计输入，不能生成真实 case manifest。

## 持续 Goal 重排（2026-10-26，主代理架构 / ZCode 执行）

本轮不再把“测试通过”当作阶段目标，而以“真实 case 收据可自动变成论文结果包”为唯一停止条件。主代理负责系统契约、论文 estimand、证据等级、优先级、代码修复和最终验收；单个 ZCode 会话负责固定命令、编译、回归、负例、schema/digest 检查和证据提取。ZCode 不修改仓库、不裁决科学语义、不启动 GPU/仿真。

推进顺序固定为：

1. **输入门硬化**：`case_input_audit_v1` 必须拒绝 digest 正确但语义产品为空的 revision（regions/entities/routes/interaction poses 等）；robot roster 必须是 runtime-qualified 且身份已绑定。审计输出只决定 `case_ready`，不代替出题人语义绑定。
2. **G1 单 case**：协作者交付真实语义地图、机器人池、合同/oracle、replay/evaluator identity 后，主代理生成唯一 `case-manifest.v1`；evaluator 提供一份 terminal receipt 和 artifact manifest。ZCode 执行 `artifact → bridge → raw receipt → normalize → compute → export → replay`，任何缺失仍 fail-closed。
3. **论文支持目录**：单 case 通过后才建立 D2 C0–C3、D3 relation ledger、D4 healthy/loss、D5 六方向与 causal/sham、D6 独立 calibration 的固定目录。目录未满时结果可计算但只能是 incomplete/diagnostic。
4. **真实 GPU 诊断**：最后才做真实 GPU 与三路恢复；ToDesk 等显示服务不进入 benchmark 语义或公开资格依赖。三路不一致只保留诊断差异，不打开资格门。
5. **发布审计**：独立复核 hash 链、统计支持、物理证据、replay 和 release gate；在此以前 `rankable`、`paper_score_eligible`、`physical_qualified` 及认证 flags 全部 false。

2026-10-26 的 ZCode 审计发现并报告了一个输入契约缺口：原审计器只拒绝 routes 缺失/非列表，不拒绝 `routes=[]`。主代理已将空 routes 纳入必需语义产品的 fail-closed 条件；ZCode 当前版本复测 `py_compile=0`，routes 负例与真实 home_02 检查均 exit 0，后者明确报告 `semantic semantics.routes is empty`。此类问题属于系统契约修复，不把它归咎于测试或 GPU 环境。

同轮新增 `benchmark/production_results_v1.py` 作为单一 CPU-only 生产编排入口，强制执行
`raw receipt → normalize/oracle/obligation reduction → compute → export → replay verify`。
它要求 case manifest 和内容寻址 evidence，输出仍保持 diagnostic/unqualified；ZCode 正在用
既有 C0–C3 fixture 验收正例、篡改 evidence 负例和 replay。

本轮主代理又补上 `run_production_case_from_artifacts()`：生产入口现在可以接收带有不可变
`BranchJob` 身份和 evaluator verdict 的已验证分支 artifact，先经 `receipt_bridge_v1` 逐个
验证并装配 raw receipt，再进入同一 normalize→compute→export→replay 路径。该 helper 不从
进程状态、`completed`、锁服务答复或 caller score 推断科学事实；artifact 缺失、重复、篡改或
材料不完整时在创建输出目录前失败。它只是部署接缝，输出仍是 `measured_unqualified`，三项
正式 gate 保持 false。

该 CPU/模拟纵向切片现已完成：ZCode 报告正例 CLI exit 0、7 个输出文件和独立
`verify_export` replay 通过；tampered evidence 以 mismatch、missing evidence 以 missing
分别 exit 2，均未创建输出目录。该证据关闭了“生产编排入口未验证”这一项，但不关闭
G1-real-receipt，也不把模拟结果升级为论文数据。

输入审计输出现额外包含 descriptive `semantic_summary` 和 `roster_summary`：前者提供
场景/坐标系、语义产品计数和拓扑节点/边计数，后者提供 roster 状态、已绑定身份数和
runtime-qualified 数量。这些字段只服务于后续 case-manifest 组装，不推断任务语义，
也不会放宽任何 fail-closed 条件。

### G0 接缝补强：审计输入到 case-manifest 组装器（2026-10-26）

主代理新增 `benchmark/case_manifest_builder_v1.py`，把输入审计与既有
`case_manifest_v1` 验证器接成一条可复用的生产路径。组装器的边界是：

- 先执行 `audit_case_inputs`，审计失败时不产生 manifest；
- 只接受 runtime-qualified 且实例身份已绑定的机器人池，并要求每个选中实例显式提供
  asset/capability/initial-state 三类 digest；review-only 简历不能越级；
- 任务/锚点/关系签名、实体/区域绑定、horizon、稳定窗口、合同/oracle、catalog/plan、
  replay 和 evaluator identity 全部来自显式 binding spec，不从地图或机器人简历猜测；
- semantic-map digest 覆盖 manifest 与 manifest 列出的全部内容寻址文件，topology digest
  绑定实际 topology 字节，最终仍通过 `bind_case_manifest` 重算并校验
  `manifest_digest`；
- 提供 CLI 入口，方便协作者材料到位后生成唯一 `case-manifest.v1`。当前 `home_02` 的
  旧版/空语义 revision 与 review-only robot pool 仍应 fail-closed，不产生正式 manifest。

该接缝只关闭“审计结果无法进入 manifest”的工程缺口；它不把现有旧地图、review roster
或 synthetic receipt 升级为真实 case，也不改变 `measured_unqualified`、`diagnostic_only`
和 formal gates=false 的边界。ZCode 负责编译、相关回归和真实旧输入的负例验收。

## Goal 重开后的架构执行计划（2026-10-26，主代理决策版）

本 Goal 继续保持 `active`。停止条件不是“回归测试全绿”，而是：给定一份真实、已绑定的
semantic case，系统能从 evaluator 的终局收据自动生成可重算的论文结果包，并且对所有缺失、
篡改、未知和不完整证据保持 fail-closed。达到该条件前，`rankable`、
`paper_score_eligible`、`physical_qualified` 及认证 flags 均必须为 `false`。

### 职责重新收敛

- 主代理负责论文版本与 estimand、数据契约、结果计算器/聚合器的实现、科学语义判断、阶段
  决策、代码修复和最终验收。
- ZCode 只执行主代理冻结的测试、编译、schema/digest 检查、回归、批处理和只读证据提取；
  不改源码/测试/论文/正式 artifacts，不裁决语义，不启动 GPU/CUDA/Isaac/Kit。
- ToDesk 或任何其它显示服务不进入公开 benchmark 语义，也不成为资格依赖；GPU 运行另有
  独立的诊断阶段。

### 主线阶段

1. **G0 接缝收尾**：完成 `receipt_material → artifact → receipt bridge/producer →
   normalize → compute → export → replay` 的固定契约审查。ZCode 给出命令、退出码和证据路径，
   主代理只修复契约或实现问题。
2. **G0.5 论文结果层补齐**：在不改变 raw receipt 契约的前提下，把论文的固定 cell 权重、
   base-instance/repeat 聚合、D1/D3/D4 cluster interval、D2 finite-sample retention set、
   D5/D6 Hoeffding 支持、unknown/incomplete/range 状态接成一个生产聚合入口。当前
   `results_pipeline_v3` 已能计算六个点估计并导出重放包，但 synthetic fixture 不能替代该
   真实面板层；本阶段的验收是“生成的模拟目录与直接按论文公式计算逐字段一致”，不是打开资格门。
3. **G1 单真实 case**：协作者提供语义地图、topology、机器人池、contract/oracle、replay/
   evaluator identity 后，主代理审查字段闭包并生成唯一 `case-manifest.v1`；再接收一份真实
   terminal receipt 和 artifact manifest，跑通完整纵向闭环。没有真实 receipt 不进入 GPU 运行。
4. **G2 论文支持目录**：以固定 catalog 建立 D2 C0–C3、D3 relation ledger、D4 healthy/loss
   closure、D5 source/target/sham/causal（每 family ≥30）、D6 独立 calibration；目录不足时
   结果只能 `incomplete`/`NA`/`diagnostic`，不允许重加权补齐。
5. **G3/G4 物理诊断与发布审计**：真实 GPU 三路等价、独立审计和 release gate 只在 G1/G2
   证据完整后进行；不一致保留差异，不能静默重跑或升级资格。

### 接入地图与机器人池后的“可直接出结果”判据

文件存在或 digest 正确都不够。必须同时满足：

`semantic-map + robot-pool + explicit contract/oracle + replay/evaluator identity`
`→ case-manifest.v1 → terminal receipt + artifact bytes`
`→ oracle/obligation reduction → fixed-panel aggregation → D1–D6`
`→ export + replay verify`。

地图/机器人池只冻结 case 身份和可部署性边界；它们不能替代 evaluator-owned 的事件/里程碑/
区域/接触/身份流、D1 可行性事实、D4 closure、D5 attempt、D6 独立校准和 artifact bytes。
其中任一项缺失，系统必须明确报告 `not_evaluable` 或 `incomplete`，不能猜测、删除或把
process exit/caller score 当作科学事实。

### 当前实质缺口与优先级

1. 上游真实语义产品：现有 `home_02` revision 语义产品为空，机器人池仍是 review-only，故
   不能生成真实 manifest。
2. 一份 evaluator-owned 的真实 terminal receipt 和可验证 artifact manifest；当前接缝已存在，
   但尚未有真实运行输入。
3. 论文面板聚合层的真实目录输入与不确定性输出；现有 synthetic 结果仅是部署烟囱测试。
4. D2–D6 固定支持目录、独立校准和配对/因果审计；单 case 只能证明接口，不足以支持论文表格。
5. GPU 三路恢复与 shared-session qualification；其证据等级仍低于论文资格，且不阻塞 CPU 结果层
   的开发。

### G0.5-paper-panel 实现记录（2026-10-26）

主代理新增 `benchmark/paper_panel_v1.py` 作为固定 cell 结果层。它接收
`heterocoord.paper-panel-directory.v1`，对每个 cell 先做 repeat → base-instance 聚合，再按
预注册 cell weight 聚合；权重不因缺失或覆盖不足而重分配。覆盖门失败返回
`incomplete` 且不产生 panel point；未知/缺失 repeat 返回 `range` 并保留 lower/upper
识别区间。D3 组合入口固定使用
`100/3 * (p2 + (p3j+p3s)/2 + p4)`，因此 P3-J/P3-S 合计只占 1/3；
`fixed_catalog_hoeffding_half_width` 对 D5/D6 暴露论文固定目录 Hoeffding 半宽。

该层只产生 normalized panel estimate，不打开资格门，也不代替真实 evaluator receipt。

### 2026-10-26 运行时名册到 case-manifest 接线

`case_manifest_builder_v1._runtime_robots()` 现同时识别旧版 review resume 与
`heterocoord.robot_runtime_roster` v1 运行时名册。新版路径只接受实例号/来源简历号的显式
选择，并要求每个条目 `runtime_qualified=true` 且存在资格收据，再复制 asset/capability/
initial-state 三类绑定摘要到 manifest；当前 27 条名册仍全部为 false，因此旧输入和当前名册
均继续 fail-closed。该接线没有把 CPU 收据验证、模拟结果或 review-only 简历升级为运行时资格，
也没有改变正式门。
它包含 `directory_digest`，并且带 unknown 的面板不附加 cluster-bootstrap 区间，必须先解决
识别区间再做 sampling uncertainty。ZCode 只读验收记录：编译 exit 0；专项测试首次发现
D3 测试夹具错误并修正后通过（6 passed）；既有结果管线回归 10 passed；独立边界检查
22 passed。首次边界脚本的错误手算值已明确归类为测试夹具问题，未导致源码修改；正式 gates
仍全部 false。

### G0.6 raw receipt → fixed panel 接缝（2026-10-26）

为使真实 evaluator 收据到位后可以直接计算论文面板，主代理新增
`build_panel_directory_from_receipts()` 与 `build_panel_estimates_from_receipts()`：

- `PANEL_BINDING_SCHEMA` 显式登记 endpoint、repeat 集、cell 权重、计划实例、基础设施有效实例、覆盖门和允许的 evaluator fact 路径；不从 semantic map、进程状态或 caller score 推断统计身份；
- 每条 raw receipt 先按 `receipt_adapter_v3` 严格校验，再按 base-instance/repeat 唯一映射到一个 cell；重复、未登记 repeat、零/多 cell 映射和不支持的字段路径均 fail-closed；
- 缺少的 repeat 保持缺失，由固定面板输出 `range`；已观测的 `unreached` obligation 按绑定记录 V2 计零，`unknown/incomplete` 保持未知，不修改原始运行状态（2026-10-26 修正此前错误描述与实现）；
- 输出 `heterocoord.paper-panel-package.v1`，保留 binding digest、完整目录、estimate 和 package digest，可与 D1–D6 normalized report 并列归档；`build_paper_panel_package()` 是 production seam 的受控入口，正式资格门仍全 false。

该接缝的验收由 ZCode 只读执行；在真实 evaluator receipt 和语义地图到位前，它只证明部署接口和 fail-closed 行为，不产生论文正式数据。

### G0.8 收据观测 ID 闭包（2026-10-26）

主代理在 `receipt_adapter_v3` 增加了 case-bound observation binding：当收据绑定
`case-manifest.v1` 时，zone interval 的区域、contact interval 的实体，以及区域持有者和
接触机器人都必须属于 manifest 登记的 zone/entity/robot 集合。digest 正确但引用外部 ID 的
收据现在在 oracle/义务归约前 fail-closed；合法 ID 仍沿用原有 D1–D6 校验。该检查只收紧
输入契约，不从地图推断事件，也不改变论文公式或正式资格门。

同轮复测又发现一个实现接缝错误：校验器曾把解析后的 `PredicateStreams` 当作字典下标访问，
导致合法收据触发 `TypeError`。现已改为访问其 `zone_intervals` / `contact_intervals` 属性；
这是恢复既定 ID 闭包校验的基础设施修复，不改变 oracle 语义、结果公式或资格门。修复后的
边界复测仍由同一 ZCode 会话执行，证据写入 `/tmp/heterocoord-receipt-binding-zcode-20261026/`。
