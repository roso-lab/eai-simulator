# Scenario Generation

[Chinese](README.zh-CN.md)

Offline validation and optional LLM generation of Factory V2 scenario JSON for
`python simulator.py --scenario-file`. The V2 contract keeps the Factory stage
itself immutable: the model selects registered robots, attachments, audited
procedural overlay entities (boxes, beams, markers), ground-floor targets, and a
prerequisite-based phase DAG. It never sees or emits USD paths, prim paths, or
controller source paths.

This is benchmark infrastructure, not yet a publication-ready benchmark. Read
[Scientific scope](SCIENTIFIC_SCOPE.md) before interpreting a label or comparing
methods.

## Contract

The three scene applications now live under `demo/factory_benchmark`,
`demo/warehouse_benchmark` and `demo/modern_house_benchmark`. Each owns its task
blueprints, experiment profile, asset allowlist and maintained navigation smoke
episode. See [demo setup and asset migration](../../demo/benchmark/README.md).
`execute_episode.py` remains a compatibility command for the shared demo runtime;
robots and attachments still load through `simulator.py`.

- `source/EAI/EAI/scenario_spec/` owns the JSON Schema, semantic validation
  (IDs, references, DAG, capabilities, physics bounds), the ground-floor
  occupancy-map geometry gate, and the connectivity gate (static barriers are
  rasterized before a flood fill, so a placement set that seals a route is
  rejected).
- `source/EAI/EAI/scenario_spec/templates.py` is the scene template layer: the
  factory stage plus semantic zones and anchors deterministically derived from
  the occupancy map. `operators.py` is the V3 layer on top: semantic task
  specs (robots/entities/targets by zone), a seeded sampler that resolves
  anchors and compiles frozen V2 episodes, per-condition deltas, and the
  TRN-01/SAR-01/COV-03 skeleton recipes.
- `source/EAI_assets/EAI_assets/scenario_assets.py` is the audited asset
  registry; `source/EAI_hmrs/EAI_hmrs/scenario_builder.py` compiles validated
  entities into `RigidObjectCfg`/`AssetBaseCfg` under
  `{ENV_REGEX_NS}/scenario_<id>` (direct child of the env root, like the robots — spawn paths under a not-yet-existing intermediate prim are rejected by the environment cloner).
- Saved environment JSON stays version 1 unless it is a full validated V2
  payload; mixing `composition`/`phase_graph` into a V1 file is rejected.
- Local gates are authoritative. Structured Outputs only shape the model
  response; a scenario is accepted after schema, semantic, geometry, and
  connectivity validation pass, and it is only `placement_validated` — not an
  execution result. Kinematic entities are moving barriers (rasterized,
  excluded from connectivity checkpoints); navigating to a static entity uses
  approach semantics (nearest passable cell).
- `source/EAI/EAI/scenario_spec/oracle.py` is the deterministic Feasibility
  oracle (existence certificate, distinct from the Performance oracle and from
  probes): every phase leg is solved with A* on a per-robot inflated occupancy
  grid, so a route exists only when the agent's full disk fits along it — a
  narrow-doorway dead layout is rejected offline, without RL. It also emits a
  capability/payload set-cover certificate (`minimum_team_size`,
  `minimum_teams`, `single_agent_feasible`, `forced_cooperation`). A robot
  being indispensable is not by itself cooperation: only a minimum team size
  above one earns `forced_cooperation`. Arm-level 3-D collision-free checks (CuRobo) are a
  staged addition: the report carries an explicit `arm_collision_check:
  not_installed` state instead of silently skipping them; once CuRobo is
  installed in a GPU environment, UR5/Z1 transport legs get IK plus
  collision-free trajectory solving inside headless Isaac, and only then may
  instances be labelled beyond `feasibility_validated`.
- Instance families are the Suite operator's data face: one base placement
  materialized under per-condition deltas, so paired conditions share anchors,
  poses, robots, and phase graph exactly, and differ only in declared axes
  (for example an H2 payload-mass sweep).

## Commands

Configuration evidence can be derived without starting Isaac from a frozen
scenario and a `batch_execute` attempt object:

```bash
python tools/scenario_generation/record_configuration_microtests.py \
  --scenario <scenario.json> --attempt <attempt.json> --output <records.json>
```

