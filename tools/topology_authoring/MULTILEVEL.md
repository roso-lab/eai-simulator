# Multilevel topology authoring

This guide covers descriptor-driven authoring of version 2 topology products for
static, metre/Z-up USD scenes. Version 2 adds explicit levels, semantic
landmarks, transition nodes, and traversal-aware edges. It does not infer floors
from mesh geometry or certify that a robot can physically traverse a route.

## Prepare a scene descriptor

Create `scene.json` beside the scene assets. `usd_path` may be absolute or
relative to the descriptor directory. Level IDs are author-controlled stable
identifiers; `support_z_m` is the floor support height in scene-world metres.

```json
{
  "schema": "eai.topology.usd_scene",
  "version": 1,
  "scene_key": "office",
  "usd_path": "office.usd",
  "levels": [
    {"id": "1f", "label": "First floor", "support_z_m": 0.0},
    {"id": "2f", "label": "Second floor", "support_z_m": 3.2}
  ],
  "semantic_targets": [
    {
      "ref": "place:lobby@1f",
      "label": "Lobby",
      "kind": "target_entrance",
      "position_xyz": [1.0, 2.0, 0.0]
    },
    {
      "ref": "place:conference@2f",
      "label": "Conference room",
      "kind": "target_entrance",
      "position_xyz": [8.0, 5.0, 3.2]
    }
  ],
  "support_mesh_paths": ["/Office/Floors", "/Office/Stairs"],
  "level_view_hide_paths": {
    "1f": ["/Office/Roof", "/Office/SecondFloor"],
    "2f": ["/Office/Roof"]
  },
  "level_view_cut_z_m": {
    "1f": 2.8,
    "2f": 6.0
  }
}
```

`semantic_targets` and the two view helpers are optional. When
`support_mesh_paths` is omitted, the adapter considers the scene's supported
mesh geometry. `level_view_hide_paths` contains absolute USD prim paths hidden
only in the authoring session layer. Scenes whose meshes span several floors
may additionally provide the adapter-supported `level_view_cut_z_m` mapping for
a geometry-only cutaway. These view changes do not alter the source USD,
collision geometry, support extraction, or runtime raycast proxy.

Support inputs must be static collision triangle meshes. Enabled rigid-body
ancestors (including kinematic bodies), animated transforms, and animated mesh
points/topology are rejected; a frozen mesh cannot certify moving support.

Check the descriptor and source digests without opening the interactive Kit
window:

```bash
python tools/topology_authoring/author.py \
  --scene-key office \
  --adapter tools.topology_authoring.adapters.usd_scene:UsdSceneAdapter \
  --asset-root /path/to/office-assets \
  --output-root /path/to/prepared-package \
  --headless
```

Remove `--headless` to open the authoring window. The USD adapter still requires
an environment that provides OpenUSD; interactive authoring requires Isaac Sim.

## Version 2 topology contract

A generated product uses `schema: eai.navigation.topology` and `version: 2`.
The abridged example below shows the multilevel fields; generated products also
retain the existing source-digest, locking, polyline, and profile fields.

```json
{
  "schema": "eai.navigation.topology",
  "version": 2,
  "scene_key": "office",
  "coordinate_frame": "scene_world",
  "meters_per_unit": 1.0,
  "up_axis": "Z",
  "levels": [
    {"id": "1f", "label": "First floor", "support_z_m": 0.0},
    {"id": "2f", "label": "Second floor", "support_z_m": 3.2}
  ],
  "nodes": [
    {
      "id": "lobby",
      "kind": "target_entrance",
      "position_xyz": [1.0, 2.0, 0.0],
      "level_id": "1f",
      "label": "Lobby",
      "aliases": ["entrance"],
      "target_refs": ["place:lobby@1f"]
    },
    {
      "id": "stair_mid",
      "kind": "transition",
      "position_xyz": [4.0, 3.0, 1.6],
      "level_id": null,
      "label": "Main stair midpoint",
      "aliases": [],
      "target_refs": []
    }
  ],
  "edges": [
    {
      "id": "lobby_to_stair",
      "from": "lobby",
      "to": "stair_mid",
      "directed": true,
      "traversal_mode": "stairs",
      "required_controller_capabilities": ["rough_terrain"]
    }
  ]
}
```

