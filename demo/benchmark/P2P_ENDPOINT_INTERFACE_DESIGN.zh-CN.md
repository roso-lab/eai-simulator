# P2：p2p participant service 接口设计（P2P-IFACE-DESIGN）

状态：**设计草案，提交评审冻结；未实现 participant service**
日期：2026-09-24
输入基线：`codex/benchmark-pose-telemetry`
上游依据：`COMMUNICATION_STAGE22_DESIGN.zh-CN.md`（第 89-99 行五个未拍板问题）、
`STAGE31_PRIORITY_ORACLE_AND_SCORING.zh-CN.md`（"并行和串行安排"第 4 条）

本文件是 `evidence_class=contract` 的开发轨道设计输出。按
`development_track_marker.py` 的纪律（`EVIDENCE_CLASSES` 含 `contract`，
`_RELEASE` 恒假，`build_development_marker`/`stamp_evidence_record`），
本文及其后续实现产出的所有记录保持 `scoreable=false`、
`execution_validated=false`、`business_outcomes_validated=false`、
`release_eligible=false`；基础设施缺口记为
`communication_formal_episode_pending`（`communication_capacity_experiments.py`
的 `COMMUNICATION_FORMAL_BLOCKER`），不进入能力分母。

本文只做接口设计，不实现代码，不改任何冻结契约。

## 0. 决策摘要

| # | 问题 | 推荐答案 |
| --- | --- | --- |
| 1 | endpoint identity | 采用 `cloud` / `edge:<edge_id>` / `robot:<agent_id>`，episode 注册时冻结 |
| 2 | companion contract v1 vs Goal v4 action | companion contract v1（独立可选小协议），不改 Goal action contract |
| 3 | edge 是否跨 p2p | v1 不允许；p2p 池仅限 robot↔robot，edge 间走 backhaul |
| 4 | consume 语义 | 送达 observation 即进 AoI delivery 口径；显式 consume 只影响 consume 口径与派遣证据资格；唯一 consume 动词是既有冻结的 `consume_message` |
| 5 | 可信时钟组件 | 以 `GoalSession` 可信仿真时钟为唯一时间权威；物理 wall clock、通知真实送达延迟、规划计算耗时保持 `null` |

## 1. 范围与不重叠的边界

- 对象：goal 主轨（`goal_algorithm_adapter.py` → `GoalSession`）上，
  云—边—端三端点之间的参与者侧 publish/consume 接口（STAGE22 的 E2）。
- 沿用 STAGE22 冻结约束：不动冻结题包、`reach_m`、Goal v2/v3 action/event
  词汇。v2 基础词汇为 6 动作（`_ACTION_FIELDS`：`select_team`/`submit_plan`/
  `revise_plan`/`dispatch_skill`/`cancel_skill`/`consume_message`）与 8 事件
  （`_EVENT_FIELDS`：`episode_started`/`observation`/`member_join`/
  `member_exit`/`capability_change`/`communication_status`/`action_result`/
  `tick`）；v3 部署契约另加 `deployment_assessment` 动作与
  `_DEPLOYMENT_EVENT_FIELDS` 的 4 个部署事件。均见
  `goal_driven_contract.py`。
- 低层控制黑名单沿用 `goal_algorithm_adapter.py` `_LOW_LEVEL_ACTION_TYPES`：
  通信工具不是绕过高层动作约束的侧门。
- 进程内部讨论不计量；只有经 `CommunicationRuntime` 的消息进三池账本
  （STAGE22 冻结约束）。

## 2. 五个未拍板接口问题的建议答案

### 2.1 问题一：endpoint identity 命名

**推荐**：采用 STAGE22 拓扑表的三层命名——云总脑 `cloud`、边脑
`edge:<edge_id>`、机器人身体 `robot:<agent_id>`，在 episode 注册时冻结；
参与者只能引用已注册端点，不能创建端点。

**理由**：

- STAGE22 拓扑表（`COMMUNICATION_STAGE22_DESIGN.zh-CN.md` 第 28-36 行）已经
  把层、端点模式、允许方向、计量池绑定成一张表；命名与池推导天然耦合，
  无前缀自由字符串无法判定层归属。
- `communication.py` 的 `access` 契约按端点字符串做单机接入上限
  （`_access_endpoints`/`_access_policy`），p2p 池取
  `sorted({producer_id, recipient_id})` 为两端 access 端点——带层前缀的字符串
  可直接作为 access 键，不需要第二套映射。
- `robot:<agent_id>` 与 `GoalSession` 的 agent 词汇一一对应
  （`candidate_robot_pool` 的 `id`，`goal_session.py` 构造器里的 `self._robots`）；
  成员资格跟随当前 roster（`member_join`/`member_exit` 更新 `_current`），
  复用既有生命周期事件，不新增身份机制。

