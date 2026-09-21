# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause
"""B2 Velocity RSL Controller Configuration for Direct Environments.

This controller defines observations and actions for Direct environments.
Observations are computed directly from robot data without Manager dependencies.

Observation structure (order preserved, matching robot_lab training config):
- base_ang_vel_b (3D): Angular velocity in body frame (scale=0.25)
- projected_gravity_b (3D): Gravity direction in body frame (scale=1.0)
- velocity_commands (3D): Velocity command [lin_vel_x, lin_vel_y, ang_vel_z] (scale=1.0)
- joint_pos_rel (12D): Joint positions relative to default positions (scale=1.0)
- joint_vel_rel (12D): Joint velocities relative to default velocities (scale=0.05)
- last_action (12D): Last action applied (scale=1.0)

Actions are joint position targets (12D) with different scales:
- hip joints: scale=0.125
- other joints: scale=0.25
- use_default_offset: True
"""

from pathlib import Path
import re
import torch
import numpy as np
from typing import Any, Dict
from isaaclab.utils import configclass
from EAI.controllers import RSLControllerCfg


def compute_rsl_action_from_command(
    controller_cfg: Any,
    env: Any,
    robot_name: str,
    command: torch.Tensor,
    controller_dict: Dict[str, Any]
) -> torch.Tensor:
    """Compute action from external command for RSL controllers using ONNX policy.

    For RSL controllers, this function:
    1. Sets the command to environment (for observation computation)
    2. Computes observations (which include the command)
    3. Uses ONNX policy to compute action from observations

    Args:
        controller_cfg: Controller configuration instance (RSLControllerCfg)
        env: Environment instance
        robot_name: Name of the robot
        command: External command tensor (e.g., velocity command [vx, vy, wz])
        controller_dict: Dictionary containing loaded ONNX policy

    Returns:
        Action tensor of shape (num_envs, action_dim)
    """
    # Set command to environment (for observation computation)
    if hasattr(env, 'set_command'):
        env.set_command(robot_name, controller_cfg.command_name, command)

    # Compute observations (which include the command)
    obs = controller_cfg.compute_observations(env, robot_name)

    # Compute action from observations using ONNX policy
    action = controller_cfg.compute_action(env, robot_name, obs, controller_dict)

    return action


