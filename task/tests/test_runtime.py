"""Generic runtime integration; fixtures deliberately carry no business task."""

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor

import pytest

from algorithm.common_coordination.contract import (
    AlgorithmCondition, Assignment, CoordinationTask, FailureCode, RobotCapability, TaskGraph,
)
from algorithm.skills.contracts import SkillCommand, SkillPlan, SkillRequest
from task.contracts import AlgorithmDecision, DependencyOutcome, EaiTaskPackage, ExecutionUpdate, RobotState, TaskRunRequest
from algorithm.common_coordination.contract import ExecutionFeedback
from task.executors import BoundSkill, ExecutionReceipt, SkillTaskExecutor
from task.observation import WorldSnapshot
from task.resources import ResourceManager
from task.runner import TaskRunner


CONDITION = AlgorithmCondition("fixture", "inline", "fixture", "protocol_synthetic", "fixture")
TASK = CoordinationTask("move", "Synthetic action", frozenset({"act"}))
ASSIGNMENT = Assignment("move", "r1", "fixture")


def roster(*, first_active=True):
    return tuple(RobotState(RobotCapability(name, frozenset({"act"}), active),
                            (float(index), 0.0, 0.0))
                 for index, (name, active) in enumerate((("r1", first_active), ("r2", True))))


def observe(scene):
    return WorldSnapshot(scene_id="test-scene", revision=scene["tick"], tick=scene["tick"],
                         robots=roster(first_active=scene.get("first_active", True)),
                         objects={"item": {"phase": scene.get("phase", "initial")}})


class Provider:
    skill_id = "test.skill"

    def plan(self, request, *, observation=None):
        return SkillPlan(request.request_id, request.skill_id, request.robot_id,
                         (SkillCommand("act", dict(request.parameters)),))


def receipt(plan, *, outcome=DependencyOutcome.COMPLETED, quiescent=True):
    return ExecutionReceipt(plan.request_id, plan.skill_id, plan.robot_id, outcome, quiescent,
                            FailureCode.NONE if outcome is DependencyOutcome.COMPLETED
                            else FailureCode.EXECUTION_FAILURE)


def resolver(execute=None, *, stop=None, provider=None, request_id="request-1", validate=None):
    def resolve(assignment, task, *, scene):
        return BoundSkill(
            SkillRequest(request_id, "test.skill", assignment.robot_id),
            provider or Provider(), execute or (lambda plan, **kwargs: receipt(plan)),
            validate or (lambda **kwargs: None), ("object:item",), stop,
        )
    return resolve


def test_snapshot_detaches_inputs_and_all_accessors():
    robots = list(roster())
    objects = {"item": {"value": [1]}}
    snapshot = WorldSnapshot(scene_id="s", revision=0, tick=0, robots=robots, objects=objects)
    digest = snapshot.digest
    objects["item"]["value"].append(2)
    snapshot.to_dict()["objects"]["item"]["value"].append(3)
    snapshot.robots[0].metadata["changed"] = True
    assert snapshot.digest == digest
    assert snapshot.to_dict()["objects"]["item"]["value"] == [1]
    assert "changed" not in snapshot.robots[0].metadata


@pytest.mark.parametrize("kwargs", [
    {"revision": -1}, {"tick": True}, {"scene_id": ""},
    {"robots": roster() + roster()}, {"objects": {"x": float("nan")}},
])
def test_snapshot_rejects_invalid_observations(kwargs):
    values = dict(scene_id="s", revision=0, tick=0, robots=roster())
    values.update(kwargs)
    with pytest.raises((ValueError, TypeError)):
        WorldSnapshot(**values)


@pytest.mark.parametrize("scene,revision,tick,robots", [
    ("other", 2, 2, roster()), ("s", 0, 2, roster()),
    ("s", 2, 0, roster()), ("s", 1, 1, roster(first_active=False)),
])
def test_snapshot_rejects_stale_or_reused_revision(scene, revision, tick, robots):
    before = WorldSnapshot(scene_id="s", revision=1, tick=1, robots=roster())
    after = WorldSnapshot(scene_id=scene, revision=revision, tick=tick, robots=robots)
    with pytest.raises(ValueError):
        after.validate_successor(before)


