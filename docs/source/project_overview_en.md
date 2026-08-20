# Project Overview

This document is for researchers and developers who need to evaluate, use, or extend EAI Simulator. It describes the platform scope, repository structure, entity catalog, control interfaces, and primary workflows.

## Platform Introduction

EAI Simulator is a social physical simulation platform for research into human-machine coexistence and collaboration.

Built on Isaac Lab, the platform provides configurable physical simulation and heterogeneous control interfaces. The environment layer composes humans, robots, manipulators, and sensors. The algorithm and demo layers can further define roles, information flows, task constraints, multi-agent discussions, and collaboration rules. Social capabilities are therefore composable environment and experiment capabilities rather than a standalone rule engine enabled by default in every JSON environment.

- The physical simulation layer is built on Isaac Lab and supports reinforcement learning, conventional control, and external policies.
- The current catalog covers legged, humanoid, and aerial robots, mobile bases, combined mobile-manipulator platforms, and human assets.
- The repository focuses on simulation execution, inference environments, pretrained policy loading, ROS2 interfaces, and reusable experiment entry points.

## Architecture

<iframe
  class="eai-architecture-frame"
  src="eai-simulator-architecture.html?embed=1&amp;lang=en&amp;theme-sync=3"
  title="Interactive EAI Simulator architecture"
  loading="eager"
></iframe>

## Repository Structure

```text
eai-simulator/
├── simulator.py                         # Unified entry point for Isaac Sim and JSON environments
├── demo/
│   └── fire_rescue/                     # Robot-only fire-rescue experiment and port 8767 monitor
├── algorithm/
│   ├── emos/                            # Multi-robot LLM discussion and task assignment
│   ├── global_planner/                  # 2D planning, path tracking, and velocity commands
│   ├── keyboard/                        # ROS2 cmd_vel keyboard publisher
│   └── ros/                             # ROS2 / Nav2 algorithms and diagnostic tools
├── source/
│   ├── EAI/EAI/
│   │   ├── controllers/                 # Base controller interfaces
│   │   ├── hmrs_env/
│   │   │   ├── env_diy/                # JSON environment selection, saving, and asset processing
│   │   │   ├── multi_robot_direct_env.py
│   │   │   └── update.sh               # Env DIY asset update entry point
│   │   └── hmrs_ros/                    # Generic ROS2 cmd_vel input interface
│   ├── EAI_assets/EAI_assets/
│   │   ├── robots/                      # Robot assets
│   │   ├── scene/                       # Scene asset configuration
│   │   ├── sensor/                      # Orsus and LiDAR
│   │   └── controller/                  # Conventional controllers and trained policy configurations
│   ├── EAI_env_diy/
│   │   ├── config/extension.toml        # Reloadable Isaac Sim Extension manifest
│   │   └── EAI_env_diy/                 # 3D editing model, Viewport UI, and USD preview
│   └── EAI_hmrs/EAI_hmrs/
│       ├── env_builder.py               # Generic JSON environment builder
│       ├── controller_loader.py         # On-demand controller loading
│       └── envs/                        # JSON environments launched with --env
├── docs/source/                         # Sphinx documentation sources
└── usd/                                 # Local USD asset cache and image resources
```

## Core Modules

### EAI (Core Module)

**Location**: `source/EAI/EAI/`

**Purpose**: Provides the controller system, environment base classes, and common components.

**Main components**:

- **`controllers/`**: Controller base classes and loaders
  - `base.py`: The `ControllerCfg` base class and its unified interface
  - `skrl_controller.py`: Base class for SKRL reinforcement-learning controllers
  - `rsl_controller.py`: Base class for RSL-RL ONNX controllers
  - `differential_drive_controller.py`: Base class for differential-drive controllers
  - `utils.py`: Utilities such as `ONNXPolicy` and model loading
- **`hmrs_env/`**: Multi-robot environment base classes
  - `multi_robot_direct_env.py`: `MultiRobotDirectEnv`, built on DirectMARL

### EAI_assets (Assets and Controllers)

**Location**: `source/EAI_assets/EAI_assets/`

**Purpose**: Manages robot assets, scenes, sensors, and controller configurations.

**Main components**:

- **`robots/`**: Robot asset configurations, including USD paths and physical parameters
- **`scene/`**: Scene configurations, including terrain, lighting, and obstacles
- **`sensor/`**: Sensor configurations
  - `high_sensor/`: High-frequency sensors with CPU streams, such as Orsus
  - `low_sensor/`: Low-frequency GPU streams for reinforcement learning
- **`controller/`**: Controller configurations
  - `traditional/`: Conventional controllers such as differential drive
  - `rl/`: Reinforcement-learning controllers using SKRL or RSL-RL

### EAI_hmrs (Inference Environments)

**Location**: `source/EAI_hmrs/EAI_hmrs/`

**Purpose**: Stores JSON environment configurations and builds inference environments with the generic builder.

**Characteristics**:

- Based on `MultiRobotDirectEnv` and the DirectMARL architecture
- Uses one `controllers` dictionary for controller management
- Does not enable domain randomization by default
- Removes reward functions

**Environment list**: See the [Environment Guide](environments_en.md).

## Controllers and Robots Available to simulator.py

`simulator.py` only uses JSON environments. With `--env=<name>`, it loads `source/EAI_hmrs/EAI_hmrs/envs/<name>.json`. Without `--env`, it opens the Env DIY startup menu. Select item 3 or run `python simulator.py --diy-3d` for true 3D editing; this saves Viewport transforms as physical `spawn_pose` values.

Robot choices are defined by `source/EAI/EAI/hmrs_env/env_diy/catalog.py::ROBOT_KEYS` and `source/EAI_hmrs/EAI_hmrs/env_builder.py::ROBOT_OPTIONS`. The following 13 types are currently available:

| Env DIY key | Robot / object | Default controller | Optional payloads | Common entry point |
| ----------- | -------------- | ------------------ | ----------------- | ------------------ |
| `carter` | Carter differential base | `CARTER_DIFF_CFG` | Orsus, LiDAR, Z1 | JSON / Env DIY |
| `pepper` | Pepper holonomic base | `PEPPER_HOLONOMIC_CFG` | - | JSON / Env DIY |
| `go2` | Unitree Go2 | `GO2_VELOCITY_RSL_CFG` | Orsus, LiDAR, UR5, Z1 | JSON / Env DIY |
| `b2` | Unitree B2 | `B2_VELOCITY_RSL_CFG` | Orsus, LiDAR, UR5, Z1 | JSON / Env DIY |
| `m20` | DeepRobotics M20 | `M20_ROUGH_RSL_CFG` | Orsus, LiDAR, UR5, Z1 | JSON / Env DIY |
| `scout` | Scout mobile base | `SCOUT_DIFF_CFG` | Orsus, LiDAR, UR5, Z1 | JSON / Env DIY |
| `g1` | Unitree G1 | `G1_SKRL_CFG` | - | JSON / Env DIY |
| `cf2x` | Crazyflie CF2X | `QUADCOPTER_GOAL_SKRL_CFG` | Built-in camera, keyboard, ROS | JSON / Env DIY |
| `iris` | Pegasus 3DR Iris | `PEGASUS_IRIS_POSITION_CFG` | Built-in camera, keyboard, ROS | JSON / Env DIY |
| `pegasus` | Pegasus research quadrotor | `PEGASUS_X4_POSITION_CFG` | Built-in camera, keyboard, ROS | JSON / Env DIY |
| `lite3` | DeepRobotics Lite3 | `LITE3_VELOCITY_RSL_CFG` | Orsus, LiDAR, UR5, Z1 | JSON / Env DIY |
| `mushr_v2` | MuSHR Nano v2 Ackermann base | `MUSHR_ACKERMANN_CFG` | Built-in camera, LiDAR, keyboard, ROS | JSON / Env DIY |
| `coco` | Coco AIRS Ackermann base | `COCO_ACKERMANN_CFG` | Orsus, LiDAR, keyboard, ROS | JSON / Env DIY |

> Controller configurations are stored in `source/EAI_assets/EAI_assets/controller/`, under `rl/` and `traditional/`. `UR5_IK_CFG` and `Z1_IK_CFG` provide manipulator attachments for the compatible hosts listed above.

## Environments and Tasks

