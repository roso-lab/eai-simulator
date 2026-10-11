# 多场景分支数据采集工具

[English](README.md) · [第三方插件接入](PLUGIN_GUIDE.md) · [示例依赖](examples/README.md)

当前运行入口、诊断范围、证据校验与外部依赖见
[通用运行接入](RUNTIME_INTEGRATION.zh-CN.md)。CLI 支持 `runtime_factory` 注入
backend/scheduler；`EaiSessionBackend` 使用主线程串行会话。具体场景与任务仍由外部提供。

`benchmark_branch` 是通用的分支并行数据采集器：支持按场景、任务、系统、条件和
repeat 生成配对实验，执行分支、保存账本、重试恢复、校验、汇总和导出。
本次发布包含工具代码、测试、schema、配置示例和文档。具体任务 runner、场景资产、
机器人控制器及 evaluator 由上游插件提供。

## 与 physics_replay 的关系

| 模块 | 职责 |
| --- | --- |
| `tools/benchmark_branch/` | 四类实验分支、身份、执行器、账本、恢复和数据校验 |
| `tools/physics_replay/` | checkpoint capture/restore/fidelity；ReplayScheduler 负责 GPU 准入、容量和 lease 生命周期 |
| 上游任务插件 | 创建 Isaac 应用、分支状态隔离、物理推进、任务语义和外部 evaluator |

采集器不会猜测 GPU 容量。CLI 的 runtime factory 或上层 runner 显式提供一个
`SessionBackend` 和一个 `ReplayScheduler` 后，`BranchExecutor` 会让所有 scene group
共用同一容量账本，并在每个 scene session 内并发准入 branch。两个独立 CLI 进程不会
自动共享显存预算。
工具不实现 EMOS planner、D1–D6 指标或第二套物理成功判定。

## 安装与快速使用

从仓库根目录执行，Python 3.10+。JSON/Dummy 采集只用标准库，测试需要 `pytest`；
完整 YAML 语法可选安装 `PyYAML`，Parquet 导出需要 `pyarrow`。
只有真实场景插件需要 Isaac Sim/Isaac Lab、资产、控制器和对应 GPU 环境。

```bash
python3 -m tools.benchmark_branch plan --config tools/benchmark_branch/examples/dummy_32.json
python3 -m tools.benchmark_branch run --config tools/benchmark_branch/examples/dummy_32.json \
  --max-workers 32 --output /tmp/branch-dummy-run
python3 -m tools.benchmark_branch validate --run-dir /tmp/branch-dummy-run
python3 -m tools.benchmark_branch summarize --run-dir /tmp/branch-dummy-run
python3 -m tools.benchmark_branch export --run-dir /tmp/branch-dummy-run --format jsonl
python3 -m tools.benchmark_branch resume --run-dir /tmp/branch-dummy-run --max-workers 32
```

新运行使用新目录；重复执行时更换目录或显式 `resume`。`run` 还支持 `--slots`、
`--arms`、`--execution-mode`、`--execution-scope`、`--timeout`、`--queue-size`、
`--resume`。正式计划必须含四类 arm，不能用 `--arms` 静默删掉某类。
`validate` 返回非零表示完整性校验失败；命令退出成功本身不是物理成功证据。

## 实验规模与四类分支

| 分支 | 用途 |
| --- | --- |
| `fact` | 正式事实执行，保留原始结果 |
| `reference` | 同一 checkpoint 的健康或无干预延续 |
| `sham` | 检查 checkpoint 恢复和回放一致性 |
| `intervention` | 通信、角色、时序、能力或资源的诊断干预 |

`pair_slots = slots × repeats × 场景数 × 任务数 × 系统数 × 条件数`；
`logical_branches = pair_slots × 4`。单一组合默认 `slots=32`，得到 128 个逻辑分支。

新建简易计划默认 `base_identity_version=2`：同一 run/scene/task/version/slot/base seed
跨 system、condition、repeat 使用同一个基础实例 ID；分支与配对组仍区分这些轴。
显式 benchmark jobs 保留上游身份。恢复旧封存计划时，缺少版本字段按 v1 重建，
不改写历史日志或自动将旧样本升级为可推断数据。

