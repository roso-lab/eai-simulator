# HeteroCoord Goal 阶段性总结

日期：2026-10-22  
Goal：修复 ZCode/Zode 连接、启用 agent-team，并持续推进 HeteroCoord 后续任务。

## 已完成

- 建立 agent-team workspace，并登记 `zcode-connection-and-regression` 任务。
- 验证 ZCode bridge：GLM-5.3/max、build 模式、插件无诊断；主要 MCP 已连接。
- ZCode 只读回归：论文绑定/paper-state/P0–P2 89 passed，history runtime 71 passed，
  vectorized/qualification/backend/repair 61 passed，`compileall` 通过。
- 主代理新增 [source_run.py](/home/pupil/HeteroCoord/tools/benchmark_branch/source_run.py)
  evaluator-owned diagnostic contract producer，并新增 focused tests；四组回归合计
  156 passed。
- 论文镜像已同步到 Overleaf commit
  `72b6a3b975ae83743dc1d447b99edd728cb1dcdc`，包含 `sec/supp_system_state.tex`。
- 6 个 pilot candidate 已完成主代理只读一致性复核：6/6 唯一且存在，coverage、domain、
  relation、P3 subtype 与 manifest 一致；30 个 catalog anchor 均仍为
  `planned/not_yet_certified`。
- 已生成 [pilot contract artifact](/home/pupil/HeteroCoord/artifacts/catalog/contracts/72b6a3b975ae83743dc1d447b99edd728cb1dcdc/pilot_contracts.json)，显式承载 6 个候选的 obligation、milestone、capability、resource、skill、physical witness、oracle 和 certification 字段；主代理回归 159 passed，ZCode focused 验证 3 passed。
- 新增 [capability evidence inventory](/home/pupil/HeteroCoord/artifacts/catalog/contracts/72b6a3b975ae83743dc1d447b99edd728cb1dcdc/capability_evidence_inventory.json)：6 个候选各覆盖当前实际 28 条 robot resume 记录；主代理 focused tests 修正后 10 passed。ZCode 首次测试发现旧的 30 条硬编码假设，主代理已修正为从实际名册计数；随后 ZCode 只读复核确认 392 条 evidence entry 全部 `runtime_qualified=false`。
- resource/temporal 约束已生成 19 个形式化 predicate drafts，全部标记 `UNBOUND(...)` 与 `draft_pending_validation`；ZCode 复核 `test_pilot_contract.py` 为 3 passed，确认 6 个合同仍全部未认证。
- 每个 predicate draft 已补充 `input_fields`、`units`、`time_window`、`binding_status`；ZCode 逐项核验 19/19 完整，且所有 certification flags 仍为 false。
- 每个 predicate 进一步补充 `binding_spec.required_quantities`、`binding_spec.event_pair` 和 `unbound_parameters`；ZCode 逐项核验 19/19 完整，仍未提升任何 certification gate。
- 2026-10-23 针对 ZCode 发现的 4 个空事件对候选完成保守修正：新增
  `binding_candidates`，将 resource predicate 显式接到待绑定的 `resource_ledger`，将
  temporal mutex 接到待绑定的 `interval_ledger`；不把容量/区间语义伪装成 milestone
  event pair。重新生成的合同 artifact 含 19 个 predicate，主代理 focused regression
  **11 passed**，ZCode 只读复核通过；所有候选仍为 `draft_pending_validation`，认证和论文
  资格标志均为 false。
- 随后补齐 S1 的 role-obligation、skill 和 oracle 输入接缝，并加固
  `validate_pilot_contract_set` 的 pending drift rejection。主代理与 ZCode 回归均为
  **14 passed**，当前 6 合同 artifact 通过真实 validator（`validator ok 6`），任何待审
  字段漂移为 certified 都会被拒绝。
- S2 又增加了 source bundle 的可重算身份：`SourceRunBundle.reproducibility_key` 固定
  checkpoint/history/initializer/observation 四元摘要，`assert_reproducible_with` 拒绝
  同一 source_run_id 的摘要漂移。主代理与 ZCode 回归均为 **16 passed**，同 ID 双次生产
  输出 `reproducible True True False`；这仍是 evaluator-factory diagnostic seam，不是
  Isaac/PhysX physical qualification。

