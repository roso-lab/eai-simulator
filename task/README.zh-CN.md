# 通用任务编排层

`task/` 是外部任务包、协调算法、调用方持有的 EAI session 与独立 evaluator
之间的轻量边界。它不负责构建场景、管理 Isaac/Kit 生命周期、实现控制器或任务
成功谓词，也不拥有 USD、测评任务身份或正式场景配置。

```text
外部 EaiTaskPackage + 实时机器人 roster
                 -> planner / 可选 replan callback
                 -> TaskRunner DAG scheduler
                 -> 注入 executor（独占 EAI/Kit stepping）
                 -> 独立 evaluator（独占物理 verdict）
```

本目录不内置正式任务 manifest。单元测试使用通用 inline fixture；有场景约束的
短诊断输入只能放在项目忽略的 `tmp/` 下，不能因此升级成发布任务。

## 两种运行方式

`TaskRunner.run(...)` 保留兼容的一次性 decision/dispatch API。
`TaskRunner.run_orchestrated(...)` 提供可选的有界反馈编排：

- `OrchestrationLimits` 同时限制 round 和 dispatch 总数；
- 初始 planner 可分配完整 DAG，也可只分配当前子集；
- `ReplanProvider` 每轮显式替换 pending assignment；返回 `None` 即停止，
  runner 不切换算法，也不自动重试失败 assignment；
- executor 必须返回 `ExecutionUpdate`，其中
  `completed/retryable_failure/terminal_failure` 只控制依赖是否可继续；
- terminal failure 会阻塞后继任务，但独立 ready 任务仍可执行；
- 每轮同一机器人最多选一个任务；可选 `execute_batch` 一次接收 ready set，
  但 runner 不创建线程、不执行 Isaac step，Kit 仍由 callback 在 owner thread
  内串行访问；
- 未知 task、inactive robot、重复 task assignment、assigned/unassigned 重叠、
  能力不满足以及 feedback 身份缺失/多余均 fail closed。

调度层的 `completed` 不是物理成功。只有外部 evaluator 能给出任务/物理 verdict。
缺 executor 时，即使传入 evaluator，也必须返回
`N/A / physical_evidence_required`；缺 evaluator 时同样保持 `N/A`。

## 外部依赖协议

1. 调用方构造或加载 `EaiTaskPackage`，再用实时 session 采样的 robot roster
   调用 `bind_runtime(...)`。
2. scene provider 返回调用方持有的不透明 scene/session context。
3. planner 只提交 `AlgorithmDecision`，不能执行控制或宣称物理成功。
4. runner 可验证 task/robot identity、DAG 和声明的 robot capability；对象引用、
   controller resource、asset 与 skill 是否真实存在，只能由 executor 针对 live
   scene 解析。缺失或陈旧资源必须返回显式失败，不能伪造成 completed。
5. executor 独占控制器调用与 simulator stepping；batch callback 可在一个 session
   内串行处理 ready set。
6. evaluator 独占 milestone predicate 和最终 verdict；replay 也由调用方注入。

事件 ledger 会记录 decision、dispatch、scheduler update、有界终止和 evaluator
verdict，且整个任务合同层无需导入 Isaac Sim。
