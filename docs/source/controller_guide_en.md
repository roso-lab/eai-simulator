# Controller Development

This document details the controller architecture of the EAI platform and how to add custom controllers.

## Controller architecture overview

### ControllerCfg base class

All controller configuration classes inherit from `ControllerCfg`, located in `source/EAI/EAI/controllers/base.py`.

```python
@configclass
class ControllerCfg:
    """Controller configuration base class"""

    robot_type: str = "Unknown"
    """Robot type identifier"""

    observation_func: Optional[ObsFunc] = None
    """Observation calculation function: (env, robot_name) -> observation_tensor"""

    apply_action_func: Optional[ApplyActionFunc] = None
    """Action application function: (env, robot_name, action, controller_dict) -> None"""

    compute_action_from_command_func: Optional[ComputeActionFromCommandFunc] = None
    """Command to action function: (controller_cfg, env, robot_name, command, controller_dict) -> action_tensor"""

    def compute_observations(self, env, robot_name) -> torch.Tensor:
        """Compute observations (call observation_func)"""

    def compute_action(self, env, robot_name, observations, controller_dict) -> torch.Tensor:
        """Calculating actions from observations (subclass implementation)"""

    def compute_action_from_command(self, env, robot_name, command, controller_dict) -> torch.Tensor:
        """Compute action from command (call compute_action_from_command_func)"""

    def apply_action(self, env, robot_name, action, controller_dict) -> None:
        """Apply action (call apply_action_func)"""

    def load(self, robot_name, task_name, device, env) -> Dict[str, Any]:
        """Load controller resources (subclass implementation)"""
```

### Controller workflow

```
user script
    │
    ├─> env.step(actions)  # actions: {robot_name: command_tensor}
    │
    └─> MultiRobotDirectEnv._pre_physics_step(actions)
            │
            ├─> For each robot:
            │   controller_cfg.compute_action_from_command(
            │       env, robot_name, command, controller_dict
            │   )
            │   │
            │ ├─> [Traditional controller] Directly convert commands into actions
            │   │   action = compute_action_from_command_func(...)
            │   │
            │ └─> [RL controller] Set command -> Compute observation -> Run policy inference
            │       env.set_command(...)
            │       obs = observation_func(env, robot_name)
            │       action = policy.act(obs)
            │
            └─> Store calculated actions into self.actions_dict

    └─> MultiRobotDirectEnv._apply_action()
            │
            └─> For each robot:
                controller_cfg.apply_action(
                    env, robot_name, action, controller_dict
                )
                │
                └─> apply_action_func(env, robot_name, action, controller_dict)
```

### Default controller cfg in Env DIY

The lightweight window, terminal quick setup, and Isaac Sim 3D extension share the same catalog. Default host-robot configurations are listed below. When `manual` is selected, the JSON `controller.cfg` value must still be a configuration name that `env_builder.py` can resolve.

| Host robot | Default cfg | Type |
|---|---|---|
| Carter | `CARTER_DIFF_CFG` | Differential drive |
| Pepper | `PEPPER_HOLONOMIC_CFG` | Holonomic drive |
| Go2 | `GO2_VELOCITY_RSL_CFG` | RSL-RL speed strategy |
| B2 | `B2_VELOCITY_RSL_CFG` | RSL-RL speed strategy |
| M20 | `M20_ROUGH_RSL_CFG` | RSL-RL rough terrain strategy |
| Lite3 | `LITE3_VELOCITY_RSL_CFG` | RSL-RL speed policy |
| Scout | `SCOUT_DIFF_CFG` | Differential drive |
| G1 | `G1_SKRL_CFG` | SKRL PPO |
| CF2X | `QUADCOPTER_GOAL_SKRL_CFG` | SKRL target position |
| Human | `HUMAN_ANIMATION_CFG` | Kinematic Animation |

UR5 and Z1 do not belong to the host controller, but are auxiliary controllers mounted to the host: `UR5_IK_CFG` and `Z1_IK_CFG`. They all come from `ManipulatorIkControllerCfg` and are triggered by the actual attachment instance in the host selection; no articulation or ROS2 topic is created for unmounted manipulators.

