"""Minimal behavior-tree capability auction over typed EAI records."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable, Mapping, Sequence

from algorithm.common_coordination import Assignment, ContractError, CoordinationTask, RobotCapability, TaskGraph


class BTStatus(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class BTCapability:
    task: CoordinationTask
    utility: Mapping[str, float] = field(default_factory=dict)
    precondition: Callable[[str, Mapping[str, object]], bool] | None = None


@dataclass(frozen=True)
class BTPlan:
    assignments: tuple[Assignment, ...]
    ready_task_ids: tuple[str, ...]
    blocked_task_ids: tuple[str, ...]
    status: BTStatus
    plan_version: str


class CapabilityAuctioneer:
    """Deterministic highest-utility capability auction."""

    def __init__(self, robots: Iterable[RobotCapability]) -> None:
        self.robots = {robot.robot_id: robot for robot in robots}
        if not self.robots:
            raise ContractError("capability auction requires at least one robot")

    def auction(
        self,
        capability: BTCapability,
        *,
        world: Mapping[str, object] = {},
        excluded: Iterable[str] = (),
    ) -> str | None:
        excluded = set(excluded)
        candidates: list[tuple[float, str]] = []
        for robot_id, robot in self.robots.items():
            if robot_id in excluded or not robot.active:
                continue
            if not capability.task.required_capabilities <= robot.capabilities:
                continue
            if capability.precondition is not None and not capability.precondition(robot_id, world):
                continue
            candidates.append((float(capability.utility.get(robot_id, 0.0)), robot_id))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (-item[0], item[1]))
        return candidates[0][1]

    def remove_robot(self, robot_id: str) -> None:
        if robot_id not in self.robots:
            raise ContractError(f"unknown robot: {robot_id}")
        current = self.robots[robot_id]
        self.robots[robot_id] = RobotCapability(current.robot_id, current.capabilities, active=False)


class CapabilityMission:
    """Behavior-tree-like sequence/parallel frontier with reauction on failure."""

    def __init__(self, capabilities: Sequence[BTCapability], robots: Iterable[RobotCapability]) -> None:
        self.capabilities = tuple(capabilities)
        TaskGraph(tuple(item.task for item in self.capabilities), source="BT-capabilities EAI")
        self._by_id = {item.task.task_id: item for item in self.capabilities}
        if len(self._by_id) != len(self.capabilities):
            raise ContractError("mission capability task ids must be unique")
        self.auctioneer = CapabilityAuctioneer(robots)
        self._completed: set[str] = set()
        self._failed: set[str] = set()
        self._round = 0

    def tick(self, *, world: Mapping[str, object] = {}, failed_robots: Iterable[str] = ()) -> BTPlan:
        failed_robots = set(failed_robots)
        ready = [
            capability
            for capability in self.capabilities
            if capability.task.task_id not in self._completed
            and capability.task.task_id not in self._failed
            and all(dep in self._completed for dep in capability.task.prerequisites)
        ]
        assignments: list[Assignment] = []
        used: set[str] = set()
        blocked: list[str] = []
        for capability in ready:
            robot_id = self.auctioneer.auction(capability, world=world, excluded=failed_robots | used)
            if robot_id is None:
                blocked.append(capability.task.task_id)
                continue
            used.add(robot_id)
            assignments.append(Assignment(capability.task.task_id, robot_id, "BT-capabilities-adapted-to-EAI", f"bt-r{self._round + 1}"))
        self._round += 1
        if blocked and not assignments:
            status = BTStatus.BLOCKED
        elif not ready and len(self._completed) == len(self.capabilities):
            status = BTStatus.SUCCEEDED
        else:
            status = BTStatus.RUNNING
        return BTPlan(tuple(assignments), tuple(item.task.task_id for item in ready), tuple(blocked), status, f"bt-r{self._round}")

    def feedback(self, task_id: str, *, success: bool, robot_id: str, capability_lost: bool = False) -> None:
        if task_id not in self._by_id:
            raise ContractError(f"unknown mission task: {task_id}")
        if success:
            self._completed.add(task_id)
            return
        self._failed.add(task_id)
        if capability_lost:
            self.auctioneer.remove_robot(robot_id)
            self._failed.discard(task_id)
