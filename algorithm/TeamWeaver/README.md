# TeamWeaver — 面向异构多机器人动态协作的 LLM + MIQP 规划算法

TeamWeaver 把**大语言模型（LLM）的语义任务分解**与**混合整数二次规划（MIQP）的约束优化**结合起来，用于异构多机器人团队在动态环境中的任务分解、分配与重规划。在 EAI 工程里，它作为**外部规划器**被调用：读取“任务指令 + 符号化世界状态”，输出“任务 DAG + 机器人分配”，再由 EAI / Nav2 / cmd_vel 等执行层落地。它不构建仿真场景，也不直接操作机器人。

> 本项目由上游论文代码 fork 而来，上游内部统一使用 `habitat_llm` 包名；本仓库已将所有内部引用统一为 `TeamWeaver`，**不再需要任何 shim**。

---

## 1. 目录结构

```
algorithm/TeamWeaver/                 # 算法包根目录（即 Python 包 TeamWeaver）
├── __init__.py
├── eai_adapter/                      # ★ EAI 集成层（对外公开 API，纯 Python，独立可用）
├── tests/                            # 适配层单测（test_eai_*.py + conftest/eai_test_support）
├── README.md                         # 本文件
└── requirements-eai.txt              # EAI 适配层依赖
```

- **`eai_adapter/`**：语义任务分解（DeepSeek）+ MIQP 任务分配 + 阶段调度 + 能力本体的完整实现（纯 Python，只需 numpy/scipy/openai/httpx）。
- 上游论文原始代码（依赖 Habitat-Sim / PARTNR / PyTorch / Hydra，无法在 EAI 环境运行）已不随本目录分发；本目录只保留可直接在 EAI 中调用的适配层。

---

## 2. 导入方式（单一命名空间 `TeamWeaver`）

整个包统一以 **`TeamWeaver`** 为顶层名导入。内部代码使用绝对导入（`from TeamWeaver.xxx import ...`），因此需要把 `algorithm/` 目录加入 `PYTHONPATH`：

```bash
export PYTHONPATH="$PWD/algorithm:$PYTHONPATH"
```

```python
from TeamWeaver.eai_adapter import (
    TeamWeaverPipeline,
    DeepSeekSemanticDecomposer,
    DynamicMIQPAllocator,
)
```

> 说明：不要写成 `algorithm.TeamWeaver`——包内是绝对导入 `TeamWeaver.*`，顶层名必须能直接 import。

---

## 3. 在 EAI 中的定位与调用机理

TeamWeaver 与 EAI 的关系是**规划器 ↔ 执行环境**的松耦合：

```text
任务指令（自然语言） + SymbolicWorldState（符号化世界状态）
                          │
                          ▼
              TeamWeaverPipeline
   ┌─────────────────────────────────────────────┐
   │ 1. DeepSeekSemanticDecomposer  语义分解任务 DAG │
   │ 2. validate_plan_payload       能力/约束校验    │
   │ 3. PhaseScheduler              阶段/依赖调度     │
   │ 4. DynamicMIQPAllocator        机器人-任务分配    │
   └─────────────────────────────────────────────┘
                          │
                          ▼
     TeamWeaverPlan（任务 DAG + 分配结果） → 回灌给 EAI / Nav2 执行
```

闭环运行时：执行层把每项技能的成功/失败/超时通过 `ExecutionFeedback` 送回 `TeamWeaverPipeline.accept_feedback()`；管道据此推进阶段、必要时触发 `replan()` 重新语义分解与再分配（例如障碍物阻塞、机器人失联等动态事件）。

---

## 4. 对外公开 API

入口：`TeamWeaver.eai_adapter`。`__init__.py` 的 `__all__` 导出的公开符号包括：

| 符号 | 作用 |
|---|---|
| `TeamWeaverPipeline` | 顶层管道：`plan_initial` / `replan` / `accept_feedback` / `mark_navigating` / `mark_operating` / `mission_status` / `cancel` |
| `TeamWeaverPlan` | 一次规划结果（任务、分配、阶段索引、重规划原因等） |
| `DeepSeekSemanticDecomposer` | LLM 语义分解器（OpenAI 兼容协议，默认指向 DeepSeek） |
| `DynamicMIQPAllocator` | MIQP 分配器（Gurobi 求解，缺 license 时回退 scipy 匈牙利算法） |
| `PhaseScheduler` | 任务阶段 / 依赖 / 抢占调度 |
| `CapabilityTracker` | 机器人能力随执行反馈的动态更新 |
| `SemanticTask` / `SymbolicWorldState` / `ExecutionFeedback` / `ObstacleSnapshot` | 输入输出数据结构 |
| `validate_plan_payload` | LLM 输出 JSON 的能力/约束校验 |

---

## 5. 输入 / 输出契约

### 输入：`SymbolicWorldState`（`task_models.py`）

符号化世界快照，关键字段：

- `hazard_id / hazard_position / hazard_active` — 险情（火情）描述
- `targets` — `TargetSnapshot`（ref、position、available、kind、compatible_task_types）
- `robots` — `RobotSnapshot`（name、position、base_capabilities、reliability、equipment、busy、current_load、safe、preemptible…）
- `obstacles` — `ObstacleSnapshot`（障碍物 id/位置/尺寸/是否阻塞某任务）
- `extinguisher_* / rescue_channel_open` — 灭火器与救援通道实体状态
- `completed_task_ids / failed_task_ids / recent_feedback / facts / observations` — 执行历史与观测

