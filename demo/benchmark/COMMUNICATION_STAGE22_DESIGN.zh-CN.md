# 阶段 22：云—边—端通信接线设计（COMM-DESIGN）

状态：**设计完成，等待接口拍板；未实现 E2 participant service**  
日期：2026-09-24  
输入基线：`codex/benchmark-pose-telemetry`，`c622025`

## 目的与边界

通信底座已经支持 backhaul 上行/下行共享池、p2p 共享池、单机接入上限、规范
序列化字节、逐收件人传输、主动/缓冲/TTL 丢包、回放校验和连续 AoI。阶段 22
只定义如何把这些能力接到一个正式 Goal episode 的云—边—端事件面；本文件不是
正式 episode 已接线的证明，也不抬升任何发布标志。

以下约束冻结不变：

- 题面仍只公开总体目标和可验证里程碑；模型自行拆题、选人、排时序和重规划。
- 位置是可选工具。模型可以不接入、按需查询或订阅；不接入不能判错。
- 进程内部讨论不进入 benchmark 计量。只有经过 benchmark 链路的消息才记账。
- 不修改冻结题包、见证计划、`reach_m`、Goal v2/v3 action/event 词汇或评分协议原文。
- `scoreable=false`、`execution_validated=false`、`business_outcomes_validated=false`
  在整个设计和实现阶段均保持不变。

## 拓扑和可信端点

正式 episode 采用三层端点命名。端点是 evaluator/host 可信绑定，不由被测模型
自报：

| 层 | 端点模式 | 允许的发布方向 | 计量池 |
|---|---|---|---|
| 云总脑 | `cloud` | 向边脑/参与者下发计划、派遣、取消；接收遥测和观察 | `backhaul_downlink` / `backhaul_uplink` |
| 边脑 | `edge:<edge_id>` | 向云上报、向所辖机器人下发；按契约可向其他边脑转发 | backhaul 两池 |
| 机器人身体 | `robot:<robot_id>` | 向所属边脑/云上报；向另一机器人发送 relay/handoff/negotiation | backhaul 上行、p2p |

机器人到机器人消息必须显式给出 producer 和 recipient，并同时受 p2p 共享池及
两端 `access` 上限约束。广播按每个收件人副本计量。端点路由和 edge 所属关系在
episode 注册时冻结；模型只能选择允许的收件人，不能创建未注册端点。

## 推荐的兼容接口

推荐新增可选 companion contract，而不是把 `publish_message` 静默加入现有 Goal
v2/v3 action 集合：

```yaml
schema: eai.benchmark.goal_communication_tools
version: 1
optional: true
transport_report_schema: eai.benchmark.communication
transport_report_version: 2
operations:
  publish:
    fields: [call_id, submitted_at_s, producer_id, recipients, channel,
             pool, direction, information_id, estimate, valid_until_s]
  consume:
    fields: [message_id, consumer_id, submitted_at_s]
```

`GoalAlgorithmHost` 在初始化时按 opt-in 能力向参与者暴露此工具。工具调用由
`GoalSession` 统一准入：校验端点、当前 roster/capability、收件人边界、消息有效期
和动作预算；通过 `CommunicationRuntime.publish/consume` 处理；参与者只能看到已
送达且未过期的消息 observation，不能看到队列、未来调度、丢包原因、池状态或
评估器隐藏事件。每个正式 episode 只保留一个 replay-bound transport archive，
pose/camera/backend 事件在相同 archive 中按来源标识合并。

若产品选择把操作并入 action/event envelope，则必须新建 Goal contract revision
（例如 v4），并提供旧 v2/v3 适配器的兼容路径。不能依据运行时是否实现工具而改变
现有 action vocabulary，也不能把 `relay` 或 `synchronize` 计划节点直接解释成
p2p 字节证据。

## 下行与生命周期事件

派遣、取消和计划下发只有在可信 host 生成规范消息并经 backhaul downlink 传输后，
才能计为下行通信。传输延迟或丢包时，后端不得先执行再补记字节；动作的 admission
记录和物理执行回执仍是两个事实。成员退出、能力变化等生命周期通知如果要计入
通信成本，也必须由可信事件源发布并记录 `published_at_s`、`delivered_at_s`、
`consumed_at_s`；没有真实来源时保持 `null`。

## 评分侧车和证据门禁

目前可以采信：accepted action 计数、Goal host receipt clock、声明为 proxy 的
activation cost、经过 replay 校验的通信字节和 AoI。以下字段继续为 `null`，不能
从 wall clock 或模型日志推断：物理仿真时间、规划/重规划计算耗时、通知送达延迟、
未接线的 p2p 流量、消息对决策的因果影响。

每条 `bytes_served`、`dropped`、`expired` 记录需要保留 publication 的 pool 和
显式 direction。缺少方向时允许保留总字节/池字节，但评分侧车的方向分解必须
fail-closed 为 `null`，不能按 channel 名称猜测。通信证据缺失或 replay 失败时
不得当作零成本。

## 必须先拍板的接口问题

实现 E2 前请确认：

1. endpoint identity 是否采用本文件的 `cloud` / `edge:<id>` / `robot:<id>` 命名；
2. 是否采用可选 companion contract v1（推荐），还是另行注册 Goal v4 action/event；
3. edge brain 是否允许跨 edge p2p，还是只允许机器人端点 p2p；
4. `consume` 是否要求参与者显式确认，还是送达 observation 即可进入 AoI delivery
   统计、显式 consume 只影响 consume 口径；
5. 哪个可信 host 组件提供真实物理时钟、通知送达时刻和规划计算计时；在确认前这些
   字段保持 `null`。

## 验收顺序

1. 先以离线 contract/replay 测试验证 endpoint、收件人和方向字段；
2. 再实现 GoalSession companion service，并保留旧 v2/v3 参与者回归；
3. 接入 pose/camera/backend 的单一 replay archive；
4. 在本机和 ljy 隔离树运行固定延迟 vs 抖动 JP、IID 丢包 vs 突发 BP 以及 C90/C50
   临界点；报告绝对容量和 C/C0，bootstrap CI 只在有效样本内计算，不外推；
5. 任何 native provenance、remaining-task oracle 或 sham 缺失均只记平台缺口，
   不进入正式能力分母。