def compute_b2_velocity_observations(env: Any, robot_name: str) -> torch.Tensor:
    """Compute B2 velocity observations for Direct environments.

    Observation order (matching training configuration from rough_env_cfg.py and env.yaml):
    1. base_ang_vel_b (3D): Angular velocity in body frame (scale=0.25)
    2. projected_gravity_b (3D): Gravity direction in body frame (scale=1.0)
    3. velocity_commands (3D): Velocity command [lin_vel_x, lin_vel_y, ang_vel_z] (scale=1.0)
    4. joint_pos_rel (12D): Joint positions relative to default positions (scale=1.0)
       Order: FR_hip, FR_thigh, FR_calf, FL_hip, FL_thigh, FL_calf,
              RR_hip, RR_thigh, RR_calf, RL_hip, RL_thigh, RL_calf
    5. joint_vel_rel (12D): Joint velocities relative to default velocities (scale=0.05)
    6. last_action (12D): Last action applied (scale=1.0)

    Note: base_lin_vel and height_scan are NOT included (matching training config).

    Args:
        env: Environment instance
        robot_name: Name of the robot

    Returns:
        Observation tensor of shape (num_envs, obs_dim) where obs_dim = 3+3+3+12+12+12 = 45
    """
    robot = env.scene.articulations[robot_name]

    # Get robot state in body frame
    # Note: base_lin_vel is NOT included (matching training config)
    base_ang_vel_b = robot.data.root_ang_vel_b  # (num_envs, 3)
    projected_gravity_b = robot.data.projected_gravity_b  # (num_envs, 3)

    # Get joint positions and velocities relative to default
    # B2 has 12 joints in specific order: FR, FL, RR, RL (each with hip, thigh, calf)
    joint_names = [
        "FR_hip_joint", "FR_thigh_joint", "FR_calf_joint",
        "FL_hip_joint", "FL_thigh_joint", "FL_calf_joint",
        "RR_hip_joint", "RR_thigh_joint", "RR_calf_joint",
        "RL_hip_joint", "RL_thigh_joint", "RL_calf_joint",
    ]

    # Find joint IDs in the correct order
    joint_ids, _ = robot.find_joints(joint_names, preserve_order=True)



    joint_pos = robot.data.joint_pos[:, joint_ids]  # (num_envs, 12)
    joint_vel = robot.data.joint_vel[:, joint_ids]  # (num_envs, 12)
    default_joint_pos = robot.data.default_joint_pos[:, joint_ids]  # (num_envs, 12)
    default_joint_vel = robot.data.default_joint_vel[:, joint_ids]  # (num_envs, 12)

    joint_pos_rel = joint_pos - default_joint_pos  # (num_envs, 12)
    joint_vel_rel = joint_vel - default_joint_vel  # (num_envs, 12)

    # Get velocity command from environment
    # Try to get from env.get_command() first (supports multi-robot)
    velocity_command = None
    if hasattr(env, 'get_command') and callable(env.get_command):
        velocity_command = env.get_command(robot_name, "base_velocity")

    if velocity_command is None:
        # Try robot-specific attribute names
        cmd_attr_names = [
            f'_base_velocity_{robot_name}',
            f'_velocity_command_{robot_name}',
            '_base_velocity',
            '_velocity_command',
            'base_velocity',
            'velocity_command'
        ]
        for attr_name in cmd_attr_names:
            if hasattr(env, attr_name):
                velocity_command = getattr(env, attr_name)
                if velocity_command is not None:
                    break

    if velocity_command is None:
        # Fallback: return zeros for velocity command
        num_envs = base_ang_vel_b.shape[0]
        device = base_ang_vel_b.device
        velocity_command = torch.zeros(num_envs, 3, device=device, dtype=torch.float32)
    else:
        # Ensure it's 3D [lin_vel_x, lin_vel_y, ang_vel_z]
        if velocity_command.shape[-1] == 2:
            # If 2D, assume [lin_vel_x, ang_vel_z], add lin_vel_y = 0
            velocity_command = torch.cat([
                velocity_command[:, 0:1],  # lin_vel_x
                torch.zeros_like(velocity_command[:, 0:1]),  # lin_vel_y = 0
                velocity_command[:, 1:2],  # ang_vel_z
            ], dim=-1)
        elif velocity_command.shape[-1] > 3:
            # If > 3D, take first 3 dimensions
            velocity_command = velocity_command[:, :3]

    # Get last action from environment
    last_action = None
    if hasattr(env, 'actions_dict') and robot_name in env.actions_dict:
        last_action = env.actions_dict[robot_name]
        # Ensure last_action matches joint order (if it's full joint tensor, extract correct joints)
        if last_action.shape[-1] == robot.num_joints:
            last_action = last_action[:, joint_ids]

    if last_action is None:
        # Fallback: return zeros for last action
        num_envs = base_ang_vel_b.shape[0]
        device = base_ang_vel_b.device
        last_action = torch.zeros(num_envs, 12, device=device, dtype=torch.float32)

    # Apply scaling (matching training configuration from env.yaml)
    # base_ang_vel scale: 0.25
    base_ang_vel_b = base_ang_vel_b * 0.25
    # projected_gravity scale: 1.0 (no change)
    # velocity_commands scale: 1.0 (no change)
    # joint_pos_rel scale: 1.0 (no change)
    # joint_vel_rel scale: 0.05
    joint_vel_rel = joint_vel_rel * 0.05
    # last_action scale: 1.0 (no change)

    # Apply clipping (matching training configuration)
    base_ang_vel_b = torch.clamp(base_ang_vel_b, -100.0, 100.0)
    projected_gravity_b = torch.clamp(projected_gravity_b, -100.0, 100.0)
    velocity_command = torch.clamp(velocity_command, -100.0, 100.0)
    joint_pos_rel = torch.clamp(joint_pos_rel, -100.0, 100.0)
    joint_vel_rel = torch.clamp(joint_vel_rel, -100.0, 100.0)
    last_action = torch.clamp(last_action, -100.0, 100.0)

    # Concatenate all observations in the original order
    # Total dimension: 3 (base_ang_vel_b) + 3 (projected_gravity_b) + 3 (velocity_command)
    #                + 12 (joint_pos_rel) + 12 (joint_vel_rel) + 12 (last_action) = 45
    obs = torch.cat([
        base_ang_vel_b,       # 3D (scaled by 0.25)
        projected_gravity_b,  # 3D
        velocity_command,     # 3D
        joint_pos_rel,        # 12D
        joint_vel_rel,        # 12D (scaled by 0.05)
        last_action,          # 12D
    ], dim=-1)

    # Verify observation dimension
    assert obs.shape[-1] == 45, f"Observation dimension mismatch: expected 45, got {obs.shape[-1]}"

    return obs


