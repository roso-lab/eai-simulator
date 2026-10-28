# GPU / shared-session 阶段重排（历史记录）

> **基线说明（2026-10-22）**：本文档按 `1f329494e755523692f5f96c8e0889c6cec15a82`
> 编写，保留其 GPU/shared-session 分析但不再作为当前论文依据。当前唯一有效论文基线是
> Overleaf `72b6a3b975ae83743dc1d447b99edd728cb1dcdc`；执行顺序已并入
> [后续任务规划与阶段性收尾](FOLLOWUP_TASK_PLAN_20261022.zh-CN.md)。

日期：2026-10-21
论文基准：`1f329494e755523692f5f96c8e0889c6cec15a82`
工程目录：`/home/pupil/HeteroCoord`
论文目录：`/home/pupil/CVPR2026_Template`

## 结论先行

最新论文把同一 GPU-parallel Isaac Lab session 的并行环境槽位定义为允许的执行拓扑，但没有把它直接视为合格证据。正式 paired evidence 之前必须完成：

1. uninterrupted、independent-process restore、shared-session restore 三路 qualification；
2. slot 私有状态与调度不变性验证；
3. hash 抽样的 isolated open-loop audit；
4. shared-session crash/physics-buffer warning 的 active-block 失效和原身份 isolated rerun；
5. GPU runner 的实际设备、身份链、容差、显存/租约和 clean shutdown 收据。

因此，当前主工程不是“CPU 验收”，也不是“shared-session 已通过”：CPU 结果仅为协议/单元诊断；已有 GPU task-body 运行因 `session_close_observed=false` 仍是 diagnostic，不能升级为物理或论文证据。

## 论文条款到工程结构

| 论文要求 | 工程承载位置 | 当前状态 |
| --- | --- | --- |
| GPU-parallel Isaac Lab slots | `tools/benchmark_branch/vectorized.py`、`vectorized_executor.py` | 有 managed vectorized contract；当前主要是合成 backend，未完成真实 Isaac GPU qualification |
| 每 slot 的 robot/object/controller/skill、locks/queues/routes、message buffers、brain memory、logical clock 私有 | `VectorizedSession.bind/restore/step`、slot/channel ledger | 有静态隔离与读回校验；真实 PhysX hidden state、完整 EAI fleet state 尚未覆盖 |
| branch identifiers/random keys 不依赖调度 | `BranchJob` identity、pair/random namespaces、slot binding | 有身份字段与校验；需在 GPU slot permutation/batch qualification 中实测 |
| 连续轨迹允许 GPU 数值差异 | restore deviation / manifest tolerance | 有归一化容差接口；尚无 GPU 三路实测及 `eta_exec` widening 收据 |
| ledger、离散 observation、skill outcome、milestone order 一致 | vector ledgers、validation、isolated audit | 有协议校验；无真实 shared-vs-isolated differential 证据 |
| active shared crash 使 active blocks 失效 | `vectorized_executor.py` exception path、R7 regression | 已修复 pending block 误归因；生产 watchdog/isolated rerun 仍未接线 |
| formal shared-session release gate | `benchmark/release_v3.py`、`verify_p0_p5.py`、qualification flags | 继续 fail-closed，所有正式门为 `false` |

## G0–G6 执行顺序

### G0：论文与 GPU runner 身份冻结

冻结 paper SHA、Isaac Sim/Lab 版本、driver、GPU UUID、physics/controller actual device、项目 `source/` provenance、execution mode、batch range、slot map、manifest tolerance、`tau_replay`/`eta_exec` 和 qualification record。

验收：requested device 以 `cuda:*` 开头，`torch.cuda.is_available()`、GPU UUID、actual physics device 均可由 runner/observer 读取，且来源不是 caller 自报。缺任一字段就停在 `pending/infrastructure_invalid`。

当前结果：GPU 可见性与 UUID 收集接口已存在；当前纸面基准已切到 `1f329494...`。

### G1：单场景 GPU 物理收据

在项目内 `/home/pupil/HeteroCoord/source/` 运行一个最小真实 Factory 场景，显式启用 GPU physics；记录 checkpoint readback、物理 predicate、lease/memory admission/release、source/asset/controller digest、session/slot identity 和 clean Kit shutdown。运行前后记录磁盘/内存；结束强制执行：

```bash
pkill -9 -f omni.telemetry.transmitter
```

验收：actual physics device、GPU UUID、物理结果、clean shutdown、telemetry cleanup 同时存在。任一缺失只能标为 diagnostic。

