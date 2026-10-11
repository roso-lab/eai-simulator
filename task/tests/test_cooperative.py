from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from algorithm.common_coordination.contract import (
    Assignment,
    CoordinationTask,
    FailureCode,
    RobotCapability,
    TaskGraph,
)
from algorithm.skills.contracts import SkillCommand, SkillPlan, SkillRequest
from task.contracts import (
    DependencyOutcome,
    EaiTaskPackage,
    RobotState,
    TaskRunRequest,
)
from task.executors.cooperative import CooperativeBatchExecutor, CooperativeBinding
from task.executors.skills import ExecutionReceipt
from task.observation import WorldSnapshot
from task.resources import ResourceManager


TASKS = (
    CoordinationTask("left", "Synthetic left action"),
    CoordinationTask("right", "Synthetic right action"),
)
ASSIGNMENTS = (
    Assignment("left", "r1", "fixture"),
    Assignment("right", "r2", "fixture"),
)


class Provider:
    skill_id = "fixture.cooperative"

    def __init__(self, events=None, name=""):
        self.events = events
        self.name = name

    def plan(self, request, *, observation=None):
        if self.events is not None:
            self.events.append(("plan", self.name, observation))
        return SkillPlan(
            request.request_id,
            request.skill_id,
            request.robot_id,
            (SkillCommand("act", {"robot_id": request.robot_id}),),
        )


def make_receipt(
    request_id,
    robot_id,
    *,
    outcome=DependencyOutcome.COMPLETED,
):
    return ExecutionReceipt(
        request_id,
        "fixture.cooperative",
        robot_id,
        outcome,
        True,
        FailureCode.NONE
        if outcome is DependencyOutcome.COMPLETED
        else FailureCode.EXECUTION_FAILURE,
    )


class ScriptedHandle:
    def __init__(
        self,
        request_id,
        robot_id,
        *,
        complete_on=1,
        action=None,
        complete_after_cancel=False,
    ):
        self.request_id = request_id
        self.robot_id = robot_id
        self.complete_on = complete_on
        self.action = action if action is not None else f"action:{robot_id}"
        self.complete_after_cancel = complete_after_cancel
        self.polls = 0
        self.cancel_reasons = []

    def command(self):
        return self.action

    def after_step(self):
        self.polls += 1
        if self.cancel_reasons and self.complete_after_cancel:
            return make_receipt(self.request_id, self.robot_id)
        if not self.cancel_reasons and self.polls >= self.complete_on:
            return make_receipt(self.request_id, self.robot_id)
        return None

    def cancel(self, reason):
        self.cancel_reasons.append(reason)


def package(tasks=TASKS):
    return EaiTaskPackage(
        "cooperative-fixture",
        "test-scene",
        TaskGraph(tuple(tasks), "inline"),
        (
            RobotState(RobotCapability("r1", frozenset())),
            RobotState(RobotCapability("r2", frozenset())),
        ),
    )


def binding(
    assignment,
    handle,
    *,
    request_id=None,
    resources=(),
    events=None,
    start=None,
):
    request_id = request_id or f"request:{assignment.task_id}"
    provider = Provider(events, assignment.task_id)

    def start_handle(plan, *, scene):
        if events is not None:
            events.append(("start", assignment.task_id))
        if start is not None:
            return start(plan, scene=scene)
        return handle

    return CooperativeBinding(
        SkillRequest(request_id, provider.skill_id, assignment.robot_id),
        provider,
        start_handle,
        lambda **kwargs: None,
        tuple(resources),
    )


def executor_for(
    bindings,
    *,
    step,
    neutral_actions,
    resources=None,
    observation_provider=None,
    max_ticks=8,
    max_cancel_ticks=3,
    run_package=None,
):
    executor = CooperativeBatchExecutor(
        lambda assignment, task, *, scene: bindings[assignment.task_id],
        step=step,
        neutral_actions=neutral_actions,
        resources=resources,
        observation_provider=observation_provider,
        max_ticks=max_ticks,
        max_cancel_ticks=max_cancel_ticks,
        expected_scene_id="test-scene",
    )
    executor.bind_run(TaskRunRequest(run_package or package()))
    return executor


