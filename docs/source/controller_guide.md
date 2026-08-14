# 控制器开发

本文档详细说明 EAI 平台的控制器架构和如何添加自定义控制器。

## 控制器架构概述

### ControllerCfg 基类

所有控制器配置类都继承自 `ControllerCfg`，位于 `source/EAI/EAI/controllers/base.py`。

```python
@configclass
class ControllerCfg:
    """控制器配置基类"""
    
    robot_type: str = "Unknown"
    """机器人类型标识符"""
    
    observation_func: Optional[ObsFunc] = None
    """观测计算函数: (env, robot_name) -> observation_tensor"""
    
    apply_action_func: Optional[ApplyActionFunc] = None
    """动作应用函数: (env, robot_name, action, controller_dict) -> None"""
    
    compute_action_from_command_func: Optional[ComputeActionFromCommandFunc] = None
    """命令转动作函数: (controller_cfg, env, robot_name, command, controller_dict) -> action_tensor"""
    
    def compute_observations(self, env, robot_name) -> torch.Tensor:
        """计算观测（调用 observation_func）"""
    
    def compute_action(self, env, robot_name, observations, controller_dict) -> torch.Tensor:
        """从观测计算动作（子类实现）"""
    
    def compute_action_from_command(self, env, robot_name, command, controller_dict) -> torch.Tensor:
        """从命令计算动作（调用 compute_action_from_command_func）"""
    
    def apply_action(self, env, robot_name, action, controller_dict) -> None:
        """应用动作（调用 apply_action_func）"""
    
    def load(self, robot_name, task_name, device, env) -> Dict[str, Any]:
        """加载控制器资源（子类实现）"""
```

### 控制器工作流程

```
用户脚本
    │
    ├─> env.step(actions)  # actions: {robot_name: command_tensor}
    │
    └─> MultiRobotDirectEnv._pre_physics_step(actions)
            │
            ├─> 对每个机器人:
            │   controller_cfg.compute_action_from_command(
            │       env, robot_name, command, controller_dict
            │   )
            │   │
            │   ├─> [传统控制器] 直接转换命令为动作
            │   │   action = compute_action_from_command_func(...)
            │   │
            │   └─> [RL控制器] 设置命令 -> 计算观测 -> 策略计算动作
            │       env.set_command(...)
            │       obs = observation_func(env, robot_name)
            │       action = policy.act(obs)
            │
            └─> 存储计算后的动作到 self.actions_dict

    └─> MultiRobotDirectEnv._apply_action()
            │
            └─> 对每个机器人:
                controller_cfg.apply_action(
                    env, robot_name, action, controller_dict
                )
                │
                └─> apply_action_func(env, robot_name, action, controller_dict)
```

### Env DIY 中的默认 controller cfg

轻量窗口、终端快速模式和 Isaac Sim 3D 插件共用同一个 catalog。宿主机器人默认配置如下；选择 `manual` 时，JSON 中的 `controller.cfg` 必须仍然是 `env_builder.py` 可以解析的配置名。

| 宿主机器人 | 默认 cfg | 类型 |
|---|---|---|
| Carter | `CARTER_DIFF_CFG` | 差速驱动 |
| Pepper | `PEPPER_HOLONOMIC_CFG` | 全向驱动 |
| Go2 | `GO2_VELOCITY_RSL_CFG` | RSL-RL 速度策略 |
| B2 | `B2_VELOCITY_RSL_CFG` | RSL-RL 速度策略 |
| M20 | `M20_ROUGH_RSL_CFG` | RSL-RL 粗糙地形策略 |
| Lite3 | `LITE3_VELOCITY_RSL_CFG` | RSL-RL 速度策略 |
| Scout | `SCOUT_DIFF_CFG` | 差速驱动 |
| G1 | `G1_SKRL_CFG` | SKRL PPO |
| CF2X | `QUADCOPTER_GOAL_SKRL_CFG` | SKRL 目标位置 |

UR5 和 Z1 不属于宿主 controller，而是挂载到宿主后的 auxiliary controller：`UR5_IK_CFG` 和 `Z1_IK_CFG`。它们都来自 `ManipulatorIkControllerCfg`，并由宿主 selection 中的实际附件实例触发；不会为未挂载的机械臂创建 articulation 或 ROS2 topic。

