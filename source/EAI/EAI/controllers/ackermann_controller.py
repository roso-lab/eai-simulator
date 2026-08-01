# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause

"""Traditional Ackermann controller configuration for command-driven bases."""

from __future__ import annotations

from typing import Any, Dict, Optional

import torch
from isaaclab.utils import configclass

from .base import ControllerCfg


@configclass
class AckermannControllerCfg(ControllerCfg):
    """Configuration shared by front-steered mobile bases.

    Commands are ``[vx, vy, wz]`` or ``[vx, wz]``. The controller produces the
    configured steering position targets followed by drive-wheel velocities.
    """

    wheel_base: float = 0.4
    track_width: float = 0.2
    wheel_radius: float = 0.1
    steering_joint_names: tuple[str, ...] = ("front_left_steer", "front_right_steer")
    drive_joint_names: tuple[str, ...] = ("back_left_wheel", "back_right_wheel")
    drive_mode: str = "rwd"
    max_linear_speed: float = 3.0
    max_steering_angle: float = 0.488
    min_forward_speed: float = 0.15

    def compute_action(
        self,
        env: Any,
        robot_name: str,
        observations: Optional[torch.Tensor],
        controller_dict: Dict[str, Any],
    ) -> torch.Tensor:
        """Command-driven controllers do not consume an observation policy."""

        action_dim = len(self.steering_joint_names) + len(self.drive_joint_names)
        return torch.zeros((env.num_envs, action_dim), device=env.device)

    def load(
        self,
        robot_name: str,
        task_name: str,
        device: str,
        env: Any,
    ) -> Dict[str, Any]:
        return {"name": robot_name}
