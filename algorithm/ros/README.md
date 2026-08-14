# EAI ROS 2 Interfaces

`algorithm/ros/` contains optional external ROS 2 algorithms and diagnostic tools. The reusable Isaac-side interface lives in `EAI.hmrs_ros` and is not tied to Nav2.

## Command Input

EAI uses a direct ROS 2 `geometry_msgs/msg/Twist` control path:

```text
Keyboard / Nav2 / another ROS 2 algorithm
  -> /<robot_name>/cmd_vel
  -> EAI.hmrs_ros.ROS2CmdVelBridge
  -> EAI.hmrs_ros.ROS2TwistSubscriber
  -> Isaac Sim robot controller
```

Enable the command subscriber for robots selected with the JSON `keyboard` or `ros` tool. To force it for every robot, launch:

```bash
python simulator.py \
  --env=EAI-Factory-v0 \
  --device=cuda:0 \
  --enable-cmd-vel-bridge
```

The previous `--enable-nav2-bridge` spelling remains a deprecated CLI alias. Infrastructure code should use cmd_vel terminology because Keyboard, Nav2, and other algorithms share the same interface.

## Keyboard

The keyboard process publishes Twist messages directly and does not use file IPC:

```bash
source /opt/ros/humble/setup.bash
python3 algorithm/keyboard/keyboard.py \
  --robot iris_1 \
  --vertical-speed 0.5
```

`W/S` publishes forward/backward `linear.x`, `A/D` publishes lateral
`linear.y`, `R/F` publishes ascend/descend `linear.z`, and `C/V` publishes yaw
`angular.z`. Use `K` or Space to stop, `Q` to switch between discovered robots,
and `Esc` or `Ctrl-C` to exit. `--linear-speed`, `--vertical-speed`, and
`--angular-speed` configure the corresponding command magnitudes.

## Nav2

Nav2-specific launch files and configuration remain under `algorithm/ros/nav2/`. Nav2 publishes the same `/<robot_name>/cmd_vel` interface consumed by `ROS2CmdVelBridge`.

See `algorithm/ros/nav2/README.md` for setup and launch instructions.

## Sensor Output

Orsus and standalone ROS LiDAR output do not pass through `EAI.hmrs_ros`:

- Orsus camera images are published by OmniGraph nodes embedded in the Orsus USD.
- Orsus point cloud and odometry are published by the Orsus LiDAR/Odometry OmniGraph.
- Standalone ROS LiDAR point cloud and odometry are published by its own USD OmniGraph.
- The JSON `camera` tool controls the Orsus left/right image graphs.
- The JSON `ros` tool controls the Orsus point-cloud and odometry graph.
- Iris, Pegasus, and CF2X always carry their camera, `Example_Rotary` LiDAR,
  IMU, GPS, magnetometer, and barometer resources. Their JSON `camera` and
  `ros` tools gate ROS topic publishers, not sensor existence.

Use the same viewer for every published camera. With no arguments it discovers
all current and later `sensor_msgs/msg/Image` topics, including aerial monocular
cameras and Orsus stereo cameras:

```bash
source /opt/ros/humble/setup.bash
python3 algorithm/ros/tools/vis_sensors.py
```

Filter to one robot with `--sensor camera --namespace /iris_1`. Existing
Orsus camera-plus-cloud and cloud-only modes remain available through
`--sensor orsus --namespace /carter_1` and `--sensor lidar --namespace /carter_1`.

The removed JSON file bridge was only an old workaround that copied Twist messages through `/tmp/*.json`. Keyboard, Nav2, Orsus, LiDAR, and the current Simulator do not require it.

## Manipulator Control

UR5 and Z1 use native ROS2 Bridge/OmniGraph interfaces inside the main Simulator process:

```text
/<robot>/ur5/{target_pose,joint_command,joint_states,ee_pose}
/<robot>/z1/{target_pose,joint_command,joint_states,ee_pose,gripper_command,gripper_state}
```

Use the shared command tool for either model:

```bash
python3 algorithm/ros/tools/manipulator_command.py \
  --robot go2_1 --model z1 \
  --joint 0.0 0.8 -1.2 0.0 0.0 0.0 --wait
```

## Structure

```text
algorithm/ros/
├── bridges/
│   └── ros2_odometry_bridge.py
├── nav2/
│   ├── nav2_setup.py
│   ├── run_nav2.sh
│   ├── send_goal.py
│   └── tf_bridge.py
├── tools/
    ├── ros2_nav2_test.py
    ├── ros2_send_cmd_vel.py
    ├── manipulator_command.py
    └── vis_sensors.py

source/EAI/EAI/hmrs_ros/
├── __init__.py
├── cmd_vel_bridge.py
├── manipulator_omnigraph.py
├── ur5_omnigraph.py
├── z1_omnigraph.py
└── twist_subscriber.py
```