Every regular node references a declared level and lies within 0.20 m of that
level's support Z. Only `kind: transition` nodes may have `level_id: null`.
Every edge has nonzero XY length. A `walk` edge stays on one declared level and
cannot join an unassigned transition node. `stairs` and `ramp` describe
admission requirements; their traversal mode is itself an implicit controller
capability, in addition to any strings in
`required_controller_capabilities`.

Stable `target_refs` must be unique across version 2 nodes. Labels and aliases
are human-facing and may repeat, but a repeated match is deliberately rejected
as ambiguous unless a level filter disambiguates it. Prefer floor-qualified
refs such as `place:conference@2f` in executable requests.

## Edit one level or the whole building

The editor starts on the first declared level and shows its exact support Z.
Choose a level to project clicks toward that floor and to filter the overlay and
picking. Choose **All Levels** to inspect the complete graph or place an
unassigned transition node. Changing the level cancels an unfinished node or
edge preview so a click sequence cannot silently cross views.

Regular nodes require an active level. Use **Transition Node** for stair or ramp
intermediate points; in All Levels mode such a node is stored with
`level_id: null`. Transition nodes and cross-level connections remain visible
from adjacent floor views. Stacked nodes with identical XY are selected using
their full XYZ position and the active-level filter.

For a selected node, edit its XYZ, level ID, landmark label, aliases, and stable
target bindings. For an edge, choose `walk`, `stairs`, or `ramp` and enter any
additional controller capabilities. Use one-way edges when ascent and descent
are not both supported.

The executor follows node endpoints, not decorative polyline bends. Author
enough stair/ramp nodes to provide real XY progression and put every required
turn in the node graph. A purely vertical edge is invalid for the planar ground
controller.

Drafts and products are written below
`authoring/navigation/ground/`. Version 2 initialization from `scene.json`
levels is the document's initial state, so undo cannot fall back into a
mismatched version 1 draft.

## Preview and export a landmark route

In **Landmark Route Preview**, enter an exact target ref or an unambiguous node
ID, label, or alias. Enter the controller's actual capabilities as a
comma-separated list, for example `walk, stairs, rough_terrain`. The preview
uses the same pure resolver and graph digest as navigation admission. A failed
or graph-stale preview is removed instead of leaving an old route visible.

**Export Preview** atomically writes:

```text
authoring/navigation/ground/route.preview.json
```

This file records resolved node IDs, XYZ points, stable refs, and graph identity.
It is a planning/inspection artifact, not a runtime success receipt.

The same operations are available without Isaac Sim:

```bash
python -m tools.topology_authoring.route \
  --topology /path/to/topology.json \
  --list-landmarks

python -m tools.topology_authoring.route \
  --topology /path/to/topology.json \
  --origin place:lobby@1f \
  --destination place:conference@2f \
  --capability walk \
  --capability stairs \
  --capability rough_terrain \
  --output /path/to/route.json
```

Use `--origin-level` and `--destination-level` only when a label or alias needs
an explicit level filter. Run
`python -m tools.topology_authoring.route --help` for the complete interface.
An unreachable or capability-incompatible route is not admitted.

## Deployment diagnostic

### English landmarks and authored heading

Use the existing editor to name points, for example `pantry_1f` and
`meeting_room_2f`. Labels/aliases belong to the topology and do not require
predeclared external semantic targets; their stable node ID is the execution
fallback. External target bindings still require the source's target inventory.
Duplicate names need an explicit level filter.
The **Named Landmarks** list shows saved names and levels. **Select** activates
the corresponding floor and node; **Origin / Destination** insert its stable
node ID into the route query so duplicate labels remain unambiguous.

