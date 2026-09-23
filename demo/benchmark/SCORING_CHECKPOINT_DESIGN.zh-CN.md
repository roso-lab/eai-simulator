# Goal 主线评分适配的 Checkpoint 设计（阶段草案）

优先级更新：先实现[部署可行性确认主线P0](DEPLOYMENT_ASSESSMENT_P0.zh-CN.md)，
再实现本合同的恢复/并行分支。新增`initial_assessment_pre`/`reassessment_pre`
保存点必须包含门禁状态、评估轮次、团队/能力版本、通知队列和声明前成本；
动态对照仍使用变化生效前的`treatment_pre`，求助终止不等于业务完成。

剩余可行性oracle修订：正式标签仅`deployable|not_deployable`，另以
`oracle_status=verified|failed`归档平台证据质量。`initial_assessment_pre`和
`reassessment_pre`须保留预注册测量点ID、oracle模型/注册哈希、可信当前谓词与资源状态、
状态量测依据及缓存二元真值；在参与者声明前完成oracle，恢复时不得按参与者结果重算
或选择剔除。失败点按事先规则使该episode退出部署判断指标并单列基础设施缺口。
所有要求评估的轮次及oracle结果均保存，包含未作答即被下一外生变化替代的轮次。
正式发布前每个预注册实例/处理点必须有verified证据；通用有限状态搜索通过不能替代
五场景原生谓词映射及模型完备性验证。变化后oracle查询不能替代变化前`treatment_pre`。

本文件只规定把 Goal 自分解主线的原始回合证据接到后续评分实验所需
checkpoint 的设计边界，不改变公开题面、题包哈希、`scoreable` 闸门或
评分协议原文。Goal 主线继续由被测系统自行分解任务、选择团队、安排时序，
并通过公开里程碑和环境结果评价。协议 3.1 的固定子任务 `VAR/FSR/IRR`
不能直接套用到该主线；若要纳入统一评分，须先修订协议并定义与自分解
里程碑相容的分母和外部校验规则。

截至本阶段，runner 的 `runtime_state` 是符号/业务状态快照，不能称为
仿真 checkpoint。当前尚未实现可恢复的原生物理状态、物理引擎内部状态、
随机流计数器和分支恢复；因此任何记录只能标记为
`unverified_for_protocol_replay`，不得生成 fact/reference/sham/intervention
因果结果，也不得把通信回放账本或计划窗口时钟改名为物理 checkpoint。

## 1. Checkpoint 的最小语义

一个可用于协议配对的 checkpoint 必须在预注册触发点冻结同一历史节点，能够
独立恢复出四类状态，并验证恢复后版本和实体键一致：

| 状态域 | 必须保存的内容 | 当前 Goal 轨证据 | 验收要求 |
| --- | --- | --- | --- |
| 环境/物理 | 场景、物体、机器人和工具的完整状态；接触、约束、任务相关服务；物理引擎内部状态 | 仅有符号 `robot_regions/object_states`；无 PhysX 状态 | restore 后原生状态哈希或逐字段容差一致；接触/约束不丢失 |
| 被测系统可恢复状态 | 模型/适配器可写记忆、计划、未决请求、缓存、内部随机状态及版本 | participant 事件和计划日志可归档；无通用恢复接口 | 能在不重建首轮的情况下恢复同一 participant 状态；清空条件可证明 |
| 系统可见历史 | 已送达观察、任务确认、里程碑、消息及其有效期；按实体和事件键排序 | GoalSession 公开事件/动作日志 | fact/reference/sham 从同一可见历史起点恢复，禁止隐式补发 |
| 随机与时钟 | 仿真 RNG、环境/通信/算法随机流计数器；实际物理时钟和决策时钟 | host 收据时钟；`CommunicationRuntime` 独立窗口账本 | 分支使用同一外生随机流带；墙钟、仿真秒、决策步换算冻结 |

每个 checkpoint 还应绑定 `task_id`、任务合同版本、场景版本、代码/资产版本、
实体键集合、条件和随机重复 `s`、触发事件键、时间戳精度及状态摘要哈希。
摘要哈希只能用于一致性检查，不能替代可恢复内容。

## 2. 预注册触发点与分支用途

触发点必须在查看结果前冻结。每个点生成一个稳定 `checkpoint_id`，并记录
触发前最后一个外部有效事件和可评价时间轴。

