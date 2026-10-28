# Decision fixture limitation

This independent single-robot live API probe is **not a physical integration input**.
The scene string declares native [x,z,y] but carries planar/height coordinates inconsistently with the UR5 physical workbench. This was noticed after launch; the launched input and result are preserved unchanged. Do not reuse this case as measured geometry or physical success. No executor is bound. A physical input must explicitly convert EAI [x,y,z] to native [x,z,y].
