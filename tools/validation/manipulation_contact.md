# UR5 contact grasp and mobile arm fixtures

These are opt-in Isaac Sim 5.1 / Isaac Lab component physics experiments. They
reuse EAI assets, the UR5 IK controller, and the existing UR5/Z1 payload mount
builders. They do not register a new selectable robot or change default payload
mass scales. The mount builders now apply solver and self-collision properties
to the actual floating articulation root after removing the world joint.
These fixtures do not run the full EAI scenario benchmark. Full EAI scenes should still
use `open_simulator_session()`.

## Inputs and commands

Activate `env_isaaclab`. Local EAI UR5, Scout, Z1, B2 and Go2 assets must already
be available for the combinations selected. UR5 additionally requires the
[official NVIDIA Robotiq 2F-85 asset](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/assets/usd_assets_robots.html)
with its referenced USD/mesh dependencies. `--asset-root` is the directory
containing `Robotiq/2F-85/Robotiq_2F_85_edit.usd`. External assets stay outside
tracked source; downloading an entrypoint alone is insufficient.

Run from the repository root, choosing a new output directory each time:

```bash
python tools/validation/ur5_grasp_smoke.py \
  --asset-root /absolute/path/to/Isaac/Robots \
  --report-dir tmp/grasp_fixed --mass 0.2
python tools/validation/ur5_grasp_smoke.py \
  --asset-root /absolute/path/to/Isaac/Robots \
  --report-dir tmp/grasp_open --open-control
python tools/validation/ur5_grasp_smoke.py \
  --asset-root /absolute/path/to/Isaac/Robots \
  --report-dir tmp/grasp_scout --host scout
python tools/validation/mobile_arm_mount_smoke.py \
  --host b2 --report-dir tmp/z1_b2
python tools/validation/mobile_arm_mount_smoke.py \
  --host scout --arm ur5 --gripper-usd /absolute/path/to/Robotiq_2F_85_edit.usd \
  --gui --report-dir tmp/gui_scout_ur5
python tools/validation/mobile_arm_mount_smoke.py \
  --host go2 --report-dir tmp/z1_go2
python tools/validation/mobile_arm_mount_smoke.py \
  --host b2 --bare-host --report-dir tmp/b2_baseline
python tools/validation/ur5_grasp_checks.py tmp/grasp_fixed/report.json
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q \
  tools/validation/test_ur5_grasp_checks.py
```

The simulations run headless by default on CPU PhysX at 240 Hz; Kit still loads
rendering extensions and needs a working Isaac installation. `--gui` enables
rendering in the UR5 fixture. `--probe` exports an assembled stage after settling
without a grasp attempt. Tests of the evaluator require only Python and pytest.
Simulation commands write `report.json`, `assembly.usda` and, on a caught error,
`error.txt`. They must not run against a hardware ROS bridge.

Mounted roots and their hosts are reset together to the pre-warm-up spawn pose
with zero velocity. Normal EAI environment resets also use the authored mounted
pose, rather than copying a possibly displaced or moving warm-up state.
`--legacy-reset` exists only to reproduce the previous fixture initialization.

The local Kit runtime can stall during timeline stop or full application cleanup
after all reports are written. These dedicated processes use the supported
`SimulationApp.close(skip_cleanup=True)` after artifact export. Bound runs with
`timeout -k 5s 90s ...` if needed; record timeout
separately from physics acceptance. A timeout or zero process status alone is
never proof of a successful simulation. Require complete JSON, exported stage,
no `error.txt`, and evaluator acceptance. A timeout before those artifacts exist
is an incomplete trial. `assembly.usda` references the external assets and is
an assembly inspection artifact, not a time-sampled replay of the experiment.

## Physical model and limits

- The wrist-to-gripper attachment is a real fixed joint within the UR5
  articulation. The object is never fixed to the gripper. Only initial placement
  writes object poses; all task motion uses joint drives and simulated contacts.
- UR5 source body masses total approximately 20.994 kg. Mounts explicitly use
  `mass_scale=1`; existing EAI default reduced-mass profiles are not changed.
  Source masses are not a certification of the real arm's mass distribution.
