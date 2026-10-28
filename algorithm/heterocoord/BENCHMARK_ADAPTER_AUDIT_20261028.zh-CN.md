# 被测算法接入审计与 Benchmark 优化点

日期：2026-10-28。审计对象为 HeteroCoord 分支六个论文结果列的原生 adapter：FMC-ATA、BT capabilities、SADCHER、HARL:HAPPO、TeamWeaver、EMOS。审计只检查接入边界和实验公平性，不把接口测试当作论文分数或物理成功。

## 审计结论

| 项目 | 结论 | 处理 |
|---|---|---|
| 原算法是否实际被调用 | 通过。六个入口均由 `benchmark.native_algorithms.build_native_adapter` 构造，并由固定 commit 的 source integrity envelope 包裹 | 保留源码、权重、配置和调用前后 hash；不允许旧 Fire Rescue facade 作为 fallback |
| 输入是否被偷偷修正 | 通过。各 adapter 对不可表达的 DAG、coalition、能力、权重、观测和状态返回拒绝或 `N/A` | 保留原生输入与拒绝原因；不做固定分配、最大概率选择或静默重试 |
| 输出是否被宿主重分配 | 通过。TaskRunner 只校验身份、能力、依赖和执行反馈；宿主不替算法重新选择机器人 | 物理宿主只消费 native assignment；BT/FMC 的队列和树状态由原生接口返回 |
| 反馈生命周期 | 分方法处理。HARL 使用 decision token 和完整反馈；BT 使用 endpoint feedback；SADCHER 将 executor 完成状态转成显式 native status；FMC 明确只消费原生日程，EMOS 当前不虚构 native feedback | Benchmark 统计必须记录 `feedback_scope`，不能把宿主反馈统一命名为算法重规划 |
| 计时 | 已优化。统一 envelope 保存 provider wall time、源码校验/封装耗时和总耗时 | 论文算法耗时默认取 provider wall time；端点启动、Python 导入、源码校验、Isaac 执行另列 |
| 证据回执 | 已优化。统一 CLI 拒绝覆盖已有 task/config/checkpoint/receipt，并使用同目录临时文件加 hard link 发布 | 每次运行使用新的 evidence directory 或新的 report 路径；并校验输入 hash 未改变 |
| 条件身份 | 通过。正式六列、源码 commit、checkpoint manifest、adapter 配置和信息权利保持独立登记 | 未冻结的正式比较条件继续为 pending，不进入论文分数汇总 |

## 方法级 Benchmark 优化

### FMC-ATA

当前 adapter 只接受原算法能严格表达的独立、单能力、`max_players=1` 任务，并保留原生 schedule 顺序。这是必要的公平边界。建议 Benchmark 将“原生求解时间”“schedule 消费时间”“UR5 执行时间”分成三列；单任务 phase-II 收尾停滞继续计为原生失败/xfail，不能通过延长宿主等待或重新竞拍修复。

### BT capabilities

BT 的 auction、`Parallel`、`MemorySequence` 和依赖汇合都通过 native ROS endpoint 进入。Benchmark 应分别报告首次竞拍、每次 reauction、树 tick、executor feedback 和最终 evaluator milestone。不要把 endpoint 协议应答当成机器人完成；当前真实 ROS 测试是协议证据，物理成功必须引用独立 EAI receipt。

### SADCHER

SADCHER 的输入包含显式位置、持续时间、状态和剩余时间，且原生环境固定 speed normalization。Benchmark 应冻结场景坐标、duration normalization、checkpoint 文件和 CUDA/CPU 条件；一个原生 idle 输出应保留为 idle，不能通过手动改 XY、强制派发或替换 checkpoint 变成成功。coalition dispatch 尚未接入通用单机器人 `Assignment`，应单独列为 `not_evaluable`。

### HARL:HAPPO

HARL 必须按 manifest 绑定 actor 数、观测 schema、action mask、RNN 状态、checkpoint hash、episode id 和反馈 token。当前本地 `.pt` 是 smoke/自训练条件，不能标为官方权重。Benchmark 应把 actor inference、环境 step、反馈更新、reset 和 checkpoint 加载分别计时，并禁止把不匹配 UR5 的策略输出映射成抓放动作。

### TeamWeaver

TeamWeaver 保留原 HRCS/MIQP、Gurobi/CVXPY 和原 LLMPlanner。提示词和结构化输出格式约束可以作为单独实验条件，但必须记录 prompt/schema hash、模型、token 请求数和 parser 拒绝数；不能把“输出格式更稳定”写成算法性能提升。当前完整 semantic 多轮持久状态和 coalition 尚未满足通用 Benchmark 条件，应继续单独报告。

### EMOS

EMOS 的结构化输出约束是合法的 I/O 条件，因为原始讨论和反思仍然执行，且原始响应和 hash 被保留。Benchmark 应同时报告 native request 次数、token 用量、schema refusal、输入初始化次数、宿主执行时间和 evaluator 结果。没有外部 native feedback 接口时，不应调用通用 replan 或将宿主反馈写回 EMOS。

## 建议的最终统计格式

每个算法和每个场景至少保存：

- `source_commit`、`adapter_hash`、`checkpoint_or_manifest_hash`、`task_hash`、`condition_hash`；
- native decision / native failure / unsupported input / timeout / executor failure / evaluator success 的互斥状态；
- `provider_wall_s`、`adapter_overhead_wall_s`、`executor_wall_s`、`evaluator_wall_s`；
- native calls、replan calls、feedback batches、request/token 计数；
- assignment coverage、dependency completion、physical milestone 和最终分数的独立字段。

只有在统一场景、重复次数、随机种子、通信/故障条件、信息权利和独立 evaluator 都冻结后，才汇总 Benchmark 分数。当前分支达到的是可审计的原生接入和有界执行条件，尚未自动升级为论文复现结果。

## 本轮验证

- 共享接入与回执审计：298 passed。
- EMOS/BT 专项：204 passed。
- HARL：69 passed。
- SADCHER：60 passed。
- FMC-ATA：68 passed，4 个已知上游限制 xfail。
- 证据：`records/algorithm_adapters/audit_20261028/`。
