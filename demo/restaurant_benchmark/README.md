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
it with a `0.01` scale in the metre-based simulator. The source audit separates
a successful authoring preflight and effective asset-closure observation from
benchmark readiness. The fixed source package still reports 56 raw unresolved
paths, but the versioned closure policy classifies all of them as inactive
composition references, stale metadata paths with valid defaults, or Isaac Kit
core MDL modules. The absence of an occupancy map and the scene's sparse collision
declarations keep `benchmark_ready=false`, `execution_validated=false`, and
`scoreable=false`.

The scene-only preset is `restaurant_scene`:

```bash
EAI_USD_ROOT=/home/pupil/eai-simulator/usd \
  python simulator.py --env restaurant_scene
```

A bounded native diagnostic writes its report below `tmp/`:

```bash
EAI_USD_ROOT=/home/pupil/eai-simulator/usd \
EAI_CONTROLLER_ROOT=/home/pupil/eai-simulator/source/EAI_assets/EAI_assets/controller \
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  /home/pupil/miniconda3/envs/env_isaaclab/bin/python \
  -m demo.restaurant_benchmark.native_probe \
  --output-root tmp/restaurant_benchmark/native-smoke --device cpu --steps 8
```

The controller root is required when this branch runs in a separate worktree:
the ignored controller bundle lives in the main checkout and must not be copied
or committed. A bounded Carter spawn and zero-command diagnostic uses
the same probe:

```bash
EAI_USD_ROOT=/home/pupil/eai-simulator/usd \
EAI_CONTROLLER_ROOT=/home/pupil/eai-simulator/source/EAI_assets/EAI_assets/controller \
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  /home/pupil/miniconda3/envs/env_isaaclab/bin/python \
  -m demo.restaurant_benchmark.native_probe \
  --output-root tmp/restaurant_benchmark/carter-spawn \
  --device cpu --robot carter --settle-steps 120 --steps 240
```

Three current-source CPU diagnostics have now completed through the shared
`open_simulator_session(...)` boundary:

- The headless run created `/World/Restaurant`, reset the shared environment,
  reached ten total physics steps, and shut down cleanly. Its report SHA-256 is
  `e729bf9daa84de721e2ee87311863286a32f0c405c8d714e23c0c2d5e0eae804`.
- The GUI run reached 34 total physics steps and captured a 1280 x 720 author
  review image. The report SHA-256 is
  `c4acb32344739c9616245a9fed8582af93ad3e7776d8a3410bb949d283b41317`;
  the PNG SHA-256 is
  `3d16c1f58ac303223bb6ca395b17efeb02334d119c97a706d4362caff3ade1d0`.
  The image visibly contains the restaurant interior, including tables, chairs,
  bar surfaces, and the multi-level structure.
- The Carter run spawned `carter_1` at `[15.0, 14.0, 0.20]` m, settled for 120
  control steps, then moved approximately `0.01194` m during a 240-step
  zero-command window with approximately `-4.47e-7` m vertical drift. Its report
  SHA-256 is
  `80c22d3572c342fb8c85b8a093d68d2be0492d39431009714aa83a749bcb4d23`.
  This is a spawn and zero-command observation. No acceptance threshold was
  frozen, so it does not validate support stability or navigation.

The GUI probe temporarily hides only
`/World/Restaurant/assemblies_context/assembly_BaseBuildingL0/CeilingL0` for its
author-review camera. The report records that path and labels the capture
`author_review_camera_not_agent_observation`; this visibility change is not part
of the runtime scene contract.

All diagnostics have scope `scene_load_step_and_optional_author_view_only`.
They deliberately keep execution, business-outcome, and scoreability flags
false. Generated reports and pixels live below `tmp/restaurant_benchmark/` and
must not be committed.

