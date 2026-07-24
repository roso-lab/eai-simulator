# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause

"""Base Controller Configuration with Loader Support."""

import torch
from typing import Optional, Any, Dict, Callable, List, Type
from isaaclab.utils import configclass


# Type alias for observation function: (env, robot_name) -> observation_tensor
ObsFunc = Callable[[Any, str], torch.Tensor]

# Type alias for apply action function: (env, robot_name, action, controller_dict) -> None
ApplyActionFunc = Callable[[Any, str, torch.Tensor, Dict[str, Any]], None]

# Type alias for compute action from command function: (controller_cfg, env, robot_name, command, controller_dict) -> action_tensor
ComputeActionFromCommandFunc = Callable[[Any, Any, str, torch.Tensor, Dict[str, Any]], torch.Tensor]


@configclass
class ControllerCfg:
    """Base configuration class for all controller types.
    
    All controller configuration classes should inherit from this base class.
    This provides a common interface and ensures type compatibility.
    
    Subclasses should add controller-specific parameters while maintaining
    compatibility with the unified controller loading system.
    """
    
    # Robot type identifier (common to all controllers)
    robot_type: str = "Unknown"
    """Robot type identifier."""
    
    # Optional observation computation function (for RL controllers)
    # Must be a callable function: (env, robot_name) -> observation_tensor (DirectMARL style)
    observation_func: Optional[ObsFunc] = None
    """Observation computation function. Must be a callable function (env, robot_name) -> tensor."""
    
    # Optional apply action function
    # Must be a callable function: (env, robot_name, action, controller_dict) -> None
    apply_action_func: Optional[ApplyActionFunc] = None
    """Apply action function. Must be a callable function (env, robot_name, action, controller_dict) -> None."""
    
    # Optional compute action from command function
    # Must be a callable function: (controller_cfg, env, robot_name, command, controller_dict) -> action_tensor
    compute_action_from_command_func: Optional[ComputeActionFromCommandFunc] = None
    """Compute action from command function. Must be a callable function (controller_cfg, env, robot_name, command, controller_dict) -> action_tensor."""
    
    def compute_observations(self, env: Any, robot_name: str) -> torch.Tensor:
        """Compute observations for this controller.
        
        observation_func must be a callable function: (env, robot_name) -> tensor
        
        Args:
            env: Environment instance
            robot_name: Name of the robot
            
        Returns:
            Observation tensor of shape (num_envs, obs_dim)
            
        Raises:
            ValueError: If observation_func is None or not callable
        """
        obs_func = self.observation_func
        if obs_func is None:
            # For controllers that don't need observations (e.g., command-driven controllers)
            # Return an empty tensor
            num_envs = env.num_envs if hasattr(env, 'num_envs') else 1
            device = env.device if hasattr(env, 'device') else torch.device('cpu')
            return torch.zeros(num_envs, 0, device=device, dtype=torch.float32)
        
        # observation_func must be a callable function
        if not callable(obs_func):
            raise ValueError(
                f"Controller config for {robot_name} has invalid observation_func. "
                f"Expected callable function, got {type(obs_func)}"
            )
        
        return obs_func(env, robot_name)
    
    def compute_action(
        self, 
        env: Any, 
        robot_name: str, 
        observations: Optional[torch.Tensor],
        controller_dict: Dict[str, Any]
    ) -> torch.Tensor:
        """Compute action for this controller.
        
        This method should be overridden by subclasses.
        
        Args:
            env: Environment instance
            robot_name: Name of the robot
            observations: Observation tensor (None for command-driven controllers)
            controller_dict: Dictionary containing loaded controller (policy, etc.)
            
        Returns:
            Action tensor of shape (num_envs, action_dim)
        """
        raise NotImplementedError("Subclasses must implement compute_action")
    
    def compute_action_from_command(
        self,
        env: Any,
        robot_name: str,
        command: torch.Tensor,
        controller_dict: Dict[str, Any]
    ) -> torch.Tensor:
        """Compute action from external command (e.g., velocity command).
        
        compute_action_from_command_func must be a callable function:
        (controller_cfg, env, robot_name, command, controller_dict) -> action_tensor
        
        Args:
            env: Environment instance
            robot_name: Name of the robot
            command: External command tensor (e.g., velocity command [vx, vy, wz])
            controller_dict: Dictionary containing loaded controller data
            
        Returns:
            Action tensor of shape (num_envs, action_dim)
            
        Raises:
            ValueError: If compute_action_from_command_func is None or not callable
        """
        func = self.compute_action_from_command_func
        if func is None:
            # Default implementation: return command as-is
            return command
        
        # compute_action_from_command_func must be a callable function
        if not callable(func):
            raise ValueError(
                f"Controller config for {robot_name} has invalid compute_action_from_command_func. "
                f"Expected callable function, got {type(func)}"
            )
        
        return func(self, env, robot_name, command, controller_dict)
    
    def apply_action(
        self,
        env: Any,
        robot_name: str,
        action: torch.Tensor,
        controller_dict: Dict[str, Any]
    ) -> None:
        """Apply action to the robot.
        
        apply_action_func must be a callable function: (env, robot_name, action, controller_dict) -> None
        
        Args:
            env: Environment instance
            robot_name: Name of the robot
            action: Action tensor of shape (num_envs, action_dim)
            controller_dict: Dictionary containing loaded controller and related data
            
        Raises:
            ValueError: If apply_action_func is None or not callable
        """
        action_func = self.apply_action_func
        if action_func is None:
            raise ValueError(
                f"Controller config for {robot_name} must have apply_action_func defined. "
                f"All actions should be applied by the controller's apply_action_func."
            )
        
        # apply_action_func must be a callable function
        if not callable(action_func):
            raise ValueError(
                f"Controller config for {robot_name} has invalid apply_action_func. "
                f"Expected callable function, got {type(action_func)}"
            )
        
        return action_func(env, robot_name, action, controller_dict)
    
    def load(
        self,
        robot_name: str,
        task_name: str,
        device: str,
        env: Any,
    ) -> Dict[str, Any]:
        """Load controller resources (policy, model, etc.).
        
        This method should be overridden by subclasses to implement controller-specific loading.
        
        Args:
            robot_name: Name of the robot
            task_name: Task name for config registry
            device: Device string (e.g., "cuda:0")
            env: Environment instance
            
        Returns:
            Dictionary with controller metadata and loaded resources
        """
        raise NotImplementedError("Subclasses must implement load method")


