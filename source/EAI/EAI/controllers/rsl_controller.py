# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause
"""RSL-RL Controller Configuration with Loader Support."""

import os
import torch
import numpy as np
from typing import Optional, Any, Dict
from isaaclab.utils import configclass
from pathlib import Path

try:
    import onnxruntime as ort
except ImportError:
    raise ImportError(
        "onnxruntime is required for RSL controller. "
        "Install it with: pip install onnxruntime"
    )

from .base import ControllerCfg
from .utils import find_checkpoint, ONNXPolicy


@configclass
class RSLControllerCfg(ControllerCfg):
    """Base configuration class for RSL-RL Controllers.
    
    This base class defines all common RSL-RL-related parameters for robot controllers,
    including ONNX model paths and observation/action configuration.
    
    Subclasses should inherit from this class and can override default values or add
    robot-specific parameters.
    """
    
    # Model configuration
    model_path: str = "controller/rsl/model/robot.onnx"
    """Path to the trained ONNX model. Can be relative or absolute."""
    
    # Policy configuration templates (for single robot)
    # These will be adapted for multi-robot scenarios
    observation_func: Optional[Any] = None
    """Single robot observation computation function. Can be a function or Manager-based config."""
    
    # Command name for external commands
    command_name: str = "base_velocity"
    """Command name used for external commands (e.g., 'base_velocity' for velocity control, 'goal_position' for goal control)."""
    
    actions_cfg: Optional[Any] = None
    """Single robot actions configuration template. Should use asset_name="robot"."""
    
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
        
        # Try to find relative to EAI_assets
        eai_assets_root = Path(__file__).resolve().parents[4] / "EAI_assets" / "EAI_assets"
        model_abs_path = eai_assets_root / self.model_path
        
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
        """Compute action from observations using RSL ONNX policy.
        
        Args:
            env: Environment instance
            robot_name: Name of the robot
            observations: Observation tensor
            controller_dict: Dictionary containing loaded ONNX policy
            
        Returns:
            Action tensor of shape (num_envs, action_dim)
        """
        if observations is None:
            raise ValueError(f"RSL controller for {robot_name} requires observations")
        
        policy = controller_dict.get('policy')
        if policy is None:
            raise ValueError(f"Policy not found in controller_dict for {robot_name}")
        
        # Forward through ONNX policy
        # ONNXPolicy.act() expects a dictionary with "states" key and returns (actions, None)
        with torch.inference_mode():
            action_tuple = policy.act({"states": observations})
            action = action_tuple[0]  # Extract actions from tuple
        
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
        """Load RSL-RL ONNX controller.
        
        Args:
            robot_name: Name of the robot
            task_name: Task name for config registry (unused, kept for compatibility)
            device: Device string (e.g., "cuda:0")
            env: Environment instance (unused)
        
        Returns:
            Dictionary with controller metadata and loaded ONNX policy
        """
        # Resolve model path
        model_path = self.resolve_model_path()
        
        # Try to find the model if path is relative
        if not os.path.isabs(model_path):
            # First try find_checkpoint utility
            found = find_checkpoint(os.path.basename(model_path))
            if found:
                model_path = found
            elif os.path.exists(model_path):
                model_path = os.path.abspath(model_path)
            else:
                # Try relative to EAI_assets directory
                current_file = Path(__file__).resolve()
                eai_assets_root = current_file.parents[4] / "EAI_assets" / "EAI_assets"
                potential_path = eai_assets_root / model_path
                if potential_path.exists():
                    model_path = str(potential_path)
        
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"ONNX model not found: {model_path}")
        
        # Determine execution provider
        if device != "cpu" and "CUDAExecutionProvider" in ort.get_available_providers():
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        else:
            providers = ["CPUExecutionProvider"]
        
        # Load ONNX model
        sess_options = ort.SessionOptions()
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        
        # Create inference session
        try:
            session = ort.InferenceSession(model_path, sess_options=sess_options, providers=providers)
        except Exception as e:
            raise RuntimeError(f"Failed to load ONNX model from {model_path}: {e}")
        
        # Wrap session in policy object
        policy = ONNXPolicy(session, device)
        
        return {
            'name': robot_name,
            'policy': policy,
        }