## 证据

- [后续任务规划](./FOLLOWUP_TASK_PLAN_20261022.zh-CN.md)
- [ZCode 回归交接报告](/home/pupil/Documents/Codex/agent-team/workspaces/heterocoord-goal-zcode-20261022/handoffs/zcode-regression-20261022.md)
- [protocol manifest](/home/pupil/HeteroCoord/artifacts/protocol/72b6a3b975ae83743dc1d447b99edd728cb1dcdc/protocol_manifest.json)
- [pilot candidates](/home/pupil/HeteroCoord/artifacts/catalog/pilot-candidates-20261022.json)

## 尚未完成与边界

当前所有 formal gates 仍为 false：physical checkpoint、history initialization、branch
isolation、closed-loop audit、statistical widening、paper score eligibility 和 rankable。
source-run 目前是 evaluator-owned diagnostic contract，不是真实 Isaac/PhysX checkpoint；
CPU/mock/vectorized 测试不能替代 GPU qualification。ZCode 的第二次 pilot audit 因权限请求
在会话超时后取消，故其结果没有被宣称为独立 artifact。

## 下一阶段

1. 由 case author/evaluator 为 6 个 candidate 提供真实 role-obligation、skill 和 oracle
   semantic binding；在收到可重算 receipt 之前继续保持 pending。
2. 接入真实 evaluator source adapter，形成可重算 source bundle/receipt。
3. 在 isolated fresh continuation 通过后，再推进 native shared-slot 与 GPU 三路 qualification。
4. 最后开展 closed-loop audit、catalog/calibration/baseline、paired pilot 和论文结果回填。

本轮又完成 S2 receipt persistence：`SourceRunReceipt.from_row()` 可从 collector-owned JSON
row 恢复并重新验证 seal，拒绝篡改、未知字段和 `physical_qualified=true`。主代理与 ZCode
回归均为 **18 passed**，往返输出 `roundtrip True False source-run-receipt.v1`。这仍是
diagnostic receipt seam，不是物理资格。

本轮进一步补充 `SourceRunBundle.manifest_row()` / `validate_manifest_row()`：manifest 只含
版本、sealed receipt 和 source identity，不携带私有 state；主代理与 ZCode 回归均为
**20 passed**，manifest 往返输出 `manifest source-run-manifest.v1 True False`。
随后将 manifest 升级为 `source-run-manifest.v2`，新增覆盖 schema/receipt/source identity 的
`manifest_digest`；identity 篡改现在会被 manifest seal 拒绝。主代理与 ZCode 回归均为
**21 passed**，验收输出 `manifest source-run-manifest.v2 True True`。

本文件是阶段性收尾，不代表研究任务最终完成。

## 2026-10-23 全量诊断回归补充

ZCode 会话 `sess_80f3a53f-d10a-4810-a7fc-9b05d9d36981` 已恢复并完成任务书规定的五步只读回归：论文绑定/paper-state/P0--P2 **89 passed**，history runtime **71 passed**，vectorized/qualification/backend/repair **61 passed**，`compileall` 退出码 0，source-run/pilot-contract/capability-evidence **21 passed**；合计 **242 passed、0 failed、0 error**。执行期间 ZCode 未修改源码、测试、catalog、论文，未启动 Isaac/Kit/GPU。

该结果只证明当前合同、合成 history runtime、vectorized 诊断和 S1/S2 receipt seam 在 CPU 侧回归稳定；不提升任何 physical/GPU qualification、closed-loop、statistical widening、paper-score eligibility 或 rankable gate。

## S2 adapter 接缝推进（2026-10-23）

为真实 evaluator source collector 预留了显式 adapter 边界：`source_run.py` 新增
`EvaluatorSourceAdapter` / `CallableEvaluatorSourceAdapter` 与
`EvaluatorOwnedSourceProducer.from_adapter(...)`。adapter 只能提供 evaluator-owned payload，
执行模型被限制为 `isolated_process`，receipt 的 `physical_qualified` 仍由 producer 硬编码为
`False`，并在 row restore 与 bundle verify 两处再次拒绝篡改。该接缝不宣称 Isaac/PhysX
连接，只为后续真实 collector 接入提供稳定入口。

