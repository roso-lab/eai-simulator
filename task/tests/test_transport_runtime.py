from __future__ import annotations

from algorithm.common_coordination.contract import (
    AlgorithmCondition,
    Assignment,
    CoordinationTask,
    FailureCode,
    RobotCapability,
    TaskGraph,
)
from task.contracts import AlgorithmDecision, EaiTaskPackage, DependencyOutcome, RobotState, TaskRunRequest
from task.executors.transport_runtime import TransportOrchestrationExecutor
from task.runner import TaskRunner
from task.transport import TransportAdmissionResolver


CONDITION = AlgorithmCondition(
    algorithm_id="transport-fixture", source_reference="test", implementation="fixture",
    execution_scope="assignment_only", planner="fixture",
)


def _task(*, object_ref="box", request_id=None):
    transport = {"object_ref": object_ref, "source_ref": "map:source", "target_ref": "map:target"}
    if request_id is not None:
        transport["request_id"] = request_id
    return CoordinationTask("move", "Move object", metadata={"transport": transport})


def _package(task=None):
    task = task or _task()
    return EaiTaskPackage(
        task_id="case", scene_id="scene", graph=TaskGraph(tasks=(task,), source="test"),
        robots=(RobotState(RobotCapability("robot-a", frozenset({"transport"}))),),
    )


def _assignment():
    return Assignment("move", "robot-a", source="transport-fixture")


def test_overload_is_terminal_and_never_calls_runtime():
    resolver = TransportAdmissionResolver({"box": {"mass_kg": 4}}, {"robot-a": {"payload_kg": 3}})
    calls = []
    executor = TransportOrchestrationExecutor(
        resolver, navigate=lambda *_: calls.append("navigate") or True,
        execute_command=lambda *_: calls.append("execute") or True,
    )
    executor.bind_run(TaskRunRequest(_package()))
    update = executor.execute(_assignment(), _task(), scene={})
    assert update.dependency_outcome is DependencyOutcome.TERMINAL_FAILURE
    assert update.feedback.failure_code is FailureCode.INFEASIBLE_TEAM
    assert calls == []
    assert [event["kind"] for event in executor.ledger.events] == ["run_bound", "transport_command_bound", "transport_admission"]


def test_admitted_transport_navigates_executes_and_records_provenance():
    resolver = TransportAdmissionResolver({"box": {"physics": {"mass_kg": 3}}}, {"robot-a": {"manipulation": {"payload_kg": 3}}})
    calls = []
    executor = TransportOrchestrationExecutor(
        resolver, navigate=lambda command, scene: calls.append(("navigate", command.request_id)) or True,
        execute_command=lambda command, scene: calls.append(("execute", command.object_ref)) or True,
    )
    executor.bind_run(TaskRunRequest(_package()))
    update = executor.execute(_assignment(), _task(), scene={"scene_id": "scene"})
    assert update.dependency_outcome is DependencyOutcome.COMPLETED
    assert update.feedback.failure_code is FailureCode.NONE
    assert calls[0][0] == "navigate" and calls[1] == ("execute", "box")
    assert update.feedback.world_changes["transport_admission"]["result"] == 1
    assert [event["kind"] for event in executor.ledger.events][-2:] == ["transport_started", "transport_returned"]


def test_missing_authoritative_record_fails_closed_without_callback():
    executor = TransportOrchestrationExecutor(
        TransportAdmissionResolver({}, {"robot-a": {"payload_kg": 3}}),
        execute_command=lambda *_: (_ for _ in ()).throw(AssertionError("must not execute")),
    )
    executor.bind_run(TaskRunRequest(_package()))
    update = executor.execute(_assignment(), _task(), scene={})
    assert update.dependency_outcome is DependencyOutcome.TERMINAL_FAILURE
    assert update.feedback.failure_code is FailureCode.INFEASIBLE_TEAM


