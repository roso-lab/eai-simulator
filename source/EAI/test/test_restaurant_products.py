import json
from pathlib import Path
import shutil

import numpy as np
from PIL import Image
import pytest


def _synthetic_source(path: Path) -> None:
    from pxr import Usd, UsdGeom

    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 0.01)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())

    floor = UsdGeom.Mesh.Define(
        stage,
        "/World/rh_Restaurant/Geometry/G_Arch/G_Floor/test_floor",
    )
    floor.CreatePointsAttr([(0, 0, 0), (200, 0, 0), (200, 200, 0), (0, 200, 0)])
    floor.CreateFaceVertexCountsAttr([4])
    floor.CreateFaceVertexIndicesAttr([0, 1, 2, 3])

    wall = UsdGeom.Mesh.Define(stage, "/World/TestWall")
    wall.CreatePointsAttr(
        [(95, 0, 0), (105, 0, 0), (105, 200, 0), (95, 200, 0),
         (95, 0, 100), (105, 0, 100), (105, 200, 100), (95, 200, 100)]
    )
    wall.CreateFaceVertexCountsAttr([4, 4, 4, 4, 4, 4])
    wall.CreateFaceVertexIndicesAttr(
        [0, 1, 2, 3, 4, 7, 6, 5, 0, 4, 5, 1,
         1, 5, 6, 2, 2, 6, 7, 3, 3, 7, 4, 0]
    )
    stage.GetRootLayer().Save()


def test_restaurant_product_core_writes_bound_map_and_collision_overlay(tmp_path):
    from pxr import Usd, UsdPhysics

    from tools.assets import build_restaurant_products as products

    source = tmp_path / "Restaurant_Only.usda"
    output = tmp_path / "products"
    _synthetic_source(source)
    report = products._write_products(
        source_usd=source,
        output_dir=output,
        source_record={"entry_sha256": "synthetic-test"},
    )

    assert report["publication_status"] == "local_candidate"
    assert report["provider_revision"] is None
    assert report["release_gates"] == {
        "navigation_ready": False,
        "execution_validated": False,
        "scoreable": False,
    }
    assert report["geometry"]["support_cells"] > 0
    assert report["geometry"]["raw_obstacle_cells"] > 0
    assert report["geometry"]["inflated_obstacle_cells"] > report["geometry"]["raw_obstacle_cells"]
    assert report["connectivity"]["component_count"] == 2

    pixels = np.asarray(Image.open(output / "restaurant_benchmark_map.png"))
    assert {0, 205, 254}.issubset(set(np.unique(pixels)))
    metadata = json.loads((output / "bench_meta.json").read_text())
    for name, row in metadata["products"].items():
        assert (output / name).is_file()
        assert len(row["sha256"]) == 64

    stage = Usd.Stage.Open(str(output / "restaurant_benchmark_bench.usda"))
    assert stage.GetDefaultPrim().GetPath().pathString == "/World"
    proxies = [
        prim for prim in stage.Traverse()
        if str(prim.GetPath()).startswith(
            "/World/BenchCollision/ground_obstacles/o"
        )
    ]
    assert proxies
    assert all(prim.HasAPI(UsdPhysics.CollisionAPI) for prim in proxies)

    map_path = output / "restaurant_benchmark_map.png"
    map_path.write_bytes(map_path.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="product hash mismatch"):
        products.validate_products(output)


def test_restaurant_product_frame_is_fixed_and_covers_the_source_bounds():
    from tools.assets import build_restaurant_products as products

    height, width = products._map_shape()
    assert (height, width) == (590, 730)
    assert products.MAP_ORIGIN_M[0] <= -0.237
    assert products.MAP_ORIGIN_M[1] <= -2.762
    assert products.MAP_MAX_M[0] >= 35.794
    assert products.MAP_MAX_M[1] >= 25.926


def test_restaurant_mesh_topology_rejects_malformed_nonempty_mesh(tmp_path):
    from pxr import Usd, UsdGeom

    from tools.assets import build_restaurant_products as products

    stage = Usd.Stage.CreateNew(str(tmp_path / "bad.usda"))
    mesh = UsdGeom.Mesh.Define(stage, "/BadMesh")
    mesh.CreatePointsAttr([(0, 0, 0), (1, 0, 0), (0, 1, 0)])
    mesh.CreateFaceVertexCountsAttr([3])
    mesh.CreateFaceVertexIndicesAttr([0, 1, 7])
    with pytest.raises(ValueError, match="out-of-range face index"):
        products._validate_mesh_topology(mesh.GetPrim())


