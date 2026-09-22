"""Per-scene deployment primitives for the three indoor goal scenes.

This module is the occupancy-grid counterpart of the City spawn gate
(``demo.benchmark.goal_driven_tasks.validate_city_spawn_points``, which keeps
its semantic-map polygon/obstacle logic and is intentionally NOT re-implemented
here).  Warehouse/House/Factory have no semantic road polygon; their
deployment authority is the provider occupancy map, so this module reuses the
existing loader ``EAI.scenario_spec.geometry.provider_map``/``FactoryMap``
(ROS map_server YAML + PNG, ``free = (negate ? p : 1-p) < free_thresh``,
unknown cells blocked) instead of duplicating any YAML/PNG parsing.

Public pieces:
- ``load_scene_map(scene_key)`` loads the scene occupancy map through the
  shared loader (scene aliases: warehouse->warehouse_nvd, house->
  modern_house_nvd, factory->factory).
- ``validate_scene_spawn_points(scene_key, points_m, *, clearance_m=0.6)``
  admits points only when the containing cell is free under the loader's
  criterion and the conservative distance to the nearest non-free
  (occupied/unknown) cell is at least ``clearance_m``.  Like the City gate it
  raises ``ValueError`` with per-point reasons, so a pass is a full pass.
- ``spawn_anchor_rotation(anchors, instance_index)`` is the deterministic
  cross-instance rotation used to exchange frozen pool instances over one
  frozen anchor list.
- ``build_robot_spawn_positions(pool_rows, anchors, ...)`` emits the
  ``public_world.robot_spawn_positions`` mapping (finite 3-D coordinates per
  agent id, the format accepted by ``goal_driven_contract``).

``ROUTE_CORRIDORS`` freezes the declared ground-route keep-out bands (derived
from ``goal_driven_fixtures.scene_routes`` widths and the indoor overlay's
region centers) so spawn anchors can also be kept off key corridors.
Frozen anchor sets produced with this module live under
``tmp/scene_v2_anchors/`` (runtime data, not tracked).
"""

from __future__ import annotations

import math
from collections.abc import Sequence as _SequenceABC
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[2]
ANCHOR_FREEZE_DIR = REPO_ROOT / "tmp" / "scene_v2_anchors"

# Deployment aliases for the three indoor goal scenes.  The provider keys are
# also accepted; every other key (including "city") is rejected, because City
# spawn validation stays on its frozen semantic map.
SCENE_DEPLOYMENT_KEYS = {
    "warehouse": "warehouse_nvd",
    "house": "modern_house_nvd",
    "factory": "factory",
}
_ALIAS_TO_CANONICAL = {
    **{alias: alias for alias in SCENE_DEPLOYMENT_KEYS},
    **{provider: alias for alias, provider in SCENE_DEPLOYMENT_KEYS.items()},
}

DEFAULT_CLEARANCE_M = 0.6

# Search window around a point when measuring the distance to the nearest
# non-free cell.  Must stay >= clearance_m + half cell diagonal so "nothing
# found inside the window" still proves the clearance requirement.
_NONFREE_SEARCH_MARGIN_M = 2.0

# Route endpoints are region homes (standby/dock/work areas), not corridor
# blockage, so each declared route band is evaluated on its interior with this
# margin pulled in from both ends before an anchor is compared against it.
# A declared band whose whole length is at most twice this margin (dock lane,
# receiving hop, dock->standby_a, the short furniture hops) is a local
# junction between adjacent homes, not a transit corridor: it has no interior
# to keep clear and is skipped entirely by the corridor evaluation.
_ROUTE_END_MARGIN_M = 1.0
# Selection margin used when freezing anchors: an anchor must clear every
# route band interior by more than this, not merely by epsilon.
ROUTE_CORRIDOR_MIN_CLEARANCE_M = 0.2

# Reference spawn heights per robot type, frozen from the three scenes'
# existing native spawn value lists (goal_indoor_deployment._SPAWN_PROPOSALS
# and the tracked scenario JSONs).  Aerial types spawn above the anchor plane.
SPAWN_Z_BY_SCENE_AND_TYPE = {
    "warehouse": {"iris": 1.0, "m20": 0.52, "carter": 0.2},
    "house": {"iris": 1.6, "m20": 0.77, "carter": 0.45},
    "factory": {"iris": 1.0, "m20": 0.52, "carter": 0.2},
}
# Used when no scene table is selected; identical to the factory/warehouse
# shared values and documented as such, never invented per call.
DEFAULT_SPAWN_Z_BY_TYPE = SPAWN_Z_BY_SCENE_AND_TYPE["factory"]

