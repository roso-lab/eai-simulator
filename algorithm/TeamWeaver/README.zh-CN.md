# TeamWeaver：LLM + MIQP 协作规划

TeamWeaver 将大语言模型的语义任务分解与混合整数二次规划（MIQP）分配结合，用于异构多机器人团队的任务分解、分配、阶段调度和动态重规划。在 EAI 中它是外部规划器：输入自然语言任务和符号化世界状态，输出任务 DAG 与机器人分配，再交给 EAI、Nav2 或 cmd_vel 执行。它不构建仿真场景，也不直接驱动机器人。

本目录是上游研究代码的 EAI 适配层。上游的 Habitat-Sim、PARTNR、PyTorch、Hydra 运行时不属于当前 EAI 边界；包内统一使用 TeamWeaver 命名空间，不需要 habitat_llm shim。

## 目录和安装

~~~text
algorithm/TeamWeaver/
├── __init__.py
├── eai_adapter/          # 纯 Python EAI 集成层
├── tests/                # 已维护的适配层测试
├── README.md
├── README.zh-CN.md
└── requirements-eai.txt
~~~

~~~bash
pip install -r algorithm/TeamWeaver/requirements-eai.txt
export PYTHONPATH="$PWD/algorithm:$PYTHONPATH"
~~~

必需依赖为 numpy、scipy、openai 和 httpx。gurobipy 可选；没有有效 license 时自动回退到 scipy 匈牙利算法。语义分解需要 DEEPSEEK_API_KEY；TEAMWEAVER_DEEPSEEK_BASE_URL 和 TEAMWEAVER_DEEPSEEK_MODEL 可选。

## 数据流

~~~text
自然语言任务 + SymbolicWorldState
        │
        ▼
TeamWeaverPipeline
  1. DeepSeekSemanticDecomposer  -> 任务 DAG
  2. validate_plan_payload        -> 能力与约束校验
  3. PhaseScheduler               -> 阶段与依赖调度
  4. DynamicMIQPAllocator         -> 机器人分配
        │
        ▼
TeamWeaverPlan -> EAI / Nav2 执行层
~~~

执行层通过 ExecutionFeedback 回报成功、失败、超时和世界变化；当障碍物、机器人失联或其他动态事件阻塞任务时，管道会推进阶段或重新规划。

## 公开 API

从 TeamWeaver.eai_adapter 导入：

| 符号 | 作用 |
|---|---|
| TeamWeaverPipeline | 顶层流程：plan_initial、replan、accept_feedback、mission_status 和 cancel。 |
| TeamWeaverPlan | 任务、分配、阶段索引和重规划原因。 |
| DeepSeekSemanticDecomposer | OpenAI 兼容协议的语义分解器，默认使用 DeepSeek。 |
| DynamicMIQPAllocator | MIQP 分配器，缺少条件时回退匈牙利算法。 |
| PhaseScheduler | 任务阶段、依赖和抢占调度。 |
| CapabilityTracker | 根据执行反馈动态更新能力。 |
| SemanticTask、SymbolicWorldState、ExecutionFeedback | 规划输入、任务和反馈数据结构。 |
| validate_plan_payload | 校验 LLM JSON 的能力与约束。 |

## 最小调用

~~~python
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
    robots=(),
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
plan = pipeline.plan_initial('检查火情并扑灭火源', world)
print(plan.phase_index, plan.phase_total)
~~~

SymbolicWorldState 是带校验的 frozen dataclass。实际集成应由 EAI 世界状态采样器构造它，不应使用上面的空对象示例。

## 验证和扩展

~~~bash
PYTHONPATH="$PWD/algorithm" pytest -q algorithm/TeamWeaver/tests/test_eai_*.py
~~~

增加能力可修改 capability_ontology.py，增加任务类型可修改 task_models.py 和 ontology 映射；替换分解器或分配器时，通过依赖注入完成，不要把仿真场景逻辑写入本包。
