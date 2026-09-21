# Modern House NVD benchmark demo

[中文](README.zh-CN.md) · [Delivery status overview (zh)](../benchmark/DELIVERY.zh-CN.md)

**Goal-track handoff (2026-09-20):** `MODERN_HOUSE-GOAL-01` uses the same
`GoalAlgorithmHost` / `GoalSession` interface and nine-condition authoring matrix
as all four scenes. Low-level skills follow a fixed benchmark policy; native
execution evidence remains a separate gate. See the [handoff guide](../benchmark/ALGORITHM_HANDOFF.zh-CN.md).

> **Status (2026-09-19, stage 18)** — Runnable: native detector/path backend
> readback (stage-18 v4, a two-snapshot diagnostic whose snapshots now bracket
> an author-side furniture intervention: paired-support armchair carry plus
> full ground withdrawal, with the closeout event injected through the public
> provider path before the after snapshot); offline measured region intervals
> (occupancy rays plus authored bench wall cuboids) with corroborated
> outside/threshold/inside doorways and spawn-membership checks; kinematic
> reference episode v6 (11/11 nodes); resident-path and bounded-withdrawal
> diagnostics; corrected spawn heights (v5, casters on the 0.25 m floor).
> Blocked: a formal before/after **business** witness — the intervention is an
> author diagnostic action, not participant-driven task execution.
> `release_status=pending`, `scoreable=false`.

The scene-owned `HouseNativeSceneDetector` ideal sensor and
`IsaacResidentPathBackend` now provide concrete session readback adapters.
See [native backend integration](NATIVE_BACKENDS.md) for the direct in-session
diagnostic, frame/step/source provenance, visibility limits and fail-closed
legacy-runtime boundary. No native execution or business success is implied.

The goal-track `live_path.py` measures a candidate resident polyline in the live
PhysX scene using forward/reverse box sweeps at 0.9 m width and 2.0 m height.
It includes idle robots and furniture, records a 1 mm vertical contact tolerance,
and separately samples floor support at intervals of at most 0.1 m. Sampled
support is not a continuous floor certificate or a completed business episode.
`goal_physical_probe` writes this evidence to `physical_report.json:resident_path`
and explicitly enables scene queries in headless mode. Disabled queries yield
unknown evidence, never free space. These goal-track thresholds are distinct
from the historical fixed-DAG task described below.

```bash
# env_isaaclab; serialize scene launches and use a fresh output directory.
python -m demo.benchmark.goal_physical_probe --scene modern_house_nvd \
  --steps 60 --gui --output-root tmp/house_resident_path
python -m demo.benchmark.goal_physical_probe --execute --output-root tmp/house_resident_path
```

Add `--house-withdrawal --steps 1200` during preparation for a bounded native
controller diagnostic: settle for 60 steps, move M20_1 and Iris to diagnostic
parking poses, and measure the same corridor again. It neither teleports roots
nor removes colliders. The report preserves commands and PhysX state. Parking
poses are author diagnostics, not certified public standby-region bindings.
Temporary robot obstruction describes participant strategy and current corridor
state; it is not an authoring rejection. Authoring checks initial layout,
mandatory-point consistency and existence of a feasible solution. Participants
need not use the diagnostic parking poses or withdrawal sequence. Object spawns
include the 0.25 m floor elevation. The earlier CPU withdrawal v4 placed the
bookstack inside the floor, so it validates checker response only, not business
completion.

