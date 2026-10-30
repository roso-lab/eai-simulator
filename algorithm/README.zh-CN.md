# 算法模块

本目录存放可由 Simulator 或外部 demo 调用的可复用规划器和 ROS 集成客户端。各模块有独立的运行边界；依赖和启动方式请查看对应模块的 README。

## 模块目录

| 路径 | 作用 |
|---|---|
| algorithm/emos/ | 场景驱动的多智能体 LLM 讨论和子任务分配。 |
| algorithm/TeamWeaver/ | LLM 任务分解、MIQP/匈牙利分配、阶段调度和重规划。 |
| algorithm/global_planner/ | 独立的二维占据栅格规划、路径跟踪和速度命令生成。 |
| algorithm/multi_robot_navigation/ | 原生 db-CBS、同步轨迹和 EAI Simulator 会话适配器。 |
| algorithm/nav2/ | 外部 ROS2/Nav2 配置、TF 补全、点云转换和目标客户端。 |
| algorithm/keyboard/ | 面向 EAI cmd_vel 接口的交互式 ROS2 键盘客户端。 |

## 导入边界

从仓库根目录运行时，namespace package 模块可以直接导入。多机导航通过 __init__.py 导出公开 session，EAI 适配层位于 eai_plugin.py。

~~~python
from algorithm.emos.engine import EMOSDiscussionManager
from algorithm.emos.types import scenario_from_dict
from algorithm.global_planner.session import GlobalNavSession
from algorithm.multi_robot_navigation import DbcbsNavigationSession
from algorithm.multi_robot_navigation.eai_plugin import EaiMultiRobotNavigationPlugin
~~~

从仓库根目录启动 demo 时不需要第二份 checkout：

~~~bash
cd eai-simulator
python -m demo.fire_rescue.main --env=EAI-Factory-v0 --device=cuda:0
~~~

Fire Rescue 通过 demo/fire_rescue/algorithm_paths.py 选择本仓库的 algorithm/。适配层只连接仿真状态和规划器，不会替换规划器实现。

## 运行边界

- EMOS 接收场景、机器人规格和可选环境状态；不构建 Isaac 场景，也不执行动作。
- TeamWeaver 接收自然语言任务和符号化世界状态；输出任务 DAG 与分配结果，不输出仿真控制命令。
- global_planner 独立于 Isaac Sim、EMOS、Torch 和 ROS。
- multi_robot_navigation 管理规划 worker 和动作生成；调用方管理 Isaac 应用、仿真循环和清理。
- Nav2 与 keyboard 使用所选系统 ROS 的 Python，不使用 env_isaaclab 的 Python。不要混用导入 rclpy 的进程和 Isaac Lab Conda 解释器。
- tools/ros2/ 存放 ROS 运维客户端，不是额外的控制算法，也不负责机械臂 graph。

## 场景资源

EAI 场景的 provider 伴随资源统一声明在 `EAI_assets.scene_resources`。已运行在 EAI package 环境内的代码可以调用 `ensure_scene_resource(scene, resource)`；不能导入 Isaac Lab 环境的 system-ROS 等外部算法使用仓库快速路径：

~~~bash
python simulator.py assets list --format json
python simulator.py assets ensure --scene warehouse --resource occupancy_map --format json
~~~

两种方式都委托同一个 EAI asset resolver，并遵循 `EAI_USD_ROOT`、`EAI_ASSETS_HF_REPO`、`EAI_ASSETS_HF_REVISION` 和 `EAI_ASSETS_AUTO_DOWNLOAD`。算法不得自行拼接 Hugging Face 路径或实现下载器；用户显式传入的自定义地图不属于 provider 合同。

## 文档

每个模块 README 都说明导入路径、依赖、启动命令和限制。Provider 资产和模型权重可以通过统一 resolver 按需下载；ROS 安装和 Isaac Sim 仍属于运行时前置条件。
