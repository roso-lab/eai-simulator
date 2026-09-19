# Vertical traversal probes / 楼梯与坡道协议

`vertical_traversal.py` supplies pure admission, health and height-progress helpers.
`tools.scenario_generation.vertical_task` is the separate bounded Isaac executor for
the explicit Factory `F-VERT-01` probe. It uses the shared ground route planner for
the approach, then follows the measured main-stair surface with a capped velocity
command. Passing its unit tests is not evidence that any robot can climb a stair.

## Current boundary and evidence

`EAI.scenario_spec.geometry.ground_surface_exclusions()` excludes the three Factory
vertical-link envelopes only for `F-INS-01`, `F-TRAFFIC-01` and `F-COV-01`. This is
a temporary boundary of those ground navigation protocols, not a claim that all
ground robots must avoid stairs. No gate is removed by this module.

The existing Factory template records ground at 0 m, west-ramp support at 0.85 m,
and the mezzanine at 1.57 m. Its `StairLink` bounds describe USD geometry: the main
stair's 2.57 m maximum is **not** a verified arrival floor. The west-ramp bounding
box is only 0.54 m wide, versus Go2's conservative planning diameter of 1.10 m.
A level label or an overlap of bounding boxes cannot establish a walkable route,
measured step dimensions, slope, local support, or controller capability.

Read-only inspection on 2026-09-16 of the six local historical reports under
`tmp/validated_pool/trn01_first/execution/sar0[12]*report.json` confirmed:

| Runs | Go2 terminal position / outcome |
| --- | --- |
| SAR-01 s17 / s23 | (-2.30,-3.31,0.06) / (-2.46,-3.18,0.06), deadlocked |
| SAR-02 s17 / s23 | (-2.08,-3.33,0.06) / (-2.76,-3.22,0.06), deadlocked |
| SAR-01 / SAR-02 s31 | executed, terminal root height 0.32 / 0.33 m |

Those old reports contain terminal XYZ and deadlock records but no quaternion or
trajectory. They support a collapsed-height diagnosis near the stair envelope;
they cannot identify the first bad contact or prove the underlying fall mechanism.
The installed `rl/go2_rsl_rl/go2_rsl_rl.py` defines 45 proprioceptive observations
and no terrain height scan. This fact alone does not establish what terrain its
downloaded policy was trained on. The reports remain local runtime artifacts.

Read those existing reports without starting a simulator:

```bash
python -m tools.scenario_generation.diagnose_vertical_episode \
  tmp/validated_pool/trn01_first/execution/sar0[12]*report.json \
  --robot go2_1 --reference-root-z-m 0.32 \
  > /tmp/eai_vertical_diagnosis.json
```

The CLI accepts legacy `final_poses` and current `pose_trajectory.samples`
(`robot_poses_xyzyaw` or `positions`). It emits the minimum observed root height,
lowest sampled step, terminal XYZ, malformed-sample count and this robot's
deadlocks. `--reference-root-z-m` supplies a same-floor comparison height, not a
support measurement. In all of these old reports `health_verdict` remains
`unknown`: XYZYAW does not provide roll/pitch, and a single low root height does
not provide continuous support/contact evidence. Missing local reports can be
replaced by any new report path; no download or mutation is performed.

## Probe contract

1. Supply a `VerticalProbe` with **measured walkable** start/goal support XYZ,
   narrowest clear width, maximum local slope and maximum step height. Do not
   synthesize these from a USD bounding box or assume a linear support-height ramp.
2. `admit_vertical_probe()` separates legged (`quadruped`, `biped`) stairs from
   wheeled ramps and checks footprint clearance and explicit configuration-specific
   slope/step limits. Wheeled robots can therefore probe only ramps within those
   limits. Missing limits fail closed. A positive result is `admitted_for_probe`,
   always accompanied by `execution_validated: false`; no capability is promoted.
3. After settling, record each moving body's root-to-support clearance. Call
   `advance_ground_health()` each control step with the current root height,
   WXYZ quaternion, actual measured support height and simulation-time `dt_s`.
   Defaults flag sustained clearance below 45% of the baseline or tilt above
   60 degrees for 0.5 seconds. These are explicit diagnostic thresholds, not
   calibrated safety or capability limits. Brief low poses reset when healthy.
   Unknown support produces `support_unknown`; it still permits a tilt diagnosis.
   Non-finite poses and zero quaternions fail immediately.
4. `advance_vertical_progress()` counts best-so-far signed height advance for
   ascent and descent. Completion requires horizontal arrival, root height relative
   to settled clearance, measured support at the destination level, healthy pose,
   and continuous dwell. XY arrival alone and oscillation never satisfy it. The
   executor must separately enforce route/contact checks and bounded timeouts.

Use one state dictionary per robot and per active probe. Reset state at episode,
settle and route changes. Health failures must stop and report `fallen` or
`invalid_state` before generic waypoint deadlock recovery; do not replan a fallen
robot as if it were merely blocked. Calling these helpers alone does not wire that
behavior into the existing executor.

## Integration and heterogeneous comparison

