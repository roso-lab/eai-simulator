from __future__ import annotations

import json

import pytest

from algorithm.common_coordination.contract import (
    AlgorithmCondition,
    Assignment,
    CoordinationTask,
    ExecutionFeedback,
    FailureCode,
    RobotCapability,
    TaskGraph,
)
from task.contracts import (
    AlgorithmDecision,
    DependencyOutcome,
    EaiTaskPackage,
    ExecutionUpdate,
    OrchestrationLimits,
    RobotState,
    TaskRunRequest,
)
from task.runner import TaskRunner


CONDITION = AlgorithmCondition(
    algorithm_id="test-planner",
    source_reference="inline-test-fixture",
    implementation="test",
    execution_scope="assignment_only",
    planner="fixture",
)


def _package(*tasks: CoordinationTask) -> EaiTaskPackage:
    return EaiTaskPackage(
        task_id="generic-dag",
        scene_id="caller-owned-scene",
        graph=TaskGraph(tasks=tuple(tasks), source="inline-test-fixture"),
        robots=(
            RobotState(RobotCapability("r1", frozenset({"navigate"}))),
            RobotState(RobotCapability("r2", frozenset({"navigate", "inspect"}))),
        ),
    )


def _decision(*assignments: Assignment) -> AlgorithmDecision:
    return AlgorithmDecision(condition=CONDITION, assignments=assignments)


def _assignment(task_id: str, robot_id: str) -> Assignment:
    return Assignment(task_id, robot_id, source="test-planner")


def _update(
    assignment: Assignment,
    outcome: DependencyOutcome = DependencyOutcome.COMPLETED,
) -> ExecutionUpdate:
    failure = (
        FailureCode.NONE
        if outcome is DependencyOutcome.COMPLETED
        else FailureCode.EXECUTION_FAILURE
    )
    return ExecutionUpdate(
        feedback=ExecutionFeedback(
            task_id=assignment.task_id,
            robot_id=assignment.robot_id,
            outcome=outcome.value,
            failure_code=failure,
        ),
        dependency_outcome=outcome,
    )


class CompletingBatchExecutor:
    def __init__(self) -> None:
        self.batches: list[tuple[str, ...]] = []

    def execute_batch(self, assignments, tasks, *, scene):
        assert scene == {"scene_id": "caller-owned-scene"}
        assert set(tasks) >= {item.task_id for item in assignments}
        self.batches.append(tuple(item.task_id for item in assignments))
        return tuple(_update(item) for item in assignments)


class ExplodingEvaluator:
    def evaluate(self, **kwargs):
        raise AssertionError("an evaluator must not run without an executor")


def test_one_shot_runner_is_na_without_executor_even_if_evaluator_exists():
    package = _package(CoordinationTask("inspect", "Inspect a target"))

    result = TaskRunner(evaluator=ExplodingEvaluator()).run(
        TaskRunRequest(package),
        lambda task, robots: (),
        condition=CONDITION,
    )

    assert result.verdict == {
        "status": "N/A",
        "reason": "physical_evidence_required",
        "unassigned_tasks": [],
    }
    assert result.ledger.events[-1]["kind"] == "verdict"


def test_one_shot_feedback_identity_must_match_dispatch():
    package = _package(CoordinationTask("inspect", "Inspect a target"))

    class WrongExecutor:
        def execute(self, assignment, task, *, scene):
            return ExecutionFeedback("inspect", "r2", "completed")

    with pytest.raises(ValueError, match="identity does not match"):
        TaskRunner(executor=WrongExecutor()).run(
            TaskRunRequest(package),
            lambda task, robots: _decision(_assignment("inspect", "r1")),
        )


def test_task_package_uses_inline_json_and_binds_runtime_without_mutation(tmp_path):
    source = tmp_path / "generic_task.json"
    source.write_text(
        json.dumps(
            {
                "task_id": "generic",
                "scene_id": "external-scene",
                "tasks": [{"task_id": "move", "description": "Move payload"}],
                "robots": [],
            }
        ),
        encoding="utf-8",
    )
    package = EaiTaskPackage.from_json(source)
    robot = RobotState(RobotCapability("r1", frozenset({"navigate"})))

    bound = package.bind_runtime(robots=(robot,), scene_id="live-scene")

    assert package.robots == ()
    assert bound.scene_id == "live-scene"
    assert bound.robots[0].capability.robot_id == "r1"


