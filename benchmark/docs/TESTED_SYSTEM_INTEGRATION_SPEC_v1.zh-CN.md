# 被测系统接入规范 v1（算法无关）

本规范定义 benchmark 与**任何**被测系统（tested system）之间的统一接入合同。合同所有权归
benchmark：统一输入输出类型定义在 `task/contracts.py`，接入协议定义在
`benchmark/adapters.py`（`DecisionProvider` 等 Protocol）。每个被测算法通过自己的
适配器（adapter）对齐这份合同后接通；算法特有的行为只写在各自的适配器附录里，
不得修改本规范定义的接口语义。当前已知被测系统：EMOS（零号，已接通，见
[EMOS 适配器附录](EMOS_BENCHMARK_INTEGRATION_SPEC_v1.zh-CN.md)）、TeamWeaver
（已通过 `benchmark/teamweaver.py` 合同适配器接通，见文末接入状态）。

## 1. 统一合同（权威来源=代码）

| 类型 | 定义处 | 含义 |
|---|---|---|
| `EaiTaskPackage` | `task/contracts.py` | 场景无关任务包：`task_id`、`scene_id`、任务图 `graph`、机器人名册 `robots`、`observation_schema`（当前 `task.observation.v1`）、`metadata`。支持 `from_json` 加载。 |
| `RobotState` | `task/contracts.py` | 对被测系统公开的机器人状态：能力 `capability`、可选 `position`（三元组）、`metadata`。 |
| `AlgorithmDecision` | `task/contracts.py` | 被测系统的统一输出：`condition`（条件分配状态）、`assignments`（任务指派）、`unassigned_tasks`、`provenance`。**不含任何物理成功声明**。 |
| `DecisionProvider` | `benchmark/adapters.py` | 接入协议：`(task: EaiTaskPackage, robots) -> AlgorithmDecision`，注释明示"must not claim physical success"。 |

物理执行、技能规划、里程碑、oracle 判定、回放与终局收据全部属于 benchmark/EAI
侧；被测系统只做决策。`Assignment`、`TaskGraph`、`EventLedger`、`RobotCapability`
等基础类型来自 `algorithm/common_coordination/contract.py`。

## 2. 生命周期与调用顺序

1. **初始化** `initialize(H_r, o_r)`：`H_r` 为 evaluator 提供的历史重建映射，`o_r`
   为当前公开观测映射（至少含 `robot_positions`，且键集必须与绑定名册的 robot IDs
   完全一致）。初始化受 `initialization_budget_calls` 计量，默认 1；预算耗尽
   fail-closed。初始化完成前不得发起分配。
2. **决策调用**：宿主按协议 `(task, robots)` 调用适配器，回收 typed
   `AlgorithmDecision`。事件驱动模式下为每回合 `act(events)`；被测系统在 act 中
   只允许发起相机工具调用与 `consume_message`，低层控制请求直接被拒。
3. **收尾**：会话关闭、收据定稿均由宿主完成；被测系统没有终局写入权。

## 3. fail-closed 边界

- 决策输出不得写入物理成功事实；oracle 与义务判定只消费 evaluator 自己的账本。
- 被测系统抛异常、返回畸形结构、或（若使用外部模型端点）模型返回非 mapping——
  一律 fail-closed 拒绝，不降级、不重试。
- 锁服务（typed-lock grant/deny）、占用账本、占用谓词归 benchmark evaluator；
  被测系统不直接取得或解释锁语义，其分配输出也不豁免 oracle 的占用检查。

## 4. 计量

- 初始化调用次数受预算计量（进配置收据）。
- 外部模型端点调用按次计量；端点异常即 fail-closed。
- 观测字节进通信账本（`CommunicationRuntime` 计量、`BranchLedger` 记账、
  `ObservationArtifactStore` 存证），计入 D2 通信成本；被拒请求零痕迹。

## 5. 观测服务（on-demand camera，可选 opt-in）

被测系统通过**宿主持有的可选观测伴侣**获取机器人相机画面；观测是计量服务，
不是免费感知。

