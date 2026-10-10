# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause
"""Pepper 全向底盘控制器 — 将速度命令积分成 XDisp/YDisp/ZRot 位置目标。"""

from __future__ import annotations

from typing import Any, Dict

import torch
from isaaclab.utils import configclass

from EAI.controllers import DifferentialDriveControllerCfg

# Pepper 底盘关节名（与 pepper.py URDF/USD 一致）
_PEPPER_BASE_JOINTS = ["XDisp", "YDisp", "ZRot"]

# 缓存底盘关节 ID（避免每帧 find_joints）
_pepper_base_ids_cache: dict[int, list[int]] = {}

_PEPPER_TARGET_KEY = "pepper_position_target"


def _get_pepper_base_ids(robot: Any) -> list[int]:
    rid = id(robot)
    if rid not in _pepper_base_ids_cache:
        joint_ids, _ = robot.find_joints(_PEPPER_BASE_JOINTS, preserve_order=True)
        _pepper_base_ids_cache[rid] = list(joint_ids)
    return _pepper_base_ids_cache[rid]


def _normalize_command(command: torch.Tensor) -> torch.Tensor:
    """Return a (num_envs, 3) tensor for [x/vx, y/vy, theta/wz]."""
    num_envs = command.shape[0]
    out = torch.zeros(num_envs, 3, device=command.device, dtype=command.dtype)
    out[..., : min(command.shape[-1], 3)] = command[..., :3]
    return out


def _current_base_target(env: Any, robot_name: str, command: torch.Tensor) -> torch.Tensor:
    """Read current Pepper base joint positions as a hold target."""
    if robot_name in env.scene.articulations:
        robot = env.scene.articulations[robot_name]
        base_ids = _get_pepper_base_ids(robot)
        return robot.data.joint_pos[:, base_ids].to(device=command.device, dtype=command.dtype).clone()
    return torch.zeros(command.shape[0], 3, device=command.device, dtype=command.dtype)


# ── 计算动作：将速度命令积分为位置目标 ────────────────────────────────────────

def compute_pepper_holonomic_action_from_command(
    controller_cfg: Any,
    env: Any,
    robot_name: str,
    command: torch.Tensor,
    controller_dict: Dict[str, Any],
) -> torch.Tensor:
    """Convert [vx, vy, wz] into Pepper's [x, y, theta] position target."""
    action_command = _normalize_command(command)
    target = controller_dict.get(_PEPPER_TARGET_KEY)
    if target is None or tuple(target.shape) != tuple(action_command.shape):
        target = _current_base_target(env, robot_name, action_command)

    if torch.any(torch.abs(action_command) > 1.0e-6):
        dt = float(getattr(env, "step_dt", 0.02))
        scale = torch.tensor(
            [
                controller_cfg.linear_speed_scale,
                controller_cfg.linear_speed_scale,
                controller_cfg.angular_speed_scale,
            ],
            device=action_command.device,
            dtype=action_command.dtype,
        )
        target = target + action_command * scale * dt

    controller_dict[_PEPPER_TARGET_KEY] = target.clone()
    return target


# ── 应用动作：设置底盘关节位置目标 ───────────────────────────────────────────

def apply_pepper_holonomic_action(
    env: Any,
    robot_name: str,
    action: torch.Tensor,
    controller_dict: Dict[str, Any],
) -> None:
    """将 [x, y, theta] 写入 XDisp/YDisp/ZRot 关节位置目标。"""
    if robot_name not in env.scene.articulations:
        raise ValueError(f"Robot {robot_name} not found in scene")
    robot = env.scene.articulations[robot_name]
    base_ids = _get_pepper_base_ids(robot)
    if not base_ids:
        return
    pos_target = action[..., :3]  # (num_envs, 3)
    robot.set_joint_position_target(pos_target, joint_ids=base_ids)


# ── 配置类 ────────────────────────────────────────────────────────────────────

@configclass
class PepperHolonomicControllerCfg(DifferentialDriveControllerCfg):
    """Pepper 全向底盘控制器配置 (位置控制 XDisp/YDisp/ZRot)。"""

    action_dim: int = 3
    linear_speed_scale: float = 1.0
    angular_speed_scale: float = 1.0

    def compute_action(
        self,
        env: Any,
        robot_name: str,
        observations: torch.Tensor | None,
        controller_dict: Dict[str, Any],
    ) -> torch.Tensor:
        return torch.zeros((env.num_envs, self.action_dim), device=env.device)

    def load(
        self,
        robot_name: str,
        task_name: str,
        device: str,
        env: Any,
    ) -> Dict[str, Any]:
        return {
            "name": robot_name,
            "robot_type": self.robot_type,
            "action_dim": self.action_dim,
        }


PEPPER_HOLONOMIC_CFG = PepperHolonomicControllerCfg(
    robot_type="Pepper",
    # wheel_base / wheel_radius 在全向底盘位置目标模式下无意义，设占位值
    wheel_base=0.0,
    wheel_radius=1.0,
    left_wheel_joint_name="XDisp",
    right_wheel_joint_name="ZRot",
    apply_action_func=apply_pepper_holonomic_action,
    compute_action_from_command_func=compute_pepper_holonomic_action_from_command,
)
