# 快速开始

本文档介绍 EAI Simulator 当前的 JSON 环境工作流。工程当前以 Isaac Sim 5.1、Isaac Lab 2.x 和 `env_isaaclab` conda 环境为基准。

## 前置要求

- Isaac Sim 5.1 与 Isaac Lab 2.x 已安装。
- 使用包含 Isaac Lab 依赖的 `env_isaaclab` conda 环境。
- CPU 和 CUDA GPU 均可运行。

## 安装

克隆仓库并进入仓库根目录后运行：

```bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate env_isaaclab

pip install -e source/EAI
pip install -e source/EAI_assets
pip install -e source/EAI_hmrs
```

也可使用：

```bash
./tools/setup/install_packages.sh
```

## 环境文件

每个可启动环境对应一个 JSON：

```text
source/EAI_hmrs/EAI_hmrs/envs/<env_name>.json
```

查看可用环境：

```bash
find source/EAI_hmrs/EAI_hmrs/envs -maxdepth 1 -name '*.json' -printf '%f\n' | sort
```

## 选择运行方式

| 命令 | 界面 | 适用场景 | 位姿来源 |
|---|---|---|---|
| `python simulator.py` | Env DIY 启动菜单（启动后选择 `1`/`2`/`3`） | 选择轻量窗口、终端快速或 3D 编辑器 | 取决于所选模式 |
| `python simulator.py --diy-3d` | Isaac Sim 右侧 Env DIY 3D 插件 | 需要真实碰撞面、高度和三维拖动 | 真实 `spawn_pose` |
| `python simulator.py --env=<name>` | 直接进入正式仿真 | 启动已保存的 JSON 环境 | JSON 中的 `spawn_pose` 或默认位置 |

未指定 `--env` 时，可在启动菜单中选择 `3` 进入 3D 编辑器；`--diy-3d` 仍可作为直接入口。运行前编辑完成后点击插件中的 `Run`，程序会在同一个 Isaac Sim/Kit 进程中从预览 Stage 切换到正式环境，不会关闭再打开 Isaac Sim。

> **当前状态**：`--diy-3d` 仍处于持续优化阶段。建议用于开发、资产验证和流程联调；插件布局、资产目录和部分控制器接口可能随版本调整，正式实验请保留导出的 selection JSON。

## 启动已有环境

```bash
python simulator.py --env robo
```

这是工程推荐的快速开始命令。`--env robo` 会读取：

```text
source/EAI_hmrs/EAI_hmrs/envs/robo.json
```

`robo` 环境在平面场景中加载轮式、足式、人形及无人机等异构机器人，并为每个对象启用键盘控制；registry 人类资产通过 `python -u tools/human_assets/run_demo.py` 单独运行，完整能力见[人类资产开发](human_assets.md)。

## Env DIY

不传 `--env` 即进入 Env DIY：

```bash
python simulator.py --num_envs=1 --device=cuda:0
```

可选择：

- 可视化窗口：按 `Scenes → Robots → Payloads → Tools` 拖拽场景、宿主机器人、机械臂/传感器和控制工具。
- 终端快速：按相同顺序选择场景、宿主机器人、Manipulators、Sensors 和 Tools。

保存后的环境直接写入 `source/EAI_hmrs/EAI_hmrs/envs/`，之后通过保存名称再次启动。

需要按仿真世界中的真实三维位置编辑时，使用 Isaac Sim 3D 模式：

```bash
python simulator.py --diy-3d --device=cuda:0
```

该模式在正式仿真运行前打开 Isaac Sim 三维编辑器，用于保存真实的机器人位置。详细的三维编辑、资产准备和失败恢复流程见[环境说明](environments.md)。

`--diy-3d` 只启动一次 Isaac Sim AppLauncher。点击 Run 后，程序在同一个 Kit 进程中完成正式环境切换；资产下载、机械臂控制和故障排查见[环境说明](environments.md)与[机械臂](ur5_control.md)。

### 外部键盘控制

仓库保留了最小键盘环境 `source/EAI_hmrs/EAI_hmrs/envs/keyboard.json`。先启动：

```bash
python simulator.py --env=keyboard --device=cuda:0
```

