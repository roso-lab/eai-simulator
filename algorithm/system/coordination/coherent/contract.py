"""Small, serialisable contracts between COHERENT and EAI.

The original COHERENT implementation consumes OmniGibson graph state and a
fixed action table.  These records are the replacement boundary for EAI.  No
Isaac, ROS, Torch, or LLM package is imported here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Tuple


class ContractError(ValueError):
    """Raised for malformed or unsafe task/skill records."""


@dataclass(frozen=True)
class CapabilityProfile:
    robot_id: str
    capabilities: frozenset[str]
    active: bool = True

    def __post_init__(self) -> None:
        if not self.robot_id:
            raise ContractError("robot_id cannot be empty")
        if any(not isinstance(item, str) or not item for item in self.capabilities):
            raise ContractError("capabilities must contain non-empty strings")


@dataclass(frozen=True)
class SkillSpec:
    name: str
    required_capabilities: frozenset[str] = frozenset()
    description: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise ContractError("skill name cannot be empty")


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    description: str
    required_capabilities: frozenset[str]
    dependencies: Tuple[str, ...] = ()
    preferred_skills: Tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.task_id or not self.description:
            raise ContractError("task_id and description are required")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ContractError(f"duplicate dependencies for {self.task_id}")
        if self.task_id in self.dependencies:
            raise ContractError(f"task cannot depend on itself: {self.task_id}")


@dataclass(frozen=True)
class PlanAssignment:
    task_id: str
    robot_id: str


@dataclass(frozen=True)
class Proposal:
    assignments: Tuple[PlanAssignment, ...]
    rationale: str = ""
    source: str = "deterministic_fallback"

    def by_task(self) -> dict[str, str]:
        return {item.task_id: item.robot_id for item in self.assignments}


@dataclass(frozen=True)
class ActionCommand:
    task_id: str
    robot_id: str
    skill: str
    arguments: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Feedback:
    task_id: str
    robot_id: str
    success: bool
    message: str = ""
    capability_lost: bool = False
    terminal: bool = False
