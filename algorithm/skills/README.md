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

Pepper composite transport routes to `PEPPER_BOX_TRANSPORT_CFG`. G1 is deliberately not exposed
as a supported skill. Provider planning and executor feedback never certify a
physical outcome; only the external evaluator may do so.
