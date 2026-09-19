"""Synthetic USD regression checks; no installed scene, Isaac, GPU or network."""

from copy import deepcopy
import json

import numpy as np
import pytest

pytest.importorskip("pxr.Usd", reason="standalone OpenUSD is required")
from pxr import Gf, Usd, UsdGeom, UsdPhysics

from demo.benchmark.application import (
    _collision_proxy_render_policy_validated,
    _nvd_product_provenance_report,
)
from tools.assets import rebuild_nvd_products as products


def _provenance_fixture(tmp_path, monkeypatch):
    import yaml
    from PIL import Image

    scene_dir = tmp_path / "scene"
    scene_dir.mkdir()
    (scene_dir / "fixture_bench.usd").write_bytes(b"bench-v1")
    (tmp_path / "dependency.usda").write_text(
        '#usda 1.0\n\ndef Xform "Dependency" {}\n')
    (tmp_path / "provider.usd").write_text(
        '#usda 1.0\n\ndef Xform "Root" (references = @dependency.usda@) {}\n')
    Image.fromarray(np.array([[0, 254], [254, 0]], dtype=np.uint8)).save(
        scene_dir / "ground_map_0.6m.png")
    (scene_dir / "ground_map_0.6m.yaml").write_text(yaml.safe_dump({
        "image": "ground_map_0.6m.png",
        "resolution": 0.05,
        "origin": [-1.0, 2.0, 0.0],
        "negate": 0,
        "occupied_thresh": 0.65,
        "free_thresh": 0.196,
    }))
    voxels = np.zeros((24, 2, 2), dtype=bool)
    voxels[2, 0, 0] = True
    np.savez(
        scene_dir / "drone_voxels_0.25m.npz",
        voxels=voxels,
        x0=-1.0,
        y0=2.0,
        res=0.05,
        vox=0.25,
    )
    metadata_path = scene_dir / "bench_meta.json"
    metadata_path.write_text("{}\n")
    monkeypatch.setattr(products, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(products, "SCENES", {"fixture": "scene"})
    monkeypatch.setattr(products, "SOURCE_ROOT_USD", {"fixture": "../provider.usd"})
    manifest = products.build_product_provenance("fixture", asset_root=tmp_path)
    metadata = {"product_provenance": manifest}
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    return scene_dir, metadata


def test_product_provenance_binds_source_products_frames_and_generator(tmp_path, monkeypatch):
    _scene_dir, metadata = _provenance_fixture(tmp_path, monkeypatch)
    manifest = products.validate_product_provenance("fixture", asset_root=tmp_path)
    assert manifest == metadata["product_provenance"]
    assert manifest["generator"] == {
        "module": "tools.assets.rebuild_nvd_products",
        "algorithm_version": "1",
    }
    assert manifest["settings"]["map_frame"]["shape"] == [2, 2]
    assert manifest["settings"]["voxel_frame"]["shape"] == [24, 2, 2]
    assert manifest["source"]["provider_root_usd"]["sha256"]
    assert manifest["source"]["closure"]["file_count"] == 2
    assert set(manifest["products"]) == {
        "bench_usd", "ground_map_png", "ground_map_yaml", "drone_voxels_npz"}


def test_product_provenance_rejects_changed_product_bytes(tmp_path, monkeypatch):
    scene_dir, _metadata = _provenance_fixture(tmp_path, monkeypatch)
    with (scene_dir / "fixture_bench.usd").open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="products.bench_usd.sha256"):
        products.validate_product_provenance("fixture", asset_root=tmp_path)


def test_product_provenance_rejects_changed_source_dependency(tmp_path, monkeypatch):
    _scene_dir, _metadata = _provenance_fixture(tmp_path, monkeypatch)
    (tmp_path / "dependency.usda").write_text(
        '#usda 1.0\n\ndef Xform "ChangedDependency" {}\n')
    with pytest.raises(ValueError, match="source.closure"):
        products.validate_product_provenance("fixture", asset_root=tmp_path)


def test_demo_asset_gate_uses_resolver_asset_root_and_fails_closed(tmp_path, monkeypatch):
    scene_dir, _metadata = _provenance_fixture(tmp_path, monkeypatch)

    class Config:
        NVD_PRODUCT_PROVENANCE_SCENE = "fixture"

    assert _nvd_product_provenance_report(Config, tmp_path)["validated"]
    with (scene_dir / "fixture_bench.usd").open("ab") as stream:
        stream.write(b"changed")
    report = _nvd_product_provenance_report(Config, tmp_path)
    assert not report["validated"]
    assert "products.bench_usd.sha256" in report["error"]


