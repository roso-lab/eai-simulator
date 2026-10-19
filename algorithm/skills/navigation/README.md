# Unified node-route navigation

The only navigation Skill is `eai.navigation.node_route`. A request contains
one symbolic `target_ref` and a `world_state_seq`; coordinates and controller
commands are never accepted at the Skill boundary.

`NavigationProvider` is bound to one frozen `TopologyDocument` product and one
`NavigationProfile`. It resolves an exact node ID or a unique
`node.target_refs` value, then consumes semantic regions, entities, interaction
poses and named routes when those bindings are present. Regions choose entrance
nodes, task poses choose approach nodes, and named routes constrain the edge
sequence. The provider checks the measured start and runs deterministic Dijkstra
over directed node edges. It emits a single
`navigation_target` containing the authored node ID, index, XYZ and optional
final heading. It does not import Isaac or know a controller command format.

Ground and aerial products use the same JSON schema, but each robot loads its
own map product. A ground profile measures start matching, edge length,
arrival and route length in XY. An aerial profile measures them in XYZ. Edge
polylines and traversal metadata are authoring data; the runtime only uses
node endpoints and directed edges.

`NavigationExecutor` owns the lifecycle around a shared host physics tick. Its
`prepare()` phase reads the adapter observation, advances the provider and
writes one controller-independent target. The host calls `env.step()` once,
then `after_step()` consumes measured pose, velocity and stop feedback. The
executor also owns robot leases, sequence and simulation-time checks,
deadlines, cancellation, private evidence and quarantine on an unconfirmed
stop.

`NavigationControllerAdapter` is the extension point for a robot or chassis.
`PlanarVelocityNavigationAdapter` compiles a target into `base_velocity` and
`WorldPositionNavigationAdapter` compiles it into a world `goal_position`.
Adding another controller only requires another adapter; the Skill and
Provider remain unchanged.

Topology authoring lives in `tools/topology_authoring`. `TopologyDocument`
can produce independent ground and aerial products with identical schema and
stable v2 route digests.
