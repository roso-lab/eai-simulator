# 全局路径规划器

algorithm/global_planner/ 是独立的二维占据栅格规划包，负责地图加载、A*/RRT 规划、障碍膨胀、目标邻域搜索、路径后处理、路径跟踪和速度命令生成。它不依赖 Isaac Sim、EMOS、Torch 或 ROS，可以在离线 Python 环境中使用。

## 模块

| 文件 | 作用 |
|---|---|
| core.py | FactoryMapPlanner、A*/RRT、障碍膨胀和目标邻域搜索。 |
| tracking.py | 机器人 profile、路径跟踪和速度平滑。 |
| session.py | GlobalNavSession，封装规划、跟踪和每个 agent 的状态。 |
| csrc/ 和 build_cpp.sh | 可选的 _planner_cpp 加速扩展。 |

## 导入和最小示例

~~~python
import os
from pathlib import Path

from algorithm.global_planner.session import GlobalNavSession

usd_root = Path(os.environ.get("EAI_USD_ROOT", "usd"))
session = GlobalNavSession(
    map_yaml=usd_root / "scene/factory/factory_map.yaml",
)
session.register_agent('m20_1', use_goal_position=False)
planned = session.plan_to_goal(
    robot_name='m20_1',
    start_xy=(-3.0, 5.0),
    goal_xy=(-4.5, -4.0),
)
assert planned
~~~

规划器本身不下载或携带生产地图。调用方应先通过所选场景的资产预检填充 `EAI_USD_ROOT`，再传入上面的 provider 路径；测试应使用临时地图 fixture。

Fire Rescue 通过 demo/fire_rescue/runtime/algorithm_adapter.py 连接位姿和控制张量。目标位于障碍物内部时，应使用规划器的目标邻域搜索，不要强制规划到障碍物中心；Fire Rescue 当前使用火源约 3 m 邻域。

## 依赖和构建

~~~bash
pip install -r algorithm/global_planner/requirements.txt
cd algorithm/global_planner
bash build_cpp.sh
~~~

C++ 扩展是本地构建产物，不要提交 Git；没有扩展时自动使用 Python 实现。从仓库根目录运行即可导入 algorithm.global_planner.*，不需要 __init__.py。
