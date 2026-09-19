# Warehouse NVD benchmark demo

[中文](README.zh-CN.md)

The goal-track native attachment/dock diagnostic is separate from the historical
fixed-DAG episode below. Run in `env_isaaclab`, with scenes serialized:

```bash
python -m demo.warehouse_benchmark.native_witness_probe --device cpu \
  --output-root tmp/warehouse_native_witness
python -m demo.warehouse_benchmark.native_witness_probe --execute \
  --output-root tmp/warehouse_native_witness
```

Preparation freezes sources, objects and a diagnostic carrier spawn. Execution
uses a fresh Kit worker and the shared physical-probe lock. `dock_receiver.py`
checks the complete rotated object envelope against the candidate dock AABB,
frozen identity/mass, removed attachment constraints and a fresh post-release
physics step. The receipt certifies this candidate-region readback only; it does
not certify a deployed dock service, cooperative load sharing or the complete
business task. `native_carry.py` advances only the robot-side connector target
in bounded increments; PhysX owns the body response. The diagnostic now checks
actual lift clearance throughout transport, lowers the object, removes the
constraint and obtains the dock readback after fresh physics steps. A target
update alone is never evidence of lifting. Paired load sharing and team-exit
recovery still require separate execution evidence.
The goal task contains a crate and carton; a service car is not mandatory.
Output directories are single-use. No prescribed route is added to the task.

This application owns the warehouse_nvd task blueprint, asset allowlist and experiment profile in `scenario.py`. `config.py` selects its USD and occupancy map. `scenarios/business_logistics_01.json` is the default business challenge input; `navigation_smoke.json` is an explicitly named smoke fixture. Generated task pools and reports belong in `tmp/`.

From the repository root, in `env_isaaclab`:

```bash
python -m demo.warehouse_benchmark.main --check-assets
python -m demo.warehouse_benchmark.main --scenario demo/warehouse_benchmark/scenarios/navigation_smoke.json --gui --steps 400
python -m demo.warehouse_benchmark.main --scenario <episode.json> --headless --report tmp/warehouse_benchmark/execution.json
```

The smoke episode drives Carter with an unscaled Z1 through a short navigation leg. It checks assembly, arm inertials and navigation, not manipulation. The default physics device is CPU. Use `--transport-mode kinematic` only for explicitly labelled scripted transport experiments.

The public participant runtime controls allocation and reassignment for navigation, aerial-scan and kinematic-transport objectives: selected agents or teams get pickup/delivery routes rebuilt from live poses when the phase becomes ready. Object ownership changes only through runtime pickup/release events; reassignment is rejected while an object is carried, and a member exit drops a carried object at the carrier's position. Public fleet declarations expose domain, mobility and configured attachment interfaces while keeping rated payload unknown. Generated multi-robot tasks mount one unscaled Z1 on M20 and keep Carter as a base configuration; non-finite root states fail closed as `physics_state_invalid`. This is a runtime-stability boundary, not manipulation or payload evidence.

Each supplied V2 JSON must have `scene_key: warehouse_nvd`. Robots and attachments are built by `simulator.py` and `env_builder.py` through `open_simulator_session`, with the normal asset preflight. The shared executor lives in `demo/benchmark/runtime.py`; it does not create a second application lifecycle.

See [shared runtime and asset layout](../benchmark/README.md) for migration, preparation, physical evidence limits and asset distribution.

Generate a scene-owned task with:

```bash
python -m demo.warehouse_benchmark.challenge --generate-scenario tmp/warehouse_task.json
python -m demo.warehouse_benchmark.challenge \
  --scenario tmp/warehouse_task.json \
  --output-dir tmp/warehouse_benchmark/challenge
```

The first command writes the generated business scenario and its QA sidecar;
the second publishes the four-layer challenge bundle. Do not pass
`scenarios/navigation_smoke.json` to this publisher: it is a runtime assembly
fixture and the formal compiler rejects it as incomplete business authoring.
The public task includes a bounded regeneration skeleton with the reviewed
objective roles, native prop roles, factor axes, lifecycle and communication
boundaries.