**替代项**：沿用容量实验现用的无前缀拼写（`robot_1`/`edge_1`，
`communication_capacity_experiments.py` `run_delay_cell`/`run_loss_cell`）。
否决：那是 evaluator 侧负载脚本拼写，不是参与者契约；无层信息则方向推导
必须靠 channel 名猜测，违反 fail-closed 原则。离线容量实验不需要改造，
正式接线时由服务把注册端点映射为 access 键即可。

**影响面**：companion 服务校验层、access 契约键、episode 注册配置需新增
端点表（`cloud` 端点列表、`edges: {edge_id: [agent_id,...]}` 所属关系、
robot 清单）。edge 所属关系存放于正式 runner 的 `runtime_config`
（具体字段名 unknown，见 4.2）。

### 2.2 问题二：companion contract v1 还是 Goal v4 action

**推荐**：可选 companion contract v1（独立小协议），发布为工具调用；
不注册 Goal v4 action/event。

**理由**：

- 冻结纪律：`goal_driven_contract.py` 的 `GOAL_ACTION_SCHEMA`/
  `GOAL_ACTION_VERSION`（`eai.benchmark.goal_action` v1）与 v2/v3 词汇是
  已发布契约；STAGE22 冻结约束明令不修改 Goal v2/v3 action/event 词汇；
  STAGE31 "并行和串行安排"第 4 条要求 P2 不得与未冻结接口同时改 Goal
  action contract。
- 先例已存在两次且模式成熟：pose（`goal_pose_tools.py`
  `POSE_TOOL_SCHEMA = "eai.benchmark.goal_pose_tool_call"`）与 camera
  （`eai.benchmark.goal_camera_tool_call`）都是 goal 动作旁边的可选工具调用，
  由 `GoalAlgorithmHost.request_actions` 统一分发：call_id 与 action_id 及其他
  工具 call_id 互斥校验、结果下一回合以事件投递、失败关闭桥接
  （`goal_algorithm_adapter.py` 第 277-358 行）。p2p 服务完整复用该路径。
- consume 已是冻结 goal 动作（`consume_message`），且 host 已有针对
  pose/camera 遥测的消费所有权挂钩（`goal_algorithm_adapter.py` 第 322-351 行：
  `owns_message`/`can_consume`/`telemetry.consume`）。引入 v4 动作集会分叉
  这条唯一路径。
- 发布隔离：companion 是 opt-in 能力，经 profile 显式宣告；不接入的旧
  v2/v3 参与者行为零变化，满足"不静默升级旧参与者"。

**替代项**：注册 Goal v4 action/event（STAGE22 第 64-67 行也承认此路径）。
推迟：只有当评分侧要求把通信动作计入 action ledger 本身时才有必要；
届时必须新建 contract revision 并提供旧版适配器，成本与风险远超收益。

**影响面**：`GoalAlgorithmHost` 新增构造参数与分发分支（镜像 pose/camera）；
`goal_driven_contract.py`/`goal_session.py` 词汇零改动。

### 2.3 问题三：edge 是否允许跨 edge p2p

**推荐**：v1 不允许。p2p 池仅限 `robot:*` ↔ `robot:*`；edge 相关流量一律
backhaul（edge→cloud 上行、cloud→edge 下行）；edge→其他 edge 的转发按
STAGE22 边脑行"按契约可向其他边脑转发"理解为由云中转的 backhaul 两段，
不是 p2p 直连。

**理由**：

- STAGE22 拓扑表本身如此：机器人行才允许 p2p（relay/handoff/negotiation），
  边脑行计量池是 "backhaul 两池"。
- 容量证据只覆盖 robot↔robot p2p：`communication_capacity_experiments.py`
  `run_loss_cell` 只有 `robot_1`→`robot_2` 的 p2p 负载与 C90/C50 包围；
  edge↔edge 直连没有任何容量曲线，开放它等于让未测路径进正式计量。
- `communication.py` `_access_endpoints` 的 p2p 分支对任意端点对都成立，
  未来放开是加容量单元的增量变更，不是接口破坏性变更——推迟没有锁定成本。

**替代项**：v1 即开放 edge↔edge p2p。否决：见上，无容量证据；且 edge 间
直连会让"cloud 是唯一下发权威"的下行语义变模糊。

**影响面**：publish 校验新增 `endpoint_pair_forbidden` 错误码；池推导表
（4.3）保持三行；容量实验矩阵暂不扩展。

### 2.4 问题四：consume 确认语义

