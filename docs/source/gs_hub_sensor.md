---
orphan: true
---

# GS-Hub 传感器

GS-Hub 是一个集成传感器模块，包含激光雷达（Lidar）和里程计（Odometry），用于 ROS2 导航栈集成。

GS-Hub 可挂载到 Carter、Go2、B2、M20、Scout、Coco 和 Lite3。除点云与里程计外，当前 GS-Hub USD Graph 还会发布左右相机图像，可使用仓库中的 `algorithm/ros/tools/vis_sensors.py` 同时查看双目图像和点云俯视图。相机、点云和里程计发布需要在同一宿主机器人上挂载 `ros` tool。

## 功能概述

GS-Hub 传感器提供以下功能：

1. **左/右相机图像**: 发布 `/<robot>/GS_Hub_L_cam` 和 `/<robot>/GS_Hub_R_cam`（sensor_msgs/Image）
2. **点云输出**: 发布 `/<robot>/cloud` 话题（sensor_msgs/PointCloud2）
3. **里程计信息**: 发布 `/<robot>/odometry` 话题（nav_msgs/Odometry）
4. **ROS2 集成**: 自动配置 ROS2 环境，并按机器人实例名设置话题命名空间

## 架构

GS-Hub 使用 Isaac Sim 的 Graph 系统实现：

```
Carter 机器人
    ↓ (物理状态)
GS-Hub Graph
    ├─ Isaac Compute Odometry Node
    │   └─ 发布 /<robot>/odometry（里程计）
    └─ Isaac Publish Lidar Node
        └─ 发布 /<robot>/cloud（点云）
    ↓ (ROS2 话题)
ROS2 Navigation2 导航栈
```

## 在环境中使用

### 1. 在场景配置中添加 GS-Hub

在环境配置文件的场景类中添加 GS-Hub：

```python
from EAI_assets.sensor.high_sensor import GSHubCfg

@configclass
class YourSceneCfg(InteractiveSceneCfg):
    # ... 其他资产 ...
    
    gs_hub = GSHubCfg(
        # 将 GS-Hub 附加到机器人的底盘链接
        prim_path="{ENV_REGEX_NS}/Carter/Carter/GS_Hub_chassis_link/GSHub",
        # 外参标定数据（相对于底盘链接）
        init_state=AssetBaseCfg.InitialStateCfg(
            pos=(0.026, 0, 0.418),  # (x, y, z) 米
        )
    )
```

**注意**: 
- `prim_path` 必须指向机器人底盘链接的子路径
- 位置需要根据实际机器人模型进行标定

### 2. 自动修复机制

GS-Hub 在加载时会自动修复 Graph 中的 `chassisPrim` 连接：

```python
def spawn_and_fix_gshub(prim_path, cfg, translation, orientation):
    # 1. 加载 USD 模型
    sim_utils.spawn_from_usd(...)
    
    # 2. 查找所有环境实例
    matched_parents = prim_utils.find_matching_prim_paths(parent_regex)
    
    # 3. 为每个实例修复 Graph 连接
    for specific_path in resolved_paths:
        # 修复 odometry node 的 chassisPrim 连接
        rel.SetTargets([target_path])
```

这确保了多环境场景下每个实例都能正确连接到对应的机器人。

## ROS2 环境配置

GS-Hub 会自动配置 ROS2 环境：

```python
def configure_ros_env():
    # 1. 设置 ROS2 版本
    os.environ["ROS_DISTRO"] = "humble"
    os.environ["RMW_IMPLEMENTATION"] = "rmw_fastrtps_cpp"
    
    # 2. 查找 Isaac ROS Bridge 路径
    isaac_ros_path = find_isaac_ros_bridge_path()
    
    # 3. 设置环境变量
    os.environ["ISAAC_ROS_PATH"] = isaac_ros_path
    os.environ["LD_LIBRARY_PATH"] = ...
    os.environ["AMENT_PREFIX_PATH"] = ...
```

