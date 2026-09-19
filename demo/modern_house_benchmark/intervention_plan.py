"""Pure occupancy-derived planning for the Modern House author intervention.

The intervention between the two native probe snapshots is an author-side
diagnostic action: paired-support carriers reposition the deployed armchair,
then every ground robot withdraws from the resident corridor. This module owns
only the offline planning half. It picks furniture-parking and withdrawal cells
from the provider occupancy map, defines the report timing contract, and never
imports Isaac, USD, ROS, or Torch. A plan is a map-checked candidate, not a
business execution certificate; ``scoreable`` stays False everywhere.
"""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any, Mapping, Sequence


CORRIDOR_WIDTH_M = 0.9
WITHDRAWAL_CLEARANCE_MARGIN_M = 0.1
PARKING_MARGIN_M = 0.05
PARKING_SEARCH_RADIUS_M = 3.0
WITHDRAWAL_SEARCH_RADIUS_M = 8.0

INTERVENTION_EVENT_ORDER = (
    "before_probe_completed",
    "carriers_approach_completed",
    "attachment_prepared",
    "attachment_committed",
    "carry_started",
    "carry_completed",
    "attachment_detached",
    "withdrawal_started",
    "withdrawal_completed",
    "closeout_recorded",
)


def _point(value: Any) -> tuple[float, float]:
    if (not isinstance(value, (list, tuple)) or len(value) < 2
            or any(isinstance(item, bool) or not isinstance(item, (int, float))
                   or not math.isfinite(item) for item in value[:2])):
        raise ValueError("finite metric XY required")
    return float(value[0]), float(value[1])


def _half_extents(size_xy: Sequence[float]) -> tuple[float, float]:
    values = [_point([value, 0.0])[0] for value in size_xy[:2]]
    if any(value <= 0 for value in values):
        raise ValueError("positive finite footprint size required")
    return values[0] / 2, values[1] / 2


def circumscribed_half_extent(size_xy: Sequence[float]) -> float:
    """Conservative circumscribed half extent; matches the evaluator's rule."""
    half_x, half_y = _half_extents(size_xy)
    return math.hypot(half_x, half_y)


def _segment_distance(point: tuple[float, float], start: tuple[float, float],
                      end: tuple[float, float]) -> float:
    delta_x, delta_y = end[0] - start[0], end[1] - start[1]
    length_squared = delta_x * delta_x + delta_y * delta_y
    if length_squared == 0:
        return math.dist(point, start)
    projection = ((point[0] - start[0]) * delta_x + (point[1] - start[1]) * delta_y) / length_squared
    projection = max(0.0, min(1.0, projection))
    return math.dist(point, (start[0] + projection * delta_x, start[1] + projection * delta_y))


def corridor_distance(xy: Sequence[float], corridor_points: Sequence[Sequence[float]],
                      *, width_m: float = CORRIDOR_WIDTH_M) -> float:
    """Distance from a point to the swept corridor centerline, minus half width."""
    point = _point(xy)
    if not 2 <= len(corridor_points) <= 64:
        raise ValueError("bounded corridor centerline required")
    route = [_point(p) for p in corridor_points]
    if any(math.dist(a, b) <= 1e-6 for a, b in zip(route, route[1:])):
        raise ValueError("degenerate corridor point")
    return min(_segment_distance(point, a, b) for a, b in zip(route, route[1:])) - float(width_m) / 2


def _excluded(x: float, y: float, exclusions: Sequence[Mapping[str, Any]]) -> bool:
    for zone in exclusions:
        if math.hypot(x - zone["center_xy_m"][0], y - zone["center_xy_m"][1]) < zone["radius_m"]:
            return True
    return False


def _candidate_cells(occupancy: Any, preferred: tuple[float, float], search_radius_m: float):
    """Deterministic free-cell enumeration ordered by distance to the proposal."""
    resolution = float(occupancy.resolution)
    span = max(1, int(math.ceil(search_radius_m / resolution)))
    preferred_ix, preferred_iy = occupancy.local(*preferred)
    preferred_ix, preferred_iy = math.floor(preferred_ix / resolution), math.floor(preferred_iy / resolution)
    candidates = []
    for iy in range(preferred_iy - span, preferred_iy + span + 1):
        for ix in range(preferred_ix - span, preferred_ix + span + 1):
            if not 0 <= ix < occupancy.width or not 0 <= iy < occupancy.height:
                continue
            x, y = occupancy.world((ix + 0.5) * resolution, (iy + 0.5) * resolution)
            distance = math.hypot(x - preferred[0], y - preferred[1])
            if distance <= search_radius_m:
                candidates.append((round(distance, 9), round(y, 9), round(x, 9), x, y))
    candidates.sort()
    return candidates


