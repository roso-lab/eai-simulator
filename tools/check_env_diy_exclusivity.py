#!/usr/bin/env python3
"""Lightweight regression checks for mutually exclusive Env DIY payloads."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
for source_root in (Path(), Path("source/EAI"), Path("source/EAI_env_diy")):
    path = str(REPO_ROOT / source_root)
    if path not in sys.path:
        sys.path.insert(0, path)

import simulator
from EAI.hmrs_env.env_diy import catalog, storage
from EAI.hmrs_env.env_diy.flow import interactive_selection_from_dict
from EAI_env_diy.model import AuthoringModel


ERROR_FRAGMENT = "cannot attach both Orsus and LiDAR"


def _robot(robot_type: str, *attachments: str) -> dict:
    return {
        "type": robot_type,
        "attachments": [{"type": attachment} for attachment in attachments],
    }


def _expect_conflict(operation) -> None:
    try:
        operation()
    except ValueError as exc:
        if ERROR_FRAGMENT not in str(exc):
            raise AssertionError(f"Unexpected validation error: {exc}") from exc
        return
    raise AssertionError("Expected the Orsus/LiDAR exclusivity check to fail.")


def _check_shared_selection_validation() -> None:
    for attachments in (("orsus", "lidar"), ("LIDAR", "ORSUS")):
        _expect_conflict(
            lambda attachments=attachments: catalog.validate_attachment_types(
                "carter", attachments
            )
        )

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


def _check_authoring_model_validation() -> None:
    for first, second in (("orsus", "lidar"), ("lidar", "orsus")):
        model = AuthoringModel("plane")
        robot_id = model.add_robot("carter")
        model.attach(robot_id, first)
        _expect_conflict(lambda: model.attach(robot_id, second))


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

    config = simulator.SimulatorLaunchConfig(
        env="conflicting_sensor_scene",
        resolved_env_name="conflicting_sensor_scene",
        selection_data=conflicting,
        existing_simulation_app=object(),
    )
    with (
        patch.object(simulator, "_load_env_cfg") as load_env_cfg,
        patch.object(simulator, "_create_env") as create_env,
    ):
        _expect_conflict(lambda: _enter_session(config))
    load_env_cfg.assert_not_called()
    create_env.assert_not_called()


def _enter_session(config: simulator.SimulatorLaunchConfig) -> None:
    with simulator.open_simulator_session(config):
        pass


def main() -> None:
    _check_shared_selection_validation()
    _check_authoring_model_validation()
    _check_simulator_validation()
    print("PASS: Env DIY Orsus/LiDAR exclusivity")


if __name__ == "__main__":
    main()
