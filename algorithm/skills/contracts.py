"""Dependency-light contracts for EAI execution skills.

These records mirror the HeteroCoord ``simulator.skill.v1`` boundary.  A
provider compiles an already assigned request into controller-facing commands;
it does not import Isaac Sim, Torch, or ROS and it cannot certify a physical
outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
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


def _json_safe(value: Any, name: str) -> Any:
    if value is None or isinstance(value, (str, bool, int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise SkillContractError(f"{name} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item, f"{name}.{key}") for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, name) for item in value]
    raise SkillContractError(f"{name} must be JSON-safe")


@dataclass(frozen=True)
class SkillRequest:
    """One low-level skill request produced after high-level assignment."""

    request_id: str
    skill_id: str
    robot_id: str
    target_ref: str | None = None
    target_pose: tuple[float, ...] | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    task_id: str | None = None
    skill_version: str = "simulator.skill.v1"
    simulator_snapshot: Mapping[str, Any] | None = None
    timing: Mapping[str, Any] | None = None
    budget: Mapping[str, Any] | None = None
    branch_context: Mapping[str, Any] | None = None

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
        if self.task_id is not None:
            if not isinstance(self.task_id, str) or not self.task_id.strip():
                raise SkillContractError("task_id must be a non-empty string")
            object.__setattr__(self, "task_id", self.task_id.strip())
        if not isinstance(self.skill_version, str) or not self.skill_version.strip():
            raise SkillContractError("skill_version must be a non-empty string")
        for name in ("simulator_snapshot", "timing", "budget", "branch_context"):
            value = getattr(self, name)
            if value is not None:
                if not isinstance(value, Mapping):
                    raise SkillContractError(f"{name} must be an object")
                object.__setattr__(self, name, _json_safe(value, name))

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SkillRequest":
        if not isinstance(value, Mapping):
            raise SkillContractError("SkillRequest must be an object")
        required = {"request_id", "skill_id", "robot_id", "parameters"}
        allowed = required | {
            "target_ref", "target_pose", "task_id", "skill_version",
            "simulator_snapshot", "timing", "budget", "branch_context",
        }
        if missing := sorted(required - set(value)):
            raise SkillContractError(f"SkillRequest missing fields: {missing}")
        if unknown := sorted(set(value) - allowed):
            raise SkillContractError(f"SkillRequest has unknown fields: {unknown}")
        for name in ("request_id", "skill_id", "robot_id", "task_id", "skill_version", "target_ref"):
            if name in {"request_id", "skill_id", "robot_id"} and value[name] is None:
                raise SkillContractError(f"{name} is required")
            if name in value and value[name] is not None:
                if not isinstance(value[name], str) or not value[name].strip():
                    raise SkillContractError(f"{name} must be a non-empty string")
        if not isinstance(value["parameters"], Mapping):
            raise SkillContractError("parameters must be an object")
        pose = value.get("target_pose")
        if pose is not None and not isinstance(pose, (list, tuple)):
            raise SkillContractError("target_pose must be an array")
        if pose is not None and any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in pose):
            raise SkillContractError("target_pose must contain finite numeric values")
        values = dict(value)
        values["parameters"] = _json_safe(value["parameters"], "parameters")
        return cls(**values)

    def to_mapping(self) -> dict[str, Any]:
        return _json_safe({
            "request_id": self.request_id, "skill_id": self.skill_id,
            "robot_id": self.robot_id, "target_ref": self.target_ref,
            "target_pose": self.target_pose, "parameters": self.parameters,
            "task_id": self.task_id, "skill_version": self.skill_version,
            "simulator_snapshot": self.simulator_snapshot, "timing": self.timing,
            "budget": self.budget, "branch_context": self.branch_context,
        }, "request")


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

    def to_mapping(self) -> dict[str, Any]:
        return {"command_type": self.command_type, "payload": _json_safe(self.payload, "payload")}


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

    def to_mapping(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id, "skill_id": self.skill_id,
            "robot_id": self.robot_id, "status": self.status,
            "commands": [command.to_mapping() for command in self.commands],
            "metadata": _json_safe(self.metadata, "metadata"),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_mapping(), allow_nan=False, sort_keys=True, separators=(",", ":"))


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
