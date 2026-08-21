# Algorithm

`algorithm/` 放置可由 Simulator 和外部 demo 独立调用的算法模块。

| 路径 | 说明 |
|---|---|
| `emos/` | 多智能体 LLM 讨论、任务解析与分配 |
| `TeamWeaver/` | LLM + MIQP 任务分解、分配与重规划 |
| `global_planner/` | 2D 占据栅格规划、路径跟踪与 `GlobalNavSession` |
| `multi_robot_navigation/` | db-CBS 原生规划内核、同步轨迹运行时与 EAI `SimulatorSession` 多机导航组件（无需 launch） |
| `nav2/` | 独立于 Isaac Python 环境运行的外部 ROS2/Nav2 配置、launch 与目标客户端 |
| `keyboard/keyboard.py` | 向 EAI `cmd_vel` 接口发布 ROS2 Twist 的交互式键盘客户端 |

通用 ROS 运维客户端位于根目录 `tools/vis_sensors.py`、`tools/send_cmd_vel.py` 和
`tools/send_manipulator_command.py`。它们分别负责传感器查看、测试速度发布以及向
UR5/Z1 正式 topic 发布命令并可选等待状态；它们是系统 ROS Python 工具，不是机械臂
控制算法，也不会执行 IK、创建 OmniGraph 或激活机械臂 graph。

## 独立导入

`algorithm/`、`algorithm/emos/` 和 `algorithm/global_planner/` 使用 Python
namespace package。`algorithm/multi_robot_navigation/` 通过 `__init__.py` 导出
db-CBS 规划内核，EAI 适配器由 `eai_plugin.py` 导出。调用方可按下面方式导入：

```python
from algorithm.emos.engine import EMOSDiscussionManager
from algorithm.emos.types import scenario_from_dict
from algorithm.global_planner.session import GlobalNavSession
from algorithm.multi_robot_navigation import DbcbsNavigationSession
from algorithm.multi_robot_navigation.eai_plugin import EaiMultiRobotNavigationPlugin
```

从仓库根目录启动时无需额外软链接：

```bash
cd eai-simulator
python -m demo.fire_rescue.main --env=EAI-Factory-v0 --device=cuda:0
```

Fire Rescue 通过 `demo/fire_rescue/algorithm_paths.py` 保证优先使用本仓库的 `algorithm/`，避免加载
外部 checkout 中的另一份实现。

## 依赖边界

- `emos/` 不依赖 Isaac Sim 场景实现，仿真信息通过场景规格和机器人状态传入。
- `global_planner/` 不依赖 EMOS；它只处理地图、规划、跟踪和速度命令。
- `multi_robot_navigation/` 内含 db-CBS、定制 OMPL、dynoplan/dynobench、运动基元和 EAI 适配层，并保留显式 `planner_backend="global"` 兼容模式。
- `TeamWeaver/` 是外部任务规划器，不构造仿真场景，也不直接驱动机器人。
- `nav2/` 与 `keyboard/keyboard.py` 是外部 ROS 集成边界；导入 `rclpy` 的进程使用所选系统 ROS Python，不与 `env_isaaclab` 混用。
- `demo/fire_rescue/runtime/algorithm_adapter.py` 只保留 Fire Rescue 的兼容类名。
- 各算法的安装和构建说明见各自目录中的 `README.md`；独立 Python 依赖文件仅在需要时提供。
