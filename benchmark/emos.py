"""EMOS-to-benchmark decision adapter.

This module adapts the existing EMOS assignment boundary into the generic
benchmark runtime.  It deliberately stops at ``AlgorithmDecision``:
controller calls, skill execution, physical milestones and terminal outcomes
remain owned by benchmark/EAI adapters.

The default callback is EMOS's deterministic fallback assignment so
the contract can be tested without an LLM, credentials, Isaac Sim or ROS.
Callers may inject the native EMOS assignment callback when those services are
available and should record that provenance in the resulting decision.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
import math
from typing import Any, Callable, Mapping, Sequence

from algorithm.common_coordination.contract import AlgorithmCondition, Assignment, TaskGraph
from algorithm.heterocoord.fire_rescue_bridge import build_fire_rescue_records
from algorithm.system.coordination.emos.assignment import fallback_assignment
from algorithm.system.coordination.emos.benchmark_condition import condition_record
from algorithm.system.coordination.emos.policy import AssignmentRequest, assignment_tasks, validate_assignment_policy
from task.contracts import AlgorithmDecision, EaiTaskPackage, RobotState


AssignmentPolicy = Callable[[AssignmentRequest], Mapping[str, str]]
ModelEndpoint = Callable[[Mapping[str, object]], Mapping[str, str]]


@dataclass
class EaiTaskRoundRobinAdapter:
    """Minimal executable EMOS adapter for any typed task package.

    It is a deterministic baseline, not a paper score claim. Initialization
    consumes only public history/observation, then each pending task is mapped
    to the next active robot. The host still owns all physical execution.
    """

    algorithm_id: str = "EMOS-room06-baseline"
    plan_version: str = "emos-eai-task-round-robin-v1"
    _initialized: bool = False
    _history: Mapping[str, Any] = field(default_factory=dict)
    _observation: Mapping[str, Any] = field(default_factory=dict)

    def initialize(self, H_r: Mapping[str, Any], o_r: Mapping[str, Any]) -> None:
        if self._initialized:
            raise RuntimeError("EMOS typed adapter is already initialized")
        if not isinstance(H_r, Mapping) or not isinstance(o_r, Mapping):
            raise TypeError("H_r and o_r must be mappings")
        positions = o_r.get("robot_positions")
        if not isinstance(positions, Mapping) or not positions:
            raise ValueError("o_r.robot_positions is required")
        if any(not isinstance(key, str) for key in positions):
            raise ValueError("robot position IDs must be strings")
        for robot_id, position in positions.items():
            if not isinstance(position, (list, tuple)) or len(position) != 3:
                raise ValueError(f"o_r.robot_positions[{robot_id!r}] must be a 3-vector")
            if any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(value) for value in position):
                raise ValueError("robot positions must be finite numeric vectors")
        self._history = deepcopy(dict(H_r))
        self._observation = deepcopy(dict(o_r))
        self._initialized = True

    def __call__(self, task: EaiTaskPackage, robots: Sequence[RobotState]) -> AlgorithmDecision:
        if not self._initialized:
            raise RuntimeError("EMOS typed adapter requires initialize before decision")
        if {r.capability.robot_id for r in robots} != set(self._observation["robot_positions"]):
            raise ValueError("public observation roster differs from runtime roster")
        active = sorted(r.capability.robot_id for r in robots if r.capability.active)
        if not active:
            raise ValueError("no active robot in public roster")
        tasks = sorted(task.graph.tasks, key=lambda row: row.task_id)
        assignments = tuple(Assignment(row.task_id, active[i % len(active)], self.algorithm_id, self.plan_version)
                            for i, row in enumerate(tasks))
        return AlgorithmDecision(
            condition=AlgorithmCondition(self.algorithm_id, "benchmark.emos", self.plan_version,
                                         "eai_runtime", "round_robin", notes="diagnostic baseline"),
            assignments=assignments, provenance={
                "adapter": "benchmark.emos.EaiTaskRoundRobinAdapter",
                "initialized": True, "history_keys": sorted(self._history),
            })


@dataclass(frozen=True)
class CaseAssignmentRequest:
    """Explicit, scene-agnostic EMOS request for non-fire-rescue cases.

    The case adapter deliberately accepts task and robot identities supplied by
    a bound case package.  It never infers semantic-map entities, capabilities,
    or physical outcomes from a manifest digest.  This is the contract-level
    seam used by room_06/liv-t-04 until a task-specific EMOS policy is bound.
    """

    task_description: str
    scenario_id: str
    robot_positions: dict[str, tuple[float, float, float]]
    task_ids: tuple[str, ...]
    completed_task_ids: tuple[str, ...] = ()
    excluded_task_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.task_description).strip() or not str(self.scenario_id).strip():
            raise ValueError("case assignment request requires task_description and scenario_id")
        if not isinstance(self.robot_positions, Mapping) or not self.robot_positions:
            raise ValueError("case assignment request requires robot_positions")
        if not self.task_ids or len(set(self.task_ids)) != len(self.task_ids):
            raise ValueError("case assignment request task_ids must be unique and non-empty")
        if any(not isinstance(item, str) or not item.strip() for item in self.task_ids):
            raise ValueError("case assignment request task_ids must be non-empty strings")
        if set(self.completed_task_ids) - set(self.task_ids):
            raise ValueError("completed_task_ids contains an unknown task")
        if set(self.excluded_task_ids) - set(self.task_ids):
            raise ValueError("excluded_task_ids contains an unknown task")
        normalized = {}
        for robot_id, value in self.robot_positions.items():
            if not isinstance(robot_id, str) or not robot_id.strip() or not isinstance(value, (tuple, list)) or len(value) != 3:
                raise ValueError("case assignment robot positions must be 3-vectors")
            vector = tuple(float(item) for item in value)
            if not all(math.isfinite(item) for item in vector):
                raise ValueError("case assignment robot positions must be finite")
            normalized[robot_id] = vector
        object.__setattr__(self, "robot_positions", normalized)
        object.__setattr__(self, "task_ids", tuple(self.task_ids))
        object.__setattr__(self, "completed_task_ids", tuple(self.completed_task_ids))
        object.__setattr__(self, "excluded_task_ids", tuple(self.excluded_task_ids))


def case_round_robin_assign(request: CaseAssignmentRequest) -> dict[str, str]:
    """Deterministic contract-only fallback for a generic case.

    This is intentionally a dry-run policy, not a physical or paper result.
    It assigns each pending task to one available robot in stable order; a
    task-specific EMOS policy can replace it through ``assignment_policy``.
    """

    pending = [
        task_id for task_id in request.task_ids
        if task_id not in set(request.completed_task_ids)
        and task_id not in set(request.excluded_task_ids)
    ]
    robots = sorted(request.robot_positions)
    if not pending or not robots:
        raise ValueError("case fallback has no pending tasks or robots")
    return {robot_id: pending[index % len(pending)] for index, robot_id in enumerate(robots)}


CaseAssignmentPolicy = Callable[[CaseAssignmentRequest], Mapping[str, str]]


def emos_fallback_assign(request: AssignmentRequest) -> dict[str, str]:
    """Run EMOS's deterministic fallback without touching a live runtime."""

    assignments = fallback_assignment(
        request.scenario,
        dict(request.subtask_positions),
        sorted(request.robot_positions),
        robots_completed_extinguisher=set(request.completed_extinguisher),
        robots_completed_rescue=set(request.completed_rescue),
        sys_log=lambda _message: None,
        push_chat=lambda *_args: None,
        generate_report=lambda _assignments: None,
        excluded_subtask_ids=set(request.excluded_subtask_ids),
    )
    return {robot_id: robot_task.subtask_colour for robot_id, robot_task in assignments.items()}