def test_two_robot_commands_merge_into_one_shared_step_after_full_prebuild():
    events = []
    handles = {
        "left": ScriptedHandle("request:left", "r1", action={"v": 1}),
        "right": ScriptedHandle("request:right", "r2", action={"v": 2}),
    }
    bindings = {
        task_id: binding(assignment, handles[task_id], events=events)
        for task_id, assignment in zip(("left", "right"), ASSIGNMENTS, strict=True)
    }
    stepped = []
    executor = executor_for(
        bindings,
        step=lambda scene, actions: stepped.append(dict(actions)),
        neutral_actions=lambda scene: {
            "r1": {"v": 0},
            "r2": {"v": 0},
            "idle": {"v": 0},
        },
    )

    updates = executor.execute_batch(
        ASSIGNMENTS,
        {task.task_id: task for task in TASKS},
        scene={},
    )

    assert stepped == [{"r1": {"v": 1}, "r2": {"v": 2}, "idle": {"v": 0}}]
    assert [item.dependency_outcome for item in updates] == [
        DependencyOutcome.COMPLETED,
        DependencyOutcome.COMPLETED,
    ]
    assert [(kind, name) for kind, name, *_ in events[:2]] == [
        ("plan", "left"),
        ("plan", "right"),
    ]
    assert all(event[0] == "start" for event in events[2:])
    assert executor.resources.snapshot()["leases"] == []


def test_handles_complete_on_different_shared_ticks():
    handles = {
        "left": ScriptedHandle("request:left", "r1", complete_on=1),
        "right": ScriptedHandle("request:right", "r2", complete_on=3),
    }
    bindings = {
        task_id: binding(assignment, handles[task_id])
        for task_id, assignment in zip(("left", "right"), ASSIGNMENTS, strict=True)
    }
    stepped = []
    executor = executor_for(
        bindings,
        step=lambda scene, actions: stepped.append(dict(actions)),
        neutral_actions=lambda scene: {"r1": "neutral", "r2": "neutral"},
    )

    updates = executor.execute_batch(
        ASSIGNMENTS,
        {task.task_id: task for task in TASKS},
        scene={},
    )

    assert len(stepped) == 3
    assert stepped[0] == {"r1": "action:r1", "r2": "action:r2"}
    assert stepped[1:] == [
        {"r1": "neutral", "r2": "action:r2"},
        {"r1": "neutral", "r2": "action:r2"},
    ]
    assert all(item.dependency_outcome is DependencyOutcome.COMPLETED for item in updates)


def test_resource_conflict_has_no_start_side_effect_and_other_work_continues():
    resources = ResourceManager()
    held = resources.acquire("external", ("object:shared",))
    starts = []
    handles = {
        "left": ScriptedHandle("request:left", "r1"),
        "right": ScriptedHandle("request:right", "r2"),
    }
    bindings = {
        "left": binding(
            ASSIGNMENTS[0], handles["left"], resources=("object:shared",),
            start=lambda plan, **kwargs: starts.append("left") or handles["left"],
        ),
        "right": binding(
            ASSIGNMENTS[1], handles["right"], resources=("object:free",),
            start=lambda plan, **kwargs: starts.append("right") or handles["right"],
        ),
    }
    executor = executor_for(
        bindings,
        step=lambda scene, actions: None,
        neutral_actions=lambda scene: {},
        resources=resources,
    )

    updates = executor.execute_batch(
        ASSIGNMENTS,
        {task.task_id: task for task in TASKS},
        scene={},
    )

    assert updates[0].dependency_outcome is DependencyOutcome.RETRYABLE_FAILURE
    assert updates[1].dependency_outcome is DependencyOutcome.COMPLETED
    assert starts == ["right"]
    resources.release(held)


@pytest.mark.parametrize("duplicate", ["robot", "request"])
def test_duplicate_identity_is_rejected_before_any_handle_starts(duplicate):
    starts = []
    assignments = (
        ASSIGNMENTS[0],
        replace(ASSIGNMENTS[1], robot_id="r1") if duplicate == "robot" else ASSIGNMENTS[1],
    )
    request_ids = ("same", "same") if duplicate == "request" else ("left", "right")
    bindings = {
        task_id: binding(
            assignment,
            ScriptedHandle(request_id, assignment.robot_id),
            request_id=request_id,
            start=lambda plan, **kwargs: starts.append(plan.request_id),
        )
        for task_id, assignment, request_id in zip(
            ("left", "right"), assignments, request_ids, strict=True
        )
    }
    executor = executor_for(
        bindings,
        step=lambda scene, actions: pytest.fail("unexpected step"),
        neutral_actions=lambda scene: {},
    )

    with pytest.raises(ValueError, match=f"duplicate.*{duplicate}"):
        executor.execute_batch(
            assignments,
            {task.task_id: task for task in TASKS},
            scene={},
        )
    assert starts == []
    assert executor.resources.snapshot()["leases"] == []


