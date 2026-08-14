from __future__ import annotations

import json
from dataclasses import replace

import pytest

from TeamWeaver.tests.eai_test_support import (
    tasks_from_payloads,
    valid_button_payload,
    valid_inspect_payload,
)


RALLY_OFFSETS = (
    (1.5, 1.2),
    (-1.5, 1.2),
    (-2.0, -1.0),
    (2.0, -1.0),
)


def world_with_fire_rallies(world):
    from TeamWeaver.eai_adapter.task_models import TargetSnapshot, TaskType

    hazard_x, hazard_y, _ = world.hazard_position
    rallies = tuple(
        TargetSnapshot(
            ref=f"hazard_{world.hazard_id}_fire_rally_{index}",
            position=(hazard_x + offset_x, hazard_y + offset_y, 0.0),
            available=True,
            kind="system_fire_rally",
            compatible_task_types=(TaskType.NAVIGATE,),
        )
        for index, (offset_x, offset_y) in enumerate(RALLY_OFFSETS, start=1)
    )
    return replace(world, targets=tuple(world.targets) + rallies)


def test_remote_terminal_branch_gets_one_fire_return_task(factory_world):
    from TeamWeaver.eai_adapter.mission_graph import append_fire_return_tasks

    world = world_with_fire_rallies(factory_world)
    tasks = tasks_from_payloads(
        world,
        valid_inspect_payload(task_id="inspect_fire"),
        valid_button_payload(
            task_id="open_channel",
            prerequisites=["inspect_fire"],
        ),
    )

    enriched = append_fire_return_tasks(tasks, world)

    assert len(enriched) == 3
    return_task = next(task for task in enriched if task.continuation_of is not None)
    assert return_task.continuation_of == "open_channel"
    assert return_task.prerequisites == ("open_channel",)
    assert return_task.task_type.value == "navigate"
    assert return_task.target_ref.startswith("hazard_1_fire_rally_")
    assert return_task.required_agent is None
    assert set(return_task.requirements) == {"navigation"}


def test_terminal_branch_already_inside_fire_radius_is_not_extended(factory_world):
    from TeamWeaver.eai_adapter.mission_graph import append_fire_return_tasks

    world = world_with_fire_rallies(factory_world)
    tasks = tasks_from_payloads(world, valid_inspect_payload())

    assert append_fire_return_tasks(tasks, world) == tasks


def test_terminal_delivery_inside_fire_radius_still_returns_its_owner(factory_world):
    from TeamWeaver.eai_adapter.mission_graph import append_fire_return_tasks

    world = world_with_fire_rallies(factory_world)
    tasks = tasks_from_payloads(
        world,
        valid_inspect_payload(
            task_id="deliver_extinguisher",
            task_type="deliver_extinguisher",
            description="Deliver the extinguisher to the fire response point",
            target_ref="hazard_1_yellow_delivery",
            requirements={},
            can_parallel=False,
            estimated_duration_s=120.0,
        ),
    )

    enriched = append_fire_return_tasks(tasks, world)

    assert len(enriched) == 2
    return_task = enriched[-1]
    assert return_task.continuation_of == "deliver_extinguisher"
    assert return_task.prerequisites == ("deliver_extinguisher",)
    assert return_task.required_agent is None


def test_non_terminal_extinguisher_pickup_does_not_get_fire_return(factory_world):
    from TeamWeaver.eai_adapter.mission_graph import append_fire_return_tasks

    world = world_with_fire_rallies(factory_world)
    tasks = tasks_from_payloads(
        world,
        valid_inspect_payload(
            task_id="pick_extinguisher",
            task_type="pick_extinguisher",
            description="Pick up the factory extinguisher",
            target_ref="fire_extinguisher_pickup",
            requirements={},
            prerequisites=[],
        ),
        valid_inspect_payload(
            task_id="deliver_extinguisher",
            task_type="deliver_extinguisher",
            description="Deliver the extinguisher to the fire",
            target_ref="hazard_1_yellow_delivery",
            requirements={},
            prerequisites=["pick_extinguisher"],
        ),
    )

    enriched = append_fire_return_tasks(tasks, world)

    assert [
        task.continuation_of
        for task in enriched
        if task.continuation_of is not None
    ] == ["deliver_extinguisher"]


