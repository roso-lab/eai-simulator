# Local benchmark scenes

The benchmark asset pack imported on 2026-09-20 is installed under `usd/scene/`,
`usd/assets/nvd/`, and `usd/demo/city/`. Keep these directories together: the NVD
scene wrappers reference `../../assets/nvd/`. These local assets have not been
published to the default Hugging Face release; a clean clone cannot download them
from `v0.1.0-beta.1`. Do not change the provider revision to run this local import.

From the repository root, activate `env_isaaclab` and run one command at a time:

```bash
conda activate env_isaaclab
python simulator.py --env benchmark_city
python simulator.py --env benchmark_warehouse_nvd
python simulator.py --env benchmark_restaurant
python simulator.py --env benchmark_modern_house_nvd
python simulator.py --env benchmark_factory
```

Each saved environment contains one Carter robot with its default differential
controller and an explicit physical spawn pose. The scene keys also appear in
the terminal, lightweight Env DIY, and 3D authoring catalogs.

| Environment | Scene key | Root layer below `usd/scene/` |
| --- | --- | --- |
| `benchmark_city` | `city` | `city/city_static.usd` |
| `benchmark_warehouse_nvd` | `warehouse_nvd` | `warehouse_nvd/warehouse_nvd_bench.usd` |
| `benchmark_restaurant` | `restaurant` | `restaurant/Restaurant_Only.usda` |
| `benchmark_modern_house_nvd` | `modern_house_nvd` | `modern_house_nvd/modern_house_nvd_bench.usd` |
| `benchmark_factory` | `factory_benchmark` | `factory_benchmark/factory.usd` |

The imported Factory is a separate variant from `factory`; existing Fire Rescue
assets are preserved. The restaurant uses centimeter units, so its spawn scale
is `0.01`; its visible meshes receive static triangle collision APIs in the
runtime stage. The NVD benchmark wrappers already convert centimeters to meters
and must not be scaled again. Source USD files are not rewritten by these configs.

City is the static city scene. Traffic vehicles and signals are installed under
`usd/demo/city/`, but this launcher does not animate traffic or run the scenario
JSON. City and restaurant do not include navigation maps. The shared asset CLI
exposes the imported maps for `warehouse_nvd`, `modern_house_nvd`, and
`factory_benchmark`; it does not synthesize missing maps. NVD collision proxies
and the house's unsupported elevated navigation retain the original asset pack's
limitations; launching a scene is not benchmark mission acceptance.

Local migration and validation records are under `tmp/benchmark_import_20260920/`.

The saved `benchmark_warehouse_orsus_iris` selection adds Carter with Orsus stereo
and point cloud plus Iris with RGB. Its five-waypoint shared-physics experiment defaults to 16 slots;
experiment is documented in
[`warehouse_branches.md`](../../../demo/fire_rescue/branching/docs/warehouse_branches.md).

## Validation on 2026-09-20

Using `/home/airs/miniconda3/envs/env_isaaclab/bin/python`, the following checks
passed:

```bash
python tools/validation/check_scene_map_assets.py
node tools/validation/check_env_diy_runtime.mjs all
python simulator.py assets list --format json
```

All five JSON selections round-trip through the shared parser with their physical
spawn poses intact; their local asset requirements are `READY`. The 2,369 moved
files (11,814,068,243 bytes) retained their sizes and inodes during the same-volume
move. The generated menu thumbnails show maps or projected layouts, not rendered
screenshots.

The local validation script was run separately for each environment:

```bash
EAI_ASSETS_AUTO_DOWNLOAD=0 python -u tmp/benchmark_import_20260920/smoke.py benchmark_city
EAI_ASSETS_AUTO_DOWNLOAD=0 python -u tmp/benchmark_import_20260920/smoke.py benchmark_warehouse_nvd
EAI_ASSETS_AUTO_DOWNLOAD=0 python -u tmp/benchmark_import_20260920/smoke.py benchmark_restaurant
EAI_ASSETS_AUTO_DOWNLOAD=0 python -u tmp/benchmark_import_20260920/smoke.py benchmark_modern_house_nvd
EAI_ASSETS_AUTO_DOWNLOAD=0 python -u tmp/benchmark_import_20260920/smoke.py benchmark_factory
```

Each completed 90 environment steps on `cuda:0` with one Carter. Final horizontal
drift was below 3 mm; city settled at road height 0.2 m, and the other four settled
near floor height 0 m. Restaurant uses the verified aisle spawn `(2, 8, 0.25)`.
The script uses `open_simulator_session` with the saved selection; it does not
exercise the interactive GUI or normal launcher's separate preflight subprocess.

The first city run with camera rendering enabled was stopped after about five
minutes without completion. Imported assets also emit legacy material/instance
warnings (including warehouse instance-scale warnings) under Isaac Sim 5.1.
Complete visual rendering and full-scene collision coverage have not been
validated. These short startup checks establish loading and the configured robot
spawn, not navigation or benchmark mission success.
