# HeteroCoord fresh-history qualification 后续执行计划

日期：2026-10-23  
论文基线：`72b6a3b975ae83743dc1d447b99edd728cb1dcdc`  
输入证据：`artifacts/project_runtime/source-collector-cuda-20261023-02/`  
状态：设计与 CPU/mock 验证阶段；七项 formal gate 全部保持 `false`

## 1. 当前科学边界

干净的 CUDA `-02` 只证明 evaluator-owned source checkpoint 能在隔离 GPU 上完成 capture、
sealed readback、固定 tick hold、source/collection/physical receipt、lease 与 cleanup 闭环。它没有
执行论文要求的 fresh submitted-system initialization、初始化后 condition assignment、真实 branch
continuation、K=3 复用、closed-loop audit 或 statistical widening。

下一阶段的最小科学问题是：能否从同一 evaluator-owned source lineage 启动四个全新隔离上下文，
在任何 condition 信息释放前完成初始化并持久化 collector-owned outcome/cleanup receipts，再在统一
barrier 后分配 fact/reference/sham/intervention。该阶段先证明因果顺序和隔离身份，不生成论文分数。

## 2. 不重复造轮子的接线原则

复用现有稳定接口：

- `SourceRunBundle.fresh_initialization_request`：condition-blind 初始化请求；
- `HistoryBlockRuntime`：admission → 四上下文初始化 → all-terminal/full-budget barrier → assignment；
- `IsolatedHistoryBackend`：spawned process、close/reap 与 `ProcessCleanupReceipt`；
- `ReplayIdentityManifest` / `CollectorReceiptBundle`：sealed ledger 重算；
- `aggregate_history_stores`：仅在三个 finalized store 上构建 K=3 diagnostic receipt；
- `PhysicalHoldReadbackReceipt`：绑定 `-02` 的 source/checkpoint/device/cleanup 身份；
- `run_project_probe` 的 telemetry、selected-GPU compute inventory 与 lease guard。

不直接把 `HistoryBlockRuntime` 的 `protocol_synthetic` 改成 formal native。先新增一个薄的
collector-owned qualification runner，负责验证输入 artifact、冻结执行计划、调用一次既有 runtime，
并从 finalized ledger 重算 receipt；runtime 与 receipt 均不得自授 formal qualification。

### 实现前必须关闭的三个 P1

Sol 流程审查（`handoffs/s6-fresh-history-plan-review.md`，109 passed，SHA-256
`ec6e4022d247a1c62a0ca92eae0f11e45bc4b0ab49a1490cf9080a137321d44a`）确认没有 P0，但指出三项
实现阻塞：

1. `-02` manifest 只有 digest，不能独立重建 `HistorySource`。必须有 evaluator-owned、按
   manifest/checkpoint/history/initializer/observation 四元 digest 校验的 materialized input bundle；
   loader 在 child 启动前拒绝 stale、越界、symlink 或 sidecar 漂移。
2. private stream/namespace 目前只是字符串标签。runner 必须额外封存 capability root、private
   RNG、tool/model endpoint namespace、writable byte budget 和 cleanup root，并审计实际写入/外部
   调用；字符串对齐本身不能证明 branch isolation。
3. `HistoryBlockRuntime` 只保存 device 字符串，不能承担真实 GPU admission。必须由外层 supervisor
   管 telemetry/selected-GPU compute baseline/after、UUID、lease、child close/reap 和 release；任一
   inventory error、非空 baseline、UUID drift 或 cleanup residue 均 fail closed。

在这三项解决前，S6.1 只能做 contract/loader 的 CPU/mock 实现，不能启动新的 CUDA/native runner。

## 3. 最小 runner 输入

runner 必须在创建输出目录和启动 child 前完成以下验证：

1. `-02/supervisor.json`：process/receipt pass、compute baseline/after 为空、GPU UUID 一致、lease
   acquire/release、session close 和 telemetry cleanup 完整；
2. `-02/run/result.json`：七项 gate 全 false；source manifest、live collection receipt 与 physical
   hold receipt 均可严格恢复；三者 source/checkpoint/manifest/collection identity 一致；