def test_executor_rejects_duplicate_requests_and_cross_session_reuse():
    calls = []
    def execute(plan, **kwargs):
        calls.append(plan.request_id)
        return receipt(plan)
    executor = SkillTaskExecutor(resolver(execute))
    scene = {}
    assert executor.execute(ASSIGNMENT, TASK, scene=scene).dependency_outcome is DependencyOutcome.COMPLETED
    again = executor.execute(ASSIGNMENT, TASK, scene=scene)
    assert again.feedback.failure_code is FailureCode.INVALID_PLAN
    assert calls == ["request-1"]
    assert executor.resources.snapshot()["leases"] == []
    with pytest.raises(ValueError, match="across sessions"):
        executor.execute(ASSIGNMENT, TASK, scene={})


def test_resource_contention_does_not_execute_or_consume_request():
    resources = ResourceManager()
    held = resources.acquire("other", ("object:item",))
    executor = SkillTaskExecutor(resolver(), resources=resources)
    scene = {}
    assert executor.execute(ASSIGNMENT, TASK, scene=scene).dependency_outcome is DependencyOutcome.RETRYABLE_FAILURE
    resources.release(held)
    assert executor.execute(ASSIGNMENT, TASK, scene=scene).dependency_outcome is DependencyOutcome.COMPLETED


@pytest.mark.parametrize("stop_confirmed", [True, False, None, 1])
def test_runtime_failure_releases_only_after_explicit_stop(stop_confirmed):
    def execute(plan, **kwargs):
        raise RuntimeError("driver failed after starting command")
    executor = SkillTaskExecutor(resolver(execute, stop=lambda **kwargs: stop_confirmed))
    result = executor.execute(ASSIGNMENT, TASK, scene={})
    if stop_confirmed is True:
        assert result.dependency_outcome is DependencyOutcome.RETRYABLE_FAILURE
        assert executor.resources.snapshot()["leases"] == []
    else:
        assert result.feedback.failure_code is FailureCode.SAFE_STOP
        assert executor.resources.snapshot()["leases"][0]["state"] == "quarantined"
        lease, = executor.quarantined_leases
        executor.resources.recover(lease, quiescent=True)
        executor.resources.release(lease)


def test_invalid_runtime_identity_quarantines_and_never_completes():
    def execute(plan, **kwargs):
        return replace(receipt(plan), request_id="another-request")
    executor = SkillTaskExecutor(resolver(execute))
    result = executor.execute(ASSIGNMENT, TASK, scene={})
    assert result.dependency_outcome is DependencyOutcome.TERMINAL_FAILURE
    assert executor.quarantined_leases


def test_unconfirmed_quiescence_cannot_unlock_dependencies():
    executor = SkillTaskExecutor(resolver(lambda plan, **kwargs: receipt(plan, quiescent=False),
                                         stop=lambda **kwargs: True))
    assert executor.execute(ASSIGNMENT, TASK, scene={}).dependency_outcome is DependencyOutcome.RETRYABLE_FAILURE


def test_wrong_thread_never_calls_binding_or_controller():
    executor = SkillTaskExecutor(lambda *args, **kwargs: pytest.fail("unexpected binding"))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(executor.execute, ASSIGNMENT, TASK, scene={})
        with pytest.raises(RuntimeError, match="owner thread"):
            future.result()


def test_invalid_plan_identity_and_live_binding_do_not_execute():
    class BadProvider(Provider):
        def plan(self, request, **kwargs):
            return replace(super().plan(request, **kwargs), robot_id="r2")
    executor = SkillTaskExecutor(resolver(provider=BadProvider()))
    assert executor.execute(ASSIGNMENT, TASK, scene={}).feedback.failure_code is FailureCode.INVALID_PLAN
    assert executor.resources.snapshot()["leases"] == []