下游的冻结计划检查、只读 collector/evaluator 交接和模拟数学验证见
[实验分析说明](../experiment_analysis/README.md) 与
[框架审查](../experiment_analysis/ARCHITECTURE_AUDIT.zh-CN.md)。该层不会自行授予论文发布资格。
`max-workers=32` 是请求并发上限，需要分波运行全部 128 分支。
[多维示例](examples/dummy_matrix.json) 演示笛卡尔积；不同场景支持的任务组合应由上游选择。

默认 CLI executor 仍使用一个 adapter 和一个 base checkpoint，不会自动为每个 scene
选择不同插件或创建不同 checkpoint。显式接入 `SessionBackend` 后，backend 可以按
`scene_key` 同时打开多个 scene session；各 scene 的 branch 会由同一个 scheduler
统一准入。backend 仍需自行提供每个场景的合同、checkpoint 和 Isaac session。

## 两种执行方式

| 模式 | 状态和故障隔离 |
| --- | --- |
| `isolated_parallel`（默认） | 每分支新进程、独立 runtime/session/RNG/输出；父进程通过有界队列写总账 |
| `shared_session` | 一个场景由一个 adapter 管理 simulator session；分支占用独立环境槽位，共用只读静态资产 |

共享模式由插件实现 `run_shared_session()`。分支之间必须隔离可变物理状态、控制器、
任务状态、消息队列、随机流和 restore 状态，并验证跨环境碰撞过滤。共享进程崩溃可能
影响整个场景，不能声称拥有“每分支独立进程”的故障隔离能力。
`job.slot` 是实验配对编号；`environment_slot` 才是当前波次的物理环境槽位。

纯 Python 的共享接口检查：

```bash
python3 -m tools.benchmark_branch run --config tools/benchmark_branch/examples/dummy_32.json \
  --execution-mode shared_session --max-workers 32 --output /tmp/branch-shared-dummy-run
```

这不会启动 Isaac。实际“双场景同时打开，每场景一个 Isaac、内部 32 分支”需要在 GPU
主机上由上游分别启动 Modern House 与 Warehouse 的共享插件，各用独立输出目录，并
经统一调度器准入。示例配置见 [examples/README.md](examples/README.md)。
配置保留 `kinematic` 搬运和 `public_greedy` participant，不能据此宣称动态接触搬运或
EMOS 已验收；完整 authored horizon 也不等于 evaluator 确认完整物理任务成功。

## 如何读并行计数

- `requested`：计划分支总数；`requested_max_workers`：请求进程/槽位上限。
- `admitted_max_workers` 或 `admitted_shared_envs`：资源预算允许的容量。
- `resident`：现有后端可能保留已分配或峰值容量，运行结束后不代表仍有存活 worker。
- `active`：执行器当前活动计数；`completed/failed/queued`：运行完成、失败和待执行数量。
- `observed_peak_branch_workers`：进程模式为父进程看到的存活子进程峰值；共享模式取插件报告，缺失记 0。

不能把准入数、队列数或存活进程数直接当作真实物理并行数。32 分支并行验收还需要
32 个分支仿真区间实际重叠、heartbeat、首末 tick、独立身份/账本、隔离与设备证据。
当前 `validate.overlap_detected` 只表示存在重叠，不是 32 路重叠的强制门槛。
共享 Dummy 只能证明协议记账，不能证明 GPU 物理容量。

## 数据、恢复和校验

输出目录包含 `manifest.json`、`plan.json`、`run_state.json`、`collector_state.json`、
`checksums.sha256`、`validation.json`、`summary.json`，以及每分支的
`branches/<branch-id>/ledger.jsonl`。总账为追加写 JSONL：

