#!/usr/bin/env python3
"""
Compatibility wrapper for the old Carter-specific launch filename.

Use algorithm/ros/nav2/nav2.launch.py for new commands.
"""

import importlib.util
from pathlib import Path


def _load_unified_launch_module():
    launch_path = Path(__file__).with_name("nav2.launch.py")
    spec = importlib.util.spec_from_file_location("eai_unified_nav2_launch", launch_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load unified Nav2 launch file: {launch_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def generate_launch_description():
    return _load_unified_launch_module().generate_launch_description()