# Frozen ground-route keep-out bands, derived from the public route
# declarations (goal_driven_fixtures.scene_routes: clearance_m[0] is the lane
# width) and the indoor overlay region centers.  Each entry is one straight
# band between two declared region centers with the declared half width;
# opposite-direction route ids over identical geometry are merged.  Aerial-only
# routes are excluded (they live above the ground spawn plane).  Regenerate by
# re-deriving from scene_routes + _REGION_PROPOSALS when either declaration
# changes; do not hand-tune values here.
ROUTE_CORRIDORS = {
    "factory": (
        {"id": "staging_to_lower|lower_to_staging",
         "a_xy_m": [-1.95, 3.4], "b_xy_m": [5.55, 3.4], "half_width_m": 0.7},
        {"id": "lower_to_stair|stair_to_lower",
         "a_xy_m": [5.55, 3.4], "b_xy_m": [1.5, -0.35], "half_width_m": 0.475},
        {"id": "staging_to_stair|stair_to_staging",
         "a_xy_m": [-1.95, 3.4], "b_xy_m": [1.5, -0.35], "half_width_m": 0.5},
        {"id": "main_stair_up|main_stair_down",
         "a_xy_m": [1.5, -0.35], "b_xy_m": [1.5, -7.5], "half_width_m": 0.425},
    ),
    "warehouse": (
        {"id": "rack_a_to_consolidation|consolidation_to_rack_a",
         "a_xy_m": [-10.6, -11.75], "b_xy_m": [-5.0, -11.75], "half_width_m": 0.925},
        {"id": "rack_a_shortcut",
         "a_xy_m": [-10.6, -11.75], "b_xy_m": [-5.0, -11.75], "half_width_m": 0.475},
        {"id": "rack_b_to_consolidation|consolidation_to_rack_b",
         "a_xy_m": [-5.0, -11.75], "b_xy_m": [-7.0, -10.05], "half_width_m": 0.475},
        {"id": "consolidation_to_dock|dock_to_consolidation",
         "a_xy_m": [-5.0, -11.75], "b_xy_m": [-5.0, -10.75], "half_width_m": 0.925},
        {"id": "dock_to_buffer",
         "a_xy_m": [-5.0, -10.75], "b_xy_m": [-4.0, -9.75], "half_width_m": 0.925},
        {"id": "dock_to_standby_a",
         "a_xy_m": [-5.0, -10.75], "b_xy_m": [-3.575, -11.75], "half_width_m": 0.475},
        {"id": "dock_to_standby_b",
         "a_xy_m": [-5.0, -10.75], "b_xy_m": [-2.5, -11.75], "half_width_m": 0.475},
    ),
    "house": (
        {"id": "entry_to_chair",
         "a_xy_m": [4.0, 3.5], "b_xy_m": [5.0, 13.5], "half_width_m": 0.5},
        {"id": "chair_to_parking_wide",
         "a_xy_m": [5.0, 13.5], "b_xy_m": [5.0, 15.0], "half_width_m": 1.05},
        {"id": "chair_to_parking_shortcut",
         "a_xy_m": [5.0, 13.5], "b_xy_m": [5.0, 15.0], "half_width_m": 0.5},
        {"id": "parking_to_living|living_to_parking",
         "a_xy_m": [5.0, 15.0], "b_xy_m": [4.5, 12.3], "half_width_m": 0.475},
        {"id": "entry_to_living",
         "a_xy_m": [4.0, 3.5], "b_xy_m": [4.5, 12.3], "half_width_m": 0.5},
        {"id": "living_to_staging|staging_to_living",
         "a_xy_m": [4.5, 12.3], "b_xy_m": [4.0, 13.0], "half_width_m": 0.425},
        {"id": "staging_to_chair",
         "a_xy_m": [4.0, 13.0], "b_xy_m": [5.0, 13.5], "half_width_m": 0.475},
        {"id": "staging_to_standby_a",
         "a_xy_m": [4.0, 13.0], "b_xy_m": [8.4, 6.0], "half_width_m": 0.45},
        {"id": "parking_to_standby_a",
         "a_xy_m": [5.0, 15.0], "b_xy_m": [8.4, 6.0], "half_width_m": 0.45},
        {"id": "parking_to_standby_b",
         "a_xy_m": [5.0, 15.0], "b_xy_m": [8.4, 10.0], "half_width_m": 0.45},
        {"id": "resident_entry_segment",
         "a_xy_m": [4.0, 3.5], "b_xy_m": [3.5, 9.5], "half_width_m": 0.5},
        {"id": "resident_living_segment",
         "a_xy_m": [3.5, 9.5], "b_xy_m": [4.5, 12.3], "half_width_m": 0.5},
    ),
}