**推荐**：采纳 STAGE22 问题 4 的第二选项——消息送达并以 observation 投递
后即进入 AoI delivery 口径；参与者显式 consume 只影响 consume 口径与
派遣证据资格，不影响送达统计。**并且 v1 只保留一个 consume 动词：既有
冻结的 `consume_message` goal 动作**；STAGE22 草案里 companion 的
`consume` 操作不作为独立工具调用暴露（见第 6 节裁决 R2）。

**理由**：

- `communication.py` 默认 `aoi_sample_policy="delivery"`（`advance`/
  `consume` 中的采样分支）：delivery 口径天然存在，显式 consume 切换的是
  evaluator 侧统计配置，不是参与者义务。
- 证据门槛依赖显式消费：`goal_session.py` `_admit_dispatch` 要求
  `dispatch_skill` 的 `evidence_refs ⊆ consumed_message_ids`（`_consumed`
  集合，`submit_action` 的 `consume_message` 分支写入）。若消费只走
  companion 工具调用，消息永远进不了 `GoalSession._consumed`，无法作为
  派遣证据——这是"唯一 consume 动词"裁决的硬依据。
- 预算豁免先例：`goal_session.py` 第 517 行对 `cancel_skill`/`consume_message`
  豁免仿真预算检查——消费不应被预算惩罚；pose profile 亦声明
  `"consumption": "existing consume_message action; receipt is not proof of
  decision dependence"`（`goal_pose_tools.py` `public_profile`）。
- `consumer_id` 取被接受的 `consume_message` 动作的 `action_id`：先例是
  `goal_pose_telemetry.py` `GoalPoseTelemetry.consume` 以
  `consumer_id=action_id` 调 `CommunicationRuntime.consume`，并用
  `_actions` 映射做 action_id 重试幂等。

**替代项**：要求显式 consume 才计 AoI delivery。否决：惩罚未消费的接收方、
悄悄改变默认采样语义；且与"送达即证据、消费是决策侧事实"的分层矛盾。

**影响面**：`GoalAlgorithmHost` 的 consume 分支为 comm 遥测扩展
`owns_message`/`can_consume`/`consume` 三挂（镜像 pose/camera）；评分侧车
同时保留 delivery 与 consume 两个口径。

### 2.5 问题五：可信 host 时钟组件

**推荐**：v1 以 `GoalSession` 的可信仿真时钟为唯一时间权威。publish、
送达、消费、条件变更的全部时间戳绑定该时钟：companion 服务每次
`tick(now)` 用 session 当前时间驱动 `CommunicationRuntime.advance`；
`published_at_s` 恒等于提交时刻的 session 时间。物理 wall clock、通知真实
送达延迟、规划/重规划计算耗时三者在确认组件前保持 `null`。

**理由**：

- `GoalSession._clock` 拒绝时间倒退、`emit`/`update_runtime_state`/
  `complete_skill` 单调推进（`goal_session.py` 第 179-183 行）；host 在
  仿真循环上串行调用（模块 docstring 与 `goal_algorithm_adapter.py`
  docstring）。这是现成、可信、可回放的时间源。
- pose 先例明确"caller owns the trusted simulation clock"
  （`goal_pose_tools.py` 模块 docstring 第 1 行）；`GoalPoseTelemetry.publish`
  强制 `sampled_at_s == runtime.time_s`。
- STAGE22 第 74-75、79-83 行：通知送达时刻没有真实来源时保持 `null`，
  不得从 wall clock 或模型日志推断。ALGORITHM_HANDOFF 亦要求同步后端依据
  同一物理时钟返回不早于当前可信时间的完成时间。
- 规划计算计时当前没有任何可信组件提供（unknown）：`GoalSession` 只记
  `submitted_at_s`/`dispatched_at_s` 等账本时刻，不测思考耗时。保持 `null`
  是唯一不造假的选择。

**替代项**：新建 wall-clock/计时可信组件。推迟：那是一个独立可信服务
（自带 provenance），不属于本接口 v1；若未来引入，也只填充现在为 `null`
的字段，不改变本接口的时间语义。

**影响面**：四个时间字段（`published_at_s`/`delivered_at_s`/`consumed_at_s`/
条件变更时刻）全部落在仿真时钟上；评分侧车 null 字段清单不变。

## 3. 冻结接口草案：p2p participant service v1

### 3.1 能力声明与挂接方式

- 能力 schema：`eai.benchmark.goal_communication_tools`，`version: 1`，
  `optional: true`（沿用 STAGE22 第 43-48 行的 schema 名）。
- 工具调用 envelope schema：`eai.benchmark.goal_communication_tool_call`，
  `version: 1`（镜像 `POSE_TOOL_SCHEMA` 命名法）。
