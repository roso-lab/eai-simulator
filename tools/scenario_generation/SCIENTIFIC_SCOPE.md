# Scientific Scope and Evidence Tiers

This directory is a reproducible scenario-generation and validation prototype.
It is useful now for controlled engineering experiments on the registered
Factory scene. It is not yet sufficient for claims about general multi-robot
intelligence, physical manipulation, or sim-to-real transfer.

PARTNR is a useful design reference because it combines generated instructions
with simulation-grounded evaluation, but its published benchmark spans 100,000
tasks, 60 environments, and 5,819 objects. This prototype has one scene, a
small authored skeleton set, and no held-out scene split; the two should not be
compared as datasets. See [PARTNR (ICLR 2025)](https://arxiv.org/abs/2411.00081).

## Evidence tiers

| Tier | What it establishes | What it does not establish |
| --- | --- | --- |
| `placement_validated` | Schema, semantic references, footprint placement, 2-D connectivity, deterministic route existence, and non-trivial goal predicates passed. | Asset loading, runtime stability, physical feasibility, policy performance. |
| `spawn_validated` | The selected Isaac configuration started and settled under zero robot commands within the configured drift tolerance. | Navigation, manipulation, collision avoidance, task completion. |
| `navigation_execution_validated` | This executor's assigned robots reached the declared navigation goals, its state evaluator observed the goal predicates, and its bounded no-progress watchdog did not fire. Ordinary contact is permitted. | Optimal planning, generalization, contact-force safety, or perception robustness. |
| `kinematic_execution_validated` | The preceding navigation evidence plus logical transport completion under an explicit root-pose override. | Grasping, arm IK, contacts, load limits, object dynamics, physical transport. |
| future `physical_execution_validated` | Requires a documented physical manipulation stack and measured success under contact dynamics. | Generalization beyond the tested distribution. |

The [standalone Z1 calibration](PHYSICAL_CALIBRATION.md) can establish scoped
component E7 evidence for prepositioned contact grasp/lift/transfer/release. It
does not establish the future scene-level physical execution tier above. Scenario
benchmarks preserve arm mass and inertia and require a runtime source audit;
unscaled asset agreement does not establish hardware equivalence.

The offline feasibility report is an existence certificate from a 2-D inflated
grid. It is not a performance oracle and must not be named or counted as an
empirical execution success.

## What is currently scientifically feasible

With fixed versions of the Factory map, assets, controller bundles, seeds, and
the exact scenario JSON, the pipeline can support reproducible studies of:

- rejection rates and failure modes of constrained scenario generation;
- deterministic route feasibility under declared robot footprints and barriers;
- performance of a specified allocation or navigation planner on a fixed,
  disclosed distribution; and
- paired changes that alter only a declared family delta, provided execution
  evidence is retained per instance.

Each claim must state the evidence tier, controller/model versions, seed set,
timeouts, hardware/physics configuration, and excluded instances. `tmp/` is
runtime data, not a durable benchmark release.

## What blocks a publishable benchmark claim

1. **Construct validity.** The current skeletons encode a small, authored task
   taxonomy. Pool generation now publishes an exact capability/payload
   set-cover certificate and labels single-robot counterexamples explicitly,
   but the current skill vocabulary still does not establish timing,
   route-aware, or cooperative-manipulation necessity. A prescribed
   multi-robot allocation is not proof of cooperation.
2. **Internal validity.** The execution harness permits contact and detects
   sustained no-progress deadlocks; it does not classify task-safe versus
   unsafe contacts or measure contact forces. Its kinematic transport also
   bypasses physical manipulation. Those outcomes cannot be used as physical-
   task success labels.
3. **External validity.** One Factory layout and registered assets provide no
   scene-level generalization test. A held-out scene/geometry split is needed
   before reporting general performance.
4. **Statistical conclusion validity.** A few successful episodes or seeds
   are demonstrations, not effect estimates. Report per-instance outcomes,
   confidence intervals over predeclared independent seeds, and failures;
   never replace failed runs with only successful examples.
5. **Reproducibility.** Release frozen episodes, map/asset hashes, generator
   configuration, controller versions, reports, and evaluation code. Retain
   unsuccessful reports as part of the dataset provenance.

## Minimum protocol for the next research phase

1. Pre-register task axes and hypotheses (for example morphology, payload,
   temporal dependency, and true concurrent allocation), then create a fixed
   train/dev/test split by scene and family rather than by episode alone.
2. Provide non-LLM and LLM baselines for allocation and planning; use the same
   observation/action interfaces and compute budget.
3. Evaluate success, partial completion, makespan, distance/energy proxy,
   deadlock count, intervention/replan count, wall time, and failure class.
   Record contacts separately only when the task defines which contacts are
   unsafe; do not treat ordinary task contact as failure.
4. Replace kinematic transport with a physical grasp-and-release stack
   (IK, task-appropriate clearance checks, contact/stability checks, and payload tests), or
   keep it as a separately named symbolic/kinematic baseline.

The planned intermediate tier is `idealized_manipulation_execution`: real
base motion plus collision-free docking and arm trajectories, followed by a
deterministic end-effector/object constraint. It may abstract contact closure,
friction, and slip, but must retain tool compatibility, reach, collision,
payload/stability limits, carried-object collision, and release settling. It
must not be reported as physical grasp validation. Until that tier exists,
mass-sweep kinematic reports explicitly state that mass does not affect
execution.
5. Automate a provenance manifest that links every aggregate number to exact
   per-instance reports and configuration hashes.

## Current split and aggregation safeguard

`benchmark_protocol.py` now freezes a reproducible *within-Factory* split for
settled pool records.  It assigns whole connected components of `family_id` and
`layout_signature` to one side, so paired conditions and accidental duplicate
layouts cannot leak from development into test.  The report only counts an
execution attempt if its recorded scenario SHA-256 still matches the frozen
instance bytes; changed or missing inputs are explicitly incomplete evidence.
Test evidence must also be timestamped on or after `frozen_at_utc`; earlier
reports remain audit history, but cannot be reused as blind-test outcomes.

```bash
# Creates a new file and refuses to overwrite it.
python tools/scenario_generation/benchmark_protocol.py freeze \
  --pool tmp/validated_pool/factory \
  --output tmp/validated_pool/factory/protocol_v2.json \
  --split-seed 20260911 --test-fraction 0.25

# Run a declared split, then summarize only matching per-instance evidence.
python tools/scenario_generation/batch_execute.py --pool tmp/validated_pool/factory \
  --protocol tmp/validated_pool/factory/protocol_v2.json --split development \
  --transport-mode auto
python tools/scenario_generation/benchmark_protocol.py summarize \
  --pool tmp/validated_pool/factory \
  --protocol tmp/validated_pool/factory/protocol_v2.json --split test
```

This prevents episode-level leakage, but does not transform one Factory layout
into a scene-generalization benchmark.  Navigation and kinematic execution
tiers remain separate in every aggregate.

Until those requirements are met, describe this work as a **validated scenario
generation and execution-harness prototype**, not as a benchmark that measures
general physical multi-robot collaboration.
