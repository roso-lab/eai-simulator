# Navigation skill providers

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
Global-grid, db-CBS, Nav2 and keyboard providers remain separate pending entries.
