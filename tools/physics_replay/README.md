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
root velocity, joint state, and `joint_target` are writable. Contact force and
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
drift. PhysX solver caches and actuator internals are not serialized, so a
`restore.ok` report can still contain `passed: false` with non-zero drift.

The first version reports physical-state restore and action replay separately.
PhysX solver caches, controller/planner hidden state, live ROS input, and
unified noise tapes are not restored automatically, so causal replay requires
the later action/communication/noise protocol.
