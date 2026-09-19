"""Adversarial checks for continuous live resident-volume queries."""

from copy import deepcopy
from types import SimpleNamespace
import pytest

from demo.modern_house_benchmark.live_path import measure_resident_path, static_support_cover


CONTEXT = {"episode_id": "episode", "stage_identifier": "stage", "frame_id": "USD_world",
           "meters_per_unit": 1., "physics_step": 20,
           "readback_frozen": True, "kinematics_current": True, "scene_queries_enabled": True,
           "dynamic_query_pose_current": True}


class Query:
    obstacle = None
    missing_support = False

    def overlap_box(self, half, origin, rotation, callback, any_hit):
        return 0

    def sweep_box_all(self, half, origin, rotation, direction, distance, callback, both_sides):
        if self.obstacle:
            callback({"collision": self.obstacle, "rigid_body": self.obstacle,
                      "distance": distance/2, "protoIndex": 0xFFFFFFFF})
            return True
        return False

    def raycast_closest(self, origin, direction, distance):
        return {"hit": not self.missing_support, "collision": "/World/floor",
                "position": [origin[0], origin[1], .25], "normal": [0., 0., 1.]}


def measure(query, **kwargs):
    return measure_resident_path(query, points=[[0., 0., .25], [2., 0., .25]],
                                 context=CONTEXT, **kwargs)


def test_clear_path_retains_support_and_business_limits():
    result = measure(Query())
    assert result["status"] == "clear_volume_support_sampled"
    assert result["continuous_volume_clear"]
    assert len(result["segments"][0]["checks"]) == 6
    assert not result["continuous_support_validated"]
    assert not result["scoreable"]


@pytest.mark.parametrize("path", ["/World/idle_robot/chassis", "/World/chair", "/World/books"])
def test_middle_obstacle_not_missed_or_excluded(path):
    query = Query()
    query.obstacle = path
    result = measure(query)
    assert result["status"] == "blocked"
    assert not result["continuous_volume_clear"]
    assert result["excluded_collision_paths"] == []


def test_missing_floor_is_not_clearance_success():
    query = Query()
    query.missing_support = True
    result = measure(query)
    assert result["status"] == "blocked"
    assert "support_samples_missing_or_uneven" in result["blockers"]


def test_incomplete_callback_inventory_fails_closed():
    class Incomplete(Query):
        def sweep_box_all(self, *args):
            return True  # says hit, but supplies no evidence
    assert measure(Incomplete())["status"] == "blocked"


@pytest.mark.parametrize("options", [{"width_m": .8}, {"height_m": 1.9},
                                      {"width_m": float("nan")}, {"support_spacing_m": 1.}])
def test_cannot_weaken_public_envelope(options):
    assert measure(Query(), **options)["status"] == "unknown"


def test_unfrozen_episode_rejected():
    context = deepcopy(CONTEXT)
    context["readback_frozen"] = False
    assert measure_resident_path(Query(), points=[[0, 0, .25], [1, 0, .25]],
                                 context=context)["status"] == "unknown"


def test_native_iterable_hit_vectors_are_supported():
    class Vector:
        def __init__(self, values):
            self.values = values

        def __iter__(self):
            return iter(self.values)

    class NativeQuery(Query):
        def raycast_closest(self, *args):
            hit = super().raycast_closest(*args)
            for key in ("position", "normal"):
                hit[key] = Vector(hit[key])
            return hit

    assert measure(NativeQuery())["status"] == "clear_volume_support_sampled"


def test_sole_contact_gap_does_not_raise_required_ceiling():
    result = measure(Query())
    for check in result["segments"][0]["checks"]:
        assert check["center_xyz_m"][2]+check["half_extents_m"][2] <= 2.25
        assert check["center_xyz_m"][2]-check["half_extents_m"][2] >= .25


def test_headless_disabled_queries_cannot_mean_free_space():
    context = {**CONTEXT, "scene_queries_enabled": False}
    result = measure_resident_path(Query(), points=[[0, 0, .25], [1, 0, .25]], context=context)
    assert result["status"] == "unknown"
    assert result["segments"] == []


def test_instance_hits_block_resident_even_without_stable_instance_index():
    class InstanceQuery(Query):
        def sweep_box_all(self, half, origin, rotation, direction, distance, callback, both_sides):
            callback({"collision": "/World/instances", "rigid_body": "/World/instances",
                      "distance": distance/2, "protoIndex": 885186200})
            return True
    result = measure(InstanceQuery())
    assert result["status"] == "blocked"
    assert not result["continuous_volume_clear"]
    assert result["raw_obstacle_hit_metadata"][0]["protoIndex"] == 885186200


def test_native_callback_objects_need_not_be_mappings():
    class NativeQuery(Query):
        def overlap_box(self, half, origin, rotation, callback, any_hit):
            callback(SimpleNamespace(collision="/World/chair", rigid_body="/World/chair", protoIndex=0))
            return 1

        def sweep_box_all(self, half, origin, rotation, direction, distance, callback, both_sides):
            callback(SimpleNamespace(collision="/World/chair", rigid_body="/World/chair",
                                     protoIndex=0, distance=distance/2))
            return True

    result = measure(NativeQuery())
    assert result["status"] == "blocked"
    assert result["blockers"] == ["segment_0:resident_volume_obstructed"]


def test_stale_gpu_scene_query_never_certifies_dynamic_clearance():
    context = {**CONTEXT, "dynamic_query_pose_current": False}
    result = measure_resident_path(Query(), points=[[0, 0, .25], [1, 0, .25]], context=context)
    assert result["status"] == "unknown"
    assert not result["continuous_volume_clear"]
    assert "dynamic_scene_query_pose_not_synchronized" in result["blockers"]


@pytest.mark.parametrize("gap,expected", [(0., "covered"), (.001, "gap")])
def test_continuous_support_detects_gap_between_ray_samples(gap, expected):
    from pxr import Usd, UsdGeom, UsdPhysics
    stage = Usd.Stage.CreateInMemory()
    for path, lo, hi in (("/left", -1., 1.-gap), ("/right", 1.+gap, 3.)):
        cube = UsdGeom.Cube.Define(stage, path)
        cube.GetSizeAttr().Set(1.)
        cube.AddTranslateOp().Set(((lo+hi)/2, 0., .125))
        cube.AddScaleOp().Set((hi-lo, 2., .25))
        UsdPhysics.CollisionAPI.Apply(cube.GetPrim()).CreateCollisionEnabledAttr(True)
    measurement = {"context": CONTEXT, "points": [[0., 0., .25], [2., 0., .25]],
                   "width_m": .9, "support_samples": [
                       {"supported": True, "collision": "/left"},
                       {"supported": True, "collision": "/right"}]}
    result = static_support_cover(stage, measurement)
    assert result["status"] == expected


def test_only_verified_below_sole_static_support_can_be_filtered():
    query = Query()
    query.obstacle = "/World/floor"
    bounds = {"/World/floor": [[-2., -2., 0.], [4., 2., .25]]}
    result = measure(query, support_colliders=bounds)
    assert result["status"] == "clear_volume_support_sampled"
    assert all(h["verified_floor_contact"] for h in result["raw_obstacle_hit_metadata"])
    bounds["/World/floor"][1][2] = .5
    assert measure(query, support_colliders=bounds)["status"] == "unknown"
