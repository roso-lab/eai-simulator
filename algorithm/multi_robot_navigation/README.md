# EAI Multi-Robot Navigation

This directory contains the complete multi-robot navigation implementation for
EAI Simulator: the native db-CBS planner, synchronized trajectory runtime,
occupancy-map conversion, EAI session adapter, and optional viewport UI. The
caller continues to own the Isaac application and simulation loop.

It does not use a ROS launch file and does not create a second simulator.

## Contents

- `native/`: db-CBS, the customized OMPL revision, dynoplan/dynobench, robot
  models, motion primitives, licenses, and third-party provenance.
- `planner.py`: typed db-CBS problem/result conversion and native execution.
- `map_environment.py`: occupancy-map conversion for db-CBS.
- `session.py` and `trajectory.py`: mission state and synchronized playback.
- `build_native.sh` and `fetch_motion_primitives.py`: native build and verified
  motion-primitive setup.
- `eai_plugin.py`: simulator-session adapter, planner worker, mission state,
  safety checks, and action generation.
- `interaction.py`: pure USD prim-path selection helpers.
- `ui.py`: optional Isaac viewport controls and route visualization.
- `maps/`: maintained occupancy maps for built-in EAI scenes.
- `test_plugin.py`: optional torch-backed tests that use simulator doubles
  and do not start Isaac Sim; they skip cleanly when torch is unavailable.
- `integration.py`: runnable Isaac Sim integration entry point for interactive,
  explicit-goal, and exchange missions.

## Use in any EAI scene

```python
from algorithm.multi_robot_navigation.eai_plugin import (
    EaiMultiRobotNavigationPlugin,
)
from simulator import SimulatorLaunchConfig, open_simulator_session

config = SimulatorLaunchConfig(env="my_env", enable_ros_bridge_extension=False)
with open_simulator_session(config) as sim:
    navigation = EaiMultiRobotNavigationPlugin.from_session(sim)
    try:
        navigation.select_robot("carter_1")
        navigation.set_selected_goal((3.0, -2.0))
        navigation.set_goal("go2_1", (-1.0, 4.0))
        navigation.start_navigation()

        while sim.simulation_app.is_running():
            sim.env.step(navigation.compute_actions())
    finally:
        navigation.close()
```

`from_session()` reads the active scene from EAI's selection data and selects
one of the maps in `algorithm/multi_robot_navigation/maps`. Pass `map_yaml=`
for a custom scene or changed scene geometry.

## Runtime behavior

- Discovers robot instances from `SimulatorSession.possible_agents`.
- Filters CF2X, Iris, Pegasus and other aerial aliases.
- Reads EAI controller metadata and supports velocity-command and
  goal-position ground controllers.
- Plans robot goals as one conflict-aware batch.
- Runs initial db-CBS planning and conflict-triggered replanning on one
  background worker. `start_navigation()` accepts the request immediately;
  its result reports accepted robot goals, not final planner success. Inspect
  `state().planning_error` while stepping the simulation for the final result.
  While a plan is pending, `compute_actions()` remains non-blocking and returns
  hold commands so the Isaac simulation loop continues stepping.
- Uses a built-in per-type radius table aligned with the Nav2 profiles;
  `dbcbs_robot_radii=` can override robot types or individual instances.
- Robots without a goal reserve their current position and remain stopped.
- Validates continuous pairwise clearance after optimization. When real poses
  approach the configured clearance, it holds one control frame, keeps the
  mission goals, and replans from the current poses. Failed replans retain the
  active mission and retry instead of cancelling navigation.
- Exposes selection and pending-goal state for an EAI UI, RViz adapter, or
  another task layer such as EMOS.
- Provides an Isaac viewport UI that selects a robot by clicking its real USD
  model, assigns a goal by clicking a surface, and starts any non-empty subset
  of the ground team from one panel.
- Returns actions only for managed ground robots, so a host can merge commands
  from other components before calling `env.step()`.

Call `close()` when the host shuts down to cancel queued work and release the
planner worker. A running native planner remains bounded by
`dbcbs_planning_timeout` and never runs on the Isaac simulation thread.

The default backend is the db-CBS implementation in this directory. It does not
resolve or import a separate db-CBS checkout. Pass `planner_backend="global"`
only when the older occupancy-grid planner is specifically required.

Build the native EAI core once in `env_isaaclab` before the first db-CBS run:

```bash
cd /path/to/eai-simulator
conda activate env_isaaclab
python -m pip install --no-deps crocoddyl==2.0.2
algorithm/multi_robot_navigation/build_native.sh
```

The native target also requires CMake, a C++17 compiler, Boost, Eigen, FCL,
yaml-cpp, and Crocoddyl. Base revisions, license paths, and motion-primitive
provenance are recorded in `native/THIRD_PARTY.md`.

Run the navigation plugin tests without ambient pytest plugins. The module uses simulator doubles and skips cleanly when `torch` is unavailable; to execute the full suite, run it in `env_isaaclab` or another Python environment with `torch`, NumPy, Pillow (`PIL`), PyYAML, and pytest installed:

```bash
conda activate env_isaaclab
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest --rootdir="$PWD" -q \
  algorithm/multi_robot_navigation/test_plugin.py
```

## Fire Rescue compatibility

`demo/fire_rescue/runtime/algorithm_adapter.py` keeps the historical
`EmosFactoryNavBridge` name as a thin subclass of this component. Existing Fire
Rescue code therefore continues to use the global-planner backend and manage
its aerial agents, while other EAI demos can import the generic plugin
directly.

## Runnable integration test

Open the interactive viewport component:

```bash
cd /path/to/eai-simulator
conda activate env_isaaclab
python -m algorithm.multi_robot_navigation.integration \
  --env dbcbs_slam_team --real-time
```

The interaction sequence is robot click, ground click, then **Start
Navigation**. Repeat the first two clicks for each robot that should
participate. A yellow ring marks the current selection, per-robot colors mark
goals and paths, and the selection ring is removed after planning starts.

Run a non-interactive exchange mission:

```bash
cd /path/to/eai-simulator
conda activate env_isaaclab
python -m algorithm.multi_robot_navigation.integration \
  --env dbcbs_slam_team --exchange --real-time
```
