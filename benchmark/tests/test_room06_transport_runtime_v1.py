from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from algorithm.common_coordination.contract import AlgorithmCondition, Assignment
from benchmark.room06_transport_runtime_v1 import (
    Room06TransportRuntimeError,
    bind_room06_transport_runtime,
)
from task.contracts import EaiTaskPackage, RobotState, TaskRunRequest
from task.contracts import AlgorithmDecision
from algorithm.common_coordination.contract import RobotCapability, CoordinationTask, TaskGraph
from task.transport import TransportCommand


ROOT = Path(__file__).resolve().parents[2]
ROSTER_PATH = ROOT / "benchmark/robot_resume/robot_runtime_roster.v1.promoted.json"
RESUME_PATH = ROOT / "benchmark/robot_resume/robot_pool_resume.v1.json"
SELECTED = ("hc-rt-010", "hc-rt-015", "hc-rt-025")


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _package(*, selected=SELECTED):
    body = {
        "schema": "heterocoord.room06-fixed-package.v1",
        "plan": {"selected_robot_ids": list(selected)},
        "task_setup": {
            "schema": "heterocoord.room06-task-setup.v1",
            "task_id": "liv-t-04",
            "objects": [
                {"entity_id": "book_batch_01", "definition": {"kind": "book_batch"}},
                {"entity_id": "heavy_object", "definition": {"kind": "logical_load", "mass_kg": 4.0}},
            ],
        },
    }
    return {**body, "package_digest": _digest(body)}


def _transport_task():
    return CoordinationTask(
        "move-heavy",
        "Move the heavy object",
        metadata={
            "transport": {
                "object_ref": "heavy_object",
                "source_ref": "floor1-bookshelf",
                "target_ref": "floor2-bookshelf-1",
            }
        },
    )


def test_room06_binding_uses_source_resume_payload_and_selected_roster_only():
    roster = json.loads(ROSTER_PATH.read_text(encoding="utf-8"))
    resumes = json.loads(RESUME_PATH.read_text(encoding="utf-8"))
    binding = bind_room06_transport_runtime(
        _package(), runtime_roster_document=roster, resume_document=resumes,
        execute_command=lambda *_: True,
    )
    assert binding.selected_robot_ids == SELECTED
    assert binding.object_ids == ("book_batch_01", "heavy_object")

    heavy = binding.executor.admission.check(TransportCommand(
        request_id="room06-heavy",
        task_id="liv-t-04",
        robot_id="hc-rt-010",
        object_ref="heavy_object",
        source_ref="floor1-bookshelf",
        target_ref="floor2-bookshelf-1",
    ))
    assert heavy.admitted is False
    assert heavy.reason == "object mass exceeds robot payload"

    outside = binding.executor.admission.check(TransportCommand(
        request_id="room06-outside",
        task_id="liv-t-04",
        robot_id="hc-rt-002",
        object_ref="heavy_object",
        source_ref="floor1-bookshelf",
        target_ref="floor2-bookshelf-1",
    ))
    assert outside.admitted is False
    assert outside.reason == "robot is outside the selected runtime roster"


def test_room06_binding_propagates_transport_ledger_into_orchestration():
    roster = json.loads(ROSTER_PATH.read_text(encoding="utf-8"))
    resumes = json.loads(RESUME_PATH.read_text(encoding="utf-8"))
    binding = bind_room06_transport_runtime(
        _package(), runtime_roster_document=roster, resume_document=resumes,
        execute_command=lambda *_: True,
    )
    task = _transport_task()
    package = EaiTaskPackage(
        task_id="liv-t-04",
        scene_id="room_06",
        graph=TaskGraph(tasks=(task,), source="room06-test"),
        robots=tuple(
            RobotState(RobotCapability(robot_id, frozenset({"transport"})))
            for robot_id in SELECTED
        ),
    )
    decision = AlgorithmDecision(
        condition=AlgorithmCondition(
            algorithm_id="room06-test",
            source_reference="test",
            implementation="fixture",
            execution_scope="assignment_only",
            planner="fixture",
        ),
        assignments=(Assignment("move-heavy", "hc-rt-025", "room06-test"),),
    )
    from task.runner import TaskRunner

    result = TaskRunner(orchestration_executor=binding.executor).run_orchestrated(
        TaskRunRequest(package), lambda *_: decision
    )
    assert result.task_states == {"move-heavy": "completed"}
    runtime_event = next(item for item in result.ledger.events if item["kind"] == "executor_ledger")
    assert runtime_event["payload"]["schema_version"] == "task.transport_runtime.v1"
    assert any(item["kind"] == "transport_admission" for item in runtime_event["payload"]["events"])


def test_room06_binding_rejects_tampered_package_digest():
    roster = json.loads(ROSTER_PATH.read_text(encoding="utf-8"))
    resumes = json.loads(RESUME_PATH.read_text(encoding="utf-8"))
    package = _package()
    package["task_setup"]["objects"][1]["definition"]["mass_kg"] = 1.0
    with pytest.raises(Room06TransportRuntimeError, match="package_digest"):
        bind_room06_transport_runtime(
            package, runtime_roster_document=roster, resume_document=resumes,
        )
