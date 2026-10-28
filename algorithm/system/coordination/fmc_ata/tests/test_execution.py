"""Host-consumption unit tests; fixtures are NOT native or physical evidence.

No solver, Isaac, BT module, subprocess, or external endpoint is invoked.
"""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from algorithm.common_coordination.contract import (
    AlgorithmCondition, Assignment, CoordinationTask, ExecutionFeedback,
    FailureCode, RobotCapability, TaskGraph,
)
from algorithm.system.coordination.fmc_ata.execution import (
    NativeFMCFeedbackExecutor, NativeFMCScheduleError, NativeFMCScheduleSession,
)
from task.contracts import (
    AlgorithmDecision, DependencyOutcome, EaiTaskPackage, ExecutionUpdate,
    OrchestrationLimits, RobotState, TaskRunRequest,
)
from task.runner import TaskRunner


def package():
    return EaiTaskPackage("static-mission", "unit-test-only", TaskGraph(tuple(
        CoordinationTask(name, "unit fixture " + name, frozenset({"work"}))
        for name in "ABCD"), "unit-test-only"), tuple(
        RobotState(RobotCapability(rid, frozenset({"work"})), (x, 0., 0.))
        for rid, x in (("r1", 0.), ("r2", 2.5))))


def native_decision():
    """Deliberately different graph vs native order: r1=B,A; r2=D,C."""
    return AlgorithmDecision(AlgorithmCondition("FMC-ATA", "test-fixture", "unit-test",
        "native_allocation_and_schedule_only", "test-fixture"),
        tuple(Assignment(task, rid, "native-fixture", "version-1")
              for task, rid in (("B", "r1"), ("A", "r1"), ("D", "r2"), ("C", "r2"))),
        provenance={"native_input": {
            "tasks": [{"id": i, "task_id": name} for i, name in enumerate("ABCD", 1)],
            "players": [{"id": -1, "robot_id": "r1"}, {"id": -2, "robot_id": "r2"}]},
            "native_output": {"players": [
                {"id": -1, "schedule": [{"task_id": 2}, {"task_id": 1}]},
                {"id": -2, "schedule": [{"task_id": 4}, {"task_id": 3}]}]},
            "physical_evidence": "not_evaluated"})


class Adapter:
    """Callable protocol double; deliberately not a VerifiedNativeAdapter type."""
    def __init__(self, decision=None):
        self.calls = 0
        self.decision = decision if decision is not None else native_decision()

    def __call__(self, task, robots):
        self.calls += 1
        return self.decision

    def replan(self, *args):
        raise AssertionError("native replanning is forbidden")

    def feedback(self, *args):
        raise AssertionError("host feedback must not be sent to FMC")

    def feedback_batch(self, *args):
        raise AssertionError("host feedback must not be sent to FMC")

    def close(self):
        raise AssertionError("static adapter has no live native session to close")


@pytest.fixture
def active():
    task, adapter = package(), Adapter()
    session = NativeFMCScheduleSession(adapter)
    first = session(task, task.robots)
    return task, adapter, session, first


def update(assignment, *, outcome="success", code=FailureCode.NONE,
           dependency=DependencyOutcome.COMPLETED):
    return ExecutionUpdate(ExecutionFeedback(assignment.task_id, assignment.robot_id,
        outcome, code, world_changes={"evidence_scope": "unit_test_only"}), dependency)


class Executor:
    def __init__(self):
        self.batches, self.updates, self.bound = [], [], None

    def bind_run(self, request):
        self.bound = request

    def execute_batch(self, assignments, tasks, *, scene):
        self.batches.append((tuple(assignments), scene))
        result = tuple(update(a) for a in assignments)
        self.updates.append(result)
        return tuple(reversed(result))  # wrapper must preserve dispatch identity/order


def test_head_only_and_original_schedule_preserved(active):
    task, adapter, session, first = active
    assert [(a.task_id, a.robot_id) for a in first.assignments] == [("B", "r1"), ("D", "r2")]
    assert not first.unassigned_tasks  # queue tails are deferred, not native-unassigned
    assert first.condition == adapter.decision.condition
    assert first.provenance["native_output"] == adapter.decision.provenance["native_output"]
    assert first.provenance["host_schedule_consumption"]["native_replanning"] is False
    assert first.provenance["physical_evidence"] == "not_evaluated"
    for _ in range(3):
        assert session.replan(task, task.robots).assignments == first.assignments
    assert adapter.calls == 1


