"""Small faithful boundary for the official MRTA heuristic + planRepair code.

The upstream MATLAB implementation is kept as the provenance authority.  This
module mirrors its Agent/Task fields, heuristic allocation and delay repair in
pure Python so the Isaac Lab environment does not acquire a MATLAB or solver
dependency.  It is therefore reported as an interface-equivalent adapter,
not as a zero-modification execution of the upstream MATLAB files.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable, Mapping, Sequence

from algorithm.common_coordination import Assignment, ContractError, RobotCapability


@dataclass(frozen=True)
class HeuristicAgent:
    agent_id: str
    capabilities: frozenset[str]
    position: tuple[float, float] = (0.0, 0.0)
    battery: float = 1.0
    speed: float = 1.0
    active: bool = True

    def __post_init__(self) -> None:
        if not str(self.agent_id).strip():
            raise ContractError("agent_id is required")
        if len(self.position) < 2:
            raise ContractError("position requires x/y")
        if float(self.battery) < 0 or float(self.speed) <= 0:
            raise ContractError("battery must be non-negative and speed positive")


@dataclass(frozen=True)
class HeuristicTask:
    task_id: str
    required_capabilities: frozenset[str]
    position: tuple[float, float] = (0.0, 0.0)
    duration_s: float = 1.0
    deadline_s: float = math.inf
    priority: float = 0.0
    robots_required: int = 1

    def __post_init__(self) -> None:
        if not str(self.task_id).strip():
            raise ContractError("task_id is required")
        if int(self.robots_required) != 1:
            raise ContractError(
                "Fire Rescue adapter supports one robot per indivisible skill; coalition tasks are N/A"
            )
        if float(self.duration_s) < 0:
            raise ContractError("duration_s must be non-negative")


@dataclass(frozen=True)
class HeuristicPlan:
    assignments: tuple[Assignment, ...]
    objective: float
    version: str
    unassigned_task_ids: tuple[str, ...]
    source: str = "official-mrta-heuristic-equivalent"

    @property
    def mapping(self) -> dict[str, str]:
        return {item.robot_id: item.task_id for item in self.assignments}


class HeuristicTaskAllocator:
    """Greedy dynamic-reward allocator with a deterministic repair round."""

    def __init__(self, agents: Sequence[HeuristicAgent], *, version: int = 9) -> None:
        if not agents:
            raise ContractError("at least one agent is required")
        self.agents = tuple(agents)
        self.version = int(version)
        self._round = 0

    @classmethod
    def from_capabilities(cls, robots: Sequence[RobotCapability], positions: Mapping[str, Sequence[float]]):
        def xy(robot_id: str) -> tuple[float, float]:
            value = positions.get(robot_id)
            if value is None:
                return (0.0, 0.0)
            return (float(value[0]), float(value[1]))

        agents = tuple(
            HeuristicAgent(
                robot.robot_id,
                robot.capabilities,
                xy(robot.robot_id),
                active=robot.active,
            )
            for robot in robots
        )
        return cls(agents)

    def allocate(self, tasks: Sequence[HeuristicTask]) -> HeuristicPlan:
        tasks = tuple(tasks)
        if not tasks:
            raise ContractError("at least one task is required")
        available = [agent for agent in self.agents if agent.active]
        chosen: list[Assignment] = []
        used: set[str] = set()
        objective = 0.0
        # The official heuristic orders by dynamic reward/waiting time.  EAI
        # has no waiting history on the first round, so priority then task id
        # is the stable equivalent tie-breaker.
        ordered = sorted(tasks, key=lambda item: (-float(item.priority), item.task_id))
        for task in ordered:
            candidates = [
                agent for agent in available
                if agent.agent_id not in used
                and task.required_capabilities <= agent.capabilities
            ]
            if not candidates:
                continue
            agent = min(candidates, key=lambda item: (self._cost(item, task), item.agent_id))
            score = self._utility(agent, task)
            objective += score
            chosen.append(
                Assignment(task.task_id, agent.agent_id, "MRTA-heuristic-adapted-to-EAI", f"mrta-r{self._round + 1}")
            )
            used.add(agent.agent_id)
        self._round += 1
        assigned = {item.task_id for item in chosen}
        return HeuristicPlan(
            tuple(chosen),
            objective,
            f"mrta-r{self._round}",
            tuple(task.task_id for task in tasks if task.task_id not in assigned),
        )

    def repair(
        self,
        tasks: Sequence[HeuristicTask],
        previous: HeuristicPlan,
        *,
        failed_robot_ids: Iterable[str] = (),
        delayed_task_ids: Iterable[str] = (),
    ) -> HeuristicPlan:
        """Repair the affected queue, equivalent to upstream ``planRepair``."""
        failed = {str(item) for item in failed_robot_ids}
        delayed = {str(item) for item in delayed_task_ids}
        by_task = {task.task_id: task for task in tasks}
        preserved: list[Assignment] = []
        released_tasks: list[HeuristicTask] = []
        # A delayed task is released, but its robot remains available for the
        # repair round.  A failed robot is excluded from replacement.
        for assignment in previous.assignments:
            if assignment.robot_id in failed or assignment.task_id in delayed:
                if assignment.task_id in by_task:
                    released_tasks.append(by_task[assignment.task_id])
            else:
                preserved.append(assignment)
        assigned_ids = {item.task_id for item in preserved}
        released_tasks.extend(
            task for task in tasks
            if task.task_id not in assigned_ids
            and task.task_id not in {item.task_id for item in released_tasks}
        )
        preserved_agents = {item.robot_id for item in preserved}
        free_agents = tuple(
            agent for agent in self.agents
            if agent.agent_id not in preserved_agents and agent.agent_id not in failed
        )
        if not free_agents:
            return HeuristicPlan(
                tuple(preserved),
                0.0,
                f"mrta-repair-r{self._round + 1}",
                tuple(task.task_id for task in tasks if task.task_id not in {item.task_id for item in preserved}),
                source="official-mrta-planRepair-equivalent",
            )
        repaired = HeuristicTaskAllocator(free_agents, version=13).allocate(released_tasks)
        assignments = tuple(preserved) + repaired.assignments
        assigned = {item.task_id for item in assignments}
        return HeuristicPlan(
            assignments,
            repaired.objective,
            f"mrta-repair-r{self._round + 1}",
            tuple(task.task_id for task in tasks if task.task_id not in assigned),
            source="official-mrta-planRepair-equivalent",
        )

    @staticmethod
    def _cost(agent: HeuristicAgent, task: HeuristicTask) -> float:
        distance = math.hypot(agent.position[0] - task.position[0], agent.position[1] - task.position[1])
        return distance / max(agent.speed, 1e-6) + 0.25 * (1.0 - min(agent.battery, 1.0))

    @classmethod
    def _utility(cls, agent: HeuristicAgent, task: HeuristicTask) -> float:
        return float(task.priority) - cls._cost(agent, task)


__all__ = ["HeuristicAgent", "HeuristicTask", "HeuristicTaskAllocator", "HeuristicPlan"]
