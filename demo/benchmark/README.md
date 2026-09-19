# Benchmark applications / 场景公共运行层

> 交付状态总览（已完成 / 现在可用 / 待开发被阻断）见
> [`DELIVERY.zh-CN.md`](DELIVERY.zh-CN.md)；阶段 1–18 权威勾选记录见
> [`TODO.zh-CN.md`](TODO.zh-CN.md)。本文描述模块契约与运行边界。

The independent applications are [Factory](../factory_benchmark/README.zh-CN.md),
[Warehouse](../warehouse_benchmark/README.zh-CN.md) and
[Modern House](../modern_house_benchmark/README.zh-CN.md).
Each owns `config.py`, `scenario.py` and maintained `scenarios/*.json` files. The
`business_logistics_01.json` and `home_recovery_01.json` files are the reviewed
business challenge inputs; `navigation_smoke.json` remains an explicitly named
smoke fixture.
The old `tools/scenario_generation/execute_episode.py` command delegates to
`demo/benchmark/runtime.py`; imports remain compatible. Generation, admission,
protocol analysis and physical calibration remain reusable tools. Scene task
blueprints, task profiles and asset allowlists are read from the individual demos.
Warehouse and Modern House manifest builders/validators live in their respective
`manifest.py`; `tools/scenario_generation/warehouse_benchmark.py` preserves the old
exports. Shared scope and certificate helpers live in `demo/benchmark/protocol.py`.
`scheduling.py` owns atomic robot/object/shared-resource reservations and
simulation-time node deadlines, conditional recovery groups, and skipped recovery
branches after normal success. `allocation.py` compares fixed, greedy and bounded
exact assignments with shared eligibility and candidate-evaluation budgets. Ordered
phases can reuse robots; `same_team_as` keeps consecutive roles together.
`compile_assignment_plan()` freezes those choices into the V2 phase graph.
Requirements can also declare `required_evidence_tier` and measured radar axes;
configurations below those gates are rejected before assignment.
`capability_profile.py` exports configuration-level radar data while keeping
unmeasured axes unknown. `observations.py` records producer, sensor configuration,
coordinate frame, validity and explicit consumers; navigation hover alone is not an
observation.

The author-side task contract and review are in
[`TASK_DESIGN_REVIEW.zh-CN.md`](TASK_DESIGN_REVIEW.zh-CN.md) and `authoring.py`.
They define what a task-writing model may regenerate, what is fixed by the
benchmark, and which claims remain unmeasured. Warehouse and Modern House are
currently fixed business DAG tracks with allocation and coordination decisions;
they are not autonomous order or household-item planning evaluations.

Evidence scope, checked against the implementation on 2026-09-17: Warehouse v8
and Modern House v6 completed full kinematic Isaac episodes under the same final
runtime provenance. They validate bounded motion, camera windows, logical object
ownership and measured joint-formation constraints. They do not certify complete
tasks through live domain perception, physical transport or stair traversal.
Historical runs remain scoped to their frozen inputs and sources. See the detailed
[task design and evidence boundary](BENCHMARK_DESIGN.zh-CN.md).

The independent goal-driven main track is documented in
[`GOAL_DRIVEN_TRACK.zh-CN.md`](GOAL_DRIVEN_TRACK.zh-CN.md). It publishes
outcome-only public tasks and a v2 participant API; the existing fixed DAG
packages remain available as `fixed_dag_diagnostic` compatibility fixtures.
Its author-only generation brief, semantic witness review, cross-mobility
counterfactual certificate, and one-factor treatment validator are task-design
evidence only; execution and scoring remain external release gates.

`python -m demo.benchmark.goal_suite list` lists the four task-generation
registrations. `goal_suite audit --output-root <new-directory>` generates and
reads back the three reviewed scene families across all nine factor conditions,
freezes their contents, and checks 21 control/variant pairs. Supply
`--city-package <city-prepare-directory> --verify-sources` to include the fourth
scene as a hash-verified, explicitly blocked preparation candidate. This does
not activate simulator scenes or city service skills. `goal_suite verify
--output-root <directory> --verify-sources` repeats the audit from disk.
See [the generation and handoff workflow](GOAL_SUITE.zh-CN.md) for source
verification, reserved splits, city service proposals, and remaining owners.