- 钩子 `initialize_camera_tools(profile)`：在 `initialize(H_r, o_r)` 之后、首个
  `act(events)` 之前由宿主调用；未实现该钩子视为未 opt-in——零帧、零流量、相机
  调用被拒（`camera tool interface was not connected`）。
- 可选钩子 `update_camera_tools(delta)` 接收成员加入增量；无该钩子的被测系统仍会
  收到 `observation(kind=camera_tools_profile_delta)` 公开事件。
- 请求与回执：`SkillRequest(camera_ref, mode=capture, deadline, resolution,
  quality)`；`accepted ≠ delivered`，每请求恰一终态（delivered/timeout/cancelled/
  failed/inactive_robot/episode_ended）；拍摄占用 5 仿真拍（`capture_cost_ticks`）；
  quality 仅 encoding 白名单+max_bytes，deadline 严格大于当前且不超仿真预算；
  已计入字节不回滚，终态后迟到帧抑制。
- 回执只含 sha256 摘要、元数据与内容寻址 artifact 身份，绝不含 payload 或本地
  路径；隐藏候选 ID 不可枚举，唯一激活路径=宿主成员加入事件按会话序号幂等激活
  （capability_version 严格递增）。
- 消费收据须显式 `consume_message`；收据不构成决策依赖的证明。
- 宿主实现：`benchmark.observation_episode.ObservationEpisodeDriver`；执行边界
  `algorithm.skills.observation.runtime.CameraObservationRuntime`。本期不做：M2
  连续订阅、发现类锚点、历史时刻拍摄。

## 6. 序列化与提示词规范

- **权威是类型，不是文档**：序列化以 `task/contracts.py` 的 dataclass 字段为准。
- 任务包 JSON：`EaiTaskPackage.from_json`；现成样例 `task/config/fire_rescue.json`
  （fire-rescue 场景；其他场景任务包格式相同）。
- `H_r`/`o_r` 为 mapping：`o_r` 至少含 `robot_positions`（robot_id → 三元组）；
  `H_r` 的键集由 evaluator 的历史重建输出定义，附录可给各算法视角的样例。
- **LLM 类被测系统的提示词模板**：统一 envelope 为
  `{task_package(序列化), H_r(序列化), o_r(序列化), output_format(=AlgorithmDecision
  的 JSON 形态)}`；具体措辞模板由各算法附录给出实例，本规范只锁 envelope 与输出
  格式，保证可比较性。

## 7. 接入状态与清单

| 被测系统 | 状态 |
|---|---|
| EMOS | 零号，已接通（`benchmark/emos.py`，见适配器附录） |
| TeamWeaver | **已接通**（`benchmark/teamweaver.py`；适配器消费 `EaiTaskPackage`，输出 typed `AlgorithmDecision`，不声明物理成功；算法本体 `algorithm/system/coordination/TeamWeaver/` 未修改）。无依赖边、非空历史的合同输入按适配器规范明确 fail-closed，待裁决项见下。 |

### TeamWeaver 待用户裁决

以下是适配器明确暴露的算法能力边界，集成过程不自行变通：

1. 非空历史 `H_r`：当前 `initialize(H_r, o_r)` 无法将历史重建为
   `FactoryTaskSpec`/allocator 原生状态，因此恢复分支拒绝并留证；需确认 TeamWeaver
   本体是否提供历史消费/重建语义。
2. 任务图依赖边 `prerequisites`：`FactoryTaskSpec` 没有对应字段；含依赖边的任务包拒绝，
   不自行实现调度映射，需由用户/算法方裁决。当前 fire-rescue 与 room_06 全链任务包
   不含依赖边，不阻挡已登记案例。

新算法接入清单：

1. 实现决策入口（`DecisionProvider` 形态）；
2. （可选）实现 `initialize(H_r, o_r)` 与预算计量；
3. （可选）实现观测钩子以 opt-in 相机观测；
4. 不写物理成功字段、不解释锁语义；
5. 提供适配器级测试（正例 + fail-closed 反例），随附录文档登记。
