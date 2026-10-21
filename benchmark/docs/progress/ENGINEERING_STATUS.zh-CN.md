# HeteroCoord 当前工程状态

更新日期：2026-10-21。论文基准：`75ceff6f55202b6b1a31b2e2e0f6de56023940fd`（此前 `bb82b7b` 与 `af12f6d` 已被 Overleaf 最新提交取代）。
本报告面向框架判断；历史实验、失败收据和阶段报告继续保留，论文未修改。

新论文协议状态：fixed-system disruption、paired skill-failure rerun、frozen-time external
contention、protective-stop load-state checkpoint 目前已在协议登记层建立 fail-closed 契约，
但尚无完整正式实验批次。`violated` 与 `unreached` 的 D3 语义已在评价器中分离；这不等于
已经具备论文可发布的事件表、物理 stop dynamics 或校准目录。

## 当前结论

工程已经形成“论文规则 → 实验登记 → 本地执行 → 原始证据 → 独立评价 → 发布检查”
的主体结构。项目本地部署、真实导航、双向通信计量和一个机械臂组件实验已有实测
证据。当前仍未形成可以填写论文正式结果表的完整实验批次。

最主要的缺口在证据生产侧：场景锚点还没有逐个成为认证实例；完整任务技能尚未取得
物理资格；严格恢复仍有原生时钟与后续轨迹问题。统计程序通过测试不能补足这些证据。

### 2026-10-21：Tool 诊断证据接入项目 ledger

原 tool 会话新增的 `heterocoord.diagnostic_trace_evidence.v1` envelope 现在有一个项目内
适配边界：`benchmark/diagnostic_evidence_v3.py`。它只接受明确的 `diagnostic_only`、
`rankable=false`、`process_failure_scope=shared_session` 和三个 qualification flag 全为
`false` 的记录，并保留 branch 差异、numeric/exact equivalence、容差、candidate/reference
路径和 shutdown status。摘要复算后，记录可以追加到 HeteroCoord 的 branch-local ledger，
但不会生成 D1–D6 的 `MetricResultV3`，不会改变权重、support、coverage 或 release status。

这一层解决的是“tool 诊断证据可追溯”，不是“tool 已成为正式论文 runner”。当前真实 tool
结果仍是 observable diagnostic；`numeric_equivalence=true` 不等于 `exact_equivalence=true`，
Kit shutdown timeout 也不等于 clean pass。独立验收发现并修复了 envelope identity 与
ledger identity 脱钩的问题：所有 mapping ingress 现在直接验证摘要，写入 ledger 前还会把
envelope 的 RunKey 核心字段与真实 ledger 逐项比较。验证后的 envelope 使用深度不可变状态，
直接构造和验证后 mutation 均不能进入 ledger；tool v1 canonical digest 的非 ASCII 与整数数值
兼容也已覆盖。适配器/fixture/artifact/release 定向测试为 58 passed；在切换到论文提交
`75ceff6` 并补齐新增协议注册门禁后，通过项目运行边界执行的选定主工程回归为
798 passed。直接在带有旧外部 EAI 模块的 shell 中运行测试会被
runtime 的 fail-closed 检查拒绝，正式命令必须经 `tools.project_runtime exec` 启动。

阶段反思：把诊断证据从 tool 工作树带到主工程时，必须保留其不可排名属性和故障域，不能
因为“能够写入 ledger”就提升为物理或统计资格。下一步仍是正式 task driver、原生隐藏状态、
独立进程 strict replay、clean shutdown、认证目录、校准和基线；这些缺口没有被本适配层
缩小成“已完成”。

## 各层如何配合

| 层次 | 负责什么 | 现在的状态 |
| --- | --- | --- |
| 论文规则与登记 | 固定指标、实验身份、样本与权重、成功/失败/未知值规则 | 已绑定当前论文；D3 relation cells 已固定为不含 primary label，正式目录仍缺 |
| 场景与任务目录 | 给出任务、机器人池、能力、角色、时序和资源要求 | 30 个作者定义锚点，全部仍为计划状态，不能称为 30 个认证实例 |
| 可部署性与技能验证 | 判断契约是否有合法执行方案，并验证所用技能在物理环境有效 | 新增了显式整数 tick 合约的有界精确 producer/checker；30 个高层目录锚点尚未提供足够细粒度输入，任务级物理验证仍缺 |
| 运行与通信 | 统一推进仿真、执行技能、传输指令与反馈、管理资源锁 | 使用工程内 EAI；真实导航和停车边界、序列化后的通信计量已跑通 |
| 分支与恢复 | 在合格共同起点执行对照，限制并发，记录重试与清理 | 工具优化已接入；完整状态的保存与校验已实现，严格物理恢复资格尚未通过 |
| 评价与发布 | 按论文计算六维结果、区间和配对比较，阻止不完整证据进入排名 | 软件机制已建立；正式目录、校准、基线和缺陷对照数据仍缺失 |

