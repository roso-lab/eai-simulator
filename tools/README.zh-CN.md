# 仓库工具

[English](README.md)

本目录中的命令均从仓库根目录运行。`tools/` 汇总彼此独立的运维、验证、ROS2 和 Human 资产创作入口；它不是一个统一 Python 包，也不提供统一 API。运行前应先阅读所属目录说明和命令帮助。

## 目录导航

| 目录 | 职责 | 运行环境边界 |
| --- | --- | --- |
| [`setup/`](setup/README.zh-CN.md) | editable package 安装、ROS 发行版选择和主机 inotify 限制 | Bash、`pip` 和可选主机管理权限 |
| [`validation/`](validation/README.zh-CN.md) | 轻量仓库一致性与回归检查 | 仓库 Python 或 Node.js |
| [`ros2/`](ros2/README.zh-CN.md) | 外部传感器、移动底盘和机械臂客户端及其聚焦测试 | 选定的系统 ROS2 Python |
| [`human_assets/`](human_assets/README.zh-CN.md) | Human 转换、创作、迁移、cache 生成、验证和演示 | 依命令使用纯 Python 或 Isaac Sim/OpenUSD |

## 运行环境边界

- `setup/` 可能安装系统包、editable Python 包和持久主机配置，运行前必须检查副作用。
- `validation/` 下的 Python 检查是轻量检查；Env DIY runtime 检查需要 Node.js 20 LTS 或更新 LTS。
- `ros2/` 下导入 `rclpy` 的程序必须使用选定的系统 ROS2 Python，不能使用 `env_isaaclab` 解释器。
- Human 资产命令的要求不同。计划、JSON 动作创作、迁移和结构验证可以使用纯 Python；转换、导入、cache 生成和 runtime demo 可能需要 Isaac Sim 或 `pxr`。

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

## 副作用

- `setup/install_packages.sh` 会安装 `pywebview[qt]`、可能通过 `sudo` 调用 `apt-get`、安装或卸载 editable package，并在当前 Python prefix 下保存 ROS 发行版。
- `setup/configure_inotify_limits.sh` 不带 `--dry-run` 时会写入 `/etc/sysctl.d/90-eai-isaac-sim-inotify.conf` 并重新加载实时内核限制。
- `ros2/send_cmd_vel.py` 会向真实运行中的机器人发布指令。退出时会尝试发送零速度，但 simulator bridge 没有过期指令 watchdog；必须观察机器人并确认已经停止。
- `ros2/send_manipulator_command.py` 会发布真实机械臂命令，运行前应确认机器人实例、模型、目标和周边空间。
- `human_assets/` 下的命令可能写入创作、转换、迁移或 cache 资产及元数据；存在 plan 或 dry-run 模式时应优先使用。

## 轻量验证

以下检查不会有意启动 Isaac Sim、连接实时 ROS graph 或修改仓库源文件：

```bash
bash -n tools/setup/install_packages.sh tools/setup/configure_inotify_limits.sh tools/setup/ros_distro.sh
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest --rootdir="$PWD" -q -p no:cacheprovider \
  tools/ros2/tests/test_vis_sensors.py \
  tools/ros2/tests/test_send_cmd_vel.py \
  tools/ros2/tests/test_send_manipulator_command.py
python tools/validation/check_asset_download_errors.py
python tools/validation/check_documentation_consistency.py
python tools/validation/check_env_diy_exclusivity.py
python tools/validation/check_release_links.py
python tools/validation/check_ros_distro_config.py
node tools/validation/check_env_diy_runtime.mjs all
```

ROS2 测试使用 mock 检查客户端生命周期和纯辅助逻辑，不能替代真实 ROS2 与 simulator 集成验证。