def choose_free_cell(occupancy: Any, preferred_xy: Sequence[float], half_extent_m: float, *,
                     search_radius_m: float, exclusions: Sequence[Mapping[str, Any]] = (),
                     extra_admission=None) -> dict[str, Any]:
    """Pick the nearest occupancy-admitted cell for a circumscribed footprint.

    The authored preference is tried first; on conflict the nearest map-free
    cell wins, with the selection recorded so the report can show why the
    intervention did not park on occupied scene geometry.
    """
    preferred = _point(preferred_xy)
    if not math.isfinite(half_extent_m) or half_extent_m <= 0:
        raise ValueError("positive finite half extent required")

    def admitted(x: float, y: float) -> bool:
        if not occupancy.rectangle_free(x, y, half_extent_m, half_extent_m):
            return False
        if _excluded(x, y, exclusions):
            return False
        if extra_admission is not None and not extra_admission(x, y):
            return False
        return True

    preferred_admitted = admitted(*preferred)
    if preferred_admitted:
        return {
            "position_xy_m": [round(preferred[0], 4), round(preferred[1], 4)],
            "admitted": True,
            "preferred_admitted": True,
            "basis": "authored_preference_occupancy_admitted",
            "map_sha256": occupancy.sha256,
            "half_extent_m": half_extent_m,
            "search_radius_m": search_radius_m,
        }
    for distance, _, _, x, y in _candidate_cells(occupancy, preferred, search_radius_m):
        if not admitted(x, y):
            continue
        return {
            "position_xy_m": [round(x, 4), round(y, 4)],
            "admitted": True,
            "preferred_admitted": False,
            "preferred_xy_m": [round(preferred[0], 4), round(preferred[1], 4)],
            "displacement_m": round(math.hypot(x - preferred[0], y - preferred[1]), 4),
            "basis": "nearest_occupancy_free_cell_from_authored_preference",
            "map_sha256": occupancy.sha256,
            "half_extent_m": half_extent_m,
            "search_radius_m": search_radius_m,
        }
    return {
        "position_xy_m": None,
        "admitted": False,
        "preferred_admitted": False,
        "preferred_xy_m": [round(preferred[0], 4), round(preferred[1], 4)],
        "basis": "no_occupancy_admitted_cell_within_search_radius",
        "map_sha256": occupancy.sha256,
        "half_extent_m": half_extent_m,
        "search_radius_m": search_radius_m,
    }


def plan_furniture_parking(occupancy: Any, preferred_xy: Sequence[float],
                           object_size_xy: Sequence[float], *,
                           margin_m: float = PARKING_MARGIN_M,
                           search_radius_m: float = PARKING_SEARCH_RADIUS_M) -> dict[str, Any]:
    """Furniture parking target: authored preference, else nearest free cell."""
    half = circumscribed_half_extent(object_size_xy) + float(margin_m)
    result = choose_free_cell(occupancy, preferred_xy, half, search_radius_m=search_radius_m)
    return {**result, "kind": "furniture_parking",
            "footprint_size_xy_m": [float(object_size_xy[0]), float(object_size_xy[1])],
            "margin_m": float(margin_m)}


