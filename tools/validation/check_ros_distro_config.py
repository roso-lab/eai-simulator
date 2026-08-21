#!/usr/bin/env python3
"""Lightweight checks for ROS distro selection without importing Isaac Sim."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[2]
EAI_ASSETS_SOURCE = REPO_ROOT / "source" / "EAI_assets"
sys.path.insert(0, str(EAI_ASSETS_SOURCE))

from EAI_assets import ros_config  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir)
        config_path = temp_root / "ros_distro"
        config_path.write_text("jazzy\n", encoding="utf-8")

        with patch.dict(os.environ, {}, clear=True):
            assert ros_config.resolve_ros_distro(config_path=config_path) == "jazzy"

        with patch.dict(os.environ, {"ROS_DISTRO": "jazzy"}, clear=True):
            config_path.write_text("humble\n", encoding="utf-8")
            assert ros_config.resolve_ros_distro(config_path=config_path) == "jazzy"

        isaac_root = temp_root / "isaacsim"
        humble_path = isaac_root / "exts" / "isaacsim.ros2.bridge" / "humble"
        jazzy_path = isaac_root / "exts" / "isaacsim.ros2.bridge" / "jazzy"
        humble_path.mkdir(parents=True)
        (jazzy_path / "lib").mkdir(parents=True)
        env = {
            "ROS_DISTRO": "jazzy",
            "ISAAC_ROS_PATH": str(humble_path),
            "EAI_ISAACSIM_ROOT": str(isaac_root),
        }
        with patch.dict(os.environ, env, clear=True):
            assert ros_config.configure_ros_env() == str(jazzy_path)
            assert os.environ["ISAAC_ROS_PATH"] == str(jazzy_path)
            assert os.environ["LD_LIBRARY_PATH"].startswith(str(jazzy_path / "lib"))

    try:
        ros_config.resolve_ros_distro("rolling")
    except ValueError:
        pass
    else:
        raise AssertionError("unsupported ROS distro was accepted")

    print("PASS: ROS distro configuration")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
