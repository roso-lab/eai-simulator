"""COHERENT PEFA orchestration over the EAI typed task/skill contract.

This is an adapter, not a replacement for the released OmniGibson runtime.
It keeps the Proposal--Execution--Feedback--Adjustment loop and exposes one
action at a time, matching the serial PEFA execution mode in the paper.  A
caller may provide an LLM planner/action selector, but the default is a
deterministic planner for reproducible offline tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable, Mapping, Optional, Sequence

from .contract import (
    ActionCommand,
    CapabilityProfile,
    ContractError,
    Feedback,
    PlanAssignment,
    Proposal,
    SkillSpec,
    TaskSpec,
)


class UnsupportedCapability(str, Enum):
    """Dimensions not claimed by the released COHERENT PEFA implementation."""

    D2_COMMUNICATION_STRESS = "N/A: communication delay/loss adaptation"
    D4_MEMBER_EXIT = "N/A: member-exit recovery"
    D5_TRANSFER = "N/A: matched cross-domain transfer"
    PARALLEL_JOINT_ACTION = "N/A: parallel joint-action execution"


@dataclass(frozen=True)
class LedgerEvent:
    sequence: int
    kind: str
    payload: Mapping[str, object]


class EventLedger:
    """Append-only deterministic event ledger suitable for benchmark replay."""

    def __init__(self) -> None:
        self._events: list[LedgerEvent] = []

    def append(self, kind: str, **payload: object) -> LedgerEvent:
        if not kind:
            raise ValueError("ledger event kind cannot be empty")
        event = LedgerEvent(len(self._events), kind, dict(payload))
        self._events.append(event)
        return event

    @property
    def events(self) -> tuple[LedgerEvent, ...]:
        return tuple(self._events)


@dataclass(frozen=True)
class PEFAReport:
    completed_tasks: tuple[str, ...]
    failed_tasks: tuple[str, ...]
    steps: int
    plan_valid: bool
    terminal: bool
    unsupported: tuple[str, ...]
    ledger: tuple[LedgerEvent, ...]


Planner = Callable[[Sequence[TaskSpec], Sequence[CapabilityProfile]], Proposal]
ActionSelector = Callable[[TaskSpec, CapabilityProfile, Sequence[SkillSpec]], str]


class COHERENTAdapter:
    """Typed PEFA adapter with capability validation and feedback adjustment."""

    unsupported = tuple(item.value for item in UnsupportedCapability)

    def __init__(
        self,
        *,
        robots: Iterable[CapabilityProfile],
        skills: Iterable[SkillSpec],
        planner: Optional[Planner] = None,
        action_selector: Optional[ActionSelector] = None,
    ) -> None:
        self.robots = tuple(robots)
        self.skills = tuple(skills)
        if not self.robots or len({item.robot_id for item in self.robots}) != len(self.robots):
            raise ContractError("robots must be non-empty and uniquely named")
        if len({item.name for item in self.skills}) != len(self.skills):
            raise ContractError("skills must be uniquely named")
        self._planner = planner
        self._action_selector = action_selector
        self.ledger = EventLedger()
        self._tasks: dict[str, TaskSpec] = {}
        self._proposal: Optional[Proposal] = None
        self._completed: set[str] = set()
        self._failed: set[str] = set()
        self._unavailable_robots: set[str] = set()
        self._pending_command: Optional[ActionCommand] = None
        self._steps = 0

    def propose(self, tasks: Iterable[TaskSpec]) -> Proposal:
        task_list = tuple(tasks)
        self._tasks = {task.task_id: task for task in task_list}
        if len(self._tasks) != len(task_list) or not task_list:
            raise ContractError("tasks must be non-empty and uniquely named")
        unknown_dependencies = {
            dependency
            for task in task_list
            for dependency in task.dependencies
            if dependency not in self._tasks
        }
        if unknown_dependencies:
            raise ContractError(f"unknown task dependencies: {sorted(unknown_dependencies)}")
        self._validate_dependency_graph(task_list)
        if self._planner is None:
            proposal = self._deterministic_proposal(task_list)
        else:
            proposal = self._planner(task_list, self.robots)
        if not isinstance(proposal, Proposal):
            raise ContractError("planner must return a Proposal")
        self.validate_proposal(proposal)
        self._proposal = proposal
        self.ledger.append(
            "proposal",
            source=proposal.source,
            assignments=[item.__dict__ for item in proposal.assignments],
        )
        return proposal

    def validate_proposal(self, proposal: Proposal) -> None:
        expected = set(self._tasks)
        actual = proposal.by_task()
        if set(actual) != expected or len(actual) != len(proposal.assignments):
            raise ContractError("proposal must assign every task exactly once")
        robots = {item.robot_id: item for item in self.robots}
        for task_id, robot_id in actual.items():
            profile = robots.get(robot_id)
            if profile is None or not profile.active:
                raise ContractError(f"assigned robot is unavailable: {robot_id}")
            missing = self._tasks[task_id].required_capabilities - profile.capabilities
            if missing:
                raise ContractError(f"{robot_id} lacks {sorted(missing)} for {task_id}")

    def next_action(self) -> Optional[ActionCommand]:
        if self._proposal is None:
            raise ContractError("propose() must be called before next_action()")
        if self._pending_command is not None:
            raise ContractError("feedback is required before requesting the next action")
        assignment = self._proposal.by_task()
        robots = {item.robot_id: item for item in self.robots}
        for task_id, task in self._tasks.items():
            if task_id in self._completed or task_id in self._failed:
                continue
            if any(dependency not in self._completed for dependency in task.dependencies):
                continue
            robot_id = assignment[task_id]
            if robot_id in self._unavailable_robots:
                self._reassign(task_id, excluded=set(self._unavailable_robots))
                if self._proposal.by_task().get(task_id) in self._unavailable_robots:
                    continue
                robot_id = self._proposal.by_task()[task_id]
            robot = robots[robot_id]
            skill_name = self._select_skill(task, robot)
            command = ActionCommand(task_id, robot_id, skill_name, dict(task.metadata))
            self._steps += 1
            self._pending_command = command
            self.ledger.append("execution", task_id=task_id, robot_id=robot_id, skill=skill_name)
            return command
        return None

    def feedback(self, feedback: Feedback) -> None:
        if not isinstance(feedback, Feedback):
            raise ContractError("executor must return a Feedback record")
        if feedback.task_id not in self._tasks:
            raise ContractError(f"unknown feedback task: {feedback.task_id}")
        if self._proposal is None or self._proposal.by_task().get(feedback.task_id) != feedback.robot_id:
            raise ContractError("feedback robot does not match current proposal")
        if self._pending_command is None:
            raise ContractError("feedback received without a pending action")
        if (feedback.task_id, feedback.robot_id) != (
            self._pending_command.task_id,
            self._pending_command.robot_id,
        ):
            raise ContractError("feedback does not match the pending action")
        self._pending_command = None
        self.ledger.append(
            "feedback",
            task_id=feedback.task_id,
            robot_id=feedback.robot_id,
            success=feedback.success,
            capability_lost=feedback.capability_lost,
            terminal=feedback.terminal,
            message=feedback.message,
        )
        if feedback.success:
            self._completed.add(feedback.task_id)
            return
        self._failed.add(feedback.task_id)
        if feedback.capability_lost:
            self._unavailable_robots.add(feedback.robot_id)
            self._reassign(feedback.task_id, excluded=set(self._unavailable_robots))
        else:
            self._reassign(feedback.task_id, excluded=set())

    def run(self, tasks: Iterable[TaskSpec], executor: Callable[[ActionCommand], Feedback], *, max_steps: int = 100) -> PEFAReport:
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps <= 0:
            raise ContractError("max_steps must be a positive integer")
        self._reset_run_state()
        self.propose(tasks)
        while self._steps < max_steps:
            command = self.next_action()
            if command is None:
                break
            result = executor(command)
            self.feedback(result)
            if len(self._completed) == len(self._tasks):
                break
        terminal = len(self._completed) == len(self._tasks)
        self.ledger.append("terminal", success=terminal, completed=sorted(self._completed))
        return PEFAReport(
            tuple(sorted(self._completed)),
            tuple(sorted(self._failed)),
            self._steps,
            True,
            terminal,
            self.unsupported,
            self.ledger.events,
        )

    def _reset_run_state(self) -> None:
        """Make repeated fixture runs independent and replayable."""

        self.ledger = EventLedger()
        self._tasks = {}
        self._proposal = None
        self._completed.clear()
        self._failed.clear()
        self._unavailable_robots.clear()
        self._pending_command = None
        self._steps = 0

    @staticmethod
    def _validate_dependency_graph(tasks: Sequence[TaskSpec]) -> None:
        """Reject cyclic task graphs before any planner or executor runs."""

        dependencies = {task.task_id: set(task.dependencies) for task in tasks}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(task_id: str) -> None:
            if task_id in visiting:
                raise ContractError(f"cyclic task dependency involving {task_id}")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in dependencies[task_id]:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in dependencies:
            visit(task_id)

    def _deterministic_proposal(self, tasks: Sequence[TaskSpec]) -> Proposal:
        assignments: list[PlanAssignment] = []
        for task in tasks:
            candidates = [
                profile
                for profile in self.robots
                if profile.active and task.required_capabilities <= profile.capabilities
            ]
            if not candidates:
                raise ContractError(f"no capable robot for {task.task_id}")
            assignments.append(PlanAssignment(task.task_id, candidates[0].robot_id))
        return Proposal(tuple(assignments), rationale="first capable active robot", source="deterministic_fallback")

    def _select_skill(self, task: TaskSpec, robot: CapabilityProfile) -> str:
        available = [skill for skill in self.skills if skill.required_capabilities <= robot.capabilities]
        if task.preferred_skills:
            available = [skill for skill in available if skill.name in task.preferred_skills]
        if self._action_selector is not None:
            selected = self._action_selector(task, robot, tuple(available))
            if selected not in {skill.name for skill in available}:
                raise ContractError(f"action selector returned unavailable skill: {selected}")
            return selected
        if not available:
            raise ContractError(f"no skill for task {task.task_id} and robot {robot.robot_id}")
        return available[0].name

    def _reassign(self, task_id: str, *, excluded: set[str]) -> None:
        task = self._tasks[task_id]
        candidates = [
            profile
            for profile in self.robots
            if profile.robot_id not in excluded and profile.active and task.required_capabilities <= profile.capabilities
        ]
        if not candidates:
            self.ledger.append("adjustment_failed", task_id=task_id, reason="no_capable_robot")
            return
        old = self._proposal.by_task()
        old[task_id] = candidates[0].robot_id
        self._proposal = Proposal(tuple(PlanAssignment(key, value) for key, value in old.items()), "feedback adjustment", "feedback_adjustment")
        self._failed.discard(task_id)
        self.ledger.append("adjustment", task_id=task_id, robot_id=candidates[0].robot_id)