## 已包含的控制器

### 1. DifferentialDriveControllerCfg（差速驱动控制器）

**文件位置**: `source/EAI/EAI/controllers/differential_drive_controller.py`

**用途**: 传统控制器，用于差速驱动机器人（如 Carter 小车）

**需要定义的函数**:

1. **`compute_action_from_command_func`**: 将速度命令转换为轮子速度
   - **目的**: 实现差速驱动运动学，将 `[vx, wz]` 转换为 `[left_wheel_vel, right_wheel_vel]`
   - **示例**: `source/EAI_assets/EAI_assets/controller/traditional/carter_diff/carter_diff.py:13-61`

2. **`apply_action_func`**: 将轮子速度应用到机器人
   - **目的**: 设置左右轮的目标速度
   - **示例**: `source/EAI_assets/EAI_assets/controller/traditional/carter_diff/carter_diff.py:64-124`

**配置示例**:
```python
CARTER_DIFF_CFG = DifferentialDriveControllerCfg(
    robot_type="Carter",
    wheel_base=0.413,  # 轮距
    wheel_radius=0.14,  # 轮子半径
    left_wheel_joint_name="joint_wheel_left",
    right_wheel_joint_name="joint_wheel_right",
    apply_action_func=apply_carter_action,
    compute_action_from_command_func=compute_differential_drive_action_from_command,
)
```

### 2. SKRLControllerCfg（SKRL 强化学习控制器）

**文件位置**: `source/EAI/EAI/controllers/skrl_controller.py`

**用途**: 加载 PyTorch 格式的预训练 SKRL 策略并执行推理。

**需要定义的函数**:

1. **`observation_func`**: 计算观测
   - **目的**: 从机器人状态计算观测张量
   - **签名**: `(env: Any, robot_name: str) -> torch.Tensor`
   - **返回**: 形状为 `(num_envs, obs_dim)` 的观测张量

2. **`apply_action_func`**: 应用动作到机器人
   - **目的**: 将策略输出的动作应用到机器人关节/执行器
   - **签名**: `(env: Any, robot_name: str, action: torch.Tensor, controller_dict: Dict[str, Any]) -> None`

3. **`compute_action_from_command_func`**: 从命令计算动作（可选）
   - **目的**: 对于速度控制，设置命令后计算观测并使用策略计算动作
   - **默认**: 使用 `compute_skrl_action_from_command`（通用实现）
   - **特殊场景**: 目标位置控制（如无人机）需要自定义

运行时直接使用仓库已经提供的控制器配置，例如 `G1_SKRL_CFG` 和 `QUADCOPTER_GOAL_SKRL_CFG`；本文档只说明预训练策略的加载与推理接口。

**已实现的机器人**:
- **G1**: `source/EAI_assets/EAI_assets/controller/rl/g1_skrl/g1_skrl.py`
  - 观测: 包含速度、姿态、关节状态等
  - 动作: 29维关节位置
- **Quadcopter**: `source/EAI_assets/EAI_assets/controller/rl/quadcopter_goal_skrl/quadcopter_goal_skrl.py`
  - 观测: 12维（速度、姿态、目标位置相对坐标）
  - 动作: 4维（推力、力矩）

### 3. RSLControllerCfg（RSL-RL ONNX 控制器）

**文件位置**: `source/EAI/EAI/controllers/rsl_controller.py`

**用途**: 加载 ONNX 格式的预训练 RSL-RL 策略并执行推理。

**需要定义的函数**:

1. **`observation_func`**: 计算观测（与 SKRL 相同）
   - **目的**: 从机器人状态计算观测张量
   - **注意**: 输入张量必须满足随模型发布的维度、顺序、缩放和裁剪约定

2. **`apply_action_func`**: 应用动作到机器人（与 SKRL 相同）
   - **目的**: 将策略输出的动作应用到机器人

3. **`compute_action_from_command_func`**: 从命令计算动作（可选）
   - **目的**: 与 SKRL 相同
   - **默认**: 使用 `compute_rsl_action_from_command`（通用实现）

**配置示例**:
```python
GO2_VELOCITY_RSL_CFG = Go2VelocityRSLControllerCfg(
    model_path=str(_RL_DIR / "model" / "policy.onnx"),
    robot_type="Go2Velocity",
    observation_func=compute_go2_velocity_observations,
    apply_action_func=apply_go2_velocity_action,
    compute_action_from_command_func=compute_rsl_action_from_command,
)
```

