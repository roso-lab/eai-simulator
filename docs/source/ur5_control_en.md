# Manipulator Control

UR5 and Z1 share a formal **ROS2 Bridge + OmniGraph** topic convention. The current `simulator.py` explicitly registers graphs for selected UR5 attachments only. The Z1 model spec, interface declarations, and controller remain supported, but selecting Z1 does not call the equivalent `setup_robot(...)`. For a graph that is active, ROS2 messages enter the simulator directly without files under `tmp/` or a separate manipulator bridge process.

The manipulator controller is a replaceable external algorithm; the simulator is responsible for accurately receiving ROS2 commands, writing the target into the corresponding articulation, and publishing the actual joint and end states.

## From Env DIY selection to controller

UR5/Z1 is not a standalone robot, but a host attachment under `Payloads → Manipulators`. Env DIY will automatically write the default controller cfg according to the host:

| Accessories | Default cfg | Controller implementation | Additional interfaces |
|---|---|---|---|
| UR5 | `UR5_IK_CFG` | `ManipulatorIkControllerCfg` + DLS Differential IK | None |
| Z1 | `Z1_IK_CFG` | `ManipulatorIkControllerCfg` + DLS Differential IK | `gripper_command` / `gripper_state` |

Both create an independent `<robot>_arm` articulation connected to the host through a FixedJoint. The host chassis/leg controller runs separately from the manipulator controller, and one host cannot mount UR5 and Z1 simultaneously. The main launcher currently creates a ROS2 OmniGraph for UR5 attachments only; Navigation I/O (internal key `navigation_io`) and Keyboard (internal key `keyboard`) affect host `cmd_vel` and do not supply Z1 graph registration.

### From Env DIY to ROS2 control

When editing before running, select `Plane` in the 3D plug-in, add `Go2`, and mount `Z1` from `Payloads → Manipulators`. Confirm that the host uses `GO2_VELOCITY_RSL_CFG` and the attachment uses `Z1_IK_CFG`. The commands below demonstrate the formal Z1 topic syntax. Because the main launcher does not activate that graph, first use `ros2 topic list` to confirm that an integration entry point has registered Z1:

```bash
python simulator.py --diy-3d --device=cuda:0
# Plane → Go2 → Payloads → Manipulators → Z1 → Run

source /opt/ros/humble/setup.bash
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
python3 tools/ros2/send_manipulator_command.py \
  --robot go2_1 --model z1 \
  --joint 0.0 0.8 -1.2 0.0 0.0 0.0 --wait
python3 tools/ros2/send_manipulator_command.py \
  --robot go2_1 --model z1 --gripper -0.20 --wait
```

Isaac Sim will not be restarted after clicking Run; the preview resources will be released and the production environment will be created in the same Kit process.

`tools/ros2/send_manipulator_command.py` is an external system-ROS `rclpy` publisher/waiter for the formal UR5/Z1 joint, pose, and gripper topics. It allows up to three seconds for subscriber discovery before publishing; `--timeout` starts after that phase and bounds feedback waiting only with `--wait`. It does not perform IK, create OmniGraph, register a manipulator, start Isaac Sim, or prove graph activation. Its `tools/ros2/` location does not provide `rclpy` inside `env_isaaclab`; run it in a separate system Python shell after sourcing the selected ROS distribution.

## Quick Start

Let's take Go2+Z1 as an example to start an environment:

Run the following commands from the repository root:

```bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate env_isaaclab
source /opt/ros/humble/setup.bash
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp

python simulator.py \
  --env=<env_name> \
  --num_envs=1 \
  --device=cuda:0 \
  --enable-cmd-vel-bridge
```

The control path only requires the main `simulator.py` process. Do not start another Isaac Sim instance or a separate manipulator bridge at the same time.

## Support range

| Robot Arm | Supported Host Robots | Additional Interfaces |
| --- | --- | --- |
| UR5 | Go2, B2, M20, Scout, Lite3 | None |
| Z1 | Carter, Go2, B2, M20, Scout, Lite3 | Independent gripper topic |

The same robot cannot mount UR5 and Z1 at the same time; different robots can select different robotic arms:

```text
/m20_1/ur5/*
/go2_1/z1/*
/b2_1/z1/*
```

Env DIY configuration example:

```json
{
  "type": "go2",
  "controller": {"mode": "default", "cfg": "GO2_VELOCITY_RSL_CFG"},
  "attachments": [
    {"type": "z1", "controller": {"mode": "default", "cfg": "Z1_IK_CFG"}}
  ]
}
```

After mounting, the robot arm is an independent `<robot>_arm` articulation. The host controller only controls the chassis or legs, and the robot arm controller only controls the robot arm joints.

## Topic specification

The robot instance name is the first-level namespace, and the robot arm model is the second-level namespace:

| Direction | UR5 | Z1 | Type |
| --- | --- | --- | --- |
| Input | `/<robot>/ur5/joint_command` | `/<robot>/z1/joint_command` | `sensor_msgs/msg/JointState` |
| Input | `/<robot>/ur5/target_pose` | `/<robot>/z1/target_pose` | `geometry_msgs/msg/PoseStamped` |
| Output | `/<robot>/ur5/joint_states` | `/<robot>/z1/joint_states` | `sensor_msgs/msg/JointState` |
| Output | `/<robot>/ur5/ee_pose` | `/<robot>/z1/ee_pose` | `geometry_msgs/msg/PoseStamped` |
| Input | — | `/<robot>/z1/gripper_command` | `sensor_msgs/msg/JointState` |
| Output | — | `/<robot>/z1/gripper_state` | `sensor_msgs/msg/JointState` |

