from __future__ import annotations

import argparse
import ast
import importlib.util
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "send_goal.py"


class GoalStatus:
    STATUS_UNKNOWN = 0
    STATUS_ACCEPTED = 1
    STATUS_EXECUTING = 2
    STATUS_CANCELING = 3
    STATUS_SUCCEEDED = 4
    STATUS_CANCELED = 5
    STATUS_ABORTED = 6


def _load_script(
    monkeypatch,
    *,
    server_ready=True,
    accepted=True,
    goal_response_ready=True,
    result_ready=True,
    cancel_ready=True,
    cancel_return_code=0,
    cancel_includes_goal=True,
    cancel_error=None,
    result_status=GoalStatus.STATUS_SUCCEEDED,
):
    monkeypatch.delenv("RMW_IMPLEMENTATION", raising=False)
    state = {
        "active": False,
        "events": [],
        "server_ready": server_ready,
        "accepted": accepted,
        "goal_response_ready": goal_response_ready,
        "result_ready": result_ready,
        "cancel_ready": cancel_ready,
        "cancel_return_code": cancel_return_code,
        "cancel_includes_goal": cancel_includes_goal,
        "cancel_error": cancel_error,
        "result_status": result_status,
    }

    class Logger:
        def info(self, message, **_kwargs):
            state["events"].append(("info", message))

        def error(self, message):
            state["events"].append(("error", message))

    class ClockNow:
        def to_msg(self):
            return "stamp"

    class Clock:
        def now(self):
            return ClockNow()

    class Node:
        def __init__(self, name):
            state["events"].append(("node", name))
            self._logger = Logger()

        def get_logger(self):
            return self._logger

        def get_clock(self):
            return Clock()

        def destroy_node(self):
            state["events"].append(("destroy_node",))

    class Future:
        def __init__(self, value, *, ready=True):
            self._value = value
            self._ready = ready

        def done(self):
            return self._ready

        def result(self):
            return self._value

    class GoalHandle:
        def __init__(self):
            self.accepted = state["accepted"]
            self.goal_id = "goal-id"

        def get_result_async(self):
            return Future(
                SimpleNamespace(status=state["result_status"], result=SimpleNamespace()),
                ready=state["result_ready"],
            )

        def cancel_goal_async(self):
            state["events"].append(("cancel_goal",))
            if state["cancel_error"] is not None:
                raise state["cancel_error"]
            goals_canceling = (
                [SimpleNamespace(goal_id=self.goal_id)]
                if state["cancel_includes_goal"]
                else []
            )
            return Future(
                SimpleNamespace(
                    return_code=state["cancel_return_code"],
                    goals_canceling=goals_canceling,
                ),
                ready=state["cancel_ready"],
            )

    class ActionClient:
        def __init__(self, _node, _action_type, name):
            state["events"].append(("action_client", name))

        def wait_for_server(self, *, timeout_sec):
            state["events"].append(("wait_for_server", timeout_sec))
            return state["server_ready"]

        def send_goal_async(self, _goal, *, feedback_callback):
            state["events"].append(("send_goal", feedback_callback))
            return Future(GoalHandle(), ready=state["goal_response_ready"])

    class NavigateToPose:
        class Goal:
            def __init__(self):
                self.pose = SimpleNamespace(
                    header=SimpleNamespace(frame_id="", stamp=None),
                    pose=SimpleNamespace(
                        position=SimpleNamespace(x=0.0, y=0.0),
                        orientation=SimpleNamespace(x=0.0, y=0.0, z=0.0, w=0.0),
                    ),
                )

    class ExternalShutdownException(Exception):
        pass

    rclpy = types.ModuleType("rclpy")
    rclpy.__path__ = []

    def init():
        state["events"].append(("init", os.environ.get("RMW_IMPLEMENTATION")))
        state["active"] = True

    def spin_until_future_complete(_node, _future, *, timeout_sec=None):
        state["events"].append(("spin_until_future_complete", timeout_sec))

    def ok():
        return state["active"]

    def try_shutdown():
        state["events"].append(("try_shutdown", state["active"]))
        state["active"] = False

    def shutdown():
        state["events"].append(("shutdown",))
        if not state["active"]:
            raise RuntimeError("rcl_shutdown already called")
        state["active"] = False

    rclpy.init = init
    rclpy.spin_until_future_complete = spin_until_future_complete
    rclpy.ok = ok
    rclpy.try_shutdown = try_shutdown
    rclpy.shutdown = shutdown

    modules = {
        "rclpy": rclpy,
        "rclpy.action": types.ModuleType("rclpy.action"),
        "rclpy.node": types.ModuleType("rclpy.node"),
        "rclpy.executors": types.ModuleType("rclpy.executors"),
        "nav2_msgs": types.ModuleType("nav2_msgs"),
        "nav2_msgs.action": types.ModuleType("nav2_msgs.action"),
        "geometry_msgs": types.ModuleType("geometry_msgs"),
        "geometry_msgs.msg": types.ModuleType("geometry_msgs.msg"),
        "action_msgs": types.ModuleType("action_msgs"),
        "action_msgs.msg": types.ModuleType("action_msgs.msg"),
        "action_msgs.srv": types.ModuleType("action_msgs.srv"),
    }
    modules["rclpy.action"].ActionClient = ActionClient
    modules["rclpy.node"].Node = Node
    modules["rclpy.executors"].ExternalShutdownException = ExternalShutdownException
    modules["nav2_msgs.action"].NavigateToPose = NavigateToPose
    modules["geometry_msgs.msg"].PoseStamped = object
    modules["action_msgs.msg"].GoalStatus = GoalStatus
    modules["action_msgs.srv"].CancelGoal = SimpleNamespace(
        Response=SimpleNamespace(ERROR_NONE=0)
    )
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)

    spec = importlib.util.spec_from_file_location("eai_nav2_send_goal_test", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, state


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (GoalStatus.STATUS_SUCCEEDED, True),
        (GoalStatus.STATUS_CANCELED, False),
        (GoalStatus.STATUS_ABORTED, False),
    ],
)
def test_send_reports_success_only_for_succeeded_action_result(monkeypatch, status, expected):
    send_goal, _state = _load_script(monkeypatch, result_status=status)
    node = send_goal.GoalSender(1.0, 2.0, 0.5)

    assert node.send() is expected


