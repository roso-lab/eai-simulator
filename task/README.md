# HeteroCoord task layer

`task/` is the benchmark and experiment layer built on top of the EAI core. It
contains task contracts, task manifests, orchestration, replay hooks, and
evidence boundaries. It does not contain USD assets, Isaac Kit extensions, a
Gym registration, or robot controllers. Low-level planning/control providers
are classified under `algorithm/skills/`; EAI-backed execution is injected
through `task/executors/`.

The dependency direction is:

```text
task manifest + robot roster
          -> algorithm adapter
          -> task runner
          -> injected EAI SkillExecutor / simulator session
          -> independent evaluator and append-only ledger
```

The runner can be unit-tested without Isaac Sim. Load the static task JSON and
call `EaiTaskPackage.bind_runtime(...)` after the EAI session provides the
active robot roster. A plan or assignment is a
decision record only. When the EAI executor or evaluator is not injected, the
run is reported as `N/A` with `physical_evidence_required`.

## Directory contract

```text
task/
├── config/       task packages; no scene assets
├── contracts.py   dependency-light public records
├── runner.py      composition and lifecycle boundary
├── fixtures/      symbolic adapter-test inputs, never physical evidence
├── replay/        future checkpoint/branch adapters
├── executors/     future EAI skill bindings
├── evaluators/    future physical-evidence readers
└── tests/         simulator-free contract tests
```

System algorithms stay under `algorithm/system/` and emit decisions through the
task contracts. EMOS and TeamWeaver are tested implementations under
`algorithm/system/coordination/`; they do not have top-level compatibility
copies. Skill providers stay under `algorithm/skills/` and emit `SkillPlan`
records. EAI owns
`SimulatorLaunchConfig`, controller application, physical skills, and Isaac
lifecycle. Assets remain provider-managed; `usd/` is intentionally not a source
tree for this project.

## Core launcher

The basic simulator now requires an explicit saved environment:

```bash
python simulator.py --env EAI-Factory-Z1-v0 --headless
```

The standalone `source/EAI_env_diy` Kit extension and its `--diy-3d` entry point
are not part of HeteroCoord. Use a maintained JSON environment under
`source/EAI_hmrs/EAI_hmrs/envs/`, or add a task-owned scenario provider without
reintroducing an EAI UI extension.
