from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).with_name("send_cmd_vel.py")


def _load_script():
    spec = importlib.util.spec_from_file_location("eai_public_send_cmd_vel", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cmd_vel = _load_script()


class FakeVector:
    def __init__(self):
        self.x = None
        self.y = None
        self.z = None


class FakeTwist:
    def __init__(self):
        self.linear = FakeVector()
        self.angular = FakeVector()


class FakeLogger:
    def info(self, _message):
        pass


class FakeSignalHandlerOptions:
    NO = object()


def _components(message):
    return (
        message.linear.x,
        message.linear.y,
        message.linear.z,
        message.angular.x,
        message.angular.y,
        message.angular.z,
    )


def _fake_ros_bundle(
    *,
    interrupt: bool = False,
    spin_error: Exception | None = None,
    cleanup_publish_failure: int | None = None,
    cleanup_wait_failure: int | None = None,
    destroy_error: Exception | None = None,
    shutdown_error: Exception | None = None,
):
    events = []
    context = {"active": False, "signal_handlers_disabled": False}

    class Publisher:
        def __init__(self):
            self.stop_attempts = 0

        def publish(self, message):
            components = _components(message)
            if not context["active"]:
                events.append(("publish_inactive", components))
                raise RuntimeError("ROS context is inactive")
            if components == (0.0, 0.0, 0.0, 0.0, 0.0, 0.0):
                self.stop_attempts += 1
                if self.stop_attempts == cleanup_publish_failure:
                    events.append(("publish_error", components))
                    raise RuntimeError("cleanup publish failed")
            events.append(("publish", components))

    class Timer:
        def __init__(self, callback):
            self.callback = callback
            self.canceled = False

        def cancel(self):
            events.append(("cancel_timer",))
            self.canceled = True

        def fire_if_ready(self):
            if not self.canceled:
                self.callback()

    class Node:
        def __init__(self, name):
            events.append(("node", name))
            self.timer = None

        def create_publisher(self, message_type, topic, depth):
            assert message_type is FakeTwist
            events.append(("create_publisher", topic, depth))
            return Publisher()

        def create_timer(self, period, callback):
            events.append(("create_timer", period))
            self.timer = Timer(callback)
            return self.timer

        def get_logger(self):
            return FakeLogger()

        def destroy_node(self):
            events.append(("destroy_node",))
            if destroy_error is not None:
                raise destroy_error

    class Rclpy:
        def __init__(self):
            self.cleanup_waits = 0

        def init(self, *, signal_handler_options=None):
            events.append(("init", signal_handler_options))
            context["active"] = True
            context["signal_handlers_disabled"] = signal_handler_options is FakeSignalHandlerOptions.NO

        def spin(self, node):
            events.append(("spin",))
            assert node.timer is not None
            node.timer.fire_if_ready()
            if interrupt:
                if not context["signal_handlers_disabled"]:
                    context["active"] = False
                raise KeyboardInterrupt
            if spin_error is not None:
                raise spin_error

        def spin_once(self, node, *, timeout_sec):
            events.append(("spin_once", timeout_sec))
            if not context["active"]:
                raise RuntimeError("ROS context is inactive")
            self.cleanup_waits += 1
            if self.cleanup_waits == cleanup_wait_failure:
                raise RuntimeError("cleanup wait failed")
            if node.timer is not None:
                node.timer.fire_if_ready()

        def shutdown(self):
            events.append(("shutdown",))
            context["active"] = False
            if shutdown_error is not None:
                raise shutdown_error

    return events, Rclpy(), Node, FakeSignalHandlerOptions


@pytest.mark.parametrize("value", ["", "/", " /// "])
def test_normalize_robot_name_rejects_empty_names(value):
    with pytest.raises(argparse.ArgumentTypeError, match="must not be empty"):
        cmd_vel.normalize_robot_name(value)


def test_normalize_robot_name_removes_outer_slashes():
    assert cmd_vel.normalize_robot_name(" //carter_1// ") == "carter_1"


def test_non_negative_rate_rejects_negative_values():
    with pytest.raises(argparse.ArgumentTypeError, match="non-negative"):
        cmd_vel.non_negative_rate("-0.1")
    assert cmd_vel.non_negative_rate("0") == 0.0
    assert cmd_vel.non_negative_rate("10.5") == 10.5


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_non_negative_rate_rejects_non_finite_values(value):
    with pytest.raises(argparse.ArgumentTypeError, match="finite"):
        cmd_vel.non_negative_rate(value)


@pytest.mark.parametrize("flag", ["--linear", "--angular"])
@pytest.mark.parametrize("value", ["nan", "inf"])
def test_parser_rejects_non_finite_velocity_components(flag, value):
    with pytest.raises(SystemExit) as exc_info:
        cmd_vel.build_parser().parse_args([flag, value])

    assert exc_info.value.code == 2


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_finite_float_rejects_every_non_finite_spelling(value):
    with pytest.raises(argparse.ArgumentTypeError, match="finite"):
        cmd_vel.finite_float(value)


def test_cmd_vel_topic_uses_normalized_robot_name():
    assert cmd_vel.cmd_vel_topic("/carter_1/") == "/carter_1/cmd_vel"


def test_build_twist_sets_all_six_components():
    message = cmd_vel.build_twist(FakeTwist, linear_x=1.25, angular_z=-0.75)

    assert _components(message) == (1.25, 0.0, 0.0, 0.0, 0.0, -0.75)


def test_publish_zero_velocity_repeats_and_waits_after_each_publish():
    events = []

    class Publisher:
        def publish(self, message):
            events.append(("publish", _components(message)))

    def wait_for_delivery():
        events.append(("wait",))

    cmd_vel.publish_zero_velocity(Publisher(), FakeTwist, wait_for_delivery=wait_for_delivery)

    assert cmd_vel.STOP_MESSAGE_COUNT == 3
    assert events == [
        ("publish", (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)),
        ("wait",),
        ("publish", (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)),
        ("wait",),
        ("publish", (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)),
        ("wait",),
    ]


def test_help_warns_that_process_exit_does_not_prove_the_robot_stopped():
    assert "does not prove the robot stopped" in cmd_vel.build_parser().format_help()


@pytest.mark.parametrize("interrupt", [False, True], ids=("normal-return", "keyboard-interrupt"))
def test_continuous_main_publishes_three_zeros_before_destroy_and_shutdown(monkeypatch, interrupt):
    events, fake_rclpy, node_type, signal_options = _fake_ros_bundle(interrupt=interrupt)
    monkeypatch.setattr(cmd_vel, "_load_ros", lambda: (fake_rclpy, node_type, FakeTwist, signal_options))

    assert cmd_vel.main(["--robot", "/carter_1/", "--linear", "1.25", "--angular", "-0.75", "--rate", "10"]) == 0

    published = [event[1] for event in events if event[0] == "publish"]
    zero = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert published == [
        (1.25, 0.0, 0.0, 0.0, 0.0, -0.75),
        zero,
        zero,
        zero,
    ]
    first_stop = published.index(zero)
    assert published[first_stop:] == [zero] * cmd_vel.STOP_MESSAGE_COUNT
    assert events.index(("cancel_timer",)) < next(
        index for index, event in enumerate(events) if event == ("publish", zero)
    )
    assert [event[0] for event in events][-8:] == [
        "publish",
        "spin_once",
        "publish",
        "spin_once",
        "publish",
        "spin_once",
        "destroy_node",
        "shutdown",
    ]


def test_one_shot_main_publishes_once_and_waits_before_teardown(monkeypatch):
    events, fake_rclpy, node_type, signal_options = _fake_ros_bundle()
    monkeypatch.setattr(cmd_vel, "_load_ros", lambda: (fake_rclpy, node_type, FakeTwist, signal_options))

    assert cmd_vel.main(["--robot", "carter_1", "--linear", "0.5", "--angular", "0.25"]) == 0

    assert [event[1] for event in events if event[0] == "publish"] == [
        (0.5, 0.0, 0.0, 0.0, 0.0, 0.25)
    ]
    assert [event[0] for event in events][-4:] == ["publish", "spin_once", "destroy_node", "shutdown"]
    assert ("spin_once", 0.5) in events


def test_spin_failure_is_preserved_when_stop_delivery_also_fails(monkeypatch, capsys):
    events, fake_rclpy, node_type, signal_options = _fake_ros_bundle(
        spin_error=RuntimeError("spin runtime failed"),
        cleanup_wait_failure=1,
    )
    monkeypatch.setattr(cmd_vel, "_load_ros", lambda: (fake_rclpy, node_type, FakeTwist, signal_options))

    assert cmd_vel.main(["--linear", "1", "--rate", "10"]) == 2

    stderr = capsys.readouterr().err
    assert "spin runtime failed" in stderr
    assert "cleanup wait failed" in stderr
    assert ("destroy_node",) in events
    assert ("shutdown",) in events


@pytest.mark.parametrize("failure_kind", ["publish", "wait"])
def test_one_stop_failure_does_not_prevent_remaining_stop_attempts(monkeypatch, capsys, failure_kind):
    options = {
        "cleanup_publish_failure": 1 if failure_kind == "publish" else None,
        "cleanup_wait_failure": 1 if failure_kind == "wait" else None,
    }
    events, fake_rclpy, node_type, signal_options = _fake_ros_bundle(**options)
    monkeypatch.setattr(cmd_vel, "_load_ros", lambda: (fake_rclpy, node_type, FakeTwist, signal_options))

    assert cmd_vel.main(["--linear", "1", "--rate", "10"]) == 2

    zero = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    stop_attempts = [
        event for event in events if event[0] in {"publish", "publish_error"} and event[1] == zero
    ]
    assert len(stop_attempts) == cmd_vel.STOP_MESSAGE_COUNT
    assert failure_kind in capsys.readouterr().err
    assert [event[0] for event in events][-2:] == ["destroy_node", "shutdown"]


@pytest.mark.parametrize("failure_kind", ["destroy", "shutdown"])
def test_teardown_failure_returns_nonzero_without_escaping(monkeypatch, capsys, failure_kind):
    options = {
        "destroy_error": RuntimeError("destroy failed") if failure_kind == "destroy" else None,
        "shutdown_error": RuntimeError("shutdown failed") if failure_kind == "shutdown" else None,
    }
    events, fake_rclpy, node_type, signal_options = _fake_ros_bundle(**options)
    monkeypatch.setattr(cmd_vel, "_load_ros", lambda: (fake_rclpy, node_type, FakeTwist, signal_options))

    assert cmd_vel.main(["--linear", "0.5"]) == 2

    assert f"{failure_kind} failed" in capsys.readouterr().err
    assert ("destroy_node",) in events
    assert ("shutdown",) in events


def test_ctrl_c_keeps_context_alive_until_all_stop_messages_are_delivered(monkeypatch):
    events, fake_rclpy, node_type, signal_options = _fake_ros_bundle(interrupt=True)
    monkeypatch.setattr(cmd_vel, "_load_ros", lambda: (fake_rclpy, node_type, FakeTwist, signal_options))

    assert cmd_vel.main(["--linear", "1", "--rate", "10"]) == 0

    zero = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    assert ("init", signal_options.NO) in events
    assert not any(event[0] == "publish_inactive" for event in events)
    assert [event[1] for event in events if event[0] == "publish"][-3:] == [zero, zero, zero]
    assert events[-1] == ("shutdown",)
