"""Pure control-state helpers shared by benchmark execution and regression tests."""

import math


SCAN_YAW_TOLERANCE_RAD = 0.15
WAIT_YAW_TOLERANCE_RAD = 0.05
WAYPOINT_PROGRESS_M = 0.01
WAYPOINT_HEADING_PROGRESS_RAD = 0.05


def update_waypoint_progress(state, *, waypoint, distance_m, heading_error_rad, step):
    if (not math.isfinite(distance_m) or distance_m < 0 or not math.isfinite(heading_error_rad)):
        raise ValueError("waypoint progress requires finite distance and heading measurements")
    identity = tuple(waypoint)
    heading_error = abs(heading_error_rad)
    if state.get("progress_waypoint") != identity:
        state.update(progress_waypoint=identity, best_distance_m=distance_m,
                     best_heading_error_rad=heading_error, step=step)
        return True
    distance_progress = distance_m <= state["best_distance_m"] - WAYPOINT_PROGRESS_M
    heading_progress = heading_error <= state["best_heading_error_rad"] - WAYPOINT_HEADING_PROGRESS_RAD
    if distance_progress:
        state["best_distance_m"] = distance_m
    if heading_progress:
        state["best_heading_error_rad"] = heading_error
    if distance_progress or heading_progress:
        state["step"] = step
    return distance_progress or heading_progress


def advance_view_alignment(entry, measured_yaw_rad, dt):
    if not math.isfinite(dt) or dt <= 0 or not math.isfinite(measured_yaw_rad):
        raise ValueError("view alignment requires finite yaw and positive timestep")
    error = (entry["wait_yaw_rad"] - measured_yaw_rad + math.pi) % (2 * math.pi) - math.pi
    within = abs(error) <= WAIT_YAW_TOLERANCE_RAD
    entry["alignment_elapsed_s"] = (entry.get("alignment_elapsed_s", 0.0) + dt
                                    if within and entry.get("alignment_previous_in_tolerance") else 0.0)
    entry["alignment_previous_in_tolerance"] = within
    entry["alignment_yaw_error_rad"] = abs(error)
    return entry["alignment_elapsed_s"] + 1e-9 >= 0.5, max(-1.0, min(1.0, 2.0 * error))


def scan_hover_goal(target, *, scan_height_m=None):
    if len(target) != 3 or not all(math.isfinite(value) for value in target):
        raise ValueError("scan target must be a finite world position with three coordinates")
    height = max(1.5, float(target[2]) + 1.0) if scan_height_m is None else scan_height_m
    if (isinstance(height, bool) or not isinstance(height, (int, float))
            or not math.isfinite(height) or not 0 < height <= 100 or height <= target[2]):
        raise ValueError("scan_height_m must be a finite world height above the target and within (0, 100]")
    return (float(target[0]), float(target[1]), float(height))


def advance_hover(entry, position, dt, *, tolerance_m=0.2, duration_s=0.5, measured_yaw_rad=None):
    if not math.isfinite(dt) or dt <= 0:
        raise ValueError("control timestep must be finite and positive")
    if tolerance_m <= 0 or duration_s <= 0:
        raise ValueError("hover tolerance and duration must be positive")
    goal = entry["scan_goal"]
    valid = len(position) == 3 and all(math.isfinite(value) for value in position)
    distance = math.dist(position, goal) if valid else math.inf
    in_tolerance = distance <= tolerance_m
    if entry.get("scan_yaw_rad") is not None:
        valid_yaw = (not isinstance(measured_yaw_rad, bool)
                     and isinstance(measured_yaw_rad, (int, float)) and math.isfinite(measured_yaw_rad))
        yaw_error = (abs((measured_yaw_rad - entry["scan_yaw_rad"] + math.pi) % (2 * math.pi) - math.pi)
                     if valid_yaw else math.inf)
        entry["hover_yaw_error_rad"] = yaw_error if valid_yaw else None
        in_tolerance = in_tolerance and yaw_error <= SCAN_YAW_TOLERANCE_RAD
    if in_tolerance and entry.get("hover_previous_in_tolerance", False):
        entry["hover_elapsed_s"] = entry.get("hover_elapsed_s", 0.0) + dt
    else:
        entry["hover_elapsed_s"] = 0.0
    entry["hover_previous_in_tolerance"] = in_tolerance
    entry["hover_distance_m"] = distance if valid else None
    return entry["hover_elapsed_s"] + 1e-9 >= duration_s
