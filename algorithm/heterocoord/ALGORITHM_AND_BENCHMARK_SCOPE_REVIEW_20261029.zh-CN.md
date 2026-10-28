# 六类被测算法与 HeteroCoord Benchmark 适配边界审查

日期：2026-10-29

本报告用于下一阶段适配前的研究基线。它把论文/公开源码中的原生能力、当前 EAI adapter 的转换边界和 HeteroCoord Benchmark 的可测边界分开登记。报告不把 adapter 增加的执行调度、反馈账本或 evaluator 证据归因给原算法，也不把缺少证据的结果填成零。

## 1. 审查材料与固定版本

Benchmark 论文材料：

- `/home/airs/下载/CVPR2026_Template.pdf`
- 当前提取文本：`/tmp/cvpr2026_template.txt`
- 论文正式结果列：FMC-ATA、BT capabilities、SADCHER、HARL:HAPPO、TeamWeaver、EMOS。
- HeteroMRTA 按用户要求排除。

公开源码快照由 `paper_algorithms.json` 和 source receipt 登记；本次审查使用的上游提交为：

| 算法 | 上游快照 |
|---|---|
| FMC-ATA | `7872a94776cfe21cf58cadf44007382deb650eea` |
| BT capabilities / ros_bt_py | `5438f6e8d7599e39ec12104c0a9f0a8f1543838d` |
| SADCHER | `a6b5363a4ab9bccb540f0ecf7f10709b8c7911ae` |
| HARL / HAPPO | `b1af98b0dbab72a2eee9d160751cd09aedbb8ce2` |
| TeamWeaver | `edf4619e4730860b96eebaac39620df4bbdc486a` |
| EMOS | `e9501db45d634b087bf5d1a14228266685e8feeb` |

远端检查：`origin/HeteroCoord` 当前为 `5d6d1bc38132ac743c481cfaa8c174bc8bd2c502`；本报告在隔离发布工作树中编写，不覆盖主工作区的用户改动。

## 2. Benchmark 先决原则

HeteroCoord 的比较对象是“注册过的算法条件”，不是一个把所有算法强行变成相同任务分配器的接口。运行前必须登记任务义务图、关系签名、P1/P2/P3/P4 原语、P3-J/P3-S 子类型、公开观测、信息权限、干预预算、源码版本、模型/权重版本和 adapter 版本。

算法负责其原生的分解、角色、coalition、通信、重规划和停止决定。Fleet/EAI 负责身份、路由、锁、执行和审计；外部 evaluator 负责里程碑、物理条件和终止证据。算法输出不能声明物理成功。

因此需要区分以下状态：

| 状态 | 含义 |
|---|---|
| `native_decision` | 原算法在其原生输入契约下产生了输出 |
| `native_failure` | 原算法按原生语义失败或拒绝 |
| `unsupported_input` | adapter 没有猜测或补齐原算法未定义的输入 |
| `adapter_scope_rejection` | 原生输出存在，但统一接入契约无法表达 |
| `executor_failure` | EAI 执行边界失败 |
| `evaluator_success` | 外部 evaluator 证实了注册的里程碑 |
| `N/A` / `not_evaluable` | 当前条件没有定义或没有足够证据支持该指标 |

`N/A` 不是零分，也不是把算法判为失败。它表示该指标在当前算法条件、场景、权限或证据边界下不可比较。

## 3. 统一接入契约的关键限制

当前权威类型在 `task/contracts.py` 和 `algorithm/common_coordination/contract.py`：

- `EaiTaskPackage` 提供任务图、机器人名册、能力、位置和公开 metadata；
- `AlgorithmDecision` 提供条件身份、`Assignment`、未分配任务和 provenance；
- `Assignment` 当前是 `(task_id, robot_id)` 的单机器人绑定；
- `ExecutionFeedback` 和 `MilestoneEvidence` 属于 EAI/evaluator 边界；
- `TaskRunner` 可以消费 DAG 反馈，但不会凭队列清空、进程退出或 `env.step()` 推断物理成功。

