"""Host-ticked canonical node-route execution with a planar controller adapter."""
from __future__ import annotations

from copy import deepcopy
import json
import math

import pytest

from algorithm.skills.contracts import SkillContractError
from algorithm.skills.navigation import NavigationPose, NavigationTarget, ground_navigation_profile
from algorithm.skills.tests.test_ground_navigation import _request, _topology
from task.executors.navigation import NavigationExecutor
from task.executors.navigation_backends import PlanarVelocityNavigationAdapter


class _Harness:
    def __init__(self, robot_id="carter_1", *, stable_ticks=1):
        self.robot_id = robot_id
        self.pose = NavigationPose((0.0, 0.0, 0.0), 0.0)
        self.sequence = 1
        self.time_s = 0.0
        self.velocity = [0.0, 0.0, 0.0]
        self.commands = []
        self.next_pose = None
        self.freeze_clock = False
        self.fail_commands = False
        self.adapter = PlanarVelocityNavigationAdapter(
            robot_id,
            pose_reader=lambda *_: self.pose,
            command_sink=self.write,
            sequence_reader=lambda *_: self.sequence,
            time_reader=lambda *_: self.time_s,
            velocity_reader=lambda *_: self.velocity,
            profile=ground_navigation_profile(),
            stable_ticks=stable_ticks,
        )

    def write(self, command):
        if self.fail_commands:
            raise RuntimeError("private controller failure")
        self.commands.append(command)
        self.velocity = list(command.payload["velocity"])

    def advance(self):
        if not self.freeze_clock:
            self.sequence += 1
            self.time_s += 0.1
        if self.next_pose is not None:
            self.pose = self.next_pose
            self.next_pose = None
        # A zero command is measured as stopped after the host tick.
        if self.commands and self.commands[-1].payload["velocity"] == [0.0, 0.0, 0.0]:
            self.velocity = [0.0, 0.0, 0.0]


def _current_request(harness, intent="start", *, task_id="navigate", robot_id=None, **parameters):
    return _request(
        intent, task_id=task_id, robot_id=robot_id or harness.robot_id,
        world_state_seq=harness.sequence, **parameters,
    )


def _executor(harness=None, *, topology=None, **kwargs):
    harness = harness or _Harness()
    executor = NavigationExecutor(
        topology=deepcopy(topology or _topology()), advance=harness.advance,
        simulation_time=lambda: harness.time_s, **kwargs,
    )
    executor.register_adapter(harness.adapter)
    return harness, executor


def _settle_completed(harness, executor, task_id="navigate"):
    assert executor.needs_host_tick(task_id)
    harness.advance()
    return executor.after_step(task_id)


def test_dispatch_start_route_progress_arrival_and_measured_stop():
    harness, executor = _executor()
    queued = executor.dispatch(_current_request(harness))
    assert queued.state["execution_state"] == "holding"
    assert harness.commands[-1].payload["velocity"] == [0.0, 0.0, 0.0]

    harness.next_pose = NavigationPose((2.0, 0.0, 5.0), 0.0)
    first = executor.step(_current_request(harness))
    assert first.state["status"] == "pending"
    assert first.state["target_node_id"] == "c"

    harness.next_pose = NavigationPose((2.0, 2.0, -4.0), math.pi / 2)
    completed = executor.step(_current_request(harness, "continue"))
    assert completed.state["status"] == "completed"
    assert completed.state["stop_confirmed"] is False
    stopped = _settle_completed(harness, executor)
    assert stopped.state["status"] == "completed"
    assert stopped.state["stop_confirmed"] is True
    assert stopped.state["quarantined"] is False
    assert executor.active_task_ids == ()
    assert executor.resources.snapshot()["leases"] == []
    assert stopped.private_evidence["execution_sample_count"] >= 4


def test_plan_is_pure_and_compiles_only_abstract_topology_target():
    harness, executor = _executor()
    command_count = len(harness.commands)
    plan = executor.plan(_current_request(harness))
    assert len(harness.commands) == command_count
    assert plan.commands[0].command_type == "navigation_target"
    assert plan.commands[0].payload["node_id"] == "b"
    assert executor.resources.snapshot()["leases"] == []


def test_hold_and_stop_are_lifecycle_intents_not_coordinate_replanning():
    harness, executor = _executor()
    executor.dispatch(_current_request(harness))
    held = executor.step(_current_request(harness, "hold"))
    assert held.state["status"] == "pending"
    assert harness.commands[-1].payload["velocity"] == [0.0, 0.0, 0.0]

    stopped = executor.step(_current_request(harness, "stop"))
    assert stopped.state["status"] == "cancelled"
    assert stopped.state["stop_confirmed"] is True
    with pytest.raises(SkillContractError, match="not active"):
        executor.prepare(_current_request(harness, "continue"))