The minimal Go2 reproduction configuration should retain seed 17, Factory,
`GO2_VELOCITY_RSL_CFG`, no attachments, and the historical Go2 spawn
`[-1.95, -8.60, 0.40]` / quaternion `[1,0,0,0]` from
`tmp/validated_pool/trn01_first/instances/sar01_s17_ur5_base.json`. Remove M20,
its arm and the transport node in a **new diagnostic fixture**, preserving the
original frozen instance. Keep the requested physics device and record its value,
controller/weight hashes and scene hashes.

Use two bounded phases: (1) zero-command settling then a short flat approach,
with candidate endpoint `[-1.95,-8.00,0.0]`; (2) a separately admitted approach to
the historical stair incident region `x=-2.30,y=-3.31`. Phase 1's free-space and
spawn clearance must be rechecked against the active map. Phase 2's support
route/endpoint height, step dimensions and contacts must be measured before
issuing commands; **do not use z=0 or the stair bbox top as its goal height**.
Use a 0.10 m/s initial speed ceiling, a 30 s simulation-time budget per phase,
and immediate stop on invalid state or sustained health failure. These are
diagnostic experiment limits, not validated controller limits. Log every control
step's command, XYZ, full WXYZ, support/contact identity and joint/velocity state;
sampled terminal XYZ is insufficient. Establish the settled clearance separately
for each run rather than hard-coding the historical 0.32 m.

`vertical_task` is an executable candidate probe, not a general locomotion planner.
It records the route, root XYZ, complete WXYZ, support raycasts, health and height
progress, stops on a fallen/invalid state, and returns a nonzero status unless the
destination support dwell is proven. The current Factory runs remain candidate
failures: the default Go2 policy reaches the measured stair entrance but has not
completed the ascent. The offline diagnosis command above remains read-only and
does not silently run the stair phase.

Foot-contact calibration and runtime gait observation use separate thresholds.
The trailing zero-command calibration window requires at least three identified
feet on a near-horizontal static collision surface in every admitted sample,
with every foot present in at least 80 percent of the window. Runtime support
then accepts one identified foot as a measured observation, but only samples
with at least two supporting feet may accumulate vertical progress. A zero-foot
gait gap may remain temporarily unobserved for at most 80 ms; it contributes no
height evidence or progress and fails closed when that bound expires.

The next physics probe needs a surface-following route/controller, support samples
and quaternion/contact traces to locate the first fall. Follow with ascent and
descent trials using exact controller/configuration and asset hashes, bounded
commands/timeouts, and recorded health/progress. Failed runs remain evidence.

## Bounded Go2 flat health/approach probe

`tools/validation/ground_health_probe.py` supplies the first executable
diagnostic boundary. It generates a new in-memory-equivalent Factory V2 JSON
scenario containing only `go2_1`, `GO2_VELOCITY_RSL_CFG`, seed 17, no attachments,
and a short flat approach. The scenario is passed through
`open_simulator_session`; it does not mutate the historical SAR fixture and does
not remove the Factory 2-D stair exclusions.

The probe first settles with zero command, establishes root-to-support clearance
from a downward PhysX raycast, then runs at at most 0.10 m/s. Every sample records
the planar command, root XYZ, complete WXYZ, raycast hit identity/distance and
`advance_ground_health` output. Raycast API differences, self/robot hits,
missing hit identity, unknown support, invalid pose, or sustained low clearance
fail closed. A failed sample stops the bounded run; it is not converted into a
waypoint deadlock or stair attempt. The current adapter intentionally records
`unknown` when this Isaac SDK's query binding cannot provide reliable support.

After GPU/display and gated asset coordination, the main agent can run exactly:

```bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate env_isaaclab
PYTHONPATH="$PWD:$PWD/source/EAI:$PWD/source/EAI_assets:$PWD/source/EAI_hmrs" \
python tools/validation/ground_health_probe.py --headless --device cuda:0 \
  --steps 600 --settle-steps 120 --speed 0.10 \
  --scenario-file tmp/go2_flat_ground_probe.json \
  > tmp/go2_flat_ground_probe.report.json
```

The generated JSON is a diagnostic fixture description, not a capability claim.
This probe measures flat-ground pose/support health and a short approach only; it
does not measure stair ascent/descent, contact wrench quality, route progress on
vertical geometry, or benchmark success. A stair trial still requires a separate
measured support route and a surface-following controller.

Only a validated vertical executor may replace the current 2-D swept-disk exclusion
check **inside its admitted active vertical segment** with appropriate 3-D geometry,
support and contact checks. Do not globally disable swept clearance, bypass a
stair envelope for all ground tasks, or infer an elevated route from a free 2-D cell.

A paired task should give a legged and wheeled configuration the same start and
destination semantics, permitting the measured stair route only to the legged
probe and a measured low-ramp alternative to the wheeled probe. Report admission,
physical outcomes and route cost separately. No such fixture is published as
executable until those routes and controllers pass the physical probes; changing
historical blind-test fixtures to tune this behavior would invalidate that split.

Pure regression command (no Isaac launch):

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
python -m pytest -q -p no:cacheprovider \
  source/EAI/test/test_benchmark_vertical_traversal.py
```
