# Multi-Robot Navigation (db-CBS)

EAI's multi-robot navigation component generates conflict-aware trajectories for ground robots in one scene and tracks them inside a single `SimulatorSession`. It uses db-CBS by default and lets you assign goals to multiple robots through either a visual panel or the command line.

The component does not require ROS or Nav2. Existing ROS/Nav2 workflows remain available as an independent navigation option.

## Feature Overview

- Plan mutually conflict-free trajectories for multiple ground robots in one batch.
- Select robots and goals directly in the Isaac Sim viewport.
- Assign goals to part of the team from the command line, or cycle the full team through their initial positions.
- Monitor robot clearance during execution, hold the team when robots get too close, and replan from current positions.
- Start with a built-in three-robot Factory example containing Carter, Scout, and Go2.

The component currently requires `num_envs=1`. It excludes aerial types such as CF2X, Iris, and Pegasus by default and manages ground articulations only.

## Runtime Flow

```text
EAI selection + current robot poses + per-robot goals
        ↓
Occupancy map → obstacle boxes + planning frame
        ↓
db-CBS batch planning with inter-robot conflict constraints
        +
Ground robots without goals reserved as stationary obstacles
        ↓
dynoplan optimization → synchronized double-integrator trajectories
        ↓
Continuous clearance validation → per-frame velocity/position commands
        ↓
Team hold on close approach → background replan → resume execution
```

Initial planning and runtime replanning run on one background worker. While a plan is pending, `compute_actions()` returns hold commands so the native planner never blocks the Isaac Sim frame loop.

## First Build

Prepare Isaac Lab's `env_isaaclab` environment as described in the [Installation Guide](installation_en.md). The native db-CBS target also needs CMake, C++17, Boost, Eigen, FCL, yaml-cpp, and Crocoddyl. The build script resolves these libraries only from the active Conda environment, `/opt/openrobots`, `/opt/ros/humble`, and existing loader paths; it does not install system packages.

```bash
cd /path/to/eai-simulator
conda activate env_isaaclab
python -m pip install --no-deps crocoddyl==2.0.2
EAI_DBCBS_BUILD_JOBS=8 algorithm/multi_robot_navigation/build_native.sh
```

After a successful build, the script automatically prepares the motion primitives required by db-CBS. If they are not installed locally, it downloads them from the location provided by upstream db-CBS and verifies both file size and SHA-256. Existing files are verified and reused.

## Interactive Navigation

Launch the three-robot Factory scene:

```bash
python -m algorithm.multi_robot_navigation.test.main \
  --env dbcbs_slam_team --real-time
```

When neither `--goal` nor `--exchange` is supplied, the demo opens the `EAI Multi-Robot Navigation` panel:

1. Click a ground robot in the Isaac Sim viewport.
2. Click the floor or another pickable surface to assign a world-coordinate goal.
3. Repeat for every robot that should participate.
4. Click **Start Navigation** to submit all assigned goals as one batch.

A yellow ring marks the current selection. Per-robot colors show goals and remaining paths. Ground robots without goals stay stopped and reserve their current positions during planning.

Interactive mode needs a visible viewport and cannot be combined with `--headless`.

## Command-Line Missions

Repeat `--goal ROBOT:X,Y` to assign only part of the team:

```bash
python -m algorithm.multi_robot_navigation.test.main \
  --env dbcbs_slam_team \
  --goal carter_1:3.0,3.0 \
  --goal go2_1:-2.0,1.0 \
  --real-time
```

Cycle every ground robot to the next robot's initial position:

```bash
python -m algorithm.multi_robot_navigation.test.main \
  --env dbcbs_slam_team --exchange --real-time
```

Non-interactive missions can use `--headless`. `--max-seconds` controls the mission timeout and `--hold-seconds` controls how long the simulation remains open after completion; neither changes the native planner timeout.

## Maps

`from_session()` selects a map under `algorithm/multi_robot_navigation/maps/` from the environment JSON's `scene_key`. The built-in scene keys are:

```text
airs, desert, factory, garden, hospital, plane, warehouse
```