Enter XYZ in metres and choose **Typed Node Preview**, or click a support surface.
Enter optional heading degrees (+X=0, +Y=90) or choose **Pick Heading** followed by
a second viewport point. Only the second point's XY direction affects yaw.
The node's `heading_rad` is serialized in normalized radians; labels, position,
level, aliases and heading survive save/reload and undo/redo.

Route exports contain `route_headings_rad` aligned with `route_points_m` and a
terminal `target_heading_rad`. A constrained terminal heading is also emitted as
`skill_parameters.target_yaw`. Intermediate headings are metadata; execution
aligns only at the destination, rechecking position and support height throughout
the turn. Missing heading keeps position-only arrival. Version 1 is unchanged.
A same-node semantic query does not certify heading arrival: the execution
provider must read the actual robot pose, including after a pre-start hold.

The upstream fixed JSON format will be supplied by the user later. No natural
language parser or new task-input schema is introduced; existing explicit
origin/destination resolver parameters remain the integration boundary.

Model integration starts with `landmark_inventory(document)`. A model selects
authored origin/destination references, then `resolve_landmark_route` returns
ordered XYZ points, `target_ref` and `skill_parameters` for the existing
`eai.navigation.ground_navigation` request. The host still supplies robot/task
IDs, intent and the fresh world-state sequence. Admission checks the actual
robot start; the declared origin is not a substitute for measured state.

Inject `support_height_reader(robot_id)` into the existing executor to measure
the actual support surface. Do not subtract a constant from body-root Z. Floor
height bands, including ordinary node heights, must be separated by more than
0.40 m so the 0.20 m arrival tolerances do not overlap. Small stair landings use
transition nodes. Ground commands remain `[vx, 0, wz]`; the RL controller owns
gait and stair traversal. Declared capabilities are admission constraints, not
certification for arbitrary stairs.

The deployment CLI launches a declared route through the shared simulator
session. It requires a prepared Isaac Sim environment and normally a CUDA GPU:

```bash
python -m tools.topology_authoring.deploy \
  --scene-key office \
  --asset-root /path/to/office-assets \
  --topology /path/to/topology.json \
  --origin place:lobby@1f \
  --destination place:conference@2f \
  --output-root tmp/office-route-01 \
  --controller GO2_MIXED_STAIRS_CFG \
  --device cuda:0
```

The output directory must be new. It receives pinned inputs, route progress,
native samples, optional images, and a final diagnostic result. Run
`python -m tools.topology_authoring.deploy --help` for timing, speed, tolerance,
headless, scanner-offset, and GUI-hold options. This command creates diagnostic
evidence only; it does not create a benchmark task or formal experiment
contract.

Capture uses render-only updates and asserts that physics time stays unchanged.
Samples retain support height, posture, scan validity, terminal zero application
and settled speed. Falling beyond 60 degrees from upright terminates the route;
sampling/capture errors attempt a zero command and retain a failed receipt.
Zero velocity requests stopping, not physical freezing or self-righting.
Mixed-stairs yaw is capped at its trained 0.6 rad/s range. See the
[native validation record](MULTILEVEL_VALIDATION.md) for measured results and
local GUI commands.

## Version 1 compatibility and limits

Version 1 products retain their sparse node/edge serialization, XY routing,
graph digest, and XY-only arrival behavior. They do not gain inferred levels or
version 2 metadata. A source descriptor with `levels` initializes a new version
2 document explicitly.

Schema validation, source digests, support projection, route resolution, and a
green authoring preview are not proof of physical traversal. They do not, by
themselves, establish robot footprint clearance, stair dimensions, friction,
collision quality, controller training coverage, scanner validity, obstacle
avoidance, traffic handling, or safe access from the spawn pose to the first
node. Support Z is the surface height, not the robot articulation-root Z.
Claim physical success only from a native run with retained measurements and an
appropriate task evaluator.