def package_from_fire_rescue_request(
    request: AssignmentRequest,
    *,
    task_id: str | None = None,
    scene_id: str = "fire_rescue",
) -> EaiTaskPackage:
    """Translate an EMOS symbolic request to the generic task package.

    The returned package contains only task/robot contracts and public spawn
    observations.  It is not a physical scene and must not be treated as
    evaluator evidence.
    """

    capabilities, tasks = build_fire_rescue_records(request)
    capability_by_id = {item.robot_id: item for item in capabilities}
    robots = tuple(
        RobotState(
            capability=capability_by_id[robot_id],
            position=tuple(request.robot_positions[robot_id]),
            metadata={"position_source": request.position_source or "unknown"},
        )
        for robot_id in sorted(capability_by_id)
    )
    graph = TaskGraph(tasks=tasks, source=f"emos:{request.scenario.scenario_id}")
    return EaiTaskPackage(
        task_id=task_id or request.scenario.scenario_id,
        scene_id=scene_id,
        graph=graph,
        robots=robots,
        metadata={
            "algorithm_input": "emos.AssignmentRequest",
            "scenario_id": request.scenario.scenario_id,
            "position_source": request.position_source or "unknown",
            "subtask_positions": dict(request.subtask_positions),
            "completed_extinguisher": list(request.completed_extinguisher),
            "completed_rescue": list(request.completed_rescue),
        },
    )


