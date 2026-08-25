# EAI Multi-Robot Navigation

This directory implements EAI Simulator multi-robot navigation: the native db-CBS planner, occupancy-map conversion, synchronized trajectory runtime, EAI session adapter, and optional viewport UI. The caller owns the Isaac application and simulation loop. This component does not use a ROS launch file and does not create a second simulator.

## Layout and entry points

- native/: db-CBS, the customized OMPL revision, dynoplan/dynobench, robot models, motion primitives, and licenses.
- planner.py, session.py, trajectory.py: typed planning conversion, mission state, and synchronized playback.
- map_environment.py: occupancy-map conversion.
- eai_plugin.py: EAI session adapter, bounded planner worker, mission state, safety checks, and action generation.
- interaction.py and ui.py: optional USD and viewport helpers.
- integration.py: interactive, explicit-goal, and exchange-mission CLI.
- build_native.sh and fetch_motion_primitives.py: native build and verified motion-primitive setup.

## Use in an EAI scene

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

from_session() selects a maintained map from maps/ using EAI selection data; pass map_yaml for a custom scene. The plugin discovers agents from SimulatorSession.possible_agents and returns actions only for managed ground robots.

## Runtime behavior

- The default backend is the local db-CBS implementation; planner_backend='global' is an explicit compatibility mode.
- Initial and conflict-triggered replanning run on a bounded worker instead of the Isaac thread.
- start_navigation() accepts goals; state().planning_error reports the final planning result.
- compute_actions() remains non-blocking and emits hold commands while planning is pending.
- Continuous-clearance checks can hold one frame and replan from current poses; failed replans retain the mission and retry.
- close() cancels queued work and releases the worker.

## Native build

~~~bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate env_isaaclab
python -m pip install --no-deps crocoddyl==2.0.2
algorithm/multi_robot_navigation/build_native.sh
~~~

The native build also requires CMake, a C++17 compiler, Boost, Eigen, FCL, yaml-cpp, and Crocoddyl. See native/THIRD_PARTY.md for provenance.

## Integration commands

~~~bash
python -m algorithm.multi_robot_navigation.integration --env dbcbs_slam_team --real-time
python -m algorithm.multi_robot_navigation.integration --env dbcbs_slam_team --exchange --real-time
~~~

The first command opens viewport selection for robots and ground goals; the second runs a non-interactive exchange mission. Full Isaac execution requires assets and env_isaaclab and is not an offline smoke test.