**已实现的机器人**:
- **Go2 Velocity**: `source/EAI_assets/EAI_assets/controller/rl/go2_rsl_rl/go2_rsl_rl.py`
  - 观测: 45维（速度、重力方向、命令、12个关节状态、上一动作）
  - 动作: 12维关节位置
- **M20 Rough**: `source/EAI_assets/EAI_assets/controller/rl/m20_rough_rsl/m20_rough_rsl.py`
  - 观测: 48维（包含速度、姿态、16个关节状态等）
  - 动作: 16维（12个腿关节位置 + 4个轮子速度）

### 4. ManipulatorIkControllerCfg（UR5/Z1）

**基础实现**：`source/EAI_assets/EAI_assets/controller/traditional/manipulator_ik/manipulator_ik.py`

| 配置 | 模型 spec | 命令 | 结果 |
|---|---|---|---|
| `UR5_IK_CFG` | `UR5_MODEL_SPEC`，六轴 UR5 关节 | 六轴关节位置或 `target_pose` | 关节目标限幅后写入 `<robot>_arm`，发布 UR5 状态 |
| `Z1_IK_CFG` | `Z1_MODEL_SPEC`，六轴 Z1 关节 + `jointGripper` | 六轴关节位置、`target_pose` 或独立夹爪位置 | 机械臂和夹爪分别限幅并发布状态 |

`target_pose` 使用 DLS Differential IK（`lambda_val=0.02`），目标可以用 `world` 或 `base_link` 表示；`base_link` 会先通过宿主根位姿转换到 world。关节命令不经过 IK，直接按模型关节名重排并写入目标。每次控制循环最多施加 `0.10 rad` 的关节变化，避免外部位姿目标导致突跳。

ROS2 OmniGraph 只在 selection 中存在对应附件时建立。消息进入 `ManipulatorOmniGraphManager` 后按机器人实例名和机械臂型号隔离，`m20_1` 的命令不会被 `m20_2` 消费。reset 时会清理命令、IK 平滑状态和夹爪状态。

详细 topic、消息类型、Z1 夹爪命令和 `manipulator_command.py` 示例见 :doc:`机械臂 <ur5_control>`。


## 如何定义新控制器

定义新控制器分为两个层次：

1. **创建控制器基础类**（在 `source/EAI/EAI/controllers/`）：定义控制器类型（如 MPC、PID 等）
2. **创建具体控制器配置**（在 `source/EAI_assets/EAI_assets/controller/`）：定义特定机器人的控制器实例

### 步骤 1: 创建控制器基础类（在 `source/EAI/EAI/controllers/`）

如果需要定义新的控制器类型（如 **MPC**、**PID**、**ILC** 等），需要在 `source/EAI/EAI/controllers/` 目录下创建新的基础类。

**何时需要创建基础类？**
- 控制器类型与现有类型（SKRL、RSL、差速驱动）完全不同
- 需要定义控制器类型特定的参数和加载逻辑
- 多个机器人可能共享相同的控制器类型

**何时不需要创建基础类？**
- 控制器只是现有类型的变体（如新的 SKRL 控制器）→ 直接使用 `SKRLControllerCfg`
- 控制器类型相同，只是参数不同 → 在 `EAI_assets` 中创建配置实例

#### 1.1 创建基础类文件

在 `source/EAI/EAI/controllers/` 下创建新文件，例如 `mpc_controller.py`:

如果需要定义新的控制器类型（如 MPC、PID、ILC 等），需要在 `source/EAI/EAI/controllers/` 目录下创建新的基础类。

#### 1.1 创建基础类文件

在 `source/EAI/EAI/controllers/` 下创建新文件，例如 `mpc_controller.py`:

