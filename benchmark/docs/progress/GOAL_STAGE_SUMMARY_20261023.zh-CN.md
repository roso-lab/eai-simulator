# HeteroCoord Goal 阶段总结与后续任务规划

日期：2026-10-23  
项目：`/home/pupil/HeteroCoord`  
论文镜像：`/home/pupil/CVPR2026_Template`  
论文基线：`72b6a3b975ae83743dc1d447b99edd728cb1dcdc`  
总体状态：Goal active；CPU/mock 实现阶段收口；CUDA diagnostic 等待单独授权

## 1. 论文科学逻辑

最新论文补充材料 `sec/supp_system_state.tex` 要求证据沿同一因果链建立：冻结物理 checkpoint 与
permitted history；为每个分支创建 fresh context；初始化过程 condition-blind；预算/屏障结束后才
随机分配 fact、reference、sham、intervention；同一 repeat 共享 exogenous stream，同时保持 private
initialization/policy stream 与 writable state 隔离；每个 eligible checkpoint 执行 K=3 repeats；
最后通过 closed-loop audit 和 statistical widening 才能进入论文计分与排名。

这决定了实现顺序不能从“代码能跑”直接跳到“论文结果成立”。当前架构把 source、physical hold、
history initialization、branch identity、cleanup/replay、closed-loop audit 和统计资格拆成独立 sealed
receipts，并由外部 gate 审查组合，避免单个模块自授权。

## 2. 已完成架构与实现

1. 已恢复 ZCode GLM-5.3/max 桥接并启用 agent-team 工作区。ZCode 只执行主代理制定的只读测试和
   证据采集；架构、代码、修复与最终验收由主代理负责。
2. 已完成项目自有 `EAI-Factory-Z1-v0` 的 supervised native source seam，生成 source、collection、
   checkpoint、session-close 与 telemetry cleanup 证据。
3. 已完成 `PhysicalHoldReadbackReceipt` 及 CPU native producer：hold 期间只读 physics state，不调用
   `env.step()`；checkpoint/start/end/post tick 保持一致；真实 readback error/tolerance 与 cleanup seal
   被交叉绑定。
4. 已完成 `SharedResourceHistoryBranchReceipt`：固定 K=3 × 四条件、12 个 fresh contexts、后屏障
   assignment、repeat 内共享/repeat 间不同的 exogenous stream、private streams、branch-local
   writable namespaces、attempt/retry identity 和 runtime/device/lease/eligibility/cleanup seals。
5. 已完成三个独立 `HistoryBlockRuntime` store 的 collector-side 汇聚：每次 runtime 仍只负责一个
   repeat；汇聚器验证三个 store 的 seal、时序、lineage、outcome、cleanup 和 replay manifest，重算
   一个 K=3 总收据。

## 3. 当前证据

```text
S5 native hold producer：主代理/ZCode 35 passed
S5 shared-resource contract：主代理/ZCode 40 passed
S5 K=3 ledger aggregation：主代理 92 passed；ZCode 92 passed
compileall / scoped diff checks：全部 exit 0
最新 CPU native artifact：artifacts/project_runtime/source-collector-cpu-20261023-03/
```

K=3 汇聚最终受检文件 SHA-256：

```text
history_aggregation.py              c94bb9a1583c2fe6b52d570882f4ffb26640b2c773a1706ad179d23cd94790a5
history_runtime.py                  f02913f23a7f6ff30c5ab7bb7848546cefc948985872977559c3db197be9bf89
test_history_aggregation.py         1241dc0c194440853f945dfb156e9221677ba22bdbda94b26a4201dc472ccc9a
test_history_runtime.py             6311be1eb7bb3efd71c6092a6b1f43f64aef607e46a0b6350f2f815948c26bc1
tools/benchmark_branch/__init__.py  05751afe3717f5334c8b91059d476329aacace0e100ad3b086077fd8cf0d7244
```

## 4. 资格边界

所有现有 CPU/mock/native diagnostic 证据都不能替代真实 CUDA/physical qualification。以下七个 gate
当前必须且确实全部为 `false`：

```text
physical_checkpoint_qualified=false
history_initialization_qualified=false
branch_isolation_qualified=false
closed_loop_audit_qualified=false
statistical_widening_qualified=false
paper_score_eligible=false
rankable=false
```