def test_atomic_complete_batch_advances_one_head_per_robot(active):
    task, adapter, session, first = active
    session.begin_batch(first.assignments)
    with pytest.raises(NativeFMCScheduleError, match="feedback is required"):
        session.replan(task, task.robots)
    updates = tuple(update(a) for a in reversed(first.assignments))
    returned = session.feedback_batch(updates)
    assert [u.feedback.task_id for u in returned] == ["B", "D"]
    assert [a.task_id for a in session.replan(task, task.robots).assignments] == ["A", "C"]
    assert session.snapshot()["cursors"] == {"r1": 1, "r2": 1}
    assert adapter.calls == 1


@pytest.mark.parametrize("kind", ["missing", "duplicate", "wrong_robot", "tail", "unknown", "empty", "bare_feedback"])
def test_bad_entire_batch_is_atomic_and_stops_mission(active, kind):
    task, adapter, session, first = active
    session.begin_batch(first.assignments)
    good = [update(a) for a in first.assignments]
    if kind == "missing":
        bad = good[:1]
    elif kind == "duplicate":
        bad = good + good[:1]
    elif kind == "wrong_robot":
        bad = [good[0], replace(good[1], feedback=replace(good[1].feedback, robot_id="r1"))]
    elif kind == "tail":
        bad = [good[0], replace(good[1], feedback=replace(good[1].feedback, task_id="C"))]
    elif kind == "unknown":
        bad = [good[0], replace(good[1], feedback=replace(good[1].feedback, task_id="alien"))]
    elif kind == "empty":
        bad = []
    else:
        bad = [u.feedback for u in good]
    before = session.snapshot()
    with pytest.raises(NativeFMCScheduleError):
        session.feedback_batch(bad)
    after = session.snapshot()
    assert after["cursors"] == before["cursors"]
    assert after["accepted_feedback_batches"] == before["accepted_feedback_batches"]
    assert after["completed_task_ids"] == []
    assert after["status"] == "failed"
    assert session.replan(task, task.robots) is None
    assert adapter.calls == 1


@pytest.mark.parametrize("outcome,code,dependency", [
    ("not_executed", FailureCode.NONE, DependencyOutcome.COMPLETED),
    ("running", FailureCode.NONE, DependencyOutcome.COMPLETED),
    ("failure", FailureCode.NONE, DependencyOutcome.COMPLETED),
    ("success", FailureCode.EXECUTION_FAILURE, DependencyOutcome.COMPLETED),
    ("success", FailureCode.EXECUTION_FAILURE, DependencyOutcome.TERMINAL_FAILURE),
    ("failure", FailureCode.NONE, DependencyOutcome.TERMINAL_FAILURE),
    ("failure", FailureCode.EXECUTION_FAILURE, DependencyOutcome.RETRYABLE_FAILURE),
])
def test_contradictory_or_nonterminal_feedback_cannot_advance(active, outcome, code, dependency):
    task, adapter, session, first = active
    session.begin_batch(first.assignments)
    records = [update(first.assignments[0]), update(first.assignments[1], outcome=outcome, code=code, dependency=dependency)]
    with pytest.raises(NativeFMCScheduleError, match="contradict"):
        session.feedback_batch(records)
    assert session.snapshot()["cursors"] == {"r1": 0, "r2": 0}
    assert session.snapshot()["accepted_feedback_batches"] == []
    assert session.replan(task, task.robots) is None


def test_valid_terminal_failure_records_batch_but_stops_all_queues(active):
    task, adapter, session, first = active
    session.begin_batch(first.assignments)
    records = [update(first.assignments[0]), update(first.assignments[1], outcome="failure",
               code=FailureCode.EXECUTION_FAILURE, dependency=DependencyOutcome.TERMINAL_FAILURE)]
    assert session.feedback_batch(records) == tuple(records)
    snapshot = session.snapshot()
    assert snapshot["cursors"] == {"r1": 0, "r2": 0}
    assert len(snapshot["accepted_feedback_batches"]) == 1
    assert snapshot["status"] == "failed"
    assert session.replan(task, task.robots) is None
    with pytest.raises(NativeFMCScheduleError):
        session.begin_batch(first.assignments)
    assert adapter.calls == 1


def test_unsolicited_feedback_is_not_a_dispatch(active):
    _, _, session, first = active
    with pytest.raises(NativeFMCScheduleError, match="without registered"):
        session.feedback_batch(tuple(update(a) for a in first.assignments))
    assert session.snapshot()["completed_task_ids"] == []


def test_stale_feedback_cannot_consume_next_head(active):
    task, _, session, first = active
    session.begin_batch(first.assignments)
    old = tuple(update(a) for a in first.assignments)
    session.feedback_batch(old)
    heads = session.replan(task, task.robots).assignments
    session.begin_batch(heads)
    with pytest.raises(NativeFMCScheduleError):
        session.feedback_batch(old)
    assert session.snapshot()["cursors"] == {"r1": 1, "r2": 1}