def canonical_scene_key(scene_key: str) -> str:
    """Normalize a deployment alias or provider key to the canonical alias."""
    key = str(scene_key).strip()
    try:
        return _ALIAS_TO_CANONICAL[key]
    except KeyError:
        if key == "city":
            raise ValueError(
                "city spawn validation stays on the frozen semantic map; use "
                "demo.benchmark.goal_driven_tasks.validate_city_spawn_points"
            ) from None
        raise ValueError(
            f"unsupported scene deployment key {scene_key!r}; expected one of "
            f"{sorted(SCENE_DEPLOYMENT_KEYS)} (or their provider keys "
            f"{sorted(SCENE_DEPLOYMENT_KEYS.values())})"
        ) from None


def provider_scene_key(scene_key: str) -> str:
    """Return the provider/overlay scene key for a deployment alias."""
    return SCENE_DEPLOYMENT_KEYS[canonical_scene_key(scene_key)]


def load_scene_map(scene_key: str) -> Any:
    """Load the scene occupancy map through the shared existing loader.

    Delegates to ``EAI.scenario_spec.geometry.provider_map`` so YAML/PNG
    parsing, the free-cell criterion (unknown cells blocked), origin handling,
    and the map digest all keep exactly the loader's semantics.  Missing map
    files surface the loader's own error (``OSError``/``ScenarioError``).
    """
    from EAI.scenario_spec.geometry import provider_map

    return provider_map(scene_key=provider_scene_key(scene_key), ensure=False)


def _finite_xy(point: Any, label: str) -> tuple[float, float]:
    if isinstance(point, (str, bytes)) or not isinstance(point, _SequenceABC):
        raise ValueError(f"{label}: expected a finite 2D/3D point")
    if len(point) not in (2, 3):
        raise ValueError(f"{label}: expected a finite 2D/3D point")
    try:
        x, y = float(point[0]), float(point[1])
    except (TypeError, ValueError):
        raise ValueError(f"{label}: coordinates must be finite numbers") from None
    if not (math.isfinite(x) and math.isfinite(y)):
        raise ValueError(f"{label}: coordinates must be finite numbers")
    return x, y


def _point_cell(occupancy: Any, x: float, y: float) -> tuple[int, int] | None:
    """Map cell (col, row) containing the world point, or None off-grid."""
    lx, ly = occupancy.local(x, y)
    col, row = math.floor(lx / occupancy.resolution), math.floor(ly / occupancy.resolution)
    if 0 <= col < occupancy.width and 0 <= row < occupancy.height:
        return col, row
    return None


