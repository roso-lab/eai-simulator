from __future__ import annotations

import pytest

pytest.importorskip("pxr")
from pxr import Gf, Usd, UsdGeom, UsdPhysics, UsdShade, Vt

from EAI_assets.scene.factory_runtime import (
    FACTORY_RAYCAST_PROXY_PRIM_PATH,
    build_factory_raycast_proxy,
    configure_factory_high_friction,
)


def _define_mesh(stage, path, points, counts, indices):
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.CreatePointsAttr().Set([Gf.Vec3f(*point) for point in points])
    mesh.CreateFaceVertexCountsAttr().Set(counts)
    mesh.CreateFaceVertexIndicesAttr().Set(indices)
    return mesh


def _factory_stage():
    stage = Usd.Stage.CreateInMemory()
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Xform.Define(stage, "/World/Indoor")
    _define_mesh(
        stage,
        "/World/Indoor/GroundPlane/CollisionMesh",
        [(-2, -2, 0), (2, -2, 0), (2, 2, 0), (-2, 2, 0)],
        [4],
        [0, 1, 2, 3],
    )
    stairs = UsdGeom.Xform.Define(stage, "/World/Indoor/SM_StairsSmall")
    stairs.AddTranslateOp().Set(Gf.Vec3d(1.0, 2.0, 0.5))
    UsdGeom.Xform.Define(stage, "/World/Indoor/SM_StairsSmall/SM_StairsSmall")
    for section in ("Section0", "Section1"):
        _define_mesh(
            stage,
            f"/World/Indoor/SM_StairsSmall/SM_StairsSmall/{section}",
            [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
            [3],
            [0, 1, 2],
        )
    _define_mesh(
        stage,
        "/World/Indoor/wall/wall_06",
        [(-1, -1, 1.5), (1, -1, 1.5), (1, 1, 1.5), (-1, 1, 1.5)],
        [4],
        [0, 1, 2, 3],
    )
    _define_mesh(
        stage,
        "/World/Indoor/RaycastMergeMesh",
        [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
        [3],
        [0, 1, 2],
    )
    UsdGeom.Scope.Define(stage, "/World/Indoor/Pysical_Material")
    UsdShade.Material.Define(
        stage, "/World/Indoor/Pysical_Material/TrainHighFriction_PM"
    )
    return stage


def test_factory_proxy_is_transformed_triangulated_and_index_safe():
    stage = _factory_stage()

    report = build_factory_raycast_proxy(stage, "/World/Indoor")

    assert report.prim_path == FACTORY_RAYCAST_PROXY_PRIM_PATH
    assert report.point_count == 14
    assert report.triangle_count == 6
    assert stage.GetPrimAtPath("/World/Indoor/RaycastMergeMesh").IsActive() is False

    mesh = UsdGeom.Mesh(stage.GetPrimAtPath(report.prim_path))
    points = mesh.GetPointsAttr().Get()
    counts = mesh.GetFaceVertexCountsAttr().Get()
    indices = mesh.GetFaceVertexIndicesAttr().Get()
    assert set(counts) == {3}
    assert sum(counts) == len(indices)
    assert max(indices) < len(points)
    assert UsdGeom.Mesh.ValidateTopology(
        Vt.IntArray(indices), Vt.IntArray(counts), len(points)
    ) == (True, "")
    assert tuple(points[4]) == pytest.approx((1.0, 2.0, 0.5))


def test_factory_high_friction_material_is_applied_and_bound():
    stage = _factory_stage()

    material_path = configure_factory_high_friction(stage, "/World/Indoor")

    material_prim = stage.GetPrimAtPath(material_path)
    material_api = UsdPhysics.MaterialAPI(material_prim)
    assert material_api.GetStaticFrictionAttr().Get() == pytest.approx(1.0)
    assert material_api.GetDynamicFrictionAttr().Get() == pytest.approx(1.0)
    for source_path in (
        "/World/Indoor/GroundPlane/CollisionMesh",
        "/World/Indoor/SM_StairsSmall/SM_StairsSmall/Section0",
        "/World/Indoor/SM_StairsSmall/SM_StairsSmall/Section1",
        "/World/Indoor/wall/wall_06",
    ):
        binding = UsdShade.MaterialBindingAPI(
            stage.GetPrimAtPath(source_path)
        ).GetDirectBinding("physics")
        assert binding.GetMaterialPath() == material_prim.GetPath()