def test_obstacle_removal_gets_return_before_blocked_branch_resumes(factory_world):
    from TeamWeaver.eai_adapter.mission_graph import append_fire_return_tasks
    from TeamWeaver.eai_adapter.task_models import (
        ObstacleSnapshot,
        TargetSnapshot,
        TaskType,
    )

    obstacle = ObstacleSnapshot(
        "runtime_obstacle_1",
        (-5.0, -2.0, 0.25),
        (2.3, 0.3, 0.5),
        True,
        False,
        "inspect_fire",
        "carter_1",
        (-5.0, -0.8, 0.0),
        (-5.0, 1.7, 0.25),
    )
    world = replace(
        factory_world,
        obstacles=(obstacle,),
        targets=factory_world.targets
        + (
            TargetSnapshot(
                obstacle.obstacle_id,
                obstacle.standoff_position,
                True,
                "obstacle",
                (TaskType.REMOVE_OBSTACLE,),
            ),
        ),
    )
    world = world_with_fire_rallies(world)
    from TeamWeaver.tests.eai_test_support import valid_remove_obstacle_payload

    tasks = tasks_from_payloads(
        world,
        valid_remove_obstacle_payload(),
        valid_inspect_payload(
            task_id="inspect_fire",
            prerequisites=["clear_runtime_obstacle"],
        ),
    )

    enriched = append_fire_return_tasks(tasks, world)

    assert any(
        task.continuation_of == "clear_runtime_obstacle" for task in enriched
    )


def test_virtual_terminal_target_still_requires_a_fire_return(factory_world):
    from TeamWeaver.eai_adapter.mission_graph import append_fire_return_tasks

    world = world_with_fire_rallies(factory_world)
    world = replace(
        world,
        targets=tuple(
            replace(target, position=None)
            if target.ref == "hold_current_position"
            else target
            for target in world.targets
        ),
    )
    wait_payload = valid_inspect_payload(
        task_id="wait_for_updates",
        task_type="wait",
        description="Wait safely for an update",
        target_ref="hold_current_position",
        requirements={},
        estimated_duration_s=5.0,
    )
    tasks = tasks_from_payloads(world, wait_payload)

    enriched = append_fire_return_tasks(tasks, world)

    assert len(enriched) == 2
    assert enriched[-1].continuation_of == "wait_for_updates"


def test_fire_return_enrichment_is_stable_and_not_recursive(factory_world):
    from TeamWeaver.eai_adapter.mission_graph import append_fire_return_tasks

    world = world_with_fire_rallies(factory_world)
    tasks = tasks_from_payloads(world, valid_button_payload())

    first = append_fire_return_tasks(tasks, world)
    second = append_fire_return_tasks(first, world)

    assert second == first
    assert sum(task.continuation_of is not None for task in second) == 1


def test_reserved_fire_rally_targets_are_hidden_from_deepseek(factory_world):
    from TeamWeaver.eai_adapter.semantic_decomposer import DeepSeekSemanticDecomposer

    world = world_with_fire_rallies(factory_world)
    messages = DeepSeekSemanticDecomposer(
        env={"DEEPSEEK_API_KEY": "test"},
    ).build_messages("Respond", world)
    payload = json.loads(messages[1]["content"])

    assert not any("fire_rally" in ref for ref in payload["allowed_target_refs"])
    assert not any(
        target["kind"] == "system_fire_rally"
        for target in payload["available_targets"]
    )
    assert not any(
        target["kind"] == "system_fire_rally"
        for target in payload["world_state"]["targets"]
    )
    assert "system_fire_rally" not in messages[1]["content"]


def test_provider_cannot_claim_a_reserved_fire_rally_target(factory_world):
    from TeamWeaver.eai_adapter.capability_ontology import (
        PlanValidationError,
        validate_plan_payload,
    )

    world = world_with_fire_rallies(factory_world)
    payload = valid_inspect_payload(
        task_id="provider_return",
        task_type="navigate",
        description="Provider-created return task",
        target_ref="hazard_1_fire_rally_1",
        requirements={
            "navigation": {"minimum": 0.6, "weight": 1.0, "hard": True}
        },
    )

    with pytest.raises(PlanValidationError, match="reserved"):
        validate_plan_payload({"tasks": [payload]}, world)
