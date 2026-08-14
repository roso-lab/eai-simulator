from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from TeamWeaver.eai_adapter.capability_ontology import hard_feasible
from TeamWeaver.eai_adapter.task_models import (
    RobotSnapshot,
    SemanticTask,
    SymbolicWorldState,
    TaskType,
)


INFEASIBLE_COST = 1.0e12
PREEMPTION_COST = 4.0
MiqpSolver = Callable[..., Sequence[tuple[int, int]]]


@dataclass(frozen=True)
class PhaseAssignment:
    task_id: str
    robot_name: str
    target_ref: str
    pair_cost: float
    relaxed: bool
    changed: bool
    preempted_task_id: str | None = None


@dataclass(frozen=True)
class ObjectiveBreakdown:
    execution: float = 0.0
    relaxation: float = 0.0
    load: float = 0.0
    transition: float = 0.0
    deferred: float = 0.0
    preemption: float = 0.0

    @property
    def total(self) -> float:
        return (
            self.execution
            + self.relaxation
            + self.load
            + self.transition
            + self.deferred
            + self.preemption
        )


@dataclass(frozen=True)
class AllocationResult:
    assignments: tuple[PhaseAssignment, ...]
    deferred_task_ids: tuple[str, ...]
    relaxed_task_ids: tuple[str, ...]
    changed_task_ids: tuple[str, ...]
    hard_infeasible_task_ids: tuple[str, ...]
    solver: str
    total_cost: float
    objective: ObjectiveBreakdown
    fallback_reason: str | None = None

    @property
    def assignment_by_task(self) -> Mapping[str, PhaseAssignment]:
        return MappingProxyType({item.task_id: item for item in self.assignments})


