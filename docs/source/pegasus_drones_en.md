# Pegasus Drones

EAI ships three drone platforms — the 3DR Iris, the Pegasus research quadrotor, and CF2X — with keyboard/ROS2 goal control and a default sensor suite of a forward monocular camera, an `Example_Rotary` 128-line LiDAR, and base sensors such as IMU/GPS. Airframe USD assets and controllers are downloaded on demand from the Hugging Face dataset by the EAI asset resolver; no extra extension is required.

## Quick start

```bash
conda activate env_isaaclab
python simulator.py --env=pegasus_drones --device=cuda:0
```

The example spawns one `iris_1` at 1 m and enables keyboard goal control and
ROS. Keyboard or ROS `Twist` `linear.x/y/z` updates the 3D position goal;
`angular.z` updates yaw. The control topic is `/iris_1/cmd_vel`.

`iris`, `pegasus`, and `cf2x` include a forward-facing monocular camera and an
aerial LiDAR by default. The sensor resources remain in the scene even when no
tool is selected. Their ROS 2 publishers are controlled by two independent Env DIY
tools. Adding Camera to an aerial robot branch publishes
`/<robot>/camera/image_raw` (`sensor_msgs/msg/Image`) and
`/<robot>/camera/camera_info` (`sensor_msgs/msg/CameraInfo`). Adding Navigation
I/O publishes `/<robot>/lidar/pointcloud` (`sensor_msgs/msg/PointCloud2`). A
Camera-only branch does not publish the LiDAR topic, a Navigation-I/O-only branch
does not publish camera topics, and selecting both tools publishes both streams.
Navigation I/O retains the internal `ros` key in environment JSON.

After starting the simulator, run the unified sensor visualizer from a ROS 2
Humble terminal. The visualizer requires system ROS Python with `rclpy`,
`sensor_msgs`, `cv_bridge`, OpenCV, and NumPy, plus a working graphical display;
placing it under the root `tools/` directory does not provide those dependencies
inside `env_isaaclab`. With no arguments it dynamically discovers every
`sensor_msgs/msg/Image` topic on the current ROS graph, covering the Iris,
Pegasus, and CF2X monocular cameras as well as both Orsus cameras. Cameras that
appear after the visualizer starts are subscribed automatically:

```bash
source /opt/ros/humble/setup.bash
python3 tools/vis_sensors.py
```

Use a namespace filter to show only one aerial robot. The built-in example uses
the `iris_1` instance:

```bash
python3 tools/vis_sensors.py --sensor camera --namespace /iris_1
```

`iris`, `pegasus`, and `cf2x` also include an accelerometer and gyroscope with
white noise, random walk, turn-on bias, and first-order time-varying bias, plus
GPS, magnetometer, and barometer. These models exist by default; Navigation I/O
on the same aerial robot branch only controls their ROS topic publication.

The LiDAR on all three aerial robots uses Pegasus Simulator's original
`IsaacSensorCreateRtxLidar` path with the `Example_Rotary` configuration and
does not reuse the ground-robot HESAI/Pandar sensor. `Example_Rotary` is a
128-channel 3D LiDAR, so it does not publish the 2D-only `LaserScan`.

## Assets and configurations

| Env DIY type | USD | Default controller cfg |
|---|---|---|
| `cf2x` | `usd/robot/cf2x/cf2x.usd` | `QUADCOPTER_GOAL_SKRL_CFG` |
| `iris` | `usd/robot/pegasus/iris/iris.usd` | `PEGASUS_IRIS_POSITION_CFG` |
| `pegasus` | `usd/robot/pegasus/pegasus/pegasus_optimized.usdc` | `PEGASUS_X4_POSITION_CFG` |

The default configurations use geometric position and yaw control. The
controller converts the goal into collective thrust and body torque, then
allocates these values to four rotor speeds using the real rotor locations for
each asset. The actuator layer retains Pegasus's `T = k * omega^2` thrust
curve, rotor reaction moment, and body-frame linear drag.

For an external algorithm that directly outputs motor speed in rad/s, select
`PEGASUS_IRIS_ROTOR_CFG` or `PEGASUS_X4_ROTOR_CFG` manually in JSON and call:

```python
rotor_speed = torch.tensor([[650.0, 650.0, 650.0, 650.0]], device=env.device)
env.step({"iris_1": rotor_speed})
```

The direct input order is `[rotor0, rotor1, rotor2, rotor3]` in rad/s and each
value is limited to `[0, 1100]`. This interface can be connected to PX4,
ArduPilot, or custom flight software. The Pegasus MAVLink backends themselves
are not embedded in EAI, so an external backend must translate its outputs to
this tensor interface.

## Sources and Licenses

Dynamics and airframe assets are derived from
[Pegasus Simulator](https://github.com/PegasusSimulator/PegasusSimulator)
(BSD-3-Clause); the 3DR Iris model comes from
[PX4](https://github.com/PX4/PX4-SITL_gazebo-classic/) (BSD-3-Clause).
Full attribution and license texts are available here:

- {download}`Pegasus Simulator attribution <_static/licenses/pegasus_simulator/README.md>`
- {download}`Pegasus Simulator BSD 3-Clause license <_static/licenses/pegasus_simulator/LICENSE>`
- {download}`3DR Iris / PX4 BSD 3-Clause license <_static/licenses/pegasus_simulator/IRIS_LICENSE.rst>`
