# 项目总览

本文档面向需要评估、使用或扩展 EAI Simulator 的研究者与开发者，说明平台边界、目录结构、实体目录、控制接口和主要工作流。

## 平台简介

EAI Simulator 是一个面向人机共融研究的社会化物理仿真平台。

平台基于 Isaac Lab 提供可配置的物理仿真和异构控制入口。环境层负责组合人、机器人、机械臂与传感器；算法和 Demo 层可以进一步定义角色、信息流、任务约束、多智能体讨论与协作规则。因此，社会化能力是可组合的环境与实验能力，而不是所有 JSON 环境默认启用的独立规则引擎。

- 基于 Isaac Lab 构建物理仿真层，支持强化学习、传统控制和外部策略接入。
- 当前目录覆盖足式/人形/飞行机器人、移动底盘、机械臂组合平台和人体资产。
- 当前仓库聚焦仿真运行、推理环境、预训练策略加载、ROS2 接口与可复用实验入口。

## 架构图

<iframe
  class="eai-architecture-frame"
  src="eai-simulator-architecture.html?embed=1&amp;theme-sync=3"
  title="EAI Simulator 交互式架构图"
  loading="eager"
></iframe>

## 项目目录结构

```text
eai-simulator/
├── simulator.py                         # Isaac Sim 与 JSON 环境统一入口
├── demo/
│   └── fire_rescue/                     # 灭火纯机器人实验与 8767 监控页面
├── algorithm/
│   ├── emos/                            # 多机器人 LLM 讨论与任务分配
│   ├── global_planner/                  # 二维规划、路径跟踪与速度命令
│   ├── keyboard/                        # ROS2 cmd_vel 键盘发布工具
│   └── ros/                             # ROS2 / Nav2 算法与诊断工具
├── source/
│   ├── EAI/EAI/
│   │   ├── controllers/                 # 控制器基础接口
│   │   ├── hmrs_env/
│   │   │   ├── env_diy/                # JSON 环境选择、保存与素材处理
│   │   │   ├── multi_robot_direct_env.py
│   │   │   └── update.sh               # Env DIY 素材更新入口
│   │   └── hmrs_ros/                    # 通用 ROS2 cmd_vel 输入接口
│   ├── EAI_assets/EAI_assets/
│   │   ├── robots/                      # 机器人资产
│   │   ├── scene/                       # 场景资产配置
│   │   ├── sensor/                      # GS-Hub 与 LiDAR
│   │   └── controller/                  # 传统控制器和已训练策略配置
│   ├── EAI_env_diy/
│   │   ├── config/extension.toml        # 可重载 Isaac Sim Extension 清单
│   │   └── EAI_env_diy/                 # 3D 编辑模型、Viewport UI 与 USD 预览
│   └── EAI_hmrs/EAI_hmrs/
│       ├── env_builder.py               # JSON 通用环境 Builder
│       ├── controller_loader.py         # 控制器按需加载
│       └── envs/                        # 可通过 --env 启动的 JSON
├── docs/source/                         # Sphinx 文档源文件
└── usd/                                 # 本地 USD 素材缓存与图片资源
```

## 核心模块详解

### EAI（核心模块）

**位置**: `source/EAI/EAI/`

**功能**: 提供控制器系统、环境基类、通用组件

**主要组件**:

- **`controllers/`**: 控制器基类与加载器
  - `base.py`: `ControllerCfg` 基类，定义统一接口
  - `skrl_controller.py`: SKRL RL 控制器基类
  - `rsl_controller.py`: RSL-RL ONNX 控制器基类
  - `differential_drive_controller.py`: 差速驱动控制器基类
  - `utils.py`: 工具函数（ONNXPolicy、模型加载等）
- **`hmrs_env/`**: 多机器人环境基类
  - `multi_robot_direct_env.py`: `MultiRobotDirectEnv`，基于 DirectMARL

### EAI_assets（资产与控制器）

**位置**: `source/EAI_assets/EAI_assets/`

**功能**: 管理机器人资产、场景、传感器、控制器配置

**主要组件**:

- **`robots/`**: 机器人资产配置（USD 路径、物理参数等）
- **`scene/`**: 场景配置（地形、光照、障碍物等）
- **`sensor/`**: 传感器配置
  - `high_sensor/`: 高频传感器（CPU 流，如 GS-Hub）
  - `low_sensor/`: 低频传感器（GPU 流，用于 RL）
- **`controller/`**: 控制器配置
  - `traditional/`: 传统控制器（差速驱动等）
  - `rl/`: RL 控制器（SKRL、RSL-RL）

### EAI_hmrs（推理环境）

**位置**: `source/EAI_hmrs/EAI_hmrs/`

**功能**: 保存 JSON 环境配置，并由通用 Builder 构建推理环境

**特点**:

- 基于 `MultiRobotDirectEnv`（DirectMARL 架构）
- 统一 `controllers` 字典管理
- 默认不启用 Domain Randomization
- 移除 Reward Functions

**环境列表**: 参考[环境说明](environments.md)。

## 控制器与机器人（simulator.py 可调用）


`simulator.py` 只使用 JSON 环境。传入 `--env=<name>` 时读取
`source/EAI_hmrs/EAI_hmrs/envs/<name>.json`；未指定 `--env` 时进入 Env DIY 启动菜单。选择第 3 项或使用 `python simulator.py --diy-3d` 可进入真实三维编辑，它把 Viewport transform 保存为物理 `spawn_pose`。
机器人选择由 `source/EAI/EAI/hmrs_env/env_diy/flow.py::ROBOT_KEYS` 和
`source/EAI_hmrs/EAI_hmrs/env_builder.py::ROBOT_OPTIONS` 定义，目前可选 12 类：

| Env DIY key | 机器人/对象 | 默认控制器 | 可选附件 | 常用入口 |
| ---------- | ---------- | ---------- | -------- | -------- |
| `carter` | Carter differential base | `CARTER_DIFF_CFG` | GS-Hub, LiDAR, Z1 | JSON / Env DIY |
| `pepper` | Pepper holonomic base | `PEPPER_HOLONOMIC_CFG` | - | JSON / Env DIY |
| `go2` | Unitree Go2 | `GO2_VELOCITY_RSL_CFG` | GS-Hub, LiDAR, UR5, Z1 | JSON / Env DIY |
| `b2` | Unitree B2 | `B2_VELOCITY_RSL_CFG` | GS-Hub, LiDAR, UR5, Z1 | JSON / Env DIY |
| `m20` | DeepRobotics M20 | `M20_ROUGH_RSL_CFG` | GS-Hub, LiDAR, UR5, Z1 | JSON / Env DIY |
| `scout` | Scout mobile base | `SCOUT_DIFF_CFG` | GS-Hub, LiDAR, UR5, Z1 | JSON / Env DIY |
| `g1` | Unitree G1 | `G1_SKRL_CFG` | - | JSON / Env DIY |
| `cf2x` | Crazyflie CF2X | `QUADCOPTER_GOAL_SKRL_CFG` | - | JSON / Env DIY |
| `human` | Human animation | `HUMAN_ANIMATION_CFG` | - | JSON / Env DIY |
| `lite3` | DeepRobotics Lite3 | `LITE3_VELOCITY_RSL_CFG` | GS-Hub, LiDAR, UR5, Z1 | JSON / Env DIY |
| `mushr_v2` | MuSHR Nano v2 Ackermann base | `MUSHR_ACKERMANN_CFG` | LiDAR, keyboard, ROS | JSON / Env DIY |
| `coco` | Coco AIRS Ackermann base | `COCO_ACKERMANN_CFG` | GS-Hub, LiDAR, keyboard, ROS | JSON / Env DIY |


> 控制器配置位置：`source/EAI_assets/EAI_assets/controller/`（`rl/` 与 `traditional/`）。`UR5_IK_CFG` 和 `Z1_IK_CFG` 用于上表所列兼容宿主的机械臂附件。

## 环境与任务

- 所有环境配置位于 `source/EAI_hmrs/EAI_hmrs/envs/`，目录内只放 JSON。
- `robo.json` 是包含 human 和其他机器人、可通过键盘控制的综合快速开始环境。
- `EAI-Factory-v0.json` 是 Fire Rescue 使用的固定机器人组合。
- Env DIY 保存结果与手工维护的环境使用相同 schema 和启动方式。
- 通过 `python simulator.py --env=<env_name>` 启动，不包含 `.json` 后缀。

