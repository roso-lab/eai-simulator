# 载荷组件

Env DIY 将安装到宿主机器人上的设备保存在 `robots[].attachments[]`。当前分为 Sensors（Orsus、LiDAR、RealSense D455）和 Manipulators（UR5、Z1）；可视化窗口、终端快速模式和 Isaac Sim 3D 编辑器共用同一份目录与兼容性规则。

## 工程结构

| 位置 | 职责 |
|---|---|
| `source/EAI/EAI/hmrs_env/env_diy/catalog.py` | 定义载荷类型、默认 controller cfg 和支持的宿主机器人，是三个 Env DIY 前端共用的目录真源 |
| `source/EAI/EAI/hmrs_env/env_diy/flow.py` | 将界面选择转换为 `AttachmentSelection`，检查重复项、宿主兼容性以及 UR5/Z1、Orsus/LiDAR 互斥规则 |
| `source/EAI/EAI/hmrs_env/env_diy/storage.py` | 将载荷写入环境 JSON，并在读取时规范化 `robots[].attachments[]` |
| `source/EAI_hmrs/EAI_hmrs/env_builder.py` | 根据环境选择创建传感器、机械臂 articulation、FixedJoint 和对应控制器 |
| `source/EAI_assets/EAI_assets/sensor/` | 提供 Orsus 与 LiDAR 的资产配置和 ROS2 发布实现 |
| `source/EAI_assets/EAI_assets/robots/*_mount.py` | 定义 UR5/Z1 的通用挂载原语及不同宿主的安装 profile |
| `source/EAI_assets/EAI_assets/controller/traditional/` | 提供 `UR5_IK_CFG`、`Z1_IK_CFG` 与公共机械臂 IK 控制器 |
| `source/EAI/EAI/hmrs_ros/manipulator_omnigraph.py` | 按机器人实例建立机械臂 ROS2 命令与状态接口 |
| `usd/payloads/sensors/` | 保存 Orsus、LiDAR 等传感器 USD 资产 |
| `usd/payloads/manipulators/` | 保存 UR5、Z1 等机械臂 USD、URDF 与源描述资产 |

## 调用方式

使用 Env DIY 时，先选择宿主机器人，再从 `Payloads → Sensors` 或 `Payloads → Manipulators` 添加兼容设备。保存后的配置示例：

```json
{
  "type": "go2",
  "controller": {"mode": "default", "cfg": "GO2_VELOCITY_RSL_CFG"},
  "attachments": [
    {"type": "orsus", "controller": null},
    {"type": "z1", "controller": {"mode": "default", "cfg": "Z1_IK_CFG"}},
    {"type": "navigation_io", "controller": null}
  ]
}
```

之后可以直接启动保存的环境：

```bash
python simulator.py --env=<env_name> --num_envs=1 --device=cuda:0
```

运行时调用链如下：

```text
环境 JSON
  → storage.py / flow.py 解析并校验 attachments
  → env_builder.py 匹配宿主安装参数
  → 创建传感器或 <robot>_arm articulation
  → 配置 ROS2 Graph 与 controller
  → MultiRobotDirectEnv 启动正式环境
```

Orsus 的左右相机图像只由 Camera Tool 控制，点云和里程计只由导航接口（Navigation I/O）控制。导航接口在环境 JSON 中使用内部键 `navigation_io`。Iris、Pegasus、CF2X 默认带有相机、`Example_Rotary` LiDAR 和基础传感器；Camera 与导航接口只控制相应 topic 发布。地面机器人只有先挂载 Orsus 或 RealSense D455 才能选择 Camera Tool；MuSHR 不支持 Orsus，因此必须显式挂载 RealSense D455 才能获得图像。当前 `simulator.py` 会为 selection 中实际挂载的 UR5 和 Z1 注册机械臂 OmniGraph，且不依赖 Navigation I/O。发送命令前仍应通过运行时 topic 确认 `setup_robot(...)` 已成功，而不能只依据静态接口声明。

## 适配性

| 载荷 | 分类 | 支持的宿主机器人 | 默认控制配置 |
|---|---|---|---|
| Orsus | Sensor | Carter、Go2、B2、M20、Scout、Coco、Lite3 | 无 |
| LiDAR | Sensor | Carter、Go2、B2、M20、Scout、MuSHR v2、Coco、Lite3 | 无 |
| RealSense D455 | Sensor | Pepper、MuSHR v2、Carter、Go2、B2、M20、Scout、Coco、Lite3 | 无 |
| UR5 | Manipulator | Go2、B2、M20、Scout、Lite3 | `UR5_IK_CFG` |
| Z1 | Manipulator | Carter、Go2、B2、M20、Scout、Lite3 | `Z1_IK_CFG` |

同一宿主可以安装兼容的传感器和一种机械臂，但不能同时安装 Orsus 与 LiDAR，也不能同时安装 UR5 与 Z1。互斥只作用于同一机器人；不同机器人可分别使用 Orsus 或 LiDAR。每个实例的 ROS2 namespace 使用 Builder 生成的机器人名称，例如 `go2_1`、`m20_1`；多机器人之间的传感器数据和机械臂命令相互隔离。

## 分类文档

- [Orsus 传感器](orsus_sensor.md)
- [RealSense D455](realsense_tutorial.md)
- [机械臂](ur5_control.md)