Set `EAI_NVD_ASSET_ROOT` to the prepared native-prop root. Generation selects
`wooden_crate_a` and `cardbox_c` at audited sizes and estimated masses; unavailable
assets are rejected without substituting boxes. Generation admits the task only
after schema, 2-D placement, connectivity, shared-executor planning and continuous
3-D aerial-clearance checks and writes `warehouse_task.audit.json` beside the
task as release-QA evidence. The Warehouse DAG models rack scanning,
two-sided aisle access, joint crate transfer, carton consolidation/dispatch,
dock review and aisle release; it does not reuse the residential task topology.
The maintained template uses Iris geometry and interfaces for aerial scans. The
NVD demo defaults to `--aerial-motion-mode kinematic`: a 0.6 m/s bounded motion
baseline whose commanded and measured segments remain subject to continuous
swept-AABB admission, the scene voxel product, task-object AABBs, bounded local
detours and per-step tracking. Use `--aerial-motion-mode controller` to diagnose
the provider position controller separately. Neither mode is scored as
heterogeneous cooperation; the kinematic mode does not establish flight dynamics,
actuator limits, disturbance rejection or dynamic-obstacle safety.

The Isaac participant enables allocation, reassignment, navigation and
`consume_message`. Captured observations pass through the deterministic runtime
queue; only delivered, fresh observations can be consumed, and dependent nodes
remain blocked until their declared evidence is consumed. Participant `wait`
remains in the separate protocol harness. Evaluators may inject the initial
communication contract, a private condition schedule and lifecycle callbacks
through the programmatic `main(...)` boundary. The maintained scenario does not
yet contain a preloaded reserve or a physical lifecycle reference episode.
Business success additionally requires private identity bindings, measured
delivery and dock receipts. See [authoring and evidence boundaries](../benchmark/BENCHMARK_DESIGN.zh-CN.md).

The current-source reference report is
`tmp/benchmark_release/warehouse_business_iris_kinematic_full_v8_20260917.json`.
It used `scene_public_greedy_v2` and the metric auto budget, completed all 10 nodes
in 5,594 control steps, validated both RGB windows, both aerial routes, measured
ground tracking and physics health, and audited 126 joint-carry samples (3.4279 m
maximum separation under a 3.6727 m bound). All three kinematic delivery predicates
and final placement passed. Identity and dock-receipt predicates stayed false
because the live adapters are absent, so `business_task_success` remains false and
the result does not promote the release.

For the next serial Isaac witness run, prepare and execute the native single
carton carry probe with the explicitly selected CPU diagnostic device:

```bash
python -m demo.warehouse_benchmark.native_witness_probe \
  --output-root tmp/warehouse_native_witness_stage17 --device cpu
python -m demo.warehouse_benchmark.native_witness_probe \
  --output-root tmp/warehouse_native_witness_stage17 --device cpu --execute
```

The execute phase reuses `goal_attachment_probe` deployment and `LiveBodies`,
enables PhysX scene queries before scene creation, commits a native fixed-point
attachment, samples the carton after real simulation steps, detaches it, and
asks the Warehouse-owned dock receiver to read the released runtime object
pose. Carried-clearance evidence remains unknown under CUDA Direct GPU because
CPU scene queries can read stale actor poses; this probe does not certify
clearance. The paired wooden-crate case remains explicitly blocked until
two-member fixed-point/load readback exists.

The shared runtime invokes domain hooks after each captured frame. The
Warehouse-owned adapter validates detector, dock, and object-pose claims in
`demo.warehouse_benchmark.live_witness`; no reference provider is used by the
Warehouse entry point. No detector weights or dock backend ship with this
benchmark yet, so the v8 business result remains unchanged.

The scene-owned entry point is
`demo.warehouse_benchmark.main.build_domain_evidence_provider(public_task, ...)`.
Pass `detector_backend`, `dock_service_backend`, and `object_pose_backend`
objects implementing `detect`, `issue_handoff`, and `sample_pose` respectively,
then pass the returned provider as `domain_evidence_provider` to `main(...)`.
The provider binds the detector to the rack identity scan by default (additional
scan objective IDs can be supplied explicitly) and binds the service and pose
backends to the outbound dock observation. If any
backend is absent, that component is reported as `pending`, the dock chain is
disabled until both dock backends exist, and the business evidence remains
`unknown`/pending; no success claim is synthesized.