def _nearest_nonfree_evidence(occupancy: Any, x: float, y: float,
                              search_radius_m: float) -> dict[str, Any]:
    """Distance from the point to the nearest non-free cell center in-window.

    The reported ``free_distance_m`` subtracts the half cell diagonal so it is
    a conservative lower bound of the true distance to occupied/unknown cell
    area, never an overestimate.  When no non-free cell exists inside the
    window, ``nearest_nonfree_distance_m`` is None and the bound is the window
    radius, which by construction already satisfies any clearance the caller
    asked for.
    """
    import numpy as np

    resolution = float(occupancy.resolution)
    half_diagonal = resolution * math.sqrt(2.0) / 2.0
    lx, ly = occupancy.local(x, y)
    span = int(math.ceil(search_radius_m / resolution))
    center_col, center_row = (math.floor(lx / resolution), math.floor(ly / resolution))
    col0, col1 = max(0, center_col - span), min(occupancy.width, center_col + span + 1)
    row0, row1 = max(0, center_row - span), min(occupancy.height, center_row + span + 1)
    if col0 >= col1 or row0 >= row1:
        return {"nearest_nonfree_distance_m": None,
                "nearest_nonfree_cell": None,
                "free_distance_m": round(search_radius_m - half_diagonal, 4),
                "nearest_nonfree_within_search_radius": False}
    window = ~np.asarray(occupancy.free[occupancy.height - row1:occupancy.height - row0,
                                        col0:col1], dtype=bool)
    xs = (np.arange(col0, col1) + 0.5) * resolution - lx
    ys = (np.arange(row0, row1) + 0.5) * resolution - ly
    distances = np.hypot(xs[None, :], ys[:, None])
    distances[~window] = math.inf
    if not np.isfinite(distances).any():
        return {"nearest_nonfree_distance_m": None,
                "nearest_nonfree_cell": None,
                "free_distance_m": round(search_radius_m - half_diagonal, 4),
                "nearest_nonfree_within_search_radius": False}
    flat = int(np.argmin(distances))
    cell_row, cell_col = np.unravel_index(flat, distances.shape)
    nearest = float(distances[cell_row, cell_col])
    return {
        "nearest_nonfree_distance_m": round(nearest, 4),
        "nearest_nonfree_cell": [int(col0 + int(cell_col)), int(row0 + int(cell_row))],
        "free_distance_m": round(max(0.0, nearest - half_diagonal), 4),
        "nearest_nonfree_within_search_radius": True,
    }


def validate_scene_spawn_points(scene_key: Any, points: Any, *,
                                clearance_m: float = DEFAULT_CLEARANCE_M,
                                scene_map: Any = None) -> dict[str, Any]:
    """Admit spawn anchors against the scene occupancy grid.

    Every point must (1) fall in a free cell under the shared loader's
    criterion (occupancy probability below ``free_thresh``; unknown/off-grid
    cells are blocked) and (2) keep a conservative distance of at least
    ``clearance_m`` from the nearest occupied/unknown cell area.  Mirrors the
    City gate contract: any rejected point raises ``ValueError`` with
    per-point reasons, and the success payload carries per-point evidence
    (cell, nearest non-free cell, free distance) plus the map digest and
    resolution so a pass is auditable against the exact map artifact.
    """
    canonical = canonical_scene_key(str(scene_key))
    if (isinstance(clearance_m, bool) or not isinstance(clearance_m, (int, float))
            or not math.isfinite(clearance_m) or clearance_m <= 0):
        raise ValueError("clearance_m must be a positive finite number")
    clearance = float(clearance_m)
    if scene_map is None:
        scene_map = load_scene_map(canonical)
    occupancy = scene_map
    resolution = float(occupancy.resolution)
    search_radius = clearance + _NONFREE_SEARCH_MARGIN_M

    labelled = ([(str(key), points[key]) for key in points] if isinstance(points, Mapping)
                else [(f"point[{index}]", point) for index, point in enumerate(points)])
    if not labelled:
        raise ValueError("spawn point validation requires at least one point")

    failures: list[str] = []
    accepted: list[str] = []
    evidence: list[dict[str, Any]] = []
    for label, point in labelled:
        try:
            x, y = _finite_xy(point, label)
        except ValueError as exc:
            failures.append(str(exc))
            continue
        cell = _point_cell(occupancy, x, y)
        if cell is None:
            failures.append(f"{label} ({x:g}, {y:g}): outside the occupancy map grid")
            continue
        col, row = cell
        if not bool(occupancy.free[occupancy.height - 1 - row, col]):
            failures.append(f"{label} ({x:g}, {y:g}): inside an occupied/unknown cell"
                            f" (cell col={col}, row={row})")
            continue
        nearest = _nearest_nonfree_evidence(occupancy, x, y, search_radius)
        free_distance = (nearest["free_distance_m"]
                         if nearest["nearest_nonfree_within_search_radius"]
                         else search_radius - resolution * math.sqrt(2.0) / 2.0)
        if free_distance + 1e-9 < clearance:
            failures.append(
                f"{label} ({x:g}, {y:g}): free distance {free_distance:.3f} m is below"
                f" the required {clearance:g} m clearance to occupied/unknown cells")
            continue
        accepted.append(label)
        evidence.append({"label": label, "xy_m": [round(x, 4), round(y, 4)],
                         "cell": [col, row], "in_free_cell": True, **nearest})
    if failures:
        raise ValueError("scene spawn point validation failed: " + "; ".join(failures))
    return {
        "validated": True,
        "scene_key": canonical,
        "provider_scene_key": provider_scene_key(canonical),
        "map_sha256": occupancy.sha256,
        "resolution_m": resolution,
        "clearance_m": clearance,
        "point_count": len(accepted),
        "accepted": accepted,
        "points": evidence,
    }


