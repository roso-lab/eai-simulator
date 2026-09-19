#!/usr/bin/env python
"""Rebuild NVD benchmark occupancy products from complete scene geometry.

The original warehouse_nvd/modern_house_nvd products (``ground_map_0.6m``,
``drone_voxels_0.25m`` and the ``BenchCollision`` proxy layer) were sampled
incompletely: entire instance categories (pillars, fencing, piles) and about
half of the racks and cardboxes never reached the occupancy products, so
benchmark spawn/route gates certified positions that the visible scene
blocks.  This tool re-derives every navigation-critical product directly from
the bench USD:

- native instances and solid point-instancer instances contribute their
  aggregated prototype world bounding box
  (conservative: a rack footprint blocks its whole cell area);
- non-instanced meshes are rasterized triangle-exactly (vectorized numpy)
  so floor slabs and huge structural meshes stay drivable where they are
  hollow; only large triangles are tested with a grid, small ones fill their
  cell bounding box;
- PaintTool tree trunks contribute tight ground-band proxies; grass and high
  branches do not create invisible collision, while sofa/armchair meshes use
  native convex-decomposition collision instead of coarse proxy cubes;
- ``/World/BenchCollision`` itself is excluded (it is regenerated here).

Outputs per scene (write with ``--apply``; default is a dry-run report):

- ``ground_map_0.6m.png`` rewritten in place (yaml frame unchanged);
- ``drone_voxels_0.25m.npz`` rewritten in place (same schema);
- ``BenchCollision`` wall cubes (source-derived heights capped at 0.8 m),
  positive-volume boxes, and below-ground boxes rebuilt from corrected grids;
- ``BenchCollision/zz_ground`` rebuilt as a static Z-up collision Plane at
  z=0 (the old thin Cube caused unstable wheel contacts);
- the ``BenchCollision`` group is normalized to an imageable ``Xform`` and
  authored ``visibility=invisible``: the proxy layer is physics-only, so
  sensor cameras keep seeing the real NVD props while wall/aerial/ground
  collision is unchanged;
- ``bench_meta.json`` gains a ``rebuild`` record and deterministic product
  provenance.  The provenance deliberately remains ``local_candidate`` until
  these exact bytes are published and verified at an immutable provider
  revision.

``mezzanine_map`` and other ``zz_*`` plates are intentionally preserved: the
mezzanine slab was detected from the real mesh set (walk_z in bench_meta)
and is independent of the ground-band defect.

The verification pass fails closed: source geometry must exist, at least 95%
of solid instances must land on occupied cells in the regenerated ground map,
pillar/fencing/pile/rack coverage must be complete, and every source voxel not
already represented by a wall cube must be covered by a generated volume cube.

Usage (offline; standalone pxr + numpy + PyYAML + Pillow, no Isaac):

    python -m tools.assets.rebuild_nvd_products --scene warehouse_nvd
    python -m tools.assets.rebuild_nvd_products --scene warehouse_nvd --apply
    python -m tools.assets.rebuild_nvd_products --scene warehouse_nvd --check-ground
    python -m tools.assets.rebuild_nvd_products --scene warehouse_nvd --check-provenance
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENES = {
    "warehouse_nvd": "usd/scene/warehouse_nvd",
    "modern_house_nvd": "usd/scene/modern_house_nvd",
}
SOURCE_ROOT_USD = {
    "warehouse_nvd": "../../assets/nvd/XR_Content_NVD@10010/Assets/XR/Stages/Indoor/Warehouse/Warehouse_XR.usd",
    "modern_house_nvd": "../../assets/nvd/XR_Content_NVD@10010/Assets/XR/Stages/Indoor/Modern_House_XR.usd",
}
PRODUCT_PROVENANCE_SCHEMA = "eai.nvd-products.provenance.v1"
GENERATOR_ID = "tools.assets.rebuild_nvd_products"
GENERATOR_VERSION = "1"

GROUND_BAND = (0.06, 0.60)   # solid-above-floor band for the ground map
WALL_HEIGHT = 0.8            # maximum regenerated wall height above z=0
MIN_WALL_HEIGHT = 0.06       # do not create zero-thickness wall colliders
WALL_HEIGHT_QUANTUM = 0.05   # stable authored heights, close to source geometry
AERIAL_FROM_LAYER = 2        # z >= 0.5 m
VOXEL_Z = 0.25
MAX_Z = 6.0
MIN_Z = -2.5
SMALL_TRIANGLE_CELLS = 16    # <= this many bbox cells: conservative fill
LARGE_GRID_CAP = (2048, 2048)
GROUND_PATH = "/World/BenchCollision/zz_ground"

# These provider point instancers are deliberately non-solid ground cover.
# Grass and flower cards are decorative, while tree trunks remain obstacles.
# The latter contribute only trunk/bark mesh triangles, not foliage bounds.
NON_SOLID_POINT_INSTANCERS = ("grass_short_", "meadowlark", "magnolia_topper")
SOLID_PAINT_TOOL_MESH_TOKENS = ("trunk", "bark")
NATIVE_COLLISION_ASSET_ROOTS = (
    "/assets/ext_seating/roxana_",
    "/assets/int_living/whitecliff_sofa/",
    "/assets/int_living/armchair/",
    "/assets/int_lamp/floorlamp/",
    "/assets/int_lamp/brassfloorlamp/",
    "/assets/int_lamp/mechant/",
    "/circularbollard",
    "/abstractbike/",
)
IGNORED_MESH_PATH_TOKENS = (
    "/hye/geometry/windows/",
    "glass",
    "frostedglass",
    "leaves",
    "leaf",
    "flower",
    "foliage",
    "grass_short_",
    "meadowlark",
    "magnolia_topper",
    "pinkflamingos",
    "flutted_",
    "fluted_",
    "/int_living/rug_",
    "/int_plant/",
    "plant_succulent",
    "stitch",
    "screw",
    "bolt",
)
SMALL_MESH_MAX_DIM = 0.18
GENERATED_PROXY_NAME_RE = re.compile(r"(?:w|f|g)\d+|(?:b|gh)\d+_\d+")
STALE_PROXY_NAME_RE = re.compile(r"(?:f|g)\d+|(?:b|gh)\d+_\d+")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha256(value) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _product_paths(scene_key: str, *, asset_root: Path | None = None) -> dict[str, Path]:
    if asset_root is None:
        scene_dir = (REPO_ROOT / SCENES[scene_key]).resolve()
        boundary = (REPO_ROOT / "usd").resolve()
    else:
        boundary = Path(asset_root).resolve()
        configured = Path(SCENES[scene_key])
        if configured.parts and configured.parts[0] == "usd":
            configured = Path(*configured.parts[1:])
        scene_dir = (boundary / configured).resolve()
    paths = {
        "bench_usd": scene_dir / f"{scene_key}_bench.usd",
        "ground_map_png": scene_dir / "ground_map_0.6m.png",
        "ground_map_yaml": scene_dir / "ground_map_0.6m.yaml",
        "drone_voxels_npz": scene_dir / "drone_voxels_0.25m.npz",
        "provider_root_usd": (scene_dir / SOURCE_ROOT_USD[scene_key]).resolve(),
        "metadata": scene_dir / "bench_meta.json",
    }
    for name, path in paths.items():
        try:
            path.relative_to(boundary)
        except ValueError as exc:
            raise ValueError(f"{name} escapes the repository: {path}") from exc
        if not path.is_file():
            raise ValueError(f"missing NVD provenance input {name}: {path}")
    return paths


def _source_closure(paths: dict[str, Path], *, asset_root: Path | None) -> dict:
    from tools.assets.usd_closure import audit

    boundary = ((REPO_ROOT / "usd").resolve() if asset_root is None
                else Path(asset_root).resolve())
    report = audit(
        [paths["provider_root_usd"]], boundary=boundary, hashes=True)
    if report["validated"] is not True:
        raise ValueError(
            "provider source closure is incomplete: "
            f"missing={report['missing']}, absolute={report['absolute_references']}, "
            f"outside={report['outside_boundary']}")
    digest_payload = {
        "files": report["files"],
        "runtime_modules": report["runtime_modules"],
    }
    return {
        "root_path": paths["provider_root_usd"].relative_to(boundary).as_posix(),
        "file_count": int(report["file_count"]),
        "total_bytes": int(report["total_bytes"]),
        "runtime_modules": list(report["runtime_modules"]),
        "manifest_sha256": _canonical_sha256(digest_payload),
    }


def _product_summary(paths: dict[str, Path]) -> dict:
    import yaml
    from PIL import Image

    config = yaml.safe_load(paths["ground_map_yaml"].read_text())
    if not isinstance(config, dict) or not isinstance(config.get("origin"), list):
        raise ValueError("ground-map YAML lacks a structured origin")
    with Image.open(paths["ground_map_png"]) as image:
        pixels = np.asarray(image.convert("L"))
    occupied = pixels == 0
    with np.load(paths["drone_voxels_npz"], allow_pickle=False) as archive:
        required = {"voxels", "x0", "y0", "res", "vox"}
        if not required.issubset(archive.files):
            raise ValueError(
                f"voxel product lacks fields: {sorted(required - set(archive.files))}")
        voxels = np.asarray(archive["voxels"], dtype=bool)
        voxel_frame = {
            "x0": float(archive["x0"]),
            "y0": float(archive["y0"]),
            "resolution_m": float(archive["res"]),
            "voxel_height_m": float(archive["vox"]),
        }
    origin = [float(value) for value in config["origin"]]
    if len(origin) != 3:
        raise ValueError("ground-map origin must contain x, y and yaw")
    resolution = float(config["resolution"])
    if voxels.ndim != 3 or tuple(voxels.shape[1:]) != tuple(pixels.shape):
        raise ValueError("ground map and voxel product use different XY shapes")
    if not (np.isclose(voxel_frame["x0"], origin[0])
            and np.isclose(voxel_frame["y0"], origin[1])
            and np.isclose(voxel_frame["resolution_m"], resolution)
            and np.isclose(voxel_frame["voxel_height_m"], VOXEL_Z)):
        raise ValueError("ground map and voxel product use different frames")
    return {
        "ground_map": {
            "shape": [int(value) for value in pixels.shape],
            "occupied_cells": int(occupied.sum()),
            "occupied_fraction": float(occupied.mean()),
            "resolution_m": resolution,
            "origin": origin,
        },
        "drone_voxels": {
            "shape": [int(value) for value in voxels.shape],
            "occupied_voxels": int(voxels.sum()),
            "occupied_fraction": float(voxels.mean()),
            **voxel_frame,
        },
    }


def build_product_provenance(scene_key: str, *, asset_root: Path | None = None) -> dict:
    """Build a deterministic manifest for one local NVD product bundle."""

    paths = _product_paths(scene_key, asset_root=asset_root)
    summary = _product_summary(paths)
    closure = _source_closure(paths, asset_root=asset_root)
    scene_dir = paths["metadata"].parent
    product_names = (
        "bench_usd", "ground_map_png", "ground_map_yaml", "drone_voxels_npz")
    return {
        "schema": PRODUCT_PROVENANCE_SCHEMA,
        "scene": scene_key,
        "publication_status": "local_candidate",
        "provider_revision": None,
        "generator": {
            "module": GENERATOR_ID,
            "algorithm_version": GENERATOR_VERSION,
        },
        "settings": {
            "ground_band_m": [float(value) for value in GROUND_BAND],
            "wall_height_m": float(WALL_HEIGHT),
            "voxel_height_m": float(VOXEL_Z),
            "minimum_z_m": float(MIN_Z),
            "maximum_z_m": float(MAX_Z),
            "aerial_from_layer": int(AERIAL_FROM_LAYER),
            "aerial_from_z_m": float(AERIAL_FROM_LAYER * VOXEL_Z),
            "small_triangle_cell_limit": int(SMALL_TRIANGLE_CELLS),
            "large_grid_cap": [int(value) for value in LARGE_GRID_CAP],
            "map_frame": summary["ground_map"],
            "voxel_frame": summary["drone_voxels"],
        },
        "source": {
            "provider_root_usd": {
                "path": SOURCE_ROOT_USD[scene_key],
                "sha256": _sha256(paths["provider_root_usd"]),
            },
            "closure": closure,
        },
        "products": {
            name: {
                "path": paths[name].relative_to(scene_dir).as_posix(),
                "sha256": _sha256(paths[name]),
            }
            for name in product_names
        },
    }


def _first_difference(expected, actual, path="product_provenance") -> str | None:
    if type(expected) is not type(actual):
        return f"{path} has type {type(actual).__name__}, expected {type(expected).__name__}"
    if isinstance(expected, dict):
        if set(expected) != set(actual):
            return f"{path} keys differ"
        for key in expected:
            difference = _first_difference(expected[key], actual[key], f"{path}.{key}")
            if difference:
                return difference
        return None
    if isinstance(expected, list):
        if len(expected) != len(actual):
            return f"{path} length differs"
        for index, (left, right) in enumerate(zip(expected, actual)):
            difference = _first_difference(left, right, f"{path}[{index}]")
            if difference:
                return difference
        return None
    return None if expected == actual else f"{path} differs"


def validate_product_provenance(
        scene_key: str, *, metadata: dict | None = None,
        asset_root: Path | None = None) -> dict:
    """Fail closed unless the sidecar exactly binds the current local bundle."""

    paths = _product_paths(scene_key, asset_root=asset_root)
    if metadata is None:
        metadata = json.loads(paths["metadata"].read_text())
    manifest = metadata.get("product_provenance") if isinstance(metadata, dict) else None
    if not isinstance(manifest, dict):
        raise ValueError("bench metadata lacks product_provenance")
    if manifest.get("publication_status") != "local_candidate":
        raise ValueError("NVD products must remain an explicit local_candidate")
    if manifest.get("provider_revision") is not None:
        raise ValueError("a local candidate cannot claim an immutable provider revision")
    expected = build_product_provenance(scene_key, asset_root=asset_root)
    difference = _first_difference(expected, manifest)
    if difference:
        raise ValueError(f"NVD product provenance mismatch: {difference}")
    return manifest


def _open_stage(usd_path: Path):
    from pxr import Usd

    return Usd.Stage.Open(str(usd_path))


def _check_ground(stage) -> dict:
    """Read-only authored-floor check; does not certify PhysX contact behavior."""
    from pxr import Usd, UsdGeom, UsdPhysics

    prim = stage.GetPrimAtPath(GROUND_PATH)
    errors = []
    if UsdGeom.GetStageUpAxis(stage) != "Z":
        errors.append("stage must be Z-up")
    if not np.isclose(UsdGeom.GetStageMetersPerUnit(stage), 1.0):
        errors.append("stage must use metres")
    if not prim:
        errors.append("ground prim missing")
    else:
        if prim.GetTypeName() != "Plane":
            errors.append("ground must be a Plane")
        axis = prim.GetAttribute("axis").Get()
        if axis != "Z":
            errors.append("ground axis must be Z")
        if not prim.HasAPI(UsdPhysics.CollisionAPI):
            errors.append("ground CollisionAPI missing")
        elif not UsdPhysics.CollisionAPI(prim).GetCollisionEnabledAttr().Get():
            errors.append("ground collision disabled")
        ancestor = prim
        while ancestor and not ancestor.IsPseudoRoot():
            if ancestor.HasAPI(UsdPhysics.RigidBodyAPI):
                errors.append("ground must be static (no rigid-body ancestor)")
                break
            ancestor = ancestor.GetParent()
        matrix = np.array(UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()))
        # NVD wrappers and their generated floor have identity transforms.
        # This also rejects inherited floor tilt/height/scale changes.
        if not np.allclose(matrix, np.eye(4), atol=1e-9, rtol=0):
            errors.append("ground world transform must be identity at z=0")
    return {"path": GROUND_PATH, "type": prim.GetTypeName() if prim else None,
            "verified": not errors, "errors": errors}


def _define_ground_plane(stage):
    from pxr import Sdf, UsdGeom, UsdPhysics

    # Delete the legacy generated prim so Cube extent/scale, rigid-body APIs,
    # and other obsolete properties cannot leak into the Plane definition.
    stage.RemovePrim(GROUND_PATH)
    prim = stage.DefinePrim(GROUND_PATH, "Plane")
    prim.CreateAttribute("axis", Sdf.ValueTypeNames.Token, custom=False).Set("Z")
    UsdGeom.Xformable(prim).AddTranslateOp().Set((0.0, 0.0, 0.0))
    UsdPhysics.CollisionAPI.Apply(prim).CreateCollisionEnabledAttr(True)
    report = _check_ground(stage)
    if not report["verified"]:
        raise ValueError(f"Invalid generated NVD ground: {report['errors']}")
    return report


def _mesh_world_triangles(prim):
    """World-space triangle array (n, 3, 3) for one non-instanced mesh."""
    from pxr import Usd, UsdGeom

    points = prim.GetAttribute("points").Get()
    indices = prim.GetAttribute("faceVertexIndices").Get()
    counts = prim.GetAttribute("faceVertexCounts").Get()
    if not (points and indices and counts):
        return None
    xf = np.array(UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(
        Usd.TimeCode.Default()))
    pts = np.array(points, dtype=float)
    world = (np.hstack([pts, np.ones((len(pts), 1))]) @ xf)[:, :3]
    idx = np.array(indices, dtype=np.int64)
    counts = np.array(counts, dtype=np.int64)

    # Fan-triangulate every polygon with a vectorized index builder.
    tri_counts = np.maximum(counts - 2, 0)
    if tri_counts.sum() == 0:
        return None
    mesh_index = np.repeat(np.arange(len(counts)), tri_counts)
    starts = np.repeat(np.cumsum(np.concatenate([[0], counts]))[:-1], tri_counts)
    local_k = np.arange(int(tri_counts.sum())) - np.repeat(
        np.cumsum(tri_counts) - tri_counts, tri_counts)
    tris = np.stack([
        idx[starts],
        idx[starts + local_k + 1],
        idx[starts + local_k + 2],
    ], axis=1)
    return world[tris]


def _mesh_collision_policy(prim, *, size=None):
    """Classify one visual mesh as native collision, proxy, or ignored.

    The policy is intentionally path-based for authored asset families and
    size-based only for unclassified decorative fragments.  This keeps tiny
    leaves, stitches and hardware out of PhysX while preserving a conservative
    Cube fallback for structural or otherwise substantial meshes.
    """

    lowered = str(prim.GetPath()).lower()
    for token in IGNORED_MESH_PATH_TOKENS:
        if token in lowered:
            return "ignore", token
    if any(root in lowered for root in NATIVE_COLLISION_ASSET_ROOTS):
        return "native", "physical_asset_family"
    if size is not None and max(float(value) for value in size) < SMALL_MESH_MAX_DIM:
        return "ignore", "small_decorative_mesh"
    return "proxy", "grid_proxy_fallback"


def _collect_geometry(stage):
    """Return solid instance boxes, mesh triangles, and collection statistics."""
    from pxr import Usd, UsdGeom

    cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy])

    instance_boxes = []
    mesh_tri_arrays = []
    tree_tri_arrays = []
    point_instancers = 0
    point_instances = 0
    excluded_point_instances = 0
    mesh_policy_counts = {"native": 0, "proxy": 0, "ignore": 0}
    mesh_policy_reasons = {}

    def has_point_instancer_ancestor(prim):
        ancestor = prim.GetParent()
        while ancestor and not ancestor.IsPseudoRoot():
            if ancestor.IsA(UsdGeom.PointInstancer):
                return True
            ancestor = ancestor.GetParent()
        return False

    def instance_world_bbox(prim):
        proto = prim.GetPrototype()
        if proto is None:
            return None
        try:
            rng = cache.ComputeUntransformedBound(proto).ComputeAlignedRange()
        except Exception:
            return None
        mn, mx = rng.GetMin(), rng.GetMax()
        xf = np.array(UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()))
        corners = np.array([[x, y, z, 1.0]
                            for x in (mn[0], mx[0])
                            for y in (mn[1], mx[1])
                            for z in (mn[2], mx[2])])
        world = corners @ xf  # USD row-vector convention: p @ M
        return world[:, :3]

    def point_instance_world_bboxes(prim):
        instancer = UsdGeom.PointInstancer(prim)
        targets = instancer.GetPrototypesRel().GetTargets()
        indices = instancer.GetProtoIndicesAttr().Get() or []
        transforms = instancer.ComputeInstanceTransformsAtTime(
            Usd.TimeCode.Default(), Usd.TimeCode.Default(),
            UsdGeom.PointInstancer.IncludeProtoXform)
        if len(indices) != len(transforms):
            raise ValueError(
                f"PointInstancer index/transform count mismatch: {prim.GetPath()}")
        instancer_world = np.array(UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()))
        prototype_corners = {}
        for prototype_index in set(indices):
            if prototype_index < 0 or prototype_index >= len(targets):
                raise ValueError(
                    f"PointInstancer prototype index out of range: {prim.GetPath()}")
            prototype = stage.GetPrimAtPath(targets[prototype_index])
            if not prototype:
                raise ValueError(
                    f"PointInstancer prototype is missing: {targets[prototype_index]}")
            rng = cache.ComputeUntransformedBound(prototype).ComputeAlignedRange()
            mn, mx = rng.GetMin(), rng.GetMax()
            prototype_corners[prototype_index] = np.array([
                [x, y, z, 1.0]
                for x in (mn[0], mx[0])
                for y in (mn[1], mx[1])
                for z in (mn[2], mx[2])
            ])
        result = []
        for index, (prototype_index, transform) in enumerate(zip(indices, transforms)):
            world = prototype_corners[prototype_index] @ np.array(transform) @ instancer_world
            result.append((
                float(world[:, 0].min()), float(world[:, 1].min()),
                float(world[:, 0].max()), float(world[:, 1].max()),
                float(world[:, 2].min()), float(world[:, 2].max()),
                f"{prim.GetName()}[{index}]",
            ))
        return result

    def point_instance_solid_meshes(prim):
        """Expand trunk/bark triangles at each tree's actual world position."""

        instancer = UsdGeom.PointInstancer(prim)
        targets = instancer.GetPrototypesRel().GetTargets()
        indices = instancer.GetProtoIndicesAttr().Get() or []
        transforms = instancer.ComputeInstanceTransformsAtTime(
            Usd.TimeCode.Default(), Usd.TimeCode.Default(),
            UsdGeom.PointInstancer.IncludeProtoXform)
        if len(indices) != len(transforms):
            raise ValueError(f"PointInstancer transform count mismatch: {prim.GetPath()}")
        instancer_world = np.array(UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()))
        local_triangles = {}
        for prototype_index in set(indices):
            if prototype_index < 0 or prototype_index >= len(targets):
                raise ValueError(f"PointInstancer prototype index out of range: {prim.GetPath()}")
            prototype = stage.GetPrimAtPath(targets[prototype_index])
            prototype_world_inverse = np.linalg.inv(np.array(
                UsdGeom.Xformable(prototype).ComputeLocalToWorldTransform(
                    Usd.TimeCode.Default())))
            arrays = []
            for candidate in Usd.PrimRange(prototype):
                if (candidate.GetTypeName() != "Mesh"
                        or not any(token in candidate.GetName().lower()
                                   for token in SOLID_PAINT_TOOL_MESH_TOKENS)):
                    continue
                world = _mesh_world_triangles(candidate)
                if world is None or not len(world):
                    continue
                homogeneous = np.concatenate(
                    [world, np.ones((*world.shape[:-1], 1))], axis=-1)
                arrays.append((homogeneous @ prototype_world_inverse)[..., :3])
            if not arrays:
                raise ValueError(f"tree PointInstancer has no trunk/bark mesh: {prim.GetPath()}")
            local_triangles[prototype_index] = arrays
        result = []
        for prototype_index, transform in zip(indices, transforms):
            matrix = np.array(transform) @ instancer_world
            for triangles in local_triangles[prototype_index]:
                homogeneous = np.concatenate(
                    [triangles, np.ones((*triangles.shape[:-1], 1))], axis=-1)
                result.append((homogeneous @ matrix)[..., :3])
        return result

    for prim in stage.Traverse():
        path = str(prim.GetPath())
        if path.startswith("/World/BenchCollision"):
            continue
        if prim.IsA(UsdGeom.PointInstancer):
            # Nested instancers are already included in their outer prototype's
            # aggregate bound.  Processing them again would author collisions at
            # the prototype/template coordinates instead of instance positions.
            if has_point_instancer_ancestor(prim):
                continue
            count = len(UsdGeom.PointInstancer(prim).GetProtoIndicesAttr().Get() or [])
            point_instancers += 1
            if any(token in path.lower() for token in NON_SOLID_POINT_INSTANCERS):
                excluded_point_instances += count
                continue
            if "/PaintTool/" in path:
                tree_tri_arrays.extend(point_instance_solid_meshes(prim))
                point_instances += count
                continue
            boxes = point_instance_world_bboxes(prim)
            instance_boxes.extend(boxes)
            point_instances += len(boxes)
            continue
        if prim.IsInstance():
            box = instance_world_bbox(prim)
            if box is not None and len(box):
                instance_boxes.append((
                    float(box[:, 0].min()), float(box[:, 1].min()),
                    float(box[:, 0].max()), float(box[:, 1].max()),
                    float(box[:, 2].min()), float(box[:, 2].max()),
                    prim.GetName()))
            continue
        if prim.GetTypeName() == "Mesh":
            if has_point_instancer_ancestor(prim):
                continue
            rng = cache.ComputeWorldBound(prim).ComputeAlignedRange()
            size = np.asarray(rng.GetMax(), dtype=float) - np.asarray(
                rng.GetMin(), dtype=float)
            policy, reason = _mesh_collision_policy(prim, size=size)
            mesh_policy_counts[policy] += 1
            mesh_policy_reasons[reason] = mesh_policy_reasons.get(reason, 0) + 1
            if policy != "proxy":
                continue
            tris = _mesh_world_triangles(prim)
            if tris is not None and len(tris):
                mesh_tri_arrays.append(tris)
    return instance_boxes, mesh_tri_arrays, tree_tri_arrays, {
        "point_instancers": point_instancers,
        "point_instances": point_instances,
        "excluded_non_solid_point_instances": excluded_point_instances,
        "mesh_policy_counts": mesh_policy_counts,
        "mesh_policy_reasons": dict(sorted(mesh_policy_reasons.items())),
        "native_collision_meshes": mesh_policy_counts["native"],
        "proxy_meshes": mesh_policy_counts["proxy"],
        "ignored_meshes": mesh_policy_counts["ignore"],
    }