def test_no_execution_callback_fails_closed_after_admission():
    executor = TransportOrchestrationExecutor(
        TransportAdmissionResolver({"box": {"mass_kg": 1}}, {"robot-a": {"payload_kg": 2}}),
    )
    executor.bind_run(TaskRunRequest(_package()))
    update = executor.execute(_assignment(), _task(), scene={})
    assert update.dependency_outcome is DependencyOutcome.TERMINAL_FAILURE
    assert update.feedback.failure_code is FailureCode.EXECUTION_FAILURE
    assert executor.ledger.events[-1]["kind"] == "transport_failed"


def test_runner_propagates_transport_admission_failure_and_keeps_independent_task_running():
    first = CoordinationTask("move", "Move object", metadata={"transport": {"object_ref": "heavy", "source_ref": "map:s", "target_ref": "map:t"}})
    second = CoordinationTask("inspect", "Inspect object", metadata={"transport": {"object_ref": "light", "source_ref": "map:s2", "target_ref": "map:t2"}})
    package = EaiTaskPackage(
        task_id="case", scene_id="scene", graph=TaskGraph(tasks=(first, second), source="test"),
        robots=(RobotState(RobotCapability("robot-a", frozenset({"transport"}))), RobotState(RobotCapability("robot-b", frozenset({"inspect"})))),
    )
    executor = TransportOrchestrationExecutor(
        TransportAdmissionResolver({"heavy": {"mass_kg": 5}, "light": {"mass_kg": 1}}, {"robot-a": {"payload_kg": 2}, "robot-b": {"payload_kg": 5}}),
        execute_command=lambda *_: True,
    )
    decision = AlgorithmDecision(condition=CONDITION, assignments=(Assignment("move", "robot-a", "fixture"), Assignment("inspect", "robot-b", "fixture")))
    result = TaskRunner(orchestration_executor=executor).run_orchestrated(
        TaskRunRequest(package), lambda task, robots: decision,
    )
    assert result.task_states == {"move": "failed", "inspect": "completed"}
    assert result.feedback[0].failure_code is FailureCode.INFEASIBLE_TEAM
    runtime_event = next(event for event in result.ledger.events if event["kind"] == "executor_ledger")
    assert runtime_event["payload"]["schema_version"] == "task.transport_runtime.v1"
    assert runtime_event["payload"]["digest"] == executor.ledger.digest()
    assert any(item["kind"] == "transport_admission" for item in runtime_event["payload"]["events"])


def test_forged_command_mass_is_ignored_by_json_constructor():
    executor = TransportOrchestrationExecutor.from_json_documents(
        {"objects": [{"id": "box", "mass_kg": 7}]},
        {"robots": [{"robot_id": "robot-a", "payload_kg": 3}]},
        execute_command=lambda *_: True,
    )
    executor.bind_run(TaskRunRequest(_package()))
    forged = _task()
    forged.metadata["transport"]["parameters"] = {"mass_kg": 0.1}
    update = executor.execute(_assignment(), forged, scene={})
    assert update.feedback.failure_code is FailureCode.INFEASIBLE_TEAM


def test_runtime_roster_constructor_binds_source_resume_before_ledger():
    executor = TransportOrchestrationExecutor.from_runtime_roster_documents(
        {"objects": [{"id": "box", "mass_kg": 3.0}]},
        {"robots": [{
            "instance_id": "hc-rt-010",
            "source_resume_id": "resume-a",
            "capability_binding": {
                "source_input": {"source_resume_id": "resume-a"}
            },
        }]},
        {"resumes": [{
            "resume_id": "resume-a",
            "manipulation": {"payload_kg": 3.0},
        }]},
        execute_command=lambda *_: True,
    )
    executor.bind_run(TaskRunRequest(_package()))
    assignment = Assignment("move", "hc-rt-010", source="transport-fixture")
    update = executor.execute(assignment, _task(), scene={})

    assert update.dependency_outcome is DependencyOutcome.COMPLETED
    admission = next(
        event for event in executor.ledger.events
        if event["kind"] == "transport_admission"
    )
    assert admission["payload"]["robot_payload_kg"] == 3.0
    command = next(
        event for event in executor.ledger.events
        if event["kind"] == "transport_command_bound"
    )
    assert command["payload"]["robot_id"] == "hc-rt-010"