## Included Controllers

### 1. DifferentialDriveControllerCfg (differential drive controller)

**File location**: `source/EAI/EAI/controllers/differential_drive_controller.py`

**Use**: Traditional controller for differential drive robots (such as Carter)

**Functions that need to be defined**:

1. **`compute_action_from_command_func`**: Convert speed command to wheel speed
   - **Purpose**: Implement differential drive kinematics and convert `[vx, wz]` into `[left_wheel_vel, right_wheel_vel]`
   - **Example**: `source/EAI_assets/EAI_assets/controller/traditional/carter_diff/carter_diff.py:13-61`

2. **`apply_action_func`**: Apply wheel speed to robot
   - **Purpose**: Set the target speed of the left and right wheels
   - **Example**: `source/EAI_assets/EAI_assets/controller/traditional/carter_diff/carter_diff.py:64-124`

**Configuration example**:
```python
CARTER_DIFF_CFG = DifferentialDriveControllerCfg(
    robot_type="Carter",
    wheel_base=0.413, # Wheel base
    wheel_radius=0.14, # wheel radius
    left_wheel_joint_name="joint_wheel_left",
    right_wheel_joint_name="joint_wheel_right",
    apply_action_func=apply_carter_action,
    compute_action_from_command_func=compute_differential_drive_action_from_command,
)
```

### 2. SKRLControllerCfg (SKRL reinforcement learning controller)

**File location**: `source/EAI/EAI/controllers/skrl_controller.py`

**Purpose**: Load a pre-trained SKRL policy in PyTorch format and perform inference.

**Functions that need to be defined**:

1. **`observation_func`**: Calculation of observations
   - **Purpose**: Calculate the observation tensor from the robot state
   - **Signature**: `(env: Any, robot_name: str) -> torch.Tensor`
   - **Returns**: Observation tensor with shape `(num_envs, obs_dim)`

2. **`apply_action_func`**: Apply action to robot
   - **Purpose**: Apply the actions output by the strategy to the robot joints/actuators
   - **Signature**: `(env: Any, robot_name: str, action: torch.Tensor, controller_dict: Dict[str, Any]) -> None`

3. **`compute_action_from_command_func`**: Compute action from command (optional)
   - **Purpose**: For speed control, calculate observations after setting commands and use strategies to calculate actions
   - **Default**: use `compute_skrl_action_from_command` (generic implementation)
   - **Special Scenario**: Target position control (such as drone) needs to be customized

When running, directly use the controller configuration provided by the warehouse, such as `G1_SKRL_CFG` and `QUADCOPTER_GOAL_SKRL_CFG`; this document only describes the loading and inference interface of the pre-training strategy.

**Implemented Robot**:
- **G1**: `source/EAI_assets/EAI_assets/controller/rl/g1_skrl/g1_skrl.py`
  - Observation: including speed, attitude, joint status, etc.
  - Action: 29-dimensional joint positions
- **Quadcopter**: `source/EAI_assets/EAI_assets/controller/rl/quadcopter_goal_skrl/quadcopter_goal_skrl.py`
  - Observation: 12 dimensions (speed, attitude, relative coordinates of target position)
  - Action: 4 dimensions (thrust, torque)

### 3. RSLControllerCfg (RSL-RL ONNX controller)

**File location**: `source/EAI/EAI/controllers/rsl_controller.py`

**Purpose**: Load a pre-trained RSL-RL policy in ONNX format and perform inference.

**Functions that need to be defined**:

1. **`observation_func`**: calculate observation (same as SKRL)
   - **Purpose**: Calculate the observation tensor from the robot state
   - **Note**: Input tensors must meet the dimensionality, ordering, scaling and clipping conventions published with the model

2. **`apply_action_func`**: Apply action to robot (same as SKRL)
   - **Purpose**: Apply the actions output by the strategy to the robot

3. **`compute_action_from_command_func`**: Compute action from command (optional)
   - **PURPOSE**: Same as SKRL
   - **Default**: use `compute_rsl_action_from_command` (generic implementation)

