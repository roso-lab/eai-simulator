from __future__ import annotations

from dataclasses import replace

import pytest

from TeamWeaver.tests.eai_test_support import (
    failure,
    path_failure,
    success,
    tasks_from_payloads,
    valid_button_payload,
    valid_inspect_payload,
    valid_relay_payload,
)
from TeamWeaver.eai_adapter.task_models import FailureKind, FeedbackOutcome, TaskStatus


def test_scheduler_releases_only_dependency_ready_frontier(factory_world):
    tasks = tasks_from_payloads(
        factory_world,
        valid_inspect_payload(task_id="inspect_fire"),
        valid_button_payload(task_id="open_channel", prerequisites=["inspect_fire"]),
    )
    from TeamWeaver.eai_adapter.phase_scheduler import PhaseScheduler

    scheduler = PhaseScheduler(tasks)

    assert [task.task_id for task in scheduler.ready_tasks()] == ["inspect_fire"]
    scheduler.mark_assigned("inspect_fire", "carter_1")
    scheduler.mark_navigating("inspect_fire")
    scheduler.mark_operating("inspect_fire")
    scheduler.apply_feedback(success("inspect_fire", "carter_1", ("sensing",)))

    assert scheduler.status("inspect_fire") is TaskStatus.SUCCEEDED
    assert [task.task_id for task in scheduler.ready_tasks()] == ["open_channel"]


def test_non_parallel_ready_task_forms_a_serial_phase(factory_world):
    tasks = tasks_from_payloads(
        factory_world,
        valid_inspect_payload(task_id="parallel", priority=1),
        valid_button_payload(task_id="serial", can_parallel=False, priority=2),
    )
    from TeamWeaver.eai_adapter.phase_scheduler import PhaseScheduler

    phase = PhaseScheduler(tasks).next_phase()
    assert [task.task_id for task in phase] == ["serial"]


def test_second_recoverable_failure_becomes_permanent(factory_world):
    tasks = tasks_from_payloads(factory_world, valid_inspect_payload())
    from TeamWeaver.eai_adapter.phase_scheduler import PhaseScheduler

    scheduler = PhaseScheduler(tasks)
    scheduler.mark_assigned("inspect_fire", "carter_1")
    scheduler.mark_navigating("inspect_fire")
    scheduler.apply_feedback(path_failure("inspect_fire", "carter_1"))
    assert scheduler.status("inspect_fire") is TaskStatus.FAILED
    assert scheduler.retry_failed("inspect_fire") is True
    assert scheduler.status("inspect_fire") is TaskStatus.READY

    scheduler.mark_assigned("inspect_fire", "m20_1")
    scheduler.mark_navigating("inspect_fire")
    scheduler.apply_feedback(path_failure("inspect_fire", "m20_1"))
    assert scheduler.retry_failed("inspect_fire") is False
    assert scheduler.permanently_failed_task_ids == frozenset({"inspect_fire"})


def test_constraint_failure_is_permanent_on_first_attempt(factory_world):
    tasks = tasks_from_payloads(factory_world, valid_button_payload())
    from TeamWeaver.eai_adapter.phase_scheduler import PhaseScheduler

    scheduler = PhaseScheduler(tasks)
    scheduler.mark_assigned("open_rescue_channel", "scout_1")
    scheduler.mark_navigating("open_rescue_channel")
    scheduler.apply_feedback(
        failure(
            "open_rescue_channel",
            "scout_1",
            FailureKind.CONSTRAINT_VIOLATION,
            reason="required equipment disappeared",
        )
    )

    assert scheduler.retry_failed("open_rescue_channel") is False
    assert scheduler.mission_status() == "failed"


