---
orphan: true
---

# RealSense D455 传感器

RealSense D455 是一个集成 **RGB 彩色相机、深度相机与 6 轴 IMU** 的传感器模块，可解耦装载到 Pepper、MuSHR v2、Carter、Go2、B2、M20、Scout、Coco 和 Lite3 等机器人上。其 ROS2 发布方式、话题命名与门控机制与 [Orsus](orsus_sensor.md) 完全对齐。

MuSHR v2 的 USD 中已删除内置相机与 `camera_link`（保留 `camera_bottom_screw_frame` 挂架），D455 即该机器人的相机：装载 D455 后不再合成内置单目相机，图像发布完全由 D455 载荷负责（外参按标定值 `(0.03345, -0.00097, 0.01424)`）。

RGB/深度图像与相机内参只受同一宿主上的 `camera` tool 控制；IMU 只受 `ros` tool 控制，两个开关彼此独立。

包含 RealSense D455（且启用了 camera/ros tool）的场景当前只支持单环境，启动时必须传入 `--num_envs 1`（与 Orsus 一致）。

## 功能概述

RealSense D455 提供以下功能（基于官方 Intel RealSense D455 资产 `rsd455.usd`，与 Isaac Sim 资产库一致）：

1. **RGB 图像**: 发布 `/<robot>/RealsenseD455_rgb`（sensor_msgs/Image，rgb8，1280x720）
2. **深度图像**: 发布 `/<robot>/RealsenseD455_depth`（sensor_msgs/Image，32FC1，米）
3. **相机内参**: 发布 `/<robot>/RealsenseD455_camera_info`（sensor_msgs/CameraInfo）
4. **IMU**: 发布 `/<robot>/RealsenseD455_imu`（sensor_msgs/Imu，线加速度含重力 / 角速度 / 四元数）
5. **ROS2 集成**: 自动配置 ROS2 环境，并按机器人实例名设置话题命名空间

## 架构

与 Orsus 相同，RealSense D455 的发布图内置在资产 USD 中（GS_Hub 同款模板），
camera/ros 两个 tool 在 spawn 时通过 `prim.SetActive` 独立门控各组图，
每个实例的 ROS namespace 在 spawn 时覆写发布节点的 `inputs:nodeNamespace`：

```
Pepper / Carter / Go2 / ...（兼容机器人）
    └─ RealsenseD455
       ├─ Graphs/ROS2_publish_RGB         ─┐
       ├─ Graphs/ROS2_publish_Depth        ├─ camera tool 门控
       ├─ Graphs/ROS2_publish_CameraInfo  ─┘
       └─ Graphs/ROS2_publish_IMU         ── ros tool 门控
          （IsaacReadIMU 读取载荷自身 Imu_Sensor，载荷刚体保持 kinematic）
    ↓ ROS2 话题
/<robot>/RealsenseD455_rgb / RealsenseD455_depth / RealsenseD455_camera_info / RealsenseD455_imu
```

载荷刚体默认设为 kinematic 并关闭碰撞（保留 RigidBodyAPI 以匹配 Isaac Lab
物理视图模式列表），既避免嵌套刚体干扰宿主，又让内置 IMU 传感器持续输出
重力、姿态与随宿主运动产生的加速度。

## 在环境中使用

### 1. JSON 环境配置

在环境配置文件中给机器人添加 `realsense_d455` 载荷与 `camera`/`ros` tool：

```json
{
  "scene_key": "plane",
  "robots": [
    {
      "type": "pepper",
      "controller": {"mode": "default", "cfg": "PEPPER_HOLONOMIC_CFG"},
      "attachments": [
        {"type": "realsense_d455"},
        {"type": "camera"},
        {"type": "ros"}
      ]
    }
  ]
}
```

### 2. 环境配置类（编程方式）

```python
from EAI_assets.sensor.high_sensor import RealSenseD455Cfg

@configclass
class YourSceneCfg(InteractiveSceneCfg):
    realsense = RealSenseD455Cfg(
        prim_path="{ENV_REGEX_NS}/Pepper/Pepper/Head/RealsenseD455",
        init_state=AssetBaseCfg.InitialStateCfg(pos=(0.0, 0.0, 0.14)),
        ros_namespace="/pepper_1",
        enable_camera_publish=True,
        enable_imu_publish=True,
    )
```

**注意**:
- `prim_path` 必须指向机器人挂载链接的子路径（Pepper 头顶为 `Head`）
- 位置/朝向按宿主在 `env_builder.py` 的 `RobotOption` 中声明（`realsense_mount_link` / `realsense_offset` / `realsense_rot`），可按实际机器人标定

### 3. Env DIY（终端 / 网页 / 3D 编辑器）

