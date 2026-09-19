---
name: usd-to-gazebo-convert
description: >-
  Convert an Isaac Sim USD scene (such as the EAI factory scene under
  usd/scene/) into a Gazebo Classic 11 asset that opens correctly:
  static model.sdf files, textured Collada meshes, an SDF world with one
  include per placed instance, plus a full validation battery. Use this
  skill whenever a USD scene must be viewable/simulatable in Gazebo.
---

# USD scene -> Gazebo Classic 11 asset

This skill packages the complete, validated workflow used to produce
`usd/scene/factory_gazebo/` from `usd/scene/factory/factory.usd`
(43 models / 73 instances / 141 meshes / 59 textures, physics-verified
pose readback, ~3 s world load, zero errors).

**Target platform:** Gazebo Classic 11 (Ubuntu 22.04, `libgazebo11`,
`libgazebo-dev`). The workflow was verified against exactly this stack;
several facts below are *specific to gazebo11's sdformat9 and assimp 5.2*
and must not be assumed for other versions without re-verifying
(see `references/gazebo11-verified-facts.md`).

## Prerequisites

```bash
# USD Python (no Isaac needed). Install with proxies unset if the proxy is down:
pip install usd-core numpy          # Python >= 3.11 has wheels
# C++ validation harnesses (one-time, ~10 s):
bash scripts/build_native.sh        # uses pkg-config gazebo; needs libgazebo-dev
# Optional visual gate:
#   Blender >= 4.x at any path (script takes it as an argument)
```

## Workflow

### Phase 0 — Verify platform facts before writing any converter code

Do not trust assumptions about coordinate axes or pose formats; each of
these was empirically wrong at least once during development:

1. **DAE axis handling of gazebo11** — load a probe DAE through
   `gazebo::common::MeshManager` (use `scripts/meshcheck.cpp`) and confirm
   coordinates come through raw. For gazebo11 they do (no up-axis
   conversion), so USD Z-up meters can be written to Collada unchanged.
2. **Pose grammar of sdformat9** — 6 values `x y z roll pitch yaw` only.
   The 7-value quaternion form is silently parsed as RPY and corrupts every
   orientation. Verify by loading a world with a probe plugin
   (`scripts/worldprobe.cpp`) and reading back `WorldPose()`.
3. Note the environment quirks in `references/pitfalls.md` (broken `gz`
   CLI versions, embedded `gazebo::setupServer()` hangs, leftover
   gzserver holding port 11345).

### Phase 1 — Analyze the USD scene (read-only, no Isaac required)

Open the stage with `pxr` (usd-core) and record:

- Up axis (`UsdGeom.GetStageUpAxis`) and `metersPerUnit`. If not Z-up/1.0,
  add an explicit conversion step and re-verify Phase 0 facts for the
  transformed data.
- Top-level placed instances under the scene root: their payload/reference
  targets, world transforms (esp. **non-unit or non-uniform scales**), and
  authored `active=false` prims (these must be excluded; the source scene
  may intentionally hide instances).
- Mesh inventory: triangle counts, UV primvar interpolation mix, authored
  normals, `GeomSubset` per-face materials.
- Material system: MDL (`info:mdl:sourceAsset`) vs `UsdPreviewSurface`.
  For MDL, the converter parses the MDL module text for parameter defaults
  and applies prim-level `inputs:*` overrides; texture parameters referenced
  with relative paths resolve against the MDL file location. Handle UDIM
  (`<UDIM>` -> first existing tile) and note `<UDIM>` files resolve no
  further automatically.

`scripts/usd_to_gazebo.py` prints most of this during conversion; run the
analysis snippets in `references/validation.md` when in doubt.

### Phase 2 — Convert

```bash
python scripts/usd_to_gazebo.py \
  --source usd/scene/factory \
  --scene factory \
  --out usd/scene/factory_gazebo
# useful flags:
#   --obj-out DIR   additional OBJ+MTL export (for Blender/other DCC checks)
#   --no-collisions skip box collision generation
```

