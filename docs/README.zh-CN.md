<p align="center">
  <a href="../README.md">English</a> · <a href="README.zh-CN.md">中文</a>
</p>

<p align="center">
  <img src="source/_static/img/logo.png" alt="EAI Simulator 标志" width="520">
</p>

<h1 align="center">EAI Simulator</h1>

<p align="center">
  <a href="https://releases.ubuntu.com/22.04/"><img src="https://img.shields.io/badge/platform-linux--64-orange.svg" alt="平台：Linux x86-64"></a>
  <a href="https://docs.isaacsim.omniverse.nvidia.com/5.1.0/index.html"><img src="https://img.shields.io/badge/Isaac%20Sim-5.1-76B900.svg" alt="Isaac Sim 5.1"></a>
  <a href="https://github.com/roso-lab/eai-simulator/releases/tag/v0.1.0"><img src="https://img.shields.io/badge/release-v0.1.0-007ec6.svg" alt="发布版本：v0.1.0"></a>
  <a href="../LICENSE"><img src="https://img.shields.io/badge/license-MIT-yellow.svg" alt="许可证：MIT"></a>
</p>

<p align="center">
  <strong>面向人机共融研究的社会化物理仿真平台。</strong><br>
  基于 Isaac Lab 组合异构环境、连接感知与控制，并验证具身智能协作任务。
</p>

<p align="center">
  <a href="https://www.rosolab.com/roso-lab/eai-simulator/docs/getting_started.html"><strong>快速开始</strong></a> ·
  <a href="https://www.rosolab.com/roso-lab/eai-simulator/docs/"><strong>完整文档</strong></a> ·
  <a href="https://www.rosolab.com/roso-lab/eai-simulator/docs/community.html"><strong>社区</strong></a>
</p>

<p align="center">
  <img src="source/assets/media/demo.gif" alt="EAI Simulator 异构机器人与任务运行演示" width="960">
</p>

## 功能特性

<table>
<tr>
<td width="43%" valign="middle">
  <h3>世界、智能体、控制器——独立定义，自由组合</h3>
  <p>仿真世界定义、异构智能体配置、控制与感知接口——三者独立设计、独立组合。预置丰富的机器人、传感器和控制器，同时每个层面暴露标准接口，让外部算法和自定义 payload 解耦接入，不修改核心即可扩展。环境构建支持可视化编辑器、仿真内三维插件和引导式终端三种 Env DIY 工作流，均生成统一、可复用的 JSON 格式。</p>
  <p><a href="https://www.rosolab.com/roso-lab/eai-simulator/docs/environments.html">环境指南 →</a></p>
</td>
<td width="57%">
  <a href="https://www.rosolab.com/roso-lab/eai-simulator/docs/environments.html"><img src="source/assets/media/feature-可视化diy.gif" alt="使用 EAI Env DIY 可视化编辑器组合并运行环境" width="100%"></a>
</td>
</tr>
<tr>
<td width="43%" valign="middle">
  <h3>连接感知与控制</h3>
  <p>挂载 Orsus 或 LiDAR，并连接键盘控制、ROS2 速度命令、Nav2、UR5/Z1 机械臂或外部策略，无需重新实现环境。</p>
  <p><a href="https://www.rosolab.com/roso-lab/eai-simulator/docs/interface_catalog.html">查看接口 →</a></p>
</td>
<td width="57%">
  <a href="https://www.rosolab.com/roso-lab/eai-simulator/docs/orsus_sensor.html"><img src="source/assets/media/orsus_demo.gif" alt="EAI Simulator 中的 Orsus 多模态传感输出" width="100%"></a>
</td>
</tr>
<tr>
<td width="43%" valign="middle">
  <h3>运行多智能体协作实验</h3>
  <p>从导航和操作扩展到多智能体讨论、基于能力的任务分配，以及 Fire Rescue 等协同实验。</p>
  <p><a href="https://www.rosolab.com/roso-lab/eai-simulator/docs/getting_started.html#fire-rescue-demo">运行实验 →</a></p>