def _triangle_cell_overlap(px, py, tri, half_cell):
    """Conservative 2-D triangle/AABB overlap, including degenerate lines."""

    result = ((px + half_cell >= tri[:, 0].min())
              & (px - half_cell <= tri[:, 0].max())
              & (py + half_cell >= tri[:, 1].min())
              & (py - half_cell <= tri[:, 1].max()))
    for start, end in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
        edge = end[:2] - start[:2]
        normal = np.array([-edge[1], edge[0]])
        if np.allclose(normal, 0.0):
            continue
        triangle_projection = tri[:, :2] @ normal
        center_projection = px * normal[0] + py * normal[1]
        radius = half_cell * (abs(normal[0]) + abs(normal[1]))
        result &= ((center_projection + radius >= triangle_projection.min())
                   & (center_projection - radius <= triangle_projection.max()))
    return result


def _apply_native_mesh_collisions(stage):
    """Author native collision on meshes classified by the audit policy."""

    from pxr import Usd, UsdGeom, UsdPhysics

    cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render, UsdGeom.Tokens.proxy])
    applied = 0
    removed = 0
    for prim in stage.Traverse():
        if prim.GetTypeName() != "Mesh":
            continue
        rng = cache.ComputeWorldBound(prim).ComputeAlignedRange()
        size = np.asarray(rng.GetMax(), dtype=float) - np.asarray(
            rng.GetMin(), dtype=float)
        policy, _reason = _mesh_collision_policy(prim, size=size)
        if policy != "native":
            if prim.HasAPI(UsdPhysics.CollisionAPI):
                prim.RemoveAPI(UsdPhysics.CollisionAPI)
                removed += 1
            if prim.HasAPI(UsdPhysics.MeshCollisionAPI):
                prim.RemoveAPI(UsdPhysics.MeshCollisionAPI)
            continue
        UsdPhysics.CollisionAPI.Apply(prim).CreateCollisionEnabledAttr(True)
        UsdPhysics.MeshCollisionAPI.Apply(prim).CreateApproximationAttr().Set(
            "convexDecomposition")
        applied += 1
    return {"applied": applied, "removed_stale": removed}