For enabled axis-aligned static floor cubes, the witness also checks continuous
planar coverage, including gaps between ray samples. Only verified cuboids
entirely below the sole plane count as floor contacts; dynamic objects remain
obstacles. Geometry coverage and live support samples have separate provenance.
Direct GPU suppresses CPU actor-pose readback, so CPU scene queries can be stale.
The hook checks `is_readback_suppressed()` and reports unknown dynamic clearance
in that mode. Use an explicitly selected `--device cpu` author diagnostic for
synchronized queries; production device selection is not silently changed.
See [NVIDIA's limitation](https://nvidia-omniverse.github.io/PhysX/physx/5.5.1/docs/DirectGPUAPI.html).

[中文](README.zh-CN.md)

This application owns the modern_house_nvd task blueprint, asset allowlist and experiment profile in `scenario.py`. `config.py` selects its USD and occupancy map. `scenarios/home_recovery_01.json` is the default business challenge input; `navigation_smoke.json` is an explicitly named smoke fixture. Generated task pools and reports belong in `tmp/`.

From the repository root, in `env_isaaclab`:

```bash
python -m demo.modern_house_benchmark.main --check-assets
python -m demo.modern_house_benchmark.main --scenario demo/modern_house_benchmark/scenarios/navigation_smoke.json --gui --steps 400
python -m demo.modern_house_benchmark.main --scenario <episode.json> --headless --report tmp/modern_house_benchmark/execution.json
```

The smoke episode drives Carter with an unscaled Z1 through a short navigation leg. It checks assembly, arm inertials and navigation, not manipulation. The default physics device is CPU. Use `--transport-mode kinematic` only for explicitly labelled scripted transport experiments.

The public participant runtime controls allocation and reassignment for navigation, aerial-scan and kinematic-transport objectives: selected agents or teams get pickup/delivery routes rebuilt from live poses when the phase becomes ready. Object ownership changes only through runtime pickup/release events; reassignment is rejected while an object is carried, and a member exit drops a carried object at the carrier's position. Public fleet declarations expose domain, mobility and configured attachment interfaces while keeping rated payload unknown. Non-finite articulation or rigid-object root states fail closed as `physics_state_invalid`; this does not establish manipulation, collision-margin or payload evidence.

Each supplied V2 JSON must have `scene_key: modern_house_nvd`. Robots and attachments are built by `simulator.py` and `env_builder.py` through `open_simulator_session`, with the normal asset preflight. The shared executor lives in `demo/benchmark/runtime.py`; it does not create a second application lifecycle.

See [shared runtime and asset layout](../benchmark/README.md) for migration, preparation, physical evidence limits and asset distribution.

Generate a scene-owned task with:

```bash
python -m demo.modern_house_benchmark.challenge --generate-scenario tmp/modern_house_task.json
python -m demo.modern_house_benchmark.challenge \
  --scenario tmp/modern_house_task.json \
  --output-dir tmp/modern_house_benchmark/challenge
```

The first command writes the generated business scenario and its QA sidecar;
the second publishes the four-layer challenge bundle. Do not pass
`scenarios/navigation_smoke.json` to this publisher: it is a runtime assembly
fixture and the formal compiler rejects it as incomplete business authoring.
The public task includes a bounded regeneration skeleton with the reviewed
objective roles, native prop roles, factor axes, lifecycle and communication
boundaries.

Set `EAI_NVD_ASSET_ROOT` to the prepared native-prop root. Generation selects
`mh_armchair` and `mh_bookstack` with audited sizes and estimated masses; missing
assets fail admission. Generation admits the task only after schema, 2-D
placement, connectivity, shared-executor planning and continuous 3-D aerial
clearance checks and writes `modern_house_task.audit.json` beside
the task as release-QA evidence. The residential DAG models entry reconnaissance,
two-sided furniture access, a joint furniture move under kinematic ownership,
small-object staging, ground-team withdrawal and final room review. Low observation
positions and a separate furniture viewpoint avoid the blocked former flight
height. The template uses Iris geometry and interfaces; the NVD demo defaults to
`--aerial-motion-mode kinematic`, a 0.6 m/s bounded motion baseline with continuous
swept-AABB admission, the scene voxel product, task-object AABBs, bounded local
detours and per-step tracking. `--aerial-motion-mode controller` remains available
for a separate provider-controller diagnostic. The kinematic baseline does not
establish flight dynamics, camera visibility, dynamic-obstacle safety or general
flight planning. A finite short run is only a
configuration-screening result, not payload, grasp-force, collision-margin or
load-dynamics evidence.

Allocation, reassignment, navigation and `consume_message` are enabled in the
Isaac participant. Captured observations pass through the deterministic runtime
queue; only delivered, fresh observations can be consumed, and dependent nodes
remain blocked until their declared evidence is consumed. Participant `wait`
remains in the separate protocol harness. Evaluators may inject the initial
communication contract, a private condition schedule and lifecycle callbacks
through the programmatic `main(...)` boundary. The maintained scenario does not
yet contain a preloaded reserve or a physical lifecycle reference episode. Final
business acceptance requires identity/delivery evidence and a measured continuous
0.8m circulation path after both ground robots withdraw; a completed DAG is
insufficient. See [authoring and evidence boundaries](../benchmark/BENCHMARK_DESIGN.zh-CN.md).

The current-source reference report is
`tmp/benchmark_release/modern_house_business_iris_kinematic_full_v6_20260917.json`.
It used `scene_public_greedy_v2` and the metric auto budget, completed all 11 nodes
in 9,505 control steps, validated three RGB windows and three aerial routes, and
audited 116 joint-furniture samples (3.5407 m maximum separation under a 3.7262 m
bound). Both kinematic delivery predicates and final placement passed. The entry
scan now parks the camera-only Iris at `(15.5, 1.5, 0.85)`, outside the furniture
work corridor. Keepsake identity and the measured resident access path stayed
false because live adapters are absent, so the report does not establish business
success or release readiness.

The shared runtime now forwards captured frames and trusted objective-completion
events. Use `reference_domain_providers.build_modern_house_reference_collector()`
to inject a real keepsake detector and path backend. The before/after paths are
accepted only from their designated scan frames; the after sample must follow
`hall_release` and retain the bound occupancy-map digest. No visual detector
weights ship with this benchmark. The native ideal-sensor/path
adapters above require explicit live-session integration; the historical v6
business result remains false.

The serial native diagnostic runs in `env_isaaclab` without starting ROS:

```bash
python -m demo.modern_house_benchmark.native_probe --output-root tmp/house_native_backend
python -m demo.modern_house_benchmark.native_probe --output-root tmp/house_native_backend --execute
```

Use a fresh directory. It archives two real camera frames and scene-bound
identity/path snapshots separated by an author-side intervention: the two
carriers approach the deployed armchair, attach it with native paired-support
fixed joints, carry it toward an occupancy-derived parking cell, detach, and
then every ground robot withdraws to occupancy-derived cells outside the
resident corridor. The worker records the intervention timing
(`report["intervention"]["events"]`), asserts the occupancy-map digest is
unchanged, and only then steps the after snapshot with the observed closeout
time injected through `process_event`/`record_closeout`. Parking and
withdrawal cells come from `intervention_plan.plan_furniture_parking()` /
`plan_withdrawal_targets()`; when the authored parking preference conflicts
with scene occupancy, the nearest free cell wins and the report records why.
The ideal sensor excludes only the observer's own bodies; other robots still
occlude it. Resident paths exclude no robots. A blocked path is a valid
measurement. This diagnostic does not perform household restoration or certify
the complete business episode; `scoreable` stays false.

The scene-owned entry point is
`demo.modern_house_benchmark.main.build_domain_evidence_provider(public_task, ...)`.
Pass a detector implementing `detect(context)` and a path backend implementing
`measure_path(context)`, then pass the returned provider as
`domain_evidence_provider` to `main(...)`. The detector is bound to the public
`furniture_clearance_scan` objective by default; the path backend receives only
the public task, occupancy-map handle and designated `room_entry_scan` /
`room_condition_scan` frame requests. If either backend is absent, its component
stays `pending` and the provider emits no substitute claim. Even with both
backends present, the provider emits claims only; private object identity,
localization, path feasibility and business success remain evaluator checks.
