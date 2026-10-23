# 通用运行接入与验收边界

本工具为后续任务包提供执行和审计机制，不内置正式场景、任务 DAG、算法选择、
成功谓词或论文指标。Factory 等临时诊断的插件、配置和结果只能放在 `tmp/`；
不得把诊断目标注册成默认业务任务。

## 运行范围

| scope | 输入与用途 | 可以证明什么 |
| --- | --- | --- |
| `protocol_synthetic` | Dummy 或协议插件 | 生命周期、并发记账和文件完整性 |
| `measured_diagnostic` | 外部临时任务 + 实际 session + evaluator | 指定诊断的物理观测；不具备正式配对或论文发布资格 |
| `measured` | 外部 benchmark 冻结的 explicit jobs + 实际 session + evaluator | 正式身份下的原始实测证据；仍需外部严格配对与统计验收 |

`measured_diagnostic` 允许只选择 fact；两种实测范围均要求 scheduler preflight、
step/heartbeat、checkpoint readback 和可核验的 evaluator artifact。
`measured` 在启动 session 之前校验上游身份字段，不能靠改 scope 将诊断晋升。
`formal_pairing_eligible` 始终为 false，`strict_pair_claim_status` 始终为 unsupported。

## CLI 的插件入口

任务包提供可导入的 `module:factory`，在 JSON 配置中填写：

```json
{
  "runtime_factory": "my_task_package.runtime:create_runtime",
  "runtime_kwargs": {"task_file": "tmp/my-diagnostic/task.json"},
  "execution_scope": "measured_diagnostic",
  "slots": 1,
  "arms": ["fact"]
}
```

factory 签名为 `create_runtime(*, plan, run_dir, **runtime_kwargs)`，返回
`tools.benchmark_branch.runtime.RuntimeComponents`：adapter、session_backend、
scheduler、plugin_id、plugin_version，以及 executor_options。可选 executor_options
仅包含 requested_device、scene_reservation、branch_reservation、warmup_ticks。
factory 只组装对象，不启动 Isaac；正式场景在 `open_scene` 中启动。

```bash
python -m tools.benchmark_branch run --config tmp/my-diagnostic/config.json \
  --output tmp/my-diagnostic/run-1 --max-workers 1
python -m tools.benchmark_branch validate --run-dir tmp/my-diagnostic/run-1
python -m tools.benchmark_branch resume --run-dir tmp/my-diagnostic/run-1
```

run/resume 共用插件加载路径。manifest 固定 factory 源文件 SHA-256、配置和插件版本；
改变后必须新建运行目录。该 hash 不覆盖传递依赖、外部任务文件或资产，任务包必须在
自身版本/合同 digest 中固定这些输入，并在 `validate_contract` 检查。不要把凭据写入配置。
已有运行目录不得覆盖，重试保留原始 attempt 和失败记录。
恢复前校验已有 seal 与冻结计划，损坏时拒绝写入，不能通过 resume 重算校验和掩盖损坏。
seal 强制覆盖所有账本、evaluator 文件、plan 和 manifest；缺失 seal 或必需条目时
禁止自动 resume。旧版本不完整 seal 和未封存崩溃目录同样保留供人工审计，不自动迁移。
离线验证检查每一次 attempt 的
证据，不只检查最后一次。若重试进程在旧 seal 之后追加数据又崩溃而未封存，保留原目录
供审计并新建恢复运行，工具不会猜测哪些不匹配属于合法追加。

## Isaac 生命周期

`EaiSessionBackend` 只调用 `simulator.open_simulator_session`，不创建第二套 launcher。
调用者注入 launch_config、branch_driver、evaluator 和 run_dir。

driver 提供 `warmup(session, ticks=...)`、`open_branch(session, job, lease=...)`、
`run_branch(runtime, job, emit=...)`、`close_branch(runtime)`。
driver 负责 controller 特定的空闲命令、实际恢复、执行与原始观测；不同 controller
的动作维度不同，backend 不猜测零命令。driver 在 session 上暴露实际使用的
`benchmark_action_tensor`，供 `probe_eai_devices` 读取；probe 同时读取 live physics
device、GPU dynamics/pipeline 和 CUDA UUID。缺失信息拒绝准入，不回填配置值。
`CudaResourceProbe(device)` 可作为 scheduler 的 resource_probe，采集指定 CUDA 设备
的 UUID、可用显存和主机可用内存；遵循 CUDA_VISIBLE_DEVICES，不固定取物理 GPU 0。
跨进程统一容量预留仍由部署层负责，瞬时可用显存采样不能替代资源所有权。

