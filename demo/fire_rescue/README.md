# Fire Rescue Demo

[Chinese](README.zh-CN.md)

This is a robot-only fire-rescue experiment. The demo does not register a Gym environment, start an experiment supervisor, or persist experiment records.

## Launch

```bash
cd eai-simulator
conda activate env_isaaclab

python -m demo.fire_rescue.main \
  --env=EAI-Factory-v0 \
  --device=cuda:0 \
  --trials=1 \
  --trial-hazard-ids=1 \
  --auto-fire-delay=0 \
  --emos-llm-preset=zhipu-glm4-flash
```

After a normal non-headless launch (without `--replay-native`), the terminal prompts you to open:

```text
http://127.0.0.1:8767/
```

The Dashboard data service uses port `8767`; the Isaac Sim live-image WebSocket uses port `8766`.

## Native checkpoint controls (experimental)

Use the Isaac Sim window directly, without either browser service:

```bash
python -m demo.fire_rescue.main \
  --env EAI-Factory-v0 --device cpu --trials 1 --auto-fire-delay 0 \
  --replay-native --replay-local-fallback \
  --branch-checkpoint-dir tmp/replay/fire_rescue_native
```

The **EAI Physics Checkpoints** window provides:

| Control | Behavior |
| --- | --- |
| Save / F5 | Save another checkpoint at the next safe completed environment-step boundary. |
| Checkpoint dropdown | Select an earlier checkpoint without discarding later checkpoints. |
| Restore / F9 | Restore the selected checkpoint and remain paused. |
| Pause/Run / F8 | Pause the demo loop or continue from the current state. |
| Step / F10 | Execute one complete environment step, including controller/physics decimation, then pause. |
| Verify 20 steps | Compare two continuations frame by frame, report drift, then pause. |

Focus the Isaac window for shortcuts. Use these controls instead of Kit's built-in
timeline Stop/Play buttons, which have different reset semantics. The first safe
checkpoint after assignment is saved automatically and pauses for inspection. A pending save does not skip
normal physics or robot-arm updates. Restoring waits for active background workers
to finish, discards their obsolete results, and never automatically resumes.

Native mode requires CPU PhysX and one environment. Rendering still uses the GPU.
It cannot be combined with `--branch-interactive` or `--replay-verify-dir`.
`--replay-local-fallback` avoids the initial LLM allocation call; it is not a general
record/replay protocol for later external services, ROS input, or manual input.
No assignment JSON editor is included in this first native version.

### State coverage and precision boundary

This is an **observable-state and supported runtime checkpoint**, not a complete
PhysX solver serialization. It captures registered articulations/rigid objects,
UR5 joint state, position/velocity/effort targets, actual PhysX drive targets,
implicit/PD/DC/delayed-PD actuator state (including delay rings and their pointers),
joint-acceleration history, environment commands/counters, Python/NumPy/Torch RNG,
navigation queues/tracking/reservations, supported obstacle/UR5/EMOS state, and
simulation-time task clocks. It also captures physical USD attributes, declared
runtime objects, light state, and non-scene rigid-body poses/velocities such as the
extinguisher, including physics sleep flags. Pausing freezes task clocks; restoring rewinds them.

Only safe checkpoints are accepted: no active worker/pending planning result,
unsupported UR5 grab/drag phase, active external wrench, deformable, or unadapted
actuator. Unknown Python state fails explicitly rather than being silently omitted.
Topology changes outside the declared runtime objects are not supported. Checkpoints
are restricted to the creating process/session, not a cross-process resume format.
Camera/ROS/OS input history and PhysX contact/solver caches are not restored.

`--replay-restore-tolerance` defaults to `1e-5`. It checks immediate read-back,
**not future trajectory equivalence**. Failure after restore writes begin, or a failed
trajectory accuracy check, blocks further steps until a valid checkpoint is restored.
A rejected pre-write validation leaves the current simulation unchanged and paused;
F8/F10 can continue it unless an earlier failure already made its state unsafe.
`last_restore.json` records the failure phase and whether state may have changed,
instead of leaving a stale successful report. The trajectory checker
uses a separate strict `1e-3` threshold for every numeric channel: pose position is
in meters, orientation in radians, other values in their channel's native units.
Passing position error alone is insufficient if velocity or joint errors fail.
These are acceptance limits, not achieved-accuracy promises.

The verification report distinguishes `original_vs_restored` (verification starts
immediately after saving, before another step) from `two_restored_runs` (an older
checkpoint is restored first). The latter only measures repeatability of restored
runs and must not be presented as fidelity to the original uninterrupted trajectory.
All frames are checked, including non-scene rigid bodies; maximum errors and reference
motion are recorded. Long-run/contact-rich error can grow even when read-back passes.