| 触发类别 | 何时保存 | 可支持的比较/诊断 |
| --- | --- | --- |
| `initial` | episode 开始、首个 participant 输入前 | 经验学习共同首轮主干、完整重复回合 |
| `decision_pre` | 首次需要作出分配/时序/通信决策前 | 公开里程碑主线的决策前配对；不得注入隐藏子任务答案 |
| `treatment_pre` | 预注册通信降级、成员加入/退出、能力失效、需求变化等处理生效前 | change/no-change、join/no-join、fault/healthy twin；必须在处理及其通知发生前保存 |
| `replan_pre` | 输入交付后、参与者下一次决策调用之前 | 新计划与 hold-plan reference；不得用变化后的快照构造 change/no-change |
| `milestone_change` | 公开里程碑谓词首次改变、服务状态改变或新增需求生效时 | 绑定 `Q(t)`、受影响里程碑集、恢复和成本窗口 |
| `episode_round` | 经验学习每轮开始和结束 | 学习条件 R 与无经验基线 C 的同轮配对；轮次索引 `n` 必须独立 |

一个点若没有完整状态域或无法复现其事件历史，只能归档为
`raw_observation_checkpoint`，不得进入严格分支分析。触发后发生的变化必须
在事实分支、参考分支、sham 和干预分支中分别写入事件日志；不得以恢复后
重新生成的时间或计划顺序推断原始触发时刻。

失败诊断使用预注册规则选取失败前最近的合法 `decision_pre`；不能在看过结果后
重建有利快照。`milestone_change` 是测量边界，不自动具备处理前分支资格。
里程碑不能满足时不得等到成功才保存；保存策略与算法行为、终局结果无关。

宿主还须保存 GoalSession、调度器、技能进度、未决动作、通信队列、字节预算、
投递/消费游标、工具订阅、相机请求以及可重建渲染资源的描述。恢复不能清空队列
后当作“通信改善”。异步模型调用或不可序列化控制器存在时，需要明确的暂停
边界；不能暂停/恢复的状态域应标不可恢复并拒绝严格分支比较。

## 3. Goal 自分解主线的里程碑绑定

### 按指标预注册保存点

| 能力与目标量 | 必需保存点 | 配对与验收 |
| --- | --- | --- |
| 分配调度：里程碑覆盖、分解/分配/排程可执行证据 | 首次决策前；后续可观测计划修订前 | 保持目标和环境约束一致；不与唯一作者DAG做匹配；旧固定任务分母指标不计算 |
| 通信适应：降级保持率、吞吐、消费陈旧度 | 链路处理或首个受处理消息进入队列前 | 正常/降级从同一队列、游标和预算分支；环境随机流相同，通信随机流可配对；选用工具与否由模型决定 |
| 耦合协调：交接、服务连续、同步/互斥占用 | 环境耦合条件生效前的合法决策边界 | 对照改变真实环境机制，不能给模型下发执行顺序；同步指标要求真实并行开始/结束与占用事件 |
| 动态重构—规模响应 | 需求增长或成员加入及其公开通知前 | change/no-change或join/no-join，处理时冻结新增/受影响里程碑，不用变化后总工作量直接横比 |
| 动态重构—失效恢复 | 故障或能力损失及其公开通知前 | fault/healthy twin；trigger→response→resume按实际时间，未恢复为删失，不记成在H恢复 |
| 经验学习 | 共同首轮初始；每轮环境回档前及首轮完成后 | R/C共享首轮历史；环境恢复与经验保留分离；未覆盖外部记忆和随机性时不声称严格学习效应 |
| 成本效率 | 所有事实分支起点；重规划决策前 | 启动/通信/指令/重规划成本与Q(t)同钟；hold-plan参考只诊断重规划收益；分支开销不回填事实 |
| 失败诊断 | 失败前最近的预注册合法决策点 | sham先验复现；每个单干预/组合独立恢复；不得事后挑选最容易挽救的点 |

以上是测量触发合同，不是参与者任务时序。具体实例还需冻结触发的事件键、
保存频率/保留窗口、H、里程碑权重、恢复容差、处理允许修改的字段和统计单位。
同一保存点可支撑多种指标，但一个对照不能同时改变非目标因素。

Goal 不向系统提供固定子任务全集。任务合同应冻结公开目标、成功谓词、公开
里程碑集合及每个里程碑的外部验证条件。被测系统可以自行产生子任务、联盟
和时序安排；评测器只记录可观察的计划版本、动作、确认、服务状态和里程碑
进展。至少保存：

