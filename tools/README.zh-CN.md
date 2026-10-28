# 仓库工具

[English](README.md)

本目录中的命令均从仓库根目录运行。`tools/` 汇总彼此独立的运维、验证、ROS2 和 Human 资产创作入口；它不是一个统一 Python 包，也不提供统一 API。运行前应先阅读所属目录说明和命令帮助。

## 目录导航

| 目录 | 职责 | 运行环境边界 |
| --- | --- | --- |
| [`setup/`](setup/README.zh-CN.md) | editable package 安装、ROS 发行版选择和主机 inotify 限制 | Bash、`pip` 和可选主机管理权限 |
| [`validation/`](validation/README.zh-CN.md) | 仓库检查及按需执行的抓取物理实验 | Python 或 Node.js；物理实验需要 Isaac Lab |
| [`ros2/`](ros2/README.zh-CN.md) | 外部传感器、移动底盘和机械臂客户端及其聚焦测试 | 选定的系统 ROS2 Python |
| [`human_assets/`](human_assets/README.zh-CN.md) | Human 转换、创作、迁移、cache 生成、验证和演示 | 依命令使用纯 Python 或 Isaac Sim/OpenUSD |
| [`physics_replay/`](physics_replay/README.zh-CN.md) | Isaac Sim 物理状态、command/action/noise tape、checkpoint 恢复和 episode 检查 | 纯 Python 检查；Factory 验证需要 `env_isaaclab` |
| [`topology_authoring/`](topology_authoring/README.zh-CN.md) | 基于场景适配器的通用拓扑草稿和 Isaac Sim 创作界面 | 纯 Python 模型或 Isaac Sim GUI；由适配器决定场景输入 |

根目录脚本 [`semantic_map_traversability.py`](semantic_map_traversability.py)
使用机器人目录中的机器人审计已创作的语义导航网络。`--check` 路径为纯
Python；物理执行需要 `env_isaaclab`、Isaac Sim 渲染和所选机器人/控制器资产。

## 运行环境边界

- `setup/` 可能安装系统包、editable Python 包和持久主机配置，运行前必须检查副作用。
- `validation/` 大部分检查为轻量检查；[抓取与移动机械臂实验](validation/manipulation_contact.md)会明确启动 Isaac Sim。Env DIY runtime 检查需要 Node.js 20 LTS 或更新 LTS。
- `ros2/` 下导入 `rclpy` 的程序必须使用选定的系统 ROS2 Python，不能使用 `env_isaaclab` 解释器。
- Human 资产命令的要求不同。计划、JSON 动作创作、迁移和结构验证可以使用纯 Python；转换、导入、cache 生成和 runtime demo 可能需要 Isaac Sim 或 `pxr`。
- `semantic_map_traversability.py --check` 不启动 Isaac Sim，只验证输入并冻结测试计划。只能在具备可用 GPU 渲染的 `env_isaaclab` shell 中去掉 `--check`；物理测试始终 headless 运行，但会进行离屏录像。

## 常用入口

### 环境设置

```bash
./tools/setup/install_packages.sh --help
./tools/setup/configure_inotify_limits.sh --dry-run
source tools/setup/ros_distro.sh
```

### 轻量验证

```bash
python tools/validation/check_asset_download_errors.py
python tools/validation/check_documentation_consistency.py
python tools/validation/check_env_diy_exclusivity.py
node tools/validation/check_env_diy_runtime.mjs all
python tools/validation/check_release_links.py
python tools/validation/check_ros_distro_config.py
```

### ROS2 客户端

在独立 shell 中加载选定的系统 ROS2 环境：

```bash
source /opt/ros/humble/setup.bash
/usr/bin/python3 tools/ros2/vis_sensors.py --help
/usr/bin/python3 tools/ros2/send_cmd_vel.py --help
/usr/bin/python3 tools/ros2/send_manipulator_command.py --help
```

### Human 资产

```bash
python tools/human_assets/validate_assets.py --help
python tools/human_assets/edit_action.py --help
python tools/human_assets/convert_gltf_assets.py --help
python tools/human_assets/migrate_assets.py --help
```

`scene.py` 和 `motion_controls.py` 是被其他脚本导入的辅助模块，不是独立命令。运行会写 USD、manifest、cache 或报告的命令前，请先阅读 Human 资产指南。

### 拓扑创作

```bash
python tools/topology_authoring/author.py \
  --scene-key warehouse \
  --adapter tools.topology_authoring.adapters.empty:EmptyTopologyAdapter \
  --asset-root /path/to/scene-assets \
  --output-root /path/to/prepared-package
```

加 `--headless` 可在不启动 Isaac Sim 时检查适配器。