Repeat `--attempt` for multiple runs of that frozen input. If using a pool,
export the selected instance's `execution` object as the attempt file first.
The current converter accepts Factory navigation/camera episodes without fault
interventions or a manipulation payload policy. It checks current execution
sources, assets, dossier identity and the mass audit; older unverifiable inputs
remain rejected with a reason. It binds its own converter/protocol source hashes
to every v2 record. Profiles recompute records from those files before admission.
Keep the input and attempt files beside the results; deleting or editing them
invalidates the record. A protocol pass retains raw metrics and task scope,
without a normalized radar score. The output includes a ledger of all supplied
attempts; it is not itself a preregistered failure denominator.
Factory inventory detection records retain identified-frame counts, observed marker
IDs and fused localization error derived by replaying the source attempt. Profiles
admit only an exact configuration and scope match; navigation evidence cannot satisfy
a detection protocol, and neither protocol check fills a radar axis.

Capability profile v2 also reports configuration-bound manipulator deployment
evidence for all 14 connected robot types. The 42-row Env DIY matrix remains
separate from retained Kinova Gen3 candidate-only evidence. Stationary mount
fixtures and fixed-base component E7 records cannot populate radar scores or
admit dynamic mobile manipulation. Rated payload, hardware grasp force,
collision safety margin, wind resistance, and load dynamics remain unknown.

Camera checks share the runtime's `demo/benchmark/camera_validation.py`: capture
positions and sampled window poses must remain in the declared observation region;
aerial frames must match the measured hover position, goal and duration. The runtime
records robot-base world-position proxies, not calibrated camera extrinsics. A valid
window does not prove target visibility, identification or continuous containment.
Optional `aerial_scan.scan_height_m` and `scan_yaw_rad` specify absolute world Z and
yaw. Generation/compilation preserve them and microtest scope binds both; requested
yaw also requires measured heading evidence. The executor accepts heading requests
only for the paired Iris/Pegasus position controllers' native `goal_yaw` interface.

`batch_execute.py --archive-camera-frames` freezes the archive option and forwards it
to the demo executor. Each report gets a new adjacent directory of raw arrays, PNG
previews and parameters from its RGB render product. Simulator parameters do not
establish hardware camera calibration; the original base-position proxy remains
explicitly separate.

```bash
# Offline registry views (no API call, no Isaac)
python tools/scenario_generation/generate_factory.py --schema
python tools/scenario_generation/generate_factory.py --manifest
python tools/scenario_generation/generate_family.py --manifest

# Validate without Isaac Sim; full mode resolves the Factory occupancy map
python tools/scenario_generation/validate_factory.py --schema-only <scenario.json>
python tools/scenario_generation/validate_factory.py <scenario.json>

# Settle smoke: spawn the instance in Isaac, step zero commands, check drift
# headless needs enable_cameras (built into the tool: plain headless.kit cannot build the SDG pipeline)
python tools/scenario_generation/settle_smoke.py <scenario.json> --steps 50 --headless --device cpu

# Sampler mode: skeleton recipe -> paired family (offline, no API)
python tools/scenario_generation/generate_family.py \
  --skeleton TRN-01 --seed 17 --out-dir tmp/generated_scenarios/trn01_demo

# Proposal mode: LLM proposes the semantic task spec, sampler owns geometry
OPENAI_API_KEY=... python tools/scenario_generation/generate_family.py \
  --instruction "Design a two-robot handoff transport across blocked zones" \
  --seed 17 --out-dir tmp/generated_scenarios/llm_demo

# Generate with the configured model, then run it
OPENAI_API_KEY=... python tools/scenario_generation/generate_factory.py \
  --instruction "Generate a transfer task for one wheeled and one legged robot" \
  --output tmp/generated_scenarios/demo.json
python simulator.py --scenario-file tmp/generated_scenarios/demo.json --num_envs 1
```

Generation re-validates every attempt locally and feeds structured errors back
for a bounded repair loop (`--attempts`, default 3, maximum 5). The output file
is created exclusively and never overwritten. `EAI_SCENARIO_MODEL` overrides the
default model.

## Requirements

`jsonschema`, `numpy`, `Pillow`, and `PyYAML` are required for validation and
geometry checks; generation additionally requires `openai` and a configured
`OPENAI_API_KEY`. The Factory occupancy map follows the standard asset resolver
environment variables and can be fetched on first use.

## Focused tests

```bash
PYTHONPATH=source/EAI:source/EAI_assets \
  python -m pytest -q source/EAI/test/test_scenario_spec.py source/EAI/test/test_scenario_family.py source/EAI/test/test_scenario_oracle.py
```

## Instance pools

`batch_validate.py` turns multi-seed families into a labelled pool. Each
instance passes the offline gate stack (`placement_validated`) and then a
headless Isaac settle subprocess (`spawn_validated`, zero-command drift
check). The pool directory keeps instance files, per-instance settle reports,
family records, and a `pool.json` manifest; reruns skip instances already
labelled spawn_validated.

