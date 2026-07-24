# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause
"""Heterogeneous multi-robot environment and Env DIY utilities."""

from __future__ import annotations

from typing import Any

__all__ = [
    "MultiRobotDirectEnvCfg",
    "MultiRobotDirectEnv",
]


def __getattr__(name: str) -> Any:
    if name == "MultiRobotDirectEnvCfg":
        from .multi_robot_direct_env_cfg import MultiRobotDirectEnvCfg

        return MultiRobotDirectEnvCfg
    if name == "MultiRobotDirectEnv":
        from .multi_robot_direct_env import MultiRobotDirectEnv

        return MultiRobotDirectEnv
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
