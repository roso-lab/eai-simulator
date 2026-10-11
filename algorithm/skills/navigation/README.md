# Navigation skill providers

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
