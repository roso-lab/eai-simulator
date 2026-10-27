"""TeamWeaver contract adapter.

This module is deliberately an I/O boundary.  It translates the benchmark
contracts to TeamWeaver's existing ``DynamicFactoryAllocator`` records and
translates its ``AllocationResult`` back to ``AlgorithmDecision``.  No
TeamWeaver implementation is modified here.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import math
from typing import Any, Mapping, Sequence

from algorithm.common_coordination.contract import AlgorithmCondition, Assignment
from algorithm.system.coordination.TeamWeaver.eai_adapter.dynamic_allocator import (
    AllocationResult,
    TaskAssignment,
    DynamicFactoryAllocator,
    RobotState as TeamWeaverRobotState,
)
from algorithm.system.coordination.TeamWeaver.eai_adapter.factory_tasks import FactoryTaskSpec
from task.contracts import AlgorithmDecision, EaiTaskPackage, RobotState


@dataclass(frozen=True)
class TeamWeaverDecisionAdapter:
    """Expose TeamWeaver through the benchmark ``DecisionProvider`` contract.

    ``initialize`` validates the evaluator roster and stores opaque history and
    public observations. Allocation is blocked until initialization.
    """

    allocator: Any = None
    robot_ids: tuple[str, ...] = ()
    condition: AlgorithmCondition | None = None
    plan_version: str = "teamweaver-contract-v1"
    provenance: Mapping[str, object] | None = None
    initialization_budget_calls: int = 1
    _initialized: bool = field(default=False, init=False, repr=False)
    _initialization_calls: int = field(default=0, init=False, repr=False)
    _history: Mapping[str, object] = field(default_factory=dict, init=False, repr=False)
    _observation: Mapping[str, object] = field(default_factory=dict, init=False, repr=False)
    _robot_ids: frozenset[str] = field(default_factory=frozenset, init=False, repr=False)
    _completed_task_ids: frozenset[str] = field(default_factory=frozenset, init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "allocator", self.allocator or DynamicFactoryAllocator(prefer_miqp=True))
        object.__setattr__(self, "condition", self.condition or AlgorithmCondition(
            algorithm_id="TeamWeaver-contract-adapter",
            source_reference="algorithm/system/coordination/TeamWeaver",
            implementation="contract-io-adapter",
            execution_scope="allocation_only",
            planner="TeamWeaver.DynamicFactoryAllocator",
            model_or_api="deterministic-native-allocator",
            supported_primitives=("task_graph_allocation", "capability_matching"),
            unsupported_dimensions=("physical_execution", "physical_success", "unmapped_task_fields"),
            physical_executor_owner="eai_runtime",
            notes="Edgeless allocation only; nonempty history and dependency graphs await a mapping ruling.",
        ))
        object.__setattr__(self, "provenance", dict(self.provenance or {}))
        if not self.robot_ids or len(set(self.robot_ids)) != len(self.robot_ids) or any(not isinstance(item, str) or not item.strip() for item in self.robot_ids):
            raise ValueError("robot_ids must be unique non-empty strings")
        if not isinstance(self.initialization_budget_calls, int) or isinstance(self.initialization_budget_calls, bool) or self.initialization_budget_calls < 0:
            raise ValueError("initialization_budget_calls must be a non-negative integer")
        if not callable(getattr(self.allocator, "allocate", None)):
            raise TypeError("allocator must provide allocate(robots, tasks)")

    @staticmethod
    def _validate_observation(observation: Mapping[str, Any], expected_ids: set[str]) -> dict[str, Any]:
        if not isinstance(observation, Mapping):
            raise TypeError("o_r must be a mapping")
        positions = observation.get("robot_positions")
        if not isinstance(positions, Mapping) or set(positions) != expected_ids:
            raise ValueError("TeamWeaver initialization robot_positions roster mismatch")
        normalized: dict[str, tuple[float, float, float]] = {}
        for robot_id in sorted(expected_ids):
            value = positions[robot_id]
            if isinstance(value, (str, bytes)) or not isinstance(value, (tuple, list)) or len(value) != 3:
                raise ValueError(f"o_r.robot_positions[{robot_id!r}] must be a 3-vector")
            vector = tuple(float(item) for item in value)
            if not all(math.isfinite(item) for item in vector):
                raise ValueError(f"o_r.robot_positions[{robot_id!r}] must be finite")
            normalized[robot_id] = vector
        result = dict(observation)
        result["robot_positions"] = normalized
        return result

    def initialize(self, H_r: Mapping[str, Any], o_r: Mapping[str, Any]) -> Mapping[str, Any]:
        if self._initialized:
            raise RuntimeError("TeamWeaver adapter is already initialized")
        if self.initialization_budget_calls <= self._initialization_calls:
            raise RuntimeError("TeamWeaver initialization budget exhausted")
        object.__setattr__(self, "_initialization_calls", self._initialization_calls + 1)
        if not isinstance(H_r, Mapping):
            raise TypeError("H_r must be a mapping")
        expected = set(self.robot_ids)
        if not expected:
            raise ValueError("TeamWeaver initialization requires a bound robot roster")
        observation = self._validate_observation(o_r, expected)
        history = deepcopy(dict(H_r))
        if history:
            raise ValueError("non-empty H_r has no native FactoryAllocator reconstruction mapping; awaiting ruling")
        completed = history.get("completed_task_ids", ())
        if isinstance(completed, (str, bytes)) or not isinstance(completed, Sequence):
            raise ValueError("H_r.completed_task_ids must be a sequence")
        object.__setattr__(self, "_robot_ids", frozenset(expected))
        object.__setattr__(self, "_completed_task_ids", frozenset(str(item) for item in completed))
        object.__setattr__(self, "_history", history)
        object.__setattr__(self, "_observation", observation)
        object.__setattr__(self, "_initialized", True)
        return history

    @staticmethod
    def _target_xy(package: EaiTaskPackage, task: Any) -> tuple[float, float]:
        candidates = [task.metadata.get("target_xy"), task.metadata.get("target_position")]
        positions = package.metadata.get("target_positions", {})
        if isinstance(positions, Mapping):
            candidates.extend((positions.get(task.task_id), positions.get(task.target_ref)))
        for value in candidates:
            if isinstance(value, (tuple, list)) and len(value) in (2, 3):
                vector = tuple(float(item) for item in value[:2])
                if all(math.isfinite(item) for item in vector):
                    return vector
        raise ValueError(f"task {task.task_id!r} has no finite target position mapping")

    def __call__(self, task: EaiTaskPackage, robots: Sequence[RobotState]) -> AlgorithmDecision:
        if not self._initialized:
            raise RuntimeError("TeamWeaver adapter must be initialized before conditional allocation")
        if not isinstance(task, EaiTaskPackage) or not task.graph.valid:
            raise ValueError("TeamWeaver requires a valid EaiTaskPackage")
        runtime_ids = {item.capability.robot_id for item in robots}
        package_ids = {item.capability.robot_id for item in task.robots}
        expected = set(self._robot_ids) if self._robot_ids else package_ids
        if len(robots) != len(runtime_ids) or runtime_ids != package_ids or runtime_ids != expected:
            raise ValueError("TeamWeaver runtime/package robot roster mismatch")
        if any(item.prerequisites for item in task.graph.tasks):
            raise ValueError("prerequisite edges have no FactoryTaskSpec mapping; awaiting ruling")
        active = [item for item in robots if item.capability.active]
        completed = set(self._completed_task_ids)
        task_by_id = {item.task_id: item for item in task.graph.tasks}
        ready = [item for item in task.graph.tasks if item.task_id not in completed and not (set(item.prerequisites) - completed)]
        pending_ids = set(task_by_id) - completed
        if not ready:
            return AlgorithmDecision(condition=self.condition, unassigned_tasks=tuple(sorted(pending_ids)), provenance=self._provenance(task, "none", ()))
        tw_robots = []
        for item in active:
            position = self._observation["robot_positions"][item.capability.robot_id]
            if position is None or len(position) != 3 or not all(math.isfinite(float(v)) for v in position):
                raise ValueError(f"robot {item.capability.robot_id!r} has no finite position")
            capabilities = {name: 1.0 for name in item.capability.capabilities}
            load = item.metadata.get("current_load", 0)
            if isinstance(load, bool) or not isinstance(load, int) or load < 0:
                raise ValueError("robot current_load must be a non-negative integer")
            tw_robots.append(TeamWeaverRobotState(item.capability.robot_id, (float(position[0]), float(position[1])), capabilities, load))
        tw_tasks = []
        for item in ready:
            target = self._target_xy(task, item)
            requirements = item.metadata.get("capability_requirements", {})
            if not isinstance(requirements, Mapping):
                raise ValueError(f"task {item.task_id!r} capability_requirements must be a mapping")
            req = {str(k): float(v) for k, v in requirements.items()}
            if any(not math.isfinite(v) or v < 0 for v in req.values()):
                raise ValueError("capability requirements must be finite and non-negative")
            for capability in item.required_capabilities:
                req.setdefault(capability, 1.0)
            tw_tasks.append(FactoryTaskSpec(item.task_id, item.description, item.target_ref or item.task_id, target, req, tuple(sorted(item.required_capabilities))))
        try:
            result = self.allocator.allocate(tw_robots, tw_tasks)
        except Exception as exc:
            raise RuntimeError("TeamWeaver allocation failed; decision rejected") from exc
        if not isinstance(result, AllocationResult):
            raise TypeError("TeamWeaver must return AllocationResult")
        if not result.assignments:
            raise RuntimeError("TeamWeaver returned no assignments")
        native_robots = {r.name: r for r in tw_robots}
        native_tasks = {t.task_id: t for t in tw_tasks}
        seen_robots, seen_tasks = set(), set()
        for item in result.assignments:
            if not isinstance(item, TaskAssignment):
                raise TypeError("malformed TaskAssignment")
            if (not isinstance(item.robot, TeamWeaverRobotState)
                    or not isinstance(item.task, FactoryTaskSpec)
                    or native_robots.get(item.robot.name) != item.robot
                    or native_tasks.get(item.task.task_id) != item.task
                    or item.robot.name in seen_robots or item.task.task_id in seen_tasks
                    or not math.isfinite(item.cost)):
                raise ValueError("malformed or foreign allocation assignment")
            seen_robots.add(item.robot.name)
            seen_tasks.add(item.task.task_id)
        if (seen_tasks != set(native_tasks) or not math.isfinite(result.total_cost)
                or result.solver not in ("miqp", "hungarian")):
            raise ValueError("malformed or incomplete allocation result")
        assignments = tuple(Assignment(item.task.task_id, item.robot.name, self.condition.algorithm_id, self.plan_version) for item in result.assignments)
        assigned = {item.task_id for item in assignments}
        return AlgorithmDecision(condition=self.condition, assignments=assignments, unassigned_tasks=tuple(sorted(pending_ids - assigned)), provenance=self._provenance(task, result.solver, tuple(item.task_id for item in ready), fallback_reason=result.fallback_reason))

    def _provenance(self, task: EaiTaskPackage, solver: str, ready: Sequence[str], **extra: object) -> dict[str, object]:
        return {"adapter": "benchmark.teamweaver.TeamWeaverDecisionAdapter", "initialized": self._initialized, "initialization_calls": self._initialization_calls, "solver": solver, "ready_task_ids": list(ready), "discarded_fields": ["robot.position.z", "graph.source", "observation fields except robot_positions", "opaque package/task/robot metadata except documented mapping keys"], "history_scope": "empty_history_only", **dict(self.provenance or {}), **extra}


__all__ = ["TeamWeaverDecisionAdapter"]
