# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause
"""Go2 Mixed-Stairs RSL Controller Configuration (robot_lab 232-D height-scan policy)."""

from .go2_mixed_stairs import (
    GO2_MIXED_STAIRS_CFG,
    Go2MixedStairsControllerCfg,
    compute_go2_mixed_stairs_observations,
)

__all__ = [
    "GO2_MIXED_STAIRS_CFG",
    "Go2MixedStairsControllerCfg",
    "compute_go2_mixed_stairs_observations",
]
