# Validation tools

[中文说明](README.zh-CN.md)

`tools/validation/` contains repository, configuration, asset, and optional
runtime smoke checks. It is a quality gate around the benchmark; it is not a
task evaluator and it does not replace `task/evaluators/`.

## What it checks

The checks may cover:

- repository and documentation consistency;
- asset-preflight and provider error reporting;
- scene/map ownership and resource declarations;
- ROS distribution and setup configuration;
- Env DIY or interface compatibility contracts;
- robot/attachment/URDF/USD and selected physics smoke probes.

A passing check proves only the named contract. It does not prove that Isaac
Sim, GPU, ROS2, external assets, a controller, or a multi-robot task will work
end to end.

## Boundary with task evaluation

```text
tools/validation/
    Is the repository/configuration/input admissible?

task/evaluators/
    Did the physical task satisfy its milestones?
```

For example, `check_scene_map_assets.py` checks that map declarations and asset
ownership are consistent. A task evaluator must still decide whether the robot
reached the target, inspected the hazard, or completed extinguishing.

## Lightweight checks

Run the applicable checks from the repository root:

```bash
python tools/validation/check_asset_download_errors.py
python tools/validation/check_documentation_consistency.py
python tools/validation/check_env_diy_exclusivity.py
python tools/validation/check_release_links.py
python tools/validation/check_ros_distro_config.py
python tools/validation/check_scene_map_assets.py
node tools/validation/check_env_diy_runtime.mjs all
```

The lightweight checks use local files, mocks, or pure modules and should not
download assets or launch Isaac Sim. Run them individually when diagnosing a
failure so that nonzero exit codes remain visible.

## Optional runtime probes

Some files in this directory intentionally cross the lightweight boundary,
for example the manipulation/contact documentation and headless mount smoke
tests. Treat these as opt-in integration checks:

- run them only in the intended `env_isaaclab`;
- use the exact asset/controller configuration under test;
- keep reports outside the source tree unless they are maintained fixtures;
- do not interpret a probe as a benchmark task success result.

## When to run checks

GUI lifecycle regression tests can run without Isaac:

```bash
python -m pytest -q tools/validation/test_simulator_gui_shutdown.py tools/validation/test_box_transport_capture.py
```

`open_simulator_session()` releases existing Kit GUI callbacks before closing
an application it owns; it does not close or alter caller-owned applications.
After environment close, it stops physics and releases the shared native tensor
view while PhysX is alive, including references retained by replay/controller
objects. These objects cannot be used after their session closes. The Kit 107
compatibility guard releases the native owner of the invalidated view because
the upstream wrapper has no explicit close method.
Each cleanup error is isolated so remaining cleanup and application close can
proceed. A GUI integration check must separately confirm physical evidence and
a zero process exit with the entire worker process group terminated. A physical
success report alone does not qualify a crash during extension teardown.

- Documentation or algorithm inventory changes → documentation and release-link checks.
- Preflight, provider, or asset resolver changes → asset-download and scene-resource checks.
- Env DIY, interface, or compatibility changes → Env DIY checks.
- Setup or ROS distribution changes → ROS distribution check.
- Robot mount or physics changes → the relevant optional runtime probe and unit tests.

After these checks, run the affected unit tests. If the change crosses the
runtime boundary, perform a separate Isaac Sim integration test without
altering the existing simulator, Torch, ROS, or asset-cache environment.

For task contracts and execution orchestration, read
[task/README.md](../../task/README.md). For optional scenario authoring, read
[scenario generation](../scenario_generation/README.md).
