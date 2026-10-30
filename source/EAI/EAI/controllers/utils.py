# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause

"""Utility functions for controller loading."""

import os
import torch
import numpy as np
import importlib
from pathlib import Path
from typing import Optional, Tuple, Any, Dict

try:
    import onnxruntime as ort
except ImportError:
    ort = None


def get_model_dims(model_path: str) -> Tuple[int, int]:
    """Extract obs_dim and act_dim from model checkpoint."""
    checkpoint = torch.load(model_path, map_location='cpu')
    
    # Handle different checkpoint formats
    if isinstance(checkpoint, dict):
        if 'state_dict' in checkpoint:
            state_dict = checkpoint['state_dict']
        elif 'policy' in checkpoint:
            state_dict = checkpoint['policy']
        else:
            state_dict = checkpoint
    else:
        raise ValueError(f"Unexpected checkpoint format: {type(checkpoint)}")
    
    if state_dict is None:
        raise ValueError(f"Cannot find state_dict in checkpoint. Top-level keys: {list(checkpoint.keys())}")
    
    all_keys = list(state_dict.keys())
    
    # Get act_dim
    act_dim = None
    for key in all_keys:
        if 'policy_layer.weight' in key:
            act_dim = state_dict[key].shape[0]
            break
    
    if act_dim is None:
        for key in all_keys:
            if 'log_std_parameter' in key:
                shape = state_dict[key].shape
                act_dim = shape[0] if len(shape) == 1 else shape[-1]
                break
    
    # Get obs_dim
    obs_dim = None
    patterns = [
        'net_container.0.weight',
        'net.0.weight',
        'features.0.weight',
        'actor.0.weight',
        'policy.0.weight',
        'mlp.0.weight',
    ]
    for pattern in patterns:
        for key in all_keys:
            if pattern in key:
                shape = state_dict[key].shape
                if len(shape) >= 2:
                    obs_dim = shape[1]
                    break
        if obs_dim is not None:
            break
    
    if obs_dim is None:
        for key in sorted(all_keys):
            if '.weight' in key and len(state_dict[key].shape) == 2:
                if 'policy_layer' not in key and 'value_layer' not in key and 'critic' not in key:
                    obs_dim = state_dict[key].shape[1]
                    break
    
    if act_dim is None:
        raise ValueError(
            f"Cannot infer act_dim from {model_path}. "
            f"Top-level keys: {list(checkpoint.keys()) if isinstance(checkpoint, dict) else 'N/A'}, "
            f"Policy keys (first 10): {all_keys[:10]}"
        )
    
    if obs_dim is None:
        raise ValueError(
            f"Cannot infer obs_dim from {model_path}. "
            f"Top-level keys: {list(checkpoint.keys()) if isinstance(checkpoint, dict) else 'N/A'}, "
            f"Policy keys (first 10): {all_keys[:10]}"
        )
    
    return obs_dim, act_dim


def find_checkpoint(name: str) -> Optional[str]:
    """Find checkpoint file by name."""
    for root, _, files in os.walk(os.getcwd()):
        if name in files:
            return os.path.join(root, name)
    return None


def load_yaml_config(path: str):
    """Load YAML config file."""
    module_name, filename = path.split(":", 1)
    module = importlib.import_module(module_name)
    yaml_path = Path(module.__file__).parent / filename
    import yaml
    with open(yaml_path, 'r') as f:
        return yaml.safe_load(f)


class ONNXPolicy:
    """Wrapper for ONNX model that provides act() method compatible with dispatcher."""
    
    def __init__(self, session: Any, device: str = "cpu"):
        """
        Initialize ONNX policy wrapper.
        
        Args:
            session: ONNX Runtime inference session
            device: Device string (e.g., "cuda:0" or "cpu")
        """
        if ort is None:
            raise ImportError(
                "onnxruntime is required for ONNXPolicy. "
                "Install it with: pip install onnxruntime"
            )
        
        self.session = session
        self.device = device
        
        # Get input/output names
        self.input_name = session.get_inputs()[0].name
        output_names = [output.name for output in session.get_outputs()]
        self.output_name = output_names[0] if output_names else None
    
    def act(self, inputs: Dict[str, Any], role: str = "policy") -> tuple:
        """
        Run inference with ONNX model.
        
        Args:
            inputs: Dictionary with "states" key containing observation tensor
            role: Role string (unused, kept for compatibility)
        
        Returns:
            Tuple containing (actions, None) where actions is a numpy array
        """
        if "states" not in inputs:
            raise ValueError("Input dictionary must contain 'states' key")
        
        states = inputs["states"]
        
        # Convert to numpy if tensor
        if hasattr(states, "cpu"):
            states = states.cpu().numpy()
        elif hasattr(states, "numpy"):
            states = states.numpy()
        
        # Ensure contiguous array
        if not states.flags['C_CONTIGUOUS']:
            states = np.ascontiguousarray(states)
        
        # Run inference
        outputs = self.session.run([self.output_name], {self.input_name: states})
        actions = outputs[0]
        
        return (actions, None)
    
    def reset(self, dones: Optional[Any] = None):
        """Reset internal state (no-op for ONNX models without recurrent state)."""
        pass

