# HeteroCoord 阶段收尾：受监督原生数据源闭环

日期：2026-10-23  
论文基线：`72b6a3b975ae83743dc1d447b99edd728cb1dcdc`  
状态：本阶段完成；总体 Goal 继续 active

## 本阶段范围

本阶段只关闭从项目自有 Isaac 环境到 sealed diagnostic source receipt 的最小原生链路。它验证环境选择、paper-state 投影、checkpoint 落盘、source/collection seals、进程关闭和 telemetry 清理，不修改论文正文，也不授予物理资格、论文分数或排名资格。

## 已完成内容

1. 恢复 ZCode GLM-5.3/max 连接并启用 agent-team 工作区；ZCode 仅承担限定测试、受控命令执行和证据复核，架构、实现、修复与最终验收由主代理负责。
2. 固化 `live_paper_projection`：默认 paper-state 为 7 维 pose 加 6 维 velocity；joint velocity 仅校验，因冻结 schema 只注册 `joint_position`，故不写入状态向量。
3. 接通项目自有 `EAI-Factory-Z1-v0` 原生 source collector；环境选择进入 application digest，source seal 与 collection seal 分别校验。
4. 完成 CPU supervisor：requested device 必须等于 actual device，CPU 路径不得申领 GPU lease，不得残留 telemetry，且七个正式 gate 必须全部为 false。
5. 修复真实 Kit 关闭路径上的回执终结问题：`app.close()` 会在 session context 退出时终止解释器，导致 child 中 context 之后的代码不可达。现在 supervisor 只在完整 `session_close.json` marker 存在时，后处理回填 `session_close_receipt` 和 `session_close_observed=true`；不完整 marker 继续 fail closed。

## 验证证据

- 主代理 focused regression：33 passed。
- ZCode 独立 focused regression：33 passed。
- 目标模块 `compileall`：exit 0。
- 修复后原生 CPU 运行：一次执行、exit 0、无重试、无 GPU/CUDA workload。
- 运行目录：`artifacts/project_runtime/source-collector-cpu-20261023-02/`。
- supervisor：`passed=true`，`reason=receipt_and_process_pass`，process return code 0。
- child：`status=completed`，requested/actual device 均为 `cpu`，环境为 `EAI-Factory-Z1-v0`。
- cleanup：`session_close_observed=true`，close receipt 已持久化，telemetry residue 为空，最终 transmitter 不存在。
- checkpoint：tick 8，JSON、SHA-256 sidecar 与 NPZ 三件套均存在且 sidecar 匹配。
- collection：`qualification_status=diagnostic_readback_only`。

关键摘要：

```text
application_package_digest = c8f0b1eaf91b11729a35e3f9498354c06c4d4c32d85e01043e85197d127575ba
manifest_digest            = 7cb37c5bd85c5271087f99b943c22a60a2d1cc0faed3df97cf81c7f7f030365e
collection_digest          = 00071a6e5bc922bb31f937f6cc4a02a605b68daadc0b01d83968eb8793d50583
checkpoint_sha256          = 9ccd68c4ec44ac1bf4f76928b90f759dbfc4e8156b41d5de0a8bf9d22da15f76
```

## 保留的失败证据

首次原生 CPU 产物 `artifacts/project_runtime/source-collector-cpu-20261023-01/` 被原样保留。其 native process exit 0 且 supervisor 当时通过，但独立验收发现 child `result.json` 中 `session_close_observed=false` 且缺少 close receipt，因此该运行被判为 blocked，未被接受为成功证据。只有修复、增加正反测试并通过回归后，才在新目录执行一次独立重跑。

## 当前资格边界

以下字段必须并且当前确实全部为 `false`：

```text
physical_checkpoint_qualified=false
history_initialization_qualified=false
branch_isolation_qualified=false
closed_loop_audit_qualified=false
statistical_widening_qualified=false
paper_score_eligible=false
rankable=false
```

因此，本阶段结论只能表述为“受监督的原生 diagnostic source seam 已闭环”，不能表述为物理 checkpoint 已认证、算法实验已完成或论文结果可填充。

## 后续任务顺序

