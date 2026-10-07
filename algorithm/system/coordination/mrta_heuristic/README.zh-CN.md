# MRTA heuristic planner → EAI

该适配对应 ICRA 2024 与 IEEE T-RO 2025 的官方
[MRTA heuristic planner](https://github.com/multirobot-use/mrta_heuristic_planner)，官方代码
固定在 `c69509e8d2370f55824519dda0a13252e1817415`。论文代码是 MATLAB，包含
`heuristicTaskAllocator` 和 `planRepair`；本目录提供不依赖 MATLAB/Gurobi 的 Python
接口等价适配，方便在 `env_isaaclab` 中复现任务分配与延迟修复边界。

它不输出 Isaac 控制动作，也不负责按钮、抓取、导航或物理成功。Fire Rescue 中的
不可拆分任务按单机能力分配；coalition/fractional allocation 记为 `N/A`。正式账本会
把算法计划、EAI 执行反馈和物理里程碑分开记录。

在 EAI 场景入口中加载回调：

```text
algorithm.system.coordination.mrta_heuristic.fire_rescue_policy:assign
```