def _rasterize_tris(tris, mask, *, res, ox, oy, z_lo, z_hi,
                    height_map=None):
    """Mark every cell intersected by triangles in the z window."""
    height, width = mask.shape
    zmin = tris[:, :, 2].min(axis=1)
    zmax = tris[:, :, 2].max(axis=1)
    keep = (zmax > z_lo) & (zmin < z_hi)
    tris = tris[keep]
    zmax = zmax[keep]
    if not len(tris):
        return
    raw_cx0 = np.floor((tris[:, :, 0].min(axis=1) - ox) / res).astype(np.int64)
    raw_cx1 = np.floor((tris[:, :, 0].max(axis=1) - ox) / res).astype(np.int64)
    raw_cy0 = np.floor((tris[:, :, 1].min(axis=1) - oy) / res).astype(np.int64)
    raw_cy1 = np.floor((tris[:, :, 1].max(axis=1) - oy) / res).astype(np.int64)
    in_frame = ((raw_cx1 >= 0) & (raw_cx0 < width)
                & (raw_cy1 >= 0) & (raw_cy0 < height))
    tris = tris[in_frame]
    zmax = zmax[in_frame]
    if not len(tris):
        return
    cx0 = np.clip(raw_cx0[in_frame], 0, width - 1)
    cx1 = np.clip(raw_cx1[in_frame], 0, width - 1)
    cy0 = np.clip(raw_cy0[in_frame], 0, height - 1)
    cy1 = np.clip(raw_cy1[in_frame], 0, height - 1)
    sizes = (cx1 - cx0 + 1) * (cy1 - cy0 + 1)
    small = sizes <= SMALL_TRIANGLE_CELLS
    if small.any():
        _fill_rects(mask, cx0[small], cy0[small], cx1[small], cy1[small])
        if height_map is not None:
            for x0, x1, y0, y1, height in zip(
                    cx0[small], cx1[small], cy0[small], cy1[small],
                    zmax[small]):
                height_map[y0:y1 + 1, x0:x1 + 1] = np.maximum(
                    height_map[y0:y1 + 1, x0:x1 + 1], height)
    large = ~small
    if large.any():
        for tri, x0, x1, y0, y1 in zip(tris[large], cx0[large], cx1[large],
                                       cy0[large], cy1[large]):
            span_x, span_y = int(x1 - x0 + 1), int(y1 - y0 + 1)
            # Chunk rather than stride: striding silently left unchecked cells
            # in very large meshes and caused visible gaps in proxy coverage.
            for ys in range(y0, y1 + 1, LARGE_GRID_CAP[1]):
                ye = min(y1 + 1, ys + LARGE_GRID_CAP[1])
                for xs in range(x0, x1 + 1, LARGE_GRID_CAP[0]):
                    xe = min(x1 + 1, xs + LARGE_GRID_CAP[0])
                    cols = np.arange(xs, xe)
                    rows = np.arange(ys, ye)
                    px, py = np.meshgrid((cols + 0.5) * res + ox,
                                         (rows + 0.5) * res + oy)
                    overlap = _triangle_cell_overlap(px, py, tri, res / 2.0)
                    mask[np.ix_(rows, cols)] |= overlap
                    if height_map is not None and overlap.any():
                        target = height_map[ys:ye, xs:xe]
                        target[overlap] = np.maximum(
                            target[overlap], tri[:, 2].max())


