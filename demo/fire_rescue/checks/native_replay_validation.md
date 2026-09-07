# Native replay validation — 2026-09-07

## Fresh-scene reconstruction: measured carrying acceptance

A separate reconstruction path now re-executes from the canonical initial scene
in a fresh process instead of writing a partial snapshot. It streams the
uninterrupted original to a checksummed, pickle-free journal, verifies every
environment step and actual solver substep with **zero numerical tolerance**, and
only allows live continuation at a fully matched named task boundary. Config,
ONNX graph/provider, loaded source, platform, file-backed USD layer and measurement
layout fingerprints must match. This is not yet wired into the full Fire Rescue
main/EMOS/navigation event-delivery loop.

### A physical joint bug found by the dynamic fixture

Physical grab anchors previously used `UsdGeom.ComputeLocalToWorldTransform()`
even when the articulation's active PhysX pose had moved without corresponding
USD pose writes. In the first controlled run, the supposed end-effector-origin
anchor was `[0.32969591, 0.44674939, -0.08211939]` rather than approximately zero;
the measured boundary anchor discrepancy peaked at about **0.507 m** and the
fixture failed. `UR5Manager` now uses the live PhysX body position/orientation
when defining a physical weld; failure to read a live body is not silently
replaced with USD state. Strict pickup distance checks use the live target pose
too. Authoring an offline in-memory stage still uses its authored USD transforms.

No mass reduction, impulse clipping, added damping, or copied force history was
used to make the corrected test pass. The controlled fixture initially places the
real extinguisher beside the end effector and creates an ideal physical weld in
both clean scenes. That is **not** an autonomous reach/grasp demonstration or a
validation of a frictional gripper.

### Actual results on this host

Factory, seed 0, CPU PhysX with enhanced determinism before scene creation,
physics dt 0.005 s, decimation 4, actual M20 ONNX inference, Gaussian action noise
std 0.001, original command `[0.05, 0, 0]`:

| Measurement | Result |
| --- | --- |
| Independent-process locomotion | 64 environment steps / 256 physics substeps; all 191 recorded channels equal |
| Independent-process physical carry | 160 environment steps / 640 physics substeps; all 201 recorded channels equal |
| Carry checkpoint | Completed environment step 80, `extinguisher_carried` |
| Measured motion / force / friction / reaction inter-run errors | **0.0** throughout the recorded horizon |
| Actual ONNX observations and raw/controller actions | Exact numerical equality; no extra inference for measurement |
| Raw/noisy commands and before/after global RNG states | Exact numerical equality; real action noise remained enabled |
| Payload mass from active PhysX | **16.576526641845703 kg**, original physical attributes unchanged |
| Payload displacement after checkpoint | **0.020493092015385628 m** |
| Peak scene net-contact force component | **8832.337890625 N**, including startup transients across the scene |
| Peak physical weld anchor residual, all substeps | **0.0019569807989441267 m** |

The weld residual is a **solver/constraint residual in each original simulation**,
not an inter-run rewind error. Exact replay of that residual does not make the
constraint ideal or physical reality error-free. Contact peaks are not a claim
about the extinguisher's grip force. The reference covers the first task step
onward, not uninstrumented application initialization.

Artifact root: `tmp/replay/reconstruction_final_20260907/`;
`test_factory_fresh_process_rec0/audit/comparison.json` is locomotion and
`test_factory_fresh_process_rec1/audit/comparison.json` is carrying.
The actual integration command was:

```bash
EAI_RUN_NATIVE_REPLAY=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
conda run --no-capture-output -n env_isaaclab python -m pytest -q \
  tools/physics_replay/tests/test_reconstruction_isaac.py \
  tools/physics_replay/tests/test_native_isaac.py \
  --basetemp=tmp/replay/reconstruction_final_20260907
```

Result: **5 passed** (both reconstruction scenarios and the three existing native
integration checks). The native force fixture passing remains a measurement-pipeline
result, not proof that the snapshot backend has zero physical deviation.

