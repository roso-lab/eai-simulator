#!/usr/bin/env python
"""Build local Restaurant benchmark collision and occupancy products.

The source package is authored in centimetres and remains immutable.  This
tool composes that package, rasterizes its actual mesh triangles into a fixed
metre-based frame, and writes a local-candidate bundle:

* ``restaurant_benchmark_bench.usda`` references the source scene at 0.01
  scale and adds invisible, static ground-band collision proxies;
* ``restaurant_benchmark_map.png`` is a ROS occupancy image with explicit
  occupied, free, and unknown cells;
* ``restaurant_benchmark_map.yaml`` fixes the map frame and thresholds; and
* ``bench_meta.json`` binds the source-closure policy, generator settings, and
  product hashes.

Generated products are runtime evidence, not tracked source.  Write them to an
explicit output directory and keep them at ``local_candidate`` status until
the exact bytes pass native raycast, navigation, and business-episode checks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from demo.restaurant_benchmark import config
from demo.restaurant_benchmark.scene_audit import audit_restaurant_scene
from tools.assets.rebuild_nvd_products import (
    _decompose_rects,
    _define_cube,
    _mesh_world_triangles,
    _rasterize_tris,
)


SCHEMA = "eai.restaurant-products.v1"
GENERATOR = "tools.assets.build_restaurant_products"
GENERATOR_VERSION = "1"

MAP_RESOLUTION_M = 0.05
MAP_ORIGIN_M = (-0.5, -3.0)
MAP_MAX_M = (36.0, 26.5)
SUPPORT_BAND_M = (-0.05, 0.06)
OBSTACLE_BAND_M = (0.05, 1.00)
CARTER_INFLATION_RADIUS_M = 0.40
COLLISION_HEIGHT_M = 1.00

FLOOR_PATH_PREFIXES = (
    "/World/rh_Restaurant/Geometry/G_Arch/G_Floor/",
    "/World/BaseBuilding/Geometry/Floors/",
)
FLOOR_PATHS = frozenset(
    {"/World/assemblies_context/assembly_BaseBuildingL0/Default_Floor_758"}
)
SOURCE_COLLISION_PATHS = (
    "/BaseBuilding/Geometry/Floors/Floor/Generic_150mm___Fin_A/"
    "Floors_549077/Ceramic_Tile_812",
    "/assemblies_context/assembly_BaseBuildingL0/Default_Floor_758",
)

PRODUCT_NAMES = (
    "restaurant_benchmark_bench.usda",
    "restaurant_benchmark_map.png",
    "restaurant_benchmark_map.yaml",
)
RELEASE_GATES = {
    "navigation_ready": False,
    "execution_validated": False,
    "scoreable": False,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _generator_record() -> dict[str, str]:
    return {
        "module": GENERATOR,
        "algorithm_version": GENERATOR_VERSION,
        "source_sha256": _sha256(Path(__file__).resolve()),
    }


def _settings() -> dict[str, Any]:
    return {
        "resolution_m": MAP_RESOLUTION_M,
        "origin_m": [*MAP_ORIGIN_M, 0.0],
        "maximum_m": list(MAP_MAX_M),
        "support_band_m": list(SUPPORT_BAND_M),
        "obstacle_band_m": list(OBSTACLE_BAND_M),
        "carter_inflation_radius_m": CARTER_INFLATION_RADIUS_M,
        "collision_height_m": COLLISION_HEIGHT_M,
        "floor_path_prefixes": list(FLOOR_PATH_PREFIXES),
        "floor_paths": sorted(FLOOR_PATHS),
    }


def _map_yaml_payload() -> dict[str, Any]:
    return {
        "image": "restaurant_benchmark_map.png",
        "resolution": MAP_RESOLUTION_M,
        "origin": [MAP_ORIGIN_M[0], MAP_ORIGIN_M[1], 0.0],
        "negate": 0,
        "occupied_thresh": 0.65,
        "free_thresh": 0.196,
    }


def _map_shape() -> tuple[int, int]:
    width = math.ceil((MAP_MAX_M[0] - MAP_ORIGIN_M[0]) / MAP_RESOLUTION_M)
    height = math.ceil((MAP_MAX_M[1] - MAP_ORIGIN_M[1]) / MAP_RESOLUTION_M)
    return height, width


def _is_floor_path(path: str) -> bool:
    return path in FLOOR_PATHS or any(
        path.startswith(prefix) for prefix in FLOOR_PATH_PREFIXES
    )


def _world_range_m(cache, prim) -> tuple[np.ndarray, np.ndarray]:
    rng = cache.ComputeWorldBound(prim).ComputeAlignedRange()
    minimum = np.asarray(rng.GetMin(), dtype=float) * config.SOURCE_METERS_PER_UNIT
    maximum = np.asarray(rng.GetMax(), dtype=float) * config.SOURCE_METERS_PER_UNIT
    return minimum, maximum


def _validate_mesh_topology(prim) -> bool:
    """Return false for an empty mesh and reject malformed nonempty topology."""
    points = prim.GetAttribute("points").Get() or []
    indices = prim.GetAttribute("faceVertexIndices").Get() or []
    counts = prim.GetAttribute("faceVertexCounts").Get() or []
    if not points and not indices and not counts:
        return False
    path = prim.GetPath()
    if not points or not indices or not counts:
        raise ValueError(f"incomplete mesh topology: {path}")
    count_values = np.asarray(counts, dtype=np.int64)
    index_values = np.asarray(indices, dtype=np.int64)
    if (count_values < 3).any() or int(count_values.sum()) != len(index_values):
        raise ValueError(f"invalid face counts for mesh: {path}")
    if index_values.min() < 0 or index_values.max() >= len(points):
        raise ValueError(f"out-of-range face index for mesh: {path}")
    return True


def _collect_masks(stage) -> tuple[np.ndarray, np.ndarray, dict[str, int]]:
    """Rasterize support and ground-obstacle masks from composed mesh geometry."""
    from pxr import Usd, UsdGeom

    shape = _map_shape()
    support = np.zeros(shape, dtype=bool)
    obstacle = np.zeros(shape, dtype=bool)
    cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy],
        useExtentsHint=True,
    )
    counts = {
        "meshes_seen": 0,
        "floor_meshes_rasterized": 0,
        "obstacle_meshes_rasterized": 0,
        "triangles_rasterized": 0,
        "empty_meshes_skipped": 0,
        "invisible_meshes_skipped": 0,
        "out_of_band_meshes_skipped": 0,
    }

    for prim in stage.Traverse():
        if prim.GetTypeName() != "Mesh":
            continue
        counts["meshes_seen"] += 1
        if not _validate_mesh_topology(prim):
            counts["empty_meshes_skipped"] += 1
            continue
        imageable = UsdGeom.Imageable(prim)
        if imageable and imageable.ComputeVisibility() == UsdGeom.Tokens.invisible:
            counts["invisible_meshes_skipped"] += 1
            continue
        purpose = imageable.ComputePurpose() if imageable else UsdGeom.Tokens.default_
        if purpose == UsdGeom.Tokens.guide:
            counts["invisible_meshes_skipped"] += 1
            continue

        path = str(prim.GetPath())
        floor = _is_floor_path(path)
        minimum, maximum = _world_range_m(cache, prim)
        support_candidate = floor and (
            maximum[2] > SUPPORT_BAND_M[0] and minimum[2] < SUPPORT_BAND_M[1]
        )
        obstacle_candidate = (not floor) and (
            maximum[2] > OBSTACLE_BAND_M[0] and minimum[2] < OBSTACLE_BAND_M[1]
        )
        if not support_candidate and not obstacle_candidate:
            counts["out_of_band_meshes_skipped"] += 1
            continue

        triangles = _mesh_world_triangles(prim)
        if triangles is None or not len(triangles):
            continue
        triangles = np.asarray(triangles, dtype=float) * config.SOURCE_METERS_PER_UNIT
        counts["triangles_rasterized"] += int(len(triangles))
        if support_candidate:
            _rasterize_tris(
                triangles,
                support,
                res=MAP_RESOLUTION_M,
                ox=MAP_ORIGIN_M[0],
                oy=MAP_ORIGIN_M[1],
                z_lo=SUPPORT_BAND_M[0],
                z_hi=SUPPORT_BAND_M[1],
            )
            counts["floor_meshes_rasterized"] += 1
        if obstacle_candidate:
            _rasterize_tris(
                triangles,
                obstacle,
                res=MAP_RESOLUTION_M,
                ox=MAP_ORIGIN_M[0],
                oy=MAP_ORIGIN_M[1],
                z_lo=OBSTACLE_BAND_M[0],
                z_hi=OBSTACLE_BAND_M[1],
            )
            counts["obstacle_meshes_rasterized"] += 1

    obstacle &= support
    if not support.any():
        raise ValueError("restaurant floor policy produced no supported cells")
    if not obstacle.any():
        raise ValueError("restaurant ground band produced no obstacle cells")
    return support, obstacle, counts


def _disk_structure(radius_cells: int) -> np.ndarray:
    axis = np.arange(-radius_cells, radius_cells + 1)
    x, y = np.meshgrid(axis, axis)
    return x * x + y * y <= radius_cells * radius_cells


def _derive_navigation_masks(
    support: np.ndarray, obstacle: np.ndarray
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    from scipy import ndimage

    radius_cells = math.ceil(CARTER_INFLATION_RADIUS_M / MAP_RESOLUTION_M)
    inflated = ndimage.binary_dilation(
        obstacle, structure=_disk_structure(radius_cells)
    ) & support
    free = support & ~inflated
    labels, component_count = ndimage.label(
        free, structure=np.ones((3, 3), dtype=np.uint8)
    )
    sizes = np.bincount(labels.ravel())[1:]
    largest = int(sizes.max()) if sizes.size else 0
    if not free.any() or largest == 0:
        raise ValueError("Carter-inflated restaurant map contains no navigable component")
    connectivity = {
        "component_count": int(component_count),
        "largest_component_cells": largest,
        "largest_component_fraction_of_free": float(largest / free.sum()),
        "free_cells": int(free.sum()),
    }
    return inflated, free, connectivity


def _write_map(path: Path, *, support: np.ndarray, obstacle: np.ndarray) -> None:
    from PIL import Image

    pixels = np.full(support.shape, 205, dtype=np.uint8)
    pixels[support] = 254
    pixels[obstacle] = 0
    Image.fromarray(np.flipud(pixels)).save(path)


def _write_yaml(path: Path) -> None:
    import yaml

    path.write_text(
        yaml.safe_dump(_map_yaml_payload(), sort_keys=False), encoding="utf-8"
    )


def _write_overlay(path: Path, source_usd: Path, obstacle: np.ndarray) -> int:
    from pxr import Sdf, Usd, UsdGeom, UsdPhysics

    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())

    visual = UsdGeom.Xform.Define(stage, "/World/Visual")
    reference_path = os.path.relpath(source_usd, path.parent).replace(os.sep, "/")
    visual.GetPrim().GetReferences().AddReference(reference_path)
    scale_ops = [
        op
        for op in visual.GetOrderedXformOps()
        if op.GetOpType() == UsdGeom.XformOp.TypeScale
    ]
    scale_op = scale_ops[-1] if scale_ops else visual.AddScaleOp()
    scale_op.Set(config.RUNTIME_SCALE)

    for source_path in SOURCE_COLLISION_PATHS:
        inherited = stage.OverridePrim(f"/World/Visual{source_path}")
        UsdPhysics.CollisionAPI(inherited).CreateCollisionEnabledAttr(False)

    collision = UsdGeom.Xform.Define(stage, "/World/BenchCollision")
    UsdGeom.Imageable(collision).CreateVisibilityAttr().Set(UsdGeom.Tokens.invisible)
    ground = stage.DefinePrim("/World/BenchCollision/zz_ground", "Plane")
    ground.CreateAttribute("axis", Sdf.ValueTypeNames.Token, custom=False).Set("Z")
    UsdPhysics.CollisionAPI.Apply(ground).CreateCollisionEnabledAttr(True)
    UsdGeom.Scope.Define(stage, "/World/BenchCollision/ground_obstacles")
    rectangles = _decompose_rects(obstacle)
    for index, (c0, r0, c1, r1) in enumerate(rectangles):
        x0 = MAP_ORIGIN_M[0] + c0 * MAP_RESOLUTION_M
        y0 = MAP_ORIGIN_M[1] + r0 * MAP_RESOLUTION_M
        x1 = MAP_ORIGIN_M[0] + (c1 + 1) * MAP_RESOLUTION_M
        y1 = MAP_ORIGIN_M[1] + (r1 + 1) * MAP_RESOLUTION_M
        _define_cube(
            stage,
            f"/World/BenchCollision/ground_obstacles/o{index:05d}",
            (x0, y0),
            (x1, y1),
            0.0,
            COLLISION_HEIGHT_M,
        )
    stage.GetRootLayer().Save()

    reopened = Usd.Stage.Open(str(path))
    if reopened is None or reopened.GetDefaultPrim().GetPath() != Sdf.Path("/World"):
        raise ValueError("generated Restaurant overlay cannot be reopened")
    return len(rectangles)


def _source_record(source_usd: Path) -> dict[str, Any]:
    source_usd = source_usd.resolve()
    if _sha256(source_usd) != config.SOURCE_SHA256:
        raise ValueError("Restaurant input SHA does not match the frozen source")
    usd_root = source_usd.parents[2]
    canonical_source = (usd_root / config.USD_RELATIVE_PATH).resolve()
    if source_usd != canonical_source:
        raise ValueError("Restaurant input path is not the canonical scene entry")
    report = audit_restaurant_scene(usd_root)
    if Path(report.get("source", "")).resolve() != source_usd:
        raise ValueError("Restaurant audit did not inspect the rasterized source")
    if report.get("source_sha256") != config.SOURCE_SHA256:
        raise ValueError("Restaurant source SHA does not match the frozen policy")
    effective = report.get("effective_closure", {})
    if not effective.get("effective_asset_closure_validated"):
        raise ValueError("Restaurant effective asset closure is not validated")
    return {
        "entry_path": config.USD_RELATIVE_PATH,
        "entry_sha256": report["source_sha256"],
        "source_meters_per_unit": config.SOURCE_METERS_PER_UNIT,
        "runtime_scale": list(config.RUNTIME_SCALE),
        "raw_unresolved_dependency_count": report["unresolved_dependency_count"],
        "unresolved_manifest_sha256": effective["unresolved_manifest_sha256"],
        "used_layer_count": effective["used_layer_count"],
        "used_layer_manifest_sha256": effective["used_layer_manifest_sha256"],
        "effective_asset_closure_validated": True,
    }


def _write_products(
    *,
    source_usd: Path,
    output_dir: Path,
    source_record: Mapping[str, Any],
) -> dict[str, Any]:
    from pxr import Usd

    stage = Usd.Stage.Open(str(source_usd))
    if stage is None:
        raise ValueError(f"cannot open Restaurant source stage: {source_usd}")
    support, obstacle, collection = _collect_masks(stage)
    inflated, free, connectivity = _derive_navigation_masks(support, obstacle)

    output_dir.mkdir(parents=True, exist_ok=True)
    overlay = output_dir / PRODUCT_NAMES[0]
    map_png = output_dir / PRODUCT_NAMES[1]
    map_yaml = output_dir / PRODUCT_NAMES[2]
    proxy_count = _write_overlay(overlay, source_usd, obstacle)
    _write_map(map_png, support=support, obstacle=obstacle)
    _write_yaml(map_yaml)

    products = {
        name: {"path": name, "sha256": _sha256(output_dir / name)}
        for name in PRODUCT_NAMES
    }
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "publication_status": "local_candidate",
        "provider_revision": None,
        "generator": _generator_record(),
        "source": dict(source_record),
        "settings": _settings(),
        "geometry": {
            **collection,
            "shape": list(support.shape),
            "support_cells": int(support.sum()),
            "raw_obstacle_cells": int(obstacle.sum()),
            "inflated_obstacle_cells": int(inflated.sum()),
            "collision_proxy_count": int(proxy_count),
        },
        "connectivity": connectivity,
        "release_gates": RELEASE_GATES,
        "products": products,
    }
    report["bundle_manifest_sha256"] = _canonical_sha256(products)
    metadata = output_dir / "bench_meta.json"
    metadata.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    validate_products(output_dir)
    return report


def validate_products(
    output_dir: str | Path, *, source_usd: str | Path | None = None
) -> dict[str, Any]:
    """Require exact raw-map/proxy correspondence and wrapper physics policy."""
    from PIL import Image
    from pxr import Usd, UsdGeom, UsdPhysics
    import yaml

    root = Path(output_dir).expanduser().resolve()
    metadata = json.loads((root / "bench_meta.json").read_text(encoding="utf-8"))
    if metadata.get("schema") != SCHEMA:
        raise ValueError("Restaurant product schema mismatch")
    if metadata.get("publication_status") != "local_candidate":
        raise ValueError("Restaurant products must remain a local_candidate")
    if metadata.get("provider_revision") is not None:
        raise ValueError("Restaurant local candidate cannot name a provider revision")
    if metadata.get("release_gates") != RELEASE_GATES:
        raise ValueError("Restaurant release gates must remain false")
    if metadata.get("generator") != _generator_record():
        raise ValueError("Restaurant generator record mismatch")
    if metadata.get("settings") != _settings():
        raise ValueError("Restaurant generator settings mismatch")
    if source_usd is not None:
        source = Path(source_usd).expanduser().resolve()
        if metadata.get("source") != _source_record(source):
            raise ValueError("Restaurant product source provenance mismatch")
    product_rows = metadata.get("products", {})
    if set(product_rows) != set(PRODUCT_NAMES):
        raise ValueError("Restaurant product inventory mismatch")
    for name in PRODUCT_NAMES:
        row = product_rows[name]
        if row.get("path") != name:
            raise ValueError(f"Restaurant product path mismatch: {name}")
        if _sha256(root / name) != row.get("sha256"):
            raise ValueError(f"Restaurant product hash mismatch: {name}")
    if metadata.get("bundle_manifest_sha256") != _canonical_sha256(product_rows):
        raise ValueError("Restaurant bundle manifest mismatch")

    map_yaml = yaml.safe_load((root / PRODUCT_NAMES[2]).read_text(encoding="utf-8"))
    if map_yaml != _map_yaml_payload():
        raise ValueError("Restaurant map YAML contract mismatch")

    pixels = np.flipud(np.asarray(Image.open(root / PRODUCT_NAMES[1]).convert("L")))
    if pixels.shape != _map_shape():
        raise ValueError("Restaurant map shape mismatch")
    map_obstacle = pixels == 0
    if not set(np.unique(pixels)).issubset({0, 205, 254}):
        raise ValueError("Restaurant map contains values outside occupied/free/unknown")
    support = pixels != 205
    inflated, _free, connectivity = _derive_navigation_masks(support, map_obstacle)
    if metadata.get("connectivity") != connectivity:
        raise ValueError("Restaurant connectivity evidence mismatch")
    geometry = metadata.get("geometry", {})
    expected_geometry = {
        "shape": list(pixels.shape),
        "support_cells": int(support.sum()),
        "raw_obstacle_cells": int(map_obstacle.sum()),
        "inflated_obstacle_cells": int(inflated.sum()),
    }
    for key, value in expected_geometry.items():
        if geometry.get(key) != value:
            raise ValueError(f"Restaurant geometry evidence mismatch: {key}")

    stage = Usd.Stage.Open(str(root / PRODUCT_NAMES[0]))
    if stage is None:
        raise ValueError("Restaurant overlay cannot be reopened")
    if (
        stage.GetDefaultPrim().GetPath().pathString != "/World"
        or UsdGeom.GetStageUpAxis(stage) != UsdGeom.Tokens.z
        or not np.isclose(UsdGeom.GetStageMetersPerUnit(stage), 1.0)
    ):
        raise ValueError("Restaurant overlay must remain a metre/Z-up World stage")
    composition_errors = [str(error) for error in stage.GetCompositionErrors()]
    if composition_errors:
        raise ValueError(
            "Restaurant overlay has composition errors: " + "; ".join(composition_errors)
        )
    visual = stage.GetPrimAtPath("/World/Visual")
    if not visual or not visual.GetChildren():
        raise ValueError("Restaurant visual source reference is unresolved")
    expected_visual_matrix = np.diag([*config.RUNTIME_SCALE, 1.0])
    visual_matrix = np.asarray(
        UsdGeom.Xformable(visual).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    )
    if not np.allclose(visual_matrix, expected_visual_matrix, atol=1e-9, rtol=0):
        raise ValueError("Restaurant visual source transform mismatch")
    if source_usd is not None:
        expected_source = Path(source_usd).expanduser().resolve()
        used_layers = {
            Path(layer.realPath).resolve()
            for layer in stage.GetUsedLayers()
            if layer.realPath
        }
        if expected_source not in used_layers:
            raise ValueError("Restaurant overlay does not compose the supplied source")
    collision_root = stage.GetPrimAtPath("/World/BenchCollision")
    if (
        not collision_root
        or collision_root.GetTypeName() != "Xform"
        or UsdGeom.Imageable(collision_root).ComputeVisibility()
        != UsdGeom.Tokens.invisible
    ):
        raise ValueError("Restaurant BenchCollision must be an invisible Xform")

    def has_rigid_body_ancestor(prim) -> bool:
        current = prim
        while current and not current.IsPseudoRoot():
            if current.HasAPI(UsdPhysics.RigidBodyAPI):
                return True
            current = current.GetParent()
        return False

    ground = stage.GetPrimAtPath("/World/BenchCollision/zz_ground")
    if (
        not ground
        or ground.GetTypeName() != "Plane"
        or not ground.HasAPI(UsdPhysics.CollisionAPI)
        or UsdPhysics.CollisionAPI(ground).GetCollisionEnabledAttr().Get() is not True
        or ground.GetAttribute("axis").Get() != "Z"
    ):
        raise ValueError("Restaurant overlay lacks the static Z-up ground Plane")
    if has_rigid_body_ancestor(ground):
        raise ValueError("Restaurant ground Plane must remain static")
    ground_matrix = np.asarray(
        UsdGeom.Xformable(ground).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
    )
    if not np.allclose(ground_matrix, np.eye(4), atol=1e-9, rtol=0):
        raise ValueError("Restaurant ground Plane must remain at the identity transform")
    for source_path in SOURCE_COLLISION_PATHS:
        inherited = stage.GetPrimAtPath(f"/World/Visual{source_path}")
        enabled = UsdPhysics.CollisionAPI(inherited).GetCollisionEnabledAttr().Get()
        if enabled is not False:
            raise ValueError(f"inherited Restaurant floor collider remains enabled: {source_path}")

    proxy_obstacle = np.zeros(map_obstacle.shape, dtype=bool)
    prefix = "/World/BenchCollision/ground_obstacles/o"
    proxy_count = 0
    for prim in stage.Traverse():
        if not str(prim.GetPath()).startswith(prefix):
            continue
        if (
            prim.GetTypeName() != "Cube"
            or not prim.HasAPI(UsdPhysics.CollisionAPI)
            or UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is not True
            or has_rigid_body_ancestor(prim)
        ):
            raise ValueError(f"Restaurant proxy is not static enabled collision: {prim.GetPath()}")
        matrix = np.asarray(
            UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(Usd.TimeCode.Default())
        )
        center_x, center_y = matrix[3, 0], matrix[3, 1]
        size_x = np.linalg.norm(matrix[0, :3])
        size_y = np.linalg.norm(matrix[1, :3])
        center_z = matrix[3, 2]
        size_z = np.linalg.norm(matrix[2, :3])
        if not (
            np.isclose(center_z - size_z / 2, 0.0, atol=1e-9)
            and np.isclose(center_z + size_z / 2, COLLISION_HEIGHT_M, atol=1e-9)
        ):
            raise ValueError(f"Restaurant proxy has an invalid Z extent: {prim.GetPath()}")
        c0 = round((center_x - size_x / 2 - MAP_ORIGIN_M[0]) / MAP_RESOLUTION_M)
        c1 = round((center_x + size_x / 2 - MAP_ORIGIN_M[0]) / MAP_RESOLUTION_M)
        r0 = round((center_y - size_y / 2 - MAP_ORIGIN_M[1]) / MAP_RESOLUTION_M)
        r1 = round((center_y + size_y / 2 - MAP_ORIGIN_M[1]) / MAP_RESOLUTION_M)
        if not (0 <= c0 < c1 <= proxy_obstacle.shape[1]
                and 0 <= r0 < r1 <= proxy_obstacle.shape[0]):
            raise ValueError(f"Restaurant proxy lies outside the map: {prim.GetPath()}")
        proxy_obstacle[r0:r1, c0:c1] = True
        proxy_count += 1
    if proxy_count == 0 or not np.array_equal(proxy_obstacle, map_obstacle):
        mismatch = int(np.count_nonzero(proxy_obstacle ^ map_obstacle))
        raise ValueError(f"Restaurant map/proxy mismatch: {mismatch} cells")
    if geometry.get("collision_proxy_count") != proxy_count:
        raise ValueError("Restaurant collision proxy count evidence mismatch")
    return {
        "validated": True,
        "proxy_count": proxy_count,
        "occupied_cells": int(map_obstacle.sum()),
    }


def build_products(source_usd: str | Path, output_dir: str | Path) -> dict[str, Any]:
    """Build a fail-closed local candidate from the frozen Restaurant source."""
    source = Path(source_usd).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    output = Path(output_dir).expanduser().resolve()
    report = _write_products(
        source_usd=source,
        output_dir=output,
        source_record=_source_record(source),
    )
    validate_products(output, source_usd=source)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-usd", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--check", action="store_true", help="validate an existing output directory"
    )
    args = parser.parse_args(argv)
    report = (
        validate_products(args.output_dir, source_usd=args.source_usd)
        if args.check
        else build_products(args.source_usd, args.output_dir)
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