def test_timeout_has_distinct_state_and_can_retry_once(factory_world):
    from TeamWeaver.eai_adapter.task_models import ExecutionFeedback
    from TeamWeaver.eai_adapter.phase_scheduler import PhaseScheduler

    tasks = tasks_from_payloads(factory_world, valid_inspect_payload())
    scheduler = PhaseScheduler(tasks)
    scheduler.mark_assigned("inspect_fire", "carter_1")
    scheduler.mark_navigating("inspect_fire")
    scheduler.apply_feedback(
        ExecutionFeedback(
            task_id="inspect_fire",
            robot_name="carter_1",
            outcome=FeedbackOutcome.TIMED_OUT,
            reason="skill deadline reached",
            failure_kind=FailureKind.TIMEOUT,
            relevant_capabilities=("navigation",),
            world_changes={},
            timestamp_s=5.0,
        )
    )
    assert scheduler.status("inspect_fire") is TaskStatus.TIMED_OUT
    assert scheduler.retry_failed("inspect_fire") is True


def test_successful_relay_unlocks_dag_but_keeps_robot_leased(factory_world):
    tasks = tasks_from_payloads(
        factory_world,
        valid_relay_payload(task_id="relay"),
        valid_inspect_payload(task_id="inspect_remote", prerequisites=["relay"]),
    )
    from TeamWeaver.eai_adapter.phase_scheduler import PhaseScheduler

    scheduler = PhaseScheduler(tasks)
    scheduler.mark_assigned("relay", "carter_1")
    scheduler.mark_navigating("relay")
    scheduler.mark_operating("relay")
    scheduler.apply_feedback(success("relay", "carter_1", ("relay",)))

    assert scheduler.status("relay") is TaskStatus.SUCCEEDED
    assert scheduler.leased_robot_names == frozenset({"carter_1"})
    assert [task.task_id for task in scheduler.ready_tasks()] == ["inspect_remote"]

    scheduler.force_succeeded("inspect_remote")
    assert scheduler.leased_robot_names == frozenset()


def test_recoverable_dependent_failure_keeps_relay_robot_leased(factory_world):
    tasks = tasks_from_payloads(
        factory_world,
        valid_relay_payload(task_id="relay"),
        valid_inspect_payload(task_id="inspect_remote", prerequisites=["relay"]),
    )
    from TeamWeaver.eai_adapter.phase_scheduler import PhaseScheduler

    scheduler = PhaseScheduler(tasks)
    scheduler.mark_assigned("relay", "carter_1")
    scheduler.mark_navigating("relay")
    scheduler.apply_feedback(success("relay", "carter_1", ("relay",)))
    scheduler.mark_assigned("inspect_remote", "m20_1")
    scheduler.mark_navigating("inspect_remote")
    scheduler.apply_feedback(path_failure("inspect_remote", "m20_1"))

    assert scheduler.leased_robot_names == frozenset({"carter_1"})
    assert scheduler.retry_failed("inspect_remote") is True
    assert scheduler.leased_robot_names == frozenset({"carter_1"})


def test_reconcile_preserves_succeeded_and_active_records(factory_world):
    initial = tasks_from_payloads(
        factory_world,
        valid_inspect_payload(task_id="inspect_fire"),
        valid_button_payload(task_id="open_channel"),
    )
    revised = tasks_from_payloads(
        factory_world,
        valid_inspect_payload(task_id="inspect_fire", description="rewritten"),
        valid_button_payload(task_id="open_channel", description="rewritten"),
        valid_relay_payload(task_id="new_relay"),
    )
    from TeamWeaver.eai_adapter.phase_scheduler import PhaseScheduler

    scheduler = PhaseScheduler(initial)
    scheduler.force_succeeded("inspect_fire")
    scheduler.mark_assigned("open_channel", "scout_1")
    scheduler.mark_navigating("open_channel")
    original_active = scheduler.task("open_channel")

    scheduler.reconcile(revised)

    assert scheduler.status("inspect_fire") is TaskStatus.SUCCEEDED
    assert scheduler.task("open_channel") is original_active
    assert scheduler.assigned_robot("open_channel") == "scout_1"
    assert "new_relay" in scheduler.task_ids


