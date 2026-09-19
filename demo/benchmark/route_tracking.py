"""Conservative line-of-sight advancement within an already inflated map."""

import math


def segment_is_passable(grid, start, end):
    """Check all crossed grid cells, including both sides of boundary contact."""
    a = tuple(value / grid.resolution for value in grid._local(*start))
    b = tuple(value / grid.resolution for value in grid._local(*end))
    if not all(math.isfinite(value) for value in a + b):
        return False
    times = {0., 1.}
    for left, right in zip(a, b):
        if left == right:
            continue
        for edge in range(math.ceil(min(left, right)), math.floor(max(left, right)) + 1):
            times.add((edge - left) / (right - left))
    boundaries = sorted(times)
    samples = boundaries + [(left + right) / 2 for left, right in zip(boundaries, boundaries[1:])]
    for time in samples:
        cells = []
        for left, right in zip(a, b):
            value = left + time * (right - left)
            if abs(value - round(value)) < 1e-9:
                cells.append((round(value) - 1, round(value)))
            else:
                cells.append((math.floor(value),))
        if not all(grid.passable(x, y) for x in cells[0] for y in cells[1]):
            return False
    return True


def advance_visible_waypoints(route, *, position, grid, lookahead_m=.3):
    """Skip nearby intermediate points only when the next segment is free.

    A velocity policy need not stabilize at a 5 cm cell centre in an open
    corridor. This does not change terminal arrival, interaction tolerances or
    obstacle inflation. Obstructed corners must still be tracked explicitly.
    """
    if not math.isfinite(lookahead_m) or lookahead_m <= 0:
        raise ValueError("lookahead_m must be finite and positive")
    advanced = 0
    while (len(route) > 1 and math.dist(position, route[0]) <= lookahead_m
           and segment_is_passable(grid, position, route[1])):
        route.pop(0)
        advanced += 1
    return advanced