`goal_session.py` adds a separate stateful admission ledger for the v2 goal API.
`goal_navigation_probe` connects one author diagnostic dispatch to an actual
Warehouse navigation run and a hash-bound backend receipt. It does not bind the
order-task regions or certify physical control, business outcomes, or scores.
See [the deployment diagnostic workflow](GOAL_DEPLOYMENT.zh-CN.md).

`python -m demo.benchmark.goal_scene_binding audit --output-dir <new-directory>`
inventories the three goal scenes against existing authoring records and local
USD/map evidence. It preserves unbound identities and parameter differences;
neither a matching name nor a file hash promotes a candidate to a scene binding.
See [the author-only binding workflow](GOAL_SCENE_BINDING.zh-CN.md).

`goal_deployment audit --city-package <prepare-directory> --output-dir <new-directory>`
compiles all four scenes into author-only configurations with frozen object values,
distinct robot identities and source-bound spatial candidates. `goal_deployment
verify <report>` rebuilds these configurations. `goal_deployment_conformance`
exercises occupancy hold/release through the stateful reliable-skill reference host;
it does not apply native entities or certify physical/business outcomes.
See [four-scene configuration and reference execution](GOAL_DEPLOYMENT_CONFIGURATION.zh-CN.md).

`goal_physical_probe` applies native scene objects and independent robot instances
in a serial Isaac session. `goal_physical_review` audits native identity/time/device
evidence; `goal_object_readback_review` separately checks object source, mass, mesh
dimensions and fixed frames. `goal_attachment_probe` exercises an explicitly
separate ideal FixedJoint microdiagnostic. These tools never confer business or
scoring readiness. See [physical diagnostics and evidence limits](GOAL_PHYSICAL.zh-CN.md).
The [stage 12 evidence review](STAGE12_REVIEW.zh-CN.md) records the serial four-scene
diagnostics, corrected House floor offsets, fixed-point follow/release readback,
actual visual reviews and concrete remaining physical blockers.

The node counts below describe the fixed-DAG diagnostic fixtures. The goal-driven
main track exposes business outcomes and public capabilities; participants author
their own task graphs and allocations.

| Scene | Reviewed task skeleton and purpose | Current evaluation limit |
| --- | --- | --- |
| Factory | `aerial_view → ground_approach → ground_view → route_confirmation`: turn inspection evidence into assembly/dock station dispatch, using a label on a scene box | The participant controls only dispatch; traffic scheduling and sustained station coverage are separate task families. This does not evaluate production restart or hazard isolation. |
| Warehouse NVD | 10 nodes: rack scan, two-sided access, joint crate transfer, carton consolidation, dock approach, dispatch, post-dispatch scan, handoff hold and aisle closeout | Preserve the specified order identity/quantity and handoff chain. `wooden_crate_a` and `cardbox_c` are native prop declarations; live SKU/dock evidence and physical carrying remain pending. Priority-order selection and pre-dispatch authorization are not evaluated. |
| Modern House NVD | 11 nodes: entry baseline, furniture access/reposition/review, specified books staging, review positioning, two robots withdrawing, final room scan and hold | Restore the entrance-to-living-area path while placing the armchair and books appropriately. `mh_armchair` and `mh_bookstack` are native prop declarations; live identity/access-path evidence and physical manipulation remain pending. This is not unknown-item search or general resident-safety certification. |

Scans, transfers and waits serve these scene-specific outcomes. A declared native
prop does not establish successful USD instantiation or physical interaction.
`business_rules.task_skeleton` is the public regeneration scaffold: it preserves
reviewed roles, object types, causal dependencies, factor vocabulary and claim
limits. An authoring LLM may vary eligible fleets and admitted layouts within that
scaffold; a tested participant receives an instantiated task. Author-side
generation checks do not establish physical feasibility or autonomous decomposition.

