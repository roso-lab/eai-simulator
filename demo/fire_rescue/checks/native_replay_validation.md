# Native replay validation — 2026-09-07

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

The focused lightweight command below now reports **130 passed, 1 skipped** (the new
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
provider because it did not enumerate the Kit window. A regular visible native
launch saves its initial checkpoint and stays paused for manual inspection.

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
  --ignore=tools/physics_replay/tests/test_isaac_integration.py
```

Result: **120 passed**. Coverage includes actual installed Isaac Lab delay-ring
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