def _anchor_xy(anchor: Any, label: str) -> tuple[float, float]:
    if isinstance(anchor, Mapping):
        if "xy_m" not in anchor:
            raise ValueError(f"{label}: anchor mapping lacks the 'xy_m' entry")
        return _finite_xy(anchor["xy_m"], label)
    return _finite_xy(anchor, label)


def spawn_anchor_rotation(anchors: Any, instance_index: Any) -> tuple[float, float]:
    """Deterministically map one frozen instance index onto the anchor list.

    The anchor list rotates by the instance index: instance ``k`` of a frozen
    roster takes ``anchors[k % len(anchors)]`` (equivalently, the first
    element of the list rotated by ``k``).  Identical rosters therefore
    exchange positions across frozen runs by pure rotation, never by
    re-randomization.  Injectivity is the caller's contract: a pool larger
    than the anchor list would wrap (``build_robot_spawn_positions`` refuses
    that case instead).
    """
    if isinstance(anchors, (str, bytes)) or not isinstance(anchors, _SequenceABC):
        raise ValueError("anchors must be a non-empty sequence of 2D/3D points")
    anchors = list(anchors)
    if not anchors:
        raise ValueError("spawn anchor rotation requires at least one anchor")
    if isinstance(instance_index, bool) or not isinstance(instance_index, int):
        raise ValueError("instance_index must be a non-negative int")
    if instance_index < 0:
        raise ValueError("instance_index must be a non-negative int")
    return _anchor_xy(anchors[instance_index % len(anchors)],
                      f"anchors[{instance_index % len(anchors)}]")


def build_robot_spawn_positions(pool_rows: Any, anchors: Any, *,
                                spawn_z_by_type: Mapping[str, float] | None = None,
                                scene_key: str | None = None) -> dict[str, list[float]]:
    """Build the ``public_world.robot_spawn_positions`` payload.

    Pool rows keep their frozen order: the row at position ``i`` is instance
    ``i`` and receives ``spawn_anchor_rotation(anchors, i)``, so the whole
    frozen roster maps injectively onto the anchor list (the pool may not
    exceed the anchor list).  Z comes from the frozen per-type reference
    heights: ``spawn_z_by_type`` when supplied, else the per-scene table for
    ``scene_key``, else the documented factory/warehouse default table.  The
    result is a plain ``{agent_id: [x, y, z]}`` mapping of finite floats, the
    exact shape ``goal_driven_contract`` accepts for ``robot_spawn_positions``.
    """
    if isinstance(pool_rows, (str, bytes)) or not isinstance(pool_rows, _SequenceABC):
        raise ValueError("pool_rows must be a sequence of pool robot rows")
    rows = list(pool_rows)
    if isinstance(anchors, (str, bytes)) or not isinstance(anchors, _SequenceABC):
        raise ValueError("anchors must be a non-empty sequence of anchors")
    anchor_list = list(anchors)
    if not anchor_list:
        raise ValueError("spawn anchor list must not be empty")
    if len(rows) > len(anchor_list):
        raise ValueError(
            f"frozen pool of {len(rows)} robots exceeds the {len(anchor_list)} anchors;"
            " extend the validated anchor list before building spawn positions")

    if spawn_z_by_type is not None:
        z_table = {str(key): float(value) for key, value in spawn_z_by_type.items()}
    elif scene_key is not None:
        canonical = canonical_scene_key(scene_key)
        z_table = dict(SPAWN_Z_BY_SCENE_AND_TYPE[canonical])
    else:
        z_table = dict(DEFAULT_SPAWN_Z_BY_TYPE)

    positions: dict[str, list[float]] = {}
    for instance_index, row in enumerate(rows):
        if not isinstance(row, Mapping) or "id" not in row or "type" not in row:
            raise ValueError(f"pool row {instance_index} must be a mapping with 'id' and 'type'")
        agent_id = row["id"]
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError(f"pool row {instance_index} has an invalid agent id")
        if agent_id in positions:
            raise ValueError(f"duplicate pool agent id: {agent_id}")
        robot_type = row["type"]
        if robot_type not in z_table:
            raise ValueError(
                f"pool robot {agent_id} has type {robot_type!r} without a reference spawn"
                f" z; known types: {sorted(z_table)}")
        x, y = spawn_anchor_rotation(anchor_list, instance_index)
        positions[agent_id] = [round(x, 4), round(y, 4), float(z_table[robot_type])]
    if not positions:
        raise ValueError("pool_rows must contain at least one robot")
    return positions