## Challenge boundary

`challenge_contract.py` defines the pure-Python publication boundary used by
third-party benchmark participants. A challenge is split into four artifacts:

- `public_task_spec`: scene/fleet, objectives, resources, visible observations,
  allowed actions and scoring declarations;
- `participant_api`: ordered event and action envelopes for allocation,
  navigation, waiting, reassignment and message consumption;
- `hidden_evaluator`: evaluator-only truth sources, interventions, success
  predicates, provenance and release-QA requirements;
- `baseline_solver`: optional reference policies that consume only participant-
  visible inputs and are never normative task answers.

Configuration-level E6 records remain release QA and reference-execution evidence.
They are not participant admission requirements. `write_challenge_bundle()` writes
public artifacts below `release/` and hidden evaluator state below `evaluator/` so
an evaluator package is not accidentally published as part of the participant kit.
`participant_runtime.py` validates and logs ordered event/action turns. A task exposes only
the subset of the global action vocabulary implemented by that runtime; undeclared payload
fields and task-disabled action types fail closed.

Those four publication artifacts are distinct from the **four input categories
given to a tested model on the three business tracks**:

1. `release/public_task.json`: scene, public fleet/interfaces, targets, objective
   DAG, resource constraints, business rules and task skeleton. Warehouse/House
   objects have opaque public labels; their identity bindings remain private.
2. `release/participant_api.json`: legal action/event envelopes, controlled
   objectives and implementation status for the selected runtime.
3. `factor_manifest`, embedded in `public_task.json`: factor names, levels,
   evidence tracks and single-factor comparison rules; no future private schedule.
4. Runtime observations, state and lifecycle events actually delivered by the
   current simulation time; API vocabulary alone does not enable an event.

Factory F-INS allows `navigate_goal`, `wait` and `consume_message` on
`route_confirmation`. Its first three evidence phases are benchmark-managed.
Warehouse/House Isaac participants may `allocate`, `reassign`, `navigate_goal`
and `consume_message` within the supplied DAG; transport destinations stay fixed
and carried objects cannot be reassigned before release. Captured observations
pass through the deterministic communication queue, and dependent objectives wait
until every required delivered message is explicitly consumed. One delivered
observation may be consumed once by each declared downstream objective. A runnable
`wait` skill node is different from a participant `wait` action; the latter remains
limited to the separate `protocol_components_no_motion` harness.

The benchmark fixes the assets, initial fleet, business decomposition, object-role
bindings, targets, thresholds and budgets, and manages scheduling, motion baselines,
evidence collection and scoring. Models submit permitted `participant_action`
envelopes; optional explanations do not affect the machine score. They do not
rewrite the DAG or certify outcomes. The hidden evaluator retains identity/marker
truth, future fault and message schedules, reserve pools, reference assignments
and independent detector/dock/access-path checks. Neither self-reported success
nor a supplied pose, identity or service receipt substitutes for trusted evidence.

Lifecycle support is explicitly tiered. Scenario schema and public-task projection
support disjoint initial and preloaded-reserve inventories; reserve identities stay
out of `initial_fleet`. `member_join` and versioned `capability_change` enter the
participant stream only after injected Isaac callbacks return matching articulation
or controller/version readback. `MultiRobotDirectEnv` now supplies the shared binding:
inactive reserves are pinned at their authored parking pose, and the same versioned
capability declaration gates controller output. Setting `navigation_interface=false`
suppresses primary and auxiliary control before the callback can report success.
Attachment, sensor and payload changes require a separate scene adapter and are
rejected by this binding.
This activates an existing articulation and does not claim dynamic spawn. The
maintained scenarios now have author-private map-bound lifecycle plans compiled
by `demo.benchmark.lifecycle_plan.build_maintained_lifecycle_plan()`. The plans
cover a reserve parking pose, a join rendezvous, an exit replacement and a
navigation capability-change event, and record the occupancy-map hash used for
the offline check. They are not included in `public_task.json`; a changed map
or USD product must be recompiled rather than reusing an old point. Physical
reference join/capability episodes remain pending. `member_exit` uses
an already-loaded ground replacement and measured poses where the runtime contract
permits it. With a live environment binding, the exiting agent's command gate is
closed and read back before the event is published; this still needs a physical
reference episode. F-INS dispatch does not accept faults. Communication delay, loss,
staleness and bandwidth now govern Warehouse/House participant observation delivery
and consumption, with evaluator-only schedules and deterministic replay. They remain
a benchmark transport model rather than a measured radio model.