class DynamicMIQPAllocator:
    def __init__(
        self,
        *,
        prefer_miqp: bool = True,
        miqp_solver: MiqpSolver | None = None,
        feasibility_filter: (
            Callable[[Any, Any, Any], bool] | None
        ) = None,
    ) -> None:
        self.prefer_miqp = bool(prefer_miqp)
        self.miqp_solver = miqp_solver
        self._feasibility_filter = feasibility_filter

    def allocate(
        self,
        world: SymbolicWorldState,
        tasks: Sequence[SemanticTask],
        previous_assignments: Mapping[str, str] | None = None,
    ) -> AllocationResult:
        task_list = tuple(sorted(tasks, key=lambda item: (item.priority, item.task_id)))
        previous = dict(previous_assignments or {})
        if not task_list:
            return AllocationResult(
                assignments=(),
                deferred_task_ids=(),
                relaxed_task_ids=(),
                changed_task_ids=(),
                hard_infeasible_task_ids=(),
                solver="none",
                total_cost=0.0,
                objective=ObjectiveBreakdown(),
            )
        if len({task.task_id for task in task_list}) != len(task_list):
            raise ValueError("allocation task ids must be unique")

        includes_removal = any(
            task.task_type is TaskType.REMOVE_OBSTACLE for task in task_list
        )
        robots = tuple(
            sorted(
                (
                    robot
                    for robot in world.robots
                    if robot.safe and (includes_removal or not robot.busy)
                ),
                key=lambda item: item.name,
            )
        )
        feasible, pair_costs, soft_gaps, preemption_costs = self._pair_data(
            world,
            robots,
            task_list,
        )
        hard_infeasible = tuple(
            task.task_id
            for task_index, task in enumerate(task_list)
            if not feasible[:, task_index].any()
        )

        fallback_reason = None
        if self.prefer_miqp:
            try:
                solver = self.miqp_solver or self._solve_gurobi
                pairs = solver(
                    pair_costs,
                    feasible,
                    soft_gaps,
                    preemption_costs,
                    robots,
                    task_list,
                    previous,
                )
                return self._build_result(
                    world,
                    robots,
                    task_list,
                    pair_costs,
                    feasible,
                    soft_gaps,
                    preemption_costs,
                    pairs,
                    previous,
                    solver_name="miqp",
                    hard_infeasible=hard_infeasible,
                )
            except Exception as exc:
                fallback_reason = str(exc) or type(exc).__name__

        pairs = self._solve_hungarian(
            pair_costs,
            feasible,
            soft_gaps,
            preemption_costs,
            robots,
            task_list,
            previous,
        )
        return self._build_result(
            world,
            robots,
            task_list,
            pair_costs,
            feasible,
            soft_gaps,
            preemption_costs,
            pairs,
            previous,
            solver_name="hungarian",
            hard_infeasible=hard_infeasible,
            fallback_reason=fallback_reason,
        )

    @staticmethod
    def soft_capability_gap(robot: RobotSnapshot, task: SemanticTask) -> float:
        soft = tuple(
            (name, requirement)
            for name, requirement in task.requirements.items()
            if not requirement.hard
        )
        total_weight = sum(requirement.weight for _name, requirement in soft)
        if total_weight <= 1.0e-9:
            return 0.0
        effective = robot.effective_capabilities
        return sum(
            requirement.weight
            * max(0.0, requirement.minimum - effective.get(name, 0.0))
            for name, requirement in soft
        ) / total_weight

    @staticmethod
    def preferred_agent_penalty(robot: RobotSnapshot, task: SemanticTask) -> float:
        return 0.0 if task.preferred_agent in (None, robot.name) else 1.0

    @staticmethod
    def distance(
        robot: RobotSnapshot,
        task: SemanticTask,
        world: SymbolicWorldState,
    ) -> float:
        target = world.target_by_ref(task.target_ref)
        if target.kind == "virtual" or target.position is None:
            return 0.0
        return math.dist(robot.position, target.position[:2])

    def _pair_data(
        self,
        world: SymbolicWorldState,
        robots: Sequence[RobotSnapshot],
        tasks: Sequence[SemanticTask],
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        feasible = np.zeros((len(robots), len(tasks)), dtype=bool)
        distances = np.zeros((len(robots), len(tasks)), dtype=float)
        soft_gaps = np.zeros((len(robots), len(tasks)), dtype=float)
        preemption_costs = np.zeros((len(robots), len(tasks)), dtype=float)
        active_obstacle = world.active_obstacle()
        continuation_agents = {
            task.required_agent
            for task in tasks
            if task.required_agent is not None
        }
        _filter = self._feasibility_filter
        for robot_index, robot in enumerate(robots):
            for task_index, task in enumerate(tasks):
                if _filter is not None:
                    feasible[robot_index, task_index] = (
                        hard_feasible(robot, task)
                        and _filter(robot, task, world)
                    )
                else:
                    carrier_matches = (
                        task.task_type is not TaskType.DELIVER_EXTINGUISHER
                        or world.extinguisher_carrier == robot.name
                    )
                    obstacle_matches = True
                    if task.task_type is TaskType.REMOVE_OBSTACLE:
                        obstacle_matches = (
                            active_obstacle is not None
                            and task.target_ref == active_obstacle.obstacle_id
                            and robot.name != active_obstacle.blocked_robot
                        )
                    blocked_robot_ineligible = (
                        active_obstacle is not None
                        and robot.name == active_obstacle.blocked_robot
                        and not robot.busy
                    )
                    feasible[robot_index, task_index] = (
                        hard_feasible(robot, task)
                        and carrier_matches
                        and obstacle_matches
                        and not blocked_robot_ineligible
                    )
                if (
                    feasible[robot_index, task_index]
                    and robot.name in continuation_agents
                    and task.required_agent != robot.name
                ):
                    feasible[robot_index, task_index] = False
                if (
                    feasible[robot_index, task_index]
                    and task.task_type is TaskType.REMOVE_OBSTACLE
                    and robot.busy
                ):
                    preemption_costs[robot_index, task_index] = PREEMPTION_COST
                distances[robot_index, task_index] = self.distance(robot, task, world)
                soft_gaps[robot_index, task_index] = self.soft_capability_gap(
                    robot, task
                )
        max_pair_distance = float(distances.max()) if distances.size else 0.0
        distance_scale = max_pair_distance if max_pair_distance > 1.0e-9 else 1.0
        pair_costs = np.zeros_like(distances)
        for robot_index, robot in enumerate(robots):
            for task_index, task in enumerate(tasks):
                pair_costs[robot_index, task_index] = (
                    distances[robot_index, task_index] / distance_scale
                    + 3.0 * soft_gaps[robot_index, task_index]
                    + 0.5 * max(0, int(robot.current_load))
                    + 0.5 * self.preferred_agent_penalty(robot, task)
                )
        pair_costs[~feasible] = INFEASIBLE_COST
        return feasible, pair_costs, soft_gaps, preemption_costs

    @staticmethod
    def _solve_gurobi(
        pair_costs: np.ndarray,
        feasible: np.ndarray,
        soft_gaps: np.ndarray,
        preemption_costs: np.ndarray,
        robots: Sequence[RobotSnapshot],
        tasks: Sequence[SemanticTask],
        previous: Mapping[str, str],
    ) -> tuple[tuple[int, int], ...]:
        import gurobipy as gp

        robot_count = len(robots)
        task_count = len(tasks)
        model = gp.Model("teamweaver_dynamic_phase")
        model.Params.OutputFlag = 0
        x = model.addVars(robot_count, task_count, vtype=gp.GRB.BINARY, name="x")
        u = model.addVars(task_count, vtype=gp.GRB.BINARY, name="u")
        phi = model.addVars(task_count, vtype=gp.GRB.BINARY, name="phi")
        z = model.addVars(robot_count, task_count, vtype=gp.GRB.BINARY, name="z")

        for task_index, task in enumerate(tasks):
            model.addConstr(
                gp.quicksum(x[robot_index, task_index] for robot_index in range(robot_count))
                + u[task_index]
                == 1
            )
            for robot_index, robot in enumerate(robots):
                if not feasible[robot_index, task_index]:
                    model.addConstr(x[robot_index, task_index] == 0)
                if soft_gaps[robot_index, task_index] > 1.0e-9:
                    model.addConstr(phi[task_index] >= x[robot_index, task_index])
                previous_value = int(previous.get(task.task_id) == robot.name)
                model.addConstr(
                    z[robot_index, task_index]
                    >= x[robot_index, task_index] - previous_value
                )
                model.addConstr(
                    z[robot_index, task_index]
                    >= previous_value - x[robot_index, task_index]
                )
        for robot_index in range(robot_count):
            model.addConstr(
                gp.quicksum(x[robot_index, task_index] for task_index in range(task_count))
                <= 1
            )

        execution = gp.quicksum(
            float(pair_costs[robot_index, task_index]) * x[robot_index, task_index]
            for robot_index in range(robot_count)
            for task_index in range(task_count)
            if feasible[robot_index, task_index]
        )
        relaxation = gp.quicksum(
            10.0 * (6 - task.priority) * phi[task_index]
            for task_index, task in enumerate(tasks)
        )
        load = gp.QuadExpr()
        for robot_index, robot in enumerate(robots):
            assigned = gp.quicksum(
                x[robot_index, task_index] for task_index in range(task_count)
            )
            total_load = float(max(0, int(robot.current_load))) + assigned
            load += 0.1 * total_load * total_load
        transition = gp.quicksum(
            2.0 * z[robot_index, task_index]
            for robot_index in range(robot_count)
            for task_index in range(task_count)
        )
        available_robot_names = {robot.name for robot in robots}
        transition += 2.0 * sum(
            task.task_id in previous
            and previous[task.task_id] not in available_robot_names
            for task in tasks
        )
        deferred = gp.quicksum(
            20.0 * (6 - task.priority) * u[task_index]
            for task_index, task in enumerate(tasks)
        )
        preemption = gp.quicksum(
            float(preemption_costs[robot_index, task_index])
            * x[robot_index, task_index]
            for robot_index in range(robot_count)
            for task_index in range(task_count)
        )
        model.setObjective(
            execution + relaxation + load + transition + deferred + preemption,
            gp.GRB.MINIMIZE,
        )
        model.optimize()
        if model.Status != gp.GRB.OPTIMAL:
            raise RuntimeError(f"Gurobi phase allocation status is {model.Status}")
        return tuple(
            (robot_index, task_index)
            for robot_index in range(robot_count)
            for task_index in range(task_count)
            if x[robot_index, task_index].X > 0.5
        )

    @staticmethod
    def _solve_hungarian(
        pair_costs: np.ndarray,
        feasible: np.ndarray,
        soft_gaps: np.ndarray,
        preemption_costs: np.ndarray,
        robots: Sequence[RobotSnapshot],
        tasks: Sequence[SemanticTask],
        previous: Mapping[str, str],
    ) -> tuple[tuple[int, int], ...]:
        from scipy.optimize import linear_sum_assignment

        robot_count = len(robots)
        task_count = len(tasks)
        matrix = np.full((robot_count + task_count, task_count), INFEASIBLE_COST)
        for robot_index, robot in enumerate(robots):
            load = max(0, int(robot.current_load))
            incremental_load = 0.1 * ((load + 1) ** 2 - load**2)
            for task_index, task in enumerate(tasks):
                if not feasible[robot_index, task_index]:
                    continue
                relaxation = (
                    10.0 * (6 - task.priority)
                    if soft_gaps[robot_index, task_index] > 1.0e-9
                    else 0.0
                )
                transition = _selected_transition_cost(
                    task.task_id, robot.name, previous
                )
                matrix[robot_index, task_index] = (
                    pair_costs[robot_index, task_index]
                    + relaxation
                    + incremental_load
                    + transition
                    + preemption_costs[robot_index, task_index]
                    + 1.0e-9 * (robot_index * task_count + task_index)
                )
        for task_index, task in enumerate(tasks):
            transition = 2.0 if task.task_id in previous else 0.0
            matrix[robot_count + task_index, task_index] = (
                20.0 * (6 - task.priority) + transition + 1.0e-9 * task_index
            )
        rows, columns = linear_sum_assignment(matrix)
        return tuple(
            (int(row), int(column))
            for row, column in zip(rows, columns)
            if int(row) < robot_count
        )

    @staticmethod
    def _build_result(
        world: SymbolicWorldState,
        robots: Sequence[RobotSnapshot],
        tasks: Sequence[SemanticTask],
        pair_costs: np.ndarray,
        feasible: np.ndarray,
        soft_gaps: np.ndarray,
        preemption_costs: np.ndarray,
        pairs: Sequence[tuple[int, int]],
        previous: Mapping[str, str],
        *,
        solver_name: str,
        hard_infeasible: tuple[str, ...],
        fallback_reason: str | None = None,
    ) -> AllocationResult:
        normalized = tuple((int(row), int(column)) for row, column in pairs)
        if len({row for row, _column in normalized}) != len(normalized):
            raise RuntimeError(f"{solver_name} assigned one robot more than once")
        if len({column for _row, column in normalized}) != len(normalized):
            raise RuntimeError(f"{solver_name} assigned one task more than once")
        for row, column in normalized:
            if (
                row < 0
                or row >= len(robots)
                or column < 0
                or column >= len(tasks)
                or not feasible[row, column]
            ):
                raise RuntimeError(f"{solver_name} returned an infeasible assignment")

        by_task_index = {column: row for row, column in normalized}
        assignments = tuple(
            PhaseAssignment(
                task_id=task.task_id,
                robot_name=robots[by_task_index[task_index]].name,
                target_ref=task.target_ref,
                pair_cost=float(pair_costs[by_task_index[task_index], task_index]),
                relaxed=bool(
                    soft_gaps[by_task_index[task_index], task_index] > 1.0e-9
                ),
                changed=(
                    task.task_id in previous
                    and previous[task.task_id]
                    != robots[by_task_index[task_index]].name
                ),
                preempted_task_id=(
                    robots[by_task_index[task_index]].current_task
                    if preemption_costs[
                        by_task_index[task_index], task_index
                    ]
                    > 0.0
                    else None
                ),
            )
            for task_index, task in enumerate(tasks)
            if task_index in by_task_index
        )
        deferred = tuple(
            task.task_id
            for task_index, task in enumerate(tasks)
            if task_index not in by_task_index
        )
        objective = _objective_breakdown(
            robots,
            tasks,
            pair_costs,
            soft_gaps,
            preemption_costs,
            by_task_index,
            previous,
        )
        relaxed = tuple(item.task_id for item in assignments if item.relaxed)
        changed = tuple(item.task_id for item in assignments if item.changed)
        return AllocationResult(
            assignments=assignments,
            deferred_task_ids=deferred,
            relaxed_task_ids=relaxed,
            changed_task_ids=changed,
            hard_infeasible_task_ids=hard_infeasible,
            solver=solver_name,
            total_cost=objective.total,
            objective=objective,
            fallback_reason=fallback_reason,
        )


def _selected_transition_cost(
    task_id: str,
    robot_name: str,
    previous: Mapping[str, str],
) -> float:
    previous_robot = previous.get(task_id)
    if previous_robot is None:
        return 2.0
    return 0.0 if previous_robot == robot_name else 4.0


def _objective_breakdown(
    robots: Sequence[RobotSnapshot],
    tasks: Sequence[SemanticTask],
    pair_costs: np.ndarray,
    soft_gaps: np.ndarray,
    preemption_costs: np.ndarray,
    by_task_index: Mapping[int, int],
    previous: Mapping[str, str],
) -> ObjectiveBreakdown:
    execution = sum(
        float(pair_costs[robot_index, task_index])
        for task_index, robot_index in by_task_index.items()
    )
    relaxation = sum(
        10.0 * (6 - tasks[task_index].priority)
        for task_index, robot_index in by_task_index.items()
        if soft_gaps[robot_index, task_index] > 1.0e-9
    )
    assignments_by_robot = {
        robot_index: sum(1 for selected in by_task_index.values() if selected == robot_index)
        for robot_index in range(len(robots))
    }
    load = 0.1 * sum(
        (max(0, int(robot.current_load)) + assignments_by_robot[robot_index]) ** 2
        for robot_index, robot in enumerate(robots)
    )
    transition = 0.0
    for task_index, task in enumerate(tasks):
        selected_robot = by_task_index.get(task_index)
        if selected_robot is None:
            if task.task_id in previous:
                transition += 2.0
        else:
            transition += _selected_transition_cost(
                task.task_id, robots[selected_robot].name, previous
            )
    deferred = sum(
        20.0 * (6 - task.priority)
        for task_index, task in enumerate(tasks)
        if task_index not in by_task_index
    )
    preemption = sum(
        float(preemption_costs[robot_index, task_index])
        for task_index, robot_index in by_task_index.items()
    )
    return ObjectiveBreakdown(
        execution=float(execution),
        relaxation=float(relaxation),
        load=float(load),
        transition=float(transition),
        deferred=float(deferred),
        preemption=float(preemption),
    )
