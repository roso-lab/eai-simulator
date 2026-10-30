"""
Multi-LLM Policy for Isaac Lab.

This module provides a multi-agent LLM policy that coordinates
multiple robots through group discussion and individual action execution.

对应 EMOS 中的:
- habitat_baselines/rl/multi_agent/multi_llm_policy.py
- habitat_baselines/rl/hrl/hl/llm_policy.py
"""

import os
import json
import datetime
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass
import torch

# 支持作为包导入或直接运行
try:
    from .MultiLLM_discussion import (
        group_discussion,
        AgentArguments,
        get_full_capabilities,
        get_text_capabilities,
        ROBOT_DESCRIPTION,
    )
    from .llm_agent import IsaacLLMAgent
    from .robot_resume import (
        generate_all_robot_resumes,
        generate_robot_resume,
        infer_robot_type,
    )
    from .actions.robot_actions import ACTION_POOL
except ImportError:
    from MultiLLM_discussion import (
        group_discussion,
        AgentArguments,
        get_full_capabilities,
        get_text_capabilities,
        ROBOT_DESCRIPTION,
    )
    from llm_agent import IsaacLLMAgent
    from robot_resume import (
        generate_all_robot_resumes,
        generate_robot_resume,
        infer_robot_type,
    )
    from actions.robot_actions import ACTION_POOL

# Try to import scene graph
try:
    from EAI.hmrs_scene import SceneGraphIsaac
except ImportError:
    SceneGraphIsaac = None


# Ablation modes for experiments
ABLATION_MODE = {
    (True, True, True, True): "FULL",
    (False, True, True, True): "NO_GROUP_DISCUSSION",
    (True, False, True, True): "NO_AGENT_REFLECTION",
    (True, True, False, True): "NO_ROBOT_RESUME",
    (True, True, True, False): "NO_NUMERICAL",
}


@dataclass
class PolicyConfig:
    """Configuration for MultiLLMPolicy."""
    
    # Group discussion settings
    should_group_discussion: bool = True
    should_agent_reflection: bool = True
    should_robot_resume: bool = True
    should_numerical: bool = True
    max_discussion_rounds: int = 3
    
    # Scene graph settings
    use_scene_graph: bool = True
    meters_per_grid: float = 0.05
    
    # Logging settings
    save_chat_history: bool = True
    save_chat_history_dir: str = "logs/multi_llm"
    
    # Termination settings
    should_terminate_on_wait: bool = False