def test_restaurant_checker_rejects_release_metadata_tampering(tmp_path):
    from tools.assets import build_restaurant_products as products

    source = tmp_path / "Restaurant_Only.usda"
    output = tmp_path / "products"
    _synthetic_source(source)
    products._write_products(
        source_usd=source,
        output_dir=output,
        source_record={"entry_sha256": "synthetic-test"},
    )
    metadata_path = output / "bench_meta.json"
    original = json.loads(metadata_path.read_text())
    mutations = {
        "schema": lambda value: value.update(schema="fake.schema"),
        "provider": lambda value: value.update(provider_revision="fake-revision"),
        "release gates": lambda value: value["release_gates"].update(scoreable=True),
        "settings": lambda value: value["settings"].update(resolution_m=0.25),
        "generator": lambda value: value["generator"].update(algorithm_version="fake"),
        "bundle": lambda value: value.update(bundle_manifest_sha256="0" * 64),
    }
    for _label, mutate in mutations.items():
        candidate = json.loads(json.dumps(original))
        mutate(candidate)
        metadata_path.write_text(json.dumps(candidate))
        with pytest.raises(ValueError, match="Restaurant"):
            products.validate_products(output)
        metadata_path.write_text(json.dumps(original))


def test_restaurant_checker_rejects_disabled_collision_and_broken_reference(tmp_path):
    from pxr import Usd, UsdPhysics

    from tools.assets import build_restaurant_products as products

    source = tmp_path / "Restaurant_Only.usda"
    output = tmp_path / "products"
    _synthetic_source(source)
    products._write_products(
        source_usd=source,
        output_dir=output,
        source_record={"entry_sha256": "synthetic-test"},
    )

    moved = tmp_path / "nested" / "moved-products"
    shutil.copytree(output, moved)
    with pytest.raises(ValueError, match="composition errors|unresolved"):
        products.validate_products(moved)

    stage = Usd.Stage.Open(str(output / products.PRODUCT_NAMES[0]))
    ground = stage.GetPrimAtPath("/World/BenchCollision/zz_ground")
    UsdPhysics.CollisionAPI(ground).GetCollisionEnabledAttr().Set(False)
    stage.GetRootLayer().Save()
    metadata_path = output / "bench_meta.json"
    metadata = json.loads(metadata_path.read_text())
    overlay_name = products.PRODUCT_NAMES[0]
    metadata["products"][overlay_name]["sha256"] = products._sha256(
        output / overlay_name
    )
    metadata["bundle_manifest_sha256"] = products._canonical_sha256(
        metadata["products"]
    )
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="ground Plane"):
        products.validate_products(output)

    UsdPhysics.CollisionAPI(ground).GetCollisionEnabledAttr().Set(True)
    proxy = next(
        prim
        for prim in stage.Traverse()
        if str(prim.GetPath()).startswith(
            "/World/BenchCollision/ground_obstacles/o"
        )
    )
    UsdPhysics.CollisionAPI(proxy).GetCollisionEnabledAttr().Set(False)
    stage.GetRootLayer().Save()
    metadata["products"][overlay_name]["sha256"] = products._sha256(
        output / overlay_name
    )
    metadata["bundle_manifest_sha256"] = products._canonical_sha256(
        metadata["products"]
    )
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match="proxy is not static enabled collision"):
        products.validate_products(output)


def test_restaurant_source_record_rejects_a_same_named_noncanonical_input(
    tmp_path, monkeypatch
):
    from tools.assets import build_restaurant_products as products

    source = tmp_path / "alternate" / "other" / "place" / "Restaurant_Only.usda"
    source.parent.mkdir(parents=True)
    _synthetic_source(source)
    monkeypatch.setattr(products.config, "SOURCE_SHA256", products._sha256(source))
    with pytest.raises(ValueError, match="input path"):
        products._source_record(source)