def test_external_cancellation_drains_to_receipt_and_cannot_become_completion():
    handle = ScriptedHandle(
        "request:left",
        "r1",
        complete_on=99,
        complete_after_cancel=True,
    )
    bindings = {"left": binding(ASSIGNMENTS[0], handle)}
    holder = {}

    def step(scene, actions):
        holder["executor"].cancel("operator request")

    executor = executor_for(
        bindings,
        step=step,
        neutral_actions=lambda scene: {"r1": "neutral"},
    )
    holder["executor"] = executor

    update = executor.execute(ASSIGNMENTS[0], TASKS[0], scene={})

    assert update.dependency_outcome is DependencyOutcome.RETRYABLE_FAILURE
    assert update.feedback.failure_code is FailureCode.EXECUTION_FAILURE
    assert handle.cancel_reasons == ["operator request"]
    assert executor.resources.snapshot()["leases"] == []


def test_stalled_cancellation_quarantines_lease_after_bounded_drain():
    class ClaimingHandle(ScriptedHandle):
        def cancel(self, reason):
            super().cancel(reason)
            return True  # Python return is deliberately not stop confirmation.

    handle = ClaimingHandle("request:left", "r1", complete_on=99)
    executor = executor_for(
        {"left": binding(ASSIGNMENTS[0], handle)},
        step=lambda scene, actions: None,
        neutral_actions=lambda scene: {"r1": "neutral"},
        max_ticks=1,
        max_cancel_ticks=2,
    )

    update = executor.execute(ASSIGNMENTS[0], TASKS[0], scene={})

    assert update.dependency_outcome is DependencyOutcome.TERMINAL_FAILURE
    assert update.feedback.failure_code is FailureCode.SAFE_STOP
    assert len(handle.cancel_reasons) == 1
    lease, = executor.quarantined_leases
    assert executor.resources.snapshot()["leases"] == [{
        "lease_id": lease.lease_id,
        "owner": lease.owner,
        "resources": list(lease.resources),
        "state": "quarantined",
        "quarantine_reason": "cancellation unconfirmed: cooperative tick budget exhausted",
    }]


def test_partial_start_exception_has_only_tracked_quarantined_occupancy():
    first = ScriptedHandle("request:left", "r1")
    starts = []

    def fail_after_side_effect(plan, **kwargs):
        starts.append("right-side-effect")
        raise RuntimeError("start failed after touching controller")

    bindings = {
        "left": binding(ASSIGNMENTS[0], first),
        "right": binding(
            ASSIGNMENTS[1],
            None,
            start=fail_after_side_effect,
        ),
    }
    executor = executor_for(
        bindings,
        step=lambda scene, actions: None,
        neutral_actions=lambda scene: {},
    )

    updates = executor.execute_batch(
        ASSIGNMENTS,
        {task.task_id: task for task in TASKS},
        scene={},
    )

    assert updates[0].dependency_outcome is DependencyOutcome.TERMINAL_FAILURE
    assert updates[1].dependency_outcome is DependencyOutcome.TERMINAL_FAILURE
    assert updates[1].feedback.failure_code is FailureCode.SAFE_STOP
    assert starts == ["right-side-effect"]
    assert len(executor.quarantined_leases) == 2
    snapshot = executor.resources.snapshot()
    assert set(snapshot['resources']) == {'robot:r1', 'robot:r2'}
    assert all(item['state']=='quarantined' for item in snapshot['leases'])
    assert first.polls == 0
    with pytest.raises(RuntimeError, match='quarantined'):
        executor.execute_batch(ASSIGNMENTS, {task.task_id:task for task in TASKS}, scene={})


