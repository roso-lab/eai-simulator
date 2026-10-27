# Editable semantic map: living_house

This directory is the scene-local editable map package for
`scene/Living and public space/house/house.usda`. Start the integrated launcher from the repository
root; it discovers this map automatically:

```bash
python tools/topology_authoring/launch.py --scene living_house
```

`project.json.current_revision` points to the fixed `current/` directory. Edit
through the map tool, which atomically replaces its complete contents on save
without accumulating revision folders. `base/` contains immutable geometry
reference grids. `evidence/` preserves earlier visual observations; it is not
USD content. Logical revision numbers still protect concurrent edits.