```python
# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause

"""MPC Controller Configuration with Loader Support."""

import torch
from typing import Optional, Any, Dict
from isaaclab.utils import configclass
from pathlib import Path
from .base import ControllerCfg


@configclass
class MPCControllerCfg(ControllerCfg):
    """Base configuration class for MPC Controllers.
    
    This base class defines all common MPC-related parameters for robot controllers.
    
    Subclasses should inherit from this class and can override default values or add
    robot-specific parameters.
    """
    
    # MPC 特定参数
    horizon: int = 10
    """预测时域长度"""
    
    dt: float = 0.02
    """控制周期（秒）"""
    
    model_path: Optional[str] = None
    """MPC 模型路径（如果使用学习模型）"""
    
    # 约束参数
    u_min: Optional[torch.Tensor] = None
    """控制输入下界"""
    
    u_max: Optional[torch.Tensor] = None
    """控制输入上界"""
    
    def compute_action(
        self, 
        env: Any, 
        robot_name: str, 
        observations: Optional[torch.Tensor],
        controller_dict: Dict[str, Any]
    ) -> torch.Tensor:
        """Compute action from observations using MPC solver.
        
        Args:
            env: Environment instance
            robot_name: Name of the robot
            observations: Observation tensor (包含状态信息)
            controller_dict: Dictionary containing loaded MPC solver
            
        Returns:
            Action tensor of shape (num_envs, action_dim)
        """
        if observations is None:
            raise ValueError(f"MPC controller for {robot_name} requires observations")
        
        mpc_solver = controller_dict.get('solver')
        if mpc_solver is None:
            raise ValueError(f"MPC solver not found in controller_dict for {robot_name}")
        
        # 调用 MPC 求解器
        # 这里需要根据具体的 MPC 实现来调用
        action = mpc_solver.solve(observations)
        
        return action
    
    def load(
        self,
        robot_name: str,
        task_name: str,
        device: str,
        env: Any,
    ) -> Dict[str, Any]:
        """Load MPC controller resources (solver, model, etc.).
        
        Args:
            robot_name: Name of the robot
            task_name: Task name for config registry (unused)
            device: Device string (e.g., "cuda:0")
            env: Environment instance
            
        Returns:
            Dictionary with controller metadata and loaded resources
        """
        # 创建或加载 MPC 求解器
        # mpc_solver = create_mpc_solver(...)
        
        return {
            'name': robot_name,
            'solver': None,  # 实际的 MPC 求解器实例
        }
```

#### 1.2 在 `__init__.py` 中导出

在 `source/EAI/EAI/controllers/__init__.py` 中添加导出：

```python
from .mpc_controller import MPCControllerCfg

__all__ = [
    # ... existing exports ...
    "MPCControllerCfg",
]
```

#### 1.3 基础类必须实现的方法

所有继承自 `ControllerCfg` 的基础类**必须实现**以下方法：

##### `load()` 方法（必须实现）

**签名**:
```python
def load(
    self,
    robot_name: str,
    task_name: str,
    device: str,
    env: Any,
) -> Dict[str, Any]:
```

**职责**: 加载控制器资源（模型、求解器、参数等）

**调用时机**: 环境初始化时，通过 `load_all_controllers()` 统一调用

**返回值**: `Dict[str, Any]`，必须包含：
- `'name': robot_name`（必需）
- 其他控制器特定资源（如 `'policy'`, `'solver'`, `'model'` 等）

**示例**:
```python
def load(self, robot_name, task_name, device, env) -> Dict[str, Any]:
    # SKRL: 加载 PyTorch 模型
    # RSL: 加载 ONNX 模型
    # MPC: 初始化求解器
    # 传统控制器: 返回基本元数据
    
    return {
        'name': robot_name,
        'solver': mpc_solver,  # 或 'policy', 'model' 等
    }
```

##### `compute_action()` 方法（必须实现）

**签名**:
```python
def compute_action(
    self, 
    env: Any, 
    robot_name: str, 
    observations: Optional[torch.Tensor],
    controller_dict: Dict[str, Any]
) -> torch.Tensor:
```

**职责**: 从观测计算动作

**调用时机**: 在 `compute_action_from_command` 中调用（对于需要观测的控制器）

**参数**:
- `observations`: 观测张量（形状: `(num_envs, obs_dim)`）
- `controller_dict`: 包含 `load()` 方法加载的资源

**返回值**: 动作张量（形状: `(num_envs, action_dim)`）

**示例**:
```python
def compute_action(self, env, robot_name, observations, controller_dict):
    # SKRL/RSL: 调用策略网络
    policy = controller_dict['policy']
    action = policy.act({"states": observations})[0]
    
    # MPC: 调用求解器
    solver = controller_dict['solver']
    action = solver.solve(observations)
    
    return action
```

