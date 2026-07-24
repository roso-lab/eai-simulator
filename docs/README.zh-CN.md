[English](../README.md) | [中文](README.zh-CN.md)

<p align="center">
  <img src="docs/source/_static/img/logo.png" alt="EAI Simulator 标志" width="640">
</p>

# EAI Simulator

[![Platform](https://img.shields.io/badge/platform-linux--64-orange.svg)](https://releases.ubuntu.com/22.04/)
[![Isaac Sim](https://img.shields.io/badge/Isaac%20Sim-5.1-76B900.svg)](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/index.html)
[![License](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

EAI Simulator 是一个基于 Isaac Lab 的社会化物理仿真平台。它将物理仿真与社会规则结合，
为控制算法提供包含角色、信息流、多模态感知和统一控制接口的异构人机环境。

## 核心能力

- 集成人类、异构机器人、机械臂、传感器和多类场景。
- 通过 JSON 环境和 Env DIY 工作流组合仿真任务。
- 支持强化学习、传统控制和外部策略接入。
- 提供导航、速度控制和机械臂相关的 ROS2 接口。
- 从 Hugging Face 按需下载受限访问的 USD 资产和策略权重。

## 前置条件

- Linux（推荐 Ubuntu 22.04）
- Isaac Sim 5.1
- Isaac Lab 2.x 及其 conda 环境
- ROS2 Humble（仅 ROS2 和 Nav2 工作流需要）

## 快速开始

```bash
git clone https://github.com/Huang-Qijun/eai-simulator.git
cd eai-simulator
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate env_isaaclab
./tools/install_packages.sh
```

先申请
[EAI Simulator 资产数据集](https://huggingface.co/datasets/HuangQIjun/eai-simulator-assets)访问权限，
然后登录并启动推荐示例：

```bash
hf auth login
python simulator.py --env robo
```

## 文档

详细的安装、配置与开发说明请参阅
[EAI Simulator 文档](https://www.rosolab.com/roso-lab/eai-simulator/docs/)。文档源文件位于
[docs/source](docs/source)。

## 社区与贡献

这个 GitHub 仓库是公开镜像和社区入口。维护者继续在内部 GitLab 仓库中进行主线开发，再将公开更新
镜像回 GitHub。

- 可复现 bug、范围清晰的功能请求和文档问题请使用
  [GitHub Issues](https://github.com/Huang-Qijun/eai-simulator/issues)。
- 问答、早期想法和社区提案请使用
  [GitHub Discussions](https://github.com/Huang-Qijun/eai-simulator/discussions)。
- 提交 Pull Request 前请阅读 [CONTRIBUTING.zh-CN.md](CONTRIBUTING.zh-CN.md)、
  [SUPPORT.zh-CN.md](SUPPORT.zh-CN.md) 和
  [community_workflow.zh-CN.md](community_workflow.zh-CN.md)。
- 安全漏洞请按照 [SECURITY.zh-CN.md](SECURITY.zh-CN.md) 私下报告。

## 许可证

本仓库源代码采用 [MIT License](LICENSE)。Isaac Sim、Isaac Lab、Omniverse 组件、第三方依赖和
下载资产仍分别受其各自许可证约束。
