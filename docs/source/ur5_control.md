# 机械臂

UR5 和 Z1 共享正式的 **ROS2 Bridge + OmniGraph** topic 规范。当前 `simulator.py` 会为 selection 中实际挂载的 UR5 和 Z1 使用对应 model spec 调用 `setup_robot(...)`。静态接口声明不代表 setup 成功；对于已经激活的 graph，ROS2 消息直接进入仿真器，不经过 `tmp/` 文件，也不需要额外启动机械臂 bridge 进程。

机械臂控制器属于可替换的外部算法；仿真器负责准确接收 ROS2 命令、把目标写入对应 articulation，并发布实际关节与末端状态。

## 从 Env DIY 选择到控制器

UR5/Z1 不是独立机器人，而是 `Payloads → Manipulators` 下的宿主附件。Env DIY 会根据宿主自动写入默认 controller cfg：

| 附件 | 默认 cfg | 控制器实现 | 额外接口 |
|---|---|---|---|
| UR5 | `UR5_IK_CFG` | `ManipulatorIkControllerCfg` + DLS Differential IK | 无 |
| Z1 | `Z1_IK_CFG` | `ManipulatorIkControllerCfg` + DLS Differential IK | `gripper_command` / `gripper_state` |

两者都创建独立的 `<robot>_arm` articulation，并通过 FixedJoint 连接宿主。宿主的底盘/腿部 controller 与机械臂 controller 分开运行；同一个宿主不能同时挂载 UR5 和 Z1。主启动器会为 UR5 和 Z1 附件建立 ROS2 OmniGraph；Navigation I/O（内部键 `navigation_io`）或 Keyboard（内部键 `keyboard`）只影响宿主 `cmd_vel`，不参与机械臂 graph 注册。

### 从 Env DIY 到 ROS2 控制

运行前编辑时，在三维插件中选择 `Plane`，添加 `Go2`，再从 `Payloads → Manipulators` 将 `Z1` 挂载到 Go2。确认宿主使用 `GO2_VELOCITY_RSL_CFG`、附件使用 `Z1_IK_CFG`。下面展示正式 Z1 topic 的客户端语法。主启动器会尝试注册选中的 Z1；发送前仍应通过 `ros2 topic list` 确认本次 `setup_robot(...)` 已成功：

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

点击 Run 后不会重新启动 Isaac Sim；预览资源会在同一个 Kit 进程内释放并创建正式环境。

`tools/ros2/send_manipulator_command.py` 是在系统 ROS Python 中运行的外部 `rclpy` 发布/等待客户端，覆盖 UR5/Z1 的关节、位姿和夹爪正式 topic。发布前最多允许 3 秒发现订阅者；`--timeout` 从该阶段之后开始，只在使用 `--wait` 时限制状态回读等待。它不执行 IK，不创建 OmniGraph，不注册机械臂，不启动 Isaac Sim，也不能证明 graph 已激活。不要把脚本位于 `tools/ros2/` 误解为 `env_isaaclab` 已提供 `rclpy`；应在另一个已 source 所选 ROS 发行版的系统 Python 终端运行。

## 快速开始

下面以 Go2+Z1 为例启动一个环境：

以下命令均从仓库根目录执行：

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

正式控制链路只需要主 `simulator.py` 进程，不要同时启动额外的 Isaac Sim 或独立机械臂 bridge。

## 支持范围

| 机械臂 | 支持的宿主机器人 | 额外接口 |
| --- | --- | --- |
| UR5 | Go2、B2、M20、Scout、Lite3 | 无 |
| Z1 | Carter、Go2、B2、M20、Scout、Lite3 | 独立夹爪 topic |

同一个机器人不能同时挂载 UR5 和 Z1；不同机器人可以分别选择不同机械臂：

```text
/m20_1/ur5/*
/go2_1/z1/*
/b2_1/z1/*
```

Env DIY 配置示例：

```json
{
  "type": "go2",
  "controller": {"mode": "default", "cfg": "GO2_VELOCITY_RSL_CFG"},
  "attachments": [
    {"type": "z1", "controller": {"mode": "default", "cfg": "Z1_IK_CFG"}}
  ]
}
```

挂载后机械臂是独立的 `<robot>_arm` articulation。宿主控制器只控制底盘或腿部，机械臂控制器只控制机械臂关节。

## Topic 规范

机器人实例名是一级 namespace，机械臂型号是二级 namespace：

| 方向 | UR5 | Z1 | 类型 |
| --- | --- | --- | --- |
| 输入 | `/<robot>/ur5/joint_command` | `/<robot>/z1/joint_command` | `sensor_msgs/msg/JointState` |
| 输入 | `/<robot>/ur5/target_pose` | `/<robot>/z1/target_pose` | `geometry_msgs/msg/PoseStamped` |
| 输出 | `/<robot>/ur5/joint_states` | `/<robot>/z1/joint_states` | `sensor_msgs/msg/JointState` |
| 输出 | `/<robot>/ur5/ee_pose` | `/<robot>/z1/ee_pose` | `geometry_msgs/msg/PoseStamped` |
| 输入 | — | `/<robot>/z1/gripper_command` | `sensor_msgs/msg/JointState` |
| 输出 | — | `/<robot>/z1/gripper_state` | `sensor_msgs/msg/JointState` |

