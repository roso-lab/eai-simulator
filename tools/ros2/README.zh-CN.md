# ROS2 运维客户端

[English](README.md)

这些程序是外部 ROS2 客户端，用于查看传感器或向正在运行的 EAI Simulator 场景发送命令。它们不会启动 Isaac Sim、创建机器人，也不会启用 ROS publisher/subscriber。应先启动带所需接口的 simulator，再在独立的系统 ROS shell 中运行这些工具。

## 运行环境

Ubuntu 22.04 上的 Humble 是已验证基线。只有选定的系统 ROS2 环境已经安装并与 simulator bridge 匹配时，才替换下面的 `humble`。必须使用系统 ROS Python，不能使用 `env_isaaclab`：

```bash
source /opt/ros/humble/setup.bash
command -v python3
python3 -c "import rclpy; print(rclpy.__file__)"
```

`vis_sensors.py` 还需要 NumPy、OpenCV、`cv_bridge`、`sensor_msgs` 和 `point_cloud2`。命令客户端需要其导入的标准 ROS2 message package。应通过选定的 ROS 发行版或其受管理 Python 环境安装这些依赖。

## 客户端

### 传感器可视化

`vis_sensors.py` 显示相机/深度图像和俯视点云。`auto` 自动发现全部 `sensor_msgs/msg/Image` topic；`camera` 在 namespace 下发现图像；`orsus`、`realsense` 和 `lidar` 使用各自预期 topic。未显式传入 `--namespace` 时，特定传感器模式使用旧版 `/isaac` 默认 namespace。

```bash
/usr/bin/python3 tools/ros2/vis_sensors.py --help
/usr/bin/python3 tools/ros2/vis_sensors.py --sensor camera --namespace /iris_1
/usr/bin/python3 tools/ros2/vis_sensors.py --sensor realsense --namespace /mushr_1
```

非 8-bit 图像会按有限数值缩放显示，`NaN` 和无穷像素显示为黑色。RealSense IMU 只打印 topic，不做可视化。

### 移动底盘速度

`send_cmd_vel.py` 向 `/<robot>/cmd_vel` 发布 `geometry_msgs/msg/Twist`。`--linear` 是 m/s 单位的 `linear.x`，`--angular` 是 rad/s 单位的 `angular.z`，`--rate 0` 只发布一次；正 rate 会持续发布直到 Ctrl+C。

```bash
/usr/bin/python3 tools/ros2/send_cmd_vel.py --help
/usr/bin/python3 tools/ros2/send_cmd_vel.py --robot carter_1 --linear 0.2 --angular 0.0 --rate 10
```

退出时客户端会尝试多次发送零速度并等待交付。Simulator bridge 没有过期命令 watchdog，因此进程退出本身不能证明机器人已停止；必须观察机器人并确认零速度已经送达。

### 机械臂命令

`send_manipulator_command.py` 发送原生 UR5 或 Z1 命令。必须且只能选择一种目标：六个关节位置、三个值的笛卡尔目标，或 Z1 gripper 值。`--wait` 会检查状态反馈，直到达到容差或超时。笛卡尔 wait 模式要求 `world` frame，因为反馈位姿使用世界坐标。

```bash
/usr/bin/python3 tools/ros2/send_manipulator_command.py --help
/usr/bin/python3 tools/ros2/send_manipulator_command.py \
  --robot m20_1 --model ur5 \
  --joint 0.0 -1.2 1.5 -1.8 -1.57 0.0 \
  --wait
```

UR5 不提供 Z1 gripper 命令。发布任何命令前必须确认机器人/模型组合和周边物理空间。

## 接口发现

这些工具只能使用当前场景实际启用的接口。可通过仓库 interface catalog 和 runtime snapshot 检查声明及实时状态：

```bash
python simulator.py interfaces list --json
python simulator.py interfaces scene --env keyboard --json
python simulator.py interfaces status --probe
```

接口有声明不代表 runtime publisher/subscriber 已启动；当前 session 的 runtime snapshot 和 ROS graph 才是有效依据。

## 聚焦测试

现有测试应保留，因为它们无需实时 ROS graph 即可保护参数验证、topic 构造、图像/深度转换、反馈匹配、Ctrl+C 处理和节点/publisher 清理：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest --rootdir="$PWD" -q -p no:cacheprovider \
  tools/ros2/tests/test_vis_sensors.py \
  tools/ros2/tests/test_send_cmd_vel.py \
  tools/ros2/tests/test_send_manipulator_command.py
```

这些单元测试使用 mock，不验证 DDS 发现、simulator bridge 启动、真实 topic 数据、机器人运动或硬件安全。修改集成契约时还必须单独执行实时验证。
