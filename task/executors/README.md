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