以 `m20_1` 的 UR5 为例，实际 topic 为：

```text
/m20_1/ur5/target_pose
/m20_1/ur5/joint_command
/m20_1/ur5/joint_states
/m20_1/ur5/ee_pose
```

查询环境声明的接口，不启动 Isaac Sim。静态结果不证明对应 graph 会在运行时创建：

```bash
python simulator.py interfaces scene --env <env_name>
```

仿真运行后查询 ROS2 topic：

```bash
ros2 topic list | grep -E '/(ur5|z1)/'
```

## 关节控制

统一外部客户端可向 UR5 和 Z1 的正式 topic 发布命令；示例仍要求运行时已经激活对应 graph：

```bash
python3 tools/ros2/send_manipulator_command.py \
  --robot go2_1 \
  --model z1 \
  --joint 0.0 0.8 -1.2 0.0 0.0 0.0 \
  --wait \
  --timeout 30
```

Z1 标准顺序为 `joint1 joint2 joint3 joint4 joint5 joint6`；UR5 标准顺序为：

```text
shoulder_pan_joint shoulder_lift_joint elbow_joint
wrist_1_joint wrist_2_joint wrist_3_joint
```

提供 `name` 时，仿真器会按名称重排；缺失、重复、数量不符或包含 `NaN/Inf` 的命令会被丢弃：

```bash
ros2 topic pub --once \
  /go2_1/z1/joint_command \
  sensor_msgs/msg/JointState \
  "{name: [joint6, joint1, joint4, joint2, joint5, joint3], position: [0.0, 0.2, -0.1, 0.6, 0.0, -1.0]}"
```

读取实际状态：

```bash
ros2 topic echo --once /go2_1/z1/joint_states
```

`joint_states.position` 和 `joint_states.velocity` 来自仿真器当前 articulation 状态，不是目标值回显。

## 位姿控制

`target_pose` 是完整 6D 末端位姿输入，`header.frame_id` 支持 `world` 和 `base_link`：

- `base_link`：推荐用于挂载机械臂，目标相对于宿主机器人；
- `world`：场景绝对坐标，必须先读取当前 `ee_pose` 再选择附近目标。

四元数使用 ROS 标准 `x y z w` 顺序；单位姿态为 `0 0 0 1`。全零四元数不会被拒绝，而是表示只更新目标位置并保持当前末端姿态；非零四元数会先归一化。

挂载机械臂时推荐：

```bash
python3 tools/ros2/send_manipulator_command.py \
  --robot go2_1 \
  --model z1 \
  --xyz 0.45 0.00 0.65 \
  --quat 0 0 0 1 \
  --frame-id base_link
```

显式姿态使用 ROS 标准 `x y z w` 顺序：

```bash
python3 tools/ros2/send_manipulator_command.py \
  --robot go2_1 \
  --model z1 \
  --xyz 0.45 0.00 0.65 \
  --quat 0.0 0.7071 0.0 0.7071 \
  --frame-id base_link
```

`--wait` 的位姿回读按 `ee_pose` 的 world 坐标比较，因此客户端会拒绝 `--frame-id base_link` 与 `--wait` 的组合；可改用 `ros2 topic echo --once /<robot>/z1/ee_pose` 观察结果。IK 由仿真器内的可替换控制器执行，不由 `send_manipulator_command.py` 执行。当前公共控制器使用 Isaac Lab Differential IK 的 DLS（阻尼最小二乘）方法，并把单次关节变化限制在 `0.10 rad` 内；如果目标超出机械臂工作空间，外部算法应重新选择目标，而不是持续把关节推向软限位。

## Z1 夹爪

夹爪使用独立 topic，不会覆盖六轴机械臂命令：

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

## 底盘与机械臂联合控制

底盘仍使用 EAI 原生 `cmd_vel` topic，和机械臂接口相互独立：

```bash
python3 algorithm/keyboard/keyboard.py --robot go2_1
```

键盘运行期间可以同时发送机械臂关节或夹爪命令。机械臂 topic 不会修改底盘控制器状态。

## 重置与故障排查

环境 reset 会清理机械臂命令状态，避免旧目标在 reset 后继续执行。若 topic 列表为空，先确认 selection 确实挂载了 UR5/Z1，并检查本次 `setup_robot(...)` 是否成功。再检查：

```bash
python simulator.py interfaces scene --env <env_name> | grep -E '/(ur5|z1)/'
ros2 topic list | grep -E '/(ur5|z1)/'
```

若使用 `world` 位姿后机械臂抖动，优先检查目标是否在当前末端附近；挂载机械臂通常应改用 `base_link`。不要同时启动两个 Isaac Sim。

完整接口 ID：

```bash
python simulator.py interfaces show ros.ur5.joint_command
python simulator.py interfaces show ros.z1.joint_command
python simulator.py interfaces show ros.z1.gripper_command
```