def apply_b2_velocity_action(
    env: Any,
    robot_name: str,
    action: torch.Tensor,
    controller_dict: Dict[str, Any]
) -> None:
    """Apply action to B2 as joint position targets.

    This matches the training configuration:
    - hip joints: scale=0.125
    - other joints (thigh, calf): scale=0.25
    - use_default_offset: True
    - Processed action: action * scale + default_joint_pos

    Args:
        env: Environment instance
        robot_name: Name of the robot
        action: Action tensor of shape (num_envs, action_dim) from policy
               These are raw actions (relative offsets from default position)
        controller_dict: Dictionary containing loaded controller data
    """
    # 测试用：将策略输出张量全部覆写为零（用于零动作测试，正常使用时请注释掉）
    # action = torch.zeros_like(action)

    if robot_name not in env.scene.articulations:
        raise ValueError(f"Robot {robot_name} not found in scene")

    robot = env.scene.articulations[robot_name]

    # Get robot's joint names in the correct order
    joint_names = [
        "FR_hip_joint", "FR_thigh_joint", "FR_calf_joint",
        "FL_hip_joint", "FL_thigh_joint", "FL_calf_joint",
        "RR_hip_joint", "RR_thigh_joint", "RR_calf_joint",
        "RL_hip_joint", "RL_thigh_joint", "RL_calf_joint",
    ]

    # Find joint IDs
    joint_ids, _ = robot.find_joints(joint_names, preserve_order=True)

    # Get action dimension
    action_dim = action.shape[-1] if len(action.shape) > 1 else action.shape[0]

    if action_dim != 12:
        raise ValueError(
            f"Action dimension {action_dim} doesn't match expected 12 joints for B2."
        )

    # Get default joint positions (offset)
    default_joint_pos = robot.data.default_joint_pos[:, joint_ids]  # (num_envs, 12)

    # Training config: different scales for hip vs other joints
    # From rough_env_cfg.py: scale = {".*_hip_joint": 0.125, "^(?!.*_hip_joint).*": 0.25}
    # Create scale tensor matching joint order
    action_scale = torch.ones_like(action)
    # hip joints: scale = 0.125 (indices 0, 3, 6, 9)
    hip_indices = [0, 3, 6, 9]
    # other joints: scale = 0.25 (indices 1, 2, 4, 5, 7, 8, 10, 11)
    other_indices = [1, 2, 4, 5, 7, 8, 10, 11]

    action_scale[:, hip_indices] = 0.125
    action_scale[:, other_indices] = 0.25

    # Process action: scale and add offset (matching training)
    # From env.yaml: use_default_offset: true
    # Processed action: action * scale + default_joint_pos
    processed_action = action * action_scale + default_joint_pos

    # ===== 关键修改：手动调用 DCMotor 计算并应用扭矩 =====
    # 在 DirectMARLEnv 中，set_joint_position_target() 不会自动触发 DCMotor
    # 需要手动调用 DCMotor 的 compute() 方法或直接计算 PD 扭矩

    # 首先设置 joint_position_target
    robot.set_joint_position_target(processed_action, joint_ids=joint_ids)

    # 方法 2: 如果 compute() 方法不存在或失败，手动计算 PD 扭矩
    # 由于 DCMotor 的 joint_ids 可能为空，我们需要通过 joint_names_expr 来匹配关节
    # 获取当前关节状态
    joint_pos = robot.data.joint_pos[:, joint_ids]  # (num_envs, 12)
    joint_vel = robot.data.joint_vel[:, joint_ids]  # (num_envs, 12)

    # 计算位置误差
    pos_error = processed_action - joint_pos  # (num_envs, 12)

    # 初始化总扭矩
    total_torque = torch.zeros_like(joint_pos)  # (num_envs, 12)

    # 获取所有关节名称（用于匹配）
    all_robot_joint_names = robot.joint_names if hasattr(robot, 'joint_names') else None

    # 为每个 actuator 计算扭矩
    for actuator_name, actuator in robot.actuators.items():
        # 检查是否有必要的属性
        if not (hasattr(actuator, 'stiffness') and hasattr(actuator, 'damping')):
            continue

        # 方法 A: 尝试从 joint_indices 获取（根据文档，这比 joint_ids 更可靠）
        act_joint_ids_list = []
        if hasattr(actuator, 'joint_indices'):
            act_joint_indices = actuator.joint_indices
            if isinstance(act_joint_indices, torch.Tensor):
                act_joint_ids_list = act_joint_indices.cpu().tolist()
            elif isinstance(act_joint_indices, (list, tuple)):
                act_joint_ids_list = list(act_joint_indices)
            elif act_joint_indices is not None:
                act_joint_ids_list = [act_joint_indices]

        # 方法 B: 尝试从 joint_ids 获取（如果 joint_indices 不存在）
        if not act_joint_ids_list and hasattr(actuator, 'joint_ids'):
            act_joint_ids = actuator.joint_ids
            if isinstance(act_joint_ids, torch.Tensor):
                act_joint_ids_list = act_joint_ids.cpu().tolist()
            elif isinstance(act_joint_ids, (list, tuple)):
                act_joint_ids_list = list(act_joint_ids)
            elif act_joint_ids is not None:
                act_joint_ids_list = [act_joint_ids]

        # 方法 C: 如果 joint_indices 和 joint_ids 都为空，通过 joint_names_expr 匹配关节
        if not act_joint_ids_list and hasattr(actuator, 'joint_names_expr') and all_robot_joint_names:
            joint_names_expr = actuator.joint_names_expr
            if isinstance(joint_names_expr, str):
                joint_names_expr = [joint_names_expr]

            # 匹配所有符合表达式的关节名称
            for expr in joint_names_expr:
                pattern = re.compile(expr)
                for i, jn in enumerate(all_robot_joint_names):
                    if pattern.match(jn):
                        act_joint_ids_list.append(i)

        # 找到这些关节在我们 12 个关节中的索引
        act_indices_in_our_joints = []
        for act_jid in act_joint_ids_list:
            # 在 joint_ids 中查找对应的索引
            if isinstance(joint_ids, torch.Tensor):
                matches = (joint_ids == act_jid).nonzero(as_tuple=True)[0]
            else:
                matches = [i for i, jid in enumerate(joint_ids) if jid == act_jid]

            if len(matches) > 0:
                act_indices_in_our_joints.append(matches[0].item() if isinstance(matches[0], torch.Tensor) else matches[0])

        if act_indices_in_our_joints:
            # 获取 stiffness 和 damping
            stiffness = actuator.stiffness
            damping = actuator.damping

            # 处理 stiffness（可能是 tensor 或标量）
            if isinstance(stiffness, torch.Tensor):
                if stiffness.ndim > 1:
                    stiffness_val = stiffness[0, 0].item() if stiffness.numel() > 0 else 160.0
                else:
                    stiffness_val = stiffness[0].item() if stiffness.numel() > 0 else 160.0
            else:
                stiffness_val = float(stiffness) if stiffness is not None else 160.0

            # 处理 damping（可能是 tensor 或标量）
            if isinstance(damping, torch.Tensor):
                if damping.ndim > 1:
                    damping_val = damping[0, 0].item() if damping.numel() > 0 else 5.0
                else:
                    damping_val = damping[0].item() if damping.numel() > 0 else 5.0
            else:
                damping_val = float(damping) if damping is not None else 5.0

            # 计算 PD 扭矩: tau = Kp * (q_target - q) - Kd * qd
            for idx in act_indices_in_our_joints:
                total_torque[:, idx] = (
                    stiffness_val * pos_error[:, idx] -
                    damping_val * joint_vel[:, idx]
                )

    # 应用计算出的扭矩
    if total_torque.abs().max() > 0:  # 只有在有非零扭矩时才应用
        robot.set_joint_effort_target(total_torque, joint_ids=joint_ids)