论文正文未修改；当前不能填写结果表、生成排名或声称算法实验已经完成。

## 5. 后续任务规划

### P0：单次最小 CUDA diagnostic（等待明确授权）

- 一个场景、一个 checkpoint、一个受控 GPU lease；先核对设备空闲、driver/runtime identity 和
  telemetry 基线。
- 只运行最小 hold/continue 路径，生成 actual device、GPU UUID、runtime、lease、session close、
  telemetry cleanup 与失败语义证据。
- 只允许一次执行，不跑矩阵、不自动重试；单次通过仍保持所有 gate 为 false。

### P1：真实 K=3 fresh-history/branch qualification

- 用同一 reviewed physical checkpoint/history 运行 r1/r2/r3，每次四个 fresh contexts。
- 验证 condition-blind 初始化、post-barrier assignment、private namespace/stream 隔离、repeat 内共享
  exogenous stream、repeat 间 stream 区分，以及 retry/replay identity。
- 任一 GPU warning、cleanup residue、lineage 或 isolation 漂移都使配置不合格并保留失败 artifact。

### P2：closed-loop audit

- 以注册 hash 抽取 shared blocks，在 isolated process 中重放 logged brain decisions。
- 比较 observation、skill outcome、milestone order、trajectory tolerance 和 scored ledger；保留
  method failure、infrastructure failure、unknown 与 pair-invalid 的原始归因。

### P3：preregistered statistical pilot

- 冻结 paired unit、primary metric、effect size、exclusion、seed、stopping rule 和 K=3 规则。
- 先做 calibration/baseline，再做小规模 paired pilot；计算 audit discordance 上界并进入最坏端点
  widening。没有 preregistration 和 audit receipt 时不扩大样本、不产生论文分数。

### P4：paper eligibility review

- 逐项核验 physical、history、branch、closed-loop、statistical 五级 receipts。
- 只有全部 gate 经外部 reviewed evidence 通过后，才讨论 `paper_score_eligible`、`rankable` 与论文
  结果更新；论文正文修改仍需单独任务与证据审查。

## 6. 当前暂停点

CPU/mock 实现与 K=3 汇聚已完成并独立验收。继续推进所需的新权限是“执行一次最小 CUDA
diagnostic”。在获得明确授权前，Goal 保持 active，但不启动 GPU/native workload。

## 7. CUDA admission guard 收尾与授权边界

主代理已完成 source-collector CUDA admission guard：只有 child/runtime GPU identity 与
supervisor GPU UUID 一致、lease acquire/release 均真实成立、session close 完整且 telemetry residue
为空时，supervisor 才可报告 diagnostic pass。CPU/mock 回归与 ZCode 独立复测均为 36 passed，
compileall 和 scoped diff check 均通过；没有启动任何 GPU/CUDA workload。任务
`s5-cuda-admission-guard` 已在团队账本标记 done。

主代理在当前工作树对 native source seam 相关四文件执行了最新兼容回归：50 passed；
`compileall` 与 scoped `git diff --check` 均 exit 0。该回归仍为 CPU/mock-only，不产生任何 GPU
或物理资格证据。

当前唯一暂停点是用户授权，而不是代码缺口。单次执行授权包冻结一个场景、一个 checkpoint、
`cuda:0`、一个新输出目录和一次命令，失败即停且不重试；详见
`CUDA_DIAGNOSTIC_AUTHORIZATION_20261023.zh-CN.md`。授权前七个 formal gates 继续全部为 false，
论文正文保持不变。

### 授权包只读预检记录

ZCode 已通过现有桥接会话读取授权包并按任务书开始预检；其 Bash 权限策略将第一步环境 import
命令标为 high-risk。协调者拒绝扩大本轮 ZCode 权限，因此 ZCode 按 fail-closed 规则立即停止，
没有启动 CUDA/Isaac/Kit/GPU、没有修改文件，也没有重试。入口 `--help`、输出目录不存在性、
源码哈希等静态证据已由主代理核对；环境 import 与真实 CUDA 运行仍留待用户明确授权后的最终预检。

