"""Relocation must preserve authored physics and include hidden asset dependencies."""

import pytest

pytest.importorskip("pxr")
from pxr import Sdf, Usd, UsdGeom, UsdPhysics

from tools.assets.usd_closure import audit
from tools.assets.relocate_usd_closure import relocate
from tools.assets.import_simple_car import (
    _missing_visual_reference_targets,
    _repair_visual_reference_targets,
)


def test_simple_car_empty_visual_reference_targets_are_materialized(tmp_path):
    layer = Sdf.Layer.CreateNew(str(tmp_path / "simple_car_base.usda"))
    visual_link = Sdf.CreatePrimInLayer(layer, "/gshub/lidar_link/visuals")
    visual_link.referenceList.Add(Sdf.Reference("", "/visuals/lidar_link"))
    layer.Save()

    layer = Sdf.Layer.FindOrOpen(str(tmp_path / "simple_car_base.usda"))
    assert _missing_visual_reference_targets(layer) == ("/visuals/lidar_link",)
    assert _repair_visual_reference_targets(layer) == ("/visuals/lidar_link",)
    assert _missing_visual_reference_targets(layer) == ()


def test_relocation_includes_variants_samples_udim_and_mdl(tmp_path):
    source = tmp_path / "download"
    source.mkdir()
    usd = tmp_path / "eai" / "usd"
    usd.mkdir(parents=True)
    child = Usd.Stage.CreateNew(str(source / "child.usda"))
    body = UsdGeom.Xform.Define(child, "/Body").GetPrim()
    child.SetDefaultPrim(body)
    UsdPhysics.MassAPI.Apply(body).CreateMassAttr(5.22)
    (source / "paint.1001.png").write_bytes(b"texture fixture")
    (source / "paint.1002.png").write_bytes(b"second tile")
    (source / "detail.png").write_bytes(b"sampled fixture")
    (source / "material.mdl").write_text(
        'mdl 1.4; using ::OmniPBR import OmniPBR; export material fixture() = '
        'OmniPBR(diffuse_texture: texture_2d("detail.png"));')
    body.CreateAttribute("inputs:mdl", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath("material.mdl"))
    body.CreateAttribute("inputs:tiles", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath("paint.<UDIM>.png"))
    body.CreateAttribute("inputs:sample", Sdf.ValueTypeNames.AssetArray).Set(
        [Sdf.AssetPath("detail.png")], time=5)
    variants = body.GetVariantSets().AddVariantSet("look")
    for name in ("default", "optional"):
        variants.AddVariant(name)
        variants.SetVariantSelection(name)
        with variants.GetVariantEditContext():
            body.CreateAttribute("inputs:variant", Sdf.ValueTypeNames.Asset).Set(Sdf.AssetPath("detail.png"))
    variants.SetVariantSelection("default")
    child.GetRootLayer().Save()
    root = Usd.Stage.CreateNew(str(usd / "root.usda"))
    prim = UsdGeom.Xform.Define(root, "/Scene").GetPrim()
    prim.GetPayloads().AddPayload(str(source / "child.usda"))
    root.GetRootLayer().Save()
    source_bytes = (source / "child.usda").read_bytes()
    report = relocate([usd / "root.usda"], source_root=source, destination=usd / "assets",
                      boundary=usd, backup=tmp_path / "backup")
    assert report["validated"]
    assert report["copied_files"] == 5
    assert (source / "child.usda").read_bytes() == source_bytes
    reopened = Usd.Stage.Open(str(usd / "root.usda"))
    assert UsdPhysics.MassAPI(reopened.GetPrimAtPath("/Scene")).GetMassAttr().Get() == pytest.approx(5.22)
    assert "<UDIM>" in (usd / "assets" / "child.usda").read_text()
    assert (tmp_path / "backup" / "root.usda").exists()