主代理 focused suite 为 **25 passed**；ZCode 独立验收 adapter 增量时整组为 **24 passed**、
随后单文件补测为 **16 passed**，compileall 通过，并确认 adapter 关键边界和测试文件哈希
无漂移。最后新增的非 isolated 负向测试也已由 ZCode 单文件复跑通过。

adapter 改动后的主代理交叉回归重新通过：论文绑定/paper-state/P0--P2 89 passed，history
runtime 71 passed，vectorized/qualification/backend/repair 61 passed，adapter/pilot/
capability focused 25 passed，`compileall` 退出码 0；合计 **246 passed**。pilot artifact
validator 仍为 `validator ok 6`，认证候选数 0、论文可评分候选数 0，状态仍为
`candidate_contract_only`。

## S3 fresh initialization handoff（2026-10-23）

`SourceRunBundle.fresh_initialization_request(...)` 已把已验签的 evaluator-owned source
接到 condition-blind fresh initialization request：先验证 bundle，再绑定 checkpoint/history/
initializer digest，分配独立 `fresh_context_id`、`private_initialization_stream` 和 branch
writable namespace；condition、submitted/private state 与 outcome 注入由 source/paper-state
两层校验拒绝。主代理 source-run 测试 18 passed，S3 focused suite **27 passed**；ZCode
独立复核同样为 **27 passed**，`compileall` 退出码 0。随后主代理重新跑四组兼容性回归，合计
**248 passed**。该 handoff 仍是 CPU/synthetic diagnostic seam，不是 fresh process 或物理
checkpoint qualification。
## S3 initialization outcome receipt（2026-10-23）

主代理为 `InitializationOutcome` 增加 collector-owned JSON row 的落盘/恢复接口：
`row()` 生成 `initialization_outcome_digest` seal，`from_row()` 严格校验字段集合、schema、
seal 与布尔字段类型，并拒绝任何 `physical_hold_qualified=true` 的恢复结果。该 receipt
只记录初始化诊断计数与失败信息，不授予 physical/history qualification。

ZCode 只读复核本轮 receipt persistence：相关 paper-state/source/pilot/capability 测试
**104 passed**，`compileall` 退出码 **0**，并定位确认 `InitializationOutcome` 位于
`paper_state_v3.py:1499`；同时复核 fresh-request 边界仍先验签 source bundle、保持
condition-blind 和 `physical_qualified=false`。主代理随后重跑兼容性回归：89 + 71 + 61 + 27
= **248 passed**，全量 `python -m compileall -q benchmark tools/benchmark_branch` 通过。

本轮仍不接入真实物理 collector，不生成 physical outcome/cleanup evidence，不提升任何
qualification、paper-score 或 rankable gate。
## S3 source bundle → isolated history runtime（2026-10-23）

本轮将已验签的 `SourceRunBundle.source` 接入现有 `HistoryBlockRuntime` 与
`IsolatedHistoryBackend`，并把 checkpoint ledger 的初始化记录从内部 dataclass 转储改为
`InitializationOutcome.row()` 的 collector-facing sealed row。新增集成测试覆盖：evaluator
source bundle、4 个 spawned CPU process、ledger 读取、`InitializationOutcome.from_row()`
往返恢复，以及 `physical_hold_qualified=false` 和无裸 `digest` 字段约束。

主代理 focused suite 为 **72 history + 27 source + 68 paper = 167 passed**；ZCode 独立
复核同样为 **167 passed**，`compileall` 退出码 **0**，未发现需要修复的问题。兼容性全回归
为 **89 + 72 + 61 + 27 = 249 passed**，全量 compileall 通过。

该接缝仍是 CPU/diagnostic：spawned process 隔离不等于 Isaac/PhysX/GPU qualification，
也不改变 history initialization、branch isolation、paper-score 或 rankable gate。下一步是
在不改变 sealed row 边界的前提下接入真实 collector-owned initialization/cleanup receipt。
## S3 process cleanup receipt（2026-10-23）

为 isolated history backend 增加 `ProcessCleanupReceipt`：它只记录 context/worker、退出码、
closed/reaped、timeout 状态和 `cleanup_status`，通过严格字段集合与 SHA-256 seal 落盘/恢复；
receipt 不含任何 qualification 字段。`HistoryBlockRuntime` 在 finally 中将每个 isolated
context 的 receipt 以 `context_cleanup` event 同步写入 events ledger；清理失败仍标记
`cleanup_failed` 并抛出，不被吞掉。

