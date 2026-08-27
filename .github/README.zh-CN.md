<p align="center">
  <a href="../README.md">English</a> · <a href="README.zh-CN.md">中文</a>
</p>

<p align="center">
  <img src="assets/logo.png" alt="EAI Simulator 标志" width="520">
</p>

<h1 align="center">EAI Simulator</h1>

<p align="center">
  <a href="https://releases.ubuntu.com/22.04/"><img src="https://img.shields.io/badge/platform-linux--64-orange.svg" alt="平台：Linux x86-64"></a>
  <a href="https://docs.isaacsim.omniverse.nvidia.com/5.1.0/index.html"><img src="https://img.shields.io/badge/Isaac%20Sim-5.1-76B900.svg" alt="Isaac Sim 5.1"></a>
  <a href="https://github.com/roso-lab/eai-simulator/releases/tag/v0.1.0-beta.1"><img src="https://img.shields.io/badge/release-v0.1.0--beta.1-007ec6.svg" alt="发布版本：v0.1.0-beta.1"></a>
  <a href="../LICENSE"><img src="https://img.shields.io/badge/license-MIT-yellow.svg" alt="许可证：MIT"></a>
</p>

<p align="center">
  <strong>面向人机共融研究的社会化物理仿真平台。</strong><br>
  基于 Isaac Lab 组合异构环境、连接感知与控制，并验证具身智能协作任务。
</p>

<p align="center">
  <a href="https://www.rosolab.com/roso-lab/eai-simulator/docs/getting_started.html"><strong>快速开始</strong></a> ·
  <a href="https://www.rosolab.com/roso-lab/eai-simulator/docs/"><strong>完整文档</strong></a> ·
  <a href="#社区与支持"><strong>社区</strong></a>
</p>

<p align="center">
  <img src="assets/demo.gif" alt="EAI Simulator 异构机器人与任务运行演示" width="960">
</p>

## 功能特性

<table>
<tr>
<td width="43%" valign="middle">
  <h3>分钟级搭建任意仿真环境</h3>
  <p>选一个场景、放置机器人、挂载负载与工具，包括 LiDAR、机械臂和相机，再接入外部控制器即可运行。可视化编辑器、仿真内三维插件和引导式终端三种工作流生成统一可复用的 JSON，环境可以像代码一样共享、版本化与复现。</p>
  <p><a href="https://www.rosolab.com/roso-lab/eai-simulator/docs/environments.html">环境指南 →</a></p>
</td>
<td width="57%">
  <a href="https://www.rosolab.com/roso-lab/eai-simulator/docs/environments.html"><img src="assets/env-diy.gif" alt="使用 EAI Env DIY 可视化编辑器组合并运行环境" width="100%"></a>
</td>
</tr>
<tr>
<td width="43%" valign="middle">
  <h3>开箱即用的资产库</h3>
  <p>13 种异构机器人、6 个场景、5 种可挂载负载、18 个控制器配置和 44 个带 12 组动作的人类角色，全部通过同一个 gated 资产数据集发布。</p>
  <p><a href="https://huggingface.co/datasets/rosolab/eai-simulator-assets">申请资产访问 →</a></p>
</td>
<td width="57%">
  <a href="https://huggingface.co/datasets/rosolab/eai-simulator-assets"><img src="assets/asset-library.gif" alt="EAI Simulator 资产库预览" width="100%"></a>
</td>
</tr>
<tr>
<td width="43%" valign="middle">
  <h3>连接感知与控制</h3>
  <p>挂载 Orsus、RealSense D455 或 LiDAR 传感与 UR5/Z1 机械臂，再通过 ROS2 速度命令、Nav2 或 RL 策略等外部控制器统一驱动，无需重建环境。</p>
  <p><a href="https://www.rosolab.com/roso-lab/eai-simulator/docs/interface_catalog.html">查看接口 →</a></p>
</td>
<td width="57%">
  <a href="https://www.rosolab.com/roso-lab/eai-simulator/docs/orsus_sensor.html"><img src="assets/orsus-demo.gif" alt="EAI Simulator 中的 Orsus 多模态传感输出" width="100%"></a>
</td>
</tr>
<tr>
<td width="43%" valign="middle">
  <h3>运行多智能体协作实验</h3>
  <p>内置 EMOS、TeamWeaver、2D 全局规划、db-CBS 多机器人导航、Nav2 集成和键盘控制 6 个可复用算法包，以及 Fire Rescue 工厂火灾巡检异构协同实验。</p>
  <p><a href="https://www.rosolab.com/roso-lab/eai-simulator/docs/getting_started.html#fire-rescue-demo">运行实验 →</a></p>
