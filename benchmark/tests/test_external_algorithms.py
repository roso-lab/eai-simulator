from __future__ import annotations

import pytest

from algorithm.common_coordination.contract import ExecutionFeedback, FailureCode
from benchmark.external_algorithms import (
    EXTERNAL_FIRE_RESCUE_ALGORITHMS,
    ExternalAlgorithmDecisionAdapter,
    package_for_external_algorithm,
)
from task.contracts import DependencyOutcome, ExecutionUpdate, OrchestrationLimits, TaskRunRequest
from task.fixtures.fire_rescue import build_fire_rescue_assignment_request
from task.runner import TaskRunner


@pytest.mark.parametrize("algorithm", EXTERNAL_FIRE_RESCUE_ALGORITHMS)
def test_external_algorithms_emit_typed_decisions_without_physical_claims(algorithm: str) -> None:
    request = build_fire_rescue_assignment_request()
    package = package_for_external_algorithm(request)
    adapter = ExternalAlgorithmDecisionAdapter(request, algorithm)

    decision = adapter(package, package.robots)

    assert decision.condition.algorithm_id.endswith("-adapted-to-EAI")
    assert set(decision.assignments) or set(decision.unassigned_tasks)
    assert "physical_success" not in decision.provenance
    assert decision.provenance["adapter"].endswith("ExternalAlgorithmDecisionAdapter")


def test_external_decision_runs_through_task_runner_and_stays_na() -> None:
    request = build_fire_rescue_assignment_request()
    package = package_for_external_algorithm(request)
    # This tests the legacy runner seam without a method-specific Torch runtime.
    # Native SADCHER acceptance is covered in its isolated environment.
    adapter = ExternalAlgorithmDecisionAdapter(request, "lammap")

    result = TaskRunner().run(TaskRunRequest(package), adapter)

    assert result.verdict["status"] == "N/A"
    assert result.verdict["reason"] == "physical_evidence_required"
    assert result.feedback
    assert all(item.failure_code is FailureCode.MILESTONE_UNVERIFIED for item in result.feedback)


def test_robot_fleet_feedback_replan_retries_pending_task() -> None:
    request = build_fire_rescue_assignment_request()
    package = package_for_external_algorithm(request)
    adapter = ExternalAlgorithmDecisionAdapter(request, "robot_fleet")
    calls = {"yellow": 0}

    class Executor:
        def execute(self, assignment, task, *, scene):
            del task, scene
            if assignment.task_id == "yellow" and calls["yellow"] == 0:
                calls["yellow"] += 1
                return ExecutionUpdate(
                    ExecutionFeedback(
                        assignment.task_id,
                        assignment.robot_id,
                        "timeout",
                        failure_code=FailureCode.TIMEOUT,
                    ),
                    DependencyOutcome.RETRYABLE_FAILURE,
                )
            if assignment.task_id == "yellow":
                calls["yellow"] += 1
            return ExecutionUpdate(
                ExecutionFeedback(assignment.task_id, assignment.robot_id, "success"),
                DependencyOutcome.COMPLETED,
            )

    result = TaskRunner(orchestration_executor=Executor()).run_orchestrated(
        TaskRunRequest(package),
        adapter,
        replan=adapter.replan,
        limits=OrchestrationLimits(max_rounds=8, max_dispatches=16),
    )

    assert result.task_states == {task.task_id: "completed" for task in package.graph.tasks}
    assert result.termination_reason == "all_dependencies_completed"
    assert len(result.decisions) >= 2
    assert calls["yellow"] == 2


def test_happo_is_explicitly_not_applicable_and_is_not_dispatched() -> None:
    request = build_fire_rescue_assignment_request()
    package = package_for_external_algorithm(request)
    adapter = ExternalAlgorithmDecisionAdapter(request, "harl_happo")

    result = TaskRunner().run_orchestrated(
        TaskRunRequest(package),
        adapter,
        replan=adapter.replan,
        limits=OrchestrationLimits(max_rounds=2, max_dispatches=4),
    )

    assert not result.decision.assignments
    assert set(result.decision.unassigned_tasks) == {task.task_id for task in package.graph.tasks}
    assert result.verdict["status"] == "N/A"


def test_external_adapter_rejects_runtime_roster_mismatch() -> None:
    request = build_fire_rescue_assignment_request()
    package = package_for_external_algorithm(request)
    adapter = ExternalAlgorithmDecisionAdapter(request, "lammap")

    with pytest.raises(ValueError, match="roster mismatch"):
        adapter(package, package.robots[:-1])
