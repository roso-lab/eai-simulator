# Pegasus 无人机

EAI 内置 3DR Iris、Pegasus research quadrotor 与 CF2X 三种无人机，支持键盘/ROS2 目标控制，默认搭载前视单目相机、`Example_Rotary` 128 线 LiDAR 与 IMU/GPS 等基础传感器。机体 USD 与控制器由 EAI 资产解析器从 Hugging Face 数据集按需下载，无需安装额外扩展。

## 快速运行

```bash
conda activate env_isaaclab
python simulator.py --env=pegasus_drones --device=cuda:0
```

示例生成单台 `iris_1`，默认高度为 1 m，并启用键盘目标控制和 ROS。
键盘/ROS `Twist` 的 `linear.x/y/z` 更新三维目标位置，`angular.z` 更新目标航向。
控制 topic 是 `/iris_1/cmd_vel`。

`iris`、`pegasus` 和 `cf2x` 默认均带有前视单目相机与无人机 LiDAR；未选择 Tool 时，
传感器实体仍存在于场景中。ROS 2 发布使用两个相互独立的 Env DIY tool：同一无人机分支添加 Camera 后，
发布 `/<robot>/camera/image_raw`（`sensor_msgs/msg/Image`）和
`/<robot>/camera/camera_info`（`sensor_msgs/msg/CameraInfo`）；添加导航接口（Navigation I/O）后，
发布 `/<robot>/lidar/pointcloud`（`sensor_msgs/msg/PointCloud2`）。因此只选择
Camera 不会发布 LiDAR topic，只选择导航接口也不会发布相机 topic，两者都选择时才同时发布。导航接口在环境 JSON 中仍使用内部键 `ros`。

启动仿真后，可在 ROS 2 Humble 终端直接运行统一传感器查看器。无参数模式会动态发现
当前 ROS graph 中的所有 `sensor_msgs/msg/Image` topic，因此同时支持 Iris、Pegasus、
CF2X 单目相机和 Orsus 左右相机；即使相机晚于查看器启动，也会自动订阅：

```bash
source /opt/ros/humble/setup.bash
python3 algorithm/ros/tools/vis_sensors.py
```

只查看一台无人机时，通过 namespace 过滤。例如内置示例的实例名为 `iris_1`：

```bash
python3 algorithm/ros/tools/vis_sensors.py --sensor camera --namespace /iris_1
```

`iris`、`pegasus` 和 `cf2x` 均包含带白噪声、随机游走、启动偏置和一阶时变偏置的
加速度计与陀螺仪，以及 GPS、磁力计和气压计。这些模型默认存在；同一无人机分支上的
导航接口只控制对应 ROS topic 的发布。

三种无人机的 LiDAR 使用 Pegasus Simulator 原实现的
`IsaacSensorCreateRtxLidar` 与 `Example_Rotary` 配置，不复用地面机器人的
HESAI/Pandar 传感器。`Example_Rotary` 是 128 线 3D LiDAR，因此不发布仅适用于
2D LiDAR 的 `LaserScan`。

## 资产和配置

| Env DIY 类型 | USD | 默认 controller cfg |
|---|---|---|
| `cf2x` | `usd/robot/cf2x/cf2x.usd` | `QUADCOPTER_GOAL_SKRL_CFG` |
| `iris` | `usd/robot/pegasus/iris/iris.usd` | `PEGASUS_IRIS_POSITION_CFG` |
| `pegasus` | `usd/robot/pegasus/pegasus/pegasus_optimized.usdc` | `PEGASUS_X4_POSITION_CFG` |

默认 cfg 使用几何位置/航向控制器。控制器将目标位姿转换为总推力与三轴力矩，
再通过每个机型的真实转子坐标分配为四个转子角速度。执行层保留 Pegasus 的
`T = k * omega^2` 二次推力、桨叶反扭矩与机体系线性阻力。

需要外部算法直接输出四个电机转速（rad/s）时，可在 JSON 中手动选择
`PEGASUS_IRIS_ROTOR_CFG` 或 `PEGASUS_X4_ROTOR_CFG`，随后调用：

```python
rotor_speed = torch.tensor([[650.0, 650.0, 650.0, 650.0]], device=env.device)
env.step({"iris_1": rotor_speed})
```

转子直控 cfg 的输入顺序是 `[rotor0, rotor1, rotor2, rotor3]`，单位为 rad/s，
范围被限制到 `[0, 1100]`。它适合接 PX4/ArduPilot 或自定义飞控，但 EAI 当前
没有把 Pegasus 的 MAVLink 后端一起嵌入；外部飞控需自行把输出转换为该张量接口。

## 来源与许可证

动力学与机体资产源自 [Pegasus Simulator](https://github.com/PegasusSimulator/PegasusSimulator)
（BSD-3-Clause）；3DR Iris 模型来自
[PX4](https://github.com/PX4/PX4-SITL_gazebo-classic/)（BSD-3-Clause）。完整署名与许可证文本见：

- {download}`Pegasus Simulator 来源说明 <_static/licenses/pegasus_simulator/README.md>`
- {download}`Pegasus Simulator BSD-3-Clause 许可证 <_static/licenses/pegasus_simulator/LICENSE>`
- {download}`3DR Iris / PX4 BSD-3-Clause 许可证 <_static/licenses/pegasus_simulator/IRIS_LICENSE.rst>`