## 工作流

1. **Env DIY 创建自定义环境** — 不传 `--env` 启动 `simulator.py`，先进入自定义 env 流程：
  ```bash
  python simulator.py --num_envs=1 --device=cuda:0
  ```
  启动后会提示选择 env 制定方式：
  - `1. 可视化窗口`：通过 Env DIY 窗口按 `Scenes → Robots → Payloads → Tools` 选择环境；Payloads 下分为 Manipulators（UR5/Z1）和 Sensors（GS-Hub/LiDAR），Tools 提供 Camera/Keyboard/ROS。Camera Tool 独立控制 Iris、Pegasus、CF2X 内置单目相机和 GS-Hub 相机的 ROS 图像发布；ROS Tool 控制三种无人机的 LiDAR、IMU、GPS、磁力计和气压计，以及 GS-Hub 的 LiDAR 点云、里程计和 scan 发布。配置可保存为 `source/EAI_hmrs/EAI_hmrs/envs/<env_name>.json`。
  - `2. 终端快速`：按与可视化窗口相同的顺序选择场景、宿主机器人、机械臂、传感器和工具，再选择控制器，并可选择是否保存和立即运行。
  - `3. Isaac Sim 3D 编辑器`：在 Isaac Sim Viewport 中编辑机器人真实 `spawn_pose`；也可运行 `python simulator.py --diy-3d --device=cuda:0` 直接进入。

