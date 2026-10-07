# HeteroCoord baseline registry

`registry.py` records the six algorithms that are actually columns in the
latest paper's Table 3/4 and the additional user-requested candidates.
It stores source URLs and commits, implementation status, checkpoint/training
access, supported dimensions and the EAI physical-executor boundary.

The six primary columns are FMC-ATA, BT capabilities, Gosrich et al.,
HARL:HAPPO, Qrainbow and EMOS. COHERENT, MRTA heuristic, Hybrid SUSD, LiP-LLM
and Robust Online Epistemic Replanning are additional candidates and must be
reported separately. The existing TeamWeaver EAI integration is also recorded
as an extra condition, not as a HeteroCoord Table 3/4 baseline. DCMRTA and
HeteroMRTA are retained as official-checkpoint decision-only candidates with
separate fixed-dispatch Fire Rescue tracks. Missing official
code or learned weights remain `N/A`; they are never replaced with a different
algorithm or an unrecorded self-trained policy. The LiP-LLM and epistemic
wrappers in this workspace are inspection fixtures only and are not reported as
reproductions.

## Layer classification

Every record in `registry.py` carries `layer="system"` plus explicit `role`,
`input_contract`, `output_contract`, and executor ownership. Motion and control
modules are intentionally absent from this provenance table; they are skill
providers listed in `algorithm/skills/registry.py`. The paper comparison stays
focused on high-level coordination while every condition can reuse the same
EAI executor and skill stack.
# Fire Rescue typed coordination boundary

The benchmark monitors a submitted system; it does not make EMOS, TeamWeaver,
COHERENT or FMC-ATA compete inside one scene. Each system receives its own
independent run on the same registered contract/base instance. The
`FireRescueCoordinationBridge` records submitted decisions and EAI evidence.
Its `plan()` method is a legacy adapter diagnostic and is never a fallback for
the neutral benchmark path.

`fire_rescue_bridge.py` is the benchmark-facing seam for the public-source
conditions selected for the HeteroCoord reproduction:

- `BT-capabilities` runs a capability/utility auction and emits typed
  assignments. `algorithm.system.coordination.bt_capabilities.fire_rescue_policy:assign` is the
  stateless callback used by the existing Fire Rescue runner.
- `COHERENT` keeps the proposal stage and its deterministic Fire Rescue
  capability matching. `algorithm.system.coordination.coherent.fire_rescue_policy:assign`
  remains the callback entry point.
- `FMC-ATA` is explicitly `N/A` for the current Fire Rescue colours because
  they are indivisible physical skills. The adapter can only be evaluated when
  a task definition supplies an explicit divisible workload decomposition;
  `algorithm.system.coordination.fmc_ata.fire_rescue_policy:assign` uses the scenario's
  declared default mapping so the EAI execution/feedback path can still run;
  the ledger records `allocation_provider=fixed_default_assignment` and keeps
  the FMC-ATA allocation result itself as `N/A`.
- `MRTA heuristic` follows the public ICRA 2024/T-RO 2025 market/heuristic
  allocator and its delay-repair equivalent. It is runnable with the Z1 scene.
- `Hybrid SUSD MRTA` follows the public ICRA 2023 market-initialization and
  SUSD allocation stages. It is runnable with the Z1 scene; native failure
  replanning remains `N/A`, while adapter-level reallocation is recorded
  separately.
- `DCMRTA` retains an official-checkpoint native-decision track. Its Fire
  Rescue full-flow track uses the declared default mapping because the native
  10–20-agent/20–50-task coalition input distribution does not match EAI.
- `HeteroMRTA` follows the same two-track rule. Its native 3–5-species/15–50-task
  checkpoint is decision-only; Fire Rescue uses the declared default mapping,
  with cross-domain generalization reported as `N/A`.

The bridge records algorithm plans, the mapping actually dispatched to EAI,
execution feedback, evaluator-owned milestone evidence, and a provenance
manifest. An assignment or a successful planner return never marks the mission
successful. Only the Fire Rescue evaluator's `mission_success` milestone can
provide physical success evidence. Pass `--coordination-ledger-dir DIR` to the
Fire Rescue demo to persist `coordination_run.json` for each trial.
