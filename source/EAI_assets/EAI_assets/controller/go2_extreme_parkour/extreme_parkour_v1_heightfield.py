"""Teacher height-field tensor for the isolated Extreme Parkour v1 map."""

from __future__ import annotations

import torch

from .extreme_parkour_v1_geometry import (
    HORIZONTAL_SCALE,
    NUM_COLUMNS,
    NUM_LEVELS,
    TRACK_SIZE,
    terrain_height_field,
)


def build_extreme_parkour_v1_heightfields(
    device: torch.device, dtype: torch.dtype
) -> torch.Tensor:
    """Build ``(level, column, x, y)`` from the exact collision-height source."""

    num_x = round(TRACK_SIZE[0] / HORIZONTAL_SCALE) + 1
    num_y = round(TRACK_SIZE[1] / HORIZONTAL_SCALE) + 1
    fields = torch.empty((NUM_LEVELS, NUM_COLUMNS, num_x, num_y), device=device, dtype=dtype)
    for level in range(NUM_LEVELS):
        for column in range(NUM_COLUMNS):
            fields[level, column] = torch.as_tensor(
                terrain_height_field(level, column), device=device, dtype=dtype
            )
    return fields
