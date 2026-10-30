"""
Base skill class for Isaac Lab robots.

Skills are higher-level abstractions that execute sequences of low-level
commands to achieve specific goals (navigation, manipulation, etc.).

对应 EMOS 中的 HierarchicalPolicy 中的技能选择机制。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
import torch
import numpy as np


class SkillStatus(Enum):
    """Status of a skill execution."""
    IDLE = "idle"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class SkillResult:
    """Result of a skill execution step."""
    status: SkillStatus
    command: Optional[torch.Tensor] = None  # Robot command to execute
    message: str = ""
    progress: float = 0.0  # 0.0 to 1.0
    data: Dict[str, Any] = field(default_factory=dict)


class BaseSkill(ABC):
    """
    Base class for robot skills.
    
    A skill represents a high-level behavior that may take multiple
    simulation steps to complete. Skills convert high-level goals
    (e.g., "navigate to object X") into sequences of low-level
    robot commands (e.g., velocity commands).
    
    Attributes:
        name: Name of the skill
        env: Reference to the environment
        robot_name: Name of the robot executing this skill
        status: Current status of the skill
    """
    
    def __init__(
        self,
        name: str,
        env: Any,
        robot_name: str,
        **kwargs
    ):
        self.name = name
        self.env = env
        self.robot_name = robot_name
        self.status = SkillStatus.IDLE
        self._target = None
        self._start_time = None
        self._step_count = 0
        self._max_steps = kwargs.get("max_steps", 1000)
    
    @abstractmethod
    def reset(self, target: Any, **kwargs) -> None:
        """
        Reset the skill with a new target.
        
        Args:
            target: The target for this skill (e.g., position, object name)
            **kwargs: Additional skill-specific parameters
        """
        self._target = target
        self.status = SkillStatus.RUNNING
        self._step_count = 0
    
    @abstractmethod
    def step(self) -> SkillResult:
        """
        Execute one step of the skill.
        
        Returns:
            SkillResult containing the command and status
        """
        pass
    
    @abstractmethod
    def is_done(self) -> bool:
        """Check if the skill has completed (success or failure)."""
        pass
    
    def cancel(self) -> None:
        """Cancel the current skill execution."""
        self.status = SkillStatus.CANCELLED
        self._target = None
    
    def get_robot(self):
        """Get the robot articulation from the environment."""
        if hasattr(self.env, 'scene') and hasattr(self.env.scene, 'articulations'):
            return self.env.scene.articulations.get(self.robot_name)
        return None
    
    def get_robot_position(self) -> Optional[np.ndarray]:
        """Get current robot position."""
        robot = self.get_robot()
        if robot is not None and hasattr(robot, 'data'):
            pos = robot.data.root_pos_w
            if pos is not None and len(pos) > 0:
                return pos[0].cpu().numpy()
        return None
    
    def get_robot_orientation(self) -> Optional[np.ndarray]:
        """Get current robot orientation as quaternion [w, x, y, z]."""
        robot = self.get_robot()
        if robot is not None and hasattr(robot, 'data'):
            quat = robot.data.root_quat_w
            if quat is not None and len(quat) > 0:
                return quat[0].cpu().numpy()
        return None
    
    def get_robot_velocity(self) -> Optional[Tuple[np.ndarray, np.ndarray]]:
        """Get current robot linear and angular velocity."""
        robot = self.get_robot()
        if robot is not None and hasattr(robot, 'data'):
            lin_vel = robot.data.root_lin_vel_w
            ang_vel = robot.data.root_ang_vel_w
            if lin_vel is not None and ang_vel is not None:
                return (
                    lin_vel[0].cpu().numpy(),
                    ang_vel[0].cpu().numpy()
                )
        return None
    
    def _check_timeout(self) -> bool:
        """Check if skill has exceeded maximum steps."""
        self._step_count += 1
        if self._step_count >= self._max_steps:
            self.status = SkillStatus.FAILED
            return True
        return False


class SkillManager:
    """
    Manages skill execution for a robot.
    
    Handles skill selection, execution, and transitions.
    """
    
    def __init__(self, env: Any, robot_name: str):
        self.env = env
        self.robot_name = robot_name
        self.skills: Dict[str, BaseSkill] = {}
        self.current_skill: Optional[BaseSkill] = None
    
    def register_skill(self, skill: BaseSkill) -> None:
        """Register a skill with the manager."""
        self.skills[skill.name] = skill
    
    def start_skill(self, skill_name: str, target: Any, **kwargs) -> bool:
        """
        Start executing a skill.
        
        Args:
            skill_name: Name of the skill to execute
            target: Target for the skill
            **kwargs: Additional parameters
            
        Returns:
            True if skill was started successfully
        """
        if skill_name not in self.skills:
            print(f"Warning: Skill '{skill_name}' not registered")
            return False
        
        # Cancel current skill if any
        if self.current_skill is not None:
            self.current_skill.cancel()
        
        self.current_skill = self.skills[skill_name]
        self.current_skill.reset(target, **kwargs)
        return True
    
    def step(self) -> Optional[SkillResult]:
        """
        Execute one step of the current skill.
        
        Returns:
            SkillResult if a skill is running, None otherwise
        """
        if self.current_skill is None:
            return None
        
        result = self.current_skill.step()
        
        # Clear skill if done
        if self.current_skill.is_done():
            self.current_skill = None
        
        return result
    
    def is_busy(self) -> bool:
        """Check if a skill is currently executing."""
        return self.current_skill is not None
    
    def cancel_current(self) -> None:
        """Cancel the current skill."""
        if self.current_skill is not None:
            self.current_skill.cancel()
            self.current_skill = None