另已审计 `tools/project_runtime.py`：wrapper 会将内层 `python` 替换为外层 env_isaaclab 的
`sys.executable`，故授权命令不存在解释器漂移风险；该结论来自源码检查，不等同于已执行 CUDA。
同时识别出 cleanup 会执行 `pkill -9 -f omni.telemetry.transmitter`，授权包已新增“运行前存在任何
同名 telemetry 进程即停止”的硬条件，避免清理其他 Isaac 会话的进程。

### Telemetry cleanup 隔离修复（2026-10-23）

为消除该风险，`telemetry_processes()` 现在先建立 PID 快照，`cleanup_isaac_telemetry()` 只对
不在 baseline 中的新增 PID 发送定向 SIGKILL，并将 kill error/残留进程写入 receipt；不再使用
广域 `pkill -f`。source-collector 在 CUDA admission 前若发现任何已有 telemetry 进程则直接
fail-closed。主代理与 ZCode 独立复测均为 **45 passed**，compileall 与 scoped diff check 均 exit 0；
未启动 CUDA/Isaac/Kit/GPU，七个 gate 未改变。

后续审计又发现 vectorized backend 的默认 cleanup 分支未携带 session baseline。现已让
`_EaiVectorSession` 保存启动前 PID 集，并在 `__enter__` 失败、初始化失败与正常 close 三条路径中
传回同一 baseline；显式注入的测试 cleanup 接口保持兼容。主代理与 ZCode 独立复测均为
**46 passed**，compileall 与 scoped diff check 通过，仍未启动 native/GPU workload。

补充的 `context.__enter__` 失败路径回归也已完成：主代理与 ZCode 各 **47 passed**，compileall 与
scoped diff check 通过，证明 session baseline 在 native enter 失败时仍被复用；所有 formal gates
保持 false。

授权包现已同时冻结 supervisor/native 哈希与 supporting runtime/vectorized cleanup 哈希，避免
后续授权只验证入口文件而遗漏实际资源隔离实现。

新增 `run_project_probe --dry-run` 作为授权前无副作用参数验证入口：主代理实测完整
`source-collector --device cuda:0` 命令解析通过，目标目录未创建，未采集 GPU、未启动 native；
新增回归后聚焦集合为 **48 passed**，compileall/diff check 通过。授权包中的入口哈希已更新。

ZCode 随后使用同一只读任务书完成独立复测：聚焦集合 **48 passed in 0.75s**、退出码 0；
`--dry-run` 退出码 0，输出 `dry_run=true`、`probe=source-collector`、`requested_device=cuda:0`，
且没有启动 child collector，目标输出目录仍不存在；六个目标文件 `compileall` 退出码 0，
scoped `git diff --check` 退出码 0。ZCode 明确未运行 `nvidia-smi`、真实 source-collector、
Isaac、Kit、GPU 或 CUDA，也未修改源代码/测试。该结果只加强回归与授权前参数验证证据，不改变
七个 formal gates。

### 非 CUDA 兼容回归补充（本轮 Goal continuation）

为确认新增 dry-run 与 telemetry 隔离没有破坏论文协议和既有架构，主代理又执行了两组只读
回归：论文绑定、paper-state/P0-P2、history runtime、vectorized/recovery/release 共 **230
passed in 30.48s**；source-run、pilot contract、capability evidence、shared-resource contract
和 K=3 history aggregation 共 **41 passed in 12.27s**。随后对 `benchmark` 与
`tools/benchmark_branch` 执行全量 `compileall`，并执行全工作树 `git diff --check`，均退出码 0。
这些证据仍属于 CPU/contract/diagnostic 层，不改变任何 formal gate，也不替代真实
evaluator-owned physical source receipt。

ZCode 对同一批次完成独立只读复测并得到一致结果：230 tests（29.92s）+ 41 tests（12.19s）=
**271 passed**；全量 `benchmark tools/benchmark_branch` compileall 和全工作树 `git diff --check`
均退出码 0。ZCode 明确未运行 CUDA/Isaac/Kit/GPU/native 或 nvidia-smi，未修改源码/测试。

随后主代理与 ZCode 分别完成 artifacts gate 一致性审计：ZCode 扫描 42 个 JSON 文件，其中 12
个含 gate 字段、共 42 次 gate 字段出现，全部为 `false`；JSON 全部可解析，CUDA 目标目录仍
不存在。该审计没有写入或修复任何文件，确认没有 diagnostic artifact 越级为 formal qualification。