def test_product_provenance_rejects_changed_source_hash(tmp_path, monkeypatch):
    _scene_dir, metadata = _provenance_fixture(tmp_path, monkeypatch)
    changed = deepcopy(metadata)
    changed["product_provenance"]["source"]["provider_root_usd"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="source.provider_root_usd.sha256"):
        products.validate_product_provenance(
            "fixture", metadata=changed, asset_root=tmp_path)


@pytest.mark.parametrize(
    ("status", "revision"),
    [("provider_release", None), ("local_candidate", "v0.1.0")],
)
def test_local_candidate_cannot_claim_provider_release(
        tmp_path, monkeypatch, status, revision):
    _scene_dir, metadata = _provenance_fixture(tmp_path, monkeypatch)
    changed = deepcopy(metadata)
    changed["product_provenance"]["publication_status"] = status
    changed["product_provenance"]["provider_revision"] = revision
    with pytest.raises(ValueError, match="local_candidate|provider revision"):
        products.validate_product_provenance(
            "fixture", metadata=changed, asset_root=tmp_path)


def test_installed_nvd_sidecars_match_local_products_and_house_has_voxels():
    for scene in ("warehouse_nvd", "modern_house_nvd"):
        manifest = products.validate_product_provenance(scene)
        assert manifest["publication_status"] == "local_candidate"
        assert manifest["provider_revision"] is None
    metadata = json.loads(
        (products.REPO_ROOT / products.SCENES["modern_house_nvd"] / "bench_meta.json")
        .read_text())
    drone = metadata["products"]["drone_voxels"]
    assert "skipped" not in drone
    assert drone["shape"] == [24, 404, 933]
    assert drone["occupied_voxels"] > 0
    assert drone["occupied_voxels"] == metadata["product_provenance"][
        "settings"]["voxel_frame"]["occupied_voxels"]


def _stage(path=None):
    stage = Usd.Stage.CreateNew(str(path)) if path else Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage, "Z")
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    UsdGeom.Xform.Define(stage, "/World")
    UsdGeom.Scope.Define(stage, "/World/BenchCollision")
    return stage


def test_rebuild_replaces_legacy_cube_floor_and_is_idempotent(tmp_path):
    path = tmp_path / "bench.usda"
    stage = _stage(path)
    floor = UsdGeom.Cube.Define(stage, products.GROUND_PATH)
    floor.CreateSizeAttr(1.0)
    UsdGeom.Xformable(floor).AddScaleOp().Set((70, 70, 0.01))
    UsdPhysics.RigidBodyAPI.Apply(floor.GetPrim())
    plate = UsdGeom.Cube.Define(stage, "/World/BenchCollision/zz_mezzanine")
    plate.CreateSizeAttr(12.0)
    UsdGeom.Xform.Define(stage, "/World/BenchCollision/walkway")
    UsdGeom.Cube.Define(stage, "/World/BenchCollision/w1000")
    legacy_names = ("f0001", "g0001", "b03_0001", "gh023_0001")
    for legacy_name in legacy_names:
        UsdGeom.Cube.Define(stage, f"/World/BenchCollision/{legacy_name}")

    for _ in range(2):
        products._rewrite_proxies(
            stage, path, [(0, 0, 0, 0)], [], 0.1, 0, 0,
            below_rects=[(-0.25, 1, 1, 1, 1)])
        assert products._check_ground(stage)["verified"]
        ground = stage.GetPrimAtPath(products.GROUND_PATH)
        assert not ground.GetAttribute("size")
        assert not ground.GetAttribute("xformOp:scale")
        assert plate.GetSizeAttr().Get() == 12.0
        assert stage.GetPrimAtPath("/World/BenchCollision/walkway")
        assert not stage.GetPrimAtPath("/World/BenchCollision/w1000")
        assert not any(stage.GetPrimAtPath(f"/World/BenchCollision/{name}")
                       for name in legacy_names)
        wall = stage.GetPrimAtPath("/World/BenchCollision/w0000")
        bench = stage.GetPrimAtPath("/World/BenchCollision")
        assert bench.GetTypeName() == "Xform"
        assert UsdGeom.Imageable(bench).ComputeVisibility() == UsdGeom.Tokens.invisible
        assert UsdGeom.Imageable(wall).ComputeVisibility() == UsdGeom.Tokens.invisible
        assert wall.HasAPI(UsdPhysics.CollisionAPI)
        assert UsdPhysics.CollisionAPI(wall).GetCollisionEnabledAttr().Get()
        below = stage.GetPrimAtPath("/World/BenchCollision/below_ground/b00000")
        assert below.HasAPI(UsdPhysics.CollisionAPI)
        bounds = UsdGeom.BBoxCache(0, ["default"]).ComputeWorldBound(
            below).ComputeAlignedRange()
        assert bounds.GetMin()[2] == pytest.approx(-0.25)
        assert bounds.GetMax()[2] == pytest.approx(0.0)

    reopened = Usd.Stage.Open(str(path), load=Usd.Stage.LoadNone)
    assert products._check_ground(reopened)["verified"]
    reopened_bench = reopened.GetPrimAtPath("/World/BenchCollision")
    reopened_wall = reopened.GetPrimAtPath("/World/BenchCollision/w0000")
    assert reopened_bench.GetTypeName() == "Xform"
    assert UsdGeom.Imageable(reopened_wall).ComputeVisibility() == UsdGeom.Tokens.invisible
    assert reopened_wall.HasAPI(UsdPhysics.CollisionAPI)


