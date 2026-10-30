from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable, Dict, List, Mapping, Sequence, Tuple

import numpy as np

from TeamWeaver.eai_adapter.factory_tasks import FactoryTaskSpec


PairList = List[Tuple[int, int]]
MiqpSolver = Callable[[np.ndarray, np.ndarray, np.ndarray], PairList]
INFEASIBLE_COST = 1.0e9


class AllocationError(RuntimeError):
    pass


@dataclass(frozen=True)
class RobotState:
    name: str
    position: Tuple[float, float]
    capabilities: Mapping[str, float]
    current_load: int = 0


@dataclass(frozen=True)
class TaskAssignment:
    robot: RobotState
    task: FactoryTaskSpec
    cost: float


@dataclass(frozen=True)
class AllocationResult:
    assignments: List[TaskAssignment]
    solver: str
    total_cost: float
    fallback_reason: str | None = None

    def by_robot(self) -> Dict[str, FactoryTaskSpec]:
        return {item.robot.name: item.task for item in self.assignments}


class DynamicFactoryAllocator:
    def __init__(
        self,
        *,
        prefer_miqp: bool = True,
        miqp_solver: MiqpSolver | None = None,
        distance_weight: float = 1.0,
        capability_weight: float = 3.0,
        load_weight: float = 0.5,
        load_balance_weight: float = 0.1,
    ) -> None:
        self.prefer_miqp = bool(prefer_miqp)
        self.miqp_solver = miqp_solver
        self.distance_weight = float(distance_weight)
        self.capability_weight = float(capability_weight)
        self.load_weight = float(load_weight)
        self.load_balance_weight = float(load_balance_weight)

    def allocate(
        self,
        robots: Sequence[RobotState],
        tasks: Sequence[FactoryTaskSpec],
    ) -> AllocationResult:
        robot_list = list(robots)
        task_list = list(tasks)
        self._validate_inputs(robot_list, task_list)
        cost_matrix, feasible = self._build_cost_matrix(robot_list, task_list)
        loads = np.asarray([max(0, int(robot.current_load)) for robot in robot_list], dtype=float)

        fallback_reason = None
        if self.prefer_miqp:
            try:
                solver = self.miqp_solver or self._solve_default_miqp
                pairs = solver(cost_matrix, feasible, loads)
                return self._build_result(
                    robot_list,
                    task_list,
                    cost_matrix,
                    feasible,
                    pairs,
                    solver_name="miqp",
                )
            except Exception as exc:
                fallback_reason = str(exc) or type(exc).__name__

        pairs = self._solve_hungarian(cost_matrix, feasible, loads)
        return self._build_result(
            robot_list,
            task_list,
            cost_matrix,
            feasible,
            pairs,
            solver_name="hungarian",
            fallback_reason=fallback_reason,
        )

    def _validate_inputs(
        self,
        robots: Sequence[RobotState],
        tasks: Sequence[FactoryTaskSpec],
    ) -> None:
        if not tasks:
            raise AllocationError("Factory allocation requires at least one task")
        if len(robots) < len(tasks):
            raise AllocationError(
                f"Factory allocation requires {len(tasks)} available robots, got {len(robots)}"
            )
        if len({robot.name for robot in robots}) != len(robots):
            raise AllocationError("Factory allocation robot names must be unique")
        if len({task.task_id for task in tasks}) != len(tasks):
            raise AllocationError("Factory allocation task ids must be unique")
        for robot in robots:
            if len(robot.position) != 2 or not all(math.isfinite(float(value)) for value in robot.position):
                raise AllocationError(f"Robot {robot.name} position must contain two finite values")
        for task in tasks:
            if len(task.target_xy) != 2 or not all(math.isfinite(float(value)) for value in task.target_xy):
                raise AllocationError(f"Task {task.task_id} target must contain two finite values")

    def _build_cost_matrix(
        self,
        robots: Sequence[RobotState],
        tasks: Sequence[FactoryTaskSpec],
    ) -> Tuple[np.ndarray, np.ndarray]:
        distances = np.zeros((len(robots), len(tasks)), dtype=float)
        feasible = np.ones((len(robots), len(tasks)), dtype=bool)
        capability_penalties = np.zeros_like(distances)

        for robot_index, robot in enumerate(robots):
            for task_index, task in enumerate(tasks):
                distances[robot_index, task_index] = math.dist(robot.position, task.target_xy)
                feasible[robot_index, task_index] = all(
                    float(robot.capabilities.get(capability, 0.0)) > 0.0
                    for capability in task.hard_capabilities
                )
                capability_penalties[robot_index, task_index] = self._capability_penalty(
                    robot.capabilities,
                    task.capability_requirements,
                )

        for task_index, task in enumerate(tasks):
            if not feasible[:, task_index].any():
                raise AllocationError(
                    f"No robot satisfies hard capabilities for task {task.task_id}"
                )

        max_distance = max(float(distances.max()), 1.0)
        normalized_distances = distances / max_distance
        loads = np.asarray([max(0, int(robot.current_load)) for robot in robots], dtype=float)
        cost = (
            self.distance_weight * normalized_distances
            + self.capability_weight * capability_penalties
            + self.load_weight * loads[:, np.newaxis]
        )
        robot_indices, task_indices = np.indices(cost.shape, dtype=float)
        tie_breaker = np.square(robot_indices - task_indices) * 1.0e-9
        cost = cost + tie_breaker
        cost[~feasible] = INFEASIBLE_COST
        return cost, feasible

    @staticmethod
    def _capability_penalty(
        capabilities: Mapping[str, float],
        requirements: Mapping[str, float],
    ) -> float:
        positive = {
            name: max(0.0, float(weight))
            for name, weight in requirements.items()
            if float(weight) > 0.0
        }
        total = sum(positive.values())
        if total <= 0.0:
            return 0.0
        missing = sum(
            weight * max(0.0, 1.0 - float(capabilities.get(name, 0.0)))
            for name, weight in positive.items()
        )
        return missing / total

    def _solve_default_miqp(
        self,
        cost_matrix: np.ndarray,
        feasible: np.ndarray,
        loads: np.ndarray,
    ) -> PairList:
        return _solve_gurobi(
            cost_matrix,
            feasible,
            loads,
            load_balance_weight=self.load_balance_weight,
        )

    @staticmethod
    def _solve_hungarian(
        cost_matrix: np.ndarray,
        feasible: np.ndarray,
        _loads: np.ndarray,
    ) -> PairList:
        from scipy.optimize import linear_sum_assignment

        rows, columns = linear_sum_assignment(cost_matrix)
        pairs = [(int(row), int(column)) for row, column in zip(rows, columns)]
        if any(not feasible[row, column] for row, column in pairs):
            raise AllocationError("Hungarian solver selected an infeasible assignment")
        return pairs

    @staticmethod
    def _build_result(
        robots: Sequence[RobotState],
        tasks: Sequence[FactoryTaskSpec],
        cost_matrix: np.ndarray,
        feasible: np.ndarray,
        pairs: Sequence[Tuple[int, int]],
        *,
        solver_name: str,
        fallback_reason: str | None = None,
    ) -> AllocationResult:
        normalized_pairs = [(int(row), int(column)) for row, column in pairs]
        rows = [row for row, _ in normalized_pairs]
        columns = [column for _, column in normalized_pairs]
        if len(normalized_pairs) != len(tasks) or len(set(rows)) != len(rows):
            raise AllocationError(f"{solver_name} did not assign every task to a unique robot")
        if set(columns) != set(range(len(tasks))):
            raise AllocationError(f"{solver_name} did not assign every task exactly once")
        for row, column in normalized_pairs:
            if row < 0 or row >= len(robots) or not feasible[row, column]:
                raise AllocationError(f"{solver_name} returned an infeasible assignment")

        assignments = [
            TaskAssignment(
                robot=robots[row],
                task=tasks[column],
                cost=float(cost_matrix[row, column]),
            )
            for row, column in sorted(normalized_pairs)
        ]
        return AllocationResult(
            assignments=assignments,
            solver=solver_name,
            total_cost=float(sum(item.cost for item in assignments)),
            fallback_reason=fallback_reason,
        )


