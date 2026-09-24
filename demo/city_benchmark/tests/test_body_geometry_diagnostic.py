"""CPU-only OpenUSD checks for the City terminal body diagnostic."""

from __future__ import annotations

import json
import math

import pytest

from demo.city_benchmark.body_geometry_diagnostic import capture_body_geometry


def _footprint(*, center=(0.0, 0.0), radius=10.0):
    return {
        "region_id": "assembly_candidate_003",
        "footprint": "disk",
        "center_xy_m": list(center),
        "radius_m": radius,
        "support_plane_z_m": 0.3,
        "capacity": 1,
        "geometry_sha256": "0" * 64,
    }


def _capture(stage, *, footprint=None):
    return capture_body_geometry(
        stage,
        prim_path="/Actor",
        person_id="affected_person_02",
        episode_id="episode",
        source_id="isaac.city.runtime",
        captured_native_s=98.02,
        footprint=footprint or _footprint(),
    )


def _stage_with_mesh(points, *, extent):
    pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom

    stage = Usd.Stage.CreateInMemory()
    actor = UsdGeom.Xform.Define(stage, "/Actor")
    actor.AddTranslateOp().Set((10.0, 20.0, 0.0))
    actor.AddRotateZOp().Set(45.0)
    UsdGeom.Xform.Define(stage, "/Actor/Asset")
    mesh = UsdGeom.Mesh.Define(stage, "/Actor/Asset/Mesh")
    mesh.CreatePointsAttr(points)
    mesh.CreateExtentAttr(extent)
    return stage, mesh


def test_rotated_current_mesh_separates_oriented_points_from_world_aabb():
    points = [
        (-1.0, -2.0, 0.0), (-1.0, 2.0, 0.0),
        (1.0, -2.0, 0.0), (1.0, 2.0, 0.0),
        (-1.0, -2.0, 1.0), (-1.0, 2.0, 1.0),
        (1.0, -2.0, 1.0), (1.0, 2.0, 1.0),
    ]
    stage, _mesh = _stage_with_mesh(points, extent=[(-1, -2, 0), (1, 2, 1)])
    result = _capture(stage, footprint=_footprint(center=(10, 20), radius=2.5))

    assert result["status"] == "measured"
    assert result["reason_codes"] == []
    assert result["measurement"]["actor_local_range"]["min"] == \
        pytest.approx([-1.0, -2.0, 0.0])
    assert result["measurement"]["actor_local_range"]["max"] == \
        pytest.approx([1.0, 2.0, 1.0])
    assert result["transforms"]["root"]["world_yaw_rad"] == pytest.approx(math.pi / 4)
    assert result["measurement"]["max_visible_mesh_point_xy_distance_m"] == \
        pytest.approx(math.sqrt(5.0))
    assert result["measurement"]["max_oriented_corner_xy_distance_m"] == \
        pytest.approx(math.sqrt(5.0))
    assert result["measurement"]["visible_mesh_points_inside_footprint"] is True
    assert result["measurement"]["oriented_bound_inside_footprint"] is True

    low, high = result["world_aabb"]["min"], result["world_aabb"]["max"]
    world_aabb_distance = max(
        math.hypot(x - 10.0, y - 20.0)
        for x in (low[0], high[0]) for y in (low[1], high[1]))
    assert world_aabb_distance == pytest.approx(3.0)
    assert world_aabb_distance > 2.5

    archived_points = result["measurement"]["meshes"][0]["world_points_m"]
    assert max(math.hypot(p[0] - 10.0, p[1] - 20.0) for p in archived_points) == \
        pytest.approx(result["measurement"]["max_visible_mesh_point_xy_distance_m"])
    assert max(math.hypot(p[0] - 10.0, p[1] - 20.0)
               for p in result["measurement"]["oriented_world_corners"]) == \
        pytest.approx(result["measurement"]["max_oriented_corner_xy_distance_m"])
    json.dumps(result, allow_nan=False)
    for key in ("platform_valid", "execution_validated",
                "business_outcomes_validated", "scoreable"):
        assert result[key] is False