- 挂接：`GoalAlgorithmHost.__init__` 新增 `communication_tools` 参数；
  `request_actions` 新增同名 schema 的分发分支，完整镜像 pose 分支
  （`goal_algorithm_adapter.py` 第 277-294 行）：call_id 与 goal action_id、
  pose call_id、camera request_id 四方互斥校验；服务 `submit(...)` 返回的
  结果记录进入本批返回值；送达事件下一回合投递。
- adapter 侧可选挂钩：`initialize_communication_tools(profile)`（镜像
  `initialize_tools`/`initialize_camera_tools`）。未实现该挂钩的 adapter 不收到
  profile、不看到任何通信事件——这是"不静默升级"的机制保证。
- `public_profile()` 字段（镜像 `GoalPoseTools.public_profile`）：
  `schema/version/episode_id/public_task_sha256/optional`、
  `endpoints`（`cloud`、`edges` 及其所属 robot、robots）、`allowed_directions`
  （3.3 的池推导表）、`channels`（封闭词表）、`validity`（TTL 规则说明）、
  `call_schema/call_version/call_fields`、`consumption`（既有 `consume_message`
  动作；回执不是决策依赖证明）、`cost`（规范字节，无 token 计价）。
- profile 的 `public_task_sha256` 必须与 `participant_api` 一致
  （镜像 `goal_algorithm_adapter.py` 第 135-146 行的校验与
  `ValueError("... does not match public task")`）。
- 端点注册在 episode 启动时冻结为不可变表：`cloud`（唯一）、
  `edges: {edge_id: [agent_id, ...]}`、robots 并集。roster 动态性只影响
  "当前成员"校验（见 3.4 错误码），不改变注册表。

### 3.2 publish：完整签名

**请求**（参与者 → host，字段集封闭，多余/缺失字段即 `envelope_invalid`）：

```json
{
  "schema": "eai.benchmark.goal_communication_tool_call",
  "version": 1,
  "call_id": "<非空字符串，全局唯一>",
  "submitted_at_s": 12.5,
  "operation": "publish",
  "arguments": {
    "producer_id": "robot:r2",
    "recipients": ["robot:r7"],
    "channel": "relay",
    "estimate": {"...": "任意规范 JSON 值"},
    "valid_until_s": 15.0,
    "information_id": "可选字符串"
  }
}
```

约束：

- `submitted_at_s` 必须等于当前 session 时间（镜像 pose 的
  "submission must match current episode time"）。
- `recipients` 为**单元素列表**（v1 裁决 R4，见第 6 节）：一次 publish 恰好
  一个收件人；向多个端点扩散同一信息 = 多次调用共享 `information_id`。
  字段保持列表形状以便 v2 前向兼容。
- 参与者**不提供** `pool`、`direction`、`access_robot_id`、`message_id`：
  池与方向由 host 按端点对推导（3.3）；`message_id` 由 host 指定为
  `f"{episode_id}:comm:{n}"` 单调序号（镜像 `GoalPoseTelemetry` 的
  `message_prefix` 模式，保证 `GoalSession` observation `message_id`
  唯一性检查可通过）。
- `estimate` 只能含有限 JSON 值（`communication.py` `_json_copy` 的约束）；
  线上字节数由运行时按五个公开字段的规范 JSON 计算（`communication.py`
  `publish` 第 590-596 行），声明式 size 无法绕过。
- `valid_until_s > submitted_at_s`；生效有效期取
  `min(请求值, published + pool ttl_s)`（`communication.py` 第 626-629 行）。
- 仿真预算结束后拒绝 publish（`budget_exhausted`），镜像
  `goal_pose_tools.py` 第 136-137 行的预算检查；`consume_message` 不受此限
  （`goal_session.py` 第 517 行豁免）。

**响应**（进入本批结果列表，参与者和 evaluator 可见）：

```json
{
  "accepted": true,
  "call_id": "...",
  "operation": "publish",
  "message_id": "<episode>:comm:17",
  "producer_id": "robot:r2",
  "recipients": ["robot:r7"],
  "channel": "relay",
  "direction": "p2p",
  "pool": "p2p",
  "size_bytes": 234,
  "submitted_at_s": 12.5,
  "valid_until_s": 14.0,
  "execution_validated": false
}
```

`accepted` 只表示**进入传输账本**，不是送达承诺；送达以后续 observation
事件为准（3.5）。响应中的 `direction`/`pool` 是 host 推导结果的显式回显，
让评分侧车的方向分解永不需要从 channel 名猜测。

**错误处理**：任何校验失败抛 `ValueError` 并使桥接 fail-closed
（完全沿用 pose `submit` 的 raise 路径与 `GoalAlgorithmHost` 第 353-358 行
的半批不重放语义）；同时在本服务调用账本里记录稳定错误码（镜像
`classify_plan_rejection` 的"稳定码+原始文本"先例）。冻结错误码：

