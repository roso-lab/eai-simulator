"""Contract tests and opt-in live ROS tests; no fake rospy or algorithm doubles."""
import os
import json
from dataclasses import replace
from pathlib import Path

import pytest

from algorithm.common_coordination.contract import (
    CoordinationTask, ExecutionFeedback, FailureCode, RobotCapability, TaskGraph)
from algorithm.system.coordination.bt_capabilities.native_adapter import (
    NativeBTCapabilitiesAdapter, NativeBTError, NativeBTFeedbackExecutor, encode_task)
from task.contracts import (DependencyOutcome, EaiTaskPackage, ExecutionUpdate, RobotState, TaskRunRequest)
from task.runner import TaskRunner


def package(*, chain=False, bids=None):
    tasks = [CoordinationTask("inspect", "inspect public target", frozenset({"camera"}),
                              metadata={"bt_native": {"bids": bids or {"robot_a": 9, "robot_b": 2}}})]
    if chain:
        tasks.append(CoordinationTask("verify", "verify inspected target", frozenset({"camera"}),
                                     ("inspect",), metadata={"bt_native": {"bids": {"robot_a": 1, "robot_b": 8}}}))
    return EaiTaskPackage("native_bt_fixture", "protocol_fixture", TaskGraph(tuple(tasks), "native_test"),
                          tuple(RobotState(RobotCapability(r, frozenset({"camera"})))
                                for r in ("robot_a", "robot_b")))


def test_explicit_costs_and_linear_order():
    task = package(chain=True)
    reordered = replace(task, graph=TaskGraph(tuple(reversed(task.graph.tasks)), "test"))
    assert [t["task_id"] for t in encode_task(reordered, reordered.robots)["tasks"]] == ["inspect", "verify"]
    assert encode_task(task, task.robots)["tasks"][0]["bids"] == {"robot_a": 9.0, "robot_b": 2.0}
    assert encode_task(task, task.robots)["graph_mode"] == "linear"
    assert encode_task(task, task.robots)["tasks"][1]["prerequisites"] == ["inspect"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, True, "2"])
def test_invalid_costs_fail_closed(value):
    task = package(bids={"robot_a": value, "robot_b": 2})
    with pytest.raises(ValueError, match="finite nonnegative"):
        encode_task(task, task.robots)


def test_missing_bid_never_becomes_default_cost():
    task = package(bids={"robot_a": 3})
    with pytest.raises(ValueError, match="missing explicit bid"):
        encode_task(task, task.robots)


def test_inactive_and_incapable_robots_do_not_bid():
    task = package(bids={"robot_a": 3})
    robots = (task.robots[0], RobotState(RobotCapability("robot_b", frozenset(), active=False)))
    task = replace(task, robots=robots)
    assert encode_task(task, robots)["tasks"][0]["bids"] == {"robot_a": 3.0}


def test_independent_graph_preserves_input_without_serial_heuristic():
    task = package()
    other = replace(task.graph.tasks[0], task_id="other")
    fork = replace(task, graph=TaskGraph((other,) + task.graph.tasks, "fork"))
    payload = encode_task(fork, fork.robots)
    assert payload["graph_mode"] == "dag"
    assert [item["task_id"] for item in payload["tasks"]] == ["other", "inspect"]
    assert all(item["prerequisites"] == [] for item in payload["tasks"])


def test_missing_source_is_explicit_blocker(tmp_path):
    with pytest.raises(NativeBTError, match="upstream source missing"):
        NativeBTCapabilitiesAdapter(tmp_path, endpoint="http://127.0.0.1:1")


@pytest.fixture
def native(request):
    endpoint = os.environ.get("HETEROCOORD_BT_NATIVE_ENDPOINT")
    source = os.environ.get("HETEROCOORD_BT_SOURCE_ROOT")
    if not endpoint or not source:
        pytest.skip("native ROS runtime not requested; contract tests are not native evidence")
    adapter = NativeBTCapabilitiesAdapter(source, endpoint=endpoint)
    yield adapter
    evidence_dir = os.environ.get("HETEROCOORD_BT_EVIDENCE_DIR")
    try:
        if evidence_dir and adapter.last_native_state is not None:
            path = Path(evidence_dir)
            path.mkdir(parents=True, exist_ok=True)
            (path / (request.node.name + ".json")).write_text(json.dumps({
                "evidence_scope": "native_protocol_only_not_simulation",
                "native": adapter.last_native_state,
                "task_runner": getattr(adapter, "runner_result", None),
            }, indent=2) + "\n")
    finally:
        adapter.close()


class ProtocolExecutor:
    """Explicit protocol fixture. No simulator or physical task claim."""
    def execute(self, assignment, task, *, scene):
        return ExecutionFeedback(task.task_id, assignment.robot_id, "protocol_ack",
                                 message="fixture acknowledgement; no physical execution")


def test_live_native_auction_and_task_runner_completion(native):
    task = package()
    executor = NativeBTFeedbackExecutor(native, ProtocolExecutor(), success_predicate=lambda f: f.outcome == "protocol_ack")
    result = TaskRunner(executor=executor).run(TaskRunRequest(task), native)
    native.runner_result = result.to_dict()
    assert result.decision.assignments[0].robot_id == "robot_b"
    assert result.verdict == {"status": "N/A", "reason": "physical_evidence_required", "unassigned_tasks": []}
    assert native.last_native_state["tree_state"] == "SUCCEEDED"
    assert native.last_native_state["leaf_states"][0]["feedback"]["outcome"] == "protocol_ack"
    assert len(native.last_native_state["auction_messages"]) >= 5


def test_live_chain_feedback_drives_original_memory_sequence(native):
    task = package(chain=True)
    forwarding = NativeBTFeedbackExecutor(native, ProtocolExecutor(), success_predicate=lambda f: True)

    class OrchestrationExecutor:
        def execute(self, assignment, task, *, scene):
            return ExecutionUpdate(forwarding.execute(assignment, task, scene=scene), DependencyOutcome.COMPLETED)

    result = TaskRunner(orchestration_executor=OrchestrationExecutor()).run_orchestrated(
        TaskRunRequest(task), native, replan=native.replan)
    native.runner_result = result.to_dict()
    assert [item.robot_id for item in result.feedback] == ["robot_b", "robot_a"]
    assert result.task_states == {"inspect": "completed", "verify": "completed"}
    assert result.verdict["status"] == "N/A"
    assert native.last_native_state["tree_state"] == "SUCCEEDED"


def test_live_failure_stops_native_sequence_and_rejects_duplicate_feedback(native):
    task = package(chain=True)
    decision = native(task, task.robots)
    feedback = ExecutionFeedback("inspect", decision.assignments[0].robot_id, "failed", FailureCode.EXECUTION_FAILURE)
    with pytest.raises(NativeBTError, match="identity"):
        native.feedback(replace(feedback, robot_id="robot_a"), succeeded=False)
    result = native.feedback(feedback, succeeded=False)
    assert result["tree_state"] == "FAILED"
    assert result["assignments"] == []
    assert result["leaf_states"][1]["feedback"] is None
    with pytest.raises(NativeBTError, match="consumed|terminal"):
        native.feedback(feedback, succeeded=False)


def test_live_no_eligible_bid_is_native_failure(native):
    task = package()
    robots = tuple(replace(r, capability=replace(r.capability, active=False)) for r in task.robots)
    task = replace(task, robots=robots)
    result = native(task, task.robots)
    assert result.assignments == ()
    assert result.unassigned_tasks == ("inspect",)
    assert native.last_native_state["tree_state"] == "FAILED"
    assert "No valid bids" in native.last_native_state["leaf_states"][0]["auction_error"]