def test_start_callback_quarantine_returns_safe_stop_without_starting_peer():
    resources = ResourceManager()
    first = ScriptedHandle("request:left", "r1")

    def start(plan, **kwargs):
        resources.quarantine_active("injected start restore failure")
        return first

    executor = executor_for(
        {"left": binding(ASSIGNMENTS[0], first, start=start),
         "right": binding(ASSIGNMENTS[1], None,
                          start=lambda *a, **kw: pytest.fail("peer must not start"))},
        resources=resources, step=lambda *a: pytest.fail("quarantined scene must not step"),
        neutral_actions=lambda scene: {},
    )
    updates = executor.execute_batch(ASSIGNMENTS, {task.task_id: task for task in TASKS}, scene={})
    assert all(update.feedback.failure_code is FailureCode.SAFE_STOP for update in updates)
    assert first.cancel_reasons and first.polls == 0
    assert len(executor.quarantined_leases) == 2
    assert all(record["state"] == "quarantined" for record in resources.snapshot()["leases"])


def test_wrong_receipt_stays_terminal_after_quiescent_cancel_confirmation():
    class WrongThenStopped(ScriptedHandle):
        def after_step(self):
            self.polls += 1
            if self.polls == 1:
                return make_receipt("foreign-request", self.robot_id)
            if self.cancel_reasons:
                return make_receipt(self.request_id, self.robot_id)
            return None

    handle = WrongThenStopped("request:left", "r1")
    executor = executor_for(
        {"left": binding(ASSIGNMENTS[0], handle)},
        step=lambda scene, actions: None,
        neutral_actions=lambda scene: {},
    )

    update = executor.execute(ASSIGNMENTS[0], TASKS[0], scene={})

    assert update.dependency_outcome is DependencyOutcome.TERMINAL_FAILURE
    assert update.feedback.failure_code is FailureCode.INVALID_PLAN
    assert handle.cancel_reasons
    assert executor.resources.snapshot()["leases"] == []


def test_step_exception_preserves_failure_and_releases_only_after_safe_drain():
    handle = ScriptedHandle(
        "request:left", "r1", complete_on=99, complete_after_cancel=True
    )
    calls = []

    def step(scene, actions):
        calls.append(dict(actions))
        if len(calls) == 1:
            raise RuntimeError("shared step failed")

    executor = executor_for(
        {"left": binding(ASSIGNMENTS[0], handle)},
        step=step,
        neutral_actions=lambda scene: {},
    )

    update = executor.execute(ASSIGNMENTS[0], TASKS[0], scene={})

    assert update.dependency_outcome is DependencyOutcome.RETRYABLE_FAILURE
    assert "shared step failed: RuntimeError: shared step failed" in update.feedback.message
    assert handle.cancel_reasons
    assert len(calls) == 2  # Confirmation must follow a successful physical step.
    assert handle.polls == 1
    assert executor.resources.snapshot()["leases"] == []


@pytest.mark.parametrize("operation", ["neutral", "step"])
def test_failed_ticks_cannot_fabricate_quiescent_receipts(operation):
    handle = ScriptedHandle("request:left", "r1", complete_after_cancel=True)
    attempts = []

    def fail(*args):
        attempts.append(operation)
        raise RuntimeError("no completed physical tick")

    executor = executor_for(
        {"left": binding(ASSIGNMENTS[0], handle)},
        step=fail if operation == "step" else lambda *args: pytest.fail("unexpected step"),
        neutral_actions=fail if operation == "neutral" else lambda scene: {},
        max_cancel_ticks=3,
    )
    update = executor.execute(ASSIGNMENTS[0], TASKS[0], scene={})
    assert len(attempts) == 3
    assert handle.polls == 0
    assert update.feedback.failure_code is FailureCode.SAFE_STOP
    assert len(executor.quarantined_leases) == 1


