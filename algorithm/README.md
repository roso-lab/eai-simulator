# Algorithm

`algorithm/` 放置可由 Simulator 和外部 demo 独立调用的算法模块。

| 路径 | 说明 |
|---|---|
| `emos/` | 多智能体 LLM 讨论、任务解析与分配 |
| `global_planner/` | 2D 占据栅格规划、路径跟踪与 `GlobalNavSession` |

## 独立导入

`algorithm/`、`algorithm/emos/` 和 `algorithm/global_planner/` 使用 Python namespace package，不依赖目录顶层的 `__init__.py`。调用方应导入具体模块：

```python
from algorithm.emos.engine import EMOSDiscussionManager
from algorithm.emos.types import scenario_from_dict
from algorithm.global_planner.session import GlobalNavSession
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
- `demo/fire_rescue/runtime/algorithm_adapter.py` 是 Simulator 与 `global_planner` 之间的薄适配层。
- 各算法的安装和构建说明见各自目录中的 `README.md` 与 `requirements.txt`。
