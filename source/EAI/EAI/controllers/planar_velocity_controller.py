# Copyright (c) 2026, EAI Simulator contributors.
# SPDX-License-Identifier: BSD-3-Clause
"""Root planar-velocity controller configuration."""

from __future__ import annotations

from typing import Any, Dict, Optional

import torch
from isaaclab.utils import configclass

from .base import ControllerCfg


@configclass
class PlanarVelocityControllerCfg(ControllerCfg):
    """Command-driven controller for Gazebo planar-move compatible robots."""

    allow_lateral_velocity: bool = True
    linear_velocity_limit: float | None = None
    angular_velocity_limit: float | None = None

    def compute_action(
        self,
        env: Any,
        robot_name: str,
        observations: Optional[torch.Tensor],
        controller_dict: Dict[str, Any],
    ) -> torch.Tensor:
        del robot_name, observations, controller_dict
        return torch.zeros((env.num_envs, 3), device=env.device)

    def load(
        self,
        robot_name: str,
        task_name: str,
        device: str,
        env: Any,
    ) -> Dict[str, Any]:
        del task_name, device, env
        return {"name": robot_name}


__all__ = ["PlanarVelocityControllerCfg"]