@dataclass(frozen=True)
class EmosDecisionAdapter:
    """Expose EMOS as a benchmark decision provider with an explicit init seam.

    ``initialize`` is intentionally a data-only boundary.  It accepts the
    evaluator-owned history reconstruction and public observation, validates
    that the observed robot IDs exactly match the bound runtime roster, and
    optionally calls a metered model endpoint.  No method output is treated as
    a physical fact; benchmark/EAI still owns execution, oracle reduction and
    terminal receipts.

    ``initialization_budget_calls`` defaults to one so a new integration can
    use the paper contract immediately.  Existing callers that invoke the
    adapter directly remain backward compatible; setting ``require_initialize``
    (or providing a model endpoint) enables the strict production path.
    """

    request: AssignmentRequest
    policy: AssignmentPolicy = emos_fallback_assign
    condition: AlgorithmCondition | None = None
    plan_version: str = "emos-assignment-v1"
    provenance: Mapping[str, object] | None = None
    model_endpoint: ModelEndpoint | None = None
    initialization_budget_calls: int = 1
    require_initialize: bool = False
    _initialized: bool = field(default=False, init=False, repr=False)
    _initialization_calls: int = field(default=0, init=False, repr=False)
    _history: Mapping[str, object] = field(default_factory=dict, init=False, repr=False)
    _observation: Mapping[str, object] = field(default_factory=dict, init=False, repr=False)
    _model_mapping: Mapping[str, str] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy", validate_assignment_policy(self.policy))
        object.__setattr__(self, "condition", self.condition or condition_record(model_or_api="deterministic-emos-fallback"))
        object.__setattr__(self, "provenance", dict(self.provenance or {}))
        if type(self.initialization_budget_calls) is not int or self.initialization_budget_calls < 0:
            raise ValueError("initialization_budget_calls must be a non-negative integer")
        if self.model_endpoint is not None and not callable(self.model_endpoint):
            raise TypeError("model_endpoint must be callable")

    @staticmethod
    def _validate_observation(observation: Mapping[str, Any], expected_ids: set[str]) -> dict[str, Any]:
        if not isinstance(observation, Mapping):
            raise TypeError("o_r must be a mapping")
        positions = observation.get("robot_positions")
        if not isinstance(positions, Mapping):
            raise ValueError("o_r.robot_positions must be a mapping")
        observed_ids = set(positions)
        if observed_ids != expected_ids:
            raise ValueError(
                "EMOS initialization robot roster mismatch: "
                f"expected={sorted(expected_ids)}, observed={sorted(observed_ids)}"
            )
        normalized = dict(observation)
        normalized_positions: dict[str, tuple[float, float, float]] = {}
        for robot_id in sorted(expected_ids):
            value = positions[robot_id]
            if isinstance(value, (str, bytes)) or not isinstance(value, (tuple, list)) or len(value) != 3:
                raise ValueError(f"o_r.robot_positions[{robot_id!r}] must be a 3-vector")
            vector = tuple(float(item) for item in value)
            if not all(math.isfinite(item) for item in vector):
                raise ValueError(f"o_r.robot_positions[{robot_id!r}] must be finite")
            normalized_positions[robot_id] = vector
        normalized["robot_positions"] = normalized_positions
        return normalized

    def initialize(self, H_r: Mapping[str, Any], o_r: Mapping[str, Any]) -> AssignmentRequest:
        """Bind history/public observation before the first conditional allocation.

        The call is budgeted and idempotence is explicit: a second call is
        rejected rather than silently rebuilding history with a different
        roster.  ``H_r`` is copied as evaluator-owned history; only registered
        AssignmentRequest fields are allowed to alter the request.
        """

        if self._initialized:
            raise RuntimeError("EMOS adapter is already initialized")
        if self.initialization_budget_calls <= self._initialization_calls:
            raise RuntimeError("EMOS initialization budget exhausted")
        if not isinstance(H_r, Mapping):
            raise TypeError("H_r must be a mapping")
        expected_ids = set(self.request.robot_positions)
        observation = self._validate_observation(o_r, expected_ids)
        history = deepcopy(dict(H_r))
        request = self.request
        # History reconstruction may update only the fields that already belong
        # to AssignmentRequest; unknown keys remain opaque provenance.
        updates: dict[str, Any] = {"robot_positions": dict(observation["robot_positions"])}
        for key in (
            "subtask_positions", "completed_extinguisher", "completed_rescue",
            "rescue_constraints", "excluded_subtask_ids", "task_description",
        ):
            if key in history:
                updates[key] = deepcopy(history[key])
        try:
            request = replace(request, **updates)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"H_r contains invalid AssignmentRequest fields: {exc}") from exc
        object.__setattr__(self, "request", request)
        object.__setattr__(self, "_history", history)
        object.__setattr__(self, "_observation", observation)
        object.__setattr__(self, "_initialization_calls", self._initialization_calls + 1)
        if self.model_endpoint is not None:
            payload = {
                "history": deepcopy(history),
                "observation": deepcopy(observation),
                "scenario_id": request.scenario.scenario_id,
                "robot_ids": sorted(expected_ids),
            }
            try:
                mapping = self.model_endpoint(payload)
            except Exception as exc:  # endpoint failures are fail-closed
                raise RuntimeError("EMOS model endpoint failed during initialization") from exc
            if not isinstance(mapping, Mapping) or any(
                not isinstance(robot_id, str) or not isinstance(task_id, str)
                for robot_id, task_id in mapping.items()
            ):
                raise TypeError("EMOS model endpoint must return a robot-to-subtask mapping")
            unknown = set(mapping) - expected_ids
            if unknown:
                raise ValueError(f"EMOS model endpoint returned unknown robots: {sorted(unknown)}")
            object.__setattr__(self, "_model_mapping", dict(mapping))
        object.__setattr__(self, "_initialized", True)
        return request

    def update_observation(self, o_r: Mapping[str, Any]) -> AssignmentRequest:
        """Update public positions for a later conditional allocation round."""

        if not self._initialized:
            raise RuntimeError("EMOS adapter must be initialized before update_observation")
        observation = self._validate_observation(o_r, set(self.request.robot_positions))
        request = replace(self.request, robot_positions=dict(observation["robot_positions"]))
        object.__setattr__(self, "request", request)
        object.__setattr__(self, "_observation", observation)
        object.__setattr__(self, "_model_mapping", None)
        return request

    def __call__(self, task: EaiTaskPackage, robots: tuple[RobotState, ...]) -> AlgorithmDecision:
        """Run assignment only and convert the mapping to typed records."""

        runtime_robot_ids = {item.capability.robot_id for item in robots}
        request_robot_ids = set(self.request.robot_positions)
        if runtime_robot_ids != request_robot_ids:
            raise ValueError(
                "EMOS request/runtime roster mismatch: "
                f"request={sorted(request_robot_ids)}, runtime={sorted(runtime_robot_ids)}"
            )
        if (self.require_initialize or self.model_endpoint is not None) and not self._initialized:
            raise RuntimeError("EMOS adapter must be initialized before conditional allocation")
        mapping = self._model_mapping if self._model_mapping is not None else self.policy(self.request)
        if not isinstance(mapping, Mapping):
            raise TypeError("EMOS assignment policy must return a robot-to-subtask mapping")
        assignment_tasks(self.request, mapping)  # preserve EMOS-specific validation

        task_ids = {item.task_id for item in task.graph.tasks}
        robot_ids = {item.capability.robot_id for item in task.robots if item.capability.active}
        unknown_tasks = set(mapping.values()) - task_ids
        unknown_robots = set(mapping) - robot_ids
        if unknown_tasks or unknown_robots:
            raise ValueError(
                f"EMOS mapping does not match task package: unknown_tasks={sorted(unknown_tasks)}, "
                f"unknown_robots={sorted(unknown_robots)}"
            )

        assignments = tuple(
            Assignment(
                task_id=str(task_id),
                robot_id=str(robot_id),
                source=self.condition.algorithm_id,
                plan_version=self.plan_version,
            )
            for robot_id, task_id in sorted(mapping.items())
        )
        assigned = {item.task_id for item in assignments}
        provenance = {
            "adapter": "benchmark.emos.EmosDecisionAdapter",
            "policy": f"{self.policy.__module__}:{self.policy.__name__}",
            "scenario_id": self.request.scenario.scenario_id,
            "position_source": self.request.position_source or "unknown",
            "initialized": self._initialized,
            "initialization_calls": self._initialization_calls,
            "model_endpoint": self.model_endpoint is not None,
            **dict(self.provenance or {}),
        }
        return AlgorithmDecision(
            condition=self.condition,
            assignments=assignments,
            unassigned_tasks=tuple(sorted(task_ids - assigned)),
            provenance=provenance,
        )


