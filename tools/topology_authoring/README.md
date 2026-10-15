# Generic topology authoring

`tools/topology_authoring` provides a scene-adapter based topology editor. Run
`python tools/topology_authoring/author.py --help`. Use `--headless` to check
an adapter without starting Isaac Sim. Every scene starts with an empty graph;
the editor does not import roads, junctions, or centerlines automatically.
Adapters only load scene facts, semantic targets, and support-surface checks.
Pass `--semantic-map release/maps/task_semantic_map.json` when the semantic map
is stored in a prepared package rather than beside the source assets.

For optional floor grouping, landmark labels, ordered node-route export and Go2
deployment, see [the multilevel workflow](MULTILEVEL.md).
The bundled `UsdSceneAdapter` accepts a scene descriptor; scene data stays with
the caller and does not become a benchmark task.

For an aerial product, add `--domain aerial`. The same editor supports arbitrary
world XYZ, English labels/aliases, optional heading metadata and directed edges.
Drafts/products/previews are stored separately under
`authoring/navigation/aerial/`. Ground and aerial products have the same JSON
schema; their runtime profiles choose XY or XYZ semantics. Bundled adapters
check structure only, not obstacle clearance. Each bend must be a node. Yaw is
passed to the Provider only when the selected controller profile supports it.

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

Products and the node-route Skill share the same executable contract:
nonempty unique IDs, finite numeric XYZ (no booleans or numeric strings), valid
directed endpoints, including zero-length links under the chosen metric. Coordinates must be
`scene_world`, metres (`meters_per_unit=1`), and Z-up. Scene-bound loading checks
the scene key, current source digests, and every semantic binding, including
when the scene's target inventory is empty. An empty draft may be saved/loaded;
generation requires at least two nodes and an edge. Disconnected components and
one-way unreachable routes remain valid authoring choices; the skill reports
an unreachable request; the Executor asks its adapter to stop.

The adapter's `validate_polyline` receives the **two endpoint positions actually
followed by ground navigation**, once per declared profile. Decorative edge
polylines are checked for well-formed points/endpoints but do not establish
passage. The adapter supplies scene checks; `EmptyTopologyAdapter` supplies no
geometry, semantic targets or asset digests and cannot certify a real scene.

`load_product()` always checks product structure, even without an adapter.
For experiment integration, the caller freezes `product_digest(document)` with
the scene/semantic inputs and adapter version in its task/asset identity, then
calls `load_product(path, adapter, source, expected_digest=frozen_digest)` before
injecting the document into `NavigationExecutor`. The full product digest
includes editing metadata. The skill separately records an order-independent
`route_graph_digest` of nodes, bindings and directed edges in private route
evidence; this does not replace source identity or a physical evaluator. Hosts
must not silently swap maps during a run; explicit replan keeps the bound map.

Saving uses an atomic replacement and rejects nonfinite JSON. Reloaded drafts
start a new undo/redo history. A failed scene reload preserves the document;
adapters should construct a replacement stage rather than mutate the currently
attached stage in place. Headless validation errors return a nonzero exit code.
Map validation and navigation arrival are not physical task-success verdicts.

Navigation follows only nodes and edge direction. A ground profile uses XY
distance and an aerial profile uses XYZ length; neither profile reads traversal
labels or controller capabilities. Both ignore edge polylines and profile
clearance at runtime. Express each bend with an intermediate node and connect
the nodes in order; changing an edge's polyline alone does not change the
robot's path. Authors guarantee direct passage between adjacent nodes and
choose spacing manually. The runtime imposes no automatic spacing limit and
performs no obstacle avoidance.
The host must also guarantee direct access from the robot's initial position
to the nearest start node; that existing start/replan connection is not an
authored edge and is not certified by product validation.

See [validation commands, native procedure and limits](VALIDATION.md) for the
authoring-to-execution checks.
