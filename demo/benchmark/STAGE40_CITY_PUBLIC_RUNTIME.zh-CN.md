# 阶段40：City公开运行观测与真实开始时刻

承接 `6d1f6f4`，主线保持 R1/R2。新增能力是可选的可信场景遥测输入和执行时钟对账；
不改冻结题包、见证、能力半径，不提供作者拆题/指派/时序，也不提升任何发布标志。

## 公开观测路径

`run_city_autonomous_context/episode(..., communication_runtime=runtime)` 显式接入
一个宿主预配置的共享 `CommunicationRuntime`；至少声明 `backhaul_uplink` 和
`backhaul_downlink` 两池。未提供时保持既有接口。新入口使用严格执行时钟。

`GoalCityRuntimeIntake` 在初始回合和技能完成边界读取 native runner 已捕获并送达的
observation，采用字段白名单重新构造公开消息：实体 ID、类别、米制位置、车辆公开状态、
源观测时间/有效期、公开集合候选及可调用的 incident recipient service ID。
集合区安全与容量仍标 `unverified`；不把静态候选当作已测安全区。

这是 `city_runtime` 提供的场景状态遥测，不声称是某机器人的相机/FoV，不伪造机器人
last-mile归属。共享回传池记录真实规范序列化字节、排队、延迟、丢包与有效期；仅链路
实际送达的新鲜消息进入 `observation` 事件。过期或丢弃不产生公开成功事件，拥塞不触发
免费重采样。机器人位姿按需工具与本场景实体输入是不同接口。

参与者可忽略消息，或使用现有 `consume_message` 消费并在自己的 task/dispatch
`evidence_refs` 中引用。Host 将该消息的消费同步到唯一 transport owner，避免会话账本
已消费而通信账本未计量。共享下行使用既有 `GoalExecutionDownlink` 传递完整技能命令，
未实际送达不执行；迟到、过期和取消仍按既有回执语义记录。这个模式不实现分布式边脑，
不修改待用户冻结的多端点接口。

原始 native observation、源 episode/时钟映射只进入 evaluator 侧
`execution_observer_report.json`；参与者看不到私有人员到集合区绑定、角色分配、路线、
source内部字段或完整原生 context。共享账本与replay进入已有评分原始事实侧车。

## 执行时钟

`CityNativeGoalBackend(strict_dispatch_clock=True)` 在任何服务调用前以可信 host 当前时刻
对齐真实 native 时钟。计划区间采用既有半开区间 `[start,end)`；等待物理步进后已越过
末端的请求必须失败，不在过窗后新发服务请求。旧直接调用默认 `False`，保持兼容。

`dispatch_clock` 分别保存原始派发、当前backend入口、物理开始/结束和原生服务请求时间，
用 epoch 显式换算相对秒。backend入口时刻不冒充重试后服务请求时刻，计划重叠不冒充
真实并发。`advance_idle()` 仅推进物理时钟供通信等待，按公开仿真预算限制；没有可达
下一步时报告基础设施限制，不凭空回填目标时间。相同tick的native观测复用原ID，不续期。

## 当前仍不能声称的能力

1. 当前公开集合候选缺少几何、可达性和容量的实测查询；底层人员路线存在隐藏目的地约束。
   不能靠泄露 person→assembly 绑定或作者分工补齐。后续需要合法的公开地图/affordance接口。
2. 测试策略只从公开task/API/observation生成局部计划，证明接口可用，不代表真实模型已完成
   四项业务结果。旧 witness-derived allocator也不能当作完整自主拆题证据。
3. `CITY-GOAL-NATIVE-01` 初始native二元oracle及实体版本账本仍未认证；合成development
   reader不替代它。默认oracle继续failed/null，不进入正式部署判断分母。
4. 原始进展向量、通信/消费与回执可复算，不代表正式Q(t)、首次物理满足时刻或完整六轴分数。

所有报告继续 `scoreable=false`、`execution_validated=false`、
`business_outcomes_validated=false`。验收以冻结提交及实际测试/原生归档为准。

## 本机验收

15 文件联合回归：`258 passed in 21.62s`，使用 `env_isaaclab`、禁用第三方 pytest
自动插件，清单及日志位于 `tmp/stage40-validation/test-files.txt`、`local-joint.log`。
独立审查完成公开字段隔离、共享账本消费、预算及池 TTL 截断检查，未发现剩余可复现阻断。
本记录尚不代表 ljy 或原生验证完成；后续验收单独记录。