| 错误码 | 触发 |
| --- | --- |
| `envelope_invalid` / `version_unsupported` | 字段集/类型/schema/版本不符 |
| `call_id_invalid_or_reused` / `call_id_collision` | 与本服务历史 call_id、goal action_id 或其他工具 id 冲突 |
| `submission_time_mismatch` | `submitted_at_s` ≠ 当前 session 时间 |
| `budget_exhausted` | 仿真预算已结束 |
| `operation_unknown` | `operation` 不在词表 |
| `producer_unknown` / `producer_not_current_member` | 生产端点未注册 / 已退出的成员端点 |
| `recipient_unknown` / `recipient_not_current_member` / `recipient_invalid` | 收件端点未注册 / 非当前成员 / 列表形状非法（非单元素、重复） |
| `endpoint_pair_forbidden` | 端点对不在 3.3 方向表内（含跨 edge p2p、edge 越辖下发、robot 越辖上报） |
| `channel_unknown` | channel 不在封闭词表 |
| `estimate_not_canonical_json` | 含非有限/非 JSON 值 |
| `validity_invalid` | `valid_until_s <= submitted_at_s` |
| `transport_internal` | `CommunicationRuntime` 意外失败；记为平台缺口，不算模型失败 |

### 3.3 池与方向推导表（host 侧，非参与者输入）

| producer | recipient | pool | direction |
| --- | --- | --- | --- |
| `robot:*` | `robot:*` | `p2p` | `p2p` |
| `robot:*` | 所属 `edge:<id>` 或 `cloud` | `backhaul_uplink` | `uplink` |
| `edge:*` | `cloud` | `backhaul_uplink` | `uplink` |
| `cloud` | `edge:*` 或 `robot:*` | `backhaul_downlink` | `downlink` |
| `edge:*` | 所辖 `robot:*` | `backhaul_downlink` | `downlink` |
| 其他一切组合 | — | `endpoint_pair_forbidden` | — |

推导结果以 `pool=`/`direction=` 显式传给 `CommunicationRuntime.publish`
（签名见 `communication.py` 第 565-567 行），`access_robot_id` 为 `None`，
access 约束由 `_access_endpoints` 按池自动生效（p2p 取两端、downlink 取
收端、其余取发端）。

**下行语义边界**：`dispatch_skill`/`cancel_skill` 的"派遣/取消下发"只有在
可信 host 生成规范消息并经 backhaul downlink 实际传输后才计为下行通信
（STAGE22 第 70-73 行：admission 记录与执行回执是两个事实，不得先执行再
补记字节）。v1 中这是 host 侧对同一 `CommunicationRuntime` 的复用，不需要
参与者接口变化；未接线时保持 blocker 记录。参与者不得把 `relay`/
`synchronize` 计划节点当作 p2p 字节证据（STAGE22 第 66-67 行）。

### 3.4 consume：复用冻结 goal 动作

**请求**就是既有 `eai.benchmark.goal_action` v1（不新增字段）：

```json
{
  "schema": "eai.benchmark.goal_action", "version": 1,
  "action_id": "consume-3", "submitted_at_s": 13.0,
  "type": "consume_message", "payload": {"message_id": "<episode>:comm:17"}
}
```

**host 侧流程**（镜像 `goal_algorithm_adapter.py` 第 322-351 行的 pose/camera
消费路径，四步）：

1. comm 遥测 `owns_message(message_id)` 为真时，要求该 id 已在送达可见集
   （`_comm_visible_ids`，镜像 `_pose_visible_ids`），否则
   "message has not been delivered to the algorithm"。
2. `can_consume(message_id, time_s=now)`：当前已送达且新鲜
   （`now < valid_until_s`），不预测未来传输状态（镜像
   `GoalPoseTelemetry.can_consume` 的不预测原则）。
3. `session.submit_action(action)` 正常走冻结校验；失败照旧抛出
   （`submit_plan`/`revise_plan` 才记 `record_action_rejection`，其余不变）。
4. 成功后 comm 遥测 `consume(message_id, action_id=..., time_s=now)`，内部
   调 `CommunicationRuntime.consume(recipient=<收件端点>,
   consumer_id=action_id)`；action_id 重试幂等（镜像
   `GoalPoseTelemetry.consume` 的 `_actions` 映射）。

对齐 `CommunicationRuntime.consume` 的拒绝原因（第 959-969 行），稳定错误码：
`consume_unknown_message`、`consume_not_delivered`（含 in-transit/queued/
dropped/expired 各状态）、`consume_stale`（过期）、`consume_already_consumed`。
消费成功使 message_id 进入 `GoalSession._consumed`，从下一回合起可作
`dispatch_skill` 的 `evidence_refs`。

### 3.5 事件侧：送达 / 过期 / 丢弃