### S2 source producer RNG 边界修复（2026-10-23）

主代理审计发现 `_FORBIDDEN_KEYS` 只覆盖 model/condition/outcome/private state，未显式拒绝
submitted-system 的 RNG 状态。现已扩展 source-run contract，拒绝 `private_rng`、
`private_random_state`、`policy_rng`、`policy_random_state`、`submitted_rng`、
`submitted_random_state`、`rng_state`、`random_state` 等嵌套字段，同时保留 evaluator-owned
`random_counters` 作为外生计数。新增参数化回归覆盖全部八类键；source-run + live adapter +
pilot/shared-resource/K=3 组合回归为 **51 passed**，compileall 与 scoped diff check 通过。
该修复只收紧 source boundary，不授予任何 physical 或 paper gate。

进一步审计 live checkpoint bridge：若 backend checkpoint 暴露 `source_run_id`，现在必须与请求
的 source run 一致；不一致会在读取 sealed store 和 projection 前 fail-closed。新增 identity-drift
回归后，source-run/live-adapter focused suite 为 **31 passed**，与 pilot/shared-resource/K=3
组合回归为 **52 passed**；compileall 和 scoped diff check 通过。当前仍只属于 diagnostic seam。

### Dry-run 与 telemetry admission 顺序修复（2026-10-23）

扩大组合回归时首次得到 **170 passed, 1 failed**。失败并非 CUDA 执行，而是机器上已有
`omni.telemetry.transmitter` 时，`run_project_probe --dry-run` 在返回参数验证结果前仍查询并拒绝
runtime telemetry。由于 dry-run 的合同是只验证路径、参数和 child command，不采集运行态、不创建
输出目录，该顺序属于兼容回归。

现已将 dry-run 返回移到 `telemetry_processes()` 之前，并将回归测试收紧为“若 dry-run 查询
telemetry 就立即失败”。真实非 dry-run CUDA 路径仍在输出目录创建、GPU identity、lease 和 child
启动前拒绝非空 telemetry baseline，没有放宽 admission gate。修复后聚焦回归 **48 passed**，完整
source/live/history/K=3/native-supervisor/vectorized/runtime 组合回归 **171 passed in 39.14s**；目标模块
`compileall`、全工作树 `git diff --check` 均退出码 0。artifacts 复核仍为 42 个 JSON、12 个含 gate
文件、42 次 gate 字段、0 个 true、0 个无效 JSON，CUDA 目标目录不存在。

agent-team Sol/high reviewer 独立审查结论为 ACCEPT、无 P0/P1/P2；其纯 mock 反例确认真实
non-dry-run CUDA 会在 GPU identity、lease、child 与输出目录之前拒绝既有 telemetry。ZCode provider
限额尚未恢复，因此未重复调用。本轮没有终止既有 telemetry 进程，没有运行 `nvidia-smi`、Isaac、
Kit、CUDA、GPU 或 native workload；论文正文未修改，七个 formal gates 继续全部为 false。

### 最小 CUDA diagnostic 与 compute-isolation 修复（2026-10-23）

用户已解除本项目 CUDA/native 诊断的逐次授权暂停点，并确认 ZCode 恢复。主代理恢复 ZCode
GLM-5.3/max 会话并委派 CPU/mock 回归；ZCode 实际完成旧输入 50 项回归，后因源码版本变化停止，
旧哈希证据未用于新实现验收。

首次 CUDA artifact `source-collector-cuda-20261023-01/` 的 supervisor receipt 虽显示 exit 0、UUID
一致、lease acquire/release、session close 与 telemetry cleanup 均通过，但离线时间线发现另一条用户侧
ZCode 渲染任务同时占用同一 GPU。原 admission 只检查 telemetry 与本项目 lease，没有检查已有
compute app，因此主代理主动将 `-01` 判为并发污染、不可接受并原样保留；没有升级任何 gate。