def _skinned_stage():
    pytest.importorskip("pxr")
    from pxr import Gf, Usd, UsdGeom, UsdSkel, Vt

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(stage, "/Actor")
    UsdGeom.Xform.Define(stage, "/Actor/Asset")
    skel_root = UsdSkel.Root.Define(stage, "/Actor/Asset/Rig")
    skeleton = UsdSkel.Skeleton.Define(stage, "/Actor/Asset/Rig/Skeleton")
    # Keep the skeleton prim itself non-identity so the test also catches
    # omission of the final skeleton-local-to-world transform.
    UsdGeom.Xformable(skeleton.GetPrim()).AddTranslateOp().Set((0.0, 3.0, 0.0))
    skeleton.CreateJointsAttr().Set(Vt.TokenArray(["root"]))
    identity = Gf.Matrix4d(1.0)
    skeleton.CreateBindTransformsAttr().Set(Vt.Matrix4dArray([identity]))
    skeleton.CreateRestTransformsAttr().Set(Vt.Matrix4dArray([identity]))

    animation = UsdSkel.Animation.Define(stage, "/Actor/Asset/Rig/Animation")
    animation.CreateJointsAttr().Set(Vt.TokenArray(["root"]))
    animation.CreateTranslationsAttr().Set(Vt.Vec3fArray([(2.0, 0.0, 0.0)]))
    animation.CreateRotationsAttr().Set(Vt.QuatfArray([Gf.Quatf(1.0)]))
    animation.CreateScalesAttr().Set(Vt.Vec3hArray([(1.0, 1.0, 1.0)]))

    root_binding = UsdSkel.BindingAPI.Apply(skel_root.GetPrim())
    root_binding.CreateSkeletonRel().SetTargets([skeleton.GetPath()])
    root_binding.CreateAnimationSourceRel().SetTargets([animation.GetPath()])

    mesh = UsdGeom.Mesh.Define(stage, "/Actor/Asset/Rig/Mesh")
    mesh.CreatePointsAttr([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)])
    mesh.CreateExtentAttr([(0.0, 0.0, 0.0), (1.0, 1.0, 0.0)])
    # ComputeSkinnedPoints returns skeleton-local points.  A non-identity mesh
    # transform and matching geomBindTransform catch accidental application of
    # mesh-to-world after skinning, which would translate these points twice.
    mesh.AddTranslateOp().Set((5.0, 0.0, 0.0))
    mesh_binding = UsdSkel.BindingAPI.Apply(mesh.GetPrim())
    geom_bind = Gf.Matrix4d(1.0)
    geom_bind.SetTranslate((5.0, 0.0, 0.0))
    mesh_binding.CreateGeomBindTransformAttr().Set(geom_bind)
    mesh_binding.SetRigidJointInfluence(0, 1.0)
    return stage, mesh


def test_current_usdskel_points_are_archived_and_offline_recomputable():
    stage, _mesh = _skinned_stage()
    result = _capture(stage, footprint=_footprint(center=(0, 0), radius=4.0))

    assert result["status"] == "measured", result
    mesh = result["measurement"]["meshes"][0]
    assert mesh["point_source"] == "current_skinned_points"
    assert mesh["point_space"] == "skeleton_local"
    expected = [[7.0, 3.0, 0.0], [8.0, 3.0, 0.0], [7.0, 4.0, 0.0]]
    assert len(mesh["world_points_m"]) == len(expected)
    for actual, wanted in zip(mesh["world_points_m"], expected):
        assert actual == pytest.approx(wanted)
    offline_max = max(math.hypot(point[0], point[1]) for point in mesh["world_points_m"])
    assert offline_max == pytest.approx(math.sqrt(73.0))
    assert result["measurement"]["max_visible_mesh_point_xy_distance_m"] == \
        pytest.approx(offline_max)
    # Bind-pose points would stop at x=6; x=7..8 proves the animation was sampled.
    assert result["measurement"]["actor_local_range"]["min"][0] == pytest.approx(7.0)
    assert result["measurement"]["actor_local_range"]["max"][0] == pytest.approx(8.0)
    assert result["measurement"]["actor_local_range"]["min"][1] == pytest.approx(3.0)
    assert result["measurement"]["actor_local_range"]["max"][1] == pytest.approx(4.0)


def test_inherited_proxy_purpose_is_not_recorded_as_visible_render_geometry():
    pytest.importorskip("pxr")
    from pxr import Usd, UsdGeom

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(stage, "/Actor")
    UsdGeom.Xform.Define(stage, "/Actor/Asset")
    proxy = UsdGeom.Xform.Define(stage, "/Actor/Asset/Proxy")
    proxy.CreatePurposeAttr().Set(UsdGeom.Tokens.proxy)
    mesh = UsdGeom.Mesh.Define(stage, "/Actor/Asset/Proxy/Mesh")
    mesh.CreatePointsAttr([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)])
    mesh.CreateExtentAttr([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)])
    render = UsdGeom.Mesh.Define(stage, "/Actor/Asset/RenderMesh")
    render.CreatePointsAttr([(0.0, 0.0, 0.0), (0.0, 1.0, 0.0)])
    render.CreateExtentAttr([(0.0, 0.0, 0.0), (0.0, 1.0, 0.0)])

    result = _capture(stage)
    assert result["status"] == "measured"
    assert result["measurement"]["mesh_count"] == 1
    assert result["measurement"]["meshes"][0]["prim_path"] == \
        "/Actor/Asset/RenderMesh"