evaluator 提供 `evaluate(runtime, job, execution=..., run_dir=...)`。它读取执行后的
物理状态/原始观测，独立产生 verdict；driver 自报 physical_success 不会传入最终结果。

backend 声明 `execution_affinity="caller_thread"`，因此 session 创建、step 和关闭
在主线程串行执行。请求 32 worker 不会制造 32 路物理并行的记录。需要实际并行时，
上层应提供隔离进程 backend 和统一资源准入；禁止多线程直接 step 同一 Isaac world。
通用 thread_pool backend 仍用于明确支持并发的 runtime；其隔离认证由提供者负责。
Kit fast_shutdown 在此 backend 中关闭，以便关闭 session 后仍能释放 lease 和封存账本。

`--timeout` 对 session backend 是协作式期限：emit 和返回点检查超时。阻塞的原生
step 无法被安全线程中断，需要部署层的进程 watchdog；不宣称为硬实时超时。

## 可重验的物理证据

外部 evaluator 调用 `write_evaluator_evidence`，写出内容寻址 JSON，包含：完整 job
绑定 digest（含 attempt/checkpoint）、evaluator 版本、原始 observations、status、
completed、physical_success 和 terminal/safety/feasibility 三个布尔谓词。
允许记录失败；谓词不全为 true 时，physical_success 不得为 true。

collector 与离线 validate 都重新读文件，验证 SHA-256、路径在运行目录内、分支身份、
版本以及 verdict 一致性。伪造非空 hash、借用另一分支/旧 attempt 的证据、事后修改
artifact 均拒绝。Hash 证明完整性和绑定，不证明 evaluator 诚实或谓词科学有效；
领域审计器仍须复核原始观测。固定版本和可信 evaluator 是外部任务包的责任。

## 外部 benchmark 的责任

旧文档中的 `benchmark.*` 是上游权威层名称，不是本仓库随附或自动安装的包。
本仓库以 `branch_plan_from_batch` 的结构化对象接口接收其冻结身份，不复制论文协议。
上游负责 ExperimentBatch、causal replay、sham 校准、strict pair gate、reducer 和
release。只有上游实现安装后才能做其版本兼容测试；协议 fixture 测试不能替代它。
旧 `demo.benchmark.branch_plugins` 测试是历史可选集成，不是通用工具的运行依赖。

接入新任务的顺序：先验证合同与资源依赖，再单分支实测，再恢复/失败/重试，随后按
外部协议验证配对，最后逐级扩展隔离并发。不能由 collector 自动批准科学结论。

## 开发阶段与后续工作

当前阶段是“通用单 session 基础链路已验证，可以接入真实任务”，不是完整实验系统
已验收。运行接入、恢复前完整性校验、逐 attempt 证据审计和资源释放已有回归覆盖；
task 层提供可选的有界 DAG 调度、执行反馈和重规划回调，仍需外部 executor/evaluator。

本次针对工具、回放字段、任务编排和方法注册的轻量回归为 109 passed、1 skipped；
跳过项是未安装的历史外部场景插件。Isaac GPU 临时诊断完成 2 个串行分支，每个
120 tick，并验证完成后 resume 不重复执行。其物理目标仅为动态刚体恢复、下落及
稳定支撑，不代表机器人搬运流程或正式方法效果。请求 4 worker 时实际峰值仍为 1。
临时场景、插件和原始实验产物全部保留在 `tmp/`，不作为维护的任务输入。

后续工作可沿两条路线推进，无须等待正式任务流程全部完成：