@pytest.mark.parametrize("bad", ["tail", "duplicate", "altered_source", "changed_task"])
def test_invalid_dispatch_rejected_before_executor(active, bad):
    task, _, session, first = active
    executor = Executor()
    wrapper = NativeFMCFeedbackExecutor(session, executor)
    assignments = first.assignments
    tasks = {t.task_id: t for t in task.graph.tasks}
    if bad == "tail":
        assignments = (Assignment("A", "r1", "native-fixture", "version-1"),)
    elif bad == "duplicate":
        assignments = (first.assignments[0], first.assignments[0])
    elif bad == "altered_source":
        assignments = (replace(first.assignments[0], source="forged"),)
    else:
        tasks["B"] = replace(tasks["B"], description="changed")
    with pytest.raises(NativeFMCScheduleError):
        wrapper.execute_batch(assignments, tasks, scene={})
    assert not executor.batches
    assert session.snapshot()["cursors"] == {"r1": 0, "r2": 0}


def test_wrapper_forwards_same_objects_and_real_results(active):
    task, _, session, first = active
    executor, scene = Executor(), object()
    wrapper = NativeFMCFeedbackExecutor(session, executor)
    request = TaskRunRequest(task)
    wrapper.bind_run(request)
    assert executor.bound is request
    result = wrapper.execute_batch(first.assignments, {t.task_id: t for t in task.graph.tasks}, scene=scene)
    assert executor.batches == [(first.assignments, scene)]
    assert all(a is b for a, b in zip(result, executor.updates[0]))


def test_execute_only_singleton_path(active):
    task, adapter, session, first = active
    assignment = first.assignments[0]
    node = next(t for t in task.graph.tasks if t.task_id == assignment.task_id)
    record = update(assignment)
    seen = []
    class Single:
        def execute(self, a, t, *, scene):
            seen.append((a, t, scene))
            return record
    wrapper = NativeFMCFeedbackExecutor(session, Single())
    scene = object()
    assert wrapper.execute(assignment, node, scene=scene) is record
    assert seen == [(assignment, node, scene)]
    assert [a.task_id for a in session.replan(task, task.robots).assignments] == ["A", "D"]
    assert adapter.calls == 1


def test_batch_wrapper_singleton_fallback(active):
    task, _, session, first = active
    a = first.assignments[0]
    result = update(a)
    executor = SimpleNamespace(execute=lambda *args, **kwargs: result)
    assert NativeFMCFeedbackExecutor(session, executor).execute_batch((a,),
        {t.task_id: t for t in task.graph.tasks}, scene=None) == (result,)


def test_multi_batch_requires_batch_executor_before_any_side_effect(active):
    task, _, session, first = active
    executor = SimpleNamespace(execute=lambda *a, **k: pytest.fail("must not serially execute"))
    with pytest.raises(NativeFMCScheduleError, match="requires executor.execute_batch"):
        NativeFMCFeedbackExecutor(session, executor).execute_batch(first.assignments,
            {t.task_id: t for t in task.graph.tasks}, scene=None)
    assert session.snapshot()["status"] == "failed"


def test_executor_exception_does_not_advance_or_retry(active):
    task, adapter, session, first = active
    class Broken:
        def execute_batch(self, *args, **kwargs):
            raise RuntimeError("real executor failed")
    with pytest.raises(RuntimeError, match="real executor failed"):
        NativeFMCFeedbackExecutor(session, Broken()).execute_batch(first.assignments,
            {t.task_id: t for t in task.graph.tasks}, scene=None)
    assert session.replan(task, task.robots) is None
    assert session.snapshot()["cursors"] == {"r1": 0, "r2": 0}
    assert adapter.calls == 1


def test_runner_preserves_native_order_without_re_solving():
    task, adapter, executor = package(), Adapter(), Executor()
    session = NativeFMCScheduleSession(adapter)
    class Evaluator:
        def evaluate(self, **kwargs):
            return {"status": "N/A", "reason": "unit_test_no_physical_evidence"}
    result = TaskRunner(orchestration_executor=NativeFMCFeedbackExecutor(session, executor),
        evaluator=Evaluator()).run_orchestrated(TaskRunRequest(task), session, replan=session.replan,
        limits=OrchestrationLimits(max_rounds=4, max_dispatches=4))
    assert [[a.task_id for a in batch] for batch, _ in executor.batches] == [["B", "D"], ["A", "C"]]
    assert result.dispatch_count == 4
    assert set(result.task_states.values()) == {"completed"}
    assert result.verdict["status"] == "N/A"
    assert session.snapshot()["status"] == "completed"
    assert not session.replan(task, task.robots).assignments
    session.close()
    session.close()
    assert session.replan(task, task.robots) is None
    assert adapter.calls == 1