流程中的关键依赖是：先有经过验证的实例和技能，才能冻结正式运行批次；运行保留原始
证据之后，评价器才能产生可发布结论。分支工具负责执行秩序，不能替代实例认证或证明
两个分支确实具有相同的完整物理起点。

## P4 canonical oracle 前置链

新增 [canonical_oracle_v3.py](/home/pupil/HeteroCoord/benchmark/canonical_oracle_v3.py)
和 [test_canonical_oracle_v3.py](/home/pupil/HeteroCoord/benchmark/tests/test_canonical_oracle_v3.py)。
它接收冻结的细粒度合同：机器人能力与角色、整数 tick、obligation 时序、coalition
边界与参与 tick、P2 precedence、资源容量、mutex 和 horizon/deadline。对小型合同使用
确定性的有界穷举，不做优化；每个 deployable witness 都由独立 checker 重新验证，完整穷举
且无 witness 才能生成可复核的 infeasibility proof，预算耗尽、输入不完整或 checker 不一致
一律保持 `feasibility_unverified`。

这个 producer 目前没有自动把 `task_catalog.json` 的 30 个高层锚点升级为认证实例，因为
目录没有提供完整的 robot capability matrix、resource capacity、mutex ordering 和
registered integer-tick obligation contract。现有 EAI 2-D inflated-A* 仍只是导航/能力预筛查。
因此本阶段提高的是“如何产生和检查 canonical 证据”的工程能力，正式目录认证仍未完成。

本轮又补了两个 P4 输入边界。`oracle_clock_v3.py` 按论文的 0.02 s 离散时钟执行精确
十进制编译：duration/release 使用 ceil，deadline/horizon 使用 floor；目录投影不再用
可能向上扩大的 `round`。`canonical_package_v3.py` 要求显式机器合同与冻结的论文、
目录、场景字节摘要匹配，所有输入必须位于项目目录；它不会从 `objective`、自然语言
resource 或 role 字符串推断约束。当前目录仍是 30 个 `planned/not_yet_certified` 锚点，
只能输出缺口清单。

新的 `canonical_deployment_v3.py` 已覆盖显式团队选择、capability、role、coalition、
participant tick、P2 precedence/handoff release/acceptance、P4 resource capacity/mutex
ordering 和 horizon 的小合同有界搜索，并由独立 checker 重验 witness/proof。package → contract →
solve → witness 的端到端路径已加入测试；当前 package、tick、deployment 聚焦测试共
43 项通过（14 项包绑定、11 项 tick 编译、8 项部署合同、10 项独立 checker/耦合反例）；独立 checker
不调用 producer 的搜索、witness 或校验 helper，并对 candidate、domain item 和 wall-clock 预算
分别 fail-closed。因此实现已越过“共享 helper”这一原型阻断，但仍只能称为明确合同 bounded prototype，
不能称为论文完整 oracle 或正式 `team_state_infeasible` 证书。缺完整目录实例和物理技能
资格，不能把任何 30 个锚点升级为正式 deployable。

验收补充：此实现明确是论文契约的窄子集。内部结果即使为可行或不可行，进入论文证书
边界时仍为 `feasibility_unverified`；完整角色/参与关系、交接与所有权语义尚未齐备。
手工构造的结果不能进入生产者适配入口，已有 witness 会重新校验。该入口限制用于防止
误用，不是对同进程恶意代码的安全沙箱。团队规模不可行证明的独立检查已修正。
当前 oracle 与发布相关聚焦测试 63 项通过；最新 canonical deployment 组合为 53 项 focused，主工程最近选定回归为 774 passed（以最近实际命令为准）。

## 用户指定的分支回溯优化如何融入

来源会话：`01a15819-a82f-7fb0-8088-dcd0e4f6da24`；来源提交：
`c51e86d756210e9f76623db25fbf090d282b6234`。此前 Sol 已读取会话本地记录并核对来源。

