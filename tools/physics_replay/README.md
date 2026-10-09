# Physics Replay Tool

`physics_replay` records selected Isaac Sim state and restores checkpoints in
the same simulator process. It lives under `tools/physics_replay/` and uses an
existing `open_simulator_session()` instead of creating a second application.

## Replay/GPU scheduler

`execution/scheduler.py` is the resource-admission and session-lifecycle layer.
It does not implement task graphs, EMOS, evaluators, or D1-D6 metrics. The
benchmark layer must create one isolated session/coordinator per scene/branch;
the scheduler never merges mutable physics state for different `branch_key`
values.

`ReplayCapacity` applies all three limits: `max_parallel_scenes`, global
`max_parallel_branches`, and `max_branches_per_scene`. Requests over capacity
queue or are explicitly rejected (`wait=False`); the scheduler never silently
oversubscribes the GPU. Scene and branch leases are idempotently released by
context managers, including worker exceptions. `preflight()` must observe the
actual physics and controller devices before a branch can be admitted. CUDA
requested with CPU physics is `failed`; an unobservable device is
`unavailable`, and neither is formal GPU evidence. Warm-up ticks are audited
but excluded from episode execution time. GPU UUID, memory, PID, capacity,
release and abort information are resource evidence only, not physical success,
paired validity, or causal replay.

`recover_stale()` reclaims only leases whose owner PID has been independently
confirmed dead and emits `stale_lease_recovered`; timeouts never delete a live
owner's lease. Existing `ReplayCoordinator` remains responsible for checkpoint
restore and fidelity reporting.

The Isaac-independent `run_fake_parallel()` smoke verifies distinct mutable
session objects, overlapping active workers, and cleanup after exceptions. It
is scheduler lifecycle evidence only, not GPU capacity or physical task
evidence:

```bash
python3 -m pytest -q tools/physics_replay/tests/test_scheduler.py
```

## Fresh-scene reconstruction journal

`reconstruction.py` adds a separate alternative: **re-execute from a clean initial
scene**, rather than write a partial snapshot at the requested point. The caller
owns the task and simulator lifecycle. `ReconstructionRecorder.append(channels,
task_state=..., checkpoint=...)` streams compressed NumPy frames (including
variable-size contacts), task state and named checkpoint boundaries. `close()`
seals the uninterrupted original; incomplete recordings are not accepted.
Artifacts are checksum-checked, pickle-free, and never overwrite an existing run.

In a different process, construct `ReconstructionVerifier(reference,
fingerprint, checkpoint="extinguisher_carried")`. Rebuild the initial scene,
execute the task and actual policy normally, and call `compare(...)` after every
environment step with all substep measurements. No recorded pose, action or
force is applied by the verifier. `release()` permits live continuation **only at
the exactly matched target boundary**. Alternatively, continue comparing through
the original horizon to audit continuation. A new branch after release is not
certified as the original trajectory. The gate rejects any numerical difference,
shape/dtype/channel mismatch, changed task timing, corrupt data or fingerprint
mismatch. Reconstruction costs the full prefix simulation time; it is not an
instantaneous F9 restore or a serialized solver-cache implementation.

The caller must declare its runtime/config/model/asset fingerprints and include
all relevant inputs, policy memory, noise generators, task events and observations
in the compared channels/state. A user-supplied fingerprint alone is not a proof
of completeness. `complete_physical_state_proven` therefore remains false even
after an exact finite-horizon match. Fresh-process PID checking also does not
replace the caller's obligation to construct a clean scene.

The supported bounded Factory acceptance entry point is:

```bash
python -m demo.fire_rescue.checks.reconstruct_physics --output tmp/replay/carry_reconstruction_01
```

It creates sequential fresh processes through `open_simulator_session`, preserves
the real extinguisher's physical properties, and recomputes the actual ONNX policy
and Gaussian command noise. `PolicyInferenceTrace` observes the calls' real
inputs/outputs without a second inference or random draw. CPU physics force data
is sampled post-solve. The command includes a post-carry checkpoint and an
optional verified-prefix live release; see the Fire Rescue README for commands.
This is not yet the complete Fire Rescue main/EMOS replay path. It does not
silently extend native snapshots to recurrent policies or unadapted sensors.

Run an Isaac-free episode check from the repository root:

```bash
python tools/physics_replay/cli.py inspect --episode tmp/replay/episode_0001
```

