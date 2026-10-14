# EAI skills

`eai.manipulation.pick_place` is the only public local pick/place skill. Its execution entry is `PickPlaceExecutor.execute(request) -> bool`; each request performs one `pick` or `place`, and the caller owns navigation and transport timing.

The host creates a robot controller and a `PickPlaceAdapter`, registers it by `adapter.robot_id`, and supplies the existing physics step and simulation clock. The executor validates world coordinates, converts them to the sampled chassis frame, owns leases and the local state machine, and returns only the final boolean result. Arm, drone, Pepper and G1 adapters retain their robot-specific motion and feedback logic.

Navigation remains provided by the independent ground and aerial navigation skills. It is never selected or started by PickPlace. The machine-readable registry contains navigation, local manipulation, inspection, coordination and external I/O contracts; deprecated complete transport skill IDs are not registered.

See [the local PickPlace guide](pick_place/README.zh-CN.md) and [the robot addition guide](pick_place/ADDING_ROBOTS.zh-CN.md).
