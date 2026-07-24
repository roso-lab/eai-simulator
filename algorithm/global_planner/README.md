# Global Planner

`algorithm/global_planner/` 是 EAI Simulator 的独立二维路径规划包，负责占据栅格规划、路径后处理、路径跟踪和速度命令生成。它不依赖 Isaac Sim、EMOS、Torch 或 ROS，可在离线 Python 环境中单独使用。

## 模块结构

| 文件 | 作用 |
|---|---|
| `core.py` | `FactoryMapPlanner`、A* / RRT、障碍膨胀、目标邻域搜索和路径后处理 |
| `tracking.py` | 机器人导航 profile、路径跟踪和速度平滑 |
| `session.py` | `GlobalNavSession`，统一封装规划、跟踪和每机器人状态 |
| `csrc/` | 可选 C++ 规划加速实现 |
| `build_cpp.sh` | 构建本地 `_planner_cpp` 扩展 |

## 在 EAI 仓库中导入

从仓库根目录运行时，按 namespace package 导入：

```python
from algorithm.global_planner.core import FactoryMapPlanner
from algorithm.global_planner.session import GlobalNavSession
```

Fire Rescue 的适配入口是：

```text
demo/fire_rescue/runtime/algorithm_adapter.py
```

该适配层只负责将 EAI 的机器人位姿和控制张量转换为规划器输入输出；规划算法本身保持独立。

## 最小示例

```python
from algorithm.global_planner.session import GlobalNavSession

session = GlobalNavSession(
    map_yaml="demo/fire_rescue/assets/factory_map.yaml",
)
session.register_agent("m20_1", use_goal_position=False)

planned = session.plan_to_goal(
    robot_name="m20_1",
    start_xy=(-3.0, 5.0),
    goal_xy=(-4.5, -4.0),
)
assert planned
```

火源或其他目标可能位于障碍物内部。此时调用方应使用规划器提供的目标邻域搜索，选择指定半径内的可达位置，而不是强制规划到障碍物中心。Fire Rescue 当前使用火源约 `3 m` 邻域目标。

## 可选 C++ 加速

```bash
cd eai-simulator/algorithm/global_planner
bash build_cpp.sh
```

生成的 `_planner_cpp*.so` 是本机构建产物，不提交 Git。没有扩展时自动使用 Python 实现。

## 依赖

```bash
pip install -r algorithm/global_planner/requirements.txt
```

该目录不需要 `__init__.py`；从 EAI 仓库根目录启动即可使用 `algorithm.global_planner.*` 导入路径。
