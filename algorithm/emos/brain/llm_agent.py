"""
LLM Agent for Isaac Lab action execution.

This module provides an LLM-based agent that can execute actions
in the Isaac Lab environment based on natural language commands.

对应 EMOS 中的:
- habitat_mas/agents/crab_agent.py
- habitat_baselines/rl/hrl/hl/llm_policy.py
"""

import json
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass

# 支持作为包导入或直接运行
try:
    from .API.Model_API import DeepSeekModel
    from .API.crab_core import Action
    from .actions.robot_actions import ACTION_POOL, get_action_descriptions
    from .skills.base_skill import SkillManager
    from .skills.navigation_skill import NavigationSkill
    from .skills.coordination_skill import WaitSkill, CoordinationSkill
except ImportError:
    from API.Model_API import DeepSeekModel
    from API.crab_core import Action
    from actions.robot_actions import ACTION_POOL, get_action_descriptions
    from skills.base_skill import SkillManager
    from skills.navigation_skill import NavigationSkill
    from skills.coordination_skill import WaitSkill, CoordinationSkill


# Request template for inter-agent communication
REQUEST_TEMPLATE = '"{source_agent}" agent sent you a request: "{request}".'

# System prompt template for action execution
ROBOT_EXECUTION_SYSTEM_PROMPT = """You are a "{robot_type}" agent called "{robot_name}".
You MUST complete the subtask assigned to you:
\"\"\"
{subtask_description}
\"\"\"

You have access to the following actions:
{action_descriptions}

You MUST take one and only one action using function call in each step.
If you think the task cannot be done by yourself, you can use `send_request` function to ask other agents for help.
Think carefully about the current state and choose the most appropriate action to make progress on your subtask.
"""

# Prompt for first action
START_ACTION_PROMPT = """You are starting the task.
Here is the current environment description:
\"\"\"
{scene_description}
\"\"\"

Based on the task and environment, generate the most appropriate first action.
Make sure the action strictly follows the tool call's parameter format.
"""

# Prompt for subsequent actions
STEP_ACTION_PROMPT = """You have completed your previous action.
Here is the current environment state:
\"\"\"
{scene_description}
\"\"\"

Based on the task progress, generate the most appropriate next action.
Make sure the action strictly follows the tool call's parameter format.
"""


@dataclass
class AgentArguments:
    """Arguments passed to an agent from group discussion."""
    robot_id: str
    robot_type: str
    task_description: str
    subtask_description: str
    chat_history: Optional[List[Dict[str, str]]] = None