随后新增 selected-GPU compute-app isolation：`gpu_compute_processes()` 只接受 `nvidia-smi` exit 0
和严格四字段行；任何已有 compute app、inventory failure 或 malformed row 均在输出目录创建、lease
和 child 启动之前 fail-closed。supervisor 记录 compute baseline/after，source/G1 gate 要求二者均为空。
Sol/high R1 找出非零退出与短行两个 fail-open；修复并同步 G1 fixture 后，R2 结论 ACCEPT、无剩余
P0/P1/P2。主代理与 Sol 组合回归均为 **71 passed**，compileall 与 diff-check 通过。

等待用户侧 GPU 任务自然结束后，主代理在新目录
`artifacts/project_runtime/source-collector-cuda-20261023-02/` 执行一次干净诊断。结果：process exit 0，
supervisor `passed=true`，compute baseline/after 均为空，child/supervisor 均为 `cuda:0` 且 UUID 为
`GPU-aa40060e-cb9d-b04a-e70c-4c91cbb67fb0`，lease acquire/release 均为 true，session close 完整，
telemetry baseline/residue 均为空，checkpoint sidecar SHA-256 匹配。source、collection、physical receipt
均可通过各自 `from_row`/manifest validator 离线恢复。

该结果只关闭“单场景、单 checkpoint、隔离 GPU 的 diagnostic CUDA admission”。artifacts 当前共
52 个 JSON、16 个含 gate 文件、60 次 gate 字段出现，0 个 true、0 个无效 JSON。七个 formal gates
继续全部为 false；论文正文未修改。下一阶段是基于这份干净 evaluator-owned CUDA source receipt
设计并执行 fresh isolated history/branch qualification，而不是直接扩展统计矩阵或回填论文结果。

### ZCode compute-isolation R2 验收补记（2026-10-23）

ZCode GLM-5.3/max 已在冻结输入上完成终态复测：71 passed（0.78s），compileall 与 scoped
diff-check 均 exit 0，结论 ACCEPT、无 P0/P1。证据文件
`handoffs/gpu-compute-isolation-zcode-r2.md` 的 SHA-256 为
`3bf708a522b1e982fce0ca07038b2875660b9cb959dc96f5396fef3b56f81b12`。两条非阻塞 P2
为 G1 compute-drift 专项测试补强及 fail-closed 格式行为备案；均不影响当前 diagnostic seam 收口，
也不改变任何 formal gate。

### S6.1 materialized fresh-history admission（2026-10-23）

S6.1 初版曾只核对 payload 内自报的 digest 字段。Sol 的独立反例证明，checkpoint state 或 history
records 被替换后，只要保留旧 digest 字符串仍会被接受。现已改为从四个 raw payload 重新调用
`EvaluatorOwnedSourceProducer`，实际重建并验证 `PhysicalCheckpoint`、`PermittedHistory`、
`FreshInitializer` 与 observation，再要求重建 receipt、source identity 与 manifest 精确相等。旧反例已
fail-closed；index-only `from_row()` 仍无 payload bytes，调用 spawn 边界会明确拒绝。

为接通未来真实 collector，`LiveCheckpointEvaluatorSourceAdapter` 会保留 detached evaluator payload，
新的 native source-collector artifact 将写出 `materialized_source/` 下五个 JSON 文件。已有 CUDA `-02`
不含这些字节，保持原样、不补写、不覆盖，因此仍只是干净的 source diagnostic evidence，不能直接作为
S6.2 child spawn 输入。

资源策略也改为 CPU/CUDA 互斥 union：CPU 的 GPU UUID、compute/telemetry baseline 与 lease 明确为
N/A；`cuda:N` 才要求 UUID、空 baseline 和 lease。该校验只消除自相矛盾的请求策略，不代表观测事实。
Sol 的后续设计审查确认 materialized-loader P1 已关闭，但保留三个实现 P1：四 context 的真实
capability enforcement/audit、外层 supervisor 持有的 lease 内资源生命周期，以及 synthetic runtime
与未来 native seam 的分离。新的 CUDA/native run、K=3、统计矩阵和论文结果回填仍全部暂停；七项
formal gate 继续为 `false`。

### S6.1 终验与 S6.2 capability runtime 收口（2026-10-23）