A further real process reconstructed the carrying checkpoint, verified all 80
prefix steps, then admitted a new live speed command **0.1 m/s** for 80 more
environment steps. It exited 0 with `released_for_live_continuation=true` and
`original_continuation_verified=false`, correctly refusing to equate a changed
branch with the original future. Payload displacement after this release was
0.021107088774442673 m. Artifact: `tmp/replay/reconstruction_live_release_20260907/`.
The live-release check was then incorporated into the carrying integration test;
rerunning `test_reconstruction_isaac.py` with
`--basetemp=tmp/replay/reconstruction_release_regression_20260907` produced
**2 passed**, covering both scenarios and the carrying test's additional live
branch process.

These are finite-horizon measured equalities on one configuration/platform, not
a proof of all hidden state or universal determinism. In particular, the new
fixture does not cover arbitrary recurrent/stochastic models, sensor histories,
external services, asynchronous EMOS/planner deliveries or a complete mission.
`solver_cache_serialized` and `complete_physical_state_proven` remain **false**.
The old F9 snapshot route has not been replaced by this new reconstruction gate.

Lightweight validation after this round:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
conda run --no-capture-output -n env_isaaclab python -m pytest -q \
  tools/physics_replay/tests \
  demo/fire_rescue/tests/test_replay_cli.py \
  demo/fire_rescue/tests/test_replay_components.py \
  demo/fire_rescue/tests/test_assignment_policy.py \
  demo/fire_rescue/tests/test_task_checkpoints.py
```

Result: **210 passed, 6 skipped**. The skipped checks are opt-in/runtime-dependent;
this result alone is not an Isaac integration or fidelity verdict. New regressions
cover the release gate, 1e-12 drift, variable contact shapes, corrupt/incomplete
data, process/config mismatches, actual single-call policy observation capture,
and a worker that reports an error despite returning exit status zero.

## Fidelity optimization (supersedes the coverage gaps listed below)

The current implementation adds fail-closed policy/noise resource adapters,
in-place named RNG streams, explicit ongoing-wrench source adapters, standard
Isaac Lab noise bias/initialization state, and zero-tolerance original-vs-restored
audits. Physics diagnostics sample after every actual sim.step, including contact
and friction data; no recorded force is injected into the closed loop. Exact mode
uses an ideal physical weld without teleport pre-alignment or artificial payload
mass/damping tuning. Weld authoring checks do not certify dynamic grasp fidelity.

Unit regressions now verify the previously failing memory-policy continuation
(expected/restored action 1.0), independent RNG sequences while preserving object
identity, real Isaac Lab additive-bias initialization and continued nonconstant
noise, and detection of a 1e-12 force difference despite identical boundary state.
Unknown resources and stochastic ONNX graphs are rejected. These checks validate
the adapter/measurement contract, not arbitrary external algorithms.

**Zero-deviation physical replay remains unproven.** PhysX contact/solver caches
are not serialized, arbitrary sensor histories and external services are not
adapted, and the native snapshot audit does not verify policy observations or
distributions. The separate fresh-scene fixture above now observes actual ONNX
inputs/outputs, but it does not certify arbitrary policy distributions.
The zero-tolerance flag cannot make those missing states deterministic; it rejects
observed differences instead of claiming that pose read-back proves fidelity.

### Actual measurements after optimization

The lightweight command used the `env_isaaclab` Python with
`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, running `tools/physics_replay/tests` and the
Fire Rescue `test_replay_cli.py`, `test_replay_components.py`,
`test_assignment_policy.py`, and `test_task_checkpoints.py` suites:
**185 passed, 4 skipped**. Automatic plugin loading initially failed on an unrelated
system-ROS pytest plugin's missing `lark` dependency; no ROS/system packages were changed.

The explicit integration command was
`EAI_RUN_NATIVE_REPLAY=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 conda run --no-capture-output -n env_isaaclab python -m pytest -q tools/physics_replay/tests/test_native_isaac.py`.
It passed **3 functional/instrumentation tests**, including the earlier visual-carry
and task-runner regressions. The new force test deliberately does **not** assert
zero physical deviation; its report separates measurement-pipeline success from
the failed physical-fidelity result. Artifacts: `tmp/replay/fidelity_final.XD11ZE/`.
A final rerun after resource read-back hardening also passed all 3 tests:
`tmp/replay/fidelity_confirm.pSaQrF/`.

The new force fixture warms up 40 environment steps, injects real Gaussian command
noise (standard deviation 0.001) into the M20 ONNX controller, then compares
4 original continuation steps against 4 restored steps. Measurements occur at
each 0.005-second physics step, not just the final pose:

| Measurement | Observed result |
| --- | --- |
| Post-noise M20 command maximum error | **0.0**: the actual noisy inputs repeat in this fixture |
| Maximum root-position trajectory error | `1.8621301e-5 m` (about 18.6 micrometers) |
| Maximum net-contact-force component error | **`16.4847336 N`** |
| Maximum controller-action error | `0.00171575` |
| Peak measured contact / friction force components | `233.3343 N` / `56.6921 N` (not zero probes) |
| Zero-deviation physical result | **False** |

These numbers are from a different, shorter fixture than the historical 100-step
test below; they are not evidence of a before/after accuracy improvement.
The physical-weld fixture verifies unchanged payload attributes (including mass
2.5 kg), consistent local joint orientations, and exclusion from the robot's
articulation topology. It is a USD-authoring check, **not** a dynamic load/grasp test.

The actual headless CLI was also run with `--task-checkpoints --replay-native
--replay-exact --replay-native-test-steps 20` and
`--assignment-policy algorithm.emos.example_policy:assign`.
It failed during exact transform read-back (`max_abs=2.38418579102e-7`, tolerance
zero), before the replay continuation, and exited **1**. This is an intended
strict rejection, not a fidelity pass. Artifact: `tmp/replay/exact_cli.GPwfhN/`.
An earlier 100-step attempt exposed Kit fast shutdown returning exit status zero
despite a validation exception; the demo now posts the failing exit status before
session cleanup, with both a unit regression and the actual CLI check above.

## Pre-optimization coverage audit (historical)

**Immediate physical read-back is not a complete-restoration verdict.** The current
native trajectory reader samples configured root/joint state and targets at completed
environment-step boundaries. It does not compare policy observations, recurrent state,
actual contact forces/impulses or the force/torque history of every physics substep.
The generic adapter has read-only effort/contact channels, but the native target list
does not register those channels. Stored actuator buffers and current actuation forces
are not equivalent to restored PhysX contact/solver caches or a replayed force process.

`EnvironmentBuffersComponent` deliberately retains the `policy` object as a resource
without snapshotting its internals. That is not a statefulness check: an unknown
recurrent/stateful policy can currently pass capture while keeping future hidden state.
Environment `_action_noise_model` and `_observation_noise_model` state is also absent.
The selected M20 controller uses an ONNX wrapper with no explicit recurrent-state
interface; its downloaded inference configuration comments out observation-noise draws.
Consequently the Factory runs do not certify noisy/recurrent RL restoration.

An isolated Conda Python experiment directly exercised the existing snapshot components
(no Isaac scene or RL training was launched):

| Probe | Observed result |
| --- | --- |
| Python/NumPy global RNG sequence after restore | Exact sample equality |
| Torch CPU/CUDA global RNG sequence after restore | Exact sample equality |
| Independent `np.random.default_rng` / `torch.Generator` sequence | Not restored; samples differ |
| Minimal memory-bearing policy, identical observation `1.0` | Reference action `1.0`, restored continuation `1.75` |
| Noise-model state stub with bias `0.2` at capture, then changed to `0.8` | Bias remains `0.8` after restore |

The policy and bias examples are controlled stubs demonstrating missing adapter
coverage, not measurements of the real M20 model. Restoring global RNG states is not
proof that actual environment/policy disturbances are replayed. Native mode does not
route those disturbances through `NoiseTape`. The legacy verifier's
`fire_rescue/verification` sample is a bookkeeping probe, not noise injected into the
robot observation/action path. Independent generators, correlated-noise state and
different sampling order must be covered explicitly.

Default extinguisher attachment is also a modeling boundary: follow mode disables the
original rigid body and creates a non-physical visual proxy. Its successful carry
checkpoint tests do not validate physically loaded grasp/contact dynamics.

The next fidelity acceptance must separate four checks: snapshot state coverage;
policy-only replay of original observations/masks with policy memory and policy noise;
actuator/physics replay of recorded commands and external disturbances at physics-step
resolution; and the actual closed-loop original-versus-restored rollout. Compare first
divergence, observations/actions/memory, poses/velocities, actual torque, contact impulse,
contact events and task outcomes with per-channel units/tolerances. Unsupported state
must be declared unsupported, not accepted merely because physical read-back passes.