该环境生成 `carter_1` 并启用 `/carter_1/cmd_vel`。另一个终端运行：

```bash
source /opt/ros/humble/setup.bash
python3 algorithm/keyboard/keyboard.py --robot carter_1
```

其他临时环境仍可通过 Env DIY 添加 `keyboard` tool，实例名按类型和顺序生成。

MuSHR Nano v2 的键盘环境为 `mushr_v2_keyboard`，使用 MuSHR 自身的 Ackermann 控制器和 `0.15 m/s` 纯角速度最小前进速度：

```bash
python simulator.py --env=mushr_v2_keyboard --device=cuda:0
source /opt/ros/humble/setup.bash
/usr/bin/python3 algorithm/keyboard/keyboard.py --robot mushr_v2_1 --linear-speed 0.5 --angular-speed 0.8
```

`W/S` 控制 `linear.x`，`A/D` 控制 `linear.y`，`R/F` 控制无人机上升/下降的 `linear.z`，`C/V` 控制 `angular.z`；MuSHR 不使用 `linear.y`，也不能原地旋转。可通过 `--vertical-speed` 单独设置无人机垂直速度。

`robo` 环境中的所有对象也已配置 `keyboard` tool。仿真启动后，可在另一个终端运行统一键盘发布器：

```bash
/usr/bin/python3 algorithm/keyboard/keyboard.py
```

## Nav2 示例

仓库包含 Factory + Carter 的 Nav2 JSON 示例：

```bash
# 终端 1：Isaac Sim GUI
conda activate env_isaaclab
python simulator.py --env=nav2 --device=cuda:0
```

```bash
# 终端 2：Nav2 与 RViz
source /opt/ros/humble/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
ros2 launch algorithm/nav2/nav2.launch.py \
  robot_name:=carter_1 robot_type:=Carter scene:=factory rviz:=true
```

```bash
# 终端 3：发送目标
source /opt/ros/humble/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
/usr/bin/python3 algorithm/nav2/send_goal.py --x -5.0 --y -8.0
```

该命令仅在 Nav2 返回 `STATUS_SUCCEEDED` 时以状态码 0 退出；服务未就绪、目标被拒绝、取消、中止或超时时会返回非零状态码。`send_goal.py` 终端中的 Ctrl+C 只停止目标客户端；要关闭一键启动的 Nav2 和 Isaac Sim，请在 `run_nav2.sh` 所在终端按 Ctrl+C，并等待清理完成。

## Fire Rescue Demo

纯机器人火灾救援实验使用同一个 Simulator 接口和 `EAI-Factory-v0.json`：

```bash
python -m demo.fire_rescue.main \
  --env=EAI-Factory-v0 \
  --device=cuda:0 \
  --trials=1 \
  --trial-hazard-ids=1 \
  --auto-fire-delay=0 \
  --emos-llm-preset=zhipu-glm4-flash
```

非 headless 启动后，终端会提示监控页面：

```text
http://127.0.0.1:8767/
```

完整结构与算法调用方式见 `demo/fire_rescue/README.md`。

## 验证

先检查 CLI，无需启动 Isaac Sim：

```bash
python simulator.py --help
python -m demo.fire_rescue.main --help
```

再启动一个 JSON 环境进行真实验证：

```bash
python simulator.py --env robo
```

## 常见问题

### `ModuleNotFoundError`

重新执行三个可编辑安装命令，并确认当前终端已激活 `env_isaaclab`。

### 找不到环境 JSON

确认文件存在且 `--env` 不包含 `.json`：

```bash
ls source/EAI_hmrs/EAI_hmrs/envs/EAI-Factory-v0.json
```

### CUDA 内存不足

保持 `--num_envs=1`，关闭其他占用 GPU 的进程。

### ROS2 话题未发布

确认 JSON 中机器人包含 `navigation_io` 或 `keyboard` 附件，并检查 Isaac Sim ROS2 Bridge 与 ROS Humble 环境变量。

## 下一步

- :doc:`环境说明 <environments>`
- :doc:`项目概览 <project_overview>`
- :doc:`控制器开发 <controller_guide>`
- :doc:`人类资产开发 <human_assets>`