@pytest.mark.parametrize(
    ("server_ready", "accepted"),
    [(False, True), (True, False)],
)
def test_main_returns_nonzero_when_goal_cannot_start(monkeypatch, server_ready, accepted):
    send_goal, state = _load_script(
        monkeypatch,
        server_ready=server_ready,
        accepted=accepted,
    )

    assert send_goal.main(["--x", "1", "--y", "2"]) == 2
    assert state["events"][-2:] == [("destroy_node",), ("try_shutdown", True)]


def test_main_selects_cyclonedds_before_ros_initialization(monkeypatch):
    send_goal, state = _load_script(monkeypatch, server_ready=False)

    assert send_goal.main(["--x", "1", "--y", "2"]) == 2
    assert state["events"][0] == ("init", "rmw_cyclonedds_cpp")


def test_main_rejects_an_explicit_incompatible_rmw_before_ros_init(
    monkeypatch, capsys
):
    send_goal, state = _load_script(monkeypatch)
    monkeypatch.setenv("RMW_IMPLEMENTATION", "rmw_fastrtps_cpp")

    assert send_goal.main(["--x", "1", "--y", "2"]) == 2
    assert state["events"] == []
    assert "rmw_cyclonedds_cpp" in capsys.readouterr().err


def test_send_stops_when_goal_response_times_out(monkeypatch):
    send_goal, state = _load_script(monkeypatch, goal_response_ready=False)
    node = send_goal.GoalSender(1.0, 2.0, 0.5)

    assert node.send() is False
    assert ("spin_until_future_complete", 10.0) in state["events"]
    assert any(
        event[0] == "error" and "响应超时" in event[1]
        for event in state["events"]
    )


def test_send_cancels_goal_when_result_times_out(monkeypatch):
    send_goal, state = _load_script(monkeypatch, result_ready=False)
    node = send_goal.GoalSender(1.0, 2.0, 0.5)

    assert node.send() is False
    assert ("spin_until_future_complete", 300.0) in state["events"]
    assert ("cancel_goal",) in state["events"]
    assert ("spin_until_future_complete", 5.0) in state["events"]
    assert any(
        event[0] == "info" and "取消请求已接受" in event[1]
        for event in state["events"]
    )
    assert any(
        event[0] == "error" and "导航超时" in event[1]
        for event in state["events"]
    )


@pytest.mark.parametrize(
    ("cancel_return_code", "cancel_includes_goal"),
    [(1, True), (0, False)],
)
def test_send_reports_when_timed_out_goal_cannot_be_canceled(
    monkeypatch, cancel_return_code, cancel_includes_goal
):
    send_goal, state = _load_script(
        monkeypatch,
        result_ready=False,
        cancel_return_code=cancel_return_code,
        cancel_includes_goal=cancel_includes_goal,
    )
    node = send_goal.GoalSender(1.0, 2.0, 0.5)

    assert node.send() is False
    assert any(
        event[0] == "error" and "取消请求未被接受" in event[1]
        for event in state["events"]
    )