def test_orchestrated_runner_executes_only_ready_dag_tasks_in_bounded_batches():
    package = _package(
        CoordinationTask("map", "Map area", required_capabilities=frozenset({"navigate"})),
        CoordinationTask("inspect", "Inspect", required_capabilities=frozenset({"inspect"})),
        CoordinationTask(
            "report",
            "Report map",
            required_capabilities=frozenset({"inspect"}),
            prerequisites=("map",),
        ),
    )
    executor = CompletingBatchExecutor()
    initial = _decision(
        _assignment("map", "r1"),
        _assignment("inspect", "r2"),
        _assignment("report", "r2"),
    )

    result = TaskRunner(orchestration_executor=executor).run_orchestrated(
        TaskRunRequest(package),
        lambda task, robots: initial,
        limits=OrchestrationLimits(max_rounds=3, max_dispatches=3),
    )

    assert executor.batches == [("map", "inspect"), ("report",)]
    assert result.task_states == {
        "map": "completed",
        "inspect": "completed",
        "report": "completed",
    }
    assert result.termination_reason == "all_dependencies_completed"
    assert result.verdict["status"] == "N/A"
    assert result.verdict["reason"] == "physical_evidence_required"


def test_retryable_failure_requires_explicit_replan_before_retry():
    package = _package(
        CoordinationTask("move", "Move", required_capabilities=frozenset({"navigate"})),
        CoordinationTask("inspect", "Inspect", prerequisites=("move",)),
    )

    class RetryExecutor:
        def __init__(self):
            self.calls = 0

        def execute(self, assignment, task, *, scene):
            self.calls += 1
            if self.calls == 1:
                return _update(assignment, DependencyOutcome.RETRYABLE_FAILURE)
            return _update(assignment)

    no_replan_executor = RetryExecutor()
    initial = _decision(_assignment("move", "r1"))
    without_replan = TaskRunner(
        orchestration_executor=no_replan_executor
    ).run_orchestrated(TaskRunRequest(package), lambda task, robots: initial)
    assert no_replan_executor.calls == 1
    assert without_replan.task_states["move"] == "pending"
    assert without_replan.termination_reason == "no_ready_assignments"

    executor = RetryExecutor()

    def replan(task, robots, state):
        if state.pending_task_ids == ("move", "inspect"):
            return _decision(_assignment("move", "r2"))
        if state.pending_task_ids == ("inspect",):
            return _decision(_assignment("inspect", "r2"))
        return None

    result = TaskRunner(orchestration_executor=executor).run_orchestrated(
        TaskRunRequest(package),
        lambda task, robots: initial,
        replan=replan,
        limits=OrchestrationLimits(max_rounds=4, max_dispatches=4),
    )
    assert result.task_states == {"move": "completed", "inspect": "completed"}
    assert result.dispatch_count == 3
    assert len(result.decisions) == 3


def test_terminal_failure_blocks_descendants_but_keeps_independent_work():
    package = _package(
        CoordinationTask("failed-root", "Root"),
        CoordinationTask("blocked-child", "Child", prerequisites=("failed-root",)),
        CoordinationTask("independent", "Independent"),
    )

    class PartialExecutor:
        def execute_batch(self, assignments, tasks, *, scene):
            return tuple(
                _update(
                    item,
                    DependencyOutcome.TERMINAL_FAILURE
                    if item.task_id == "failed-root"
                    else DependencyOutcome.COMPLETED,
                )
                for item in assignments
            )

    result = TaskRunner(orchestration_executor=PartialExecutor()).run_orchestrated(
        TaskRunRequest(package),
        lambda task, robots: _decision(
            _assignment("failed-root", "r1"),
            _assignment("blocked-child", "r1"),
            _assignment("independent", "r2"),
        ),
    )

    assert result.task_states == {
        "failed-root": "failed",
        "blocked-child": "blocked",
        "independent": "completed",
    }
    assert result.termination_reason == "partial_failure"