def test_runner_stops_after_failed_batch_without_tail_dispatch():
    task, adapter = package(), Adapter()
    session = NativeFMCScheduleSession(adapter)
    class Failing(Executor):
        def execute_batch(self, assignments, tasks, *, scene):
            result = super().execute_batch(assignments, tasks, scene=scene)
            return (update(assignments[0], outcome="failure", code=FailureCode.TIMEOUT,
                           dependency=DependencyOutcome.TERMINAL_FAILURE), update(assignments[1]))
    executor = Failing()
    result = TaskRunner(orchestration_executor=NativeFMCFeedbackExecutor(session, executor)).run_orchestrated(
        TaskRunRequest(task), session, replan=session.replan)
    assert len(executor.batches) == 1
    assert result.termination_reason == "planner_stopped"
    assert result.task_states["A"] == result.task_states["C"] == "pending"
    assert result.verdict["status"] == "N/A"
    assert adapter.calls == 1


@pytest.mark.parametrize("kind", ["partial", "reordered", "unassigned", "missing_raw"])
def test_native_output_not_repaired_or_resolved_again(kind):
    task, d = package(), native_decision()
    if kind == "partial":
        d = replace(d, assignments=d.assignments[:-1])
    elif kind == "reordered":
        d = replace(d, assignments=tuple(reversed(d.assignments)))
    elif kind == "unassigned":
        d = replace(d, unassigned_tasks=("A",))
    else:
        d = replace(d, provenance={})
    adapter = Adapter(d)
    session = NativeFMCScheduleSession(adapter)
    with pytest.raises(NativeFMCScheduleError):
        session(task, task.robots)
    with pytest.raises(NativeFMCScheduleError, match="already used"):
        session(task, task.robots)
    assert session.replan(task, task.robots) is None
    assert adapter.calls == 1


def test_solver_exception_consumes_the_only_attempt():
    task, calls = package(), []
    def failing(*args):
        calls.append(1)
        raise RuntimeError("native timeout")
    session = NativeFMCScheduleSession(failing)
    with pytest.raises(RuntimeError, match="native timeout"):
        session(task, task.robots)
    with pytest.raises(NativeFMCScheduleError, match="already used"):
        session(task, task.robots)
    assert calls == [1]


def test_task_or_roster_changes_cannot_trigger_hidden_resolve(active):
    task, adapter, session, _ = active
    changed = replace(task, scene_id="different")
    with pytest.raises(NativeFMCScheduleError, match="changed"):
        session.replan(changed, task.robots)
    assert adapter.calls == 1


def test_runner_state_cannot_fabricate_completion(active):
    task, adapter, session, _ = active
    state = SimpleNamespace(completed_task_ids=("B",), pending_task_ids=("A", "C", "D"),
                            failed_task_ids=(), blocked_task_ids=())
    with pytest.raises(NativeFMCScheduleError, match="contradicts"):
        session.replan(task, task.robots, state)
    assert session.snapshot()["cursors"] == {"r1": 0, "r2": 0}
    assert adapter.calls == 1


def test_caller_mutation_does_not_change_saved_plan_or_receipts(active):
    task, adapter, session, first = active
    first.provenance["native_output"]["players"].clear()
    adapter.decision.provenance["native_input"]["tasks"].clear()
    snapshot = session.snapshot()
    snapshot["cursors"]["r1"] = 999
    assert len(session.replan(task, task.robots).provenance["native_input"]["tasks"]) == 4
    assert len(session.replan(task, task.robots).provenance["native_output"]["players"]) == 2
    session.begin_batch(first.assignments)
    records = [update(a) for a in first.assignments]
    session.feedback_batch(records)
    records[0].feedback.world_changes["evidence_scope"] = "forged-after-commit"
    assert session.snapshot()["accepted_feedback_batches"][0][0]["feedback"]["world_changes"]["evidence_scope"] == "unit_test_only"


def test_close_is_idempotent_and_never_calls_adapter(active):
    task, adapter, session, first = active
    session.close()
    session.close()
    assert session.snapshot()["closed"] is True
    assert session.replan(task, task.robots) is None
    with pytest.raises(NativeFMCScheduleError, match="closed"):
        session.begin_batch(first.assignments)
    assert adapter.calls == 1


def test_close_before_plan_does_not_solve():
    task, adapter = package(), Adapter()
    session = NativeFMCScheduleSession(adapter)
    session.close()
    with pytest.raises(NativeFMCScheduleError, match="closed"):
        session(task, task.robots)
    assert adapter.calls == 0
