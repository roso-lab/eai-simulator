"""Scene variants keep labels attached to existing geometry with relative dependencies."""

import hashlib
from pathlib import Path

import pytest

from demo.factory_benchmark.inventory_scene import author_variant, configure_variant
from demo.factory_benchmark.prim_bindings import FACTORY_PRIM_BINDINGS
from demo.factory_benchmark.label_geometry import fit_surface_label, validate_label_support


@pytest.mark.parametrize("state", ["visible", "covered", "absent"])
def test_label_variant_preserves_base_scene_and_declares_supported_geometry(tmp_path, state):
    from pxr import Usd, UsdGeom

    base = tmp_path / "base.usda"
    stage = Usd.Stage.CreateNew(str(base))
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    parent_path = FACTORY_PRIM_BINDINGS["inspection_target_box"]["prim_path"]
    parent = UsdGeom.Xform.Define(stage, parent_path)
    parent.AddTranslateOp().Set((-9, -6, 2))
    box = UsdGeom.Mesh.Define(stage, parent_path + "/Geometry")
    box.CreatePointsAttr([(0.25, -0.25, 0), (0.25, 0.25, 0),
                          (0.25, 0.25, 0.31), (0.25, -0.25, 0.31)])
    box.CreateFaceVertexIndicesAttr([0, 1, 2, 0, 2, 3])
    box.CreateFaceVertexCountsAttr([3, 3])
    box.CreateSubdivisionSchemeAttr("none")
    stage.GetRootLayer().Save()
    original = base.read_bytes()
    directory = tmp_path / "variant"
    directory.mkdir()
    output = directory / "inventory.usda"
    manifest = author_variant(base, output, marker_id=17, state=state)
    assert base.read_bytes() == original
    assert manifest["base_sha256"] == hashlib.sha256(original).hexdigest()
    assert manifest["world_center_m"][0] == pytest.approx(-8.749)
    assert manifest["version"] == 2
    assert manifest["marker_side_m"] == 0.09
    overlay = Usd.Stage.Open(str(output))
    assert list(overlay.GetRootLayer().subLayerPaths) == ["../base.usda"]
    assert overlay.GetDefaultPrim().GetPath() == "/World"
    assert validate_label_support(stage, overlay, manifest)["validated"]
    marker = overlay.GetPrimAtPath(manifest["label_prim_path"])
    if state == "absent":
        assert not marker
    else:
        shapes = [prim for prim in Usd.PrimRange(marker) if prim.GetTypeName() == "Mesh"]
        assert len(shapes) == (3 if state == "covered" else 2)
        assert all(not prim.GetAppliedSchemas() or list(prim.GetAppliedSchemas()) == ["MaterialBindingAPI"]
                   for prim in shapes)
        ink = UsdGeom.Mesh(overlay.GetPrimAtPath(manifest["label_prim_path"] + "/Ink"))
        points = ink.GetPointsAttr().Get()
        points[0] += (0.01, 0, 0)
        ink.GetPointsAttr().Set(points)
        with pytest.raises(ValueError, match="detached"):
            validate_label_support(stage, overlay, manifest)
    with pytest.raises(FileExistsError):
        author_variant(base, output, marker_id=17)


def test_variant_rejects_changed_asset_before_loading(tmp_path):
    from types import SimpleNamespace

    path = tmp_path / "variant.usda"
    path.write_text("changed")
    config = SimpleNamespace(scene=SimpleNamespace(world=SimpleNamespace(spawn=SimpleNamespace(usd_path="base"))))
    with pytest.raises(ValueError, match="changed"):
        configure_variant(config, path=path, sha256="a" * 64)
    assert config.scene.world.spawn.usd_path == "base"


def test_bbox_extent_cannot_substitute_for_actual_surface_support():
    triangle = {"prim_path": "/box", "face_index": 0,
                "vertices_local_m": [[0.25, 0, 0], [0.25, 0.5, 0], [0.25, 0, 0.3]]}
    with pytest.raises(ValueError, match="no complete support"):
        fit_surface_label([triangle], 0.24)
    support = fit_surface_label([triangle], 0.12)
    assert support["method"] == "whole_square_inside_single_source_triangle"


def test_triangle_fit_follows_sloped_plane_without_scaling_print():
    import numpy as np

    triangle = {"prim_path": "/box", "face_index": 0,
                "vertices_local_m": [[0.245, 0, 0], [0.25, 0.5, 0], [0.25, 0, 0.3]]}
    support = fit_surface_label([triangle], 0.12)
    normal, horizontal, vertical = [np.array(support[key]) for key in ("normal", "horizontal", "vertical")]
    assert np.array([normal, horizontal, vertical]) @ np.array([normal, horizontal, vertical]).T == pytest.approx(np.eye(3))
    assert np.dot(np.array(support["local_center_m"]) - triangle["vertices_local_m"][0], normal) == pytest.approx(0.001)


def test_inventory_pairing_excludes_only_the_declared_overlay():
    from copy import deepcopy
    from demo.factory_benchmark.run_inventory_matrix import paired_runtime_signature

    report = {"execution_provenance": {"runtime": {
        "files": {"scene": "a", "robot": "b", "overlay": "c"}, "usd_roots": ["robot", "overlay"],
        "effective": {"control_dt_s": 0.02}, "occupancy_sha256": "d", "plan_sha256": "e"}},
        "inventory_detection": {"world_variant": {"path": "overlay", "sha256": "c",
            "marker_side_m": 0.09, "surface_support": {"face_index": 225}, "base_sha256": "a"}}}
    second = deepcopy(report)
    second["execution_provenance"]["runtime"]["files"]["overlay"] = "new"
    second["inventory_detection"]["world_variant"]["sha256"] = "new"
    assert paired_runtime_signature(report) == paired_runtime_signature(second)
    second["execution_provenance"]["runtime"]["files"]["robot"] = "changed"
    assert paired_runtime_signature(report) != paired_runtime_signature(second)
    del second["execution_provenance"]["runtime"]["files"]["overlay"]
    with pytest.raises(ValueError, match="file closure"):
        paired_runtime_signature(second)


def test_missing_inspection_view_cannot_count_as_all_views_localized():
    from demo.factory_benchmark.inspect_inventory import summarize_view_evidence

    views = {"aerial_view": "aerial/camera", "ground_view": "ground/camera"}
    aerial = {"observation_id": "aerial_view:frame:1", "view": "aerial/camera", "localization_validated": True}
    result = summarize_view_evidence([aerial], views)
    assert result["missing_view_nodes"] == ["ground_view"]
    assert not result["all_views_localized"]
    assert not result["all_views_observed"]
    ground = {"observation_id": "ground_view:frame:2", "view": "ground/camera", "localization_validated": True}
    assert not summarize_view_evidence([aerial, ground], views)["all_views_localized"]
    assert summarize_view_evidence([aerial, ground], views, ["aerial_view", "ground_view"])["all_views_localized"]
    with pytest.raises(ValueError, match="declared inspection view"):
        summarize_view_evidence([{**ground, "view": "aerial/camera"}], views)