class IsaacLLMAgent:
    """
    LLM-based agent for Isaac Lab action execution.
    
    This agent uses an LLM to decide which actions to take based on
    the current environment state and the assigned task.
    
    Corresponds to EMOS CrabAgent.
    """
    
    # Class-level message pipe for inter-agent communication
    message_pipe: Dict[str, List[str]] = {}
    
    def __init__(
        self,
        name: str,
        env: Any,
        actions: List[Action] = None,
        scene_graph: Any = None,
        **kwargs
    ):
        """
        Initialize the LLM agent.
        
        Args:
            name: Agent/robot name
            env: Isaac Lab environment instance
            actions: List of available actions (default: ACTION_POOL)
            scene_graph: Scene graph for environment understanding
            **kwargs: Additional configuration
        """
        self.name = name
        self.env = env
        self.actions = actions or ACTION_POOL
        self.scene_graph = scene_graph
        
        # LLM model (initialized later with task context)
        self.llm_model: Optional[DeepSeekModel] = None
        
        # State
        self.initialized = False
        self.start_act = False
        self.robot_type = ""
        self.task_description = ""
        self.subtask_description = ""
        
        # LLM call rate limiting (prevent rapid API calls)
        self._steps_since_last_llm_call = 0
        self._min_steps_between_llm_calls = 50  # Minimum steps before calling LLM again
        
        # Skill manager for executing actions
        self.skill_manager = SkillManager(env, name)
        self._init_skills()
        
        # Logging
        self.enable_logging = kwargs.get("enable_logging", False)
        self.logging_file = kwargs.get("logging_file", "")
        
        # Action prompt
        self.action_prompt = get_action_descriptions()
    
    def _init_skills(self, robot_type: str = None) -> None:
        """Initialize skill manager with available skills.
        
        Args:
            robot_type: Type of robot (e.g., "G1", "Go2", "Quadcopter").
                       If None, will be inferred from robot name.
        """
        # Clear existing skills if re-initializing
        self.skill_manager.skills.clear()
        
        # Navigation skill with robot type for specialized controller selection
        nav_skill = NavigationSkill(
            self.env, 
            self.name, 
            scene_graph=self.scene_graph,
            robot_type=robot_type
        )
        self.skill_manager.register_skill(nav_skill)
        
        # Wait skill
        wait_skill = WaitSkill(self.env, self.name)
        self.skill_manager.register_skill(wait_skill)
        
        # Coordination skill
        coord_skill = CoordinationSkill(self.env, self.name)
        self.skill_manager.register_skill(coord_skill)
    
    def _normalize_robot_type(self, robot_type: str) -> str:
        """Normalize robot type string to standard format.
        
        Args:
            robot_type: Robot type string (e.g., "G1Robot", "Go2_Navigation", etc.)
            
        Returns:
            Normalized type: "g1", "go2", "quadcopter", or original string
        """
        if robot_type is None:
            return None
        
        type_lower = robot_type.lower()
        
        if "g1" in type_lower or "humanoid" in type_lower:
            return "g1"
        elif "go2" in type_lower or "quadruped" in type_lower:
            return "go2"
        elif "quadcopter" in type_lower or "drone" in type_lower or "uav" in type_lower:
            return "quadcopter"
        
        return robot_type
    
    def init_agent(
        self,
        robot_type: str,
        task_description: str,
        subtask_description: str,
        chat_history: Optional[List[Dict]] = None,
        enable_logging: bool = False,
        logging_file: str = "",
    ) -> None:
        """
        Initialize the agent with task context.
        
        This should be called after group discussion to set up
        the agent with its assigned subtask.
        
        Args:
            robot_type: Type of robot (e.g., "G1Robot", "Quadcopter")
            task_description: Overall task description
            subtask_description: Subtask assigned to this agent
            chat_history: Chat history from group discussion
            enable_logging: Whether to enable logging
            logging_file: Path to logging file
        """
        self.robot_type = robot_type
        self.task_description = task_description
        self.subtask_description = subtask_description
        self.enable_logging = enable_logging
        self.logging_file = logging_file
        
        # Re-initialize skills with robot type for specialized controllers
        # This allows NavigationSkill to use G1/Go2/Quadcopter specific controllers
        self._init_skills(robot_type=self._normalize_robot_type(robot_type))
        
        # Create system prompt
        system_prompt = ROBOT_EXECUTION_SYSTEM_PROMPT.format(
            robot_type=robot_type,
            robot_name=self.name,
            subtask_description=subtask_description or task_description,
            action_descriptions=self.action_prompt,
        )
        
        # Initialize LLM model
        self.llm_model = DeepSeekModel(
            system_prompt=system_prompt,
            action_space=self.actions,
            discussion_stage=False,  # Action execution mode
            code_execution=False,
            enable_logging=enable_logging,
            logging_file=logging_file,
            agent_name=self.name,
        )
        
        # Inject chat history from group discussion
        # chat_history from group_discussion is already a list of turns (each turn is a list of messages)
        # So we don't need to wrap each element again
        if chat_history is not None:
            self.llm_model.chat_history = chat_history
        
        self.initialized = True
        self.start_act = False
        
        # Generate initial action plan
        if subtask_description:
            planning_prompt = (
                f"Your assigned subtask is:\n"
                f'"""\n{subtask_description}\n"""\n\n'
                f"You have access to these actions:\n{self.action_prompt}\n"
                "Plan the action sequence to complete your subtask."
            )
            print(f"==============={self.name} Planning==============")
            print(planning_prompt)
            response = self.llm_model.chat(planning_prompt, crab_planning=True)
            print(f"==============={self.name} Plan==============")
            print(response)
    
    def get_token_usage(self) -> int:
        """Get total token usage for this agent."""
        if self.llm_model is not None:
            return self.llm_model.token_usage
        return 0
    
    def get_next_action(
        self,
        scene_description: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get the next action from the LLM.
        
        Args:
            scene_description: Current scene/environment description
            
        Returns:
            Action dictionary with 'name' and 'arguments', or None
        """
        if not self.initialized or self.llm_model is None:
            raise RuntimeError(f"Agent {self.name} not initialized. Call init_agent first.")
        
        # Check for incoming messages from other agents
        observation = scene_description
        if self.name in IsaacLLMAgent.message_pipe and IsaacLLMAgent.message_pipe[self.name]:
            messages = " ".join(IsaacLLMAgent.message_pipe[self.name])
            observation = f"{scene_description}\n\nMessages received: {messages}"
            IsaacLLMAgent.message_pipe[self.name] = []
        
        # Create prompt based on whether this is first action
        if not self.start_act:
            prompt = START_ACTION_PROMPT.format(scene_description=observation)
            self.start_act = True
        else:
            prompt = STEP_ACTION_PROMPT.format(scene_description=observation)
        
        # Query LLM for next action
        try:
            result = self.llm_model.chat(prompt)
            
            print(f"==============={self.name} LLM Output==============")
            print(result)
            print(f"===============Token Usage: {self.get_token_usage()}==============")
            
            if result is None:
                return {"name": "wait", "arguments": {"duration_ms": 500}}
            
            # Parse action
            if isinstance(result, tuple):
                action_name, parameters = result
            else:
                # Text response - no action
                return {"name": "wait", "arguments": {"duration_ms": 500}}
            
            # Handle send_request specially
            if action_name == "send_request":
                target_agent = parameters.get("target_agent", "")
                if target_agent == self.name:
                    return None  # Can't send request to self
                
                request = parameters.get("request", "")
                if target_agent not in IsaacLLMAgent.message_pipe:
                    IsaacLLMAgent.message_pipe[target_agent] = []
                
                message = REQUEST_TEMPLATE.format(
                    source_agent=self.name,
                    request=request
                )
                IsaacLLMAgent.message_pipe[target_agent].append(message)
                return {"name": "wait", "arguments": {"duration_ms": 500}}
            
            # Add robot name to parameters if needed
            if "robot" in parameters or action_name in ["nav_to_obj", "nav_to_position", "pick", "place"]:
                parameters["robot"] = self.name
            
            return {"name": action_name, "arguments": parameters}
            
        except Exception as e:
            print(f"Error getting action from LLM: {e}")
            return {"name": "wait", "arguments": {"duration_ms": 500}}
    
    def execute_action(
        self,
        action: Dict[str, Any]
    ) -> Optional[Any]:
        """
        Execute an action and return the robot command.
        
        Args:
            action: Action dictionary with 'name' and 'arguments'
            
        Returns:
            Robot command tensor or None
        """
        if action is None:
            return None
        
        action_name = action.get("name", "wait")
        arguments = action.get("arguments", {})
        
        # Map action to skill
        if action_name == "nav_to_obj":
            target = arguments.get("target_obj", "")
            return self.skill_manager.start_skill("navigation", {"object": target})
        
        elif action_name == "nav_to_position":
            # Handle both formats: target_position list or separate target_x/y/z
            if "target_position" in arguments:
                target = arguments.get("target_position")
            else:
                # LLM outputs target_x, target_y, target_z separately
                target = [
                    arguments.get("target_x", 0.0),
                    arguments.get("target_y", 0.0),
                    arguments.get("target_z", 0.0),
                ]
            print(f"[{self.name}] Navigation target: {target}")
            return self.skill_manager.start_skill("navigation", target)
        
        elif action_name == "nav_to_robot":
            target = arguments.get("target_robot", "")
            return self.skill_manager.start_skill("navigation", {"robot": target})
        
        elif action_name == "wait":
            duration = arguments.get("duration_ms", 500)
            return self.skill_manager.start_skill("wait", duration)
        
        elif action_name == "send_request":
            return self.skill_manager.start_skill("coordination", arguments)
        
        # TODO: Implement pick, place, reset_arm skills
        elif action_name in ["pick", "place", "reset_arm"]:
            print(f"Warning: Action '{action_name}' not yet implemented")
            return self.skill_manager.start_skill("wait", 500)
        
        return None
    
    def step(self, scene_description: str) -> Optional[Any]:
        """
        Execute one step: get action from LLM and execute it.
        
        Args:
            scene_description: Current scene description
            
        Returns:
            Robot command tensor or None
        """
        self._steps_since_last_llm_call += 1
        
        # If skill is currently running, continue it
        if self.skill_manager.is_busy():
            result = self.skill_manager.step()
            if result is not None:
                return result.command
        
        # Rate limit LLM calls to prevent rapid API consumption
        if self._steps_since_last_llm_call < self._min_steps_between_llm_calls:
            # Not enough steps since last LLM call, continue waiting
            if not self.skill_manager.is_busy():
                self.skill_manager.start_skill("wait", 100)
            result = self.skill_manager.step()
            if result is not None:
                return result.command
            return None
        
        # Reset counter before LLM call
        self._steps_since_last_llm_call = 0
        
        # Get next action from LLM
        action = self.get_next_action(scene_description)
        
        # Execute action
        self.execute_action(action)
        
        # Step the skill manager
        result = self.skill_manager.step()
        if result is not None:
            return result.command
        
        return None
    
    def reset(self) -> None:
        """Reset agent state for new episode."""
        self.initialized = False
        self.start_act = False
        self.llm_model = None
        self.skill_manager.cancel_current()
    
    @classmethod
    def clear_message_pipe(cls) -> None:
        """Clear all pending messages."""
        cls.message_pipe.clear()