@pytest.mark.parametrize(
    "decision, message",
    [
        (_decision(_assignment("missing", "r1")), "unknown tasks"),
        (_decision(_assignment("known", "missing")), "unknown or inactive robot"),
        (
            _decision(_assignment("known", "r1"), _assignment("known", "r2")),
            "assigns a task more than once",
        ),
        (
            AlgorithmDecision(
                condition=CONDITION,
                assignments=(_assignment("known", "r1"),),
                unassigned_tasks=("known",),
            ),
            "both assigned and unassigned",
        ),
    ],
)
def test_unknown_and_duplicate_decisions_fail_closed(decision, message):
    package = _package(CoordinationTask("known", "Known"))
    with pytest.raises(ValueError, match=message):
        TaskRunner().run_orchestrated(
            TaskRunRequest(package),
            lambda task, robots: decision,
        )


def test_capability_mismatch_fails_before_executor_dispatch():
    package = _package(
        CoordinationTask(
            "inspect",
            "Inspect",
            required_capabilities=frozenset({"inspect"}),
        )
    )
    with pytest.raises(ValueError, match="lacks capabilities"):
        TaskRunner().run_orchestrated(
            TaskRunRequest(package),
            lambda task, robots: _decision(_assignment("inspect", "r1")),
        )


def test_batch_feedback_must_match_every_dispatched_assignment():
    package = _package(CoordinationTask("known", "Known"))

    class WrongFeedbackExecutor:
        def execute_batch(self, assignments, tasks, *, scene):
            return (_update(_assignment("known", "r2")),)

    with pytest.raises(ValueError, match="do not match"):
        TaskRunner(
            orchestration_executor=WrongFeedbackExecutor()
        ).run_orchestrated(
            TaskRunRequest(package),
            lambda task, robots: _decision(_assignment("known", "r1")),
        )


def test_hard_limits_stop_before_exceeding_dispatch_budget():
    package = _package(
        CoordinationTask("one", "One"),
        CoordinationTask("two", "Two"),
    )
    executor = CompletingBatchExecutor()
    result = TaskRunner(orchestration_executor=executor).run_orchestrated(
        TaskRunRequest(package),
        lambda task, robots: _decision(
            _assignment("one", "r1"),
            _assignment("two", "r2"),
        ),
        limits=OrchestrationLimits(max_rounds=1, max_dispatches=1),
    )
    assert executor.batches == []
    assert result.dispatch_count == 0
    assert result.termination_reason == "max_dispatches_reached"


def test_round_limit_stops_before_an_extra_replan_callback():
    package = _package(
        CoordinationTask("one", "One"),
        CoordinationTask("two", "Two", prerequisites=("one",)),
    )
    executor = CompletingBatchExecutor()
    replans = []

    def replan(task, robots, state):
        replans.append(state)
        return _decision(_assignment("two", "r2"))

    result = TaskRunner(orchestration_executor=executor).run_orchestrated(
        TaskRunRequest(package),
        lambda task, robots: _decision(_assignment("one", "r1")),
        replan=replan,
        limits=OrchestrationLimits(max_rounds=1, max_dispatches=2),
    )
    assert replans == []
    assert result.task_states == {"one": "completed", "two": "pending"}
    assert result.termination_reason == "max_rounds_reached"


def test_orchestrated_runner_is_na_without_executor_even_if_evaluator_exists():
    package = _package(CoordinationTask("known", "Known"))
    result = TaskRunner(evaluator=ExplodingEvaluator()).run_orchestrated(
        TaskRunRequest(package),
        lambda task, robots: _decision(_assignment("known", "r1")),
    )
    assert result.termination_reason == "executor_unavailable"
    assert result.verdict["status"] == "N/A"
    assert result.verdict["reason"] == "physical_evidence_required"