主代理 history focused suite **74 passed**；ZCode 独立复核 **74 passed**、compileall
`EXIT=0`，并确认状态一致性与篡改拒绝。兼容性回归为 **89 + 74 + 61 + 27 = 251 passed**，
全量 compileall 通过。该 receipt 仍是 CPU 进程生命周期诊断证据，不能提升任何 physical、
history、branch、paper-score 或 rankable gate。
## S3 cleanup receipt identity binding（2026-10-23）

`ProcessCleanupReceipt` 现在额外绑定 `run_id`、`source_run_id` 和 `checkpoint_digest`；
`HistoryBlockRuntime` 在 finally 中从 `BranchRunStore` 与 sealed source checkpoint 显式注入
三者。`from_row()` 要求四个 identity 字段均为非空字符串，并继续执行 seal 与生命周期状态
一致性校验。测试验证恢复后的 receipt identity 与 source bundle 完全一致。

ZCode 独立复核 history **74 passed**、compileall `EXIT=0`，确认回执仍无
`qualified/physical` 字段。主代理完整回归维持 **251 passed**。这使 cleanup receipt 可用于
失败/重试审计，但仍不能改变任何 qualification 或 paper-score gate。

## S3 retry/replay identity audit 收尾（2026-10-23）

本阶段最后一项只读验收已由 ZCode（GLM-5.3/max）完成。`ProcessCleanupReceipt.assert_identity`
在 `history_process.py:63-69` 将期望的 `(run_id, source_run_id, checkpoint_digest)` 与回执自身
三元组逐项比较；任一不一致即抛出 `ValueError("cleanup receipt identity mismatch")`，一致才返回
`True`。该方法不读取、不写入任何 qualification 字段，也不改变任何 gate。

验收证据：

- `PYTHONPATH=. pytest -q tools/benchmark_branch/tests/test_history_runtime.py`：**74 passed in 12.42s**，退出码 0；
- `python -m compileall -q tools/benchmark_branch/history_process.py tools/benchmark_branch/history_runtime.py tools/benchmark_branch/tests/test_history_runtime.py`：`EXIT=0`；
- 正向测试覆盖 4 个从 ledger 恢复的 receipt，负向测试覆盖篡改 `source_run_id` 的 fail-closed 拒绝；
- ZCode 全程只读，未修改源码/测试/catalog/论文，未启动 Isaac/Kit/GPU。

因此，cleanup receipt 已具备可供后续 retry/replay audit 使用的 run/source/checkpoint identity
封条；本项标记为完成，但仍属于 CPU/diagnostic seam。真实 collector-owned initialization 与
cleanup receipt、真实物理 checkpoint、history/branch/GPU qualification 仍未接入。

## S3 retry/replay identity manifest（2026-10-23）

在 cleanup receipt identity binding 之上，新增 `ReplayIdentityManifest`（schema
`heterocoord.history.replay_identity.v1`）。它只从两个已验签来源构建：
`InitializationOutcome.from_row()` 与 `ProcessCleanupReceipt.from_row()`；每个 context 必须
在 checkpoint ledger 和 cleanup ledger 中精确配对，cleanup receipt 必须先通过
`(run_id, source_run_id, checkpoint_digest)` identity 校验。manifest 自身有严格字段集合、
SHA-256 seal、确定性 entry 排序和 fail-closed identity assertion，不包含任何 qualification、
physical 或 paper-score 字段。

验收证据：

- 主代理 `test_history_runtime.py`：**75 passed in 15.78s**；
- ZCode 独立只读复核：**75 passed in 15.75s**；
- 目标文件 `compileall`：`EXIT=0`；
- 兼容性回归：论文/paper-state/P0–P2 **89 passed**，vectorized/repair **61 passed**，
  source/pilot/capability **27 passed**；
- 新增测试覆盖 ledger 构建、row/from_row roundtrip、篡改 seal 拒绝和 identity mismatch 拒绝。