Benchmark 的 `C-P3J-01` 要求至少两个具备资格的参与者在同步窗口内完成一个不可拆分联合义务，并由外部 joint predicate 验收。当前 `Assignment` 没有 coalition member 集合，因此：

1. SADCHER 的原生 coalition 输出不能截成一个 robot；
2. 一个单机器人 `Assignment` 不能冒充 P3-J；
3. 若当前 executor 不能接收完整 coalition，应保存完整 native decision，并记录 `benchmark_contract_cannot_represent_native_coalition`；
4. 只有在统一契约和 executor 都新增经过登记的 coalition 表达后，才可以继续做 SADCHER P3-J 执行测试；
5. 在此之前，SADCHER 的 coalition 能力应标为“原算法已有、当前统一接入未表达”，不能标为“算法不支持”。

## 4. 六类算法的原生边界

### 4.1 FMC-ATA

原生输入包括 player 的位置、速度、能力和 workload，task 的位置和 importance，`max_players`，通信协议、NCLO/convergence 参数以及异步消息状态。原生 solver 是 Fisher market 风格的异步分配过程，输出是按 player 的 schedule/fraction/任务分配状态。

当前 adapter 保留原始 `FMC_ATA`、utility、阈值、通信协议和终止逻辑，不取最大 fraction，不排序、不修复原生 schedule，也不在 native failure 后补分配。宿主只消费原生日程队列并把 executor feedback 记入 EAI ledger；这不等于 FMC 内部重新竞拍。

当前声明的有效范围是：静态任务、单能力、空闲机器人、理想通信、无前置依赖、`max_players=1` 的独立单任务条件。原生动态任务到达、通信退化、fractional/coalition 表达和反馈驱动重分配尚未作为当前适配条件启用。

应保持 `N/A` 的项目：

- `native_feedback_reallocation`：当前生命周期没有把 EAI feedback 写回 FMC market state；
- coalition 物理执行和 divisible-workload 的统一验收：当前 transport/assignment 契约没有原生表示；
- 接触抓取、真实物理成功率和全局最优性：FMC-ATA 本身不提供这些 evaluator 能力；
- 动态到达、延迟/丢包：除非输入条件明确注册并由原生协议消费。

允许的下一步是扩展到源码已经定义的通信、动态到达和 fraction 输入，并保持原生输出表示。禁止用 argmax、四舍五入、host 重新分配或默认 coalition 替代原 solver。

### 4.2 BT capabilities / ros_bt_py

原生系统由 capability repository、capability auction、`ParallelAuctionManager`、`MemorySequence`、`Parallel` 以及 ROS service/action 边界组成。其分配输出是 capability/mission tree 的结构化任务关系；行为树 tick 根据 child state 推进 success/failure/continue。

当前 adapter 检查 source import、algorithm class、tree 类型、task/robot/bid identity，不为缺失 bid 生成默认 cost，并保留 sequence/parallel 差异。EAI feedback leaf 是接入边界：auction 先产生 assignment，执行反馈进入 native BT leaf，再由 `MemorySequence`/`Parallel` 推进。

当前可声明的是原生竞拍、树状态和已注册执行反馈的协议闭环。不能把以下内容归因给 BT 算法：

- UR5 follow-proxy 或其他执行器的物理成功；
- 外部 evaluator 的接触、位置或 milestone 判断；
- auction 全局最优性；
- 未提供的复杂 DAG、coalition 或 MissionControl 全系统泛化能力。

缺少物理 evidence、原 ROS 全系统契约或论文同构场景时，对应分数必须 `N/A`。

### 4.3 SADCHER

SADCHER 原生具备动态 coalition formation、任务 precedence、机器人/任务位置、任务 duration、机器人 capability、remaining time、task status、同步 coalition 执行和实时 reassignment/replanning。方法侧包含 attention/GAT、transformer、reward MLP、robot-task reward matrix、relaxed bipartite matching、coalition assignment、precedence masking、同步到达和等待。

