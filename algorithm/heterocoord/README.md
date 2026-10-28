# HeteroCoord provenance registry

`registry.py` records the exact ordered six result columns in paper
`0ffd470fecdf5b4bdad82dbd9c23c439198b43d3` plus separately labelled
candidates. It is a provenance and implementation-condition inventory, not a
runtime readiness registry. Every record therefore carries
`published_runtime_readiness="not_established_by_registry"`.

The six primary entries are FMC-ATA, BT capabilities, SADCHER,
HARL:HAPPO, TeamWeaver adapted to EAI, and EMOS, in that order. Qrainbow remains
an additional literature candidate; it is not renamed or silently reused as
TeamWeaver. Missing source, integration code, model, information rights, runtime
qualification, or weights stays explicit `N/A`; the registry never substitutes
a different algorithm, an unrecorded self-trained policy, or a fixed dispatch
and labels it as the original method.

## Paper implementation register

`PRIMARY_SYSTEM_COLUMNS` is the sole ordered-column API. Each registry row also
records source/code/model and adapter versions, cognition/training/runtime
configuration, supplied decomposition, task and disturbance scope, comparison
condition, information-rights group, adaptation components, actor/action
encoding, semantic/world models, solver, replanning, and budget accounting.
`validate_implementation_register()` rejects a missing field or primary-roster
drift. `primary_condition_record()` resolves the complete runtime system,
comparison-condition, and information-rights identity; arbitrary diagnostic
identities do not become paper-rankable.

The current register intentionally leaves unresolved information rights and
unqualified runtimes as `N/A`. A complete six-row structural roster can only
pass the formal baseline gate when each row supplies independent physical and
artifact qualification evidence; the registry itself never supplies it.

## TeamWeaver boundary

The TeamWeaver column names the EAI-adapted implementation. Its uniform adapter
does not modify `algorithm/system/coordination/TeamWeaver/`. Non-empty history
reconstruction and task graphs with `prerequisites` remain fail-closed, and
computation/message budget binding plus full physical runtime qualification
remain pending. The adapter therefore establishes bounded connectivity, not
paper-wide or physical qualification.

## HARL-HAPPO boundary

The official HARL source revision is recorded for provenance, but this tree has
no `algorithm/harl_isaac/` integration path and no verifiable official HAPPO
weights. Consequently HARL-HAPPO is `N/A` for current EAI execution and official
weight evaluation. Any future EAI adapter and any locally trained checkpoint
must be registered as a new, separately identified condition.

## Layer classification

Every record carries `layer="system"` plus `role`, `input_contract`,
`output_contract`, and executor/evaluator ownership. System algorithms emit
`AlgorithmDecision`; `task.executors` and the EAI runtime own execution, while
`task.evaluators` alone own physical success. Motion/control providers belong
in the skill registry, not this table.

## Historical scenario bridge

`fire_rescue_bridge.py` is retained as a scenario-bound adapter and ledger
fixture. It can record submitted decisions, dispatch feedback, evaluator-owned
milestones, and provenance. Its `plan()` method is a legacy diagnostic and is
never a neutral-runner fallback.

Earlier short runs of some adapters against a local Z1 scenario are historical
diagnostic evidence only. Their temporary task/scene inputs are not a published
task package, are not present under `task/config/`, and do not establish current
cross-scene or published runtime readiness. A future measured diagnostic must
supply its scenario under ignored `tmp/`, bind the exact runtime/plugin identity,
and preserve external executor/evaluator ownership.

Fixed-default scenario dispatches for FMC-ATA, DCMRTA, or HeteroMRTA exercise
only the execution/ledger seam. They do not validate the native algorithm on
that scenario, demonstrate checkpoint generalization, or create physical
success. Those unsupported dimensions remain `N/A`.