def test_asset_gate_rejects_invisible_attribute_on_untyped_proxy_root():
    stage = _stage()
    root = stage.GetPrimAtPath("/World/BenchCollision")
    UsdGeom.Imageable(root).CreateVisibilityAttr().Set(UsdGeom.Tokens.invisible)
    assert not _collision_proxy_render_policy_validated(stage.GetRootLayer())

    root.SetTypeName("Xform")
    assert _collision_proxy_render_policy_validated(stage.GetRootLayer())

    root.GetAttribute("visibility").Set(UsdGeom.Tokens.inherited)
    assert not _collision_proxy_render_policy_validated(stage.GetRootLayer())


@pytest.mark.parametrize("defect", ["missing", "cube", "disabled", "raised", "rigid_parent"])
def test_ground_check_rejects_unstable_floor_contract(defect):
    stage = _stage()
    products._define_ground_plane(stage)
    floor = stage.GetPrimAtPath(products.GROUND_PATH)
    if defect == "missing":
        stage.RemovePrim(products.GROUND_PATH)
    elif defect == "cube":
        floor.SetTypeName("Cube")
    elif defect == "disabled":
        UsdPhysics.CollisionAPI(floor).GetCollisionEnabledAttr().Set(False)
    elif defect == "raised":
        floor.GetAttribute("xformOp:translate").Set((0, 0, 0.01))
    else:
        UsdPhysics.RigidBodyAPI.Apply(stage.GetPrimAtPath("/World"))
    assert not products._check_ground(stage)["verified"]


def test_ground_check_cli_is_read_only(tmp_path, monkeypatch, capsys):
    path = tmp_path / "fixture_bench.usd"
    stage = _stage(path)
    products._define_ground_plane(stage)
    stage.GetRootLayer().Save()
    before = path.read_bytes()
    monkeypatch.setattr(products, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(products, "SCENES", {"fixture": "."})
    assert products.main(["--scene", "fixture", "--check-ground"]) == 0
    assert path.read_bytes() == before
    assert '"verified": true' in capsys.readouterr().out


def test_exact_rectangles_do_not_wall_in_concave_free_space():
    occupied = np.array([[1, 1, 1], [1, 0, 0], [1, 1, 0]], dtype=bool)
    rebuilt = np.zeros_like(occupied)
    for x0, y0, x1, y1 in products._decompose_rects(occupied):
        rebuilt[y0:y1 + 1, x0:x1 + 1] = True
    np.testing.assert_array_equal(rebuilt, occupied)


def test_height_aware_rectangles_keep_source_wall_height():
    occupied = np.ones((2, 2), dtype=bool)
    heights = np.array([[0.10, 0.10], [0.20, 0.20]])
    rects = products._decompose_rects(occupied, heights)
    assert (0, 0, 1, 0, 0.10) in rects
    assert (0, 1, 1, 1, 0.20) in rects


def test_explicit_furniture_meshes_use_native_collision_api():
    stage = _stage()
    mesh = UsdGeom.Mesh.Define(
        stage, "/World/SceneXR/Assets/EXT_Seating/Roxana_Sofa/Body")
    mesh.CreatePointsAttr([(-1, -1, 0), (1, -1, 0), (1, 1, 0)])
    mesh.CreateFaceVertexCountsAttr([3])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 2])

    collision_result = products._apply_native_mesh_collisions(stage)
    assert collision_result == {"applied": 1, "removed_stale": 0}
    prim = mesh.GetPrim()
    assert prim.HasAPI(UsdPhysics.CollisionAPI)
    assert prim.HasAPI(UsdPhysics.MeshCollisionAPI)
    assert UsdPhysics.MeshCollisionAPI(prim).GetApproximationAttr().Get() == (
        "convexDecomposition")
    _boxes, triangles, tree_triangles, report = products._collect_geometry(stage)
    assert not triangles
    assert not tree_triangles
    assert report["native_collision_meshes"] == 1