def _fill_rects(mask, cx0, cy0, cx1, cy1):
    """Vectorized fill of per-triangle cell bounding boxes."""
    widths = (cx1 - cx0 + 1).astype(np.int64)
    heights = (cy1 - cy0 + 1).astype(np.int64)
    counts = widths * heights
    if counts.sum() == 0:
        return
    tri_index = np.repeat(np.arange(len(counts)), counts)
    total = int(counts.sum())
    offsets = np.arange(total) - np.repeat(np.cumsum(counts) - counts, counts)
    w_rep = widths[tri_index]
    cols = cx0[tri_index] + offsets % w_rep
    rows = cy0[tri_index] + offsets // w_rep
    mask[rows, cols] = True


def _point_in_tri(px, py, tri):
    ax, ay = tri[0, 0], tri[0, 1]
    bx, by = tri[1, 0], tri[1, 1]
    cx, cy = tri[2, 0], tri[2, 1]
    d = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
    if abs(d) < 1e-12:
        return ((px >= min(ax, bx, cx)) & (px <= max(ax, bx, cx))
                & (py >= min(ay, by, cy)) & (py <= max(ay, by, cy)))
    l1 = ((by - cy) * (px - cx) + (cx - bx) * (py - cy)) / d
    l2 = ((cy - ay) * (px - cx) + (ax - cx) * (py - cy)) / d
    l3 = 1.0 - l1 - l2
    return (l1 >= -1e-9) & (l2 >= -1e-9) & (l3 >= -1e-9)


