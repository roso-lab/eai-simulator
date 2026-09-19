# Gazebo Classic 11 verified facts

Every fact below was verified empirically during the factory conversion
(2026-09). If your gazebo/assimp versions differ, re-verify before relying
on them; the verification method is listed for each fact.

## Coordinate systems

- **Collada coordinates are loaded raw.** `gazebo::common::MeshManager`
  (via its assimp 5.2 path) applies no up-axis conversion: a vertex written
  as (0,0,2) comes back as (0,0,2) whether the DAE declares `Z_UP` or
  `Y_UP`. Verified by loading probe DAEs through MeshManager with a small
  C++ program (`scripts/meshcheck.cpp`) and inspecting the bounds.
  Consequence: USD Z-up meter data can be written to Collada unchanged;
  SDF poses use the USD world transforms directly.
- The USD scene itself must be Z-up meters
  (`UsdGeom.GetStageUpAxis == "Z"`, `GetStageMetersPerUnit == 1.0`); the
  converter warns otherwise. Gazebo worlds are Z-up meters.

## Pose grammar (the one that broke production)

- **sdformat9 (gazebo11) parses `<pose>` strictly as 6 values
  `x y z roll pitch yaw`.** The 7-value quaternion form `x y z qw qx qy qz`
  is a gz-sim/sdformat12 extension. On gazebo11 a 7-value pose is silently
  parsed as `x y z R P Y` using the first six numbers (the seventh is
  dropped): the identity quaternion `1 0 0 0` becomes a 57.3 deg tilt about
  X. `gz sdf -k` does NOT catch this (it validates syntax, not pose
  component counts).
- Roll-pitch-yaw here is extrinsic X-Y-Z, i.e. column-convention
  `R = Rz(yaw) · Ry(pitch) · Rx(roll)`.
- Verified by loading a world with a plugin that prints
  `WorldPose()` (`scripts/worldprobe.cpp`) and comparing against intended
  values. This is the only reliable check.

## Collada content requirements (assimp 5.2)

- `<diffuse>`/`<emission>`/`<transparent>` must live inside
  `<technique sid="common"><lambert>` within `<profile_COMMON>`. Emitting
  them directly under `profile_COMMON` segfaults gazebo's loader.
- `<triangles count>` is the number of triangles. With two input channels
  (VERTEX + TEXCOORD, two indices per corner) that is `len(<p> data) / 6`,
  not /4 and not /2. Getting this wrong makes assimp read past the index
  array and produce garbage geometry.
- Missing normals are fine: assimp computes them. USD authored normals are
  not required for the conversion.
- Textures: external images referenced from `<library_images>` +
  `<effect>` diffuse texture. Gazebo classic also binds OGRE material
  scripts whose material names match the DAE material names; the converter
  emits both paths (DAE textures for portability, `.material` scripts in
  `materials/scripts/` with textures in `materials/textures/` for the
  classic renderer).

## SDF structure semantics

- `<include><pose>` and `<visual><pose>` are rigid transforms only
  (translation + rotation). **Scale cannot be expressed in a pose**; it
  goes in `<mesh><scale>`. Non-uniform top-level scale composed with
  rotated children can produce shear that pose+scale cannot represent --
  detect it (row-orthogonality check in the converter) and split/bake
  differently if it fires.
- `<include>` requires `model://` URIs resolvable through
  `GAZEBO_MODEL_PATH`; each model directory needs a `model.config`.
- Referencing built-in assets (`<include><uri>sun</uri>`) depends on the
  default model database path; exporting `GAZEBO_MODEL_PATH` can shadow
  it. The converter therefore emits an explicit directional `<light>`
  instead of including `sun`.

## Transform math (USD conventions)

- USD/Gf use **row vectors**: `p' = p · M`, and nested transforms compose
  as `M_child_to_world = M_child_to_parent · M_parent_to_world`... in
  product order: `p_world = p_local · M_world_of_prim`.
- The correct per-visual bake that cancels exactly the rigid part of the
  top prim is `B = Mw · T(−t_top) · R_include⁻¹` (row-convention matrix
  products; `Mw` = mesh local-to-world). Applied left to right to the
  point: world transform, then un-rotate, then un-translate.
- Rotation extraction from a matrix carrying scale: normalize the ROWS of
  the linear part (row vectors are the scaled basis vectors). Do NOT use
  `ExtractRotationQuat()` for this -- on a scaled matrix it performs a
  polar extraction and returns a *different* rotation than row
  normalization, and the include pose and visual bake then disagree.
- `Gf.Matrix3d` -> quaternion: Shepperd's formulas are column-convention;
  feed them the transpose of the Gf row-major matrix or you get the
  conjugate (inverse) rotation for every non-symmetric rotation.
- Matrix -> quaternion inverse/transpose errors are subtle: identity and
  180 deg rotations survive, everything else breaks. Only a numeric
  roundtrip test (compose back and compare vertex positions) is trusted.

## Environment quirks (this machine / common setups)

- The system `gz` CLI may be gz-tools2 from a newer stack: `gz sdf` works,
  but `gz model`/`gz topic` (gazebo-classic transport commands) return
  "Invalid arguments". Don't use them to check world state; use the probe
  plugin.
- Embedding `gazebo::setupServer()` in a standalone C++ test can hang even
  for an empty world depending on the environment. Loading through real
  `gzserver` + probe plugin output is reliable.
- A leftover `gzserver` process holds the master port (11345) and makes
  the next one exit instantly (often with an empty log). `pkill -9 -x
  gzserver` between runs.
- `gzserver` stdout/stderr can be lost when the process is killed hard
  (block buffering). Poll the log file for the probe line instead of
  expecting a flush on exit.