`domain_adapters.py` is the boundary for live scene evidence. The runtime binds
the live Isaac environment, actual observation store, occupancy map, and public
task packet, then calls the adapter immediately after every captured frame.
Detector output is batch-validated and attached to that same observation before
trusted objective-completion events are forwarded to stateful providers and
final identity, service, and access claims are collected. The detector frame
hook does not receive the Isaac environment or private domain contract. The private evaluator checks
those records against hidden identity and service truth. If the adapter is not
connected, `domain_evidence.adapter_status` stays `pending_live_scene_adapter`.
The injectable collector accepts `identity_checks`, `domain_service_events` and
`access_paths`; default Warehouse/House runs have no live SKU/household detector,
dock service or access-path sampler. `live_claims_recorded` means claims were
collected, not accepted by the evaluator. Invalid collector output rolls back the
batch and records `collector_error`; a frame-hook error remains sticky and blocks
final claim ingestion. Scheduler completion or process exit code
zero cannot certify business success; inspect `business_task_success` and the
independent domain predicates separately from motion execution.

`reference_domain_providers.py` supplies strict Warehouse and Modern House
composition helpers over injected backends. It contains no detector weights,
dock state source, object-pose sampler, or house path implementation; those live
scene components are still release blockers.

`scene_challenge.py` is the reusable offline compiler for non-Factory demos. It
consumes a scene-owned V2 scenario and emits the same four artifacts while
keeping the scene blueprint and task profile in the owning demo. Warehouse and
Modern House expose domain, mobility and configured attachment interfaces in the
public fleet without inventing payload or manipulation evidence. Their runtime
accepts participant allocation/reassignment for navigation, aerial-scan, wait,
and kinematic transport objectives. Transport routes are rebuilt from live robot
and object poses; a carried object cannot be reassigned until release. Joint
transport uses separate footprint-aware pickup and delivery slots and a progress
watchdog for each member. These are idealized ownership and route checks, not
physical grasp or formation-control evidence.

Generated Warehouse and Modern House tasks pass schema, 2-D placement,
connectivity and shared-executor plan construction before publication. Warehouse
uses a rack/aisle/consolidation/dock logistics DAG; Modern House uses an
entry/furniture/room-review DAG. These are distinct scene-owned business graphs,
not renamed copies of one shared answer template.

Navigation to a declared target completes using the evaluator's zone-visit
predicate, with the exact target retained as the terminal waypoint. Visiting a
zone does not require remaining there afterward. Legged policy tracking projects
velocity into the body frame; Pepper instead reads its moving `base_link` and
drives virtual joints in the fixed root frame. Reports identify these state sources
and save sampled world poses, including arrival and terminal states, for audit.

Aerial navigation uses the existing 2-D A* waypoints with a world-height position
command for each segment; scans continuously resend the hover goal. It now has a
3-D admission gate in `aerial_navigation.py`: before the first flight command and
after reassignment/replanning, the executor checks the route from the measured
pose through every waypoint and the final hover point. Every actual position
command is checked again, including when a waypoint was consumed before reaching
its planned altitude. Continuous swept boxes prevent thin obstacles between
sample points from being missed. The body box uses the declared planning radius
on all three axes plus a 0.10 m admission margin; this is a conservative protocol
envelope, not a measured aerodynamic clearance guarantee.

