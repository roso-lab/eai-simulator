# 安装指南

本文档详细说明如何安装和配置 EAI 平台。

## 前置要求

### 必需项

1. **Isaac Sim**:
   - 版本: Isaac Sim 5.1
   - 安装: 参考 [Isaac Lab 安装指南](https://isaac-sim.github.io/IsaacLab/main/source/setup/installation/index.html)

2. **Isaac Lab**:
   - 版本: Isaac Lab 2.x

3. **Python**: 使用 `env_isaaclab` 环境中随 Isaac Sim/Isaac Lab 配置的 Python

4. **运行设备**:
   - 普通机器人环境支持 CPU 或 CUDA GPU
   - 包含 human 的环境会自动使用 CPU PhysX，规避 Isaac Sim 5.1 GPU 姿态写入崩溃

5. **操作系统**:
   - Linux（Ubuntu 22.04 推荐）

### 可选项

- **ROS2 Humble**: 如果需要使用 GS-Hub 传感器和 ROS2 导航
- **Git**: 用于克隆仓库

## 安装步骤

### 1. 克隆仓库

```bash
git clone <repository_url>
cd eai-simulator
```

### 2. 激活 Isaac Lab 环境

```bash
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate env_isaaclab
```

### 3. 安装 EAI 包

使用提供的安装脚本（推荐）：

```bash
# 安装所有包
./tools/install_packages.sh

# 查看帮助
./tools/install_packages.sh -h

# 详细输出
./tools/install_packages.sh -v

# 卸载所有包
./tools/install_packages.sh -u
```

或者手动安装：

```bash
# 确保在 Isaac Lab 的 Python 环境中
pip install -e source/EAI
pip install -e source/EAI_assets
pip install -e source/EAI_hmrs
```

### 4. 申请 Hugging Face 资产权限

仿真运行需要的大体积 USD 资产和 RL 模型权重不直接放在 Git 仓库中，而是存放在 gated Hugging Face 数据集：
[HuangQIjun/eai-simulator-assets](https://huggingface.co/datasets/HuangQIjun/eai-simulator-assets)。

首次运行前，请打开上面的链接提交访问申请。账号通过后，在终端登录 Hugging Face：

```bash
hf auth login
```

`simulator.py` 启动时会自动检测缺失的 `usd/` 资产和 `source/EAI_assets/EAI_assets/controller/rl/` 下的模型文件，并从该数据集按需下载。高级用法：

```bash
# 使用其他兼容的 Hugging Face dataset
export EAI_ASSETS_HF_REPO=<namespace>/<dataset_name>

# 禁用自动下载，缺失资产时直接报错
export EAI_ASSETS_AUTO_DOWNLOAD=0
```

### 5. 验证安装

检查 JSON 环境配置：

```bash
find source/EAI_hmrs/EAI_hmrs/envs -maxdepth 1 -name '*.json' -printf '%f\n' | sort
```

环境不再注册到 Gym。每个名称对应
`source/EAI_hmrs/EAI_hmrs/envs/<env_name>.json`，例如 `EAI-Factory-v0.json`。

检查统一入口并启动一个 JSON 环境：

```bash
python simulator.py --help
python simulator.py --env robo
```

## ROS2 配置（可选）

### 安装 ROS2 Humble

如果您需要使用 GS-Hub 传感器和 ROS2 导航：

```bash
# Ubuntu 22.04
sudo apt install software-properties-common
sudo add-apt-repository universe
sudo apt update && sudo apt install curl -y
sudo curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.asc | sudo apt-key add -
sudo sh -c 'echo "deb http://packages.ros.org/ros/ubuntu $(lsb_release -cs) main" > /etc/apt/sources.list.d/ros-latest.list'
sudo apt update
sudo apt install ros-humble-desktop-full

# 设置环境变量
echo "source /opt/ros/humble/setup.bash" >> ~/.bashrc
source ~/.bashrc
```

### 安装 Navigation2（可选）

```bash
sudo apt install ros-humble-navigation2
sudo apt install ros-humble-nav2-bringup
```

### 安装 Isaac Sim ROS2 Bridge

Isaac Sim ROS2 Bridge 通常随 Isaac Sim 一起安装。如果未安装：

1. 通过 Omniverse Launcher 安装扩展
2. 或参考 [Isaac Sim ROS2 Bridge 文档](https://docs.omniverse.nvidia.com/app_isaacsim/app_isaacsim/install_ext_ros_bridge.html)

## IDE 配置（可选）

### VSCode

1. **运行 VSCode 任务**:
   - 按 `Ctrl+Shift+P`
   - 选择 `Tasks: Run Task`
   - 运行 `setup_python_env`
   - 输入 Isaac Sim 安装路径

2. **配置 Python 解释器**:
   - 选择 Isaac Lab 的 Python 环境
   - 确保 `.vscode/.python.env` 已创建

3. **配置 Pylance**（如果需要）:
   
   在 `.vscode/settings.json` 中添加：

   ```json
   {
     "python.analysis.extraPaths": [
       "<path-to-project>/source/EAI",
       "<path-to-project>/source/EAI_assets",
       "<path-to-project>/source/EAI_hmrs"
     ]
   }
   ```

## 常见问题

### Q1: `ModuleNotFoundError: No module named 'EAI'`

**原因**: EAI 包未正确安装

**解决**:
```bash
pip install -e source/EAI
pip install -e source/EAI_assets
pip install -e source/EAI_hmrs
```

### Q2: `CUDA out of memory`

**原因**: GPU 内存不足

**解决**:
- 减少并行环境数量: `--num_envs=1`
- 关闭其他 GPU 进程
- 使用较小的模型

### Q3: ROS2 话题未发布

**原因**: ROS2 环境未正确配置

**解决**:
```bash
# 检查 ROS2 环境
echo $ROS_DISTRO  # 应该输出 "humble"

# 如果未设置，手动设置
source /opt/ros/humble/setup.bash
export ROS_DISTRO=humble
```

### Q4: 模型文件未找到

**原因**: 模型路径配置错误或模型文件不存在

**解决**:
- 检查 `source/EAI_assets/EAI_assets/controller/rl/*/model/` 目录
- 确认模型文件存在（`.pt` 或 `.onnx`）
- 检查控制器配置中的 `model_path`

### Q5: Isaac Sim 无法启动

**原因**: 多种可能（GPU 驱动、CUDA 版本、权限等）

**解决**:
- 检查 GPU 驱动: `nvidia-smi`
- 检查 CUDA 版本: `nvcc --version`
- 查看 Isaac Sim 日志
- 参考 [Isaac Sim 故障排除指南](https://docs.omniverse.nvidia.com/app_isaacsim/app_isaacsim/troubleshooting.html)

## 卸载

卸载所有 EAI 包：

```bash
./tools/install_packages.sh -u
```

或手动卸载：

```bash
pip uninstall EAI EAI-assets EAI-hmrs
```

## 开发模式安装

如果您需要修改源代码，使用可编辑安装（已包含在安装脚本中）：

```bash
pip install -e source/EAI
pip install -e source/EAI_assets
pip install -e source/EAI_hmrs
```

这样修改源代码后无需重新安装。

## 验证安装完整性

运行入口测试并启动 JSON 环境：

```bash
python -m unittest source.EAI_assets.test.test_simulator_entry
python simulator.py --env=EAI-Factory-v0 --num_envs=1 --device=cuda:0
```

## 下一步

- 查看 :doc:`快速开始指南 <getting_started>`
- 查看 :doc:`项目概览 <project_overview>`
- 查看 :doc:`环境说明 <environments>`