| 路线 | 待完成内容 | 验收边界 |
| --- | --- | --- |
| 独立开发的基础设施 | 已增加隔离进程 supervisor、固定跨进程 GPU slot 预留、硬超时 watchdog、崩溃后的显式新 attempt；声明依赖指纹 | 见 PROCESS_RUNTIME.zh-CN.md；真实轻量 Isaac 已测1/2/4进程。不是通用32路或复杂场景容量认证 |
| 通用状态接口 | 已增加 WorldSnapshot、共享资源、协作步进与同session skill checkpoint，对漏项和不匹配拒绝 | 真实 Pepper 持箱回退已测；完整因果状态仍由机器人、controller、随机源和通信状态共同确定 |
| 需要真实任务的集成 | 物体与 skill 绑定、业务 DAG、成功/安全/可行性谓词、执行反馈与算法重规划闭环 | 真实状态与独立 evaluator 证明端到端任务效果；任务返回 completed 不等于物理成功 |
| 需要外部实验协议的验收 | 完整因果恢复、sham 校准、严格配对、统计 reducer 与发布门禁 | 上游冻结身份及协议验收；本工具不自行授予严格配对资格 |

真实任务接入后应以该任务重新测量容量、恢复轨迹及成功/安全谓词。
当前1→2→4轻量诊断不能替代正式任务的测量，尚未验证32路GPU物理并发。

task 层后续已增加通用 `SkillTaskExecutor`、live `WorldSnapshot` 与 session 内
资源租约。branch driver 可在自己持有的 session 上组装这些组件；每个分支使用
新 executor，业务任务仍由外部输入。接线与限制见
[task 运行接入说明](../../task/RUNTIME_GUIDE.zh-CN.md)，分阶段进度见
[开发清单](../../task/DEVELOPMENT_TODO.zh-CN.md)。该资源管理器不替代 GPU scheduler，
也不为跨进程隔离或正式任务成功提供认证。

外层进程隔离与依赖声明入口见 [进程与复现说明](PROCESS_RUNTIME.zh-CN.md)。
## 新建场景诊断与执行/物理结果分离

默认仍要求真实 checkpoint restore/readback。新建物理场景的临时接线诊断可显式设置
`job.metadata.checkpoint_receipt_mode="fresh_initialization_v1"`，仅限
`measured_diagnostic`、`fact` 分支和 session backend。正式 `measured`、其他分支
或独立进程 adapter 不能使用此方式代替恢复。

`metadata.initialization_spec` 在运行前冻结，包含
`schema="heterocoord.fresh_initialization.v1"`、非空标量映射 `expected` 和同键的
非负 `absolute_tolerances`。`checkpoint_digest` 是该规范的 canonical digest；
`restore_checkpoint_digest` 必须为空。它标识初始化规范，不是捕获的物理状态。
driver 从真实场景读取同键标量，通过 `initialization_receipt(job, readback)` 生成
凭据，并在第一个 branch step 前仅发送一次 `emit("checkpoints", receipt)`。
凭据保存原始读数、其哈希及逐项容差比较，离线 validator 重新计算；不能携带
`restore_passed`/`restore_status`。缺少或失败的初始化不能提供已测物理结果。
具体读数的含义和容差仍由外部任务/诊断 driver 决定，通用工具不认证场景语义。

`EaiSessionBackend` 的 driver 应返回明确的 `status`、布尔 `completed` 和
`method_outcome`。独立 evaluator 的完整签名结果保存为 `evaluator_verdict`。
驱动器状态 `valid_terminal`/`completed`/`success` 对应 `completed=true`；
`crashed`、`method_timeout`、`malformed_action`、`budget_overrun`、`missing_response`、
`safe_stop`、`team_state_infeasible`、`right_censored`、`infrastructure_invalid`
对应各自未完成结果。未知状态、非布尔完成标记及互相矛盾的别名字段均拒绝。
方法失败时 episode 保留方法失败状态和 `completed=false`，但 evaluator 可以
记录清理后的 `physical_success=true`。这两个值分别回答动作是否成功执行、
场景是否达到评估器的物理终态。例如持箱回调异常后安全放下，不能变成方法成功。
单批绑定和多批分析均拒绝把 sealed method failure 重标为 completed。
旧的 flat evaluator artifact 继续兼容；嵌套与顶层重复证据字段必须一致。

## 2026-10-20 工具更新

实测 `resume` 现在要求冻结的基础设施恢复登记并保留方法失败；全局并发、关闭失败
资源保留、有界回放缓存和主系统状态读回字段见
[优化与接入说明](OPTIMIZATION_20261020.zh-CN.md)。旧文档中的“重试”描述不能解释为
允许重跑方法失败，也不能把新进程重建插入旧配对。