三种 Env DIY 方式均已注册 `realsense_d455` 传感器载荷：

- 终端：无参运行 `python simulator.py`，Payloads 步骤中选择 RealSense D455
- 网页编辑器：`python simulator.py --diy`（载荷卡片 RealSense D455）
- 3D 编辑器：`python simulator.py --diy-3d`（3D 视口中放置，自动预览）

三种方式生成的 JSON 完全一致，可通过 `python simulator.py --env <name>` 复用。

## 发布的话题

话题 namespace 与机器人实例名一致。例如第一台 Pepper 会发布：

```text
/pepper_1/RealsenseD455_rgb
/pepper_1/RealsenseD455_depth
/pepper_1/RealsenseD455_camera_info
/pepper_1/RealsenseD455_imu
```

### 门控矩阵

| 选择 | RGB/深度/内参 | IMU |
|---|---|---|
| `realsense_d455` + `camera` | ✅ | ❌ |
| `realsense_d455` + `ros` | ❌ | ✅ |
| `realsense_d455` + `camera` + `ros` | ✅ | ✅ |
| 仅 `realsense_d455` | ❌ | ❌ |

## 使用示例

### 示例 1: Pepper 头顶装载验证环境

保存以下 JSON 为 `source/EAI_hmrs/EAI_hmrs/envs/pepper_realsense.json`：

```json
{
  "scene_key": "plane",
  "robots": [
    {
      "type": "pepper",
      "controller": {"mode": "default", "cfg": "PEPPER_HOLONOMIC_CFG"},
      "attachments": [
        {"type": "realsense_d455"},
        {"type": "camera"},
        {"type": "ros"}
      ]
    }
  ]
}
```

运行环境：

```bash
python simulator.py --env=pepper_realsense
```

### 示例 2: 检查 ROS2 话题

在另一个终端中：

```bash
# 列出 RealSense 话题
ros2 topic list | grep RealsenseD455

# 查看 IMU（以 pepper_1 为例）
ros2 topic echo /pepper_1/RealsenseD455_imu

# 查看图像发布频率
ros2 topic hz /pepper_1/RealsenseD455_rgb
```

### 示例 3: 可视化

```bash
source /opt/ros/humble/setup.bash
python3 algorithm/ros/tools/vis_sensors.py --sensor realsense --namespace /pepper_1
```

打开 RGB 与深度两个窗口；IMU 用 `ros2 topic echo /pepper_1/RealsenseD455_imu` 查看。

## 故障排除

### 问题 1: 图像话题未发布

**检查**:
1. JSON 中是否同时选择了 `realsense_d455` 与 `camera` tool（缺一不可）
2. ROS2 环境：`echo $ROS_DISTRO`（自动配置 humble + rmw_fastrtps_cpp）
3. 仿真日志中的 `[RealsenseD455]` 消息（应显示 `camera=on` 与 namespace 覆写成功）

### 问题 2: IMU 话题未发布

**检查**:
1. JSON 中是否选择了 `ros` tool
2. 日志中 `[RealsenseD455] Publisher graphs: ... imu=on`
3. GUI 模式下话题应约 27Hz 发布（headless 模式下物理 IMU 可能无读数，
   话题不注册属预期行为）

### 问题 3: 载荷干扰宿主机器人

载荷刚体默认设为 kinematic 并关闭碰撞（`disable_physics=True`），不影响
宿主动力学。若需要载荷参与物理，可设置 `RealSenseD455Cfg(disable_physics=False)`。

### 问题 4: 终端刷屏 "No adjacent samples found for interpolation at time N/30"

Isaac Sim 5.1 的 simulation manager 只保留 31 个仿真时间插值样本（按 ~60Hz
设计），而 EAI 物理步长为 200Hz，传感器发布图的帧时间查询先于样本记录，
产生每帧两条警告（回退值正确，纯噪音）。`simulator.py` 启动时会把
`isaacsim.core.simulation_manager.plugin` 通道的日志阈值提高到 ERROR
（保留真实错误），日志中可见：

    [EAI Simulator] Suppressed isaacsim.core.simulation_manager.plugin warning spam.

若该行未出现，检查 `simulator.py::_silence_simulation_manager_time_log_spam`。

## 参考

- **实现文件**: `source/EAI_assets/EAI_assets/sensor/high_sensor/realsense_d455.py`
- **USD 资产**: `usd/payloads/sensors/realsense_d455/rsd455_d455.usd`（引用官方 `rsd455.usd`，内置 4 个发布图）
- **Orsus 对齐参考**: `source/EAI_assets/EAI_assets/sensor/high_sensor/orsus.py`
- **接口声明**: `source/EAI/EAI/interface_catalog/interfaces/sensors/realsense_d455.yaml`
