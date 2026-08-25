# ROS2 Operational Clients

[Chinese](README.zh-CN.md)

These programs are external ROS2 clients for inspecting sensors and sending commands to a running EAI Simulator scene. They do not launch Isaac Sim, create robots, or enable ROS publishers and subscribers. Start the simulator with the required interfaces first, then run these tools in a separate system-ROS shell.

## Environment

Humble on Ubuntu 22.04 is the validated baseline. Replace `humble` only when the selected system ROS2 environment is already installed and matches the simulator bridge. Use the system ROS Python rather than `env_isaaclab`:

```bash
source /opt/ros/humble/setup.bash
command -v python3
python3 -c "import rclpy; print(rclpy.__file__)"
```

`vis_sensors.py` also needs NumPy, OpenCV, `cv_bridge`, `sensor_msgs`, and `point_cloud2`. The command clients need the standard ROS2 message packages they import. Install these through the selected ROS distribution or its managed Python environment.

## Clients

### Sensor visualization

`vis_sensors.py` displays camera/depth images and a top-down point-cloud view. `auto` discovers all `sensor_msgs/msg/Image` topics; `camera` scopes discovery to a namespace; `orsus`, `realsense`, and `lidar` use their expected topics. Explicit sensor modes default to the legacy `/isaac` namespace unless `--namespace` is supplied.

```bash
/usr/bin/python3 tools/ros2/vis_sensors.py --help
/usr/bin/python3 tools/ros2/vis_sensors.py --sensor camera --namespace /iris_1
/usr/bin/python3 tools/ros2/vis_sensors.py --sensor realsense --namespace /mushr_1
```

Non-8-bit images are scaled from finite values for display; `NaN` and infinite pixels render black. RealSense IMU data is reported but not visualized.

### Mobile-base velocity

`send_cmd_vel.py` publishes `geometry_msgs/msg/Twist` to `/<robot>/cmd_vel`. `--linear` is `linear.x` in m/s, `--angular` is `angular.z` in rad/s, and `--rate 0` publishes once. A positive rate publishes continuously until Ctrl+C.

```bash
/usr/bin/python3 tools/ros2/send_cmd_vel.py --help
/usr/bin/python3 tools/ros2/send_cmd_vel.py --robot carter_1 --linear 0.2 --angular 0.0 --rate 10
```

On teardown the client attempts several zero-velocity publications and delivery waits. The simulator bridge has no stale-command watchdog, so process exit alone does not prove the robot stopped. Observe the robot and confirm zero velocity was delivered.

### Manipulator commands

`send_manipulator_command.py` sends native UR5 or Z1 commands. Exactly one target is required: six joint positions, a three-value Cartesian target, or the Z1 gripper value. `--wait` checks state feedback until the target reaches the configured tolerance or timeout. Cartesian wait mode requires the `world` frame because the feedback pose uses world coordinates.

```bash
/usr/bin/python3 tools/ros2/send_manipulator_command.py --help
/usr/bin/python3 tools/ros2/send_manipulator_command.py \
  --robot m20_1 --model ur5 \
  --joint 0.0 -1.2 1.5 -1.8 -1.57 0.0 \
  --wait
```

UR5 does not expose the Z1 gripper command. Validate robot/model pairing and physical clearance before publishing any command.

## Interface discovery

The tools can only use interfaces enabled by the selected scene. Use the repository interface catalog and runtime snapshot to inspect declarations and live status:

```bash
python simulator.py interfaces list --json
python simulator.py interfaces scene --env keyboard --json
python simulator.py interfaces status --probe
```

A declared interface is not proof that its runtime publisher or subscriber started. The runtime snapshot and ROS graph remain authoritative for the active session.

## Focused tests

The tracked tests are intentionally retained because they protect parser validation, topic construction, image/depth conversion, feedback matching, Ctrl+C handling, and node/publisher cleanup without requiring a live ROS graph:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest --rootdir="$PWD" -q -p no:cacheprovider \
  tools/ros2/tests/test_vis_sensors.py \
  tools/ros2/tests/test_send_cmd_vel.py \
  tools/ros2/tests/test_send_manipulator_command.py
```

These unit tests use mocks and do not validate DDS discovery, simulator bridge startup, real topic data, robot motion, or hardware safety. Run live checks separately when changing an integration contract.
