"""Typed Fire Rescue boundary for the HeteroCoord algorithm conditions.

The three conditions in this module are planners/allocators.  They do not own
Isaac Sim actions or mission success.  A :class:`FireRescueCoordinationBridge`
keeps that distinction explicit by recording algorithm output separately from
EAI execution feedback and evaluator-owned physical milestones.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

from algorithm.common_coordination import (
    AlgorithmCondition,
    Assignment,
    CoordinationTask,
    EventLedger,
    ExecutionFeedback,
    FailureCode,
    MilestoneEvidence,
    RobotCapability,
    RunManifest,
)


class PlanStatus(str, Enum):
    PLANNED = "planned"
    NOT_APPLICABLE = "not_applicable"
    FAILED = "failed"


@dataclass(frozen=True)
class BridgePlan:
    algorithm_id: str
    status: PlanStatus
    assignments: tuple[Assignment, ...] = ()
    mapping: Mapping[str, str] | None = None
    fractional_allocations: tuple[Mapping[str, object], ...] = ()
    reason: str = ""
    allocation_provider: str = "algorithm"

    def to_dict(self) -> dict[str, object]:
        return {
            "algorithm_id": self.algorithm_id,
            "status": self.status.value,
            "assignments": [asdict(item) for item in self.assignments],
            "mapping": dict(self.mapping or {}),
            "fractional_allocations": [dict(item) for item in self.fractional_allocations],
            "reason": self.reason,
            "allocation_provider": self.allocation_provider,
        }


def _algorithm_key(specification: str) -> str:
    value = str(specification or "").strip().lower()
    if "fmc_ata" in value or value in {"fmc-ata", "fmc_ata"}:
        return "fmc_ata"
    if "bt_capabilities" in value or "bt-capabilities" in value or value in {"bt", "bt_capabilities"}:
        return "bt_capabilities"
    if "coherent" in value:
        return "coherent"
    if "teamweaver" in value:
        return "teamweaver"
    if "sadcher" in value:
        return "sadcher"
    if "robot_fleet" in value or "robot-fleet" in value or "robotfleet" in value:
        return "robot_fleet"
    if "lamma" in value or "lamma-p" in value or "lamma_p" in value:
        return "lammap"
    if "harl" in value or "happo" in value:
        return "harl_happo"
    if "mrta_heuristic" in value or "mrta-heuristic" in value:
        return "mrta_heuristic"
    if "hybrid_susd" in value or "hybrid-susd" in value or "susd" in value:
        return "hybrid_susd"
    if "dcmrta" in value or "dynamic_coalition" in value:
        return "dcmrta"
    if "heteromrta" in value or "hetero_mrta" in value:
        return "heteromrta"
    if value in {"emos", ""}:
        return "emos"
    return value


def _condition_for(key: str) -> AlgorithmCondition:
    if key == "fmc_ata":
        from algorithm.system.coordination.fmc_ata.provenance import condition

        return condition()
    if key == "bt_capabilities":
        from algorithm.system.coordination.bt_capabilities.provenance import condition

        return condition()
    if key == "coherent":
        from algorithm.system.coordination.coherent.fire_rescue_policy import condition_record

        return condition_record()
    if key == "teamweaver":
        from algorithm.system.coordination.TeamWeaver.eai_adapter.benchmark_condition import condition_record

        return condition_record()
    if key == "sadcher":
        from algorithm.system.coordination.sadcher.benchmark_condition import condition_record

        return condition_record()
    if key == "robot_fleet":
        from algorithm.system.coordination.robot_fleet.benchmark_condition import condition_record

        return condition_record()
    if key == "lammap":
        from algorithm.system.coordination.lammap.benchmark_condition import condition_record

        return condition_record()
    if key == "harl_happo":
        from algorithm.system.coordination.harl.benchmark_condition import condition_record

        return condition_record()
    if key == "mrta_heuristic":
        from algorithm.system.coordination.mrta_heuristic.provenance import condition

        return condition()
    if key == "hybrid_susd":
        from algorithm.system.coordination.hybrid_susd.provenance import condition

        return condition()
    if key == "dcmrta":
        from algorithm.system.coordination.dcmrta.provenance import condition

        return condition()
    if key == "heteromrta":
        from algorithm.system.coordination.heteromrta.provenance import condition

        return condition()
    if key == "emos":
        from algorithm.system.coordination.emos.benchmark_condition import condition_record

        return condition_record()
    return AlgorithmCondition(
        algorithm_id=str(key),
        source_reference="unregistered",
        implementation="unregistered_adapter",
        execution_scope="assignment",
        planner="unknown",
        notes="No registered Fire Rescue condition for this policy specification.",
    )


def _robot_capabilities(robot_id: str, raw: Iterable[str]) -> frozenset[str]:
    text = f"{robot_id} {' '.join(str(item) for item in raw)}".lower()
    capabilities = {"navigate"}
    if any(token in text for token in ("m20", "四足", "quadruped", "翻越", "台阶")):
        capabilities.update({"terrain", "carry_extinguisher"})
    if any(token in text for token in ("scout", "ur5", "机械臂", "arm")):
        capabilities.update({"button", "carry_extinguisher"})
    if any(token in text for token in ("carter", "orsus", "传感", "sensor", "雷达", "camera")):
        capabilities.add("sensor")
    return frozenset(capabilities)


def _task_capabilities(task_id: str, name: str, description: str) -> frozenset[str]:
    text = f"{task_id} {name} {description}".lower()
    if task_id == "green" or any(token in text for token in ("按钮", "通道", "button", "channel")):
        return frozenset({"button"})
    if task_id == "yellow" or any(token in text for token in ("灭火", "extinguish", "夹取", "运送")):
        return frozenset({"terrain", "carry_extinguisher"})
    if task_id == "blue" or any(token in text for token in ("数据", "传感", "雷达", "sensor", "data")):
        return frozenset({"sensor"})
    if task_id == "red" or any(token in text for token in ("侦查", "火源", "scout", "terrain")):
        return frozenset({"terrain"})
    return frozenset({"navigate"})


def build_fire_rescue_records(request: Any) -> tuple[tuple[RobotCapability, ...], tuple[CoordinationTask, ...]]:
    """Translate the existing EMOS AssignmentRequest into typed records."""

    if request is None:
        raise ValueError("Fire Rescue coordination requires an AssignmentRequest")
    scenario = request.scenario
    excluded = frozenset(request.excluded_subtask_ids)
    completed_robots = set(request.completed_extinguisher) | set(request.completed_rescue)
    robots = tuple(
        RobotCapability(
            robot_id,
            _robot_capabilities(
                robot_id,
                getattr(scenario.robot_profiles.get(robot_id), "capabilities", ()),
            ),
            active=robot_id not in completed_robots,
        )
        for robot_id in sorted(request.robot_positions)
    )
    tasks = tuple(
        CoordinationTask(
            task_id=subtask_id,
            description=str(subtask.description or subtask.name),
            required_capabilities=_task_capabilities(subtask_id, subtask.name, subtask.description),
            target_ref=subtask_id,
            metadata={"subtask_id": subtask_id, "decomposable": False},
        )
        for subtask_id, subtask in scenario.subtasks.items()
        if subtask_id not in excluded
    )
    if not tasks:
        raise ValueError("Fire Rescue request has no open subtasks")
    return robots, tasks


class FireRescueCoordinationBridge:
    """Coordinate one Fire Rescue trial without taking physical ownership."""

    _SIGNAL_TO_MILESTONE = {
        "assignment_applied": "assignment_applied",
        "extinguisher_carried": "extinguisher_carried",
        "rescue_channel_open": "rescue_channel_open",
        "obstacle_removed": "obstacle_removed",
        "mission_success": "mission_success",
    }
    _MILESTONE_TO_TASK = {
        "extinguisher_carried": "yellow",
        "rescue_channel_open": "green",
        "obstacle_removed": "orange",
    }

    def __init__(
        self,
        policy_specification: str,
        *,
        task_id: str,
        scene_id: str = "fire_rescue",
        repeat_id: str = "",
        branch: str = "fact",
        output_dir: str | Path | None = None,
        decomposable_task_ids: Iterable[str] = (),
        validate_submitted_plan: bool = False,
        submitted_system_provenance: Mapping[str, object] | None = None,
    ) -> None:
        self.policy_specification = str(policy_specification or "emos")
        self.key = _algorithm_key(self.policy_specification)
        self.condition = _condition_for(self.key)
        self.task_id = str(task_id)
        self.scene_id = str(scene_id)
        self.repeat_id = str(repeat_id)
        self.branch = str(branch)
        self.output_dir = Path(output_dir).expanduser() if output_dir else None
        self.decomposable_task_ids = frozenset(str(item) for item in decomposable_task_ids)
        # The neutral benchmark path records decisions made by the submitted
        # system.  Internal planner recomputation is opt-in for legacy adapter
        # diagnostics and must never be used to fill a missing decision.
        self.validate_submitted_plan = bool(validate_submitted_plan)
        self.submitted_system_provenance = dict(submitted_system_provenance or {})
        self.ledger = EventLedger(schema_version="fire_rescue_coordination.v1")
        self.plan_result: BridgePlan | None = None
        self.submitted_decisions: list[dict[str, object]] = []
        self._active_assignments: tuple[Assignment, ...] = ()
        self._last_request: Any | None = None
        self.feedback_records: list[ExecutionFeedback] = []
        self.milestones: dict[str, MilestoneEvidence] = {}
        self._feedback_keys: set[tuple[str, str]] = set()
        self._finalized = False
        self.ledger.append(
            "run_started",
            algorithm_id=self.condition.algorithm_id,
            task_id=self.task_id,
            scene_id=self.scene_id,
            branch=self.branch,
            submitted_system_provenance=dict(self.submitted_system_provenance),
        )

    def _utilities(self, request: Any, robots: tuple[RobotCapability, ...], tasks: tuple[CoordinationTask, ...]) -> dict[tuple[str, str], float]:
        preferred = dict(getattr(request.scenario, "preferred_fallback", {}) or {})
        utilities: dict[tuple[str, str], float] = {}
        for robot in robots:
            for task in tasks:
                target = request.subtask_positions.get(task.task_id)
                position = request.robot_positions.get(robot.robot_id)
                distance = 0.0
                if target is not None and position is not None:
                    distance = math.hypot(float(position[0]) - float(target[0]), float(position[1]) - float(target[1]))
                utilities[(robot.robot_id, task.task_id)] = (100.0 if preferred.get(robot.robot_id) == task.task_id else 0.0) - distance
        return utilities

    def plan(self, request: Any) -> BridgePlan:
        """Run an explicit legacy adapter plan for diagnostics only.

        The neutral benchmark path does not call this method.  A submitted
        system's decision is recorded by :meth:`observe_assignment`; this
        method exists only when a caller explicitly asks to inspect one of the
        workspace adapters in isolation.
        """

        self._last_request = request
        robots, tasks = build_fire_rescue_records(request)
        try:
            if self.key == "fmc_ata":
                invalid = [task.task_id for task in tasks if task.task_id not in self.decomposable_task_ids]
                if invalid:
                    from algorithm.system.coordination.fmc_ata.fire_rescue_policy import default_assignment

                    mapping = default_assignment(request)
                    assignments = tuple(
                        Assignment(task_id, robot_id, self.condition.algorithm_id, "fixed-default-r1")
                        for robot_id, task_id in sorted(mapping.items())
                    )
                    result = BridgePlan(
                        self.condition.algorithm_id,
                        PlanStatus.PLANNED,
                        assignments=assignments,
                        mapping=dict(mapping),
                        reason=(
                            "Fire Rescue subtasks are indivisible physical skills; "
                            "FMC-ATA market allocation is N/A. The benchmark uses the "
                            "scenario's declared default mapping so the EAI execution "
                            "and feedback path can still be evaluated. "
                            f"Undecomposed tasks: {', '.join(invalid)}"
                        ),
                        allocation_provider="fixed_default_assignment",
                    )
                else:
                    from algorithm.system.coordination.fmc_ata import FMCATAPlanner

                    planner = FMCATAPlanner(robots)
                    allocation = planner.allocate(tasks, utilities=self._utilities(request, robots, tasks))
                    assignments = tuple(
                        Assignment(item.task_id, item.robot_id, self.condition.algorithm_id, item.plan_version)
                        for item in allocation
                    )
                    result = BridgePlan(
                        self.condition.algorithm_id,
                        PlanStatus.PLANNED,
                        assignments=assignments,
                        mapping={item.robot_id: item.task_id for item in assignments},
                        fractional_allocations=tuple(asdict(item) for item in allocation),
                        allocation_provider="fmc_ata_market",
                    )
            elif self.key == "bt_capabilities":
                from algorithm.system.coordination.bt_capabilities import BTCapability, CapabilityMission

                utilities = self._utilities(request, robots, tasks)
                mission = CapabilityMission(
                    tuple(BTCapability(task, {robot.robot_id: utilities[(robot.robot_id, task.task_id)] for robot in robots}) for task in tasks),
                    robots,
                )
                bt_plan = mission.tick()
                assignments = tuple(
                    Assignment(item.task_id, item.robot_id, self.condition.algorithm_id, bt_plan.plan_version)
                    for item in bt_plan.assignments
                )
                result = BridgePlan(
                    self.condition.algorithm_id,
                    PlanStatus.PLANNED if assignments else PlanStatus.FAILED,
                    assignments=assignments,
                    mapping={item.robot_id: item.task_id for item in assignments},
                    reason="" if assignments else "BT capability auction found no ready capable task",
                    allocation_provider="bt_capability_auction",
                )
            elif self.key == "coherent":
                from algorithm.system.coordination.coherent.fire_rescue_policy import assign

                mapping = assign(request)
                assignments = tuple(
                    Assignment(task_id, robot_id, self.condition.algorithm_id, "coherent-proposal-1")
                    for robot_id, task_id in sorted(mapping.items())
                )
                result = BridgePlan(
                    self.condition.algorithm_id,
                    PlanStatus.PLANNED,
                    assignments=assignments,
                    mapping=dict(mapping),
                    allocation_provider="coherent_pefa_adapter",
                )
            elif self.key == "mrta_heuristic":
                from algorithm.system.coordination.mrta_heuristic.fire_rescue_policy import assign

                mapping = assign(request)
                assignments = tuple(
                    Assignment(task_id, robot_id, self.condition.algorithm_id, "mrta-r1")
                    for robot_id, task_id in sorted(mapping.items())
                )
                result = BridgePlan(
                    self.condition.algorithm_id,
                    PlanStatus.PLANNED,
                    assignments=assignments,
                    mapping=dict(mapping),
                    allocation_provider="mrta_heuristic_adapter",
                )
            elif self.key == "hybrid_susd":
                from algorithm.system.coordination.hybrid_susd.fire_rescue_policy import assign

                mapping = assign(request)
                assignments = tuple(
                    Assignment(task_id, robot_id, self.condition.algorithm_id, "hybrid-susd-r1")
                    for robot_id, task_id in sorted(mapping.items())
                )
                result = BridgePlan(
                    self.condition.algorithm_id,
                    PlanStatus.PLANNED,
                    assignments=assignments,
                    mapping=dict(mapping),
                    allocation_provider="hybrid_susd_adapter",
                )
            elif self.key == "dcmrta":
                from algorithm.system.coordination.dcmrta.fire_rescue_policy import default_assignment

                mapping = default_assignment(request)
                assignments = tuple(
                    Assignment(task_id, robot_id, self.condition.algorithm_id, "fixed-default-r1")
                    for robot_id, task_id in sorted(mapping.items())
                )
                result = BridgePlan(
                    self.condition.algorithm_id,
                    PlanStatus.PLANNED,
                    assignments=assignments,
                    mapping=dict(mapping),
                    reason=(
                        "The official DCMRTA checkpoint is retained for native-input "
                        "decision-only evaluation. Fire Rescue uses its declared default "
                        "mapping because the 4-agent/4-task scene is outside the checkpoint "
                        "training distribution."
                    ),
                    allocation_provider="fixed_default_assignment;dcmrta_native_decision_N/A",
                )
            elif self.key == "heteromrta":
                from algorithm.system.coordination.heteromrta.fire_rescue_policy import assign

                mapping = assign(request)
                assignments = tuple(
                    Assignment(task_id, robot_id, self.condition.algorithm_id, "fixed-default-r1")
                    for robot_id, task_id in sorted(mapping.items())
                )
                result = BridgePlan(
                    self.condition.algorithm_id,
                    PlanStatus.PLANNED,
                    assignments=assignments,
                    mapping=dict(mapping),
                    reason=(
                        "The official HeteroMRTA checkpoint is retained for native-input "
                        "decision-only evaluation. Fire Rescue uses its declared default "
                        "mapping because the 3-5-species/15-50-task distribution does not "
                        "match the 4-agent/4-task scene."
                    ),
                    allocation_provider="fixed_default_assignment;heteromrta_native_decision_N/A",
                )
            elif self.key == "sadcher":
                from algorithm.system.coordination.sadcher.fire_rescue_policy import assign

                mapping = assign(request)
                assignments = tuple(
                    Assignment(task_id, robot_id, self.condition.algorithm_id, "sadcher-native-r1")
                    for robot_id, task_id in sorted(mapping.items())
                )
                result = BridgePlan(
                    self.condition.algorithm_id,
                    PlanStatus.PLANNED,
                    assignments=assignments,
                    mapping=dict(mapping),
                    allocation_provider="sadcher_native_network_and_matching",
                )
            elif self.key == "robot_fleet":
                from algorithm.system.coordination.robot_fleet.fire_rescue_policy import assign

                mapping = assign(request)
                assignments = tuple(
                    Assignment(task_id, robot_id, self.condition.algorithm_id, "robot-fleet-dag-r1")
                    for robot_id, task_id in sorted(mapping.items())
                )
                result = BridgePlan(
                    self.condition.algorithm_id,
                    PlanStatus.PLANNED,
                    assignments=assignments,
                    mapping=dict(mapping),
                    allocation_provider="robot_fleet_in_memory_dag_allocator",
                    reason=(
                        "RobotFleet's PostgreSQL/Docker service boundary is represented by "
                        "the typed EAI task contract and in-memory allocator for this hook."
                    ),
                )
            elif self.key == "lammap":
                from algorithm.system.coordination.lammap.fire_rescue_policy import assign

                mapping = assign(request)
                assignments = tuple(
                    Assignment(task_id, robot_id, self.condition.algorithm_id, "lamma-p-order-r1")
                    for robot_id, task_id in sorted(mapping.items())
                )
                result = BridgePlan(
                    self.condition.algorithm_id,
                    PlanStatus.PLANNED,
                    assignments=assignments,
                    mapping=dict(mapping),
                    allocation_provider="lamma_p_dependency_skill_adapter",
                    reason=(
                        "The Fire Rescue task contract is already frozen by EAI; no "
                        "AI2-THOR household PDDL or unregistered LLM decomposition is claimed."
                    ),
                )
            elif self.key == "harl_happo":
                from algorithm.system.coordination.harl.fire_rescue_policy import (
                    HAPPOCheckpointUnavailable,
                    assign,
                )

                # This branch intentionally fails closed when no registered
                # HAPPO checkpoint/environment contract is available.
                try:
                    mapping = assign(request)
                except HAPPOCheckpointUnavailable as exc:
                    result = BridgePlan(
                        self.condition.algorithm_id,
                        PlanStatus.NOT_APPLICABLE,
                        reason=str(exc),
                        allocation_provider="happo_native_policy_checkpoint_required",
                    )
                else:
                    assignments = tuple(
                        Assignment(task_id, robot_id, self.condition.algorithm_id, "happo-native-r1")
                        for robot_id, task_id in sorted(mapping.items())
                    )
                    result = BridgePlan(
                        self.condition.algorithm_id,
                        PlanStatus.PLANNED,
                        assignments=assignments,
                        mapping=dict(mapping),
                        allocation_provider="happo_native_policy",
                    )
            else:
                result = BridgePlan(
                    self.condition.algorithm_id,
                    PlanStatus.FAILED,
                    reason=f"No Fire Rescue typed planner registered for policy {self.policy_specification!r}",
                )
        except Exception as exc:
            result = BridgePlan(self.condition.algorithm_id, PlanStatus.FAILED, reason=str(exc))
        self.plan_result = result
        self.ledger.append("plan", **result.to_dict())
        return result

    def observe_assignment(self, assignments: Mapping[str, str], *, request: Any | None = None, timestamp_s: float = 0.0) -> tuple[Assignment, ...]:
        """Record the mapping actually handed to EAI navigation."""

        # Each request can be a new online-replanning round.  In the neutral
        # benchmark path the submitted system already made the decision; the
        # bridge records it and never calls a fallback or a second planner.
        if request is not None:
            self._last_request = request
            if self.validate_submitted_plan:
                self.plan(request)
        records = tuple(
            Assignment(task_id=str(task_id), robot_id=str(robot_id), source=self.condition.algorithm_id, plan_version="runtime")
            for robot_id, task_id in sorted(assignments.items())
        )
        self._active_assignments = records
        expected = dict(self.plan_result.mapping or {}) if self.plan_result and self.plan_result.status is PlanStatus.PLANNED else None
        matches = None if expected is None else expected == dict(assignments)
        decision = {
            "source": "submitted_system",
            "algorithm_id": self.condition.algorithm_id,
            "allocation_provider": "emos_submitted_system" if self.key == "emos" else "submitted_system",
            "provenance": dict(self.submitted_system_provenance),
            "assignments": [asdict(item) for item in records],
            "timestamp_s": float(timestamp_s),
        }
        self.submitted_decisions.append(decision)
        self.ledger.append("submitted_system_decision", **decision)
        self.ledger.append(
            "assignment_applied",
            timestamp_s=float(timestamp_s),
            assignments=[asdict(item) for item in records],
            matches_algorithm_plan=matches,
        )
        if expected is not None and not matches:
            self.ledger.append("assignment_mismatch", expected=expected, actual=dict(assignments))
        return records

    def record_feedback(self, feedback: ExecutionFeedback) -> None:
        if not isinstance(feedback, ExecutionFeedback):
            raise TypeError("feedback must be an ExecutionFeedback record")
        self.feedback_records.append(feedback)
        self.ledger.append("execution_feedback", **asdict(feedback))

    def record_member_exit(self, robot_id: str, *, timestamp_s: float = 0.0, reason: str = "") -> None:
        """Record a member-exit intervention without inventing a replacement."""

        self.ledger.append(
            "member_exit",
            robot_id=str(robot_id),
            timestamp_s=float(timestamp_s),
            reason=str(reason),
            failure_code=FailureCode.MEMBER_EXIT.value,
        )

    def record_branch_assignment(self, assignments: Mapping[str, Mapping[str, object]], *, checkpoint_id: str, timestamp_s: float = 0.0) -> None:
        # A replay branch is a new evaluation unit. Keep the fact-run events in
        # the append-only ledger, but do not allow fact milestones to certify
        # the branch result.
        self.branch = f"branch:{checkpoint_id}"
        self.plan_result = None
        self.feedback_records.clear()
        self.milestones.clear()
        self._feedback_keys.clear()
        self.ledger.append("branch_started", checkpoint_id=str(checkpoint_id), timestamp_s=float(timestamp_s))
        mapping = {
            str(robot_id): str(value.get("colour", ""))
            for robot_id, value in assignments.items()
        }
        self.observe_assignment(mapping, timestamp_s=timestamp_s)
        self.ledger.append(
            "branch_assignment",
            checkpoint_id=str(checkpoint_id),
            assignments=dict(assignments),
            timestamp_s=float(timestamp_s),
        )

    def record_milestone(self, evidence: MilestoneEvidence) -> None:
        if not isinstance(evidence, MilestoneEvidence):
            raise TypeError("evidence must be a MilestoneEvidence record")
        previous = self.milestones.get(evidence.milestone_id)
        if previous is not None and previous.satisfied == evidence.satisfied:
            return
        self.milestones[evidence.milestone_id] = evidence
        self.ledger.append("milestone_evidence", **asdict(evidence))

    def observe_signals(self, signals: Mapping[str, bool], *, timestamp_s: float = 0.0, source: str = "eai.fire_rescue.evaluator") -> None:
        """Capture edge-triggered evaluator signals without inferring success."""

        for signal, milestone_id in self._SIGNAL_TO_MILESTONE.items():
            if signal not in signals:
                continue
            self.record_milestone(
                MilestoneEvidence(
                    milestone_id=milestone_id,
                    satisfied=bool(signals[signal]),
                    source=source,
                    timestamp_s=float(timestamp_s),
                    predicate_version="fire_rescue.task_checkpoints.v1",
                )
            )
            task_id = self._MILESTONE_TO_TASK.get(milestone_id)
            if task_id and bool(signals[signal]):
                assignments = self._active_assignments
                if not assignments and self.plan_result is not None:
                    assignments = self.plan_result.assignments
                for assignment in assignments:
                    feedback_key = (task_id, assignment.robot_id)
                    if assignment.task_id == task_id and feedback_key not in self._feedback_keys:
                        self._feedback_keys.add(feedback_key)
                        self.record_feedback(
                            ExecutionFeedback(
                                task_id=task_id,
                                robot_id=assignment.robot_id,
                                outcome="success",
                                timestamp_s=float(timestamp_s),
                                world_changes={"milestone_id": milestone_id},
                            )
                        )

    def record_terminal_execution_feedback(self, result: str, *, timestamp_s: float = 0.0) -> None:
        """Close an interrupted/short run with explicit executor feedback.

        A bounded smoke or timed-out episode often ends before a physical
        milestone can be reached.  The evaluator knows that the mission did
        not finish, but it must not be converted into an inferred success or
        silently omitted feedback.  Record one timeout feedback per still-open
        assignment and make the replanning limitation explicit in the ledger.
        """
        active_assignments = self._active_assignments
        if not active_assignments and self.plan_result is not None and self.plan_result.status is PlanStatus.PLANNED:
            active_assignments = self.plan_result.assignments
        if not active_assignments:
            return
        terminal_result = str(result)
        if terminal_result in {"success", "not_applicable"}:
            return
        completed = {
            (item.task_id, item.robot_id)
            for item in self.feedback_records
            if str(item.outcome).lower() == "success"
        }
        open_tasks: list[str] = []
        for assignment in active_assignments:
            key = (assignment.task_id, assignment.robot_id)
            if key in completed:
                continue
            open_tasks.append(assignment.task_id)
            self.record_feedback(
                ExecutionFeedback(
                    task_id=assignment.task_id,
                    robot_id=assignment.robot_id,
                    outcome="failure",
                    failure_code=FailureCode.TIMEOUT,
                    message="EAI evaluator terminated before the assigned milestone was verified",
                    timestamp_s=float(timestamp_s),
                    world_changes={"terminal_result": terminal_result},
                )
            )
        self.ledger.append(
            "replan_requested",
            trigger="terminal_execution_feedback",
            open_tasks=sorted(set(open_tasks)),
            timestamp_s=float(timestamp_s),
        )
        if self.key in {"mrta_heuristic", "hybrid_susd", "robot_fleet", "lammap"} and self._last_request is not None and open_tasks:
            if self.key == "mrta_heuristic":
                from algorithm.system.coordination.mrta_heuristic.fire_rescue_policy import repair
                provider = "MRTA-heuristic-planRepair-equivalent"
            elif self.key == "hybrid_susd":
                from algorithm.system.coordination.hybrid_susd.fire_rescue_policy import repair
                provider = "Hybrid-SUSD-adapter-reallocation"
            elif self.key == "robot_fleet":
                from algorithm.system.coordination.robot_fleet.fire_rescue_policy import repair
                provider = "RobotFleet-feedback-reallocation"
            else:
                from algorithm.system.coordination.lammap.fire_rescue_policy import repair
                provider = "LaMMA-P-adapter-reallocation"

            previous = (
                dict(self.plan_result.mapping or {})
                if self.plan_result is not None
                else {item.robot_id: item.task_id for item in active_assignments}
            )
            failed_robots = {
                item.robot_id
                for item in self.feedback_records
                if item.failure_code in {FailureCode.CAPABILITY_LOSS, FailureCode.MEMBER_EXIT, FailureCode.SAFETY_VIOLATION}
            }
            mapping = repair(
                self._last_request,
                previous,
                failed_robot_ids=failed_robots,
                delayed_task_ids=open_tasks,
            )
            self.ledger.append(
                "replan",
                algorithm_id=self.condition.algorithm_id,
                status="planned_not_dispatched",
                mapping=dict(mapping),
                allocation_provider=provider,
                timestamp_s=float(timestamp_s),
            )
            self.ledger.append(
                "replan_status",
                status="planned_not_dispatched",
                reason=(
                    "An adapter repair/reallocation plan was generated after EAI timeout; "
                    "no additional physical step was available in the bounded run."
                ),
                timestamp_s=float(timestamp_s),
            )
        else:
            self.ledger.append(
                "replan_status",
                status="not_available_in_bounded_run",
                reason=(
                    "The EAI runner ended at its configured step/time bound; no new "
                    "AssignmentRequest was supplied for an online replanning round."
                ),
                timestamp_s=float(timestamp_s),
            )

    def finalize(self, result: str, *, timestamp_s: float = 0.0) -> dict[str, object]:
        if self._finalized:
            return self.to_dict()
        self.record_terminal_execution_feedback(result, timestamp_s=timestamp_s)
        physical_verified = self.milestones.get("mission_success")
        verified = bool(physical_verified is not None and physical_verified.satisfied is True)
        self.ledger.append(
            "terminal",
            result=str(result),
            physical_success_verified=verified,
            timestamp_s=float(timestamp_s),
        )
        if str(result) not in {"success", "not_applicable"}:
            self.ledger.append(
                "terminal_failure",
                failure_code=FailureCode.TIMEOUT.value if str(result) == "fail" else FailureCode.EXECUTION_FAILURE.value,
                reason="mission did not satisfy the evaluator terminal predicate",
            )
        self._finalized = True
        payload = self.to_dict()
        if self.output_dir is not None:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            destination = self.output_dir / "coordination_run.json"
            temporary = destination.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
            temporary.replace(destination)
        return payload

    def to_dict(self) -> dict[str, object]:
        manifest = RunManifest(
            condition=self.condition,
            task_id=self.task_id,
            scene_id=self.scene_id,
            branch=self.branch,
            repeat_id=self.repeat_id,
            checkpoint_digest=self.ledger.digest(),
            observation_schema="fire_rescue.assignment.v1;fire_rescue.milestone.v1",
            status="finalized" if self._finalized else "running",
        )
        return {
            "schema_version": "fire_rescue_coordination.v1",
            "policy_specification": self.policy_specification,
            "algorithm_key": self.key,
            "condition": self.condition.to_dict(),
            "manifest": manifest.to_dict(),
            "plan": self.plan_result.to_dict() if self.plan_result is not None else None,
            "submitted_decisions": list(self.submitted_decisions),
            "feedback": [asdict(item) for item in self.feedback_records],
            "milestones": [asdict(item) for item in self.milestones.values()],
            "ledger": {"schema_version": self.ledger.schema_version, "digest": self.ledger.digest(), "events": list(self.ledger.events)},
            "finalized": self._finalized,
        }


__all__ = [
    "BridgePlan",
    "FireRescueCoordinationBridge",
    "PlanStatus",
    "build_fire_rescue_records",
]
