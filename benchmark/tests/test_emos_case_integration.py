from __future__ import annotations

import pytest

from algorithm.common_coordination.contract import CoordinationTask, RobotCapability, TaskGraph
from benchmark.emos import (
    CaseAssignmentRequest,
    EmosCaseDecisionAdapter,
    package_from_fire_rescue_request,
)
from task.contracts import EaiTaskPackage, RobotState


def _room06_package() -> tuple[EaiTaskPackage, tuple[RobotState, ...]]:
    tasks = (
        CoordinationTask("relay", "cross-floor book relay", target_ref="floor1-bookshelf"),
        CoordinationTask("heavy_carry", "joint heavy-object carry", target_ref="floor2-bookshelf-1"),
    )
    robots = tuple(
        RobotState(
            capability=RobotCapability(robot_id=robot_id, capabilities=frozenset({"mobile_base", "payload_transport"})),
            position=(float(index), 0.0, 0.0),
        )
        for index, robot_id in enumerate(("hc-rt-006", "hc-rt-020", "hc-rt-021"))
    )
    package = EaiTaskPackage(
        task_id="liv-t-04",
        scene_id="room_06",
        graph=TaskGraph(tasks=tasks, source="case-dry-run:room_06"),
        robots=robots,
        metadata={"scenario_id": "liv-t-04", "case_id": "room06-livt04-20261027"},
    )
    return package, robots


def test_case_emos_initialize_and_decide_without_fire_rescue_bridge() -> None:
    package, robots = _room06_package()
    request = CaseAssignmentRequest(
        task_description="room_06 liv-t-04 contract dry-run",
        scenario_id="liv-t-04",
        robot_positions={robot.capability.robot_id: robot.position for robot in robots},
        task_ids=("relay", "heavy_carry"),
    )
    adapter = EmosCaseDecisionAdapter(request, require_initialize=True)
    with pytest.raises(RuntimeError, match="must be initialized"):
        adapter(package, robots)
    adapter.initialize(
        {"task_description": request.task_description},
        {"robot_positions": dict(request.robot_positions), "tick": 0},
    )
    decision = adapter(package, robots)
    assert decision.assignments
    assert decision.provenance["adapter"] == "benchmark.emos.EmosCaseDecisionAdapter"
    assert decision.provenance["physical_success"] is False


def test_case_emos_model_endpoint_is_metered_and_bound_to_case_ids() -> None:
    package, robots = _room06_package()
    request = CaseAssignmentRequest(
        task_description="room_06 liv-t-04 contract dry-run",
        scenario_id="liv-t-04",
        robot_positions={robot.capability.robot_id: robot.position for robot in robots},
        task_ids=("relay", "heavy_carry"),
    )
    calls = []

    def endpoint(payload):
        calls.append(payload)
        return {"hc-rt-006": "relay", "hc-rt-020": "heavy_carry"}

    adapter = EmosCaseDecisionAdapter(request, model_endpoint=endpoint, require_initialize=True)
    adapter.initialize({}, {"robot_positions": dict(request.robot_positions)})
    decision = adapter(package, robots)
    assert len(calls) == 1
    assert calls[0]["scenario_id"] == "liv-t-04"
    assert set(calls[0]["robot_ids"]) == {"hc-rt-006", "hc-rt-020", "hc-rt-021"}
    assert {item.task_id for item in decision.assignments} == {"relay", "heavy_carry"}


def test_case_emos_rejects_runtime_roster_mismatch() -> None:
    package, robots = _room06_package()
    request = CaseAssignmentRequest(
        task_description="room_06 liv-t-04 contract dry-run",
        scenario_id="liv-t-04",
        robot_positions={robot.capability.robot_id: robot.position for robot in robots},
        task_ids=("relay", "heavy_carry"),
    )
    adapter = EmosCaseDecisionAdapter(request)
    with pytest.raises(ValueError, match="roster mismatch"):
        adapter(package, robots[:-1])