def normalize_controller_entry(entry):
    """Normalize a controllers dict value to ``(primary_cfg, aux_cfgs)``.

    The *controllers* dictionary in ``MultiRobotDirectEnvCfg`` accepts two
    forms for each robot entry:

    * **Single** ``ControllerCfg`` – backward-compatible, no auxiliaries.
    * **Tuple** ``(primary_cfg, *aux_cfgs)`` – first element is the primary
      controller (responsible for observations, actions, loading); remaining
      elements are auxiliary post-apply callables (e.g. ``UR5_IK_CFG``).

    Returns:
        ``(primary_cfg, aux_cfgs)`` where *aux_cfgs* is an empty tuple when
        the entry is a single controller.
    """
    if isinstance(entry, tuple):
        if len(entry) == 0:
            raise ValueError("Controller tuple must not be empty")
        return entry[0], entry[1:]
    return entry, ()


def load_all_controllers(
    env_cfg: Any,
    task_name: str,
    device: str,
    env: Any,
) -> List[Dict[str, Any]]:
    """Load all controllers from env_cfg using their load methods.
    
    This is the unified entry point for loading controllers.
    Each controller type implements its own load method.
    Only primary controllers (first element when tuple form is used) are loaded;
    auxiliary controllers are managed by the environment runtime.
    
    Args:
        env_cfg: Environment configuration
        task_name: Task name for config registry
        device: Device string (e.g., "cuda:0")
        env: Environment instance
    
    Returns:
        List of controller dictionaries
    """
    controllers = []
    
    if hasattr(env_cfg, 'controllers') and env_cfg.controllers:
        for robot_name, entry in env_cfg.controllers.items():
            primary_cfg, _ = normalize_controller_entry(entry)
            controller = primary_cfg.load(robot_name, task_name, device, env)
            controllers.append(controller)
    
    if not controllers:
        raise ValueError(
            "No controllers found in env_cfg. "
        )
    
    return controllers