For a bounded headless accuracy check, add `--headless --replay-native-test-steps 100`
to the launch command. This explicit test mode exits after verification, fails with
a nonzero exit status if strict limits are exceeded, and times out after 180 seconds
inside the demo loop. It does not bound application startup or individual blocking calls.
Normal native use requires a visible window. Output is under the printed per-trial
directory: `last_restore.json`, `trajectory_verification.json`, and checksum-protected
JSON/NPZ component checkpoints. Repeated launches preserve previous trial directories.

Extinguisher pickup disables the original rigid body and uses a visual proxy. Native
restore treats its enable flag as restorable state, not a deleted body, removes the
future proxy, restores the original properties, and flushes buffered PhysX changes
before accessing the rigid-body view. It does not advance physics to recreate the body.
Actual additions/removals outside declared runtime objects remain rejected, with
the affected paths in the error. USD runtime checkpoints use component schema v2;
restart an older running demo to use the fix (old session checkpoints are not portable).

## Interactive assignment branches

Use the non-headless Dashboard mode to run the fact episode to completion, edit the
first EMOS assignment, and continue from the same in-process physical checkpoint:

```bash
python -m demo.fire_rescue.main \
  --env=EAI-Factory-v0 --device=cpu --trials=1 --auto-fire-delay=0 \
  --branch-interactive \
  --branch-checkpoint-dir=tmp/replay/fire_rescue_branch
```

After the fact run succeeds, fails, or times out, edit the assignment JSON in the
EMOS panel, click **提交修改**, and then **开始分支**. The branch restores robot pose,
velocity, joint state and targets, navigation queues, supported obstacle state, and
RNG state in the same simulator process before dispatching the edited assignment.

`--branch-interactive --headless` is rejected because no Dashboard queue can receive
the edit. For the Factory smoke path, add `--replay-local-fallback` to avoid LLM
network latency. If an assignment arrives while an unsupported UR5 drag phase is
active, the tool reports `checkpoint_pending` and waits for the next safe checkpoint.
Reusing the same output root is safe: an existing `trial_01` is preserved and the
next attempt is written to `trial_01_run_02`, then `trial_01_run_03`, and so on.

## Environment loading

External callers still use `--env=<name>`, but the name identifies a JSON file rather than a Gym registry entry:

```text
--env=EAI-Factory-v0
        |
        v
source/EAI_hmrs/EAI_hmrs/envs/EAI-Factory-v0.json
        |
        v
simulator.py
        |
        |-- EAI.hmrs_env.env_diy.storage.load_task()
        |-- EAI.hmrs_env.env_diy.flow.interactive_selection_from_dict()
        `-- EAI_hmrs.env_builder.build_interactive_env_cfg_from_selection()
        |
        v
EAI.hmrs_env.MultiRobotDirectEnv
```

`simulator.py` no longer checks for a registered Gym environment and does not call `load_cfg_from_registry()` or `gym.make()`.

The Factory occupancy map is a scene-provider asset at `EAI_USD_ROOT/scene/factory/factory_map.yaml` with its sibling PNG. `default_factory_map_yaml()` ensures that external pair through `EAI_assets.asset_resolver`; the Demo does not own or fall back to files under `demo/fire_rescue/assets/`.

## Factory JSON requirements

`source/EAI_hmrs/EAI_hmrs/envs/EAI-Factory-v0.json` declares the Factory scene and four robots:

- `carter_1`: Carter differential-drive base with Orsus data capture.
- `m20_1`: M20 with UR5.
- `m20_2`: M20 with UR5.
- `scout_1`: Scout with UR5, responsible for the rescue-channel button.

The attachments use the minimum experiment configuration:

- Only `carter_1` carries Orsus.
- `m20_1`, `m20_2`, and `scout_1` carry only the UR5 required by their tasks.
- Fire Rescue does not use Nav2. The JSON does not attach `navigation_io`, and startup does not load the Isaac ROS Bridge.

Robot instance names are generated by the EAI builder from robot type and occurrence order. Fire Rescue keeps the shared JSON naming rule and uses the default generated name `scout_1`.

The JSON may optionally provide `spawn_pose`:

```json
{
  "spawn_pose": {
    "position": [1.0, 2.0, 0.5],
    "rotation": [1.0, 0.0, 0.0, 0.0]
  }
}
```

Without `spawn_pose`, the EAI builder uses the scene default layout.

This experiment needs a fixed initial layout, so `SimulatorLaunchConfig.env_cfg_hook` writes spawn positions before environment creation. The experiment positions are defined by `ROBOT_SPAWN_POSES` in `config.py` and do not alter the shared EAI JSON environment.

## Simulator interface

`main.py` constructs `SimulatorLaunchConfig` and then calls:

```python
with open_simulator_session(launch_config) as sim:
    run_robot_baseline_experiment(sim, demo_config)
