# Factory benchmark demo

[中文](README.zh-CN.md) · [Delivery status overview (zh)](../benchmark/DELIVERY.zh-CN.md) · [Go2 locomotion base (zh)](../benchmark/GO2_LOCOMOTION_BASE.zh-CN.md)

**Goal-track handoff (2026-09-20):** `FACTORY-GOAL-01` uses the same
`GoalAlgorithmHost` / `GoalSession` interface and nine-condition authoring matrix
as all four scenes. Low-level skills follow a fixed benchmark policy; native
execution evidence remains a separate gate. See the [handoff guide](../benchmark/ALGORITHM_HANDOFF.zh-CN.md).

> **Status (2026-09-21, benchmark locomotion-base update)** — Runnable: F-INS-01 four-layer challenge
> package with dispatch matrix and ArUco label engineering baseline; F-TRAFFIC /
> F-COV comparison tasks; stair measurement binding (`flight_02`, root + 67
> dependency-layer hashes) and F-VERT-01 candidate compiler entry; 14-type
> configuration smoke. The default Go2 F-VERT base uses
> `GO2_MIXED_STAIRS_CFG`, which now carries the continued training weights
> (checkpoint 3999); the original Stage-A export is retired from registration. M20 remains the existing stair-capable baseline. A fresh
> continuous climb/platform/descent PhysX records for Go2 and B2 are still required;
> B2 is now registered through `B2_MIXED_STAIRS_CFG` but remains unpromoted until its
> independent evidence is archived. All packages remain `release_status=pending`,
> `scoreable=false`.

This application owns the factory task blueprint, asset allowlist and experiment profile in `scenario.py`. `config.py` selects its USD and occupancy map. Maintained smoke episodes live in `scenarios/`; generated task pools and reports belong in `tmp/`.

`scenarios/inspection_01.json` now uses Iris position/yaw control plus SimpleCar and
Carter. The 2267-step run archived one aerial and three ground frames; the independent
audit projects the target centre and all eight bounds corners inside both views.
Aerial capture position/yaw errors were 0.0317 m and 0.000472 rad. Prior CF2X view
failures remain archived. Detection, occlusion and localization evaluation are pending;
this is a validated capture setup, not a complete inspection result.

From the repository root, in `env_isaaclab`:

```bash
python -m demo.factory_benchmark.main --check-assets
python -m demo.factory_benchmark.main --scenario demo/factory_benchmark/scenarios/navigation_smoke.json --gui --steps 400
python -m demo.factory_benchmark.main --scenario demo/factory_benchmark/scenarios/traffic_01.json --headless --steps 5000
python -m demo.factory_benchmark.main --scenario <episode.json> --headless --report tmp/factory_benchmark/execution.json
```

The smoke episode drives Carter with an unscaled Z1 through a short navigation leg. It checks assembly, arm inertials and navigation, not manipulation. The default physics device is CPU. Use `--transport-mode kinematic` only for explicitly labelled scripted transport experiments.

The maintained traffic pairing assigns SimpleCar to the dock route and Carter to the yard route. A 5000-step CPU Isaac smoke completed both routes with no shared-resource conflicts and throughput 2. Reversing the assignment stalled Carter at the current dock entrance collision geometry; this is a scene/configuration pairing result, not a general Carter capability claim.

`scenarios/coverage_01.json` is a single-agent/same-type coverage control: three SimpleCars concurrently watch the storage, assembly, and dock sites with live C01 RGB frames. The CPU Isaac smoke completed all three sites with no shared-resource conflicts and a maximum observation age of about 1.24 s against a 30 s bound. This validates freshness evidence only; it does not validate recognition accuracy, localization error, or member-loss recovery.