**前置要求**:
- 已安装 Isaac Sim ROS2 Bridge
- ROS2 Humble 环境

## 发布的话题

GS-Hub 会根据机器人实例名设置 ROS namespace。例如 `carter_1` 机器人会发布 `/carter_1/GS_Hub_L_cam`、`/carter_1/GS_Hub_R_cam`、`/carter_1/odometry` 和 `/carter_1/cloud`。

### `/<robot>/GS_Hub_L_cam` 与 `/<robot>/GS_Hub_R_cam` (sensor_msgs/Image)

左右相机分别提供 GS-Hub 的双目图像。话题 namespace 与 Env DIY 生成的机器人实例名一致，例如第一台 Carter 通常使用：

```text
/carter_1/GS_Hub_L_cam
/carter_1/GS_Hub_R_cam
```

如果环境包含多台同类型机器人，请先通过 `ros2 topic list` 确认实际实例名。

### `/<robot>/odometry` (nav_msgs/Odometry)

**频率**: 与仿真步频同步（通常 60 Hz）

**内容**:
- `pose.pose.position`: 机器人位置（x, y, z）
- `pose.pose.orientation`: 机器人姿态（四元数）
- `twist.twist.linear`: 线速度（vx, vy, vz）
- `twist.twist.angular`: 角速度（wx, wy, wz）

### `/<robot>/cloud` (sensor_msgs/PointCloud2)

**频率**: 与仿真步频同步（通常 60 Hz）

**内容**:
- 3D 点云数据，原始 frame 语义由 GS-Hub USD 图给出
- Nav2 使用前应通过 `algorithm/ros/nav2/tf_bridge.py` 和 `pointcloud_to_laserscan` 处理

### `/<robot>/scan` (sensor_msgs/LaserScan)

`/<robot>/scan` 不是 GS-Hub 直接发布的话题，而是 `algorithm/ros/nav2` 将 `/<robot>/cloud` 处理后生成的 Nav2 输入话题。

## 使用示例

### 示例 1: Carter ROS2 传感器环境

参考 JSON 配置 `source/EAI_hmrs/EAI_hmrs/envs/nav2.json`。

运行环境：

```bash
python simulator.py \
  --env=nav2 \
  --num_envs=1
```

### 示例 2: 检查 ROS2 话题

在另一个终端中：

```bash
# 列出所有话题
ros2 topic list

# 筛选 GS-Hub 相机与点云话题
ros2 topic list | grep -E 'GS_Hub_[LR]_cam|/cloud$'

# 查看里程计信息（以 carter_1 为例）
ros2 topic echo /carter_1/odometry

# 查看原始点云
ros2 topic echo /carter_1/cloud

# 查看 Nav2 使用的 LaserScan（启动 algorithm/ros/nav2 后）
ros2 topic echo /carter_1/scan
```

### 示例 3: 可视化 GS-Hub 相机与点云

先在一个终端启动带 GS-Hub 和 ROS tool 的图形化仿真环境。仓库内置的 `nav2` 环境使用 Factory + Carter + GS-Hub：

```bash
conda activate env_isaaclab
python simulator.py --env=nav2 --num_envs=1 --device=cuda:0
```

等待 Isaac Sim 完成加载后，在另一个终端运行可视化脚本。`nav2` 中的机器人实例名为 `carter_1`，因此 namespace 使用 `/carter_1`：

```bash
source /opt/ros/humble/setup.bash
python3 algorithm/ros/tools/vis_sensors.py \
  --sensor gshub \
  --namespace /carter_1
```

脚本会订阅以下三个话题，并打开 `Left Camera`、`Right Camera` 和 `Lidar BEV` 三个 OpenCV 窗口：

```text
/carter_1/GS_Hub_L_cam
/carter_1/GS_Hub_R_cam
/carter_1/cloud
```

