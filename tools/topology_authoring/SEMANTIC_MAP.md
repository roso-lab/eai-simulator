# Editable semantic topology maps

Use `tools.topology_authoring.author` for scene-independent authoring. The adapter
provides geometry and support checks; the saved map contains an executable graph
and independently versioned annotations that can be reopened and extended.

| Layer | Stored records | Purpose |
| --- | --- | --- |
| Floors | `levels`, IDs, labels, support Z | Explicit elevation and floor filtering |
| Navigation | XYZ `nodes` and `edges`, direction, walk/stairs/ramp, capabilities | Executable waypoint graph |
| Regions | Horizontal polygon prisms, height, floor, entrance nodes | Room locations and volumes |
| Entities | Generic objects, optional XYZ/region/floor, properties | Persistent scene facts |
| Named routes | Ordered real node/edge IDs | Editable within-floor and cross-floor routes |
| Interaction poses | Owner, XYZ, optional yaw and approach node | Later task annotations |

The optional `semantics` object uses `schema: eai.scene.semantics`, `version: 1`,
arrays `regions`, `entities`, `interaction_poses`, `routes`, and object `extensions`.
Legacy topology v1/v2 maps without this extension retain their previous format;
unknown semantic versions are rejected. Coordinates are scene_world, metres,
Z-up. All numeric values must be finite. Regions are horizontal polygon prisms,
not arbitrary slanted volumes; stair elevation is expressed through XYZ nodes.

## Authoring and later editing

1. Declare floor IDs, labels and support heights in **Add / Edit Floors**.
   A legacy graph can be upgraded when every node matches exactly one floor.
2. Create and label entrance and stair endpoint nodes on their floors.
3. Fill region ID, label, kind, height and entrance IDs in **Rooms / Regions /
   3D Routes**. **Draw Region**, click the boundary vertices, then **Confirm
   Region**. Do not repeat the closing vertex. **Undo Vertex** pops a click.
4. Set existing start/end node IDs, choose **Draw walk / stairs / ramp**, click
   intermediate waypoints and **Confirm Route**. stairs/ramp select All Levels
   and retain actual click elevation. Turns and landings become real navigation
   nodes. The complete route commits as one undoable operation.
5. Save Draft, Validate and Generate. Pending previews cannot be generated.
   Reopen the saved map and add task details when the task is known.

Purple outlines show region prisms, yellow markers entities and pink markers
interaction poses. Walk edges are green, stairs orange and ramps blue. Floor
filtering inherits the owner/region floor. All Levels displays cross-floor routes.

**Editable Semantic Records** selects saved records for JSON editing and provides
entity, interaction pose and region templates. Stable IDs cannot be renamed in
place. Deleting referenced regions, entities, nodes or edges is rejected; deleting
a named route annotation retains the graph. Undo/redo and reload clear the record
editor to prevent stale text from overwriting restored data.

An interaction XYZ may be at tabletop height. Its optional `node_id` is a ground
approach waypoint on the same inherited floor, not the hand position. The optional
`heading_rad` is yaw in [-π, π]. Extra end-effector/task parameters belong in
`properties`; this authoring tool does not execute grasp actions.

## Synthetic two-floor example

Use the prepared Isaac/OpenUSD Python environment and a new output directory.
The generator creates two slabs, 20 solid treads, room annotations and a table
annotation without initial grasp poses. It is a synthetic demonstration asset.

```bash
python -m tools.topology_authoring.examples.two_floor_house \
  --output-root /tmp/semantic-house-demo
python -m tools.topology_authoring.author \
  --scene-key synthetic_house \
  --adapter tools.topology_authoring.adapters.usd_scene:UsdSceneAdapter \
  --asset-root /tmp/semantic-house-demo \
  --output-root /tmp/semantic-house-edited \
  --draft /tmp/semantic-house-demo/topology.json
```

`--draft` accepts a saved draft or product; `--headless` verifies reopening without
Kit. UI Load Draft reads `authoring/navigation/ground/topology.draft.json` below
the current output directory. Generate writes `topology.json` there. Reloading
the same scene source retains semantic maps; changed source digests are rejected.

To add a task pose, select New Interaction Pose and set a unique ID,
`owner_id: object:table`, `level_id: ground`, `node_id: living`,
`position_xyz: [-2, 0.5, 0.85]`, and optional `heading_rad: 1.57`.
Apply Record, then save/generate. This preserves nodes/edges/levels and the
navigation route graph digest while changing the full product digest.

## APIs and limits

`TopologyDocument` supports `upsert_semantic`, `remove_semantic`,
`replace_semantics`, `add_waypoint_route`, undo/redo and atomic persistence.
`semantic_inventory` lists annotations; `resolve_semantic` resolves ID/label/alias,
rejects ambiguity and filters by inherited floor. `point_in_region` tests the
annotated prism.

Navigation consumes semantic bindings before it selects a route. A region is
resolved through its entrance nodes; an entity prefers an owned interaction pose
and otherwise uses its region entrances; an interaction pose uses its optional
approach node; and a named route constrains selection to its ordered nodes and
edges. The runtime still follows the executable graph, so authors choose
waypoint spacing and ensure direct passage. Entity and interaction geometry can
be added later without changing the route graph identity; changing an entrance
or named route binding does change it.
The bundled USD adapter uses static triangulated collision support surfaces,
metres and Z-up. Support continuity is not swept robot collision/clearance
certification or proof that arbitrary stairs can be traversed.

Regression coverage is in `tools/validation/test_semantic_topology_authoring.py`.
For native callback, persistence and rendering verification, run:

```bash
python -m tools.validation.semantic_topology_editor_smoke \
  --asset-root /tmp/semantic-house-demo \
  --output-root /tmp/semantic-house-native-check
```

The panel screenshots render the actual UI builders in isolated native windows.
The procedure does not deliver OS mouse events or physically drive a robot.
See [the verification record](SEMANTIC_MAP_VALIDATION.md).