</td>
<td width="57%">
  <a href="https://www.rosolab.com/roso-lab/eai-simulator/docs/getting_started.html#fire-rescue-demo"><img src="source/assets/media/demo.gif" alt="EAI Simulator 异构机器人与任务运行演示" width="100%"></a>
</td>
</tr>
</table>

## 面向机器人研究

| 异构实体 | 可组合世界 | 感知与控制 |
| --- | --- | --- |
| Human、Carter、Pepper、MuSHR、Scout、Go2、B2、M20、Lite3、G1 和 CF2X | Plane、Warehouse、Factory、AIRS、Garden、Desert、Hospital 和可复用 JSON 环境 | 传统与 RL 控制器、ROS2、Nav2、Orsus、LiDAR、UR5、Z1 和外部策略 |

README 只概括支持范围，详细能力以专题文档为准：

- **[快速开始](https://www.rosolab.com/roso-lab/eai-simulator/docs/getting_started.html)：** 安装依赖、申请资产并运行第一个环境。
- **[构建环境](https://www.rosolab.com/roso-lab/eai-simulator/docs/environments.html)：** JSON 环境、Env DIY、三维布置与 Payload。
- **[ROS2 接口](https://www.rosolab.com/roso-lab/eai-simulator/docs/interface_catalog.html)：** 控制输入、传感输出、Nav2 与机械臂。
- **[控制器开发](https://www.rosolab.com/roso-lab/eai-simulator/docs/controller_guide.html)：** 传统控制、RL、IK 和外部控制器接入。

## 版本发布与功能规划

- **[v0.1.0 发布说明](https://github.com/roso-lab/eai-simulator/releases/tag/v0.1.0)：** 查看当前已发布版本包含的能力、运行要求和已知注意事项。
- **[功能规划](https://www.rosolab.com/roso-lab/eai-simulator/docs/roadmap.html)：** 了解仍在开发、尚未包含在当前发布版本中的下一阶段能力。

## 快速开始

开始前需要安装 **Ubuntu 22.04**、**Isaac Sim 5.1**、**Isaac Lab 2.x**，并准备 Isaac Lab 的 `env_isaaclab` conda 环境。只有 ROS2 或 Nav2 工作流需要 ROS2 Humble。

先申请 gated [EAI Simulator 资产数据集](https://huggingface.co/datasets/HuangQIjun/eai-simulator-assets)的访问权限，然后运行：

```bash
git clone https://github.com/roso-lab/eai-simulator.git
cd eai-simulator
conda activate env_isaaclab
./tools/install_packages.sh
hf auth login
python simulator.py --env robo
```

conda 初始化、资产配置、ROS2 安装和常见问题见[安装指南](https://www.rosolab.com/roso-lab/eai-simulator/docs/installation.html)。

## 社区与支持

- **[GitHub Discussions](https://github.com/roso-lab/eai-simulator/discussions)：** 使用问题、研究场景、早期想法和社区提案。
- **[GitHub Issues](https://github.com/roso-lab/eai-simulator/issues)：** 可复现 bug、范围明确的功能请求和文档问题。
- **[贡献指南](CONTRIBUTING.zh-CN.md)：** 开发环境、测试要求、Pull Request 和 GitHub/GitLab 维护流程。
- **[支持说明](SUPPORT.zh-CN.md)：** 渠道选择和支持边界。
- **[安全策略](SECURITY.zh-CN.md)：** 私密漏洞报告。请勿公开报告安全问题。

GitHub 是公开社区入口。维护者继续在内部 GitLab 仓库中进行主线开发，并将公开更新镜像回 GitHub。

## 许可证

本仓库源代码采用 [MIT License](../LICENSE)。Isaac Sim、Isaac Lab、Omniverse 组件、第三方依赖和下载资产仍分别受其各自许可证约束。