Warehouse and Modern House declare local voxel products in their demo configs.
Their maintained business templates use Iris geometry and interfaces. The NVD
demos default to a bounded 0.6 m/s kinematic aerial baseline; the provider
position controller remains selectable as a separate diagnostic. Factory retains
its separately validated CF2X capture fixture.
The reader respects positive-world-Y voxel rows, 0.25 m height layers, the NVD
5×5 XY aerial collision proxies, and the ground-map walls extruded to 0.8 m
(conservatively rounded upward to a voxel layer). Missing/invalid products or any
body extent outside the volume yield `unknown` and reject flight. Factory also
declares a voxel product and metadata sidecar. Its
`tools/assets/build_factory_voxel_product.py` rasterizes world AABBs of enabled
USD collision meshes and records root-USD/map/product hashes and parameters.
That is a conservative 3-D product, not a substitute made from a 2-D occupancy map.
Declarations and local files do not establish geometry completeness, immutable
provider publication or live flight validation; missing/invalid products and
failed metadata validation still reject admission. Ground navigation is unaffected.

Reports include `aerial_route_validation`, admission failures, and per-control-step
measured swept-volume violations. The executor includes current task-object boxes
in these checks and binds the voxel/map bytes to runtime provenance; changing a
product invalidates the evidence. When a route or command is blocked,
`plan_local_aerial_detour()` tries a bounded set of local intermediate points and
applies the same continuous swept-AABB gate to each candidate. If none is admitted,
flight is rejected. This **continuous swept-AABB + conservative voxel** mechanism
is only a simple benchmark-managed motion baseline. Kinematic NVD motion does not
establish flight dynamics, actuator limits or disturbance rejection. The mechanism
does not provide a complete
3-D planner, predict moving obstacles, certify concurrent robot clearance or source
geometry completeness, or establish RL tracking or flight safety. Motion-baseline
failure must be reported separately from heterogeneous-team decisions. Live Isaac
flight validation remains required.

Use `python tools/scenario_generation/export_capability_profiles.py --output <report.json>`
or `--format html` to export the current 14-type coverage view. The export does not rank
robots and leaves every unmeasured radar axis unknown.

`record_configuration_microtests.py` derives v2 checks from frozen scenario and
batch-attempt files. Profile export revalidates the sources and recomputes the
record rather than trusting a supplied E6 label. Single protocol passes appear in
`protocol_checks` with raw metrics; they do not assign a normalized radar score.
Factory navigation/capture requirements use exact configuration and task-scope
checks, while recognition and information-service axes still need their own
measurements. Legacy v1 records remain historical data and cannot satisfy this
admission boundary. Supplied failed attempts stay in the conversion ledger.

## Runtime boundary

```text
demo/<scene>_benchmark/main.py
  -> isolated, read-only USD/map audit
  -> SimulatorLaunchConfig(scenario_file=..., num_envs=1)
  -> simulator.open_simulator_session (normal asset preflight)
  -> env_builder: scene + robots + unscaled attachments + scenario entities
  -> demo/benchmark/runtime.py: mass/inertia audit, control, evaluation
```

The asset audit uses a subprocess. Importing standalone `pxr` in the launcher
process before Isaac starts can mix incompatible OpenUSD libraries. `--check-assets`
is a standalone offline command and never starts Isaac. Wrong-scene episodes fail
before asset checks or application launch.

## USD layout and migration

```text
usd/scene/factory/factory.usd                 + SubUSDs/materials/textures/map
usd/scene/warehouse_nvd/warehouse_nvd_bench.usd + occupancy maps/voxels/bench_meta
usd/scene/modern_house_nvd/modern_house_nvd_bench.usd + occupancy map/bench_meta
usd/assets/nvd/<provider package>/...         referenced USD, MDL and textures
```

