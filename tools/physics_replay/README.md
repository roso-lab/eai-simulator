# Physics Replay Tool

`physics_replay` records selected Isaac Sim state and restores checkpoints in
the same simulator process. It lives under `tools/physics_replay/` and uses an
existing `open_simulator_session()` instead of creating a second application.

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

`demo.fire_rescue.main --replay-native --device cpu` provides a native Kit window
and F5/F9/F8/F10 shortcuts without a browser. See the Fire Rescue English/Chinese
READMEs for prerequisites, safe checkpoint restrictions, and the runnable command.
This mode is separate from the legacy tape and assignment-editing workflows.

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