Use `validate` in the `env_isaaclab` environment to resolve the recorded Prim
paths against a saved EAI environment:

```bash
python tools/physics_replay/cli.py validate \
  --episode tmp/replay/episode_0001 \
  --env my_env \
  --device cuda:0
```

The Python API accepts `TargetSpec` values with absolute USD Prim paths. A
recorded `frame[t]` is the state immediately before `env.step(action[t])`;
after restoring `t`, callers can replace that action and continue. Root pose,
root velocity, joint state, `joint_target`, and `joint_effort_target` are writable. Contact force and
joint effort are recorded diagnostics and are recomputed on the next physics
step.

Factory Fire Rescue verification is opt-in and uses local EMOS fallback plus a
fixed action/command/noise tape:

```bash
EAI_RUN_FIRE_RESCUE_REPLAY=1 conda run --no-capture-output -n env_isaaclab \
  python -m demo.fire_rescue.main \
  --env EAI-Factory-v0 --headless --trials 1 --auto-fire-delay 0 \
  --replay-verify-dir tmp/replay/fire_rescue \
  --replay-verify-steps 50 --replay-local-fallback --device cpu
```

The verification report separates successful state write-back from post-restore
drift. In this legacy tape workflow, PhysX solver caches and actuator internals are not serialized, so a
`restore.ok` report can still contain `passed: false` with non-zero drift.

Interactive assignment branching uses the same checkpoint store through
`demo.fire_rescue.main --branch-interactive`. It requires the non-headless
Dashboard; see `demo/fire_rescue/README.md` for the edit/start workflow.

The legacy API reports physical-state restore and action replay separately.
PhysX solver caches, controller/planner hidden state, live ROS input, and
unified noise tapes are not restored automatically, so causal replay requires
the later action/communication/noise protocol.

## Live native checkpoints

### State coverage and zero-tolerance audits

`--replay-exact` is an opt-in **audit**, not a promise of bitwise PhysX rollback.
It requires the native CPU, single-environment workflow, enables enhanced PhysX
determinism before scene creation, and uses zero tolerance for read-back and
continuation. Verification must start at a freshly saved checkpoint: comparing
two restored runs is rejected. Any measured difference fails and pauses execution.
`complete_physical_state_restored` and `solver_cache_restored` remain false;
even a finite-window pass does not certify unmeasured state or arbitrary future runs.

Exact verification measures each real physics substep after the solver returns:
body transforms/velocities, joint states, applied actuator efforts, link reaction
wrenches, contact forces/points/normals/separations, friction patches, and actual
computed controller actions. Missing substeps, non-finite samples and exhausted
contact buffers fail rather than appearing as zero error. Contact processing is
enabled explicitly on CPU and its previous setting is restored on scope exit.
Forces are measured, never overwritten with the original force trace. The report
includes the first divergent environment step and substep-qualified channels.
This diagnostic is slower than ordinary checkpointing and does not yet compare
policy observation/distribution traces or reproduce PhysX's private solver caches.

Native checkpoints no longer silently keep a policy's future memory. Trusted
policy, noise, or other resource objects implement this in-process contract:

```python
def capture_replay_state(self):
    return {"hidden": self.hidden, "observation_history": self.observation_history}

def validate_replay_state(self, state):
    if set(state) != {"hidden", "observation_history"}:
        raise ValueError("invalid replay state")

def restore_replay_state(self, state):
    self.hidden = state["hidden"]
    self.observation_history = state["observation_history"]
```

The framework copies supported tensors/arrays and restores the same resource
object. Validation must be side-effect free. Include recurrent memory, filters,
normalizers, counters, private sampling state, and optimizer state if training
continues online. A genuinely stateless resource may explicitly declare
`replay_stateless = True`; this is a trusted author assertion, not automatic proof.
Unknown resources fail closed. The built-in EAI ONNX wrapper is supported only
after graph/identity/configuration checks against a conservative feed-forward
operator allowlist; random, custom, recurrent-interface or uninspectable graphs
require an explicit adapter. Standard Isaac Lab constant/uniform/Gaussian noise
models include mutable parameters, additive bias and first-use expansion state.