Design decisions encoded in the converter (change only with a reason):

- **Grouping**: top prims sharing one payload file and one top-level scale
  become one Gazebo model with several `<include>`s. Instances with
  per-instance nested overrides that change internal transforms would break
  this assumption — the validation in Phase 3 catches it; if it fires,
  split the group (bake per instance).
- **Baking**: per-visual transform `B = Mw · T(−t_top) · R_include⁻¹`
  (row-vector convention), decomposed into pose + `<mesh><scale>`. The
  include pose is rigid-only; top-level scale must land in the visual
  scale. Rotation extraction uses row normalization, and quaternion
  conversion needs the transposed matrix in Shepperd's formulas. Getting
  any of these wrong yields 90°/mirror errors that only the Phase 3
  physics readback catches.
- **Materials**: albedo texture or color extracted per material; glass
  materials become transparent; pure-metal MDLs without local files fall
  back to flat colors. Emitted Collada must wrap `<diffuse>` inside
  `<technique sid="common"><lambert>` (assimp segfaults otherwise) and
  `<triangles count>` must equal index-pair count / 6.
- **Poses**: every `<pose>` is 6-value RPY (see Phase 0 fact 2).
- **Collisions**: axis-aligned bounding boxes padded to ≥ 2 cm thickness,
  in the visual's rotated frame. Static models. This keeps physics cheap;
  switch specific models to mesh collisions manually if a robot must
  interact with non-box geometry.

### Phase 3 — Validate (all gates, in order; a failure at gate N invalidates gates > N)

The controlling principle: **validate through the real consumer, never
through your own parser**. `gz sdf -k` is syntax-only and will happily pass
a world where every orientation is garbage (this happened).

1. `gz sdf -k world.sdf` and every `models/*/model.sdf` — syntax only.
2. Mesh loadability: run every DAE through gazebo's own MeshManager
   (`scripts/meshcheck.cpp`). Zero files may fail; spot-check that vertex
   counts/bboxes match the USD side (assimp may split seam vertices, so
   compare bounds, not counts).
3. Vertex-level world-bbox comparison: for every instance, compute the USD
   point-cloud AABB and the SDF-side AABB (DAE points x visual pose/scale
   x include pose) and require agreement within
   `0.01 m + 0.1% of size` (`scripts/validate_bbox.py`).
4. **Physics pose readback** (the gate that caught the production bug):
   inject `scripts/worldprobe.cpp` into a copy of the world, load with
   `gzserver`, and compare every model's `WorldPose()` against the USD
   ground truth: position ≤ 1 mm, orientation ≤ 0.2°. This is the only
   check that sees exactly what physics and the GUI see.
5. Visual gate (optional but recommended): render both the USD source and
   the converted scene in Blender headlessly
   (`scripts/blender_render_check.py`, Workbench TEXTURE mode) and compare
   overview/top images. The converted scene must be indistinguishable in
   geometry and layout.

Record all five gate results in the output README (see
`usd/scene/factory_gazebo/README.md` for the expected shape).

### Phase 4 — Handoff

Write the output `README.md`: how to open (`GAZEBO_MODEL_PATH` + world
path), directory structure, what was converted, known limitations
(box collisions, diffuse-only materials, UDIM tiles, light replacement),
regeneration command, and the validation table. Keep
`conversion_manifest.json` (instance poses + warnings) next to the world.

## References

- `references/gazebo11-verified-facts.md` — every platform behavior this
  workflow depends on, with the empirical method used to verify it.
- `references/pitfalls.md` — the concrete bugs hit during development
  (root cause + fix), useful when a gate fails.
- `references/validation.md` — ready-to-paste snippets for Phase 1
  analysis and the exact tool invocations for every Phase 3 gate.
- `usd/scene/factory_gazebo/` in this repository is the reference output
  produced by this skill; use it as a worked example.
