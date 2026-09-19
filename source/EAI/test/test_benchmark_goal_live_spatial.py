"""Synthetic USD classification plus injectable rays; no Isaac/PhysX launch."""

from copy import deepcopy
import json

import pytest
from pxr import Usd, UsdGeom, UsdPhysics

from demo.benchmark import goal_live_spatial as spatial


@pytest.fixture
def stage():
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, "Z")
    stage.DefinePrim("/World/Robot", "Xform")
    for name in ("Floor", "Obstacle"):
        prim = stage.DefinePrim("/World/" + name, "Cube")
        UsdPhysics.CollisionAPI.Apply(prim)
    return stage


@pytest.fixture
def deployment():
    ground = {"mobility": "ground", "physical_profile": {"body_size_m": [.8, .5, .8]}}
    regions = [{"id": name, "public_region": {"access_modes": ["ground_navigation"]},
                "candidate_geometry": {"center_m": [x, 0., z],
                                       "aabb_xy_m": [x-.5, -.5, x+.5, .5]}}
               for name, x, z in (("entry", 0., 0.), ("landing", 2., 1.))]
    return {"scene_key": "factory", "public_task": {"public_world": {}},
            "region_bindings": regions,
            "route_bindings": [{"id": "walk", "public_route": {
                "access_modes": ["ground_navigation"], "clearance_m": [1., 1.5]},
                "planning_candidate": {"polyline_m": [[0., 0., 0.], [2., 0., 0.]]}}],
            "robot_instances": [{"public_id": "agent", "prim_path": "/World/Robot",
                "physical_profile": ground["physical_profile"], "public_declaration": ground,
                "spawn_binding": {"deployment_directive": {"position_m": [0., 0., .4]}}}],
            "scene_overlay": {"vertical_link_bindings": [{"id": "stair",
                "public_vertical_link": {"from_region_id": "entry", "to_region_id": "landing"}}]}}


def hit(origin, direction, distance, path="/World/Floor"):
    return {"collision": path, "rigid_body": "",
            "position": [a + b * distance for a, b in zip(origin, direction)],
            "normal": [0., 0., 1.], "distance": distance}


class FloorQuery:
    def __init__(self):
        self.calls = []

    def raycast_all(self, origin, direction, distance, callback):
        self.calls.append((origin, direction, distance))
        if direction == (0., 0., -1.) and 0 <= origin[2] <= distance:
            callback(hit(origin, direction, origin[2]))


def test_classified_support_all_categories_and_no_execution_promotion(stage, deployment):
    before = deepcopy(deployment)
    query = FloorQuery()
    result = spatial.collect_live_spatial(stage, deployment, query=query)
    sample = result["spawns"][0]["samples"][0]
    assert sample["support"]["support_z_m"] == 0
    assert sample["support"]["hit"]["collision"] == "/World/Floor"
    assert sample["support"]["hit"]["kind"] == "static"
    assert sample["support_patch"]["status"] == "measured"
    assert len(sample["support_patch"]["rays"]) == 9
    assert all(r["status"] == "sampled_no_hit" for r in sample["clearance_rays"])
    assert result["spawns"][0]["robot_pose_measured"] is False
    assert result["vertical_links"][0]["status"] == "blocked"
    assert len(result["vertical_links"][0]["endpoint_checks"]) == 2
    assert "missing_bound_stair_tread_geometry" in result["vertical_links"][0]["blockers"]
    assert result["query_count"] == len(query.calls)
    assert result["scoreable"] is result["traversal_validated"] is False
    assert result["measurement_authenticity_validated"] is False
    assert deployment == before
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("kind", ["dynamic", "disabled", "visual_only", "missing"])
def test_nearer_unverified_collider_is_not_skipped_to_claim_floor(stage, deployment, kind):
    obstacle = stage.GetPrimAtPath("/World/Obstacle")
    if kind == "dynamic":
        UsdPhysics.RigidBodyAPI.Apply(obstacle)
    elif kind == "disabled":
        UsdPhysics.CollisionAPI(obstacle).CreateCollisionEnabledAttr(False)
    elif kind == "visual_only":
        obstacle.RemoveAPI(UsdPhysics.CollisionAPI)
    else:
        stage.RemovePrim("/World/Obstacle")

    def query(origin, direction, distance, callback):
        callback(hit(origin, direction, .8))
        callback(hit(origin, direction, .2, "/World/Obstacle"))

    result = spatial.collect_live_spatial(stage, deployment, query=query)
    support = result["spawns"][0]["samples"][0]["support"]
    assert support["status"] == "unknown"
    assert support["hit"]["collision"] == "/World/Obstacle"
    assert support["reason"] == "nonstatic_or_unverified_collider"


def test_clearance_hit_at_midpoint_blocks_free_route_endpoints(stage, deployment):
    floor = FloorQuery()

    def query(origin, direction, distance, callback):
        if direction == [1., 0., 0.] and origin[0] == 1.:
            callback(hit(origin, direction, .2, "/World/Obstacle"))
        else:
            floor.raycast_all(origin, direction, distance, callback)

    result = spatial.collect_live_spatial(stage, deployment, query=query)
    path = result["routes"][0]["paths"][0]
    assert len(path["samples"]) == 3
    assert path["status"] == "blocked"
    assert path["samples"][1]["clearance_rays"][1]["nearest_hit"]["collision"] == "/World/Obstacle"
    assert path["samples"][0]["clearance_rays"][1]["status"] == "sampled_no_hit"
    assert path["samples"][2]["clearance_rays"][1]["status"] == "sampled_no_hit"