这项工作关闭了 CPU diagnostic retry/replay identity manifest 的阶段性缺口，但没有接入真实
collector，也没有提升任何 physical、history、branch、closed-loop、statistical、paper-score
或 rankable gate。

## S3 replay ledger recomputation audit（2026-10-23）

为使 manifest 真正可用于重放检查，新增 `ReplayIdentityManifest.assert_matches_ledger_rows()`。
该方法使用 manifest 自带的 run/source/checkpoint identity，从当前 sealed checkpoint/cleanup
ledger 重新构建候选 manifest，并同时比较 manifest digest 与 context-entry 集合。账本行顺序
变化不影响结果；context 缺失/新增、receipt seal 漂移或 identity 漂移均 fail-closed。

主代理与 ZCode 均验证 `test_history_runtime.py` **75 passed**、目标文件 compileall
`EXIT=0`。新增测试覆盖 reversed ledger 重算成功和 cleanup context 缺失拒绝；所有 qualification
gate 保持原值。

## S3 runtime manifest event 接入（2026-10-23）

`HistoryBlockRuntime.finally` 现已在 cleanup receipt 能力存在、所有 context 清理成功且 ledger
完整时，自动构建并写入唯一的 `kind="replay_identity_manifest"` event。该 event 只携带 source/
checkpoint identity 和 sealed manifest row。没有 cleanup receipt 的 fake backend 会跳过该路径，
不会把缺少诊断能力误报为 cleanup failure；manifest 构建或落账异常则标记 `cleanup_failed` 并
继续抛出。

主代理与 ZCode 均通过 history runtime **75 passed** 和 compileall `EXIT=0`；集成测试确认
isolated backend 产生 exactly one manifest event，并可从 ledger 重算匹配。该接入仍为 CPU/
diagnostic 生命周期证据，不改变任何 qualification gate。

## S3 manifest failure-path 收尾（2026-10-23）

新增 `test_manifest_build_failure_marks_cleanup_failed_and_reraises`，强制 manifest 构建失败并验证：错误写入 `cleanup_errors`，`store.manifest["status"]` 变为 `cleanup_failed`，store finalize 仍执行，上层继续收到 `RuntimeError("history context cleanup failed")`。ZCode（GLM-5.3/max）独立验收：history runtime **76 passed**、目标文件 `compileall` **EXIT=0**；未修改源码/测试/catalog/论文，未启动 Isaac/Kit/GPU，qualification gates 保持原值。

本轮 Goal 的 ZCode 连接、agent-team 协作和 CPU diagnostic replay/cleanup seam 已阶段性收尾。下一阶段优先接入真实 evaluator-owned initialization/cleanup collector，复用既有 sealed row、identity 和 replay manifest；在真实物理 checkpoint、history/branch isolation、GPU shared-slot 与 closed-loop audit 证据完成前，所有 formal gates 继续为 false。

## S4 collector receipt handoff seam（2026-10-23）

为真实 collector 接入补齐了 `CollectorReceiptBundle`（schema
`heterocoord.history.collector_receipts.v1`）。它把已验证的 `source-run-manifest.v2` 与由
checkpoint/cleanup sealed ledgers 重算得到的 `ReplayIdentityManifest` 绑定成一个可持久化
handoff：`row()/from_row()` 对 bundle digest、source manifest seal、source/checkpoint identity
执行 fail-closed 校验；source identity 不再由调用方自由传入，而是从已验签 source receipt
派生。bundle 明确不包含 qualification、physical 或 paper-score 字段。

主代理与 ZCode 独立验收 `test_history_runtime.py` **78 passed**，目标文件 compileall
`EXIT=0`；新增测试覆盖 collector bundle roundtrip 及 source identity drift 拒绝。该 seam
仍是 CPU/diagnostic handoff，不代表真实 Isaac/PhysX collector 或任何资格门已通过。

## S4 真实 collector 环境预检（2026-10-23）

ZCode 只读预检确认当前 **base** 环境为 Python 3.14.7，仅 `pxr` 可用，`isaacsim`、
`omni.isaac.lab` 和 `torch` 不可用，因此本轮未启动 Isaac/Kit/GPU。主代理随后核验
`env_isaaclab` 确实存在，且其中 `isaacsim=True`、`torch=True`、`pxr=True`。代码中已有
`open_simulator_session`、`LiveCheckpointBackend` 和 native Isaac tests，但尚未接到
`EvaluatorSourceAdapter`/`CollectorReceiptBundle`。

