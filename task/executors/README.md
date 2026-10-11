# EAI executors

这里放将高层 `Assignment` 转换为低层 `SkillRequest`，调用
`algorithm/skills/` 中的 provider 得到 `SkillPlan`，再映射为 EAI
controller/skill 调用的实现。

```text
Assignment (system algorithm)
        -> SkillRequest
        -> algorithm/skills provider
        -> SkillPlan / SkillCommand
        -> EAI controller contract
        -> simulator step and ExecutionFeedback
```

executor 可以依赖 `simulator.py` 的 session API，但系统算法和 skill provider
不得直接调用 executor、推进 Isaac 或访问关节句柄。Skill provider 只计算计划或
命令；物理完成必须由 `task/evaluators/` 从 EAI 状态产生证据。

`ground_navigation.py` 提供 `GroundNavigationExecutor` 控制周期适配器：接收
地面 `SkillRequest`，从已创建的 EAI 环境读取真实 XYZ+yaw，将纯 Provider
生成的 `base_velocity` 经 `env.step` 传入 controller。调用者注入已校验拓扑、
世界状态校验、模拟时间或动作聚合回调；executor 不创建 simulator。

显式调用 `dispatch` 后，用 `control` 发送 start、continue、hold、replan 或 stop。
任务独立保存模拟时间和私有命令证据；成功、取消、不可达、超时或异常都应用
零速度，应用失败时记录停止错误。通用接入不加载 City benchmark 或地图产品。
高层 `Assignment -> SkillRequest` 的映射与 TaskRunner 注入仍由任务 host 完成。

详见[地面导航说明](../../algorithm/skills/navigation/GROUND_NAVIGATION.zh-CN.md)。