调用 `plan_initial(instruction: str, world: SymbolicWorldState)`。

### 中间表示：`SemanticTask`

LLM 分解得到的最小可执行任务：

- `task_id / task_type / description / target_ref / priority`
- `requirements` — 能力需求（`CapabilityRequirement(minimum, weight, hard)`）
- `prerequisites` — 前置任务 DAG 依赖
- `can_parallel` — 是否允许与其它任务并行
- `estimated_duration_s / preferred_agent`
- `continuation_of / required_agent` — 系统续接任务（如取灭火器后必须返回火点）

`TaskType` 枚举：`navigate` / `inspect` / `establish_relay` / `pick_extinguisher` / `deliver_extinguisher` / `press_rescue_button` / `remove_obstacle` / `wait`。

能力本体 `CAPABILITIES`（`capability_ontology.py`）：`navigation` / `sensing` / `relay` / `payload` / `agility` / `manipulation` / `button_press` / `extinguisher_handling` / `obstacle_handling`。

### 输出：`TeamWeaverPlan`

- `tasks` — 当前完整任务集合（含已完成的）
- `allocation` — 机器人-任务分配（`assignments` / `solver` / `total_cost` / 抢占信息）
- `phase_index / phase_total` — 当前阶段 / 总阶段
- `completed_task_ids / failed_task_ids / replan_reason`

### 反馈：`ExecutionFeedback`

执行层回报单技能结果：`task_id / robot_name / outcome / reason / failure_kind / relevant_capabilities / world_changes / timestamp_s`。`FailureKind` 覆盖执行失败、路径失败、超时、机器人跌倒、导航停滞、控制失败、约束违规、世界状态冲突、中继丢失等。

---

## 6. 快速开始

```bash
# 1) 依赖（见 requirements-eai.txt）
pip install -r algorithm/TeamWeaver/requirements-eai.txt  # from the repository root
# pip install gurobipy                        # 可选：启用真正 MIQP，否则自动回退匈牙利算法

# 2) 环境变量（DeepSeek 语义分解必需）
export DEEPSEEK_API_KEY="sk-..."
export TEAMWEAVER_DEEPSEEK_BASE_URL="https://api.deepseek.com/v1"   # 可选，默认值
export TEAMWEAVER_DEEPSEEK_MODEL="deepseek-chat"                    # 可选，默认值

# 3) 运行（从仓库根目录，需把 algorithm/ 加入 PYTHONPATH）
PYTHONPATH="$PWD/algorithm" python - <<'PY'
from TeamWeaver.eai_adapter import (
    TeamWeaverPipeline,
    DeepSeekSemanticDecomposer,
    DynamicMIQPAllocator,
)
from TeamWeaver.eai_adapter.task_models import SymbolicWorldState

world = SymbolicWorldState(
    hazard_id=1,
    hazard_position=(0.0, 0.0, 0.0),
    hazard_active=True,
    targets=(),
    robots=(),            # 这里填 RobotSnapshot 列表
    extinguisher_available=False,
    extinguisher_carrier=None,
    extinguisher_delivered=False,
    rescue_channel_open=False,
    completed_task_ids=(),
    failed_task_ids=(),
    recent_feedback=(),
    observations={},
)

pipeline = TeamWeaverPipeline(
    decomposer=DeepSeekSemanticDecomposer(),
    allocator=DynamicMIQPAllocator(),
)
plan = pipeline.plan_initial("检查火情并扑灭火源", world)
print(plan.decomposition_source, plan.phase_index, plan.phase_total)
for assignment in plan.allocation.assignments:
    print(assignment.task_id, "->", assignment.robot_name)
PY
```

> 注意：`SymbolicWorldState` 是 frozen dataclass，字段较多且都带校验；该类型由 `algorithm/TeamWeaver/eai_adapter/task_models.py` 定义并从 `eai_adapter` 导出。实际接入时，调用方应使用自己的 EAI 世界状态采样器构造该对象，而不是手写空对象。

---

## 7. 测试

```bash
# 适配层单测（纯 Python，无 Isaac / 无 GPU / 无外部 LLM 调用，已通过 141 项）
PYTHONPATH="$PWD/algorithm" pytest -q algorithm/TeamWeaver/tests/test_eai_*.py
```

---

## 8. 扩展点

| 想做什么 | 改哪里 |
|---|---|
| 增加机器人能力维度 | `eai_adapter/capability_ontology.py` 的 `CAPABILITIES` 集合 + `validate_plan_payload` |
| 增加任务类型 | `eai_adapter/task_models.py` 的 `TaskType` 枚举 + ontology 中的需求映射 |
| 更换/增加 LLM 后端 | 实现 `DeepSeekSemanticDecomposer` 同款接口（`decompose(instruction, world, ...) -> SemanticDecompositionResult`），通过 `TeamWeaverPipeline(decomposer=...)` 注入 |
| 更换分配求解器 | 传入 `DynamicMIQPAllocator(miqp_solver=...)` 或实现 `allocate(...) -> AllocationResult` |
| 场景相关后处理 | `TeamWeaverPipeline(post_decomposition=...)`，或 `eai_adapter/scenario_config.py` 的 `set_active_scenario(...)` |

---

## 9. 依赖

EAI 适配层（本 README 覆盖的范围）只依赖纯 Python 数值栈：

- **必需**：`numpy`、`scipy`、`openai`、`httpx`
- **可选**：`gurobipy`（缺失时 MIQP 自动回退到 scipy 的匈牙利分配）
- **测试**：`pytest`