ZCode GLM-5.3/max 已完成 S6.1 R2：133 + 32 tests passed，compileall 与 scoped diff-check exit 0，
结论 ACCEPT、无 P0/P1；handoff SHA-256 为
`1a3feb42940df833066edb1a59427a2f864403cd0d84f297e0d57083783172e4`。因此
`s6.1-materialized-source-admission` 已从 validating 转为 done。它仍不改变既有 `-02` artifact，
不补写 materialized bytes，也不授予任何 gate。

主代理随后实现 evaluator-owned 四 context capability runtime。其 CPU/mock 边界实际执行：唯一
allocation/root/RNG/endpoint，dirfd + `O_NOFOLLOW` 安全创建，失败事务回滚，common-barrier 后才开放
policy capability，统一 assignment SHA，tool/model 合计预算，write content/tree/event ledger，以及
close-once 与 root cleanup audit。所有 launch/audit receipt 明确封存 `enforcement_mode=mock`。

Sol 独立审查不是一次形式复核：它先后复现 symlink TOCTOU、部分 allocation 残留、abort 误判、
close 后复用、空目录与 FIFO/socket 漏审计、launch relabel、短 digest、plan 注入和 partial-cohort
receipt 漂移；主代理逐项修复并添加负向测试。冻结版本最终由 Sol ACCEPT，无剩余 P0/P1/P2，focused
32 passed；handoff SHA-256 为
`196c305d073d781c54f4a5724ecd022f8a24e6c5e0a0eb763977458f560e27b8`。主代理最终八文件组合回归
**206 passed in 39.52s**，compileall 与 scoped diff-check 均通过。

当前阶段只关闭了三个设计 P1 中的 capability CPU/mock 子项。剩余关键工作为：外层 resource
supervisor 的 CPU N/A / CUDA under-lease 生命周期，以及 `protocol_synthetic` 与未来
`native_diagnostic` seam 的严格分离。下一批先实现 CPU/mock `ResourceGuard` 与 supervisor 顺序/失败
矩阵；继续禁止新 CUDA/native、K=3/统计扩张与论文结果修改，七项 formal gate 全部保持 `false`。
### S6.2 ResourceGuard R6 与 S6.3 supervisor contract scaffold（2026-10-23）

S6.2 CPU/mock `ResourceGuard` 已完成独立攻击性收口。冻结源码
`tools/benchmark_branch/fresh_history_resource.py` SHA-256 为
`182daadfa1c3413e4aa1ffa75df53c015141cbe2ea75fb8c3ee91e5eecf05766`，测试为
`126400c20afb23564558df92cb1c62f8871f2709d3e2e0eb173a59b8cc99c012`。
主代理 focused **60 passed**，组合回归 **257 passed**；Sol handoff
`s6-resource-guard-review.md`（SHA-256 `742d8ec610e065d3769c76f850c9a5ff31bc9c4eb196af8e07ea3bd7d474a6cb`）结论
**ACCEPT，无 P0/P1/P2**。R6 绑定四 context、group leader、run/source/checkpoint
identity、正常 exit code、cleanup seal、真实 phase prefix 和 strong handle registry；GPU、lease、native session
字段显式为 `not_applicable`，七项 formal gate 全为 `false`。没有运行 CUDA/native/GPU/Isaac/nvidia-smi。

随后落地 S6.3 `fresh_history_supervisor.py` 契约 scaffold：sealed plan/receipt、materialized-source
前置失败、mock enforcement 和 gate-closed receipt，并新增 3 个 focused contract tests；与 Resource/Capability
组合共 **95 passed**，compileall/diff-check 通过。ZCode GLM-5.3/max 已按冻结输入完成只读复测：95 passed，四项检查均通过，确认 scaffold 没有宣称 `HistoryBlockRuntime` 已绑定 evaluator-owned context IDs/process leaders。

S6.3 仍为 **running**，不能视为成功 fresh-history run。下一步必须先给 `HistoryBlockRuntime` 增加 evaluator-owned、condition-blind context 注入 seam，并在 initialization 前返回 process-group leaders；随后再把 capability audit、cleanup/replay receipt 与 supervisor 状态机绑定。继续禁止 CUDA/native、K=3/统计扩张、论文结果修改和任何 gate promotion。

### S6.3 evaluator-owned runtime injection（2026-10-23，仍进行中）

