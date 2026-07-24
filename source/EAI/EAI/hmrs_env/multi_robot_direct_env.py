# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause
"""Multi-Robot DirectMARL Environment Implementation."""

from __future__ import annotations

import torch
from collections.abc import Sequence
from typing import Any, Dict

from isaaclab.envs import DirectMARLEnv
from .multi_robot_direct_env_cfg import MultiRobotDirectEnvCfg
from EAI.controllers import ControllerCfg, load_all_controllers
from EAI.controllers.base import normalize_controller_entry


class MultiRobotDirectEnv(DirectMARLEnv):
    """Multi-robot DirectMARL environment.
    
    This environment handles multiple robots by delegating all logic to controller configs.
    The environment only provides the scene and calls controller methods for observations and actions.
    """
    
    cfg: MultiRobotDirectEnvCfg
    
    def __init__(self, cfg: MultiRobotDirectEnvCfg, render_mode: str | None = None, **kwargs):
        """Initialize the multi-robot DirectMARL environment."""
        super().__init__(cfg, render_mode, **kwargs)
        self._capture_attached_articulation_defaults()
        
        # Store actions dictionary (will be set in _pre_physics_step)
        self.actions_dict: Dict[str, torch.Tensor] = {}

        # Primary controller per robot; optional auxiliary controllers (e.g. UR5 IK) after apply_action.
        self._controller_configs: Dict[str, ControllerCfg] = {}
        self._auxiliary_controllers: Dict[str, tuple] = {}
        for robot_name, entry in cfg.controllers.items():
            primary, aux = normalize_controller_entry(entry)
            self._controller_configs[robot_name] = primary
            self._auxiliary_controllers[robot_name] = aux

        # Loaded controllers (populated after scene is ready)
        self._controllers: list[Dict[str, Any]] = []
        self._controllers_dict: Dict[str, Dict[str, Any]] = {}  # robot_name -> controller_dict # ?
        
        # Flag to track if controllers are loaded
        self._controllers_loaded = False

    def _capture_attached_articulation_defaults(self) -> None:
        for name, robot in self.scene.articulations.items():
            if not name.endswith("_arm"):
                continue
            root_state = robot.data.root_state_w.clone()
            root_state[:, :3] -= self.scene.env_origins
            robot.data.default_root_state[:] = root_state
    
    def reset(self, seed: int | None = None, options: dict[str, Any] | None = None):
        """Reset the environment and load controllers on first reset."""
        # Load controllers on first reset (when scene is ready)
        if not self._controllers_loaded:
            if not self.sim.is_playing():
                raise RuntimeError("Cannot load controllers: simulation is not playing")
            
            self._load_controllers()
            self._update_spaces_with_actual_dims()
            self._controllers_loaded = True
        
        # Call parent reset
        return super().reset(seed, options)
    
    def _load_controllers(self):
        """Load all controllers using controller loaders."""
        task_name = getattr(self.cfg, 'task_name', 'unknown')
        
        # Load all controllers
        self._controllers = load_all_controllers(
            self.cfg,
            task_name=task_name,
            device=str(self.device),
            env=self,
        )
        
        # Build dictionary mapping robot names to controller dictionaries
        for controller_dict in self._controllers:
            robot_name = controller_dict['name']
            self._controllers_dict[robot_name] = controller_dict
    
    def _update_spaces_with_actual_dims(self):
        """Update observation and action spaces with actual computed dimensions."""
        # For observations: compute once to get actual dimensions
        for robot_name in self.cfg.possible_agents:
            controller_cfg = self._controller_configs[robot_name]
            
            # Compute observation dimensions
            # Some controllers (e.g., traditional command-driven controllers) may not need observations
            if controller_cfg.observation_func is not None:
                try:
                    obs = controller_cfg.compute_observations(self, robot_name)
                    obs_dim = obs.shape[-1] if len(obs.shape) > 1 else obs.shape[0]
                    self.cfg.observation_spaces[robot_name] = obs_dim
                except Exception as e:
                    print(f"Warning: Could not compute observation dimension for {robot_name}: {e}")
                    # Use placeholder
                    self.cfg.observation_spaces[robot_name] = 48
            else:
                # For controllers without observation_func (e.g., traditional command-driven controllers),
                # use a minimal observation space (0D or 1D placeholder)
                # These controllers don't use observations, so we provide an empty/minimal space
                self.cfg.observation_spaces[robot_name] = 0
            
            # For actions: infer from loaded controller or robot DOF
            try:
                act_dim = None
                
                # Priority 1: Try to get from loaded controller (e.g., from model checkpoint)
                if robot_name in self._controllers_dict:
                    controller_dict = self._controllers_dict[robot_name]
                    # For SKRL controllers, try to get action_dim from policy
                    if 'policy' in controller_dict:
                        policy = controller_dict['policy']
                        # Try to get action_dim from policy's action_space
                        if hasattr(policy, 'action_space'):
                            action_space = policy.action_space
                            if hasattr(action_space, 'shape') and len(action_space.shape) > 0:
                                act_dim = action_space.shape[0]
                        # Try to get from policy model's output layer
                        if act_dim is None and hasattr(policy, 'model'):
                            model = policy.model
                            # Check for log_std_parameter
                            if hasattr(model, 'log_std_parameter'):
                                act_dim = model.log_std_parameter.shape[0]
                            # Try to find policy_layer in model
                            if act_dim is None:
                                for name, param in model.named_parameters():
                                    if 'policy_layer' in name and 'weight' in name:
                                        act_dim = param.shape[0]
                                        break
                
                # Priority 2: Try to get from robot DOF (for non-RL controllers or fallback)
                if act_dim is None and robot_name in self.scene.articulations:
                    robot = self.scene.articulations[robot_name]
                    if hasattr(robot, 'num_joints'):
                        act_dim = robot.num_joints
                        # Only use if it's not a placeholder value
                        if act_dim == 20:
                            act_dim = None  # Don't use placeholder
                
                # Priority 3: Try to get from controller config if it has action_dim attribute
                if act_dim is None and hasattr(controller_cfg, 'action_dim'):
                    act_dim = controller_cfg.action_dim
                
                # Set action space dimension
                if act_dim is not None:
                    self.cfg.action_spaces[robot_name] = act_dim
                else:
                    # Fallback: use placeholder (will be updated when action is first applied)
                    self.cfg.action_spaces[robot_name] = self.cfg.action_spaces.get(robot_name, 20)
            except Exception as e:
                print(f"Warning: Could not infer action dimension for {robot_name}: {e}")
                self.cfg.action_spaces[robot_name] = self.cfg.action_spaces.get(robot_name, 20)
        
        # Update observation and action spaces in the environment
        from isaaclab.envs.utils.spaces import spec_to_gym_space
        self.observation_spaces = {
            agent: spec_to_gym_space(self.cfg.observation_spaces[agent]) 
            for agent in self.cfg.possible_agents
        }
        self.action_spaces = {
            agent: spec_to_gym_space(self.cfg.action_spaces[agent])
            for agent in self.cfg.possible_agents
        }
    
    def _get_observations(self) -> dict[str, torch.Tensor]:
        """Compute observations for all agents.
        
        All observations are computed by controllers using their observation_func.
        Controllers are responsible for defining and computing their own observations.
        
        observation_func must be a callable function: (env, robot_name) -> observation_tensor
        For controllers without observation_func, returns an empty observation tensor.
        """
        
        observations = {}
        for robot_name in self.cfg.possible_agents:
            controller_cfg = self._controller_configs[robot_name]
            
            if controller_cfg.observation_func is None:
                # For controllers without observation_func (e.g., traditional command-driven controllers),
                # return an empty observation tensor
                obs_dim = self.cfg.observation_spaces.get(robot_name, 0)
                if obs_dim == 0:
                    # Empty observation (0D tensor)
                    observations[robot_name] = torch.zeros(self.num_envs, 0, device=self.device)
                else:
                    # Placeholder observation if dimension was set
                    observations[robot_name] = torch.zeros(self.num_envs, obs_dim, device=self.device)
            else:
                # Use controller's compute_observations method
                obs = controller_cfg.compute_observations(self, robot_name)
                observations[robot_name] = obs
        
        return observations
    
    def set_command(self, robot_name: str, command_name: str, value: torch.Tensor) -> None:
        """Set command value for a robot.
        
        This is a simple interface for DirectMARL environments to set command values
        that will be used in observation computation.
        
        Commands are stored as environment attributes that observation functions can access.
        For multi-robot scenarios, commands are stored with robot name suffix.
        
        Args:
            robot_name: Name of the robot
            command_name: Name of the command (e.g., "base_velocity", "goal_position")
            value: Command value tensor of shape (num_envs, command_dim)
        """
        # Initialize commands dictionary if it doesn't exist
        if not hasattr(self, '_commands_dict'):
            self._commands_dict = {}
        
        # Store command with robot name as key
        if robot_name not in self._commands_dict:
            self._commands_dict[robot_name] = {}
        
        self._commands_dict[robot_name][command_name] = value.clone()
        
        # Also set as attribute for backward compatibility (with robot name suffix)
        attr_name = f"_{command_name}_{robot_name}" if not command_name.startswith('_') else f"{command_name}_{robot_name}"
        setattr(self, attr_name, value.clone())
    
    def get_command(self, robot_name: str, command_name: str) -> torch.Tensor | None:
        """Get command value for a robot.
        
        Args:
            robot_name: Name of the robot
            command_name: Name of the command
            
        Returns:
            Command value tensor or None if not found
        """
        # Try commands dictionary first
        if hasattr(self, '_commands_dict') and robot_name in self._commands_dict:
            if command_name in self._commands_dict[robot_name]:
                return self._commands_dict[robot_name][command_name]
        
        # Try attribute with robot name suffix
        attr_name = f"_{command_name}_{robot_name}" if not command_name.startswith('_') else f"{command_name}_{robot_name}"
        if hasattr(self, attr_name):
            return getattr(self, attr_name)
        
        # Fallback: try without robot name (for backward compatibility)
        attr_name = f"_{command_name}" if not command_name.startswith('_') else command_name
        if hasattr(self, attr_name):
            return getattr(self, attr_name)
        
        return None
    
    def _pre_physics_step(self, actions: dict[str, torch.Tensor]) -> None:
        """Pre-process actions before physics step.
        
        Actions passed here are external commands (e.g., velocity commands from keyboard).
        Controllers are responsible for converting commands to actions using their
        compute_action_from_command method.
        
        Note: For RL controllers, the observation computation (inside compute_action_from_command)
        will use the last action from self.actions_dict, which should be from the previous step.
        After computing new actions, we update self.actions_dict for the next step.
        """
        
        # Store previous actions_dict for last_action computation in observations
        # This will be used by RL controllers when computing observations
        previous_actions_dict = self.actions_dict.copy() if self.actions_dict else {}
        
        # Let controllers convert commands to actions
        computed_actions = {}
        
        for robot_name in self.cfg.possible_agents:
            if robot_name not in actions:
                continue
            
            command = actions[robot_name]
            controller_cfg = self._controller_configs[robot_name]
            controller_dict = self._controllers_dict.get(robot_name, {})
            
            # Temporarily set previous action for observation computation
            # This ensures last_action in observations is from the previous step
            if robot_name in previous_actions_dict:
                self.actions_dict[robot_name] = previous_actions_dict[robot_name]
            
            # Let controller convert command to action
            # This handles both traditional controllers (command -> action) 
            # and RL controllers (command -> observation -> policy -> action)
            action = controller_cfg.compute_action_from_command(
                self, robot_name, command, controller_dict
            )
            computed_actions[robot_name] = action
        
        # Store computed actions for application and next step's last_action
        self.actions_dict = computed_actions
    
    def _apply_action(self) -> None:
        """Apply actions to all robots.
        
        All actions are applied by controllers using their apply_action methods.
        Controllers are responsible for applying actions appropriately for their robot type.
        
        Also updates action space dimensions if they were placeholders.
        """
        
        for robot_name in self.cfg.possible_agents:
            if robot_name not in self.actions_dict:
                continue
            
            action = self.actions_dict[robot_name]
            controller_cfg = self._controller_configs[robot_name]
            controller_dict = self._controllers_dict.get(robot_name, {})
            
            # Update action space dimension if it was a placeholder and we now have actual action
            if self.cfg.action_spaces.get(robot_name) == 20 and action is not None:
                act_dim = action.shape[-1] if len(action.shape) > 1 else action.shape[0]
                if act_dim != 20:  # Only update if it's not the placeholder
                    self.cfg.action_spaces[robot_name] = act_dim
                    # Update the gym space as well
                    from isaaclab.envs.utils.spaces import spec_to_gym_space
                    self.action_spaces[robot_name] = spec_to_gym_space(act_dim)
            
            # Call controller's apply_action method
            # Controller handles how to apply actions (joint positions, forces, etc.)
            controller_cfg.apply_action(self, robot_name, action, controller_dict)

        seen_aux_ids: set[int] = set()
        for robot_name in self.cfg.possible_agents:
            for aux in self._auxiliary_controllers.get(robot_name, ()):
                aux_id = id(aux)
                if aux_id not in seen_aux_ids:
                    seen_aux_ids.add(aux_id)
                    if callable(aux):
                        aux(self)

        post = getattr(self.cfg, "post_apply_action", None)
        if post is not None and callable(post):
            post(self)

    def _get_rewards(self) -> dict[str, torch.Tensor]:
        """Compute rewards for all agents."""
        return {agent: torch.zeros(self.num_envs, device=self.device) for agent in self.cfg.possible_agents}
    
    def _get_dones(self) -> tuple[dict[str, torch.Tensor], dict[str, torch.Tensor]]:
        """Compute done flags for all agents."""
        time_out = self.episode_length_buf >= self.max_episode_length - 1
        return (
            {agent: torch.zeros(self.num_envs, dtype=torch.bool, device=self.device) for agent in self.cfg.possible_agents},
            {agent: time_out.clone() for agent in self.cfg.possible_agents}
        )
    
    def _reset_idx(self, env_ids: Sequence[int] | None) -> None:
        """Reset environments at specified indices."""
        super()._reset_idx(env_ids)
        if env_ids is None:
            env_ids = torch.arange(self.num_envs, device=self.device)
        elif not isinstance(env_ids, torch.Tensor):
            env_ids = torch.as_tensor(env_ids, device=self.device, dtype=torch.long)

        for robot in self.scene.articulations.values():
            robot.reset(env_ids)
            default_root_state = robot.data.default_root_state[env_ids].clone()
            default_root_state[:, :3] += self.scene.env_origins[env_ids]
            joint_pos = robot.data.default_joint_pos[env_ids]
            joint_vel = robot.data.default_joint_vel[env_ids]

            robot.write_root_pose_to_sim(default_root_state[:, :7], env_ids)
            robot.write_root_velocity_to_sim(default_root_state[:, 7:], env_ids)
            robot.write_joint_state_to_sim(joint_pos, joint_vel, None, env_ids)
            robot.set_joint_position_target(joint_pos, env_ids=env_ids)

        self.actions_dict = {}
        # Controllers can override this if they need to reset their state
        hook = getattr(self.cfg, "after_reset_idx_hook", None)
        if hook is not None and callable(hook):
            hook(self, env_ids)