def _solve_gurobi(
    cost_matrix: np.ndarray,
    feasible: np.ndarray,
    loads: np.ndarray,
    *,
    load_balance_weight: float,
) -> PairList:
    import gurobipy as gp

    robot_count, task_count = cost_matrix.shape
    model = gp.Model("teamweaver_factory_assignment")
    model.Params.OutputFlag = 0
    assignment = model.addVars(robot_count, task_count, vtype=gp.GRB.BINARY, name="x")

    for task_index in range(task_count):
        model.addConstr(
            gp.quicksum(assignment[robot_index, task_index] for robot_index in range(robot_count))
            == 1
        )
    for robot_index in range(robot_count):
        model.addConstr(
            gp.quicksum(assignment[robot_index, task_index] for task_index in range(task_count))
            <= 1
        )
        for task_index in range(task_count):
            if not feasible[robot_index, task_index]:
                model.addConstr(assignment[robot_index, task_index] == 0)

    linear_cost = gp.quicksum(
        float(cost_matrix[robot_index, task_index]) * assignment[robot_index, task_index]
        for robot_index in range(robot_count)
        for task_index in range(task_count)
    )
    load_cost = gp.QuadExpr()
    for robot_index in range(robot_count):
        assigned_load = gp.quicksum(
            assignment[robot_index, task_index] for task_index in range(task_count)
        )
        total_load = float(loads[robot_index]) + assigned_load
        load_cost += total_load * total_load
    model.setObjective(linear_cost + float(load_balance_weight) * load_cost, gp.GRB.MINIMIZE)
    model.optimize()
    if model.Status != gp.GRB.OPTIMAL:
        raise AllocationError(f"Gurobi assignment status is {model.Status}")

    return [
        (robot_index, task_index)
        for robot_index in range(robot_count)
        for task_index in range(task_count)
        if assignment[robot_index, task_index].X > 0.5
    ]