def _fill_box(mask, x0, y0, x1, y1, res, ox, oy):
    height, width = mask.shape
    raw_c0 = int(np.floor((x0 - ox) / res))
    raw_c1 = int(np.floor((x1 - ox) / res))
    raw_r0 = int(np.floor((y0 - oy) / res))
    raw_r1 = int(np.floor((y1 - oy) / res))
    if raw_c1 < 0 or raw_c0 >= width or raw_r1 < 0 or raw_r0 >= height:
        return
    c0 = max(0, raw_c0)
    c1 = min(width - 1, raw_c1)
    r0 = max(0, raw_r0)
    r1 = min(height - 1, raw_r1)
    mask[r0:r1 + 1, c0:c1 + 1] = True


def _fill_box_height(mask, heights, x0, y0, x1, y1, z1, res, ox, oy):
    """Fill an instance footprint and retain its highest source point."""

    height, width = mask.shape
    raw_c0 = int(np.floor((x0 - ox) / res))
    raw_c1 = int(np.floor((x1 - ox) / res))
    raw_r0 = int(np.floor((y0 - oy) / res))
    raw_r1 = int(np.floor((y1 - oy) / res))
    if raw_c1 < 0 or raw_c0 >= width or raw_r1 < 0 or raw_r0 >= height:
        return
    c0 = max(0, raw_c0)
    c1 = min(width - 1, raw_c1)
    r0 = max(0, raw_r0)
    r1 = min(height - 1, raw_r1)
    mask[r0:r1 + 1, c0:c1 + 1] = True
    heights[r0:r1 + 1, c0:c1 + 1] = np.maximum(
        heights[r0:r1 + 1, c0:c1 + 1], float(z1))


def _frame(map_yaml: Path):
    import yaml

    cfg = yaml.safe_load(map_yaml.read_text())
    return (cfg, float(cfg["resolution"]), float(cfg["origin"][0]),
            float(cfg["origin"][1]))


def _components_to_rects(mask):
    """Connected-component bounding boxes via scipy.ndimage.label."""
    from scipy import ndimage

    labels, count = ndimage.label(mask)
    if count == 0:
        return []
    rects = []
    for label_index, sl in enumerate(ndimage.find_objects(labels), start=1):
        rows, cols = sl
        rects.append((int(cols.start), int(rows.start),
                      int(cols.stop) - 1, int(rows.stop) - 1))
    return rects


def _decompose_rects(mask, heights=None):
    """Exact rectangle decomposition: rectangles cover occupied cells only.

    Component bounding boxes would also wall in the free space inside concave
    clusters (robot spawn points have landed inside such overhangs), so each
    output rectangle is a solid run of occupied cells extended downwards.
    """
    height, width = mask.shape
    covered = np.zeros_like(mask, dtype=bool)
    rects = []
    if heights is not None and heights.shape != mask.shape:
        raise ValueError("height map must have the same shape as mask")
    for r in range(height):
        row = mask[r] & ~covered[r]
        if not row.any():
            continue
        c = 0
        while c < width:
            if not row[c]:
                c += 1
                continue
            start = c
            value = heights[r, c] if heights is not None else None
            while (c < width and row[c]
                   and (heights is None or heights[r, c] == value)):
                c += 1
            end = c - 1
            bottom = r
            while (bottom + 1 < height
                   and mask[bottom + 1, start:end + 1].all()
                   and not covered[bottom + 1, start:end + 1].any()
                   and (heights is None or np.all(
                       heights[bottom + 1, start:end + 1] == value))):
                bottom += 1
            covered[r:bottom + 1, start:end + 1] = True
            if heights is None:
                rects.append((start, r, end, bottom))
            else:
                rects.append((start, r, end, bottom, float(value)))
    return rects


def _category(name: str) -> str:
    lowered = name.lower()
    for key in ("rack", "cardbox", "carbox", "pile", "pillar", "pallet",
                "ramp", "floor", "walkway", "fenc", "stair", "wall", "build",
                "roof", "pipe", "beam", "sofa", "table", "chair", "plant"):
        if key in lowered:
            return key
    return "other"


