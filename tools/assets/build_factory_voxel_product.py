#!/usr/bin/env python
"""Build the Factory's conservative aerial navigation voxel product.

The product is deliberately derived from collision-enabled meshes in the
released Factory USD.  Each mesh contributes its world AABB to every 0.25 m
layer it intersects.  This over-approximates thin or irregular geometry, but
it gives the simple aerial baseline a reproducible 3-D collision envelope.
The ground occupancy map remains a separate 0--0.8 m proxy in
``demo.benchmark.aerial_navigation``; the 3-D product must never be replaced
by that 2-D map.

The output NPZ uses the shared ``voxels[z, y, x]`` schema.  A sidecar records
the exact USD/map hashes and all generation parameters so a provider release
can bind the product to its source closure.  This tool does not claim a
flight-safety certificate, sensor visibility, or tracking performance.

Usage::

    python -m tools.assets.build_factory_voxel_product --write
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
from pathlib import Path

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_USD = REPO_ROOT / "usd/scene/factory/factory.usd"
DEFAULT_MAP = REPO_ROOT / "usd/scene/factory/factory_map.yaml"
DEFAULT_PRODUCT = REPO_ROOT / "usd/scene/factory/drone_voxels_0.25m.npz"
DEFAULT_METADATA = REPO_ROOT / "usd/scene/factory/drone_voxels_0.25m.meta.json"
VOXEL_SIZE_M = 0.25
SCHEMA = "eai.benchmark.aerial_voxel_product"
VERSION = 1


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _map_frame(map_yaml: Path) -> tuple[dict, float, float, float, tuple[int, int]]:
    import yaml
    from PIL import Image

    config = yaml.safe_load(map_yaml.read_text())
    if not isinstance(config, dict):
        raise ValueError("Factory map YAML must be an object")
    resolution = float(config["resolution"])
    origin = tuple(float(value) for value in config["origin"])
    if (not math.isfinite(resolution) or resolution <= 0 or len(origin) != 3
            or not all(math.isfinite(value) for value in origin)
            or not math.isclose(origin[2], 0.0, abs_tol=1e-9)):
        raise ValueError("Factory aerial product requires a finite unrotated map frame")
    image_path = map_yaml.parent / str(config["image"])
    with Image.open(image_path) as image:
        shape = tuple(np.asarray(image.convert("L")).shape)
    if len(shape) != 2 or min(shape) < 1:
        raise ValueError("Factory occupancy image must be nonempty")
    return config, resolution, origin[0], origin[1], shape


def _collision_boxes(usd_path: Path) -> tuple[list[dict], float]:
    from pxr import Usd, UsdGeom, UsdPhysics

    # Load the full composed stage.  LoadNone only sees the root layer's
    # partial prim index and would silently omit the referenced scene meshes.
    stage = Usd.Stage.Open(str(usd_path))
    if not stage:
        raise ValueError(f"cannot open Factory USD: {usd_path}")
    if UsdGeom.GetStageUpAxis(stage) != "Z" or not math.isclose(
            float(UsdGeom.GetStageMetersPerUnit(stage)), 1.0, abs_tol=1e-9):
        raise ValueError("Factory USD must be Z-up and metre-scaled")
    cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy])
    boxes = []
    max_z = 0.0
    for prim in stage.Traverse():
        if prim.GetTypeName() != "Mesh" or not prim.HasAPI(UsdPhysics.CollisionAPI):
            continue
        enabled = prim.GetAttribute("physics:collisionEnabled").Get()
        if enabled is False:
            continue
        bounds = cache.ComputeWorldBound(prim).ComputeAlignedRange()
        minimum, maximum = bounds.GetMin(), bounds.GetMax()
        values = tuple(float(value) for value in (*minimum, *maximum))
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"non-finite collision bounds: {prim.GetPath()}")
        if maximum[0] <= minimum[0] or maximum[1] <= minimum[1] or maximum[2] <= minimum[2]:
            continue
        boxes.append({
            "prim_path": str(prim.GetPath()),
            "bounds_m": [values[0], values[1], values[2], values[3], values[4], values[5]],
        })
        max_z = max(max_z, values[5])
    if not boxes:
        raise ValueError("Factory USD contains no enabled collision meshes")
    return boxes, max_z


def _cell_span(low: float, high: float, origin: float, resolution: float, limit: int):
    # AABB rasterization includes both cells touched by a face.  The small
    # epsilon avoids losing a cell solely because a USD float is rounded down.
    epsilon = 1e-9
    first = max(0, int(math.floor((low - origin - epsilon) / resolution)))
    last = min(limit - 1, int(math.ceil((high - origin + epsilon) / resolution) - 1))
    return first, last


def build_product(usd_path: Path, map_yaml: Path) -> tuple[np.ndarray, dict]:
    map_config, resolution, x0, y0, shape = _map_frame(map_yaml)
    height, width = shape
    boxes, max_z = _collision_boxes(usd_path)
    z_layers = max(1, int(math.ceil((max_z + VOXEL_SIZE_M) / VOXEL_SIZE_M)))
    voxels = np.zeros((z_layers, height, width), dtype=np.bool_)
    for item in boxes:
        xmin, ymin, zmin, xmax, ymax, zmax = item["bounds_m"]
        cx0, cx1 = _cell_span(xmin, xmax, x0, resolution, width)
        cy0, cy1 = _cell_span(ymin, ymax, y0, resolution, height)
        cz0 = max(0, int(math.floor((zmin - 1e-9) / VOXEL_SIZE_M)))
        cz1 = min(z_layers - 1, int(math.ceil((zmax + 1e-9) / VOXEL_SIZE_M) - 1))
        if cx0 > cx1 or cy0 > cy1 or cz0 > cz1:
            continue
        voxels[cz0:cz1 + 1, cy0:cy1 + 1, cx0:cx1 + 1] = True
    metadata = {
        "schema": SCHEMA,
        "version": VERSION,
        "scene_key": "factory",
        "source": {
            "usd": usd_path.as_posix(),
            "usd_sha256": _sha256(usd_path),
            "map_yaml": map_yaml.as_posix(),
            "map_yaml_sha256": _sha256(map_yaml),
            "map_image": (map_yaml.parent / str(map_config["image"])).as_posix(),
            "map_image_sha256": _sha256(map_yaml.parent / str(map_config["image"])),
        },
        "parameters": {
            "rasterizer": "collision_mesh_world_aabb_v1",
            "resolution_xy_m": resolution,
            "resolution_z_m": VOXEL_SIZE_M,
            "origin_xyz_m": [x0, y0, 0.0],
            "shape_zyx": list(voxels.shape),
            "aabb_inclusion_epsilon_m": 1e-9,
        },
        "collision_mesh_count": len(boxes),
        "occupied_voxel_count": int(voxels.sum()),
        "occupied_fraction": float(voxels.mean()),
        "z_extent_m": [0.0, z_layers * VOXEL_SIZE_M],
        "validation_scope": "conservative_static_collision_aabbs_only",
        "execution_validated": False,
        "limitations": [
            "not a global flight planner",
            "not sensor visibility or tracking evidence",
            "not a collision-safety or dynamic-obstacle guarantee",
        ],
    }
    return voxels, metadata


def write_product(voxels: np.ndarray, metadata: dict, output: Path, metadata_output: Path) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    metadata_output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=output.parent, prefix=output.name + ".", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
    try:
        np.savez(temporary, voxels=voxels, x0=float(metadata["parameters"]["origin_xyz_m"][0]),
                 y0=float(metadata["parameters"]["origin_xyz_m"][1]),
                 res=float(metadata["parameters"]["resolution_xy_m"]), vox=float(VOXEL_SIZE_M),
                 schema=np.asarray(SCHEMA), version=np.asarray(VERSION),
                 source_usd_sha256=np.asarray(metadata["source"]["usd_sha256"]))
        # np.savez appends .npz when given a non-.npz suffix.
        generated = temporary.with_name(temporary.name + ".npz")
        generated.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
        temporary.with_name(temporary.name + ".npz").unlink(missing_ok=True)
    metadata["product_sha256"] = _sha256(output)
    metadata_output.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    return metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--usd", type=Path, default=DEFAULT_USD)
    parser.add_argument("--map-yaml", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--output", type=Path, default=DEFAULT_PRODUCT)
    parser.add_argument("--metadata-output", type=Path, default=DEFAULT_METADATA)
    parser.add_argument("--write", action="store_true", help="write the derived product")
    args = parser.parse_args(argv)
    voxels, metadata = build_product(args.usd, args.map_yaml)
    if args.write:
        metadata = write_product(voxels, metadata, args.output, args.metadata_output)
    else:
        metadata["would_write"] = {"product": args.output.as_posix(), "metadata": args.metadata_output.as_posix()}
    print(json.dumps(metadata, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["SCHEMA", "VERSION", "VOXEL_SIZE_M", "build_product", "write_product"]
