# EAI City Demo（城市场景运行时子包）

> 本包原为独立目录 `demo/city/`，2026-09-19 合并为
> [`demo/city_benchmark/scene/`](../README.md) 子包：它是城市**场景运行时库**
> （事故序列、车辆/路人、Flow 效果、环境配置钩子），`demo/city_benchmark/`
> 的任务生成与审核层建立在其上。依赖方向：`demo.benchmark 核心 →
> demo.city_benchmark → demo.city_benchmark.scene`。本演示仍可独立运行。

Run the city accident through `simulator.py` / `open_simulator_session`, with
Go2, Carter, and Pegasus Iris using the existing EAI controllers. The registered
`modern_house_nvd` selection supplies the builder scaffold; a local environment
configuration hook replaces its world with the city. No global city registration
or additional application lifecycle is required.

## Run

From the repository root with the prepared Isaac Lab environment:

```bash
DISPLAY=:1 /home/pupil/miniconda3/envs/env_isaaclab/bin/python demo/city_benchmark/scene/main.py \
  --device cuda:0 --people 4 --seed 7 --frames 0 --loop --start-paused
```

The GUI opens on the accident camera after a 90-frame material/render warmup.
Click **Pause / Resume** in **City accident demo** to start. **Replay** restarts
traffic and effects; **Focus accident** restores the camera after free navigation.
`--frames 0` keeps the scene and Flow nodes alive until the window is closed;
`--loop` repeats the 20-second episode. Omit `--start-paused` to start automatically.
For seed 7: vehicle ignition at 2.5 s, wall impact near 6.8 s, building ignition
near 8.3 s. These are simulation seconds, shown in the demo panel.

In Stage, clear any filters and expand `/World/CityFlow`: its children are
`Fire` and `DenseSmoke`. Startup checks both Flow extensions, RTX enablement,
and the live emitter nodes; failure raises an explicit error.
`--capture-dir tmp/city_eai_demo/gui_check --frames 720` saves four images from
the actual GUI viewport and exits after a bounded run.

For a bounded headless run including Flow:

```bash
DISPLAY=:1 /home/pupil/miniconda3/envs/env_isaaclab/bin/python demo/city_benchmark/scene/main.py \
  --headless --frames 1300 --seed 7 --report tmp/city_eai_demo/scenario_report.json
```

The default scene has **6 background cars plus one incident car**. It uses the
SUV and hatchback exports; the sports-car export is excluded because its lamps
and trim contain inconsistent face winding. Complete material subsets are
explicitly declared as partitions in the session layer.

Background
cars are distributed across routes with staggered offsets and at least 18 m
initial spacing; a car is omitted if no spaced starting point is found. This is
an initial-placement constraint, not a continuous traffic collision guarantee.

The scene also includes **4 walking background people** by default. `--people N`
sets an upper limit from 0 to 8 (`--people 0` disables them). `people.py` reuses the
existing `HumanAssetRegistry`, installed `motions/cache`, and
`UsdHumanStageRuntime`; it does not add people to the robot/controller roster.
Four RPM/Synbody models use the existing `walk` motion. Short 6 m out-and-back
paths are taken from non-crosswalk sections of `city_scenario.json`; candidates
near building bounds, the incident approach, impact area, or robot starts are
excluded. If there are too few safe sections, fewer people are spawned.
Speeds are 0.65–0.8 m/s for the default four. Their collisions are disabled;
they are scenery actors with walking animation, not evacuation/task agents.

Walking humans support **GPU PhysX**. The demo honors `--device` regardless of
`--people`; `--device cuda:0` uses GPU physics and `--device cpu` selects CPU.
GPU rendering and Flow remain enabled.
Human USD and retarget caches must already be installed. All pose writes use
the session layer; the original human assets remain unchanged. Human paths reset
with the episode and advance using the same EAI simulation timestep.

Unless `--target-path` is supplied, seeded selection is restricted to the four
buildings whose centers are nearest the city building-envelope center. Default
seed 7 now hits approximately `(-45.05, 8.80)` instead of `(-89.36, 82.54)`.
`CityScenarioConfig.central_target_candidates=0` restores city-wide random
selection for batch generation, and `max_background_cars` controls traffic count.

Use `--no-flow` for physics/episode validation without Flow volumes. `--seed N`
reproduces the building and route selection. `--target-path /World/City/Building_...`
restricts planning to an existing visible building. Different plans have different
impact times; a very short frame limit does not exercise every accident phase.
Official presets are resolved from the repository-local `usd/effects/` root
(see `usd/effects/README.md`); `EAI_NVD_FLOW_ROOT` can still point at the
original NVD Flow download directory. Iris also creates its built-in sensor suite, including RTX LiDAR,
even when ROS publication is disabled.

## Lifecycle and boundaries

1. `env_cfg_hook` installs the ordinary `UsdFileCfg.func` callback.
2. During `InteractiveScene` construction, that callback composes the city,
   disables scenery rigid bodies, discovers usable buildings, seats buildings,
   plans against actual final wall geometry, normalizes/spawns 6 background
   cars plus one incident car, and composes one Fire and one DenseSmoke preset.
   It places the vehicles at their initial poses before PhysX reset.
