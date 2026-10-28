# Editable semantic map: service_city_01

This directory is the scene-local editable map package for
`scene/Service and operations/city_01/city_01.usda`. Start the integrated launcher from the repository
root; it discovers this map automatically:

```bash
python tools/topology_authoring/launch.py --scene service_city_01
```

`project.json.current_revision` points to the fixed `current/` directory. Edit
through the map tool, which atomically replaces its complete contents on save
without accumulating revision folders. `base/` contains immutable geometry
reference grids. `evidence/` preserves earlier visual observations; it is not
USD content. Logical revision numbers still protect concurrent edits.