**Configuration example**:
```python
GO2_VELOCITY_RSL_CFG = Go2VelocityRSLControllerCfg(
    model_path=str(_RL_DIR / "model" / "policy.onnx"),
    robot_type="Go2Velocity",
    observation_func=compute_go2_velocity_observations,
    apply_action_func=apply_go2_velocity_action,
    compute_action_from_command_func=compute_rsl_action_from_command,
)
```

**Implemented Robot**:
- **Go2 Velocity**: `source/EAI_assets/EAI_assets/controller/rl/go2_rsl_rl/go2_rsl_rl.py`
  - Observation: 45 dimensions (speed, gravity direction, command, 12 joint states, previous action)
  - Action: 12-dimensional joint positions
- **M20 Rough**: `source/EAI_assets/EAI_assets/controller/rl/m20_rough_rsl/m20_rough_rsl.py`
  - Observation: 48 dimensions (including speed, attitude, 16 joint states, etc.)
  - Action: 16 dimensions (12 leg joint positions + 4 wheel speeds)

### 4. HumanAnimationControllerCfg (Human Animation Controller)

**File location**: `source/EAI_assets/EAI_assets/controller/traditional/human_animation/human_animation.py`

**Use**: Convert keyboard targets to human steering, collision detection, rigid body displacement and walking animations.

The running process adopts a "turn around first, then move forward" state machine. The controller maintains position and yaw independently, and the walking animation is played only after PhysX accepts the actual displacement; the stop command will immediately converge the target to the current position.

#### Isaac Sim 5.1 / Isaac Lab 2.x Experience Conclusion

- Modifying only the USD Xform will make the model appear to be moving, but will not reliably move PhysX rigidbodies and colliders; movement with collision must be written into the real-time PhysX rigid-body transform.
- In Isaac Sim 5.1, GPU `RigidBodyView.set_transforms()` will trigger CUDA error 700, dynamic control is not available under Direct GPU API, and GPU kinematic targets are not implemented. Environments containing human therefore automatically use CPU PhysX.
- human uses invisible kinematic capsules as collision proxy. Check the candidate position through PhysX overlap query before translation, keep idle during collision, and do not play the walking animation in place.
- Keyboard target step size, rigid body movement speed, turn speed and animation FPS must be set separately. The keyboard step size cannot directly use a small physics `dt`, and the animation speed should not determine the world coordinate displacement.
- When stopped with `K` or space, the bridge layer must simultaneously clear the speed and align the target position/yaw to the controller's current state, otherwise the character will continue to chase the previously accumulated target.

### 5. ManipulatorIkControllerCfg (UR5/Z1)

**Basic implementation**: `source/EAI_assets/EAI_assets/controller/traditional/manipulator_ik/manipulator_ik.py`

| configuration | model spec | command | results |
|---|---|---|---|
| `UR5_IK_CFG` | `UR5_MODEL_SPEC`, six-axis UR5 joint | Six-axis joint position or `target_pose` | Write `<robot>_arm` after joint target limiting, publish UR5 status |
| `Z1_IK_CFG` | `Z1_MODEL_SPEC`, six-axis Z1 joint + `jointGripper` | Six-axis joint position, `target_pose` or independent gripper position | The robot arm and gripper are respectively limited and released status |

`target_pose` uses DLS Differential IK (`lambda_val=0.02`). The target can be represented by `world` or `base_link`; `base_link` will first be converted to world through the host root pose. The joint command does not go through IK, but is directly rearranged according to the model joint name and written to the target. A maximum of `0.10 rad` joint changes are applied per control loop to avoid sudden jumps caused by external pose targets.

ROS2 OmniGraph is only created when the corresponding attachment exists in the selection. After the message enters `ManipulatorOmniGraphManager`, it is isolated according to the robot instance name and robot arm model. The command of `m20_1` will not be consumed by `m20_2`. The command, IK smoothing state and gripper state will be cleaned up when reset.

For detailed topics, message types, Z1 gripper commands, and `manipulator_command.py` examples, see [Manipulator Control](ur5_control_en.md).


## How to define a new controller