def test_mesh_audit_policy_separates_structure_decor_and_furniture():
    stage = _stage()
    window = UsdGeom.Mesh.Define(stage, "/World/SceneXR/Hye/Geometry/windows/Glass")
    wall = UsdGeom.Mesh.Define(stage, "/World/SceneXR/Hye/Geometry/Layer0/Mesh")
    chair = UsdGeom.Mesh.Define(
        stage, "/World/SceneXR/Assets/EXT_Seating/Roxana_FoldingChair/Base")
    tiny = UsdGeom.Mesh.Define(stage, "/World/SceneXR/Assets/INT_Shelves/tiny")

    assert products._mesh_collision_policy(window, size=(2, 2, 2))[0] == "ignore"
    assert products._mesh_collision_policy(wall, size=(2, 2, 2))[0] == "proxy"
    assert products._mesh_collision_policy(chair, size=(1, 1, 1))[0] == "native"
    assert products._mesh_collision_policy(tiny, size=(.1, .1, .1))[0] == "ignore"


def test_triangle_rasterizer_covers_vertical_projection_and_ignores_outside_geometry():
    mask = np.zeros((3, 3), dtype=bool)
    # The XY projection is a vertical line that misses every cell center.  The
    # old center-point test silently dropped the complete physical wall.
    vertical = np.array([[[0.9, 0.1, 0.1],
                          [0.9, 2.9, 0.1],
                          [0.9, 2.9, 0.9]]])
    products._rasterize_tris(
        vertical, mask, res=1.0, ox=0.0, oy=0.0, z_lo=0.0, z_hi=1.0)
    np.testing.assert_array_equal(mask[:, 0], np.ones(3, dtype=bool))
    assert not mask[:, 1:].any()

    outside = np.array([[[-10.0, 0.1, 0.1],
                         [-10.0, 2.9, 0.1],
                         [-10.0, 2.9, 0.9]]])
    products._rasterize_tris(
        outside, mask, res=1.0, ox=0.0, oy=0.0, z_lo=0.0, z_hi=1.0)
    np.testing.assert_array_equal(mask[:, 0], np.ones(3, dtype=bool))


def test_point_instancer_uses_instance_positions_not_prototype_coordinates():
    stage = _stage()
    instancer = UsdGeom.PointInstancer.Define(stage, "/World/Trees")
    prototype = UsdGeom.Cube.Define(stage, "/World/Trees/Prototype")
    prototype.CreateSizeAttr(2.0)
    instancer.CreatePrototypesRel().SetTargets([prototype.GetPath()])
    instancer.CreateProtoIndicesAttr([0, 0])
    instancer.CreatePositionsAttr([Gf.Vec3f(10, 0, 0), Gf.Vec3f(20, 0, 0)])

    boxes, triangles, tree_triangles, report = products._collect_geometry(stage)

    assert not triangles
    assert not tree_triangles
    assert report["point_instancers"] == 1
    assert report["point_instances"] == 2
    assert [(box[0], box[2]) for box in boxes] == pytest.approx(
        [(9.0, 11.0), (19.0, 21.0)])