@pytest.mark.parametrize("operation", ["neutral", "command", "step", "poll", "cancel"])
@pytest.mark.parametrize("fatal", [KeyboardInterrupt, SystemExit])
def test_fatal_interrupt_propagates_before_more_physics_or_receipts(operation, fatal):
    effects = []

    def interrupt():
        effects.append("interrupt")
        raise fatal("operator interrupt")

    class InterruptedHandle(ScriptedHandle):
        def command(self):
            if operation == "command":
                interrupt()
            return super().command()

        def after_step(self):
            effects.append("poll")
            if operation == "poll":
                interrupt()
            return super().after_step()

        def cancel(self, reason):
            super().cancel(reason)
            if operation == "cancel":
                interrupt()

    handle = InterruptedHandle("request:left", "r1", complete_on=99)

    def step(scene, actions):
        effects.append("step")
        if operation == "step":
            interrupt()

    executor = executor_for(
        {"left": binding(ASSIGNMENTS[0], handle)}, step=step,
        neutral_actions=lambda scene: interrupt() if operation == "neutral" else {},
        max_ticks=1,
    )
    with pytest.raises(fatal, match="operator interrupt"):
        executor.execute(ASSIGNMENTS[0], TASKS[0], scene={})
    assert all(event not in {"step", "poll"} for event in effects[effects.index("interrupt") + 1:])
    assert executor.resources.snapshot()["leases"][0]["state"] == "quarantined"


@pytest.mark.parametrize("operation", ["neutral", "command", "poll"])
def test_callback_quarantine_halts_at_boundary_before_step_or_release(operation):
    resources = ResourceManager()
    effects = []

    def quarantine():
        effects.append("quarantine")
        resources.quarantine_active("injected restore failure")

    class QuarantiningHandle(ScriptedHandle):
        def command(self):
            if operation == "command":
                quarantine()
            return super().command()

        def after_step(self):
            if operation == "poll":
                quarantine()
            return super().after_step()

    first = QuarantiningHandle("request:left", "r1")
    second = ScriptedHandle("request:right", "r2")

    def neutral(scene):
        if operation == "neutral":
            quarantine()
        return {}

    executor = executor_for(
        {"left": binding(ASSIGNMENTS[0], first), "right": binding(ASSIGNMENTS[1], second)},
        step=lambda *args: effects.append("step"), neutral_actions=neutral, resources=resources,
    )
    updates = executor.execute_batch(ASSIGNMENTS, {task.task_id: task for task in TASKS}, scene={})
    assert "step" not in effects[effects.index("quarantine") + 1:]
    assert second.polls == 0
    assert all(update.feedback.failure_code is FailureCode.SAFE_STOP for update in updates)
    assert len(executor.quarantined_leases) == 2
    assert all(record["state"] == "quarantined" for record in resources.snapshot()["leases"])


def test_interrupted_admission_releases_unstarted_leases_and_allows_fresh_request():
    class InterruptedResources(ResourceManager):
        interrupted = False

        def acquire(self, owner, resources):
            if owner == "request:right" and not self.interrupted:
                self.interrupted = True
                raise KeyboardInterrupt("admission interrupted")
            return super().acquire(owner, resources)

    resources = InterruptedResources()
    handles = [ScriptedHandle("request:left", "r1"), ScriptedHandle("request:right", "r2")]
    executor = executor_for(
        {task.task_id: binding(assignment, handle)
         for task, assignment, handle in zip(TASKS, ASSIGNMENTS, handles)},
        step=lambda *args: None, neutral_actions=lambda scene: {}, resources=resources,
    )
    scene = {}
    with pytest.raises(KeyboardInterrupt, match="admission interrupted"):
        executor.execute_batch(ASSIGNMENTS, {task.task_id: task for task in TASKS}, scene=scene)
    assert resources.snapshot()["leases"] == []
    assert all(handle.polls == 0 for handle in handles)
    assert executor.execute(ASSIGNMENTS[0], TASKS[0], scene=scene).dependency_outcome is DependencyOutcome.COMPLETED


def test_missing_command_prevents_partial_batch_step_and_stale_peer_completion():
    class BrokenCommand(ScriptedHandle):
        def command(self):
            raise RuntimeError("controller command unavailable")

    handles = (BrokenCommand("request:left", "r1"), ScriptedHandle("request:right", "r2"))
    executor = executor_for(
        {task.task_id: binding(assignment, handle)
         for task, assignment, handle in zip(TASKS, ASSIGNMENTS, handles)},
        step=lambda *args: pytest.fail("partial action map must not advance scene"),
        neutral_actions=lambda scene: {}, max_cancel_ticks=2,
    )
    updates = executor.execute_batch(ASSIGNMENTS, {task.task_id: task for task in TASKS}, scene={})
    assert all(handle.polls == 0 for handle in handles)
    assert all(update.feedback.failure_code is FailureCode.SAFE_STOP for update in updates)
    assert len(executor.quarantined_leases) == 2