因此当前不是机器级依赖阻塞，而是运行环境切换与 adapter 接线尚未完成。下一阶段必须使用
`env_isaaclab`，由 evaluator-owned isolated process 完成真实
LiveCheckpoint 组件快照、permitted history 组装、checkpoint/readback digest、source manifest
和 collector bundle 落账。当前 `pxr` 可用不等于 PhysX/Isaac qualification，所有 gates 继续为
false。

## S4 LiveCheckpoint source adapter 收尾（2026-10-23）

新增 `LiveCheckpointEvaluatorSourceAdapter` 与 sealed `LiveCheckpointCollectionReceipt`，把真实
checkpoint backend 接到 evaluator-owned source producer 的最小边界固定下来。调用顺序为
`capture(step)` → sealed `store.read(identifier)` → `restore(identifier)` 严格 readback →
evaluator-owned state/observation/history/initializer/node projection。readback 必须同时满足
`passed=True`、`phase=readback`、step 一致和 `observable_state_checkpoint` fidelity；否则在
collection receipt 或 source bundle 生成前 fail closed。

主代理与 ZCode 独立 focused 回归均为 **99 passed**，目标文件 compileall `EXIT=0`，并在
`env_isaaclab` 中完成纯导入检查 `adapter-import-ok`。本轮没有启动 Isaac/Kit/GPU。
collection receipt 永久为 `diagnostic_readback_only`，`SourceRunReceipt.physical_qualified`
继续为 false。该增量只完成“真实 backend 到 source contract 的可注入桥”，尚未形成真实
session 运行证据，不能提升 physical/history/branch/closed-loop/statistical/paper-score/rankable
任一 gate。

## S4 native smoke 启动前审计（2026-10-23）

`env_isaaclab` 的只读 CUDA 查询确认 1 张 Quadro RTX 6000（CUDA 12.8、24 GiB、驱动
580.178.04），project-runtime doctor 同时确认三个 EAI 包全部来自当前 HeteroCoord checkout。
但现有 `test_native_isaac.py` 三个 worker 都硬编码 `device="cpu"`，并直接使用
`LiveCheckpointBackend/LiveReplayController`，没有 source adapter、cleanup receipt、replay
identity manifest 或 telemetry cleanup receipt。

因此本轮没有启动 native test。下一步先增加专用单 checkpoint CUDA diagnostic worker，记录
requested/runtime device、GPU identity、source/collection receipt 和 finally cleanup/telemetry 状态；
只有该 worker 的 fail-closed 路径完成后才运行一次受控 smoke。GPU 可见性本身不改变任何 gate。

## S4 受监督原生 source seam 阶段收尾（2026-10-23）

ZCode 连接已恢复，agent-team 工作区持续有效。主代理完成项目自有
`EAI-Factory-Z1-v0` source collector、paper-state projection 与 supervisor 接线；主代理和
ZCode 分别通过 **33 tests**，目标模块 compileall `EXIT=0`。

首次原生 CPU 产物 `source-collector-cpu-20261023-01` 被保留为失败证据：进程虽 exit 0，
但 child close receipt 未持久化。根因是 Kit 在 `app.close()` 内终止解释器，使 child context
之后的终结代码不可达。修复后由 supervisor 仅依据完整 session-close marker 回填 child receipt，
不完整 marker 继续 fail closed。

修复后的 `source-collector-cpu-20261023-02` 仅执行一次并通过独立验收：supervisor pass、
requested/actual device 均为 CPU、无 GPU lease、telemetry 为空、source/collection seals 与
checkpoint sidecar 一致、child close receipt 已持久化。该运行仍是
`diagnostic_readback_only`；physical checkpoint、history initialization、branch isolation、
closed-loop、statistics、paper score 与 rankable 七个 gate 全部保持 false。

完整阶段总结见 `STAGE_CLOSEOUT_20261023.zh-CN.md`。总体 Goal 继续 active；下一阶段先设计
physical hold/readback/tick receipt，再经评审决定是否启动最小 GPU/shared-resource 实验，不自动
启动 CUDA。
