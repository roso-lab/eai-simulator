# Pitfalls hit during the factory conversion (root cause -> fix)

Each entry was a real bug that produced wrong output while intermediate
checks passed. When a Phase 3 gate fails, match the symptom against this
list first.

## Symptom: scene loads but everything is tilted / lying flat / jumbled
- **Root cause:** 7-value quaternion `<pose>` parsed as 6-value RPY by
  sdformat9. `1 0 0 0` becomes a 57.3 deg X tilt; every orientation in
  every file is wrong.
- **Why the checks missed it:** `gz sdf -k` is syntax-only; a probe that
  only counts models doesn't look at poses; any custom parser that reads
  quats will see exactly what you wrote.
- **Fix:** emit 6-value RPY (`quat_to_rpy` in the converter) and add the
  physics pose readback gate (`scripts/verify_poses.py`) to the battery.

## Symptom: scale wrong (walls hundreds of meters, objects huge)
- **Root cause:** scale placed only in the include pose (which cannot hold
  scale) or cancelled out by baking relative to the full top transform.
- **Fix:** bake per visual with `B = Mw · T(−t_top) · R_include⁻¹` so the
  top prim's scale survives into `<mesh><scale>`. Include pose keeps only
  translation+rotation from `rigid_part()`.

## Symptom: some instances at wrong positions/rotations, others fine
- **Root cause:** include pose rotation extracted with
  `ExtractRotationQuat()` (polar decomposition of a scaled matrix) while
  visual baking stripped the row-normalized rotation -- the two rotations
  disagree on any non-uniformly-scaled, rotated instance.
- **Fix:** one single helper (`rigid_part`) provides the (quat, translation)
  used by the include pose AND the inverse rotation used by baking.

## Symptom: everything mirrored/rotated 90 deg, small instances misplaced
- **Root cause:** Shepperd's matrix->quaternion formulas applied to the
  Gf row-major matrix without transposing -- produces the conjugate
  rotation.
- **Fix:** transpose first (`mat3_to_quat` in the converter); unit-test
  against `Gf.Rotation.SetAxisAngle(...).GetQuat()` with random axes.

## Symptom: garbage triangles / dangling geometry in rendered meshes
- **Root cause:** `<triangles count>` written as index pairs / 2 instead of
  / 6 (two indices per corner). assimp reads past the end of the `<p>`
  array.
- **Fix:** `count = len(indices) // 6`.

## Symptom: gazebo MeshManager segfaults on the generated DAE
- **Root cause:** `<diffuse>` written directly under `<profile_COMMON>`
  without the `<technique sid="common"><lambert>` wrapper.
- **Fix:** wrap effect parameters correctly (see `write_collada`).

## Symptom: disabled instances appear (or expected ones are missing)
- **Root cause:** USD scene authors disable instances via `active=false`
  overs. A plain `Usd.PrimRange(prim, Usd.PrimDefaultPredicate)` filters
  the prims themselves but `Usd.PrimDefaultPredicate` is an *instance* --
  calling it like a function (`Usd.PrimDefaultPredicate()`) makes the range
  accept everything; and a bare predicate does not always prune subtrees
  the way `stage.Traverse()` does.
- **Fix:** explicit `effectively_active(prim)` ancestor check in the mesh
  walk (see converter), plus payload-group keying that includes the
  rounded top-level scale.

## Symptom: instances grouped by payload end up at wrong transforms
- **Root cause:** grouping assumes every instance of a payload has the same
  internal transform chain. Some scenes author per-instance overrides on
  *nested* prims (not just the top prim), which breaks the shared-bake
  assumption.
- **Fix:** the vertex-level bbox gate (Phase 3, gate 3) exposes this as a
  mismatch. If it fires, split the group per instance and bake each one
  separately.

## Symptom: world loads but a `<include><uri>sun</uri>` is missing
- **Root cause:** exporting `GAZEBO_MODEL_PATH` shadows the default model
  database path, so built-in models are no longer found (silently).
- **Fix:** emit an explicit directional `<light>` in the world instead of
  including `sun`.

## Symptom: DAE loads in Blender but scene tilted 90 deg there (not in Gazebo)
- **Root cause (Blender):** the OBJ importer's Y-up->Z-up conversion is an
  object-level transform; `transform_apply` bakes it into vertices before
  the SDF pose matrix is assigned.
- **Fix:** never `transform_apply`; overwrite `matrix_world` directly
  (`scripts/blender_render_check.py` does this).

## Symptom: verification harness hangs even for an empty world
- **Root cause:** embedding `gazebo::setupServer()` in a standalone C++
  binary hangs in some environments.
- **Fix:** use real `gzserver` + world-probe plugin output (see
  `verify_poses.py`).

## Symptom: second gzserver exits instantly with an empty log
- **Root cause:** leftover gzserver holds master port 11345; the new one
  dies before flushing its buffer.
- **Fix:** `pkill -9 -x gzserver` before each run; poll the log file for
  `PROBE_LOADED` instead of waiting for process exit.
