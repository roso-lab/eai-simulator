# Global Planner

algorithm/global_planner/ is a standalone 2D occupancy-grid package for map loading, A*/RRT planning, obstacle inflation, reachable goal-neighborhood search, path post-processing, path tracking, and velocity commands. It is independent of Isaac Sim, EMOS, Torch, and ROS and can run in an offline Python environment.

## Modules

| File | Responsibility |
|---|---|
| core.py | FactoryMapPlanner, A*/RRT, obstacle inflation, and goal-neighborhood search. |
| tracking.py | Robot profiles, path tracking, and smoothed commands. |
| session.py | GlobalNavSession for planning, tracking, and per-agent state. |
| csrc/ and build_cpp.sh | Optional _planner_cpp acceleration extension. |

## Import and minimal example

~~~python
from algorithm.global_planner.session import GlobalNavSession

session = GlobalNavSession(
    map_yaml='demo/fire_rescue/assets/factory_map.yaml',
)
session.register_agent('m20_1', use_goal_position=False)
planned = session.plan_to_goal(
    robot_name='m20_1',
    start_xy=(-3.0, 5.0),
    goal_xy=(-4.5, -4.0),
)
assert planned
~~~

Fire Rescue connects poses and action tensors through demo/fire_rescue/runtime/algorithm_adapter.py. When a goal lies inside an obstacle, use the planner's goal-neighborhood search instead of forcing the obstacle center; Fire Rescue uses an approximately 3 m fire-source neighborhood.

## Dependencies and build

~~~bash
pip install -r algorithm/global_planner/requirements.txt
cd algorithm/global_planner
bash build_cpp.sh
~~~

The C++ extension is a local build artifact and must not be committed. Without it the Python implementation is used automatically. Start from the repository root to import algorithm.global_planner.*; no __init__.py is required.