### 语义地图可通行性

机器人和场景是必填的精确目录/项目 key。工具为轮式机器人和机器狗选择
`ground` XY 网络，为无人机选择 `aerial` XYZ 网络；所选网络不存在时不会回退到
另一类网络。路线选择和推进只调用 `algorithm.skills.navigation`。

不启动 Isaac Sim 检查标准测试计划：

```bash
python tools/semantic_map_traversability.py \
  --robot go2 --scene living_home_02 --check
```

标准物理测试会让支持原地旋转的地面机器人在每个节点旋转，通过所有允许边，
并抽取最多十组可达长路线。空中测试不生成节点旋转；不支持原地旋转的地面控制器
会将该部分标记为不支持。隔离子网和不可达的单向点对会写入报告，但不会被伪造为路线用例。

使用大小写精确的 `--StressT` 进行人工指定压力测试：

```bash
python tools/semantic_map_traversability.py \
  --robot go2 --scene living_home_02 \
  --StressT node:0001 node:0002,node:0004 \
  --check

conda run --no-capture-output -n env_isaaclab \
  python tools/semantic_map_traversability.py \
  --robot go2 --scene living_home_02 \
  --StressT node:0001 node:0002,node:0004
```

每个单节点条目连续原地旋转三圈。节点对必须双向可达，并按
`A -> B -> A -> B -> A` 执行；仅首段重置机器人。条目按输入顺序保留，重复条目也会成为
独立批次。压力模式不执行全图边扫描和随机路线抽样。`--StressT` 与 `--check` 组合时，
只验证完整节点 ID 和双向可达性，不启动 Isaac Sim。

每次调用都按北京时间创建
`~/tmp/<YYYYMMDD_HHMMSS>_<robot>_<scene>/`（也可用 `--output-root` 替换根目录），并写入
`report.md`、`report.json`、`test-plan.json`、`test-results.json`、逐项轨迹和保留的 MP4 证据。
压力测试保留所有已执行视频；标准模式中成功的节点/边用例可删除临时视频。视频默认为
H.264、1280x720、15 FPS。`--seed` 控制标准模式的长路线抽样，`--device` 选择仿真设备，
`--asset-root` 覆盖场景资产发现根目录。退出码 `0` 表示完整通过，`1` 表示测试失败或部分完成，
`2` 表示输入、仿真或测量基础设施错误。

## 副作用

- `setup/install_packages.sh` 会安装 `pywebview[qt]`、可能通过 `sudo` 调用 `apt-get`、安装或卸载 editable package，并在当前 Python prefix 下保存 ROS 发行版。
- `setup/configure_inotify_limits.sh` 不带 `--dry-run` 时会写入 `/etc/sysctl.d/90-eai-isaac-sim-inotify.conf` 并重新加载实时内核限制。
- `ros2/send_cmd_vel.py` 会向真实运行中的机器人发布指令。退出时会尝试发送零速度，但 simulator bridge 没有过期指令 watchdog；必须观察机器人并确认已经停止。
- `ros2/send_manipulator_command.py` 会发布真实机械臂命令，运行前应确认机器人实例、模型、目标和周边空间。
- `human_assets/` 下的命令可能写入创作、转换、迁移或 cache 资产及元数据；存在 plan 或 dry-run 模式时应优先使用。
- `semantic_map_traversability.py` 每次调用都会创建新报告目录。物理模式会启动一个 headless Isaac Sim 会话、录制离屏 H.264 视频，并可能消耗较多 GPU 时间和磁盘空间。命令会在执行前后校验场景和语义地图输入哈希，任何输入变更都会被视为基础设施错误。

## 轻量验证

以下检查不会有意启动 Isaac Sim、连接实时 ROS graph 或修改仓库源文件：

```bash
bash -n tools/setup/install_packages.sh tools/setup/configure_inotify_limits.sh tools/setup/ros_distro.sh
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest --rootdir="$PWD" -q -p no:cacheprovider \
  tools/ros2/tests/test_vis_sensors.py \
  tools/ros2/tests/test_send_cmd_vel.py \
  tools/ros2/tests/test_send_manipulator_command.py \
  tools/tests/test_semantic_map_traversability.py
python tools/validation/check_asset_download_errors.py
python tools/validation/check_documentation_consistency.py
python tools/validation/check_env_diy_exclusivity.py
python tools/validation/check_release_links.py
python tools/validation/check_ros_distro_config.py
node tools/validation/check_env_diy_runtime.mjs all
```

ROS2 测试使用 mock 检查客户端生命周期和纯辅助逻辑，不能替代真实 ROS2 与 simulator 集成验证。
