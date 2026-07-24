# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause

"""Differential Drive Controller Configuration with Loader Support."""

import torch
from typing import Any, Dict, Optional
from isaaclab.utils import configclass
from .base import ControllerCfg

@configclass
class DifferentialDriveControllerCfg(ControllerCfg):
    """Base configuration class for Differential Drive Controllers.
    
    This class defines parameters for differential drive controllers (e.g., wheeled robots)
    that don't use RL models. It inherits common fields from ControllerCfg.
    
    Wheel parameters are defined directly in the controller config.
    """
    
    # Wheel parameters
    wheel_base: float = 0.4
    """Distance between left and right wheels in meters."""
    
    wheel_radius: float = 0.1
    """Radius of the wheels in meters."""
    
    left_wheel_joint_name: str = "joint_wheel_left"
    """Name of the left wheel joint."""
    
    right_wheel_joint_name: str = "joint_wheel_right"
    """Name of the right wheel joint."""
    
    def compute_action(
        self, 
        env: Any, 
        robot_name: str, 
        observations: Optional[torch.Tensor],
        controller_dict: Dict[str, Any]
    ) -> torch.Tensor:
        """Compute action for differential drive controller.
        
        For command-driven controllers, action comes directly from external input (e.g., keyboard).
        This method is not used for command-driven controllers, but included for interface compatibility.
        """
        # For command-driven controllers, action is provided directly from environment
        # Return zero action as placeholder (actual action comes from environment input)
        return torch.zeros((env.num_envs, 2), device=env.device)
    
    def load(
        self,
        robot_name: str,
        task_name: str,
        device: str,
        env: Any,
    ) -> Dict[str, Any]:
        """Load differential drive controller metadata.
        
        Differential drive controllers (e.g., wheeled robots) don't require model loading,
        they just need metadata for command routing.
        
        Args:
            robot_name: Name of the robot
            task_name: Task name (unused)
            device: Device string (unused)
            env: Environment instance (unused)
        
        Returns:
            Dictionary with controller metadata
        """
        return {
            'name': robot_name,
        }