@pytest.mark.parametrize("yaw", [math.pi, -math.pi, math.pi / 2])
def test_planar_adapter_rotates_before_cruising_for_large_heading_error(yaw):
    harness = _Harness()
    harness.pose = NavigationPose((0, 0, 0), yaw)
    target = NavigationTarget("b", (2, 0, 0), 1)
    harness.adapter.apply_navigation_target(target, harness.adapter.observe())
    velocity = harness.commands[-1].payload["velocity"]
    assert velocity[:2] == [0.0, 0.0]
    assert abs(velocity[2]) == pytest.approx(1.0)


def test_planar_adapter_uses_short_heading_error_and_authored_final_yaw():
    harness = _Harness()
    harness.pose = NavigationPose((0, 0, 0), -math.pi + 0.01)
    harness.adapter.apply_navigation_target(
        NavigationTarget("b", (-2, 0, 0), 1), harness.adapter.observe()
    )
    assert harness.commands[-1].payload["velocity"] == pytest.approx([0.5, 0.0, -0.02])
    harness.pose = NavigationPose((-2, 0, 0), 0)
    harness.adapter.apply_navigation_target(
        NavigationTarget("b", (-2, 0, 0), 1, math.pi / 2), harness.adapter.observe()
    )
    assert harness.commands[-1].payload["velocity"][0] == 0.0
    assert harness.commands[-1].payload["velocity"][2] > 0.0


def test_unreachable_route_stops_and_releases_only_after_quiescence():
    graph = _topology()
    graph["edges"] = []
    harness, executor = _executor(topology=graph)
    executor.dispatch(_current_request(harness))
    failed = executor.step(_current_request(harness))
    assert failed.state["status"] == "failed"
    assert failed.state["reason_code"] == "route_unreachable"
    assert failed.state["stop_confirmed"] is True
    assert harness.commands[-1].payload["velocity"] == [0.0, 0.0, 0.0]


def test_timeout_is_terminal_and_zero_with_measured_release():
    harness, executor = _executor(timeout_s=0.05)
    executor.dispatch(_current_request(harness))
    harness.time_s = 0.05
    failed = executor.step(_current_request(harness))
    assert failed.state["status"] == "failed" and failed.state["reason_code"] == "timeout"
    assert failed.state["stop_confirmed"] is True


def test_nonadvancing_host_tick_quarantines_instead_of_certifying_stop():
    harness, executor = _executor()
    executor.dispatch(_current_request(harness))
    harness.freeze_clock = True
    failed = executor.step(_current_request(harness))
    assert failed.state["status"] == "failed"
    assert failed.state["quarantined"] is True
    snapshot = executor.resources.snapshot()
    assert snapshot["leases"][0]["state"] == "quarantined"


def test_failed_controller_write_is_private_and_cannot_certify_stopping():
    harness, executor = _executor()
    executor.dispatch(_current_request(harness))
    harness.fail_commands = True
    failed = executor.prepare(_current_request(harness))
    assert failed.state["status"] == "failed"
    assert failed.state["quarantined"] is True
    assert "private controller failure" not in json.dumps(failed.to_mapping())


def test_bad_world_state_at_dispatch_is_rejected_before_lease_or_command():
    harness = _Harness()
    executor = NavigationExecutor(
        topology=_topology(), advance=harness.advance,
        snapshot_validator=lambda *_: False,
    )
    executor.register_adapter(harness.adapter)
    with pytest.raises(SkillContractError, match="snapshot"):
        executor.dispatch(_current_request(harness))
    assert harness.commands == []
    assert executor.resources.snapshot()["leases"] == []


def test_route_graph_mutation_after_dispatch_fails_closed():
    harness, executor = _executor()
    executor.dispatch(_current_request(harness))
    executor.topology["edges"][0]["directed"] = False
    failed = executor.prepare(_current_request(harness))
    assert failed.state["status"] == "failed"
    assert failed.state["quarantined"] is False


def test_endpoint_drift_cannot_be_promoted_by_stopped_feedback():
    harness, executor = _executor(max_stop_ticks=1)
    executor.dispatch(_current_request(harness))
    harness.next_pose = NavigationPose((2, 0, 0), 0)
    executor.step(_current_request(harness))
    harness.next_pose = NavigationPose((2, 2, 0), 0)
    completed = executor.step(_current_request(harness, "continue"))
    assert completed.state["status"] == "completed"
    harness.next_pose = NavigationPose((9, 9, 0), 0)
    failed = _settle_completed(harness, executor)
    assert failed.state["status"] == "failed"
    assert failed.state["reason_code"] == "endpoint_drift"


def test_retired_direct_pose_request_is_rejected_before_adapter_action():
    harness, executor = _executor()
    legacy = _current_request(harness)
    legacy["target_ref"] = None
    legacy["target_pose"] = [2.0, 2.0, 0.0]
    with pytest.raises(SkillContractError, match="target_ref only"):
        executor.dispatch(legacy)
    assert harness.commands == []