def test_send_reports_cancel_request_exception(monkeypatch):
    send_goal, state = _load_script(
        monkeypatch,
        result_ready=False,
        cancel_error=RuntimeError("cancel transport failed"),
    )
    node = send_goal.GoalSender(1.0, 2.0, 0.5)

    assert node.send() is False
    assert any(
        event[0] == "error" and "cancel transport failed" in event[1]
        for event in state["events"]
    )


def test_main_returns_130_when_ros_shutdown_interrupts_cancel_request(monkeypatch):
    send_goal, state = _load_script(monkeypatch, result_ready=False)
    original_spin = send_goal.rclpy.spin_until_future_complete

    def shutdown_during_cancel_wait(node, future, *, timeout_sec=None):
        original_spin(node, future, timeout_sec=timeout_sec)
        if ("cancel_goal",) in state["events"]:
            state["active"] = False
            raise send_goal.ExternalShutdownException()

    monkeypatch.setattr(
        send_goal.rclpy,
        "spin_until_future_complete",
        shutdown_during_cancel_wait,
    )

    assert send_goal.main(["--x", "1", "--y", "2"]) == 130
    assert not any(
        event[0] == "error" and "取消请求失败" in event[1]
        for event in state["events"]
    )
    assert state["events"][-2:] == [("destroy_node",), ("try_shutdown", False)]


def test_main_passes_cli_timeouts_to_goal_sender(monkeypatch):
    send_goal, state = _load_script(monkeypatch)

    assert (
        send_goal.main(
            [
                "--x",
                "1",
                "--y",
                "2",
                "--goal-response-timeout",
                "3",
                "--result-timeout",
                "4",
            ]
        )
        == 0
    )
    assert ("spin_until_future_complete", 3.0) in state["events"]
    assert ("spin_until_future_complete", 4.0) in state["events"]


def test_main_handles_ctrl_c_after_default_ros_handler_shuts_context_down(monkeypatch):
    send_goal, state = _load_script(monkeypatch)

    class InterruptingGoalSender:
        def __init__(self, *_args):
            pass

        def send(self):
            state["active"] = False
            raise KeyboardInterrupt

        def destroy_node(self):
            state["events"].append(("destroy_node",))

    monkeypatch.setattr(send_goal, "GoalSender", InterruptingGoalSender)

    assert send_goal.main(["--x", "1", "--y", "2"]) == 130
    assert ("shutdown",) not in state["events"]
    assert state["events"][-1] == ("try_shutdown", False)


def test_main_shuts_down_ros_when_node_construction_fails(monkeypatch):
    send_goal, state = _load_script(monkeypatch)

    class FailingGoalSender:
        def __init__(self, *_args):
            raise RuntimeError("node construction failed")

    monkeypatch.setattr(send_goal, "GoalSender", FailingGoalSender)

    with pytest.raises(RuntimeError, match="node construction failed"):
        send_goal.main(["--x", "1", "--y", "2"])

    assert state["events"][-1] == ("try_shutdown", True)


@pytest.mark.parametrize(
    "goal_args",
    [
        ["--x", "nan", "--y", "0"],
        ["--x", "0", "--y", "inf"],
        ["--x", "0", "--y", "0", "--yaw", "nan"],
    ],
)
def test_main_rejects_non_finite_goal_values_before_ros_init(monkeypatch, goal_args):
    send_goal, state = _load_script(monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        send_goal.main(goal_args)

    assert exc_info.value.code == 2
    assert state["events"] == []


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_finite_float_rejects_every_non_finite_spelling(monkeypatch, value):
    send_goal, _state = _load_script(monkeypatch)

    with pytest.raises(argparse.ArgumentTypeError, match="finite"):
        send_goal.finite_float(value)


def test_script_uses_main_return_value_as_process_status():
    tree = compile(SCRIPT_PATH.read_text(encoding="utf-8"), str(SCRIPT_PATH), "exec", ast.PyCF_ONLY_AST)
    guard = tree.body[-1]

    assert isinstance(guard, ast.If)
    assert isinstance(guard.body[0], ast.Raise)
    assert isinstance(guard.body[0].exc, ast.Call)
    assert isinstance(guard.body[0].exc.func, ast.Name)
    assert guard.body[0].exc.func.id == "SystemExit"
