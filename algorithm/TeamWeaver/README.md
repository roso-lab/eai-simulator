# TeamWeaver: LLM + MIQP Cooperative Planning

TeamWeaver combines LLM-based semantic task decomposition with mixed-integer quadratic programming (MIQP) allocation for heterogeneous multi-robot teams. In EAI it is an external planner: it consumes a natural-language instruction and symbolic world state, returns a task DAG and robot assignments, and leaves execution to EAI, Nav2, or cmd_vel. It does not build simulator scenes or drive robots directly.

This directory contains the EAI adapter from the upstream research code. Habitat-Sim, PARTNR, PyTorch, and Hydra are outside the current EAI runtime boundary. The package uses the single TeamWeaver namespace and no habitat_llm compatibility shim.

## Layout and installation

~~~text
algorithm/TeamWeaver/
├── __init__.py
├── eai_adapter/          # pure-Python EAI integration layer
├── tests/                # maintained adapter tests
├── README.md
├── README.zh-CN.md
└── requirements-eai.txt
~~~

~~~bash
pip install -r algorithm/TeamWeaver/requirements-eai.txt
export PYTHONPATH="$PWD/algorithm:$PYTHONPATH"
~~~

The required stack is numpy, scipy, openai, and httpx. gurobipy is optional; without a valid license the allocator falls back to scipy's Hungarian assignment. Semantic decomposition requires DEEPSEEK_API_KEY. TEAMWEAVER_DEEPSEEK_BASE_URL and TEAMWEAVER_DEEPSEEK_MODEL are optional overrides.

## Data flow

~~~text
Natural-language instruction + SymbolicWorldState
        │
        ▼
TeamWeaverPipeline
  1. DeepSeekSemanticDecomposer  -> task DAG
  2. validate_plan_payload        -> capability/constraint checks
  3. PhaseScheduler               -> phases and dependencies
  4. DynamicMIQPAllocator         -> robot assignment
        │
        ▼
TeamWeaverPlan -> EAI / Nav2 execution layer
~~~

The execution layer reports success, failure, timeout, and world changes through ExecutionFeedback. The pipeline advances phases or replans when an obstacle, lost robot, or other dynamic event blocks the mission.

## Public API

Import from TeamWeaver.eai_adapter:

| Symbol | Responsibility |
|---|---|
| TeamWeaverPipeline | Top-level lifecycle: plan_initial, replan, accept_feedback, mission_status, and cancel. |
| TeamWeaverPlan | Tasks, assignments, phase indexes, and replanning reason. |
| DeepSeekSemanticDecomposer | OpenAI-compatible semantic decomposer, defaulting to DeepSeek. |
| DynamicMIQPAllocator | MIQP allocator with a Hungarian fallback. |
| PhaseScheduler | Task phases, dependencies, and preemption. |
| CapabilityTracker | Dynamic capability updates from execution feedback. |
| SemanticTask, SymbolicWorldState, ExecutionFeedback | Planning input, task, and feedback data structures. |
| validate_plan_payload | Capability and constraint validation for LLM JSON. |

## Minimal call

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
plan = pipeline.plan_initial('inspect the hazard and extinguish it', world)
print(plan.phase_index, plan.phase_total)
~~~

SymbolicWorldState is a validated frozen dataclass. Production integrations should build it from live EAI state rather than using the empty example above.

## Validation and extension

~~~bash
PYTHONPATH="$PWD/algorithm" pytest -q algorithm/TeamWeaver/tests/test_eai_*.py
~~~

Add capabilities in capability_ontology.py, task types in task_models.py plus the ontology mapping, and alternative decomposers or allocators through dependency injection. Keep simulator-specific logic outside this package.
