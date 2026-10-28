# Repository Tools

[Chinese](README.zh-CN.md)

Run commands in this tree from the repository root. `tools/` groups independent operational, validation, ROS2, and human-asset-authoring entry points; it is not one Python package or a uniform API. Read the owning directory guide and the command help before running a tool.

## Directory guide

| Directory | Responsibility | Runtime boundary |
| --- | --- | --- |
| [`setup/`](setup/README.md) | Editable package installation, ROS distribution selection, and host inotify limits | Bash, `pip`, and optional host administration |
| [`validation/`](validation/README.md) | Repository checks and opt-in manipulation physics fixtures | Python or Node.js; physics fixtures require Isaac Lab |
| [`ros2/`](ros2/README.md) | External sensor, mobile-base, and manipulator clients plus focused tests | Selected system ROS2 Python |
| [`human_assets/`](human_assets/README.md) | Human conversion, authoring, migration, cache generation, validation, and demo workflows | Pure Python or Isaac Sim/OpenUSD, depending on the command |
| [`physics_replay/`](physics_replay/README.md) | Isaac Sim physics, command/action/noise tapes, checkpoint restore, and episode inspection | Pure Python inspection; Factory validation requires `env_isaaclab` |
| [`experiment_analysis/`](experiment_analysis/README.md) | Frozen multi-batch schedules, bounded collector snapshots, descriptive estimators, atomic result publication and portable sealed-evidence archives | Standard-library Python; result/archive publishing on Linux; no simulator or release approval |
| [`topology_authoring/`](topology_authoring/README.md) | Scene-adapter based topology drafts and Isaac Sim authoring UI | Pure Python model or Isaac Sim GUI; adapter decides scene inputs |

The root-level [`semantic_map_traversability.py`](semantic_map_traversability.py)
audits an authored semantic navigation network with a catalog robot. Its
`--check` path is pure Python; physical execution requires `env_isaaclab`,
Isaac Sim rendering, and the selected robot/controller assets.

## Runtime boundaries

- `setup/` can install system packages, editable Python packages, and persistent host configuration. Review its side effects first.
- Most checks under `validation/` are lightweight; the [manipulation fixtures](validation/manipulation_contact.md) explicitly launch Isaac Sim. Node.js 20 LTS or newer is required by the Env DIY runtime check.
- Programs under `ros2/` that import `rclpy` must use the selected system ROS2 Python, not the interpreter in `env_isaaclab`.
- Human-asset commands have mixed requirements. Planning, JSON authoring, migration, and structural validation can be pure Python; conversion, import, cache generation, and the runtime demo can require Isaac Sim or `pxr`.
- `semantic_map_traversability.py --check` validates inputs and freezes a test plan without starting Isaac Sim. Omit `--check` only from an `env_isaaclab` shell with a working GPU renderer; physical runs are always headless but still render off-screen video.

## Quick entry points

### Setup

```bash
./tools/setup/install_packages.sh --help
./tools/setup/configure_inotify_limits.sh --dry-run
source tools/setup/ros_distro.sh
```

### Validation

```bash
python tools/validation/check_asset_download_errors.py
python tools/validation/check_documentation_consistency.py
python tools/validation/check_env_diy_exclusivity.py
node tools/validation/check_env_diy_runtime.mjs all
python tools/validation/check_release_links.py
python tools/validation/check_ros_distro_config.py
```

### ROS2 clients

Use a separate shell with the selected system ROS2 environment sourced:

```bash
source /opt/ros/humble/setup.bash
/usr/bin/python3 tools/ros2/vis_sensors.py --help
/usr/bin/python3 tools/ros2/send_cmd_vel.py --help
/usr/bin/python3 tools/ros2/send_manipulator_command.py --help
```

### Human assets

```bash
python tools/human_assets/validate_assets.py --help
python tools/human_assets/edit_action.py --help
python tools/human_assets/convert_gltf_assets.py --help
python tools/human_assets/migrate_assets.py --help
```

`scene.py` and `motion_controls.py` are imported support modules, not standalone commands. See the human-asset guide before running a command that writes USD, manifests, caches, or reports.

### Topology authoring

```bash
python tools/topology_authoring/author.py \
  --scene-key warehouse \
  --adapter tools.topology_authoring.adapters.empty:EmptyTopologyAdapter \
  --asset-root /path/to/scene-assets \
  --output-root /path/to/prepared-package
```