_B2_MIXED_JOINT_ORDER = (
    "FR_hip_joint", "FR_thigh_joint", "FR_calf_joint",
    "FL_hip_joint", "FL_thigh_joint", "FL_calf_joint",
    "RR_hip_joint", "RR_thigh_joint", "RR_calf_joint",
    "RL_hip_joint", "RL_thigh_joint", "RL_calf_joint",
)
_B2_MIXED_DEFAULT_POS = torch.tensor([0.0, 0.8, -1.5] * 4, dtype=torch.float32)
_B2_MIXED_ACTION_SCALE = torch.tensor([0.125, 0.25, 0.25] * 4, dtype=torch.float32)
_B2_MIXED_HEIGHT_RAYS = 187


def _b2_mixed_velocity_command(env: Any, robot_name: str, like: torch.Tensor) -> torch.Tensor:
    command = env.get_command(robot_name, "base_velocity") if hasattr(env, "get_command") else None
    if command is None:
        return torch.zeros(like.shape[0], 3, dtype=like.dtype, device=like.device)
    if command.dim() == 1:
        command = command.unsqueeze(0)
    if command.shape[-1] == 2:
        command = torch.cat((command[:, :1], torch.zeros_like(command[:, :1]), command[:, 1:2]), dim=-1)
    elif command.shape[-1] > 3:
        command = command[:, :3]
    if command.shape[0] != like.shape[0]:
        command = command.expand(like.shape[0], -1)
    # Match UniformThresholdVelocityCommand used by the mixed-stairs training.
    keep = (torch.linalg.vector_norm(command[:, :2], dim=-1) > 0.2).unsqueeze(-1)
    command = command.clone()
    command[:, :2] = torch.where(keep, command[:, :2], torch.zeros_like(command[:, :2]))
    return command


