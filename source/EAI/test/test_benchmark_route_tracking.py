import numpy as np

from demo.benchmark.route_tracking import advance_visible_waypoints, segment_is_passable
from EAI.scenario_spec.oracle import InflatedGrid


class Map:
    resolution = .1
    width = height = 30
    free = np.ones((height, width), dtype=bool)

    @staticmethod
    def local(x, y):
        return x, y


def test_open_corridor_skips_cell_centre_but_preserves_terminal():
    grid = InflatedGrid(Map(), .1)
    route = [(1.05, 1.05), (2.05, 1.05)]
    assert advance_visible_waypoints(route, position=(1.18, 1.06), grid=grid) == 1
    assert route == [(2.05, 1.05)]
    assert advance_visible_waypoints(route, position=(2.04, 1.05), grid=grid) == 0


def test_no_corner_cutting_even_for_thin_obstacle_between_endpoints():
    free = Map.free.copy()
    free[30 - 1 - 11, 11] = False
    grid = InflatedGrid(Map(), .01, free)
    route = [(1.05, 1.35), (1.45, 1.35)]
    assert not segment_is_passable(grid, (1.05, 1.05), (1.45, 1.35))
    assert advance_visible_waypoints(route, position=(1.05, 1.06), grid=grid) == 0
    assert not segment_is_passable(grid, (1.05, 1.05), (1.25, 1.25))


def test_boundary_contact_checks_both_adjacent_cells_and_map_edges():
    free = Map.free.copy()
    free[30 - 1 - 10, 11] = False
    grid = InflatedGrid(Map(), .01, free)
    assert not segment_is_passable(grid, (1.05, 1.1), (1.35, 1.1))
    assert not segment_is_passable(grid, (-.01, 1.), (.2, 1.))