All active file references are relative to their owning USD/MDL file. The NVD
dependency tree retains the provider hierarchy and contains only reachable files,
including nonselected variants, payloads, sublayers, asset arrays, time samples,
UDIM tiles and literal MDL resources/imports. Both wrapper stages declare metres
and Z up; their visual reference transforms retain the existing 0.01 cm-to-m scale.
Neither robot geometry nor arm mass/inertia is reduced by this scene conversion.
Modern House preparation disables `/World/BenchCollision/zz_mezzanine_slab`:
the old horizontal-surface bounding box authored a 100.584 by 113.329 metre solid
slab at z=0.996..1.110 over the entire scene. It collides with a full-size mounted
Z1 during ground navigation and does not establish a real walkable floor. The
ground plate and 518 ground obstacle proxies remain enabled. Elevated navigation
is unsupported until a surface-specific collision mesh is validated. This physics
correction changes the scene hash; prior execution reports must be regenerated.

`EAI_USD_ROOT` can relocate the entire `usd/` tree. NVD scene configs and occupancy
maps use this same root. Copy the whole dependency closure, not only the root USD.
Bare Isaac MDL modules (OmniPBR, OmniGlass, OmniSurface, OmniSurfacePresets,
`nvidia/core_definitions`, and MDL standard modules) remain runtime dependencies;
their availability is verified by an actual Isaac launch, not by the offline audit.
Deleted/ordered composition list items are not file dependencies and retain their
original semantics. This is a file closure audit, not an MDL compiler, renderer,
or arbitrary script/resource-network audit; computed MDL resource paths fail closed.

Local migration from previously installed wrappers, before relocating them:

```bash
python tools/assets/relocate_usd_closure.py \
  usd/scene/factory/factory.usd \
  usd/scene/warehouse_nvd/warehouse_nvd_bench.usd \
  usd/scene/modern_house_nvd/modern_house_nvd_bench.usd \
  --source-root <NVD_download_root> --destination usd/assets/nvd \
  --aliases tools/assets/benchmark_asset_repairs.json \
  --backup tmp/benchmark_migration/originals --report tmp/benchmark_migration/migration.json
python -m demo.warehouse_benchmark.prepare --backup tmp/benchmark_migration/warehouse_metadata.usd
python -m demo.modern_house_benchmark.prepare --backup tmp/benchmark_migration/house_metadata.usd
```

The importer checks missing dependencies and destination conflicts before copying,
backs up modified local layers, preserves external source packages, rewrites USD
with the OpenUSD API, and records installed hashes and repairs. It refuses to
overwrite an existing imported file. Already-local layers can be audited without
reimporting. Case corrections, renamed Roxana metal textures, and the old rack
Omniverse URL are mapped to supplied local assets; no replacement geometry or
synthetic material is generated. The repair manifest is specific to these source
packages and should not be reused blindly with another provider revision.

```bash
python tools/assets/audit_usd_closure.py usd/scene/factory/factory.usd \
  usd/scene/warehouse_nvd/warehouse_nvd_bench.usd \
  usd/scene/modern_house_nvd/modern_house_nvd_bench.usd \
  --strict --hashes --json tmp/benchmark_migration/closure.json
```

These commands migrate an existing installation; they do not reconstruct missing
benchmark collision proxies or occupancy maps from a clean checkout. Production
USD, maps, textures and generated closure reports remain ignored runtime assets.
For another machine, transfer the complete local USD bundle or publish a licensed,
pinned provider bundle first. No provider release is implied by this local change.
Optional scenario props resolved by `EAI_NVD_ASSET_ROOT` are separate from the base
scene closure; using a new task asset requires importing and auditing that asset too.

## Physics and evidence / 物理证据边界

`provenance.py` captures scenario bytes, maintained execution sources and requested
control/render/wall-time options before launch. Before task control, it separately
hashes the selected local USD dependency closure (including materials/textures),
controller assets, loaded repository/controller Python files and occupancy map,
and records the plan and effective physics/control/render periods. Missing local
dependencies or files changing during execution invalidate evidence. Runtime MDL
modules, driver binaries and GPU nondeterminism remain outside this file identity
contract; this is not a full simulator-state snapshot or replay certificate.