def test_invalid_transition_and_feedback_robot_are_rejected(factory_world):
    tasks = tasks_from_payloads(factory_world, valid_inspect_payload())
    from TeamWeaver.eai_adapter.phase_scheduler import PhaseScheduler, TaskStateError

    scheduler = PhaseScheduler(tasks)
    with pytest.raises(TaskStateError):
        scheduler.mark_navigating("inspect_fire")
    scheduler.mark_assigned("inspect_fire", "carter_1")
    scheduler.mark_navigating("inspect_fire")
    with pytest.raises(TaskStateError, match="robot"):
        scheduler.apply_feedback(success("inspect_fire", "m20_1"))


def test_completed_world_task_can_release_returned_dependent(factory_world):
    completed_world = replace(factory_world, completed_task_ids=("prior",))
    tasks = tasks_from_payloads(
        completed_world,
        valid_inspect_payload(task_id="current", prerequisites=["prior"]),
    )
    from TeamWeaver.eai_adapter.phase_scheduler import PhaseScheduler

    scheduler = PhaseScheduler(tasks, completed_task_ids=("prior",))
    assert [task.task_id for task in scheduler.ready_tasks()] == ["current"]


def test_semantic_reconcile_can_replace_permanently_failed_task(factory_world):
    initial = tasks_from_payloads(factory_world, valid_inspect_payload(task_id="old"))
    replacement = tasks_from_payloads(
        factory_world,
        valid_inspect_payload(task_id="replacement"),
    )
    from TeamWeaver.eai_adapter.phase_scheduler import PhaseScheduler

    scheduler = PhaseScheduler(initial)
    scheduler.mark_assigned("old", "carter_1")
    scheduler.mark_navigating("old")
    scheduler.apply_feedback(
        failure(
            "old",
            "carter_1",
            FailureKind.WORLD_STATE_CONFLICT,
            reason="target is no longer available",
        )
    )
    assert scheduler.mission_status() == "failed"

    scheduler.reconcile(replacement)

    assert "old" not in scheduler.task_ids
    assert scheduler.permanently_failed_task_ids == frozenset()
    assert [task.task_id for task in scheduler.ready_tasks()] == ["replacement"]


def test_permanent_failure_cannot_be_forced_or_retried_with_same_task_id(factory_world):
    initial = tasks_from_payloads(factory_world, valid_inspect_payload(task_id="old"))
    same_id = tasks_from_payloads(
        factory_world,
        valid_inspect_payload(task_id="old", description="LLM tried to rewrite it"),
    )
    from TeamWeaver.eai_adapter.phase_scheduler import PhaseScheduler, TaskStateError

    scheduler = PhaseScheduler(initial)
    scheduler.mark_assigned("old", "carter_1")
    scheduler.mark_navigating("old")
    scheduler.apply_feedback(
        failure("old", "carter_1", FailureKind.WORLD_STATE_CONFLICT)
    )

    with pytest.raises(TaskStateError, match="permanently failed"):
        scheduler.force_succeeded("old")
    with pytest.raises(TaskStateError, match="new task_id"):
        scheduler.reconcile(same_id)
    assert scheduler.status("old") is TaskStatus.FAILED


def test_cancelled_feedback_releases_assignment(factory_world):
    from TeamWeaver.eai_adapter.phase_scheduler import PhaseScheduler
    from TeamWeaver.eai_adapter.task_models import ExecutionFeedback

    scheduler = PhaseScheduler(
        tasks_from_payloads(factory_world, valid_inspect_payload())
    )
    scheduler.mark_assigned("inspect_fire", "carter_1")
    scheduler.mark_navigating("inspect_fire")
    scheduler.apply_feedback(
        ExecutionFeedback(
            task_id="inspect_fire",
            robot_name="carter_1",
            outcome=FeedbackOutcome.CANCELLED,
            reason="plan replaced",
            failure_kind=FailureKind.NONE,
            relevant_capabilities=(),
            world_changes={},
            timestamp_s=4.0,
        )
    )

    assert scheduler.status("inspect_fire") is TaskStatus.CANCELLED
    assert scheduler.assigned_robot("inspect_fire") is None
    assert scheduler.attempts("inspect_fire") == 1


