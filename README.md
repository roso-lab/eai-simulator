[English](README.md) | [中文](docs/README.zh-CN.md)

<p align="center">
  <img src="docs/source/_static/img/logo.png" alt="EAI Simulator logo" width="640">
</p>

# EAI Simulator

[![Platform](https://img.shields.io/badge/platform-linux--64-orange.svg)](https://releases.ubuntu.com/22.04/)
[![Isaac Sim](https://img.shields.io/badge/Isaac%20Sim-5.1-76B900.svg)](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/index.html)
[![License](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

EAI Simulator is a social-physical simulation platform built on Isaac Lab. It couples physical
simulation with social rules to create heterogeneous human-robot environments with roles,
information flow, multimodal sensing, and reusable control interfaces.

## Key Features

- Heterogeneous robots, humans, manipulators, sensors, and scenes.
- JSON environments and Env DIY workflows for composing simulations.
- Reinforcement learning, traditional control, and external policy integration.
- ROS2 interfaces for navigation, velocity control, and manipulators.
- Gated USD assets and policy weights downloaded from Hugging Face on demand.

## Prerequisites

- Linux (Ubuntu 22.04 recommended)
- Isaac Sim 5.1
- Isaac Lab 2.x and its conda environment
- ROS2 Humble for ROS2 and Nav2 workflows (optional)

## Quick Start

```bash
git clone https://github.com/Huang-Qijun/eai-simulator.git
cd eai-simulator
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate env_isaaclab
./tools/install_packages.sh
```

Request access to
[the EAI Simulator assets dataset](https://huggingface.co/datasets/HuangQIjun/eai-simulator-assets),
then authenticate and start the recommended example:

```bash
hf auth login
python simulator.py --env robo
```

## Documentation

See the [EAI Simulator documentation](https://www.rosolab.com/roso-lab/eai-simulator/docs/) for detailed
installation, configuration, and development guides. The documentation source lives in
[docs/source](docs/source).

## Community and Contributing

This GitHub repository is the public mirror and community entry point. Maintainers continue
canonical development in the internal GitLab repository, then mirror public updates back to GitHub.

- Use [GitHub Issues](https://github.com/Huang-Qijun/eai-simulator/issues) for reproducible bugs,
  scoped feature requests, and documentation problems.
- Use [GitHub Discussions](https://github.com/Huang-Qijun/eai-simulator/discussions) for questions,
  early ideas, and community proposals.
- Read [CONTRIBUTING.md](CONTRIBUTING.md), [SUPPORT.md](SUPPORT.md), and
  [docs/community_workflow.md](docs/community_workflow.md) before opening a pull request.
- Report vulnerabilities privately according to [SECURITY.md](SECURITY.md).

## License

The source code in this repository is available under the [MIT License](LICENSE). Isaac Sim,
Isaac Lab, Omniverse components, third-party dependencies, and downloaded assets remain subject
to their respective license terms.