本轮已完成 runtime seam 的第一版收口：精确四个 sealed `ContextCapabilitySpec`、cohort plan digest、checkpoint-bound RNG commitments、condition-blind child launch、barrier 后 policy activation、初始化 seal、强制 pre-initialization registration receipt，以及独立 process-group leader 验证；aggregation 同时重新验证 capability plan/branch digest 与旧 ledger identity。

live tree 验证为 **181 passed**，`compileall` exit 0，scoped `git diff --check` exit 0。当前冻结 SHA：`history_runtime.py` `0e346f30…e6b372`；`history_process.py` `8056265c…d3fb4`；`history_aggregation.py` `101bab02…5622a`；注入测试 `618750c5…d77d0a`；runtime 测试 `1ca264e8…c416b`；aggregation 测试 `5ae90a35…f0ab9`；`__init__.py` `a51d04a1…4514`。

这仍不能把 S6.3 标为完成：supervisor scaffold 的 phase callback 尚未在 initialization 前接收并登记真实 context/process identities，且 supervisor receipt 尚未从全部真实 ledgers 重算。因此本轮不产生 qualification 或 gate promotion。

Sol R2 对当前 seam 的独立审查结论为 **REJECT**（handoff `s6-history-runtime-injection-review-r2.md`）：还需由 supervisor 持有 cohort/admission 权限，使用有实际效果的 capability initialization seal，防止伪造 registration receipt，调整 factory-before-admission 顺序，明确失败 initializer 的 policy 语义，强制 capability activation，并证明 cleanup root 由 evaluator 独占。故 181 项通过是接口/负向矩阵证据，不是 S6.3 完成证据。

### 后续排队

1. 以 `oracle-predicates-binding` 的 scoped commits `cf839ec8`、`0b6890c0` 接入 V2 19 条谓词，避免整分支合并；live tree 重新跑 26 项 oracle 专项与回归。
2. 在合同生成器登记评审 3.A 七字段：W/S 权重 1、I/R/T 权重 0、`τ_stable=25` 初值，以及 activation denominator、必要性、单调性、可见性、首次有效防重复规则。
3. 对 C-P2-02 的 prod/svc/liv 三件套同步插入 `quarantine_marked`，登记新版本+哈希并重跑 deployment-oracle 见证回放、技能验证门和难度分量；旧证书不沿用。
4. 实现 P4 类型化锁服务：被测系统可见 grant/deny 与评估器私有位置占用账本互补；无令牌动作或超容量占用才违约，申请/重试不算占用。
## 本轮增量（2026-10-23，合同 V2 与 S6.3 局部修复）

- `history_process.py`：初始化封存改为真实 IPC phase transition；超时/关闭改为按已验证 PGID 发送 SIGTERM/SIGKILL，避免只清 root PID。
- `history_runtime.py`：backend `activate_policy` 纳入必需接口；初始化超时上下文不再发送封存命令。
- `pilot_contract.py`：合同生成器登记七字段里程碑策略（W/S=1，I/R/T=0，`tau_stable_ticks=25`、激活分母、必要性、单调性、可见性、首次有效防重复）和 P4 类型化锁服务边界。
- `benchmark/task_catalog.json`：升级 `paper-task-catalog.v2`，登记 `C-P2-02-quarantine-marked-v2`，并将 `quarantine_marked` 同步加入 prod/svc/liv 三件套；登记及待重跑链见 [`C-P2-02_CONTRACT_REGISTRY_20261023.zh-CN.md`](C-P2-02_CONTRACT_REGISTRY_20261023.zh-CN.md)。
- 新增 `typed_lock_service.py`：公开 grant/deny/release，评估器私有位置占用账本，显式区分“无令牌动作”和“超容量占用”，申请/重试不计占用。
- `fresh_history_supervisor.py`：新增 supervisor-owned `FreshHistoryExecutionSession` 与准入 receipt；context handles 在提交四组 process leaders 前不可见，旧的 phase_runner 事后 `group_leaders` 自报路径已移除。
- 根据 Sol R4 复核再修：child 的 policy IPC 现在必须先收到 seal ACK，backend handle 记录 `sealed`；pilot validator 会拒绝七字段和 typed-lock policy 篡改；catalog loader 要求 v2 schema/合同版本，并把顶层版本注入 canonical digest。

