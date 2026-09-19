"""Static Factory stair measurement tests; no Isaac application is started."""

import json
from pathlib import Path

import pytest

from tools.scenario_generation.measure_factory_stair import (
    detect_tread_candidates, measure_factory_stair,
)


def _face(face_id, z, y0, y1, *, width=2.0, area=None):
    points = [[0.0, y0, z], [width, y0, z], [width, y1, z], [0.0, y1, z]]
    return {
        "prim_path": "/World/SM_StairsSmall/StairMesh",
        "face_index": face_id,
        "points_world_m": points,
        "area_m2": area if area is not None else width * abs(y1 - y0),
        "long_edge_axis_world_xy": [1.0, 0.0],
    }


def test_tread_sequence_uses_authored_faces_and_never_bbox_top():
    report = detect_tread_candidates([
        _face(0, 0.20, -0.40, 0.0),
        _face(1, 0.40, -0.80, -0.40),
        _face(2, 0.60, -1.20, -0.80),
        _face(3, 0.0, 0.0, 2.0, width=2.0),  # broad landing, not a tread
    ])
    assert report["status"] == "measured"
    assert len(report["flights"]) == 1
    treads = report["flights"][0]["treads"]
    assert [round(row["riser_height_m"], 3) for row in treads[1:]] == [0.2, 0.2]
    assert [round(row["tread_depth_m"], 3) for row in treads[1:]] == [0.4, 0.4]
    assert max(row["support_z_m"] for row in treads) == pytest.approx(0.6)


def test_geometry_insufficiency_is_unknown():
    report = detect_tread_candidates([_face(0, 0.2, -0.4, 0.0, width=0.5)])
    assert report["status"] == "unknown"
    assert report["reason"] == "no_tread_like_horizontal_faces"
    assert report["treads"] == []


def test_nonmonotonic_horizontal_faces_do_not_become_a_stair_flight():
    report = detect_tread_candidates([
        _face(0, 0.20, -0.40, 0.0),
        _face(1, 0.60, -0.80, -0.40),
        _face(2, 0.40, -1.20, -0.80),
    ])
    assert report["status"] == "unknown"
    assert report["flights"][0]["reason"] == \
        "insufficient_monotonic_tread_sequence"


def _mesh(stage, path, points, counts, indices):
    from pxr import UsdGeom

    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.GetPointsAttr().Set(points)
    mesh.GetFaceVertexCountsAttr().Set(counts)
    mesh.GetFaceVertexIndicesAttr().Set(indices)
    return mesh


def test_measure_temp_usd_stage_binds_aabb_faces_supports_and_root_hash(tmp_path):
    pytest.importorskip("pxr")
    from pxr import Gf, Usd

    stage_path = tmp_path / "factory.usda"
    stage = Usd.Stage.CreateNew(str(stage_path))
    stage.SetMetadata("metersPerUnit", 1.0)
    stage.SetMetadata("upAxis", "Z")
    root = stage.DefinePrim("/World/SM_StairsSmall", "Xform")
    del root

    # Each tread is an authored upward quad.  The two landings are broad
    # horizontal faces and are eligible as support evidence only through
    # endpoint adjacency, never through the root AABB.
    points = []
    indices = []
    for z, y0, y1 in ((0.2, -0.4, 0.0), (0.4, -0.8, -0.4), (0.6, -1.2, -0.8)):
        base = len(points)
        points.extend([Gf.Vec3f(0, y0, z), Gf.Vec3f(2, y0, z),
                       Gf.Vec3f(2, y1, z), Gf.Vec3f(0, y1, z)])
        indices.extend([base, base + 1, base + 2, base + 3])
    _mesh(stage, "/World/SM_StairsSmall/StairMesh", points, [4, 4, 4], indices)

    support_points = [Gf.Vec3f(0, 0, 0), Gf.Vec3f(2, 0, 0),
                      Gf.Vec3f(2, 1, 0), Gf.Vec3f(0, 1, 0),
                      Gf.Vec3f(0, -2.0, 0.6), Gf.Vec3f(2, -2.0, 0.6),
                      Gf.Vec3f(2, -1.2, 0.6), Gf.Vec3f(0, -1.2, 0.6)]
    _mesh(stage, "/World/SM_StairsSmall/StartLanding", support_points[:4], [4], [0, 1, 2, 3])
    _mesh(stage, "/World/SM_StairsSmall/EndLanding", support_points[4:], [4], [0, 1, 2, 3])
    stage.GetRootLayer().Save()

    report = measure_factory_stair(stage_path)
    assert report["status"] == "measured"
    assert report["world_aabb_m"]["max"][2] == pytest.approx(0.6)
    assert report["direction"]["run_axis_world_xy"] == pytest.approx([0.0, 1.0])
    assert report["net_width_m"]["value_m"] == pytest.approx(2.0)
    assert report["support_regions"]["status"] == "measured"
    assert report["support_regions"]["start"]["status"] == "measured"
    assert report["support_regions"]["end"]["status"] == "measured"
    assert report["support_search_world_aabb_m"] == {
        "min": pytest.approx([-1.0, -3.0, -0.35]),
        "max": pytest.approx([3.0, 2.0, 0.95]),
    }
    assert report["provenance"]["root_layer"]["sha256"]
    assert len(report["provenance"]["root_layer"]["sha256"]) == 64

    # The public CLI contract remains JSON serializable and does not write a
    # report beside the stage unless a caller explicitly redirects stdout.
    json.dumps(report, allow_nan=False)
