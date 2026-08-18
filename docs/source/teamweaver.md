# TeamWeaver

TeamWeaver 把**大语言模型（LLM）的语义任务分解**与**混合整数二次规划（MIQP）的约束优化**结合起来，面向异构多机器人团队在动态环境中的任务分解、分配与重规划。

## 定位

在 EAI 工程中，TeamWeaver 作为**外部规划器**被调用：读取"任务指令 + 符号化世界状态"，输出"任务 DAG + 机器人分配"，再由 EAI / Nav2 / cmd_vel 等执行层执行。它不构建仿真场景，也不直接操作机器人。

## 核心流程

```text
任务指令（自然语言） + SymbolicWorldState（符号化世界状态）
        ↓
DeepSeekSemanticDecomposer   LLM 语义分解任务 DAG
validate_plan_payload        能力/约束校验
PhaseScheduler               阶段/依赖调度
DynamicMIQPAllocator         机器人-任务分配（Gurobi，缺 license 回退匈牙利算法）
        ↓
TeamWeaverPlan（任务 DAG + 分配）→ EAI / Nav2 执行
```

闭环运行时，执行层把每项技能的成功/失败/超时通过 `ExecutionFeedback` 送回 `TeamWeaverPipeline.accept_feedback()`；管道据此推进阶段，必要时触发 `replan()` 重新分解与再分配（例如障碍阻塞、机器人失联等动态事件）。

## 在 EAI 中的使用

公开 API 由 `TeamWeaver.eai_adapter` 导出：`TeamWeaverPipeline`、`DeepSeekSemanticDecomposer`、`DynamicMIQPAllocator`、`PhaseScheduler` 等，纯 Python 实现，仅依赖 numpy / scipy / openai / httpx。使用前把 `algorithm/` 加入 `PYTHONPATH`：

```bash
export PYTHONPATH="$PWD/algorithm:$PYTHONPATH"
```

代码、依赖与适配层测试见 `algorithm/TeamWeaver/`。

## 来源

算法源码仓库：[TeamWeaver](https://github.com/southking372/TeamWeaver)；EAI 内置副本位于 `algorithm/TeamWeaver/`。
