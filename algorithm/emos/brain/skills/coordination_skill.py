"""
Coordination skills for multi-robot systems.

Handles waiting, synchronization, and inter-robot communication.
"""

import time
from typing import Any, Dict, Optional
import torch

from .base_skill import BaseSkill, SkillResult, SkillStatus


class WaitSkill(BaseSkill):
    """
    Skill that makes the robot wait for a specified duration.
    
    Useful for coordination and timing between robots.
    """
    
    def __init__(self, env: Any, robot_name: str, **kwargs):
        super().__init__("wait", env, robot_name, **kwargs)
        self.wait_steps = 0
        self.target_steps = 0
        self.dt = kwargs.get("dt", 0.01)  # Simulation timestep
    
    def reset(self, target: Any, **kwargs) -> None:
        """
        Reset wait skill.
        
        Args:
            target: Duration in milliseconds or dict with 'duration_ms'
        """
        super().reset(target, **kwargs)
        
        if isinstance(target, (int, float)):
            duration_ms = target
        elif isinstance(target, dict):
            duration_ms = target.get("duration_ms", 500)
        else:
            duration_ms = 500
        
        # Convert milliseconds to simulation steps
        duration_sec = duration_ms / 1000.0
        self.target_steps = int(duration_sec / self.dt)
        self.wait_steps = 0
    
    def step(self) -> SkillResult:
        """Execute one wait step."""
        self.wait_steps += 1
        
        progress = self.wait_steps / max(self.target_steps, 1)
        
        if self.wait_steps >= self.target_steps:
            self.status = SkillStatus.SUCCEEDED
            return SkillResult(
                status=SkillStatus.SUCCEEDED,
                command=self._create_zero_command(),
                message="Wait complete",
                progress=1.0
            )
        
        return SkillResult(
            status=SkillStatus.RUNNING,
            command=self._create_zero_command(),
            message=f"Waiting: {self.wait_steps}/{self.target_steps} steps",
            progress=progress
        )
    
    def _create_zero_command(self) -> torch.Tensor:
        """Create zero command to keep robot stationary."""
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        return torch.zeros(1, 3, device=device, dtype=torch.float32)
    
    def is_done(self) -> bool:
        return self.status in [
            SkillStatus.SUCCEEDED,
            SkillStatus.FAILED,
            SkillStatus.CANCELLED
        ]


class CoordinationSkill(BaseSkill):
    """
    Skill for coordinating with other robots via message passing.
    
    Manages the message pipe for inter-robot communication.
    """
    
    # Class-level message pipe shared between all agents
    message_pipe: Dict[str, list] = {}
    
    def __init__(self, env: Any, robot_name: str, **kwargs):
        super().__init__("coordination", env, robot_name, **kwargs)
        self.pending_request = None
        self.waiting_for_response = False
    
    def reset(self, target: Any, **kwargs) -> None:
        """
        Reset coordination skill.
        
        Args:
            target: dict with 'target_agent' and 'request'
        """
        super().reset(target, **kwargs)
        
        if isinstance(target, dict):
            self.pending_request = {
                "target_agent": target.get("target_agent", ""),
                "request": target.get("request", ""),
                "source_agent": self.robot_name,
            }
        else:
            self.status = SkillStatus.FAILED
    
    def step(self) -> SkillResult:
        """Execute coordination step - send message and complete."""
        if self.pending_request is None:
            self.status = SkillStatus.FAILED
            return SkillResult(
                status=SkillStatus.FAILED,
                message="No request to send"
            )
        
        # Send the message
        target_agent = self.pending_request["target_agent"]
        if target_agent not in CoordinationSkill.message_pipe:
            CoordinationSkill.message_pipe[target_agent] = []
        
        message = (
            f'"{self.robot_name}" agent sent you a request: '
            f'"{self.pending_request["request"]}"'
        )
        CoordinationSkill.message_pipe[target_agent].append(message)
        
        self.status = SkillStatus.SUCCEEDED
        return SkillResult(
            status=SkillStatus.SUCCEEDED,
            command=self._create_zero_command(),
            message=f"Request sent to {target_agent}",
            progress=1.0,
            data={"target": target_agent, "message": message}
        )
    
    def _create_zero_command(self) -> torch.Tensor:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        return torch.zeros(1, 3, device=device, dtype=torch.float32)
    
    def is_done(self) -> bool:
        return self.status in [
            SkillStatus.SUCCEEDED,
            SkillStatus.FAILED,
            SkillStatus.CANCELLED
        ]
    
    @classmethod
    def get_messages(cls, agent_name: str) -> list:
        """Get and clear pending messages for an agent."""
        messages = cls.message_pipe.get(agent_name, [])
        cls.message_pipe[agent_name] = []
        return messages
    
    @classmethod
    def has_messages(cls, agent_name: str) -> bool:
        """Check if an agent has pending messages."""
        return bool(cls.message_pipe.get(agent_name, []))
