# Z1 资产与独立调试工具

Z1 在正式 EAI 环境中已经作为原生附件接入主 Isaac Sim：由 Env DIY/Builder 挂载，使用 `Z1_IK_CFG`，并通过 `/<robot>/z1/*` ROS2 Bridge/OmniGraph topic 控制。

本目录保留的 `run_z1_ros2_bridge.py`、`run_z1_ros2_bridge.sh`、`z1_joint_command.py` 和 `send_z1_joint_command.sh` 只用于 Z1 独立资产调试、USD 转换或排查材质/关节问题。它们会启动单独的 Isaac Sim，不应与 `python simulator.py ...` 同时运行。

## 正式环境控制

先在 Env DIY 中为 M20、Carter、Go2、B2、Lite3 或 Scout 添加 `z1` 附件。同一机器人不能同时挂载 UR5 和 Z1。

启动保存的环境：

```bash
cd eai-simulator
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate env_isaaclab
source /opt/ros/humble/setup.bash
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
python simulator.py --env=<env_name> --num_envs=1 --device=cuda:0
```

另开 ROS2 终端发送机械臂关节、完整 6D 位姿或夹爪命令：

```bash
cd eai-simulator
source /opt/ros/humble/setup.bash
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp

python3 algorithm/ros/tools/manipulator_command.py \
  --robot go2_1 --model z1 \
  --joint 0.0 0.8 -1.2 0.0 0.0 0.0 --wait

python3 algorithm/ros/tools/manipulator_command.py \
  --robot go2_1 --model z1 \
  --xyz 0.45 0.0 0.65 --quat 0 0 0 0 --frame-id world --wait

python3 algorithm/ros/tools/manipulator_command.py \
  --robot go2_1 --model z1 --gripper -0.20 --wait
```

完整 topic 和消息说明见 `docs/source/ur5_control.md`。

## 文件位置

- USD：`usd/payloads/manipulators/z1/z1_description.usda`
- URDF：`usd/payloads/manipulators/z1/urdf/z1_with_gripper.urdf`
- 原始描述包：`usd/payloads/manipulators/z1/source_description/`
- Isaac Lab 资产配置：`source/EAI_assets/EAI_assets/robots/z1.py`
- 多机器人挂载配置：`source/EAI_assets/EAI_assets/robots/z1_mount.py`
- 统一控制器：`source/EAI_assets/EAI_assets/controller/traditional/manipulator_ik/`

## 独立资产调试

只有在主 Simulator 未运行时才使用：

```bash
pgrep -af 'isaac|kit|SimulationApp'

source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate env_isaaclab
ISAACSIM_CONDA_ENV=env_isaaclab \
  bash algorithm/ros/z1/run_z1_ros2_bridge.sh --no-headless
```

独立调试链路使用旧的 `/z1/joint_commands` 和 `/z1/joint_states`，仅用于检查单体 USD。它不代表正式 EAI topic，不能用于验证多机器人 namespace、统一 IK 或夹爪接口。

## 重新转换 USD

```bash
cd eai-simulator
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate env_isaaclab
ISAACSIM_CONDA_ENV=env_isaaclab \
  bash algorithm/ros/z1/conversion/convert_z1_to_usd.sh
```

转换后需要重新检查关节名称、`link06` 末端、`jointGripper`、材质绑定和碰撞设置。
