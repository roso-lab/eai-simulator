"""A deterministic Python boundary for Hybrid SUSD-based MRTA.

The official implementation is MATLAB and represents robot specialization as
``Q`` and task types as ``Y``.  This adapter preserves that boundary without
requiring MATLAB inside ``env_isaaclab``:

1. a market pass creates a feasible initial assignment;
2. a finite, derivative-free SUSD-style neighborhood search improves the
   assignment cost; and
3. a repair call reruns the same allocator on open tasks after EAI feedback.

The Fire Rescue benchmark uses one indivisible task per robot dispatch slot;
the typed EAI bridge therefore emits one ``robot -> task`` mapping per round.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Iterable, Mapping, Sequence

from algorithm.common_coordination import Assignment, ContractError, RobotCapability


@dataclass(frozen=True)
class HybridAgent:
    agent_id: str
    capabilities: frozenset[str]
    position: tuple[float, float] = (0.0, 0.0)
    skill_scores: Mapping[str, float] = field(default_factory=dict)
    active: bool = True

    def __post_init__(self) -> None:
        if not str(self.agent_id).strip():
            raise ContractError("agent_id is required")
        if len(self.position) < 2:
            raise ContractError("position requires x/y")
        scores = {str(key): float(value) for key, value in dict(self.skill_scores).items()}
        if any(not math.isfinite(value) or value < 0 for value in scores.values()):
            raise ContractError("skill_scores must be finite and non-negative")
        object.__setattr__(self, "capabilities", frozenset(str(item) for item in self.capabilities))
        object.__setattr__(self, "skill_scores", scores)

    def score_for(self, capability: str) -> float:
        if capability not in self.capabilities:
            return 0.0
        return max(1.0, float(self.skill_scores.get(capability, 1.0)))


@dataclass(frozen=True)
class HybridTask:
    task_id: str
    required_capabilities: frozenset[str]
    position: tuple[float, float] = (0.0, 0.0)
    priority: float = 0.0
    duration_s: float = 1.0

    def __post_init__(self) -> None:
        if not str(self.task_id).strip():
            raise ContractError("task_id is required")
        if len(self.position) < 2:
            raise ContractError("task position requires x/y")
        if float(self.duration_s) < 0 or not math.isfinite(float(self.duration_s)):
            raise ContractError("duration_s must be finite and non-negative")
        object.__setattr__(self, "required_capabilities", frozenset(str(item) for item in self.required_capabilities))


@dataclass(frozen=True)
class HybridPlan:
    assignments: tuple[Assignment, ...]
    objective: float
    version: str
    unassigned_task_ids: tuple[str, ...] = ()
    source: str = "official-hybrid-susd-adapted-to-eai"
    market_objective: float = 0.0
    susd_iterations: int = 0

    @property
    def mapping(self) -> dict[str, str]:
        return {item.robot_id: item.task_id for item in self.assignments}


class HybridSUSDAllocator:
    """Market initialization plus deterministic derivative-free improvement.

    ``Q`` in the paper is represented by each agent's capability score.  The
    binary Fire Rescue capability catalog defaults to score 1, while callers
    may provide score 2 for a robot specialized to a skill, matching the
    paper's ``Q=[1, 2]`` style examples.
    """

    def __init__(
        self,
        agents: Sequence[HybridAgent],
        *,
        max_tasks_per_robot: int = 1,
        max_susd_iterations: int = 20,
    ) -> None:
        if not agents:
            raise ContractError("at least one agent is required")
        if int(max_tasks_per_robot) != 1:
            raise ContractError(
                "the typed EAI mapping is one indivisible task per robot; "
                "use a queue-level adapter for multi-slot routes"
            )
        if int(max_susd_iterations) < 0:
            raise ContractError("max_susd_iterations must be non-negative")
        self.agents = tuple(agents)
        self.max_tasks_per_robot = 1
        self.max_susd_iterations = int(max_susd_iterations)
        self._round = 0

    @classmethod
    def from_capabilities(
        cls,
        robots: Sequence[RobotCapability],
        positions: Mapping[str, Sequence[float]],
        *,
        max_tasks_per_robot: int = 1,
    ) -> "HybridSUSDAllocator":
        def xy(robot_id: str) -> tuple[float, float]:
            value = positions.get(robot_id)
            return (float(value[0]), float(value[1])) if value is not None else (0.0, 0.0)

        agents = tuple(
            HybridAgent(robot.robot_id, robot.capabilities, xy(robot.robot_id), active=robot.active)
            for robot in robots
        )
        return cls(agents, max_tasks_per_robot=max_tasks_per_robot)

    def allocate(self, tasks: Sequence[HybridTask]) -> HybridPlan:
        task_list = tuple(tasks)
        if not task_list:
            raise ContractError("at least one task is required")
        if len({task.task_id for task in task_list}) != len(task_list):
            raise ContractError("task ids must be unique")
        active = tuple(agent for agent in self.agents if agent.active)
        if not active:
            raise ContractError("no active agents are available")

        # Step 1: market_onlineTA equivalent.  Each task bids for the robot
        # with the best incremental route cost and specialization utility.
        ordered = sorted(task_list, key=lambda task: (-float(task.priority), task.task_id))
        assignment = self._market_assignment(ordered, active)
        market_objective = self._objective(assignment, task_list, active)

        # Step 2: susd_onlineTA equivalent.  The MATLAB method explores a
        # continuous assignment graph; this finite neighborhood is its stable
        # discrete counterpart for indivisible EAI task slots.
        best = dict(assignment)
        best_value = market_objective
        iterations = 0
        for iteration in range(self.max_susd_iterations):
            candidate, candidate_value = self._best_neighbor(best, task_list, active)
            iterations = iteration + 1
            if candidate_value <= best_value + 1e-9:
                break
            best, best_value = candidate, candidate_value

        self._round += 1
        return self._plan(best, task_list, best_value, market_objective, iterations, f"hybrid-susd-r{self._round}")

    def repair(
        self,
        tasks: Sequence[HybridTask],
        previous: HybridPlan | Mapping[str, str],
        *,
        failed_robot_ids: Iterable[str] = (),
        delayed_task_ids: Iterable[str] = (),
    ) -> HybridPlan:
        """Adapter-level reallocation on open tasks after execution feedback.

        The paper's public code has online task arrival but no EAI failure
        callback.  Consequently this is deliberately named ``repair`` and is
        reported as adapter-level reallocation by the provenance/ledger.
        """
        failed = {str(item) for item in failed_robot_ids}
        delayed = {str(item) for item in delayed_task_ids}
        task_list = tuple(task for task in tasks if task.task_id in delayed or not delayed)
        if not task_list:
            raise ContractError("repair requires at least one open task")
        available = tuple(agent for agent in self.agents if agent.agent_id not in failed and agent.active)
        if not available:
            return HybridPlan((), 0.0, f"hybrid-susd-repair-r{self._round + 1}", tuple(task.task_id for task in task_list), source="official-hybrid-susd-adapter-reallocation")
        allocator = HybridSUSDAllocator(
            available,
            max_tasks_per_robot=self.max_tasks_per_robot,
            max_susd_iterations=self.max_susd_iterations,
        )
        plan = allocator.allocate(task_list)
        return HybridPlan(
            plan.assignments,
            plan.objective,
            f"hybrid-susd-repair-r{self._round + 1}",
            plan.unassigned_task_ids,
            source="official-hybrid-susd-adapter-reallocation",
            market_objective=plan.market_objective,
            susd_iterations=plan.susd_iterations,
        )

    def _market_assignment(self, tasks: Sequence[HybridTask], agents: Sequence[HybridAgent]) -> dict[str, str]:
        used: dict[str, int] = {agent.agent_id: 0 for agent in agents}
        assignment: dict[str, str] = {}
        for task in tasks:
            candidates = [
                agent for agent in agents
                if used[agent.agent_id] < self.max_tasks_per_robot
                and task.required_capabilities <= agent.capabilities
            ]
            if not candidates:
                continue
            winner = max(candidates, key=lambda agent: (self._utility(agent, task), -used[agent.agent_id], agent.agent_id))
            assignment[winner.agent_id] = task.task_id
            used[winner.agent_id] += 1
        return assignment

    def _best_neighbor(
        self,
        current: Mapping[str, str],
        tasks: Sequence[HybridTask],
        agents: Sequence[HybridAgent],
    ) -> tuple[dict[str, str], float]:
        best = dict(current)
        best_value = self._objective(best, tasks, agents)
        task_by_id = {task.task_id: task for task in tasks}
        assigned = {task_id: agent_id for agent_id, task_id in current.items()}
        agent_by_id = {agent.agent_id: agent for agent in agents}
        # Reassignment and pairwise swaps are the discrete neighborhood used by
        # SUSD's derivative-free exploration of allocation coordinates.
        for task_id, old_agent_id in sorted(assigned.items()):
            task = task_by_id[task_id]
            for agent in sorted(agents, key=lambda item: item.agent_id):
                if agent.agent_id == old_agent_id or task.required_capabilities > agent.capabilities:
                    continue
                if agent.agent_id in current:
                    other_task = task_by_id[current[agent.agent_id]]
                    old_agent = agent_by_id[old_agent_id]
                    if task.required_capabilities <= agent.capabilities and other_task.required_capabilities <= old_agent.capabilities:
                        candidate = dict(current)
                        candidate[old_agent_id], candidate[agent.agent_id] = other_task.task_id, task_id
                    else:
                        continue
                else:
                    candidate = dict(current)
                    candidate.pop(old_agent_id)
                    candidate[agent.agent_id] = task_id
                value = self._objective(candidate, tasks, agents)
                if value > best_value + 1e-9:
                    best, best_value = candidate, value
        return best, best_value

    @classmethod
    def _utility(cls, agent: HybridAgent, task: HybridTask) -> float:
        if task.required_capabilities > agent.capabilities:
            return -math.inf
        quality = sum(agent.score_for(capability) for capability in task.required_capabilities)
        distance = math.hypot(agent.position[0] - task.position[0], agent.position[1] - task.position[1])
        return float(task.priority) + quality - distance

    @classmethod
    def _objective(
        cls,
        assignment: Mapping[str, str],
        tasks: Sequence[HybridTask],
        agents: Sequence[HybridAgent],
    ) -> float:
        task_by_id = {task.task_id: task for task in tasks}
        agent_by_id = {agent.agent_id: agent for agent in agents}
        assigned = {task_id for task_id in assignment.values()}
        value = sum(cls._utility(agent_by_id[agent_id], task_by_id[task_id]) for agent_id, task_id in assignment.items())
        # Penalize tasks left out of a bounded EAI dispatch.  The value is
        # finite so a plan is still serializable and comparable in a ledger.
        value -= 10.0 * (len(task_by_id) - len(assigned))
        return float(value)

    @staticmethod
    def _plan(
        assignment: Mapping[str, str],
        tasks: Sequence[HybridTask],
        objective: float,
        market_objective: float,
        iterations: int,
        version: str,
    ) -> HybridPlan:
        assignments = tuple(
            Assignment(task_id, robot_id, "Hybrid-SUSD-adapted-to-EAI", version)
            for robot_id, task_id in sorted(assignment.items())
        )
        assigned = {item.task_id for item in assignments}
        return HybridPlan(
            assignments,
            float(objective),
            version,
            tuple(task.task_id for task in tasks if task.task_id not in assigned),
            market_objective=float(market_objective),
            susd_iterations=int(iterations),
        )


__all__ = ["HybridAgent", "HybridTask", "HybridPlan", "HybridSUSDAllocator"]
