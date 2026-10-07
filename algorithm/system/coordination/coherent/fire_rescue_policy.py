"""Fire Rescue assignment-policy bridge for ``COHERENT-adapted-to-EAI``.

The EAI Fire Rescue runner exposes a deliberately narrow, stateless
``module:assign`` hook.  This module adapts that hook to the COHERENT PEFA
proposal stage without taking ownership of Isaac, navigation, manipulation,
or milestone verification.  The runner still executes the returned
robot-to-subtask mapping and supplies the physical evidence.

Use through the task runner or an EAI integration that loads the
`algorithm.system.coordination.coherent.fire_rescue_policy:assign` callback.

The policy is intentionally deterministic.  It does not call an LLM, so the
assignment condition can be reproduced without hidden prompt variation.  A
future LLM planner may be injected into ``COHERENTAdapter`` by a separately
registered condition, but must not silently replace this policy.
"""

from __future__ import annotations

import math
from typing import Mapping, Sequence

try:  # repository-root import
    from algorithm.system.coordination.emos.policy import AssignmentRequest
    from algorithm.common_coordination import AlgorithmCondition
except ModuleNotFoundError:  # ``PYTHONPATH=algorithm`` import
    from algorithm.system.coordination.emos.policy import AssignmentRequest
    from common_coordination import AlgorithmCondition

from .adapter import COHERENTAdapter
from .contract import CapabilityProfile, ContractError, PlanAssignment, Proposal, SkillSpec, TaskSpec
ALGORITHM_ID = "COHERENT-adapted-to-EAI"


def condition_manifest() -> dict[str, object]:
    """Return benchmark metadata for this adapter condition.

    The manifest is deliberately credential-free and makes the adaptation
    scope explicit so an evaluator cannot confuse it with a learned policy or
    the original OmniGibson per-action runtime.
    """

    record = condition_record().to_dict()
    # Keep the historical short key for callers that consumed the first
    # adapter release, while making ``unsupported_dimensions`` the shared
    # HeteroCoord contract field used by all three adapters.
    record["unsupported"] = list(record["unsupported_dimensions"])
    return record


def condition_record() -> AlgorithmCondition:
    """Return the shared HeteroCoord condition record for this adapter."""

    return AlgorithmCondition(
        algorithm_id=ALGORITHM_ID,
        source_reference="ICRA 2025 / arXiv:2409.15146 / MrKeee/COHERENT",
        implementation="adapted",
        execution_scope="assignment_level",
        planner="deterministic_capability_affinity",
        checkpoint="not_applicable_no_learned_weights",
        supported_primitives=("P1", "P2"),
        unsupported_dimensions=("P3_coupled_joint_action", "D2", "D4", "D5"),
        physical_executor_owner="eai_fire_rescue_runtime",
        serial_action_loop=False,
        notes="PEFA proposal/adjustment semantics are represented by the typed adapter; EAI owns physical execution and milestones.",
    )


def _text_capabilities(robot_name: str, raw_capabilities: Sequence[str]) -> frozenset[str]:
    """Normalize EAI's human-readable capability descriptions into tags."""

    text = f"{robot_name} {' '.join(str(item) for item in raw_capabilities)}".lower()
    capabilities = {"navigate"}
    if any(token in text for token in ("m20", "四足", "quadruped", "翻越", "台阶")):
        capabilities.update({"terrain", "carry_extinguisher"})
    if any(token in text for token in ("scout", "ur5", "机械臂", "arm")):
        capabilities.update({"button", "carry_extinguisher"})
    if any(token in text for token in ("carter", "orsus", "传感", "sensor", "雷达", "camera")):
        capabilities.add("sensor")
    return frozenset(capabilities)


def _required_capabilities(subtask_id: str, name: str, description: str) -> frozenset[str]:
    """Map a Fire Rescue subtask to capability tags used by PEFA validation."""

    text = f"{subtask_id} {name} {description}".lower()
    if subtask_id == "green" or any(token in text for token in ("按钮", "通道", "button", "channel")):
        return frozenset({"button"})
    if subtask_id == "yellow" or any(token in text for token in ("灭火", "extinguish", "夹取", "运送")):
        return frozenset({"terrain", "carry_extinguisher"})
    if subtask_id == "blue" or any(token in text for token in ("数据", "传感", "雷达", "sensor", "data")):
        return frozenset({"sensor"})
    if subtask_id == "red" or any(token in text for token in ("侦查", "火源", "scout", "terrain")):
        return frozenset({"terrain"})
    return frozenset({"navigate"})


def _distance(request: AssignmentRequest, robot_id: str, subtask_id: str) -> float:
    """Return a finite distance for deterministic tie-breaking when available."""

    robot = request.robot_positions.get(robot_id)
    target = request.subtask_positions.get(subtask_id)
    if robot is None or target is None or len(robot) < 2 or len(target) != 2:
        return math.inf
    value = math.hypot(float(robot[0]) - float(target[0]), float(robot[1]) - float(target[1]))
    return value if math.isfinite(value) else math.inf


