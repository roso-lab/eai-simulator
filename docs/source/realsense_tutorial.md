# RealSense D455

RealSense D455 是集成 **RGB 彩色相机、深度相机与 6 轴 IMU** 的传感器模块，作为可解耦装载的载荷安装在机器人上，兼容 Pepper、MuSHR v2、Carter、Go2、B2、M20、Scout、Coco 和 Lite3 等机器人。

本页介绍其装载、运行、可视化与数据读取的完整流程。教程中的命令以机器人实例 `mushr_v2_1` 为例。

## 1. 装载 RealSense D455

装载后传感器提供四个话题：

| 话题 | 类型 | 内容 | 门控 |
|---|---|---|---|
| `/<robot>/RealsenseD455_rgb` | sensor_msgs/Image | 1280x720，rgb8 | `camera` tool |
| `/<robot>/RealsenseD455_depth` | sensor_msgs/Image | 1280x720，32FC1，单位米 | `camera` tool |
| `/<robot>/RealsenseD455_camera_info` | sensor_msgs/CameraInfo | 相机内参 | `camera` tool |
| `/<robot>/RealsenseD455_imu` | sensor_msgs/Imu | 四元数 / 角速度 / 线加速度（含重力） | Navigation I/O |

图像与 IMU 两组发布图相互独立：Camera Tool 只开关图像，导航接口（Navigation I/O）只开关 IMU（与 Orsus 的门控方式一致）。导航接口在 JSON 中使用 `navigation_io` 键。

### 1.1 通过 Env DIY 装载

在 Env DIY 的 Payloads 步骤中选择 **RealSense D455**（同时按需选择 Camera 与 Navigation I/O），可用的三种入口：

- 终端引导：`python simulator.py`
- 网页编辑器：`python simulator.py --diy`
- 3D 编辑器：`python simulator.py --diy-3d`

### 1.2 通过 JSON 环境文件装载

在 `source/EAI_hmrs/EAI_hmrs/envs/<name>.json` 中给机器人添加 `realsense_d455` 载荷与内部 `camera`/`navigation_io` 工具键（示例见仓库中的 `mushr_realsense.json`）：

```json
{
  "scene_key": "plane",
  "task_name": "mushr_realsense",
  "robots": [
    {
      "type": "mushr_v2",
      "controller": {"mode": "default", "cfg": "MUSHR_ACKERMANN_CFG"},
      "attachments": [
        {"type": "realsense_d455"},
        {"type": "camera"},
        {"type": "navigation_io"}
      ]
    }
  ]
}
```

## 2. 启动仿真并检查话题

通过 Env DIY 装载后，按提示直接运行仿真；若在 Env DIY 中保存了环境，或使用仓库内置环境，则按名称启动：

```bash
python simulator.py --env=mushr_realsense
```

在另一个终端确认四个话题都已注册：

```bash
source /opt/ros/humble/setup.bash
ros2 topic list | grep RealsenseD455
# /mushr_v2_1/RealsenseD455_rgb
# /mushr_v2_1/RealsenseD455_depth
# /mushr_v2_1/RealsenseD455_camera_info
# /mushr_v2_1/RealsenseD455_imu
```

话题命名空间与机器人实例名一致（`mushr_v2` 的第一台实例即 `mushr_v2_1`）。

## 3. 可视化 RGB 与深度图像

使用仓库自带的 `tools/ros2/vis_sensors.py`。请在带图形显示的系统 ROS Python 环境中运行；它需要 `rclpy`、`sensor_msgs`、`cv_bridge`、OpenCV 和 NumPy，这些依赖不会因脚本位于 `tools/ros2/` 而自动出现在 `env_isaaclab` 中：

```bash
# 自动发现所有 Image 话题（含 RGB 与深度）
python3 tools/ros2/vis_sensors.py

# 或显式指定 RealSense 模式与命名空间
python3 tools/ros2/vis_sensors.py --sensor realsense --namespace /mushr_v2_1
```

会弹出两个窗口：`RealSense RGB` 与 `RealSense Depth`（灰度深度图）。以下两张图为同一时刻截取（时间戳对齐，dt = 0 ms），展示 RealSense D455 装载于 MuSHR v2 机器人（Factory 场景）时的输出：

| RGB 图像（1280x720，rgb8） | 深度图像（1280x720，32FC1，单位米） |
| :---: | :---: |
| ![RealSense D455 RGB 图像（装载于 MuSHR v2 机器人，Factory 场景）](assets/media/realsense_d455_rgb.png) | ![RealSense D455 深度图像（装载于 MuSHR v2 机器人，Factory 场景）](assets/media/realsense_d455_depth.png) |

深度话题为 `32FC1`（单位米），越界/无回波像素在深度窗口中显示为黑色（无数据），有限距离按 1%~99% 百分位映射为灰度。

## 4. 读取 IMU

```bash
# 连续打印 IMU 数据
ros2 topic echo /mushr_v2_1/RealsenseD455_imu

# 只打印一条
ros2 topic echo --once /mushr_v2_1/RealsenseD455_imu

# 查看发布频率（GUI 模式下约 23 Hz）
ros2 topic hz /mushr_v2_1/RealsenseD455_imu
```

IMU 话题为 `sensor_msgs/msg/Imu`，包含四元数姿态、角速度与线加速度（含重力），`frame_id` 为 `sim_imu`。

## 5. 常见问题

| 现象 | 排查 |
|---|---|
| 图像话题未发布 | 确认同时选择了 `realsense_d455` 与 `camera` tool；仿真日志应有 `[RealsenseD455] ... camera=on` |
| IMU 话题未发布 | 确认选择了 Navigation I/O；仿真日志应有 `imu=on`；headless 模式下话题可能不注册，属预期行为 |

## 相关参考

- 接口声明：`source/EAI/EAI/interface_catalog/interfaces/sensors/realsense_d455.yaml`
- 实现文件：`source/EAI_assets/EAI_assets/sensor/high_sensor/realsense_d455.py`、`source/EAI/EAI/hmrs_ros/realsense_d455_imu.py`
