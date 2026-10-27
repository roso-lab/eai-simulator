# Editable semantic map: production_consultation_room

This directory is the scene-local editable map package for
`scene/Production and logistics/consultation_room/consultation_room.usd`. Start the integrated launcher from the repository
root; it discovers this map automatically:

```bash
python tools/topology_authoring/launch.py --scene production_consultation_room
```

`project.json.current_revision` points to the fixed `current/` directory. Edit
through the map tool, which atomically replaces its complete contents on save
without accumulating revision folders. `base/` contains immutable geometry
reference grids. `evidence/` preserves earlier visual observations; it is not
USD content. Logical revision numbers still protect concurrent edits.