def test_paint_tool_tree_uses_trunk_mesh_not_full_prototype():
    stage = _stage()
    instancer = UsdGeom.PointInstancer.Define(
        stage, "/World/SceneXR/PaintTool/Common_Apple/pointInstancer")
    prototype = UsdGeom.Xform.Define(
        stage, "/World/SceneXR/PaintTool/Common_Apple/pointInstancer/asset")
    bark = UsdGeom.Mesh.Define(stage, str(prototype.GetPath()) + "/Apple_bark_Mat")
    bark.CreatePointsAttr([(0, 0, 0), (.1, 0, 0), (0, .1, .2)])
    bark.CreateFaceVertexCountsAttr([3])
    bark.CreateFaceVertexIndicesAttr([0, 1, 2])
    UsdGeom.Cube.Define(stage, str(prototype.GetPath()) + "/Leaves")
    instancer.CreatePrototypesRel().SetTargets([prototype.GetPath()])
    instancer.CreateProtoIndicesAttr([0])
    instancer.CreatePositionsAttr([Gf.Vec3f(10, 5, 0.1)])

    boxes, triangles, tree_triangles, report = products._collect_geometry(stage)

    assert not boxes
    assert not triangles
    assert len(tree_triangles) == 1
    assert tree_triangles[0][:, :, 2].max() == pytest.approx(.3)
    assert report["point_instances"] == 1


def test_generated_voxel_boxes_cover_every_source_voxel():
    voxels = np.zeros((2, 7, 7), dtype=bool)
    voxels[0, 1, 1] = True
    voxels[1, 6, 6] = True
    boxes = products._voxel_boxes(voxels, z_origin=-0.5)
    assert products._uncovered_voxel_count(
        voxels, boxes, z_origin=-0.5) == 0
    assert {box[0] for box in boxes} == {-0.5, -0.25}


def test_out_of_frame_instance_box_does_not_wrap_through_negative_indices():
    mask = np.zeros((4, 4), dtype=bool)
    products._fill_box(mask, -10, -10, -5, -5, 1.0, 0.0, 0.0)
    assert not mask.any()
    products._fill_box(mask, -1, 1, 1, 2, 1.0, 0.0, 0.0)
    np.testing.assert_array_equal(mask[:, 0], [False, True, True, False])


def _map_fixture(tmp_path, monkeypatch, *, flipped=False):
    import yaml
    from PIL import Image

    ground = np.array([[1, 1, 0, 0], [0, 0, 0, 0], [0, 0, 0, 1]], dtype=bool)
    path = tmp_path / "fixture_bench.usd"
    stage = _stage(path)
    products._rewrite_proxies(stage, path, products._decompose_rects(ground), [], .1, -2, 3)
    products._write_png(tmp_path / "ground_map_0.6m.png", ground)
    if flipped:
        with Image.open(tmp_path / "ground_map_0.6m.png") as image:
            pixels = np.array(image)
        Image.fromarray(np.flipud(pixels)).save(tmp_path / "ground_map_0.6m.png")
    (tmp_path / "ground_map_0.6m.yaml").write_text(yaml.safe_dump({
        "image": "ground_map_0.6m.png", "resolution": .1,
        "origin": [-2., 3., 0.], "negate": 0, "free_thresh": .196,
        "occupied_thresh": .65,
    }))
    monkeypatch.setattr(products, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(products, "SCENES", {"fixture": "."})
    return ground


def test_png_writer_matches_usd_and_map_reader_world_coordinates(tmp_path, monkeypatch):
    from EAI.scenario_spec.geometry import FactoryMap

    ground = _map_fixture(tmp_path, monkeypatch)
    assert products.check_map_proxies("fixture")["verified"]
    occupancy = FactoryMap(tmp_path / "ground_map_0.6m.yaml")
    np.testing.assert_array_equal(occupancy.free, ~np.flipud(ground))


def test_orientation_repair_backs_up_and_is_idempotent(tmp_path, monkeypatch):
    _map_fixture(tmp_path, monkeypatch, flipped=True)
    png = tmp_path / "ground_map_0.6m.png"
    original = png.read_bytes()
    report = products.check_map_proxies("fixture")
    assert report["diagnosis"] == "png_vertical_serialization_bug"
    assert png.read_bytes() == original
    repaired = products.check_map_proxies("fixture", repair=True)
    assert repaired["verified"] and repaired["repaired"]
    from pathlib import Path
    assert (Path(repaired["backup_directory"]) / png.name).read_bytes() == original
    assert not products.check_map_proxies("fixture", repair=True)["repaired"]


def test_orientation_repair_refuses_unrelated_mismatch(tmp_path, monkeypatch):
    from PIL import Image

    _map_fixture(tmp_path, monkeypatch)
    png = tmp_path / "ground_map_0.6m.png"
    Image.fromarray(np.full((3, 4), 254, dtype=np.uint8)).save(png)
    original = png.read_bytes()
    with pytest.raises(ValueError, match="non-reflection"):
        products.check_map_proxies("fixture", repair=True)
    assert png.read_bytes() == original
