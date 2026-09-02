# Setup Tools

[Chinese](README.zh-CN.md)

Run these scripts from the repository root. They install repository packages, persist the selected Isaac ROS bridge distribution, or change host inotify limits. Review the effects before using them on a shared machine.

## Commands

| Script | Purpose | Important effects |
| --- | --- | --- |
| `install_packages.sh` | Install or uninstall the three repository Python packages and select Humble or Jazzy | Installs `pywebview[qt]` with the active Python; can install `libxcb-cursor0` with `apt-get`; invokes bare `pip` for uninstall; writes `share/eai-simulator/ros_distro` below the active Python prefix |
| `configure_inotify_limits.sh` | Raise Linux inotify limits for Isaac Sim and large workspaces | Without `--dry-run`, writes `/etc/sysctl.d/90-eai-isaac-sim-inotify.conf` and calls `sysctl --system` |
| `ros_distro.sh` | Shared functions for validating, resolving, reading, and writing the selected ROS distribution | Must be sourced by another shell script; it is not an installer |

## Package installer

Activate the intended Isaac Lab environment and verify that bare `pip` and `python -m pip` resolve to the same environment before installation:

```bash
command -v python
command -v pip
pip --version
python -m pip --version
./tools/setup/install_packages.sh --help
./tools/setup/install_packages.sh --ros-distro humble
```

Use `--ros-distro jazzy` only when that bridge/runtime environment is already prepared. The option selects the bridge backend; it does not install system ROS2. Installation first installs and verifies `pywebview[qt]` for the Env DIY visual chooser, then installs the repository packages with `--no-deps`. `-u` uninstalls the repository packages and `-v` enables verbose package output. The script continues to the remaining repository packages when one package operation fails, then returns a failure status if any operation failed.

## Inotify limits

Inspect the generated configuration first:

```bash
./tools/setup/configure_inotify_limits.sh --dry-run
```

Applying it requires root or passwordless `sudo`:

```bash
./tools/setup/configure_inotify_limits.sh
```

The script validates current values, writes the configuration atomically, reloads the kernel settings, and attempts to restore the previous file when application fails. `EAI_INOTIFY_PROC_ROOT` is a test override supported only with `--dry-run`.

## ROS distribution helpers

`ros_distro.sh` accepts only `humble` or `jazzy`. Consumers resolve an explicit option before `ROS_DISTRO`, an installed selection, and finally the Humble default. The persisted value is written atomically below the current Python prefix.

```bash
source tools/setup/ros_distro.sh
resolve_ros_distro "${ROS_DISTRO:-}"
```

## Safe checks

```bash
bash -n tools/setup/install_packages.sh tools/setup/configure_inotify_limits.sh tools/setup/ros_distro.sh
./tools/setup/configure_inotify_limits.sh --dry-run
python tools/validation/check_ros_distro_config.py
```