- Robotiq total mass is set to 0.925 kg, including coupling, from the
  [manufacturer manual](https://blog.robotiq.com/hubfs/support-files/2F-85_2F-140_UR_PDF_2026-03-17.pdf).
  The downloaded USD lacks explicit mass on the base and two outer knuckles.
  Each missing knuckle is estimated at 9 g; the base receives the remainder.
  Existing link masses remain, and missing inertias are estimated from shapes.
  This assumption is recorded, not presented as calibrated hardware data.
- Internal gripper linkage collisions are filtered except between opposing
  inner fingertips, because source linkage collision meshes otherwise jam.
  Object and arm contacts remain enabled. This prevents any claim of complete
  self-collision or collision safety validation.
- UR5 force drives are bounded at 150 Nm for shoulder/elbow and 28 Nm for wrists,
  with a 1 rad/s joint speed limit. The gripper drive is bounded at 2 Nm by
  default (`--gripper-torque`), 0.8 rad/s. These are simulation drive settings,
  not a calibrated map from Robotiq command percent to hardware gripping force.
  Recorded projected joint forces are joint reaction/load measurements, not
  direct motor-current measurements or a certified actuator margin.
- Contact material requests static friction 0.8, dynamic friction 0.6, minimum
  combine mode and zero restitution. Binding on the gripper ancestor ensures
  instance mesh colliders inherit this physics material; every inner fingertip
  binding is checked before running. Friction and pad compliance are not hardware measured.
- The rigid coupon is 50 × 60 × 150 mm; `--mass` changes its uniform density.
  The target lies 150 mm along the gripper axis and 15 mm below the object's top,
  avoiding the proximal knuckles. The table is fixed, 300 × 700 × 100 mm for the
  fixed arm and 200 × 700 × 100 mm for Scout. `--table-length` overrides the first
  dimension. The wider table intersected the Scout body at this grasp pose;
  mobile placement now requires at least 5 mm conservative host bounding-box
  clearance before either table or object is placed. Motion is slow and
  scripted: approach, close, lift, translate, hold, lower, release and retreat.
  No perception, unknown-object grasp planning or deformable material is tested.
- Scout wheels are held at zero velocity with an experimental 20 Nm limit and
  damping 100. The base is freely supported on the ground. This is stationary
  manipulation, not a navigation, braking or rough-terrain test. Mobile UR5
  trials reject a host that fails to settle upright before placing the object,
  and reject a table placement that would intersect the ground or host.
  Scout's default root height is 0.24 m: the authored wheel geometry extends
  0.23479 m below the root, so the previous 0.20 m start penetrated the floor.
- Z1 uses unscaled asset mass, 60 Nm at joint2 and 30 Nm at the other arm joints.
  B2 uses `build_b2_stationary_cfg()` (800 Nm/rad stiffness and 20 Nm s/rad
  damping) to hold a standing pose; original DCMotor effort/speed limits stay
  unchanged. `B2_CFG` and the trained locomotion controller retain their own
  original gains. `--host-control authored` selects the old B2 PD baseline.
  Go2 retains its original PD gains. The fixture does not claim a validated
  locomotion policy with an added payload.
  Host self-collision settings retain their source configuration.

## Acceptance and interpretation

`ur5_grasp_checks.py` checks the complete 21-second timeline, finite vectors,
unscaled mass, declared absence of scripted payload attachment, and a full
two-second hold with at least 10 cm lift, bilateral finger contact and no table
or other-body support. Each jaw aggregates its inner and outer finger bodies;
all rigid bodies and the ground are included in the object contact filters.
Raw force vectors per body remain available so an unexpected knuckle, palm or
environment contact cannot be hidden by the jaw aggregation.
It also requires lateral transfer within 25 mm, relative hold slip below 10 mm,
and a final second with clear jaws, stationary object and table force near its
weight at the release target. Mobile trials additionally require tilt below
0.15 rad and planar drift below 0.1 m. These are fixture acceptance tolerances,
not hardware safety limits. The open-jaw negative control must leave the object
on the original table position; its grasp result is deliberately false.

`mobile_arm_mount_smoke.py` checks 15 seconds of standing and moving the Z1 arm:
post-settle tilt below 0.15 rad, final planar drift below 0.1 m, and joint1
excursion above 0.45 rad. It records full body masses, arm inertias, joint loads,
mount parameters, trajectories and asset hashes. This screen says nothing about
object grasping by Z1 or mobile locomotion stability.

`--arm ur5` selects an unscaled UR5 with the default Robotiq 2F-85;
`--gripper-usd` overrides that asset and `--arm ur5_bare` omits the gripper.
`--arm kinova_gen3` is an isolated Scout/B2 candidate with no gripper. It requires
`EAI_KINOVA_GEN3_USD` (or the canonical local asset) and an explicit
`EAI_KINOVA_GEN3_URDF`. Before PhysX initialization, the shared source-checked
converter replaces the empty massless interface with a non-rigid child frame
of the wrist, disables its former joint, and updates the rigid-link registry.
It rejects real interface mass, geometry, conflicting tool references and a
source-frame mismatch. Massive-link masses remain unchanged. The candidate
mount moves the articulation root to `base_link`, retains source world-joint
deactivation and uses the host rotation in its mounting transform. The trial
requires eight massive links, seven DOFs and a floating articulation.
`massless_interface_metadata` records the source URDF and conversion hashes;
resolving this interface alone does not establish hardware fidelity.

The candidate now also restores the complete Kortex URDF inertia tensors before
PhysX initialization. The source USD has zero principal-axis quaternions and
diagonal fields equal to URDF diagonal terms; its previous runtime matrices
omitted the nonzero inertia products. The converter validates base-relative
zero-pose link frames, source mass agreement and COM, applies inertial RPY, and
diagonalizes the full symmetric tensor into positive principal moments and a
proper rotation. It retains USD massive-link masses and COM, explicitly records
the small USD/URDF mass-rounding differences, and never edits external assets.
`source_inertia_metadata` binds the URDF and converter hash. This is a declared
source correction, not hardware calibration. A zero quaternion must not be
silently converted to an identity rotation for an authored-tensor claim.
See the [OpenUSD mass schema](https://openusd.org/release/api/class_usd_physics_mass_a_p_i.html)
for diagonalized inertia and principal-axis fields.

```bash
export EAI_KINOVA_GEN3_USD=/absolute/path/to/gen3n7_instanceable.usd
export EAI_KINOVA_GEN3_URDF=/absolute/path/to/GEN3-7DOF-NOVISION_FOR_URDF_ARM_V12.urdf
python tools/validation/kinova_mount_smoke.py \
  --asset "$EAI_KINOVA_GEN3_USD" --urdf "$EAI_KINOVA_GEN3_URDF" \
  --model-urdf-massless-link --apply-urdf-inertias --mode both --steps 720 \
  --report tmp/kinova_fixed_new.json
python tools/validation/mobile_arm_mount_smoke.py \
  --host scout --arm kinova_gen3 --gui --report-dir tmp/kinova_scout_gui_new
python tools/validation/kinova_inertia_audit.py \
  --asset "$EAI_KINOVA_GEN3_USD" --urdf "$EAI_KINOVA_GEN3_URDF" \
  --runtime-report tmp/kinova_scout_gui_new/report.json \
  --report tmp/kinova_scout_tensor_audit_new.json
```

The fixed fixture's v2 report records actual PhysX drive parameters. Each mode
starts from the authored joint pose with zero velocity; `zero-drive` really
sets stiffness, damping and effort limits to zero. This corrects the old
fixed fixture, which retained PD despite its phase name. Require the completed
JSON and absence of the corresponding `.error.txt`; Kit shutdown may mask the
exception exit code. Finite rollout is not a payload or contact test.

`kinova_inertia_audit.py` compares all nine tensor entries about each link COM
after matching USD/URDF link frames at zero joint pose. It verifies the runtime
report's source-layer hashes before reading PhysX matrices. Unknown original
USD axes remain unknown even when a declared URDF override matches at runtime.
The older `kinova_urdf_usd_audit.py` remains a mass-only comparison; its `match`
rows are not full-tensor validation.

Diagnostic overrides (`--host-stiffness`, `--host-damping`,
`--mount-body`, `--scout-box-inertia`, and the grasp fixture's `--arm-stiffness`,
`--arm-damping`, `--solver-iterations`, `--host-height`) are recorded in JSON.
The box-inertia option is an explicitly uncalibrated uniform-box estimate for
isolating the Scout asset's 1e-5 kg m² chassis inertias. It is off by default and
must not be treated as a measured replacement or included in authored-inertia
acceptance. The deployment repair does not need this option or reduced mass.

Reported COM-to-wheel/foot-center hull distances are geometric diagnostics on
level ground, not measured contact support polygons, dynamic tipping margins,
or certified collision clearances. The grasp report includes the object in
this COM calculation even during table support. B2 stationary gain selection
does not automatically change a benchmark's walking or balance controller.

`--bare-host` removes the arm for a standing-controller baseline; its result
is `bare_host_standing_pass`, not `stationary_motion_pass`. A failure of both
bare and loaded hosts is not evidence of exceeding the host's payload rating.

A successful contact grasp supplies component-level simulation evidence. It
does not establish an entire E7 scenario benchmark pass, actual rated payload,
hardware grip force, collision safety margin, wind resistance or validated load
dynamics. Tool, adapter, cable and payload masses all consume real wrist/base
capacity. Hardware deployment additionally requires the exact arm/base variants,
mounting plate and fastener ratings, measured mass/COM/inertias, calibrated
actuators/friction, and tests over the intended speed and reach envelope.
# Runtime mass-property evidence

`mobile_arm_mount_smoke.py` records `system_physics` on each sample: total
mass, COM and the aggregate inertia about that COM in the simulation world
frame. These fields are `simulation_derived`; they include the physics
engine's inferred parameters and must not be relabeled as hardware measurements.

The report stores `runtime_mass_properties` once after reset and
`runtime_physics_snapshots` at five sample indices. Each snapshot contains the
body positions and rotations in the same order as the stored body properties,
allowing the sample's aggregate to be recomputed independently. The trial
does not change body masses or inertias after the initial capture.

PhysX `get_inertias()` returns column-major tensors about each link COM,
expressed in the rigid-body-prim frame. The collector transposes the reshaped
buffer, applies the link-to-world rotation once, then shifts to the system COM.
The principal-axes quaternion from `get_coms()` is not applied again.
UR5 reports also include `gripper_physics_metadata` so the Robotiq total mass
declaration and estimated link quantities remain visible.

## Ground reaction and contact-reporting controls

Trial schema v2 uses one standard `ContactSensor` for each wheel/foot, with
`/World/Ground/geometry/mesh` as the filter. Body paths are discovered from the
spawned rigid bodies, including M20 wheel links and Lite3 uppercase foot links.
`contact_sensor_ground_force_vectors_N` records the filtered force matrix.
Net forces and air-time contact flags are diagnostic only: they cannot identify
the other contacting body and must not be used as ground-support evidence.
The older raw multi-body view fields (`host_ground_contact_force_total_N`,
`host_ground_contact_point_count`) are retained only for comparison; repeated
filter matches can duplicate their totals. Use the per-body sensor fields and
`ground_support` for acceptance and force balance.

Isaac Lab's `SimulationContext` sets `/physics/disableContactProcessing=true`.
`ContactSensor` enables reporting in its constructor. Raw tensor contact views
alone do not enable this CPU reporting path. Zero force/count buffers from
disabled reporting do not establish that a robot is floating or that collisions
are absent. `get_contact_data()` shares the tensor reporting path; it is not an
independent contact-event callback. Reports record this setting before sensor
creation, after creation and during the trial.

The `ground_support` screen evaluates the final 12 seconds of the 15-second
trial. At least 99% of samples must have filtered upward force of at least half
the system weight; mean upward force must be within 10% of system weight.
These are explicit fixture consistency tolerances, not hardware safety limits.
Momentary contact alone cannot pass. Existing tilt, drift and arm-excursion
conditions still apply. `max_host_ground_contact_force_N` in v2 is the maximum
sum of filtered vertical reactions after settling; single-body maxima are
separately named. Do not compare ambiguous v1 maxima with v2 values.

Reproduce the reporting negative control with the same arm, pose and assets:

```bash
python tools/validation/mobile_arm_mount_smoke.py --host scout --arm ur5 \
  --report-dir tmp/scout_ground_reporting_enabled
python tools/validation/mobile_arm_mount_smoke.py --host scout --arm ur5 \
  --disable-contact-processing --report-dir tmp/scout_ground_reporting_disabled
```

The second command disables reporting, not collision response. Its support
gate must fail even if positions are stable. Use `--bare-host` with the first
command for the unloaded baseline. Always choose fresh report directories.
`EAI_SCOUT_PHYSICS_USD` is an optional diagnostic USD override, recorded in
`host_asset_path`; it is unnecessary for the standard contact-reporting fix.

The per-body contact buffers also provide `ground_contacts`: world positions,
normals, normal forces and separation distances for contacts above 0.001 N.
`ground_contact_hull_margin_m` uses these points, not wheel/foot-link centers.
An empty/degenerate hull is `null`; negative margins are retained. Buffer force
sums are checked against the filtered force matrix and the maximum component
difference is stored on every sample. Reaching the configured 64-point buffer
capacity aborts the trial rather than silently claiming a complete hull.
This instantaneous planar hull is an additional diagnostic; it is not a
dynamic stability or hardware safety certification. A stationary force/pose
pass does not imply that all instantaneous hull margins are positive.
