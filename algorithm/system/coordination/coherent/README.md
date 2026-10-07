# COHERENT → EAI adapter

This directory contains a dependency-free PEFA adapter for the released
COHERENT algorithm.  It preserves the paper's centralized
Proposal–Execution–Feedback–Adjustment loop while replacing OmniGibson graph
state and fixed action tables with EAI typed `TaskSpec`, `SkillSpec`, and
`CapabilityProfile` records.

It is intentionally an **adapted implementation**, not a claim of zero-change
COHERENT reproduction.  Isaac Sim, ROS, and LLM clients stay outside this
module.  An EAI integration supplies an executor that maps the emitted
`ActionCommand.skill` to a typed controller and reports a `Feedback` record
after validating the physical milestone.

The released method is serial PEFA.  The adapter therefore emits one action at
a time and reports the following dimensions explicitly as unsupported unless a
separately registered extension is used: communication delay/loss (D2),
member-exit recovery (D4), matched transfer (D5), and parallel joint actions.
The Fire Rescue fixture is a pure-Python contract test; its successful run is
not an Isaac physics result.

`fire_rescue_policy:assign` is a deterministic bridge for the existing EAI
`module:assign` hook. It performs the proposal, capability validation, and
one-to-one allocation stage while the EAI runtime retains navigation,
manipulation, milestone verification, and termination ownership. Load it
through the task-layer adapter with:

```text
algorithm.system.coordination.coherent.fire_rescue_policy:assign
```

Because the host `module:assign` contract returns a round-level robot-to-subtask
mapping, this is an **assignment-level adapted condition**. It does not replace
the Fire Rescue loop with the original per-physics-step OmniGibson PEFA action
loop. A full serial action claim requires a separately registered
executor/milestone callback condition.

No API key is read or stored here.  A production LLM planner may be injected
through the `planner` callback.  Record model ID, prompt revision, temperature,
token budget, and API provenance in the surrounding benchmark run manifest.
