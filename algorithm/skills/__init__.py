"""Skill-level planning contracts and provider classifications.

Skill providers turn one already-assigned task into a path, trajectory, ROS
goal, or controller-level command. They do not assign tasks and do not claim
that the robot physically completed the task.
"""

from .contracts import SkillCommand, SkillKind, SkillPlan, SkillRequest
from .registry import SKILL_REGISTRY, skill_records

__all__ = [
    "SKILL_REGISTRY",
    "SkillCommand",
    "SkillKind",
    "SkillPlan",
    "SkillRequest",
    "skill_records",
]
