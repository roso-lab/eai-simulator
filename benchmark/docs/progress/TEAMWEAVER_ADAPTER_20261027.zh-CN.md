# TeamWeaver 合同适配器交接（2026-10-27）

## 范围与文件

本分支只新增以下三个文件：

- `benchmark/teamweaver.py`
- `benchmark/tests/test_teamweaver_case_integration.py`
- 本文档

`algorithm/system/coordination/TeamWeaver/` 及既有合同、EMOS、evaluator 文件均未修改。适配器是纯输入输出层；它不执行物理控制，也不声明物理成功。

## 逐字段映射

| 统一合同 | TeamWeaver 输入 | 规则 |
|---|---|---|
| `EaiTaskPackage.graph.tasks[*].task_id` | `FactoryTaskSpec.task_id` | 原样保留。`….description` 映射到 `name`。 |
| `description` | `FactoryTaskSpec.name` | 原样保留，仅用于算法任务标签。 |
| `target_ref` + `task.metadata.target_xy/target_position` 或 `package.metadata.target_positions` | `FactoryTaskSpec.target_name/target_xy` | 目标位置必须是有限 2/3 向量；缺失或畸形即 fail-closed。统一合同没有强制目标坐标，因此该映射要求由案例包提供。 |
| `required_capabilities` | `FactoryTaskSpec.hard_capabilities` | 每个必需能力成为硬约束；能力要求值默认 1.0。 |
| `task.metadata.capability_requirements` | `FactoryTaskSpec.capability_requirements` | 数值权重原样转换；统一必需能力缺省补 1.0。 |
| `prerequisites` 边 | 当前无 `FactoryTaskSpec` 对应字段 | 为避免改变算法语义，含依赖边的任务包明确 fail-closed，等待裁决；不自行实现调度。 |
| `RobotState.capability.robot_id` | TeamWeaver `RobotState.name` | 原样保留；重复、名册不一致均拒绝。 |
| `RobotState.position=(x,y,z)` | TeamWeaver `position=(x,y)` | 明确丢弃 z；这是二维 allocator 的既有输入限制，记录在 provenance。缺位置/非有限值拒绝。 |
| `capability.capabilities` | TeamWeaver `capabilities: name -> 1.0` | 统一能力集合转成 TeamWeaver 数值能力。 |
| `metadata.current_load` | TeamWeaver `current_load` | 可选非负整数；缺省 0。 |
| `capability.active` | 活跃机器人筛选 | inactive 不进入本轮分配。 |
| TeamWeaver `AllocationResult.assignments[*]` | `AlgorithmDecision.assignments[*]` | `task_id`、`robot.name` 转成统一 `Assignment`，来源为 condition.algorithm_id。 |
| TeamWeaver `solver`, `fallback_reason`, 总体映射信息 | `AlgorithmDecision.provenance` | 作为可审计 provenance；不写物理成功字段。`physical_success` 仅作为明确的 `False` provenance 标记，绝非成功声明。 |
| 未分配 ready/pending 节点 | `AlgorithmDecision.unassigned_tasks` | 已完成任务不重复列出；依赖未满足、无分配的任务列出。 |

初始化 `initialize(H_r, o_r)` 只复制空 evaluator 历史并校验 `o_r.robot_positions` 的键集与构造器绑定名册完全一致；预算默认 1 次，耗尽、二次初始化、错误历史或错误位置均 fail-closed。初始化完成前（默认 `require_initialize=True`）不得调用 allocator。

## Gurobi 与 CPU 验证

原始命令：

```text
/home/pupil/miniconda3/envs/env_isaaclab/bin/python -c "import gurobipy; print(gurobipy.gurobi.version())"
```

退出码 `0`，输出版本 `13.0.3`。系统默认 `python` 没有 `gurobipy`（退出码 `1`）；因此验证统一使用 `env_isaaclab` Python。TeamWeaver allocator 自带 Gurobi→SciPy Hungarian fallback；未安装依赖的解释器会在 fallback 处失败，适配器按合同将该异常拒绝，不安装、不 vendoring。

## 未修算法问题

未修改算法本体。观察到 `DynamicFactoryAllocator` 的 fallback 依赖 SciPy；在系统默认 Python 中 SciPy 也不可用，导致该路径异常。这是环境/既有依赖问题，适配器保持 fail-closed，交给主会话决定是否在运行环境声明依赖。二维 allocator 也天然丢弃统一三维位置的 z 分量，已在 provenance 和上表记录。

## 测试证据

- TeamWeaver 原有套件（基线与本分支）：使用 `env_isaaclab` Python、CPU-only、`PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q -p no:cacheprovider algorithm/system/coordination/TeamWeaver/tests`；结果见主会话运行记录。
- 适配器：同一命令目标 `benchmark/tests/test_teamweaver_case_integration.py`，退出码 `0`，`13 passed`。
- 新文件编译：`.../python -m py_compile benchmark/teamweaver.py benchmark/tests/test_teamweaver_case_integration.py`，退出码 `0`。

## 给主会话的集成建议

将本文和三个文件整体合并后，在规范第 7 节状态表增加：`TeamWeaver | 已接通（benchmark/teamweaver.py；无依赖边/非空历史的合同输入明确 fail-closed）`。保持算法目录、EMOS、统一合同和 evaluator 的写入边界不变。
