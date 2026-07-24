# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause
"""Multi-Robot DirectMARL Environment Configuration Base Class."""

from typing import Any, Dict, Optional
from dataclasses import MISSING
from isaaclab.utils import configclass
from isaaclab.envs import DirectMARLEnvCfg
from EAI.controllers.base import normalize_controller_entry


@configclass
class MultiRobotDirectEnvCfg(DirectMARLEnvCfg):
    """Base class for multi-robot DirectMARL environments.
    
    This base class provides:
    - Automatic generation of possible_agents, observation_spaces, action_spaces from controllers
    - Default simulation parameters suitable for inference
    - Commands configuration for command managers
    
    Subclasses should:
    1. Define the scene configuration
    2. Define controllers dictionary mapping robot names to their controller configs
    3. Optionally override __post_init__ to customize simulation parameters
    
    Note:
    - The observation_spaces and action_spaces are initialized with placeholder values.
      Actual dimensions are computed during environment initialization.
    - For now, we use large placeholder values (1000 for obs, 100 for actions).
      These should be replaced with actual computed dimensions in a future update.
    - Each value may also be a tuple ``(primary ControllerCfg, *auxiliary controllers)``
      (e.g. mobile base + arm IK).
    """

    # Optional: end of MultiRobotDirectEnv._apply_action / after _reset_idx (factory UR5 teleop).
    post_apply_action: Optional[Any] = None
    after_reset_idx_hook: Optional[Any] = None

    # Unified controllers configuration
    controllers: Dict[str, Any] = {}
    """统一控制器配置字典，键为机器人资产名字，值为对应的控制器配置。
    
    这是推荐的方式，可以将所有类型的控制器（SKRL、差速驱动等）放在一个字典中。
    字典的顺序很重要！它决定了观测和动作的拼接顺序。
    
    支持的控制器类型：
    - SKRLControllerCfg: SKRL 强化学习控制器
    - DifferentialDriveControllerCfg: 差速驱动传统控制器
    - 元组 (主控制器, 辅助控制器...)：如移动底盘 + UR5 机械臂 IK
    """
    
    # These will be set in __post_init__ from controllers
    possible_agents: list[str] = MISSING
    observation_spaces: dict[str, Any] = MISSING
    action_spaces: dict[str, Any] = MISSING
    state_space: int = MISSING
    
    def __post_init__(self):
        """Post-initialization: generate multi-robot configs and set simulation parameters."""
        # 1. Validate controllers is defined
        if not self.controllers:
            raise ValueError(
                "At least one controller must be defined in 'controllers' dictionary."
            )
        
        # 2. Generate possible_agents from controllers keys
        self.possible_agents = list(self.controllers.keys())
        
        # 3. Generate observation_spaces and action_spaces
        # For DirectMARL, we need actual dimensions, but computing them requires an environment instance.
        # We use placeholder values here. The actual dimensions will be computed during
        # environment initialization in _update_spaces_with_actual_dims()
        
        observation_spaces = {}
        action_spaces = {}
        
        for robot_name, entry in self.controllers.items():
            primary_cfg, _ = normalize_controller_entry(entry)
            # For observation space: use placeholder, will be updated during env init
            if primary_cfg.observation_func is not None:
                # Placeholder: will be updated during environment initialization
                # We use a reasonable default that will be replaced with actual dimension
                observation_spaces[robot_name] = 100  # Placeholder, will be updated
            else:
                # Controller doesn't need observations (command-driven)
                observation_spaces[robot_name] = 0
            
            # For action space: use placeholder, will be updated during env init
            # Actual dimension will be inferred from robot_type or robot DOF during env init
            action_spaces[robot_name] = 20  # Placeholder, will be updated during environment initialization
        
        self.observation_spaces = observation_spaces
        self.action_spaces = action_spaces
        
        # 4. Set state_space to -1 (auto-concatenate all observations)
        self.state_space = -1
        
        # 5. Set default simulation parameters (can be overridden in subclasses)
        self.decimation = 4  # Control frequency is 1/4 of simulation frequency
        self.episode_length_s = 3600.0  # 1 hour - effectively no timeout for inference
        
        # Set simulation timestep and render interval
        # Note: sim should already be initialized from DirectMARLEnvCfg default
        self.sim.dt = 0.005  # Physics simulation timestep 5ms
        self.sim.render_interval = self.decimation
        
        # Set PhysX parameters if available
        if hasattr(self.sim, 'physx') and self.sim.physx is not None:
            if hasattr(self.sim.physx, 'gpu_max_rigid_patch_count'):
                self.sim.physx.gpu_max_rigid_patch_count = 10 * 2 ** 15
        
        # 6. Set global simulation physics material from terrain (critical for friction!)
        # This ensures robot-terrain contacts use the terrain's physics material for friction
        # Without this, robots will have very low/no friction and slip
        if self.scene is not None and hasattr(self.scene, 'terrain') and self.scene.terrain is not None:
            if hasattr(self.scene.terrain, 'physics_material') and self.scene.terrain.physics_material is not None:
                self.sim.physics_material = self.scene.terrain.physics_material
        
        # 7. Disable terrain generator if present (not needed for inference)
        if self.scene is not None and hasattr(self.scene, 'terrain') and self.scene.terrain is not None:
            if hasattr(self.scene.terrain, 'terrain_generator'):
                self.scene.terrain.terrain_generator = None

