# TeamWeaver: LLM + MIQP Cooperative Planning

For the source-backed Benchmark path and the October 28, 2026 compatibility repairs,
see [NATIVE_ADAPTER_REPORT.zh-CN.md](NATIVE_ADAPTER_REPORT.zh-CN.md).
The legacy `eai_adapter` pipeline described below is a separate experimental condition;
its custom planner and Hungarian fallback are not evidence for the original HRCS solver.

TeamWeaver combines LLM-based semantic task decomposition with mixed-integer quadratic programming (MIQP) allocation for heterogeneous multi-robot teams. In EAI it is an external planner: it consumes a natural-language instruction and symbolic world state, returns a task DAG and robot assignments, and leaves execution to EAI, Nav2, or cmd_vel. It does not build simulator scenes or drive robots directly.

This directory contains the EAI adapter from the upstream research code. Habitat-Sim, PARTNR, PyTorch, and Hydra are outside the current EAI runtime boundary. The package uses the single TeamWeaver namespace and no habitat_llm compatibility shim.

## HeteroCoord benchmark condition

`algorithm.system.coordination.TeamWeaver.eai_adapter.benchmark_condition` exposes the shared
`condition_record()`/`condition_manifest()` and a Fire Rescue `assign()` bridge.
The stateful `TeamWeaverPipeline` retains the paper flow—semantic decomposition,
validation, phase scheduling, MIQP/Hungarian allocation, execution feedback,
and replanning. The stateless Fire Rescue `module:assign` seam receives a
caller-supplied frozen task contract, so its default manifest is
`execution_scope=allocation_only_fire_rescue_hook`; it does not invoke the
DeepSeek decomposer or retain feedback across rounds. Register
`execution_scope=full_pipeline` only when the pipeline is driven directly with
the actual model/API provenance and execution feedback.

The condition is named `TeamWeaver-adapted-to-EAI` and has no learned
checkpoint. Without `gurobipy` and a valid license the allocator uses scipy's
Hungarian fallback; preserve its solver/fallback reason and do not label the
result MIQP. D2/D4/D5 and D3 without external P2 handoff evidence are `N/A`,
and an assignment never replaces EAI evaluator physical milestone evidence.

## Layout and installation

~~~text
algorithm/system/coordination/TeamWeaver/
├── __init__.py
├── eai_adapter/          # pure-Python EAI integration layer
├── tests/                # maintained adapter tests
├── README.md
├── README.zh-CN.md
└── requirements-eai.txt
~~~

~~~bash
pip install -r algorithm/system/coordination/TeamWeaver/requirements-eai.txt
export PYTHONPATH="$PWD:$PWD/algorithm:$PYTHONPATH"
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

Import from algorithm.system.coordination.TeamWeaver.eai_adapter:

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
from algorithm.system.coordination.TeamWeaver.eai_adapter import (
    TeamWeaverPipeline,
    DeepSeekSemanticDecomposer,
    DynamicMIQPAllocator,
)
from algorithm.system.coordination.TeamWeaver.eai_adapter.task_models import SymbolicWorldState

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
PYTHONPATH="$PWD:$PWD/algorithm" pytest -q algorithm/system/coordination/TeamWeaver/tests/test_eai_*.py
~~~

Add capabilities in capability_ontology.py, task types in task_models.py plus the ontology mapping, and alternative decomposers or allocators through dependency injection. Keep simulator-specific logic outside this package.

Allocation-only Fire Rescue hook:

~~~text
algorithm.system.coordination.TeamWeaver.eai_adapter.benchmark_condition:assign
~~~

This replaces only allocation; EAI retains the scene, controllers, navigation,
physical execution, and termination.
