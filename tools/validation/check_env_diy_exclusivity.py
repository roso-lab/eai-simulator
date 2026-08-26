#!/usr/bin/env python3
"""Lightweight regression checks for mutually exclusive Env DIY payloads."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
for source_root in (Path(), Path("source/EAI"), Path("source/EAI_env_diy")):
    path = str(REPO_ROOT / source_root)
    if path not in sys.path:
        sys.path.insert(0, path)

import simulator
from EAI.hmrs_env.env_diy import catalog, storage
from EAI.hmrs_env.env_diy.flow import (
    AttachmentSelection,
    ControllerChoice,
    RobotSelection,
    _attachment_combination_supported,
    interactive_selection_from_dict,
    interactive_selection_to_dict,
)
from EAI.physics.aerial_sensors import aerial_sensor_specs_from_selection
from EAI_env_diy.model import AuthoringModel


ERROR_FRAGMENT = "cannot attach both Orsus and LiDAR"


def _robot(robot_type: str, *attachments: str) -> dict:
    return {
        "type": robot_type,
        "attachments": [{"type": attachment} for attachment in attachments],
    }


def _expect_conflict(operation, error_fragment: str = ERROR_FRAGMENT) -> None:
    try:
        operation()
    except ValueError as exc:
        if error_fragment not in str(exc):
            raise AssertionError(f"Unexpected validation error: {exc}") from exc
        return
    raise AssertionError(f"Expected compatibility check to fail with: {error_fragment}")


def _check_shared_selection_validation() -> None:
    expected_builtin_capabilities = {
        "orsus": ("camera", "lidar"),
        "realsense_d455": ("camera",),
        "lidar": ("lidar",),
    }
    for robot_type in ("cf2x", "iris", "pegasus"):
        for attachment_type, expected in expected_builtin_capabilities.items():
            actual = catalog.builtin_sensor_capabilities(robot_type, attachment_type)
            if actual != expected:
                raise AssertionError(
                    f"Unexpected built-in capabilities for {robot_type}/{attachment_type}: "
                    f"{actual}"
                )
    if catalog.builtin_sensor_capabilities("b2", "orsus"):
        raise AssertionError("Ground robots must not report aerial built-in sensors.")

    if catalog.tool_label("navigation_io") != "Navigation I/O":
        raise AssertionError("The navigation_io tool must display as Navigation I/O.")
    if catalog.tool_asset_name("navigation_io") != "navigation_io":
        raise AssertionError("The navigation_io tool must use its matching image asset.")
    if catalog.attachment_supported("carter", "ros"):
        raise AssertionError("The retired ros tool key must not be accepted.")
    try:
        catalog.validate_attachment_types("carter", ["ros"])
    except ValueError:
        pass
    else:
        raise AssertionError("The retired ros tool key must fail validation.")

    for attachments in (("orsus", "lidar"), ("LIDAR", "ORSUS")):
        _expect_conflict(
            lambda attachments=attachments: catalog.validate_attachment_types(
                "carter", attachments
            )
        )

    incompatible_mounts = (
        ("carter", "z1", "orsus", "Z1 and Orsus"),
        ("carter", "z1", "lidar", "Z1 and LiDAR"),
        ("go2", "ur5", "orsus", "UR5 and Orsus"),
        ("go2", "z1", "orsus", "Z1 and Orsus"),
    )
    for robot_type, manipulator, sensor, error_fragment in incompatible_mounts:
        for attachments in ((manipulator, sensor), (sensor.upper(), manipulator.upper())):
            _expect_conflict(
                lambda robot_type=robot_type, attachments=attachments: (
                    catalog.validate_attachment_types(robot_type.upper(), attachments)
                ),
                error_fragment,
            )
            _expect_conflict(
                lambda robot_type=robot_type, attachments=attachments: (
                    interactive_selection_from_dict(
                        {
                            "scene_key": "plane",
                            "robots": [_robot(robot_type, *attachments)],
                        }
                    )
                ),
                error_fragment,
            )
        terminal_robot = RobotSelection(
            robot_type,
            ControllerChoice("default", catalog.default_controller_cfg(robot_type)),
            {},
            (AttachmentSelection(manipulator, None),),
        )
        if _attachment_combination_supported(terminal_robot, sensor):
            raise AssertionError(
                f"Terminal UI must hide {robot_type} {manipulator}+{sensor}."
            )

    for robot_type, manipulator, sensor in (
        ("b2", "ur5", "orsus"),
        ("b2", "z1", "orsus"),
        ("b2", "ur5", "lidar"),
        ("b2", "z1", "lidar"),
        ("go2", "ur5", "lidar"),
        ("go2", "z1", "lidar"),
        ("m20", "z1", "orsus"),
        ("scout", "z1", "orsus"),
        ("lite3", "z1", "orsus"),
    ):
        catalog.validate_attachment_types(robot_type, (manipulator, sensor))

    selection = interactive_selection_from_dict(
        {
            "scene_key": "plane",
            "robots": [
                _robot("carter", "orsus"),
                _robot("go2", "lidar"),
            ],
        }
    )
    actual = [tuple(item.type for item in robot.attachments) for robot in selection.robots]
    if actual != [("orsus",), ("lidar",)]:
        raise AssertionError(f"Separate robot payloads changed unexpectedly: {actual}")

    navigation_selection = interactive_selection_from_dict(
        {"scene_key": "plane", "robots": [_robot("carter", "navigation_io")]}
    )
    serialized_type = interactive_selection_to_dict(navigation_selection)["robots"][0][
        "attachments"
    ][0]["type"]
    if serialized_type != "navigation_io":
        raise AssertionError("Navigation I/O must serialize as navigation_io.")

    with tempfile.TemporaryDirectory() as temporary_directory:
        _expect_conflict(
            lambda: storage.save_task(
                "invalid_sensor_case",
                {
                    "scene_key": "plane",
                    "robots": [_robot("carter", "orsus", "lidar")],
                },
                repo_root=Path(temporary_directory),
            )
        )
        _expect_conflict(
            lambda: storage.save_task(
                "invalid_carter_mount_case",
                {
                    "scene_key": "plane",
                    "robots": [_robot("carter", "z1", "lidar")],
                },
                repo_root=Path(temporary_directory),
            ),
            "Z1 and LiDAR",
        )


def _check_authoring_model_validation() -> None:
    for first, second in (("orsus", "lidar"), ("lidar", "orsus")):
        model = AuthoringModel("plane")
        robot_id = model.add_robot("carter")
        model.attach(robot_id, first)
        _expect_conflict(lambda: model.attach(robot_id, second))

    for robot_type, manipulator, sensor, error_fragment in (
        ("carter", "z1", "orsus", "Z1 and Orsus"),
        ("carter", "z1", "lidar", "Z1 and LiDAR"),
        ("go2", "ur5", "orsus", "UR5 and Orsus"),
        ("go2", "z1", "orsus", "Z1 and Orsus"),
    ):
        for first, second in ((manipulator, sensor), (sensor, manipulator)):
            model = AuthoringModel("plane")
            robot_id = model.add_robot(robot_type)
            model.attach(robot_id, first)
            _expect_conflict(
                lambda model=model, robot_id=robot_id, second=second: model.attach(
                    robot_id, second
                ),
                error_fragment,
            )


def _check_simulator_validation() -> None:
    conflicting = {"robots": [_robot("carter", "orsus", "lidar")]}
    _expect_conflict(lambda: simulator._validate_orsus_lidar_exclusivity(conflicting))
    simulator._validate_orsus_lidar_exclusivity(
        {
            "robots": [
                _robot("carter", "orsus"),
                _robot("go2", "lidar"),
            ]
        }
    )

    incompatible_mounts = (
        ("carter", "z1", "orsus", "Z1 and Orsus"),
        ("carter", "z1", "lidar", "Z1 and LiDAR"),
        ("go2", "ur5", "orsus", "UR5 and Orsus"),
        ("go2", "z1", "orsus", "Z1 and Orsus"),
    )
    for robot_type, manipulator, sensor, error_fragment in incompatible_mounts:
        for attachments in ((manipulator, sensor), (sensor.upper(), manipulator.upper())):
            selection = {"robots": [_robot(robot_type.upper(), *attachments)]}
            _expect_conflict(
                lambda selection=selection: simulator._validate_payload_mount_compatibility(
                    selection
                ),
                error_fragment,
            )

    for robot_type, manipulator, sensor in (
        ("b2", "ur5", "orsus"),
        ("b2", "z1", "orsus"),
        ("b2", "ur5", "lidar"),
        ("b2", "z1", "lidar"),
        ("go2", "ur5", "lidar"),
        ("go2", "z1", "lidar"),
        ("m20", "z1", "orsus"),
        ("scout", "z1", "orsus"),
        ("lite3", "z1", "orsus"),
    ):
        simulator._validate_payload_mount_compatibility(
            {"robots": [_robot(robot_type, manipulator, sensor)]}
        )

    config = simulator.SimulatorLaunchConfig(
        env="conflicting_payload_scene",
        resolved_env_name="conflicting_payload_scene",
        selection_data={"robots": [_robot("carter", "z1", "lidar")]},
        existing_simulation_app=object(),
    )
    with (
        patch.object(simulator, "_load_env_cfg") as load_env_cfg,
        patch.object(simulator, "_create_env") as create_env,
    ):
        _expect_conflict(lambda: _enter_session(config), "Z1 and LiDAR")
    load_env_cfg.assert_not_called()
    create_env.assert_not_called()


def _check_navigation_io_runtime_gates() -> None:
    navigation_selection = {
        "robots": [_robot("carter", "orsus", "navigation_io")]
    }
    retired_selection = {"robots": [_robot("carter", "ros")]}

    if not simulator._selection_requires_omnigraph(navigation_selection):
        raise AssertionError("Navigation I/O must request OmniGraph support.")
    if simulator._selection_requires_omnigraph(retired_selection):
        raise AssertionError("The retired ros key must not request OmniGraph support.")
    if simulator.cmd_vel_bridge_robot_names(
        navigation_selection,
        possible_agents=("carter_1",),
    ) != ("carter_1",):
        raise AssertionError("Navigation I/O must enable cmd_vel bridge consideration.")
    if simulator.cmd_vel_bridge_robot_names(
        retired_selection,
        possible_agents=("carter_1",),
    ):
        raise AssertionError("The retired ros key must not enable cmd_vel consideration.")

    reasons = simulator._sensor_scene_single_env_reasons(navigation_selection)
    if not reasons or "navigation_io" not in reasons[0]:
        raise AssertionError("Navigation I/O sensor publishing must require one environment.")

    aerial_selection = {
        "robots": [_robot("iris", "camera", "navigation_io")]
    }
    (aerial_spec,) = aerial_sensor_specs_from_selection(
        aerial_selection,
        possible_agents=("iris_1",),
    )
    if not (aerial_spec.camera and aerial_spec.lidar and aerial_spec.base_sensors):
        raise AssertionError("Navigation I/O must enable aerial navigation sensors.")

    (retired_aerial_spec,) = aerial_sensor_specs_from_selection(
        {"robots": [_robot("iris", "camera", "ros")]},
        possible_agents=("iris_1",),
    )
    if not retired_aerial_spec.camera:
        raise AssertionError("Camera gating must remain independent of Navigation I/O.")
    if retired_aerial_spec.lidar or retired_aerial_spec.base_sensors:
        raise AssertionError("The retired ros key must not enable aerial navigation sensors.")


def _enter_session(config: simulator.SimulatorLaunchConfig) -> None:
    with simulator.open_simulator_session(config):
        pass


def main() -> None:
    _check_shared_selection_validation()
    _check_authoring_model_validation()
    _check_simulator_validation()
    _check_navigation_io_runtime_gates()
    print("PASS: Env DIY compatibility and Navigation I/O runtime gates")


if __name__ == "__main__":
    main()
