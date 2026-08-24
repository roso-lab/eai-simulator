# 接口目录

EAI Simulator 提供纯命令行接口目录，用来查询机器人运动、传感器数据、宿主机器人上的机械臂控制以及未来非 ROS 设备的通信方式。UR5 和 Z1 在 Env DIY 中属于 `Payloads / Manipulators`，不是传感器；本页保留已有 ROS 接口 ID 和运行时 topic，不影响旧环境文件。

## 常用命令

列出全部接口：

```bash
python simulator.py interfaces list
```

按机器人、传感器、协议或数据类型搜索：

```bash
python simulator.py interfaces search --robot scout
python simulator.py interfaces search --sensor orsus --data-type image
python simulator.py interfaces search --protocol ros2 --text "point cloud"
```

`--sensor` 只用于 Orsus、LiDAR 等环境感知设备。UR5/Z1 通过机械臂接口 ID（例如 `ros.ur5.joint_command`）查询。

查看接口说明与调用示例：

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

查询某个已保存场景会创建的接口：

```bash
python simulator.py interfaces scene --env pegasus_drones
```

所有主要查询命令均支持 `--json`，便于脚本处理。

## 相机接口

Iris、Pegasus 和 CF2X 的内置前视单目相机使用相同的 ROS2 接口：

| 接口 ID | 端点模板 | 消息类型 |
|---|---|---|
| `ros.aerial_camera_image` | `/{robot}/camera/image_raw` | `sensor_msgs/msg/Image` |
| `ros.aerial_camera_info` | `/{robot}/camera/camera_info` | `sensor_msgs/msg/CameraInfo` |

传感器默认安装在无人机上；只有在 Env DIY 的 Tools 中选择 Camera 后，才会发布上述图像和标定 topic。Camera Tool 独立于导航接口（Navigation I/O），可以只选择 Camera。导航接口在环境 JSON 中使用 `navigation_io` 键。`{robot}` 替换为场景中的实例名，例如 `iris_1`、`pegasus_1` 或 `cf2x_1`。

MuSHR Nano v2 没有受支持的内置相机路径。只选择 Camera Tool 不会为 MuSHR 创建相机 prim，也不会声明或发布相机 topic。若需要 MuSHR 图像，必须显式挂载 RealSense D455 并选择 Camera，此时 RGB、深度和 camera-info topic 使用 RealSense 接口声明；Navigation I/O 独立控制 D455 IMU。

Orsus 的双目图像接口为 `ros.orsus.left_image`（`/{robot}/Orsus_L_cam`）和 `ros.orsus.right_image`（`/{robot}/Orsus_R_cam`），消息类型同为 `sensor_msgs/msg/Image`，也由 Camera Tool 控制。Orsus 点云、里程计和 scan 仍由导航接口控制。独立 LiDAR payload 的 `/cloud` 与 `/odometry` 也只在同时选择 Navigation I/O 后声明和发布。

统一查看当前及稍后启动的全部相机图像：

```bash
source /opt/ros/humble/setup.bash
python3 tools/ros2/vis_sensors.py
```

也可以只查看一个无人机 namespace 下的相机：

```bash
python3 tools/ros2/vis_sensors.py --sensor camera --namespace /iris_1
```

## 键盘运动接口

Keyboard Tool 与导航接口都可以为机器人启用 `/<robot>/cmd_vel` 订阅。无人机使用 `ros.aerial_cmd_vel` 接口和 `geometry_msgs/msg/Twist` 消息；键盘发布器的按键映射如下：

| 按键 | Twist 字段 | 动作 |
|---|---|---|
| `W/S` | `linear.x` | 前进/后退 |
| `A/D` | `linear.y` | 左/右侧飞（支持全向运动的机器人） |
| `R/F` | `linear.z` | 上升/下降 |
| `C/V` | `angular.z` | 偏航 |

`K` 或空格停止，`Q` 切换已发现的机器人，`Esc` 或 `Ctrl-C` 退出。使用 `--linear-speed`、`--vertical-speed` 和 `--angular-speed` 分别设置平移、垂直和偏航命令幅值，例如：

```bash
source /opt/ros/humble/setup.bash
python3 algorithm/keyboard/keyboard.py \
  --robot iris_1 \
  --vertical-speed 0.5
```

## 机械臂接口

UR5 和 Z1 使用相同的正式 ROS2 namespace 规范。主启动器会为 selection 中实际挂载的 UR5 和 Z1 调用共享 manager 的 `setup_robot(...)`；但静态接口声明仍不代表 graph 已成功激活。完整启动、控制和故障排查说明集中在[机械臂](ur5_control.md)；本页只保留接口目录和查询命令。

### UR5 机械臂接口

UR5 接口清单包含两个输入 topic 和两个输出 topic：

| 接口 ID | 端点模板 | 方向 |
|---|---|---|
| `ros.ur5.target_pose` | `/{robot}/ur5/target_pose` | 输入 |
| `ros.ur5.joint_command` | `/{robot}/ur5/joint_command` | 输入 |
| `ros.ur5.joint_states` | `/{robot}/ur5/joint_states` | 输出 |
| `ros.ur5.ee_pose` | `/{robot}/ur5/ee_pose` | 输出 |

