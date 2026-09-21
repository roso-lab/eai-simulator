# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause
"""Go2 mixed-stairs RSL controller (robot_lab 232-D height-scan policy).

Deployed observation layout, matching robot_lab UnitreeGo2RoughEnvCfg + Go2MixedCfg
training (order preserved, scales identical):
- base_ang_vel_b (3D, scale=0.25)
- projected_gravity_b (3D, scale=1.0)
- velocity_commands (3D, scale=1.0, |xy| < 0.2 zeroed like UniformThresholdVelocityCommand)
- joint_pos_rel (12D, scale=1.0, relative to TRAINING defaults in robot_lab joint order)
- joint_vel_rel (12D, scale=0.05)
- last_action (12D, scale=1.0)
- height_scan (187D = GridPatternCfg(0.1, [1.6, 1.0]), formula
  `sensor.pos_w.z - ray_hits_z - 0.5` clipped +/-1)

Actions are raw joint-position offsets processed per training config:
target = training_default + scale * action with hip scale 0.125, thigh/calf 0.25.
"""

from pathlib import Path
from typing import Any, Dict

import torch
from isaaclab.utils import configclass

from EAI.controllers import RSLControllerCfg

# robot_lab joint order (FR, FL, RR, RL x hip/thigh/calf) and training defaults.
_TRAINING_JOINT_ORDER = (
    "FR_hip_joint", "FR_thigh_joint", "FR_calf_joint",
    "FL_hip_joint", "FL_thigh_joint", "FL_calf_joint",
    "RR_hip_joint", "RR_thigh_joint", "RR_calf_joint",
    "RL_hip_joint", "RL_thigh_joint", "RL_calf_joint",
)
_TRAINING_DEFAULT_POS = torch.tensor(
    [0.0, 0.8, -1.5] * 4, dtype=torch.float32
)
_TRAINING_ACTION_SCALE = torch.tensor(
    [0.125, 0.25, 0.25] * 4, dtype=torch.float32
)
_NUM_HEIGHT_RAYS = 187  # GridPatternCfg(resolution=0.1, size=[1.6, 1.0]) -> 17 x 11


def _velocity_command(env: Any, robot_name: str, like: torch.Tensor) -> torch.Tensor:
    command = None
    if hasattr(env, "get_command") and callable(env.get_command):
        command = env.get_command(robot_name, "base_velocity")
    if command is None:
        command = torch.zeros(1, 3, dtype=like.dtype, device=like.device)
    else:
        if command.dim() == 1:
            command = command.unsqueeze(0)
        if command.shape[-1] == 2:
            zeros = torch.zeros_like(command[:, 0:1])
            command = torch.cat([command[:, 0:1], zeros, command[:, 1:2]], dim=-1)
        elif command.shape[-1] > 3:
            command = command[:, :3]
        if command.shape[0] != like.shape[0]:
            command = command.expand(like.shape[0], -1)
    # UniformThresholdVelocityCommand zeroes xy commands below 0.2 m/s.
    keep = (torch.norm(command[:, :2], dim=-1) > 0.2).unsqueeze(-1).float()
    command = command.clone()
    command[:, :2] *= keep
    return command


def _height_scan(env: Any, robot_name: str, robot: Any) -> torch.Tensor:
    """Replicate isaaclab.envs.mdp.observations.height_scan exactly."""
    sensor_name = f"{robot_name}_height_scanner"
    sensors = getattr(env.scene, "sensors", {})
    if sensor_name not in sensors:
        raise KeyError(
            f"Height scanner '{sensor_name}' missing from scene; the mixed-stairs "
            "controller requires the raycaster wired by env_builder."
        )
    raycaster = sensors[sensor_name]
    hits = raycaster.data.ray_hits_w  # (num_envs, num_rays, 3)
    if hits.shape[-2] != _NUM_HEIGHT_RAYS:
        raise ValueError(f"height scan has {hits.shape[-2]} rays, expected {_NUM_HEIGHT_RAYS}")
    if not torch.isfinite(hits).all():
        raise ValueError(f"height scan contains non-finite ray hits for {robot_name}")
    sensor_z = raycaster.data.pos_w[:, 2].unsqueeze(-1)
    scan = sensor_z - hits[..., 2] - 0.5
    if not torch.isfinite(scan).all():
        raise ValueError(f"height scan contains non-finite values for {robot_name}")
    return torch.clamp(scan, -1.0, 1.0)


