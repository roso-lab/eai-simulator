# EAI 多机导航

本目录实现 EAI Simulator 的多机器人导航：原生 db-CBS 规划器、占据地图转换、同步轨迹运行时、EAI 会话适配器和可选 viewport UI。调用方拥有 Isaac application 和仿真循环；本组件不使用 ROS launch，也不会创建第二个 simulator。

## 目录和入口

- native/：db-CBS、定制 OMPL、dynoplan/dynobench、机器人模型、运动基元和许可证。
- planner.py、session.py、trajectory.py：规划转换、任务状态和同步轨迹。
- map_environment.py：占据地图转换。
- eai_plugin.py：EAI session 适配、规划 worker、任务状态、安全检查和动作生成。
- interaction.py、ui.py：USD 选择辅助和可选 viewport 控件。
- integration.py：交互式、显式目标和 exchange mission 命令行入口。
- build_native.sh、fetch_motion_primitives.py：原生构建和校验后的运动基元设置。

## 在 EAI 场景中使用

~~~python
from algorithm.multi_robot_navigation.eai_plugin import EaiMultiRobotNavigationPlugin
from simulator import SimulatorLaunchConfig, open_simulator_session

config = SimulatorLaunchConfig(
    env='my_env',
    enable_ros_bridge_extension=False,
)
with open_simulator_session(config) as sim:
    navigation = EaiMultiRobotNavigationPlugin.from_session(sim)
    try:
        navigation.set_goal('go2_1', (-1.0, 4.0))
        navigation.start_navigation()
        while sim.simulation_app.is_running():
            sim.env.step(navigation.compute_actions())
    finally:
        navigation.close()
~~~

from_session() 根据 EAI selection data 从 maps/ 选择维护的地图；自定义场景时传入 map_yaml。组件从 SimulatorSession.possible_agents 发现机器人，只为管理的地面机器人返回动作。

## 运行行为

- 默认 backend 是本目录的 db-CBS；planner_backend='global' 只用于明确要求旧规划器的兼容场景。
- 初次规划和冲突触发的重规划在有界 worker 中执行，不阻塞 Isaac 线程。
- start_navigation() 接受目标；最终结果通过 state().planning_error 观察。
- 规划等待期间 compute_actions() 不阻塞，并返回 hold command。
- 连续距离检查可能保持一帧并从当前位姿重规划；失败的重规划保留任务并重试。
- host 关闭时必须调用 close()，以取消排队工作并释放 worker。

## 原生构建

~~~bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate env_isaaclab
python -m pip install --no-deps crocoddyl==2.0.2
algorithm/multi_robot_navigation/build_native.sh
~~~

原生构建还需要 CMake、C++17 编译器、Boost、Eigen、FCL、yaml-cpp 和 Crocoddyl。来源和许可证见 native/THIRD_PARTY.md。

## 集成命令

~~~bash
python -m algorithm.multi_robot_navigation.integration --env dbcbs_slam_team --real-time
python -m algorithm.multi_robot_navigation.integration --env dbcbs_slam_team --exchange --real-time
~~~

第一条命令打开 viewport 选择机器人和地面目标；第二条执行非交互 exchange mission。完整 Isaac 运行需要资产和 env_isaaclab，不是离线 smoke test。
