# 当前论文与工程对齐进展

> **当前权威覆盖（2026-10-27）**：冻结论文 ref 已更新为
> `overleaf-old/main@dac7b94207a2683d494bdb388fc48533c9219bdf`。scorebook、composite、
> task catalog、31 锚点合同、map-ready 编排以及 P4 package/mapping/receipt 的当前发布面
> 均使用该身份，并显式拒绝旧 `0b006d4…` 身份。论文新增的八个 contract-only difficulty
> 原始量、固定分箱、外部冻结 roster/common-pair registry 和可重算 pilot/fresh 认证契约
> 已在代码中实现；生产 certification registry 工件、真实 pilot/fresh 观测、物理资格和
> GPU 运行尚未形成，因此所有目录 difficulty 状态仍为
> `difficulty_certification_pending`，所有正式资格门保持 `false`。
>
> 下文保留 2026-10-21 至 2026-10-24 的阶段记录，用于解释演进过程；其中旧 SHA、旧测试
> 数和当时的 pending 结论均是历史证据，不是当前身份权威。

> **基线说明（2026-10-24）**：本文档原按 `1f329494e755523692f5f96c8e0889c6cec15a82`
> 编写，仅保留为历史差异记录。当前冻结的权威 Overleaf ref 是
> 当时的 `overleaf-old/main@0b006d4efcd25d5160d4b0a6e75cddd9e5265e44`；本地论文工作树仍在
> `72b6a3b975ae83743dc1d447b99edd728cb1dcdc`，不得把它误称为最新权威基线。
> 当前语义迁移状态仍为 `migrated_identity_semantics_pending`，详见
> [论文语义迁移审计](PAPER_SEMANTIC_MIGRATION_AUDIT_20261024.zh-CN.md)。

论文基准：`1f329494e755523692f5f96c8e0889c6cec15a82`（Overleaf old remote 于 2026-10-21 15:45 更新）。此前工程基线 `75ceff6`、`bb82b7b` 与 `af12f6d` 已过时。
工程目录：`/home/pupil/HeteroCoord`。日期：2026-10-21。

当前成果是工具接入、本地部署以及新论文统计协议的工程修正，尚未形成正式论文实验结果。
本次没有修改论文，没有提交或推送代码，也没有把历史物理诊断升级为新源码的资格证明。

## 1f32949 新增协议—工程差异矩阵

| 论文收紧项 | 工程当前状态 | 验收边界 |
| --- | --- | --- |
| fixed-system disruption / no retraining | 已登记为 P0 条件要求，尚未形成正式 disruption 批次 | 不能把 team size、task scale、observation setting 或 skill fidelity 的变化当成同一固定系统效果 |
| paired skill-failure rerun | 登记对象与进程内消费 ledger 已校验；attempt receipt 现在可严格序列化，并能按 branch 进入 scheduled-run 统计入口 | 尚未接入正式 task driver 或跨进程执行账本；未授予物理配对资格；方法失败 fail-closed |
| incomplete paired block | `unknown-record` 与 `pair-invalid` 在统计层分别保留，不再被合并成 method failure 或普通 unknown | 原始记录必须保留；pair-invalid 只影响相应配对 estimand，fact branch 能否独立报告仍由上游 coverage adjudication 决定 |
| external contention | 注册草案要求 frozen-time request、外部 holder/resource、与机器人请求相同的线性化合同和固定 hold window | 当前 `TypedLockManager` 尚无排队/grant/hold receipt 接线；系统自身拥堵不能当 treatment |
| protective stop | 注册草案要求 targets/controller、stop dynamics、verification receipt、load-state checkpoint、onset/release 和 reset 摘要 | 摘要格式校验不证明真实 dynamics；尚无命令拒绝、世界继续运行或 reset receipt 接线 |
| D3 obligation ledger | `completed/violated/unreached/unknown/incomplete` 已互斥；严格 JSON ingress、参与者/tick/evidence 校验和 relation-run ledger 摘要已贯通 | 仍需正式事件表和真实 obligation 记录；数值同为零不会抹掉 violated/unreached 的审计差异 |
| D2 unknown | unknown 在 scheduled repeat 层完成后再聚合；高端按最小固定权重 reference 例外；区间使用最新论文的有限样本 retention set | 不删除 unknown、不事后重加权；实现正确不等于已有正式 D2 数据 |
| shared-session GPU branches | 同一 Isaac Lab session 的 branch slot 可并行；每 slot 的 state/locks/queues/brain/logical clock 私有；必须与 isolated execution 做 qualification/audit re-execution | 当前 shared-session 仅有工程诊断；GPU receipt、slot 私有性、ledger/离散观测等价和 isolated audit 尚未完成，不能进入正式证据 |