`scenarios/coverage_01_dropout.json` is the member-exit variant: `simple_car_1` exits during storage coverage and remains in place; `simple_car_4` moves to a separate backup viewpoint and observes the same subject. Recovery requires both navigation and the full observation window. A CPU Isaac run with explicit 5 Hz rendering completed in 3820 control steps: exit at 35 s, first backup frame after 11.38 s, and maximum inter-frame/end-of-run gap across sites of 25.36 s against a 30 s bound. Default rendering exhausted a 180 s control-loop wall budget; both reports remain in `tmp/factory_benchmark/recovery_20260916/`. These are single-layout engineering checks; the post-run record does not promote a dossier. They do not establish target visibility, robot reliability, safety margin, payload, grasp force, wind resistance, or load dynamics.

```bash
python -m demo.factory_benchmark.main --scenario demo/factory_benchmark/scenarios/coverage_01_dropout.json --headless --steps 9000 --render-period-s 0.2 --wall-timeout-s 480 --report tmp/factory_benchmark/dropout.json
python -m demo.factory_benchmark.compare_allocation --scenario demo/factory_benchmark/scenarios/coverage_01.json --measurements <records.json> --output tmp/factory_benchmark/allocation.json
```

`--render-period-s` rounds up to control steps without changing physics dt or control decimation (200 Hz physics and 50 Hz control in the recorded run). It does not validate native sensor publishing rates. `--wall-timeout-s` bounds the control loop and writes a failure report on exhaustion; startup is outside that budget. The allocation comparison uses identical candidates, requirements and candidate-evaluation budgets for fixed, greedy and bounded exact methods. Sequential phases can reuse robots and `same_team_as` preserves the arrival/observation assignment. Budget exhaustion without a solution is `unknown`; `optimality_proven` applies only to the declared cost and finite candidate set.

The first unit-cost Isaac comparison passed fixed and greedy in 2582 steps each. The optimized assignment completed all six nodes in 6733 steps but failed the unchanged 30 s freshness bound: the dock's final observation was 55.86 s old. These failures remain in `tmp/factory_benchmark/scoped_microtests_20260916/paired_methods/`. This is one engineering layout, not a statistical ranking.

Use `--cost-model map_route_length` for inflated-map route distance in metres. Independent navigation roots start at each candidate's spawn; an attached wait adds zero distance and stays with its arriving member. Unreachable pairs are rejected with reasons. Intermediate origins, aerial space, turning dynamics and travel times are outside this model. Freshness still requires actual execution.

```bash
python -m demo.factory_benchmark.compare_allocation \
  --scenario demo/factory_benchmark/scenarios/coverage_01.json --measurements <records.json> \
  --cost-model map_route_length --output tmp/factory_benchmark/allocation.json
python -m demo.factory_benchmark.run_allocation_comparison \
  --scenario demo/factory_benchmark/scenarios/coverage_01.json --measurements <records.json> \
  --output-dir tmp/factory_benchmark/new_comparison
```

The second command starts Isaac sequentially for the three methods. It freezes the comparison, requests and compiled inputs before running, requires a new output directory, preserves failed attempts, and compares runtime asset/timing conditions. Source or input drift stops the comparison. The default route-distance model uses 9000 steps, CPU, 0.2 s rendering, a 480 s control-loop limit and a 630 s process limit per method. Measurements must match the current sources; old records remain historical evidence.

The route-distance rerun passed all three methods in 2582 steps each, with a 1.26 s maximum observation gap and no shared-resource conflicts. All chose the same assignments (5.1213 m planned total distance); this demonstrates single-layout execution, not optimizer superiority. Requests, reports and current configuration checks are in `tmp/factory_benchmark/route_cost_comparison_20260916/`.

The shared observation-window validator also rejected a real negative run: four live frames captured 1.5 m from a region with radius 0.5 m. Completion of the wait node did not earn E6. Coverage freshness currently measures inter-frame and end-of-episode gaps after each site's first frame; it does not bound initial commissioning latency.