Freeze the realization of exogenous random innovations, not noise to zero or one constant
value, and do not replay endogenous contact forces to fake closed-loop equivalence.
Cross-algorithm evaluation shares task/world conditions and controlled external noise;
it must not require different algorithms to produce identical actions or inherit another
algorithm's recurrent state. These broader checks are a required protocol, not currently
implemented/passed validation. The earlier strict trajectory failure still stands.

## Task-owned automatic checkpoints and algorithm callback

The task now defines assignment-applied, stable-carry, rescue-channel-open,
obstacle-removed and mission-success milestones in `task_checkpoints.py`. Safe saves
do not pause execution. The scheduler records trigger/save steps and explicit pending,
missed and not-reached milestones; simultaneous milestones share one physical snapshot.
Scheduler state is checkpointed so restoring an earlier state rearms future milestones
without saving the restored milestone again. Manual save/restore controls remain.

The task runner accepts a stateless high-level assignment callback through
`--assignment-policy module:assign`, retaining EMOS by default. The request is copied
on the simulator thread; callback exceptions or invalid outputs do not silently invoke
a different algorithm. Requests/orchestration state are covered by the existing typed
snapshot machinery; the fixed callback is a code resource, not hidden algorithm memory.
`--task-checkpoints --device cpu --trials 1 --headless` supports unattended single-trial
execution, with the same stage attachment behavior as native visible mode.

The expanded real Isaac regression command was run:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 EAI_RUN_NATIVE_REPLAY=1 \
  conda run --no-capture-output -n env_isaaclab python -m pytest -q -p no:cacheprovider \
  tools/physics_replay/tests/test_native_isaac.py
```

Result: **2 passed**. Artifacts remain untracked under
`tmp/replay/task_checkpoints_final.96Jg0m/`. The carry fixture now automatically saves
the assignment checkpoint at step 0 and carrying at step 90, without manual save
requests. It restores before/carrying/after-release states and checks continued following
and absence of duplicate milestone saves. The second case invokes the actual experiment
runner with `algorithm.emos.example_policy:assign`, no manual commands and a 220-step
bound. Assignment triggers at step 151 and saves at the safe boundary at step 152.
The runner reaches the bound and exits, recording a failed/incomplete mission and
not-reached milestones correctly; **this is not a complete mission-success test**.
Both cases require explicit success JSON written before application shutdown as well
as a zero child exit status. Merely exiting Isaac is not considered test success.

The earlier strict trajectory-accuracy failure still applies and was not rerun here.
No automatic restore/retry, cross-process resume, cross-algorithm shared snapshot or
stateful custom-policy adapter is claimed. These checks exercise automatic task
checkpointing and an actual algorithm/runner boundary, not an end-to-end algorithm
benchmark or proof of zero-drift physical rewind.

## Saving and restoring while carrying

Native checkpoint validation now distinguishes an active pickup/lift from completed
extinguisher carrying. The latter intentionally keeps `_ext_active` and
`is_carrying_extinguisher` true; the old idle-only guard therefore waited indefinitely.
Only native mode opts into stable carrying, using the full UR5 snapshot adapter.
Incomplete/inconsistent carry state, interruption/drop, concurrent arm actions,
pending plans/workers and unsupported obstacle phases remain guarded. Legacy branch
and tape adapters retain their idle-only restriction.

At the carry-only stage, the opt-in Factory test ran the actual `UR5Manager` pickup sequence
and the production native UR5 adapter/validator. Its controlled fixture positions
the original extinguisher at the end effector during GRASP; it does not test autonomous
navigation to the original shelf location. The test checks:

- A save requested during pickup/lifting stays pending, then succeeds while still carrying.
- Restoring a carry checkpoint after movement restores full UR5 state, carrier/arm
  physics, carry targets, proxy transform and original-object visibility/enabled flags.
- Restoring before pickup removes the proxy; returning to the carrying checkpoint
  recreates it and restores the carrying state.
- Releasing and moving the proxy, then restoring, returns to carrying. Subsequent
  single steps retain the carry command and align the proxy with the end effector.
- All six restores (including the earlier two regression cycles) add zero physics
  time. The largest reported numeric read-back error is `2.8168e-7`, below the
  unchanged `1e-5` tolerance. Carry-state restores report at most `2.0899e-7`.

Result: **1 passed** in the actual Isaac 5.1 CPU-PhysX Factory scene. Artifacts are
untracked under `tmp/replay/carry_regression.tJYOeY/`. The proxy moves about 0.26 m in
each five-step follow check; this proves continued following, not trajectory fidelity.
The checks use headless rendering and the real controller/backend, not OS hotkey input
or the complete EMOS/navigation scenario. The earlier asset-metadata, collision-convexHull
fallback and Fabric proxy-removal messages also remain in the log; passing these
assertions does not mean all simulator warnings/errors have been eliminated.

The original-versus-restored trajectory test has not been rerun for this change and
its previously failed strict acceptance below still applies. This feature expands
checkpoint coverage; it does not serialize missing PhysX solver/contact caches or
claim complete, zero-drift physical rewind. Existing demo processes must be restarted
to load this change, and their checkpoint files remain same-process only.

## Pickup restore regression fix

The live demo later reported `unmanaged physics topology changed outside the
registered runtime objects` after extinguisher pickup. Pickup disables the original
`/World/Indoor/SM_FireExtinguisher_02` rigid body and creates a visual proxy. Comparing
only currently enabled rigid bodies incorrectly classified this as deletion.

The fix stores all non-scene rigid-body paths separately from their enabled state
(USD component schema v2), restores original properties and removes the future
proxy, then calls PhysX `flush_changes()` before rebuilding the extra-body tensor
view. `forward()` alone only refreshed kinematics/fabric and was insufficient.
Actual unknown additions/removals are still rejected, with the affected paths.
Pre-write validation failures now leave the previous state and safety-lock status
intact; failures after writes begin still block stepping. Failed attempts overwrite
`last_restore.json` with their phase and `state_may_have_changed` instead of leaving
the previous successful report visible.

At that stage the focused lightweight command reported **130 passed, 1 skipped** (the new
Isaac integration case is skipped unless explicitly enabled). The real Factory
regression was run separately:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 EAI_RUN_NATIVE_REPLAY=1 \
  conda run --no-capture-output -n env_isaaclab python -m pytest -q -p no:cacheprovider \
  tools/physics_replay/tests/test_native_isaac.py
```

