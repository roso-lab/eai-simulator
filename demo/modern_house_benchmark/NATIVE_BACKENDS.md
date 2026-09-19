# Native House backend integration

`native_adapters.py` reads an existing caller-owned Isaac session. It never
starts Isaac, changes physics device, steps physics, moves an actor or chooses
a route. Enable `env_cfg.sim.enable_scene_query_support = True` **before** scene
creation. CPU queries with `is_readback_suppressed()` true remain unknown; empty
or stale GPU-mode queries never mean visibility or clearance.

`HouseNativeSceneDetector` is an **ideal_scene_sensor**, not RGB recognition.
It uses the verified goal deployment and `read_goal_objects` to check composed
native source hashes and exact PhysX tensor body identities. Its observation
model is explicitly omnidirectional from the observer robot's current root:
an explicit range in `(0, 10]` metres and the nearest non-self ray hit toward
the object's current centre. All hits are collected; only exact live observer
body subtrees are excluded and their hits are retained in author provenance.
Only a hit on the verified object's subtree emits a claim.
An intervening robot/wall, an absent target surface or an out-of-range object
emits no identity claim. Centre-ray misses can conservatively omit partly
visible geometry. It does not attest RGB occlusion or image recognition.

No detector weights are needed. The binding is a known **public** object ID to
an explicit public item code, never a hidden outcome condition or a prim-name
guess. Only filtered claims may enter participant observations. Full native
object readback, rejected visibility candidates, physics snapshots and source
paths are author-only diagnostics and must not be copied into participant data.

`IsaacResidentPathBackend` queries a caller-supplied route with the existing
0.9 m × 2.0 m resident envelope, every idle robot/object included, plus continuous
static-cuboid floor coverage. It records raw overlap/sweep/support results and
all scene articulation link/rigid-object tensor transforms. An obstructed route
is a valid `measurement_status="blocked"` result, not a sticky backend failure.
Before measurements can therefore describe normal obstructed initial state.
For a blocked after measurement, `ModernHousePathReferenceProvider` retains the
measurement status but withholds the positive after-path claim: the existing
legacy evaluator cannot read 3-D native outcomes. Review `path.provenance()` for
the actual obstruction; lack of a positive after claim is not an unconfigured
backend. Invalid queries, unsupported support geometry, stale readback or
disabled queries are unknown and fail closed.

Frame publication must use `capture_native_observation(...)` or equivalent
trusted acquisition stamping. Pixel bytes/shape/dtype are checked against the
published frame digest, and episode ID, acquisition step, native simulation
time and current observer tensor pose must agree. Do not label a current
snapshot with an old frame's timestamp. The acquisition stamp does not attest
render-product freshness; the ideal sensor measures the current native scene.

## Direct in-session diagnostic

Run this **inside** the coordinator's already-open `open_simulator_session`
context after goal objects have been configured/spawned and the requested
bounded settling steps completed. `occupancy` is the House provider map;
`sensor_manager` is the existing camera manager for `agent`. Supply the
candidate route being reviewed as `points`; the helper has no default route.

```python
from demo.modern_house_benchmark.native_adapters import probe_native_backends

report = probe_native_backends(
    session=session,
    stage=stage,
    deployment=deployment,
    occupancy=occupancy,
    sensor_manager=sensor_manager,
    agent="iris_1",
    episode_id=run_id,  # caller's unique episode/run UUID
    item_codes={"resident_bookstack_01": "personal_book_stack"},
    max_range_m=5.0,
    points=points,
    output_path="/home/pupil/eai-simulator/tmp/house_native_services_run/native-report.json",
)
```

Use a fresh report path. This call has no second application or launch lock;
the coordinator must hold the canonical shared launch lock
`/home/pupil/eai-simulator/tmp/goal_physical_probe.lock` for its session.
The helper writes the full report in `finally` before returning from the live
session, including failures. It exercises both real reference-provider schemas
using a labelled diagnostic packet and one before observation. It does not
fabricate closeout, participant decisions, an after episode, or business success.
An empty ideal-sensor result and a blocked path are valid diagnostic outcomes.

## Business-provider injection

```python
from demo.modern_house_benchmark.domain_adapter import ModernHouseDomainEvidenceProvider
from demo.modern_house_benchmark.native_adapters import (
    HouseNativeSceneDetector, IsaacResidentPathBackend,
)

host = dict(session=session, stage=stage, occupancy=occupancy, episode_id=run_id)
detector = HouseNativeSceneDetector(
    deployment=deployment,
    item_codes={"resident_bookstack_01": "personal_book_stack"},
    max_range_m=5.0, **host,
)
path = IsaacResidentPathBackend(points=points, **host)
provider = ModernHouseDomainEvidenceProvider(
    public_task, detector_backend=detector, path_backend=path,
)
```

The normal domain adapter must then bind this provider to the **same**
`base_env`, `occupancy` and public task, forward stamped acquired frames, and
forward genuine scheduler closeout events. Archive `detector.provenance()` and
`path.provenance()` inside the session even on failure. Configured components
are `configured_unverified`; callback presence does not prove conformance.

The historical fixed-DAG runtime currently publishes unstamped frames and uses
legacy object IDs. It is deliberately **not** silently upgraded by this adapter:
goal deployment IDs must not be aliased to evaluator-private legacy IDs.
Coordinator integration must supply matching public/task identities and trusted
acquisition stamps. The separate `native_probe.py` now verifies two native
snapshots in a serial session and archives raw frames. Those snapshots do not
execute a household intervention or emit a fabricated closeout event. Full
House business/release readiness remains pending. Scoring is unchanged.

## Focused validation (does not start Isaac)

```bash
cd /home/pupil/eai-simulator
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH=.:source/EAI:source/EAI_assets:source/EAI_hmrs \
/home/pupil/miniconda3/envs/env_isaaclab/bin/python -m pytest -p no:cacheprovider -q \
  demo/modern_house_benchmark/test_native_adapters.py \
  source/EAI/test/test_benchmark_reference_domain_providers.py \
  source/EAI/test/test_benchmark_domain_adapters.py \
  source/EAI/test/test_benchmark_live_domain_collectors.py \
  source/EAI/test/test_benchmark_house_live_path.py
```

The new tests use real standalone OpenUSD native composition/readback and
injected PhysX I/O. Deployment compilation is bypassed only in that unit fixture
and is separately covered by the shared deployment tests. Tests establish adapter
behavior, not native Isaac execution or trained visual perception.
