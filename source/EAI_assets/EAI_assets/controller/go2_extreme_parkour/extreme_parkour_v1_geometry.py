"""Pure geometry for the isolated Extreme Parkour v1 terrain.

This module intentionally has no Isaac Lab dependency.  Collision meshes,
Teacher height observations, waypoints, visualization, and geometry tests all
consume the same deterministic height fields and layouts from here.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from scipy import interpolate


TRACK_SIZE = (18.0, 4.0)
NUM_LEVELS = 10
NUM_GOALS = 8
TERRAIN_TYPES = ("flat", "box", "gap", "hurdle", "parkour_pads")
COLUMNS_PER_TYPE = 8
NUM_COLUMNS = len(TERRAIN_TYPES) * COLUMNS_PER_TYPE

# Isaaclab_Parkour deliberately uses 8 cm rather than the Isaac Gym
# reference's 5 cm grid: this keeps the complete 10 x 40 map cookable as one
# stable Isaac Lab / PhysX triangle-mesh collider.
HORIZONTAL_SCALE = 0.08
VERTICAL_SCALE = 0.005
DOWNSAMPLED_SCALE = 0.075
ROUGHNESS_MIN_AMPLITUDE = 0.02
ROUGHNESS_MAX_AMPLITUDE = 0.06
ROUGHNESS_SEED_BASE = 271_828_182
PIT_DEPTH = -0.80


def level_to_difficulty(level: int) -> float:
    """Map curriculum row 0..9 deterministically to difficulty 0..1."""

    return min(max(int(level), 0), NUM_LEVELS - 1) / (NUM_LEVELS - 1)


def terrain_type_from_column(column: int) -> str:
    column = min(max(int(column), 0), NUM_COLUMNS - 1)
    return TERRAIN_TYPES[column // COLUMNS_PER_TYPE]


def _rng(level: int, column: int, salt: int) -> np.random.Generator:
    seed = ROUGHNESS_SEED_BASE + 1009 * int(level) + 9176 * int(column) + 65_537 * salt
    return np.random.default_rng(seed)


def reference_roughness_height_field(level: int, column: int) -> np.ndarray:
    """Generate bounded reference-style roughness using bilinear interpolation.

    Extreme Parkour samples 5 mm height steps on a 7.5 cm coarse grid and
    linearly interpolates them to the Isaac-Lab-compatible 8 cm collision
    grid.  ``kx=ky=1`` is the
    RectBivariateSpline equivalent of that interpolation and, unlike the old
    cubic spline, cannot overshoot the sampled height range.
    """

    size_x, size_y = TRACK_SIZE
    num_x = round(size_x / HORIZONTAL_SCALE) + 1
    num_y = round(size_y / HORIZONTAL_SCALE) + 1
    coarse_num_x = int(size_x / DOWNSAMPLED_SCALE)
    coarse_num_y = int(size_y / DOWNSAMPLED_SCALE)

    rng = _rng(level, column, salt=1)
    amplitude = rng.uniform(ROUGHNESS_MIN_AMPLITUDE, ROUGHNESS_MAX_AMPLITUDE)
    amplitude_steps = int(amplitude / VERTICAL_SCALE)
    possible_steps = np.arange(-amplitude_steps, amplitude_steps + 1, dtype=np.int16)
    coarse_steps = rng.choice(possible_steps, size=(coarse_num_x, coarse_num_y))

    coarse_x = np.linspace(0.0, size_x, coarse_num_x)
    coarse_y = np.linspace(0.0, size_y, coarse_num_y)
    linear_spline = interpolate.RectBivariateSpline(
        coarse_x, coarse_y, coarse_steps, kx=1, ky=1
    )
    fine_x = np.linspace(0.0, size_x, num_x)
    fine_y = np.linspace(0.0, size_y, num_y)
    fine_steps = np.rint(linear_spline(fine_x, fine_y)).astype(np.int16)
    return fine_steps.astype(np.float32) * VERTICAL_SCALE


@dataclass(frozen=True)
class BoxSegment:
    x_min: float
    x_max: float
    height: float


def box_step_height(difficulty: float) -> float:
    """Single Extreme-Parkour-style step increment: 15 cm -> 45 cm."""

    return 0.15 + 0.30 * float(difficulty)


def box_layout(difficulty: float, level: int, column: int) -> tuple[BoxSegment, ...]:
    """Six touching terraces: low, middle, high, high, middle, low."""

    step = box_step_height(difficulty)
    heights = (step, 2.0 * step, 3.0 * step, 3.0 * step, 2.0 * step, step)
    rng = _rng(level, column, salt=2)
    x_cursor = 2.5
    segments: list[BoxSegment] = []
    for height in heights:
        # Original parkour_step_terrain uses x_range=[0.2, 0.4] plus step_height.
        length = rng.uniform(0.20 + step, 0.40 + step)
        segments.append(BoxSegment(x_cursor, x_cursor + length, height))
        x_cursor += length  # deliberately no gap between adjacent terraces
    return tuple(segments)


@dataclass(frozen=True)
class Gap:
    x_min: float
    x_max: float
    landing_center: float


def gap_width(difficulty: float) -> float:
    return 0.40 + 0.60 * float(difficulty)


def gap_layout(difficulty: float) -> tuple[Gap, ...]:
    width = gap_width(difficulty)
    landing_length = 1.50 - 0.25 * float(difficulty)
    x_cursor = 2.50
    gaps: list[Gap] = []
    for _ in range(6):
        gap_start = x_cursor
        gap_end = gap_start + width
        landing_end = gap_end + landing_length
        gaps.append(Gap(gap_start, gap_end, 0.5 * (gap_end + landing_end)))
        x_cursor = landing_end
    return tuple(gaps)


def hurdle_height(difficulty: float) -> float:
    return 0.20 + 0.35 * float(difficulty)


def hurdle_length(difficulty: float) -> float:
    """Match the original hurdle depth progression, 10 cm -> 40 cm."""

    return 0.10 + 0.30 * float(difficulty)


def hurdle_centers() -> tuple[float, ...]:
    return (3.5, 5.7, 7.9, 10.1, 12.3, 14.5)


@dataclass(frozen=True)
class SlopedPad:
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    signed_incline: float


def parkour_pads_layout(
    difficulty: float, level: int, column: int
) -> tuple[tuple[SlopedPad, ...], float]:
    """Return five alternating stones, a long final ramp, and exit start.

    The dimensions follow the active ``parkour_terrain`` call in the reference
    repository: stones shorten with difficulty, lateral offsets alternate,
    normal-pad incline reaches 25 cm, and the final long ramp uses the original
    10--25 cm incline schedule.
    """

    rng = _rng(level, column, salt=3)
    center_y = TRACK_SIZE[1] * 0.5
    min_length = 0.90 - 0.30 * float(difficulty)
    max_length = 1.00 - 0.20 * float(difficulty)
    max_edge_gap = 0.10 + 0.30 * float(difficulty)
    normal_incline = 0.25 * float(difficulty)
    final_incline = 0.10 + 0.15 * float(difficulty)
    pad_width = 1.0

    x_cursor = 2.50
    left_right = int(rng.integers(0, 2))
    pads: list[SlopedPad] = []
    for _ in range(5):
        edge_gap = rng.uniform(-0.10, max_edge_gap)
        x_cursor += edge_gap
        length = rng.uniform(min_length, max_length)
        lateral_offset = rng.uniform(0.20, 0.30 + 0.10 * float(difficulty))
        direction = 1.0 if left_right else -1.0
        pad_center_y = center_y + direction * lateral_offset
        pads.append(
            SlopedPad(
                x_cursor,
                x_cursor + length,
                pad_center_y - 0.5 * pad_width,
                pad_center_y + 0.5 * pad_width,
                direction * normal_incline,
            )
        )
        x_cursor += length
        left_right = 1 - left_right

    x_cursor += rng.uniform(-0.10, max_edge_gap)
    direction = 1.0 if left_right else -1.0
    lateral_offset = rng.uniform(0.20, 0.30 + 0.10 * float(difficulty))
    pad_center_y = center_y + direction * lateral_offset
    final_length = 1.60
    pads.append(
        SlopedPad(
            x_cursor,
            x_cursor + final_length,
            pad_center_y - 0.5 * pad_width,
            pad_center_y + 0.5 * pad_width,
            direction * final_incline,
        )
    )
    exit_start = x_cursor + final_length + 0.05
    return tuple(pads), exit_start


def _grid() -> tuple[np.ndarray, np.ndarray]:
    num_x = round(TRACK_SIZE[0] / HORIZONTAL_SCALE) + 1
    num_y = round(TRACK_SIZE[1] / HORIZONTAL_SCALE) + 1
    return (
        np.arange(num_x, dtype=np.float32) * HORIZONTAL_SCALE,
        np.arange(num_y, dtype=np.float32) * HORIZONTAL_SCALE,
    )


def base_terrain_height_field(level: int, column: int) -> np.ndarray:
    """Build one v1 tile before the reference roughness pass is applied."""

    level = min(max(int(level), 0), NUM_LEVELS - 1)
    column = min(max(int(column), 0), NUM_COLUMNS - 1)
    difficulty = level_to_difficulty(level)
    terrain_type = terrain_type_from_column(column)
    x, y = _grid()

    field = np.zeros((x.size, y.size), dtype=np.float32)
    if terrain_type == "flat":
        pass
    elif terrain_type == "box":
        for segment in box_layout(difficulty, level, column):
            x_mask = (x >= segment.x_min) & (x < segment.x_max)
            field[x_mask, :] = segment.height
    elif terrain_type == "gap":
        for gap in gap_layout(difficulty):
            x_mask = (x >= gap.x_min) & (x < gap.x_max)
            field[x_mask, :] = PIT_DEPTH
    elif terrain_type == "hurdle":
        half_length = 0.5 * hurdle_length(difficulty)
        height = hurdle_height(difficulty)
        for center in hurdle_centers():
            x_mask = (x >= center - half_length) & (x < center + half_length)
            field[x_mask, :] = height
    elif terrain_type == "parkour_pads":
        field.fill(PIT_DEPTH)
        field[x <= 2.50, :] = 0.0
        pads, exit_start = parkour_pads_layout(difficulty, level, column)
        for pad in pads:
            x_ids = np.flatnonzero((x >= pad.x_min) & (x < pad.x_max))
            y_ids = np.flatnonzero((y >= pad.y_min) & (y < pad.y_max))
            if x_ids.size == 0 or y_ids.size == 0:
                continue
            normalized_y = (y[y_ids] - pad.y_min) / (pad.y_max - pad.y_min)
            top = pad.signed_incline * (2.0 * normalized_y - 1.0)
            field[np.ix_(x_ids, y_ids)] = top[None, :]
        field[x >= exit_start, :] = 0.0
    else:  # pragma: no cover - guarded by terrain_type_from_column
        raise RuntimeError(f"Unsupported terrain type: {terrain_type}")
    return field


def terrain_height_field(level: int, column: int) -> np.ndarray:
    """Build one tile with the original fixed-strength roughness pass.

    The reference calls ``add_roughness(terrain)`` after generating every
    active parkour type.  Its default ``difficulty=1`` samples the amplitude
    from 2--6 cm independently for every tile, rather than scaling it with the
    curriculum row.  Applying the same map to the complete base height field
    deliberately roughens obstacle tops, normal ground, and pit floors.
    """

    return base_terrain_height_field(level, column) + reference_roughness_height_field(level, column)


def track_goals(level: int, column: int) -> np.ndarray:
    """Return eight reset-origin-relative waypoints for one tile."""

    difficulty = level_to_difficulty(level)
    terrain_type = terrain_type_from_column(column)
    center_y = TRACK_SIZE[1] * 0.5
    if terrain_type == "flat":
        goals = [(2.5 + 2.0 * index, center_y, 0.0) for index in range(NUM_GOALS)]
    elif terrain_type == "box":
        segments = box_layout(difficulty, level, column)
        goals = [(2.0, center_y, 0.0)]
        goals.extend((0.5 * (item.x_min + item.x_max), center_y, item.height) for item in segments)
        goals.append((16.5, center_y, 0.0))
    elif terrain_type == "gap":
        goals = [(2.0, center_y, 0.0)]
        goals.extend((item.landing_center, center_y, 0.0) for item in gap_layout(difficulty))
        goals.append((16.5, center_y, 0.0))
    elif terrain_type == "hurdle":
        goals = [(2.0, center_y, 0.0)]
        goals.extend((center + 0.65, center_y, 0.0) for center in hurdle_centers())
        goals.append((16.5, center_y, 0.0))
    elif terrain_type == "parkour_pads":
        pads, _ = parkour_pads_layout(difficulty, level, column)
        goals = [(2.0, center_y, 0.0)]
        goals.extend(
            (0.5 * (pad.x_min + pad.x_max), 0.5 * (pad.y_min + pad.y_max), 0.0)
            for pad in pads
        )
        goals.append((16.5, center_y, 0.0))
    else:  # pragma: no cover
        raise RuntimeError(f"Unsupported terrain type: {terrain_type}")

    if len(goals) != NUM_GOALS:
        raise RuntimeError(f"{terrain_type} produced {len(goals)} goals; expected {NUM_GOALS}.")
    origin = np.asarray((1.0, center_y, 0.0), dtype=np.float32)
    return np.asarray(goals, dtype=np.float32) - origin


@lru_cache(maxsize=1)
def build_track_goal_table() -> np.ndarray:
    """Return the immutable ``(10, 40, 8, 3)`` v1 waypoint table."""

    table = np.stack(
        [
            np.stack([track_goals(level, column) for column in range(NUM_COLUMNS)])
            for level in range(NUM_LEVELS)
        ]
    )
    table.setflags(write=False)
    return table