1. 设计物理 hold/readback/tick receipt，明确环境身份、时间推进、状态保持和失败语义。
2. 经设计评审后，才执行最小 GPU/shared-resource + history initialization + branch isolation 实验；不自动启动 CUDA。
3. 完成 closed-loop audit，以及 fault/sham/intervention/held-out recovery、calibration/baseline 和 paired pilot。
4. 只有上述资格与统计证据完整后，才重新审查 paper score eligibility、排名与论文结果表。

总体 Goal 保持 active；下一阶段从物理资格协议设计开始。

## S5 合同推进（2026-10-23）

ZCode 只读审计确认论文要求不能由当前 CPU smoke 直接证明：hold 窗口内物理 tick 不变、
controller/queues/locks/transport 快照、trajectory 级验证、结果前 eligibility 注册、K=3
重复和 GPU runtime 均仍缺失。主代理已实现
`tools/benchmark_branch/physical_qualification.py` 与 focused tests，定义
`PhysicalHoldReadbackReceipt`：身份串联、pre/post state digest、tick trace、单调 host-time hold 区间与采样次数、逐通道
readback/tolerance、runtime 和 cleanup digest 均进入 sealed row；任何 tick/state/device/
provenance/readback/cleanup 漂移都会 fail closed，且 receipt 始终不能自授权 physical qualification。

合同明确要求 checkpoint/start/end/post 四个 simulator tick 完全相等，避免用正 tick 窗口误示
物理时间推进。主代理与 ZCode 最终修订输入回归均为 **23 passed**，compileall exit 0。尚未启动 Isaac/Kit/GPU，也没有把该合同当作真实
物理资格证据；下一步是让受控 native worker 产生这些字段，再进行独立审查。

## S5 native hold producer 阶段收尾（2026-10-23）

本阶段已把上一节的合同接入真实受监督 CPU collector，但结论仍严格限定为
`diagnostic_physical_review_pending`。

实现与修复：

- child 在 restore/readback 后执行 4 次 `read_physics()`，使用主机 monotonic clock 记录正时间
  区间，期间不调用 simulator step；任何中间 state digest 漂移立即失败。
- child 持久化 checkpoint artifact digest、真实 readback report digest、pass 状态与 tolerance，
  以及 application/environment/device/provenance runtime seal。
- supervisor 只在完整 session-close marker 与空 telemetry residue 后构造最终 sealed receipt；
  不完整 close、telemetry residue、runtime digest、source/checkpoint/collection/device 漂移均不写回。
- gate 重新验证 source manifest、collection、physical receipt、raw hold、runtime 与 cleanup，并对
  source-run/checkpoint/tick/device/digest 做交叉绑定；七个正式 gate 必须全部保持 false。

验证：

```text
主代理 focused tests  = 35 passed
ZCode focused tests    = 35 passed
compileall             = exit 0（双方）
git diff --check       = exit 0（双方）
native artifact        = artifacts/project_runtime/source-collector-cpu-20261023-03/
native process         = return code 0；一次执行；无重试
device / GPU lease     = cpu / none
session close          = observed
telemetry remaining    = empty
checkpoint tick        = 8
hold observations      = 4
host hold duration     = 5,617,120 ns
readback tolerance     = 1e-05
max readback error     = 2.9808951866712727e-07
physical receipt seal  = 8c4e7c1966a0ba3a1a0fb77a1484b36c06a15fd684ae893b69d2035e0db239b4
```

Artifact fingerprints：

```text
run/result.json    da7846b869d090fb7ac9d7c5e7e3a832da5897305c3558cd00046a88326e4f42
supervisor.json    38ba1909db23c2a046a2cad1f0048ee4e5f3758c0088fe93584d39bdf4d769b7
session_close.json d392835d834d35b624dc200a8155f7f20ab8f5dd07de65bad5765d7fc3d84b39
```

资格边界：CPU/mock 证据不能晋升 `physical_checkpoint_qualified`；本次也未覆盖 GPU runtime、
trajectory verification、K=3 同 checkpoint 重复、fresh history initialization、branch isolation、
closed-loop audit 或统计 widening。因此七个 gate 继续全部为 false，论文正文保持未修改。

下一阶段从 minimal shared-resource history/branch **合同与 fail-closed tests** 开始。任何 CUDA
诊断仍需单独授权；总体 Goal 保持 active。

## S5 CUDA admission guard 收尾（2026-10-23）