这些接口适用于 Go2、B2、M20、Scout 和 Lite3。端点中的 `{robot}` 始终替换为 Env Builder 生成的实际实例名，例如 `go2_1` 或 `m20_2`。接口目录和运行时注册都按场景中真正挂载 `ur5` 的实例展开，不会为未挂载机械臂的机器人创建 UR5 topic。

UR5 topic 由 Isaac Sim ROS2 Bridge 的原生 OmniGraph 节点直接创建，命名层级与 Orsus 保持一致：机器人实例名位于一级 namespace，设备名 `ur5` 位于二级 namespace。机械臂接口不使用 `tmp/` 文件，也不需要独立 Python bridge。

查询已保存 DIY 环境中的实际机械臂端点：

```bash
python simulator.py interfaces scene --env <env_name> | grep '/ur5/'
```

仿真运行时检查当前机械臂端点：

```bash
python simulator.py interfaces status | grep '/ur5/'
ros2 topic list | grep '/ur5/'
```

`interfaces test` 可以检查 `joint_states` 和 `ee_pose` 等只读输出接口，但不会主动发送 `target_pose` 或 `joint_command`，避免测试命令意外移动机械臂。完整控制方式参见[机械臂](ur5_control.md)。

### Z1 机械臂接口

Z1 与 UR5 使用相同的原生 OmniGraph 命名层级，并增加独立夹爪输入输出：

| 接口 ID | 端点模板 | 方向 |
|---|---|---|
| `ros.z1.target_pose` | `/{robot}/z1/target_pose` | 输入 |
| `ros.z1.joint_command` | `/{robot}/z1/joint_command` | 输入 |
| `ros.z1.joint_states` | `/{robot}/z1/joint_states` | 输出 |
| `ros.z1.ee_pose` | `/{robot}/z1/ee_pose` | 输出 |
| `ros.z1.gripper_command` | `/{robot}/z1/gripper_command` | 输入 |
| `ros.z1.gripper_state` | `/{robot}/z1/gripper_state` | 输出 |

Z1 支持 Carter、Go2、B2、M20、Scout 和 Lite3。接口只按场景中真正挂载 Z1 的实例展开。同一机器人不能同时挂载 UR5 和 Z1，但不同机器人可以分别选择不同机械臂。

```bash
python simulator.py interfaces scene --env <env_name> | grep '/z1/'
python simulator.py interfaces status | grep '/z1/'
ros2 topic list | grep '/z1/'
```

控制和状态检查命令参见[机械臂](ur5_control.md)。

## 运行时状态

仿真启动后会原子写入：

```text
tmp/runtime_interfaces.json
```

可在另一个终端查看：

```bash
python simulator.py interfaces status
python simulator.py interfaces status --probe
```

`status` 展示当前场景、进程 PID、快照年龄、机器人实例以及解析后的实际端点。`--probe` 额外检查只读 ROS Topic 是否存在；运动命令和机械臂控制等写接口不会被执行。

如果希望场景启动后同时打开交互菜单：

```bash
python simulator.py --env EAI-Factory-v0 --interfaces-menu
```

也可以单独进入菜单：

```bash
python simulator.py interfaces menu
```

## 只读测试

默认只检查接口是否存在：

```bash
python simulator.py interfaces test ros.orsus.left_image --endpoint /carter_1/Orsus_L_cam
```

读取一条消息摘要，不输出完整图像或点云负载：

```bash
python simulator.py interfaces test ros.orsus.left_image \
  --endpoint /carter_1/Orsus_L_cam --mode sample
```

在有限时间内统计 Topic 频率：

```bash
python simulator.py interfaces test ros.orsus.point_cloud \
  --endpoint /carter_1/cloud --mode hz
```

若省略 `--endpoint`，命令优先从运行时快照寻找匹配接口。缺少 ROS 2 CLI、接口不存在或探测超时都会返回明确状态，不影响仿真进程。

运动 Topic、Service、Action 和其他输入接口只展示调用示例。当前版本拒绝通过 `interfaces test` 执行它们。

## 添加新设备

静态清单位于：

```text
source/EAI/EAI/interface_catalog/interfaces/robots/
source/EAI/EAI/interface_catalog/interfaces/sensors/
```

示例：

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

必填设备字段为 `id`、`name`、`category`、`models` 和 `interfaces`。必填接口字段为 `id`、`name`、`protocol`、`direction`、`kind`、`endpoint` 和 `data_type`。

端点与示例支持以下占位符：

- `{robot}`：实际机器人实例名，如 `carter_1`；
- `{robot_type}`：机器人型号；
- `{sensor}`：附件类型；
- `{env}`：场景名称；
- `{index}`：机器人在场景中的序号。

YAML 描述“应该提供什么”，运行时快照描述“当前创建了什么”，探测结果描述“此刻是否可用”。三类信息应保持分离。
