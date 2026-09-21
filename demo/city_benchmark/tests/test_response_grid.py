"""Regressions for routes west/south of the world's origin."""
import pytest

from demo.city_benchmark.response import (
    _astar_leg, _grid_cell, _nearest_clear_point, _nearest_clear_in_goal_component,
    _segment_obstruction,
)


@pytest.mark.parametrize("cell", [(-111, 103), (-1, -1), (0, 0), (17, -27)])
def test_cell_center_round_trips(cell):
    center = tuple(index * .5 + .25 for index in cell)
    assert _grid_cell(center) == cell


def test_negative_obstacle_cannot_be_crossed_or_used_as_start():
    grid = {(-111, 103)}
    center = (-55.25, 51.75)
    assert _segment_obstruction([(-55.4, 51.75), (-55.1, 51.75)], grid=grid)
    assert _astar_leg(grid, center, (-54.25, 51.75)) is None
    moved = _nearest_clear_point(grid, center)
    assert moved != center
    assert _grid_cell(moved) not in grid


def test_negative_route_does_not_report_clear_while_crossing_obstacle():
    grid = {(-111, 103)}
    path = _astar_leg(grid, (-56.25, 51.75), (-54.25, 51.75))
    assert path
    assert not _segment_obstruction(path, grid=grid)
    assert path[0] == (-56.25, 51.75)
    assert path[-1] == (-54.25, 51.75)


def test_relocation_cannot_escape_its_radius_when_origin_is_enclosed():
    # Only the distant goal is free; the old DFS returned it despite the bound.
    grid = {(x, y) for x in range(-3, 3) for y in range(-3, 3)}
    assert _nearest_clear_in_goal_component(
        grid, (-.25, -.25), (5.25, 5.25), max_radius_m=.75) is None


def test_relocation_finds_nearby_connected_cell_instead_of_dfs_far_side():
    import math
    point = _nearest_clear_in_goal_component(
        {(-1, -1)}, (-.25, -.25), (30.25, 30.25), max_radius_m=1.)
    assert point is not None
    assert math.dist(point, (-.25, -.25)) <= 1.
    assert _astar_leg({(-1, -1)}, point, (30.25, 30.25))