**注意**: 对于纯命令驱动的控制器（如差速驱动），可以返回占位符，因为实际动作来自 `compute_action_from_command`

##### 可选方法

- `resolve_model_path()`: 解析模型路径（如果需要加载模型）
- 其他控制器特定的辅助方法（如 MPC 的参数设置、约束处理等）

#### 1.4 基础类的职责

控制器基础类应该：
- 定义控制器类型的**通用参数**（如 MPC 的 `horizon`、`dt`）
- 实现**通用的加载逻辑**（如 MPC 求解器初始化）
- 实现**通用的动作计算逻辑**（如 MPC 求解流程）
- 不在基础类中包含机器人特定的实现；这些实现应放在具体配置中

#### 1.5 与 ControllerCfg 的兼容性

基础类必须继承自 `ControllerCfg`，这样才能：
- 与 `load_all_controllers()` 统一加载机制兼容
- 与环境系统的 `_pre_physics_step`、`_get_observations`、`_apply_action` 接口兼容
- 支持函数式接口（`observation_func`、`apply_action_func`、`compute_action_from_command_func`）

**关键点**:
- 基础类**继承** `ControllerCfg` 的所有方法和属性
- 基础类**重写** `load()` 和 `compute_action()` 方法
- 基础类可以**添加**控制器类型特定的参数（如 MPC 的 `horizon`）
- 机器人特定的函数（`observation_func` 等）在**具体配置**中定义，不在基础类中

#### 1.6 参考示例

查看现有的控制器基础类实现：

- **SKRL 控制器**: `source/EAI/EAI/controllers/skrl_controller.py`
  - 实现了 `load()` 方法（加载 PyTorch 模型）
  - 实现了 `compute_action()` 方法（调用策略网络）
  
- **RSL 控制器**: `source/EAI/EAI/controllers/rsl_controller.py`
  - 实现了 `load()` 方法（加载 ONNX 模型）
  - 实现了 `compute_action()` 方法（调用 ONNX 推理）
  - 定义了 `model_path` 参数
  
- **差速驱动控制器**: `source/EAI/EAI/controllers/differential_drive_controller.py`
  - 实现了 `load()` 方法（返回基本元数据，无需加载模型）
  - 实现了 `compute_action()` 方法（返回占位符，因为动作来自命令）
  - 定义了 `wheel_base`、`wheel_radius` 等参数

### 步骤 2: 创建具体控制器配置（在 `source/EAI_assets/EAI_assets/controller/`）

创建控制器配置实例，使用步骤 1 中定义的基础类（或现有基础类）。

#### 2.1 创建配置文件

在 `source/EAI_assets/EAI_assets/controller/` 下创建合适的目录结构：

- **传统控制器**: `traditional/your_robot_name/your_robot_name.py`
- **MPC 控制器**: `mpc/your_robot_name_mpc/your_robot_name_mpc.py`

#### 2.2 定义所需的函数

根据控制器类型，定义以下函数：

#### 对于传统控制器（如差速驱动）

1. **`compute_action_from_command_func`**: 命令转动作
```python
def compute_your_robot_action_from_command(
    controller_cfg: Any,
    env: Any,
    robot_name: str,
    command: torch.Tensor,
    controller_dict: Dict[str, Any]
) -> torch.Tensor:
    """将命令转换为动作"""
    # 实现转换逻辑
    # 例如：速度命令 -> 关节速度
    return action
```

2. **`apply_action_func`**: 应用动作
```python
def apply_your_robot_action(
    env: Any,
    robot_name: str,
    action: torch.Tensor,
    controller_dict: Dict[str, Any]
) -> None:
    """应用动作到机器人"""
    robot = env.scene.articulations[robot_name]
    # 实现动作应用逻辑
    # 例如：设置关节速度
    robot.set_joint_velocity_target(action, joint_ids=joint_ids)
```

#### 2.3 创建控制器配置实例

使用新定义的基础类：

```python
from EAI.controllers import MPCControllerCfg  # 新定义的基础类

# 对于 MPC 控制器
YOUR_ROBOT_MPC_CFG = MPCControllerCfg(
    robot_type="YourRobot",
    horizon=20,  # MPC 特定参数
    dt=0.01,
    observation_func=compute_your_robot_observations,
    apply_action_func=apply_your_robot_action,
    compute_action_from_command_func=compute_mpc_action_from_command,  # 可选
)
```

