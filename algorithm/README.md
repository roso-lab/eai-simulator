# Algorithm Modules

This directory contains reusable planners and ROS integration clients that can be called by the simulator or external demos. Modules have separate runtime boundaries; see each module README for its dependencies and startup procedure.

## Catalog

| Path | Purpose |
|---|---|
| algorithm/emos/ | Scenario-driven multi-agent LLM discussion and subtask allocation. |
| algorithm/TeamWeaver/ | LLM task decomposition, MIQP/Hungarian allocation, phase scheduling, and replanning. |
| algorithm/global_planner/ | Standalone 2D occupancy-grid planning, path tracking, and velocity commands. |
| algorithm/multi_robot_navigation/ | Native db-CBS, synchronized trajectories, and the EAI Simulator session adapter. |
| algorithm/nav2/ | External ROS2/Nav2 configuration, TF completion, point-cloud conversion, and goal client. |
| algorithm/keyboard/ | Interactive ROS2 keyboard client for the EAI cmd_vel interface. |

## Import boundary

From the repository root, the namespace-package modules can be imported directly. Multi-robot navigation exposes its public sessions from __init__.py; its EAI adapter is in eai_plugin.py.

~~~python
from algorithm.emos.engine import EMOSDiscussionManager
from algorithm.emos.types import scenario_from_dict
from algorithm.global_planner.session import GlobalNavSession
from algorithm.multi_robot_navigation import DbcbsNavigationSession
from algorithm.multi_robot_navigation.eai_plugin import EaiMultiRobotNavigationPlugin
~~~

A repository-root demo can be started without a second checkout:

~~~bash
cd eai-simulator
python -m demo.fire_rescue.main --env=EAI-Factory-v0 --device=cuda:0
~~~

Fire Rescue selects this repository's algorithm directory through demo/fire_rescue/algorithm_paths.py. The adapter connects simulator state to the planner and does not replace the planner implementation.

## Runtime boundaries

- EMOS receives a scenario, robot specifications, and optional environment state; it does not build an Isaac scene or execute actions.
- TeamWeaver receives natural-language tasks and symbolic world state; it returns a task DAG and assignments, not simulator commands.
- The global planner is independent of Isaac Sim, EMOS, Torch, and ROS.
- Multi-robot navigation owns its planning worker and action generation; the caller owns the Isaac application, simulation loop, and cleanup.
- Nav2 and keyboard run in the selected system-ROS Python, not in env_isaaclab. Do not mix rclpy processes with the Isaac Lab Conda interpreter.
- tools/ros2/ contains operational ROS clients, not additional control algorithms or manipulator graph owners.

## Documentation

Each module README documents its import path, dependencies, startup commands, and limitations. Provider-owned assets, model weights, ROS installations, and Isaac Sim are runtime prerequisites and are not downloaded by these examples.