`run_project_probe` 已将 source-collector CUDA 通过条件收紧为 child/runtime GPU identity、
child/supervisor UUID 一致、supervisor lease acquire/release 均为真且 telemetry residue 为空；
lease release 只在 lease context 正常退出后记录。主代理与 ZCode 均为 36 passed，compileall 与
scoped diff check 均 exit 0，未启动 collector、Isaac、Kit、GPU 或 CUDA。单次 CUDA 执行的冻结配置
与停止条件见 `CUDA_DIAGNOSTIC_AUTHORIZATION_20261023.zh-CN.md`，仍需用户明确授权。

随后安排的 ZCode 授权包只读预检在环境 import 第一步因其 Bash high-risk 权限请求被协调者拒绝而
按 fail-closed 停止；未修改文件、未重试、未启动任何 GPU workload。入口帮助、目标目录和 admission
guard 哈希已由主代理核对，剩余环境 import 与最终 GPU 状态检查保留到显式授权后的执行窗口。

## Telemetry cleanup 隔离修复收尾（2026-10-23）

已移除广域 `pkill -f omni.telemetry.transmitter`，改为 baseline-preserving 的 PID inventory 与
定向 SIGKILL；运行前已有 telemetry 进程会使 CUDA source-collector 直接停止。主代理/ZCode 各
45 tests 通过，compileall、diff check 均通过；没有启动 native/GPU workload。该修复只改善资源
隔离与 fail-closed 语义，不授予任何 formal gate。

vectorized backend 随后也完成同一 baseline 绑定：每个 session 在 open 前冻结 telemetry PID 集，
异常 enter、初始化失败和正常 close 都只清理本 session 新增进程。主代理/ZCode 各 **46 passed**，
compileall 与 diff check 通过；formal gates 继续全部为 false。

`context.__enter__` 失败路径新增回归后，主代理/ZCode 各 **47 passed**；这项测试确认 native enter
异常不会丢失原始 telemetry baseline。未启动任何 GPU/native workload。

另新增 `run_project_probe --dry-run`，用于授权前验证完整 child command；实测不创建输出目录、不
采集 GPU、不启动 native。聚焦回归现为 **48 passed**，compileall 与 diff check 通过。

## S5 shared-resource/history/branch 合同收尾（2026-10-23）

主代理已实现 sealed diagnostic-only 合同
`heterocoord.shared_resource_history_branch.v1`，把论文所需的 K=3 重复、每次
fact/reference/sham/intervention 四条件、12 个 fresh context、初始化后分配、repeat 内共享且
repeat 间不同的 exogenous stream、私有初始化/策略流、branch-local writable namespace、唯一
attempt/retry identity，以及 runtime/device/resource lease/eligibility/cleanup seals 纳入同一摘要。
任何字段漂移均 fail closed；收据状态固定为
`diagnostic_shared_resource_review_pending`，七个正式 gate 硬封为 false。

验收证据：

```text
主代理 focused tests = 40 passed
ZCode focused tests   = 40 passed in 0.33s
compileall            = exit 0（双方）
git diff --check      = exit 0（双方）
native/GPU/CUDA       = 未启动
```

ZCode 独立复测对应文件 SHA-256：

```text
shared_resource_qualification.py      fb51f671317a7fc8fe145e2f10d6c3b46031905cc6a9ba8feac6e42378a03803
test_shared_resource_qualification.py 04abf4a7d1343ea70d1798ede75ad60f1bf40d5bdd5025ac2db39824b848a2a8
__init__.py                            a96143110a8ee334514719bf4bba785bd91158be11ab33ec4719bee540aad43a
```

这一步关闭的是“合同可表达、可封存、可拒绝篡改”，并未证明真实 runtime 已产生 K=3 合格证据。
下一阶段是在不改变 `HistoryBlockRuntime` 单次 repeat 语义的前提下，将三个独立 run 的
`ReplayIdentityManifest` 与初始化、cleanup、branch ledger 汇聚为该总合同。CPU/mock 仍只用于
状态机回归；CUDA 诊断继续需要单独授权；论文正文保持未修改，总体 Goal 继续 active。

## S5 K=3 history/branch 账本汇聚收尾（2026-10-23）