def test_changed_binding_under_lease_fails_before_command_and_releases():
    checks = []
    def validate(**kwargs):
        checks.append(1)
        if len(checks) == 2:
            raise ValueError("object disappeared")
    executor = SkillTaskExecutor(resolver(validate=validate,
        execute=lambda *args, **kwargs: pytest.fail("unexpected command")))
    result = executor.execute(ASSIGNMENT, TASK, scene={})
    assert result.feedback.failure_code is FailureCode.INVALID_PLAN
    assert executor.resources.snapshot()["leases"] == []


def test_live_failure_replan_retry_dependency_and_evaluator_chain():
    scene = {"tick": 0, "first_active": True, "phase": "initial"}
    package = EaiTaskPackage("fixture", "test-scene", TaskGraph((
        TASK, CoordinationTask("verify", "Synthetic successor", frozenset({"act"}), ("move",)),
    ), "inline"), roster())
    dispatches = []
    provider_observations = []
    class ObservingProvider(Provider):
        def plan(self, request, *, observation=None):
            provider_observations.append(observation)
            return super().plan(request, observation=observation)
    def resolve(assignment, task, *, scene):
        def execute(plan, *, scene):
            dispatches.append((task.task_id, assignment.robot_id))
            scene["tick"] += 1
            if len(dispatches) == 1:
                scene["first_active"] = False
                return receipt(plan, outcome=DependencyOutcome.RETRYABLE_FAILURE)
            scene["phase"] = "moved" if task.task_id == "move" else "verified"
            return receipt(plan)
        return BoundSkill(
            SkillRequest(f"attempt-{len(dispatches)}", "test.skill", assignment.robot_id),
            ObservingProvider(), execute, lambda **kwargs: None, ("object:item",),
        )
    executor = SkillTaskExecutor(resolve, observation_provider=observe, expected_scene_id="test-scene")
    def replan(task, robots, state):
        assert not robots[0].capability.active
        assert task.metadata["runtime_observation"]["tick"] == scene["tick"]
        next_task = "move" if "move" in state.pending_task_ids else "verify"
        return AlgorithmDecision(CONDITION, (Assignment(next_task, "r2", "fixture"),))
    class Evaluator:
        def evaluate(self, *, task, decision, feedback, scene):
            assert task.metadata["runtime_observation"]["objects"]["item"]["phase"] == "verified"
            # This is synthetic protocol verification, never physical evidence.
            return {"status": "protocol_checked", "physical_success": None}
    runner = TaskRunner(scene_provider=lambda _: scene, orchestration_executor=executor,
                        observation_provider=observe, evaluator=Evaluator())
    result = runner.run_orchestrated(TaskRunRequest(package),
        lambda task, robots: AlgorithmDecision(CONDITION, (ASSIGNMENT,)), replan=replan)
    assert dispatches == [("move", "r1"), ("move", "r2"), ("verify", "r2")]
    assert result.task_states == {"move": "completed", "verify": "completed"}
    assert result.verdict["physical_success"] is None
    assert [obs["tick"] for obs in provider_observations] == [0, 1, 2]
    assert package.robots[0].capability.active  # static task is not overwritten
    assert executor.resources.snapshot()["leases"] == []
    assert len([e for e in result.ledger.events if e["kind"] == "world_observed"]) == 4


def test_runner_rejects_stale_observation_before_dispatch():
    snapshots = iter((WorldSnapshot(scene_id="test-scene", revision=2, tick=2, robots=roster()),
                      WorldSnapshot(scene_id="test-scene", revision=1, tick=1, robots=roster())))
    package = EaiTaskPackage("fixture", "test-scene", TaskGraph((TASK,), "inline"), roster())
    runner = TaskRunner(observation_provider=lambda _: next(snapshots))
    with pytest.raises(ValueError, match="regressed"):
        runner.run_orchestrated(TaskRunRequest(package),
                                lambda task, robots: AlgorithmDecision(CONDITION, (ASSIGNMENT,)))


