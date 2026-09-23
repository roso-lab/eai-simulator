# Asset Import Tools

[Chinese](README.zh-CN.md)

Run these commands from the repository root. They install resolver-managed runtime assets below `usd/`; generated production assets remain ignored by Git.

## Benchmark scene dependencies

`audit_usd_closure.py` checks relative USD dependencies, MDL resources and UDIM
tiles without starting Isaac. `relocate_usd_closure.py` installs the reachable
dependency tree, preserves source files and records hashes and explicit repairs.
`benchmark_asset_repairs.json` contains the known source-package path repairs.
See [the three benchmark demos](../../demo/benchmark/README.md) for commands,
resource layout, metre/Z-up wrapper preparation and local bundle distribution.

The closure audit also follows `customLayerData.metricsAssembler.*.write_layer`
strings. A `metrics:...metricsAssembler` URI is a Kit dynamic layer, not a
downloadable file or an automatically allowed runtime module. For materialization,
capture its actual content in the simulator session first, then pass the captured
USD as an explicit relocation alias. The relocator updates both sublayers and
the associated metadata. Verify authored units, transforms, geometry and physics
before and after, and retain the source backup and capture report.

The local Factory configuration repairs on 2026-09-16 put Iris material files
under `usd/robot/pegasus/materials/` and MuSHR's captured unit layer under
`usd/robot/mushr_v2/layers/units_adjust.usda`. These are ignored production assets;
copy their whole audited closures when distributing a local bundle. Their capture,
download, relocation and original-file backups are in `tmp/factory_benchmark/`.
This local repair does not update the remote release asset revision.
Pegasus's optimized asset also restores the original non-colliding antenna visual
mesh because simplification left invalid material-subset indices. The source hash,
unchanged transform and non-mesh-content checks, and original optimized-asset backup
are retained in `tmp/factory_benchmark/pegasus_visual_repair/`.

## Restaurant benchmark products

`build_restaurant_products.py` derives a metric Restaurant wrapper, a tri-state
ROS occupancy map, and static collision proxies from the same composed source
triangles. It requires the source entry and effective closure to match the
policy in `demo.restaurant_benchmark.config`, writes only to an explicit output
directory, and labels every bundle `local_candidate`. The checker binds the
current generator and source hashes and requires exact occupied-cell equality
between the PNG and USD proxy inventory.

```bash
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  python -m tools.assets.build_restaurant_products \
  --source-usd "$EAI_USD_ROOT/scene/restaurant/Restaurant_Only.usda" \
  --output-dir tmp/restaurant_benchmark/products-candidate
```

Generated Restaurant products remain runtime evidence below `tmp/`; do not
commit or publish them until native physics, navigation, and business-episode
gates have passed.

## NVD benchmark products

`rebuild_nvd_products.py` regenerates ground occupancy, drone voxels and collision
proxies from native-instance bounds and mesh geometry. It requires standalone
OpenUSD, NumPy, PyYAML and Pillow, and does not start Isaac. Run the default dry
report before `--apply`; apply overwrites the PNG, NPZ, bench USD and
`bench_meta.json`, so retain an asset backup first.

```bash
python -m tools.assets.rebuild_nvd_products --scene warehouse_nvd
python -m tools.assets.rebuild_nvd_products --scene warehouse_nvd --apply
python -m tools.assets.rebuild_nvd_products --scene warehouse_nvd --check-ground
python -m tools.assets.rebuild_nvd_products --scene warehouse_nvd --check-provenance
python -m tools.assets.rebuild_nvd_products --scene modern_house_nvd --check-ground
python -m tools.assets.rebuild_nvd_products --scene modern_house_nvd --check-provenance
```

Applying always recreates `/World/BenchCollision/zz_ground` as a static Z-axis
`Plane` with `PhysicsCollisionAPI`, at z=0 in the metre/Z-up wrapper. This makes
the repair for unstable wheel contact on the old thin Cube reproducible. Other
`zz_*` plates and mezzanine maps are preserved. Generated walls use exact occupied
cell rectangles; even wall indices above 999 are removed before regeneration.
Modern House tree point instances contribute trunk/bark geometry only to the
ground-band proxies; grass/flowers and high branches do not become invisible
obstacles. Wall heights follow source geometry in 0.05 m steps up to 0.8 m.
Audited physical families (seating, tables, lamps, bollards and the display
bike) receive `PhysicsCollisionAPI` and convex-decomposition collision directly;
structural meshes use Cube proxies, while glass, foliage and small decorative
fragments are ignored. The audit counts and reasons are stored in
`bench_meta.json`. Native-collision meshes are excluded from generated voxel
cubes to prevent duplicate, oversized colliders. Positive and below-ground
voxel proxies retain the map's native XY resolution rather than expanding
occupied cells fivefold.
`--check-ground` reads USD without payload loading or rebuilding products and
checks plane type, collision, static ancestry and world transform. It verifies
the authored contract only; simulator navigation/contact acceptance and provider
publication remain separate steps. Generated assets stay ignored by Git and must
be published together at an immutable provider revision.