2. **首次运行前申请 Hugging Face 资产权限** — 仿真所需的大体积 USD 资产和 RL 模型权重不直接放在 Git 仓库中，统一放在 gated Hugging Face 数据集：
  [HuangQIjun/eai-simulator-assets](https://huggingface.co/datasets/HuangQIjun/eai-simulator-assets)。
  使用前需要在该页面提交访问申请，等待账号通过后在终端登录：
  ```bash
  hf auth login
  ```
`simulator.py` 启动时会检查缺失的 `usd/` 资产和 `source/EAI_assets/EAI_assets/controller/rl/` 下的模型文件；通过授权后会按需下载缺失部分。`--diy-3d` 插件还会为场景、机器人、Payload、Tool 和 controller cfg 显示逐项状态，支持单项 `Download` 与 Run 时的 `Download all and run`。HF gated dataset 通过 `Request`、终端 `Login` 和 `Recheck` 完成授权，插件不接收 token。也可以通过 `EAI_ASSETS_HF_REPO` 指向其他兼容的数据集仓库，通过 `EAI_ASSETS_AUTO_DOWNLOAD=0` 禁用隐式下载。

3. **启动 JSON 环境** — 推荐先运行综合示例；保存后的自定义 env 使用相同加载流程：
  ```bash
  python simulator.py --env robo
  python simulator.py --env=<env_name> --num_envs=1 --device=cuda:0
  python simulator.py --env=nav2 --num_envs=1 --device=cuda:0
  ```

4. **启动 Fire Rescue 固定组合** — 名称形式保持不变，但底层读取 JSON：
  ```bash
  python simulator.py --env=EAI-Factory-v0 --num_envs=1 --device=cuda:0
  ```

5. **策略来源** — `source/EAI_assets/EAI_assets/controller/rl/` 保存仿真运行所需的预训练策略加载配置与权重路径。

## Env DIY 与外部接口示例

Env DIY 是 `simulator.py` 的自定义 env 入口，用于快速组合场景、机器人、传感器和外部控制工具。

下面的演示展示了从环境配置到仿真运行的整体效果；可以先观看完整流程，再在嵌入式工作台中逐步尝试。

```{figure} assets/media/demo.gif
:alt: EAI Simulator 整体运行演示
:class: eai-doc-media
:width: 100%

EAI Simulator 场景、机器人与任务运行演示
```

<div class="env-diy-overview-card">
  <p class="env-diy-overview-card__title">Env DIY 教程</p>
  <iframe class="env-diy-overview-frame" src="env_diy_tutorial.html?embed=1" title="EAI Env DIY 教程"></iframe>
</div>

**可视化方式**:

1. 启动仿真入口但不传 `--env`：
  ```bash
  python simulator.py --num_envs=1 --device=cuda:0
  ```
2. 在提示中选择 `1. 可视化窗口`。
3. 在 Env DIY 窗口中将场景卡拖入画布，再将机器人卡拖到场景中的目标位置。
4. 切换到“Payloads”，在 “Manipulators” 子页选择 UR5/Z1，或在 “Sensors” 子页选择 GS-Hub/LiDAR；再切换到 “Tools” 选择 Camera/Keyboard/ROS。Camera Tool 独立控制 Iris、Pegasus、CF2X 内置单目相机和 GS-Hub 相机的 ROS 图像发布；ROS Tool 控制三种无人机的 LiDAR、IMU、GPS、磁力计和气压计，以及 GS-Hub 的 LiDAR 点云、里程计和 scan 发布。选中机器人后，不兼容、已添加或已有另一种机械臂的卡片会显示为不可拖动状态。
5. 点击 `Complete Selection`，按需保存 env；保存后会写入 `source/EAI_hmrs/EAI_hmrs/envs/<env_name>.json`。
6. 如果保存了 env，之后可直接启动：
  ```bash
  python simulator.py --env=<env_name> --num_envs=1 --device=cuda:0
  ```

轻量窗口中的 `visual.x/y` 是画布布局，不是仿真坐标。需要高度、表面吸附和真实三维 transform 时，使用环境说明中的 Isaac Sim 3D 运行前编辑：

```bash
python simulator.py --diy-3d --device=cuda:0
```

三维入口、资产下载和同一 Kit 进程的运行边界见[环境说明](environments.md)。

```{figure} assets/media/eai_env_diy.gif
:alt: EAI Env DIY 三维编辑与运行演示
:class: eai-doc-media
:width: 100%

EAI Env DIY 三维场景编辑、资产准备与运行流程
```

**终端快速方式**:

1. 启动 `python simulator.py --num_envs=1 --device=cuda:0`。
2. 在提示中选择 `2. 终端快速`。
3. 按步骤选择场景、宿主机器人、UR5/Z1 机械臂、GS-Hub/LiDAR 传感器、camera/keyboard/ros 工具和控制器。Camera Tool 与 ROS Tool 的发布职责和可视化方式相同。
4. 根据提示选择是否保存 env、是否立即运行。

**keyboard 外部接口示例**:

仓库保留 `source/EAI_hmrs/EAI_hmrs/envs/keyboard.json` 作为最小键盘测试环境：

```bash
python simulator.py --env=keyboard --device=cuda:0
```

该环境生成 `carter_1` 并订阅 `/carter_1/cmd_vel`。仿真启动后，在另一个终端运行：

```bash
source /opt/ros/humble/setup.bash && python3 algorithm/keyboard/keyboard.py
```

`algorithm/keyboard/keyboard.py` 会自动发现 `/<robot>/cmd_vel` 话题；也可以显式指定机器人：

```bash
source /opt/ros/humble/setup.bash && python3 algorithm/keyboard/keyboard.py --robot carter_1
```

按键控制：`W/S/A/D` 平移，`R/F` 控制无人机上升/下降，`C/V` 转向，`K` 或空格停止，`Q` 在多个机器人间切换，`Esc` 或 `Ctrl-C` 退出。无人机垂直速度可通过 `--vertical-speed` 设置。该脚本使用 ROS Humble 的 `rclpy`，建议用系统 Python：`python3`。

```{figure} assets/media/eai-keyboard.gif
:alt: 通过键盘控制 EAI 机器人演示
:class: eai-doc-media
:width: 100%

Keyboard 工具通过 ROS2 `cmd_vel` 控制机器人
```

**Nav2 导航示例（Factory + Carter + GS-Hub）**:

仓库保留的 Nav2 示例是 `source/EAI_hmrs/EAI_hmrs/envs/nav2.json`。它选择 Factory 场景和 Carter，并添加 GS-Hub、Camera 与 ROS Tool。Camera Tool 开启 GS-Hub 图像发布；ROS Tool 开启 `/carter_1/cmd_vel` 订阅，以及 GS-Hub 的 LiDAR 点云、里程计和 scan 发布。

终端 1 启动仿真。Nav2 / GS-Hub 相关仿真必须使用 Isaac Sim GUI，不能使用 headless：

```bash
conda activate env_isaaclab
python simulator.py --env=nav2 --num_envs=1 --device=cuda:0
```

终端 2 启动 Nav2 和 RViz：

```bash
source /opt/ros/humble/setup.bash
ros2 launch algorithm/ros/nav2/nav2.launch.py robot_name:=carter_1 robot_type:=Carter scene:=factory rviz:=true
```

终端 3 发送导航目标。目标点需要选在 Factory 地图自由空间内：

```bash
source /opt/ros/humble/setup.bash
/usr/bin/python3 algorithm/ros/nav2/send_goal.py --x -5.0 --y -8.0
```

完成地图、传感器和 ROS 通道配置后，Nav2 会在 Factory 场景中规划并执行移动路径。下图放在完整命令之后，便于对照终端步骤观察最终导航效果。

```{figure} assets/media/eai-nav.gif
:alt: Carter 在 Factory 场景中执行 Nav2 导航演示
:class: eai-doc-media
:width: 100%

Factory + Carter + GS-Hub 的 Nav2 导航效果
```

## 安装与常用命令

- 可编辑安装（确保 Isaac Lab Python 环境可用）：
  ```bash
  pip install -e source/EAI
  pip install -e source/EAI_assets
  pip install -e source/EAI_hmrs
  ```
- 列出 JSON 环境配置：
  ```bash
  find source/EAI_hmrs/EAI_hmrs/envs -maxdepth 1 -name '*.json' -printf '%f\n' | sort
  ```
- 快速安装/卸载全部包（根目录）：
  ```bash
  ./tools/install_packages.sh       # 安装
  ./tools/install_packages.sh -u    # 卸载
  ./tools/install_packages.sh -v    # 详细输出
  ```
- 更新 Env DIY 图片素材（根目录）：
  ```bash
  source/EAI/EAI/hmrs_env/update.sh
  source/EAI/EAI/hmrs_env/update.sh --source-root usd/picture --output-root usd/picture/processed
  ```
  `source/EAI/EAI/hmrs_env/update.sh` 调用 `EAI.hmrs_env.env_diy.update_assets`，检查 `usd/picture/robot/`、`usd/picture/manipulator/`、`usd/picture/sensor/`、`usd/picture/tool/` 下的 PNG 原图；当 `usd/picture/processed/` 中缺少对应文件，或原图比处理后文件更新时，会重新生成透明背景、描边和发光效果的 Env DIY 调色板素材。它不负责更新 Git 代码，也不负责下载 `usd/` 场景/机器人资产或 RL 模型权重。

## 开发约定与注意事项

- `controllers` 字典键名必须与场景资产名一致，顺序决定观测/动作拼接顺序。
- 所有控制器通过环境的 `_pre_physics_step` 接口统一处理，无需手动调用 Dispatcher。
- 预训练策略加载配置和传统控制器放在 `source/EAI_assets/EAI_assets/controller/`。
- 控制器开发请参考[控制器开发指南](controller_guide.md)。
- 资产 USD 本地路径位于 `usd/`（如 `usd/robot/m20/M20.usd`、`usd/robot/go2/go2.usd`）；缺失的 USD 和 RL 模型会从 [Hugging Face 资产仓库](https://huggingface.co/datasets/HuangQIjun/eai-simulator-assets) 按需下载，访问该 gated dataset 前需要先申请权限。
- 文档构建：`cd docs && make html`；本地预览 `cd build/html && python -m http.server 8000`。

## 当前仓库边界

当前仓库聚焦仿真运行、资产配置、控制器加载、Env DIY 和 ROS/Nav2 外部接口。

## 参考页面

- **快速开始**: [开始第一次运行](getting_started.md)
- **安装指南**: [安装与依赖配置](installation.md)
- **环境说明**: [环境配置与使用](environments.md)
- **控制器开发**: [控制器开发指南](controller_guide.md)
- **GS-Hub 传感器**: [GS-Hub 使用说明](gs_hub_sensor.md)
- **下一阶段功能规划**: [查看项目 Roadmap](roadmap.md)
