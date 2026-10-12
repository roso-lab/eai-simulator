# Generic topology authoring

`tools/topology_authoring` provides a scene-adapter based topology editor. Run
`python tools/topology_authoring/author.py --help`. Use `--headless` to check
an adapter without starting Isaac Sim. Every scene starts with an empty graph;
the editor does not import roads, junctions, or centerlines automatically.
Adapters only load scene facts, semantic targets, and support-surface checks.
Pass `--semantic-map release/maps/task_semantic_map.json` when the semantic map
is stored in a prepared package rather than beside the source assets.

For any scene, use an adapter that only loads scene facts:

```bash
python tools/topology_authoring/author.py \
  --scene-key warehouse \
  --adapter tools.topology_authoring.adapters.empty:EmptyTopologyAdapter \
  --asset-root /path/to/scene-assets \
  --output-root /path/to/prepared-package
```

Scene-specific adapters are supplied by the caller and are intentionally kept
outside this generic tool. The bundled `EmptyTopologyAdapter` is useful for
checking the editor and for scenes whose support-surface facts are provided by
another process.

In the Isaac Sim viewport, a click on empty ground creates a red node preview;
`Confirm Node` commits it as a green node. Clicking a green node starts an edge
preview, and clicking another green node selects its destination. `Confirm Edge`
commits the green edge. One-way edges show one arrowhead and two-way edges show
opposing arrowheads. Clicking an existing edge selects it. Right-click cancels
the active red preview. The Selection, Node Properties, and Edge Properties
panels provide movement, deletion, semantic target binding, direction, profile,
clearance, and polyline editing. Drafts are written below
`authoring/navigation/ground/`; `Generate` writes the executable `topology.json`
only after validation.

Products and the ground-navigation skill share the same executable contract:
nonempty unique IDs, finite numeric XYZ (no booleans or numeric strings), valid
directed endpoints, and nonzero XY edge length. Coordinates must be
`scene_world`, metres (`meters_per_unit=1`), and Z-up. Scene-bound loading checks
the scene key, current source digests, and every semantic binding, including
when the scene's target inventory is empty. An empty draft may be saved/loaded;
generation requires at least two nodes and an edge. Disconnected components and
one-way unreachable routes remain valid authoring choices; the skill reports
an unreachable request and sends zero velocity.

The adapter's `validate_polyline` receives the **two endpoint positions actually
followed by ground navigation**, once per declared profile. Decorative edge
polylines are checked for well-formed points/endpoints but do not establish
passage. The adapter supplies scene checks; `EmptyTopologyAdapter` supplies no
geometry, semantic targets or asset digests and cannot certify a real scene.

`load_product()` always checks product structure, even without an adapter.
For experiment integration, the caller freezes `product_digest(document)` with
the scene/semantic inputs and adapter version in its task/asset identity, then
calls `load_product(path, adapter, source, expected_digest=frozen_digest)` before
injecting the document into `GroundNavigationExecutor`. The full product digest
includes editing metadata. The skill separately records an order-independent
`route_graph_digest` of nodes, bindings and directed edges in private route
evidence; this does not replace source identity or a physical evaluator. Hosts
must not silently swap maps during a run; explicit replan uses the supplied map.

Saving uses an atomic replacement and rejects nonfinite JSON. Reloaded drafts
start a new undo/redo history. A failed scene reload preserves the document;
adapters should construct a replacement stage rather than mutate the currently
attached stage in place. Headless validation errors return a nonzero exit code.
Map validation and navigation arrival are not physical task-success verdicts.

Ground navigation follows only nodes and edge direction, using XY distance
for shortest routes. It ignores edge polylines and profile clearance. Express
each bend with an intermediate node and connect the nodes in order; changing
an edge's polyline alone does not change the robot's path. Authors guarantee
direct passage between adjacent nodes and choose spacing manually. The runtime
imposes no automatic spacing limit and performs no obstacle avoidance.
The host must also guarantee direct access from the robot's initial position
to the nearest start node; that existing start/replan connection is not an
authored edge and is not certified by product validation.

See [validation commands, native procedure and limits](VALIDATION.md) for the
authoring-to-execution checks.