The source audit found 95 dependency layers, 349 asset dependencies, 56 raw
unresolved dependency references, and only two collision-enabled floor prims.
For source SHA-256
`618470672c40d18b93adbbcdecf69aea98931ebc4d5e1f8d13e7a77ee67e653b`, the
effective closure policy records 3 inactive structural references, 1 inactive
environment reference, 30 stale metadata texture paths with matching local
defaults, 19 inactive custom-data textures, and 3 Kit core MDL modules. It
reports zero effective unresolved dependencies only when the entry USD, the
normalized unresolved manifest, and all 65 used-layer path/content fingerprints
match policy. The visual and spawn results do not close the collision, navigation,
or task-execution gates.

The current schema-v2 audit report SHA-256 is
`f91a215b5feb9c339dccb5cbdf79123dccb50236f5f9baca346d1571887e2278`.

Before adding a formal Goal task, author conservative collision and occupancy
products from the same geometry, calibrate robot spawn and task regions in native
coordinates, validate PhysX raycasts and map connectivity, and run a real
navigation and business episode. A visual launch or stationary robot window alone
is not execution evidence.

## Local collision and occupancy candidate

`tools.assets.build_restaurant_products` is the Restaurant-specific product
builder. It never edits the 4.1 GB source package. It reads composed mesh
triangles from the policy-pinned entry USD, converts centimetres to metres, and
writes its candidate bundle to an explicit output directory:

```bash
PXR_USDC_EMIT_DEPRECATION_WARNINGS=0 \
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  /home/pupil/miniconda3/envs/env_isaaclab/bin/python \
  -m tools.assets.build_restaurant_products \
  --source-usd /home/pupil/eai-simulator/usd/scene/restaurant/Restaurant_Only.usda \
  --output-dir tmp/restaurant_benchmark/products-candidate
```

The fixed candidate frame is `[-0.5, -3.0]` to `[36.0, 26.5]` metres at
`0.05 m/cell`. Floor meshes declared by the builder form the known support
domain. Non-floor triangles intersecting the `[0.05, 1.00] m` Carter height
band form both the raw map occupancy and physical collision proxy mask. The
builder separately inflates that mask by `0.40 m` only for Carter connectivity
diagnostics, so downstream footprint checks do not double-count clearance. The
map writes free cells as 254, occupied cells as 0, and unsupported cells as 205
(unknown). The wrapper disables the two inherited overlapping floor colliders
and adds one static Z-up ground Plane.

`bench_meta.json` records `publication_status=local_candidate` and no provider
revision. It binds the frozen source entry SHA, unresolved dependency manifest,
used-layer manifest, generator version/source hash and all output hashes. The
builder also reconstructs the raw occupied cells from every authored proxy and
requires exact equality with the PNG before returning success. These products do
not change the current authoring scene gates. They remain unregistered runtime
candidates until static map/proxy correspondence, PhysX raycasts, and native
Carter navigation have all passed.

Recheck a generated directory against the current source and generator without
rewriting it:

```bash
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
  /home/pupil/miniconda3/envs/env_isaaclab/bin/python \
  -m tools.assets.build_restaurant_products \
  --source-usd /home/pupil/eai-simulator/usd/scene/restaurant/Restaurant_Only.usda \
  --output-dir tmp/restaurant_benchmark/products-candidate --check
```

The current source and generator produced the same bytes in two independent
output directories. Candidates v7 and v8 record 3,638 meshes (34 empty meshes skipped),
6,766,535 rasterized triangles, 89,051 raw occupied cells and 1,398 collision
proxies. The `0.40 m` Carter diagnostic leaves 195,364 free cells in eight
components; the largest contains 86.735% of all free cells. The maintained
candidate hashes are:

- wrapper: `13d58d212b13f744cbb7f822970ee37fbc9cfff5df894780d9946e9ee1c84521`
- map PNG: `946b44f46176393c3526953397dc9a3dfb57d7cc5085d8f4198b73743ec63adf`
- map YAML: `a111666d14597a0b2ede2f67b741c2bd75685d956c55d93769fb6ca4dbcf8e73`
- metadata: `dc3fe893959a5fd83fa9dc5a4b0861f1ffaf4bf2d776320d66a855a3f53b921a`