```

`SimulatorSession` supplies the experiment with:

- `simulation_app`: the Isaac Sim application.
- `env`: the EAI multi-robot environment.
- `base_env`: the unwrapped `MultiRobotDirectEnv`.
- `env_cfg`: the configuration built dynamically from JSON.
- `possible_agents`: robot instance names generated from the JSON environment.
- `device`, `num_envs`, and the external environment name.

Fire Rescue accesses the simulator only through this session and does not create its own Isaac Sim or Gym environment.

## Algorithm integration

### EMOS

Task allocation uses the standalone algorithm package:

```python
from algorithm.emos.engine import EMOSDiscussionManager
```

`scenario.py` defines fire tasks, robot capabilities, task constraints, and default assignments. EMOS discusses and allocates tasks using current robot positions and capabilities.

### Global Planner

Path planning uses the standalone algorithm package:

```python
from algorithm.global_planner.session import GlobalNavSession
```

`runtime/algorithm_adapter.py` only adapts EAI simulator poses and controller tensors to the planner. Planning, path tracking, and velocity-command calculation remain in `algorithm/global_planner`.

Fire Rescue uses `GlobalNavSession.plan_batch()` by default for spatiotemporal conflict planning when multiple robots are dispatched together. Before `env.step()`, the control loop also predicts short-horizon motion from robot radii, current positions, and velocities. On conflict, lower-priority robots yield; inside the hard safety distance, both robots stop and trigger asynchronous grouped replanning. Fire-adjacent tasks use robot-specific stopping points with spacing for the mounted UR5 envelope. For hazard 1, the Scout stopping point also avoids the M20 extinguisher transport corridor. Standby and safety-stop transitions clear residual base velocity so robots do not drift after arrival.

Manipulator actions and base navigation are staged. Each M20 UR5 approaches above the target, descends, and grips before the extinguisher may attach. It then raises the shoulder and folds the elbow into a compact carrying pose, which remains commanded during transport. The base is released only after lifting completes; it exits through the pickup corridor before planning toward the fire. The visual extinguisher proxy that follows the arm has no physical collision. Scout holds its base while pressing the rescue-channel button, then follows one continuous path out of the button area and toward the fire without stopping at a fixed reverse waypoint to switch tasks.

## Experiment flow

```text
main.py
  `-- experiment.py
        |-- create the EMOS manager
        |-- select the hazard ID
        `-- runtime/experiment_loop.py
              |-- start the Dashboard and image stream
              |-- create the fire marker
              |-- trigger EMOS task allocation
              |-- call global_planner for paths
              |-- control navigation, UR5, and obstacle rescue
              `-- evaluate mission success
```

Fire locations are often inside obstacles or otherwise unreachable. Final fire tasks therefore plan to a reachable point within `3 m` of the fire rather than forcing a path to its center.

## Layout

```text
demo/fire_rescue/
|-- README.md                 # English documentation
|-- README.zh-CN.md           # Chinese documentation
|-- main.py                   # CLI and SimulatorSession entry point
|-- experiment.py             # trial orchestration and env_cfg hook
|-- config.py                 # demo settings, targets, and spawn positions
|-- scenario.py               # EMOS scenario, capabilities, and task interpretation
|-- algorithm_paths.py        # local algorithm imports and provider Factory map
|-- llm_compat.py             # OpenAI compatibility repair
|
|-- dashboard/
|   |-- index.html            # robot-only monitoring page
|   |-- design-tokens.css     # page styles
|   |-- server.py             # port 8767 data and static-file service
|   `-- stream.py             # port 8766 Isaac Sim image stream
|
`-- runtime/
    |-- experiment_loop.py    # per-frame experiment loop
    |-- navigation.py         # robot task queues and navigation execution
    |-- algorithm_adapter.py  # EAI to global_planner adapter
    |-- ur5.py                # M20/Scout UR5 execution
    |-- obstacle_rescue.py    # dynamic-obstacle rescue flow
    |-- rescue_llm.py         # rescue-robot selection
    |-- mission_success.py    # unified success criteria
    |-- sim_helpers.py        # pose and light helpers
    |-- fire.py               # fire visual marker
    |-- input.py              # keyboard input
    |-- settings.py           # navigation, fire, UR5, and port constants
    `-- llm_presets.py        # LLM preset configuration
```

## Completion conditions

The experiment ends when:

- the extinguisher task completes;
- the rescue-channel button task completes;
- required dynamic-obstacle rescue completes;
- robots required by the success criteria enter the `3 m` fire neighborhood;
- the internal mission timeout is reached; or
- the user closes Isaac Sim.

The CLI has no `--max-steps` option and does not stop early after a fixed number of environment steps.

## Headless mode

Add:

```bash
--headless
```

Headless mode does not start the Dashboard or Isaac Sim image stream.