Taking UR5 of `m20_1` as an example, the actual topic is:

```text
/m20_1/ur5/target_pose
/m20_1/ur5/joint_command
/m20_1/ur5/joint_states
/m20_1/ur5/ee_pose
```

Query the interfaces declared for an environment without starting Isaac Sim. Static output does not prove that a corresponding runtime graph will be created:

```bash
python simulator.py interfaces scene --env <env_name>
```

Query the ROS2 topic after the simulation is running:

```bash
ros2 topic list | grep -E '/(ur5|z1)/'
```

## Joint Control

The unified external client can publish to the formal UR5 and Z1 topics; the corresponding runtime graph must already be active:

```bash
python3 tools/ros2/send_manipulator_command.py \
  --robot go2_1 \
  --model z1 \
  --joint 0.0 0.8 -1.2 0.0 0.0 0.0 \
  --wait \
  --timeout 30
```

The standard sequence of Z1 is `joint1 joint2 joint3 joint4 joint5 joint6`; the standard sequence of UR5 is:

```text
shoulder_pan_joint shoulder_lift_joint elbow_joint
wrist_1_joint wrist_2_joint wrist_3_joint
```

When `name` is provided, the simulator reorders joints by name. Commands with missing or duplicate names, inconsistent lengths, or `NaN/Inf` values are rejected:

```bash
ros2 topic pub --once \
  /go2_1/z1/joint_command \
  sensor_msgs/msg/JointState \
  "{name: [joint6, joint1, joint4, joint2, joint5, joint3], position: [0.0, 0.2, -0.1, 0.6, 0.0, -1.0]}"
```

Read the actual status:

```bash
ros2 topic echo --once /go2_1/z1/joint_states
```

`joint_states.position` and `joint_states.velocity` come from the current articulation state of the simulator, not the target value echo.

## Posture control

`target_pose` is the complete 6D end pose input, `header.frame_id` supports `world` and `base_link`:

- `base_link`: recommended for mounting robotic arms, the target is relative to the host robot;
- `world`: absolute coordinates of the scene, you must first read the current `ee_pose` and then select nearby targets.

Quaternions use ROS `x y z w` order; the unit orientation is `0 0 0 1`. An all-zero quaternion is not rejected: it updates the target position while retaining the current end-effector orientation. Non-zero quaternions are normalized.

Recommended when mounting the robotic arm:

```bash
python3 tools/ros2/send_manipulator_command.py \
  --robot go2_1 \
  --model z1 \
  --xyz 0.45 0.00 0.65 \
  --quat 0 0 0 1 \
  --frame-id base_link
```

Explicit poses use the ROS standard `x y z w` order:

```bash
python3 tools/ros2/send_manipulator_command.py \
  --robot go2_1 \
  --model z1 \
  --xyz 0.45 0.00 0.65 \
  --quat 0.0 0.7071 0.0 0.7071 \
  --frame-id base_link
```

For poses, `--wait` compares the target with `ee_pose` in world coordinates, so the client rejects the combination of `--frame-id base_link` and `--wait`; use `ros2 topic echo --once /<robot>/z1/ee_pose` to observe the result instead. IK runs in the simulator's replaceable controller, not in `send_manipulator_command.py`. The current public controller uses Isaac Lab Differential IK with DLS (damped least squares) and limits each joint change to `0.10 rad`. If a target is outside the workspace, the external algorithm should choose another target instead of continually pushing joints toward their soft limits.

## Z1 Gripper

The gripper uses an independent topic and does not cover the six-axis robot arm commands:

```bash
python3 tools/ros2/send_manipulator_command.py \
  --robot go2_1 \
  --model z1 \
  --gripper -0.20 \
  --wait \
  --timeout 20
```

```bash
ros2 topic echo --once /go2_1/z1/gripper_state
```

## Joint control of chassis and robotic arm

The chassis still uses EAI's native `cmd_vel` topic, which is independent of the robot arm interface:

```bash
python3 algorithm/keyboard/keyboard.py --robot go2_1
```

Robotic arm joint or gripper commands can be sent simultaneously while the keyboard is running. The robotic arm topic will not modify the chassis controller state.

## Reset and Troubleshooting

An environment reset clears manipulator command state so old targets do not continue after reset. If the topic list is empty, first confirm that a runtime graph was actually registered; the current main launcher omits that step for Z1. Then check:

```bash
python simulator.py interfaces scene --env <env_name> | grep -E '/(ur5|z1)/'
ros2 topic list | grep -E '/(ur5|z1)/'
```

If the robot arm shakes after using the `world` pose, first check whether the target is near the current end; usually you should use `base_link` to mount the robot arm instead. Do not start two Isaac Sims at the same time.

Full interface ID:

```bash
python simulator.py interfaces show ros.ur5.joint_command
python simulator.py interfaces show ros.z1.joint_command
python simulator.py interfaces show ros.z1.gripper_command
```
