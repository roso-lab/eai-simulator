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

The currently registered composite transport implementation is Pepper-only and
routes to `PEPPER_BOX_TRANSPORT_CFG`. G1 transport is deliberately not exposed
as a supported skill. Provider planning and executor feedback never certify a
physical outcome; only the external evaluator may do so.
