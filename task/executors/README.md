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
