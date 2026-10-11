# Navigation skill providers

## Injected 2D planner adapter

`NavigationProvider(planner, planner_version=...)` wraps a caller-owned existing
2D planner. It reads the assigned robot's live `WorldSnapshot`, accepts a resolved
world `target_pose`, and returns a `navigate_waypoints` SkillPlan. Map, footprint,
planning budget and algorithm version belong to the injected planner. A missing
path is `unreachable`; there is no straight-line fallback.

`NavigationHandle` adapts existing path installation and tracking callbacks to
`command / after_step / cancel`. It never steps the simulator. It confirms a
terminal receipt only after measured arrival and stable stopping; cancellation
returns failure after stable stopping. Use `CooperativeBatchExecutor` for one
merged simulator step across robots. The caller supplies controller conversion,
neutral actions, actual world pose and velocity probes.

This is an adapter, not bundled navigation infrastructure. The planner must be
installed/injected; DB-CBS, Nav2 and keyboard providers retain their own status.
The task evaluator still owns physical safety and business success.
## Authored topology navigation

`GroundNavigationProvider` implements `eai.navigation.ground_navigation` using
authored topology nodes, directed XY Dijkstra and a heading PID. It compiles
body-frame `base_velocity=[v, 0, wz]` commands and keeps independent state for
each task. It does not load occupancy maps, costmaps, DWA, OBB or collision
queries; bends require intermediate nodes.

The provider implements the shared `plan(request, observation=...)` API and
explicit `dispatch` / `control` lifecycle methods. It returns `SkillPlan`
records without importing a simulator or applying actions.

`task.executors.ground_navigation.GroundNavigationExecutor` reads real EAI root
XYZ+yaw, uses simulation-time intervals, and applies velocity tensors through
the host's existing `env.step` route. An external evaluator owns physical
success. City-specific loaders and native execution are outside this migration.

See [the Chinese guide](GROUND_NAVIGATION.zh-CN.md) for request fields, defaults,
topology authoring, lifecycle controls, executor injection and focused tests.
The injected 2D planner adapter above is independent; db-CBS, Nav2 and keyboard
providers retain their separate registry status.
