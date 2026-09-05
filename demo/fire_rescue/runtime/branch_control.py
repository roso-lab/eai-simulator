"""Pure state machine for interactive Fire Rescue fact/branch runs.

This module deliberately has no Isaac, dashboard, or simulator dependencies.  It
only owns the lifecycle and validation of the assignment checkpoint used by a
future branch runner.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from collections.abc import Iterable, Mapping
from typing import Any


DEFAULT_ROBOT_KEYS = ("carter_1", "m20_1", "m20_2", "scout_1")
ASSIGNMENT_FIELDS = ("subtask", "colour", "target_xy")


class BranchPhase(str, Enum):
    FACT_RUNNING = "fact_running"
    WAITING_ASSIGNMENT_EDIT = "waiting_assignment_edit"
    BRANCH_READY = "branch_ready"
    BRANCH_RUNNING = "branch_running"
    FINISHED = "finished"


def _assignment_items(assignments: Mapping[str, Any] | Iterable[tuple[str, Any]]):
    if isinstance(assignments, Mapping):
        return list(assignments.items())
    try:
        return list(assignments)
    except TypeError as exc:
        raise ValueError("assignments must be a mapping or iterable of pairs") from exc


def normalize_assignments(
    assignments: Mapping[str, Any] | Iterable[tuple[str, Any]],
    *,
    robot_keys: Iterable[str] = DEFAULT_ROBOT_KEYS,
) -> dict[str, dict[str, Any]]:
    """Validate and copy a robot assignment mapping into JSON-safe values."""

    expected = tuple(robot_keys)
    if len(set(expected)) != len(expected):
        raise ValueError("robot_keys contain duplicates")
    items = _assignment_items(assignments)
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            raise ValueError("each assignment must be a robot/value pair")
        robot, raw = item
        if not isinstance(robot, str) or robot not in expected:
            raise ValueError(f"unknown robot: {robot!r}")
        if robot in result:
            raise ValueError(f"duplicate robot: {robot}")
        if not isinstance(raw, Mapping):
            raise ValueError(f"assignment for {robot} must be a mapping")
        missing = [field for field in ASSIGNMENT_FIELDS if field not in raw]
        if missing:
            raise ValueError(f"missing field(s) for {robot}: {', '.join(missing)}")
        extra = [field for field in raw if field not in ASSIGNMENT_FIELDS]
        if extra:
            raise ValueError(f"unknown field(s) for {robot}: {', '.join(map(str, extra))}")
        subtask = raw["subtask"]
        colour = raw["colour"]
        target = raw["target_xy"]
        if not isinstance(subtask, str) or not subtask.strip():
            raise ValueError(f"subtask for {robot} must be a non-empty string")
        if not isinstance(colour, str) or not colour.strip():
            raise ValueError(f"colour for {robot} must be a non-empty string")
        if isinstance(target, (str, bytes)) or not isinstance(target, Iterable):
            raise ValueError(f"target_xy for {robot} must contain two numbers")
        target_values = list(target)
        if len(target_values) != 2 or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in target_values
        ):
            raise ValueError(f"target_xy for {robot} must contain two finite numbers")
        result[robot] = {
            "subtask": subtask,
            "colour": colour,
            "target_xy": [float(target_values[0]), float(target_values[1])],
        }
    missing_robots = [robot for robot in expected if robot not in result]
    if missing_robots:
        raise ValueError(f"missing robot assignment(s): {', '.join(missing_robots)}")
    return result


@dataclass(frozen=True)
class AssignmentEdit:
    """A normalized assignment submitted for the next branch."""

    assignments: dict[str, dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {"assignments": _copy_assignments(self.assignments)}


def _copy_assignments(assignments: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(robot): {
            "subtask": str(value["subtask"]),
            "colour": str(value["colour"]),
            "target_xy": [float(value["target_xy"][0]), float(value["target_xy"][1])],
        }
        for robot, value in assignments.items()
    }


class BranchController:
    """Manage one fact run and one editable counterfactual branch."""

    SCHEMA_VERSION = 1

    def __init__(self, *, robot_keys: Iterable[str] = DEFAULT_ROBOT_KEYS):
        self.robot_keys = tuple(robot_keys)
        if len(set(self.robot_keys)) != len(self.robot_keys):
            raise ValueError("robot_keys contain duplicates")
        self.phase = BranchPhase.FACT_RUNNING
        self.checkpoint_id: str | None = None
        self.assignment_checkpoint: dict[str, dict[str, Any]] | None = None
        self.assignment_edit: AssignmentEdit | None = None
        self.fact_result: Any = None
        self.branch_result: Any = None

    def enter_fact_running(self) -> None:
        if self.phase not in (BranchPhase.FACT_RUNNING, BranchPhase.FINISHED):
            raise RuntimeError(f"cannot enter fact running from {self.phase.value}")
        self.phase = BranchPhase.FACT_RUNNING
        self.fact_result = None
        self.branch_result = None
        self.assignment_edit = None

    def save_assignment_checkpoint(self, assignments: Mapping[str, Any] | Iterable[tuple[str, Any]], checkpoint_id: str) -> None:
        if self.phase is not BranchPhase.FACT_RUNNING:
            raise RuntimeError("assignment checkpoint requires fact running phase")
        if not isinstance(checkpoint_id, str) or not checkpoint_id.strip():
            raise ValueError("checkpoint_id must be a non-empty string")
        self.assignment_checkpoint = normalize_assignments(assignments, robot_keys=self.robot_keys)
        self.checkpoint_id = checkpoint_id
        self.phase = BranchPhase.WAITING_ASSIGNMENT_EDIT

    def submit_assignment_edit(self, assignments: Mapping[str, Any] | Iterable[tuple[str, Any]]) -> AssignmentEdit:
        if self.phase is not BranchPhase.WAITING_ASSIGNMENT_EDIT:
            raise RuntimeError("assignment edit is only allowed while waiting for assignment edit")
        edit = AssignmentEdit(normalize_assignments(assignments, robot_keys=self.robot_keys))
        self.assignment_edit = edit
        self.phase = BranchPhase.BRANCH_READY
        return edit

    def start_branch(self) -> None:
        if self.phase is not BranchPhase.BRANCH_READY or self.assignment_edit is None:
            raise RuntimeError("cannot start branch before an assignment edit")
        self.phase = BranchPhase.BRANCH_RUNNING

    def finish(self, result: Any) -> None:
        if self.phase is BranchPhase.FACT_RUNNING:
            self.fact_result = result
            self.phase = BranchPhase.WAITING_ASSIGNMENT_EDIT
        elif self.phase is BranchPhase.BRANCH_RUNNING:
            self.branch_result = result
            self.phase = BranchPhase.FINISHED
        else:
            raise RuntimeError(f"cannot finish from {self.phase.value}")

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "phase": self.phase.value,
            "robot_keys": list(self.robot_keys),
            "checkpoint_id": self.checkpoint_id,
            "assignment_checkpoint": (
                _copy_assignments(self.assignment_checkpoint)
                if self.assignment_checkpoint is not None
                else None
            ),
            "assignment_edit": self.assignment_edit.to_dict() if self.assignment_edit else None,
            "fact_result": self.fact_result,
            "branch_result": self.branch_result,
        }

    @classmethod
    def restore(cls, snapshot: Mapping[str, Any]) -> "BranchController":
        if not isinstance(snapshot, Mapping) or snapshot.get("schema_version") != cls.SCHEMA_VERSION:
            raise ValueError("unsupported branch controller snapshot")
        controller = cls(robot_keys=tuple(snapshot.get("robot_keys", DEFAULT_ROBOT_KEYS)))
        try:
            controller.phase = BranchPhase(snapshot["phase"])
        except (KeyError, ValueError) as exc:
            raise ValueError("invalid branch controller phase") from exc
        controller.checkpoint_id = snapshot.get("checkpoint_id")
        checkpoint = snapshot.get("assignment_checkpoint")
        if checkpoint is not None:
            controller.assignment_checkpoint = normalize_assignments(checkpoint, robot_keys=controller.robot_keys)
        edit = snapshot.get("assignment_edit")
        if edit is not None:
            controller.assignment_edit = AssignmentEdit(
                normalize_assignments(edit.get("assignments"), robot_keys=controller.robot_keys)
            )
        controller.fact_result = snapshot.get("fact_result")
        controller.branch_result = snapshot.get("branch_result")
        return controller