def test_skinned_mesh_without_current_animation_is_unknown_not_bind_pose():
    pytest.importorskip("pxr")
    from pxr import Gf, Usd, UsdGeom, UsdSkel, Vt

    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(stage, "/Actor")
    UsdGeom.Xform.Define(stage, "/Actor/Asset")
    skel_root = UsdSkel.Root.Define(stage, "/Actor/Asset/Rig")
    skeleton = UsdSkel.Skeleton.Define(stage, "/Actor/Asset/Rig/Skeleton")
    identity = Gf.Matrix4d(1.0)
    skeleton.CreateJointsAttr().Set(Vt.TokenArray(["root"]))
    skeleton.CreateBindTransformsAttr().Set(Vt.Matrix4dArray([identity]))
    skeleton.CreateRestTransformsAttr().Set(Vt.Matrix4dArray([identity]))
    UsdSkel.BindingAPI.Apply(skel_root.GetPrim()).CreateSkeletonRel().SetTargets(
        [skeleton.GetPath()])
    mesh = UsdGeom.Mesh.Define(stage, "/Actor/Asset/Rig/Mesh")
    mesh.CreatePointsAttr([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)])
    mesh.CreateExtentAttr([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)])
    binding = UsdSkel.BindingAPI.Apply(mesh.GetPrim())
    binding.CreateGeomBindTransformAttr().Set(identity)
    binding.SetRigidJointInfluence(0, 1.0)

    result = _capture(stage)
    assert result["status"] == "unknown"
    assert result["reason_codes"] == ["current_animation_unavailable"]
    assert result["measurement"]["meshes"] == []


@pytest.mark.parametrize("missing", ["translations", "rotations", "scales"])
def test_incomplete_current_animation_components_never_fall_back_to_rest_pose(missing):
    pytest.importorskip("pxr")
    from pxr import UsdSkel

    stage, _mesh = _skinned_stage()
    animation = UsdSkel.Animation(stage.GetPrimAtPath("/Actor/Asset/Rig/Animation"))
    {
        "translations": animation.GetTranslationsAttr,
        "rotations": animation.GetRotationsAttr,
        "scales": animation.GetScalesAttr,
    }[missing]().Clear()

    result = _capture(stage)
    assert result["status"] == "unknown"
    assert result["reason_codes"] == ["current_animation_unavailable"]
    assert result["measurement"]["meshes"] == []


def test_unsupported_blend_shapes_fail_closed_without_partial_points():
    pytest.importorskip("pxr")
    from pxr import UsdSkel, Vt

    stage, mesh = _stage_with_mesh(
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)],
        extent=[(0, 0, 0), (1, 0, 0)],
    )
    UsdSkel.BindingAPI.Apply(mesh.GetPrim()).CreateBlendShapesAttr().Set(
        Vt.TokenArray(["expression"]))
    result = _capture(stage)
    assert result["status"] == "unknown"
    assert result["reason_codes"] == ["unsupported_blend_shapes"]
    assert result["measurement"]["meshes"] == []
    assert result["measurement"]["point_count"] is None


def test_incomplete_skinning_never_falls_back_to_authored_points():
    pytest.importorskip("pxr")
    from pxr import UsdSkel

    stage, mesh = _stage_with_mesh(
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)],
        extent=[(0, 0, 0), (1, 0, 0)],
    )
    UsdSkel.BindingAPI.Apply(mesh.GetPrim()).SetRigidJointInfluence(0, 1.0)
    result = _capture(stage)
    assert result["status"] == "unknown"
    assert result["reason_codes"] == ["incomplete_skinning_binding"]
    assert result["measurement"]["meshes"] == []


@pytest.mark.parametrize("kwargs", [
    {"stage": None},
    {"stage": None, "prim_path": None},
    {"stage": None, "footprint": None},
    {"stage": None, "source_id": None},
    {"stage": None, "captured_native_s": float("nan")},
    {"stage": None, "footprint": _footprint(radius=float("nan"))},
])
def test_missing_fake_inputs_return_json_safe_unknown(kwargs):
    call = {
        "stage": None,
        "prim_path": "/Actor",
        "person_id": "affected_person_02",
        "episode_id": "episode",
        "source_id": "isaac.city.runtime",
        "captured_native_s": 1.0,
        "footprint": _footprint(),
    }
    call.update(kwargs)
    result = capture_body_geometry(**call)
    assert result["status"] == "unknown"
    assert result["reason_codes"]
    assert result["scoreable"] is False
    json.dumps(result, allow_nan=False)


def test_missing_stage_has_specific_fail_closed_reason():
    result = _capture(None)
    assert result["status"] == "unknown"
    assert result["reason_codes"] == ["stage_unavailable"]