## 整体框架

工程沿用一条清晰的责任链：论文定义任务和评价规则；方法产生组织和协调决策；车队接口
管理消息、权限和任务派发；当前项目中的 EAI 执行技能与物理仿真；独立评价器把实测轨迹
转成成功、失败或未知的记录；统计层据此计算六维结果；最后由发布门检查证据是否完整。

分支回溯工具位于执行与证据之间。它限制同时运行的分支数量、管理资源回收、保留失败
记录并按注册规则重试。现在已经与主工程恢复状态接口连接，并补上可校验的磁盘检查点。
存储层能在新进程中还原数据；诊断恢复桥仅允许原活跃进程加载。可信物理驱动和原生状态
完整恢复仍未完成，因此它目前承担诊断恢复，不能直接授权正式的同身份物理重跑。

## 本阶段已经改变的行为

| 范围 | 现在的行为 | 解决的问题 |
| --- | --- | --- |
| 论文身份 | scorebook、注册与新报告绑定 `1f32949`，拒绝 `75ceff6` 及更旧提交 | 避免工程内部一致却引用旧指标 |
| D2 通信适应 | 主分数使用有限样本的配对 retention 区间；同时 retention bands 仅用于 operating boundary | 主分数不再误用边界区间；零分母和未知记录必须保留有限样本覆盖语义 |
| D3 关系协调 | 显式选择统计分区、提供冻结面板并检查关系支持；缺失时 incomplete | 避免从实际成功样本反推统计目录或删除空组再分配权重 |
| 未知记录 | 物理基础设施未知按注册规则重试并保留原记录；仍未知时六个维度都保留固定权重的 identification range，D2 在两端保持参考率约束 | 不把缺失填为失败，也不事后改变统计目录 |
| D1/D4 评价器 | 新增首次 committed plan、可行性声明、七类适用检查、注册价值密度、连续服务稳定窗口、终止优先级和外部时间线接线 | 仍消费 caller-supplied evaluator facts，未授予物理或论文资格 |
| D5 跨场景适应 | 不足 30 案例的家族独立披露并排除；其余合格家族等权计算 | 一个不足量家族不再使所有合格家族的结果一并变成 NA |
| D6 资源节省声明 | 单独检查资源 Pareto 改善以及成功率、固定时限进度均不劣 | 更早失败导致的低消耗不能被当作节省 |
| 发布 | incomplete、unstable、NA 和范围结果均不能被当作确定排名 | 计算有输出与证据足以排名分开判断 |

D6 新入口只判断已经算好的注册区间是否满足声明条件，不负责生成物理证据，也不替代
原始数据校验。它显式保持 `physical_qualified=false`。

## 已解决的论文版本差异

最新 `1f32949` 已明确 relation cells 不包含 primary label，并新增 shared-session GPU branch、isolated-execution qualification 和 audit re-execution；primary label 只用于实例级显示/分层，fixed-system disruption、paired rerun 与 protective-stop/external-contention 约束继续有效。
工程将 `domain_difficulty_condition` 作为正式 D3 relation partition，仍保留 primary-stratified
模式供显式诊断使用，正式注册不再因旧冲突阻断。
该分区问题已按最新论文解决；历史报告中“等待作者确定”的文字不再代表当前状态。