Each supplied V2 JSON must have `scene_key: factory`. Robots and attachments are built by `simulator.py` and `env_builder.py` through `open_simulator_session`, with the normal asset preflight. The shared executor lives in `demo/benchmark/runtime.py`; it does not create a second application lifecycle. F-INS uses `aerial_view → ground_approach → ground_view(wait) → route_confirmation`; `observation_ref` separates navigation goals from inspection subjects. A CPU Isaac smoke completed all four nodes with one aerial and three ground RGB frames, including downstream observation consumption. Each frame records its digest, shape, dtype and observation id. The runtime's `sensor_pose_only` position is a robot-base world-position proxy, not calibrated camera extrinsics or a target estimate. `ObservationStore` provides detector source/version, target claims, estimates and source-frame linkage; the live detector and occlusion replay remain open. The evaluator-only fixture binds the Factory storage-box prim, world bounds, two allowed viewpoints and scenario/USD hashes without exposing truth to the runtime. The binding remains `visual_reference_only` and does not establish business identity or manipulation physics.

See [shared runtime and asset layout](../benchmark/README.md) for migration, preparation, physical evidence limits and asset distribution.

The new provenance path also passed a full `coverage_01.json` run: 2614 steps, all three sites observed, maximum inter-frame/end-of-run gap 1.24 s, and 436 runtime files unchanged. The batch recorder and E6 import preview accepted the exact frozen request and effective timing. Artifacts are in `tmp/factory_benchmark/provenance_20260916/`; no robot dossier was merged or promoted. This checks one configuration and layout, with recognition and statistical comparisons still pending.

Use `--archive-camera-frames --report <report.json>` to save unmodified RGB/RGBA arrays,
PNG previews and `CameraParams`/`ReferenceTime` from the same render product in a new
`<report>.frames/` directory. Pixel and preview digests are bound to observations and
rechecked during evidence conversion. The option is frozen with the execution request;
existing archive directories are rejected. These are simulator camera parameters, not
hardware calibration. Render reference time is kept separate from control sample time.

An `aerial_scan` node may set `scan_height_m`, its absolute world Z in metres,
above the target's height. The target remains on its declared floor. Omission or
null preserves `max(1.5, target_z + 1.0)`; measured hover checks and configuration
evidence bind the effective height. This setting does not certify flight clearance.
After attaching archive annotators the environment resets before task execution;
reports retain native poses before and after that reset. Original F-INS view audits
found the target outside the aerial image and behind the ground camera despite
successful capture windows. Recognition and localization remain pending.

`scan_yaw_rad` optionally requires an absolute world yaw in [-pi, pi]. The executor
uses the existing `goal_yaw` command only for Iris/Pegasus position controllers;
CF2X's position policy and direct rotor modes are rejected for this requirement.
Position and yaw must remain within 0.2 m and 0.15 rad for the same continuous
0.5 s dwell. Reports and configuration checks bind the requested and measured yaw.
Body heading control is separate from target visibility and camera calibration.

The C01 camera constructor now receives identity in Isaac Camera's default world
axes (+X forward, +Z up). Supplying a preconverted USD quaternion there converted
axes twice, turning the image sideways and pointing the camera away from the
mount's forward direction. Physical mount positions and robot dynamics are unchanged.

`python -m demo.factory_benchmark.inspect_camera_frames --scenario <scenario.json>
--report <report.json> --output <audit.json>` checks the evaluator fixture and archived
pixels, then projects the target centre and bounds into each captured view. This is an
evaluator-only audit, with no truth supplied to the policy. Projection into the image
does not establish unoccluded visibility, detection or localization.

The printed-inventory-label experiment uses `--inventory-label-id 17` with frame
archiving. A relative USD sublayer variant under `usd/scene/factory/benchmark/generated/`
adds a 0.09 m ArUco label with 0.12 m paper to an actual front-face triangle of the bound box.
The whole paper square must fit inside that triangle, with a 1 mm edge margin and
1 mm surface offset; the metric size is checked against the composed mesh. A box
bounding box alone cannot establish surface support. `--inventory-label-state covered|absent`
selects an opaque cover or no label; all conditions enter the frozen request and scene
provenance. The original scene and robot physics remain unchanged.

