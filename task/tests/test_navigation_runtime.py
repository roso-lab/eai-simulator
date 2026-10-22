"""Controller-neutral runtime boundaries for XYZ node-route navigation."""
from __future__ import annotations

from copy import deepcopy

import pytest

from algorithm.skills.contracts import SkillContractError
from algorithm.skills.navigation import (
    NavigationPose,
    NavigationTarget,
    aerial_navigation_profile,
)
from algorithm.skills.tests.test_ground_navigation import _request
from task.executors.navigation import NavigationExecutor
from task.executors.navigation_backends import WorldPositionNavigationAdapter


def _aerial_topology():
    return {
        "domain": "aerial",
        "nodes": [
            {"id": "start", "position_xyz": [0.0, 0.0, 0.0], "target_refs": []},
            {"id": "goal", "position_xyz": [1.0, 2.0, 3.0], "target_refs": ["place:goal"]},
        ],
        "edges": [{"id": "route", "from": "start", "to": "goal", "directed": True}],
    }


class _AerialHarness:
    def __init__(self, *, stable_ticks=2):
        self.pose = NavigationPose((0, 0, 0))
        self.sequence = 1
        self.time_s = 0.0
        self.velocity = [0.0, 0.0, 0.0]
        self.commands = []
        self.next_pose = None
        self.adapter = WorldPositionNavigationAdapter(
            "iris_1", pose_reader=lambda *_: self.pose,
            command_sink=self.commands.append,
            sequence_reader=lambda *_: self.sequence,
            time_reader=lambda *_: self.time_s,
            velocity_reader=lambda *_: self.velocity,
            profile=aerial_navigation_profile(arrival_radius_m=0.05),
            stable_ticks=stable_ticks,
        )

    def advance(self):
        self.sequence += 1
        self.time_s += 0.1
        if self.next_pose is not None:
            self.pose = self.next_pose
            self.next_pose = None

    def request(self, intent="start"):
        return _request(intent, robot_id="iris_1", world_state_seq=self.sequence)


def _runtime(*, stable_ticks=2, topology=None, **kwargs):
    harness = _AerialHarness(stable_ticks=stable_ticks)
    executor = NavigationExecutor(
        topology=deepcopy(topology or _aerial_topology()), advance=harness.advance,
        simulation_time=lambda: harness.time_s, **kwargs,
    )
    executor.register_adapter(harness.adapter)
    return harness, executor


def test_plan_uses_authored_xyz_node_and_does_not_issue_controller_command():
    harness, executor = _runtime()
    plan = executor.plan(harness.request())
    assert harness.commands == []
    assert plan.commands[0].command_type == "navigation_target"
    assert plan.commands[0].payload == {
        "node_id": "goal", "node_index": 1, "position_xyz": [1.0, 2.0, 3.0],
    }


def test_goal_requires_measured_xyz_arrival_and_stable_stop():
    harness, executor = _runtime(stable_ticks=2)
    executor.dispatch(harness.request())
    harness.next_pose = NavigationPose((1, 2, 3))
    completed = executor.step(harness.request())
    assert completed.state["status"] == "completed"
    assert completed.state["stop_confirmed"] is False
    assert harness.commands[-1].payload["target_pose"] == [1.0, 2.0, 3.0]

    harness.advance()
    first = executor.after_step("navigate")
    assert first.state["stop_confirmed"] is False
    harness.advance()
    second = executor.after_step("navigate")
    assert second.state["stop_confirmed"] is True


def test_cancel_never_becomes_completed_even_after_measured_stop():
    harness, executor = _runtime(stable_ticks=1)
    executor.dispatch(harness.request())
    cancelled = executor.cancel("navigate", "requested")
    assert cancelled.state["status"] == "failed"
    assert cancelled.state["reason_code"] == "cancelled"
    assert cancelled.state["stop_confirmed"] is True


def test_world_position_adapter_holds_measured_pose_on_failure():
    harness = _AerialHarness(stable_ticks=1)
    harness.pose = NavigationPose((0.25, -0.5, 1.5))
    harness.adapter.request_stop("execution failed")
    assert harness.commands[-1].payload["target_pose"] == [0.25, -0.5, 1.5]


def test_world_position_adapter_rejects_unsupported_final_heading():
    harness = _AerialHarness()
    with pytest.raises(SkillContractError, match="final heading"):
        harness.adapter.apply_navigation_target(
            NavigationTarget("goal", (1, 2, 3), 1, 0.5), harness.adapter.observe()
        )


def test_xyz_endpoint_tolerance_does_not_ignore_vertical_error():
    harness, executor = _runtime(stable_ticks=1, max_stop_ticks=1)
    executor.dispatch(harness.request())
    harness.next_pose = NavigationPose((1, 2, 3))
    completed = executor.step(harness.request())
    assert completed.state["status"] == "completed"
    harness.next_pose = NavigationPose((1, 2, 3.2))
    harness.advance()
    failed = executor.after_step("navigate")
    assert failed.state["status"] == "failed"
    assert failed.state["reason_code"] == "endpoint_drift"


@pytest.mark.parametrize("bad_goal", [(1, 2), (1, 2, 3, 4)])
def test_retired_waypoint_dimensions_are_rejected_at_request_boundary(bad_goal):
    harness, executor = _runtime()
    request = harness.request()
    request["target_ref"] = None
    request["target_pose"] = bad_goal
    with pytest.raises((SkillContractError, ValueError)):
        executor.plan(request)