3. checkpoint artifact sidecar 与当前文件 SHA-256 一致；
4. 运行计划预注册 `run_id`、`repeat_id`、assignment seed、paper/source/checkpoint digests、四角色
   roster、private stream/namespace 规则、requested/actual device 和执行环境；
5. 真实 GPU 执行前再次执行 telemetry、compute-app 和 lease admission；CPU/mock 只验证状态机，
   不伪造 GPU receipt。

## 4. 新增 sealed receipt 的最小字段

建议 schema：`heterocoord.fresh_history_initialization.v1`。receipt 只汇总可重算事实：

- paper/source/checkpoint/source-manifest/collection/physical receipt digests；
- run/repeat/execution-model/requested-device/actual-device/runtime/lease digests；
- admission event digest 与其早于全部 initialization start 的证明；
- 四个 context 的 fresh id、private initialization/policy stream、writable namespace；
- 四个 `InitializationOutcome` digest 与 terminal status；
- barrier、assignment seed/digest、四角色 roster，以及 assignment 晚于全部 terminal/full-budget barrier；
- 四个 `ProcessCleanupReceipt` digest 与一个 recomputed `ReplayIdentityManifest` digest；
- artifact/input hashes、session/telemetry/compute cleanup；
- `qualification_status=diagnostic_fresh_history_review_pending`；
- 七项 formal gate 字段硬编码为 `false`，恢复时任何 `true` 必须拒绝。

receipt 不保存 submitted-system private state、condition-before-barrier 内容、未来 outcome 或可写共享状态。

## 5. 实现批次与停止条件

### S6.1：artifact admission + receipt contract（CPU/mock）

- 新模块只读加载 `-02`，严格核对 seals、identity 和七项 false gates；
- 新 receipt 支持 `create/row/from_row`，拒绝缺字段、未知字段、篡改、identity drift 与自授权；
- 单元测试覆盖污染 `-01`、compute/telemetry drift、source/physical mismatch、gate true、seal tamper；
- 验收：focused tests、compileall、diff-check；不启动 CUDA/native。

### S6.2：single-repeat fresh isolated runner（CPU/mock）

- runner 复用 `HistoryBlockRuntime` 与 `IsolatedHistoryBackend`，只接受新输出目录；
- 从 finalized store 重算 initialization/cleanup/replay/admission/barrier/assignment 证据；
- method failure 保持 method failure，不转成 infrastructure failure；cleanup 不完整则整次 fail closed；
- 验收：正常、timeout、malformed、child crash、cleanup failure、重复 run/output 拒绝；gate 全 false。

### S6.3：single-repeat CUDA/native diagnostic

- 仅在 S6.1/S6.2 主代理与独立审查均 ACCEPT 后执行；
- 使用新目录、空 GPU、pre/post compute inventory、telemetry baseline、lease 和 finally cleanup；
- 只跑一个 scene、一个 checkpoint、一个 repeat、四 fresh contexts，不重试、不跑 K=3；
- 任何环境/identity/cleanup 异常保留 artifact 并停止；不覆盖、不删除、不升级 gate。

### S6.4：K=3 shared-resource CUDA evidence

- S6.3 通过后才执行 r1/r2/r3，三个独立 finalized store 各自使用 fresh contexts；
- `aggregate_history_stores` 从账本重算 12-context receipt；不得由 caller 拼接摘要；
- 增加 slot/namespace/private RNG/exogenous stream/retry identity、condition-blind assignment 与
  cleanup/replay 审计；仍需独立资格评审后才讨论 history/branch gate。

### S6.5：closed-loop 与统计阶段

- 从 shared blocks 注册抽样并在 isolated process 重放 brain decisions；
- 形成 discordance、affected-block、rerun 和 hypergeometric widening receipts；
- 只有 physical/history/branch/closed-loop/statistical 五层 receipt 均通过，才评估
  `paper_score_eligible` 与 `rankable`，随后才允许论文结果回填。

## 6. 当前明确禁止

- 不把 `-02` 或 CPU/mock history store 称为 formal physical/history qualification；
- 不直接扩大到多场景、多 checkpoint、K=3 或统计矩阵；
- 不用 requested `cuda:0` 字符串代替实际 GPU/lease/compute evidence；
- 不复用失败输出目录，不覆盖 `-01`/`-02`，不杀无关用户进程；
- 不修改论文结果、表格、分数或排名叙述。