其他机器人只需替换 namespace。例如查看第一台 Go2 的 GS-Hub：

```bash
source /opt/ros/humble/setup.bash
python3 algorithm/ros/tools/vis_sensors.py --sensor gshub --namespace /go2_1
```

如果使用旧的、没有按机器人实例划分 namespace 的 GS-Hub 场景，脚本默认 namespace 是 `/isaac`，可直接运行：

```bash
source /opt/ros/humble/setup.bash
python3 algorithm/ros/tools/vis_sensors.py
```

运行前需要系统 Python 环境提供 `rclpy`、`sensor_msgs`、`cv_bridge`、OpenCV 和 NumPy。若窗口没有图像，先检查对应话题是否存在并持续发布：

```bash
ros2 topic list | grep GS_Hub
ros2 topic hz /carter_1/GS_Hub_L_cam
ros2 topic hz /carter_1/GS_Hub_R_cam
```

```{figure} assets/media/gs-hub_demo.gif
:alt: GS-Hub 左右相机与点云可视化演示
:class: eai-doc-media
:width: 100%

使用 `vis_sensors.py` 查看 GS-Hub 双目图像与点云俯视图
```

### 示例 4: 集成 Navigation2

启动当前仓库维护的 Navigation2 导航栈：

```bash
bash algorithm/ros/nav2/run_nav2.sh --rviz
```

发送导航目标：

```bash
/usr/bin/python3 algorithm/ros/nav2/send_goal.py --x -7.97 --y -6.53
```

## 工作流程

### 完整 ROS2 导航工作流

```
1. 启动仿真环境
   python simulator.py --env=nav2

2. GS-Hub 自动发布话题
   /carter_1/odometry → tf_bridge → odom->base_link
   /carter_1/cloud → tf_bridge + pointcloud_to_laserscan → /carter_1/scan

3. Navigation2 规划路径
   /plan → 路径规划
   /carter_1/cmd_vel → 速度命令

4. 速度命令转换为机器人动作
   env.step({"carter_1": cmd_vel_tensor})
   → controller.compute_action_from_command(...)
   → controller.apply_action(...)

5. 机器人移动，GS-Hub 更新传感器数据
   循环回到步骤 2
```

## 故障排除

### 问题 1: ROS2 话题未发布

**检查**:
1. 确认 Isaac ROS Bridge 已安装
2. 检查 ROS2 环境变量：`echo $ROS_DISTRO`
3. 查看仿真日志中的 `[EnvSetup]` 消息

**解决**:
```bash
# 手动设置 ROS2 环境（如果需要）
source /opt/ros/humble/setup.bash
export ROS_DISTRO=humble
```

### 问题 2: Graph 连接失败

**检查**: 查看仿真日志中的 `[GSHub]` 消息

**解决**: 确认 `prim_path` 正确指向机器人底盘链接

### 问题 3: 传感器数据异常

**检查**: 
- 激光雷达范围设置
- 机器人位置是否正确

**解决**: 检查 USD 文件中的传感器配置

## 扩展

### 添加其他传感器

可以参考 GS-Hub 的实现方式添加其他传感器：

1. 创建 USD 文件（包含 Graph）
2. 创建 Python 配置类（继承 `AssetBaseCfg`）
3. 实现 `spawn` 函数（加载 USD，配置连接）

### 自定义话题名称

修改 USD 文件中的 Graph 配置，更改话题名称。

## 参考

- **实现文件**: `source/EAI_assets/EAI_assets/sensor/high_sensor/gs_hub.py`
- **USD 文件**: `usd/payloads/sensors/gs_hub/GS_Hub_fix.usd`
- **环境配置示例**: `source/EAI_hmrs/EAI_hmrs/envs/nav2.json`
- **动态挂载实现**: `source/EAI_hmrs/EAI_hmrs/env_builder.py`
- **ROS2 Navigation2**: https://navigation.ros.org/