验证：本轮合并定向套件首次 `199 passed, 1 flaky cleanup failure`，失败测试单独重跑通过；先前 supervisor/capability 套件 `176 passed`、合同/锁服务套件 `190 passed`，compileall 与 diff-check 通过。S6.3 仍未整体接受：失败初始化 aborted 终态、真实 process-group inventory/cleanup receipt 与 native 连接仍需后续闭环；formal gates 继续为 false。下一阶段重跑 deployment-oracle witness/技能门/难度重算，并补 supervisor admission 的真实 child identity 验证。

### 本轮阶段收尾（2026-10-23）

- 从 `oracle-predicates-binding` scoped port 了 V2 `oracle_predicates.py` 与专项测试；19 条谓词专项结果为 `26 passed, 4 skipped`。
- `pilot_contract.py` 已接入可执行 oracle 引用与阈值，状态严格为 `bound_pending_replay`；六候选合同工件已重新生成，19 条谓词计数保持一致，仍不授予认证。
- 新增 `cp2_replay.py` 离线审计 harness，并生成 [`replay_ledger.json`](../../../artifacts/catalog/replays/C-P2-02-quarantine-marked-v2/replay_ledger.json)：C-P2-02 三件套、V2 目录 digest 与难度 digest 已重算；无 runtime witness receipt，因此 `deployment_oracle_replayed=false`、技能门与 formal gates 仍为 false。
- 本轮 focused 回归为 `46 passed, 4 skipped`；history runtime/injection 为 `81 passed`；Sol 独审定向套件为 `103 passed`；compileall 与 diff-check 通过。

仍未闭合：真实 deployment witness replay、技能验证门、物理/原生 qualification、initializer abort receipt、真实 child PGID/admission 绑定。Goal 保持 active。

本轮继续推进后，`HistoryAbortReceipt` 已加入 history process 生命周期：失败 initializer 在未 assignment 前生成带 run/source/checkpoint/context/PID/status/failure-detail 的 sealed abort receipt，aggregation 强制其与 `InitializationOutcome.method_failure` 一一对应；新增负向/正向测试后定向回归为 `228 passed, 4 skipped`。这只关闭了 abort receipt 这个 P1 子项，真实 child PGID/admission 绑定与 deployment witness replay 仍未完成。

### Oracle predicates 与 C-P2-02 replay 收口（2026-10-24）

已按 `BRANCH_A_ORACLE_PREDICATES_20261023.zh-CN.md` 完成路径级接入。主工作树使用分支 V2 语义（`cf839ec8` 后的 `0b6890c0`）：6 个试点、19 条谓词、fail-closed cause code 与半开区间规则均保留。`pilot_contract.py` 重新生成六候选合同，19 条谓词均为 `bound_pending_replay`。

新增的 C-P2-02 replay 读取确定性 `oracle_witness.json`，对共同锚点谓词逐锚点执行并记录 evidence、witness digest 和 replay digest；三个锚点均得到 3/3 `satisfied`。focused 回归 **39 passed, 4 skipped**；benchmark_branch 全量回归 **674 passed, 5 skipped**。这仍是离线 oracle replay，`deployment_oracle_replayed=false`、技能门和 formal gates 全部保持 false。

### S10 P1 session authority 收口（2026-10-24）

协调者复核了 S10 R2 留下的 supervisor/session authority 缺口。当前工作树已有实现：supervisor session 创建并绑定唯一 `HistoryBlockRuntime`，runtime 只能消费当前 session authority；diagnostic authority、普通 callable 和逃逸的空 context 在 factory/child launch 前拒绝；finalization 交叉核对 admission、registration、runtime identity 与 process-group leaders。

CPU/mock focused 回归为 **186 passed in 41.73s**，compileall 与 scoped diff-check 均 exit 0。证据见 [`s10-p1-session-authority-closure-20261024.md`](../../../Documents/Codex/agent-team/workspaces/heterocoord-goal-zcode-20261022/handoffs/s10-p1-session-authority-closure-20261024.md)。该收口只关闭 S10 的 session-authority P1，不产生真实 fresh-history qualification；七项 formal gates 继续全部为 `false`，未启动 CUDA/Isaac/Kit/GPU/native workload。