Every apply writes an `eai.nvd-products.provenance.v1` record to
`bench_meta.json`. `--check-provenance` is read-only and binds the complete
provider USD/MDL/texture reference closure, bench USD, PNG, YAML and NPZ bytes
to the generator version, rasterization settings and shared map/voxel frame.
The record stores an aggregate closure digest rather than hundreds of file
records. It is deliberately
`publication_status=local_candidate` with a null provider revision. Publishing
the files requires a separate clean-checkout verification and a new manifest
that names the immutable provider revision; this local record is not release
evidence. The Warehouse and Modern House `--check-assets` commands run this
gate against the same resolver-selected USD root that the demo will launch.

The rebuild also normalizes `/World/BenchCollision` itself to an imageable
`Xform` and authors `visibility=invisible` on it. The proxy layer is
physics-only: sensor cameras and the viewport must see the real NVD props, not
the collision cubes. Visibility authored on the legacy untyped group prim was
silently ignored by RTX and inherited by nothing, which is why earlier
`--check-assets` runs passed while grey cubes still rendered;
`demo/benchmark/application.py` therefore gates scenes on an `Xform` proxy root
with authored `invisible`, and PhysX collision stays enabled either way.

## RMUC 2024

The importer verifies the certified Gazebo STL and PGM hashes, parses the SDF and map YAML contracts, converts the STL with Isaac Lab's mesh converter, and installs the wrapper USD, triangle-mesh collision asset, occupancy map, provenance, license, and Env DIY thumbnail.

```bash
conda activate env_isaaclab
python tools/assets/import_rmuc2024.py --source-repo /home/pupil/gazebo-sim
python tools/assets/import_rmuc2024.py --source-repo /home/pupil/gazebo-sim --check
```

Conversion starts Isaac Sim headlessly and can take several minutes. `--check` is read-only and does not start Isaac Sim. Run `python tools/assets/import_rmuc2024.py --help` for alternate source, repository, and Isaac Lab roots.

The installed provider paths are:

```text
usd/scene/rmuc2024/rmuc2024.usda
usd/scene/rmuc2024/rmuc2024_mesh.usd
usd/scene/rmuc2024/rmuc2024_map.yaml
usd/scene/rmuc2024/rmuc2024_map.png
usd/scene/rmuc2024/source.json
usd/scene/rmuc2024/LICENSE.Apache-2.0.txt
```

A release requires publishing these exact runtime files to an immutable `rosolab/eai-simulator-assets` revision and verifying that revision from a clean checkout.

## SimpleCar and OCTAGON-AIO-C01

The importer pins Gazebo commit `772445543473089ee8242771e1d946402162fe04`, verifies every designated `tmp/` document/STEP hash, converts the STEP with Isaac Sim's bundled HOOPS converter, imports the Gazebo-parity robot URDF, and writes `usd/robot/simple_car/source.json` with generated hashes and all fidelity limitations.

```bash
conda activate env_isaaclab
python tools/assets/import_simple_car.py --gazebo-repo /home/pupil/gazebo-sim
python tools/assets/import_simple_car.py --gazebo-repo /home/pupil/gazebo-sim --check
```

The conversion runs Kit in a child process because Isaac Sim's fast shutdown terminates its interpreter. The parent verifies the completion marker, atomically installs the generated files, creates the thumbnail and provenance manifest, then runs the read-only checks. Do not treat `tmp/` as a runtime dependency or publish the supplied manufacturer files without a license review.

Provider publication must include `usd/robot/simple_car/{simple_car.usda,gshub_core.usd,configuration/gshub_core_*.usd,simple_car.urdf,source.json}`, the shared sensor payload `usd/payloads/sensors/octagon_aio_c01/{octagon_aio_c01.usd,octagon_aio_c01_visual.usda,OCTAGON_AIO_C01.json,source.json}`, `usd/payloads/sensors/mid360/MID360.json`, and `controller/traditional/simple_car_planar/{__init__.py,simple_car_planar.py}` at one immutable revision. The importer de-instances the converted CAD and binds spec-derived materials (silver-white aluminium housing, black polycarbonate cover) inside `octagon_aio_c01.usd`.