def test_world_state_conflict_cancellation_rebuild_does_not_consume_attempt(
    factory_world,
):
    from TeamWeaver.eai_adapter.phase_scheduler import PhaseScheduler
    from TeamWeaver.eai_adapter.task_models import (
        ExecutionFeedback,
        TaskType,
    )

    relay = tasks_from_payloads(
        factory_world,
        valid_relay_payload(task_id="establish_relay_hazard_2"),
    )[0]
    removal = replace(
        relay,
        task_id="clear_runtime_obstacle",
        task_type=TaskType.REMOVE_OBSTACLE,
        description="Move the runtime obstacle out of the blocked corridor",
        target_ref="runtime_obstacle_1",
        can_parallel=False,
    )
    rebuilt_relay = replace(relay, prerequisites=(removal.task_id,))
    scheduler = PhaseScheduler((relay,))
    scheduler.mark_assigned(relay.task_id, "carter_1")
    scheduler.mark_navigating(relay.task_id)
    scheduler.apply_feedback(
        ExecutionFeedback(
            task_id=relay.task_id,
            robot_name="carter_1",
            outcome=FeedbackOutcome.CANCELLED,
            reason="runtime obstacle blocks the active path",
            failure_kind=FailureKind.WORLD_STATE_CONFLICT,
            relevant_capabilities=(),
            world_changes={},
            timestamp_s=5.0,
        )
    )

    scheduler.reconcile((removal, rebuilt_relay))
    assert scheduler.status(relay.task_id) is TaskStatus.PENDING
    scheduler.force_succeeded(removal.task_id)
    assert scheduler.status(relay.task_id) is TaskStatus.READY

    scheduler.mark_assigned(relay.task_id, "carter_1")
    assert scheduler.attempts(relay.task_id) == 1
    scheduler.mark_navigating(relay.task_id)
    scheduler.apply_feedback(path_failure(relay.task_id, "carter_1"))

    assert scheduler.retry_failed(relay.task_id) is True
    assert scheduler.permanently_failed_task_ids == frozenset()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("task_type", "inspect", id="task-type"),
        pytest.param("target_ref", "hazard_1_red_scout", id="target-ref"),
    ],
)
def test_obstacle_replan_rejects_same_id_with_changed_blocked_task_semantics(
    factory_world,
    field,
    value,
):
    from TeamWeaver.eai_adapter.phase_scheduler import PhaseScheduler, TaskStateError
    from TeamWeaver.eai_adapter.task_models import ExecutionFeedback

    relay = tasks_from_payloads(
        factory_world,
        valid_relay_payload(task_id="establish_relay_hazard_2"),
    )[0]
    scheduler = PhaseScheduler((relay,))
    scheduler.mark_assigned(relay.task_id, "carter_1")
    scheduler.mark_navigating(relay.task_id)
    scheduler.apply_feedback(
        ExecutionFeedback(
            task_id=relay.task_id,
            robot_name="carter_1",
            outcome=FeedbackOutcome.CANCELLED,
            reason="runtime obstacle blocks the active path",
            failure_kind=FailureKind.WORLD_STATE_CONFLICT,
            relevant_capabilities=(),
            world_changes={},
            timestamp_s=5.0,
        )
    )
    replacement = replace(relay, **{field: value})

    with pytest.raises(
        TaskStateError,
        match="must preserve task_type and target_ref",
    ):
        scheduler.reconcile((replacement,))