`HistoryBlockRuntime` 现在保持“一次运行对应一个 repeat”的架构，只增加显式 `r1/r2/r3`
身份（默认仍为 `r1`）和初始化前 diagnostic admission 事件。新增 collector-side
`aggregate_history_stores` 读取三个已经 finalize 且 seal 完整的独立 store，重算 initialization
outcome、process cleanup 和 replay manifest，再核对 admission → 四个 initialization terminal →
barrier → assignment 的时序、四条件 roster、paper lineage、private/exogenous stream、writable
namespace、attempt/retry identity，最终生成一个 K=3/12-context 总收据。

验收：

```text
主代理组合回归 = 92 passed in 37.77s
ZCode 独立复测 = 92 passed in 37.53s
compileall      = exit 0（双方）
git diff check  = exit 0（双方）
五文件 SHA-256 = 双方一致
native/GPU/CUDA = 未启动
```

因此 CPU/mock 实现阶段已收口，但这仍不等于真实物理/history/branch qualification。下一步是
一个场景、一个 checkpoint 的最小 CUDA diagnostic；它属于新的显式授权点。未获授权前不启动
GPU、不运行矩阵、不重试、不改变任何 formal gate，也不修改论文正文。

## ZCode 连接与授权前 dry-run 收尾（2026-10-23）

ZCode 桥接会话已恢复并完成同一只读任务书的独立复测：聚焦集合 **48 passed in 0.75s**，
dry-run、compileall、scoped diff check 均退出码 0；目标输出目录仍不存在。ZCode 未运行
`nvidia-smi` 或真实 source-collector，未启动 CUDA/Isaac/Kit/GPU/native workload，也未修改
项目源文件或测试。dry-run 在基环境下打印当前 `sys.executable` 的
`/home/pupil/miniconda3/bin/python`，正式授权包仍要求由 `env_isaaclab` 解释器调用，不构成
解释器漂移或授权执行。

在同一阶段收尾点，主代理补做了两组架构兼容回归：论文绑定、history runtime、vectorized/
recovery/release 等 **230 passed in 30.48s**；source-run、pilot/capability、shared-resource 和
K=3 aggregation 等 **41 passed in 12.27s**。`benchmark` 与 `tools/benchmark_branch` 全量
`compileall` 及 `git diff --check` 均通过。该补充只证明当前合同层和诊断层未回归，仍不产生
physical/history/branch qualification，也不授权 CUDA 执行。

ZCode 随后对这两组兼容回归做了独立复测：230 tests（29.92s）与 41 tests（12.19s）均通过，
合计 **271 passed**；全量 compileall 与全工作树 diff-check 也均通过。该独立证据进一步确认
论文绑定、S1/S2/S5 合同和 runtime seam 当前相互兼容，但仍不构成真实 evaluator-owned physical
receipt 或任何 qualification gate。

本阶段最终边界：agent-team 协作、CPU/mock 合同、K=3 账本、CUDA admission guard、telemetry
baseline 隔离和授权前参数验证均已由主代理与 ZCode 双重验收；最小 CUDA diagnostic 仍停在
用户明确授权点，所有 formal gates 继续为 false，论文正文未修改。

补充的 gate 一致性审计扫描 artifacts 下 42 个 JSON 文件（12 个 gate-bearing 文件、42 次 gate
字段出现），全部可解析且没有任何 `true` gate；CUDA dry-run 目标目录仍不存在。该审计不写入、不
修复，不改变当前资格边界。

随后完成 S2 source producer 边界修复：新增拒绝 submitted-system RNG/private random state
字段的 fail-closed 规则，并用参数化测试覆盖 8 类字段；evaluator 的外生 `random_counters`
仍保持可用。相关 source-run/live-adapter/pilot/shared-resource/K=3 组合回归为 **51 passed**，
compileall 与 diff-check 通过。该修复强化 evaluator-owned source 语义，仍不产生真实 physical
receipt，也不改变七个 formal gates。

live checkpoint bridge 也新增了 source identity drift 防护：backend 暴露的 checkpoint
`source_run_id` 若与请求不一致，会在 projection 前拒绝。source-run/live-adapter focused suite
为 **31 passed**，组合回归为 **52 passed**；compileall/diff-check 通过。ZCode 当前 GLM provider
已达到限额，本轮未重试其会话；主代理完成实现和验收，formal gates 仍全部为 false。