Result: **1 passed**. Two consecutive pickup/restore cycles re-enable the original
rigid body, restore visibility, remove the proxy, and support subsequent single
stepping. Physics time does not advance during either restore. Maximum numeric
read-back errors were `1.7923e-7` and `2.8122e-7`; an intentionally added unknown
disabled rigid body is rejected before writes without locking the unchanged state.
The regression runs Isaac in a child process and requires a successful JSON result
written before application shutdown; a child exit code of zero alone is not treated
as a passing test. The final local artifacts are under
`tmp/replay/pickup_regression.GYrqvc/` and are not committed.

This regression validates the pickup restore bug fix, **not** long-run trajectory
accuracy. The original accuracy measurements and limits follow below; they are not
a claim that this fix achieves complete zero-drift physical rollback.

## Scope and result

The native checkpoint UI and supported state restoration work in the Factory demo.
**The strict complete-physics continuation requirement is not met.** Immediate
state read-back passes, but original-versus-restored trajectories still diverge.
Do not describe the implementation as zero-error rollback or substitute a test
between two restored runs for a comparison with the original trajectory.

Host: Ubuntu 22.04, Isaac Sim 5.1, installed Isaac Lab under `env_isaaclab`, RTX 3090
rendering, CPU PhysX, one environment. The Factory scene contains four mobile
robots and three separately registered UR5 articulations. Local initial assignment
fallback is enabled. No browser service is started by native mode.

## Actual simulator command

```bash
PYTHONUNBUFFERED=1 conda run --no-capture-output -n env_isaaclab \
  python -m demo.fire_rescue.main \
  --env EAI-Factory-v0 --device cpu --headless --trials 1 --auto-fire-delay 0 \
  --replay-native --replay-native-test-steps 100 --replay-local-fallback \
  --branch-checkpoint-dir tmp/replay/native_validation_sleep
```

