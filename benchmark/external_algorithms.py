"""Historical diagnostic DecisionProviders, not paper benchmark algorithms.

This facade contains substitute allocators and scenario defaults. It is kept
for compatibility with diagnostic fixtures only. Production paper conditions
must use benchmark.native_algorithms.build_native_adapter, which calls the
registered original implementation and never falls back to this module.

The existing EMOS adapter established the benchmark boundary used here:
an external algorithm receives the frozen symbolic Fire Rescue request and
returns an :class:`task.contracts.AlgorithmDecision`. It does not execute
robot controls or infer physical success. This module keeps that boundary for
the classical and learned candidates pulled into this workspace and adds an
explicit feedback/replan callback for algorithms that expose one.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from algorithm.common_coordination.contract import (
    AlgorithmCondition,
    Assignment,
    ExecutionFeedback,
    FailureCode,
)
from algorithm.heterocoord.fire_rescue_bridge import (
    BridgePlan,
    FireRescueCoordinationBridge,
    PlanStatus,
)
from algorithm.system.coordination.emos.policy import AssignmentRequest
from benchmark.emos import package_from_fire_rescue_request
from task.contracts import AlgorithmDecision, EaiTaskPackage, RobotState
from task.runner import OrchestrationState


# HeteroMRTA is intentionally absent. Its old registry records are retained
# for provenance, but it is not a condition in this adaptation matrix.
EXTERNAL_FIRE_RESCUE_ALGORITHMS: tuple[str, ...] = (
    "fmc_ata",
    "bt_capabilities",
    "coherent",
    "sadcher",
    "robot_fleet",
    "lammap",
    "harl_happo",
)


def _algorithm_key(specification: str) -> str:
    value = str(specification or "").strip().lower()
    if "fmc_ata" in value or value in {"fmc-ata", "fmc_ata"}:
        return "fmc_ata"
    if "bt_capabilities" in value or "bt-capabilities" in value or value in {"bt", "bt_capabilities"}:
        return "bt_capabilities"
    if "coherent" in value:
        return "coherent"
    if "sadcher" in value:
        return "sadcher"
    if "robot_fleet" in value or "robot-fleet" in value or "robotfleet" in value:
        return "robot_fleet"
    if "lamma" in value or "lamma-p" in value or "lamma_p" in value:
        return "lammap"
    if "harl" in value or "happo" in value:
        return "harl_happo"
    raise ValueError(
        f"unsupported external Fire Rescue algorithm {specification!r}; "
        f"choose one of {', '.join(EXTERNAL_FIRE_RESCUE_ALGORITHMS)}"
    )


def _decision_from_plan(
    plan: Any,
    *,
    task: EaiTaskPackage,
    condition: AlgorithmCondition,
    adapter_name: str,
    pending_task_ids: Sequence[str] | None = None,
) -> AlgorithmDecision:
    allowed = set(pending_task_ids) if pending_task_ids is not None else {
        item.task_id for item in task.graph.tasks
    }
    active_robot_ids = {
        robot.capability.robot_id for robot in task.robots if robot.capability.active
    }
    assignments = tuple(
        item
        for item in plan.assignments
        if item.task_id in allowed and item.robot_id in active_robot_ids
    )
    assigned = {item.task_id for item in assignments}
    unassigned = tuple(sorted(allowed - assigned))
    provenance = {
        "benchmark_eligible": False,
        "evidence_scope": "legacy_diagnostic_adapter",
        "adapter": adapter_name,
        "algorithm_key": plan.algorithm_id,
        "plan_status": plan.status.value,
        "allocation_provider": plan.allocation_provider,
        "reason": plan.reason,
        "condition_manifest": condition.to_dict(),
    }
    return AlgorithmDecision(
        condition=condition,
        assignments=assignments,
        unassigned_tasks=unassigned,
        provenance=provenance,
    )


def _request_for_pending(
    request: AssignmentRequest,
    pending_task_ids: Sequence[str],
) -> AssignmentRequest:
    pending = set(str(item) for item in pending_task_ids)
    excluded = set(request.excluded_subtask_ids)
    excluded.update(task_id for task_id in request.scenario.subtasks if task_id not in pending)
    return replace(request, excluded_subtask_ids=tuple(sorted(excluded)))


def _mapping_plan(
    *,
    key: str,
    request: AssignmentRequest,
    task_id: str,
    pending_task_ids: Sequence[str] | None = None,
    previous: Mapping[str, str] | None = None,
    feedback: Sequence[ExecutionFeedback] = (),
) -> BridgePlan:
    """Run the bridge or an algorithm-specific feedback allocator."""

    effective_request = (
        request
        if pending_task_ids is None
        else _request_for_pending(request, pending_task_ids)
    )
    if key in {"robot_fleet", "lammap"} and pending_task_ids is not None:
        failed_robot_ids = {
            item.robot_id
            for item in feedback
            if item.failure_code
            in {
                FailureCode.CAPABILITY_LOSS,
                FailureCode.MEMBER_EXIT,
                FailureCode.SAFETY_VIOLATION,
            }
        }
        module_name = (
            "algorithm.system.coordination.robot_fleet.fire_rescue_policy"
            if key == "robot_fleet"
            else "algorithm.system.coordination.lammap.fire_rescue_policy"
        )
        module = __import__(module_name, fromlist=["repair"])
        pending = set(pending_task_ids)
        mapping = module.repair(
            effective_request,
            {
                str(robot_id): str(task_name)
                for robot_id, task_name in dict(previous or {}).items()
                if task_name in pending
            },
            failed_robot_ids=failed_robot_ids,
            delayed_task_ids=tuple(pending_task_ids),
        )
        bridge = FireRescueCoordinationBridge(key, task_id=task_id)
        assignments = tuple(
            Assignment(
                task_id=str(task_name),
                robot_id=str(robot_id),
                source=bridge.condition.algorithm_id,
                plan_version=f"{key}-feedback-r1",
            )
            for robot_id, task_name in sorted(mapping.items())
        )
        return BridgePlan(
            algorithm_id=bridge.condition.algorithm_id,
            status=PlanStatus.PLANNED,
            assignments=assignments,
            mapping=dict(mapping),
            reason="feedback-driven pending-task reallocation",
            allocation_provider=f"{key}_feedback_reallocation",
        )
    return FireRescueCoordinationBridge(key, task_id=task_id).plan(effective_request)


@dataclass
class ExternalAlgorithmDecisionAdapter:
    """Expose one pulled Fire Rescue algorithm as a typed decision provider."""

    request: AssignmentRequest
    algorithm: str
    plan_version: str = "external-fire-rescue-v1"
    provenance: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        self.key = _algorithm_key(self.algorithm)
        self._bridge = FireRescueCoordinationBridge(
            self.key,
            task_id=self.request.scenario.scenario_id,
        )
        self.condition = self._bridge.condition
        self.provenance = dict(self.provenance or {})
        self.last_plan: BridgePlan | None = None

    @classmethod
    def from_package(
        cls,
        request: AssignmentRequest,
        algorithm: str,
        **kwargs: object,
    ) -> "ExternalAlgorithmDecisionAdapter":
        return cls(request=request, algorithm=algorithm, **kwargs)

    def __call__(
        self,
        task: EaiTaskPackage,
        robots: Sequence[RobotState],
    ) -> AlgorithmDecision:
        self._validate_roster(task, robots)
        plan = self._bridge.plan(self.request)
        self.last_plan = plan
        return self._to_decision(task, plan)

    def replan(
        self,
        task: EaiTaskPackage,
        robots: Sequence[RobotState],
        state: OrchestrationState,
    ) -> AlgorithmDecision | None:
        """Recompute only pending work after explicit execution feedback."""

        self._validate_roster(task, robots)
        pending = tuple(state.pending_task_ids)
        if not pending:
            return AlgorithmDecision(
                condition=self.condition,
                assignments=(),
                unassigned_tasks=(),
                provenance={
                    "adapter": type(self).__name__,
                    "replan": "no_pending_tasks",
                },
            )
        previous = {
            item.robot_id: item.task_id for item in state.last_decision.assignments
        }
        plan = _mapping_plan(
            key=self.key,
            request=self.request,
            task_id=task.task_id,
            pending_task_ids=pending,
            previous=previous,
            feedback=state.feedback,
        )
        self.last_plan = plan
        return self._to_decision(task, plan, pending_task_ids=pending, replan=True)

    def _to_decision(
        self,
        task: EaiTaskPackage,
        plan: BridgePlan,
        *,
        pending_task_ids: Sequence[str] | None = None,
        replan: bool = False,
    ) -> AlgorithmDecision:
        decision = _decision_from_plan(
            plan,
            task=task,
            condition=self.condition,
            adapter_name="benchmark.external_algorithms.ExternalAlgorithmDecisionAdapter",
            pending_task_ids=pending_task_ids,
        )
        provenance = {
            **dict(decision.provenance),
            "request_scenario_id": self.request.scenario.scenario_id,
            "position_source": self.request.position_source or "unknown",
            "replan": bool(replan),
            **dict(self.provenance),
        }
        return AlgorithmDecision(
            condition=decision.condition,
            assignments=tuple(
                Assignment(
                    item.task_id,
                    item.robot_id,
                    item.source,
                    item.plan_version or self.plan_version,
                )
                for item in decision.assignments
            ),
            unassigned_tasks=decision.unassigned_tasks,
            provenance=provenance,
        )

    def _validate_roster(
        self,
        task: EaiTaskPackage,
        robots: Sequence[RobotState],
    ) -> None:
        expected = set(self.request.robot_positions)
        actual = {item.capability.robot_id for item in robots}
        if expected != actual:
            raise ValueError(
                f"{self.key} request/runtime roster mismatch: "
                f"request={sorted(expected)}, runtime={sorted(actual)}"
            )
        package_ids = {item.task_id for item in task.graph.tasks}
        open_ids = set(self.request.scenario.subtasks) - set(self.request.excluded_subtask_ids)
        if not open_ids <= package_ids:
            raise ValueError("task package does not contain the request's open subtasks")


def package_for_external_algorithm(
    request: AssignmentRequest,
    *,
    task_id: str | None = None,
    scene_id: str = "fire_rescue",
) -> EaiTaskPackage:
    """Build the common task package used by all external providers."""

    return package_from_fire_rescue_request(
        request,
        task_id=task_id,
        scene_id=scene_id,
    )


def build_external_algorithm_adapter(
    request: AssignmentRequest,
    algorithm: str,
    **kwargs: object,
) -> ExternalAlgorithmDecisionAdapter:
    return ExternalAlgorithmDecisionAdapter(request, algorithm, **kwargs)


__all__ = [
    "EXTERNAL_FIRE_RESCUE_ALGORITHMS",
    "ExternalAlgorithmDecisionAdapter",
    "build_external_algorithm_adapter",
    "package_for_external_algorithm",
]
