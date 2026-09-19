"""Geometric screens for mount fixtures; these are not hardware safety ratings."""
from __future__ import annotations

import math


def ground_support_summary(force_frames, weight_n, reporting_enabled):
    """Screen post-settle, ground-filtered vertical support; not a payload rating."""
    if not math.isfinite(weight_n) or weight_n <= 0 or not force_frames:
        raise ValueError("Positive finite weight and nonempty force frames required")
    body_count = len(force_frames[0])
    if body_count == 0 or any(len(frame) != body_count for frame in force_frames):
        raise ValueError("Consistent nonempty body coverage required")
    for frame in force_frames:
        if any(len(vector) != 3 or not all(math.isfinite(value) for value in vector) for vector in frame):
            raise ValueError("Finite three-dimensional ground force vectors required")
    vertical_forces = [sum(vector[2] for vector in frame) for frame in force_frames]
    support_fraction = sum(force >= 0.5 * weight_n for force in vertical_forces) / len(force_frames)
    mean_weight_ratio = sum(vertical_forces) / len(force_frames) / weight_n
    return {
        "source": "ground-filtered ContactSensor.force_matrix_w",
        "reporting_enabled": reporting_enabled,
        "sample_count": len(force_frames),
        "weight_N": weight_n,
        "mean_vertical_force_N": mean_weight_ratio * weight_n,
        "mean_vertical_force_weight_ratio": mean_weight_ratio,
        "min_vertical_force_N": min(vertical_forces),
        "max_vertical_force_N": max(vertical_forces),
        "max_single_body_force_norm_N": max(math.sqrt(sum(value ** 2 for value in vector))
                                             for frame in force_frames for vector in frame),
        "supported_sample_fraction": support_fraction,
        "contact_observed": reporting_enabled and max(vertical_forces) > 1.0,
        "persistent_support_pass": (reporting_enabled and support_fraction >= 0.99
                                    and 0.9 <= mean_weight_ratio <= 1.1),
        "fixture_thresholds": {"min_vertical_force_weight_ratio": 0.5,
                               "min_supported_sample_fraction": 0.99,
                               "mean_vertical_force_weight_ratio_range": [0.9, 1.1]},
        "scope": "stationary fixture diagnostic tolerances, not hardware safety margins",
    }


def box_separation(first_min, first_max, second_min, second_max):
    values = (*first_min, *first_max, *second_min, *second_max)
    if any(len(bound) != 3 for bound in (first_min, first_max, second_min, second_max)) or not all(math.isfinite(value) for value in values):
        raise ValueError("Expected finite three-dimensional bounds")
    if any(lower > upper for lower, upper in zip((*first_min, *second_min), (*first_max, *second_max))):
        raise ValueError("Reversed bounds")
    gaps = [max(first_min[axis] - second_max[axis], second_min[axis] - first_max[axis])
            for axis in range(3)]
    return math.sqrt(sum(max(0, gap) ** 2 for gap in gaps)) if max(gaps) > 0 else max(gaps)


def support_polygon_margin(center, supports):
    """Minimum signed edge margin of the XY support hull on level ground."""
    points = sorted(set(tuple(point[:2]) for point in supports))
    if len(center) < 2 or not all(math.isfinite(value) for value in center):
        raise ValueError("Invalid center of mass")
    if any(len(point) != 2 or not all(math.isfinite(value) for value in point) for point in points):
        raise ValueError("Invalid support positions")

    def cross(origin, first, second):
        return ((first[0] - origin[0]) * (second[1] - origin[1])
                - (first[1] - origin[1]) * (second[0] - origin[0]))

    lower, upper = [], []
    for point in points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    for point in reversed(points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    hull = lower[:-1] + upper[:-1]
    if len(hull) < 3:
        return None
    return min(cross(first, second, center) / math.dist(first, second)
               for first, second in zip(hull, hull[1:] + hull[:1]))
