"""Focused native NVD prop geometry and physics-schema probes.

These tests use standalone OpenUSD only.  They do not start Isaac Sim, load
ROS, download assets, or create a primitive-box substitute.
"""

import pytest

pytest.importorskip("pxr.Usd", reason="standalone OpenUSD is required")
from pxr import Gf, Usd, UsdGeom, UsdPhysics

from EAI_hmrs.scenario_builder import (
    NativeUsdGeometry,
    author_native_physics,
    inspect_native_usd,
    native_spawn_transform,
)


def _source_usd(path):
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdGeom.SetStageMetersPerUnit(stage, 0.01)
    root = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(root.GetPrim())
    mesh = UsdGeom.Mesh.Define(stage, "/World/NativeMesh")
    mesh.CreatePointsAttr().Set([
        Gf.Vec3f(10, 20, 0), Gf.Vec3f(30, 20, 0), Gf.Vec3f(30, 60, 0), Gf.Vec3f(10, 60, 0),
        Gf.Vec3f(10, 20, 20), Gf.Vec3f(30, 20, 20), Gf.Vec3f(30, 60, 20), Gf.Vec3f(10, 60, 20),
    ])
    mesh.CreateFaceVertexCountsAttr().Set([4, 4, 4, 4, 4, 4])
    mesh.CreateFaceVertexIndicesAttr().Set([0, 1, 2, 3, 4, 7, 6, 5, 0, 4, 5, 1, 1, 5, 6, 2, 2, 6, 7, 3, 4, 0, 3, 7])
    stage.GetRootLayer().Save()
    return path


def test_native_usd_probe_measures_cm_geometry_and_centres_spawn(tmp_path):
    geometry = inspect_native_usd(_source_usd(tmp_path / "native.usda"))
    assert geometry.source_meters_per_unit == pytest.approx(0.01)
    assert geometry.source_up_axis == "Z"
    assert geometry.bbox_min == pytest.approx((10.0, 20.0, 0.0))
    assert geometry.bbox_max == pytest.approx((30.0, 60.0, 20.0))
    assert geometry.bbox_center == pytest.approx((20.0, 40.0, 10.0))
    assert geometry.bbox_size == pytest.approx((20.0, 40.0, 20.0))
    transform = native_spawn_transform(
        geometry, target_meters_per_unit=1.0, target_up_axis="Z",
        desired_position=(4.0, 5.0, 0.7), desired_orientation=(1.0, 0.0, 0.0, 0.0))
    assert transform["scale"] == pytest.approx((0.01, 0.01, 0.01))
    assert transform["bbox_size_m"] == pytest.approx((0.2, 0.4, 0.2))
    assert transform["translation"] == pytest.approx((3.8, 4.6, 0.6))
    assert transform["bbox_center_world"] == pytest.approx((4.0, 5.0, 0.7))


def test_native_usd_probe_converts_y_up_without_changing_size():
    geometry = NativeUsdGeometry(0.01, "Y", (0, 0, 0), (10, 20, 30), (5, 10, 15), (10, 20, 30))
    transform = native_spawn_transform(
        geometry, target_meters_per_unit=1.0, target_up_axis="Z",
        desired_position=(1.0, 2.0, 3.0), desired_orientation=(1.0, 0.0, 0.0, 0.0))
    assert transform["scale"] == pytest.approx((0.01, 0.01, 0.01))
    assert transform["orientation"][0] == pytest.approx(2 ** -0.5)
    assert transform["orientation"][1] == pytest.approx(2 ** -0.5)
    assert transform["translation"] == pytest.approx((0.95, 2.15, 2.9))


def test_native_physics_probe_authors_mesh_collision_mass_friction(tmp_path):
    stage = Usd.Stage.CreateNew(str(tmp_path / "composed.usda"))
    root = UsdGeom.Xform.Define(stage, "/World/Prop")
    mesh = UsdGeom.Mesh.Define(stage, "/World/Prop/NativeMesh")
    mesh.CreatePointsAttr().Set([Gf.Vec3f(-1, -1, 0), Gf.Vec3f(1, -1, 0), Gf.Vec3f(1, 1, 0), Gf.Vec3f(-1, 1, 0)])
    mesh.CreateFaceVertexCountsAttr().Set([4])
    mesh.CreateFaceVertexIndicesAttr().Set([0, 1, 2, 3])
    report = author_native_physics(
        stage, "/World/Prop", dynamic=True, kinematic=False, mass_kg=8.0,
        static_friction=0.8, dynamic_friction=0.6, restitution=0.05)
    assert report["mesh_count"] == 1
    assert report["native_collision_approximation"] == "convexDecomposition"
    assert UsdPhysics.RigidBodyAPI(root.GetPrim())
    assert UsdPhysics.RigidBodyAPI(root.GetPrim()).GetKinematicEnabledAttr().Get() is False
    assert UsdPhysics.MassAPI(root.GetPrim()).GetMassAttr().Get() == pytest.approx(8.0)
    assert UsdPhysics.CollisionAPI(mesh.GetPrim())
    assert UsdPhysics.MeshCollisionAPI(mesh.GetPrim()).GetApproximationAttr().Get() == "convexDecomposition"
    material = stage.GetPrimAtPath("/World/Prop/__eai_physics_material")
    material_api = UsdPhysics.MaterialAPI(material)
    assert material_api.GetStaticFrictionAttr().Get() == pytest.approx(0.8)
    assert material_api.GetDynamicFrictionAttr().Get() == pytest.approx(0.6)
    assert material_api.GetRestitutionAttr().Get() == pytest.approx(0.05)
    from pxr import UsdShade
    assert bool(UsdShade.MaterialBindingAPI(mesh.GetPrim()).GetDirectBinding("physics").GetMaterial())