def test_missing_dependency_rejects_before_copy(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    stage = Usd.Stage.CreateNew(str(source / "root.usda"))
    stage.GetRootLayer().subLayerPaths = ["absent.usda"]
    stage.GetRootLayer().Save()
    destination = tmp_path / "usd"
    with pytest.raises(ValueError, match="Unresolved dependencies"):
        relocate([source / "root.usda"], source_root=source, destination=destination,
                 boundary=destination, backup=tmp_path / "backup")
    assert not destination.exists()


def test_metrics_layer_requires_materialization_and_relocates_metadata(tmp_path):
    source = tmp_path / "captured"
    source.mkdir()
    captured = Sdf.Layer.CreateNew(str(source / "units.usda"))
    prim = Sdf.CreatePrimInLayer(captured, "/Body")
    Sdf.AttributeSpec(prim, "physics:mass", Sdf.ValueTypeNames.Float).default = 5.22
    captured.Save()
    boundary = tmp_path / "usd"
    boundary.mkdir()
    root = Sdf.Layer.CreateNew(str(boundary / "robot.usda"))
    identifier = "metrics:UnitsAdjust-fixture.metricsAssembler"
    root.subLayerPaths = [identifier]
    root.customLayerData = {"metricsAssembler": {
        "version": 1, "/Body": {"write_layer": identifier}}}
    root.Save()
    assert audit([boundary / "robot.usda"], boundary=boundary)["missing"]
    result = relocate([boundary / "robot.usda"], source_root=source,
                      destination=boundary / "layers", boundary=boundary,
                      backup=tmp_path / "backup", aliases={identifier: str(source / "units.usda")})
    assert result["validated"]
    assert list(root.subLayerPaths) == ["layers/units.usda"]
    assert root.customLayerData["metricsAssembler"]["/Body"]["write_layer"] == "layers/units.usda"
    stage = Usd.Stage.Open(root)
    assert stage.GetPrimAtPath("/Body").GetAttribute("physics:mass").Get() == pytest.approx(5.22)


def test_metrics_metadata_without_sublayer_is_still_a_dependency(tmp_path):
    root = Sdf.Layer.CreateNew(str(tmp_path / "robot.usda"))
    root.customLayerData = {"metricsAssembler": {
        "version": 1, "/Body": {"write_layer": "missing.usda"}}}
    root.Save()
    assert audit([tmp_path / "robot.usda"], boundary=tmp_path)["missing"] == [
        {"layer": "robot.usda", "asset": "missing.usda"}]


def test_delete_list_is_not_a_dependency_and_unknown_mdl_fails(tmp_path):
    layer = Sdf.Layer.CreateNew(str(tmp_path / "root.usda"))
    prim = Sdf.CreatePrimInLayer(layer, "/World")
    prim.referenceList.deletedItems = [Sdf.Reference("/obsolete/deleted.usd")]
    attribute = Sdf.AttributeSpec(prim, "inputs:mdl", Sdf.ValueTypeNames.Asset)
    attribute.default = Sdf.AssetPath("not_a_runtime_module.mdl")
    layer.Save()
    report = audit([tmp_path / "root.usda"], boundary=tmp_path)
    assert report["missing"] == [{"layer": "root.usda", "asset": "not_a_runtime_module.mdl"}]
    assert not report["absolute_references"]


def test_external_symlink_is_not_a_portable_closure(tmp_path):
    boundary = tmp_path / "usd"
    boundary.mkdir()
    (tmp_path / "texture.png").write_bytes(b"external")
    (boundary / "texture.png").symlink_to(tmp_path / "texture.png")
    layer = Sdf.Layer.CreateNew(str(boundary / "root.usda"))
    prim = Sdf.CreatePrimInLayer(layer, "/World")
    Sdf.AttributeSpec(prim, "inputs:map", Sdf.ValueTypeNames.Asset).default = Sdf.AssetPath("texture.png")
    layer.Save()
    report = audit([boundary / "root.usda"], boundary=boundary)
    assert not report["validated"]
    assert report["outside_boundary"] == [str(tmp_path / "texture.png")]


def test_wrapper_units_do_not_rescale_visuals_or_arm_mass(tmp_path):
    from demo.benchmark.prepare import normalize_wrapper

    stage = Usd.Stage.CreateNew(str(tmp_path / "wrapper.usda"))
    visual = UsdGeom.Xform.Define(stage, "/World/Visual")
    visual.AddScaleOp().Set((0.01, 0.01, 0.01))
    UsdPhysics.MassAPI.Apply(visual.GetPrim()).CreateMassAttr(5.22)
    stage.GetRootLayer().Save()
    original = (tmp_path / "wrapper.usda").read_bytes()
    backup = tmp_path / "backup.usda"
    normalize_wrapper(tmp_path / "wrapper.usda", backup)
    assert UsdGeom.GetStageMetersPerUnit(stage) == 1.0
    assert UsdGeom.GetStageUpAxis(stage) == "Z"
    assert tuple(visual.GetOrderedXformOps()[0].Get()) == pytest.approx((0.01, 0.01, 0.01))
    assert UsdPhysics.MassAPI(visual.GetPrim()).GetMassAttr().Get() == pytest.approx(5.22)
    assert backup.read_bytes() == original
    normalize_wrapper(tmp_path / "wrapper.usda", backup)
    assert backup.read_bytes() == original


def test_rejected_proxy_is_disabled_without_removing_ground_or_scaling(tmp_path):
    from demo.benchmark.prepare import normalize_wrapper

    stage = Usd.Stage.CreateNew(str(tmp_path / "wrapper.usda"))
    ground = UsdGeom.Cube.Define(stage, "/World/Ground").GetPrim()
    ceiling = UsdGeom.Cube.Define(stage, "/World/BadSlab").GetPrim()
    UsdPhysics.CollisionAPI.Apply(ground).CreateCollisionEnabledAttr(True)
    UsdPhysics.CollisionAPI.Apply(ceiling).CreateCollisionEnabledAttr(True)
    stage.GetRootLayer().Save()
    normalize_wrapper(tmp_path / "wrapper.usda", tmp_path / "backup.usda",
                      disabled_collision_proxies=("/World/BadSlab",))
    assert UsdPhysics.CollisionAPI(ground).GetCollisionEnabledAttr().Get() is True
    assert UsdPhysics.CollisionAPI(ceiling).GetCollisionEnabledAttr().Get() is False
    assert stage.GetPrimAtPath("/World/BadSlab")
