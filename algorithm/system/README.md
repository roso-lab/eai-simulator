# System algorithms

This namespace is the canonical home for algorithms that decide what the team
should do:

- decompose a mission into subtasks or a task DAG;
- allocate subtasks or coalitions to heterogeneous robots;
- set priorities, communication actions, and replanning decisions; and
- consume symbolic execution feedback to update the plan.

A system algorithm receives a dependency-light task package, robot capability
roster, symbolic world state, and feedback ledger. It returns an assignment or
coordination decision. It must not read PhysX or joint state, call an EAI
controller, produce a velocity tensor, or claim physical success.

## Tested system algorithms

The implementation paths below are the tested algorithm locations. Their
classification is recorded in `algorithm/system/coordination/registry.py` and
their paper/checkpoint provenance is recorded in
`algorithm/heterocoord/registry.py`:

| System role | Canonical implementation path |
|---|---|
| LLM discussion and allocation | `algorithm/system/coordination/emos/` |
| LLM decomposition, MIQP allocation, replanning | `algorithm/system/coordination/TeamWeaver/` |
| centralized PEFA assignment | `algorithm/system/coordination/coherent/` |
| Fisher market allocation | `algorithm/system/coordination/fmc_ata/` |
| capability auction | `algorithm/system/coordination/bt_capabilities/` |
| deterministic heterogeneous MRTA | `algorithm/system/coordination/mrta_heuristic/` |
| Hybrid SUSD market/search | `algorithm/system/coordination/hybrid_susd/` |
| checkpoint decision adapters | `algorithm/system/coordination/dcmrta/`, `algorithm/system/coordination/heteromrta/` |
| benchmark decision/provenance bridge | `algorithm/heterocoord/` |

All listed paper adapters now live in the canonical
`algorithm.system.coordination` namespace. There are no top-level compatibility
copies for system algorithms.

## Boundary

```text
task package + robot roster + symbolic state + feedback
                         |
                         v
              system algorithm adapter
                         |
                         v
       AlgorithmDecision / task DAG / replan event
                         |
                         v
                 task.TaskRunner
```

The runner passes assignments to a skill executor. A task is only physically
successful when the EAI-owned evaluator supplies evidence.
