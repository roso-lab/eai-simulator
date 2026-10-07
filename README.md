# HeteroCoord

HeteroCoord is a benchmark integration layer built on top of the EAI
Simulator. It evaluates heterogeneous multi-robot coordination algorithms in
Isaac Sim 5.1 while keeping paper-level planning, low-level skills, simulation,
and physical evidence separate.

The project is intended for reproducible evaluation, not for silently
retraining or modifying the original algorithms. If an algorithm has no
public implementation or verifiable official checkpoint, the corresponding
track is recorded as N/A rather than presented as a reproduction.

## Scope

HeteroCoord connects five layers:

~~~text
task contract
    → paper/system algorithm
    → EAI Skill/controller executor
    → independent physical evaluator
~~~

- **Task layer** describes task graphs, robot capabilities, branches, replay
  identifiers, and experiment metadata.
- **System algorithms** decide task decomposition, robot allocation,
  coordination, communication, and replanning.
- **Skills** produce paths, trajectories, controller commands, or ROS
  transport for an already assigned task.
- **EAI runtime** creates the scene, applies controller actions, steps Isaac
  Sim, and exposes physical state.
- **Evaluators** decide whether physical milestones were satisfied.

An algorithm decision, an exit code, or a replay operation is not physical
success. Only evaluator evidence can produce a physical success verdict.

## Repository layout

~~~text
simulator.py                 EAI launcher and reusable simulator session
source/
  EAI/                       controllers, environment, interfaces, ROS bridges
  EAI_assets/                asset requirements and provider resolution
  EAI_hmrs/                  saved environments and dynamic environment builder
algorithm/
  system/coordination/       paper-level coordination algorithms and adapters
  skills/                    low-level navigation and external-I/O providers
  common_coordination/       dependency-free algorithm/task contracts
  heterocoord/               provenance and benchmark-condition registry
task/
  config/                    scene-independent task packages
  contracts.py               public task/robot/decision records
  runner.py                  orchestration boundary; no Isaac app creation
  executors/                 Assignment → Skill/EAI execution (in progress)
  evaluators/                physical milestone evaluation (in progress)
  replay/                    checkpoint and branch adapters (in progress)
tools/
  scenario_generation/       optional concrete scenario authoring and admission
  validation/                repository, asset, and runtime quality gates
  setup/                     installation helpers
usd/                         maintained metadata; production assets are external
todolist.md                 current implementation and handoff checklist
~~~

## Algorithm boundary

System algorithms live under algorithm/system/coordination/. They may consume
the task graph, robot capability roster, symbolic world state, execution
feedback, and coordination ledger. They must return decisions or plans without:

- creating an Isaac application;
- reading PhysX or joint handles;
- calling an EAI Skill or controller;
- emitting controller action tensors;
- claiming physical task success.

Skill providers live under algorithm/skills/. They implement the execution
method for an assignment but do not decide task ownership. The adapter from a
SkillPlan to EAI controllers belongs in task/executors/.

## Current benchmark status

The task and coordination contracts are implemented and can be tested without
Isaac Sim. The following runtime pieces are deliberately separated and are
still being completed:

1. checkpoint restore and branch-aware scene binding;
2. DecisionContext for observations, events, feedback, and communication state;
3. assignment validation, capability matching, and prerequisite enforcement;
4. DAG phase scheduling and multi-robot parallel execution;
5. EAI Skill/controller executor;
6. physical milestone evaluator and evidence ledger integration;
7. deterministic replay and parallel-branch adapters.

Until these pieces are connected, a run without an injected executor/evaluator
must report:

~~~text
status = N/A
reason = physical_evidence_required
~~~

See [todolist.md](todolist.md) for the authoritative implementation order.

## Environment requirements

- Ubuntu 22.04;
- Isaac Sim 5.1;
- Isaac Lab 2.x with the env_isaaclab Conda environment;
- NVIDIA GPU for full simulator workflows;
- ROS2 Humble only when using ROS2 or Nav2 workflows;
- external EAI assets and any author-authorized model/checkpoint files.

Do not overwrite the existing Isaac Sim, Torch, ROS, or asset-cache
installation. HeteroCoord should be installed and tested in the existing
environment without adding untracked weights or USD files to the repository.

## Quick start

From the repository root:

~~~bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate env_isaaclab

python -m pip show EAI EAI_assets EAI_hmrs
python simulator.py --env EAI-Factory-Z1-v0 --headless
~~~

The first command is a package check. The simulator command is an integration
smoke test and may require locally available or provider-managed assets. Do not
run it merely to execute the dependency-free unit tests.

## Lightweight checks

These checks do not require launching Isaac Sim:

~~~bash
cd /home/pupil/HeteroCoord
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  /home/pupil/miniconda3/envs/env_isaaclab/bin/python \
  -m pytest -q task/tests algorithm/common_coordination/tests
~~~

The current contract suite passes four tests in the prepared environment.
Run the relevant scripts under [tools/validation](tools/validation/README.md)
after changing setup, assets, interfaces, or documentation.

## Recommended development order

1. Read [task/README.md](task/README.md) and
   [task/HANDOFF.zh-CN.md](task/HANDOFF.zh-CN.md).
2. Run the dependency-free contract tests.
3. Implement the executor, evaluator, and replay boundaries in task/.
4. Validate one saved EAI environment with a single Isaac Sim smoke test.
5. Connect one Fire Rescue task end to end.
6. Add checkpoint branches, parallel execution, repeated trials, and then
   cross-scene benchmark matrices.

For optional scenario authoring, read
[tools/scenario_generation/README.md](tools/scenario_generation/README.md).
For algorithm and Skill contracts, read
[algorithm/README.md](algorithm/README.md).

## Non-goals

- HeteroCoord does not train replacement policies by default.
- It does not infer physical success from an algorithm's allocation.
- It does not register a second Gym task or launch a second simulator.
- It does not commit downloaded USD assets, checkpoints, runtime reports, or
  caches as source code.