## 验证与产物

本轮 `75ceff6` 论文身份和新增注册契约的聚焦测试为 **69 passed**；P3 协议—统计贯通阶段
聚焦测试为 **78 passed**，完整 `benchmark/tests` 为 **474 passed**。随后经项目本地
运行边界执行的选定回归为 **798 passed in 5.13s**，`py_compile` 与 `git diff --check`
均通过。这些结果只证明论文身份绑定、注册拒绝逻辑和既有软件路径兼容，不证明真实
paired rerun、external contention、protective stop 或 fixed-system disruption 已经跑通。

主工程最新合并验证为 **706 passed**（含持久化、恢复边界、真实资源测量入口及旧场景入口的
fail-closed 边界），命令为：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /home/pupil/miniconda3/envs/env_isaaclab/bin/python \
  -m tools.project_runtime exec -- python -m pytest -q \
  benchmark task/tests algorithm/skills/tests algorithm/heterocoord/tests \
  algorithm/common_coordination/tests tools/validation/test_topology_surfaces.py \
  tools/validation/test_topology_aerial_authoring.py
```

这是选定的工程回归范围，不是全仓所有测试。D1/D4 阶段合并为 642 passed，聚焦与发布
回归为 87 passed；ZCode 独立重跑 D1 为 27 passed。工具集此前单独验收为 315 passed、14 skipped，
本阶段没有改动该工具集。三个正式资格标志保持 false。

历史工程夹具位于 `artifacts/p0-p5/acceptance-bb82b7b/`；它们全部标明
`synthetic_engineering_fixture` 或 `engineering_acceptance_only`，不能用作论文结果。
D3、D5 已通过保存输入的命令行重算，与直接报告逐字节一致。旧验收目录和旧 native
证据均保留，旧源码指纹不会被改写。

ZCode 实际执行部署检查，确认 EAI、EAI_assets、EAI_hmrs 都从当前项目 `source/` 导入，
工作目录为当前项目，`external_editable_environment_modified=false`。共享 Conda/Isaac
环境仍复用；源码、资产缓存与运行输出使用当前工程路径。后续新运行了一次 GPU 恢复
诊断：底层物理步从检查点的 162 步留在 242 步，环境步数虽然恢复，原生时钟和后续
轨迹对照均未通过。详见 `DURABLE_RECOVERY_PROGRESS.zh-CN.md`，没有获得物理资格。

独立审查后又收紧了 D3 发布入口：诊断允许的低样本下限不能进入正式统计证据，且 formal
release 会把报告下限与实验注册下限比较。D6 仅返回 `numerical_gate_passed`；
`publication_ready=false` 固定保留，因为现有区间对象没有携带完整的共同支持、注册方法
和原始数据校验收据。该早期阶段为 67 项重点回归、609 项合并回归；随后 D1/D4 阶段
扩展到 642 项，持久化阶段为 690 项，资源测量阶段为 696 项，旧场景边界阶段为 706 项；
其独立验收数见阶段报告，
不将历史数字相加。

## 阶段反思与后续顺序

## 新论文后的 GPU-first 重排（G0–G6）

新论文允许 shared-session，但把它定义为必须经过 GPU qualification 的执行模型，而不是默认的正式证据路径。后续顺序固定如下：

| 阶段 | 目标 | 必须产生的证据 | 失败结论 |
| --- | --- | --- | --- |
| G0 | 冻结论文、GPU runner、Isaac/Isaac Lab、driver、GPU UUID、actual physics/controller device 与执行模式身份 | 版本/来源/设备/slot-map manifest | 任一身份缺失或 requested≠actual：`pending`，不得进入 paired run |
| G1 | 单场景真实 GPU 物理与任务收据 | checkpoint readback、物理 predicate、GPU UUID、lease/memory、clean Kit shutdown、telemetry cleanup | CPU fallback、无 clean shutdown 或残留 telemetry：仅 diagnostic |
| G2 | 同一 block 的 uninterrupted、isolated-process restore、shared-session restore 三路 qualification | slot 私有性、slot permutation、batch range、邻居负载/回滚、slot reuse、hold、并发会话；ledger/离散观测/技能结果/里程碑精确一致，连续状态满足 `tau_replay` | 任一分歧、buffer warning 或 active crash：shared-session 不合格，active block isolated rerun |
| G3 | shared-session 的 isolated open-loop audit | hash 抽样、brain-decision 重放、`n_dis/aud/N_blk` 上界和 `eta_exec` widening | audit 缺失或 widening 超限：取消该配置资格 |
| G4 | 一个正式 paired pilot（reference/fact，必要时 sham/intervention） | 固定 checkpoint、branch identity、random/controller substream、完整 ledger | `pair-invalid`/`unknown`/`NA`，不排名 |
| G5 | P4 认证目录、canonical oracle、独立校准和合格 baseline | 可重算 package、独立 checker、physical witness、GPU calibration | 高层 anchor、CPU oracle 或字符串 digest 不得认证 case/baseline |
| G6 | P5 nominal/fault/sham/intervention 与留出集恢复 | 固定缺陷事件、独立 audit、归因区间、held-out recovery | coverage/sham/audit/留出证据缺失：归因 unsupported |

G2/G3 完成前，shared-session 只属于工程诊断；所有 `rankable`、`physical_qualified`、`paper_score_eligible`、`process_isolation_qualified` 和 `native_full_state_qualified` 继续为 `false`。任何 Isaac/Kit 运行都必须在结束后执行 `pkill -9 -f omni.telemetry.transmitter`，并把清理结果写入运行收据。

P0 反思：更新论文提交号必须伴随规则差异审查。绑定完成只表示新产物引用正确版本，
不能表示全部评价机制已完成。本轮重新核对原文纠正了 D2 区间类型与 D3 分区的初次审计
结论，并把无法自动判断的部分显式保留为阻断。`75ceff6` 再次说明版本号替换不是协议
完成：只有把 fixed-system、whole-paired-block retry、frozen-time queue、load-state checkpoint
和 obligation disposition 变成可拒绝非法输入的契约，工程才真正随论文前进；即使如此，
契约测试仍不能替代原生运行证据。

P1/P3 反思：未知值、支持目录、统计单位与发布状态必须同时修改。只改公式会留下其它
入口绕过覆盖门或重现旧结果，因此回归检查同时覆盖直接构造、命令行重算和发布接口。

后续阶段已补齐 D1 首个提交计划的适用类别检查与双可行性支持、D4 外部时间线的价值
密度/需求区间/稳定窗口评价，详见 `D1_D4_EVALUATOR_PROGRESS.zh-CN.md`。这些入口
消费外部评价事实，仍不等于真实物理语义检查器。后续仍需推进完整车队/方法/事件的
共同起点和可信恢复；独立容量与资源校准；
认证实例目录；真实基线及预埋缺陷、sham、留出集恢复。完成这些证据前，P4/P5 的正式
科研实验保持未完成。

实际资源测量前置链已经完成一次修正后原生验证：命令和反馈共99,216bits、模拟物理
时间约7.74s，终止反馈等待停车确认。它尚无正式目录/技能资格和完整独立校准批次，
因此没有生成D6参考尺度。首次过早终止反馈的测量已追加无效判定，原件保留。
详见 `RESOURCE_MEASUREMENT_PROGRESS.zh-CN.md`。

另外生成了一个真实的 Z1 组件级物理证据：0.2 kg、3 次 close 与 3 次 open-control
配对试验经独立 verifier 判为 E7，但其范围仅是预放置物体的固定底座抓取—抬升—转移—
释放。它没有改变 30 个任务锚点的 `planned/not_yet_certified` 状态，也没有授予移动、
团队或硬件资格。证据索引位于 `/home/pupil/HeteroCoord/tmp/physical_calibration/`。
