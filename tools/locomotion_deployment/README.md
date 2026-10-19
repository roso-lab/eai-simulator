# Portable G1 / Go2 deployment handoff

The model branch now contains runnable policy exports, observation adapters,
robot/actuator configs, opt-in HeteroCoord ControllerCfg objects and the requested
Go2 source files. All relative paths resolve from this checkout. Original
checkpoints and fixed-batch Go2 export remain unchanged.

## Commands (run at the repository root)

Use the existing Isaac Lab 2.3 / Isaac Sim 5.1 Python environment. Export/verify
need torch, onnx, onnxruntime and numpy; playback additionally needs Isaac Lab.
The full Go2 training-source geometry module additionally uses scipy.

```bash
python tools/locomotion_deployment/export.py
python tools/locomotion_deployment/verify.py
python tools/locomotion_deployment/check_adapters.py
python tools/locomotion_deployment/check_isaac_imports.py
python tools/locomotion_deployment/play.py --robot g1 --num-envs 2 --steps 250 --headless --device cpu
python tools/locomotion_deployment/play.py --robot go2 --num-envs 2 --steps 250 --headless --device cpu
```

For assets outside this checkout, pass `--asset-root /path/to/usd` to playback.
Default output is `logs/deployment_play.json`; use `--output` for separate results.
Remove `--headless` to display playback. No video is recorded by this example.
The standalone headless worker flushes its result and exits without Kit teardown,
because native Kit close can hang for CPU multi-articulation scenes on this build.

## Checks performed

- G1 ONNX and TorchScript vs reconstructed checkpoint, batches 1/4/32,
  seven sequential history updates and partial resets; independent per-row
  inference compared with batches. `verification.json` records measured errors.
- G1 reconstruction vs the original training DwaqInference + ActorCritic_DWAQ:
  30 cases, batches 1/4/32, exactly equal output (`g1_training_parity.json`).
- Go2 dynamic-batch ONNX vs original fixed-batch ONNX and TorchScript,
  batches 1/4/32; dynamic vs fixed ONNX outputs were equal in these tests.
- Go2 SDK ordering, observation slices, same-step cache, yaw-free history,
  partial resets and rejection of missing height hits (`adapter_verification.json`).
- Full source and opt-in config imports after Isaac startup
  (`isaac_import_verification.json`). The Go2 adapter was additionally compared
  against the full training observation class for eight steps, batch=4 and
  partial reset. Ordering/history output matched exactly on a flat-height-scan
  fixture; this does not compare training heightfield sampling with scene raycasting.
- Actual repository USD physics playback, two robots per test, 250 control steps
  (5 seconds), on CPU. G1 action shape [2,29], 53 USD joints including 24 fingers,
  187 diagnostic rays. Go2 action shape [2,12], 132 rays. All values finite and
  no missing rays; all four robots remained upright in these bounded runs.
  See `g1_play_result.json` and `go2_play_result.json` for actual displacement and
  worst projected gravity. An initial overlapping-spawn bug was fixed before
  these final measurements; clones are separated before physics initializes.

These are export/integration checks, not renewed stairs/forest/rough-terrain
acceptance tests. G1 model_287 retains its previously documented limitations.

## HeteroCoord integration

Packages are under `source/EAI_assets/EAI_assets/controller/g1_dwaq` and
`.../go2_extreme_parkour`. After Isaac startup import their `integration` modules
to obtain `G1_DWAQ_CFG` and `GO2_EXTREME_PARKOUR_TEACHER_CFG`. Match their robot
configuration, physics/control dt and sensor names before registration. Do not
attach the policies to unrelated default robot gains or infer joint order from
USD traversal. Call controller reset at episode reset and policy switches.

G1 is a stateful three-input policy; use the included ControllerCfg/adapter,
not the generic single-input RSL ONNX loader. Go2 uses the existing RSL loader
with the new dynamic ONNX path. The deployment bundle intentionally does not
change the global G1 default, Go2 dual-version registry or downstream profile
documents: those integrations can now be completed against explicit contracts.