def _planner_for_request(request: AssignmentRequest):
    """Build the one-to-one proposal planner for one immutable request."""

    preferred = dict(request.scenario.preferred_fallback)
    green_robot = str(request.scenario.green_robot_name or "").strip()
    yellow_prefix = str(request.scenario.yellow_robot_prefix or "").strip()

    def planner(tasks: Sequence[TaskSpec], robots: Sequence[CapabilityProfile]) -> Proposal:
        available = {robot.robot_id: robot for robot in robots if robot.active}
        assignments: list[PlanAssignment] = []
        used: set[str] = set()
        for task in tasks:
            task_id = str(task.metadata.get("subtask_id", task.task_id))
            candidates = [
                robot
                for robot in available.values()
                if robot.robot_id not in used
                and task.required_capabilities <= robot.capabilities
                and not (
                    task_id == request.scenario.green_subtask_id
                    and green_robot
                    and robot.robot_id != green_robot
                )
                and not (
                    task_id == request.scenario.yellow_subtask_id
                    and yellow_prefix
                    and not robot.robot_id.startswith(yellow_prefix)
                )
            ]
            if not candidates:
                raise ContractError(f"no distinct capable robot for {task.task_id}")

            def score(robot: CapabilityProfile) -> tuple[float, float, int, str]:
                # Hard scenario affinity is represented as a stable score.  The
                # capability check remains authoritative; affinity never permits
                # an incompatible robot through.
                affinity = 0
                if preferred.get(robot.robot_id) == task_id:
                    affinity -= 100
                if task_id == request.scenario.green_subtask_id:
                    affinity += 0 if robot.robot_id == green_robot else 1000
                if task_id == request.scenario.yellow_subtask_id:
                    affinity += 0 if robot.robot_id.startswith(yellow_prefix) else 1000
                return (
                    float(affinity),
                    _distance(request, robot.robot_id, task_id),
                    len(robot.capabilities),
                    robot.robot_id,
                )

            selected = min(candidates, key=score)
            assignments.append(PlanAssignment(task.task_id, selected.robot_id))
            used.add(selected.robot_id)
        return Proposal(
            tuple(assignments),
            rationale="deterministic one-to-one capability and affinity matching",
            source=ALGORITHM_ID,
        )

    return planner


def assign(request: AssignmentRequest) -> Mapping[str, str]:
    """Return a validated Fire Rescue robot-to-subtask assignment.

    This function is intentionally a module-level stateless callback so it is
    accepted by ``algorithm.system.coordination.emos.policy.load_assignment_policy``.  It maps
    only currently open subtasks; completed/excluded subtasks are never
    reintroduced.  Any impossible one-to-one allocation raises a clear error,
    allowing the benchmark runner to record a failed condition instead of
    fabricating an assignment.
    """

    if not isinstance(request, AssignmentRequest):
        raise TypeError("COHERENT Fire Rescue policy expects an AssignmentRequest")

    excluded = frozenset(request.excluded_subtask_ids)
    open_ids = [sid for sid in request.scenario.subtasks if sid not in excluded]
    if not open_ids:
        raise ValueError("COHERENT Fire Rescue policy has no open subtasks")

    robots: list[CapabilityProfile] = []
    completed_robots = set(request.completed_extinguisher) | set(request.completed_rescue)
    for robot_id, position in request.robot_positions.items():
        if len(position) != 3 or not all(math.isfinite(float(value)) for value in position):
            raise ValueError(f"invalid robot position for {robot_id!r}")
        profile = request.scenario.robot_profiles.get(robot_id)
        raw_capabilities = profile.capabilities if profile is not None else []
        robots.append(CapabilityProfile(
            robot_id,
            _text_capabilities(robot_id, raw_capabilities),
            active=robot_id not in completed_robots,
        ))

    tasks: list[TaskSpec] = []
    skills: list[SkillSpec] = []
    for subtask_id in open_ids:
        subtask = request.scenario.subtasks[subtask_id]
        required = _required_capabilities(subtask_id, subtask.name, subtask.description)
        tasks.append(
            TaskSpec(
                task_id=f"fire_rescue:{subtask_id}",
                description=subtask.description or subtask.name,
                required_capabilities=required,
                preferred_skills=(subtask_id,),
                metadata={"subtask_id": subtask_id},
            )
        )
        skills.append(SkillSpec(subtask_id, required))

    adapter = COHERENTAdapter(
        robots=robots,
        skills=skills,
        planner=_planner_for_request(request),
    )
    proposal = adapter.propose(tasks)
    robot_to_subtask: dict[str, str] = {}
    for assignment in proposal.assignments:
        task_id = assignment.task_id.removeprefix("fire_rescue:")
        robot_to_subtask[assignment.robot_id] = task_id
    return robot_to_subtask