class MultiLLMPolicy:
    """
    Multi-LLM policy for coordinating multiple robots in Isaac Lab.
    
    This policy implements a two-stage approach:
    1. Group Discussion: Leader LLM coordinates with robot LLMs to 
       decompose tasks and assign subtasks
    2. Action Execution: Each robot's LLM agent executes its assigned
       subtask through function calls
    
    Corresponds to EMOS MultiLLMPolicy.
    """
    
    def __init__(
        self,
        env: Any,
        config: Optional[PolicyConfig] = None,
        **kwargs
    ):
        """
        Initialize the multi-LLM policy.
        
        Args:
            env: Isaac Lab environment instance
            config: Policy configuration
            **kwargs: Additional configuration options
        """
        self.env = env
        self.config = config or PolicyConfig()
        
        # Apply kwargs to config
        for key, value in kwargs.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
        
        # Get ablation mode
        self.ablation_mode = ABLATION_MODE.get(
            (
                self.config.should_group_discussion,
                self.config.should_agent_reflection,
                self.config.should_robot_resume,
                self.config.should_numerical,
            ),
            "CUSTOM"
        )
        
        # Scene graph
        self.scene_graph = None
        if self.config.use_scene_graph and SceneGraphIsaac is not None:
            self.scene_graph = SceneGraphIsaac(
                meters_per_grid=self.config.meters_per_grid
            )
        
        # Agent management
        self.agents: Dict[str, IsaacLLMAgent] = {}
        self.task_assignments: Dict[str, AgentArguments] = {}
        
        # State
        self.initialized = False
        self.episode_id = 0
        self.step_count = 0
        
        # Get robot names from environment
        self.robot_names = self._get_robot_names()
    
    def _get_robot_names(self) -> List[str]:
        """Get list of robot names from environment."""
        if hasattr(self.env, 'cfg') and hasattr(self.env.cfg, 'possible_agents'):
            return list(self.env.cfg.possible_agents)
        elif hasattr(self.env, 'scene') and hasattr(self.env.scene, 'articulations'):
            return list(self.env.scene.articulations.keys())
        return []
    
    def _get_save_dir(self) -> str:
        """Get directory for saving chat history."""
        date_str = datetime.datetime.now().strftime("%Y-%m-%d")
        save_dir = os.path.join(
            self.config.save_chat_history_dir,
            date_str,
            self.ablation_mode,
            str(self.episode_id)
        )
        os.makedirs(save_dir, exist_ok=True)
        return save_dir
    
    def _load_scene_graph(self) -> None:
        """Load scene graph from environment."""
        if self.scene_graph is None:
            return
        
        try:
            if hasattr(self.env, 'scene'):
                self.scene_graph.load_gt_scene_graph(self.env.scene)
            else:
                self.scene_graph.load_gt_scene_graph(self.env)
        except Exception as e:
            print(f"Warning: Could not load scene graph: {e}")
    
    def _get_scene_description(self) -> str:
        """Get scene description for LLM prompts."""
        if self.scene_graph is not None:
            try:
                # Get object descriptions
                objects_desc = []
                if hasattr(self.scene_graph, 'object_layer'):
                    for obj in self.scene_graph.object_layer.objects[:20]:  # Limit
                        objects_desc.append(
                            f"- {obj.label or obj.class_name} at position {obj.center}"
                        )
                
                # Get region descriptions
                regions_desc = []
                if hasattr(self.scene_graph, 'region_layer'):
                    for region in self.scene_graph.region_layer.regions[:10]:
                        regions_desc.append(
                            f"- {region.label or region.class_name}: {region.get_center()}"
                        )
                
                description = "Scene objects:\n" + "\n".join(objects_desc[:20])
                if regions_desc:
                    description += "\n\nScene regions:\n" + "\n".join(regions_desc)
                
                return description
            except Exception as e:
                print(f"Warning: Could not get scene description: {e}")
        
        # Fallback: basic description from environment
        return self._get_basic_scene_description()
    
    def _get_basic_scene_description(self) -> str:
        """Get basic scene description without scene graph."""
        description = "Scene with the following robots:\n"
        
        for robot_name in self.robot_names:
            robot = None
            if hasattr(self.env, 'scene') and hasattr(self.env.scene, 'articulations'):
                robot = self.env.scene.articulations.get(robot_name)
            
            if robot is not None and hasattr(robot, 'data'):
                try:
                    pos = robot.data.root_pos_w[0].cpu().numpy()
                    description += f"- {robot_name} at position [{pos[0]:.2f}, {pos[1]:.2f}, {pos[2]:.2f}]\n"
                except:
                    description += f"- {robot_name}\n"
            else:
                description += f"- {robot_name}\n"
        
        # Add some navigation target points for LLM reference
        description += "\nAvailable navigation target points:\n"
        description += "- Point A: [3.0, 0.0, 0.0]\n"
        description += "- Point B: [-3.0, 0.0, 0.0]\n"
        description += "- Point C: [0.0, 3.0, 0.0]\n"
        description += "- Point D: [0.0, -3.0, 0.0]\n"
        description += "- Point E: [2.0, 2.0, 0.0]\n"
        description += "- Point F: [-2.0, -2.0, 0.0]\n"
        description += "\nUse nav_to_position(target_x, target_y, target_z) to navigate to a specific position."
        
        return description
    
    def _generate_robot_resumes(self) -> Dict[str, Dict[str, Any]]:
        """Generate robot resumes for all agents."""
        resumes = {}
        
        for robot_name in self.robot_names:
            resume = generate_robot_resume(
                self.env,
                robot_name,
                include_numerical=self.config.should_numerical
            )
            resumes[robot_name] = resume
        
        return resumes
    
    def initialize(
        self,
        task_description: str,
        episode_id: int = 0,
    ) -> Dict[str, AgentArguments]:
        """
        Initialize the policy with a task.
        
        Performs group discussion to assign subtasks to agents.
        
        Args:
            task_description: Overall task description
            episode_id: Episode ID for logging
            
        Returns:
            Dictionary mapping robot names to their task assignments
        """
        self.episode_id = episode_id
        self.step_count = 0
        self.initialized = False
        
        # Clear previous state
        self.agents.clear()
        self.task_assignments.clear()
        IsaacLLMAgent.clear_message_pipe()
        
        # Load scene graph
        self._load_scene_graph()
        
        # Generate robot resumes
        robot_resumes = self._generate_robot_resumes()
        
        # Get scene description
        scene_description = self._get_scene_description()
        
        # Get save directory
        save_dir = self._get_save_dir() if self.config.save_chat_history else ""
        
        print("=" * 60)
        print(f"Initializing MultiLLMPolicy - Episode {episode_id}")
        print(f"Ablation Mode: {self.ablation_mode}")
        print(f"Robots: {self.robot_names}")
        print("=" * 60)
        
        # Run group discussion
        self.task_assignments = group_discussion(
            robot_resume=json.dumps(robot_resumes),
            scene_description=scene_description,
            task_description=task_description,
            save_chat_history=self.config.save_chat_history,
            save_chat_history_dir=save_dir,
            episode_id=episode_id,
            should_group_discussion=self.config.should_group_discussion,
            should_agent_reflection=self.config.should_agent_reflection,
            should_robot_resume=self.config.should_robot_resume,
            should_numerical=self.config.should_numerical,
            max_discussion_rounds=self.config.max_discussion_rounds,
        )
        
        # Initialize LLM agents
        for robot_name in self.robot_names:
            if robot_name not in self.task_assignments:
                continue
            
            args = self.task_assignments[robot_name]
            
            # Create agent
            agent = IsaacLLMAgent(
                name=robot_name,
                env=self.env,
                actions=ACTION_POOL,
                scene_graph=self.scene_graph,
            )
            
            # Initialize with task context
            logging_file = ""
            if self.config.save_chat_history:
                logging_file = os.path.join(save_dir, f"{robot_name}_action_history.json")
            
            agent.init_agent(
                robot_type=args.robot_type,
                task_description=args.task_description,
                subtask_description=args.subtask_description,
                chat_history=args.chat_history,
                enable_logging=self.config.save_chat_history,
                logging_file=logging_file,
            )
            
            self.agents[robot_name] = agent
        
        self.initialized = True
        return self.task_assignments
    
    def step(
        self,
        observations: Optional[Dict[str, Any]] = None
    ) -> Dict[str, torch.Tensor]:
        """
        Execute one policy step for all agents.
        
        Args:
            observations: Optional observations dictionary
            
        Returns:
            Dictionary mapping robot names to command tensors
        """
        if not self.initialized:
            raise RuntimeError("Policy not initialized. Call initialize() first.")
        
        self.step_count += 1
        
        # Get current scene description
        scene_description = self._get_scene_description()
        
        # Get actions from all agents
        commands = {}
        all_waiting = True
        
        for robot_name, agent in self.agents.items():
            try:
                command = agent.step(scene_description)
                
                if command is not None:
                    commands[robot_name] = command
                    # Check if not just waiting
                    if torch.any(command != 0):
                        all_waiting = False
                else:
                    # Default zero command
                    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
                    commands[robot_name] = torch.zeros(1, 3, device=device)
                    
            except Exception as e:
                print(f"Error getting action for {robot_name}: {e}")
                device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
                commands[robot_name] = torch.zeros(1, 3, device=device)
        
        # Check termination condition
        if self.config.should_terminate_on_wait and all_waiting:
            print("=" * 40)
            print("All agents are waiting - episode complete")
            print("=" * 40)
        
        return commands
    
    def reset(self) -> None:
        """Reset the policy for a new episode."""
        for agent in self.agents.values():
            agent.reset()
        
        self.agents.clear()
        self.task_assignments.clear()
        self.initialized = False
        self.step_count = 0
        
        IsaacLLMAgent.clear_message_pipe()
    
    def get_token_usage(self) -> Dict[str, int]:
        """Get token usage for all agents."""
        usage = {}
        for robot_name, agent in self.agents.items():
            usage[robot_name] = agent.get_token_usage()
        usage["total"] = sum(usage.values())
        return usage


