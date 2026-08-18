# TeamWeaver

TeamWeaver combines **LLM-based semantic task decomposition** with **mixed-integer quadratic programming (MIQP)** for task decomposition, allocation, and replanning of heterogeneous multi-robot teams in dynamic environments.

## Positioning

Inside the EAI project, TeamWeaver runs as an **external planner**: it reads a "task instruction + symbolic world state" and outputs a "task DAG + robot allocation", which execution layers such as EAI, Nav2, and cmd_vel then carry out. It does not build simulation scenes and does not drive robots directly.

## Core flow

```text
Task instruction (natural language) + SymbolicWorldState
        ↓
DeepSeekSemanticDecomposer   LLM semantic decomposition into a task DAG
validate_plan_payload        capability/constraint validation
PhaseScheduler               phase/dependency scheduling
DynamicMIQPAllocator         robot-task allocation (Gurobi, falls back to the Hungarian algorithm without a license)
        ↓
TeamWeaverPlan (task DAG + allocation) → executed by EAI / Nav2
```

In closed-loop operation, execution layers report per-skill success/failure/timeout through `ExecutionFeedback` back to `TeamWeaverPipeline.accept_feedback()`; the pipeline advances phases accordingly and triggers `replan()` for re-decomposition and re-allocation when dynamic events occur (for example blocked paths or a lost robot).

## Using TeamWeaver with EAI

The public API is exported by `TeamWeaver.eai_adapter`: `TeamWeaverPipeline`, `DeepSeekSemanticDecomposer`, `DynamicMIQPAllocator`, `PhaseScheduler`, and more. It is pure Python depending only on numpy / scipy / openai / httpx. Add `algorithm/` to your `PYTHONPATH` first:

```bash
export PYTHONPATH="$PWD/algorithm:$PYTHONPATH"
```

Code, dependencies, and adapter-layer tests live under `algorithm/TeamWeaver/`.

## Source

Upstream repository: [TeamWeaver](https://github.com/southking372/TeamWeaver); the copy bundled with EAI lives under `algorithm/TeamWeaver/`.