- **送达**：host 每回合先 `pump_communication_tools()`（镜像
  `pump_pose_tools`/`pump_camera_tools`）：以 session 当前时间
  `advance` 传输运行时，取各端点新送达且未消费的公开载荷
  （`public_messages`，只含 `message_id/producer_id/published_at_s/
  valid_until_s/estimate`，不含队列/条件/丢包原因），逐一
  `session.emit("observation", now, payload)`。observation 载荷
  `kind="communication_message"`，`data = {"recipient_id": <端点>,
  "estimate": ..., "information_id": ...}`（`recipient_id` 是 host 侧投递
  视图，不改变线上字节；线上字节按 `communication.py` 五字段规范 JSON 算）。
  复用 `observation` 事件而不新增事件类型——先例：`pose_update`、
  `camera_frame`、`camera_tools_profile_delta` 都走 `kind` 扩展
  （`goal_algorithm_adapter.py` 第 246-265 行）。
- **成员退出后的在途投递**：pump 对已非当前成员的收件端点不再向参与者
  投递 observation（字节成本已在账本，不回滚）；镜像 camera 的
  `activate_joined_member` 与 pose 的 `register_public_robot` 生命周期处理。
- **过期/丢弃**：**不是参与者事件**。`communication.py` 模块 docstring 与
  `public_messages` 的边界（第 36-39 行）冻结了"隐藏条件、队列状态、未来
  调度不进入参与者视图"；STAGE22 第 59-61 行同样限定参与者只能看到已送达
  且未过期消息。参与者只能间接推断：observation 一直未到、
  `valid_until_s` 已过、之后对该 message_id 的 `consume_message` 失败于
  `consume_not_delivered`/`consume_stale`。丢弃分类（`active`、`ttl`、
  `buffer_overflow`，见 `_drop_delivery`/`_service_pool`/`advance` 的
  `dropped`/`expired` 账本行）与 AoI（事件样本 + 连续时间加权曲线，按
  `(recipient, information_id)` 轨道）只在 evaluator 报告里。
- **接收回执**：publish 响应即本地准入回执（镜像 pose 的
  `pose_tool_result` control 事件语义），不承诺送达、不诊断丢包。
- **evaluator 报告**：单一 replay-bound transport archive——每 episode 一个
  `CommunicationRuntime.report()` v2（`initial_contract`/`contract`/
  `ledger`/`aoi`/`pool_utilization`），pose/camera/backend 事件按来源标识合并在
  同一 archive（STAGE22 第 61-62 行）；回放用
  `validate_communication_report`（可绑定 `expected_initial_contract`）。
  条件干预（JP/BP 的延迟/丢包 treatment）继续走
  `set_conditions(..., pool=...)`，每次变更记 `channel_condition_changed`
  且只影响后续发布（`communication_capacity_experiments.py` 模块 docstring
  的既定纪律）。

### 3.6 字节计量进三池账本

不另造账本，全部复用 `communication.py` 机制：

- episode 契约启用 pool 模式：`pools = {backhaul_uplink, backhaul_downlink,
  p2p}` + `access`（按 robot 端点键的单机上限）。服务调用
  `publish(..., pool=推导值, direction=推导值, access_robot_id=None)`。
- 每条 `bytes_served`/`dropped`/`expired` 账本行携带 pool 与显式 direction
  （`_direction_fields` 把发布时的 direction 复制进后续行）；方向分解沿用
  `bytes_by_direction`：任何 served 行缺 direction 则方向分解 fail-closed
  为 `null`、`direction_complete=false`，绝不按 channel 名猜
  （`communication_capacity_experiments.py` 第 203-223 行）。
- 多副本广播 = 多次单收件人 publish（3.2 单收件人裁决），每次调用独立
  消耗链路预算，等价于 STAGE22 第 34-35 行"广播按每个收件人副本计量"；同 `information_id` 保证 AoI 按
  `(recipient, information_id)` 正确聚合（`_aoi_information_id`）。
- 工具调用请求本身的字节（call envelope 规范 JSON）记入服务自己的
  ingress 账本，`accounting_scope="model_to_benchmark_ingress_not_radio_transport"`
  （镜像 `goal_pose_tools.py` 第 100/150 行），**不进三池**；只有消息体经
  运行时服务的字节进三池。
- 通信证据缺失或 replay 失败不得当零成本（STAGE22 第 86-87 行）；池利用
  率与 C/C0 由 `pool_utilization`/`c0_bps` 既有字段提供。

### 3.7 fail-closed 规则（接口不可用时参与者看到什么）

1. **能力未宣告/服务未连接时提交通信工具调用 = 协议违规**：桥接立即
   fail-closed（镜像 "pose tool interface was not connected" 的
   `ValueError` 路径）。没有静默降级、没有空成功响应。
