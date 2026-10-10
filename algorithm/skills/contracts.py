"""Dependency-light contracts for EAI execution skills.

These records mirror the HeteroCoord ``simulator.skill.v1`` boundary.  A
provider compiles an already assigned request into controller-facing commands;
it does not import Isaac Sim, Torch, or ROS and it cannot certify a physical
outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Mapping, Protocol


class SkillContractError(ValueError):
    """Raised when a skill request or plan is malformed."""


class SkillKind(str, Enum):
    NAVIGATION = "navigation"
    MANIPULATION = "manipulation"
    INSPECTION = "inspection"
    TRANSPORT = "transport"
    COORDINATION = "coordination"
    EXTERNAL_IO = "external_io"


def _finite(value: object, name: str) -> float:
    if isinstance(value, bool):
        raise SkillContractError(f"{name} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise SkillContractError(f"{name} must be numeric") from exc
    if not math.isfinite(result):
        raise SkillContractError(f"{name} must be finite")
    return result


@dataclass(frozen=True)
class SkillRequest:
    """One low-level skill request produced after high-level assignment."""

    request_id: str
    skill_id: str
    robot_id: str
    target_ref: str | None = None
    target_pose: tuple[float, ...] | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("request_id", "skill_id", "robot_id"):
            value = str(getattr(self, name)).strip()
            if not value:
                raise SkillContractError(f"{name} is required")
            object.__setattr__(self, name, value)
        if self.target_pose is not None:
            pose = tuple(_finite(item, "target_pose") for item in self.target_pose)
            if len(pose) not in (2, 3, 6, 7):
                raise SkillContractError("target_pose must contain 2, 3, 6, or 7 values")
            object.__setattr__(self, "target_pose", pose)
        object.__setattr__(self, "parameters", dict(self.parameters))


@dataclass(frozen=True)
class SkillCommand:
    """A command that an EAI executor can translate to controller input."""

    command_type: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        value = str(self.command_type).strip()
        if not value:
            raise SkillContractError("command_type is required")
        object.__setattr__(self, "command_type", value)
        object.__setattr__(self, "payload", dict(self.payload))


@dataclass(frozen=True)
class SkillPlan:
    """Provider output; ``planned`` is never a physical-success assertion."""

    request_id: str
    skill_id: str
    robot_id: str
    commands: tuple[SkillCommand, ...] = ()
    status: str = "planned"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("request_id", "skill_id", "robot_id", "status"):
            value = str(getattr(self, name)).strip()
            if not value:
                raise SkillContractError(f"{name} is required")
            object.__setattr__(self, name, value)
        object.__setattr__(self, "commands", tuple(self.commands))
        if any(not isinstance(item, SkillCommand) for item in self.commands):
            raise SkillContractError("commands must contain SkillCommand records")
        object.__setattr__(self, "metadata", dict(self.metadata))


class SkillProvider(Protocol):
    skill_id: str
    kind: SkillKind

    def plan(
        self,
        request: SkillRequest,
        *,
        observation: Mapping[str, Any] | None = None,
    ) -> SkillPlan:
        """Compile a plan without stepping the simulator."""


__all__ = [
    "SkillCommand",
    "SkillContractError",
    "SkillKind",
    "SkillPlan",
    "SkillProvider",
    "SkillRequest",
]
