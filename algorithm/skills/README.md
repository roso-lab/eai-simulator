# EAI low-level skills

This package follows HeteroCoord's `simulator.skill.v1` boundary:
`SkillRequest -> pure provider -> SkillPlan -> EAI executor -> controller ->
external evaluator`.

Ground navigation is implemented by
`algorithm.skills.navigation.GroundNavigationProvider`: authored node routing,
heading PID, fixed cruise speed and distance-only node arrival. Native I/O is
owned by `task.executors.ground_navigation.GroundNavigationExecutor`, which
uses the caller's existing EAI environment. It requires validated topology and
world state, with no occupancy, costmap, DWA, OBB or collision queries.
See [the navigation guide](navigation/GROUND_NAVIGATION.zh-CN.md).

The idealized manipulator skill is a private single-environment runtime for UR5
and Z1 cylindrical pick/place demonstrations. It uses model-specific yaw joints,
reduced plane IK, recovery poses, and non-physical follow proxies; it does not
provide navigation or a ROS2 pick/place action. See the
[idealized manipulation guide](IDEALIZED_MANIPULATION.zh-CN.md).

`algorithm.skills.navigation.AerialNavigationProvider` compiles world-space
`goal_position` commands from manually authored XYZ nodes and directed edges.
It supports dispatch, start, continue, hold, replan and stop without occupancy,
voxel or collision queries. See the [aerial navigation guide](AERIAL_NAVIGATION.zh-CN.md)
and run the [Iris GUI example](navigation/examples/aerial_topology_gui_demo.py):

```bash
conda run --no-capture-output -n env_isaaclab python \
  algorithm/skills/navigation/examples/aerial_topology_gui_demo.py --device cuda:0
```

`AerialPickPlaceProvider` implements `eai.transport.aerial_pick_place` with authored
XYZ navigation, a continuous vertical profile and mutation confirmations. The
caller-owned `AerialPickPlaceExecutor` applies existing position controllers,
temporary world support, owned FixedJoints and exact collision restoration.
Only iris (`PegasusIris`) and pegasus (`PegasusX4`) position robots pass the
executor robot-type gate; cf2x and every other robot is rejected at dispatch.
Attached cancellation retains the joint and resource lease for explicit host recovery.
See [the aerial transport guide](aerial_transport/AERIAL_PICK_PLACE.zh-CN.md).

The [shared aerial runtime guide](AERIAL_RUNTIME.zh-CN.md) connects these
providers to task/benchmark execution: frozen authored maps, English landmarks,
authenticated external leases, one host-owned physics tick and measured recovery.

Pepper composite transport routes to `PEPPER_BOX_TRANSPORT_CFG`. G1 is deliberately not exposed
as a supported skill. Provider planning and executor feedback never certify a
physical outcome; only the external evaluator may do so.

The machine-readable registry in `simulator_schema.py` also preserves the
generic benchmark adapter contracts for goal/velocity navigation,
manipulation, inspection, handoff and external I/O. See
[`SKILL_DEVELOPER_HANDOFF.zh-CN.md`](SKILL_DEVELOPER_HANDOFF.zh-CN.md) before
adding or changing a provider boundary.