2. **任何校验失败即抛错**，半批不重放（`GoalAlgorithmHost` 第 353-358 行）；
   稳定错误码进 evaluator 报告，参与者从异常/桥接关闭得知失败。
3. **accepted ≠ delivered**：publish 响应显式不含送达时刻；送达只能来自
   后续 observation。没有伪造的 `delivered_at_s`。
4. **传输报告缺失/replay 不匹配**：按平台缺口记录（blocker），不按零
   成本、不进模型分母（`stamp_evidence_record` 纪律）。
5. **null 字段**：物理 wall clock、通知真实送达延迟、规划计算耗时在可信
   组件确认前恒 `null`，不得从 wall clock 或模型日志推断。
6. **发布标志**：本接口一切输出盖 `evidence_class=contract` 开发标记，
   `scoreable`/`execution_validated`/`business_outcomes_validated`/
   `release_eligible` 恒 false；正式 episode 接线前 blocker 保持
   `communication_formal_episode_pending`。

## 4. 实现阶段划分与验收

### 4.1 Phase 1：离线契约测试（无 Isaac/ROS/GPU/网络）

内容：端点表与方向推导表穷举测试（含全部 `endpoint_pair_forbidden` 组合）；
publish envelope 每个错误码各触发一次；单收件人约束与
`information_id` 聚合；host 指定 message_id 的唯一性（对
`GoalSession.emit("observation")` 的重复 id 检查）；consume 四步流程含
`consume_not_delivered`/`consume_stale`/`consume_already_consumed`；
三池字节账本 + access 双端约束 + TTL 截短的方向完整性
（`direction_complete=true`）；ingress 与三池分离；产出的 report 过
`validate_communication_report` 回放一致；能力未连接时提交工具调用的
fail-closed。

**验收条件**：

- 上述确定性测试全部通过，且只用纯 Python（不 import Isaac/ROS）。
- 错误码表 3.2/3.4 每一项至少被一个用例命中。
- 一个最小 golden 契约夹具（固定端点表 + 固定池契约 + 固定调用序列）
  两次运行产物逐字节一致，`validate_communication_report(...).validated
  == True`。
- `git status` 显示 `goal_driven_contract.py`/`goal_session.py` 词汇零改动。
- 全部测试产物盖 `evidence_class=contract` 标记，三个 release 标志为 false。

### 4.2 Phase 2：正式 episode 接线

内容：`GoalAlgorithmHost` 接入 `communication_tools` 参数、分发分支与
`pump_communication_tools`；端点表进入正式 runner 的 episode 注册配置
（具体字段名 unknown，建议 `runtime_config.communication_endpoints`，
含 `cloud`/`edges` 所属关系/robots）；observation `kind=
communication_message` 投递；consume 四步挂接；单一 replay archive 合并
pose/camera/comm 来源标识；时钟绑定（全部时间戳 = session 时钟）。

**验收条件**：

- 一个启用了 companion 的 goal episode 完整走通
  publish →（延迟/受限池下）送达 observation → `consume_message` →
  该 message_id 可作 `dispatch_skill` evidence_refs。
- 账本方向分解 `direction_complete=true`；`bytes_by_direction` 与池推导表
  逐条一致；ingress 字节不出现在三池。
- 未实现 `initialize_communication_tools` 的同一 adapter 在 companion 关闭
  与打开两种 episode 配置下行为不变（无新事件、无新动作要求）。
- episode 归档 report 回放验证通过；`development_track` 标记仍为
  contract；`communication_formal_episode_pending` 仅在四场景 JP/IID/BP、
  C90/C50 正式接线完成时才由容量侧解除（不在本接口内解除）。

### 4.3 Phase 3：旧 v2/v3 参与者回归

内容：现有 goal adapter 测试全量回归（含
`test_benchmark_goal_algorithm_adapter.py`，ALGORITHM_HANDOFF 验收清单第 2 项）；
`handoff_preflight` 四场景公开回合复跑；已接入的 TeamWeaver/EMOS 适配器
冒烟（不要求实现新挂钩）。

**验收条件**：

- 全部既有测试原样通过；diff 审计确认 `goal_driven_contract.py`、
  `goal_session.py`、题包与 participant API 文件零变更。
- 没有任何路径在 adapter 未显式 opt-in 时投递 `communication_message`
  observation 或要求新动作（不静默升级）。
- 旧参与者的 manifest 校验（`validate_goal_adapter_manifest`）不受影响；
  companion 能力不改变 `evaluation_track`/`input_scope`/`control_scope`
  任何必填值。

## 5. 不做什么

- **不改 Goal action contract**：`eai.benchmark.goal_action` v1 envelope、
  v2 6 动作/v3 部署词汇、goal event 词汇一概不动；不注册 v4。
