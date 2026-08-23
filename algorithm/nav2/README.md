# EAI Simulator + Nav2

This directory contains the runnable Nav2 integration for EAI Simulator. The
simulator publishes odometry and point clouds and consumes
`/<robot>/cmd_vel`; the scripts here generate robot-specific Nav2
configuration, provide the missing TF chain, convert point clouds to laser
scans, and launch localization and navigation.

Run all commands from the repository root.

## Keep Simulator and ROS Processes Separate

Isaac Sim runs with the Python from Conda environment `env_isaaclab`. Nav2,
RViz, ROS command-line tools, `send_goal.py`, and `tf_bridge.py` run with the
Python supplied by the selected system ROS installation. Do not run the ROS
programs with the Conda Python or activate `env_isaaclab` in their shell.

The tracked environment `source/EAI_hmrs/EAI_hmrs/envs/nav2.json` selects the
Factory scene, a Carter instance named `carter_1`, Orsus, and Navigation I/O
(stored under the internal key `navigation_io`).
Orsus and RTX LiDAR publishing require the simulator's GUI rendering path, so
this workflow is not a headless smoke test.

## One-command Launch

The launcher script creates the two process environments, starts
`simulator.py --env=nav2`, waits for the cmd_vel bridge, and then starts Nav2
with the selected system ROS distribution:

```bash
bash algorithm/nav2/run_nav2.sh
bash algorithm/nav2/run_nav2.sh --rviz
```

Press Ctrl+C in the `run_nav2.sh` terminal to stop the workflow. The cleanup
handler sends signals only to the simulator and Nav2 process groups started by
this script, waits for them, and escalates only those groups if they do not
exit. Further Ctrl+C presses are ignored while that bounded cleanup finishes,
so the forced-stop phase cannot be interrupted. Do not use broad process-name
`pkill` commands for cleanup. After startup, an unexpected exit from either the
simulator or Nav2 also ends the launcher and cleans up the remaining process
group. Ctrl+C in the `send_goal.py` terminal stops only the goal client; it
does not own or stop Nav2 or Isaac Sim.

The Nav2 child runs in an otherwise clean `env -i` environment. The launcher
forwards caller-defined `ROS_DOMAIN_ID`, `ROS_LOCALHOST_ONLY`,
`ROS_AUTOMATIC_DISCOVERY_RANGE`, `ROS_STATIC_PEERS`, and `CYCLONEDDS_URI` so
that Nav2 uses the same explicit discovery configuration as the simulator. It
still selects `rmw_cyclonedds_cpp` for the Nav2 child.

Logs and generated configuration are written under a fresh per-run directory created with `mktemp -d` and mode `0700` (for example `${TMPDIR:-/tmp}/eai-nav2-run.XXXXXX`). The one-command launcher prints the exact simulator log, Nav2 log, and generated-config paths for that run.

## Manual Launch

Start the simulator in one terminal:

```bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate env_isaaclab
python simulator.py --env=nav2
```

In a separate terminal where Conda is not active, source the selected system
ROS installation and launch Nav2. Humble is shown here; use Jazzy only when
that is the bridge selection installed for this repository.

```bash
source /opt/ros/humble/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
ros2 launch algorithm/nav2/nav2.launch.py \
    robot_name:=carter_1 robot_type:=Carter sensor:=auto scene:=factory rviz:=true
```

Send a goal from another system-ROS terminal:

```bash
source /opt/ros/humble/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
/usr/bin/python3 algorithm/nav2/send_goal.py --x -5.0 --y -8.0
```

`send_goal.py` selects CycloneDDS when `RMW_IMPLEMENTATION` is unset and
refuses to send when that variable explicitly selects another RMW. This keeps
its action request and response compatible with the Nav2 process started by
`run_nav2.sh`. The command waits up to 10 seconds for the goal response and,
by default, 300 seconds for the navigation result. Override those bounds with
`--goal-response-timeout` and `--result-timeout`; a result timeout requests
goal cancellation and returns nonzero.