With `--report path.json`, `path.request.json` and `path.runtime.json` preserve the
pre-launch and pre-control snapshots. Batch execution uses distinct paths for every
attempt, binds its report digest to these snapshots and never backfills an old
report with current sources. The E6 importer additionally checks current local
files and the unscaled mass audit. Protocols freeze execution sources; their
summary checks the frozen version rather than silently accepting another evaluator.
Legacy reports/protocols remain history and require new runs/freezes for current
evidence. Requested render periods are explicit experimental conditions; native
sensor rates and recognition performance are not inferred from them.

运行前请求与控制前资产快照分别保留，批量重试不会覆盖旧报告；导入、跳过重跑和协议
汇总使用相同的绑定校验。源码/场景/渲染条件不一致或缺少指纹的旧结果不能继承当前等级。
这些检查不证明目标识别、原生传感器频率、完整状态重放或物理操作能力。

After every control step the shared executor checks articulation and rigid-object
root states for NaN/Inf. The first invalid state terminates the episode as
`physics_state_invalid`, records the entity and components using JSON-safe strings,
and prevents the corrupted trajectory from entering task scoring. This diagnoses
simulation failure; it does not identify contact-force safety or certify stability.

All V2 benchmark attachments retain mass, inertia and geometry scale 1.0, with a
per-link runtime audit before execution. Matching source USD inertials establishes
asset agreement, not equivalence to measured hardware. The smoke fixtures exercise
navigation with an unscaled arm. Scripted transport is still explicitly kinematic.
Joint transport assigns separate pickup and delivery slots using each member's
footprint, reserves the other member's terminal slot, and avoids the solid object
during approach. Participant assignment rebuilds these slots from live poses.
Each member has its own bounded distance/heading progress watchdog; an already
reached slot is rechecked before pickup/release. This prevents a shared centre
goal from becoming a permanent robot obstruction. It does not establish rigid
formation tracking or swept-load clearance: the idealized carried object follows
the members' midpoint and is placed at the delivery target on release.
This migration establishes no new E7 manipulation, rated payload, grasp-force,
collision-margin, wind-resistance or load-dynamics result. See
[physical calibration](../../tools/scenario_generation/PHYSICAL_CALIBRATION.md).

Factory's three maintained 2-D tasks retain their temporary stair/ramp surface
exclusions. They are not a general prohibition on legged climbing. The
[vertical probe protocol](VERTICAL_TRAVERSAL.md) defines morphology-specific
probe admission, continuous support-relative health and measured XYZ completion,
with a read-only CLI for historical Go2 reports. Explicit vertical metadata and
the candidate `F-VERT-01`/`main_stair` probe open a separate entry for upper-workcell
access. Ordinary ground nodes, including those in mixed DAGs, keep their exclusions.
An open entry or `admitted_for_probe=true` is not a successful climb: current
admission declarations retain `execution_validated=false`.

`tools/scenario_generation/vertical_task.py` is a benchmark-managed candidate
executor, not a participant stair-control API. It samples after `env.step`, uses
`physics_dt` for contact-force conversion and control `step_dt` for trajectory/event
time, and stops without calibrated support/contact evidence. It must not substitute
target height or teleportation for measured support, full WXYZ posture, healthy
dwell and XYZ arrival. `scenarios/vertical_stair_01.json` and the separate
`demo.factory_benchmark.vertical_challenge` compiler now describe a candidate
F-VERT-01 bundle; the original `challenge.py` remains dispatch-specific. The OpenUSD
measurement binds the root layer plus 67 dependency hashes and finds two authored
flights. A private `--stair-measurement` input selects the ground-to-mezzanine flight
without exposing that internal flight ID in release artifacts. The production scan is
spatially cropped around the stair and took 10.13 seconds while preserving the prior
flight/support result. Approach, entry and goal coordinates remain explicitly
unmeasured because authored polygons are not PhysX support or connectivity evidence, and both
`release_gate.executable` and `release_gate.scoreable` are `false`.

