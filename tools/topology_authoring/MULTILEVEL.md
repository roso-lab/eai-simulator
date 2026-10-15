# Levels, landmarks and node routes

New `TopologyDocument` products use `eai.navigation.topology` version 2.
Ground and aerial maps are separate products with identical schema. `domain`
selects editor defaults and scene geometry checks. A robot's registered
`NavigationProfile` determines runtime XY or XYZ geometry.

## Static USD input

Place a descriptor next to the scene assets:

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
  "support_mesh_paths": ["/Office/Floors", "/Office/Stairs"]
}
```

Optional `semantic_targets` bind external references. Labels and aliases may
also be authored directly on nodes. Optional `level_view_hide_paths` and
`level_view_cut_z_m` change the editing view only. The USD adapter accepts
static collision triangle meshes, rejecting moving or animated support.

```bash
python tools/topology_authoring/author.py \
  --scene-key office \
  --adapter tools.topology_authoring.adapters.usd_scene:UsdSceneAdapter \
  --asset-root /path/to/assets --output-root /path/to/package
```

Use `--headless` for validation without the editor, or `--domain aerial` for
XYZ placement and aerial corridor validation. OpenUSD is required by the USD
adapter; Isaac Sim is required by the interactive editor.

## Editing and route identity

Choose a level to filter nodes and project ground clicks to its support
surface. **All Levels** shows the full graph. The editor disambiguates stacked
nodes by XYZ and level. Level changes cancel unfinished previews. Levels are
optional authoring groups; they do not change routing distance or arrival.

Nodes contain XYZ, a stable ID, optional `target_refs`, label, aliases, level and
`heading_rad`. Typed poses, heading picks, undo/redo and save/load preserve this
information. Heading is normalized to radians. Edges contain endpoints and a
`directed` flag. Each required bend needs a node: runtime ignores polyline
bends. Ground ignores Z even for stacked or vertical links; aerial includes Z.
Map authors and controllers must provide a physically executable connection.

Traversal labels and controller capability metadata in existing products do
not affect Dijkstra or admission. New edges need neither. Duplicate bindings
are structurally readable; a request matching multiple nodes is ambiguous.
Exact node IDs take precedence over bindings. Use unique, level-qualified
references when appropriate.

`route_graph_digest` includes nodes, bindings, directed edges and semantic
metadata, ignores row ordering and decorative polylines, and is separate from
the complete `product_digest`. A frozen product binds the scene and source
digests. Replan uses the same bound graph; changing maps requires a new task.
Version 1 products remain readable with their original serialized version.

## Preview and execution

The editor accepts an exact reference or an unambiguous label/alias for preview.
Its `domain` hint chooses the preview metric. Programmatic callers pass an
explicit `NavigationProfile` to `resolve_landmark_route`; the CLI selects it
with `--metric xy` or `--metric xyz`.

```bash
python -m tools.topology_authoring.route --topology /path/to/topology.json \
  --origin lobby --destination conference --metric xy --output /path/to/route.json
```

Use `--origin-level` and `--destination-level` to disambiguate labels, and
`--list-landmarks` to inspect available names. Exported previews are inspection
artifacts. Runtime accepts only `SkillRequest(target_ref=...)`: the Provider
resolves the node and runs Dijkstra internally, checking the measured start.
Final heading applies only when the bound profile supports it.

`NavigationExecutor.prepare()` passes the current node to the registered
controller adapter. The host advances one shared physics tick, then
`after_step()` checks measured progress and stopping. The Go2 deployment
diagnostic uses this same path:

```bash
python -m tools.topology_authoring.deploy \
  --scene-key office --asset-root /path/to/assets --topology /path/to/topology.json \
  --origin lobby --destination conference --output-root tmp/office-route \
  --controller GO2_MIXED_STAIRS_CFG --device cuda:0
```

The controller owns gait and terrain traversal. Ground arrival uses XY and
optional yaw; chassis Z is recorded rather than treated as a floor test.
Posture checks stop a fallen robot. Native diagnostics record real velocity,
stop confirmation, source integrity and quarantine. A successful preview is
insufficient to establish physical traversal.
