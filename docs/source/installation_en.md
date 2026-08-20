# Installation Guide

This document details how to install and configure the EAI platform.

## Prerequisites

### Required

1. **Isaac Sim**:
   - Version: Isaac Sim 5.1
   - Installation: Refer to [Isaac Lab Installation Guide](https://isaac-sim.github.io/IsaacLab/main/source/setup/installation/index.html)

2. **Isaac Lab**:
   - Version: Isaac Lab 2.x

3. **Python**: Use the Python configured with Isaac Sim/Isaac Lab in the `env_isaaclab` environment

4. **Run the device**:
   - Common robot environment supports CPU or CUDA GPU
   - The registry-driven human demo uses CPU PhysX to avoid Isaac Sim 5.1 GPU pose-write crashes

5. **Operating system**:
   - Linux (Ubuntu 22.04 recommended)

### Optional

- **ROS2 Humble**: The currently validated system ROS baseline for Orsus and ROS2/Nav2 workflows
- **ROS2 Jazzy**: Its Isaac Sim bridge can be selected; system ROS and Nav2 dependencies require separate provisioning and validation
- **Git**: used to clone the repository

## Installation steps

### 1. Clone the repository

```bash
git clone <repository_url>
cd eai-simulator
```

### 2. Activate the Isaac Lab environment

```bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate env_isaaclab
```

### 3. Install EAI package

Use the provided installation script (recommended):

In installation mode, the script checks for `libxcb-cursor0`, which is required by the Qt xcb
platform plugin. If it is missing, the script installs it with `sudo apt-get` and may prompt for
an administrator password.

```bash
# Install all packages
./tools/install_packages.sh

# Select the Jazzy bridge for the current Python/Conda environment (default: Humble)
./tools/install_packages.sh --ros-distro jazzy

# View help
./tools/install_packages.sh -h

# Verbose output
./tools/install_packages.sh -v

# Uninstall all packages
./tools/install_packages.sh -u
```

`--ros-distro` accepts `humble` or `jazzy` and stores the selection under
`share/eai-simulator/ros_distro` in the current Python environment. An existing
`ROS_DISTRO` environment variable takes precedence. This option does not install
system ROS2, modify project source, or edit `~/.bashrc`.

Or install manually:

```bash
# Make sure you are in Isaac Lab's Python environment
pip install -e source/EAI
pip install -e source/EAI_assets
pip install -e source/EAI_hmrs
```

### 4. Apply for Hugging Face asset permissions

The large-volume USD assets and RL model weights required for simulation running are not placed directly in the Git repository, but are stored in the gated Hugging Face data set:
[HuangQIjun/eai-simulator-assets](https://huggingface.co/datasets/HuangQIjun/eai-simulator-assets)ĄŁ

Before running it for the first time, please open the link above to submit an access request. After the account is passed, log in to Hugging Face on the terminal:

```bash
hf auth login
```

When launched, `simulator.py` automatically detects missing `usd/` assets and model files under `source/EAI_assets/EAI_assets/controller/rl/` and downloads them on demand from that dataset. Advanced usage:

```bash
# Use other compatible Hugging Face dataset
export EAI_ASSETS_HF_REPO=<namespace>/<dataset_name>

# Disable automatic download and report an error directly when assets are missing
export EAI_ASSETS_AUTO_DOWNLOAD=0
```

### 5. Verify installation

Check JSON environment configuration:

```bash
find source/EAI_hmrs/EAI_hmrs/envs -maxdepth 1 -name '*.json' -printf '%f\n' | sort
```

The environment is no longer registered to Gym. Each name corresponds to
`source/EAI_hmrs/EAI_hmrs/envs/<env_name>.json`, for example `EAI-Factory-v0.json`.

Check the unified portal and start a JSON environment:

```bash
python simulator.py --help
python simulator.py --env robo
```

## ROS2 configuration (optional)

Humble is the validated full workflow on Ubuntu 22.04. When Jazzy is selected,
EAI uses the Jazzy bridge bundled with Isaac Sim, but system ROS, its matching
Python, and Nav2 packages must still be installed in a compatible environment.
Do not mix Humble `/opt/ros` paths with the Jazzy bridge in one process.

### Install ROS2 Humble

If you need to use the Orsus sensor and ROS2 navigation:

```bash
# Ubuntu 22.04
sudo apt install software-properties-common
sudo add-apt-repository universe
sudo apt update && sudo apt install curl -y
sudo curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.asc | sudo apt-key add -
sudo sh -c 'echo "deb http://packages.ros.org/ros/ubuntu $(lsb_release -cs) main" > /etc/apt/sources.list.d/ros-latest.list'
sudo apt update
sudo apt install ros-humble-desktop-full

# Set environment variables
echo "source /opt/ros/humble/setup.bash" >> ~/.bashrc
source ~/.bashrc
```

### Install Navigation2 (optional)

```bash
sudo apt install ros-humble-navigation2
sudo apt install ros-humble-nav2-bringup
```

### Install Isaac Sim ROS2 Bridge

Isaac Sim ROS2 Bridge is usually installed with Isaac Sim. If not installed:

1. Install the extension via Omniverse Launcher
2. Or refer to [Isaac Sim ROS2 Bridge Document](https://docs.omniverse.nvidia.com/app_isaacsim/app_isaacsim/install_ext_ros_bridge.html)

## FAQ

### Q1: `ModuleNotFoundError: No module named 'EAI'`

**Cause**: EAI package is not installed correctly

**solve**:
```bash
pip install -e source/EAI
pip install -e source/EAI_assets
pip install -e source/EAI_hmrs
```

### Q2: `CUDA out of memory`

**Cause**: Insufficient GPU memory

**solve**:
- Reduce the number of parallel environments: `--num_envs=1`
- Close other GPU processes
- Use smaller models

### Q3: ROS2 topic is not published yet

**Cause**: ROS2 environment is not configured correctly

**solve**:
```bash
# Check ROS2 environment
echo $ROS_DISTRO # should match the install selection, such as humble or jazzy

# If not set, set it manually
export ROS_DISTRO=humble # or jazzy
source "/opt/ros/${ROS_DISTRO}/setup.bash"
```

### Q4: Model file not found

**Cause**: The model path configuration is incorrect or the model file does not exist

**solve**:
- Check the `source/EAI_assets/EAI_assets/controller/rl/*/model/` directory
- Confirm that the model file exists (`.pt` or `.onnx`)
- Check `model_path` in controller configuration

### Q5: Isaac Sim cannot start

**Cause**: Multiple possibilities (GPU driver, CUDA version, permissions, etc.)

**solve**:
- Check GPU driver: `nvidia-smi`
- Check CUDA version: `nvcc --version`
- View Isaac Sim log
- Refer to [Isaac Sim Troubleshooting Guide](https://docs.omniverse.nvidia.com/app_isaacsim/app_isaacsim/troubleshooting.html)

## Uninstall

Uninstall all EAI packages:

```bash
./tools/install_packages.sh -u
```

Or uninstall manually:

```bash
pip uninstall EAI EAI-assets EAI-hmrs
```

## Development mode installation

If you need to modify the source code, use an editable installation (included in the installation script):

```bash
pip install -e source/EAI
pip install -e source/EAI_assets
pip install -e source/EAI_hmrs
```

There is no need to reinstall after modifying the source code in this way.

## Verify installation integrity

Run the entry test and start the JSON environment:

```bash
python -m unittest source.EAI_assets.test.test_simulator_entry
python simulator.py --env=EAI-Factory-v0 --num_envs=1 --device=cuda:0
```

## Next Steps

- View the [Quick Start](getting_started_en.md)
- View the [Project Overview](project_overview_en.md)
- View the [Environment Guide](environments_en.md)