This single-robot infrastructure diagnostic is separate from the three business
tracks. It validates only `navigate_goal` route intent and bounded `wait` for the
supplied robot, with no joint commands or pose writes. Its API reports
`executed_action_types=[]`; the Isaac intent adapter is pending. It currently
supplies task/API declarations without live event inputs or the business-track
`factor_manifest`; lifecycle and communication variations are disabled. Support
calibration, reference trajectories and independent physical acceptance remain
private and pending. Compiling the candidate is not a deployed, scored or physically
validated stair challenge. No successful stair traversal is claimed here.

## Lightweight checks

The NVD ground product uses map_server PNG rows (maximum world Y first), whereas
the internal mask, voxel volume and generated collision walls increase in world Y.
Before using a rebuilt map, check both local scenes with
`python -m tools.assets.rebuild_nvd_products --scene warehouse_nvd --check-map-proxies`
(repeat with `modern_house_nvd`) and `--check-ground`. The map check compares every
cell against the generated USD walls. `--repair-map-orientation` only repairs an
exact proven vertical reflection, saving the original bytes and hashes under
`tmp/nvd_map_orientation_backups/`; other mismatches fail. A full `--apply` rebuild
writes the correct orientation and a static Plane floor. These local products
still require provider publication and do not certify geometric completeness.
`python -m tools.scenario_generation.search_nvd_origin --scene warehouse_nvd`
rechecks candidate layouts against the corrected map. Current development origins
are Warehouse (-10, -11.75) and Modern House (17, 1.5); they are not blind-test data.

For quadruped ground routes, a nearby intermediate waypoint can be skipped only
when the entire replacement segment crosses passable cells of the inflated map,
including boundary contacts. This avoids demanding 5 cm cell-centre stabilization
from a velocity policy while preserving obstacle clearance and terminal predicates.
It does not certify moving-obstacle avoidance.

An interrupted joint carry can satisfy a declared recovery branch only with
ordered pickup, forced release, matching member exit, route reconstruction from
the drop position, replacement pickup, confirmed delivery to the original goal,
and node completion. The report names recovered nodes separately; scheduler
completion alone cannot certify object recovery or a physical grasp.
Released dynamic objects may settle or slide. Pickup therefore records the live
object position and measured robot distance, rejects out-of-range attachment,
and checks the declared kinematic interaction range rather than assuming a fixed
drop coordinate. This interaction range is not measured arm reachability.

`camera_validation.py` checks observation-window provenance and measured positions
for runtime verdicts and configuration micro-test conversion. Ground captures must
stay in their declared target region at frame and trajectory sample times; aerial
captures must match their hover event. The recorded position is the robot-base world
proxy, not calibrated camera extrinsics or a target estimate. Recovered partial
windows retain their own checks and require the separate recovery validation.

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONPATH=source/EAI:source/EAI_assets:source/EAI_env_diy \
  python -m pytest -q source/EAI/test/test_usd_closure.py source/EAI/test/test_benchmark_demos.py
```

真实场景检查和 smoke 命令见各 demo README。迁移报告、资产原件备份、启动日志和
实验结果均保存在 `tmp/benchmark_migration/`，不作为源代码提交。

## Suite release audit

Compile and audit the three business packages plus the Factory stair diagnostic:

```bash
python -m demo.benchmark.release_manifest \
  --output tmp/benchmark_release/release-manifest.json
```

The audit validates every public/API/baseline/hidden hash binding and reports a
stage checklist.  Current packages intentionally remain `release_status=pending`
and `execution_validated=false`: they are contract-review artifacts until the
listed provider, live-domain, lifecycle/factor and reference-execution blockers
are closed.  The maintained Chinese phase checklist is
[`TODO.zh-CN.md`](TODO.zh-CN.md).