#### 2.4 在 JSON 环境中使用

先将控制器加入
`source/EAI_hmrs/EAI_hmrs/env_builder.py` 的 `CONTROLLER_CFG_IMPORTS`，再在
`source/EAI_hmrs/EAI_hmrs/envs/<env_name>.json` 中选择配置名：

```json
{
  "scene_key": "factory",
  "task_name": "your_robot_demo",
  "version": 1,
  "robots": [
    {
      "type": "your_robot",
      "controller": {
        "mode": "default",
        "cfg": "YOUR_ROBOT_CFG"
      },
      "attachments": []
    }
  ]
}
```

```bash
python simulator.py --env=your_robot_demo --device=cuda:0
```

## 函数接口详解

### observation_func

**类型**: `ObsFunc = Callable[[Any, str], torch.Tensor]`

**签名**: `(env: Any, robot_name: str) -> torch.Tensor`

**目的**: 计算机器人的观测张量，用于 RL 策略推理。

**参数**:
- `env`: 环境实例（`MultiRobotDirectEnv`）
- `robot_name`: 机器人名称

**返回**: 观测张量，形状为 `(num_envs, obs_dim)`

**重要提示**:
- 观测顺序、维度、缩放和裁剪必须满足已发布模型的输入约定
- 使用 `env.get_command(robot_name, "command_name")` 获取命令
- 使用 `env.actions_dict.get(robot_name)` 获取上一时刻动作

### apply_action_func

**类型**: `ApplyActionFunc = Callable[[Any, str, torch.Tensor, Dict[str, Any]], None]`

**签名**: `(env: Any, robot_name: str, action: torch.Tensor, controller_dict: Dict[str, Any]) -> None`

**目的**: 将策略输出的动作应用到机器人。

**参数**:
- `env`: 环境实例
- `robot_name`: 机器人名称
- `action`: 动作张量，形状为 `(num_envs, action_dim)`
- `controller_dict`: 控制器字典（包含已加载的策略等）

**操作**: 通常调用 `robot.set_joint_position_target()` 或 `robot.set_joint_velocity_target()` 等。

**重要提示**:
- 动作缩放和偏移必须满足已发布模型的输出约定
- 确保动作维度与机器人关节数匹配
- 对于混合控制（如 M20：腿关节位置 + 轮子速度），需要分别处理

### compute_action_from_command_func

**类型**: `ComputeActionFromCommandFunc = Callable[[Any, Any, str, torch.Tensor, Dict[str, Any]], torch.Tensor]`

**签名**: `(controller_cfg: Any, env: Any, robot_name: str, command: torch.Tensor, controller_dict: Dict[str, Any]) -> torch.Tensor`

**目的**: 将外部命令（如速度命令）转换为动作。

**参数**:
- `controller_cfg`: 控制器配置实例
- `env`: 环境实例
- `robot_name`: 机器人名称
- `command`: 命令张量（如速度命令 `[vx, vy, wz]`）
- `controller_dict`: 控制器字典

**返回**: 动作张量，形状为 `(num_envs, action_dim)`

**工作流程**（RL 控制器）:
1. 设置命令到环境: `env.set_command(robot_name, command_name, command)`
2. 计算观测: `obs = controller_cfg.compute_observations(env, robot_name)`
3. 使用策略计算动作: `action = controller_cfg.compute_action(env, robot_name, obs, controller_dict)`

**重要提示**:
- 对于传统控制器，直接转换命令为动作
- 对于 RL 控制器，通常使用默认实现 `compute_skrl_action_from_command` 或 `compute_rsl_action_from_command`
- 对于目标位置控制（如无人机），需要自定义处理零向量占位符

## 控制器基础类详解

### ControllerCfg 基类接口

所有控制器基础类必须继承自 `ControllerCfg`（位于 `source/EAI/EAI/controllers/base.py`）：