```bash
# Dry run: generate + offline gates only
python tools/scenario_generation/batch_validate.py \
  --pool tmp/validated_pool/trn01_first --skeleton TRN-01 --arm ur5 \
  --seeds 17 23 31 --dry-run

# Collaboration datasets may reject every single-robot-feasible episode.
python tools/scenario_generation/batch_validate.py \
  --pool tmp/validated_pool/team_only --skeleton TRN-03 --arm ur5 \
  --seeds 17 23 31 --dry-run --require-team-necessary

# Real run: + headless settle per instance (~10 min each on CPU physics)
python tools/scenario_generation/batch_validate.py \
  --pool tmp/validated_pool/trn01_first --skeleton TRN-01 --arm ur5 \
  --seeds 17 23 31 --steps 50 --settle-timeout 1200 --device cpu
```

`--arm ur5` selects the m20+UR5 carrier variant of TRN-01 (the default
`--arm z1` recipe needs the Z1 controller bundle, which may not be cached
offline). TRN-01 is intentionally a single-carrier mass-sweep baseline, not a
collaboration task. Labels additionally distinguish `single_robot_feasible`
from `team_necessary`; `--require-team-necessary` rejects the former. An
optional complete `--payload-policy` JSON/file makes the gates mass-aware;
when enabled, omitted robot types are ineligible rather than unlimited.
`placement_validated` and `spawn_validated` do not imply execution success.
External instance files (e.g. LLM proposals) enter the same flow through
`--instances`, get the `ext_` prefix, and are settled with the ROS bridge off
(pure physics). Pool integrity features: per-family pairing diff (conditions
must differ only in declared delta fields), layout dedup signatures (same
signature across different families warns about accidental duplicates), and
`--list` prints the labelled manifest table. Settle notes: instances whose
robots carry sensor payloads need those USD assets cached; `navigation_io`
selections are rejected early because the `/clock` bridge requires a live ROS
environment.

### NVIDIA SimReady props (optional)

The registry can absorb NVIDIA SimReady prop libraries (NVD download). Audit
them, build the tracked registry, and expose the props through
`EAI_NVD_ASSET_ROOT`:

```bash
python -m demo  # placeholder
python tools/scenario_generation/audit_nvd_props.py \
  --roots <NVD_assets>/SimReady_Warehouse_01_NVD@10010 \
  --output tmp/nvd_audit/simready_props.json
python tools/scenario_generation/build_prop_registry.py \
  --audit tmp/nvd_audit/simready_props.json \
  --nvd-root <NVD_assets>
EAI_NVD_ASSET_ROOT=<NVD_assets_sanitized> python simulator.py --scenario-file <scenario.json>
```

Notes: SimReady props ship visual meshes only (no physics on disk) -- the
scenario builder injects rigid-body state and the registry's suggested mass at
spawn; dynamic (rigid-body) injection of these meshes currently crashes Kit,
so use them with `"mode": "visual"`. USD treats `@` as a path delimiter, so
create a sanitized shadow root (`@` renamed to `_v` in package directory
names) and point `EAI_NVD_ASSET_ROOT` there; ASCII paths are required
(non-ASCII download paths break USD file resolution).


## Phase-graph executor

`execute_episode.py` drives explicitly allocated `navigate` legs with
deterministic waypoint control. A* routes from the feasibility oracle are
tracked with proportional heading/velocity commands (the shared `[v, 0, wz]`
shape for differential-drive and velocity-RL controllers), and the runtime
evaluator records predicate trajectories and first-satisfied steps.

`transport_object` is deliberately opt-in. `--transport-mode kinematic` writes
the object's root pose above the carrier; it validates the control loop and
logical goal state only. It does **not** validate grasping, contacts, arm IK,
arm motion, payload limits, or any 8 kg/30 kg execution difference. The report
records `mass_affects_execution: false` and `manipulation_model:
scripted_root_pose`. Normal contact is allowed; a bounded watchdog reports a
deadlock only after sustained absence of waypoint completion and exhausted
replanning. A successful report is named
`navigation_execution_validated` or `kinematic_execution_validated`; it is not
called `oracle_validated`.

```bash
python tools/scenario_generation/execute_episode.py <navigation-scenario.json> \
  --report /tmp/exec.json

# Explicit non-physical transport harness.
python tools/scenario_generation/execute_episode.py <transport-scenario.json> \
  --transport-mode kinematic --report /tmp/exec.json

# Make the no-progress watchdog more or less patient (contacts remain allowed).
python tools/scenario_generation/execute_episode.py <navigation-scenario.json> \
  --stall-window 150 --max-replans 12 --report /tmp/exec.json
```

