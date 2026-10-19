# Editable semantic map: production_warehouse_02

This directory is the scene-local editable map package for
`scene/Production and logistics/warehouse_02/warehouse_02.usda`. Start the integrated launcher from the repository
root; it discovers this map automatically:

```bash
python tools/topology_authoring/launch.py --scene production_warehouse_02
```

`project.json` points to the current complete revision. Edit only through the
map tool, which writes a new revision atomically. `base/` contains the immutable
geometry grids used by every revision. `evidence/` preserves historical visual
observations referenced by the migrated revision; it is not USD content.
