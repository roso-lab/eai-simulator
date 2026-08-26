# EAI Simulator + Nav2

This directory provides the runnable Nav2 integration for EAI Simulator. The simulator publishes odometry and point clouds and consumes /<robot>/cmd_vel. These scripts generate robot-specific Nav2 configuration, complete the TF chain, convert point clouds to laser scans, and launch localization and navigation.

Run commands from the repository root. Isaac Sim uses env_isaaclab; Nav2, RViz, ROS CLI tools, send_goal.py, and tf_bridge.py use the selected system-ROS Python. Keep the two environments separate. The tracked nav2 environment selects the Factory scene, carter_1, Orsus, and Navigation I/O. Orsus and RTX LiDAR publishing require GUI rendering, so this is not a headless smoke test.

## One-command launch

```bash
bash algorithm/nav2/run_nav2.sh
bash algorithm/nav2/run_nav2.sh --rviz
```

The launcher starts simulator.py --env=nav2, waits for the cmd_vel bridge, and starts Nav2 in the selected ROS distribution. Ctrl+C in the `run_nav2.sh` terminal stops the simulator, Nav2, and any RViz process groups created by that launcher. Its bounded cleanup does not use broad `pkill` commands. Logs and generated configuration are written to a fresh 0700 temporary directory.

## Manual launch

Start Isaac Sim with the Conda interpreter:

```bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate env_isaaclab
python simulator.py --env=nav2
```

In a separate system-ROS terminal:

```bash
source /opt/ros/humble/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
ros2 launch algorithm/nav2/nav2.launch.py \\
  robot_name:=carter_1 robot_type:=Carter sensor:=auto scene:=factory rviz:=true
```

Send a goal from a third system-ROS terminal:

```bash
source /opt/ros/humble/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
/usr/bin/python3 algorithm/nav2/send_goal.py --x -5.0 --y -8.0
```

The client returns zero only for STATUS_SUCCEEDED and nonzero for unavailable, rejected, canceled, aborted, or timed-out goals. The default response/result bounds are 10/300 seconds and can be overridden. Ctrl+C in the `send_goal.py` terminal stops only the goal client; stop the Nav2 launch terminal separately, or stop `run_nav2.sh` when it owns the full workflow.

## Map, sensor, and pose selection

`nav2_profiles.yaml` maps all seven selectable scenes to provider-owned `scene/<scene>/<scene>_map.yaml` files below `EAI_USD_ROOT` (default: `<repo>/usd`). Run the simulator/Env DIY asset preflight first; Nav2 validates that both YAML and its referenced image exist and does not synthesize a Plane map. Use `map:=/absolute/path/to/map.yaml` to override the configured map. `sensor:=auto` reads the unique Orsus or lidar attachment for `robot_name` from `tmp/runtime_interfaces.json`. Without `pose`, the same live snapshot supplies the AMCL pose. The snapshot must be version 1, have a live PID, be no older than five seconds, and match the scene and robot. Explicit `sensor:=orsus` or `lidar` and `pose:=x,y,yaw` bypass those checks. Do not enable Orsus and LiDAR publishers simultaneously for one robot because both use `cloud` and `odometry` topics.

## TF and point-cloud handling

tf_bridge.py republishes odometry as dynamic odom -> base_link, publishes base_link -> lidar_link, and forwards cloud to scan_cloud. pointcloud_to_laserscan transforms the cloud to base_link, applies profile filters, and publishes scan. AMCL owns map -> odom.

## Arguments and generated files

nav2.launch.py accepts robot_name, robot_type, sensor, scene, map, pose, runtime_snapshot, and rviz. nav2_setup.py writes nav2_params.yaml, pointcloud_to_laserscan.yaml, view.rviz, and meta.txt to an owner-private temporary directory by default. Generated files are runtime output and must not be committed.
