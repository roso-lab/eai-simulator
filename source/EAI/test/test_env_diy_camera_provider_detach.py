from __future__ import annotations

from EAI_env_diy.model import AuthoringModel


def _attachment_types(model: AuthoringModel, robot_id: str) -> set[str]:
    return {item.type for item in model.robot(robot_id).attachments}


def test_detaching_one_camera_payload_preserves_camera_with_other_provider() -> None:
    model = AuthoringModel("plane")
    robot_id = model.add_robot("carter")
    for attachment in ("orsus", "realsense_d455", "camera"):
        model.attach(robot_id, attachment)

    model.detach(robot_id, "orsus")

    assert _attachment_types(model, robot_id) == {"realsense_d455", "camera"}


def test_detaching_last_camera_payload_removes_camera_without_builtin_provider() -> None:
    model = AuthoringModel("plane")
    robot_id = model.add_robot("carter")
    for attachment in ("realsense_d455", "camera"):
        model.attach(robot_id, attachment)

    model.detach(robot_id, "realsense_d455")

    assert _attachment_types(model, robot_id) == set()


def test_detaching_payload_does_not_remove_builtin_camera_tool() -> None:
    model = AuthoringModel("plane")
    robot_id = model.add_robot("iris")
    model.attach(robot_id, "camera")

    model.detach(robot_id, "realsense_d455")

    assert _attachment_types(model, robot_id) == {"camera"}
