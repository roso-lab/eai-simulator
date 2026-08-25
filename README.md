<p align="center">
  <a href="README.md">English</a> · <a href=".github/README.zh-CN.md">中文</a>
</p>

<p align="center">
  <img src=".github/assets/logo.png" alt="EAI Simulator logo" width="520">
</p>

<h1 align="center">EAI Simulator</h1>

<p align="center">
  <a href="https://releases.ubuntu.com/22.04/"><img src="https://img.shields.io/badge/platform-linux--64-orange.svg" alt="Platform: Linux x86-64"></a>
  <a href="https://docs.isaacsim.omniverse.nvidia.com/5.1.0/index.html"><img src="https://img.shields.io/badge/Isaac%20Sim-5.1-76B900.svg" alt="Isaac Sim 5.1"></a>
  <a href="https://github.com/roso-lab/eai-simulator/releases/tag/v0.1.0-beta.1"><img src="https://img.shields.io/badge/release-v0.1.0--beta.1-007ec6.svg" alt="Release: v0.1.0-beta.1"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-yellow.svg" alt="License: MIT"></a>
</p>

<p align="center">
  <strong>A social-physical simulator for human-robot coexistence.</strong><br>
  Compose heterogeneous environments, connect perception and control, and evaluate collaborative embodied tasks on Isaac Lab.
</p>

<p align="center">
  <a href="https://www.rosolab.com/roso-lab/eai-simulator/docs/getting_started.html"><strong>Get Started</strong></a> ·
  <a href="https://www.rosolab.com/roso-lab/eai-simulator/docs/"><strong>Documentation</strong></a> ·
  <a href="#community--support"><strong>Community</strong></a>
</p>

<p align="center">
  <img src=".github/assets/demo.gif" alt="EAI Simulator running heterogeneous robots and tasks" width="960">
</p>

## Features

<table>
<tr>
<td width="43%" valign="middle">
  <h3>Assemble any environment in minutes</h3>
  <p>Pick a scene, place robots, attach payloads and tools — LiDAR, manipulators, cameras — then plug in external controllers and run. Three guided workflows (visual editor, in-simulator 3D plugin, terminal) all emit the same reusable JSON, so environments are shared, versioned, and re-run like code.</p>
  <p><a href="https://www.rosolab.com/roso-lab/eai-simulator/docs/environments.html">Environment guide →</a></p>
</td>
<td width="57%">
  <a href="https://www.rosolab.com/roso-lab/eai-simulator/docs/environments.html"><img src=".github/assets/env-diy.gif" alt="Composing an environment in the Env DIY visual editor" width="100%"></a>
</td>
</tr>
<tr>
<td width="43%" valign="middle">
  <h3>A ready-made asset library</h3>
  <p>Thirteen heterogeneous robots (wheeled, legged, aerial, humanoid), seven scenes from flat ground to factory and hospital, five mountable payloads, eighteen controller configurations, and forty-four human actors with twelve action groups — all shipped through one gated asset release.</p>
  <p><a href="https://huggingface.co/datasets/rosolab/eai-simulator-assets">Request asset access →</a></p>
</td>
<td width="57%">
  <a href="https://huggingface.co/datasets/rosolab/eai-simulator-assets"><img src=".github/assets/asset-library.gif" alt="EAI Simulator asset library preview with heterogeneous robots and human actors" width="100%"></a>
</td>
</tr>
<tr>
<td width="43%" valign="middle">
  <h3>Connect perception and control</h3>
  <p>Attach Orsus, RealSense D455, or LiDAR sensing and UR5/Z1 manipulators, then drive everything through external controllers — ROS2 velocity commands, Nav2, or trained RL policies — without rebuilding the environment.</p>
  <p><a href="https://www.rosolab.com/roso-lab/eai-simulator/docs/interface_catalog.html">Browse interfaces →</a></p>
</td>
<td width="57%">
  <a href="https://www.rosolab.com/roso-lab/eai-simulator/docs/orsus_sensor.html"><img src=".github/assets/orsus-demo.gif" alt="Orsus multimodal sensor output in EAI Simulator" width="100%"></a>
