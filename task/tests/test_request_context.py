"""Extended public request context survives admission and tamper checks."""
from dataclasses import replace

import pytest

from algorithm.common_coordination.contract import Assignment, CoordinationTask, FailureCode
from algorithm.skills.contracts import SkillCommand, SkillPlan, SkillRequest
from task import DependencyOutcome, EaiTaskPackage, TaskRunRequest
from algorithm.common_coordination.contract import TaskGraph
from task.executors import BoundSkill, CooperativeBinding, CooperativeBatchExecutor, SkillTaskExecutor


@pytest.mark.parametrize("cooperative", [False, True])
@pytest.mark.parametrize("field", ["simulator_snapshot", "timing", "budget", "branch_context"])
def test_provider_context_mutation_rejected_before_any_runtime(cooperative, field):
    class Provider:
        skill_id = "fixture"

        def plan(self, request, *, observation=None):
            getattr(request, field)["value"]["nested"] = "mutated"
            return SkillPlan(request.request_id, request.skill_id, request.robot_id,
                             (SkillCommand("act"),))

    request = SkillRequest("r", "fixture", "robot", task_id="t",
                           **{field: {"value": {"nested": "original"}}})
    executor = make_executor(cooperative, request, Provider())
    result = executor.execute(Assignment("t", "robot", "test"), CoordinationTask("t", "Test"), scene={})
    assert result.feedback.failure_code is FailureCode.INVALID_PLAN
    assert "mutated" in result.feedback.message
    assert executor.resources.snapshot()["leases"] == []
    assert getattr(request, field)["value"]["nested"] == "original"


def make_executor(cooperative, request, provider):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid request must not reach runtime")

    binding_type = CooperativeBinding if cooperative else BoundSkill
    binding = binding_type(request, provider, forbidden, lambda **kw: None)
    if cooperative:
        executor = CooperativeBatchExecutor(lambda *a, **kw: binding,
            step=forbidden, neutral_actions=forbidden, max_ticks=1,
            max_cancel_ticks=1, expected_scene_id="scene")
    else:
        executor = SkillTaskExecutor(lambda *a, **kw: binding, expected_scene_id="scene")
    executor.bind_run(TaskRunRequest(EaiTaskPackage(
        "package", "scene", TaskGraph((CoordinationTask("t", "Test"),), "test"), ())))
    return executor


@pytest.mark.parametrize("cooperative", [False, True])
@pytest.mark.parametrize("changes", [{"task_id": "foreign"}, {"skill_version": "unsupported"}])
def test_foreign_task_or_version_rejected_before_planning(cooperative, changes):
    class Provider:
        skill_id = "fixture"

        def plan(self, *args, **kwargs):
            pytest.fail("invalid identity must not be planned")

    request = replace(SkillRequest("r", "fixture", "robot"), **changes)
    result = make_executor(cooperative, request, Provider()).execute(
        Assignment("t", "robot", "test"), CoordinationTask("t", "Test"), scene={})
    assert result.dependency_outcome is DependencyOutcome.TERMINAL_FAILURE
    assert result.feedback.failure_code is FailureCode.INVALID_PLAN
