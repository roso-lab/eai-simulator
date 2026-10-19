# Editable semantic map: living_room_01

This directory is the scene-local editable map package for
`scene/Living and public space/room_01/room_01.usda`. Start the integrated launcher from the repository
root; it discovers this map automatically:

```bash
python tools/topology_authoring/launch.py --scene living_room_01
```

`project.json` points to the current complete revision. Edit only through the
map tool, which writes a new revision atomically. `base/` contains the immutable
geometry grids used by every revision. `evidence/` preserves historical visual
observations referenced by the migrated revision; it is not USD content.
