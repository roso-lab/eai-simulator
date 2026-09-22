# Restaurant benchmark authoring scene

This package is the fifth goal-benchmark scene integration. The current phase
registers the local restaurant USD package with the shared selection and dynamic
environment builder, preserves its centimetre-authored source layers, and exposes
a read-only source audit.

The source package is runtime data and remains ignored by Git. Point the separate
development worktree at the supplied asset tree:

```bash
export EAI_USD_ROOT=/home/pupil/eai-simulator/usd
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  /home/pupil/miniconda3/envs/env_isaaclab/bin/python \
  -m demo.restaurant_benchmark.prepare
```

The scene source declares `metersPerUnit=0.01`; `RESTAURANT_CFG` therefore mounts
it with a `0.01` scale in the metre-based simulator. The source audit currently
separates a successful authoring preflight from benchmark readiness. Missing
composition dependencies, the absence of an occupancy map, and the scene's sparse
collision declarations keep `benchmark_ready=false`, `execution_validated=false`,
and `scoreable=false`.

The scene-only preset is `restaurant_scene`:

```bash
EAI_USD_ROOT=/home/pupil/eai-simulator/usd \
  python simulator.py --env restaurant_scene
```

A bounded native diagnostic writes its report below `tmp/`:

```bash
EAI_USD_ROOT=/home/pupil/eai-simulator/usd \
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  /home/pupil/miniconda3/envs/env_isaaclab/bin/python \
  -m demo.restaurant_benchmark.native_probe \
  --output-root tmp/restaurant_benchmark/native-smoke --device cpu --steps 8
```

Two current-source CPU diagnostics have now completed through the shared
`open_simulator_session(...)` boundary:

- The headless run created `/World/Restaurant`, reset the shared environment,
  reached ten total physics steps, and shut down cleanly. Its report SHA-256 is
  `6f9d16a4f605b28821214888bd4a6ce3a1d1897d76ef7c4c19ee9c4c89424874`.
- The GUI run reached 34 total physics steps and captured a 1280 x 720 author
  review image. The report SHA-256 is
  `c4acb32344739c9616245a9fed8582af93ad3e7776d8a3410bb949d283b41317`;
  the PNG SHA-256 is
  `3d16c1f58ac303223bb6ca395b17efeb02334d119c97a706d4362caff3ade1d0`.
  The image visibly contains the restaurant interior, including tables, chairs,
  bar surfaces, and the multi-level structure.

The GUI probe temporarily hides only
`/World/Restaurant/assemblies_context/assembly_BaseBuildingL0/CeilingL0` for its
author-review camera. The report records that path and labels the capture
`author_review_camera_not_agent_observation`; this visibility change is not part
of the runtime scene contract.

Both diagnostics have scope `scene_load_step_and_optional_author_view_only`.
They deliberately keep execution, business-outcome, and scoreability flags
false. Generated reports and pixels live below `tmp/restaurant_benchmark/` and
must not be committed.

The source audit found 95 composed layers, 349 asset dependencies, 56 unresolved
dependency references, and only two collision-enabled floor prims. The visual
result confirms that a useful render can be produced, but it does not close the
asset, collision, navigation, or task-execution gates.

Before adding a formal Goal task, complete and verify the local asset closure,
author conservative collision and occupancy products, calibrate robot spawn and
task regions in native coordinates, and capture a fresh runtime report plus GUI
pixels. A visual launch alone is not execution evidence.
