"""Continuous dynamic occupancy follows the polyline, including its footprint."""
import pytest

from demo.city_benchmark.scene.usd_scene import CityNativeRegionReader


class EmptyStaticQuery:
    def overlap_box(self, *args):
        return 0

    def sweep_box_all(self, *args):
        return False


def read(points, bounds, half=(.3, .3, .5)):
    reader = CityNativeRegionReader(
        query=EmptyStaticQuery(), regions={}, envelope_reader=lambda: {"car": bounds},
        scene_queries_enabled=True, device="cpu")
    return reader.read_route("route", points, half_extents_m=half,
                             episode_id="episode", time_s=0.)


def test_u_route_does_not_hit_a_car_only_inside_its_overall_bounds():
    result = read([(0, 0, 1), (0, 10, 1), (10, 10, 1), (10, 0, 1)],
                  {"min": [4, 4, 0], "max": [6, 6, 2]})
    assert result["clear"]
    assert not result["dynamic_envelopes"]


@pytest.mark.parametrize("points", [
    [(0, 5, 1), (10, 5, 1)],  # crossing between sparse endpoints
    [(0, 0, 1), (10, 10, 1)],  # diagonal
    [(3.7, 5, 1), (3.7, 5, 1)],  # stationary body touching the obstacle
    [(10, 5, 1), (0, 5, 1)],  # reverse direction
])
def test_segment_sweep_includes_crossings_and_body_contact(points):
    result = read(points, {"min": [4, 4, 0], "max": [6, 6, 2]})
    assert not result["clear"]
    assert "car" in result["dynamic_envelopes"]


def test_diagonal_segment_aabb_does_not_replace_actual_sweep():
    result = read([(0, 0, 1), (10, 10, 1)],
                  {"min": [1, 8, 0], "max": [2, 9, 2]})
    assert result["clear"]


def test_vertical_separation_still_counts():
    result = read([(0, 5, 4), (10, 5, 4)],
                  {"min": [4, 4, 0], "max": [6, 6, 2]})
    assert result["clear"]
