# 环境设置工具

[English](README.md)

这些脚本均从仓库根目录运行，用于安装仓库 package、保存 Isaac ROS bridge 发行版选择，或调整主机 inotify 限制。在共享机器上运行前必须检查其副作用。

## 命令

| 脚本 | 用途 | 重要副作用 |
| --- | --- | --- |
| `install_packages.sh` | 安装或卸载三个仓库 Python package，并选择 Humble 或 Jazzy | 可能通过 `apt-get` 安装 `libxcb-cursor0`；调用裸 `pip`；在当前 Python prefix 下写入 `share/eai-simulator/ros_distro` |
| `configure_inotify_limits.sh` | 提高 Isaac Sim 和大型 workspace 所需的 Linux inotify 限制 | 不带 `--dry-run` 时写入 `/etc/sysctl.d/90-eai-isaac-sim-inotify.conf` 并调用 `sysctl --system` |
| `ros_distro.sh` | 提供验证、解析、读取和写入 ROS 发行版选择的共享函数 | 应由其他 Shell 脚本 source，不是独立安装器 |

## Package 安装器

安装前激活目标 Isaac Lab 环境，并确认裸 `pip` 与 `python -m pip` 指向同一环境：

```bash
command -v python
command -v pip
pip --version
python -m pip --version
./tools/setup/install_packages.sh --help
./tools/setup/install_packages.sh --ros-distro humble
```

只有在 Jazzy bridge/runtime 环境已经准备好时才使用 `--ros-distro jazzy`。该选项只选择 bridge backend，不安装系统 ROS2。`-u` 卸载仓库 package，`-v` 输出详细 package 日志。某个 package 操作失败后脚本会继续处理其他 package，最后只要有任一操作失败就返回失败状态。

## Inotify 限制

先检查将要生成的配置：

```bash
./tools/setup/configure_inotify_limits.sh --dry-run
```

正式应用需要 root 或免密 `sudo`：

```bash
./tools/setup/configure_inotify_limits.sh
```

脚本会验证当前值、原子写入配置、重新加载内核设置，并在应用失败时尝试恢复旧文件。`EAI_INOTIFY_PROC_ROOT` 是仅供 `--dry-run` 使用的测试覆盖。

## ROS 发行版辅助函数

`ros_distro.sh` 只接受 `humble` 或 `jazzy`。调用方依次采用显式选项、`ROS_DISTRO`、已保存选择和 Humble 默认值。保存值会原子写入当前 Python prefix。

```bash
source tools/setup/ros_distro.sh
resolve_ros_distro "${ROS_DISTRO:-}"
```

## 安全检查

```bash
bash -n tools/setup/install_packages.sh tools/setup/configure_inotify_limits.sh tools/setup/ros_distro.sh
./tools/setup/configure_inotify_limits.sh --dry-run
python tools/validation/check_ros_distro_config.py
```
