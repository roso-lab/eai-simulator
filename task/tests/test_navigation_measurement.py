from __future__ import annotations

from copy import deepcopy
import json

import pytest

from algorithm.skills.contracts import SkillContractError
from algorithm.skills.navigation import NavigationPose, NavigationTarget, ground_navigation_profile
from algorithm.skills.tests.test_ground_navigation import _request, _topology
from task.executors.navigation import NavigationExecutor
from task.executors.navigation_backends import PlanarVelocityNavigationAdapter
from task.resources import InvalidResourceLeaseError, ResourceManager


class _Runtime:
    def __init__(self):
        self.pose_calls = 0
        self.pose = NavigationPose((0.0, 0.0, 0.0), 0.0)
        self.sequence = 1
        self.time_s = 0.0
        self.velocity = [0.0, 0.0, 0.0]
        self.commands = []
        self.adapter = PlanarVelocityNavigationAdapter(
            "carter_1",
            pose_reader=self.read_pose,
            command_sink=self.commands.append,
            sequence_reader=lambda *_: self.sequence,
            time_reader=lambda *_: self.time_s,
            velocity_reader=lambda *_: self.velocity,
            profile=ground_navigation_profile(),
            stable_ticks=2,
        )

    def read_pose(self, *_):
        self.pose_calls += 1
        return self.pose

    def advance(self):
        self.sequence += 1
        self.time_s += 0.1

    def request(self, intent="start"):
        return _request(intent, task_id="navigate", robot_id="carter_1",
                        world_state_seq=self.sequence)


def test_resource_measurement_has_stable_opt_in_ids_and_complete_records():
    first = ResourceManager(identity_namespace="reconstruction")
    second = ResourceManager(identity_namespace="reconstruction")
    released = first.acquire("worker-a", ("robot:r1",))
    first.release(released)
    active = first.acquire("worker-b", ("robot:r2",))
    foreign = second.acquire("worker-a", ("robot:r1",))

    assert released.lease_id == foreign.lease_id == "reconstruction:00000001"
    assert active.lease_id == "reconstruction:00000002"
    with pytest.raises(InvalidResourceLeaseError, match="not issued"):
        first.validate_lease(foreign)

    measured = first.measurement_snapshot()
    json.dumps(measured, allow_nan=False)
    assert measured["identity_counter"] == 2
    assert [row["state"] for row in measured["records"]] == ["released", "active"]
    assert measured["owner_lease_index"] == {"worker-b": active.lease_id}
    measured["records"][1]["resources"].clear()
    assert first.measurement_snapshot()["records"][1]["resources"] == ["robot:r2"]
    assert first.snapshot()["leases"] == [{
        "lease_id": active.lease_id,
        "owner": "worker-b",
        "resources": ["robot:r2"],
        "state": "active",
        "quarantine_reason": None,
    }]


def test_resource_default_identity_remains_random_and_authentic():
    first = ResourceManager()
    second = ResourceManager()
    left = first.acquire("worker", ("robot:r1",))
    right = second.acquire("worker", ("robot:r1",))

    assert left.lease_id != right.lease_id
    assert len(left.lease_id) == len(right.lease_id) == 32
    assert first.measurement_snapshot()["identity_namespace"] is None
    with pytest.raises(InvalidResourceLeaseError, match="not issued"):
        first.validate_lease(right)


@pytest.mark.parametrize("value", ["", "   ", 1])
def test_resource_identity_namespace_is_explicit_and_validated(value):
    with pytest.raises(ValueError, match="identity_namespace"):
        ResourceManager(identity_namespace=value)


def test_planar_adapter_measurement_is_detached_and_has_no_reader_side_effect():
    runtime = _Runtime()
    observation = runtime.adapter.observe()
    runtime.adapter.apply_navigation_target(
        NavigationTarget("node:b", (2.0, 0.0, 0.0), 1), observation,
    )
    calls = runtime.pose_calls
    measured = runtime.adapter.measurement_snapshot()

    assert runtime.pose_calls == calls
    assert measured["state"]["last_command"]["payload"]["velocity"] == [0.5, 0.0, 0.0]
    assert measured["state"]["stable_count"] == 0
    assert measured["configuration"]["stable_ticks"] == 2
    measured["state"]["last_command"]["payload"]["velocity"][0] = 99
    assert runtime.adapter.last_command.payload["velocity"][0] == 0.5


def test_provider_and_executor_measurement_cover_active_state_without_callbacks():
    runtime = _Runtime()
    resources = ResourceManager(identity_namespace="route-run")
    executor = NavigationExecutor(
        topology=deepcopy(_topology()),
        advance=runtime.advance,
        simulation_time=lambda: runtime.time_s,
        resources=resources,
        owner_id="navigation:reconstruction",
        timeout_s=4.0,
    )
    executor.register_adapter(runtime.adapter)
    executor.dispatch(runtime.request())
    executor.prepare(runtime.request())
    calls = runtime.pose_calls

    measured = executor.measurement_snapshot()
    assert runtime.pose_calls == calls
    json.dumps(measured, allow_nan=False)
    assert measured["owner_id"] == "navigation:reconstruction"
    assert measured["topology_binding"]["callable"] is False
    assert measured["topology_binding"]["topology_digest"]
    assert measured["active_task_ids"] == ["navigate"]
    assert measured["tasks"]["navigate"]["prepared"] is True
    assert measured["tasks"]["navigate"]["prepared_intent"] == "start"
    assert measured["tasks"]["navigate"]["lease"]["lease_id"] == "route-run:00000001"
    provider = measured["tasks"]["navigate"]["provider"]
    assert provider["tasks"]["navigate"]["request_identity"]
    assert provider["tasks"]["navigate"]["tracker"]["node_index"] == 1
    assert provider["tasks"]["navigate"]["route_graph_digest"] == measured["tasks"]["navigate"]["topology_digest"]
    assert measured["adapters"]["carter_1"]["measurement"]["state"]["last_command"]

    measured["tasks"]["navigate"]["provider"]["tasks"]["navigate"]["tracker"]["node_index"] = 99
    measured["resources"]["records"].clear()
    fresh = executor.measurement_snapshot()
    assert fresh["tasks"]["navigate"]["provider"]["tasks"]["navigate"]["tracker"]["node_index"] == 1
    assert len(fresh["resources"]["records"]) == 1


def test_executor_measurement_handles_static_robot_map_without_calling_topology_callback():
    graph = _topology()
    mapped = NavigationExecutor(topology={"carter_1": graph, "carter_2": deepcopy(graph)})
    mapped_snapshot = mapped.measurement_snapshot()
    assert mapped_snapshot["topology_binding"]["kind"] == "static_by_robot"
    assert set(mapped_snapshot["topology_binding"]["topology_by_robot"]) == {
        "carter_1", "carter_2",
    }

    calls = []
    dynamic = NavigationExecutor(topology=lambda robot_id: calls.append(robot_id) or graph)
    dynamic_snapshot = dynamic.measurement_snapshot()
    assert dynamic_snapshot["topology_binding"] == {
        "kind": "callable",
        "callable": True,
        "topology": None,
        "topology_digest": None,
        "topology_by_robot": None,
    }
    assert calls == []


@pytest.mark.parametrize("owner_id", ["", "   ", 1])
def test_executor_owner_id_is_explicit_and_validated(owner_id):
    with pytest.raises(SkillContractError, match="owner_id"):
        NavigationExecutor(topology=_topology(), owner_id=owner_id)