## Dry-run telemetry 顺序回归收尾（2026-10-23）

扩大到 source/live/history/K=3/native-supervisor/vectorized/runtime 的组合回归后，首次结果为
**170 passed, 1 failed**：机器上已有 telemetry transmitter 时，`--dry-run` 在输出参数验证结果前
进入 runtime telemetry admission。现已让 dry-run 在任何 telemetry/GPU runtime 查询前返回，并用
“telemetry helper 一旦被调用即抛错”的测试锁定无副作用边界。

真实非 dry-run CUDA 路径的安全策略保持不变：已有 telemetry 时仍在创建输出目录、GPU identity、
lease 和 child 启动之前 fail closed。修复后聚焦回归 **48 passed**，完整组合回归
**171 passed in 39.14s**，compileall 和全工作树 diff-check 均通过；42 个 artifacts JSON 中所有
42 次 formal gate 出现仍为 false，CUDA 目标目录仍不存在。

agent-team Sol/high reviewer 独立结论为 ACCEPT、无 P0/P1/P2，并用纯 mock non-dry-run CUDA 反例
验证 telemetry admission 未被放宽。ZCode 因 provider 限额未被反复重试。本轮未启动或终止任何
Isaac/Kit/telemetry/CUDA/GPU/native workload；该修复只恢复授权前 dry-run 合同，不能替代真实
physical receipt，不能升级论文分数或排名资格。

## 最小 CUDA diagnostic 阶段收尾（2026-10-23）

用户已授权后续项目内 CUDA/native 诊断无需逐次请求，并确认 ZCode 恢复。首次输出 `-01` 虽然
supervisor 自洽通过，但审计发现另一条 ZCode 渲染任务在同一 GPU 并发执行；由于原 admission 未记录
compute-app inventory，该 artifact 被保留为污染证据，不予接受。

主代理新增 selected-GPU compute baseline/after guard。Sol/high 独审 R1 找出两个 P1 fail-open：
`nvidia-smi` exit 1 被当作空清单、短 malformed row 被静默跳过。修复后所有非零退出与 malformed row
均 fail-closed，foreign compute 在 output mkdir/lease/child 之前拒绝，source/G1 receipt 必须同时含
空 baseline 和空 post-run inventory。R2 为 ACCEPT；主代理与 Sol 均 **71 passed**，compileall 与
diff-check 通过。ZCode 对新输入的只读复测也已启动，不承担代码修改或资格判断。

GPU 空闲后执行唯一一次新目录 `source-collector-cuda-20261023-02`：exit 0、supervisor passed，
compute baseline/after 为空，UUID 一致，lease 完整释放，session close 完整，telemetry 无残留，
checkpoint sidecar 匹配，三类 sealed receipt 可离线恢复。运行后无 Isaac/Kit/telemetry/GPU compute
残留。

本阶段只确认隔离 CUDA diagnostic source seam；52 个 artifacts JSON 中 60 次 formal gate 字段全部
为 false。physical/history/branch formal qualification、closed-loop audit、statistical widening、论文
分数和排名仍未完成，论文正文保持未修改。
## ZCode R2 复测收尾（2026-10-23）

ZCode GLM-5.3/max 已完成同一版本的只读复测并写入
`handoffs/gpu-compute-isolation-zcode-r2.md`（SHA-256
`3bf708a522b1e982fce0ca07038b2875660b9cb959dc96f5396fef3b56f81b12`）。固定四文件回归为
**71 passed in 0.78s**，`compileall` 与 scoped `git diff --check` 均为 exit 0；nvidia-smi
非零退出、selected-GPU malformed row、foreign compute 的 pre-output refusal，以及
baseline/after gate rejection 均有覆盖。结论为 **ACCEPT，无 P0/P1**；两条 P2 建议仅进入
backlog，不阻塞下一阶段。

本阶段正式收口为“隔离 GPU 的单场景/单 checkpoint diagnostic source admission”。下一阶段
只做 evaluator-owned fresh isolated initialization 的最小 runner/receipt 设计与 CPU/mock 验证，
再单独授权真实 history/branch 与 K=3 CUDA 证据；不扩展统计矩阵，不改论文正文，不改变七项
formal gates。
