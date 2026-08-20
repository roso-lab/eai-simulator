# Algorithm

`algorithm/` 放置可由 Simulator 和外部 demo 独立调用的算法模块。

| 路径 | 说明 |
|---|---|
| `emos/` | 多智能体 LLM 讨论、任务解析与分配 |
| `global_planner/` | 2D 占据栅格规划、路径跟踪与 `GlobalNavSession` |
| `multi_robot_navigation/` | db-CBS 原生规划内核、同步轨迹运行时与 EAI `SimulatorSession` 多机导航组件 |

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
- `demo/fire_rescue/runtime/algorithm_adapter.py` 只保留 Fire Rescue 的兼容类名。
- 各算法的安装和构建说明见各自目录中的 `README.md`；独立 Python 依赖文件仅在需要时提供。