@pytest.mark.parametrize("operation", ["validate", "start"])
def test_fatal_batch_setup_releases_only_never_started_ownership(operation):
    tasks = (*TASKS, CoordinationTask("third", "Never started"))
    assignments = (*ASSIGNMENTS, Assignment("third", "r3", "fixture"))
    handles = [ScriptedHandle(f"request:{task.task_id}", assignment.robot_id)
               for task, assignment in zip(tasks, assignments)]
    bindings = {task.task_id: binding(assignment, handle)
                for task, assignment, handle in zip(tasks, assignments, handles)}
    validations = 0

    def validate(**kwargs):
        nonlocal validations
        validations += 1
        if validations == 2:
            raise KeyboardInterrupt("setup interrupted")

    def start(*args, **kwargs):
        raise KeyboardInterrupt("setup interrupted")

    bindings["right"] = replace(bindings["right"], **{operation: validate if operation == "validate" else start})
    executor = executor_for(bindings, step=lambda *args: pytest.fail("unexpected step"),
                            neutral_actions=lambda scene: {})
    with pytest.raises(KeyboardInterrupt, match="setup interrupted"):
        executor.execute_batch(assignments, {task.task_id: task for task in tasks}, scene={})
    occupied = executor.resources.snapshot()["leases"]
    if operation == "validate":
        assert occupied == []
    else:
        assert {record["owner"] for record in occupied} == {"request:left", "request:right"}
        assert all(record["state"] == "quarantined" for record in occupied)
        assert handles[0].cancel_reasons
    assert all(handle.polls == 0 for handle in handles)


@pytest.mark.parametrize("operation", ["command", "poll"])
def test_handle_callback_exception_uses_bounded_safe_cancel(operation):
    class FailingHandle(ScriptedHandle):
        def command(self):
            if operation == "command" and not self.cancel_reasons:
                raise RuntimeError("command callback failed")
            return super().command()

        def after_step(self):
            if operation == "poll" and not self.cancel_reasons:
                raise RuntimeError("poll callback failed")
            return super().after_step()

    handle = FailingHandle(
        "request:left", "r1", complete_on=99, complete_after_cancel=True
    )
    executor = executor_for(
        {"left": binding(ASSIGNMENTS[0], handle)},
        step=lambda scene, actions: None,
        neutral_actions=lambda scene: {"r1": "neutral"},
    )

    update = executor.execute(ASSIGNMENTS[0], TASKS[0], scene={})

    assert update.dependency_outcome is DependencyOutcome.RETRYABLE_FAILURE
    assert operation in update.feedback.message
    assert handle.cancel_reasons
    assert executor.resources.snapshot()["leases"] == []


def test_consumed_request_id_cannot_start_again_in_later_batch():
    starts = []
    handle = ScriptedHandle("request:left", "r1")
    bindings = {
        "left": binding(
            ASSIGNMENTS[0],
            handle,
            start=lambda plan, **kwargs: starts.append(plan.request_id) or handle,
        )
    }
    steps = []
    executor = executor_for(
        bindings,
        step=lambda scene, actions: steps.append(dict(actions)),
        neutral_actions=lambda scene: {},
    )
    scene = {}

    first = executor.execute(ASSIGNMENTS[0], TASKS[0], scene=scene)
    second = executor.execute(ASSIGNMENTS[0], TASKS[0], scene=scene)

    assert first.dependency_outcome is DependencyOutcome.COMPLETED
    assert second.dependency_outcome is DependencyOutcome.TERMINAL_FAILURE
    assert second.feedback.failure_code is FailureCode.INVALID_PLAN
    assert starts == ["request:left"]
    assert len(steps) == 1


def test_owner_thread_and_cross_run_reuse_fail_before_effects():
    effects = []
    executor = CooperativeBatchExecutor(
        lambda *args, **kwargs: pytest.fail("unexpected resolve"),
        step=lambda scene, actions: effects.append("step"),
        neutral_actions=lambda scene: {},
        max_ticks=1,
        max_cancel_ticks=1,
        expected_scene_id="test-scene",
    )
    request = TaskRunRequest(package())

    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(RuntimeError, match="owner thread"):
            pool.submit(executor.bind_run, request).result()

    executor.bind_run(request)
    with pytest.raises(ValueError, match="fresh cooperative executor"):
        executor.bind_run(replace(request, branch_id="other"))
    assert effects == []


