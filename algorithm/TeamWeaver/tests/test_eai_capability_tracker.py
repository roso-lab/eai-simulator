from __future__ import annotations

from dataclasses import replace

import pytest

from TeamWeaver.tests.eai_test_support import failure, success
from TeamWeaver.eai_adapter.task_models import (
    ExecutionFeedback,
    FailureKind,
    FeedbackOutcome,
)


def test_capability_tracker_applies_deltas_and_clamps(factory_world):
    from TeamWeaver.eai_adapter.capability_tracker import CapabilityTracker

    tracker = CapabilityTracker.from_world(factory_world)
    tracker.apply(success("inspect", "carter_1", ("sensing",)))
    assert tracker.reliability("carter_1", "sensing") == 1.0

    updates = tracker.apply(
        failure(
            "inspect",
            "carter_1",
            FailureKind.EXECUTION_FAILURE,
            ("sensing",),
        )
    )
    assert tracker.reliability("carter_1", "sensing") == pytest.approx(0.85)
    assert updates[0].before == 1.0
    assert updates[0].after == pytest.approx(0.85)

    for _ in range(10):
        tracker.apply(
            failure(
                "navigate",
                "carter_1",
                FailureKind.PATH_FAILURE,
                ("navigation",),
            )
        )
    assert tracker.reliability("carter_1", "navigation") == 0.50


def test_timeout_uses_minus_point_one_and_updates_only_relevant_capability(
    factory_world,
):
    from TeamWeaver.eai_adapter.capability_tracker import CapabilityTracker

    tracker = CapabilityTracker.from_world(factory_world)
    manipulation_before = tracker.reliability("m20_1", "manipulation")
    updates = tracker.apply(
        ExecutionFeedback(
            task_id="navigate",
            robot_name="m20_1",
            outcome=FeedbackOutcome.TIMED_OUT,
            reason="deadline",
            failure_kind=FailureKind.TIMEOUT,
            relevant_capabilities=("navigation",),
            world_changes={},
            timestamp_s=5.0,
        )
    )

    assert updates[0].delta == -0.10
    assert tracker.reliability("m20_1", "navigation") == pytest.approx(0.90)
    assert tracker.reliability("m20_1", "manipulation") == manipulation_before


def test_cancelled_feedback_does_not_change_reliability(factory_world):
    from TeamWeaver.eai_adapter.capability_tracker import CapabilityTracker

    tracker = CapabilityTracker.from_world(factory_world)
    updates = tracker.apply(
        ExecutionFeedback(
            task_id="cancelled",
            robot_name="m20_1",
            outcome=FeedbackOutcome.CANCELLED,
            reason="plan replaced",
            failure_kind=FailureKind.NONE,
            relevant_capabilities=("navigation",),
            world_changes={},
            timestamp_s=5.0,
        )
    )
    assert updates == ()
    assert tracker.reliability("m20_1", "navigation") == 1.0


def test_overlay_changes_only_robot_reliability(factory_world):
    from TeamWeaver.eai_adapter.capability_tracker import CapabilityTracker

    tracker = CapabilityTracker.from_world(factory_world)
    tracker.apply(
        failure(
            "inspect",
            "carter_1",
            FailureKind.EXECUTION_FAILURE,
            ("sensing",),
        )
    )
    world = tracker.overlay(factory_world)
    before = factory_world.robot_by_name("carter_1")
    after = world.robot_by_name("carter_1")

    assert after.reliability["sensing"] == pytest.approx(0.85)
    assert after.position == before.position
    assert after.base_capabilities == before.base_capabilities
    assert after.equipment == before.equipment
    assert after.busy == before.busy
    assert after.effective_capabilities["sensing"] == pytest.approx(0.85)


def test_tracker_rejects_unknown_robot_or_capability(factory_world):
    from TeamWeaver.eai_adapter.capability_tracker import CapabilityTracker

    tracker = CapabilityTracker.from_world(factory_world)
    with pytest.raises(KeyError, match="unknown robot"):
        tracker.apply(success("x", "robot_99", ("navigation",)))
    with pytest.raises(KeyError, match="unknown capability"):
        tracker.apply(success("x", "carter_1", ("magic",)))


def test_overlay_uses_latest_positions_from_new_world_snapshot(factory_world):
    from TeamWeaver.eai_adapter.capability_tracker import CapabilityTracker

    tracker = CapabilityTracker.from_world(factory_world)
    moved = replace(
        factory_world.robot_by_name("m20_1"),
        position=(9.0, 8.0),
    )
    latest = replace(
        factory_world,
        robots=tuple(
            moved if robot.name == "m20_1" else robot
            for robot in factory_world.robots
        ),
    )
    assert tracker.overlay(latest).robot_by_name("m20_1").position == (9.0, 8.0)