def _b2_mixed_height_scan(env: Any, robot_name: str) -> torch.Tensor:
    sensor = env.scene.sensors[f"{robot_name}_height_scanner"]
    hits = sensor.data.ray_hits_w
    if hits.shape[-2] != _B2_MIXED_HEIGHT_RAYS:
        raise ValueError(f"height scan has {hits.shape[-2]} rays, expected {_B2_MIXED_HEIGHT_RAYS}")
    if not torch.isfinite(hits).all():
        raise ValueError(f"height scan contains non-finite ray hits for {robot_name}")
    sensor_z = sensor.data.pos_w[:, 2].unsqueeze(-1)
    return torch.clamp(sensor_z - hits[..., 2] - 0.5, -1.0, 1.0)


def compute_b2_mixed_stairs_observations(env: Any, robot_name: str) -> torch.Tensor:
    """Build the 232-D B2 mixed-stairs policy observation in training order."""
    robot = env.scene.articulations[robot_name]
    names = list(robot.joint_names)
    if set(names) != set(_B2_MIXED_JOINT_ORDER) or len(names) != len(_B2_MIXED_JOINT_ORDER):
        raise ValueError(f"b2 joint set mismatch for {robot_name}: got {names}")
    order = [names.index(name) for name in _B2_MIXED_JOINT_ORDER]
    joint_pos = robot.data.joint_pos[:, order]
    joint_vel = robot.data.joint_vel[:, order]
    default_pos = _B2_MIXED_DEFAULT_POS.to(joint_pos.device).expand(joint_pos.shape[0], -1)
    last_action = env.actions_dict.get(robot_name) if hasattr(env, "actions_dict") else None
    if last_action is None:
        last_action = torch.zeros_like(joint_pos)
    if last_action.dim() == 1:
        last_action = last_action.unsqueeze(0)
    obs = torch.cat((
        robot.data.root_ang_vel_b * 0.25,
        robot.data.projected_gravity_b,
        _b2_mixed_velocity_command(env, robot_name, joint_pos),
        joint_pos - default_pos,
        joint_vel * 0.05,
        last_action,
        _b2_mixed_height_scan(env, robot_name),
    ), dim=-1)
    if obs.shape[-1] != 232 or not torch.isfinite(obs).all():
        raise ValueError(f"invalid B2 mixed-stairs observation: shape={tuple(obs.shape)}")
    return obs


def apply_b2_mixed_stairs_action(
    env: Any,
    robot_name: str,
    action: torch.Tensor,
    controller_dict: Dict[str, Any],
) -> None:
    """Apply the mixed-stairs policy with the same B2 joint target contract."""
    apply_b2_velocity_action(env, robot_name, action, controller_dict)


# Get the directory where this file is located
_RL_DIR = Path(__file__).parent


@configclass
class B2VelocityRSLControllerCfg(RSLControllerCfg):
    """B2 Velocity-specific RSL Controller Configuration."""
    pass


# B2 Velocity RSL Controller Configuration
# Note: observation_func is now a simple function instead of Manager-based config
B2_VELOCITY_RSL_CFG = B2VelocityRSLControllerCfg(
    model_path=str(_RL_DIR / "model" / "policy.onnx"),  # ONNX 模型路径
    robot_type="B2",
    observation_func=compute_b2_velocity_observations,  # Direct function, not Manager config
    apply_action_func=apply_b2_velocity_action,  # Direct function, not method override
    compute_action_from_command_func=compute_rsl_action_from_command,  # Direct function
)


@configclass
class B2MixedStairsRSLControllerCfg(B2VelocityRSLControllerCfg):
    """B2 mixed-stairs RSL controller with the 232-D height-scan input."""


B2_MIXED_STAIRS_CFG = B2MixedStairsRSLControllerCfg(
    model_path=str(_RL_DIR / "model" / "policy.mixed_stairs.onnx"),
    robot_type="B2",
    observation_func=compute_b2_mixed_stairs_observations,
    apply_action_func=apply_b2_mixed_stairs_action,
    compute_action_from_command_func=compute_rsl_action_from_command,
)
