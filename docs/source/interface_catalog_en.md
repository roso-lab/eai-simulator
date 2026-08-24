# Interface Catalog

EAI Simulator provides a directory of pure command line interfaces for querying robot motion, sensor data, arm control on the host robot, and communication methods for future non-ROS devices. UR5 and Z1 belong to `Payloads / Manipulators` in Env DIY, not sensors; this page retains the existing ROS interface ID and runtime topic, and does not affect the old environment files.

## Common commands

List all interfaces:

```bash
python simulator.py interfaces list
```

Search by robot, sensor, protocol or data type:

```bash
python simulator.py interfaces search --robot scout
python simulator.py interfaces search --sensor orsus --data-type image
python simulator.py interfaces search --protocol ros2 --text "point cloud"
```

`--sensor` is only used for environment sensing devices such as Orsus and LiDAR. UR5/Z1 is queried by the robot interface ID (e.g. `ros.ur5.joint_command`).

View interface description and calling examples:

```bash
python simulator.py interfaces show ros.cmd_vel
python simulator.py interfaces show ros.aerial_camera_image
python simulator.py interfaces show ros.aerial_camera_info
python simulator.py interfaces show ros.aerial_cmd_vel
python simulator.py interfaces show ros.orsus.left_image
python simulator.py interfaces show ros.ur5.joint_command
python simulator.py interfaces show ros.ur5.ee_pose
python simulator.py interfaces show ros.z1.gripper_command
```

Querying the interface created by a saved scene:

```bash
python simulator.py interfaces scene --env pegasus_drones
```

All major query commands support `--json` for easy scripting.

## Camera interfaces

The built-in forward-facing monocular cameras on Iris, Pegasus, and CF2X use the same ROS2 interfaces:

| Interface ID | Endpoint Template | Message Type |
|---|---|---|
| `ros.aerial_camera_image` | `/{robot}/camera/image_raw` | `sensor_msgs/msg/Image` |
| `ros.aerial_camera_info` | `/{robot}/camera/camera_info` | `sensor_msgs/msg/CameraInfo` |

The sensors are installed on the aerial robots by default; image and calibration topics are published only when Camera is selected under Tools in Env DIY. The Camera Tool is independent of Navigation I/O, so Camera can be selected on its own. Navigation I/O uses the `navigation_io` key in environment JSON. `{robot}` is replaced by the scene instance name, such as `iris_1`, `pegasus_1`, or `cf2x_1`.

MuSHR Nano v2 has no supported built-in camera path. Selecting the Camera Tool by itself does not create a camera prim or declare/publish camera topics for MuSHR. To obtain MuSHR images, explicitly attach RealSense D455 and select Camera; the resulting RGB, depth, and camera-info topics use the RealSense interface declarations. Navigation I/O independently enables the D455 IMU.

Orsus exposes the stereo image interfaces `ros.orsus.left_image` (`/{robot}/Orsus_L_cam`) and `ros.orsus.right_image` (`/{robot}/Orsus_R_cam`). Both use `sensor_msgs/msg/Image` and are also controlled by the Camera Tool. Orsus point-cloud, odometry, and scan output remain controlled by Navigation I/O. A standalone LiDAR payload declares and publishes `/cloud` and `/odometry` only when Navigation I/O is also selected.

Use the unified viewer to display every camera topic that exists now or starts later:

```bash
source /opt/ros/humble/setup.bash
python3 tools/ros2/vis_sensors.py
```

To display only the camera below one aerial robot namespace:

```bash
python3 tools/ros2/vis_sensors.py --sensor camera --namespace /iris_1
```

## Keyboard motion interface

Either the Keyboard Tool or Navigation I/O enables the `/<robot>/cmd_vel` subscriber. Aerial robots use the `ros.aerial_cmd_vel` interface with `geometry_msgs/msg/Twist`; the keyboard publisher maps keys as follows:

| Keys | Twist Field | Motion |
|---|---|---|
| `W/S` | `linear.x` | Forward/backward |
| `A/D` | `linear.y` | Left/right lateral motion (on holonomic robots) |
| `R/F` | `linear.z` | Ascend/descend |
| `C/V` | `angular.z` | Yaw |

Use `K` or Space to stop, `Q` to switch between discovered robots, and `Esc` or `Ctrl-C` to exit. `--linear-speed`, `--vertical-speed`, and `--angular-speed` configure the planar, vertical, and yaw command magnitudes respectively. For example:

```bash
source /opt/ros/humble/setup.bash
python3 algorithm/keyboard/keyboard.py \
  --robot iris_1 \
  --vertical-speed 0.5
```

## Robotic arm interface

UR5 and Z1 share the same formal ROS2 namespace convention. The main launcher calls the shared manager's `setup_robot(...)` for UR5 and Z1 attachments that are actually selected; however, a static interface declaration still does not prove that graph setup succeeded. Complete startup, control, and troubleshooting guidance is in [Manipulator Control](ur5_control_en.md); this page only retains the interface catalog and query commands.

### UR5 Robotic Arm Interface

The UR5 interface manifest contains two input topics and two output topics:

| Interface ID | Endpoint Template | Direction |
|---|---|---|
| `ros.ur5.target_pose` | `/{robot}/ur5/target_pose` | Input |
| `ros.ur5.joint_command` | `/{robot}/ur5/joint_command` | Input |
| `ros.ur5.joint_states` | `/{robot}/ur5/joint_states` | Output |
| `ros.ur5.ee_pose` | `/{robot}/ur5/ee_pose` | Output |