当前 adapter 保留位置、duration、status、remaining duration、技能、prerequisites、start/end sentinel、checkpoint、native idle、native coalition、premature exit 和 feedback-driven native decision。遇到 coalition 时保存完整 native decision 并拒绝单机器人截断，避免改变算法语义。

当前缺口不是 SADCHER 没有 coalition，而是统一 `Assignment`/TaskRunner 尚不能表达同任务多成员联合执行。因此：

- 多机器人分别执行不同任务、带 DAG 依赖的原生闭环可测；
- 同一任务 coalition 的原生决策可以审计，但执行结果暂为 `N/A`；
- 正确原因是 `benchmark_contract_cannot_represent_native_coalition`；
- 不能写成 `algorithm_does_not_support_coalition`；
- 论文的 makespan、assignment reward、computation time 和 unseen randomized instances 不能用 EAI 物理数字直接替代。

### 4.4 HARL:HAPPO

HARL/HAPPO 是 heterogeneous cooperative MARL 的训练/推理框架。原生生命周期是 environment reset、每个 agent 的 observation、actor action distribution、environment transition、reward/done、rollout buffer、return/advantage、按 agent 顺序更新 actor、centralized critic 和 checkpoint。

原生输出是每个 agent 的离散或连续 action，具体形式由 environment、policy head 和 checkpoint 决定。HAPPO 原生不输出 `Assignment`、SkillRequest、任务依赖图、物理成功、自然语言执行反馈或 HeteroCoord checkpoint branch。

当前 adapter 的原则是严格 checkpoint-gated：必须匹配 source commit、actor 数量和顺序、observation shape/字段顺序、share observation、action space/编码、mask、normalization、recurrent state、policy head 和 checkpoint keys。宏动作环境和 `Assignment` 映射都是 HeteroCoord adapter 语义，不能称为 HAPPO 原生输出。

没有与当前环境同构的官方 checkpoint 时：

- 自训练 smoke 权重只能作为独立 `self_trained_macro_condition`；
- 不能重命名为官方 HAPPO 评测；
- 不能把新定义的 EAI task success 直接写成原论文 reward；
- UR5 抓放、P3-J、物理成功和论文分数均为 `N/A`，除非 checkpoint 和环境契约完整匹配并有 evaluator evidence。

### 4.5 TeamWeaver

TeamWeaver 的核心链路是自然语言任务/场景描述、LLM semantic decomposition、结构化任务/阶段、能力与约束建模、MIQP/optimization allocation 和机器人任务绑定。公开代码中的 LLM planner、HRCS/MIQP allocator 和动作生成属于原算法核心边界。

当前 adapter 保留原 parser、原始 LLM response、任务/约束结构、MIQP solver 和 provenance。两处 `iflen` 语法修复属于用户明确授权的语法兼容补丁，只修复源码无法解析的问题，不改变 planner、目标函数、约束或分配语义，必须单独标为 adapted implementation。

当前 EAI 还提供 `ExecutionFeedback`、`CapabilityTracker`、phase scheduler、lease/ownership、checkpoint/replay 和外部 evaluator。这些是 HeteroCoord execution/evaluation protocol extension。`accept_feedback()` 和 `replan()` 可以作为适配条件运行，但必须同时登记 `adapter_extension`，不能写成 TeamWeaver 论文原生模块。

LLM parser 失败后如果调用 fallback/direct factory planner，必须保留 `native_llm_output=rejected/unavailable` 和 `fallback_used=true`；fallback 结果不能计入 native LLM/MIQP 结果。当前任务图依赖边、非空历史重建、同任务 coalition 和完整多轮持久执行仍需按原生契约逐项验收，不能静默补实现。

### 4.6 EMOS

EMOS 原生输入包括 robot resume/type/capability/position、scene/task description、subtask description、target/object information、LLM/API 配置和 group discussion 消息状态。原生阶段是 leader 读取团队和任务信息、机器人 agent 讨论/确认、parser 解析回复并形成 assignment；Habitat-MAS/CrabAgent 执行属于后续环境边界。