These hashes identify untracked local evidence below
`tmp/restaurant_benchmark/products-candidate-v7/`; they do not publish or register
the bundle. The isolated worktree does not contain the ignored Warehouse and
Modern House NVD bundles, so their installed-sidecar test cannot run there without
copying provider data. The Restaurant product tests and all other focused NVD
algorithm tests pass; provider-installed NVD validation remains an environment
check outside this candidate batch.

## Isolated candidate runtime

The raw authoring key remains `restaurant`. The separate
`restaurant_benchmark` key loads only a hash-checked local candidate selected by
`EAI_RESTAURANT_BENCHMARK_ROOT`; the candidate is not added to the provider asset
registry. Keep `EAI_USD_ROOT` pointed at the shared read-only asset checkout so
robot assets remain available, and point the Restaurant variable at the
generated directory in this worktree:

```bash
export EAI_USD_ROOT=/home/pupil/eai-simulator/usd
export EAI_CONTROLLER_ROOT=/home/pupil/eai-simulator/source/EAI_assets/EAI_assets/controller
export EAI_RESTAURANT_BENCHMARK_ROOT=$PWD/tmp/restaurant_benchmark/products-candidate-v7
python simulator.py --env restaurant_benchmark_scene
```

The product resolver rejects an unset directory, changed product bytes, open
release gates, a provider revision, or any snapshot whose metadata, product, or
bundle hash differs from candidate v7. This keeps runtime experiments away from
the original 4.1 GB source package and prevents a local candidate from being
mistaken for a released benchmark. The initial formation origin places the first
Carter at `(21.875, 20.875)` m, inside the largest component of the candidate's
`0.40 m` inflated connectivity diagnostic.

Run that bounded probe in a fresh output directory:

```bash
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
EAI_USD_ROOT=/home/pupil/eai-simulator/usd \
EAI_CONTROLLER_ROOT=/home/pupil/eai-simulator/source/EAI_assets/EAI_assets/controller \
  /home/pupil/miniconda3/envs/env_isaaclab/bin/python \
  -m demo.restaurant_benchmark.runtime_probe \
  --products-root tmp/restaurant_benchmark/products-candidate-v7 \
  --source-usd /home/pupil/eai-simulator/usd/scene/restaurant/Restaurant_Only.usda \
  --output-root tmp/restaurant_benchmark/native-runtime-fresh --device cpu
```

The parent validates the complete bundle before starting a separate Kit worker,
then the worker checks a Plane support ray, a horizontal obstacle-proxy ray, and
Carter drift under zero command. Passing this probe validates the static
collision query and stationary-drift scope only. It keeps wheel contact, overall
physics execution, navigation, business execution, and scoreability false.

The final source-bound CPU run is `native-runtime-v3`. It loaded 1,399 enabled
colliders (one Plane and 1,398 generated proxies), hit the Plane at
`z=-0.0000024 m`, and hit proxy `o00359` after `0.975 m` along the horizontal
test ray. Carter spawned at `(21.875, 20.875, 0.20) m`; after 120 settle steps,
its 240-step zero-command observation moved `0.0119324 m` with
`0.000000149 m` vertical drift. Both ray checks and the frozen drift limits
passed. The request, report, and Isaac log SHA-256 values are respectively
`686ed3c7801dc7b73c92e44e9104c07cd10413866b965322efa68a8f3623966f`,
`4e368e1aee8572e2fd22f60d4313fd7967622f041ac4fc2427e52649adbdcae0`,
and `6a7e3b46bf62a336a212d48d3b54036e78ed6c23febdaeb008b978b2078dab0e`.
These untracked files remain below `tmp/restaurant_benchmark/native-runtime-v3/`.
Earlier v1/v2 directories are investigation records and are not acceptance
evidence because their field semantics or terminal-report checks predate the
final source binding.

Candidate v7 is deliberately machine-local. Its wrapper stores a relative
reference from the generated directory to this machine's source checkout, so
regenerating the same geometry under a different checkout or output layout
changes the wrapper SHA and the exact-snapshot resolver rejects it. A provider
release must replace that layout-dependent reference with a portable packaged
closure and freeze new release hashes.