```python
@configclass
class ControllerCfg:
    """所有控制器的基础配置类"""
    
    robot_type: str = "Unknown"
    """机器人类型标识符"""
    
    observation_func: Optional[ObsFunc] = None
    """观测计算函数: (env, robot_name) -> tensor"""
    
    apply_action_func: Optional[ApplyActionFunc] = None
    """动作应用函数: (env, robot_name, action, controller_dict) -> None"""
    
    compute_action_from_command_func: Optional[ComputeActionFromCommandFunc] = None
    """命令转动作函数: (controller_cfg, env, robot_name, command, controller_dict) -> action_tensor"""
    
    # 方法（子类必须实现）
    def load(...) -> Dict[str, Any]: ...
    def compute_action(...) -> torch.Tensor: ...
    
    # 方法（基类已实现，可直接使用）
    def compute_observations(...) -> torch.Tensor: ...
    def compute_action_from_command(...) -> torch.Tensor: ...
    def apply_action(...) -> None: ...
```

### 基础类 vs 具体配置

| 层次 | 位置 | 职责 | 示例 |
|------|------|------|------|
| **基础类** | `source/EAI/EAI/controllers/` | 定义控制器类型的通用接口和参数 | `SKRLControllerCfg`, `MPCControllerCfg`, `PIDControllerCfg` |
| **具体配置** | `source/EAI_assets/EAI_assets/controller/` | 定义特定机器人的控制器实例 | `GO2_VELOCITY_RSL_CFG`, `CARTER_DIFF_CFG` |

**设计原则**:
- 基础类定义**控制器类型的通用逻辑**
- 具体配置定义**机器人特定的参数和函数**

### 创建 MPC 控制器基础类的完整示例

#### 步骤 1: 创建基础类文件

```python
# source/EAI/EAI/controllers/mpc_controller.py

import torch
from typing import Optional, Any, Dict
from isaaclab.utils import configclass
from .base import ControllerCfg


@configclass
class MPCControllerCfg(ControllerCfg):
    """MPC 控制器基础配置类"""
    
    horizon: int = 10
    """预测时域长度"""
    
    dt: float = 0.02
    """控制周期（秒）"""
    
    def compute_action(self, env, robot_name, observations, controller_dict):
        solver = controller_dict['solver']
        action = solver.solve(observations)
        return action
    
    def load(self, robot_name, task_name, device, env):
        # 初始化 MPC 求解器
        solver = create_mpc_solver(horizon=self.horizon, dt=self.dt)
        return {'name': robot_name, 'solver': solver}
```

#### 步骤 2: 导出基础类

```python
# source/EAI/EAI/controllers/__init__.py

from .mpc_controller import MPCControllerCfg

__all__ = [
    # ... existing ...
    "MPCControllerCfg",
]
```

#### 步骤 3: 创建具体配置

```python
# source/EAI_assets/EAI_assets/controller/mpc/your_robot_mpc/your_robot_mpc.py

from EAI.controllers import MPCControllerCfg

YOUR_ROBOT_MPC_CFG = MPCControllerCfg(
    robot_type="YourRobot",
    horizon=20,
    dt=0.01,
    observation_func=compute_your_robot_observations,
    apply_action_func=apply_your_robot_action,
    compute_action_from_command_func=compute_mpc_action_from_command,
)
```

#### 步骤 4: 注册到通用 Builder

在 `source/EAI_hmrs/EAI_hmrs/env_builder.py` 中登记控制器配置：

```python
CONTROLLER_CFG_IMPORTS = {
    "YOUR_ROBOT_MPC_CFG": (
        "mpc/your_robot_mpc/your_robot_mpc.py",
        "YOUR_ROBOT_MPC_CFG",
    ),
}
```

随后在环境 JSON 的 `controller.cfg` 中填写 `YOUR_ROBOT_MPC_CFG`。

## 完整示例

### 示例 1: 传统控制器（差速驱动）

**基础类**: `DifferentialDriveControllerCfg` (已有)
**具体配置**: 参考 `source/EAI_assets/EAI_assets/controller/traditional/carter_diff/carter_diff.py`

### 示例 2: RL 控制器 - Go2（速度控制）

**基础类**: `RSLControllerCfg` (已有)
**具体配置**: 参考 `source/EAI_assets/EAI_assets/controller/rl/go2_rsl_rl/go2_rsl_rl.py`

### 示例 3: RL 控制器 - Quadcopter（目标位置控制）

