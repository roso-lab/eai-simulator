# Scenario generation tools

[中文说明](README.zh-CN.md)

`tools/scenario_generation/` is an optional authoring and admission layer for
concrete simulator scenarios. It is not a system algorithm, not a skill
provider, and not the task runner.

## Responsibility

This directory may be used to:

- generate or compile scenario JSON from registered scene templates;
- sample reproducible instance families and condition variants;
- select registered robots, attachments, targets, and payload policies;
- validate references, prerequisite DAGs, capabilities, geometry, and
  occupancy-map connectivity;
- produce robot dossiers, capability profiles, hashes, and admission reports;
- run optional settle or execution smoke checks for a generated scenario.

The output is a scenario candidate or an admission/evidence record. A passing
offline check does not mean that a robot completed a task in Isaac Sim.

## Position in HeteroCoord

```text
scenario_generation (optional scenario authoring)
                 ↓
task/config (scene-independent task contract)
                 ↓
simulator.py + EAI (scene and robot creation)
                 ↓
algorithm/system (planning and allocation)
                 ↓
task/executors (Skill/EAI execution)
                 ↓
task/evaluators (physical milestone evidence)
```

For the current fixed Fire Rescue track, use the saved EAI environment and the
task contract directly. Scenario generation is not required unless a new scene,
task instance, perturbation, or seed family is being authored.

## Boundary rules

Scenario-generation code must not:

- create a second Isaac application or replace `simulator.py`;
- drive controllers or call skills as part of an algorithm adapter;
- decide benchmark success or write physical success claims;
- embed USD paths, Prim handles, or controller internals in task contracts;
- make `task/runner.py` depend on optional generation modules.

`task/config/*.json` describes what must be done. A generated scenario describes
one concrete scene/instance in which that contract can be exercised. The two
formats must not be silently substituted for one another.

## Useful entry points

The following commands are optional and should be run from the repository root:

```bash
# Inspect registered templates or manifests without starting Isaac Sim.
python tools/scenario_generation/generate_factory.py --schema
python tools/scenario_generation/generate_family.py --manifest

# Validate a concrete scenario offline.
python tools/scenario_generation/validate_factory.py --schema-only <scenario.json>
python tools/scenario_generation/validate_factory.py <scenario.json>

# Generate a deterministic instance family.
python tools/scenario_generation/generate_family.py \
  --skeleton TRN-01 --seed 17 --out-dir tmp/generated_scenarios/trn01

# Build a pool and optionally perform headless settle checks.
python tools/scenario_generation/batch_validate.py \
  --pool tmp/validated_pool/example --skeleton TRN-01 \
  --seeds 17 23 --dry-run
```

Commands that start Isaac Sim, access external assets, or use an LLM are
integration workflows. Run them only with the intended `env_isaaclab`, asset
provider, and credentials. Do not commit generated scenarios, downloaded USD,
model weights, reports, or caches unless a workflow explicitly designates them
as tracked fixtures.

## Evidence vocabulary

Keep these labels separate:

```text
placement_validated       schema/semantic/geometry checks passed
feasibility_validated     deterministic route/capability oracle passed
spawn_validated           Isaac scene instantiated and settled
execution_validated       selected execution contract completed
physical_task_success     task evaluator accepted physical milestones
```

The last label belongs to `task/evaluators/`, not this directory. Kinematic or
offline transport checks must be reported with their limitations and must not
be presented as grasp, contact, payload, or physical-success evidence.

## Maintenance policy

This directory contains reusable EAI authoring utilities, including some
legacy scene-family and asset-audit scripts. Keep them out of the critical
Fire Rescue execution path unless their input schema and dependencies are
explicitly verified. When a script targets a removed demo or obsolete scene
format, archive or remove it instead of adding compatibility code to
`task/runner.py`.

For task execution, start with [task/README.md](../../task/README.md). For
repository and asset checks, use [tools/validation](../validation/README.md).