These interfaces are available for Go2, B2, M20, Scout and Lite3. `{robot}` in the endpoint is always replaced with the actual instance name generated by Env Builder, such as `go2_1` or `m20_2`. The interface directory and runtime registration are expanded according to the instance where `ur5` is actually mounted in the scene. UR5 topics will not be created for robots that do not have a robotic arm mounted.

The UR5 topic is created directly by the native OmniGraph node of Isaac Sim ROS2 Bridge, and the naming hierarchy is consistent with Orsus: the robot instance name is in the first-level namespace, and the device name `ur5` is in the second-level namespace. The robot interface does not use `tmp/` files and does not require a separate Python bridge.

Query the actual robot endpoints in a saved DIY environment:

```bash
python simulator.py interfaces scene --env <env_name> | grep '/ur5/'
```

Check the current robot arm endpoint while the simulation is running:

```bash
python simulator.py interfaces status | grep '/ur5/'
ros2 topic list | grep '/ur5/'
```

`interfaces test` can check read-only output interfaces such as `joint_states` and `ee_pose`, but will not actively send `target_pose` or `joint_command` to avoid test commands from accidentally moving the robot arm. For complete control methods, see [Robotic Arm](ur5_control_en.md).

### Z1 Robotic Arm Interface

Z1 uses the same native OmniGraph naming hierarchy as UR5, and adds independent gripper input and output:

| Interface ID | Endpoint Template | Direction |
|---|---|---|
| `ros.z1.target_pose` | `/{robot}/z1/target_pose` | Input |
| `ros.z1.joint_command` | `/{robot}/z1/joint_command` | Input |
| `ros.z1.joint_states` | `/{robot}/z1/joint_states` | Output |
| `ros.z1.ee_pose` | `/{robot}/z1/ee_pose` | Output |
| `ros.z1.gripper_command` | `/{robot}/z1/gripper_command` | Input |
| `ros.z1.gripper_state` | `/{robot}/z1/gripper_state` | Output |

Z1 supports Carter, Go2, B2, M20, Scout and Lite3. The interface is only expanded according to the instance that actually mounts Z1 in the scene. The same robot cannot mount UR5 and Z1 at the same time, but different robots can select different robotic arms.

```bash
python simulator.py interfaces scene --env <env_name> | grep '/z1/'
python simulator.py interfaces status | grep '/z1/'
ros2 topic list | grep '/z1/'
```

For control and status check commands, see [Robotic Arm](ur5_control_en.md).

## Runtime status

After the simulation starts, it will be written atomically:

```text
tmp/runtime_interfaces.json
```

Can be viewed in another terminal:

```bash
python simulator.py interfaces status
python simulator.py interfaces status --probe
```

`status` displays the current scene, process PID, snapshot age, bot instance, and the actual endpoint after resolution. `--probe` additionally checks whether the read-only ROS Topic exists; write interfaces such as motion commands and robot arm control will not be executed.

If you want the interaction menu to be opened at the same time after the scene is started:

```bash
python simulator.py --env EAI-Factory-v0 --interfaces-menu
```

You can also enter the menu separately:

```bash
python simulator.py interfaces menu
```

## Read only test

By default, it only checks whether the interface exists:

```bash
python simulator.py interfaces test ros.orsus.left_image --endpoint /carter_1/Orsus_L_cam
```

Read a message summary without outputting the full image or point cloud payload:

```bash
python simulator.py interfaces test ros.orsus.left_image \
  --endpoint /carter_1/Orsus_L_cam --mode sample
```

Count Topic frequency within a limited time:

```bash
python simulator.py interfaces test ros.orsus.point_cloud \
  --endpoint /carter_1/cloud --mode hz
```

If `--endpoint` is omitted, the command will first look for matching interfaces from the runtime snapshot. Missing ROS 2 CLI, non-existent interface, or probe timeout will return clear status and will not affect the simulation process.

Motion Topic, Service, Action and other input interfaces only show calling examples. The current version refuses to execute them via `interfaces test`.

## Add new device

The static manifest is located at:

```text
source/EAI/EAI/interface_catalog/interfaces/robots/
source/EAI/EAI/interface_catalog/interfaces/sensors/
```

Example:

```yaml
id: sensor.example_camera
name: Example Camera
category: sensor
models: [example_camera]
description: Example non-ROS camera.
interfaces:
  - id: http.example_camera.health
    name: Camera health
    protocol: http
    direction: output
    kind: endpoint
    endpoint: http://127.0.0.1:8080/health
    data_type: application/json
    description: Read-only camera health endpoint.
    example: curl http://127.0.0.1:8080/health
    read_only_test: {type: http}
```

The required device fields are `id`, `name`, `category`, `models` and `interfaces`. The required interface fields are `id`, `name`, `protocol`, `direction`, `kind`, `endpoint` and `data_type`.

Endpoints and examples support the following placeholders:

- `{robot}`: the actual robot instance name, such as `carter_1`;
- `{robot_type}`: robot model;
- `{sensor}`: attachment type;
- `{env}`: scene name;
- `{index}`: The serial number of the robot in the scene.

YAML describes "what should be provided", the runtime snapshot describes "what is currently created", and the detection results describe "whether it is available at this moment". The three types of information should be kept separate.