Defining a new controller is divided into two levels:

1. **Create controller base class** (in `source/EAI/EAI/controllers/`): define controller type (such as MPC, PID, etc.)
2. **Create a specific controller configuration** (in `source/EAI_assets/EAI_assets/controller/`): Define a controller instance for a specific robot

### Step 1: Create the controller base class (in `source/EAI/EAI/controllers/`)

If you need to define a new controller type (such as **MPC**, **PID**, **ILC**, etc.), you need to create a new base class in the `source/EAI/EAI/controllers/` directory.

**When do you need to create a base class? **
- Controller type completely different from existing types (SKRL, RSL, differential drive)
- Need to define controller type specific parameters and loading logic
- Multiple robots may share the same controller type

**When is it not necessary to create a base class? **
- Controllers are just variants of existing types (like new SKRL controllers) → use `SKRLControllerCfg` directly
- The controller type is the same, but the parameters are different → Create a configuration instance in `EAI_assets`

#### 1.1 Create the base class file

Create a new file under `source/EAI/EAI/controllers/`, for example `mpc_controller.py`:

If you need to define a new controller type (such as MPC, PID, ILC, etc.), you need to create a new base class in the `source/EAI/EAI/controllers/` directory.

#### 1.1 Create the base class file

Create a new file under `source/EAI/EAI/controllers/`, for example `mpc_controller.py`:

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

    # MPC specific parameters
    horizon: int = 10
    """Prediction time domain length"""

    dt: float = 0.02
    """Control period (seconds)"""

    model_path: Optional[str] = None
    """MPC model path (if using learning model)"""

    # Constraint parameters
    u_min: Optional[torch.Tensor] = None
    """Control input lower bound"""

    u_max: Optional[torch.Tensor] = None
    """Control input upper bound"""

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
            observations: Observation tensor (contains status information)
            controller_dict: Dictionary containing loaded MPC solver

        Returns:
            Action tensor of shape (num_envs, action_dim)
        """
        if observations is None:
            raise ValueError(f"MPC controller for {robot_name} requires observations")

        mpc_solver = controller_dict.get('solver')
        if mpc_solver is None:
            raise ValueError(f"MPC solver not found in controller_dict for {robot_name}")

        # Call the MPC solver
        # This needs to be called according to the specific MPC implementation
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
        # Create or load MPC solver
        # mpc_solver = create_mpc_solver(...)

        return {
            'name': robot_name,
            'solver': None, # Actual MPC solver instance
        }
```

#### 1.2 Export in `__init__.py`

Add exports in `source/EAI/EAI/controllers/__init__.py`:

```python
from .mpc_controller import MPCControllerCfg

__all__ = [
    # ... existing exports ...
    "MPCControllerCfg",
]
```

#### 1.3 Methods that the base class must implement

All base classes that inherit from `ControllerCfg` must implement the following methods:

##### `load()` method (must be implemented)

**sign**:
```python
def load(
    self,
    robot_name: str,
    task_name: str,
    device: str,
    env: Any,
) -> Dict[str, Any]:
```

**Responsibilities**: Load controller resources (models, solvers, parameters, etc.)

**Calling timing**: When the environment is initialized, call it uniformly through `load_all_controllers()`

**Return value**: `Dict[str, Any]`, must contain:
- `'name': robot_name` (required)
- Other controller-specific resources (such as `'policy'`, `'solver'`, `'model'`, etc.)

**Example**:
```python
def load(self, robot_name, task_name, device, env) -> Dict[str, Any]:
    # SKRL: Load PyTorch model
    # RSL: Load ONNX model
    # MPC: Initialize solver
    # Traditional controller: return basic metadata

    return {
        'name': robot_name,
        'solver': mpc_solver, # or 'policy', 'model' etc.
    }
```

##### `compute_action()` method (must be implemented)

**sign**:
```python
def compute_action(
    self,
    env: Any,
    robot_name: str,
    observations: Optional[torch.Tensor],
    controller_dict: Dict[str, Any]
) -> torch.Tensor:
```

**Responsibilities**: Compute actions from observations

**Calling timing**: Called in `compute_action_from_command` (for controllers that need to be observed)

**parameter**:
- `observations`: observation tensor (shape: `(num_envs, obs_dim)`)
- `controller_dict`: Contains resources loaded by the `load()` method

**Return value**: action tensor (shape: `(num_envs, action_dim)`)

**Example**:
```python
def compute_action(self, env, robot_name, observations, controller_dict):
    # SKRL/RSL: Call policy network
    policy = controller_dict['policy']
    action = policy.act({"states": observations})[0]

    # MPC: Call solver
    solver = controller_dict['solver']
    action = solver.solve(observations)

    return action
```

**Note**: For purely command-driven controllers (such as differential drives), placeholders can be returned because the actual action comes from `compute_action_from_command`

##### Optional methods

- `resolve_model_path()`: resolve model path (if you need to load the model)
- Other controller-specific auxiliary methods (such as MPC parameter setting, constraint processing, etc.)

#### 1.4 Base Class Responsibilities

The controller base class should:
- Define the **general parameters** of the controller type (such as MPC's `horizon`, `dt`)
- Implement **general loading logic** (such as MPC solver initialization)
- Implement **general action calculation logic** (such as MPC solution process)
- Do not include robot-specific implementations in base classes; these implementations should be placed in concrete configurations

#### 1.5 Compatibility with ControllerCfg

The base class must inherit from `ControllerCfg` so that:
- Compatible with `load_all_controllers()` unified loading mechanism
- Compatible with `_pre_physics_step`, `_get_observations`, `_apply_action` interfaces of environment system
- Support functional interfaces (`observation_func`, `apply_action_func`, `compute_action_from_command_func`)

**Key Points**:
- The base class **inherits** all methods and properties of `ControllerCfg`
- Base class **override** `load()` and `compute_action()` methods
- Base classes can **add** controller type specific parameters (such as MPC's `horizon`)
- Robot-specific functions (`observation_func`, etc.) are defined in the **specific configuration**, not in the base class

#### 1.6 Reference example

View existing controller base class implementations:

- **SKRL Controller**: `source/EAI/EAI/controllers/skrl_controller.py`
  - Implemented the `load()` method (loading PyTorch model)
  - Implemented the `compute_action()` method (calling the policy network)

- **RSL Controller**: `source/EAI/EAI/controllers/rsl_controller.py`
  - Implemented the `load()` method (loading ONNX model)
  - Implemented the `compute_action()` method (calling ONNX inference)
  - `model_path` parameter defined

- **Differential drive controller**: `source/EAI/EAI/controllers/differential_drive_controller.py`
  - Implemented the `load()` method (returns basic metadata, no need to load the model)
  - Implemented `compute_action()` method (returns placeholder since action comes from command)
  - Defined parameters such as `wheel_base` and `wheel_radius`

### Step 2: Create specific controller configuration (in `source/EAI_assets/EAI_assets/controller/`)

Create a controller configuration instance, using the base class defined in step 1 (or an existing base class).

#### 2.1 Create configuration file

Create a suitable directory structure under `source/EAI_assets/EAI_assets/controller/`:

- **Traditional Controller**: `traditional/your_robot_name/your_robot_name.py`
- **MPC Controller**: `mpc/your_robot_name_mpc/your_robot_name_mpc.py`

#### 2.2 Define the required functions

Depending on the controller type, the following functions are defined:

#### For traditional controllers (such as differential drives)

1. **`compute_action_from_command_func`**: command to action
```python
def compute_your_robot_action_from_command(
    controller_cfg: Any,
    env: Any,
    robot_name: str,
    command: torch.Tensor,
    controller_dict: Dict[str, Any]
) -> torch.Tensor:
    """Convert commands into actions"""
    # Implement conversion logic
    # For example: speed command -> joint speed
    return action
```

2. **`apply_action_func`**: Apply action
```python
def apply_your_robot_action(
    env: Any,
    robot_name: str,
    action: torch.Tensor,
    controller_dict: Dict[str, Any]
) -> None:
    """Apply actions to robot"""
    robot = env.scene.articulations[robot_name]
    # Implement action application logic
    # For example: set joint speed
    robot.set_joint_velocity_target(action, joint_ids=joint_ids)
```

#### 2.3 Create a controller configuration instance

Use the newly defined base class:

```python
from EAI.controllers import MPCControllerCfg # Newly defined base class

# For MPC controller
YOUR_ROBOT_MPC_CFG = MPCControllerCfg(
    robot_type="YourRobot",
    horizon=20, # MPC specific parameters
    dt=0.01,
    observation_func=compute_your_robot_observations,
    apply_action_func=apply_your_robot_action,
    compute_action_from_command_func=compute_mpc_action_from_command, # optional
)
```

#### 2.4 Use in JSON environment

First add the controller
`CONTROLLER_CFG_IMPORTS` in `source/EAI_hmrs/EAI_hmrs/env_builder.py`, and then in
Select the configuration name in `source/EAI_hmrs/EAI_hmrs/envs/<env_name>.json`:

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

## Detailed explanation of function interface

### observation_func

**Type**: `ObsFunc = Callable[[Any, str], torch.Tensor]`

**Signature**: `(env: Any, robot_name: str) -> torch.Tensor`

**Purpose**: Calculate the robot's observation tensor for RL policy reasoning.

**parameter**:
- `env`: environment instance (`MultiRobotDirectEnv`)
- `robot_name`: robot name

**Returns**: Observation tensor with shape `(num_envs, obs_dim)`

**IMPORTANT NOTE**:
- Observation order, dimensionality, scaling, and cropping must meet the input conventions of the published model
- Use `env.get_command(robot_name, "command_name")` to get the command
- Use `env.actions_dict.get(robot_name)` to get the actions at the last moment

### apply_action_func

**Type**: `ApplyActionFunc = Callable[[Any, str, torch.Tensor, Dict[str, Any]], None]`

**Signature**: `(env: Any, robot_name: str, action: torch.Tensor, controller_dict: Dict[str, Any]) -> None`

**Purpose**: Apply the actions output by the strategy to the robot.

**parameter**:
- `env`: environment instance
- `robot_name`: robot name
- `action`: action tensor, shape is `(num_envs, action_dim)`
- `controller_dict`: controller dictionary (contains loaded strategies, etc.)

**Operation**: Usually call `robot.set_joint_position_target()` or `robot.set_joint_velocity_target()` etc.

**IMPORTANT NOTE**:
- Action scale and offset must meet the output conventions of the published model
- Ensure that the movement dimensions match the number of joints in the robot
- For mixed control (such as M20: leg joint position + wheel speed), they need to be processed separately

### compute_action_from_command_func

**Type**: `ComputeActionFromCommandFunc = Callable[[Any, Any, str, torch.Tensor, Dict[str, Any]], torch.Tensor]`

**Signature**: `(controller_cfg: Any, env: Any, robot_name: str, command: torch.Tensor, controller_dict: Dict[str, Any]) -> torch.Tensor`

**Purpose**: Convert external commands (such as speed commands) into actions.

**parameter**:
- `controller_cfg`: controller configuration example
- `env`: environment instance
- `robot_name`: robot name
- `command`: command tensor (such as velocity command `[vx, vy, wz]`)
- `controller_dict`: controller dictionary

**Returns**: Action tensor with shape `(num_envs, action_dim)`

**Workflow** (RL Controller):
1. Set the command to the environment: `env.set_command(robot_name, command_name, command)`
2. Compute observations: `obs = controller_cfg.compute_observations(env, robot_name)`
3. Use strategy to calculate action: `action = controller_cfg.compute_action(env, robot_name, obs, controller_dict)`

**IMPORTANT NOTE**:
- For traditional controllers, directly convert commands into actions
- For RL controllers, usually use the default implementation `compute_skrl_action_from_command` or `compute_rsl_action_from_command`
- For target position control (such as drones), custom processing of zero vector placeholders is required

## Controller Base Classes

### ControllerCfg base class interface

All controller base classes must inherit from `ControllerCfg` (located in `source/EAI/EAI/controllers/base.py`):

```python
@configclass
class ControllerCfg:
    """Basic configuration class for all controllers"""

    robot_type: str = "Unknown"
    """Robot type identifier"""

    observation_func: Optional[ObsFunc] = None
    """Observation calculation function: (env, robot_name) -> tensor"""

    apply_action_func: Optional[ApplyActionFunc] = None
    """Action application function: (env, robot_name, action, controller_dict) -> None"""

    compute_action_from_command_func: Optional[ComputeActionFromCommandFunc] = None
    """Command to action function: (controller_cfg, env, robot_name, command, controller_dict) -> action_tensor"""

    # Method (subclasses must implement it)
    def load(...) -> Dict[str, Any]: ...
    def compute_action(...) -> torch.Tensor: ...

    # Method (the base class has been implemented and can be used directly)
    def compute_observations(...) -> torch.Tensor: ...
    def compute_action_from_command(...) -> torch.Tensor: ...
    def apply_action(...) -> None: ...
```

### Base Class vs. Concrete Configuration

| Hierarchy | Position | Responsibilities | Examples |
|------|------|------|------|
| **Base Class** | `source/EAI/EAI/controllers/` | Define common interfaces and parameters of controller types | `SKRLControllerCfg`, `MPCControllerCfg`, `PIDControllerCfg` |
| **Specific configuration** | `source/EAI_assets/EAI_assets/controller/` | Define the controller instance of a specific robot | `GO2_VELOCITY_RSL_CFG`, `CARTER_DIFF_CFG` |

**Design Principles**:
- The base class defines the **shared logic for a controller type**.
- Concrete configuration defines **robot-specific parameters and functions**

### Complete example of creating an MPC controller base class

#### Step 1: Create the Base Class File

```python
# source/EAI/EAI/controllers/mpc_controller.py

import torch
from typing import Optional, Any, Dict
from isaaclab.utils import configclass
from .base import ControllerCfg


@configclass
class MPCControllerCfg(ControllerCfg):
    """MPC controller basic configuration class"""

    horizon: int = 10
    """Prediction time domain length"""

    dt: float = 0.02
    """Control period (seconds)"""

    def compute_action(self, env, robot_name, observations, controller_dict):
        solver = controller_dict['solver']
        action = solver.solve(observations)
        return action

    def load(self, robot_name, task_name, device, env):
        # Initialize MPC solver
        solver = create_mpc_solver(horizon=self.horizon, dt=self.dt)
        return {'name': robot_name, 'solver': solver}
```

#### Step 2: Export base class

```python
# source/EAI/EAI/controllers/__init__.py

from .mpc_controller import MPCControllerCfg

__all__ = [
    # ... existing ...
    "MPCControllerCfg",
]
```

#### Step 3: Create specific configuration

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

#### Step 4: Register with the Generic Builder

Register the controller configuration in `source/EAI_hmrs/EAI_hmrs/env_builder.py`:

```python
CONTROLLER_CFG_IMPORTS = {
    "YOUR_ROBOT_MPC_CFG": (
        "mpc/your_robot_mpc/your_robot_mpc.py",
        "YOUR_ROBOT_MPC_CFG",
    ),
}
```

Then fill in `YOUR_ROBOT_MPC_CFG` in `controller.cfg` of the environment JSON.

## Complete example

### Example 1: Traditional controller (differential drive)

**Base class**: `DifferentialDriveControllerCfg` (existing)
**Specific configuration**: Refer to `source/EAI_assets/EAI_assets/controller/traditional/carter_diff/carter_diff.py`

### Example 2: RL Controller - Go2 (Speed Control)

**Base class**: `RSLControllerCfg` (existing)
**Specific configuration**: Refer to `source/EAI_assets/EAI_assets/controller/rl/go2_rsl_rl/go2_rsl_rl.py`

### Example 3: RL Controller - Quadcopter (Target Position Control)

**Base class**: `SKRLControllerCfg` (existing)
**Specific configuration**: Refer to `source/EAI_assets/EAI_assets/controller/rl/quadcopter_goal_skrl/quadcopter_goal_skrl.py`

### Example 4: RL Controller - M20 (Hybrid Control)

**Base class**: `RSLControllerCfg` (existing)
**Specific configuration**: Refer to `source/EAI_assets/EAI_assets/controller/rl/m20_rough_rsl/m20_rough_rsl.py`

### Example 5: UR5/Z1 Unified Robotic Arm Controller (ROS2 OmniGraph)

UR5 uses `UR5_IK_CFG` and Z1 uses `Z1_IK_CFG`. The two reuse `ManipulatorIkControllerCfg`, and input commands and status feedback are directly connected to the ROS2 topic by Isaac Sim's internal OmniGraph without going through temporary files or external bridges. `target_pose` uses DLS Differential IK with a full 6D pose, with the Z1 gripper controlled by an independent topic.

The robot arm is registered as an independent `<robot>_arm` articulation and connected to the host through FixedJoint. The controller only processes robot instances that are explicitly assigned to itself. UR5/Z1 and multiple robots will not cross-consume commands. The same robot cannot mount UR5 and Z1 at the same time. For the complete topic, message format and test commands, see [Robotic Arm](ur5_control_en.md).

Controller code and models are download-on-demand assets and are not committed with Git. When updating the controller, it should be uploaded to the corresponding `controller/` path in Hugging Face and downloaded using the asset parser; only universal mounts, environment registration and interface codes are retained in Git.

## Summary

### Key Points

1. **Functional Design**: All controller logic is implemented through functions instead of class methods
2. **Unified interface**: All controllers are managed uniformly through the `ControllerCfg` base class
3. **Clear responsibilities**:
   - `observation_func`: calculate observation
   - `apply_action_func`: apply action
   - `compute_action_from_command_func`: command to action
4. **Comply with model interface**: Observations and actions must meet the input and output conventions released with the pre-trained model

### Best Practices

1. **Observation function**:
   - Maintain order and dimensionality consistent with the model interface
   - Use `env.get_command()` to get the command and provide a fallback value
   - Correctly handle `last_action` (obtained from `env.actions_dict`)

2. **Action function**:
   - Implement the scaling and offset required by the model interface
   - Verify that the action dimensions match the number of robot joints
   - For hybrid control, handle different types of joints separately

3. **Command to action function**:
   - Legacy controller: direct conversion
   - RL speed control: use default implementation
   - RL target position control: custom processing placeholders

### Reference documents

#### Controller base class

- **Base class**: `source/EAI/EAI/controllers/base.py` - `ControllerCfg`
- **SKRL controller base class**: `source/EAI/EAI/controllers/skrl_controller.py` - `SKRLControllerCfg`
- **RSL controller base class**: `source/EAI/EAI/controllers/rsl_controller.py` - `RSLControllerCfg`
- **Differential-drive controller base class**: `source/EAI/EAI/controllers/differential_drive_controller.py` - `DifferentialDriveControllerCfg`

#### Specific controller configuration example

- **Carter differential drive**: `source/EAI_assets/EAI_assets/controller/traditional/carter_diff/carter_diff.py`
- **Go2 RSL-RL**: `source/EAI_assets/EAI_assets/controller/rl/go2_rsl_rl/go2_rsl_rl.py`
- **G1 SKRL**: `source/EAI_assets/EAI_assets/controller/rl/g1_skrl/g1_skrl.py`
- **Quadcopter SKRL**: `source/EAI_assets/EAI_assets/controller/rl/quadcopter_goal_skrl/quadcopter_goal_skrl.py`
- **M20 RSL**: `source/EAI_assets/EAI_assets/controller/rl/m20_rough_rsl/m20_rough_rsl.py`
- **UR5 IK and ROS2 OmniGraph**: [Manipulator Control](ur5_control_en.md)
- **UR5/Z1 Unified IK, Gripper, and ROS2 OmniGraph**: [Manipulator Control](ur5_control_en.md)

#### Environment implementation

- **Multi-robot environment base class**: `source/EAI/EAI/hmrs_env/multi_robot_direct_env.py` - `MultiRobotDirectEnv`