Pass a matching map explicitly when scene geometry changes or when using a custom scene:

```bash
python -m algorithm.multi_robot_navigation.test.main \
  --env my_env \
  --map-yaml /absolute/path/to/map.yaml \
  --goal carter_1:2.0,-1.0 \
  --real-time
```

Maps use the common occupancy-grid YAML fields `image`, `resolution`, `origin`, `occupied_thresh`, `free_thresh`, and `negate`. The image and origin must match EAI world coordinates. The current db-CBS conversion requires a zero-yaw map origin.

## Planning and Safety Semantics

Each robot has a planar bounding radius. Defaults are resolved by robot type and can be overridden by type or instance name through `dbcbs_robot_radii`. Planning also adds `dbcbs_safety_margin`:

```text
effective radius = robot radius + safety margin
required pair separation = left effective radius + right effective radius
```

A replacement trajectory is installed only when every goal in the batch succeeds. The optimized result is also checked for pairwise clearance over linearly interpolated continuous segments. A result that violates effective radii is rejected instead of being sent to controllers.

During execution, when a mission robot approaches another ground robot, the whole team first receives hold commands. The component then replans the original mission goals from current poses on the background worker. A failed replan does not cancel the mission; goals are retained and retried after the configured interval.

For db-CBS, `start_navigation()` acknowledges planner submission rather than final planner success. Read `state().planning` and `state().planning_error` for the final outcome.

## Integrating an Existing Session

```python
from algorithm.multi_robot_navigation.eai_plugin import (
    EaiMultiRobotNavigationPlugin,
)
from simulator import SimulatorLaunchConfig, open_simulator_session

config = SimulatorLaunchConfig(
    env="dbcbs_slam_team",
    enable_ros_bridge_extension=False,
)

with open_simulator_session(config) as simulator:
    navigation = EaiMultiRobotNavigationPlugin.from_session(simulator)
    try:
        navigation.set_goal("carter_1", (3.0, -2.0))
        navigation.set_goal("go2_1", (-1.0, 4.0))
        navigation.start_navigation()

        while simulator.simulation_app.is_running():
            simulator.env.step(navigation.compute_actions())
            state = navigation.state()
            if state.planning_error:
                raise RuntimeError(state.planning_error)
    finally:
        navigation.close()
```

`compute_actions()` returns commands only for managed ground robots. A host that also controls aerial robots or other agents must merge commands from its other components before `env.step()`. Always call `close()` during host shutdown to cancel queued work and release the planner worker. A running native process remains bounded by `dbcbs_planning_timeout`.

## Key Parameters

| Parameter | Default | Purpose |
| --- | ---: | --- |
| `planner_backend` | `"dbcbs"` | Use the default db-CBS backend; `"global"` is retained only for the legacy compatibility path. |
| `dbcbs_planning_timeout` | `60.0` | Maximum seconds for one native plan or replan. |
| `dbcbs_robot_radii` | `None` | Override planar radii by robot type or instance name. |
| `dbcbs_safety_margin` | `0.10` | Safety margin added to each robot radius, in metres. |
| `dbcbs_coarsen_factor` | `4` | Occupancy-grid downsampling factor before obstacle-box conversion. |
| `dbcbs_replan_clearance` | `0.25` | Extra distance beyond hard clearance that triggers an early replan. |
| `dbcbs_replan_retry_interval` | `0.50` | Minimum retry delay after a failed replan, in seconds. |
| `exclude_aerial` | `True` | Exclude known aerial robot types from the managed navigation team. |

## Current Limitations

- The planning model is a planar double integrator with a fixed `0.1 s` step. Height and full rigid-body dynamics are outside the db-CBS problem.
- The occupancy map is static. The component reacts to robot-to-robot proximity but does not update obstacle cells from sensors.
- A custom scene needs a map aligned with its world coordinates and geometry.
- Only one parallel environment is supported; `num_envs > 1` is rejected.

## Sources and Acknowledgments

This integration is based on [IMRCLab/db-CBS](https://github.com/IMRCLab/db-CBS) (MIT).
