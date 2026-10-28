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

## System algorithm inventory

The paths below include native adapters and historical implementations. Their
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
| SADCHER network scheduling and matching | `algorithm/system/coordination/sadcher/` |
| RobotFleet DAG/allocation/feedback boundary | `algorithm/system/coordination/robot_fleet/` |
| LaMMA-P dependency ordering and skill allocation | `algorithm/system/coordination/lammap/` |
| HARL/HAPPO checkpoint boundary | `algorithm/system/coordination/harl/` |
| deterministic heterogeneous MRTA | `algorithm/system/coordination/mrta_heuristic/` |
| Hybrid SUSD market/search | `algorithm/system/coordination/hybrid_susd/` |
| checkpoint decision adapter | `algorithm/system/coordination/dcmrta/` |
| benchmark decision/provenance bridge | `algorithm/heterocoord/` |

The original-algorithm entry point is `benchmark/native_algorithms.py`, with
frozen sources in `algorithm/heterocoord/paper_algorithms.json` and per-method
`native_adapter.py` files. It covers FMC-ATA, BT capabilities, SADCHER,
HARL:HAPPO, TeamWeaver and EMOS. Runtime acceptance is recorded separately in
`algorithm/heterocoord/NATIVE_ALGORITHM_ADAPTATION_REPORT_20261028.zh-CN.md`.
`benchmark/external_algorithms.py` is a historical diagnostic interface; its
rewritten/fixed policies are not eligible as original paper methods.
HeteroMRTA is retained in historical provenance records but is excluded from
the current adaptation and execution matrix.

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