- 并发在整个采集任务范围内限流；资源释放不能确认时，停止接纳后续运行。
- 只允许预先登记的基础设施故障重试。方法超时、错误动作和预算超限保留为方法结果。
- 恢复后先逐项读回，再生成恢复收据；收据必须早于第一步执行。
- 新进程重建必须使用相应的新实验身份，不能伪装为原配对运行的简单重试。
- 回放缓存增加容量上限、淘汰策略和磁盘重新校验；缓存内存下降伴随重复解压开销。
- 失败产物保留；已有成功实验不覆盖此前的失败依据。

详细接线和测试记录见 [分支回溯接入报告](TOOL_RECOVERY_INTEGRATION.zh-CN.md)。

本轮已继续接入该会话的新提交 `90451fddd0b5b70c17df7dee4e1f363091fdb92b`，完整引入
由采集器管理的向量化共享会话。一个场景内多个槽位批量推进，每个分支保有自己的状态、
消息、随机数和槽位代次，配套保存独立账本并离线复核。工具及恢复回归 377 passed、
14 skipped；合成参考运行完成 8 分支、峰值 4 槽位，离线验证通过，但 8 分支全部仍为
论文评价不可用，未获得正式配对资格。

当前正式实验路径仍为独立进程。新工具尚无真实 Isaac/GPU 向量化认证，也未测量吞吐或
显存收益；共享进程崩溃仍是共同故障域。来源会话持续优化，后续提交需要独立差异检查
和验收。详见 [向量化接入验收](TOOL_VECTORIZED_INTEGRATION.zh-CN.md)。

## 已有真实证据能说明什么

| 证据 | 支持的结论 | 不能推出的结论 |
| --- | --- | --- |
| 本地部署检查 | EAI、EAI_assets、EAI_hmrs 都来自 `/home/pupil/HeteroCoord/source/` | 已复制独立的 Conda/Isaac 安装 |
| 导航与双向计量第二轮 | 实际完成路线并确认停车；命令和反馈合计 99,216 bit，物理测量窗口约 7.74 s | 已取得 D6 参考尺度或正式校准资格 |
| Z1 0.2 kg 组件实验 | 固定底座、预放置物体下，3 次闭爪试验和 3 次开爪对照的原始轨迹通过独立验证 | 移动接近、场景任务、团队协作、真实硬件载荷或通用抓取能力 |
| 原生恢复诊断 | 可见状态接近，但原生时钟未回退、后续轨迹不一致，失败原因可复核 | 严格共同检查点或论文因果对照已经合格 |

资源测量第一轮曾在未确认停车时结束计量，现明确作废并保留原始收据和判定说明。
Z1 记录的报告摘要按规范化 JSON 内容计算，与文件原始字节摘要不同；独立验证使用
冻结的内容摘要，不能将两种摘要混用。

## P0–P5 进度和后续顺序

| 阶段 | 当前完成范围 | 下一项实质工作 |
| --- | --- | --- |
| P0 | 当前论文绑定、条款对照、未知值与发布规则 | 作者确定 D3 最终分区定义后更新冻结规则 |
| P1 | 状态、通信、恢复、登记和评价契约 | 让真实运行端完整提供这些状态与证据 |
| P2 | 本地部署和若干单场景真实诊断 | 完整任务技能、车队及候选方法的状态覆盖与物理验证 |
| P3 | 六维计算及边界测试 | 接收正式冻结数据，保持未知和未完成状态，不提前排名 |
| P4 | 目录与校准检查机制、真实计量链、组件证据，以及带完整时序/角色/交接字段的 bounded canonical producer/checker 和项目内 package binding | 将每个释放实例补齐细粒度合同并独立认证，再执行正式校准与基线 |
| P5 | 缺陷注入、sham、归因与恢复的验收规则 | 基于认证目录执行真实对照及留出集恢复 |

下一步工程顺序：将高层目录锚点编译成细粒度合同并生成独立 oracle 证据 →
任务级物理技能验证 → 冻结正式目录与校准批次 → 基线和缺陷对照。恢复方向需要增加真实
状态覆盖后再运行原生对照，不能重复同一失败前提来累积“尝试次数”。

阶段反思：软件支持、实际测量、正式资格是三种不同结论。本轮真正前进的部分是本地
实测与证据保真；剩余工作的重点是补齐可审计的证据来源，而不是继续添加“已验证”字段。