The command exits with status 0 only when Nav2 reports
`GoalStatus.STATUS_SUCCEEDED`. An unavailable server, rejected goal, canceled
goal, aborted goal, or timeout returns a nonzero status. Ctrl+C returns 130 and
stops only this client. If the goal-response timeout is reached, acceptance is
unknown; inspect the Nav2 log before sending another goal.

RViz can also be launched separately after Nav2 is running:

```bash
ros2 launch algorithm/nav2/rviz.launch.py \
    robot_name:=carter_1 robot_type:=Carter scene:=factory
```

Stop manual launches with Ctrl+C in each terminal. Let each process complete
its normal shutdown before closing the simulator.

## Map, Sensor, and Pose Selection

`nav2_profiles.yaml` maps `factory` to the tracked occupancy map
`demo/fire_rescue/assets/factory_map.yaml`; its referenced
`factory_map.png` is tracked beside it. The `plane` scene generates a blank map
inside the selected owner-private output directory. For another
scene, pass `map:=/absolute/path/to/map.yaml` or add a maintained map to the
profile.

By default, `sensor:=auto` reads `tmp/runtime_interfaces.json` and requires
exactly one supported `orsus` or `lidar` attachment for `robot_name`. With no
`pose` argument, the same snapshot supplies the robot's current world pose for
AMCL. The snapshot must have version 1, a live simulator PID, a heartbeat no
more than five seconds old, and matching scene and robot entries. The simulator
refreshes the snapshot while it runs and removes its own snapshot during
normal shutdown.

Use explicit values when the sensor and spawn pose are already known:

```bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
ros2 launch algorithm/nav2/nav2.launch.py \
    robot_name:=carter_1 robot_type:=Carter sensor:=orsus scene:=factory \
    pose:=-3,0,0 rviz:=true
```

`sensor:=orsus` or `sensor:=lidar` bypasses attachment auto-detection, and
`pose:=x,y,yaw` bypasses pose lookup. Verify explicit values against the active
simulation; they deliberately replace those snapshot checks. Do not enable
Orsus and LiDAR publishers simultaneously for one robot because both use
`/<robot>/cloud` and `/<robot>/odometry`.

## TF and Point-cloud Handling

The sensor output does not provide the complete REP-105 chain expected by
Nav2. `tf_bridge.py` performs three operations:

- republishes `/<robot>/odometry` as dynamic `odom -> base_link` TF;
- publishes the profile-specific static `base_link -> lidar_link` transform;
- republishes `/<robot>/cloud` to `/<robot>/scan_cloud` with frame
  `lidar_link`.

`pointcloud_to_laserscan` then transforms the cloud into `base_link`, applies
the profile's height and minimum-range filters, and publishes
`/<robot>/scan`. AMCL owns `map -> odom`; the bridge does not publish that
transform. Debug the processed data through `/<robot>/scan`, rather than using
the unfiltered source cloud as a Nav2 obstacle view.

## Launch Arguments

- `robot_name`: simulator instance and ROS topic namespace, such as
  `carter_1` or `go2_1`.
- `robot_type`: key in `robot_profiles`; when omitted, setup infers it from the
  instance name and falls back to `default_profile` if necessary.
- `sensor`: `auto` (default), `orsus`, or `lidar`.
- `scene`: key used for map selection and runtime-snapshot validation.
- `map`: optional map YAML override.
- `pose`: optional simulator-root pose `x,y,yaw`; setup converts it to the
  configured Nav2 base point.
- `runtime_snapshot`: defaults to `tmp/runtime_interfaces.json`.
- `rviz`: start RViz when `true`.

`nav2_setup.py` writes `nav2_params.yaml`,
`pointcloud_to_laserscan.yaml`, `view.rviz`, and `meta.txt` to an
owner-private directory. Without `--out`/`out_dir:=...` it creates a unique
`0700` directory in the system temp area; explicit output directories are
accepted only when they are directories owned by the current user and
inaccessible to group/other. Generated-file writes refuse symlink targets. The
launch files consume those generated files; they are runtime output and must not
be committed.