当前结果（2026-10-21 G1 复验）：项目内 Factory 导航探针在 `cuda:0` 上完成，实际物理设备为
`cuda:0`，GPU UUID 为 `GPU-aa40060e-cb9d-b04a-e70c-4c91cbb67fb0`；三个 EAI 包均解析自
`/home/pupil/HeteroCoord/source/`，独立导航评价和子进程返回码均通过。Isaac/Kit 在 native
`app.close()` 后可能直接终止子进程，因而子收据来不及回写 `session_close_observed`；监督器现改为
读取项目内原子关闭 marker，并确认 cleanup callbacks 完成、app close 已请求且无清理错误。G1
监督收据：`artifacts/project_runtime/gpu_navigation_20261021_r3/supervisor.json`。
遥测清理命令已执行且无残留：`pkill -9 -f omni.telemetry.transmitter`。该结果仍只属于
single-scene GPU diagnostic evidence，`paper_score_eligible=false`，不能进入 G2 正式 qualification。

### G2：三路 shared-session qualification

对同一个 registered block，在相同 GPU/config/scene 下比较：

- uninterrupted execution；
- independent-process restoration；
- shared-session slot restoration。

覆盖 slot permutation、batch size/range、neighbour load/treatment、neighbour rollback、slot reuse、brain hold、concurrent sessions。比较 evaluator-owned outputs、branch IDs、random keys、logical clocks、queues/lock ownership、skill outcomes、milestone order；连续物理状态只允许在注册的 `tau_replay` 与 endpoint `eta_exec` widening 内差异。

失败规则：任一路径不一致、slot 非私有、physics buffer warning 或 shared crash，则 shared-session 不合格；当时 active/in-flight blocks 以原 identity/random keys 转 isolated rerun，queued/not-started blocks 保持 queued/not-run。

### G3：isolated open-loop audit

在 G2 通过的配置上按独立 hash 抽样 shared blocks，把 logged brain decisions 在 isolated process 重放，比较离散/连续 observation 与 scored ledger，计算 `n_dis/aud/N_blk` 的 hypergeometric 上界，并将最坏 widening 计入 `eta_exec`。

验收：audit coverage、discordance 上界、widening 和受影响 block 清单全部可重算；超过 `eta_exec` 就撤销该配置资格。

### G4：最小 paired pilot

仅在 G2/G3 通过后运行一个完整 `(instance, system, repeat)` 的 reference/fact block，必要时加入 sham/intervention。每个 branch 从同一 checkpoint 恢复，保留实验号、存档点、random stream、controller substream、slot/session identity 和完整 ledger。

任一 branch 为 `pair-invalid`、`unknown-record`、sham mismatch 或 support 不完整时，保留原始记录并返回 `NA`/范围，不生成排名。

### G5：P4 正式目录、校准与 baseline

把细粒度 canonical package 交给独立 oracle checker，绑定 physical witness、GPU calibration、observation/skill/budget/fixed-system contract 和 artifact manifest。高层场景锚点、CPU oracle、字符串 digest 或单场景成功都不能直接认证 case/baseline。

### G6：P5 缺陷归因与留出恢复

只在 G5 认证 GPU case 上运行 nominal/fault/sham/intervention，并用 held-out case 验证选择性下降、名义等价、因素交互和恢复。缺少 sham、独立 audit、固定缺陷事件或留出覆盖时，结果只能是 unsupported。

## 当前阻塞与不可升级条件

- `shutdown_watchdog` 尚未接入生产 `vectorized_executor`，正常路径仍写 `shutdown_status=pending`；不能把 caller-provided observer 当作 collector-owned 证据。
- 当前 vectorized 测试是合成/CPU；它们证明状态机和 fail-closed 语义，不证明 Isaac/PhysX/GPU。
- 尚无真实 shared-session GPU differential、isolated open-loop audit、GPU memory/throughput calibration 或 native full-state evidence。
- `rankable`、`physical_qualified`、`paper_score_eligible`、`process_isolation_qualified`、`native_full_state_qualified` 在论文条款修订和重新对齐完成前一律保持 `false`。

## 阶段验收记录

- 论文镜像：`/home/pupil/CVPR2026_Template` HEAD、`overleaf-old/main` 和远端 `main` 均为 `1f329494e755523692f5f96c8e0889c6cec15a82`。
- P1/R7 active-vs-pending crash regression：相关 Tool tests **219 passed, 1 skipped**；新增 focused tests **54 passed**。
- 这些测试没有启动 Isaac/Kit/GPU，不能替代 G1–G3 的真实收据。

## G1 阶段反思与 handoff

- 本阶段首次完成了项目内真实 GPU 物理路径的最小闭环：设备身份、项目来源、独立评价、进程返回码、关闭边界和遥测清理均有监督层证据。
- 发现并修正了一个证据层问题：native Kit 关闭后子进程不能可靠回写结果，因此不能只读取子收据判断 clean shutdown；监督器必须读取独立的项目内关闭 marker。
- 这次修正只改善收据完整性，不改变任何资格门；单场景成功不能替代三路 restore、isolated audit、shared crash 归因和完整 GPU manifest。
- 下一阶段 handoff：优先完成 R7 watchdog 与 `vectorized_executor` 的 collector-owned 接线，再设计真实 shared-session GPU differential；在此之前不运行 G2 正式资格流程。