With the default automatic step budget, the executor reserves one full
`stall-window` for every allowed recovery cycle, so a sustained stall is
diagnosed as `deadlocked` before the outer step limit can mask it as merely
`steps_exhausted`. An explicit `--steps` intentionally overrides that reserve.

The executor refuses unallocated, unsupported, or partially executable DAGs
instead of scoring only their supported prefix. Recovery replans use the same
static/kinematic barrier map as the original feasibility check.

### Retained execution evidence

`batch_execute.py` executes only `spawn_validated` pool records by default and
adds the input SHA-256, executor configuration, report path/body, stdout/stderr
tails, and every failed attempt to `pool.json`. It adds an execution label only
after a successful process outcome, unscaled mass audit and matching provenance.
The shared `demo/benchmark/provenance.py` freezes scenario bytes, sources and
requested options before launch, then local USD/material/texture dependencies,
controller assets, loaded source files, map and effective timing before control.
Files changing during execution invalidate evidence. Each attempt retains distinct
report/request/runtime files and a report digest; current source hashes are never
backfilled into historical reports. `--render-period-s` and `--wall-timeout-s`
participate in the request fingerprint. The latter bounds task control only;
`--timeout` still bounds the entire subprocess. E6 import checks current local
files; protocol summaries check frozen execution sources. Runtime MDL modules and
driver binaries are not content-addressed, and this is not a replay certificate.

Before comparing methods, freeze a protocol split with
`benchmark_protocol.py freeze`.  It holds all paired conditions and duplicate
physical layouts together, and `summarize` rejects stale input evidence rather
than inheriting an old result.  This is a within-Factory family holdout, not a
scene-generalization split; see [Scientific scope](SCIENTIFIC_SCOPE.md).
When a frozen instance has a phase skill the current executor cannot support,
the batch record is explicitly `not_executable_by_current_harness`; it is not
counted as an Isaac runtime failure or included in runtime coverage.

```bash
# All settled navigation-only instances.
python tools/scenario_generation/batch_execute.py \
  --pool tmp/validated_pool/factory --device cpu

# One explicitly non-physical transport instance.
python tools/scenario_generation/batch_execute.py \
  --pool tmp/validated_pool/factory --ids trn01_s17_ur5_low \
  --transport-mode kinematic --device cpu

# Prefer this for a frozen study: batch execution selects and hash-checks only
# the declared development set.  Use --split test only for the final blind run.
python tools/scenario_generation/batch_execute.py \
  --pool tmp/validated_pool/factory --protocol tmp/validated_pool/factory/protocol_v2.json \
  --split development --transport-mode auto --device cpu
```

Benchmark Scenario V2 mounts now preserve original arm mass and inertia;
interactive simulator tuning is separate. Runtime reports include a per-link
mass/inertia audit, and new protocol freezes reject legacy unaudited execution
evidence. See [physical calibration](PHYSICAL_CALIBRATION.md) for manufacturer
references, the Z1 contact experiment, conditional load/clearance/wind screens,
and the boundaries of component E7 evidence.

The OpenUSD-only audit runs without Isaac application startup:

```bash
python tools/scenario_generation/audit_usd_physics.py \
  usd/robot/b2/b2_canonical.usdc --output tmp/physics_audit_v2/b2.json
```

Audit v2 reports SI mass, time-zero COM in the asset's stage frame, and inertia
about the aggregate COM. It converts stage units and USD row-vector rotations,
records source-layer hashes and authored-field provenance, and leaves missing
or invalid quantities unknown. Shape inference and mass scaling are not used.
The aggregate is pose-specific, and is not a hardware measurement.

`compose_static_mount.py` requires v2 reports and an explicit `--offset X Y Z`
and `--rotation` with nine row-major SO(3) entries. The transform maps the payload
asset stage into the base asset stage; a local mount offset alone does not
establish that mapping. Both tensors must be about their own COM. Identical
frame labels do not establish alignment. Version 1 inertia/COM compositions
are superseded because they mixed origin and COM inertia and omitted mount
rotation. Static results do not upgrade catalog deployment gates.

Focused checks using a Python environment with OpenUSD and NumPy:

```bash
PYTHONPATH=source/EAI:. python -m pytest -q +  tools/scenario_generation/test_audit_usd_physics.py +  tools/scenario_generation/test_compose_static_mount.py +  tools/scenario_generation/test_robot_dossier_physics.py
```
