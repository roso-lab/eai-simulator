# Repository Tools

Run commands in this directory from the repository root. These scripts are
operational, validation, and asset-authoring entry points; `tools/` is not a
single Python package or a uniform API. Inspect an entry point before running
it because its runtime, prerequisites, and side effects are specific to that
script.

## Directory Guide

| Directory | Responsibility | Runtime boundary |
| --- | --- | --- |
| [`setup/`](setup/) | Package installation, Git hooks, ROS distribution selection, and inotify limits | Bash and host-system tooling |
| [`validation/`](validation/) | Lightweight repository checks | Python or Node.js |
| [`ros2/`](ros2/) | External sensor, mobile-base, and manipulator clients | Selected system ROS2 Python |
| [`assets/`](assets/) | USD maintenance and repair | Isaac Sim/OpenUSD Python |
| [`human_assets/`](human_assets/) | Human-asset conversion, authoring, caching, and validation | See the [human asset guide](human_assets/README.md) |
| [`github_oauth_worker/`](github_oauth_worker/) | GitHub OAuth Cloudflare Worker | See the [worker guide](github_oauth_worker/README.md) |

## Public Entry Points

### Setup

```bash
./tools/setup/install_packages.sh --help
./tools/setup/setup-git-hooks.sh
./tools/setup/configure_inotify_limits.sh --dry-run
source tools/setup/ros_distro.sh
```

`ros_distro.sh` defines shared shell functions and is normally sourced by
another script. It is not a standalone installer.

### Validation

```bash
python tools/validation/check_asset_download_errors.py
python tools/validation/check_env_diy_exclusivity.py
node tools/validation/check_env_diy_runtime.mjs all
python tools/validation/check_ros_distro_config.py
```

Node.js 20 LTS or a newer LTS release is required for the Env DIY validator
and the OAuth worker test and deployment workflow. The Python validators do
not establish that the Node worker or an Isaac-dependent workflow is usable.

### ROS2 Clients

Use these clients in a separate shell with the selected system ROS2
distribution sourced. Programs that import `rclpy` must use the system ROS
Python, not the Python interpreter in `env_isaaclab`.

```bash
source /opt/ros/humble/setup.bash
/usr/bin/python3 tools/ros2/vis_sensors.py --help
/usr/bin/python3 tools/ros2/send_cmd_vel.py --help
/usr/bin/python3 tools/ros2/send_manipulator_command.py --help
```

Do not treat the scripts' repository location as permission to combine
`env_isaaclab` with `rclpy` or other system ROS Python packages.

### USD Repair

The repair tool requires Isaac Sim/OpenUSD Python so that `pxr` is available.
Inspect its options first, and use `--check` for read-only validation:

```bash
python tools/assets/repair_env_diy_usd.py --help
python tools/assets/repair_env_diy_usd.py --check b2 lite3
```

Without `--check`, the command writes canonical USD files and repair
manifests.

## Side Effects and Risk

- `setup/install_packages.sh` can run `apt` through `sudo` and installs or
  uninstalls editable Python packages with `pip`.
- `setup/setup-git-hooks.sh` changes the repository Git configuration and
  hook file modes.
- `setup/configure_inotify_limits.sh` without `--dry-run` writes
  `/etc/sysctl.d/90-eai-isaac-sim-inotify.conf` and reloads live kernel limits.
- `assets/repair_env_diy_usd.py` without `--check` writes canonical USD files
  and manifests.
- Commands under `human_assets/` can write authored, converted, migrated, or
  cached assets; review their inputs and outputs in the linked guide.
- Deploying the OAuth worker changes external Cloudflare and GitHub state;
  its local Node.js test does not deploy anything.

## Safe Validation

The following checks do not launch Isaac Sim, use a live ROS graph, deploy a
worker, or intentionally modify repository source:

```bash
bash -n \
  tools/setup/install_packages.sh \
  tools/setup/setup-git-hooks.sh \
  tools/setup/configure_inotify_limits.sh \
  tools/setup/ros_distro.sh
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
  python -m pytest --rootdir="$PWD" -q -p no:cacheprovider \
  tools/ros2/tests/test_vis_sensors.py \
  tools/ros2/tests/test_send_cmd_vel.py \
  tools/ros2/tests/test_send_manipulator_command.py
python tools/validation/check_asset_download_errors.py
python tools/validation/check_env_diy_exclusivity.py
python tools/validation/check_ros_distro_config.py
node tools/validation/check_env_diy_runtime.mjs all
node tools/github_oauth_worker/oauth_worker_test.mjs
```

Generated assets, caches, downloads, logs, runtime snapshots, documentation
builds, and test output are not tracked source unless a repository workflow
explicitly designates them as maintained fixtures.