class LLMMultiRobotEnvWrapper:
    """
    Wrapper that adds LLM policy support to MultiRobotDirectEnv.
    
    This wrapper integrates the MultiLLMPolicy with the environment,
    allowing for automatic LLM-driven control.
    """
    
    def __init__(
        self,
        env: Any,
        task_description: str = "",
        policy_config: Optional[PolicyConfig] = None,
    ):
        """
        Initialize the wrapper.
        
        Args:
            env: Isaac Lab MultiRobotDirectEnv instance
            task_description: Task description for LLM
            policy_config: Policy configuration
        """
        self.env = env
        self.task_description = task_description
        
        # Create policy
        self.policy = MultiLLMPolicy(env, policy_config)
        
        # Episode counter
        self.episode_count = 0
    
    def reset(self, task_description: Optional[str] = None) -> Any:
        """Reset environment and initialize policy."""
        # Reset environment
        obs = self.env.reset()
        
        # Reset policy
        self.policy.reset()
        
        # Use new task description if provided
        if task_description is not None:
            self.task_description = task_description
        
        # Initialize policy with task
        if self.task_description:
            self.policy.initialize(
                self.task_description,
                episode_id=self.episode_count
            )
        
        self.episode_count += 1
        return obs
    
    def step(self, external_actions: Optional[Dict[str, torch.Tensor]] = None):
        """
        Step environment with LLM policy or external actions.
        
        Args:
            external_actions: Optional external actions (overrides LLM)
            
        Returns:
            Environment step result (obs, reward, done, info)
        """
        if external_actions is not None:
            # Use external actions
            actions = external_actions
        elif self.policy.initialized:
            # Use LLM policy
            actions = self.policy.step()
        else:
            # No actions
            actions = {}
        
        return self.env.step(actions)
    
    def step_with_llm(self):
        """Step using only LLM policy."""
        actions = self.policy.step()
        return self.env.step(actions)
    
    def close(self):
        """Close environment."""
        self.policy.reset()
        if hasattr(self.env, 'close'):
            self.env.close()