当前 native adapter 保留原始 `group_discussion`、leader/robot prompt、parser、原始 response 和 hash。`OUTPUT_CONTRACT_VERSION` 是 adapter-side wire contract，只在原始输出解析之后验证 robot/task identity、结构和可表达性，不把 HeteroCoord evaluator 标签或外部成功事实塞回原始 prompt。

当前 adapter 没有把外部 `ExecutionFeedback` 注入原生 stage-one discussion，也没有把 adapter 的 replan 伪装成原生 EMOS feedback hook。完整 Habitat-MAS/CrabAgent、原生低层动作、同构场景和论文 success detector 尚未迁移时，对应 D2、D4、D5、物理成功和论文统计必须 `N/A`。

允许在 parser 之后增加 schema validation、ID/能力检查、原始输出保存和外部 evaluator；禁止用 evaluator 标签改写 prompt，禁止把 parser 无法识别的任务静默填到第一个可用机器人。

## 5. 当前适配结论

“可通过 adapter 调用”与“已完成全部 Benchmark 验收”是两个状态。当前六类算法均有可审计的 adapter 入口或原生接入边界，但尚不能声称六类算法都已经完成可比较的全量 Benchmark：

| 算法 | 当前可验证结论 | 仍需 `N/A` 或待验收的范围 |
|---|---|---|
| FMC-ATA | 原生静态分配和宿主日程消费可审计 | feedback reallocation、coalition/fractional 执行、物理/全局最优性 |
| BT capabilities | 原生 auction/BT 状态及已注册 feedback leaf 可审计 | 完整 MissionControl、复杂 coalition、论文全条件、物理指标泛化 |
| SADCHER | 原生 scheduler/checkpoint、DAG 多任务和完整 coalition 输出可审计 | 当前统一契约不能表达同任务 coalition 的执行验收 |
| HARL:HAPPO | 严格 source/checkpoint/observation/action 绑定可审计 | 当前 EAI 任务的官方同构权重、物理任务和论文分数 |
| TeamWeaver | 原生语义分解/MIQP 核心及语法补丁边界可审计 | 多轮持久反馈、历史重建、coalition、论文同构场景 |
| EMOS | 原生 discussion/parser/output seam 可审计 | Habitat-MAS/CrabAgent stage-two、外部反馈原生性、物理和论文完整统计 |

## 6. 下一阶段只允许推进的工作

### 6.1 可以直接推进

1. 给 SADCHER 的 scope rejection 增加结构化 `N/A_reason` 和完整 native coalition receipt，确保报告区分“原算法有能力”和“统一契约不可表达”。
2. 给所有 adapter 统一保存 native output、adapter mapping、fallback、scope rejection 和 evaluator evidence 的 provenance。
3. 为 HARL 增加 checkpoint/observation/action contract 的逐字段审计；没有完整匹配时保持 `N/A`。
4. 对 TeamWeaver 和 EMOS 的 parser/output 约束做输入 hash、输出 hash、fallback 和扩展边界审计，不改原 solver/prompt 语义。
5. 对 FMC-ATA 仅在注册了原生通信/动态到达/fraction 输入时扩展 fixture，不添加宿主重分配。

### 6.2 当前禁止推进

- 把单机器人 `Assignment` 扩写成 coalition 而不更新统一契约、executor 和 evaluator 注册；
- 为缺少能力的算法加入默认 coalition、argmax、任务修复、自动重试或隐式 replan；
- 把 TeamWeaver/EMOS 的 adapter feedback extension 报成原生反馈；
- 把 HARL 的宏动作映射报成 HAPPO 原生任务分配；
- 把 queue empty、BT `SUCCEEDED`、进程退出或有限 tick 作为物理任务成功；
- 用 `0` 代替没有定义或没有证据的指标。

本报告的结论是下一轮代码变更的准入条件：只有“原算法已有能力 + Benchmark 契约可表达 + 外部 evaluator 可验收”三项同时成立，才进入下一段适配；缺一项就保留原生输出并记录 `N/A` 或 `not_evaluable`。