def build_products(scene_key: str, *, apply: bool) -> dict:
    scene_dir = REPO_ROOT / SCENES[scene_key]
    usd_path = scene_dir / f"{scene_key}_bench.usd"
    map_yaml = scene_dir / "ground_map_0.6m.yaml"
    map_png = scene_dir / "ground_map_0.6m.png"
    vox_path = scene_dir / "drone_voxels_0.25m.npz"
    meta_path = scene_dir / "bench_meta.json"

    stage = _open_stage(usd_path)
    instance_boxes, mesh_tri_arrays, tree_tri_arrays, collection = _collect_geometry(stage)
    cfg, res, ox, oy = _frame(map_yaml)

    from PIL import Image

    image = Image.open(map_png)
    height, width = np.asarray(image.convert("L")).shape

    # ---- ground band mask: instance bboxes + triangle-exact meshes ----
    ground = np.zeros((height, width), dtype=bool)
    ground_heights = np.zeros((height, width), dtype=float)
    for x0, y0, x1, y1, z0, z1, _name in instance_boxes:
        if z1 <= GROUND_BAND[0] or z0 >= GROUND_BAND[1]:
            continue
        _fill_box_height(
            ground, ground_heights, x0, y0, x1, y1,
            min(float(z1), WALL_HEIGHT), res, ox, oy)
    for tris in (*mesh_tri_arrays, *tree_tri_arrays):
        _rasterize_tris(tris, ground, res=res, ox=ox, oy=oy,
                        z_lo=GROUND_BAND[0], z_hi=GROUND_BAND[1],
                        height_map=ground_heights)
    # Keep wall dimensions close to source geometry while avoiding tiny or
    # unstable zero-thickness colliders.  Quantization makes the USD stable
    # across repeated rebuilds and limits the maximum to the legacy contract.
    ground_heights[ground] = np.clip(
        np.ceil(ground_heights[ground] / WALL_HEIGHT_QUANTUM)
        * WALL_HEIGHT_QUANTUM,
        MIN_WALL_HEIGHT, WALL_HEIGHT)

    # ---- voxel grid ----
    layers = int(round(MAX_Z / VOXEL_Z))
    voxels = np.zeros((layers, height, width), dtype=bool)
    for x0, y0, x1, y1, z0, z1, _name in instance_boxes:
        k0 = max(0, int(np.floor(z0 / VOXEL_Z)))
        k1 = min(layers - 1, int(np.ceil(z1 / VOXEL_Z)) - 1)
        for k in range(k0, k1 + 1):
            _fill_box(voxels[k], x0, y0, x1, y1, res, ox, oy)
    for k in range(layers):
        for tris in mesh_tri_arrays:
            _rasterize_tris(tris, voxels[k], res=res, ox=ox, oy=oy,
                            z_lo=k * VOXEL_Z, z_hi=(k + 1) * VOXEL_Z)
    # Floor layer 0: planar floor slabs at z ~ 0.
    floor = np.zeros((height, width), dtype=bool)
    for tris in mesh_tri_arrays:
        _rasterize_tris(tris, floor, res=res, ox=ox, oy=oy, z_lo=-0.05, z_hi=0.06)
    voxels[0] |= floor

    # Collision below the navigation datum is a separate product.  The Modern
    # House patio and several exterior assets sit below z=0; clipping all
    # products at zero left those visible assets without any physical proxy.
    below_layers = int(round(-MIN_Z / VOXEL_Z))
    below = np.zeros((below_layers, height, width), dtype=bool)
    for x0, y0, x1, y1, z0, z1, _name in instance_boxes:
        k0 = max(0, int(np.floor((z0 - MIN_Z) / VOXEL_Z)))
        k1 = min(below_layers - 1, int(np.ceil((z1 - MIN_Z) / VOXEL_Z)) - 1)
        for k in range(k0, k1 + 1):
            _fill_box(below[k], x0, y0, x1, y1, res, ox, oy)
    for k in range(below_layers):
        z_lo = MIN_Z + k * VOXEL_Z
        for tris in mesh_tri_arrays:
            _rasterize_tris(tris, below[k], res=res, ox=ox, oy=oy,
                            z_lo=z_lo, z_hi=z_lo + VOXEL_Z)

    # ---- verification: instance footprints vs regenerated ground map ----
    total = covered = outside_domain = 0
    per_category = {}
    for x0, y0, x1, y1, z0, z1, name in instance_boxes:
        if z1 <= GROUND_BAND[0] or z0 >= GROUND_BAND[1]:
            continue
        c0 = int(np.floor((x0 - ox) / res))
        c1 = int(np.floor((x1 - ox) / res))
        r0 = int(np.floor((y0 - oy) / res))
        r1 = int(np.floor((y1 - oy) / res))
        if c1 < 0 or c0 >= width or r1 < 0 or r0 >= height:
            outside_domain += 1
            continue
        key = _category(name)
        stats = per_category.setdefault(key, [0, 0])
        total += 1
        stats[1] += 1
        if ground[max(r0, 0):min(r1, height - 1) + 1,
                  max(c0, 0):min(c1, width - 1) + 1].any():
            covered += 1
            stats[0] += 1
    coverage = covered / total if total else 1.0

    wall_rects = _decompose_rects(ground, ground_heights)
    # Wall cubes already occupy z=0..WALL_HEIGHT.  Remove their cells from the
    # same positive layers before decomposing the remaining collision volume,
    # avoiding overlapping static colliders while retaining low floating props.
    positive_collision = voxels.copy()
    for k in range(layers):
        positive_collision[k] &= ~(
            ground & (ground_heights > (k * VOXEL_Z + 1e-9)))
    # Preserve the source voxel footprint at the map resolution.  Coarsening
    # by five cells made a 0.6 m map cell expand into a 3 m collider around
    # sofas and other small furniture.
    volume_rects = _voxel_boxes(positive_collision, z_origin=0.0, downsample=1)
    below_rects = _voxel_boxes(below, z_origin=MIN_Z, downsample=1)
    positive_uncovered = _uncovered_voxel_count(
        positive_collision, volume_rects, z_origin=0.0)
    below_uncovered = _uncovered_voxel_count(below, below_rects, z_origin=MIN_Z)

    report = {
        "scene": scene_key,
        "instances_total": len(instance_boxes),
        "instances_in_ground_band": total + outside_domain,
        "instances_in_product_domain": total,
        "instances_outside_product_domain": outside_domain,
        "instance_coverage": round(coverage, 4),
        "per_category": {k: {"hit": v[0], "total": v[1]}
                         for k, v in sorted(per_category.items())},
        "ground_occupied_fraction": round(float(ground.mean()), 4),
        "wall_count": len(wall_rects),
        "volume_box_count": len(volume_rects),
        "below_ground_box_count": len(below_rects),
        "mesh_triangle_arrays": len(mesh_tri_arrays),
        "tree_trunk_triangle_arrays": len(tree_tri_arrays),
        **collection,
        "positive_collision_voxels": int(positive_collision.sum()),
        "below_ground_collision_voxels": int(below.sum()),
        "positive_collision_voxels_uncovered": positive_uncovered,
        "below_ground_collision_voxels_uncovered": below_uncovered,
        "wall_height_min_m": (float(ground_heights[ground].min())
                               if ground.any() else 0.0),
        "wall_height_max_m": (float(ground_heights[ground].max())
                               if ground.any() else 0.0),
        "legacy_proxy_count": sum(
            GENERATED_PROXY_NAME_RE.fullmatch(child.GetName()) is not None
            for child in stage.GetPrimAtPath("/World/BenchCollision").GetChildren()),
    }
    must_cover = ("pillar", "fenc", "pile", "rack")
    category_failures = [
        key for key, value in report["per_category"].items()
        if any(token in key.lower() for token in must_cover) and value["hit"] < value["total"]
    ]
    report["category_failures"] = category_failures
    report["verified"] = (
        bool(mesh_tri_arrays or instance_boxes)
        and coverage >= 0.95
        and not category_failures
        and positive_uncovered == 0
        and below_uncovered == 0
    )

    if apply:
        if not report["verified"]:
            raise SystemExit(
                f"verification failed for {scene_key}: coverage={coverage:.3f} "
                f"category_failures={category_failures}; refusing to write")
        _write_png(map_png, ground)
        _write_npz(vox_path, voxels, ox, oy, res)
        native_collision_report = _apply_native_mesh_collisions(stage)
        report.update({
            "native_collision_meshes_applied": native_collision_report["applied"],
            "stale_mesh_collision_apis_removed": native_collision_report["removed_stale"],
        })
        rewrite_report = _rewrite_proxies(
            stage, usd_path, wall_rects, volume_rects, res, ox, oy,
            below_rects=below_rects)
        report.update(rewrite_report)
        from pxr import UsdGeom

        proxy_root = stage.GetPrimAtPath("/World/BenchCollision")
        report["proxy_group_type"] = proxy_root.GetTypeName()
        report["proxy_visibility"] = UsdGeom.Imageable(
            proxy_root).GetVisibilityAttr().Get()
        report["ground"] = _check_ground(stage)
        meta = json.loads(meta_path.read_text())
        meta["rebuild"] = report
        meta.setdefault("products", {})["ground_map_0.6m"] = {
            "occupied": round(float(ground.mean()), 4),
            "cells": [int(width), int(height)],
        }
        meta["products"]["drone_voxels"] = {
            "shape": [int(value) for value in voxels.shape],
            "voxel_m": float(VOXEL_Z),
            "z_top": float(MAX_Z),
            "occupied": round(float(voxels.mean()), 4),
            "occupied_voxels": int(voxels.sum()),
        }
        meta["collision_walls"] = len(wall_rects)
        meta["collision_volume_boxes"] = len(volume_rects)
        meta["collision_below_ground_boxes"] = len(below_rects)
        meta["product_provenance"] = build_product_provenance(scene_key)
        meta_path.write_text(json.dumps(meta, indent=2) + "\n")
    return report


