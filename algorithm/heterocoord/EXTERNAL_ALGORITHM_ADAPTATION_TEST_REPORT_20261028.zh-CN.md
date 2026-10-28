# 外部算法适配与测试报告（2026-10-28）

> 历史协议测试报告，不能作为原算法 Benchmark 适配验收。2026-10-28 源码审计确认：
> FMC-ATA 使用固定映射、BT 使用本地重写、SADCHER 存在替代 matching/不完整加载路径，
> RobotFleet 与 LaMMA-P 未运行原框架。下文的“通过”仅表示当时诊断代码的测试结果。
> 原算法入口改为 `benchmark.native_algorithms.build_native_adapter`，版本清单见
> `algorithm/heterocoord/paper_algorithms.json`；正式状态以新的原生运行报告为准。

本轮运行矩阵包含 FMC-ATA、BT capabilities、COHERENT、SADCHER、RobotFleet、
LaMMA-P 和 HARL/HAPPO。EMOS 与 TeamWeaver 沿用已有适配；HeteroMRTA 按要求
排除，仅保留历史 provenance 记录。

## 适配结果

统一入口是 `benchmark.external_algorithms.ExternalAlgorithmDecisionAdapter`：

```text
AssignmentRequest
    -> EaiTaskPackage
    -> AlgorithmDecision(assignments, unassigned_tasks, provenance)
    -> TaskRunner / EAI executor / evaluator
```

适配器不执行控制、不读取 PhysX、不生成物理成功结论。RobotFleet 和 LaMMA-P
在 `TaskRunner.run_orchestrated(...)` 中提供显式 pending-task feedback replan；
SADCHER、FMC-ATA、BT capabilities 和 COHERENT 在反馈轮次重新调用其对应的
Fire Rescue 分配边界。HARL/HAPPO 没有可核验的 Fire Rescue checkpoint 与环境
契约时返回 `N/A`，不会伪造推理结果。

## 本地验证

| 范围 | 结果 |
|---|---:|
| 外部算法 + EMOS + TaskRunner + HeteroCoord 相关测试 | **83 passed** |
| `algorithm/system/coordination`（排除 TeamWeaver） | **34 passed** |
| 外部适配器专项测试 | **14 passed** |
| 新增适配器与算法目录编译 | **通过** |
| TeamWeaver 测试 | 142 passed, 4 failed |

TeamWeaver 的 4 个失败均来自可选运行时边界：2 个测试需要 `gurobipy`，2 个
测试需要 `openai`；缺少 Gurobi 时回退到 Hungarian，因而一个测试不满足其
MIQP solver 断言。它们没有改变本轮外部适配器结果。

工作区范围 pytest 还会扫描历史 `logs/g1_training/`、`source/EAI/test/` 和
其他实验目录；即使排除上述两项测试，仍因重复的
`implementation.test_contracts` 模块名和大量 Isaac/EAI 环境依赖产生 **2876
项收集错误**。该命令没有作为本轮算法通过依据。算法回归使用了上面的明确
测试路径，避免把历史实验目录误当成当前仓库测试包。

## 上游最小 smoke

| 仓库 | 结果 | 证据/边界 |
|---|---|---|
| SADCHER | **通过** | 官方 `best_checkpoint.pt` 加载并完成 Fire Rescue 分配；输出 `carter_1→blue, m20_1→red, m20_2→yellow, scout_1→green` |
| FMC-ATA | **通过** | 官方 `TaskSimple`、`PlayerSimple`、`AllocationSolverSingleTaskInit`、`Msg` 可导入 |
| COHERENT | **通过** | 官方仓库 Python 文件编译通过 |
| HARL | **通过** | 官方 `harl.algorithms.actors.happo.HAPPO` 与 `happo.yaml` 可导入/存在；没有 Fire Rescue checkpoint |
| RobotFleet | **受依赖阻断** | planner 入口缺 `openai`；allocator 入口缺 `sqlalchemy`；本地 EAI 适配因此保持 in-memory 边界 |
| BT capabilities | **受 ROS 依赖阻断** | 官方 ROS 入口缺 `rospy`；本地适配保持 capability auction 的无 ROS 纯 Python 边界 |
| LaMMA-P | **部分通过** | `scripts/*.py` 编译通过；仓库自带 `resources/example.py` 有语法错误，且缺少 `downward/fast-downward.py` |

## 仍未覆盖的证据

本轮没有启动 Isaac/EAI 物理任务，也没有产生 evaluator-owned milestone、真实
控制器反馈、物理任务完成或 D1-D6 发布证据。因此所有本地 symbolic/TaskRunner
结果只证明协议、输入输出契约、分配和有界重规划链路，不能升级为物理成功或跨域
泛化结论。