Register independent `random.Random`, NumPy `Generator`, and Torch `Generator`
objects in `base_env.replay_random_streams`, using stable source/agent names.
Their states are restored in place along with global RNG states. This reproduces
random realizations under matching sample order; it does not turn noise off or
discover unregistered generators/module globals/external services automatically.
Additional stateful resources can use `base_env.replay_state_resources`.
In exact mode, sensor adapters use keys `sensor/<scene_sensor_name>`, and configured
events require an `event_manager` adapter; registering a name is a trusted claim
that its adapter covers all relevant internal state, not an automatic discovery mechanism.
An ongoing external wrench requires **all** of its generators/timers to be
declared in `base_env.replay_external_wrench_sources` using the same resource
contract; the actuator component then restores both source state and the current
force/torque buffers. Without source adapters, nonzero external wrenches fail.

Exact Fire Rescue mode refuses visual/logical carry checkpoints and uses an ideal
physical fixed joint for extinguisher pickup, without pre-align teleportation,
mass reduction, damping inflation or impulse clipping. It preserves the current
relative orientation when welding. This is a load-bearing ideal weld, **not** a
validated frictional gripper model. Existing visual obstacle proxies and scenes
with unadapted sensor histories are unsupported in exact mode. Ordinary native
mode retains its previous visual-follow demo behavior.

`demo.fire_rescue.main --replay-native --device cpu` provides a native Kit window
and F5/F9/F8/F10 shortcuts without a browser. See the Fire Rescue English/Chinese
READMEs for prerequisites, safe checkpoint restrictions, and the runnable command.
This mode is separate from the legacy tape and assignment-editing workflows.

Fire Rescue now owns automatic logical milestones in `demo/fire_rescue/task_checkpoints.py`.
Assignment, stable carrying, rescue-channel completion, obstacle removal and mission
success request safe snapshots without pausing; manual saves remain available. Scheduler
state is itself checkpointed, so restoring a milestone does not immediately duplicate it
and restoring earlier rearms later milestones. `task_checkpoints.json` records trigger
versus save steps, IDs, pending/expired/not-reached milestones and task outcome.
Use `--task-checkpoints --device cpu --trials 1 --headless` for unattended runs and
`--assignment-policy module:assign` for a stateless assignment callback without editing
the task/launcher/checkpoints. These runs do not automatically restore, retry or share
checkpoints across algorithms/processes; existing precision and state-coverage limits apply.

Native Fire Rescue checkpoints allow stable extinguisher carrying after pickup/lifting
finishes. F5 requested during the lift waits for completion, without requiring release.
The full UR5 controller state and runtime USD proxy are restored together; checkpoints
before pickup and while carrying can be restored in either order, or after release.
F8/F10 continues carrying after restore. Active pickup/drop/interrupt transitions and
unsupported concurrent arm/obstacle operations still wait. Legacy idle-only UR5
adapters do not opt into carrying support. See the opt-in Factory regression in
`tests/test_native_isaac.py`; this checks carry-state restoration and continued following,
not long-run trajectory equivalence.

- `live.py`: completed-step command queue, multiple checkpoint persistence, strict
  read-back validation, pause/single-step, and per-frame continuation comparisons.
- `runtime_state.py`: explicitly typed, non-pickle snapshots of supported Python
  objects, tensors, environment buffers, actuator history and actual PhysX drive targets.
- `stage_state.py`: physical USD attributes, declared runtime subtrees, and extra rigid bodies.
- `native_ui.py`: Kit controls and keyboard subscription; callbacks only enqueue requests.
- `demo/fire_rescue/runtime/native_replay.py`: task-specific adapters, safety checks,
  simulation-time clocks and owned window/clock cleanup.

Unlike legacy physical-only restore, native mode captures the supported controller,
planner and delayed-actuator state explicitly. It still does **not** serialize PhysX
contact/solver caches or external input/service history. Safe in-process restore is
not arbitrary whole-simulator time travel. Read-back success and trajectory success
are separate fields, and failed precision checks block further stepping. Pre-write
validation rejection instead preserves the current state and prior safety-lock status;
`last_restore.json` distinguishes validation, restore and read-back phases. Do not
relax thresholds merely to obtain a passing result or equate two restored runs with
the original trajectory. The reports explicitly identify the comparison used.

Run the focused lightweight suite without starting Isaac:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider \
  tools/physics_replay/tests \
  demo/fire_rescue/tests/test_replay_cli.py \
  demo/fire_rescue/tests/test_replay_components.py \
  demo/fire_rescue/tests/test_branch_control.py \
  --ignore=tools/physics_replay/tests/test_isaac_integration.py
```
