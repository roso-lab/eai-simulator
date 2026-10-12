# EAI executors

`skills.py` 提供 `SkillTaskExecutor`，通过调用方注入的 `BoundSkill` 连接下面的
执行链路。它不定义业务映射，支持 live 观测、请求去重和 session 内资源租约；
异常无法确认停止时隔离占用。用法与精确边界见
[运行接入说明](../RUNTIME_GUIDE.zh-CN.md)。

`cooperative.py` 提供 `CooperativeBatchExecutor` 和非步进 handle 协议，同场景
多个机器人每 tick 合并动作后只执行一次物理步。取消必须等实测停止；未确认停止
阻断整个共享 scene，并保留资源占用。外部进程 watchdog 才能提供墙钟硬超时。

这里放将高层 `Assignment` 转换为低层 `SkillRequest`，调用
`algorithm/skills/` 中的 provider 得到 `SkillPlan`，再映射为 EAI
controller/skill 调用的实现。

```text
Assignment (system algorithm)
        -> SkillRequest
        -> algorithm/skills provider
        -> SkillPlan / SkillCommand
        -> EAI controller contract
        -> caller-thread simulator steps
        -> ExecutionFeedback / ExecutionUpdate
```

executor 可以依赖 `simulator.py` 的 session API，但系统算法和 skill provider
不得直接调用 executor、推进 Isaac 或访问关节句柄。Skill provider 只计算计划或
命令；物理完成必须由 `task/evaluators/` 从 EAI 状态产生证据。

一次性 `TaskRunner.run()` executor 返回 `ExecutionFeedback`。有界 DAG 模式的
executor 返回 `ExecutionUpdate`；其中 `DependencyOutcome` 只表示 scheduler 是否
可以解锁后继，不能替代 evaluator 的物理成功证据。可选 `execute_batch(...)`
一次接收 parallel-ready set，但必须在 session owner thread 内串行推进 Kit；runner
不会创建并发 stepping。

executor 还负责解析 live scene 中的 object reference、asset、controller resource
和 skill implementation。runner 只能验证 manifest 声明的 robot capability，不能
推断这些不透明资源是否存在；资源缺失或版本不匹配必须返回显式 failure/update。

## 地面导航

`ground_navigation.py` 提供 `GroundNavigationExecutor` 控制周期适配器：接收
地面 `SkillRequest`，从已创建的 EAI 环境读取真实 XYZ+yaw，将纯 Provider
生成的 `base_velocity` 经 `env.step` 传入 controller。调用者注入已校验拓扑、
世界状态校验、模拟时间或动作聚合回调；executor 不创建 simulator。

显式调用 `dispatch` 后，用 `control` 发送 start、continue、hold、replan 或 stop。
任务独立保存模拟时间和私有命令证据；成功、取消、不可达、超时或异常都应用
零速度，应用失败时记录停止错误。通用接入不加载 City benchmark 或地图产品。
高层 `Assignment -> SkillRequest` 的映射与 TaskRunner 注入仍由任务 host 完成。

详见 `algorithm/skills/navigation/GROUND_NAVIGATION.zh-CN.md`。

## 无人机 Pick/Place

`aerial_pick_place.py` 提供 `AerialPickPlaceExecutor` 和 `PhysxAerialTransportBridge`。
executor 使用已有 session 的真实位姿和模拟时间，写入 `[1,3]` goal_position，
经原位置控制器推进；bridge 管理临时世界支撑、完整旋转局部帧 FixedJoint 和
物体碰撞备份/恢复，保留质量等物理属性。调用者可注入引用、位姿和动作聚合回调。

每阶段只编译当前命令，修改阶段等实际应用确认后推进。持物停止/失败/超时
隔离并保留连接和资源，直到 host 显式恢复；不可报告为可自动释放的 quiescent。
高层 Assignment 映射和 evaluator 仍由 host 提供。
详见 `algorithm/skills/aerial_transport/AERIAL_PICK_PLACE.zh-CN.md`。