3. `prebuilt.py` hands the scene to the runtime. The runtime rejects a missing
   handoff or a different stage; it never silently rebuilds a city after reset.
4. EAI resets robots and yields the session. Runtime updates only existing
   transforms/emitter attributes using `base_env.step_dt`, while the caller owns
   `env.step()` and application lifetime.
5. The report is written **inside the session, before Kit closes**. Both runtime
   errors and session errors are printed before cleanup can terminate the
   process. Check the JSON `status` and frame count, not merely shell exit code.

The earlier apparent shutdown defect was diagnosed using the complete logs:
the callback had executed. Reading only the log tail missed its marker, and
writing the report after context exit lost it when Kit ended the process during
close. The standard scene callback works; the experimental `pre_env_spawn_hook`,
hardcoded target envelope, and unnormalized lightweight cars were removed.

`scenario.py` is independent of USD/Kit and accepts routes, bounds, and an
injected wall probe. `usd_scene.py` adapts the actual geometry and assets.
`runtime.py` advances/reset episode state. Scene corrections and effects are
session opinions; source assets are never saved. The live report compares
SHA-256 hashes of the city, three vehicle assets, and enabled Flow presets.

Flow references the official presets, with explicit **metre/Z-up** session
attributes and an identity parent. `densityCellSize` is a world-unit setting
(0.1 m), emitter position/radius/velocity use metres, gravity points down world Z,
and ray-march/shadow attenuation is converted as inverse length. A centimetre
conversion parent cannot normalize FlowSimulate attributes. Fire and DenseSmoke
share the same position. Flow collision stays disabled. Reset requests GPU field
clear and leaves the request asserted until the caller advances the simulator.
The demo explicitly synchronizes Kit timeline time with each EAI step so Flow
receives time deltas. Timeline commits and screenshot renders disable automatic
physics stepping; EAI remains the PhysX owner. Because Isaac Lab's render callback
suppresses Kit playback, `forceSimulate` keeps Flow layers participating there,
while `simulateWhenPaused` is pulsed only for EAI steps and disabled for UI and
capture updates. GUI captures report native Flow
active-block counts, the renderer's actual camera matrices, instance-ID vehicle
pixel coverage, and actual/expected physics step counts. Bounded capture runs
also save close views of every vehicle as `vehicle_*.png`.

### Why fire can render invisible (balanced rendering preset)

Isaac Lab's `SimulationContext` applies the default **balanced** rendering
preset *after* command-line settings, forcibly disabling `rtx.translucency`,
`rtx.reflections` and `rtx.indirectDiffuse`. Flow's ray-marched volumes
composite through those RTX features, so with the preset active the accident
still collides and Flow still simulates (active blocks grow) while the
viewport shows no fire or smoke at all. Kit command-line flags cannot fix
this because the preset overwrites them. `configure_city_env_cfg` therefore
sets `env_cfg.sim.render.enable_translucency/reflections/global_illumination`
to `True`; `RenderCfg` values are applied after the preset and win.
`verify_live_flow` fails the run loudly if any of these features is disabled
again. Verified evidence: flame pixels 0 in every phase before the fix, and
117 at impact / 2253 at building fire after it, with identical physics step
counts.

The live demo currently disables conservative roadside AABB rejection because
these bounds have rejected valid routes. Wall contact is measured against actual
mesh triangles; this is a visual accident demo, not a guarantee of a globally
collision-free route. `CitySceneAdapter` retains optional obstacle validation
for task generation. Traffic motion is scripted; it is not a dynamic crash model.

## Validation

```bash
PYTHONPATH=source/EAI:source/EAI_assets:source/EAI_hmrs \
  /home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest -q demo/city_benchmark/scene/tests
```

The suite covers planner contacts, USD session edits, source preservation, Flow
units/reset, strict stage handoff, and report/error persistence before simulated
Kit process termination.

The central sparse default completed 700 live frames (14 seconds) with Flow,
7 vehicles, and all four phases: traffic, vehicle fire (2.5 s), impact (6.8 s),
building fire (8.3 s). Source hashes were unchanged. Seeds 7, 8, and 9 also passed
actual-USD planning and placement checks; initial background spacing exceeded
23 m in these cases.

Earlier dense-scene runs with and without Flow completed 1300 frames (26 seconds).
Three robots retained finite state and the drone stayed near its initial 3 m
altitude. These are bounded runtime checks, not a general ground/collision
certification. Flow USD readback and successful live stepping do not establish
rendered visual quality or prove GPU voxel clearing; those need viewport inspection.

The walking-people runs each completed 700 frames on CPU PhysX and GPU PhysX
(`cuda:0`) with 4 animated people, 7 vehicles, all three robots, and both Flow effects.
All accident phases completed;
each person reversed at the path endpoint and remained on its assigned sidewalk.
Offline two-second checks measured movement of 1.3/1.4/1.5/1.6 m, confirmed
1.68–1.88 m human heights, grounded feet, and unchanged source USD layers.

Runtime evidence is generated below `tmp/city_eai_demo/` and is not maintained
source. The latest GPU run uses `walking_people_gpu.json` and
`walking_people_gpu.log`; `walking_people.json` records the earlier CPU run and
`central_sparse.json` records the earlier run without people.
