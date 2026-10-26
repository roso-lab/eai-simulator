# Generic topology authoring

Launch Isaac Sim and the linked browser editor with one Python entry:

```bash
python tools/topology_authoring/launch.py --catalog /path/to/map-catalog --scene living_office --asset-root usd --port 8770
```

Select the USD category, then scene name in the webpage to open the exact root
USD and reconnect the editor automatically. Camera Light is enabled.
Launching without arguments discovers the scene-local maps below `usd/scene/`.
`--check` validates all map and navigation bindings without Kit. No Script Editor paste is needed.
Standalone `map_server` below does not control Isaac scene loading.

The **Isaac Topology Editor** provides Point, Edge and Semantic Point tools,
with separate ground, aerial and semantic point layers. Only the active tool's
objects are editable; visibility controls show other layers as references.
Regions are removed from this unified editor. Aerial nodes and semantic points
show all heights independently of the selected reference basemap. Their creation
height defaults to world Z=1.5 m. Each supports 2.5D (XY drag preserving Z) and
3D (Isaac XYZ handles); properties always allow explicit Z edits.

Semantic points store world XYZ, label, kind, description, aliases, JSON
properties and optional bindings to ground/aerial navigation nodes. They are
portable map annotations and do not reference Isaac objects or require an Isaac
connection. Navigation bindings are edited from the web inspector or native
topology panel and remain independent from scene asset paths.

Drag an edge to translate its endpoints and full polyline in XY, updating all
incident endpoints. Release submits one undoable transaction; Esc cancels.
Left-click edits; Alt+left, middle and right mouse navigation remain available.
Overlays and previews use only the session layer. The legacy surface-selection
bridge remains available for scripts and should not be enabled alongside native
dragging. The separate `author.py` workflow below remains compatible.
The native panel scrolls to navigation binding, undo and save controls. On Linux with Noto
Sans CJK installed, the launcher registers Chinese glyphs before Kit starts so
names, descriptions, aliases and floor labels display correctly.

For room_06, use `python tools/topology_authoring/launch.py --scene living_room_06`.

For a single editable project containing occupancy, semantic obstacle labels and
topology, use `python -m tools.topology_authoring.map_project` and the local browser
editor `python -m tools.topology_authoring.map_server --project DIR`. See the
[unified map workflow](MAP_PROJECT.zh-CN.md) for import, catalog browsing, editing,
fixed-current atomic saves and synchronized JSON/GeoJSON/NPZ/PNG exports.

`tools/topology_authoring` provides a scene-adapter based topology editor. Run
`python tools/topology_authoring/author.py --help`. Use `--headless` to check
an adapter without starting Isaac Sim. New maps start with an empty graph;
use `--draft /path/to/topology.json` to reopen a saved map for editing.
the editor does not import roads, junctions, or centerlines automatically.
Adapters only load scene facts, semantic targets, and support-surface checks.
Pass `--semantic-map release/maps/task_semantic_map.json` when the semantic map
is stored in a prepared package rather than beside the source assets.

For editable rooms, generic objects, later task poses and true XYZ route drawing,
see [the semantic topology workflow](SEMANTIC_MAP.md).

For floor selection, stair/ramp connections, landmark labels, ordered XYZ route
export and Go2 deployment, see [the multilevel workflow](MULTILEVEL.md).
The bundled `UsdSceneAdapter` accepts a scene descriptor; scene data stays with
the caller and does not become a benchmark task.

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
directed endpoints and nonzero XY length for ground edges. Aerial edges may be
vertical. Coordinates must be
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
injecting the document into `NavigationExecutor`. The full product digest
includes editing metadata. The skill separately records an order-independent
`route_graph_digest` of nodes, bindings and directed edges in private route
evidence; this does not replace source identity or a physical evaluator. Hosts
must not silently swap maps during a run; explicit replan uses the supplied map.

Saving uses an atomic replacement and rejects nonfinite JSON. Reloaded drafts
start a new undo/redo history. A failed scene reload preserves the document;
adapters should construct a replacement stage rather than mutate the currently
attached stage in place. Headless validation errors return a nonzero exit code.
Map validation and navigation arrival are not physical task-success verdicts.

Ground navigation follows nodes and edge direction using XY distance; aerial
navigation uses XYZ distance. The topology domain must match the profile at
admission and route selection. It ignores edge polylines and profile clearance. Express
each bend with an intermediate node and connect the nodes in order; changing
an edge's polyline alone does not change the robot's path. Authors guarantee
direct passage between adjacent nodes and choose spacing manually. The runtime
imposes no automatic spacing limit and performs no obstacle avoidance.
The measured start/replan pose must already be within the nearest node's
arrival radius; otherwise routing returns `origin_not_reached`. It does not
automatically drive to the nearest node or edge.

The linked web and Isaac editor has three tools: points, edges and semantic points.
Ground and aerial graphs share base maps, with independent nodes and edges.
Semantic points occupy their own project-level layer and do not participate in
navigation. Each aerial node has an optional `edit_mode` (`2.5d` or `3d`, default
`3d`). A 2.5D drag preserves that node's Z; explicit height input remains
available. A 3D node also exposes an Isaac height handle. Both modes use XYZ
for navigation and may be connected to each other.

Semantic points store independent XYZ coordinates, semantic attributes, editing
mode and an optional current-scene single-Prim binding. Aerial nodes and semantic
points remain visible at all heights when the reference floor changes. Whole-edge
drags translate the edge and both endpoints in XY while preserving every Z.
Drags preview locally and commit once on release, sharing one undo history and
revision stream between both clients. Each save overwrites the fixed `current/`
package containing `topology.json` (ground), `topology-aerial.json` and
`semantic-points.json`, with paths and digests recorded in the manifest. Old
projects upgrade on first save and their `revisions/` directory is removed.
The cleanup command in [the project guide](MAP_PROJECT.zh-CN.md) removes retired
regions, objects, interaction poses and named routes from the current package.

See [validation commands, native procedure and limits](VALIDATION.md) for the
authoring-to-execution checks.
