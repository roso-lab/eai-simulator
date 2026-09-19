# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause
"""Multi-Robot DirectMARL Environment Implementation."""

from __future__ import annotations

import torch
from collections.abc import Sequence
from copy import deepcopy
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
        # Optional replay hook: precomputed actuator actions bypass controller inference.
        self._replay_precomputed_actions: Dict[str, torch.Tensor] | None = None
        self._replay_precomputed_used = False
        self._last_computed_actions: Dict[str, torch.Tensor] = {}

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

        # Benchmark lifecycle state is separate from Isaac's object inventory.
        # Reserve articulations stay preloaded, while this gate pins inactive
        # members at their authored parking pose and blocks controller output.
        self._benchmark_agent_active = {
            robot_name: True for robot_name in cfg.controllers
        }
        self._benchmark_capability_declarations: Dict[str, Dict[str, Any]] = {}
        self._benchmark_capability_versions = {
            robot_name: 1 for robot_name in cfg.controllers
        }
        self._benchmark_park_poses: Dict[str, torch.Tensor] = {}

    def _setup_scene(self) -> None:
        from isaaclab.sim import find_matching_prim_paths
        from EAI.physics.articulation_reset import capture_root_poses

        self._attached_spawn_poses = {}
        for name, robot in self.scene.articulations.items():
            if name.endswith("_arm"):
                root_pattern = robot.cfg.prim_path + (robot.cfg.articulation_root_prim_path or "")
                paths = find_matching_prim_paths(root_pattern)
                self._attached_spawn_poses.update(capture_root_poses(self.sim.get_initial_stage(), paths))

    def _capture_attached_articulation_defaults(self) -> None:
        from EAI.physics.articulation_reset import set_spawn_root_defaults

        for name, robot in self.scene.articulations.items():
            if not name.endswith("_arm"):
                continue
            set_spawn_root_defaults(robot, self._attached_spawn_poses, self.scene.env_origins)
    
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

    def configure_benchmark_lifecycle(
        self,
        *,
        active_agent_ids: set[str] | frozenset[str],
        capability_declarations: Dict[str, Dict[str, Any]],
        capability_versions: Dict[str, int] | None = None,
    ) -> None:
        """Configure preloaded benchmark membership and controller admission.

        This API never spawns or removes an articulation. Inactive members are
        held at their current authored pose until a verified join activates
        them. Capability declarations are consulted on every control step, so
        disabling ``navigation_interface`` has an immediate physical control
        effect instead of being metadata-only.
        """
        expected = set(self._controller_configs)
        active = set(active_agent_ids)
        declarations = dict(capability_declarations)
        versions = dict(capability_versions or {
            robot_name: 1 for robot_name in expected
        })
        if set(declarations) != expected or set(versions) != expected:
            raise ValueError(
                "benchmark lifecycle declarations and versions must exactly "
                "match configured controller ids")
        if active - expected:
            raise ValueError("benchmark active agents must be configured controllers")
        if any(not isinstance(value, dict) or not value for value in declarations.values()):
            raise ValueError("benchmark capability declarations must be nonempty objects")
        if any(type(value) is not int or value < 1 for value in versions.values()):
            raise ValueError("benchmark capability versions must be positive integers")
        missing = expected - set(self.scene.articulations)
        if missing:
            raise ValueError(
                "benchmark lifecycle is missing articulations: " + ", ".join(sorted(missing)))

        self._benchmark_capability_declarations = deepcopy(declarations)
        self._benchmark_capability_versions = versions
        for robot_name in sorted(expected):
            robot = self.scene.articulations[robot_name]
            self._benchmark_park_poses[robot_name] = robot.data.root_state_w[:, :7].clone()
            self.set_benchmark_agent_active(robot_name, robot_name in active)

    def set_benchmark_agent_active(self, robot_name: str, active: bool) -> None:
        """Enable or park one already-loaded benchmark articulation."""
        if robot_name not in self._controller_configs:
            raise KeyError(f"unknown benchmark controller '{robot_name}'")
        if type(active) is not bool:
            raise TypeError("benchmark active state must be boolean")
        if robot_name not in self.scene.articulations:
            raise ValueError(f"missing benchmark articulation '{robot_name}'")
        if not active and robot_name not in self._benchmark_park_poses:
            robot = self.scene.articulations[robot_name]
            self._benchmark_park_poses[robot_name] = robot.data.root_state_w[:, :7].clone()
        self._benchmark_agent_active[robot_name] = active
        self.actions_dict.pop(robot_name, None)
        self._last_computed_actions.pop(robot_name, None)
        if not active:
            self._hold_benchmark_agent(robot_name)

    def reconfigure_benchmark_controller(
        self,
        robot_name: str,
        capability_declaration: Dict[str, Any],
        capability_version: int,
    ) -> None:
        """Replace the command-admission declaration for one controller.

        ``navigation_interface`` is the executable part of the declaration:
        false pins the robot and suppresses controller/auxiliary output. The
        physical attachment, payload and sensor claims cannot change here.
        The declaration and monotonically increasing version are retained for
        exact readback by the benchmark transaction boundary.
        """
        if robot_name not in self._controller_configs:
            raise KeyError(f"unknown benchmark controller '{robot_name}'")
        if not isinstance(capability_declaration, dict) or not capability_declaration:
            raise ValueError("capability declaration must be a nonempty object")
        if type(capability_declaration.get("navigation_interface")) is not bool:
            raise ValueError(
                "capability declaration requires boolean navigation_interface")
        current_declaration = self._benchmark_capability_declarations.get(robot_name)
        if current_declaration is None:
            raise ValueError("benchmark controller must be configured before reconfiguration")
        unchanged = dict(current_declaration)
        unchanged["navigation_interface"] = capability_declaration["navigation_interface"]
        if capability_declaration != unchanged:
            raise ValueError(
                "benchmark controller only supports navigation_interface changes; "
                "physical attachments, sensors and payload claims require a scene adapter")
        current = self._benchmark_capability_versions.get(robot_name, 0)
        if type(capability_version) is not int or capability_version <= current:
            raise ValueError("capability version must increase monotonically")
        self._benchmark_capability_declarations[robot_name] = deepcopy(
            capability_declaration)
        self._benchmark_capability_versions[robot_name] = capability_version
        self.actions_dict.pop(robot_name, None)
        self._last_computed_actions.pop(robot_name, None)
        if not capability_declaration["navigation_interface"]:
            self._benchmark_park_poses[robot_name] = (
                self.scene.articulations[robot_name].data.root_state_w[:, :7].clone())
            self._hold_benchmark_agent(robot_name)

    def read_benchmark_agent_state(self, robot_name: str) -> Dict[str, Any]:
        """Return physical inventory and operational activation readback."""
        robot = self.scene.articulations.get(robot_name)
        prim_paths = tuple(robot.root_physx_view.prim_paths) if robot is not None else ()
        return {
            "present": robot is not None and len(prim_paths) == 1,
            "articulation": robot is not None,
            "active": self._benchmark_agent_active.get(robot_name, False),
            "prim_path": prim_paths[0] if len(prim_paths) == 1 else None,
        }

    def read_benchmark_controller_state(self, robot_name: str) -> Dict[str, Any]:
        """Return the controller guard state used by the action hot path."""
        controller = self._controller_configs.get(robot_name)
        return {
            "configured": (
                controller is not None
                and robot_name in self._controllers_dict
                and robot_name in self._benchmark_capability_declarations
            ),
            "capability_version": self._benchmark_capability_versions.get(robot_name),
            "capability_declaration": deepcopy(
                self._benchmark_capability_declarations.get(robot_name)),
            "controller_id": (
                f"{robot_name}:{type(controller).__name__}" if controller is not None else None),
        }

    def _benchmark_control_enabled(self, robot_name: str) -> bool:
        declaration = self._benchmark_capability_declarations.get(robot_name)
        return (self._benchmark_agent_active.get(robot_name, True)
                and (declaration is None or declaration["navigation_interface"] is True))

    def _hold_benchmark_agent(self, robot_name: str) -> None:
        robot = self.scene.articulations[robot_name]
        pose = self._benchmark_park_poses.get(robot_name)
        if pose is None:
            pose = robot.data.root_state_w[:, :7].clone()
            self._benchmark_park_poses[robot_name] = pose
        env_ids = torch.arange(self.num_envs, device=self.device, dtype=torch.long)
        velocity = torch.zeros((self.num_envs, 6), device=self.device)
        robot.write_root_pose_to_sim(pose, env_ids=env_ids)
        robot.write_root_velocity_to_sim(velocity, env_ids=env_ids)
        joint_velocity = torch.zeros_like(robot.data.joint_vel)
        robot.set_joint_velocity_target(joint_velocity, env_ids=env_ids)
    
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
        
        aerial_sensor_manager = getattr(self, "_aerial_sensor_manager", None)
        if aerial_sensor_manager is not None:
            aerial_sensor_manager.update(self.step_dt)
        realsense_imu_manager = getattr(self, "_realsense_imu_manager", None)
        if realsense_imu_manager is not None:
            realsense_imu_manager.update(self.step_dt)
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
        
        precomputed_actions = self._replay_precomputed_actions
        if precomputed_actions is not None:
            admitted = {
                robot_name: action
                for robot_name, action in precomputed_actions.items()
                if self._benchmark_control_enabled(robot_name)
            }
            self.actions_dict = admitted
            self._last_computed_actions = dict(admitted)
            self._replay_precomputed_actions = None
            self._replay_precomputed_used = True
            return

        # Store previous actions_dict for last_action computation in observations
        # This will be used by RL controllers when computing observations
        previous_actions_dict = self.actions_dict.copy() if self.actions_dict else {}
        
        # Let controllers convert commands to actions
        computed_actions = {}
        
        for robot_name in self.cfg.possible_agents:
            if robot_name not in actions:
                continue
            if not self._benchmark_control_enabled(robot_name):
                self._hold_benchmark_agent(robot_name)
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
        self._last_computed_actions = dict(computed_actions)
        self._replay_precomputed_used = False
    
    def _apply_action(self) -> None:
        """Apply actions to all robots.
        
        All actions are applied by controllers using their apply_action methods.
        Controllers are responsible for applying actions appropriately for their robot type.
        
        Also updates action space dimensions if they were placeholders.
        """
        
        for robot_name in self.cfg.possible_agents:
            if not self._benchmark_control_enabled(robot_name):
                self._hold_benchmark_agent(robot_name)
                continue
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
            if not self._benchmark_control_enabled(robot_name):
                continue
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

        for robot_name, controller_cfg in self._controller_configs.items():
            reset = getattr(controller_cfg, "reset", None)
            controller_dict = self._controllers_dict.get(robot_name)
            if callable(reset) and controller_dict is not None:
                reset(self, robot_name, controller_dict, env_ids)

        self.actions_dict = {}
        for robot_name in self._controller_configs:
            if not self._benchmark_control_enabled(robot_name):
                robot = self.scene.articulations[robot_name]
                self._benchmark_park_poses[robot_name] = (
                    robot.data.root_state_w[:, :7].clone())
                self._hold_benchmark_agent(robot_name)
        aerial_sensor_manager = getattr(self, "_aerial_sensor_manager", None)
        if aerial_sensor_manager is not None:
            aerial_sensor_manager.reset(env_ids)
        realsense_imu_manager = getattr(self, "_realsense_imu_manager", None)
        if realsense_imu_manager is not None:
            realsense_imu_manager.reset(env_ids)
        # Controllers can override this if they need to reset their state
        hook = getattr(self.cfg, "after_reset_idx_hook", None)
        if hook is not None and callable(hook):
            hook(self, env_ids)