def test_malformed_receipt_remains_terminal_even_after_successful_stop():
    executor = SkillTaskExecutor(resolver(
        lambda plan, **kwargs: replace(receipt(plan), request_id="foreign"),
        stop=lambda **kwargs: True))
    result = executor.execute(ASSIGNMENT, TASK, scene={})
    assert result.dependency_outcome is DependencyOutcome.TERMINAL_FAILURE
    assert result.feedback.failure_code is FailureCode.INVALID_PLAN
    assert executor.resources.snapshot()["leases"] == []


def test_executor_rejects_foreign_scene_observation_before_command():
    executor = SkillTaskExecutor(resolver(execute=lambda *args, **kwargs: pytest.fail("command")),
        observation_provider=lambda _: WorldSnapshot(scene_id="foreign", revision=0, tick=0,
                                                       robots=roster()),
        expected_scene_id="test-scene")
    result = executor.execute(ASSIGNMENT, TASK, scene={})
    assert result.feedback.failure_code is FailureCode.INVALID_PLAN


def test_runner_rejects_cached_post_dispatch_observation_before_evaluation():
    scene = {"tick": 0}
    package = EaiTaskPackage("fixture", "test-scene", TaskGraph((TASK,), "inline"), roster())
    class Evaluator:
        def evaluate(self, **kwargs):
            pytest.fail("cached world must not reach evaluator")
    runner = TaskRunner(scene_provider=lambda _: scene, observation_provider=observe,
                        orchestration_executor=SkillTaskExecutor(resolver()), evaluator=Evaluator())
    with pytest.raises(ValueError, match="fresh revision"):
        runner.run_orchestrated(TaskRunRequest(package),
            lambda task, robots: AlgorithmDecision(CONDITION, (ASSIGNMENT,)))


def test_executor_run_reuse_is_rejected_before_scene_or_checkpoint_effects():
    scene_calls, forks = [], []
    scene = {}
    class Replay:
        def fork(self, checkpoint, branch_id):
            forks.append((checkpoint, branch_id))
    def open_scene(scene_id):
        scene_calls.append(scene_id)
        return scene
    package = EaiTaskPackage("fixture", "test-scene", TaskGraph((TASK,), "inline"), roster())
    runner = TaskRunner(scene_provider=open_scene, replay=Replay(),
                        orchestration_executor=SkillTaskExecutor(resolver()))
    decide = lambda task, robots: AlgorithmDecision(CONDITION, (ASSIGNMENT,))
    result = runner.run_orchestrated(TaskRunRequest(package, branch_id="a", checkpoint="cp"), decide)
    assert result.verdict["status"] == "N/A"
    with pytest.raises(ValueError, match="fresh skill executor"):
        runner.run_orchestrated(TaskRunRequest(package, branch_id="b", checkpoint="cp"), decide)
    assert scene_calls == ["test-scene"]
    assert forks == [("cp", "a")]


def test_failure_cannot_be_reported_as_dependency_completion():
    class Executor:
        def execute(self, assignment, task, *, scene):
            assert assignment.task_id == "move"
            return ExecutionUpdate(ExecutionFeedback("move", "r1", "failed",
                FailureCode.EXECUTION_FAILURE), DependencyOutcome.COMPLETED)
    package = EaiTaskPackage("fixture", "test-scene", TaskGraph((
        TASK, CoordinationTask("child", "Successor", prerequisites=("move",)),
    ), "inline"), roster())
    runner = TaskRunner(orchestration_executor=Executor())
    with pytest.raises(ValueError, match="contradicts"):
        runner.run_orchestrated(TaskRunRequest(package),
            lambda task, robots: AlgorithmDecision(CONDITION, (ASSIGNMENT, Assignment("child", "r1", "fixture"))))


@pytest.mark.parametrize("method", ["run", "run_orchestrated"])
def test_missing_replay_is_rejected_before_scene_creation(method):
    package = EaiTaskPackage("fixture", "test-scene", TaskGraph((TASK,), "inline"), roster())
    runner = TaskRunner(scene_provider=lambda _: pytest.fail("scene must not be opened"))
    with pytest.raises(ValueError, match="no replay backend"):
        getattr(runner, method)(TaskRunRequest(package, checkpoint="cp"), lambda *args: ())