def test_obstacle_replan_rejects_semantic_substitution_on_second_reconcile(
    factory_world,
):
    from TeamWeaver.eai_adapter.phase_scheduler import PhaseScheduler, TaskStateError
    from TeamWeaver.eai_adapter.task_models import ExecutionFeedback, TaskType

    relay = tasks_from_payloads(
        factory_world,
        valid_relay_payload(task_id="establish_relay_hazard_2"),
    )[0]
    removal = replace(
        relay,
        task_id="clear_runtime_obstacle",
        task_type=TaskType.REMOVE_OBSTACLE,
        target_ref="runtime_obstacle_1",
        can_parallel=False,
    )
    rebuilt_relay = replace(relay, prerequisites=(removal.task_id,))
    scheduler = PhaseScheduler((relay,))
    scheduler.mark_assigned(relay.task_id, "carter_1")
    scheduler.mark_navigating(relay.task_id)
    scheduler.apply_feedback(
        ExecutionFeedback(
            task_id=relay.task_id,
            robot_name="carter_1",
            outcome=FeedbackOutcome.CANCELLED,
            reason="runtime obstacle blocks the active path",
            failure_kind=FailureKind.WORLD_STATE_CONFLICT,
            relevant_capabilities=(),
            world_changes={},
            timestamp_s=5.0,
        )
    )
    scheduler.reconcile((removal, rebuilt_relay))
    assert scheduler.status(relay.task_id) is TaskStatus.PENDING

    substituted_relay = replace(
        rebuilt_relay,
        task_type=TaskType.INSPECT,
        target_ref="hazard_1_red_scout",
    )
    with pytest.raises(
        TaskStateError,
        match="must preserve task_type and target_ref",
    ):
        scheduler.reconcile((removal, substituted_relay))


def test_suspend_returns_navigating_inspect_to_ready_without_consuming_attempt(
    factory_world,
):
    from TeamWeaver.eai_adapter.phase_scheduler import PhaseScheduler

    scheduler = PhaseScheduler(
        tasks_from_payloads(factory_world, valid_inspect_payload())
    )
    scheduler.mark_assigned("inspect_fire", "m20_1")
    scheduler.mark_navigating("inspect_fire")
    assert scheduler.attempts("inspect_fire") == 1
    assert scheduler.can_suspend("inspect_fire") is True

    scheduler.suspend("inspect_fire")

    assert scheduler.status("inspect_fire") is TaskStatus.READY
    assert scheduler.assigned_robot("inspect_fire") is None
    assert scheduler.attempts("inspect_fire") == 0
    scheduler.mark_assigned("inspect_fire", "m20_2")
    assert scheduler.attempts("inspect_fire") == 1


def test_suspend_rejects_operating_non_navigation_and_system_tasks(factory_world):
    from TeamWeaver.eai_adapter.phase_scheduler import PhaseScheduler, TaskStateError

    inspect = tasks_from_payloads(factory_world, valid_inspect_payload())[0]
    operating = PhaseScheduler((inspect,))
    operating.mark_assigned("inspect_fire", "m20_1")
    operating.mark_navigating("inspect_fire")
    operating.mark_operating("inspect_fire")

    relay = PhaseScheduler(
        tasks_from_payloads(factory_world, valid_relay_payload(task_id="relay"))
    )
    relay.mark_assigned("relay", "carter_1")
    relay.mark_navigating("relay")

    pickup = tasks_from_payloads(
        factory_world,
        valid_inspect_payload(
            task_id="pickup",
            task_type="pick_extinguisher",
            target_ref="fire_extinguisher_pickup",
            requirements={},
            can_parallel=False,
            estimated_duration_s=60.0,
        ),
    )[0]
    extinguisher = PhaseScheduler((pickup,))
    extinguisher.mark_assigned("pickup", "m20_1")
    extinguisher.mark_navigating("pickup")

    continuation_task = replace(
        inspect,
        task_id="system_fire_return_m20_1",
        continuation_of="inspect_fire",
        required_agent="m20_1",
    )
    continuation = PhaseScheduler((continuation_task,))
    continuation.mark_assigned("system_fire_return_m20_1", "m20_1")
    continuation.mark_navigating("system_fire_return_m20_1")

    ready = PhaseScheduler((replace(inspect, task_id="ready"),))

    for scheduler, task_id in (
        (operating, "inspect_fire"),
        (relay, "relay"),
        (extinguisher, "pickup"),
        (continuation, "system_fire_return_m20_1"),
        (ready, "ready"),
    ):
        assert scheduler.can_suspend(task_id) is False
        with pytest.raises(TaskStateError, match="not preemptible"):
            scheduler.suspend(task_id)