def compute_go2_mixed_stairs_observations(env: Any, robot_name: str) -> torch.Tensor:
    """232-D observations for the robot_lab mixed-stairs height-scan policy."""
    robot = env.scene.articulations[robot_name]

    base_ang_vel_b = robot.data.root_ang_vel_b * 0.25
    projected_gravity_b = robot.data.projected_gravity_b
    command = _velocity_command(env, robot_name, base_ang_vel_b)

    # Map articulation joint order -> robot_lab training order.
    joint_names = list(robot.joint_names)
    if len(joint_names) != len(_TRAINING_JOINT_ORDER) or set(joint_names) != set(
        _TRAINING_JOINT_ORDER
    ):
        raise ValueError(
            f"go2 joint set mismatch for {robot_name}: expected "
            f"{list(_TRAINING_JOINT_ORDER)}, got {joint_names}"
        )
    try:
        order = [joint_names.index(name) for name in _TRAINING_JOINT_ORDER]
    except ValueError as exc:
        raise ValueError(f"go2 joint set mismatch for {robot_name}: {exc}") from exc
    joint_pos = robot.data.joint_pos[:, order]
    joint_vel = robot.data.joint_vel[:, order]
    default_pos = _TRAINING_DEFAULT_POS.to(joint_pos.device).expand(joint_pos.shape[0], -1)

    joint_pos_rel = joint_pos - default_pos
    joint_vel_rel = joint_vel * 0.05

    last_action = None
    if hasattr(env, "actions_dict") and robot_name in env.actions_dict:
        last_action = env.actions_dict[robot_name]
        if last_action.dim() == 1:
            last_action = last_action.unsqueeze(0)
        if last_action.shape[-1] != len(order):
            raise ValueError(
                f"last_action dim {last_action.shape[-1]} != 12 for {robot_name}"
            )
    if last_action is None:
        last_action = torch.zeros_like(joint_pos_rel)

    obs = torch.cat([
        torch.clamp(base_ang_vel_b, -100.0, 100.0),
        torch.clamp(projected_gravity_b, -100.0, 100.0),
        torch.clamp(command, -100.0, 100.0),
        torch.clamp(joint_pos_rel, -100.0, 100.0),
        torch.clamp(joint_vel_rel, -100.0, 100.0),
        torch.clamp(last_action, -100.0, 100.0),
        _height_scan(env, robot_name, robot),
    ], dim=-1)
    if obs.shape[-1] != 232 or not torch.isfinite(obs).all():
        raise ValueError(
            f"invalid go2 observation for {robot_name}: shape={tuple(obs.shape)}, "
            f"finite={bool(torch.isfinite(obs).all())}"
        )
    return obs


def apply_go2_mixed_stairs_action(
    env: Any,
    robot_name: str,
    action: torch.Tensor,
    controller_dict: Dict[str, Any],
) -> None:
    """Apply raw policy action as joint targets: default + per-joint scale * action."""
    robot = env.scene.articulations[robot_name]
    if action.dim() == 1:
        action = action.unsqueeze(0)
    if action.shape[-1] != len(_TRAINING_JOINT_ORDER):
        raise ValueError(f"action dim {action.shape[-1]} != 12 for {robot_name}")

    scale = _TRAINING_ACTION_SCALE.to(action.device).expand(action.shape[0], -1)
    default = _TRAINING_DEFAULT_POS.to(action.device).expand(action.shape[0], -1)
    targets_training_order = default + scale * action.float()

    joint_names = list(robot.joint_names)
    if len(joint_names) != len(_TRAINING_JOINT_ORDER) or set(joint_names) != set(
        _TRAINING_JOINT_ORDER
    ):
        raise ValueError(
            f"go2 joint set mismatch for {robot_name}: expected "
            f"{list(_TRAINING_JOINT_ORDER)}, got {joint_names}"
        )
    order = [joint_names.index(name) for name in _TRAINING_JOINT_ORDER]
    # Invert the permutation: position i of the articulation vector gets the
    # training-order target for that joint.
    inv = [0] * len(order)
    for training_idx, articulation_idx in enumerate(order):
        inv[articulation_idx] = training_idx
    targets = targets_training_order[:, inv]
    robot.set_joint_position_target(targets, joint_ids=slice(None))


_RL_DIR = Path(__file__).parent


@configclass
class Go2MixedStairsControllerCfg(RSLControllerCfg):
    """Go2 mixed-stairs RSL controller configuration."""

    pass


def compute_rsl_action_from_command(
    controller_cfg: Any,
    env: Any,
    robot_name: str,
    command: torch.Tensor,
    controller_dict: Dict[str, Any],
) -> torch.Tensor:
    """Command -> observations -> ONNX policy action."""
    if hasattr(env, "set_command"):
        env.set_command(robot_name, controller_cfg.command_name, command)
    obs = controller_cfg.observation_func(env, robot_name)
    action = controller_cfg.compute_action(env, robot_name, obs, controller_dict)
    if action.shape[-1] != len(_TRAINING_JOINT_ORDER) or not torch.isfinite(
        action
    ).all():
        raise ValueError(
            f"invalid go2 policy action for {robot_name}: shape={tuple(action.shape)}, "
            f"finite={bool(torch.isfinite(action).all())}"
        )
    return action


GO2_MIXED_STAIRS_CFG = Go2MixedStairsControllerCfg(
    # Stage-B continuation of the mixed-stairs policy (checkpoint 3999),
    # exported at the bundle's canonical policy path. It supersedes the
    # original Stage-A export (checkpoint 2999).
    model_path=str(_RL_DIR / "model" / "policy.onnx"),
    robot_type="Go2",
    observation_func=compute_go2_mixed_stairs_observations,
    apply_action_func=apply_go2_mixed_stairs_action,
    compute_action_from_command_func=compute_rsl_action_from_command,
)