The first safe checkpoint was saved at environment step 152. Verification then
ran the original continuation for 100 steps, restored once, and reran 100 steps
through the normal closed-loop controller/business pipeline. Errors are maxima
over all corresponding frames, not just the endpoint. The original robots moved
approximately 1.0–1.8 meters, so this is not a comparison of two stationary scenes.

| Measurement | Observed maximum |
| --- | --- |
| Immediate root position read-back | `2.0862e-7 m` |
| Immediate root orientation read-back | `1.3328e-7 rad` |
| Immediate joint position/velocity read-back | `0` |
| 100-step robot/arm root position drift | `0.0018484 m` (about 1.85 mm) |
| 100-step robot/arm root orientation drift | `0.0019467 rad` |
| 100-step joint position drift | `0.0312574 rad` |
| 100-step joint velocity drift | `0.7393193 rad/s` |
| Extra extinguisher rigid-body position drift | `2.6656e-7 m` |
| Extra extinguisher velocity drift | `0` |

`last_restore.json` reports `passed: true` at the `1e-5` read-back tolerance.
`trajectory_verification.json` reports `passed: false` at the separate strict
`1e-3` per-channel trajectory threshold. The command exits nonzero as intended for
failed accuracy acceptance. The thresholds were not widened to produce a pass.
The runtime reports and log remain untracked under `tmp/replay/`.

A visible 20-step original-versus-restored test also initialized the native window
and completed its comparison; strict trajectory acceptance failed. Native button
and F5/F9/F8/F10 event routing plus subscription/window cleanup are unit-tested.
Actual OS key delivery was not independently verified by the desktop automation
provider because it did not enumerate the Kit window. At that stage a visible native
launch paused after its initial checkpoint; task-owned automatic saves now continue running.

## Lightweight checks

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 \
  conda run --no-capture-output -n env_isaaclab python -m pytest -q -p no:cacheprovider \
  tools/physics_replay/tests \
  demo/fire_rescue/tests/test_branch_control.py \
  demo/fire_rescue/tests/test_branch_dashboard.py \
  demo/fire_rescue/tests/test_replay_cli.py \
  demo/fire_rescue/tests/test_replay_components.py \
  demo/fire_rescue/tests/test_llm_preset_privacy.py \
  demo/fire_rescue/tests/test_task_checkpoints.py \
  demo/fire_rescue/tests/test_assignment_policy.py \
  --ignore=tools/physics_replay/tests/test_isaac_integration.py
```

Current result: **187 passed, 2 skipped**. Coverage includes automatic milestone
scheduling/restoration/expiry, algorithm request isolation/error handling, task-runner
scope/CLI wiring, stateless callback guards and stable-carry validation,
unsafe/legacy carry rejection, saves that wait until lifting finishes, installed Isaac Lab delay-ring
round trips without launching Isaac, tensor dtype/device/history preservation,
multiple checkpoints, paused restore, single stepping, temporary unsafe saves,
worker completion, failure blocking, corrupt physical schema rejection before USD
mutation, sleep flags, native callbacks and clock cleanup. This suite does not
assert that the real Factory trajectory passes the precision threshold.

`git diff --check` and `python3 -m demo.fire_rescue.main --help` also pass.
The ordinary system-Python run encountered an unrelated missing `openai` dependency
in a pre-existing privacy test. The Conda pytest run initially encountered an
auto-loaded ROS plugin's missing `lark` dependency; disabling third-party plugin
autoload for this pure suite avoided mixing the ROS and simulator Python stacks.
No packages, ROS configuration or system settings were changed to address either.

## Remaining boundary

Saved state now includes delayed-actuator rings, actual drive targets, previous
joint velocity/acceleration, task clocks and physics sleep flags. Restoring sleep
flags prevents a resting extinguisher from being unintentionally woken by pose
writes. PhysX contact/solver caches and internal wake timers are still not saved.
These measurements do not establish the sole cause of every remaining error.

Arbitrary grab/drag checkpoints, cross-process recovery, external service/input
replay and unadapted components remain unsupported. Higher-fidelity work must
address simulator-internal state or investigate reproducible reset-and-reexecution
from an earlier boundary; it cannot be solved merely by changing the UI or relaxing
the error threshold. Such a reset-and-reexecution mode is not implemented here.
