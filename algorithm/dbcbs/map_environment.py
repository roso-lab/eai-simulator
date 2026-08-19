"""Convert EAI occupancy maps into the box environment used by db-CBS."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path

import numpy as np
import yaml

from algorithm.global_planner.core import FactoryMapPlanner


@dataclass(frozen=True)
class Box:
    center_x: float
    center_y: float
    size_x: float
    size_y: float

    def as_dict(self) -> dict[str, object]:
        return {
            "type": "box",
            "center": [self.center_x, self.center_y],
            "size": [self.size_x, self.size_y],
        }


@dataclass(frozen=True)
class Environment:
    min: tuple[float, float]
    max: tuple[float, float]
    boxes: tuple[Box, ...]


@dataclass
class PlanningFrame:
    environment: Environment
    scale: float
    occupied: np.ndarray
    resolution: float
    origin: tuple[float, float]
    interior: np.ndarray

    def snap(
        self,
        x: float,
        y: float,
        *,
        max_distance: float,
        prefer_interior: bool,
    ) -> tuple[float, float] | None:
        masks = (self.interior, ~self.occupied) if prefer_interior else (~self.occupied,)
        col = int((x - self.origin[0]) / self.resolution)
        row = int((y - self.origin[1]) / self.resolution)
        max_cells = max(0, int(math.ceil(max_distance / self.resolution)))
        best: tuple[float, float, float] | None = None
        for free in masks:
            for radius in range(max_cells + 1):
                row_min, row_max = row - radius, row + radius
                col_min, col_max = col - radius, col + radius
                candidates = []
                for candidate_col in range(col_min, col_max + 1):
                    candidates.extend(((row_min, candidate_col), (row_max, candidate_col)))
                for candidate_row in range(row_min + 1, row_max):
                    candidates.extend(((candidate_row, col_min), (candidate_row, col_max)))
                for candidate_row, candidate_col in candidates:
                    if not (
                        0 <= candidate_row < free.shape[0]
                        and 0 <= candidate_col < free.shape[1]
                        and free[candidate_row, candidate_col]
                    ):
                        continue
                    px = self.origin[0] + (candidate_col + 0.5) * self.resolution
                    py = self.origin[1] + (candidate_row + 0.5) * self.resolution
                    distance = math.hypot(px - x, py - y)
                    if distance <= max_distance and (best is None or distance < best[0]):
                        best = (distance, px, py)
                if best is not None:
                    return best[1], best[2]
        return None


def _coarsen(occupied: np.ndarray, factor: int) -> tuple[np.ndarray, int]:
    factor = max(1, int(factor))
    if factor == 1:
        return occupied, factor
    height, width = occupied.shape
    coarse_height = math.ceil(height / factor)
    coarse_width = math.ceil(width / factor)
    padded = np.ones((coarse_height * factor, coarse_width * factor), dtype=bool)
    padded[:height, :width] = occupied
    return (
        padded.reshape(coarse_height, factor, coarse_width, factor).any(axis=(1, 3)),
        factor,
    )


def _rectangles(occupied: np.ndarray) -> list[tuple[int, int, int, int]]:
    height, width = occupied.shape
    open_rectangles: dict[tuple[int, int], int] = {}
    closed: list[tuple[int, int, int, int]] = []
    for row_index in range(height):
        spans: list[tuple[int, int]] = []
        start: int | None = None
        for column in range(width):
            if occupied[row_index, column] and start is None:
                start = column
            elif not occupied[row_index, column] and start is not None:
                spans.append((start, column - 1))
                start = None
        if start is not None:
            spans.append((start, width - 1))
        current = {span: open_rectangles.get(span, row_index) for span in spans}
        for span, first_row in open_rectangles.items():
            if span not in current:
                closed.append((first_row, span[0], row_index - 1, span[1]))
        open_rectangles = current
    closed.extend(
        (first_row, span[0], height - 1, span[1])
        for span, first_row in open_rectangles.items()
    )
    return closed


def _erode_free(free: np.ndarray, cells: int) -> np.ndarray:
    result = free.copy()
    for _ in range(max(0, cells)):
        previous = result.copy()
        result[1:, :] &= previous[:-1, :]
        result[:-1, :] &= previous[1:, :]
        result[:, 1:] &= previous[:, :-1]
        result[:, :-1] &= previous[:, 1:]
    return result


def build_planning_frame(
    map_yaml: str | Path,
    *,
    scale: float = 4.0,
    robot_radius: float = 0.60,
    safety_margin: float = 0.10,
    coarsen_factor: int = 4,
    max_boxes: int = 300,
    interior_clearance: float = 0.85,
) -> PlanningFrame:
    if scale <= 0.0:
        raise ValueError("db-CBS planning scale must be positive")
    if robot_radius <= 0.0 or safety_margin < 0.0:
        raise ValueError("db-CBS footprint parameters must be non-negative")
    path = Path(map_yaml).expanduser().resolve()
    metadata = yaml.safe_load(path.read_text(encoding="utf-8"))
    origin = metadata.get("origin", (0.0, 0.0, 0.0))
    if len(origin) > 2 and not math.isclose(float(origin[2]), 0.0, abs_tol=1e-9):
        raise ValueError("EAI db-CBS maps currently require a zero-yaw map origin")
    resolution = float(metadata["resolution"])
    # Native FCL checks each explicit robot radius against these boxes. Inflating
    # the occupancy grid by that radius here would count the footprint twice.
    inflation_cells = 0
    planner = FactoryMapPlanner(
        str(path), prefer_astar=True, inflation_radius_cells=inflation_cells
    )
    occupied, factor = _coarsen(planner.occ_grid.astype(bool), coarsen_factor)
    plan_resolution = resolution * factor / scale
    plan_origin = (float(origin[0]) / scale, float(origin[1]) / scale)
    boxes = []
    for row0, col0, row1, col1 in _rectangles(occupied):
        width = (col1 - col0 + 1) * plan_resolution
        height = (row1 - row0 + 1) * plan_resolution
        boxes.append(
            Box(
                round(plan_origin[0] + (col0 + (col1 - col0 + 1) / 2) * plan_resolution, 4),
                round(plan_origin[1] + (row0 + (row1 - row0 + 1) / 2) * plan_resolution, 4),
                round(width, 4),
                round(height, 4),
            )
        )
    if len(boxes) > max_boxes:
        raise ValueError(
            f"Map conversion produced {len(boxes)} boxes; maximum is {max_boxes}"
        )
    plan_height, plan_width = occupied.shape
    free = ~occupied
    clearance_cells = math.floor((interior_clearance / scale) / plan_resolution)
    return PlanningFrame(
        environment=Environment(
            min=plan_origin,
            max=(
                plan_origin[0] + plan_width * plan_resolution,
                plan_origin[1] + plan_height * plan_resolution,
            ),
            boxes=tuple(boxes),
        ),
        scale=float(scale),
        occupied=occupied,
        resolution=plan_resolution,
        origin=plan_origin,
        interior=_erode_free(free, clearance_cells),
    )


__all__ = ["Box", "Environment", "PlanningFrame", "build_planning_frame"]