def plan_withdrawal_targets(occupancy: Any, corridor_points: Sequence[Sequence[float]],
                            robots: Sequence[Mapping[str, Any]], *, parking_xy=None,
                            width_m: float = CORRIDOR_WIDTH_M,
                            clearance_margin_m: float = WITHDRAWAL_CLEARANCE_MARGIN_M,
                            search_radius_m: float = WITHDRAWAL_SEARCH_RADIUS_M) -> dict[str, Any]:
    """Withdrawal cells for every ground robot, clear of the resident corridor.

    ``robots`` rows need ``public_id``, ``position_xy`` (current live estimate)
    and ``body_size_xy``. Targets must keep the circumscribed robot footprint
    map-free, stay at least the published corridor half width plus the robot
    circumscribed radius plus a margin away from the corridor centerline, and
    not overlap the parked furniture or an already assigned withdrawal cell.
    """
    if not robots:
        raise ValueError("withdrawal planning requires at least one ground robot")
    chosen: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    if parking_xy is not None:
        exclusions.append({"center_xy_m": list(_point(parking_xy)), "radius_m": 0.9})
    for robot in robots:
        identifier = robot["public_id"]
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("withdrawal robot requires a public id")
        position = _point(robot["position_xy"])
        half = circumscribed_half_extent(robot["body_size_xy"])
        required = float(width_m) / 2 + half + float(clearance_margin_m)

        def clear_of_corridor(x: float, y: float) -> bool:
            return corridor_distance((x, y), corridor_points, width_m=width_m) >= required

        target = choose_free_cell(occupancy, position, half, search_radius_m=search_radius_m,
                                  exclusions=exclusions, extra_admission=clear_of_corridor)
        row = {
            "public_id": identifier,
            "from_xy_m": [round(position[0], 4), round(position[1], 4)],
            "circumscribed_half_extent_m": round(half, 6),
            "required_corridor_clearance_m": round(required, 6),
            **{key: value for key, value in target.items() if key != "half_extent_m"},
        }
        if target["admitted"]:
            largest = max([row["circumscribed_half_extent_m"] for row in chosen] + [half])
            exclusions.append({"center_xy_m": target["position_xy_m"],
                               "radius_m": half + largest + 0.2})
        chosen.append(row)
    return {
        "kind": "ground_robot_withdrawal",
        "corridor_points_m": [[float(p[0]), float(p[1])] for p in (_point(point) for point in corridor_points)],
        "corridor_width_m": float(width_m),
        "clearance_margin_m": float(clearance_margin_m),
        "map_sha256": occupancy.sha256,
        "targets": chosen,
        "admitted": all(row["admitted"] for row in chosen),
    }


def new_intervention_record(plan: Mapping[str, Any], occupancy_sha256: str) -> dict[str, Any]:
    """Author-side intervention report skeleton with the mandatory timing slots."""
    if not isinstance(plan, Mapping):
        raise ValueError("intervention plan must be a mapping")
    if not isinstance(occupancy_sha256, str) or len(occupancy_sha256) != 64:
        raise ValueError("binding occupancy SHA-256 required")
    return {
        "scope": "author_side_diagnostic_intervention_not_business_execution",
        "scoreable": False,
        "business_outcomes_validated": False,
        "plan": deepcopy(dict(plan)),
        "event_order": list(INTERVENTION_EVENT_ORDER),
        "events": [{"event": name, "physics_step": None, "time_s": None}
                   for name in INTERVENTION_EVENT_ORDER],
        "occupancy_sha256": {
            "binding": occupancy_sha256,
            "before_probe": None,
            "after_closeout": None,
            "asserted_unchanged": None,
        },
    }


def record_intervention_event(record: Mapping[str, Any], name: str, *,
                              physics_step: int, time_s: float,
                              extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Fill the next open timing slot; each intervention event happens once."""
    if name not in INTERVENTION_EVENT_ORDER:
        raise ValueError(f"unknown intervention event: {name}")
    if type(physics_step) is not int or physics_step < 0:
        raise ValueError("intervention events require a nonnegative physics step")
    if not isinstance(time_s, (int, float)) or isinstance(time_s, bool) or not math.isfinite(time_s) or time_s < 0:
        raise ValueError("intervention events require a finite nonnegative time")
    for row in record["events"]:
        if row["event"] == name:
            if row["physics_step"] is not None:
                raise ValueError(f"intervention event already recorded: {name}")
            row.update(physics_step=physics_step, time_s=float(time_s))
            if extra:
                row.update(deepcopy(dict(extra)))
            return deepcopy(row)
    raise ValueError(f"intervention event slot missing: {name}")


def intervention_record_complete(record: Mapping[str, Any]) -> bool:
    """True only when every timing slot and occupancy assertion is filled."""
    events = {row["event"]: row for row in record.get("events", [])}
    if set(events) != set(INTERVENTION_EVENT_ORDER):
        return False
    if any(row["physics_step"] is None or row["time_s"] is None for row in events.values()):
        return False
    stamps = [(row["time_s"], row["physics_step"]) for row in
              (events[name] for name in INTERVENTION_EVENT_ORDER)]
    if any(later < earlier for earlier, later in zip(stamps, stamps[1:])):
        return False
    occupancy = record.get("occupancy_sha256", {})
    return (occupancy.get("before_probe") is not None
            and occupancy.get("after_closeout") is not None
            and occupancy.get("asserted_unchanged") is True)


__all__ = [
    "CORRIDOR_WIDTH_M",
    "INTERVENTION_EVENT_ORDER",
    "choose_free_cell",
    "corridor_distance",
    "circumscribed_half_extent",
    "intervention_record_complete",
    "new_intervention_record",
    "plan_furniture_parking",
    "plan_withdrawal_targets",
    "record_intervention_event",
]