def test_batch_observation_is_scene_bound_and_passed_detached_to_each_provider():
    events = []
    snapshot = WorldSnapshot(
        scene_id="test-scene",
        revision=4,
        tick=9,
        robots=(
            RobotState(RobotCapability("r1", frozenset({"drive"}))),
            RobotState(RobotCapability("r2", frozenset({"inspect"}))),
        ),
        objects={"target": {"state": "ready"}},
    )
    handles = {
        "left": ScriptedHandle("request:left", "r1"),
        "right": ScriptedHandle("request:right", "r2"),
    }
    bindings = {
        task_id: binding(assignment, handles[task_id], events=events)
        for task_id, assignment in zip(("left", "right"), ASSIGNMENTS, strict=True)
    }
    executor = executor_for(
        bindings,
        step=lambda scene, actions: None,
        neutral_actions=lambda scene: {},
        observation_provider=lambda scene: snapshot,
    )

    updates = executor.execute_batch(
        ASSIGNMENTS,
        {task.task_id: task for task in TASKS},
        scene={},
    )

    observations = [event[2] for event in events if event[0] == "plan"]
    assert len(observations) == 2
    assert observations[0] == observations[1] == snapshot.to_dict()
    observations[0]["objects"]["target"]["state"] = "tampered"
    assert observations[1]["objects"]["target"]["state"] == "ready"
    assert snapshot.to_dict()["objects"]["target"]["state"] == "ready"
    assert all(item.dependency_outcome is DependencyOutcome.COMPLETED for item in updates)


def test_live_observation_rejects_inactive_and_missing_capability_before_start():
    live_tasks = (
        CoordinationTask("left", "Drive", frozenset({"drive"})),
        CoordinationTask("right", "Inspect", frozenset({"inspect"})),
    )
    snapshot = WorldSnapshot(
        scene_id="test-scene",
        revision=1,
        tick=1,
        robots=(
            RobotState(RobotCapability("r1", frozenset({"other"}))),
            RobotState(RobotCapability("r2", frozenset({"inspect"}), False)),
        ),
    )
    starts = []
    bindings = {
        task_id: binding(
            assignment,
            ScriptedHandle(f"request:{task_id}", assignment.robot_id),
            start=lambda plan, **kwargs: starts.append(plan.request_id),
        )
        for task_id, assignment in zip(("left", "right"), ASSIGNMENTS, strict=True)
    }
    executor = executor_for(
        bindings,
        step=lambda scene, actions: pytest.fail("invalid bindings must not step"),
        neutral_actions=lambda scene: {},
        observation_provider=lambda scene: snapshot,
        run_package=package(live_tasks),
    )

    updates = executor.execute_batch(
        ASSIGNMENTS,
        {task.task_id: task for task in live_tasks},
        scene={},
    )

    assert [item.dependency_outcome for item in updates] == [
        DependencyOutcome.TERMINAL_FAILURE,
        DependencyOutcome.TERMINAL_FAILURE,
    ]
    assert "lacks live capabilities" in updates[0].feedback.message
    assert "absent or inactive" in updates[1].feedback.message
    assert starts == []


def test_observation_must_be_a_monotonic_successor_across_batch_calls():
    snapshots = iter((
        WorldSnapshot(scene_id="test-scene", revision=2, tick=2,
                      robots=package().robots),
        WorldSnapshot(scene_id="test-scene", revision=1, tick=1,
                      robots=package().robots),
    ))
    handles = {"left": ScriptedHandle("request:left", "r1")}
    bindings = {"left": binding(ASSIGNMENTS[0], handles["left"])}
    executor = executor_for(
        bindings,
        step=lambda scene, actions: None,
        neutral_actions=lambda scene: {},
        observation_provider=lambda scene: next(snapshots),
    )
    scene = {}

    first = executor.execute(ASSIGNMENTS[0], TASKS[0], scene=scene)
    assert first.dependency_outcome is DependencyOutcome.COMPLETED
    bindings["left"] = binding(
        ASSIGNMENTS[0],
        ScriptedHandle("request:left:retry", "r1"),
        request_id="request:left:retry",
    )

    with pytest.raises(ValueError, match="regressed"):
        executor.execute(ASSIGNMENTS[0], TASKS[0], scene=scene)
