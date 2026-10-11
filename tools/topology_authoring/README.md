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

Ground navigation follows only nodes and edge direction, using XY distance
for shortest routes. It ignores edge polylines and profile clearance. Express
each bend with an intermediate node and connect the nodes in order; changing
an edge's polyline alone does not change the robot's path. Authors guarantee
direct passage between adjacent nodes and choose spacing manually. The runtime
imposes no automatic spacing limit and performs no obstacle avoidance.
