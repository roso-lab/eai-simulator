from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from TeamWeaver.tests.eai_test_support import (
    make_factory_world,
    path_failure,
    success,
    tasks_from_payloads,
    valid_button_payload,
    valid_inspect_payload,
    valid_relay_payload,
    valid_remove_obstacle_payload,
)
from TeamWeaver.eai_adapter.task_models import TaskStatus


def _world_with_fire_rallies(world):
    from TeamWeaver.eai_adapter.task_models import TargetSnapshot, TaskType

    hazard_x, hazard_y, _ = world.hazard_position
    offsets = ((1.5, 1.2), (-1.5, 1.2), (-2.0, -1.0), (2.0, -1.0))
    return replace(
        world,
        targets=tuple(world.targets)
        + tuple(
            TargetSnapshot(
                f"hazard_{world.hazard_id}_fire_rally_{index}",
                (hazard_x + offset_x, hazard_y + offset_y, 0.0),
                True,
                "system_fire_rally",
                (TaskType.NAVIGATE,),
            )
            for index, (offset_x, offset_y) in enumerate(offsets, start=1)
        ),
    )


class StubDecomposer:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def decompose(self, instruction, world, **kwargs):
        self.calls.append(
            SimpleNamespace(instruction=instruction, world=world, **kwargs)
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(tasks=tuple(response), source="deepseek", attempts=1)

    def cancel(self):
        self.cancelled = True


def test_batch_two_planning_api_is_exported_without_replacing_legacy_result():
    from TeamWeaver.eai_adapter import (
        AllocationResult,
        CapabilityTracker,
        DynamicMIQPAllocator,
        PhaseAllocationResult,
        TeamWeaverPipeline,
        TeamWeaverPlan,
    )
    from TeamWeaver.eai_adapter.dynamic_allocator import (
        AllocationResult as LegacyAllocationResult,
    )
    from TeamWeaver.eai_adapter.dynamic_miqp import (
        AllocationResult as DynamicAllocationResult,
    )

    assert AllocationResult is LegacyAllocationResult
    assert PhaseAllocationResult is DynamicAllocationResult
    assert all(
        value is not None
        for value in (
            CapabilityTracker,
            DynamicMIQPAllocator,
            TeamWeaverPipeline,
            TeamWeaverPlan,
        )
    )


def test_pipeline_cancel_delegates_to_semantic_provider(factory_world):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator
    from TeamWeaver.eai_adapter.teamweaver_pipeline import TeamWeaverPipeline

    decomposer = StubDecomposer([_dag_tasks(factory_world)])
    pipeline = TeamWeaverPipeline(
        decomposer=decomposer,
        allocator=DynamicMIQPAllocator(prefer_miqp=False),
    )

    pipeline.cancel()

    assert decomposer.cancelled is True


def _dag_tasks(world):
    return tasks_from_payloads(
        world,
        valid_inspect_payload(task_id="inspect_fire", can_parallel=False),
        valid_button_payload(
            task_id="open_channel",
            prerequisites=["inspect_fire"],
        ),
    )


def test_initial_plan_decomposes_dag_and_allocates_only_first_phase(factory_world):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator
    from TeamWeaver.eai_adapter.teamweaver_pipeline import TeamWeaverPipeline

    tasks = _dag_tasks(factory_world)
    decomposer = StubDecomposer([tasks])
    pipeline = TeamWeaverPipeline(
        decomposer=decomposer,
        allocator=DynamicMIQPAllocator(prefer_miqp=False),
    )

    plan = pipeline.plan_initial("Factory fire response", factory_world)

    assert plan.decomposition_source == "deepseek"
    assert plan.dynamic_task_count == 2
    assert plan.dag_valid is True
    assert plan.phase_index == 1
    assert plan.phase_total == 2
    assert [item.task_id for item in plan.allocation.assignments] == ["inspect_fire"]
    assert decomposer.calls[0].max_attempts == 3
    assert decomposer.calls[0].retry_delays == ()


def test_initial_plan_accepts_prerequisite_already_completed_in_world(factory_world):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator
    from TeamWeaver.eai_adapter.teamweaver_pipeline import TeamWeaverPipeline

    world = replace(factory_world, completed_task_ids=("prior_inspection",))
    tasks = tasks_from_payloads(
        world,
        valid_button_payload(
            task_id="open_channel",
            prerequisites=["prior_inspection"],
        ),
    )
    pipeline = TeamWeaverPipeline(
        decomposer=StubDecomposer([tasks]),
        allocator=DynamicMIQPAllocator(prefer_miqp=False),
    )

    plan = pipeline.plan_initial("Continue the response", world)

    assert plan.phase_index == 1
    assert plan.phase_total == 1
    assert [item.task_id for item in plan.allocation.assignments] == ["open_channel"]


def test_success_feedback_advances_existing_dag_without_provider_replan(
    factory_world,
):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator
    from TeamWeaver.eai_adapter.teamweaver_pipeline import TeamWeaverPipeline

    tasks = _dag_tasks(factory_world)
    decomposer = StubDecomposer(
        [tasks, AssertionError("successful DAG progress must stay local")]
    )
    pipeline = TeamWeaverPipeline(
        decomposer=decomposer,
        allocator=DynamicMIQPAllocator(prefer_miqp=False),
    )
    initial = pipeline.plan_initial("Factory fire response", factory_world)
    assigned = initial.allocation.assignments[0]
    pipeline.mark_navigating(assigned.task_id)
    pipeline.accept_feedback(success(assigned.task_id, assigned.robot_name))

    plan = pipeline.replan(factory_world, reason="phase_complete")

    assert plan.phase_index == 2
    assert plan.completed_task_ids == ("inspect_fire",)
    assert [item.task_id for item in plan.allocation.assignments] == ["open_channel"]
    assert len(decomposer.calls) == 1


def test_nonblocking_spawned_obstacle_does_not_force_semantic_progress_replan(
    factory_world,
):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator
    from TeamWeaver.eai_adapter.task_models import ObstacleSnapshot, TaskStatus
    from TeamWeaver.eai_adapter.teamweaver_pipeline import TeamWeaverPipeline

    spawned_obstacle = ObstacleSnapshot(
        "runtime_obstacle_1",
        (-5.0, -2.0, 0.25),
        (2.3, 0.3, 0.5),
        True,
        False,
        "relay",
        "carter_1",
        (-5.0, -0.8, 0.0),
        (-5.0, 1.7, 0.25),
    )
    world = replace(factory_world, obstacles=(spawned_obstacle,))
    tasks = tasks_from_payloads(
        factory_world,
        valid_inspect_payload(task_id="inspect"),
        valid_relay_payload(task_id="relay"),
    )
    decomposer = StubDecomposer(
        [tasks, AssertionError("a nonblocking obstacle must not revise the DAG")]
    )
    pipeline = TeamWeaverPipeline(
        decomposer=decomposer,
        allocator=DynamicMIQPAllocator(prefer_miqp=False),
    )
    initial = pipeline.plan_initial("Respond", factory_world)
    assignments = initial.allocation.assignment_by_task
    pipeline.mark_navigating("inspect")
    pipeline.mark_navigating("relay")
    pipeline.accept_feedback(success("inspect", assignments["inspect"].robot_name))

    progress = pipeline.replan(world, reason="phase_complete")

    assert len(decomposer.calls) == 1
    assert progress.allocation.assignments == ()
    assert pipeline.scheduler.status("relay") is TaskStatus.NAVIGATING


def test_phase_total_does_not_shrink_when_replan_omits_satisfied_dependency(
    factory_world,
):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator
    from TeamWeaver.eai_adapter.teamweaver_pipeline import TeamWeaverPipeline

    initial_tasks = _dag_tasks(factory_world)
    flattened_tasks = tasks_from_payloads(
        factory_world,
        valid_inspect_payload(task_id="inspect_fire", can_parallel=False),
        valid_button_payload(task_id="open_channel", prerequisites=[]),
    )
    pipeline = TeamWeaverPipeline(
        decomposer=StubDecomposer([initial_tasks, flattened_tasks]),
        allocator=DynamicMIQPAllocator(prefer_miqp=False),
    )
    initial = pipeline.plan_initial("Factory fire response", factory_world)
    assigned = initial.allocation.assignments[0]
    pipeline.mark_navigating(assigned.task_id)
    pipeline.accept_feedback(success(assigned.task_id, assigned.robot_name))

    replanned = pipeline.replan(factory_world, reason="phase_complete")

    assert replanned.phase_index == 2
    assert replanned.phase_total == 2


def test_path_failure_updates_reliability_and_keeps_sibling_locked(factory_world):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator
    from TeamWeaver.eai_adapter.teamweaver_pipeline import TeamWeaverPipeline

    tasks = tasks_from_payloads(
        factory_world,
        valid_inspect_payload(task_id="inspect"),
        valid_button_payload(task_id="button"),
    )
    decomposer = StubDecomposer([tasks, tasks])
    pipeline = TeamWeaverPipeline(
        decomposer=decomposer,
        allocator=DynamicMIQPAllocator(prefer_miqp=False),
    )
    initial = pipeline.plan_initial("Respond", factory_world)
    by_task = initial.allocation.assignment_by_task
    failed = by_task["inspect"]
    sibling = by_task["button"]
    pipeline.mark_navigating(failed.task_id)
    pipeline.mark_navigating(sibling.task_id)
    pipeline.accept_feedback(path_failure(failed.task_id, failed.robot_name))

    assert pipeline.scheduler.status("inspect") is TaskStatus.FAILED
    assert pipeline.scheduler.status("button") is TaskStatus.NAVIGATING
    assert pipeline.capabilities.reliability(failed.robot_name, "navigation") == 0.85

    replanned = pipeline.replan(factory_world, reason="path_failure")
    assert pipeline.scheduler.status("button") is TaskStatus.NAVIGATING
    if replanned.allocation.assignments:
        assert replanned.allocation.assignments[0].robot_name != sibling.robot_name


def test_semantic_replan_rekeys_a_reused_permanently_failed_task(factory_world):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator
    from TeamWeaver.eai_adapter.teamweaver_pipeline import TeamWeaverPipeline

    tasks = tasks_from_payloads(
        factory_world,
        valid_inspect_payload(task_id="inspect"),
    )
    decomposer = StubDecomposer([tasks, tasks, tasks])
    pipeline = TeamWeaverPipeline(
        decomposer=decomposer,
        allocator=DynamicMIQPAllocator(prefer_miqp=False),
    )
    initial = pipeline.plan_initial("Inspect", factory_world)
    first = initial.allocation.assignment_by_task["inspect"]
    pipeline.mark_navigating(first.task_id)
    pipeline.accept_feedback(path_failure(first.task_id, first.robot_name))
    retry = pipeline.replan(factory_world, reason="path_failure")
    second = retry.allocation.assignment_by_task["inspect"]
    pipeline.mark_navigating(second.task_id)
    pipeline.accept_feedback(path_failure(second.task_id, second.robot_name))

    replacement = pipeline.replan(factory_world, reason="path_failure")

    assert "inspect" not in {task.task_id for task in replacement.tasks}
    replacement_task = next(
        task for task in replacement.tasks if task.task_id.startswith("inspect_retry_")
    )
    assert replacement_task.continuation_of is None
    assert replacement.allocation.assignment_by_task[replacement_task.task_id]
    feedback = decomposer.calls[-1].world.to_payload()["recent_feedback"]
    assert [item["task_id"] for item in feedback if item.get("outcome") == "failed"] == [
        "inspect",
        "inspect",
    ]


def test_deferred_task_stays_ready_until_capacity_is_available(factory_world):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator
    from TeamWeaver.eai_adapter.teamweaver_pipeline import TeamWeaverPipeline

    tasks = tasks_from_payloads(
        factory_world,
        *(valid_inspect_payload(task_id=f"task_{index}", priority=index) for index in range(1, 6)),
    )
    pipeline = TeamWeaverPipeline(
        decomposer=StubDecomposer([tasks]),
        allocator=DynamicMIQPAllocator(prefer_miqp=False),
    )
    plan = pipeline.plan_initial("Inspect", factory_world)

    assert len(plan.allocation.assignments) == 4
    assert len(plan.allocation.deferred_task_ids) == 1
    deferred = plan.allocation.deferred_task_ids[0]
    assert pipeline.scheduler.status(deferred) is TaskStatus.READY


def test_hard_infeasible_fact_is_sent_to_semantic_replan(factory_world):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator
    from TeamWeaver.eai_adapter.teamweaver_pipeline import TeamWeaverPipeline

    no_relay_robot = replace(
        factory_world,
        robots=tuple(robot for robot in factory_world.robots if robot.name != "carter_1"),
    )
    tasks = tasks_from_payloads(no_relay_robot, valid_relay_payload())
    decomposer = StubDecomposer([tasks, tasks])
    pipeline = TeamWeaverPipeline(
        decomposer=decomposer,
        allocator=DynamicMIQPAllocator(prefer_miqp=False),
    )
    first = pipeline.plan_initial("Establish communication", no_relay_robot)
    assert first.allocation.hard_infeasible_task_ids == ("establish_relay",)

    pipeline.replan(no_relay_robot, reason="hard_infeasible")
    feedback = decomposer.calls[-1].world.to_payload()["recent_feedback"]
    assert any(item.get("failure_kind") == "hard_infeasible" for item in feedback)


def test_relay_lease_excludes_robot_from_dependent_phase(factory_world):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator
    from TeamWeaver.eai_adapter.teamweaver_pipeline import TeamWeaverPipeline

    tasks = tasks_from_payloads(
        factory_world,
        valid_relay_payload(task_id="relay"),
        valid_inspect_payload(task_id="inspect", prerequisites=["relay"]),
    )
    pipeline = TeamWeaverPipeline(
        decomposer=StubDecomposer([tasks, tasks]),
        allocator=DynamicMIQPAllocator(prefer_miqp=False),
    )
    initial = pipeline.plan_initial("Relay then inspect", factory_world)
    relay = initial.allocation.assignment_by_task["relay"]
    assert relay.robot_name == "carter_1"
    pipeline.mark_navigating("relay")
    pipeline.accept_feedback(success("relay", "carter_1", ("relay",)))

    dependent = pipeline.replan(factory_world, reason="phase_complete")
    assert dependent.allocation.assignment_by_task["inspect"].robot_name != "carter_1"


def test_mission_succeeds_only_after_all_retained_tasks_succeed(factory_world):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator
    from TeamWeaver.eai_adapter.teamweaver_pipeline import TeamWeaverPipeline

    tasks = tasks_from_payloads(factory_world, valid_inspect_payload())
    pipeline = TeamWeaverPipeline(
        decomposer=StubDecomposer([tasks]),
        allocator=DynamicMIQPAllocator(prefer_miqp=False),
    )
    plan = pipeline.plan_initial("Inspect", factory_world)
    assignment = plan.allocation.assignments[0]
    assert pipeline.mission_status() == "running"
    pipeline.mark_navigating(assignment.task_id)
    pipeline.accept_feedback(success(assignment.task_id, assignment.robot_name))
    assert pipeline.mission_status() == "succeeded"


def test_provider_exhaustion_never_creates_scheduler_or_local_tasks(factory_world):
    from TeamWeaver.eai_adapter.semantic_decomposer import (
        DecompositionAttemptError,
        DecompositionError,
    )
    from TeamWeaver.eai_adapter.teamweaver_pipeline import TeamWeaverPipeline

    error = DecompositionError(
        (DecompositionAttemptError(1, "RuntimeError", "provider down"),)
    )

    class NeverAllocator:
        def allocate(self, *_args, **_kwargs):
            raise AssertionError("allocator must not run")

    pipeline = TeamWeaverPipeline(
        decomposer=StubDecomposer([error]),
        allocator=NeverAllocator(),
    )
    with pytest.raises(DecompositionError, match="provider down"):
        pipeline.plan_initial("Respond", factory_world)
    assert pipeline.scheduler is None


def test_remote_terminal_return_uses_predecessor_robot_without_provider_replan(
    factory_world,
):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator
    from TeamWeaver.eai_adapter.teamweaver_pipeline import TeamWeaverPipeline

    world = _world_with_fire_rallies(factory_world)
    tasks = tasks_from_payloads(
        world,
        valid_button_payload(task_id="open_channel"),
    )
    decomposer = StubDecomposer([tasks])
    pipeline = TeamWeaverPipeline(
        decomposer=decomposer,
        allocator=DynamicMIQPAllocator(prefer_miqp=False),
    )

    initial = pipeline.plan_initial("Open the rescue channel", world)
    predecessor = initial.allocation.assignment_by_task["open_channel"]
    return_task = next(
        task for task in initial.tasks if task.continuation_of == "open_channel"
    )
    assert pipeline.mission_status() == "running"

    pipeline.mark_navigating("open_channel")
    pipeline.accept_feedback(success("open_channel", predecessor.robot_name))
    continuation = pipeline.replan(world, reason="world_state_change")

    assert len(decomposer.calls) == 1
    assignment = continuation.allocation.assignment_by_task[return_task.task_id]
    assert assignment.robot_name == predecessor.robot_name
    bound_task = next(
        task for task in continuation.tasks if task.task_id == return_task.task_id
    )
    assert bound_task.required_agent == predecessor.robot_name
    assert pipeline.mission_status() == "running"

    pipeline.mark_navigating(return_task.task_id)
    pipeline.accept_feedback(success(return_task.task_id, predecessor.robot_name))

    assert pipeline.mission_status() == "succeeded"


def test_ready_return_runs_with_serial_functional_task(factory_world):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator
    from TeamWeaver.eai_adapter.teamweaver_pipeline import TeamWeaverPipeline

    world = _world_with_fire_rallies(factory_world)
    tasks = tasks_from_payloads(
        world,
        valid_button_payload(task_id="open_channel"),
        valid_inspect_payload(
            task_id="pick_extinguisher",
            task_type="pick_extinguisher",
            description="Pick up the factory extinguisher",
            target_ref="fire_extinguisher_pickup",
            requirements={},
        ),
        valid_inspect_payload(
            task_id="deliver_extinguisher",
            task_type="deliver_extinguisher",
            description="Deliver the extinguisher to the fire",
            target_ref="hazard_1_yellow_delivery",
            requirements={},
            prerequisites=["pick_extinguisher"],
            can_parallel=False,
        ),
    )
    pipeline = TeamWeaverPipeline(
        decomposer=StubDecomposer([tasks]),
        allocator=DynamicMIQPAllocator(prefer_miqp=False),
    )
    initial = pipeline.plan_initial("Open the channel and deliver equipment", world)
    button_assignment = initial.allocation.assignment_by_task["open_channel"]
    pickup_assignment = initial.allocation.assignment_by_task["pick_extinguisher"]
    return_task = next(
        task for task in initial.tasks if task.continuation_of == "open_channel"
    )
    for assignment in (button_assignment, pickup_assignment):
        pipeline.mark_navigating(assignment.task_id)
        pipeline.accept_feedback(success(assignment.task_id, assignment.robot_name))
    carried_world = replace(
        world,
        extinguisher_available=False,
        extinguisher_carrier=pickup_assignment.robot_name,
    )

    progress = pipeline.replan(carried_world, reason="world_state_change")

    assert set(progress.allocation.assignment_by_task) == {
        "deliver_extinguisher",
        return_task.task_id,
    }
    assert (
        progress.allocation.assignment_by_task[return_task.task_id].robot_name
        == button_assignment.robot_name
    )


def test_return_retries_once_on_same_robot_without_provider_then_fails_mission(
    factory_world,
):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator
    from TeamWeaver.eai_adapter.teamweaver_pipeline import TeamWeaverPipeline

    world = _world_with_fire_rallies(factory_world)
    tasks = tasks_from_payloads(
        world,
        valid_button_payload(task_id="open_channel"),
    )
    decomposer = StubDecomposer([tasks])
    pipeline = TeamWeaverPipeline(
        decomposer=decomposer,
        allocator=DynamicMIQPAllocator(prefer_miqp=False),
    )
    initial = pipeline.plan_initial("Open the rescue channel", world)
    predecessor = initial.allocation.assignment_by_task["open_channel"]
    return_task = next(
        task for task in initial.tasks if task.continuation_of == "open_channel"
    )
    pipeline.mark_navigating("open_channel")
    pipeline.accept_feedback(success("open_channel", predecessor.robot_name))
    continuation = pipeline.replan(world, reason="world_state_change")
    first_return = continuation.allocation.assignment_by_task[return_task.task_id]

    pipeline.mark_navigating(return_task.task_id)
    pipeline.accept_feedback(
        path_failure(return_task.task_id, first_return.robot_name)
    )
    retry = pipeline.replan(world, reason="path_failure")

    retry_assignment = retry.allocation.assignment_by_task[return_task.task_id]
    assert len(decomposer.calls) == 1
    assert retry_assignment.robot_name == predecessor.robot_name
    assert pipeline.mission_status() == "running"

    pipeline.mark_navigating(return_task.task_id)
    pipeline.accept_feedback(
        path_failure(return_task.task_id, retry_assignment.robot_name)
    )
    final = pipeline.replan(world, reason="path_failure")

    assert len(decomposer.calls) == 1
    assert final.allocation.assignments == ()
    assert pipeline.scheduler.status(return_task.task_id) is TaskStatus.FAILED
    assert pipeline.mission_status() == "failed"


def test_ready_return_does_not_suppress_semantic_replan_for_sibling_failure(
    factory_world,
):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator
    from TeamWeaver.eai_adapter.teamweaver_pipeline import TeamWeaverPipeline

    world = _world_with_fire_rallies(factory_world)
    initial_tasks = tasks_from_payloads(
        world,
        valid_button_payload(task_id="open_channel"),
        valid_inspect_payload(task_id="inspect_fire"),
    )
    revised_tasks = tasks_from_payloads(
        world,
        valid_inspect_payload(task_id="retry_inspection"),
    )
    decomposer = StubDecomposer([initial_tasks, revised_tasks])
    pipeline = TeamWeaverPipeline(
        decomposer=decomposer,
        allocator=DynamicMIQPAllocator(prefer_miqp=False),
    )
    initial = pipeline.plan_initial("Respond to the fire", world)
    assignments = initial.allocation.assignment_by_task
    button = assignments["open_channel"]
    inspect = assignments["inspect_fire"]
    return_task = next(
        task for task in initial.tasks if task.continuation_of == "open_channel"
    )
    pipeline.mark_navigating(button.task_id)
    pipeline.mark_navigating(inspect.task_id)
    pipeline.accept_feedback(success(button.task_id, button.robot_name))
    pipeline.accept_feedback(path_failure(inspect.task_id, inspect.robot_name))

    replanned = pipeline.replan(world, reason="path_failure")

    assert len(decomposer.calls) == 2
    assert return_task.task_id in {task.task_id for task in replanned.tasks}
    assert "retry_inspection" in {task.task_id for task in replanned.tasks}


def test_semantic_replan_keeps_locked_return_target_stable(factory_world):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator
    from TeamWeaver.eai_adapter.teamweaver_pipeline import TeamWeaverPipeline

    world = _world_with_fire_rallies(factory_world)
    initial_tasks = tasks_from_payloads(
        world,
        valid_button_payload(task_id="open_channel"),
        valid_inspect_payload(task_id="failed_sibling"),
    )
    revised_tasks = tasks_from_payloads(
        world,
        valid_button_payload(task_id="a_replacement"),
        valid_button_payload(task_id="open_channel"),
    )
    decomposer = StubDecomposer([initial_tasks, revised_tasks])
    pipeline = TeamWeaverPipeline(
        decomposer=decomposer,
        allocator=DynamicMIQPAllocator(prefer_miqp=False),
    )
    initial = pipeline.plan_initial("Respond to the fire", world)
    assignments = initial.allocation.assignment_by_task
    locked_return = next(
        task for task in initial.tasks if task.continuation_of == "open_channel"
    )

    pipeline.mark_navigating("open_channel")
    pipeline.mark_navigating("failed_sibling")
    pipeline.accept_feedback(
        path_failure("failed_sibling", assignments["failed_sibling"].robot_name)
    )
    replanned = pipeline.replan(world, reason="path_failure")

    retained_return = next(
        task for task in replanned.tasks if task.task_id == locked_return.task_id
    )
    assert retained_return.target_ref == locked_return.target_ref


def test_semantic_replan_gives_new_return_an_unoccupied_rally(factory_world):
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator
    from TeamWeaver.eai_adapter.teamweaver_pipeline import TeamWeaverPipeline

    world = _world_with_fire_rallies(factory_world)
    initial_tasks = tasks_from_payloads(
        world,
        valid_button_payload(task_id="open_channel"),
        valid_inspect_payload(task_id="failed_sibling"),
    )
    revised_tasks = tasks_from_payloads(
        world,
        valid_button_payload(task_id="retry_button"),
    )
    pipeline = TeamWeaverPipeline(
        decomposer=StubDecomposer([initial_tasks, revised_tasks]),
        allocator=DynamicMIQPAllocator(prefer_miqp=False),
    )
    initial = pipeline.plan_initial("Respond to the fire", world)
    assignments = initial.allocation.assignment_by_task
    locked_return = next(
        task for task in initial.tasks if task.continuation_of == "open_channel"
    )

    pipeline.mark_navigating("open_channel")
    pipeline.mark_navigating("failed_sibling")
    pipeline.accept_feedback(
        success("open_channel", assignments["open_channel"].robot_name)
    )
    pipeline.accept_feedback(
        path_failure("failed_sibling", assignments["failed_sibling"].robot_name)
    )
    replanned = pipeline.replan(world, reason="path_failure")

    returns_by_predecessor = {
        task.continuation_of: task
        for task in replanned.tasks
        if task.continuation_of is not None
    }
    assert returns_by_predecessor["open_channel"].target_ref == (
        locked_return.target_ref
    )
    assert returns_by_predecessor["retry_button"].target_ref != (
        locked_return.target_ref
    )


def test_emergency_allocation_suspends_selected_navigation_before_assignment():
    from TeamWeaver.eai_adapter.capability_ontology import validate_plan_payload
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator
    from TeamWeaver.eai_adapter.task_models import (
        ExecutionFeedback,
        FailureKind,
        FeedbackOutcome,
        ObstacleSnapshot,
    )
    from TeamWeaver.eai_adapter.teamweaver_pipeline import TeamWeaverPipeline

    initial_world = make_factory_world()
    initial_world = replace(
        initial_world,
        robots=(
            initial_world.robot_by_name("carter_1"),
            initial_world.robot_by_name("m20_1"),
        ),
    )
    initial_tasks = tasks_from_payloads(
        initial_world,
        valid_inspect_payload(task_id="blocked", preferred_agent="carter_1"),
        valid_inspect_payload(task_id="sibling", preferred_agent="m20_1"),
    )
    obstacle = ObstacleSnapshot(
        "runtime_obstacle_1",
        (-5.0, -2.0, 0.25),
        (2.3, 0.3, 0.5),
        True,
        False,
        "blocked",
        "carter_1",
        (-5.0, -0.8, 0.0),
        (-5.0, 1.7, 0.25),
    )
    emergency_world = make_factory_world(obstacles=(obstacle,))
    emergency_world = replace(
        emergency_world,
        robots=(
            replace(
                initial_world.robot_by_name("carter_1"),
                busy=False,
                current_task=None,
            ),
            replace(
                initial_world.robot_by_name("m20_1"),
                busy=True,
                current_task="sibling",
                current_stage="navigating",
                preemptible=True,
            ),
        ),
    )
    emergency_tasks = validate_plan_payload(
        {
            "tasks": [
                valid_remove_obstacle_payload(),
                valid_inspect_payload(
                    task_id="blocked",
                    prerequisites=["clear_runtime_obstacle"],
                ),
            ]
        },
        emergency_world,
    )
    pipeline = TeamWeaverPipeline(
        decomposer=StubDecomposer([initial_tasks, emergency_tasks]),
        allocator=DynamicMIQPAllocator(prefer_miqp=False),
    )
    initial = pipeline.plan_initial("Respond", initial_world)
    assert initial.allocation.assignment_by_task["blocked"].robot_name == "carter_1"
    assert initial.allocation.assignment_by_task["sibling"].robot_name == "m20_1"
    pipeline.mark_navigating("blocked")
    pipeline.mark_navigating("sibling")
    pipeline.accept_feedback(
        ExecutionFeedback(
            task_id="blocked",
            robot_name="carter_1",
            outcome=FeedbackOutcome.CANCELLED,
            reason="runtime obstacle blocks the active path",
            failure_kind=FailureKind.WORLD_STATE_CONFLICT,
            relevant_capabilities=(),
            world_changes={},
            timestamp_s=5.0,
        )
    )

    plan = pipeline.replan(emergency_world, reason="world_state_change")

    assignment = plan.allocation.assignment_by_task["clear_runtime_obstacle"]
    assert assignment.robot_name == "m20_1"
    assert assignment.preempted_task_id == "sibling"
    assert pipeline.scheduler.status("sibling") is TaskStatus.READY
    assert pipeline.scheduler.attempts("sibling") == 0
    assert pipeline.scheduler.status("clear_runtime_obstacle") is TaskStatus.ASSIGNED


def test_blocking_obstacle_replan_allocates_ready_fire_return_in_parallel():
    from TeamWeaver.eai_adapter.capability_ontology import validate_plan_payload
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator
    from TeamWeaver.eai_adapter.task_models import (
        ExecutionFeedback,
        FailureKind,
        FeedbackOutcome,
        ObstacleSnapshot,
    )
    from TeamWeaver.eai_adapter.teamweaver_pipeline import TeamWeaverPipeline

    initial_world = _world_with_fire_rallies(make_factory_world())
    initial_tasks = tasks_from_payloads(
        initial_world,
        valid_button_payload(task_id="fast_sibling"),
        valid_inspect_payload(task_id="blocked"),
    )
    obstacle = ObstacleSnapshot(
        "runtime_obstacle_1",
        (-5.0, -2.0, 0.25),
        (2.3, 0.3, 0.5),
        True,
        False,
        "blocked",
        "carter_1",
        (-5.0, -0.8, 0.0),
        (-5.0, 1.7, 0.25),
    )
    emergency_world = _world_with_fire_rallies(
        make_factory_world(obstacles=(obstacle,))
    )
    emergency_tasks = validate_plan_payload(
        {
            "tasks": [
                valid_remove_obstacle_payload(),
                valid_inspect_payload(
                    task_id="blocked",
                    prerequisites=["clear_runtime_obstacle"],
                ),
            ]
        },
        emergency_world,
    )
    decomposer = StubDecomposer([initial_tasks, emergency_tasks])
    pipeline = TeamWeaverPipeline(
        decomposer=decomposer,
        allocator=DynamicMIQPAllocator(prefer_miqp=False),
    )
    initial = pipeline.plan_initial("Respond", initial_world)
    sibling = initial.allocation.assignment_by_task["fast_sibling"]
    victim = initial.allocation.assignment_by_task["blocked"]
    pipeline.mark_navigating("fast_sibling")
    pipeline.mark_navigating("blocked")
    pipeline.accept_feedback(success("fast_sibling", sibling.robot_name))
    pipeline.accept_feedback(
        ExecutionFeedback(
            task_id="blocked",
            robot_name=victim.robot_name,
            outcome=FeedbackOutcome.CANCELLED,
            reason="runtime obstacle blocks the active path",
            failure_kind=FailureKind.WORLD_STATE_CONFLICT,
            relevant_capabilities=(),
            world_changes={"active_obstacle": obstacle.to_payload()},
            timestamp_s=5.0,
        )
    )

    replanned = pipeline.replan(emergency_world, reason="world_state_change")

    assert len(decomposer.calls) == 2
    return_task = next(
        task for task in replanned.tasks if task.continuation_of == "fast_sibling"
    )
    assert set(replanned.allocation.assignment_by_task) == {
        "clear_runtime_obstacle",
        return_task.task_id,
    }
    assert (
        replanned.allocation.assignment_by_task[return_task.task_id].robot_name
        == sibling.robot_name
    )


def test_established_obstacle_plan_advances_completed_sibling_without_provider():
    from TeamWeaver.eai_adapter.capability_ontology import validate_plan_payload
    from TeamWeaver.eai_adapter.dynamic_miqp import DynamicMIQPAllocator
    from TeamWeaver.eai_adapter.task_models import ObstacleSnapshot, TaskStatus
    from TeamWeaver.eai_adapter.teamweaver_pipeline import TeamWeaverPipeline

    obstacle = ObstacleSnapshot(
        "runtime_obstacle_1",
        (-5.0, -2.0, 0.25),
        (2.3, 0.3, 0.5),
        True,
        False,
        "blocked",
        "carter_1",
        (-5.0, -0.8, 0.0),
        (-5.0, 1.7, 0.25),
    )
    world = _world_with_fire_rallies(make_factory_world(obstacles=(obstacle,)))
    tasks = validate_plan_payload(
        {
            "tasks": [
                valid_remove_obstacle_payload(),
                valid_inspect_payload(
                    task_id="blocked",
                    prerequisites=["clear_runtime_obstacle"],
                ),
                valid_button_payload(task_id="fast_sibling"),
            ]
        },
        world,
    )
    decomposer = StubDecomposer(
        [tasks, AssertionError("the established obstacle DAG must advance locally")]
    )
    pipeline = TeamWeaverPipeline(
        decomposer=decomposer,
        allocator=DynamicMIQPAllocator(prefer_miqp=False),
    )
    initial = pipeline.plan_initial("Respond", world)
    removal = initial.allocation.assignment_by_task["clear_runtime_obstacle"]
    sibling = initial.allocation.assignment_by_task["fast_sibling"]
    return_task = next(
        task for task in initial.tasks if task.continuation_of == "fast_sibling"
    )
    pipeline.mark_navigating(removal.task_id)
    pipeline.mark_navigating(sibling.task_id)
    pipeline.accept_feedback(success(sibling.task_id, sibling.robot_name))

    progress = pipeline.replan(world, reason="world_state_change")

    assert len(decomposer.calls) == 1
    assert pipeline.scheduler.status(removal.task_id) is TaskStatus.NAVIGATING
    assert (
        progress.allocation.assignment_by_task[return_task.task_id].robot_name
        == sibling.robot_name
    )