def _voxel_boxes(voxels, *, z_origin, downsample=5):
    """Cluster occupied voxels into boxes per Z layer.

    Callers that need close-fitting physical proxies should use
    ``downsample=1``; the default remains for the legacy aerial helper.
    """
    layers = voxels.shape[0]
    height, width = voxels[0].shape
    h2 = (height + downsample - 1) // downsample
    w2 = (width + downsample - 1) // downsample
    boxes = []
    for k in range(layers):
        layer = voxels[k]
        if not layer.any():
            continue
        pad_h = h2 * downsample - height
        pad_w = w2 * downsample - width
        padded = np.pad(layer, ((0, pad_h), (0, pad_w)))
        small = padded.reshape(h2, downsample, w2, downsample).any(axis=(1, 3))
        for cmin, rmin, cmax, rmax in _decompose_rects(small):
            boxes.append((z_origin + k * VOXEL_Z,
                          cmin * downsample, rmin * downsample,
                          (cmax + 1) * downsample - 1,
                          (rmax + 1) * downsample - 1))
    return boxes


def _aerial_boxes(voxels, *, downsample=5):
    """Backward-compatible helper for the navigation/aerial subset."""

    return [box for box in _voxel_boxes(voxels, z_origin=0.0, downsample=downsample)
            if box[0] >= AERIAL_FROM_LAYER * VOXEL_Z]


def _uncovered_voxel_count(voxels, boxes, *, z_origin, downsample=5):
    """Return source voxels not represented by the generated coarse boxes."""

    covered = np.zeros_like(voxels, dtype=bool)
    for z0, cmin, rmin, cmax, rmax in boxes:
        k = int(round((z0 - z_origin) / VOXEL_Z))
        rmax = min(rmax, covered.shape[1] - 1)
        cmax = min(cmax, covered.shape[2] - 1)
        covered[k, rmin:rmax + 1, cmin:cmax + 1] = True
    return int(np.count_nonzero(voxels & ~covered))


def _write_png(path, ground):
    from PIL import Image

    # map_server convention: 0 = occupied (black), 254 = free (white)
    pixels = np.full(ground.shape, 254, dtype=np.uint8)
    pixels[ground] = 0
    # Internal row 0 is the minimum world Y; PNG/map_server row 0 is maximum Y.
    # USD proxies and NPZ voxel layers retain the internal bottom-up convention.
    Image.fromarray(np.flipud(pixels)).save(path)


def _ground_from_proxies(stage, shape, *, res, ox, oy):
    """Recover exact bottom-up cells from generated, grid-aligned wall cubes."""
    from pxr import Usd, UsdGeom, UsdPhysics

    ground = np.zeros(shape, dtype=bool)
    bench = stage.GetPrimAtPath("/World/BenchCollision")
    if not bench:
        raise ValueError("BenchCollision group missing")
    count = 0
    for prim in bench.GetChildren():
        name = prim.GetName()
        if not (name.startswith("w") and name[1:].isdigit()):
            continue
        collision = UsdPhysics.CollisionAPI(prim)
        if (prim.GetTypeName() != "Cube" or not prim.HasAPI(UsdPhysics.CollisionAPI)
                or not collision.GetCollisionEnabledAttr().Get()):
            raise ValueError(f"unsupported generated wall: {prim.GetPath()}")
        size = UsdGeom.Cube(prim).GetSizeAttr().Get()
        matrix = np.array(UsdGeom.Xformable(prim).ComputeLocalToWorldTransform(
            Usd.TimeCode.Default()))
        if not np.allclose(matrix[:3, :3], np.diag(np.diag(matrix[:3, :3])), atol=1e-8):
            raise ValueError(f"wall is not axis aligned: {prim.GetPath()}")
        corners = np.array([[x, y, z, 1] for x in (-size / 2, size / 2)
                            for y in (-size / 2, size / 2) for z in (-size / 2, size / 2)])
        world = (corners @ matrix)[:, :3]
        low, high = world.min(axis=0), world.max(axis=0)
        if not (np.isclose(low[2], 0, atol=1e-6)
                and MIN_WALL_HEIGHT - 1e-6 <= high[2] <= WALL_HEIGHT + 1e-6):
            raise ValueError(f"wall has unexpected height: {prim.GetPath()}")
        edges = (np.array([low[0], low[1], high[0], high[1]])
                 - np.array([ox, oy, ox, oy])) / res
        indices = np.rint(edges).astype(int)
        # Cube scale ops are float32; a 60 m wall can round by a micrometre.
        if not np.allclose(edges, indices, atol=1e-4, rtol=0):
            raise ValueError(f"wall is not map-grid aligned: {prim.GetPath()}")
        c0, r0, c1, r1 = indices
        if not (0 <= c0 < c1 <= shape[1] and 0 <= r0 < r1 <= shape[0]):
            raise ValueError(f"wall lies outside map: {prim.GetPath()}")
        ground[r0:r1, c0:c1] = True
        count += 1
    if not count:
        raise ValueError("no generated ground walls found")
    return ground, count


