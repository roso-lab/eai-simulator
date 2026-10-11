# HeteroCoord provenance registry

`registry.py` records the six algorithms that are columns in the source paper
Table 3/4 plus separately labelled candidates. It is a provenance inventory,
not a runtime readiness registry. Every record therefore carries
`published_runtime_readiness="not_established_by_registry"`.

The six primary entries are FMC-ATA, BT capabilities, Gosrich et al.,
HARL:HAPPO, Qrainbow, and EMOS. Other entries remain separate candidates or an
existing EAI extra. Missing source, integration code, or weights stays explicit
`N/A`; the registry never substitutes a different algorithm, an unrecorded
self-trained policy, or a fixed dispatch and labels it as the original method.

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