- All environment configurations live in `source/EAI_hmrs/EAI_hmrs/envs/`; this directory contains JSON files only.
- `robo.json` is a comprehensive quick-start environment with multiple robots and keyboard control.
- `EAI-Factory-v0.json` contains the fixed robot composition used by Fire Rescue.
- Environments saved by Env DIY use the same schema and launch process as manually maintained environments.
- Launch an environment with `python simulator.py --env=<env_name>`, omitting the `.json` extension.

## Workflow

1. **Create a custom environment with Env DIY** - Start `simulator.py` without `--env` to enter the custom environment flow:
   ```bash
   python simulator.py --num_envs=1 --device=cuda:0
   ```
   The prompt offers three environment-authoring methods:
   - `1. Visual window`: Use the Env DIY window to select `Scenes -> Robots -> Payloads -> Tools`. Payloads are grouped into Manipulators (UR5/Z1) and Sensors (Orsus/LiDAR), while Tools provides Camera, Keyboard, and Navigation I/O. The Camera Tool independently controls ROS image publication for the built-in monocular cameras on Iris, Pegasus, CF2X, and MuSHR, plus Orsus cameras on compatible hosts. Navigation I/O controls LiDAR, IMU, GPS, magnetometer, and barometer publication for all three aerial robots, plus Orsus LiDAR point-cloud, odometry, and scan publication. For compatibility with existing environments, Navigation I/O is still serialized with the `ros` key. The result can be saved as `source/EAI_hmrs/EAI_hmrs/envs/<env_name>.json`.
   - `2. Terminal quick setup`: Select a scene, host robot, manipulator, sensor, tool, and controller in the same order as the visual window, then choose whether to save and run the environment immediately.
   - `3. Isaac Sim 3D editor`: Edit the robots' physical `spawn_pose` values directly in the Isaac Sim Viewport. You can also run `python simulator.py --diy-3d --device=cuda:0` to enter it directly.