</td>
</tr>
<tr>
<td width="43%" valign="middle">
  <h3>Run collaborative experiments</h3>
  <p>Five example algorithms ship with the simulator — multi-agent discussion and task allocation, 2D global planning and Nav2 navigation, and external control — alongside Fire Rescue, a heterogeneous factory fire-inspection experiment.</p>
  <p><a href="https://www.rosolab.com/roso-lab/eai-simulator/docs/getting_started.html#fire-rescue-demo">Run an experiment →</a></p>
</td>
<td width="57%">
  <a href="https://www.rosolab.com/roso-lab/eai-simulator/docs/getting_started.html#fire-rescue-demo"><img src=".github/assets/demo.gif" alt="EAI Simulator running heterogeneous robots and tasks" width="100%"></a>
</td>
</tr>
</table>

## Built for Robotics Research

| Heterogeneous entities | Composable worlds | Perception and control |
| --- | --- | --- |
| Humans, Carter, Pepper, MuSHR, Coco, Scout, Go2, B2, M20, Lite3, G1, CF2X, Iris, and Pegasus | Plane, warehouse, factory, AIRS, garden, desert, hospital, and reusable JSON environments | Traditional and RL controllers, ROS2, Nav2, Orsus, RealSense D455, LiDAR, UR5, Z1, and external policies |

The README summarizes the supported surface. Use the detailed guides as the source of truth:

- **[Getting Started](https://www.rosolab.com/roso-lab/eai-simulator/docs/getting_started.html):** install dependencies, request assets, and run the first environment.
- **[Build Environments](https://www.rosolab.com/roso-lab/eai-simulator/docs/environments.html):** JSON environments, Env DIY, 3D placement, and payloads.
- **[ROS2 Interfaces](https://www.rosolab.com/roso-lab/eai-simulator/docs/interface_catalog.html):** command input, sensor output, Nav2, and manipulators.
- **[Controller Development](https://www.rosolab.com/roso-lab/eai-simulator/docs/controller_guide.html):** traditional, RL, IK, and external controller integration.
- **[Human Asset Development](https://www.rosolab.com/roso-lab/eai-simulator/docs/human_assets_en.html):** 44 registry-driven actors, 12 standard actions, path following, and unified GUI/headless validation.

## Releases & Roadmap

- **[v0.1.0-beta.1 Release Notes](https://github.com/roso-lab/eai-simulator/releases/tag/v0.1.0-beta.1):** view the public release notes and downloads for the current beta.
- **[Roadmap](https://www.rosolab.com/roso-lab/eai-simulator/docs/roadmap.html):** discover the next-stage capabilities still under development and not yet included in the current release.

## Quick Start

Before starting, install **Ubuntu 22.04**, **Isaac Sim 5.1**, and **Isaac Lab 2.x** with its `env_isaaclab` conda environment. ROS2 is optional unless you use ROS2 or Nav2 workflows; Humble remains the validated system-ROS baseline.

Request access to the gated [EAI Simulator assets dataset](https://huggingface.co/datasets/rosolab/eai-simulator-assets), then run:

```bash
git clone https://github.com/roso-lab/eai-simulator.git
cd eai-simulator
conda activate env_isaaclab
./tools/setup/install_packages.sh
# Or select the Jazzy bridge backend for an already prepared Jazzy environment:
# ./tools/setup/install_packages.sh --ros-distro jazzy
hf auth login
python simulator.py --env robo
```

See the [installation guide](https://www.rosolab.com/roso-lab/eai-simulator/docs/installation.html) for conda initialization, asset configuration, ROS2 setup, and troubleshooting.

## Community & Support

- **[GitHub Discussions](https://github.com/roso-lab/eai-simulator/discussions):** questions, research scenarios, early ideas, and community proposals.
- **[GitHub Issues](https://github.com/roso-lab/eai-simulator/issues):** reproducible bugs, scoped feature requests, and documentation problems.
- **[Contributing](.github/CONTRIBUTING.md):** development setup, testing expectations, pull requests, and the GitHub/GitLab maintenance workflow.
- **[Support](.github/SUPPORT.md):** channel selection and support boundaries.
- **[Security](.github/SECURITY.md):** private vulnerability reporting. Do not report security issues publicly.

GitHub is the public community entry point. Maintainers continue canonical development in the internal GitLab repository and mirror public updates back to GitHub.

## License

The source code in this repository is available under the [MIT License](LICENSE). Isaac Sim, Isaac Lab, Omniverse components, third-party dependencies, and downloaded assets remain subject to their respective license terms.
