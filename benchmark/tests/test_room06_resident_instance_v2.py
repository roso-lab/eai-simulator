"""Per-book evidence and a deployed pool must not imply a selected coalition."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from benchmark.room06_fixed_package_v1 import verify_room06_fixed_package
from benchmark.room06_task_instance_v2 import book_members
from benchmark.room06_observation_bridge_v1 import Room06ObservationBridge, Room06ObservationBridgeError
from benchmark.room06_task_runtime_v1 import Room06TaskRuntime
from benchmark.room06_transport_runtime_v1 import bind_room06_transport_runtime
from benchmark.tests.test_room06_observation_bridge_v1 import _event
from task.transport import TransportCommand

ROOT = Path(__file__).resolve().parents[2]
INPUTS = ROOT / "artifacts/project_runtime/room06-deployment-v2-20261028/inputs"


def _package():
    return json.loads((INPUTS / "room06-fixed-package.json").read_text())


def test_public_task_projection_retains_all_objects_and_deployed_robots():
    from benchmark.room06_task_instance_v2 import public_task_package, logical_object_definitions
    from task.contracts import RobotState
    from algorithm.common_coordination.contract import RobotCapability
    package = _package()
    robots = tuple(RobotState(RobotCapability(robot, frozenset()))
                   for robot in package["plan"]["selected_robot_ids"])
    labels = {obj[key] for obj in logical_object_definitions(package["task_setup"])
              for key in ("initial_semantic_point", "target_semantic_point")}
    points = [{"label": label, "position_xyz": [0, 0, 0], "private": "omit"}
              for label in sorted(labels)]
    result = public_task_package(package, robots=robots, semantic_points=points)
    assert result.robots == robots and len(result.graph.tasks) == 7
    assert result.metadata["deadline_tick"] == 15000
    assert all(set(point) == {"label", "position_xyz"} for point in result.metadata["semantic_points"])
    heavy = next(task for task in result.graph.tasks if task.task_id == "transport:heavy_object")
    assert heavy.metadata["transport"]["minimum_holders"] == 2
    assert heavy.metadata["transport"]["object_mass_kg"] == 2.2
    assert "oracle" not in repr(result) and "preflight" not in repr(result)
    assert all(not task.required_capabilities for task in result.graph.tasks)
    with pytest.raises(ValueError, match="complete deployment"):
        public_task_package(package, robots=robots[:1], semantic_points=points)
    with pytest.raises(ValueError, match="missing an object"):
        public_task_package(package, robots=robots, semantic_points=[])


def _bridge():
    package = _package()
    digest = package["preflight"]["runtime_binding_digest"]
    runtime = Room06TaskRuntime(
        task_setup=package["task_setup"], task_object_bindings=package["task_object_bindings"],
        preflight_report=package["preflight"], runtime_binding_digest=digest,
    )
    bridge = Room06ObservationBridge(runtime, robot_ids=package["plan"]["selected_robot_ids"], runtime_binding_digest=digest)
    # A test decision can choose just one deployed robot. Deployment must not
    # silently assign every resident to this relay.
    bridge.observe_assignment(tick=0, assigned_robot_ids=["hc-rt-006"], decision_digest="a" * 64)
    return bridge


def _observe(bridge, book, phase, tick, target):
    bridge.observe_skill_event(_event(f"{book}-{phase}", phase, tick, "hc-rt-006", book, target))


@pytest.mark.parametrize("count", [1, 3, 5, 6])
def test_six_distinct_books_required_before_relay_complete(count):
    bridge = _bridge()
    for i in range(1, count + 1):
        book, tick = f"book_{i:02d}", i * 3
        _observe(bridge, book, "relay_pickup_completed", tick, "floor1-bookshelf")
        _observe(bridge, book, "topology_handoff_completed", tick + 1, bridge.runtime.transition_handoff_node_id)
        _observe(bridge, book, "relay_delivery_completed", tick + 2, f"floor2-bookshelf-{(i - 1) // 2 + 1}")
    bridge.finalize_at_horizon()
    labels = [row["label"] for row in bridge.evidence_material()["runtime_evidence"]["streams"]["milestones"]]
    assert ("relay_complete" in labels) is (count == 6)


@pytest.mark.parametrize("failure", ["batch_proxy", "wrong_shelf", "missing_pickup", "duplicate_book", "same_tick"])
def test_individual_book_counterexamples(failure):
    bridge = _bridge()
    if failure == "batch_proxy":
        with pytest.raises(Room06ObservationBridgeError, match="individual registered book"):
            _observe(bridge, "book_batch_01", "relay_pickup_completed", 1, "floor1-bookshelf")
        return
    if failure == "missing_pickup":
        with pytest.raises(Room06ObservationBridgeError, match="prior pickup"):
            _observe(bridge, "book_01", "topology_handoff_completed", 1, bridge.runtime.transition_handoff_node_id)
        return
    _observe(bridge, "book_01", "relay_pickup_completed", 1, "floor1-bookshelf")
    if failure == "same_tick":
        with pytest.raises(Room06ObservationBridgeError, match="strictly follow"):
            _observe(bridge, "book_01", "topology_handoff_completed", 1, bridge.runtime.transition_handoff_node_id)
        return
    _observe(bridge, "book_01", "topology_handoff_completed", 2, bridge.runtime.transition_handoff_node_id)
    if failure == "wrong_shelf":
        with pytest.raises(Room06ObservationBridgeError, match="wrong shelf"):
            _observe(bridge, "book_01", "relay_delivery_completed", 3, "floor2-bookshelf-2")
    else:
        _observe(bridge, "book_01", "relay_delivery_completed", 3, "floor2-bookshelf-1")
        with pytest.raises(Room06ObservationBridgeError, match="unique prior handoff"):
            bridge.observe_skill_event(_event("second-delivery", "relay_delivery_completed", 4, "hc-rt-006", "book_01", "floor2-bookshelf-1"))


@pytest.mark.parametrize("failure", ["duplicate", "unequal_distribution", "missing_member"])
def test_invalid_member_list_is_rejected(failure):
    batch = deepcopy(_package()["task_setup"]["objects"][0]["definition"])
    if failure == "duplicate":
        batch["books"][1]["entity_id"] = batch["books"][0]["entity_id"]
    elif failure == "unequal_distribution":
        batch["books"][0]["destination_semantic_point"] = "floor2-bookshelf-2"
    else:
        batch["books"].pop()
    with pytest.raises(ValueError):
        book_members(batch)


def test_resident_pool_policy_is_bound_and_does_not_assign_a_coalition():
    package = _package()
    verify_room06_fixed_package(package)
    assert package["plan"]["selected_robot_ids"] == ["hc-rt-004", "hc-rt-006", "hc-rt-015", "hc-rt-021", "hc-rt-027"]
    policy = package["scene_deployment"]["binding_policy"]
    assert policy["coalition_preassigned"] is False
    assert policy["selection_authority"] == "tested_system"
    assert policy["spawn_bindings"]["hc-rt-015"]["ground_node_id"] == "node:0025"
    assert policy["spawn_bindings"]["hc-rt-021"]["aerial_node_id"] == "node:0014"
    assert next(row for row in package["scene_deployment"]["robots"] if row["instance_id"] == "hc-rt-015")["position_m"][2] > 2.58
    assert package["source_inputs"]["preflight_binding"]["cross_floor_robot_ids"] == []
    assert not any(package["readiness"]["formal_gates"].values())


def test_book_mass_admission_and_heavy_single_robot_prohibition():
    package = _package()
    roster = json.loads((ROOT / "benchmark/robot_resume/robot_runtime_roster.v1.promoted.json").read_text())
    resumes = json.loads((ROOT / "benchmark/robot_resume/robot_pool_resume.v1.json").read_text())
    binding = bind_room06_transport_runtime(package, runtime_roster_document=roster, resume_document=resumes)
    assert len(binding.object_ids) == 7 and "book_batch_01" not in binding.object_ids
    def check(robot, obj):
        return binding.executor.admission.check(TransportCommand(request_id=f"test-{robot}-{obj}", task_id="liv-t-04", robot_id=robot, object_ref=obj, source_ref="source", target_ref="target"))
    assert check("hc-rt-006", "book_01").admitted is True
    assert check("hc-rt-004", "book_01").admitted is False  # Missing payload remains unknown.
    assert check("hc-rt-027", "book_01").admitted is False  # Remains deployed; an invalid call still fails.
    result = check("hc-rt-015", "heavy_object")
    assert result.robot_payload_kg > 2.2
    assert result.admitted is False and "joint holding" in result.reason
    assert check("hc-rt-020", "book_01").admitted is False