- `milestone_id`、版本、权重和谓词稳定时长；
- `milestone_state_before/after`、发生时间、来源实体和事件键；
- `Q(t)` 的外部采样或事件驱动更新、固定评测时限 `H`；
- 自分解计划摘要（任务/联盟/顺序/资源预约的规范化哈希），不把隐藏计划
  当作统一答案；
- 每项任务级动作的启动、终止、状态和外部结果；无效、撤销和重复动作标记；
- 公开业务结果、终止类别以及可行性/不可行声明的外部证据。

固定子任务指标在该主线上标记 `not_applicable_protocol_mismatch`，而不是
把 Goal 的里程碑数量伪装成固定任务分母。未来若协议修订，应另定义自分解
分母、机会覆盖和不可行判断的外部校验，再单独注册生产器。

## 4. 分支隔离和随机流

分支命名只使用协议规定的 `fact`、`reference`、`sham`、`intervention`。
每个分支从同一 `checkpoint_id` 独立恢复，禁止串联上一个探针分支的状态。

机器可读记录至少包括：

```json
{
  "checkpoint_id": "...",
  "branch_id": "fact|reference|sham|intervention",
  "parent_checkpoint_id": "...",
  "base_instance_id": "...",
  "condition_id": "...",
  "pair_repeat": "s01",
  "trigger_event_key": "...",
  "state_domains": {
    "environment_physics": "verified|missing",
    "participant_recoverable": "verified|missing",
    "visible_history": "verified|missing",
    "rng_stream": "verified|missing"
  },
  "clock": {"sim_time_s": null, "decision_step": null, "resolution_s": null},
  "restore_status": "verified|unverified|mismatch",
  "replay_status": "verified|replay_mismatch|stochastic_replay_unverified"
}
```

`branch_id` 为空或 `single_episode_no_protocol_branch` 表示当前 Goal 原始
归档，不能推断事实/参考关系。`reference` 的处理必须在任务定义允许的
情况下保持结果语义一致；工作集改变时只比较合同定义的新增工作、受影响
里程碑或积压，不比较未经归一化的总完成量。

## 5. 时间、成本和删失

所有事件同时保存绝对仿真时间、决策步（若有）、事件类型、实体键和来源。
没有真实仿真时钟时，host 收据时间只能作为 `host_receipt_clock` 诊断；不能
用于 PGI/AoI、物理恢复或并发声明。每个 episode 冻结 `H`，并保存：

- `t_trigger`、`t_valid_response`、可选 `t_confirm`、`t_resume`；
- `t_term`、`T_exec`、`Q_final`、固定时限 `Q(H)` 和终止类别；
- 任务级消息的 offered/link bit、接收者数、排队/送达/丢弃事件；
- 启动/预约/激活、交互、指令、重分配和重规划的原始计数；
- 里程碑进展曲线，用于后续 PGI/nPGI（仅在真实执行窗口定义域内）；
- 事件是否在 `H` 内发生及右删失时刻，不能把 `H` 填成真实事件时间。

不存在分母、参考低于预注册有效性门槛、零执行时间、无新增需求、无受影响
里程碑或任务语义不适用时，写入明确的 `N/A` 原因（如
`undefined_zero_progress`、`reference_below_threshold`、
`not_applicable_protocol_mismatch`）。探针分支和 sham 的成本单独保存，不能
回填事实分支成本或核心结果。

## 6. 原生 checkpoint 验收门禁

进入 Goal 主线的同快照评分实验前，必须通过独立、可重复的以下门禁：

1. **保存/恢复一致性**：从每类触发点恢复，环境物理状态、participant 状态、
   可见历史和 RNG 流均在预注册容差内一致；
2. **sham 回放**：同一快照的 sham 能复现事实失败/成功和进展容差；否则记
   `replay_mismatch`，停止严格归因；
3. **分支隔离**：fact/reference/sham/intervention 独立启动，分支写入互不
   污染，随机流按事件键或计数器保持配对；
4. **时钟真实性**：存在可审计的原生仿真时间和并发事件源，墙钟或计划窗口
   不能替代；
5. **经验清空**：R/C 两条件能证明评测期间可写记忆、缓存、案例库和反思日志
   的保留/清除差异，且首轮历史只执行一次；
6. **失败关闭**：任一恢复或校验失败时只写 raw-only 证据和原因，保持
   `execution_validated=false`、`business_outcomes_validated=false`、
   `scoreable=false`。

在上述门禁全部通过、并完成任务合同、外部结果校验和评分参数冻结前，
Goal sidecar 仅用于审计完整性；不得发布六维分数或把当前 Track-G 结果
宣称为协议正式 benchmark。
