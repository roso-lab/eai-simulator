# Copyright (c) 2022-2025, The Isaac Lab Project Developers.
# All rights reserved.
#
# SPDX-License-Identifier: BSD-3-Clause
"""B2 Velocity RSL Controller Configuration."""

from .b2_rsl_rl import (
    B2_MIXED_STAIRS_CFG,
    B2_VELOCITY_RSL_CFG,
    B2MixedStairsRSLControllerCfg,
    B2VelocityRSLControllerCfg,
    compute_b2_mixed_stairs_observations,
    compute_b2_velocity_observations,
)

__all__ = [
    "B2_MIXED_STAIRS_CFG",
    "B2_VELOCITY_RSL_CFG",
    "B2MixedStairsRSLControllerCfg",
    "B2VelocityRSLControllerCfg",
    "compute_b2_mixed_stairs_observations",
    "compute_b2_velocity_observations",
]
