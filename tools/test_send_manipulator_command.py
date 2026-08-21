from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).with_name("send_manipulator_command.py")


def _load_script():
    spec = importlib.util.spec_from_file_location("eai_public_send_manipulator_command", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


manipulator = _load_script()


class FakeHeader:
    def __init__(self):
        self.frame_id = ""
        self.stamp = None


class FakeJointState:
    def __init__(self):
        self.header = FakeHeader()
        self.name = []
        self.position = []


class FakeVector:
    def __init__(self):
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self.w = 0.0


class FakePose:
    def __init__(self):
        self.position = FakeVector()
        self.orientation = FakeVector()


class FakePoseStamped:
    def __init__(self):
        self.header = FakeHeader()
        self.pose = FakePose()


def _fake_ros_bundle(*, interrupt=False, node_error=None):
    state = {"active": False, "events": []}

    class Publisher:
        def __init__(self, topic):
            self.topic_name = topic
            self.messages = []

        def get_subscription_count(self):
            return 1

        def publish(self, message):
            self.messages.append(message)
            state["events"].append(("publish", self.topic_name))

    class ClockNow:
        def to_msg(self):
            return "stamp"

    class Clock:
        def now(self):
            return ClockNow()

    class Node:
        def __init__(self, name):
            state["events"].append(("node", name))
            if node_error is not None:
                raise node_error

        def create_publisher(self, message_type, topic, depth):
            state["events"].append(("create_publisher", message_type, topic, depth))
            publisher = Publisher(topic)
            state["publisher"] = publisher
            return publisher

        def get_clock(self):
            return Clock()

        def create_subscription(self, message_type, topic, callback, depth):
            subscription = object()
            state["events"].append(("create_subscription", message_type, topic, callback, depth))
            return subscription

        def destroy_subscription(self, subscription):
            state["events"].append(("destroy_subscription", subscription))

        def destroy_node(self):
            state["events"].append(("destroy_node",))

    class Rclpy:
        def init(self):
            state["events"].append(("init",))
            state["active"] = True

        def spin_once(self, _node, *, timeout_sec):
            state["events"].append(("spin_once", timeout_sec))
            if interrupt:
                state["active"] = False
                raise KeyboardInterrupt

        def ok(self):
            return state["active"]

        def try_shutdown(self):
            state["events"].append(("try_shutdown", state["active"]))
            state["active"] = False

        def shutdown(self):
            state["events"].append(("shutdown",))
            if not state["active"]:
                raise RuntimeError("rcl_shutdown already called")
            state["active"] = False

    return state, (Rclpy(), Node, FakePoseStamped, FakeJointState)


def _run_main_with_fake_ros(monkeypatch, argv):
    state, ros_bundle = _fake_ros_bundle()
    monkeypatch.setattr(manipulator, "_load_ros", lambda: ros_bundle)
    assert manipulator.main(argv) == 0
    return state["publisher"]


def test_topic_names_normalize_robot_slashes():
    assert manipulator.topic_names(" //carter_1// ", "ur5") == {
        "target_pose": "/carter_1/ur5/target_pose",
        "joint_command": "/carter_1/ur5/joint_command",
        "joint_states": "/carter_1/ur5/joint_states",
        "ee_pose": "/carter_1/ur5/ee_pose",
        "gripper_command": "/carter_1/ur5/gripper_command",
        "gripper_state": "/carter_1/ur5/gripper_state",
    }


@pytest.mark.parametrize(
    ("model", "joint", "xyz", "gripper"),
    [
        ("ur5", [0.0] * 6, None, None),
        ("ur5", None, [0.1, 0.2, 0.3], None),
        ("z1", None, None, 0.5),
    ],
)
def test_validate_target_accepts_supported_model_target_pairs(model, joint, xyz, gripper):
    manipulator.validate_target(model, joint, xyz, gripper)


def test_validate_target_rejects_gripper_for_ur5():
    with pytest.raises(ValueError, match="only supported by model z1"):
        manipulator.validate_target("ur5", None, None, 0.5)


def test_validate_target_rejects_unknown_model_and_ambiguous_target():
    with pytest.raises(ValueError, match="Unsupported model"):
        manipulator.validate_target("other", [0.0] * 6, None, None)
    with pytest.raises(ValueError, match="exactly one"):
        manipulator.validate_target("z1", [0.0] * 6, None, 0.5)


@pytest.mark.parametrize(
    "target_args",
    [
        ["--joint", "0", "1", "2", "3", "4", "nan"],
        ["--xyz", "0", "inf", "0"],
        ["--gripper", "inf"],
        ["--xyz", "0", "0", "0", "--quat", "0", "nan", "0", "1"],
    ],
)
def test_parser_rejects_non_finite_command_values(target_args):
    with pytest.raises(SystemExit) as exc_info:
        manipulator.build_parser().parse_args(
            ["--robot", "go2_1", "--model", "z1", *target_args]
        )

    assert exc_info.value.code == 2


@pytest.mark.parametrize(
    "limit_args",
    [
        ["--timeout", "-0.1"],
        ["--timeout", "nan"],
        ["--eps", "-0.1"],
        ["--eps", "inf"],
        ["--joint-eps", "-0.1"],
        ["--joint-eps", "nan"],
    ],
)
def test_parser_rejects_invalid_timeouts_and_tolerances(limit_args):
    with pytest.raises(SystemExit) as exc_info:
        manipulator.build_parser().parse_args(
            [
                "--robot",
                "carter_1",
                "--model",
                "ur5",
                "--joint",
                "0",
                "1",
                "2",
                "3",
                "4",
                "5",
                *limit_args,
            ]
        )

    assert exc_info.value.code == 2


def test_help_distinguishes_discovery_wait_from_feedback_timeout():
    help_text = manipulator.build_parser().format_help()

    assert "subscriber discovery" in help_text
    assert "additional seconds" in help_text


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_finite_float_rejects_every_non_finite_spelling(value):
    with pytest.raises(argparse.ArgumentTypeError, match="finite"):
        manipulator.finite_float(value)


def test_joint_error_uses_joint_names_instead_of_state_order():
    state_names = ["robot/wrist_1_joint", "robot/shoulder_pan_joint", "robot/elbow_joint"]
    state_positions = [3.0, 1.0, 2.0]
    target_names = ["shoulder_pan_joint", "elbow_joint", "wrist_1_joint"]
    target_positions = [1.0, 2.0, 3.0]

    assert manipulator.joint_error(state_names, state_positions, target_names, target_positions) == pytest.approx(0.0)


def test_joint_error_returns_none_when_a_target_joint_is_missing():
    assert manipulator.joint_error(["joint1"], [0.0], ["joint1", "joint2"], [0.0, 0.0]) is None


def test_position_error_is_cartesian_euclidean_distance():
    assert manipulator.position_error([1.0, 2.0, 3.0], [4.0, 6.0, 3.0]) == pytest.approx(5.0)


def test_main_calls_validate_target_before_loading_ros(monkeypatch):
    calls = []

    def reject_target(model, joint, xyz, gripper):
        calls.append((model, joint, xyz, gripper))
        raise ValueError("validation sentinel")

    monkeypatch.setattr(manipulator, "validate_target", reject_target)
    monkeypatch.setattr(manipulator, "_load_ros", lambda: pytest.fail("ROS loaded before target validation"))

    with pytest.raises(SystemExit) as exc_info:
        manipulator.main(["--robot", "carter_1", "--model", "ur5", "--joint", "0", "1", "2", "3", "4", "5"])

    assert exc_info.value.code == 2
    assert calls == [("ur5", [0.0, 1.0, 2.0, 3.0, 4.0, 5.0], None, None)]


def test_main_rejects_base_link_pose_wait_before_loading_ros(monkeypatch):
    monkeypatch.setattr(
        manipulator,
        "_load_ros",
        lambda: pytest.fail("ROS loaded for an unsupported wait frame"),
    )

    with pytest.raises(SystemExit) as exc_info:
        manipulator.main(
            [
                "--robot",
                "carter_1",
                "--model",
                "ur5",
                "--xyz",
                "0.1",
                "0.2",
                "0.3",
                "--frame-id",
                "base_link",
                "--wait",
            ]
        )

    assert exc_info.value.code == 2


def test_main_builds_ur5_joint_command(monkeypatch):
    publisher = _run_main_with_fake_ros(
        monkeypatch,
        ["--robot", "/carter_1/", "--model", "ur5", "--joint", "0", "1", "2", "3", "4", "5"],
    )

    assert publisher.topic_name == "/carter_1/ur5/joint_command"
    assert publisher.messages[0].name == list(manipulator.MODEL_JOINTS["ur5"])
    assert publisher.messages[0].position == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    assert publisher.messages[0].header.stamp == "stamp"


def test_main_builds_z1_gripper_command(monkeypatch):
    publisher = _run_main_with_fake_ros(
        monkeypatch,
        ["--robot", "go2_1", "--model", "z1", "--gripper", "0.35"],
    )

    assert publisher.topic_name == "/go2_1/z1/gripper_command"
    assert publisher.messages[0].name == ["jointGripper"]
    assert publisher.messages[0].position == [0.35]


def test_main_builds_pose_command(monkeypatch):
    publisher = _run_main_with_fake_ros(
        monkeypatch,
        [
            "--robot",
            "carter_1",
            "--model",
            "ur5",
            "--xyz",
            "0.1",
            "0.2",
            "0.3",
            "--quat",
            "0",
            "0",
            "0",
            "1",
            "--frame-id",
            "base_link",
        ],
    )

    message = publisher.messages[0]
    assert publisher.topic_name == "/carter_1/ur5/target_pose"
    assert message.header.frame_id == "base_link"
    assert (message.pose.position.x, message.pose.position.y, message.pose.position.z) == (0.1, 0.2, 0.3)
    assert (
        message.pose.orientation.x,
        message.pose.orientation.y,
        message.pose.orientation.z,
        message.pose.orientation.w,
    ) == (0.0, 0.0, 0.0, 1.0)


def test_main_handles_ctrl_c_after_ros_context_is_already_inactive(monkeypatch):
    state, ros_bundle = _fake_ros_bundle(interrupt=True)
    monkeypatch.setattr(manipulator, "_load_ros", lambda: ros_bundle)

    result = manipulator.main(
        [
            "--robot",
            "carter_1",
            "--model",
            "ur5",
            "--joint",
            "0",
            "1",
            "2",
            "3",
            "4",
            "5",
            "--wait",
        ]
    )

    assert result == 130
    assert ("shutdown",) not in state["events"]
    assert state["events"][-1] == ("try_shutdown", False)


def test_main_shuts_down_ros_when_node_construction_fails(monkeypatch):
    state, ros_bundle = _fake_ros_bundle(node_error=RuntimeError("node construction failed"))
    monkeypatch.setattr(manipulator, "_load_ros", lambda: ros_bundle)

    with pytest.raises(RuntimeError, match="node construction failed"):
        manipulator.main(
            [
                "--robot",
                "carter_1",
                "--model",
                "ur5",
                "--joint",
                "0",
                "1",
                "2",
                "3",
                "4",
                "5",
            ]
        )

    assert state["events"][-1] == ("try_shutdown", True)