- **不引入网络 API**：线下部署 + 本地边界是既定原则
  （`communication.py` 第 1 行 "Deterministic offline communication
  execution; no simulator integration"；ALGORITHM_HANDOFF 的外部进程隔离
  契约要求版本化 IPC/文件边界）。本接口不开 socket、不挂 ROS topic、
  不做任何真实网络传输。
- **不静默升级旧参与者**：能力 opt-in，未实现
  `initialize_communication_tools` 的 adapter 看不到 profile 与事件。
- **不伪造下行通信**：派遣/取消的下行字节只在可信 host 规范消息实际
  经 backhaul downlink 传输后记账；不先执行后补记。
- **不把计划节点当通信证据**：`relay`/`synchronize` 节点不是 p2p 字节。
- **不猜方向、不填 null**：缺 direction 的行方向分解为 `null`；wall clock
  类字段保持 `null`。
- **不把通信证据缺失当零成本**，不把基础设施失败当模型失败。
- **不在本设计内实现代码或抬升任何发布标志**。

## 6. 机制冲突裁决（自洽性声明）

- **R1：STAGE22 草案的 publish 字段含 `pool`/`direction` vs 端点可信原则。**
  STAGE22 YAML 草图把 `pool`、`direction` 列为 publish 字段，但同文又规定
  端点是 host 可信绑定、参与者只能选收件人。二者冲突。裁决：v1 把
  `pool`/`direction` 从参与者输入中移除，改为 host 按端点对推导，并只在
  响应与账本中回显。理由：允许自报池等于允许模型绕过容量记账
  （把上行说成 p2p）。这是对 STAGE22 草图的一处显式修正。
- **R2：companion `consume` 操作 vs 既有冻结 `consume_message` 动作。**
  两条消费路径会分叉出两份账（`GoalSession._consumed` vs
  `CommunicationRuntime` consumption），且派遣证据只认前者。裁决：v1 唯一
  consume 动词是 `consume_message` goal 动作；STAGE22 草图的 consume 字段
  （`message_id`/`consumer_id`/`submitted_at_s`）重新解释为服务内部调用
  `CommunicationRuntime.consume` 的参数（`consumer_id = action_id`，
  `submitted_at_s` 取自 goal envelope）。同为对 STAGE22 草图的显式修正。
- **R3：companion 事件 vs Goal 冻结事件词汇。** 不新增事件类型；送达消息
  复用 `observation` + `kind="communication_message"`（payload 字段集
  `{message_id, producer_id, kind, data, valid_until_s}` 已足够）。先例：
  pose/camera 的 kind 扩展。冻结词汇里已有 `communication_status`
  （`_EVENT_FIELDS`，字段 `{status, available_bytes}`）：v1 明确**不**用它承载
  送达/丢包——其字段形状面向池状态（`available_bytes`），投递它会违反
  STAGE22 "参与者不能看到池状态"的边界；该事件在本接口中保持不发射。
- **R4：多收件人 publish vs `GoalSession` observation message_id 唯一性。**
  `GoalSession.emit` 拒绝重复 observation `message_id`，而多收件人消息的
  各副本送达时刻不同，无法用单个事件表示。裁决：v1 publish 限定单收件人
  （`recipients` 为单元素列表），扩散 = 多调用共享 `information_id`；
  AoI 按 `(recipient, information_id)` 聚合因此不受损。v2 若要多收件人，
  需引入按收件人的视图 id 间接层，属接口修订。
- **R5：delivery 口径 AoI vs 显式消费证据。** 二者不合并：
  `aoi_sample_policy` 是 evaluator 配置（默认 `delivery`），显式消费只
  产生 consume 口径与派遣证据资格。两个指标并存，评分侧车分别归档。
- **R6：publish 受预算限制 vs consume 豁免。** 镜像既有不对称
  （`goal_session.py` 第 517 行豁免 `cancel_skill`/`consume_message`；
  pose 工具在预算后仅允许 `unsubscribe_pose`）：预算后 publish 拒绝、
  `consume_message` 始终允许。

## 7. 遗留 unknown 清单

- 正式 runner 配置中端点表/edge 所属关系的字段名与文件位置：unknown，
  Phase 2 定为 `runtime_config.communication_endpoints`（建议值）。
- 下行规范消息（派遣/取消）由哪个 host 组件生成：unknown；v1 只规定
  语义与账本路径，实现归属由正式 episode runner 交接文档定。
- 规划/重规划计算计时的可信组件：不存在；相关字段恒 `null`。
- 边缘越辖/跨 edge 组合是否在后续版本放开：v2 议题，需先补 edge↔edge
  p2p 容量单元（C90/C50 包围与 JP/BP 对照）。
