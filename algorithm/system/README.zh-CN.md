# 系统级算法

这里是高层多机器人系统算法的规范命名空间，负责回答“团队应该做什么”：

- 把任务拆成子任务或任务 DAG；
- 根据异构能力分配机器人或机器人联盟；
- 决定优先级、通信动作和重规划；
- 根据符号化执行反馈更新计划。

系统算法只接收无 Isaac 依赖的任务包、机器人能力表、符号世界状态和反馈账本，
只返回分配或协调决策。它不能读取 PhysX/关节状态、调用 EAI controller、生成
速度张量，也不能声明物理成功。

## 系统算法目录

下列路径同时包含原生适配器和历史实现，不代表已通过原算法验收。算法分类记录在
`algorithm/system/coordination/registry.py`，论文来源和 checkpoint 状态记录在
`algorithm/heterocoord/registry.py` 中：

| 系统职责 | 规范实现路径 |
|---|---|
| LLM 讨论与分配 | `algorithm/system/coordination/emos/` |
| LLM 分解、MIQP 分配、重规划 | `algorithm/system/coordination/TeamWeaver/` |
| 集中式 PEFA 分配 | `algorithm/system/coordination/coherent/` |
| Fisher 市场分配 | `algorithm/system/coordination/fmc_ata/` |
| 能力拍卖 | `algorithm/system/coordination/bt_capabilities/` |
| SADCHER 网络调度与匹配 | `algorithm/system/coordination/sadcher/` |
| RobotFleet DAG/分配/反馈边界 | `algorithm/system/coordination/robot_fleet/` |
| LaMMA-P 依赖顺序与技能分配 | `algorithm/system/coordination/lammap/` |
| HARL/HAPPO checkpoint 边界 | `algorithm/system/coordination/harl/` |
| 确定性异构 MRTA | `algorithm/system/coordination/mrta_heuristic/` |
| Hybrid SUSD 市场/搜索 | `algorithm/system/coordination/hybrid_susd/` |
| checkpoint 决策适配 | `algorithm/system/coordination/dcmrta/` |
| 测评决策与 provenance 桥 | `algorithm/heterocoord/` |

原算法统一入口是 `benchmark/native_algorithms.py`，固定源码清单在
`algorithm/heterocoord/paper_algorithms.json`，各方法通过 `native_adapter.py`
适配输入输出。当前 PDF 六列是 FMC-ATA、BT capabilities、SADCHER、HARL:HAPPO、
TeamWeaver、EMOS。实测状态见
`algorithm/heterocoord/NATIVE_ALGORITHM_ADAPTATION_REPORT_20261028.zh-CN.md`。
`benchmark/external_algorithms.py` 保留为历史诊断接口，其中重写或固定分配实现不能
用于原算法结果。HeteroMRTA 不进入当前适配和运行矩阵。

表中的论文适配器现在全部位于 `algorithm.system.coordination` 规范命名空间，不再
保留系统算法的顶层兼容副本。

## 数据边界

```text
任务包 + 机器人能力 + 符号状态 + 反馈
                    |
                    v
             系统算法适配器
                    |
                    v
       AlgorithmDecision / 任务 DAG / 重规划事件
                    |
                    v
                task.TaskRunner
```

TaskRunner 再把分配交给 skill executor；只有 EAI evaluator 提供物理证据后，
任务才可以被判定为物理成功。