Use `--headless` to inspect an adapter without launching Isaac Sim.

### Semantic-map traversability

The required robot and scene values are exact catalog/project keys. The tool
selects the `ground` XY network for wheeled and legged robots and the `aerial`
XYZ network for drones. It never falls back to the other network and delegates
route selection and progression only to `algorithm.skills.navigation`.

Inspect the standard plan without launching Isaac Sim:

```bash
python tools/semantic_map_traversability.py \
  --robot go2 --scene living_home_02 --check
```

A standard physical run rotates an applicable ground robot at every node,
traverses every admitted edge, and samples up to ten long reachable routes.
Aerial runs omit node rotation; ground controllers that cannot rotate in place
report that part as unsupported. Disconnected components and unreachable
directed pairs are reported but are not manufactured into route cases.

Use the case-sensitive `--StressT` option for an operator-selected stress run:

```bash
python tools/semantic_map_traversability.py \
  --robot go2 --scene living_home_02 \
  --StressT node:0001 node:0002,node:0004 \
  --check

conda run --no-capture-output -n env_isaaclab \
  python tools/semantic_map_traversability.py \
  --robot go2 --scene living_home_02 \
  --StressT node:0001 node:0002,node:0004
```

Each single-node entry rotates three continuous turns. Each node pair must be
reachable in both directions and runs `A -> B -> A -> B -> A`; only the first
leg resets the robot. Entries keep input order, including duplicates. In stress
mode the full-map edge scan and random-route sample are disabled. Combining
`--StressT` with `--check` validates exact node IDs and bidirectional
reachability without starting Isaac Sim.

Every invocation creates
`~/tmp/<YYYYMMDD_HHMMSS>_<robot>_<scene>/` using Beijing time (or uses
`--output-root`). It writes `report.md`, `report.json`, `test-plan.json`,
`test-results.json`, per-case trajectories, and retained MP4 evidence. Stress
runs retain every executed video; standard node/edge successes may discard
their temporary videos. Video defaults to H.264 at 1280x720 and 15 FPS.
`--seed` controls standard long-route sampling, `--device` selects the simulator
device, and `--asset-root` overrides scene-asset discovery. Exit status is `0`
for a complete pass, `1` for a test failure or partial result, and `2` for
invalid input or simulator/measurement infrastructure failure.

## Side effects

- `setup/install_packages.sh` installs `pywebview[qt]`, can call `apt-get` through `sudo`, installs or uninstalls editable packages, and writes the selected ROS distribution below the active Python prefix.
- `setup/configure_inotify_limits.sh` without `--dry-run` writes `/etc/sysctl.d/90-eai-isaac-sim-inotify.conf` and reloads live kernel limits.
- `ros2/send_cmd_vel.py` publishes commands to a live robot. It attempts to publish zero velocity during teardown, but the simulator bridge has no stale-command watchdog; observe the robot and verify that it stopped.
- `ros2/send_manipulator_command.py` publishes live manipulator commands. Confirm the robot instance, model, target, and surrounding clearance first.
- Commands under `human_assets/` can write authored, converted, migrated, or cached assets and metadata. Use their planning or dry-run mode where available.
- `semantic_map_traversability.py` creates a new report directory for every invocation. Physical mode launches one headless Isaac Sim session, records off-screen H.264 video, and can consume substantial GPU time and disk space. The command hashes its scene and semantic-map inputs before and after execution and treats input mutation as an infrastructure error.

## Lightweight verification

These checks do not intentionally start Isaac Sim, connect to a live ROS graph, or modify repository source:

```bash
bash -n tools/setup/install_packages.sh tools/setup/configure_inotify_limits.sh tools/setup/ros_distro.sh
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest --rootdir="$PWD" -q -p no:cacheprovider \
  tools/ros2/tests/test_vis_sensors.py \
  tools/ros2/tests/test_send_cmd_vel.py \
  tools/ros2/tests/test_send_manipulator_command.py \
  tools/tests/test_semantic_map_traversability.py
python tools/validation/check_asset_download_errors.py
python tools/validation/check_documentation_consistency.py
python tools/validation/check_env_diy_exclusivity.py
python tools/validation/check_release_links.py
python tools/validation/check_ros_distro_config.py
node tools/validation/check_env_diy_runtime.mjs all
```

The ROS2 tests use mocks for client lifecycle and pure helper behavior; they do not replace live ROS2 and simulator validation.