def check_map_proxies(scene_key, *, repair=False):
    """Check PNG against USD; repair only an exact verified vertical reflection."""
    import hashlib
    import shutil
    import tempfile
    from PIL import Image
    from pxr import Usd

    scene_dir = REPO_ROOT / SCENES[scene_key]
    cfg, res, ox, oy = _frame(scene_dir / "ground_map_0.6m.yaml")
    if not np.isclose(cfg["origin"][2], 0):
        raise ValueError("map/proxy checker requires an unrotated NVD map")
    png_path = scene_dir / cfg["image"]
    with Image.open(png_path) as image:
        pixels = np.asarray(image.convert("L"))
    occupancy = pixels / 255 if cfg.get("negate", 0) else 1 - pixels / 255
    occupied_png = occupancy >= float(cfg["free_thresh"])
    stage = Usd.Stage.Open(str(scene_dir / f"{scene_key}_bench.usd"), load=Usd.Stage.LoadNone)
    ground, count = _ground_from_proxies(stage, pixels.shape, res=res, ox=ox, oy=oy)
    expected = np.flipud(ground)
    mismatches = int(np.count_nonzero(occupied_png != expected))
    flipped_mismatches = int(np.count_nonzero(np.flipud(occupied_png) != expected))
    report = {"scene": scene_key, "wall_count": count, "cells": int(ground.size),
              "mismatched_cells": mismatches,
              "mismatched_cells_after_vertical_flip": flipped_mismatches,
              "verified": mismatches == 0, "repaired": False,
              "diagnosis": "aligned" if not mismatches else
                  "png_vertical_serialization_bug" if not flipped_mismatches else "other_mismatch"}
    if repair and mismatches:
        if flipped_mismatches:
            raise ValueError(f"Refusing non-reflection repair: {report}")
        original = png_path.read_bytes()
        backup_root = REPO_ROOT / "tmp" / "nvd_map_orientation_backups"
        backup_root.mkdir(parents=True, exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix=f"{scene_key}_", dir=backup_root))
        shutil.copy2(png_path, backup / png_path.name)
        # Flip original pixels, preserving negate/unknown/threshold semantics.
        Image.fromarray(np.flipud(pixels)).save(png_path)
        checked = check_map_proxies(scene_key)
        if not checked["verified"]:
            png_path.write_bytes(original)
            raise ValueError("PNG verification failed; original bytes restored")
        report.update(verified=True, repaired=True, mismatched_cells=0,
                      mismatched_cells_before_repair=mismatches, backup_directory=str(backup),
                      sha256_before=hashlib.sha256(original).hexdigest(),
                      sha256_after=hashlib.sha256(png_path.read_bytes()).hexdigest())
        (backup / "repair.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def _write_npz(path, voxels, ox, oy, res):
    np.savez(path, voxels=voxels, x0=float(ox), y0=float(oy),
             res=float(res), vox=float(VOXEL_Z))


def _rewrite_proxies(
    stage,
    usd_path,
    wall_rects,
    volume_rects,
    res,
    ox,
    oy,
    *,
    below_rects=(),
):
    from pxr import Sdf, Usd, UsdGeom, UsdPhysics

    bench = stage.GetPrimAtPath("/World/BenchCollision")
    if not bench.IsValid():
        raise SystemExit("BenchCollision group missing; nothing to rewrite")
    # Imageable attributes authored on the legacy untyped group are ignored by
    # RTX. Normalize it to an Xform so inherited visibility hides only render
    # geometry while the descendant CollisionAPI schemas remain active.
    bench = UsdGeom.Xform.Define(stage, "/World/BenchCollision").GetPrim()
    removed_legacy = 0
    for child in list(bench.GetAllChildren()):
        name = child.GetName()
        if (GENERATED_PROXY_NAME_RE.fullmatch(name)
                or name in {"aerial", "volume", "below_ground"}):
            if GENERATED_PROXY_NAME_RE.fullmatch(name):
                removed_legacy += 1
            stage.RemovePrim(child.GetPath())
    _define_ground_plane(stage)
    for index, rect in enumerate(sorted(wall_rects)):
        if len(rect) == 4:
            cmin, rmin, cmax, rmax = rect
            wall_z1 = WALL_HEIGHT
        else:
            cmin, rmin, cmax, rmax, wall_z1 = rect
        x0, y0 = cmin * res + ox, rmin * res + oy
        x1, y1 = (cmax + 1) * res + ox, (rmax + 1) * res + oy
        _define_cube(stage, f"/World/BenchCollision/w{index:04d}",
                     (x0, y0), (x1, y1), 0.0,
                     max(MIN_WALL_HEIGHT, min(float(wall_z1), WALL_HEIGHT)))
    UsdGeom.Scope.Define(stage, "/World/BenchCollision/volume")
    for index, (z0, cmin, rmin, cmax, rmax) in enumerate(sorted(volume_rects)):
        x0, y0 = cmin * res + ox, rmin * res + oy
        x1, y1 = (cmax + 1) * res + ox, (rmax + 1) * res + oy
        z1 = z0 + VOXEL_Z
        _define_cube(
            stage, f"/World/BenchCollision/volume/v{index:05d}",
            (x0, y0), (x1, y1), z0, z1)
    UsdGeom.Scope.Define(stage, "/World/BenchCollision/below_ground")
    for index, (z0, cmin, rmin, cmax, rmax) in enumerate(sorted(below_rects)):
        x0, y0 = cmin * res + ox, rmin * res + oy
        x1, y1 = (cmax + 1) * res + ox, (rmax + 1) * res + oy
        _define_cube(
            stage, f"/World/BenchCollision/below_ground/b{index:05d}",
            (x0, y0), (x1, y1), z0, z0 + VOXEL_Z)
    # The proxy layer is physics-only. PhysX ignores imageable visibility, so
    # wall/aerial/ground collision remains enabled.
    UsdGeom.Imageable(bench).CreateVisibilityAttr().Set(UsdGeom.Tokens.invisible)
    stage.GetRootLayer().Save()
    remaining_legacy = sum(
        STALE_PROXY_NAME_RE.fullmatch(child.GetName()) is not None
        for child in bench.GetChildren())
    if remaining_legacy:
        raise ValueError(f"stale generated collision proxies remain: {remaining_legacy}")
    return {
        "removed_legacy_proxy_count": removed_legacy,
        "remaining_legacy_proxy_count": remaining_legacy,
    }


def _define_cube(stage, path, xy_min, xy_max, z0, z1):
    from pxr import Sdf, Usd, UsdGeom, UsdPhysics

    cube = UsdGeom.Cube.Define(stage, path)
    prim = cube.GetPrim()
    cube.CreateSizeAttr(1.0)
    cube.CreateExtentAttr([(-0.5, -0.5, -0.5), (0.5, 0.5, 0.5)])
    x0, y0 = xy_min
    x1, y1 = xy_max
    xform = UsdGeom.Xformable(prim)
    xform.ClearXformOpOrder()
    xform.AddTranslateOp().Set(((x0 + x1) / 2.0, (y0 + y1) / 2.0, (z0 + z1) / 2.0))
    xform.AddScaleOp().Set((x1 - x0, y1 - y0, z1 - z0))
    UsdPhysics.CollisionAPI.Apply(prim)
    prim.CreateAttribute("physics:collisionEnabled", Sdf.ValueTypeNames.Bool).Set(True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scene", choices=sorted(SCENES), required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true",
                      help="write corrected products (default: dry-run report)")
    mode.add_argument("--check-ground", action="store_true",
                      help="read-only floor schema/transform check; skip map rebuilding")
    mode.add_argument("--check-map-proxies", action="store_true",
                      help="read-only cell-exact PNG versus generated USD wall check")
    mode.add_argument("--check-provenance", action="store_true",
                      help="read-only hash and generator-contract check for all products")
    mode.add_argument("--repair-map-orientation", action="store_true",
                      help="back up and flip PNG only when USD proves an exact vertical reflection")
    args = parser.parse_args(argv)
    if args.check_provenance:
        try:
            manifest = validate_product_provenance(args.scene)
            report = {
                "scene": args.scene,
                "verified": True,
                "publication_status": manifest["publication_status"],
                "provider_revision": manifest["provider_revision"],
                "schema": manifest["schema"],
            }
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            report = {"scene": args.scene, "verified": False, "error": str(exc)}
        print(json.dumps(report, indent=2))
        return 0 if report["verified"] else 1
    if args.check_map_proxies or args.repair_map_orientation:
        report = check_map_proxies(args.scene, repair=args.repair_map_orientation)
        print(json.dumps(report, indent=2))
        return 0 if report["verified"] else 1
    if args.check_ground:
        from pxr import Usd

        path = REPO_ROOT / SCENES[args.scene] / f"{args.scene}_bench.usd"
        stage = Usd.Stage.Open(str(path), load=Usd.Stage.LoadNone)
        report = {"scene": args.scene, **_check_ground(stage)}
        print(json.dumps(report, indent=2))
        return 0 if report["verified"] else 1
    report = build_products(args.scene, apply=args.apply)
    print(json.dumps(report, indent=2))
    return 0 if report["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
