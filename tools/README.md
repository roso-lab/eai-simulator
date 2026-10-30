# Repository Tools

[Chinese](README.zh-CN.md)

Run commands in this tree from the repository root. `tools/` groups independent operational, validation, ROS2, and human-asset-authoring entry points; it is not one Python package or a uniform API. Read the owning directory guide and the command help before running a tool.

## Directory guide

| Directory | Responsibility | Runtime boundary |
| --- | --- | --- |
| [`setup/`](setup/README.md) | Editable package installation, ROS distribution selection, and host inotify limits | Bash, `pip`, and optional host administration |
| [`validation/`](validation/README.md) | Lightweight repository consistency and regression checks | Repository Python or Node.js |
| [`ros2/`](ros2/README.md) | External sensor, mobile-base, and manipulator clients plus focused tests | Selected system ROS2 Python |
| [`human_assets/`](human_assets/README.md) | Human conversion, authoring, migration, cache generation, validation, and demo workflows | Pure Python or Isaac Sim/OpenUSD, depending on the command |

## Runtime boundaries

- `setup/` can install system packages, editable Python packages, and persistent host configuration. Review its side effects first.
- Python checks under `validation/` are lightweight, but Node.js 20 LTS or newer is required by the Env DIY runtime check.
- Programs under `ros2/` that import `rclpy` must use the selected system ROS2 Python, not the interpreter in `env_isaaclab`.
- Human-asset commands have mixed requirements. Planning, JSON authoring, migration, and structural validation can be pure Python; conversion, import, cache generation, and the runtime demo can require Isaac Sim or `pxr`.

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

## Side effects

- `setup/install_packages.sh` installs `pywebview[qt]`, can call `apt-get` through `sudo`, installs or uninstalls editable packages, and writes the selected ROS distribution below the active Python prefix.
- `setup/configure_inotify_limits.sh` without `--dry-run` writes `/etc/sysctl.d/90-eai-isaac-sim-inotify.conf` and reloads live kernel limits.
- `ros2/send_cmd_vel.py` publishes commands to a live robot. It attempts to publish zero velocity during teardown, but the simulator bridge has no stale-command watchdog; observe the robot and verify that it stopped.
- `ros2/send_manipulator_command.py` publishes live manipulator commands. Confirm the robot instance, model, target, and surrounding clearance first.
- Commands under `human_assets/` can write authored, converted, migrated, or cached assets and metadata. Use their planning or dry-run mode where available.

## Lightweight verification

These checks do not intentionally start Isaac Sim, connect to a live ROS graph, or modify repository source:

```bash
bash -n tools/setup/install_packages.sh tools/setup/configure_inotify_limits.sh tools/setup/ros_distro.sh
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest --rootdir="$PWD" -q -p no:cacheprovider \
  tools/ros2/tests/test_vis_sensors.py \
  tools/ros2/tests/test_send_cmd_vel.py \
  tools/ros2/tests/test_send_manipulator_command.py
python tools/validation/check_asset_download_errors.py
python tools/validation/check_documentation_consistency.py
python tools/validation/check_env_diy_exclusivity.py
python tools/validation/check_release_links.py
python tools/validation/check_ros_distro_config.py
node tools/validation/check_env_diy_runtime.mjs all
```

The ROS2 tests use mocks for client lifecycle and pure helper behavior; they do not replace live ROS2 and simulator validation.