@dataclass(frozen=True)
class EmosCaseDecisionAdapter:
    """Generic case-level EMOS seam, independent of the fire-rescue bridge.

    A caller supplies an explicit :class:`CaseAssignmentRequest` and a generic
    :class:`EaiTaskPackage`.  ``initialize(H_r, o_r)`` applies only the
    registered history fields and exact roster observation; the adapter emits
    typed assignments and provenance, never evaluator facts.  The default
    policy is deliberately diagnostic and deterministic until a task-specific
    EMOS policy is bound.
    """

    request: CaseAssignmentRequest
    policy: CaseAssignmentPolicy = case_round_robin_assign
    condition: AlgorithmCondition | None = None
    plan_version: str = "emos-case-dry-run-v1"
    provenance: Mapping[str, object] | None = None
    model_endpoint: ModelEndpoint | None = None
    initialization_budget_calls: int = 1
    require_initialize: bool = False
    _initialized: bool = field(default=False, init=False, repr=False)
    _initialization_calls: int = field(default=0, init=False, repr=False)
    _history: Mapping[str, object] = field(default_factory=dict, init=False, repr=False)
    _observation: Mapping[str, object] = field(default_factory=dict, init=False, repr=False)
    _model_mapping: Mapping[str, str] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not callable(self.policy):
            raise TypeError("case EMOS policy must be callable")
        object.__setattr__(self, "condition", self.condition or AlgorithmCondition(
            algorithm_id="EMOS-case-adapter",
            source_reference="benchmark.emos",
            implementation="contract-only-case-adapter",
            execution_scope="benchmark_case",
            planner="emos",
            model_or_api="deterministic-case-fallback",
        ))
        object.__setattr__(self, "provenance", dict(self.provenance or {}))
        if type(self.initialization_budget_calls) is not int or self.initialization_budget_calls < 0:
            raise ValueError("initialization_budget_calls must be a non-negative integer")
        if self.model_endpoint is not None and not callable(self.model_endpoint):
            raise TypeError("model_endpoint must be callable")

    def initialize(self, H_r: Mapping[str, Any], o_r: Mapping[str, Any]) -> CaseAssignmentRequest:
        if self._initialized:
            raise RuntimeError("EMOS case adapter is already initialized")
        if self.initialization_budget_calls <= self._initialization_calls:
            raise RuntimeError("EMOS case initialization budget exhausted")
        if not isinstance(H_r, Mapping):
            raise TypeError("H_r must be a mapping")
        observation = EmosDecisionAdapter._validate_observation(o_r, set(self.request.robot_positions))
        request = self.request
        updates: dict[str, Any] = {"robot_positions": dict(observation["robot_positions"])}
        for key in ("task_ids", "completed_task_ids", "excluded_task_ids", "task_description"):
            if key in H_r:
                updates[key] = tuple(H_r[key]) if key.endswith("_ids") else H_r[key]
        try:
            request = replace(request, **updates)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"H_r contains invalid CaseAssignmentRequest fields: {exc}") from exc
        object.__setattr__(self, "request", request)
        object.__setattr__(self, "_history", deepcopy(dict(H_r)))
        object.__setattr__(self, "_observation", observation)
        object.__setattr__(self, "_initialization_calls", self._initialization_calls + 1)
        if self.model_endpoint is not None:
            payload = {
                "history": deepcopy(dict(H_r)),
                "observation": deepcopy(observation),
                "scenario_id": request.scenario_id,
                "robot_ids": sorted(request.robot_positions),
            }
            try:
                mapping = self.model_endpoint(payload)
            except Exception as exc:
                raise RuntimeError("EMOS case model endpoint failed during initialization") from exc
            if not isinstance(mapping, Mapping) or any(
                not isinstance(robot_id, str) or not isinstance(task_id, str)
                for robot_id, task_id in mapping.items()
            ):
                raise TypeError("EMOS case model endpoint must return a robot-to-task mapping")
            unknown = set(mapping) - set(request.robot_positions)
            unknown_tasks = set(mapping.values()) - set(request.task_ids)
            if unknown or unknown_tasks:
                raise ValueError(
                    f"EMOS case model endpoint returned unknown IDs: robots={sorted(unknown)}, tasks={sorted(unknown_tasks)}"
                )
            object.__setattr__(self, "_model_mapping", dict(mapping))
        object.__setattr__(self, "_initialized", True)
        return request

    def __call__(self, task: EaiTaskPackage, robots: tuple[RobotState, ...]) -> AlgorithmDecision:
        runtime_robot_ids = {item.capability.robot_id for item in robots}
        request_robot_ids = set(self.request.robot_positions)
        if runtime_robot_ids != request_robot_ids:
            raise ValueError(
                "EMOS case request/runtime roster mismatch: "
                f"request={sorted(request_robot_ids)}, runtime={sorted(runtime_robot_ids)}"
            )
        if task.task_id != self.request.scenario_id and task.metadata.get("scenario_id") != self.request.scenario_id:
            raise ValueError("EMOS case task package scenario does not match request")
        if (self.require_initialize or self.model_endpoint is not None) and not self._initialized:
            raise RuntimeError("EMOS case adapter must be initialized before conditional allocation")
        mapping = self._model_mapping if self._model_mapping is not None else self.policy(self.request)
        if not isinstance(mapping, Mapping):
            raise TypeError("EMOS case assignment policy must return a robot-to-task mapping")
        task_ids = {item.task_id for item in task.graph.tasks}
        unknown_tasks = set(mapping.values()) - task_ids
        unknown_robots = set(mapping) - runtime_robot_ids
        if unknown_tasks or unknown_robots:
            raise ValueError(
                f"EMOS case mapping does not match task package: unknown_tasks={sorted(unknown_tasks)}, "
                f"unknown_robots={sorted(unknown_robots)}"
            )
        assignments = tuple(
            Assignment(task_id=str(task_id), robot_id=str(robot_id), source=self.condition.algorithm_id, plan_version=self.plan_version)
            for robot_id, task_id in sorted(mapping.items())
        )
        assigned = {item.task_id for item in assignments}
        provenance = {
            "adapter": "benchmark.emos.EmosCaseDecisionAdapter",
            "policy": f"{self.policy.__module__}:{self.policy.__name__}",
            "scenario_id": self.request.scenario_id,
            "initialized": self._initialized,
            "initialization_calls": self._initialization_calls,
            "model_endpoint": self.model_endpoint is not None,
            "physical_success": False,
            **dict(self.provenance or {}),
        }
        return AlgorithmDecision(
            condition=self.condition,
            assignments=assignments,
            unassigned_tasks=tuple(sorted(task_ids - assigned)),
            provenance=provenance,
        )


__all__ = [
    "AssignmentPolicy",
    "ModelEndpoint",
    "CaseAssignmentRequest",
    "CaseAssignmentPolicy",
    "case_round_robin_assign",
    "EmosDecisionAdapter",
    "EmosCaseDecisionAdapter",
    "EaiTaskRoundRobinAdapter",
    "emos_fallback_assign",
    "package_from_fire_rescue_request",
]