def _shrunk_segment_distance(point: tuple[float, float], a: Any, b: Any,
                             margin: float) -> float:
    """Distance from point to the segment interior with both ends pulled in.

    Precondition: the segment is longer than ``2 * margin`` (shorter bands are
    local junction hops and are skipped by ``route_corridor_clearance``).
    """
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    dx, dy = bx - ax, by - ay
    length = math.hypot(dx, dy)
    sx, sy = dx / length, dy / length
    # Clamp the projection onto the shrunk interior [margin, length - margin].
    offset = ((point[0] - ax) * sx + (point[1] - ay) * sy)
    offset = max(margin, min(length - margin, offset))
    return math.hypot(point[0] - (ax + offset * sx), point[1] - (ay + offset * sy))


def route_corridor_clearance(scene_key: str, point: Any) -> dict[str, Any]:
    """Minimum clearance from a point to every declared ground-route band.

    The reported clearance is ``distance to the band interior (ends pulled in
    by ``ROUTE_END_MARGIN_M``) minus the band half width``; a positive value
    means the point is outside every declared transit corridor.  Endpoint
    homes (standby/dock/work regions) stay usable because the interior
    shrinks away from both ends, and bands no longer than twice the margin
    are skipped as local junction hops with no interior at all.
    """
    canonical = canonical_scene_key(scene_key)
    x, y = _finite_xy(point, "point")
    worst: tuple[float, str] | None = None
    for corridor in ROUTE_CORRIDORS[canonical]:
        ax, ay = float(corridor["a_xy_m"][0]), float(corridor["a_xy_m"][1])
        bx, by = float(corridor["b_xy_m"][0]), float(corridor["b_xy_m"][1])
        length = math.hypot(bx - ax, by - ay)
        if length <= 2.0 * _ROUTE_END_MARGIN_M:
            continue  # local junction hop; no transit interior to keep clear
        distance = _shrunk_segment_distance((x, y), corridor["a_xy_m"], corridor["b_xy_m"],
                                            _ROUTE_END_MARGIN_M)
        clearance = distance - float(corridor["half_width_m"])
        if worst is None or clearance < worst[0]:
            worst = (clearance, str(corridor["id"]))
    if worst is None:
        raise ValueError(f"scene {canonical!r} declares no route corridors")
    return {"scene_key": canonical, "min_corridor_clearance_m": round(worst[0], 4),
            "nearest_corridor_id": worst[1],
            "outside_all_corridors": worst[0] > 0.0}


def frozen_anchor_path(scene_key: str) -> Path:
    """Path of the frozen anchor artifact for a scene under tmp/."""
    return ANCHOR_FREEZE_DIR / f"{canonical_scene_key(scene_key)}.json"


def load_frozen_anchors(scene_key: str) -> list[dict[str, Any]]:
    """Load the frozen anchor rows (with validation evidence) for a scene."""
    import json

    path = frozen_anchor_path(scene_key)
    if not path.is_file():
        raise FileNotFoundError(f"frozen anchor artifact is missing: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    anchors = payload.get("anchors")
    if not isinstance(anchors, list) or not anchors:
        raise ValueError(f"frozen anchor artifact has no anchors: {path}")
    return anchors


__all__ = [
    "ANCHOR_FREEZE_DIR",
    "DEFAULT_CLEARANCE_M",
    "DEFAULT_SPAWN_Z_BY_TYPE",
    "ROUTE_CORRIDORS",
    "ROUTE_CORRIDOR_MIN_CLEARANCE_M",
    "SCENE_DEPLOYMENT_KEYS",
    "SPAWN_Z_BY_SCENE_AND_TYPE",
    "build_robot_spawn_positions",
    "canonical_scene_key",
    "frozen_anchor_path",
    "load_frozen_anchors",
    "load_scene_map",
    "provider_scene_key",
    "route_corridor_clearance",
    "spawn_anchor_rotation",
    "validate_scene_spawn_points",
]