The online decoder receives pixels, render-product camera parameters, the public
dictionary and printed side length. It receives no expected ID or target world pose.
Corner coordinates use OpenCV's `CORNER_REFINE_APRILTAG`; the decoded dictionary remains
ArUco 4x4_50. IPPE_SQUARE and iterative PnP candidates are selected only by reprojection
error, preserving alternative residuals and solver names. This handles a reproduced
frontoparallel IPPE degeneracy without perturbing pixels. Uncalibrated confidence is null.
All valid estimates of each decoded ID within a view are fused by a fixed coordinate
median under the static-label assumption. Evaluation scores that estimate, retaining
all frame errors; it does not select the best frame using truth. The method was developed
on recorded ID17 frames and requires fresh condition runs after the source freeze.
World localization uses simulator camera extrinsics. See the official
[ArUco](https://docs.opencv.org/4.x/d5/dae/tutorial_aruco_detection.html) and
[PnP](https://docs.opencv.org/4.x/d5/d1f/calib3d_solvePnP.html) conventions.
`python -m demo.factory_benchmark.inspect_inventory --scenario <scenario.json>
--report <report.json> --attempt <attempt.json> --output <audit.json>` replays the pixels
and independently checks marker identity and position against composed USD geometry.
Downstream decision use and unlabelled-object recognition remain separate requirements.
Both declared inspection views are required even when execution stops before the ground
window. A missing view cannot satisfy a positive or negative condition. Matrix exit status
checks these conditions separately from execution-source integrity.

Ground camera waits may declare `wait_yaw_rad`. Carter/Scout differential controllers
and SimpleCar planar control turn the robot through their existing angular-velocity
input, hold heading within 0.05 rad for 0.5 s, then start the full `duration_s` capture
window. Alignment consumes the node timeout but does not shorten the observation window.
Capture and trajectory headings are checked, and the heading enters configuration
evidence scope. Other controller combinations are rejected at execution preflight.
Navigation recovery uses monotone improvements of at least 0.01 m in waypoint distance
or 0.05 rad in heading error. Slow final approaches and actual turning count as progress;
repeated oscillations do not reset the watchdog. Target radii remain the arrival criteria.

`python -m demo.factory_benchmark.run_inventory_matrix --output-dir tmp/factory_benchmark/new_inventory_matrix`
freezes and runs visible17, covered17, absent17 and visible23 in order. Every attempt
and failed check is retained; existing output directories are refused. Only the declared
label overlay may differ between runtime file closures. `--conditions visible17` selects
a preliminary engineering run. The former 0.18 m authoring version overhung the actual
box wall and is retained as failed authoring evidence, not upgraded to this protocol.

The dispatch variant uses `scenarios/inspection_dispatch_01.json` and the public business
rules in `inventory_dispatch.py`: ID17 selects the assembly observation station; ID23
selects the dock observation station. This task dispatches a robot to a real Factory
station; it does not deliver an object or operate equipment. Both candidate destinations
pass geometry, connectivity and route checks before startup.

`python -m demo.factory_benchmark.challenge --output-dir <directory>` compiles the
third-party challenge package. `release/public_task.json` omits robot assignments,
evaluator truth and reference decisions; `release/participant_api.json` declares the full
action vocabulary. The current F-INS runtime enables navigation/wait/message actions and
marks allocation/reassignment pending. The optional baseline manifest
describes solvers without embedding answers. Evaluator truth and release QA are written
separately to `evaluator/hidden_evaluator.json`. Configuration E6 checks admit the
published task and reference execution; they are not participant admission requirements.
The `immediate` and `wait_for_message` policies live in `baseline_solver.py` and are
comparison baselines, not normative task behavior.

A Python participant can be injected with
`demo.factory_benchmark.main.main(..., participant_adapter=<adapter>)`. It receives only
delivered observation messages, public phase/tick state and simulation time. Returned
actions are checked against the public task and API before execution, and schema-v3 reports
retain the event/action trace. Evaluation replays a strategy only when its manifest declares
`kind=baseline_solver`; third-party strategy internals are not replayed or treated as answers.

```bash
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m demo.factory_benchmark.run_inventory_matrix --dispatch \
  --output-dir tmp/factory_benchmark/new_dispatch_matrix
```

This freezes six fresh reference runs: two label identities crossed with shared, withheld and
delayed sensor-to-dispatcher messages. Completed observation windows publish fixed
fusions with source-frame timestamps. The immediate baseline makes one decision when dispatch
starts: one unambiguous known identity selects its station; otherwise it takes the
public assembly-station fallback. Delayed messages arrive 60 simulation seconds later;
messages expire 120 seconds after their oldest source frame. Late messages do not
change the committed route. Missing messages do not prevent task execution, and a
correct fallback guess counts as success. `--conditions shared17 shared23` runs just
the two route checks. The single-demo flags are `--inventory-dispatch-mode` and
`--inventory-message-delay-s`; both are included in execution requests.

The dispatcher receives delivered detection messages, never the true ID or automatic
raw-frame consumption through prerequisites. Selection triggers actual replanning
from the measured pose and replaces the arrival predicate. The original scenario
remains unchanged; the report binds the decision, real start, route and resolved task
digest. A visit before the decision does not satisfy dispatch. Independent evaluation
replays pixels, fusion, message availability, baseline actions when applicable, and
post-decision measured arrival. Third-party runs validate action binding without replaying
the participant's strategy.
`dispatch.evaluation.json` separates `dispatch_success` from source/protocol integrity.
A wrong default station is a retained business failure. The matrix exits nonzero for
execution or validation errors, while a verifiable wrong-route outcome remains an
explicit `dispatch_success=false` experimental result.

The two-view localization check is a separate sensor engineering criterion. Dispatch
does not require both messages, and this baseline makes no claim that aerial sensing,
two views or heterogeneity are necessary. These are fresh paired sessions, not restored
simulator states or paired noise. Dynamic dispatch cannot yet enter the static-target
configuration micro-test protocol; broader-world reliability remains unmeasured.

Factory F-INS/F-TRAFFIC/F-COV ground navigation excludes the stair and ramp envelopes
already declared by the scene template. Endpoint admission, connectivity, initial and
recovery planning, and allocation route costs share the mask. The extra 0.02 m encloses
rounded template bounds; it is not a measured collision-safety margin. Dispatch preflight
checks that these envelopes contain the corresponding current USD prims. Provider maps,
USD and robot physics remain unchanged. Legged morphology alone does not exempt a robot
from this unvalidated-surface restriction. Earlier shared-ID17 and shared-ID23 runs
selected the right station but crossed `main_stair` and failed after 12 recovery replans;
successful recognition does not overwrite those navigation failures.

The separate F-VERT-01 compiler can ingest a private, hash-bound OpenUSD stair
measurement with `--stair-measurement`. The current Factory asset exposes two authored
flights; the hidden evaluator selects the ground-to-mezzanine flight and binds the root
plus all 67 dependency-layer hashes. This remains static geometry evidence. PhysX
raycast/contact support, endpoint connectivity, controller calibration, and a healthy
traversal using the continued Go2 base are still required before the probe becomes
executable or scoreable. M20's existing stair capability is retained as a separate
baseline; B2 is still pending repair.

Aerial frame/completion timestamps refer to the already completed physics step at
capture, matching scheduler time. Runtime exceptions are saved to `.failure.json`
before simulator cleanup, and batch execution retains complete stdout/stderr logs and
digests. A missing formal report remains a failed execution even with exit code zero.

Factory allocation consumes source-verifiable v2 configuration checks. Import revalidates the pre-launch request, execution sources, installed assets, mass audit, configuration ID and report, then derives metrics from arrival events/poses or real frames across a complete observation window. Changing a record's metrics, scope or attachments cannot change its source evidence. Legacy v1 manual records are rejected for E6 admission.
For F-INS, `factory_inventory_detection_v2` also replays archived pixels and fixed per-view fusion, retaining frame counts, identified-frame counts, the observed marker ID, and fused localization error. Admission requires the exact configuration, world, observation node, and protocol scope. One labelled observation does not populate radar axes or establish generic recognition reliability, and a navigation check cannot substitute for a detection check.

```bash
python tools/scenario_generation/record_configuration_microtests.py \
  --scenario <frozen-scenario.json> --attempt <batch-attempt.json> --output <records.json>
python tools/scenario_generation/export_capability_profiles.py \
  --measurements <records.json> --format html --output tmp/factory_benchmark/capabilities.html
```

Repeat `--attempt` to retain every supplied attempt, including failures and unverifiable reports. A success-only list cannot estimate success rate. A single navigation/capture pass is a `protocol_check` with raw metrics, not a filled radar axis. Sampled navigation distance is a lower bound over the episode prefix, not a full-rate phase path or energy measurement. Capture requires a complete window with a maximum 30 s gap; it does not validate visibility, recognition or localization. Admission matches configuration, world layout, target region, observation subject and window duration. A short-route pass cannot admit another station, and new assignments still require geometry and execution validation. The current six SimpleCar+C01 checks share one three-member episode and are not six independent repeats or hardware ratings.

Generate one auditable smoke input for each of the 14 connected EAI robot types with:

```bash
PYTHONPATH=source/EAI:source/EAI_assets:source/EAI_hmrs \
  python tools/scenario_generation/configuration_smoke.py \
  --output tmp/factory_benchmark/configuration_smoke_14.json
```

The generator does not launch Isaac. Each record fixes the controller, Factory resources, sensor/tool selection, spawn condition, and protocol primitive. Missing scene/controller assets, attachment conflicts, and unsupported aerial pairings are explicit `blocked` or `not_executable` reasons. `ready_for_isaac_smoke` covers declared resources and static input checks; complete USD dependencies and physics still require verification through `simulator.py`, `open_simulator_session`, `env_builder`, and the pending physics gate. The protocols separate wheeled, Ackermann, legged/wheel-legged, biped, and aerial primitives. CF2X, Iris and Pegasus have completed goal-position and live-camera pairings; direct rotor-speed configurations remain unsupported by this protocol. The output does not establish rated payload, grasp force, collision margin, wind resistance, load dynamics, or E7 physical manipulation.

The short-route protocols are now `legged_navigation_v2` and `biped_navigation_v2`, with scenario hashes, metadata/input consistency, geometry, connectivity and initially-unsatisfied goal checks. Smoke schema v2 uses the same configuration IDs as capability profiles and keeps dossier snapshot hashes separate; regenerate older suite inputs. These passes do not replace retained long-route failures or rank robot types. M20 passed its original long route; Go2's low final height on the long route still needs a health diagnosis. Navigation completion uses the evaluator's zone-visit predicate and preserves the exact terminal goal. A completed visit remains valid after leaving the zone.

Pepper reads its moving `base_link` and commands its virtual base joints in the fixed root frame; the original scenario passed in 1763 steps after this correction. Reports expose `navigation_state_sources` and `pose_trajectory`: world positions in metres, yaw in radians, samples every 10 steps and additional initial, arrival, failure and final samples.

The 2026-09-16 Iris and Pegasus pairings each completed in 727 steps, including a continuous 0.5 s hover and live RGB frame. Iris's first run lacked complete remote-material provenance; it passed after relocating all nine dependencies into `usd/robot/pegasus/materials/`, preserving all non-asset-path content. Pegasus's optimized antenna mesh retained material indices from its original topology. Restoring this non-colliding visual mesh from the source asset preserved its world transform and all content outside the mesh; a 727-step rerun passed without the original geomSubset warning. Single-layout configuration smoke and full inspection recognition are separate acceptance criteria.

An independent physics result can be applied with `--physics-gates gates.json`; for example, `{"m20":{"status":"failed","reasons":["base_support"]}}` produces `execution.status=not_executable` with the failure reason preserved.

Allocation reports include a `reallocation_certificate`. It keeps the task requirements and success conditions fixed while allowing fresh assignment over the phase graph. `single_agent_feasible`, homogeneous-team feasibility, heterogeneous necessity, and budget exhaustion are reported separately; the current Factory coverage certificate finds a three-robot minimum, no single-agent solution, a homogeneous-team solution, and no need for heterogeneity. Isaac paired execution remains pending.