def test_aerial_missing_routes_and_missing_query_are_explicit(stage, deployment):
    deployment["robot_instances"][0]["public_declaration"]["mobility"] = "aerial"
    deployment["route_bindings"][0]["planning_candidate"] = None
    result = spatial.collect_live_spatial(stage, deployment)
    assert "scene_query_unavailable" in result["blockers"]
    assert result["query_count"] == 0
    assert result["spawns"][0]["blockers"] == ["unsupported_aerial_volume_evidence"]
    assert result["routes"][0]["blockers"] == ["missing_route_polyline_no_endpoint_interpolation"]


@pytest.mark.parametrize("metric", ["units", "axis", "roots"])
def test_unsupported_stage_frame_or_unresolved_roots_prevent_queries(stage, deployment, metric):
    if metric == "units":
        UsdGeom.SetStageMetersPerUnit(stage, .01)
    elif metric == "axis":
        UsdGeom.SetStageUpAxis(stage, "Y")
    else:
        stage.RemovePrim("/World/Robot")
    query = FloorQuery()
    result = spatial.collect_live_spatial(stage, deployment, query=query)
    assert not query.calls
    assert result["spawns"][0]["status"] == "blocked"


def test_robot_hits_are_identified_and_excluded_from_support(stage, deployment):
    def query(origin, direction, distance, callback):
        if direction == (0., 0., -1.):
            callback(hit(origin, direction, .01, "/World/Robot/body"))
            callback(hit(origin, direction, .5))
    result = spatial.collect_live_spatial(stage, deployment, query=query)
    support = result["spawns"][0]["samples"][0]["support"]
    assert support["status"] == "measured"
    assert support["excluded_hits"][0]["collision"] == "/World/Robot/body"


def test_budget_and_point_truncation_fail_closed(stage, deployment, monkeypatch):
    monkeypatch.setattr(spatial, "MAX_QUERIES", 4)
    deployment["region_bindings"][0] = {"id": "many", "geometry": {
        "type": "Polygon", "coordinates": [[[float(i), 0.] for i in range(100)]]}}
    query = FloorQuery()
    result = spatial.collect_live_spatial(stage, deployment, query=query)
    assert len(query.calls) == 4
    assert result["regions"][0]["sampling_truncated"] is True
    assert len(result["regions"][0]["samples"]) == spatial.MAX_POINTS
    assert "scene_query_budget_reached" in result["blockers"]
    assert result["regions"][0]["status"] == "blocked"


def test_directed_city_polylines_remain_separate_and_2d_height_is_not_measured(stage, deployment):
    deployment["route_bindings"][0].pop("planning_candidate")
    deployment["route_bindings"][0]["geometry"] = {"type": "DirectedPolylineSet", "routes": [
        {"id": "forward", "points_xy_m": [[0., 0.], [2., 0.]]},
        {"id": "reverse", "points_xy_m": [[2., 0.], [0., 0.]]}]}
    result = spatial.collect_live_spatial(stage, deployment, query=FloorQuery())
    paths = result["routes"][0]["paths"]
    assert [p["id"] for p in paths] == ["forward", "reverse"]
    assert paths[1]["samples"][0]["reference_xyz_m"] == [2., 0., 0.]
    assert paths[0]["samples"][0]["coordinate_basis"].startswith("2d_search_window_only")
    assert paths[0]["samples"][1]["coordinate_basis"].startswith("2d_search_window_only")


def test_query_failure_and_inconsistent_hit_do_not_produce_support(stage, deployment):
    def bad_query(origin, direction, distance, callback):
        row = hit(origin, direction, .5)
        row["position"][0] += 1
        callback(row)
    result = spatial.collect_live_spatial(stage, deployment, query=bad_query)
    assert result["spawns"][0]["samples"][0]["support"]["reason"] == "inconsistent_hit"

    def failed(*args):
        raise RuntimeError("query not ready after reset")
    result = spatial.collect_live_spatial(stage, deployment, query=failed)
    assert result["spawns"][0]["status"] == "blocked"
    assert "query not ready" in result["spawns"][0]["samples"][0]["support"]["reason"]


def test_support_patch_rejects_a_hole_outside_the_center_ray(stage, deployment):
    floor = FloorQuery()

    def query(origin, direction, distance, callback):
        if abs(origin[0]) < .2:
            floor.raycast_all(origin, direction, distance, callback)

    result = spatial.collect_live_spatial(stage, deployment, query=query)
    spawn = result["spawns"][0]
    assert spawn["samples"][0]["support"]["status"] == "measured"
    assert spawn["samples"][0]["support_patch"]["status"] == "unknown"
    assert spawn["status"] == "blocked"
    assert "support_patch_unknown_or_nonflat" in spawn["blockers"]


@pytest.mark.parametrize("mutation", ["nonfinite", "steep", "hit_limit"])
def test_bad_support_evidence_is_unknown_and_strict_json(stage, deployment, mutation):
    def query(origin, direction, distance, callback):
        row = hit(origin, direction, .5)
        if mutation == "nonfinite":
            row["distance"] = float("nan")
        if mutation == "steep":
            row["normal"] = [1., 0., 0.]
        for _ in range(257 if mutation == "hit_limit" else 1):
            if callback(row) is False:
                break

    result = spatial.collect_live_spatial(stage, deployment, query=query)
    assert result["spawns"][0]["status"] == "blocked"
    assert result["spawns"][0]["samples"][0]["support"]["status"] == "unknown"
    json.dumps(result, allow_nan=False)
