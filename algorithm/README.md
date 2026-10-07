# Algorithm and skill layers

`algorithm/` has two different responsibilities and they must not be mixed:

1. `algorithm/system/` classifies paper-level system algorithms. They decide
   task decomposition, heterogeneous allocation, coordination, communication,
   and replanning.
2. `algorithm/skills/` classifies low-level providers. They turn one assigned
   task into a path, trajectory, controller command, or ROS transport event.

The system layer answers **what the team should do**. The skill layer answers
**how one robot executes an already assigned part**. EAI owns the controller
contract and simulator stepping; `task/executors/` connects a skill plan to EAI.

## Canonical structure

```text
algorithm/
├── system/
│   └── coordination/
│       ├── emos/             # tested EMOS system algorithm
│       ├── TeamWeaver/       # tested TeamWeaver system algorithm
│       ├── coherent/         # tested COHERENT adapter
│       ├── fmc_ata/          # tested FMC-ATA adapter
│       ├── dcmrta/           # tested DCMRTA adapter
│       ├── heteromrta/       # tested HeteroMRTA adapter
│       ├── hybrid_susd/       # tested Hybrid-SUSD adapter
│       ├── mrta_heuristic/   # tested MRTA heuristic adapter
│       ├── bt_capabilities/  # tested capability-auction adapter
│       └── registry.py       # paper/system adapter classification
├── skills/
│   ├── contracts.py          # dependency-free SkillRequest/SkillPlan
│   ├── registry.py           # navigation and external-IO skill inventory
│   ├── navigation/           # navigation provider upload point
│   └── external_io/          # Nav2 and keyboard provider upload point
├── common_coordination/       # task/system contracts without Isaac imports
└── heterocoord/               # provenance and benchmark decision bridge
```

EMOS and TeamWeaver are tested system-algorithm implementations, not
compatibility copies. Their only supported import paths are
`algorithm.system.coordination.emos` and
`algorithm.system.coordination.TeamWeaver`. Navigation, Nav2, and keyboard
providers are currently absent from this branch and are reserved for the
dedicated Skill implementation. Their contracts and registry slots remain in
`algorithm/skills/`; they are not paper-level coordination algorithms.

## System algorithm boundary

System adapters may consume:

- `task.EaiTaskPackage` and its task graph;
- the active robot capability roster;
- symbolic world state and execution feedback; and
- the append-only coordination ledger.

They return `AlgorithmDecision`, task DAGs, allocations, or replanning events.
They must not create an Isaac application, access PhysX/joint handles, call an
EAI skill, emit controller action tensors, or write physical success.

The system registry is in `algorithm/system/coordination/registry.py`. The
paper/provenance registry in `algorithm/heterocoord/registry.py` records source
and checkpoint status separately, including explicit `N/A` conditions.

## Skill boundary

Skill providers accept a `SkillRequest` after allocation and return a
`SkillPlan` containing executor-facing `SkillCommand` records. They may do
map/path planning, collision-free trajectory generation, controller command
translation, or ROS/Nav2 transport. They do not decide task ownership and do
not claim that a physical milestone was reached.

The dependency-free contract is in `algorithm/skills/contracts.py`. Existing
providers are classified in `algorithm/skills/registry.py`:

| Skill provider | Planned provider path | Role |
|---|---|---|
| global 2D navigation | `algorithm/skills/navigation/global_planner/` | path and velocity generation |
| db-CBS navigation | `algorithm/skills/navigation/multi_robot_navigation/` | multi-robot trajectory and collision avoidance |
| Nav2 | `algorithm/skills/external_io/nav2/` | system-ROS goal/transport path |
| keyboard | `algorithm/skills/external_io/keyboard/` | manual velocity transport |
| EMOS internal skills | `algorithm/system/coordination/emos/brain/skills/` | EAI/Torch-dependent runtime used internally by EMOS; not a separate system algorithm |

`source/EAI/EAI/controllers/` remains the only controller/action application
contract. `task/executors/` owns the adapter from `SkillPlan` to that contract,
and `task/evaluators/` owns physical evidence.

## Decision-to-execution flow

```text
task package + roster + symbolic state
                  |
                  v
          system algorithm adapter
                  |
                  v
       AlgorithmDecision / Assignment
                  |
                  v
          task executor -> SkillRequest
                  |
                  v
            skill provider -> SkillPlan
                  |
                  v
       EAI controller and simulator step
                  |
                  v
       feedback -> evaluator -> physical verdict
```

```python
from algorithm.skills import SkillRequest
from task.contracts import EaiTaskPackage, TaskRunRequest
from task.runner import TaskRunner

package = EaiTaskPackage.from_json("task/config/fire_rescue.json")
result = TaskRunner().run(TaskRunRequest(package), decision_provider)
```

`TaskRunner` reports `N/A / physical_evidence_required` when an EAI executor or
evaluator is not injected. Do not add a second simulator launcher inside any
algorithm package. Do not put USD, downloaded checkpoints, runtime ledgers, or
experiment output under `algorithm/`.