## 7. 最近可执行下一步

先完成 S6.1 的 artifact materialization loader、namespace/resource contract 与 sealed receipt
contract，并用 CPU/mock 反例固定 fail-closed 边界；随后让 Sol 做独立代码审查、ZCode 执行既定
测试。只有三项 P1 都关闭且 S6.1 验收完成，才进入 single-repeat runner，更不会直接启动下一次
CUDA/native workload。

## 8. 2026-10-23 实现状态更新

S6.1 的 materialized loader 已改为从 raw checkpoint/history/initializer/observation 内容重建
`PhysicalCheckpoint`、`PermittedHistory`、`FreshInitializer` 和 observation，并把重建 receipt 与
manifest receipt/source identity 精确对账；不再信任 sidecar 自报 digest。index-only row 不可直接
用于 spawn。未来 native collector 会在新 artifact 中写出 `materialized_source/` 五文件；已有 `-02`
保持不可变，不能直接升级为该输入。

CPU/CUDA resource policy 已改成互斥 union，但仍只是请求合同。Sol 后续审查把剩余阻塞细化为：

1. 四 context 的 evaluator-owned capability allocation/enforcement/audit；
2. 外层 supervisor 在 lease 内完成 baseline → owned inventory → close/reap → empty after → release；
3. 保持 `HistoryBlockRuntime` 为 `protocol_synthetic`，另建 S6.3 native diagnostic seam。

因此最近下一步调整为先实现 capability runtime protocol、sealed audit receipt 与 CPU/mock 负向矩阵，
再实现 outer resource supervisor；在这两层 ACCEPT 前不进入 single-repeat CUDA/native。

## 9. S6.1 收尾与 S6.2 capability runtime（2026-10-23）

ZCode GLM-5.3/max 已对 S6.1 冻结实现完成 R2 复测：五文件组合 **133 passed**、native-source
probe **32 passed**，compileall 与 scoped diff-check 均为 exit 0，结论 ACCEPT、无 P0/P1；handoff
SHA-256 为 `1a3feb42940df833066edb1a59427a2f864403cd0d84f297e0d57083783172e4`。该证据关闭
materialized admission 的实现验收，不改变 `-02`，也不产生 formal qualification。

随后完成四 context evaluator-owned capability runtime 的 CPU/mock 实现。每个 context 具有唯一的
allocation、capability/write/cleanup root、initialization/policy RNG commitment、tool/model endpoint；
只有同一 repeat 的 exogenous stream 可共享。目录创建使用 dirfd + `O_NOFOLLOW`，preflight 后的
symlink swap 会在 allocate 时再次拒绝；中途失败会事务性撤销本轮新建目录。初始化 RNG 与 policy
RNG 按 common barrier 分阶段开放，四 context assignment digest 必须一致且为 SHA-256；tool/model
合计调用预算、写入字节预算、写内容/tree/event ledger、hardlink/symlink/FIFO/socket/空目录与 root
swap 均进入 close audit。close 后不可复用，同一 context 不可重复 audit。

Sol/high 独立审查先后发现并推动关闭多项真实反例，包括 TOCTOU、partial allocation residue、abort
误判 accepted、launch relabel、特殊文件漏审计、短 assignment digest、plan 字段注入及 partial-cohort
receipt 不可重算。冻结版本最终结论为 ACCEPT、无剩余 P0/P1/P2；focused **32 passed**，handoff
SHA-256 为 `196c305d073d781c54f4a5724ecd022f8a24e6c5e0a0eb763977458f560e27b8`。主代理在同一冻结版本
上的八文件组合回归为 **206 passed**，compileall 与 scoped diff-check 通过。

这一阶段只关闭 CPU/mock capability contract，不声称 OS/native isolation；receipt 明确固定
`enforcement_mode=mock`。下一个实现批次是 outer `ResourceGuard/FreshHistoryRunSupervisor` 的 CPU
N/A 生命周期、事件顺序和失败清理测试；在它与 synthetic/native seam 均独立 ACCEPT 前，不启动
新的 CUDA/native run，不扩 K=3，不修改论文结果，七项 formal gate 继续全部为 `false`。
