# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause
"""SKRL Controller Configuration with Loader Support."""

import os
import torch
import numpy as np
from typing import Optional, Any, Dict, Tuple
from isaaclab.utils import configclass
from pathlib import Path
from isaaclab_tasks.utils.parse_cfg import load_cfg_from_registry
from .base import ControllerCfg
from .utils import get_model_dims, find_checkpoint, load_yaml_config


@configclass
class SKRLControllerCfg(ControllerCfg):
    """Base configuration class for SKRL Controllers.
    
    This base class defines all common SKRL-related parameters for robot controllers,
    including model paths, observation/action dimensions, and training config entry points.
    
    Subclasses should inherit from this class and can override default values or add
    robot-specific parameters.
    """
    
    # Model configuration
    model_path: str = "controller/robot.pt"
    """Path to the trained model checkpoint. Can be relative or absolute."""
    
    # SKRL configuration entry point
    skrl_cfg_entry_point: str = "skrl_cfg_entry_point"
    """SKRL configuration entry point name for loading training config."""
    
    # Policy configuration templates (for single robot)
    # These will be adapted for multi-robot scenarios
    observation_func: Optional[Any] = None
    """Single robot observation computation function. Can be a function or Manager-based config."""
    
    # Command name for external commands
    command_name: str = "base_velocity"
    """Command name used for external commands (e.g., 'base_velocity' for velocity control, 'goal_position' for goal control)."""
    
    def resolve_model_path(self) -> str:
        """Resolve the model path to an absolute path if it's relative.
        
        Returns:
            Absolute path to the model file.
        """
        if Path(self.model_path).is_absolute():
            return self.model_path
        
        # Try to find the model relative to the workspace root
        workspace_root = Path(__file__).resolve().parents[5]  # Go up to workspace root
        model_abs_path = workspace_root / self.model_path
        
        if model_abs_path.exists():
            return str(model_abs_path)
        
        # Return original path if not found (will be handled by loader)
        return self.model_path
    
    def compute_action(
        self, 
        env: Any, 
        robot_name: str, 
        observations: Optional[torch.Tensor],
        controller_dict: Dict[str, Any]
    ) -> torch.Tensor:
        """Compute action from observations using SKRL policy.
        
        Args:
            env: Environment instance
            robot_name: Name of the robot
            observations: Observation tensor
            controller_dict: Dictionary containing loaded policy and preprocessor
            
        Returns:
            Action tensor of shape (num_envs, action_dim)
        """
        if observations is None:
            raise ValueError(f"SKRL controller for {robot_name} requires observations")
        
        policy = controller_dict.get('policy')
        if policy is None:
            raise ValueError(f"Policy not found in controller_dict for {robot_name}")
        
        preprocessor = controller_dict.get('preprocessor')
        
        # Apply preprocessor if available
        if preprocessor is not None:
            if hasattr(preprocessor, 'process'):
                observations = preprocessor.process(observations)
            else:
                observations = preprocessor(observations)
        
        # Forward through policy
        with torch.inference_mode():
            action = policy.act({"states": observations}, role="policy")[0]
        
        # Ensure tensor format
        if not isinstance(action, torch.Tensor):
            if isinstance(action, np.ndarray):
                action = torch.from_numpy(action).to(observations.device)
            else:
                action = torch.tensor(action, device=observations.device)
        
        return action
    
    def load(
        self,
        robot_name: str,
        task_name: str,
        device: str,
        env: Any,
    ) -> Dict[str, Any]:
        """Load SKRL controller with automatic dimension inference.
        
        Dimensions are inferred in the following priority order:
        1. From model checkpoint (most reliable - matches training)
        2. From environment (if available and not placeholder values)
        
        Args:
            robot_name: Name of the robot
            task_name: Task name for config registry
            device: Device string (e.g., "cuda:0")
            env: Environment instance for dimension inference (optional fallback)
        
        Returns:
            Dictionary with controller metadata and loaded policy
        """
        base_env = env.unwrapped if hasattr(env, 'unwrapped') else env
        
        # Try to infer dimensions from environment (but may be None or placeholder values)
        # load_skrl_model will fall back to reading from checkpoint if these are None
        act_dim = self._get_action_dim(robot_name, base_env)
        obs_dim = self._get_observation_dim(robot_name, base_env)
        
        # Load model (will read dimensions from checkpoint if obs_dim/act_dim are None)
        policy, preprocessor = self._load_skrl_model(
            self.model_path,
            self.skrl_cfg_entry_point,
            task_name,
            device,
            obs_dim=obs_dim,
            act_dim=act_dim,
        )
        
        return {
            'name': robot_name,
            'policy': policy,
            'preprocessor': preprocessor,
        }
    
    def _load_skrl_model(
        self,
        model_path: str,
        skrl_cfg_entry: str,
        task_name: str,
        device: str,
        obs_dim: Optional[int] = None,
        act_dim: Optional[int] = None,
    ) -> Tuple[Any, Any]:
        """Load SKRL model and return (policy, preprocessor)."""
        from skrl.utils.runner.torch import Runner
        from gymnasium.spaces import Box
        
        # Resolve model path
        if not os.path.isabs(model_path):
            found = find_checkpoint(os.path.basename(model_path))
            model_path = found if found else (os.path.abspath(model_path) if os.path.exists(model_path) else model_path)
        
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"Model not found: {model_path}")
        
        # Get dimensions from checkpoint (always read for validation)
        inferred_obs_dim, inferred_act_dim = get_model_dims(model_path)
        
        # Use provided dimensions if available, otherwise use checkpoint dimensions
        # If both are available, validate they match (use checkpoint dimensions as ground truth)
        if obs_dim is None:
            obs_dim = inferred_obs_dim
        elif obs_dim != inferred_obs_dim:
            print(f"Warning: Observation dimension mismatch. Environment provided: {obs_dim}, Checkpoint requires: {inferred_obs_dim}. Using checkpoint dimension.")
            obs_dim = inferred_obs_dim
        
        if act_dim is None:
            act_dim = inferred_act_dim
        elif act_dim != inferred_act_dim:
            print(f"Warning: Action dimension mismatch. Environment provided: {act_dim}, Checkpoint requires: {inferred_act_dim}. Using checkpoint dimension.")
            act_dim = inferred_act_dim
        
        # Create minimal Space objects
        observation_space = Box(low=-1.0, high=1.0, shape=(obs_dim,))
        action_space = Box(low=-1.0, high=1.0, shape=(act_dim,))
        
        # Minimal env-like object
        class MinimalEnv:
            def __init__(self, obs_space, act_space, device_str):
                self._observation_space = obs_space
                self._action_space = act_space
                self._device = torch.device(device_str)
                self.possible_agents = ["agent"]
            
            @property
            def device(self): 
                return self._device
            
            @property
            def num_envs(self): 
                return 1
            
            @property
            def observation_space(self): 
                return self._observation_space
            
            @property
            def action_space(self): 
                return self._action_space
            
            @property
            def state_space(self): 
                return self._observation_space
        
        # Load config
        if ":" in skrl_cfg_entry:
            cfg = load_yaml_config(skrl_cfg_entry)
        else:
            cfg = load_cfg_from_registry(task_name, skrl_cfg_entry)
        
        cfg["trainer"]["close_environment_at_exit"] = False
        if "agent" in cfg and "experiment" in cfg["agent"]:
            cfg["agent"]["experiment"]["write_interval"] = 0
            cfg["agent"]["experiment"]["checkpoint_interval"] = 0
        
        # Create minimal env and load model
        minimal_env = MinimalEnv(observation_space, action_space, device)
        runner = Runner(minimal_env, cfg)
        runner.agent.load(model_path)
        runner.agent.set_running_mode("eval")
        
        preprocessor = getattr(runner.agent, 'state_preprocessor', None) or getattr(runner.agent, '_state_preprocessor', None)
        return runner.agent.policy, preprocessor
    
    def _get_action_dim(self, robot_name: str, base_env: Any) -> Optional[int]:
        """Get action dimension from DirectMARL environment.
        
        Only supports DirectMARLEnv (via action_spaces).
        """
        # Priority 1: Try to get from robot DOF if available (for RL controllers)
        try:
            if hasattr(base_env, 'scene') and hasattr(base_env.scene, 'articulations'):
                if robot_name in base_env.scene.articulations:
                    robot = base_env.scene.articulations[robot_name]
                    if hasattr(robot, 'num_joints'):
                        act_dim = robot.num_joints
                        # Only return if it's not a placeholder value (20 is the placeholder)
                        if act_dim != 20:
                            return act_dim
        except (AttributeError, KeyError):
            pass
        
        # Priority 2: Try DirectMARLEnv approach (check action_spaces)
        # But only if it's not a placeholder value
        if hasattr(base_env, 'action_spaces') and isinstance(base_env.action_spaces, dict):
            if robot_name in base_env.action_spaces:
                action_space = base_env.action_spaces[robot_name]
                # action_space might be a gym.Space or an int
                if hasattr(action_space, 'shape'):
                    act_dim = action_space.shape[0] if len(action_space.shape) > 0 else None
                    # Only return if it's not a placeholder value
                    if act_dim is not None and act_dim != 20:
                        return act_dim
                elif isinstance(action_space, int) and action_space != 20:
                    return action_space
        
        # Priority 3: Check config directly (might have placeholder values)
        if hasattr(base_env, 'cfg') and hasattr(base_env.cfg, 'action_spaces'):
            if isinstance(base_env.cfg.action_spaces, dict) and robot_name in base_env.cfg.action_spaces:
                act_dim = base_env.cfg.action_spaces[robot_name]
                if isinstance(act_dim, int) and act_dim != 20:
                    return act_dim
        
        # Return None to trigger model dimension inference from checkpoint
        return None
    
    def _get_observation_dim(self, robot_name: str, base_env: Any) -> Optional[int]:
        """Get observation dimension from DirectMARL environment.
        
        Only supports DirectMARLEnv (via observation_spaces).
        """
        # Priority 1: Try to compute observation dimension by actually computing observations
        # This is the most reliable method and should be tried first
        try:
            if hasattr(base_env, 'cfg') and hasattr(base_env.cfg, 'controllers'):
                controller_cfg = base_env.cfg.controllers.get(robot_name)
                if controller_cfg and controller_cfg.observation_func:
                    # Compute observation once to get dimension
                    obs = controller_cfg.compute_observations(base_env, robot_name)
                    if isinstance(obs, torch.Tensor):
                        obs_dim = obs.shape[-1] if len(obs.shape) > 1 else obs.shape[0]
                        # Only return if it's not a placeholder value (100 is the placeholder)
                        if obs_dim != 100:
                            return obs_dim
        except Exception as e:
            # If computation fails (e.g., environment not fully initialized), continue to other methods
            pass
        
        # Priority 2: Try DirectMARLEnv approach (check observation_spaces)
        # But only if it's not a placeholder value
        if hasattr(base_env, 'observation_spaces') and isinstance(base_env.observation_spaces, dict):
            if robot_name in base_env.observation_spaces:
                obs_space = base_env.observation_spaces[robot_name]
                # obs_space might be a gym.Space or an int
                if hasattr(obs_space, 'shape'):
                    obs_dim = obs_space.shape[0] if len(obs_space.shape) > 0 else None
                    # Only return if it's not a placeholder value
                    if obs_dim is not None and obs_dim != 100:
                        return obs_dim
                elif isinstance(obs_space, int) and obs_space != 100:
                    return obs_space
        
        # Priority 3: Check config directly (might have placeholder values)
        if hasattr(base_env, 'cfg') and hasattr(base_env.cfg, 'observation_spaces'):
            if isinstance(base_env.cfg.observation_spaces, dict) and robot_name in base_env.cfg.observation_spaces:
                obs_dim = base_env.cfg.observation_spaces[robot_name]
                if isinstance(obs_dim, int) and obs_dim != 100:
                    return obs_dim
        
        # Return None to trigger model dimension inference from checkpoint
        return None