| 账本 | 内容 |
| --- | --- |
| `branch_manifest` / `checkpoints` | 稳定实验身份、合同/版本/hash、checkpoint 与 restore 读回 |
| `steps` / `episodes` | 每步状态、运行终局、外部 outcome |
| `events` / `progress` | 连续事件编号与任务进度 |
| `network` / `costs` | 消息收发/消费、决策与成本 |
| `resources` / `metrics` | 资源、worker 生命周期、心跳与计数 |
| `observations` | 传感器帧引用、hash 和观测消费；与 evaluator 真值分开 |

父进程统一写总账；生命周期边界 fsync，高频记录先 flush、关闭时 fsync。
`resume` 为未完成分支追加新 attempt，旧行保留。插件必须能够重建相同的冻结 checkpoint；
采集器不会替插件序列化任意 simulator 隐状态。resume 重载冻结的 runtime factory、
timeout 和 resource_limit；scheduler 容量及预留策略由该 factory 负责重建。

`validate` 检查身份漂移、重复 branch、checkpoint digest、restore、配对角色、sham、
缺失账本、事件序号和 checksum。restore mismatch 排除配对统计，同时保留 raw fact；
sham 或 intervention 的成功不能覆盖 fact。
`summarize` 从 raw ledger 重建结果，`export` 写新文件，不要把导出路径指定为原始账本。
JSON schema 提供字段合同，CLI 使用自身校验逻辑，不自动运行通用 JSON Schema 引擎。

`resource_limit` 支持 `max_workers`、`per_worker_ram_bytes`、`per_worker_vram_bytes`、
`min_ram_available_bytes`、`min_vram_free_bytes`。这是尽力而为的容量估算，GPU 采样当前
取 GPU 0；严格 GPU 准入必须使用 ReplayScheduler 和真实设备探针，不能仅看配置 `cuda`。

## SessionBackend 接线

正式 `measured` 运行现在可以通过 `BranchExecutor` 显式传入
`SessionBackend` 和 `ReplayScheduler`。执行顺序是：

```text
scene lease → open_scene → warm-up → scheduler.preflight
→ branch lease → open_branch/run_branch/close_branch
→ release_branch → close_scene/release_scene
```

接口定义在 [session.py](session.py)。多个 `scene_key` 会由独立 session 线程并发处理，
每个 scene 内的 branch 也按 `max_branches_per_scene` 并发运行；实际容量仍由同一个
`ReplayScheduler` 控制。`run_branch` 必须返回明确的
`completed` 和 `status`；缺失响应记为 `missing_response`。GPU preflight 失败会保留
`infrastructure_invalid` 原始 episode，`physical_success` 保持 `null`，不会取得 branch
lease。scheduler audit 写入 `resources.jsonl`。

如果 `execution_scope="measured"` 却没有同时提供这两个对象，collector 会 fail closed；
仅写 `cuda:0` 不能替代 actual physics/controller device 和 GPU UUID 证据。

## 证据状态和当前边界

| execution_scope | 含义 |
| --- | --- |
| `protocol_synthetic` | Dummy/协议/调度测试，不能当物理证据 |
| `measured` | 插件提供实际运行测量；仍可能没有 evaluator 结论 |
| `pending` | 所需执行或证据尚未完成 |
| `failed` | 执行失败，保留失败原因 |

`completed`、assignment、进程退出、回放成功和 authored phase 完成均不等于物理成功。
外部 evaluator 未接入时保留 `physical_success: null` / `not_evaluable`；超时、右删失、
无效实例、缺失响应不能自动填零。正确的 safe-stop 和 team-state-infeasible 应按外部
可行性证据解释。工具的 pairing 检查不是论文主干的 strict paired gate 或 D1–D6。

本次包保留 Modern House/Warehouse 的配置示例和可选集成测试，但不包含
`demo.benchmark.branch_plugins`、场景 runner 和资产。未安装上游插件时，对应测试明确
skip；Dummy、采集器和 scheduler 测试仍可独立执行。

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q \
  tools/physics_replay/tests/test_scheduler.py tools/benchmark_branch/tests
```

该测试集合不会启动 Isaac，不构成“双场景 GPU 各 32 分支完整任务”的验收报告。