</td>
<td width="57%">
  <a href="https://www.rosolab.com/roso-lab/eai-simulator/docs/getting_started.html#fire-rescue-demo"><img src="assets/demo.gif" alt="EAI Simulator 异构机器人与任务运行演示" width="100%"></a>
</td>
</tr>
</table>

## 面向机器人研究

| 异构实体 | 可组合世界 | 感知与控制 |
| --- | --- | --- |
| Human、Carter、Pepper、MuSHR、Coco、Scout、Go2、B2、M20、Lite3、G1、CF2X、Iris 和 Pegasus | Plane、Warehouse、Factory、AIRS、Desert、Hospital 和可复用 JSON 环境 | 传统与 RL 控制器、ROS2、Nav2、Orsus、RealSense D455、LiDAR、UR5、Z1 和外部策略 |

详细能力以专题文档为准：

- **[快速开始](https://www.rosolab.com/roso-lab/eai-simulator/docs/getting_started.html)：** 安装依赖、申请资产并运行第一个环境。
- **[构建环境](https://www.rosolab.com/roso-lab/eai-simulator/docs/environments.html)：** JSON 环境、Env DIY、三维布置与 Payload。
- **[ROS2 接口](https://www.rosolab.com/roso-lab/eai-simulator/docs/interface_catalog.html)：** 控制输入、传感输出、Nav2 与机械臂。
- **[控制器开发](https://www.rosolab.com/roso-lab/eai-simulator/docs/controller_guide.html)：** 传统控制、RL、IK 和外部控制器接入。
- **[人类资产开发](https://www.rosolab.com/roso-lab/eai-simulator/docs/human_assets.html)：** 角色、标准动作、路径跟随与统一验证。

## 版本发布与功能规划

- **[v0.1.0-beta.1 发布说明](https://github.com/roso-lab/eai-simulator/releases/tag/v0.1.0-beta.1)：** 查看当前 beta 版本包含的能力、运行要求和已知注意事项。
- **[功能规划](https://www.rosolab.com/roso-lab/eai-simulator/docs/roadmap.html)：** 了解仍在开发、尚未包含在当前发布版本中的下一阶段能力。

## 快速开始

开始前需要安装 **Ubuntu 22.04**、**Isaac Sim 5.1**、**Isaac Lab 2.x**，并准备 Isaac Lab 的 `env_isaaclab` conda 环境。只有 ROS2 或 Nav2 工作流需要 ROS2；Humble 仍是经过验证的系统 ROS 基线。

先申请 gated [EAI Simulator 资产数据集](https://huggingface.co/datasets/rosolab/eai-simulator-assets)的访问权限，然后运行：

```bash
git clone https://github.com/roso-lab/eai-simulator.git
cd eai-simulator
conda activate env_isaaclab
./tools/setup/install_packages.sh
# 已准备 Jazzy 环境时可选择 Jazzy bridge：
# ./tools/setup/install_packages.sh --ros-distro jazzy
hf auth login
python simulator.py --env robo
```

conda 初始化、资产配置、ROS2 安装和常见问题见[安装指南](https://www.rosolab.com/roso-lab/eai-simulator/docs/installation.html)。

## 社区与支持

- **[GitHub Discussions](https://github.com/roso-lab/eai-simulator/discussions)：** 使用问题、研究场景、早期想法和社区提案。
- **[GitHub Issues](https://github.com/roso-lab/eai-simulator/issues)：** 可复现 bug、范围明确的功能请求和文档问题。
- **[贡献指南](CONTRIBUTING.zh-CN.md)：** 开发环境、测试要求和 Pull Request 流程。
- **[支持说明](SUPPORT.zh-CN.md)：** 渠道选择和支持边界。
- **[安全策略](SECURITY.zh-CN.md)：** 私密漏洞报告。请勿公开报告安全问题。

GitHub 是公开社区入口。维护者继续在内部 GitLab 仓库中进行主线开发，并将公开更新发布回 GitHub。

## 许可证

本仓库源代码采用 [MIT License](../LICENSE)。Isaac Sim、Isaac Lab、Omniverse 组件、第三方依赖和下载资产仍分别受其各自许可证约束。