**基础类**: `SKRLControllerCfg` (已有)
**具体配置**: 参考 `source/EAI_assets/EAI_assets/controller/rl/quadcopter_goal_skrl/quadcopter_goal_skrl.py`

### 示例 4: RL 控制器 - M20（混合控制）

**基础类**: `RSLControllerCfg` (已有)
**具体配置**: 参考 `source/EAI_assets/EAI_assets/controller/rl/m20_rough_rsl/m20_rough_rsl.py`

### 示例 5: UR5/Z1 统一机械臂控制器（ROS2 OmniGraph）

UR5 使用 `UR5_IK_CFG`，Z1 使用 `Z1_IK_CFG`。两者复用 `ManipulatorIkControllerCfg`，输入命令和状态反馈由 Isaac Sim 内部 OmniGraph 直接连接 ROS2 topic，不经过临时文件或外部 bridge。`target_pose` 使用完整 6D pose 的 DLS Differential IK，Z1 夹爪由独立 topic 控制。

机械臂作为独立 `<robot>_arm` articulation 注册，并通过 FixedJoint 连接宿主。控制器只处理明确分配给自己的机器人实例，UR5/Z1、多机器人之间不会交叉消费命令。同一机器人不能同时挂载 UR5 和 Z1。完整 topic、消息格式和测试命令参见[机械臂](ur5_control.md)。

控制器代码和模型属于按需下载资产，不随 Git 提交。更新控制器时应上传到 Hugging Face 中对应的 `controller/` 路径，并使用资产解析器下载；Git 中只保留通用挂载、环境注册和接口代码。

## 总结

### 关键要点

1. **函数式设计**: 所有控制器逻辑通过函数实现，而非类方法
2. **统一接口**: 所有控制器通过 `ControllerCfg` 基类统一管理
3. **明确职责**: 
   - `observation_func`: 计算观测
   - `apply_action_func`: 应用动作
   - `compute_action_from_command_func`: 命令转动作
4. **遵守模型接口**: 观测和动作必须满足随预训练模型发布的输入输出约定

### 最佳实践

1. **观测函数**:
   - 保持与模型接口一致的顺序和维度
   - 使用 `env.get_command()` 获取命令，提供回退值
   - 正确处理 `last_action`（从 `env.actions_dict` 获取）

2. **动作函数**:
   - 实现模型接口要求的缩放和偏移
   - 验证动作维度与机器人关节数匹配
   - 对于混合控制，分别处理不同类型的关节

3. **命令转动作函数**:
   - 传统控制器：直接转换
   - RL 速度控制：使用默认实现
   - RL 目标位置控制：自定义处理占位符

### 参考文件

#### 控制器基础类

- **基类**: `source/EAI/EAI/controllers/base.py` - `ControllerCfg`
- **SKRL 控制器基础类**: `source/EAI/EAI/controllers/skrl_controller.py` - `SKRLControllerCfg`
- **RSL 控制器基础类**: `source/EAI/EAI/controllers/rsl_controller.py` - `RSLControllerCfg`
- **差速驱动控制器基础类**: `source/EAI/EAI/controllers/differential_drive_controller.py` - `DifferentialDriveControllerCfg`

#### 具体控制器配置示例

- **Carter 差速驱动**: `source/EAI_assets/EAI_assets/controller/traditional/carter_diff/carter_diff.py`
- **Go2 RSL-RL**: `source/EAI_assets/EAI_assets/controller/rl/go2_rsl_rl/go2_rsl_rl.py`
- **G1 SKRL**: `source/EAI_assets/EAI_assets/controller/rl/g1_skrl/g1_skrl.py`
- **Quadcopter SKRL**: `source/EAI_assets/EAI_assets/controller/rl/quadcopter_goal_skrl/quadcopter_goal_skrl.py`
- **M20 RSL**: `source/EAI_assets/EAI_assets/controller/rl/m20_rough_rsl/m20_rough_rsl.py`
- **UR5 IK 与 ROS2 OmniGraph**: :doc:`ur5_control`
- **UR5/Z1 统一 IK、夹爪与 ROS2 OmniGraph**: :doc:`ur5_control`

#### 环境实现

- **多机器人环境基类**: `source/EAI/EAI/hmrs_env/multi_robot_direct_env.py` - `MultiRobotDirectEnv`