2. **Request Hugging Face asset access before the first run** - Large USD assets and RL model weights are not stored directly in Git. They are provided through the gated Hugging Face dataset [HuangQIjun/eai-simulator-assets](https://huggingface.co/datasets/HuangQIjun/eai-simulator-assets).
   Submit an access request on that page. After your account is approved, sign in from the terminal:
   ```bash
   hf auth login
   ```
   At startup, `simulator.py` checks for missing assets under `usd/` and missing model files under `source/EAI_assets/EAI_assets/controller/rl/`. Once authorized, it downloads only the missing files as needed. The `--diy-3d` extension also displays individual status entries for scenes, robots, payloads, tools, and controller configurations. It supports per-item `Download` actions and `Download all and run` when launching. Access to the gated dataset is handled through `Request`, terminal `Login`, and `Recheck`; the extension never accepts a token. Set `EAI_ASSETS_HF_REPO` to use another compatible dataset repository, or set `EAI_ASSETS_AUTO_DOWNLOAD=0` to disable automatic downloads.

3. **Launch a JSON environment** - Start with the comprehensive example. Saved custom environments use the same loading process:
   ```bash
   python simulator.py --env robo
   python simulator.py --env=<env_name> --num_envs=1 --device=cuda:0
   python simulator.py --env=nav2 --num_envs=1 --device=cuda:0
   ```

4. **Launch the fixed Fire Rescue composition** - The environment name is unchanged, but the implementation loads JSON:
   ```bash
   python simulator.py --env=EAI-Factory-v0 --num_envs=1 --device=cuda:0
   ```

5. **Policy sources** - `source/EAI_assets/EAI_assets/controller/rl/` stores the pretrained-policy loading configurations and weight paths required by the simulator.

## Env DIY and External Interface Examples

Env DIY is the custom environment entry point in `simulator.py`. It quickly composes scenes, robots, sensors, and external control tools.

The following demo shows the complete flow from environment configuration to simulation. Watch the full workflow first, then work through it in the embedded tutorial.

```{figure} assets/media/demo.gif
:alt: Complete EAI Simulator workflow demonstration
:class: eai-doc-media
:width: 100%

EAI Simulator scene, robot, and task execution
```

<div class="env-diy-overview-card">
  <p class="env-diy-overview-card__title">Env DIY Tutorial</p>
  <iframe class="env-diy-overview-frame" src="env_diy_tutorial.html?embed=1" title="EAI Env DIY tutorial"></iframe>
</div>

**Visual workflow**:

1. Start the simulator without `--env`:
   ```bash
   python simulator.py --num_envs=1 --device=cuda:0
   ```
2. Select `1. Visual window` in the prompt.
3. Drag a scene card onto the canvas, then drag robot cards to their target positions in the scene.
4. Open `Payloads`. Choose UR5/Z1 under `Manipulators` or Orsus/LiDAR under `Sensors`, then open `Tools` to choose Camera, Keyboard, or Navigation I/O. The Camera Tool independently controls ROS image publication for the built-in monocular cameras on Iris, Pegasus, CF2X, and MuSHR, plus Orsus cameras on compatible hosts. Navigation I/O controls LiDAR, IMU, GPS, magnetometer, and barometer publication for all three aerial robots, plus Orsus LiDAR point-cloud, odometry, and scan publication. After selecting a robot, cards that are incompatible, already attached, or would add a second manipulator are disabled.
5. Select `Complete Selection` and save the environment if needed. Saved configurations are written to `source/EAI_hmrs/EAI_hmrs/envs/<env_name>.json`.
6. Launch a saved environment directly on subsequent runs:
   ```bash
   python simulator.py --env=<env_name> --num_envs=1 --device=cuda:0
   ```

The `visual.x/y` values in the lightweight window describe canvas layout, not simulation coordinates. For height, surface snapping, and true 3D transforms, use the Isaac Sim pre-run 3D editor described in the environment guide:

```bash
python simulator.py --diy-3d --device=cuda:0
```

See the [Environment Guide](environments_en.md) for the 3D entry point, asset downloads, and the execution boundary within a single Kit process.

```{figure} assets/media/eai_env_diy.gif
:alt: EAI Env DIY 3D editing and execution demonstration
:class: eai-doc-media
:width: 100%

EAI Env DIY 3D scene editing, asset preparation, and execution workflow
```

**Quick terminal workflow**:

1. Run `python simulator.py --num_envs=1 --device=cuda:0`.
2. Select `2. Terminal quick setup` in the prompt.
3. Select a scene, host robot, UR5/Z1 manipulator, Orsus/LiDAR sensor, Camera/Keyboard/Navigation I/O tool, and controller in sequence. Camera and Navigation I/O have the same publication responsibilities as in the visual workflow.
4. Follow the prompts to choose whether to save and immediately run the environment.

**Keyboard external interface example**:

The repository includes `source/EAI_hmrs/EAI_hmrs/envs/keyboard.json` as the minimal keyboard test environment:

```bash
python simulator.py --env=keyboard --device=cuda:0
```

This environment creates `carter_1` and subscribes to `/carter_1/cmd_vel`. After the simulator starts, run the following in another terminal:

```bash
source /opt/ros/humble/setup.bash && python3 algorithm/keyboard/keyboard.py
```

`algorithm/keyboard/keyboard.py` automatically discovers `/<robot>/cmd_vel` topics. You can also specify a robot explicitly:

```bash
source /opt/ros/humble/setup.bash && python3 algorithm/keyboard/keyboard.py --robot carter_1
```

Controls: `W/S/A/D` translate, `R/F` makes aerial robots ascend/descend, `C/V` turns, `K` or Space stops, `Q` switches between robots, and `Esc` or `Ctrl-C` exits. Set aerial vertical speed with `--vertical-speed`. This script uses ROS Humble's `rclpy`; the system `python3` is recommended.

```{figure} assets/media/eai-keyboard.gif
:alt: Controlling an EAI robot with the keyboard
:class: eai-doc-media
:width: 100%

The Keyboard tool controls a robot through ROS2 `cmd_vel`
```

**Nav2 navigation example (Factory + Carter + Orsus)**:

The included Nav2 example is `source/EAI_hmrs/EAI_hmrs/envs/nav2.json`. It selects the Factory scene and Carter, then adds Orsus, Camera, and Navigation I/O. The Camera Tool enables Orsus image publication. Navigation I/O enables the `/carter_1/cmd_vel` subscriber and Orsus LiDAR point-cloud, odometry, and scan publication.

In terminal 1, launch the simulator. Nav2 and Orsus simulations require the Isaac Sim GUI and cannot run headless:

```bash
conda activate env_isaaclab
python simulator.py --env=nav2 --num_envs=1 --device=cuda:0
```

In terminal 2, launch Nav2 and RViz:

```bash
source /opt/ros/humble/setup.bash
ros2 launch algorithm/ros/nav2/nav2.launch.py robot_name:=carter_1 robot_type:=Carter scene:=factory rviz:=true
```

In terminal 3, send a navigation goal. Choose a point in the free space of the Factory map:

```bash
source /opt/ros/humble/setup.bash
/usr/bin/python3 algorithm/ros/nav2/send_goal.py --x -5.0 --y -8.0
```

After the map, sensors, and ROS channels are configured, Nav2 plans and executes a path through the Factory scene. The following demo appears after the complete command sequence so that its result can be compared directly with the terminal steps.

```{figure} assets/media/eai-nav.gif
:alt: Carter running Nav2 navigation in the Factory scene
:class: eai-doc-media
:width: 100%

Nav2 navigation with Factory, Carter, and Orsus
```

## Installation and Common Commands

- Editable installation, with the Isaac Lab Python environment available:
  ```bash
  pip install -e source/EAI
  pip install -e source/EAI_assets
  pip install -e source/EAI_hmrs
  ```
- List JSON environment configurations:
  ```bash
  find source/EAI_hmrs/EAI_hmrs/envs -maxdepth 1 -name '*.json' -printf '%f\n' | sort
  ```
- Install or uninstall all packages from the repository root:
  ```bash
  ./tools/install_packages.sh       # Install
  ./tools/install_packages.sh -u    # Uninstall
  ./tools/install_packages.sh -v    # Verbose output
  ```
- Update Env DIY image assets from the repository root:
  ```bash
  source/EAI/EAI/hmrs_env/update.sh
  source/EAI/EAI/hmrs_env/update.sh --source-root usd/picture --output-root usd/picture/processed
  ```
  `source/EAI/EAI/hmrs_env/update.sh` calls `EAI.hmrs_env.env_diy.update_assets`. It checks source PNG files under `usd/picture/robot/`, `usd/picture/manipulator/`, `usd/picture/sensor/`, and `usd/picture/tool/`. When the matching output is missing from `usd/picture/processed/`, or the source is newer than the processed file, it regenerates the Env DIY palette asset with a transparent background, outline, and glow. It does not update Git code or download USD scene and robot assets or RL model weights.

## Development Conventions and Notes

- Keys in the `controllers` dictionary must match scene asset names. Dictionary order determines the observation and action concatenation order.
- All controllers are handled through the environment's `_pre_physics_step` interface; the Dispatcher does not need to be called manually.
- Pretrained-policy loading configurations and conventional controllers live in `source/EAI_assets/EAI_assets/controller/`.
- See the [Controller Development Guide](controller_guide_en.md) for controller development.
- Local USD assets are stored under `usd/`, for example `usd/robot/m20/M20.usd` and `usd/robot/go2/go2.usd`. Missing USD assets and RL models are downloaded on demand from the [Hugging Face asset repository](https://huggingface.co/datasets/HuangQIjun/eai-simulator-assets). Access to this gated dataset must be requested first.
- Build the documentation with `cd docs && make html`. Preview it locally with `cd build/html && python -m http.server 8000`.

## Current Repository Scope

The repository focuses on simulation execution, asset configuration, controller loading, Env DIY, and external ROS/Nav2 interfaces.

## Related Pages

- **Quick Start**: [Run Your First Simulation](getting_started_en.md)
- **Installation Guide**: [Installation and Dependencies](installation_en.md)
- **Environment Guide**: [Environment Configuration and Usage](environments_en.md)
- **Controller Development**: [Controller Development Guide](controller_guide_en.md)
- **Orsus Sensor**: [Orsus Usage Guide](orsus_sensor_en.md)
- **Next-Stage Feature Roadmap**: [View the Project Roadmap](roadmap_en.md)
